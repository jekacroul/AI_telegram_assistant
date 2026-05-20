"""Meeting confirmation flow.

Ties the meeting detector and the calendar engine together:

* when the contact's message shows meeting intent, a :class:`PendingMeeting`
  is opened for the chat (remembering any date hint, e.g. "завтра");
* while that negotiation is open, a message naming a concrete time
  ("в 19", "давай в 12 часов") is resolved to a real datetime and, if it
  is in the future and free, turned into a calendar event.

The event is booked at the time the contact actually named — never at an
arbitrary slot — and only while a meeting is being negotiated.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import select

from .calendar_engine import (
    _slot_label,
    calendar_engine,
    slots_from_json,
    slots_to_json,
)
from .caldav_config import get_caldav_config
from .database import CreatedMeeting, PendingMeeting, SessionLocal
from .meeting_detector import (
    detect_meeting_intent,
    extract_requested_time,
    matched_day_index,
    reschedule_source_date,
    reschedule_target_date,
)


# A self-contained proposal in one message ("давай встречу в 18", "встретимся
# в 14 завтра") should create the event directly even without a prior
# pending negotiation. To avoid booking off random "у меня встреча в 18"
# mentions, the message must contain one of these proposing markers.
_PROPOSING_KEYWORDS = (
    "давай",
    "встретим", "встрет",
    "созвонимся", "созвон", "позвоним",
    "погуляем", "погулять", "гулять", "прогуляемся",
    "сходим", "пройдёмся", "пройдемся",
    "увидимся", "повидаемся", "видеться", "видимся",
    "пойдём", "пойдем",
    "let's meet", "lets meet",
    "как насчёт", "как насчет",
)


_SERIES_MARKERS = ("каждый день", "ежедневно", "по будням", "каждый будний день")
_WORKDAYS_ONLY_MARKERS = ("рабочий день", "будний день", "по будням")
_CALL_HINT_WORDS = ("звон", "созвон", "call")

log = logging.getLogger(__name__)

# A pending meeting older than this is considered a stale negotiation and is
# no longer matched against incoming times.
_PENDING_TTL = timedelta(hours=24)

# Short-lived per-chat memory of calendar actions the assistant just
# performed. The LLM reply for the same incoming message picks it up so
# the bot can acknowledge the booking instead of re-asking for a time.
_RECENT_ACTION_TTL = timedelta(minutes=10)
_recent_actions: dict[int, list[dict]] = {}


def _remember_action(chat_id: int, kind: str, payload: dict) -> None:
    _recent_actions.setdefault(chat_id, []).append(
        {"kind": kind, "payload": payload, "at": datetime.utcnow()}
    )


def pop_recent_actions(chat_id: int) -> list[dict]:
    """Return and clear the recent calendar actions for a chat that are
    still within the TTL. Stale entries are dropped."""
    items = _recent_actions.pop(chat_id, [])
    cutoff = datetime.utcnow() - _RECENT_ACTION_TTL
    return [i for i in items if i["at"] >= cutoff]


def format_recent_actions(actions: list[dict]) -> Optional[str]:
    """Render a per-chat action memo for inclusion in the system prompt."""
    if not actions:
        return None
    lines = [
        "ВАЖНО — состояние календаря для этого ответа (источник истины):",
        "Ты только что выполнил эти действия, они уже зафиксированы:",
    ]
    for action in actions:
        kind = action.get("kind")
        p = action.get("payload") or {}
        if kind == "created":
            lines.append(
                f"- ✅ ЗАПИСАЛ встречу в календарь: {p.get('label', '')}."
                " Время свободно и теперь занято этой встречей."
            )
        elif kind == "cancelled":
            lines.append(
                f"- 🗑 УДАЛИЛ встречу: {p.get('label', '')}."
                " Этого времени в календаре больше нет."
            )
        elif kind == "rescheduled":
            lines.append(
                f"- 📅 ПЕРЕНЁС встречу: было {p.get('old_label', '')}"
                f" → стало {p.get('label', '')}. Новая запись уже в календаре."
            )

    created_count = sum(1 for a in actions if a.get("kind") == "created")
    if created_count >= 3:
        # Local models hallucinate weekdays / dates when asked to enumerate
        # a series — keep them from doing that.
        lines.append(
            "КАК ОТВЕЧАТЬ: записал серию встреч. Скажи коротко: «записал"
            " на эти дни, посмотри в календаре». КАТЕГОРИЧЕСКИ НЕЛЬЗЯ"
            " перечислять даты / числа / дни недели самостоятельно —"
            " модель часто ошибается; календарь покажет корректно."
        )
    else:
        lines.append(
            "В ответе собеседнику естественно подтверди выполненное"
            " («договорились, записал на …», «перенёс на …», «отменил»)."
            " Если упоминаешь дату или день недели — копируй её точно так,"
            " как написано выше, не пересчитывай и не выдумывай. Запрещено:"
            " говорить что ты в это время занят или не сможешь; переспрашивать"
            " время; предлагать другой вариант; противоречить только что"
            " выполненной записи. Действие уже совершено."
        )
    return "\n".join(lines)

# Substrings that signal a request to cancel meetings. "отмени" / "отменя"
# cover almost every conjugated form of отменить/отменять; "удали встреч" /
# "удали событ" require the object so generic "удали из чата" doesn't match.
_MONTH_NAMES_GENITIVE = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _parse_specific_date(text: str, today: "date") -> Optional["date"]:
    """Match an explicit calendar date in the text — '20 мая', '20 числа',
    '20-го', '20.05', '20/5' — and return it as the first occurrence on or
    after ``today``. Returns None when no such date is mentioned."""
    if not text:
        return None
    low = text.lower()

    # Most specific first: explicit day + month name (e.g. "20 мая").
    months = "|".join(_MONTH_NAMES_GENITIVE.keys())
    m = re.search(rf"\b(\d{{1,2}})\s*(?:-?го\s+)?({months})\b", low)
    if m:
        day = int(m.group(1))
        month = _MONTH_NAMES_GENITIVE[m.group(2)]
        for year in (today.year, today.year + 1):
            try:
                cand = date(year, month, day)
            except ValueError:
                continue
            if cand >= today:
                return cand
        return None

    # Numeric "DD.MM" / "DD/MM".
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})\b", low)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                cand = date(year, month, day)
            except ValueError:
                continue
            if cand >= today:
                return cand
        return None

    # Day-of-month only: "20 числа", "20-го" — current month, else roll over.
    m = re.search(r"\b(\d{1,2})\s*(?:числа|[-‐−]?го)\b", low)
    if m:
        day = int(m.group(1))
        for offset in range(0, 4):
            year = today.year
            month = today.month + offset
            while month > 12:
                month -= 12
                year += 1
            try:
                cand = date(year, month, day)
            except ValueError:
                continue
            if cand >= today:
                return cand
        return None

    return None


_CANCEL_PHRASES = (
    "отмени", "отменя",
    "удали встреч", "удали событ", "удалите встреч",
    "удали все", "удалить все", "удалить встреч",
    "снимай встреч", "снять встреч", "убери встреч",
    "убери из календар", "отказаться от встреч",
    "перенес",  # rescheduling also cancels the existing meeting
    "cancel",
)


def _encode_proposal(meeting_type: str, date_hint: str, slots: list[dict]) -> str:
    return json.dumps(
        {
            "meeting_type": meeting_type,
            "date_hint": date_hint or "",
            "slots": json.loads(slots_to_json(slots)),
        },
        ensure_ascii=False,
    )


def _decode_proposal(raw: str) -> tuple[str, str, list[dict]]:
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return "meeting", "", []
    if isinstance(data, list):  # tolerate a bare slot list
        return "meeting", "", slots_from_json(raw)
    return (
        data.get("meeting_type", "meeting"),
        data.get("date_hint", ""),
        slots_from_json(json.dumps(data.get("slots", []))),
    )


def _event_title(meeting_type: str, sender_name: str) -> str:
    who = sender_name or "собеседником"
    return f"{'Звонок' if meeting_type == 'call' else 'Встреча'} с {who}"


def _resolve_date(
    text: str, hour: int, minute: int, now: datetime
) -> Optional[date]:
    """Resolve a calendar date from textual hints (сегодня / завтра /
    weekday / DD.MM). Returns None when the text carries no date hint."""
    if not text:
        return None
    low = text.lower()
    today = now.date()
    if "послезавтра" in low:
        return today + timedelta(days=2)
    if "завтра" in low:
        return today + timedelta(days=1)
    if "сегодня" in low:
        return today
    day_idx = matched_day_index(low)
    if day_idx is not None:
        for offset in range(0, 8):
            candidate = today + timedelta(days=offset)
            if candidate.weekday() != day_idx:
                continue
            at = datetime.combine(candidate, time(hour=hour, minute=minute))
            if at > now:
                return candidate
        return None
    return _parse_specific_date(text, today)


def _resolve_start(
    incoming_text: str,
    date_hint: str,
    hour: int,
    minute: int,
    now: datetime,
) -> datetime:
    """Build the meeting start datetime: a date hint in the confirmation
    wins, then the hint remembered from the original request, then a
    default of today (if the time is still ahead) or tomorrow."""
    target = _resolve_date(incoming_text, hour, minute, now) or _resolve_date(
        date_hint or "", hour, minute, now
    )
    if target is not None:
        return datetime.combine(target, time(hour=hour, minute=minute))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


async def maybe_record_pending_meeting(
    chat_id: int,
    message_id: Optional[int],
    sender_name: str,
    incoming_text: str,
) -> None:
    """Open a pending meeting for the chat when the contact's message shows
    meeting intent, remembering any date hint so a later bare time ("в 19")
    can still be placed on the right day."""
    if not calendar_engine.is_connected:
        return
    try:
        intent = await detect_meeting_intent(incoming_text)
        if not intent or not intent.has_intent:
            return
        slots = await calendar_engine.get_free_slots()
        async with SessionLocal() as session:
            # Supersede any earlier still-pending proposal for this chat.
            stale = (
                await session.execute(
                    select(PendingMeeting).where(
                        PendingMeeting.chat_id == chat_id,
                        PendingMeeting.status == "pending",
                    )
                )
            ).scalars().all()
            for row in stale:
                row.status = "cancelled"
            session.add(
                PendingMeeting(
                    chat_id=chat_id,
                    message_id=message_id,
                    sender_name=sender_name,
                    proposed_slots_json=_encode_proposal(
                        intent.meeting_type or "meeting",
                        intent.suggested_date or "",
                        slots,
                    ),
                    status="pending",
                )
            )
            await session.commit()
        log.info(
            "opened pending meeting for chat %s (date hint=%r)",
            chat_id,
            intent.suggested_date or "",
        )
    except Exception:  # noqa: BLE001
        log.exception("maybe_record_pending_meeting failed")


async def process_incoming_confirmation(
    chat_id: int, sender_name: str, incoming_text: str
) -> Optional[dict]:
    """If an incoming message names a concrete time while a meeting is being
    negotiated, create the calendar event at exactly that time.

    Returns event info on success, otherwise None (no time named, no open
    negotiation, the time is in the past, or it clashes with an event).
    """
    if not calendar_engine.is_connected:
        return None
    requested = extract_requested_time(incoming_text)
    if requested is None:
        return None
    hour, minute = requested
    try:
        async with SessionLocal() as session:
            cfg = await get_caldav_config(session)
            if not cfg["caldav_auto_create"]:
                return None
            cutoff = datetime.utcnow() - _PENDING_TTL
            pending = (
                await session.execute(
                    select(PendingMeeting)
                    .where(
                        PendingMeeting.chat_id == chat_id,
                        PendingMeeting.status == "pending",
                        PendingMeeting.created_at >= cutoff,
                    )
                    .order_by(PendingMeeting.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            pending_id = pending.id if pending else None
            if pending is not None:
                meeting_type, date_hint, _slots = _decode_proposal(
                    pending.proposed_slots_json
                )
            else:
                # No open negotiation — accept the message as a self-contained
                # proposal only if it carries a proposing verb (or a reschedule
                # with an explicit new date). This guards against booking off
                # bare mentions like "у меня встреча в 18".
                low = incoming_text.lower()
                if "перенес" in low:
                    target = reschedule_target_date(incoming_text)
                    date_hint = target or ""
                    meeting_type = "meeting"
                elif _has_cancel_phrase(incoming_text):
                    # The cancellation flow runs separately for this message.
                    # Don't also create something at the named time.
                    return None
                elif any(kw in low for kw in _PROPOSING_KEYWORDS):
                    date_hint = ""
                    meeting_type = (
                        "call"
                        if any(w in low for w in _CALL_HINT_WORDS)
                        else "meeting"
                    )
                else:
                    return None

        now = datetime.now()
        start = _resolve_start(incoming_text, date_hint, hour, minute, now)
        if start <= now:
            log.info(
                "confirmation time %02d:%02d resolves to the past; skipping "
                "for chat %s",
                hour,
                minute,
                chat_id,
            )
            return None
        duration = max(15, int(calendar_engine.slot_duration or 60))
        end = start + timedelta(minutes=duration)

        # Don't book over an event we already created locally — iCloud may
        # not have surfaced it on the read side yet.
        if await _has_overlapping_created_meeting(start, end):
            log.info(
                "confirmation: already have a local meeting at %s; skipping",
                start,
            )
            return None
        # Don't book over an existing event on the calendar either.
        window = await calendar_engine.get_events(
            start - timedelta(hours=3), end + timedelta(hours=3)
        )
        conflict = any(
            isinstance(e["start"], datetime)
            and isinstance(e["end"], datetime)
            and not (end <= e["start"] or start >= e["end"])
            for e in window
        )
        if conflict:
            log.info(
                "requested meeting time %s conflicts with an existing event; "
                "skipping for chat %s",
                start,
                chat_id,
            )
            return None

        title = _event_title(meeting_type, sender_name)
        uid = await calendar_engine.create_event(
            title=title,
            start=start,
            end=end,
            description="Создано AI-ассистентом по подтверждению собеседника.",
        )
        if not uid:
            log.warning("confirmation matched but event creation failed")
            return None

        label = _slot_label(start, end)
        async with SessionLocal() as session:
            pending = (
                await session.execute(
                    select(PendingMeeting).where(PendingMeeting.id == pending_id)
                )
            ).scalar_one_or_none()
            if pending:
                pending.status = "confirmed"
            session.add(
                CreatedMeeting(
                    chat_id=chat_id,
                    calendar_uid=uid,
                    title=title,
                    start_time=start,
                    end_time=end,
                    created_by="auto",
                )
            )
            await session.commit()

        log.info(
            "auto-created calendar event '%s' at %s for chat %s",
            title,
            start,
            chat_id,
        )
        event_info = {
            "uid": uid,
            "title": title,
            "label": label,
            "start": start,
            "end": end,
            "sender_name": sender_name,
        }
        _remember_action(chat_id, "created", event_info)
        return event_info
    except Exception:  # noqa: BLE001
        log.exception("process_incoming_confirmation failed")
        return None


def _has_cancel_phrase(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in _CANCEL_PHRASES)


# Splits a message into clauses on punctuation or coordinating conjunctions
# ("и" / "а") so a date in a different clause doesn't bleed into the cancel
# scope. Used to keep "отмени встречу) и перенеси на послезавтра" from
# pointing the cancellation at the wrong day.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?;)\n]|(?<=\s)(?:и|а)(?=\s)")


def _cancel_clause(text: str) -> str:
    if not text:
        return ""
    low = text.lower()
    parts = _CLAUSE_SPLIT_RE.split(low)
    for part in parts:
        if any(kw in part for kw in _CANCEL_PHRASES):
            return part.strip()
    return low


def _resolve_cancel_scope(text: str, now: datetime) -> dict:
    """Pick the (start, end, time) window of events to cancel from the
    cancel clause: 'все встречи' → all upcoming; date hints narrow to that
    day; a named time further restricts within the day. With no date hint
    in the cancel clause the default is *all upcoming* events for the chat
    — the contact is talking about the meeting being negotiated, not
    arbitrary other days."""
    clause = _cancel_clause(text)
    requested = extract_requested_time(clause)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    horizon = now + timedelta(days=60)
    all_markers = (
        "все встреч", "все событ", "все мои встреч",
        "отмени все", "отменим все", "отменить все",
    )
    if any(m in clause for m in all_markers):
        return {"start": now, "end": horizon, "time": requested}
    # Reschedule: "перенеси с X на Y" → cancel the meeting at X.
    # "перенеси на Y" (no "с") → cancel whatever is currently scheduled
    # in this chat (all upcoming), then a fresh negotiation opens on Y.
    if "перенес" in clause:
        source = reschedule_source_date(clause)
        if source:
            src_date = _resolve_date(source, 0, 0, now)
            if src_date is not None:
                d = datetime.combine(src_date, time(0, 0))
                return {
                    "start": d,
                    "end": d + timedelta(days=1),
                    "time": requested,
                }
        return {"start": now, "end": horizon, "time": requested}
    if "послезавтра" in clause:
        d = today + timedelta(days=2)
        return {"start": d, "end": d + timedelta(days=1), "time": requested}
    if "завтра" in clause:
        d = today + timedelta(days=1)
        return {"start": d, "end": d + timedelta(days=1), "time": requested}
    if "сегодня" in clause:
        return {"start": today, "end": today + timedelta(days=1), "time": requested}
    day_idx = matched_day_index(clause)
    if day_idx is not None:
        for offset in range(0, 8):
            d = today + timedelta(days=offset)
            if d.weekday() == day_idx:
                return {
                    "start": d,
                    "end": d + timedelta(days=1),
                    "time": requested,
                }
    # Explicit calendar date: "20 числа", "20 мая", "20.05".
    specific = _parse_specific_date(clause, today.date())
    if specific is not None:
        d = datetime.combine(specific, time(0, 0))
        return {"start": d, "end": d + timedelta(days=1), "time": requested}
    # No specific date in the cancel clause — apply to all upcoming events
    # for this chat. In practice the contact is asking to drop the meeting
    # currently being negotiated, not random other days.
    return {"start": now, "end": horizon, "time": requested}


async def _has_overlapping_created_meeting(
    start: datetime, end: datetime
) -> bool:
    """True when an assistant-created meeting already overlaps the given
    window. Used as a local safety net so we don't book a duplicate while
    iCloud has not yet finished propagating a previous PUT."""
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(CreatedMeeting)
                .where(
                    CreatedMeeting.start_time < end,
                    CreatedMeeting.end_time > start,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    return row is not None


async def _cancel_pending_for_chat(chat_id: int) -> None:
    """Drop any open meeting negotiations for the chat so subsequent times
    don't accidentally auto-create new events."""
    async with SessionLocal() as session:
        pendings = (
            await session.execute(
                select(PendingMeeting).where(
                    PendingMeeting.chat_id == chat_id,
                    PendingMeeting.status == "pending",
                )
            )
        ).scalars().all()
        for p in pendings:
            p.status = "cancelled"
        if pendings:
            await session.commit()


async def process_cancellation(
    chat_id: int, sender_name: str, incoming_text: str
) -> Optional[dict]:
    """If the incoming message asks to cancel meetings, delete the matching
    assistant-created events from the calendar.

    Scope is limited to events the assistant created for this chat (the
    ``created_meetings`` table) — manually-added third-party events are
    never touched. Returns a dict with the cancelled events on success,
    None when the message is not a cancellation.
    """
    if not calendar_engine.is_connected:
        return None
    if not _has_cancel_phrase(incoming_text):
        return None
    try:
        now = datetime.now()
        scope = _resolve_cancel_scope(incoming_text, now)
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(CreatedMeeting)
                    .where(
                        CreatedMeeting.chat_id == chat_id,
                        CreatedMeeting.start_time >= scope["start"],
                        CreatedMeeting.start_time < scope["end"],
                    )
                    .order_by(CreatedMeeting.start_time)
                )
            ).scalars().all()
            scope_rows = [
                (r.calendar_uid, r.title, r.start_time, r.end_time) for r in rows
            ]

        if scope["time"] is not None:
            h, m = scope["time"]
            scope_rows = [
                r for r in scope_rows if r[2].hour == h and r[2].minute == m
            ]

        # Always drop pending negotiations on a cancel request — even when
        # there were no scheduled events yet, the user clearly wants out.
        await _cancel_pending_for_chat(chat_id)

        if not scope_rows:
            log.info(
                "cancellation: no matching events for chat %s (scope=%s)",
                chat_id,
                scope,
            )
            return {
                "cancelled": [],
                "rescheduled": [],
                "requested": True,
                "sender_name": sender_name,
            }

        deleted: list[dict] = []
        for uid, title, start, end in scope_rows:
            ok = await calendar_engine.delete_event(uid, around=start)
            log.info("cancel event uid=%s '%s' -> %s", uid, title, ok)
            if ok:
                deleted.append(
                    {
                        "title": title,
                        "start": start,
                        "end": end,
                        "uid": uid,
                        "label": _slot_label(start, end),
                    }
                )

        # Reschedule branch: "перенеси ... на <date>" without an explicit
        # time keeps the original time on the new day, so the user does not
        # need to repeat "в 18". A reschedule with an explicit time is
        # handled by ``process_incoming_confirmation`` instead — skip here.
        rescheduled: list[dict] = []
        target_str = reschedule_target_date(incoming_text)
        requested_time = extract_requested_time(incoming_text)
        if target_str and requested_time is None and deleted:
            target_date_obj = _resolve_date(target_str, 0, 0, now)
            if target_date_obj is not None:
                for old in deleted:
                    duration = old["end"] - old["start"]
                    new_start = datetime.combine(
                        target_date_obj, old["start"].time()
                    )
                    new_end = new_start + duration
                    if new_start <= now:
                        continue
                    window = await calendar_engine.get_events(
                        new_start - timedelta(hours=3),
                        new_end + timedelta(hours=3),
                    )
                    has_conflict = any(
                        isinstance(e["start"], datetime)
                        and isinstance(e["end"], datetime)
                        and not (new_end <= e["start"] or new_start >= e["end"])
                        for e in window
                    )
                    if has_conflict:
                        log.info(
                            "reschedule for '%s' conflicts at %s; skipping",
                            old["title"],
                            new_start,
                        )
                        continue
                    new_uid = await calendar_engine.create_event(
                        title=old["title"],
                        start=new_start,
                        end=new_end,
                        description="Перенос встречи AI-ассистентом.",
                    )
                    if not new_uid:
                        continue
                    async with SessionLocal() as session:
                        session.add(
                            CreatedMeeting(
                                chat_id=chat_id,
                                calendar_uid=new_uid,
                                title=old["title"],
                                start_time=new_start,
                                end_time=new_end,
                                created_by="auto",
                            )
                        )
                        await session.commit()
                    rescheduled.append(
                        {
                            "title": old["title"],
                            "old_start": old["start"],
                            "old_end": old["end"],
                            "old_label": old["label"],
                            "start": new_start,
                            "end": new_end,
                            "label": _slot_label(new_start, new_end),
                            "uid": new_uid,
                        }
                    )

        # Anything that was successfully re-created counts as rescheduled,
        # not as a bare cancellation.
        rescheduled_uids = {r.get("title"): r for r in rescheduled}
        cancelled_only = [
            d
            for d in deleted
            if not any(
                r["title"] == d["title"]
                and r["old_start"] == d["start"]
                for r in rescheduled
            )
        ]

        for action in rescheduled:
            _remember_action(chat_id, "rescheduled", action)
        for action in cancelled_only:
            _remember_action(chat_id, "cancelled", action)

        return {
            "cancelled": cancelled_only,
            "rescheduled": rescheduled,
            "requested": True,
            "sender_name": sender_name,
        }
    except Exception:  # noqa: BLE001
        log.exception("process_cancellation failed")
        return None


def _has_series_phrase(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(p in low for p in _SERIES_MARKERS)


def _resolve_series_end(text: str, today: "date") -> Optional["date"]:
    """Find the closing date of a recurring proposal.

    Recognised: "до конца недели", "до конца месяца", "на этой неделе",
    "до пятницы", "до 25 числа", "до 25 мая", "до завтра". Returns None
    when no closing hint is present (caller defaults to one week ahead)."""
    if not text:
        return None
    low = text.lower()
    if "до конца недели" in low or "конца недели" in low:
        days_to_sunday = (6 - today.weekday()) % 7
        return today + timedelta(days=days_to_sunday or 7)
    if "до конца месяца" in low or "конца месяца" in low:
        from calendar import monthrange
        _, last_day = monthrange(today.year, today.month)
        return today.replace(day=last_day)
    if "на этой неделе" in low or "этой неделе" in low:
        days_to_sunday = (6 - today.weekday()) % 7
        return today + timedelta(days=days_to_sunday or 7)
    if "до завтра" in low:
        return today + timedelta(days=1)
    m = re.search(
        r"\bдо\s+(понедельник\w*|вторник\w*|сред\w*|четверг\w*|"
        r"пятниц\w*|суббот\w*|воскресень\w*)",
        low,
    )
    if m:
        target = matched_day_index(m.group(1))
        if target is not None:
            days_ahead = (target - today.weekday()) % 7
            return today + timedelta(days=days_ahead or 7)
    m = re.search(r"\bдо\s+(.+?)(?:[.,!?]|$)", low)
    if m:
        spec = _parse_specific_date(m.group(1), today)
        if spec:
            return spec
    return None


def _series_dates(text: str, now: datetime) -> list:
    """Build the list of dates for the proposed series. Starts today (or
    tomorrow when the workday has already ended), runs to the resolved
    end date, and skips weekends when the message says "по будням" /
    "рабочий день"."""
    today = now.date()
    end = _resolve_series_end(text, today)
    if end is None:
        end = today + timedelta(days=6)
    if end < today:
        return []

    work_end = calendar_engine.work_end or 20
    start_date = today
    # If the workday is essentially over today, start the series tomorrow.
    if now.hour >= work_end:
        start_date = today + timedelta(days=1)

    low = text.lower()
    skip_weekends = any(p in low for p in _WORKDAYS_ONLY_MARKERS)

    dates: list = []
    d = start_date
    while d <= end:
        if not (skip_weekends and d.weekday() >= 5):
            dates.append(d)
        d += timedelta(days=1)
    return dates


async def _pick_first_free_slot_on(d: "date") -> Optional[datetime]:
    """First free slot of given length within the engine's working hours
    on the given date. Returns None when nothing fits."""
    work_start = calendar_engine.work_start or 9
    work_end = calendar_engine.work_end or 20
    duration = max(15, int(calendar_engine.slot_duration or 60))
    now = datetime.now()

    day_start = datetime.combine(d, time(work_start, 0))
    day_end = datetime.combine(d, time(work_end, 0))

    cursor = day_start
    if cursor < now:
        cursor = (
            now.replace(minute=0, second=0, microsecond=0)
            + timedelta(hours=1)
        )
        if cursor.date() != d:
            return None
        if cursor < day_start:
            cursor = day_start

    events = await calendar_engine.get_events(
        day_start, day_end + timedelta(minutes=duration)
    )

    # Local assistant-created meetings on this day — used to avoid handing
    # out a slot that already has a pending iCloud write.
    async with SessionLocal() as session:
        local_rows = (
            await session.execute(
                select(CreatedMeeting).where(
                    CreatedMeeting.start_time >= day_start,
                    CreatedMeeting.start_time < day_end + timedelta(days=1),
                )
            )
        ).scalars().all()
    local_intervals = [(r.start_time, r.end_time) for r in local_rows]

    while cursor + timedelta(minutes=duration) <= day_end:
        slot_end = cursor + timedelta(minutes=duration)
        conflict_remote = any(
            isinstance(e["start"], datetime)
            and isinstance(e["end"], datetime)
            and not (slot_end <= e["start"] or cursor >= e["end"])
            for e in events
        )
        conflict_local = any(
            not (slot_end <= s or cursor >= e)
            for s, e in local_intervals
        )
        if not conflict_remote and not conflict_local:
            return cursor
        cursor += timedelta(minutes=30)
    return None


async def process_series_proposal(
    chat_id: int, sender_name: str, incoming_text: str
) -> Optional[dict]:
    """Create a recurring series of meetings — one per day in the resolved
    range — when the message proposes "каждый день / до конца недели /
    по будням" and shows a proposing verb. Skipped on cancel phrases so
    "отмени каждый день" never accidentally books anything.
    """
    if not calendar_engine.is_connected:
        return None
    if not _has_series_phrase(incoming_text):
        return None
    if _has_cancel_phrase(incoming_text):
        return None
    low = incoming_text.lower()
    if not any(kw in low for kw in _PROPOSING_KEYWORDS):
        return None
    try:
        now = datetime.now()
        dates = _series_dates(incoming_text, now)
        if not dates:
            log.info(
                "series: trigger matched but no dates resolved for chat %s",
                chat_id,
            )
            return None
        log.info(
            "series: chat=%s candidate dates=%s",
            chat_id,
            [d.isoformat() for d in dates],
        )
        requested_time = extract_requested_time(low)
        duration = max(15, int(calendar_engine.slot_duration or 60))
        meeting_type = (
            "call"
            if any(w in low for w in _CALL_HINT_WORDS)
            else "meeting"
        )
        title = _event_title(meeting_type, sender_name)

        created: list[dict] = []
        for d in dates:
            if requested_time is not None:
                h, m = requested_time
                start = datetime.combine(d, time(h, m))
                if start <= now:
                    continue
                end_candidate = start + timedelta(minutes=duration)
                # Skip if we already booked this slot locally — guards
                # against duplicate creation on a re-issued request.
                if await _has_overlapping_created_meeting(start, end_candidate):
                    log.info(
                        "series: slot %s already booked locally; skip", start
                    )
                    continue
                window = await calendar_engine.get_events(
                    start - timedelta(hours=3),
                    end_candidate + timedelta(hours=3),
                )
                conflict = any(
                    isinstance(e["start"], datetime)
                    and isinstance(e["end"], datetime)
                    and not (
                        end_candidate <= e["start"] or start >= e["end"]
                    )
                    for e in window
                )
                if conflict:
                    continue
            else:
                slot = await _pick_first_free_slot_on(d)
                if slot is None:
                    continue
                start = slot
                if await _has_overlapping_created_meeting(
                    start, start + timedelta(minutes=duration)
                ):
                    log.info(
                        "series: picked slot %s already booked locally; skip",
                        start,
                    )
                    continue
            end = start + timedelta(minutes=duration)
            uid = await calendar_engine.create_event(
                title=title,
                start=start,
                end=end,
                description="Серия встреч, создана AI-ассистентом.",
            )
            if not uid:
                continue
            async with SessionLocal() as session:
                session.add(
                    CreatedMeeting(
                        chat_id=chat_id,
                        calendar_uid=uid,
                        title=title,
                        start_time=start,
                        end_time=end,
                        created_by="auto",
                    )
                )
                await session.commit()
            info = {
                "uid": uid,
                "title": title,
                "start": start,
                "end": end,
                "label": _slot_label(start, end),
            }
            created.append(info)
            _remember_action(chat_id, "created", info)

        if not created:
            log.warning(
                "series: trigger matched and %d candidate date(s) resolved, "
                "but nothing was booked — every slot was already taken "
                "(locally or on the calendar) for chat %s",
                len(dates),
                chat_id,
            )
            return None
        log.info(
            "created series of %d meeting(s) for chat %s", len(created), chat_id
        )
        # Diagnostic: read back the calendar over the series range and log
        # how many of our writes the server actually surfaces. A persistent
        # mismatch points at iCloud silently dropping events.
        try:
            first = created[0]["start"]
            last = created[-1]["end"]
            visible = await calendar_engine.get_events(
                first - timedelta(minutes=1), last + timedelta(minutes=1)
            )
            visible_uids = {e.get("uid") for e in visible if e.get("uid")}
            missing = [c["uid"] for c in created if c["uid"] not in visible_uids]
            log.info(
                "series verification: created=%d, visible on server=%d, "
                "missing=%s",
                len(created),
                len(visible_uids & {c["uid"] for c in created}),
                missing,
            )
        except Exception:  # noqa: BLE001
            log.exception("series verification readback failed")
        return {
            "created": created,
            "sender_name": sender_name,
        }
    except Exception:  # noqa: BLE001
        log.exception("process_series_proposal failed")
        return None
