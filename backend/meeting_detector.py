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
    "встретимся", "встреча", "встретиться", "созвонимся", "созвон",
    "звонок", "позвонить", "созвониться", "когда можешь", "свободен",
    "свободна", "когда удобно", "назначим", "назначить встречу",
    "let's meet", "lets meet", "call", "schedule", "when are you free",
    "можешь завтра", "можешь в", "давай в ", "как насчёт", "как насчет",
    "давай встретимся", "можем созвониться", "обсудим",
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


async def detect_meeting_intent(text: str) -> Optional[MeetingIntent]:
    """Return a :class:`MeetingIntent` if the message asks for a meeting/call,
    otherwise None."""
    if not text:
        return None
    text_lower = text.lower()
    if not any(kw in text_lower for kw in _KEYWORDS):
        return None

    time_match = _TIME_PATTERN.search(text_lower)
    date_match = None
    for pattern in _DATE_PATTERNS:
        m = pattern.search(text_lower)
        if m:
            date_match = m
            break

    return MeetingIntent(
        has_intent=True,
        suggested_time=time_match.group(0) if time_match else None,
        suggested_date=date_match.group(0) if date_match else None,
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
    "понедельник": 0, "вторник": 1, "среда": 2, "среду": 2, "четверг": 3,
    "пятница": 4, "пятницу": 4, "суббота": 5, "субботу": 5,
    "воскресенье": 6,
}


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
