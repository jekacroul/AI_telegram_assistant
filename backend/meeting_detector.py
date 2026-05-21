"""Detect meeting/call intent in an incoming message and extract hints."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class MeetingIntent:
    has_intent: bool
    suggested_time: Optional[str] = None
    suggested_date: Optional[str] = None
    meeting_type: Optional[str] = None  # "call" | "meeting"


_KEYWORDS = [
    # Stems cover all inflections: "встреч" -> встреча / встречу / встречи /
    # встречаться / встретиться-friendly; "встрет" -> встретимся / встретить /
    # встретиться; "созвон" -> созвонимся / созвониться / созвон.
    "встрет", "встреч", "созвон",
    "звонок", "позвонить",
    "когда можешь", "свободен", "свободна", "когда удобно",
    "назначим", "назначить встречу",
    "let's meet", "lets meet", "call", "schedule", "when are you free",
    "можешь завтра", "можешь в", "давай в ", "как насчёт", "как насчет",
    "можем созвониться", "обсудим",
    "погуляем", "погулять", "гулять", "прогуляемся", "прогулка",
    "пройдёмся", "пройдемся", "сходим", "увидимся", "повидаемся",
    "пересечёмся", "пересечемся", "выпьем кофе", "на кофе",
    "видеться", "видимся",
    # Rescheduling — should re-open a meeting negotiation on the new day.
    "перенес",
    # Calendar-availability questions — surface real events to the bot.
    "какие планы", "планы на", "календар", "график", "занят ли",
    "ты занят", "ты занята",
]

_CALL_WORDS = ["звонок", "созвон", "созвониться", "созвонимся", "позвонить", "call"]

_TIME_PATTERN = re.compile(
    r"\b(\d{1,2})[:.](\d{2})\b|\b(\d{1,2})\s*(утра|дня|вечера|ночи)\b"
)
_DATE_PATTERNS = [
    re.compile(r"\b(сегодня|завтра|послезавтра)\b"),
    re.compile(
        r"\b(понедельник|вторник|среду?|четверг|пятницу?|субботу?|"
        r"воскресенье)\b"
    ),
    re.compile(r"\b(\d{1,2})[./](\d{1,2})\b"),
]


# User-defined keyword phrases, loaded from the DB at startup and refreshed
# whenever the dashboard saves changes. Stored lowercased.
_user_keywords: list[str] = []


def set_user_keywords(phrases: list[str]) -> None:
    """Replace the user-defined keyword list with the given phrases."""
    global _user_keywords
    cleaned: list[str] = []
    for p in phrases or []:
        if not isinstance(p, str):
            continue
        s = p.strip().lower()
        if s and s not in cleaned:
            cleaned.append(s)
    _user_keywords = cleaned


def get_user_keywords() -> list[str]:
    return list(_user_keywords)


def get_builtin_keywords() -> list[str]:
    """Read-only copy of the built-in keyword list (for display in the UI)."""
    return list(_KEYWORDS)


async def detect_meeting_intent(text: str) -> Optional[MeetingIntent]:
    """Return a :class:`MeetingIntent` if the message asks for a meeting/call,
    otherwise None. Both built-in and user-defined keywords are checked."""
    if not text:
        return None
    text_lower = text.lower()
    if not any(kw in text_lower for kw in _KEYWORDS) and not any(
        kw in text_lower for kw in _user_keywords
    ):
        return None

    time_match = _TIME_PATTERN.search(text_lower)
    # In a reschedule ("перенеси с X на Y") the meaningful date is the new
    # one — Y, after "на" — not the first date in the message (which is
    # the OLD date X). Fall back to the first-match heuristic otherwise.
    suggested_date: Optional[str] = reschedule_target_date(text)
    if not suggested_date:
        for pattern in _DATE_PATTERNS:
            m = pattern.search(text_lower)
            if m:
                suggested_date = m.group(0)
                break

    return MeetingIntent(
        has_intent=True,
        suggested_time=time_match.group(0) if time_match else None,
        suggested_date=suggested_date,
        meeting_type=(
            "call"
            if any(w in text_lower for w in _CALL_WORDS)
            else "meeting"
        ),
    )


_CONFIRM_WORDS = [
    "да", "ок", "окей", "давай", "давайте", "подходит", "подойдёт",
    "подойдет", "хорошо", "договорились", "согласен", "согласна",
    "удобно", "идёт", "идет", "конечно", "отлично", "годится", "ага",
    "yes", "ok", "okay", "sure",
]

_DAY_TOKENS = {
    "понедельник": 0, "понедельника": 0,
    "вторник": 1, "вторника": 1,
    "среда": 2, "среду": 2, "среды": 2,
    "четверг": 3, "четверга": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4,
    "суббота": 5, "субботу": 5, "субботы": 5,
    "воскресенье": 6, "воскресенья": 6,
}


_RESCHEDULE_DATE_BODY = (
    r"("
    r"сегодня|завтра|послезавтра|"
    r"понедельник[ауые]?|вторник[ауые]?|сред[ауые]?|четверг[ауые]?|"
    r"пятниц[ауые]?|суббот[ауые]?|воскресень[ея]?|"
    r"\d{1,2}\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа"
    r"|сентября|октября|ноября|декабря)|"
    r"\d{1,2}\s*числа|\d{1,2}\s*-?\s*го|"
    r"\d{1,2}[./]\d{1,2}"
    r")"
)


def _reschedule_date_after(text: str, preposition: str) -> Optional[str]:
    """In a 'перенес...' phrase, return the date that follows the given
    preposition ("на" for the new date, "с" for the old)."""
    if not text or "перенес" not in text.lower():
        return None
    m = re.search(
        rf"\b{preposition}\s+{_RESCHEDULE_DATE_BODY}\b",
        text.lower(),
    )
    return m.group(1) if m else None


def reschedule_target_date(text: str) -> Optional[str]:
    """The new date in 'перенеси ... на <date>'."""
    return _reschedule_date_after(text, "на")


def reschedule_source_date(text: str) -> Optional[str]:
    """The old date in 'перенеси с <date> на ...'."""
    return _reschedule_date_after(text, "с")


def looks_like_confirmation(text: str) -> bool:
    """True if the message reads like a short agreement to a proposal."""
    if not text:
        return False
    cleaned = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    if not cleaned:
        return False
    tokens = cleaned.split()
    # Short messages dominated by a confirmation word, or any day name.
    if len(tokens) <= 6 and any(tok in _CONFIRM_WORDS for tok in tokens):
        return True
    if any(tok in _DAY_TOKENS for tok in tokens):
        return True
    return False


def matched_day_index(text: str) -> Optional[int]:
    """Return the weekday index (Mon=0) named in the text, if any."""
    if not text:
        return None
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    for tok in cleaned.split():
        if tok in _DAY_TOKENS:
            return _DAY_TOKENS[tok]
    return None


# Hours spelled out in words ("в три" → 3, "в двенадцать" → 12).
_HOUR_WORD_MAP = {
    "час": 1,
    "два": 2, "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}
# Longest-first so "двенадцать" is matched before its prefix "два".
_HOUR_WORD_ALT = "|".join(
    sorted((re.escape(w) for w in _HOUR_WORD_MAP), key=len, reverse=True)
)
# A clock hour written as 1–2 digits or spelled out in words.
_HOUR_TOKEN = rf"(\d{{1,2}}|{_HOUR_WORD_ALT})"

# Ordered most-specific first: an explicit "HH:MM" beats a bare "в HH".
_REQ_TIME_PATTERNS = [
    re.compile(r"\b(\d{1,2})[:.](\d{2})\b"),
    re.compile(rf"\bв\s+{_HOUR_TOKEN}\s*час"),
    re.compile(rf"\b{_HOUR_TOKEN}\s*час"),
    re.compile(rf"\bв\s+{_HOUR_TOKEN}\b"),
]

# Part-of-day words that move a 1–12 hour into 24-hour form.
_DAYPART_RE = re.compile(
    r"\b(утра|утром|дня|днём|днем|вечера|вечером|ночи|ночью)\b"
)


def _token_to_hour(token: str) -> Optional[int]:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return _HOUR_WORD_MAP.get(token)


def _apply_daypart(hour: int, text: str) -> int:
    """Shift a 1–12 hour into 24-hour form using a part-of-day word
    ("6 вечера" → 18, "час дня" → 13). Hours already in 24-hour form, and
    times with no part-of-day word, are returned unchanged."""
    if hour > 12:
        return hour
    m = _DAYPART_RE.search(text)
    if not m:
        return hour
    if m.group(1) in ("утра", "утром", "ночи", "ночью"):
        return 0 if hour == 12 else hour
    # дня / вечера — afternoon and evening map 1–11 onto 13–23.
    return hour if hour == 12 else hour + 12


def extract_requested_time(text: str) -> Optional[tuple[int, int]]:
    """Return an explicit clock time (hour, minute) named in the text.

    Recognises digits ("в 12 часов", "в 12", "12:00", "15.30"), hours
    spelled out in words ("в три", "в двенадцать", "в час дня") and the
    nouns "полдень" / "полночь". A part-of-day word shifts a 1–12 hour
    into 24-hour form ("в 6 вечера" → 18:00). Minute defaults to 0 when
    not stated. Returns None when no time is mentioned.
    """
    if not text:
        return None
    low = text.lower()

    if re.search(r"\bполдень\b|\bполдня\b", low):
        return (12, 0)
    if re.search(r"\bполночь\b", low):
        return (0, 0)

    for pattern in _REQ_TIME_PATTERNS:
        m = pattern.search(low)
        if not m:
            continue
        hour = _token_to_hour(m.group(1))
        if hour is None:
            continue
        minute = 0
        if pattern.groups >= 2 and m.group(2):
            minute = int(m.group(2))
        hour = _apply_daypart(hour, low)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return (hour, minute)
    return None


# An explicit time span: "с 18 до 19", "с 12:00 до 12:05", "с трёх до
# пяти". A trailing "час(ов)" after the start is tolerated.
_RANGE_RE = re.compile(
    rf"\bс\s+{_HOUR_TOKEN}(?:[:.](\d{{2}}))?\s*(?:час\w*\s+)?"
    rf"до\s+{_HOUR_TOKEN}(?:[:.](\d{{2}}))?"
)


def extract_time_range(
    text: str,
) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """Return ((start_h, start_m), (end_h, end_m)) for an explicit span
    like "с 18 до 19" or "с 12:00 до 12:05". A part-of-day word applies to
    both ends ("с 6 до 8 вечера" → 18:00–20:00). Returns None when the
    text names no such range."""
    if not text:
        return None
    low = text.lower()
    m = _RANGE_RE.search(low)
    if not m:
        return None
    start_h = _token_to_hour(m.group(1))
    end_h = _token_to_hour(m.group(3))
    if start_h is None or end_h is None:
        return None
    start_m = int(m.group(2)) if m.group(2) else 0
    end_m = int(m.group(4)) if m.group(4) else 0
    start_h = _apply_daypart(start_h, low)
    end_h = _apply_daypart(end_h, low)
    if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
        return None
    if not (0 <= start_m <= 59 and 0 <= end_m <= 59):
        return None
    return ((start_h, start_m), (end_h, end_m))

