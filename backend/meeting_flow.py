"""Meeting confirmation flow.

Ties the meeting detector and the calendar engine together:

* after the bot proposes time slots in a reply, the offered slots are stored
  as a :class:`PendingMeeting`;
* when the contact later confirms ("да", "в среду", ...), the matching slot
  is turned into a real calendar event and the owner is notified.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select

from .calendar_engine import calendar_engine, slots_from_json, slots_to_json
from .caldav_config import get_caldav_config
from .database import CreatedMeeting, PendingMeeting, SessionLocal
from .meeting_detector import (
    detect_meeting_intent,
    looks_like_confirmation,
    matched_day_index,
)

log = logging.getLogger(__name__)


def _encode_proposal(meeting_type: str, slots: list[dict]) -> str:
    return json.dumps(
        {"meeting_type": meeting_type, "slots": json.loads(slots_to_json(slots))},
        ensure_ascii=False,
    )


def _decode_proposal(raw: str) -> tuple[str, list[dict]]:
    try:
        data = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return "meeting", []
    if isinstance(data, list):  # tolerate a bare slot list
        return "meeting", slots_from_json(raw)
    meeting_type = data.get("meeting_type", "meeting")
    slots = slots_from_json(json.dumps(data.get("slots", [])))
    return meeting_type, slots


def _event_title(meeting_type: str, sender_name: str) -> str:
    who = sender_name or "собеседником"
    return f"{'Звонок' if meeting_type == 'call' else 'Встреча'} с {who}"


async def maybe_record_pending_meeting(
    chat_id: int,
    message_id: Optional[int],
    sender_name: str,
    incoming_text: str,
) -> None:
    """If the contact's message asked for a meeting and the calendar is
    connected, store the free slots the bot just offered so a later
    confirmation can be matched. No-op when the feature is disabled."""
    if not calendar_engine.is_connected:
        return
    try:
        async with SessionLocal() as session:
            cfg = await get_caldav_config(session)
        if not cfg["caldav_propose_slots"]:
            return
        intent = await detect_meeting_intent(incoming_text)
        if not intent or not intent.has_intent:
            return
        slots = await calendar_engine.get_free_slots()
        if not slots:
            return
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
                        intent.meeting_type or "meeting", slots
                    ),
                    status="pending",
                )
            )
            await session.commit()
        log.info("recorded pending meeting for chat %s (%d slots)", chat_id, len(slots))
    except Exception:  # noqa: BLE001
        log.exception("maybe_record_pending_meeting failed")


async def process_incoming_confirmation(
    chat_id: int, sender_name: str, incoming_text: str
) -> Optional[dict]:
    """Check whether an incoming message confirms a pending meeting proposal.

    On a match, create the calendar event, persist a :class:`CreatedMeeting`
    and return event info; otherwise return None.
    """
    if not calendar_engine.is_connected:
        return None
    if not looks_like_confirmation(incoming_text):
        return None
    try:
        async with SessionLocal() as session:
            cfg = await get_caldav_config(session)
            if not cfg["caldav_auto_create"]:
                return None
            pending = (
                await session.execute(
                    select(PendingMeeting)
                    .where(
                        PendingMeeting.chat_id == chat_id,
                        PendingMeeting.status == "pending",
                    )
                    .order_by(PendingMeeting.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if not pending:
                return None
            pending_id = pending.id
            meeting_type, slots = _decode_proposal(pending.proposed_slots_json)

        if not slots:
            return None

        # Prefer a slot on the weekday the contact named, else the closest.
        day_idx = matched_day_index(incoming_text)
        chosen = None
        if day_idx is not None:
            chosen = next(
                (s for s in slots if s["start"].weekday() == day_idx), None
            )
        if chosen is None:
            chosen = slots[0]

        title = _event_title(meeting_type, sender_name)
        uid = await calendar_engine.create_event(
            title=title,
            start=chosen["start"],
            end=chosen["end"],
            description="Создано AI-ассистентом по подтверждению собеседника.",
        )
        if not uid:
            log.warning("confirmation matched but event creation failed")
            return None

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
                    start_time=chosen["start"],
                    end_time=chosen["end"],
                    created_by="auto",
                )
            )
            await session.commit()

        log.info("auto-created calendar event '%s' for chat %s", title, chat_id)
        return {
            "uid": uid,
            "title": title,
            "label": chosen["label"],
            "start": chosen["start"],
            "end": chosen["end"],
            "sender_name": sender_name,
        }
    except Exception:  # noqa: BLE001
        log.exception("process_incoming_confirmation failed")
        return None
