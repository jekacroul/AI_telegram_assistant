"""DB-backed CalDAV configuration.

Connection credentials and behaviour toggles live in the ``settings`` table
so they can be edited from the web dashboard. ``.env`` values act as the
initial defaults.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_setting, set_setting

_TRUE = {"1", "true", "True", "yes", "on"}
_DEFAULT_WORK_DAYS = [0, 1, 2, 3, 4]


def _bool(value: str, default: bool) -> bool:
    if value == "":
        return default
    return value in _TRUE


def _int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_days(value: str) -> list[int]:
    if not value.strip():
        return list(_DEFAULT_WORK_DAYS)
    days: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            day = int(item)
        except ValueError:
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    return sorted(days) if days else list(_DEFAULT_WORK_DAYS)


async def get_caldav_config(session: AsyncSession) -> dict:
    """Return the full CalDAV configuration.

    Connection credentials always come from the environment (.env); only the
    behaviour settings are stored in / read from the DB.
    """
    return {
        # Connection — environment only, not editable from the dashboard.
        "caldav_url": settings.caldav_url,
        "caldav_username": settings.caldav_username,
        "caldav_password": settings.caldav_password,
        "caldav_calendar_name": settings.caldav_calendar_name,
        # Behaviour — editable from the dashboard, stored in the DB.
        "caldav_enabled": _bool(
            await get_setting(session, "caldav_enabled", ""),
            settings.caldav_enabled,
        ),
        "caldav_work_start": _int(
            await get_setting(session, "caldav_work_start", ""),
            settings.caldav_work_start,
        ),
        "caldav_work_end": _int(
            await get_setting(session, "caldav_work_end", ""),
            settings.caldav_work_end,
        ),
        "caldav_work_days": _parse_days(
            await get_setting(session, "caldav_work_days", "")
        ),
        "caldav_slot_duration": _int(
            await get_setting(session, "caldav_slot_duration", ""),
            settings.caldav_slot_duration,
        ),
        "caldav_lookahead_days": _int(
            await get_setting(session, "caldav_lookahead_days", ""),
            settings.caldav_lookahead_days,
        ),
        "caldav_propose_slots": _bool(
            await get_setting(session, "caldav_propose_slots", ""), True
        ),
        "caldav_auto_create": _bool(
            await get_setting(session, "caldav_auto_create", ""), True
        ),
        "caldav_notify": _bool(
            await get_setting(session, "caldav_notify", ""), True
        ),
    }


async def save_caldav_config(session: AsyncSession, payload: dict) -> None:
    """Persist the editable CalDAV behaviour settings.

    Connection credentials are intentionally NOT accepted here — they are
    configured exclusively through .env. Unknown / missing keys are skipped.
    """
    bool_keys = (
        "caldav_enabled", "caldav_propose_slots",
        "caldav_auto_create", "caldav_notify",
    )
    int_keys = (
        "caldav_work_start", "caldav_work_end",
        "caldav_slot_duration", "caldav_lookahead_days",
    )
    for key in bool_keys:
        if key in payload and payload[key] is not None:
            await set_setting(session, key, "1" if payload[key] else "0")
    for key in int_keys:
        if key in payload and payload[key] is not None:
            await set_setting(session, key, str(int(payload[key])))
    if "caldav_work_days" in payload and payload["caldav_work_days"] is not None:
        days = [
            str(int(d))
            for d in payload["caldav_work_days"]
            if 0 <= int(d) <= 6
        ]
        await set_setting(session, "caldav_work_days", ",".join(days))


async def apply_caldav_config_to_engine(session: AsyncSession) -> dict:
    """Load the DB config and push it onto the global ``calendar_engine``.

    Returns the loaded config dict.
    """
    from .calendar_engine import calendar_engine

    cfg = await get_caldav_config(session)
    calendar_engine.apply_config(cfg)
    return cfg


async def refresh_calendar_connection() -> bool:
    """Reload config and (re)connect the calendar engine when enabled.

    Returns the resulting connection state. Safe to call at any time.
    """
    from .calendar_engine import calendar_engine
    from .database import SessionLocal

    async with SessionLocal() as session:
        cfg = await apply_caldav_config_to_engine(session)
    if not cfg["caldav_enabled"]:
        calendar_engine.disconnect()
        return False
    return await calendar_engine.connect()
