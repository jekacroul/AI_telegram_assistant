from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from statistics import mean
from typing import Optional

import emoji
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import Message, StyleProfile


GREETING_TOKENS = {
    "привет", "приветик", "здарова", "здаров", "хай", "yo", "hi", "hello",
    "доброе", "добрый", "ку", "хеллоу", "здравствуй", "здравствуйте",
}
FAREWELL_TOKENS = {
    "пока", "удачи", "всё", "все", "досвидания", "до свидания", "бай",
    "bye", "удачи", "ладно", "увидимся", "споки",
}

WORD_RE = re.compile(r"[\wа-яА-ЯёЁ]+", re.UNICODE)
URL_RE = re.compile(r"https?://\S+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def _emoji_count(text: str) -> int:
    return sum(1 for ch in text if ch in emoji.EMOJI_DATA)


def _detect_punct_style(texts: list[str]) -> str:
    if not texts:
        return "minimal"
    total = sum(len(t) for t in texts) or 1
    punct_chars = sum(t.count(c) for t in texts for c in ".,!?;:")
    ratio = punct_chars / total
    if ratio < 0.02:
        return "minimal"
    if ratio < 0.05:
        return "casual"
    return "formal"


def _detect_tone(texts: list[str], emoji_freq: float, punct_style: str) -> str:
    joined = " ".join(texts).lower()
    swears = ["блять", "бля", "хуй", "пиздец", "ебан", "ахуен"]
    has_swears = any(s in joined for s in swears)
    casual_markers = ["ок", "норм", "лол", "кек", "хах", "ахах"]
    casual_count = sum(joined.count(m) for m in casual_markers)
    if has_swears or (casual_count > 5 and emoji_freq > 0.1):
        return "casual_friendly"
    if punct_style == "formal" and emoji_freq < 0.05:
        return "formal_polite"
    if emoji_freq > 0.2:
        return "warm_emoji_heavy"
    return "casual_friendly"


def analyze_messages(texts: list[str], reply_delays_min: list[float]) -> dict:
    cleaned = [t.strip() for t in texts if t and t.strip()]
    if not cleaned:
        return {
            "avg_message_length": 0,
            "uses_emoji": False,
            "emoji_frequency": 0.0,
            "uses_lowercase": True,
            "punctuation_style": "minimal",
            "common_words": [],
            "greeting_patterns": [],
            "farewell_patterns": [],
            "tone": "casual_friendly",
            "avg_response_delay_minutes": 0.0,
            "samples": [],
        }

    lengths = [len(t) for t in cleaned]
    total_emoji = sum(_emoji_count(t) for t in cleaned)
    total_chars = sum(lengths) or 1
    emoji_freq = total_emoji / max(len(cleaned), 1)
    uses_emoji = total_emoji > 0

    lowercase_count = sum(1 for t in cleaned if t == t.lower())
    uses_lowercase = lowercase_count / len(cleaned) > 0.6

    punct_style = _detect_punct_style(cleaned)

    all_tokens = []
    for t in cleaned:
        all_tokens.extend(_tokenize(t))

    stop = {
        "и", "в", "не", "что", "на", "я", "с", "со", "а", "то", "как", "по",
        "это", "но", "ты", "у", "за", "от", "же", "так", "о", "о", "из",
        "для", "до", "ну", "ли", "вот", "бы", "если", "к", "или",
    }
    filtered = [w for w in all_tokens if len(w) >= 2 and w not in stop]
    common = [w for w, _ in Counter(filtered).most_common(20)]

    greetings = []
    farewells = []
    for t in cleaned:
        first_word = next(iter(_tokenize(t)), "")
        if first_word in GREETING_TOKENS:
            greetings.append(first_word)
        last_word = next(iter(reversed(_tokenize(t))), "")
        if last_word in FAREWELL_TOKENS:
            farewells.append(last_word)

    greeting_patterns = [w for w, _ in Counter(greetings).most_common(5)]
    farewell_patterns = [w for w, _ in Counter(farewells).most_common(5)]

    tone = _detect_tone(cleaned, emoji_freq, punct_style)
    avg_delay = round(mean(reply_delays_min), 2) if reply_delays_min else 0.0

    samples = cleaned[-5:]

    return {
        "avg_message_length": round(mean(lengths), 1),
        "uses_emoji": uses_emoji,
        "emoji_frequency": round(emoji_freq, 3),
        "uses_lowercase": uses_lowercase,
        "punctuation_style": punct_style,
        "common_words": common,
        "greeting_patterns": greeting_patterns,
        "farewell_patterns": farewell_patterns,
        "tone": tone,
        "avg_response_delay_minutes": avg_delay,
        "samples": samples,
    }


async def collect_my_messages(session: AsyncSession) -> tuple[list[str], list[float]]:
    result = await session.execute(
        select(Message).where(Message.is_mine == True).order_by(Message.timestamp)  # noqa: E712
    )
    mine = list(result.scalars().all())
    texts = [m.text for m in mine if m.text]

    result_all = await session.execute(select(Message).order_by(Message.timestamp))
    all_msgs = list(result_all.scalars().all())

    delays: list[float] = []
    by_chat: dict[int, list[Message]] = {}
    for m in all_msgs:
        by_chat.setdefault(m.chat_id, []).append(m)
    for chat_msgs in by_chat.values():
        for i, m in enumerate(chat_msgs):
            if not m.is_mine:
                continue
            for j in range(i - 1, -1, -1):
                prev = chat_msgs[j]
                if not prev.is_mine:
                    diff = (m.timestamp - prev.timestamp).total_seconds() / 60.0
                    if 0 < diff < 24 * 60:
                        delays.append(diff)
                    break
    return texts, delays


async def reanalyze_and_store(session: AsyncSession) -> dict:
    texts, delays = await collect_my_messages(session)
    profile = analyze_messages(texts, delays)
    record = StyleProfile(
        profile_json=json.dumps(profile, ensure_ascii=False),
        messages_count=len(texts),
        created_at=datetime.utcnow(),
    )
    session.add(record)
    await session.commit()
    return profile


async def get_latest_profile(session: AsyncSession) -> Optional[dict]:
    result = await session.execute(
        select(StyleProfile).order_by(StyleProfile.created_at.desc()).limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    try:
        return json.loads(row.profile_json)
    except json.JSONDecodeError:
        return None


async def save_manual_profile(session: AsyncSession, profile: dict) -> None:
    record = StyleProfile(
        profile_json=json.dumps(profile, ensure_ascii=False),
        messages_count=int(profile.get("messages_count", 0)),
        created_at=datetime.utcnow(),
    )
    session.add(record)
    await session.commit()
