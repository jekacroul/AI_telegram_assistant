"""CalDAV calendar integration.

A thin async wrapper around the synchronous ``caldav`` library: every
network call is pushed onto a worker thread so the event loop is never
blocked. The single ``calendar_engine`` instance holds the live connection;
its configuration is loaded from DB settings (with .env values as the
fallback) via :func:`load_caldav_config`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from .config import settings

log = logging.getLogger(__name__)

# Surfaced when the optional CalDAV libraries are not installed.
_MISSING_DEPS_MSG = (
    "CalDAV-библиотеки не установлены. Выполните: "
    "pip install caldav icalendar python-dateutil pytz"
)

# Russian day/month names for human-readable slot labels.
DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DAYS_RU_FULL = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]
MONTHS_RU = [
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]


def _slot_label(start: datetime, end: datetime) -> str:
    """'Среда, 21 мая · 15:00 – 16:00'."""
    return (
        f"{DAYS_RU_FULL[start.weekday()]}, "
        f"{start.day} {MONTHS_RU[start.month - 1]} · "
        f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
    )


def _to_naive(value, tz=None) -> Optional[datetime]:
    """Coerce a CalDAV dtstart/dtend (date or datetime, aware or naive) into a
    tz-naive datetime so slot maths never mixes aware and naive values."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            if tz is not None:
                value = value.astimezone(tz)
            return value.replace(tzinfo=None)
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return None


class CalendarEngine:
    def __init__(self) -> None:
        self.client = None
        self.calendar = None
        self._connected = False
        self.last_error: str = ""
        # Live config — defaults come from .env, overridden by DB settings.
        self.url: str = settings.caldav_url
        self.username: str = settings.caldav_username
        self.password: str = settings.caldav_password
        self.calendar_name: str = settings.caldav_calendar_name
        self.lookahead_days: int = settings.caldav_lookahead_days
        self.slot_duration: int = settings.caldav_slot_duration
        self.work_start: int = settings.caldav_work_start
        self.work_end: int = settings.caldav_work_end
        # Workdays as weekday ints (Mon=0). Defaults to the working week.
        self.work_days: list[int] = [0, 1, 2, 3, 4]
        self.timezone_str: str = "Europe/Moscow"

    def apply_config(self, cfg: dict) -> None:
        """Update the live config from a dict (DB settings)."""
        self.url = cfg.get("caldav_url") or self.url
        self.username = cfg.get("caldav_username", self.username)
        self.password = cfg.get("caldav_password", self.password)
        self.calendar_name = cfg.get("caldav_calendar_name", self.calendar_name)
        self.lookahead_days = int(
            cfg.get("caldav_lookahead_days", self.lookahead_days)
        )
        self.slot_duration = int(
            cfg.get("caldav_slot_duration", self.slot_duration)
        )
        self.work_start = int(cfg.get("caldav_work_start", self.work_start))
        self.work_end = int(cfg.get("caldav_work_end", self.work_end))
        days = cfg.get("caldav_work_days")
        if days:
            self.work_days = sorted({int(d) for d in days if 0 <= int(d) <= 6})
        self.timezone_str = cfg.get("caldav_timezone", self.timezone_str)

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _connect_sync(
        self, url: str, username: str, password: str, calendar_name: str
    ) -> tuple[bool, str]:
        """Blocking connection routine — runs on a worker thread."""
        import caldav

        client = caldav.DAVClient(url=url, username=username, password=password)
        principal = client.principal()
        calendars = principal.calendars()
        if calendar_name:
            calendar = next(
                (c for c in calendars if c.name == calendar_name),
                calendars[0] if calendars else None,
            )
        else:
            calendar = calendars[0] if calendars else None
        if calendar is None:
            return False, "no calendars found on the server"
        self.client = client
        self.calendar = calendar
        return True, ""

    async def connect(self) -> bool:
        """Connect to the CalDAV server. Returns True if successful."""
        if not (self.url and self.username and self.password):
            self._connected = False
            self.last_error = "CalDAV credentials are not configured"
            return False
        try:
            ok, err = await asyncio.wait_for(
                asyncio.to_thread(
                    self._connect_sync,
                    self.url,
                    self.username,
                    self.password,
                    self.calendar_name,
                ),
                timeout=20.0,
            )
            self._connected = ok
            self.last_error = err
            if ok:
                log.info("CalDAV connected (%s)", self._calendar_display_name())
            else:
                log.warning("CalDAV connection failed: %s", err)
            return ok
        except asyncio.TimeoutError:
            self._connected = False
            self.last_error = "connection timed out"
            log.warning("CalDAV connection timed out; disabled for this session")
            return False
        except ModuleNotFoundError:
            self._connected = False
            self.last_error = _MISSING_DEPS_MSG
            log.error("CalDAV disabled: %s", _MISSING_DEPS_MSG)
            return False
        except Exception as e:  # noqa: BLE001
            self._connected = False
            self.last_error = str(e)
            log.error("CalDAV connection failed: %s", e)
            return False

    def _calendar_display_name(self) -> str:
        try:
            return str(self.calendar.name) if self.calendar else "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    def disconnect(self) -> None:
        self.client = None
        self.calendar = None
        self._connected = False

    def _raw_search(self, start: datetime, end: datetime):
        """Query the calendar for events in a range, tolerating API changes
        between ``caldav`` versions (``search`` is preferred, ``date_search``
        is the legacy fallback)."""
        try:
            return self.calendar.search(
                start=start, end=end, event=True, expand=True
            )
        except (TypeError, AttributeError):
            return self.calendar.date_search(start=start, end=end)

    def _get_events_sync(self, start: datetime, end: datetime) -> list[dict]:
        from icalendar import Calendar

        events = self._raw_search(start, end)
        result: list[dict] = []
        for event in events:
            try:
                cal = Calendar.from_ical(event.data)
            except Exception:  # noqa: BLE001
                continue
            for component in cal.walk():
                if component.name != "VEVENT":
                    continue
                dtstart_raw = component.get("dtstart")
                dtend_raw = component.get("dtend")
                if dtstart_raw is None:
                    continue
                dtstart = _to_naive(dtstart_raw.dt)
                dtend = _to_naive(dtend_raw.dt) if dtend_raw is not None else None
                if dtstart is None:
                    continue
                if dtend is None:
                    dtend = dtstart + timedelta(hours=1)
                result.append(
                    {
                        "title": str(component.get("summary", "Без названия")),
                        "start": dtstart,
                        "end": dtend,
                        "uid": str(component.get("uid", "")),
                    }
                )
        return sorted(result, key=lambda x: x["start"])

    async def get_events(self, start: datetime, end: datetime) -> list[dict]:
        """Get all events in the given (tz-naive) date range."""
        if not self._connected:
            return []
        try:
            return await asyncio.to_thread(self._get_events_sync, start, end)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to get events: %s", e)
            return []

    async def get_free_slots(
        self,
        days_ahead: Optional[int] = None,
        slot_duration_minutes: Optional[int] = None,
        work_start_hour: Optional[int] = None,
        work_end_hour: Optional[int] = None,
    ) -> list[dict]:
        """Find free time slots in working hours.

        Returns up to 5 of the closest slots, each a dict of
        ``{start, end, label}`` with a human-readable Russian label.
        """
        days = days_ahead or self.lookahead_days
        duration = slot_duration_minutes or self.slot_duration
        work_start = work_start_hour or self.work_start
        work_end = work_end_hour or self.work_end
        work_days = set(self.work_days)

        now = datetime.now()
        end_date = now + timedelta(days=days)

        events = await self.get_events(start=now, end=end_date)

        free_slots: list[dict] = []
        current = now.replace(minute=0, second=0, microsecond=0) + timedelta(
            hours=1
        )

        while current < end_date and len(free_slots) < 5:
            if current.weekday() not in work_days:
                current = (current + timedelta(days=1)).replace(
                    hour=work_start, minute=0
                )
                continue
            if current.hour < work_start:
                current = current.replace(hour=work_start, minute=0)
                continue
            if current.hour >= work_end:
                current = (current + timedelta(days=1)).replace(
                    hour=work_start, minute=0
                )
                continue

            slot_end = current + timedelta(minutes=duration)
            if slot_end.hour > work_end or (
                slot_end.hour == work_end and slot_end.minute > 0
            ):
                current += timedelta(hours=1)
                continue

            has_conflict = any(
                isinstance(e["start"], datetime)
                and isinstance(e["end"], datetime)
                and not (slot_end <= e["start"] or current >= e["end"])
                for e in events
            )
            if not has_conflict:
                free_slots.append(
                    {
                        "start": current,
                        "end": slot_end,
                        "label": _slot_label(current, slot_end),
                    }
                )
            current += timedelta(minutes=30)

        return free_slots[:5]

    def _create_event_sync(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: str,
        location: str,
        uid: str,
    ) -> None:
        from icalendar import Calendar, Event

        cal = Calendar()
        cal.add("prodid", "-//AI Assistant//caldav//")
        cal.add("version", "2.0")
        event = Event()
        event.add("summary", title)
        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("description", description)
        if location:
            event.add("location", location)
        event.add("uid", uid)
        cal.add_component(event)
        self.calendar.save_event(cal.to_ical())

    async def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        description: str = "",
        location: str = "",
    ) -> Optional[str]:
        """Create a new calendar event. Returns the event UID, or None on failure."""
        if not self._connected:
            return None
        uid = str(uuid.uuid4())
        try:
            await asyncio.to_thread(
                self._create_event_sync,
                title,
                start,
                end,
                description,
                location,
                uid,
            )
            return uid
        except Exception as e:  # noqa: BLE001
            log.error("Failed to create event: %s", e)
            return None

    def _delete_event_sync(
        self, uid: str, around: Optional[datetime] = None
    ) -> bool:
        """Try the direct UID lookup first; on any failure (some servers do
        not expose UID-search reliably), scan events in a window around
        ``around`` and match the VEVENT's UID property by hand."""
        from icalendar import Calendar as ICal

        try:
            event = self.calendar.event_by_uid(uid)
            if event is not None:
                event.delete()
                return True
        except Exception as exc:  # noqa: BLE001
            log.info("event_by_uid('%s') failed (%s); falling back to scan", uid, exc)

        if around is not None:
            window_start = around - timedelta(days=1)
            window_end = around + timedelta(days=1)
        else:
            window_start = datetime.now() - timedelta(days=30)
            window_end = datetime.now() + timedelta(days=180)
        try:
            events = self._raw_search(window_start, window_end)
        except Exception as exc:  # noqa: BLE001
            log.error("scan for uid='%s' failed: %s", uid, exc)
            return False
        for event in events:
            try:
                cal = ICal.from_ical(event.data)
            except Exception:  # noqa: BLE001
                continue
            for component in cal.walk():
                if (
                    component.name == "VEVENT"
                    and str(component.get("uid", "")) == uid
                ):
                    event.delete()
                    return True
        log.warning("delete_event: uid '%s' not found in calendar", uid)
        return False

    async def delete_event(
        self, uid: str, around: Optional[datetime] = None
    ) -> bool:
        """Delete a calendar event by UID. ``around`` narrows the fallback
        search window when the direct UID lookup is not supported."""
        if not self._connected or not uid:
            return False
        try:
            return await asyncio.to_thread(self._delete_event_sync, uid, around)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to delete event %s: %s", uid, e)
            return False

    async def get_status(self) -> dict:
        """Return connection status and basic calendar info."""
        if not self._connected:
            return {
                "connected": False,
                "calendar": None,
                "events_today": 0,
                "next_event": None,
                "error": self.last_error,
            }
        try:
            today_start = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            today_end = today_start + timedelta(days=1)
            events = await self.get_events(today_start, today_end)
            upcoming = await self.get_events(
                datetime.now(), datetime.now() + timedelta(days=14)
            )
            next_event = upcoming[0] if upcoming else None
            return {
                "connected": True,
                "calendar": self._calendar_display_name(),
                "events_today": len(events),
                "next_event": next_event,
            }
        except Exception:  # noqa: BLE001
            return {
                "connected": False,
                "calendar": None,
                "events_today": 0,
                "next_event": None,
            }


calendar_engine = CalendarEngine()


def slots_to_json(slots: list[dict]) -> str:
    """Serialize free-slot dicts (with datetime values) to a JSON string."""
    return json.dumps(
        [
            {
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat(),
                "label": s["label"],
            }
            for s in slots
        ],
        ensure_ascii=False,
    )


def slots_from_json(raw: str) -> list[dict]:
    """Inverse of :func:`slots_to_json`."""
    try:
        items = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    out: list[dict] = []
    for it in items:
        try:
            out.append(
                {
                    "start": datetime.fromisoformat(it["start"]),
                    "end": datetime.fromisoformat(it["end"]),
                    "label": it["label"],
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out
