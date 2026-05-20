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
)

log = logging.getLogger(__name__)

# A pending meeting older than this is considered a stale negotiation and is
# no longer matched against incoming times.
_PENDING_TTL = timedelta(hours=24)

# Substrings that signal a request to cancel meetings. "отмени" / "отменя"
# cover almost every conjugated form of отменить/отменять; "удали встреч" /
# "удали событ" require the object so generic "удали из чата" doesn't match.
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
            if not pending:
                return None
            pending_id = pending.id
            meeting_type, date_hint, _slots = _decode_proposal(
                pending.proposed_slots_json
            )

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

        # Don't book over an existing event.
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
        return {
            "uid": uid,
            "title": title,
            "label": label,
            "start": start,
            "end": end,
            "sender_name": sender_name,
        }
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
    # No specific date in the cancel clause — apply to all upcoming events
    # for this chat. In practice the contact is asking to drop the meeting
    # currently being negotiated, not random other days.
    return {"start": now, "end": horizon, "time": requested}


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
            return {"cancelled": [], "requested": True}

        cancelled: list[dict] = []
        for uid, title, start, end in scope_rows:
            ok = await calendar_engine.delete_event(uid, around=start)
            log.info("cancel event uid=%s '%s' -> %s", uid, title, ok)
            if ok:
                cancelled.append(
                    {
                        "title": title,
                        "start": start,
                        "end": end,
                        "uid": uid,
                        "label": _slot_label(start, end),
                    }
                )
        return {
            "cancelled": cancelled,
            "requested": True,
            "sender_name": sender_name,
        }
    except Exception:  # noqa: BLE001
        log.exception("process_cancellation failed")
        return None
