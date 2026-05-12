from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_setting, set_setting

SETTING_SCHEDULE_ENABLED = "schedule_enabled"
SETTING_SCHEDULE_TIMEZONE = "schedule_timezone"
SETTING_SCHEDULE_DAYS = "schedule_days"
SETTING_SCHEDULE_START = "schedule_start"
SETTING_SCHEDULE_END = "schedule_end"

DEFAULT_SCHEDULE_ENABLED = False
DEFAULT_SCHEDULE_TIMEZONE = "Europe/Moscow"
DEFAULT_SCHEDULE_DAYS = [0, 1, 2, 3, 4, 5, 6]
DEFAULT_SCHEDULE_START = "09:00"
DEFAULT_SCHEDULE_END = "23:00"

ALLOWED_TIMEZONES = [
    "Europe/Moscow",
    "Asia/Yekaterinburg",
    "Asia/Novosibirsk",
    "Europe/Kiev",
    "Asia/Almaty",
]

DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass(frozen=True)
class ScheduleSettings:
    enabled: bool = DEFAULT_SCHEDULE_ENABLED
    timezone: str = DEFAULT_SCHEDULE_TIMEZONE
    days: tuple[int, ...] = tuple(DEFAULT_SCHEDULE_DAYS)
    start: str = DEFAULT_SCHEDULE_START
    end: str = DEFAULT_SCHEDULE_END


def _parse_bool(value: str, default: bool = False) -> bool:
    if value == "":
        return default
    return value in ("1", "true", "True", "yes", "on")


def _parse_days(value: str) -> tuple[int, ...]:
    if not value.strip():
        return tuple(DEFAULT_SCHEDULE_DAYS)
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
    return tuple(days) if days else tuple(DEFAULT_SCHEDULE_DAYS)


def parse_time(value: str) -> time:
    try:
        parsed = datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise ValueError("time must be in HH:MM format") from exc
    return parsed.replace(second=0, microsecond=0)


def validate_schedule_payload(
    *,
    enabled: bool,
    timezone: str,
    days: list[int],
    start: str,
    end: str,
) -> ScheduleSettings:
    if timezone not in ALLOWED_TIMEZONES:
        raise ValueError("unsupported timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone is not available") from exc
    unique_days = sorted(set(days))
    if any(day < 0 or day > 6 for day in unique_days):
        raise ValueError("days must be integers from 0 to 6")
    if not unique_days:
        raise ValueError("at least one day must be selected")
    parse_time(start)
    parse_time(end)
    return ScheduleSettings(
        enabled=enabled,
        timezone=timezone,
        days=tuple(unique_days),
        start=start,
        end=end,
    )


async def get_schedule_settings(session: AsyncSession) -> ScheduleSettings:
    enabled = _parse_bool(
        await get_setting(session, SETTING_SCHEDULE_ENABLED, "0"),
        DEFAULT_SCHEDULE_ENABLED,
    )
    timezone = await get_setting(
        session, SETTING_SCHEDULE_TIMEZONE, DEFAULT_SCHEDULE_TIMEZONE
    )
    if timezone not in ALLOWED_TIMEZONES:
        timezone = DEFAULT_SCHEDULE_TIMEZONE
    days = _parse_days(
        await get_setting(
            session,
            SETTING_SCHEDULE_DAYS,
            ",".join(map(str, DEFAULT_SCHEDULE_DAYS)),
        )
    )
    start = await get_setting(session, SETTING_SCHEDULE_START, DEFAULT_SCHEDULE_START)
    end = await get_setting(session, SETTING_SCHEDULE_END, DEFAULT_SCHEDULE_END)
    try:
        parse_time(start)
    except ValueError:
        start = DEFAULT_SCHEDULE_START
    try:
        parse_time(end)
    except ValueError:
        end = DEFAULT_SCHEDULE_END
    return ScheduleSettings(
        enabled=enabled, timezone=timezone, days=days, start=start, end=end
    )


async def save_schedule_settings(
    session: AsyncSession, schedule: ScheduleSettings
) -> None:
    await set_setting(
        session, SETTING_SCHEDULE_ENABLED, "1" if schedule.enabled else "0"
    )
    await set_setting(session, SETTING_SCHEDULE_TIMEZONE, schedule.timezone)
    await set_setting(session, SETTING_SCHEDULE_DAYS, ",".join(map(str, schedule.days)))
    await set_setting(session, SETTING_SCHEDULE_START, schedule.start)
    await set_setting(session, SETTING_SCHEDULE_END, schedule.end)


def _is_active_at(
    local_dt: datetime, days: tuple[int, ...], start: time, end: time
) -> bool:
    current_day = local_dt.weekday()
    current_time = local_dt.time().replace(second=0, microsecond=0)
    if start <= end:
        return current_day in days and start <= current_time < end
    previous_day = (current_day - 1) % 7
    return (current_day in days and current_time >= start) or (
        previous_day in days and current_time < end
    )


def schedule_status(schedule: ScheduleSettings, now: datetime | None = None) -> dict:
    tz = ZoneInfo(schedule.timezone)
    local_now = (now or datetime.now(tz)).astimezone(tz)
    start = parse_time(schedule.start)
    end = parse_time(schedule.end)
    active = (
        True
        if not schedule.enabled
        else _is_active_at(local_now, schedule.days, start, end)
    )
    next_window = (
        None
        if active
        else _next_window(local_now, schedule.days, start, schedule.timezone)
    )
    return {
        "active": active,
        "now": local_now.isoformat(),
        "next_active_at": next_window.isoformat() if next_window else None,
        "next_active_text": (
            _format_next_window(local_now, next_window) if next_window else ""
        ),
    }


def _next_window(
    local_now: datetime, days: tuple[int, ...], start: time, timezone: str
) -> datetime | None:
    tz = ZoneInfo(timezone)
    for offset in range(8):
        candidate_date = (local_now + timedelta(days=offset)).date()
        candidate = datetime.combine(candidate_date, start, tzinfo=tz)
        if candidate.weekday() in days and candidate > local_now:
            return candidate
    return None


def _format_next_window(local_now: datetime, next_window: datetime | None) -> str:
    if next_window is None:
        return ""
    today = local_now.date()
    target = next_window.date()
    at = next_window.strftime("%H:%M")
    if target == today:
        return f"сегодня в {at}"
    if target == today + timedelta(days=1):
        return f"завтра в {at}"
    return f"{DAY_LABELS[next_window.weekday()]} в {at}"


async def is_within_schedule(session: AsyncSession) -> bool:
    schedule = await get_schedule_settings(session)
    return bool(schedule_status(schedule)["active"])


def schedule_to_dict(schedule: ScheduleSettings) -> dict:
    return {
        "enabled": schedule.enabled,
        "timezone": schedule.timezone,
        "days": list(schedule.days),
        "start": schedule.start,
        "end": schedule.end,
        "allowed_timezones": ALLOWED_TIMEZONES,
        **schedule_status(schedule),
    }
