"""Build a style profile from the user's own messages."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select

from .database import Message, SessionLocal, StyleProfile

EMOJI_RE = re.compile(
    "["  # broad emoji-ish ranges
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "]"
)
WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)
RU_STOP = {
    "и", "в", "не", "на", "с", "что", "это", "как", "так", "по", "у", "же",
    "к", "до", "из", "за", "от", "но", "а", "ну", "если", "для", "ещё", "ещё",
    "там", "вот", "тут", "ты", "я", "мы", "он", "она", "они", "меня", "тебе",
    "мне", "был", "была", "было", "были", "есть", "быть", "только", "потом",
    "уже", "его", "её", "их", "ли", "бы",
}
EN_STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "is",
    "are", "was", "were", "be", "for", "with", "as", "it", "this", "that",
    "i", "you", "we", "they", "he", "she", "my", "your", "our",
}


def _analyse(texts: list[str]) -> dict[str, Any]:
    if not texts:
        return {
            "messages_count": 0,
            "avg_length_chars": 0,
            "avg_length_words": 0,
            "emoji_percentage": 0,
            "common_phrases": [],
            "common_words": [],
            "punctuation": {},
            "ends_with_period_pct": 0,
            "uppercase_pct": 0,
        }
    total = len(texts)
    char_lens = [len(t) for t in texts]
    word_lens = [len(WORD_RE.findall(t)) for t in texts]
    emoji_count = sum(1 for t in texts if EMOJI_RE.search(t))
    period_count = sum(1 for t in texts if t.strip().endswith("."))
    upper_count = sum(1 for t in texts if t and t == t.upper() and any(c.isalpha() for c in t))
    punct = Counter()
    for t in texts:
        for c in t:
            if c in ".,!?…—-:;()":
                punct[c] += 1

    bigrams = Counter()
    word_freq = Counter()
    for t in texts:
        words = [w.lower() for w in WORD_RE.findall(t)]
        filtered = [w for w in words if w not in RU_STOP and w not in EN_STOP and len(w) > 2]
        word_freq.update(filtered)
        for i in range(len(filtered) - 1):
            bigrams[(filtered[i], filtered[i + 1])] += 1

    common_phrases = [
        {"phrase": " ".join(b), "count": c} for b, c in bigrams.most_common(15)
    ]
    common_words = [
        {"word": w, "count": c} for w, c in word_freq.most_common(20)
    ]

    return {
        "messages_count": total,
        "avg_length_chars": round(sum(char_lens) / total, 2),
        "avg_length_words": round(sum(word_lens) / total, 2),
        "emoji_percentage": round(emoji_count / total * 100, 2),
        "common_phrases": common_phrases,
        "common_words": common_words,
        "punctuation": dict(punct.most_common()),
        "ends_with_period_pct": round(period_count / total * 100, 2),
        "uppercase_pct": round(upper_count / total * 100, 2),
    }


async def reanalyze() -> dict[str, Any]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Message).where(Message.is_mine.is_(True)).order_by(Message.timestamp.desc()).limit(2000)
            )
        ).scalars().all()
        texts = [r.text for r in rows if r.text and r.text.strip()]
        profile = _analyse(texts)
        # Deactivate previous profiles
        prev = (await session.execute(select(StyleProfile))).scalars().all()
        for p in prev:
            p.is_active = False
        record = StyleProfile(
            profile_json=json.dumps(profile, ensure_ascii=False),
            messages_count=len(texts),
            is_active=True,
            created_at=datetime.utcnow(),
        )
        session.add(record)
        await session.commit()
    return profile


async def get_active_profile() -> dict[str, Any] | None:
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(StyleProfile).where(StyleProfile.is_active.is_(True)).order_by(
                    StyleProfile.id.desc()
                ).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        try:
            return json.loads(row.profile_json)
        except json.JSONDecodeError:
            return None


async def save_manual_profile(profile: dict[str, Any]) -> dict[str, Any]:
    async with SessionLocal() as session:
        prev = (await session.execute(select(StyleProfile))).scalars().all()
        for p in prev:
            p.is_active = False
        record = StyleProfile(
            profile_json=json.dumps(profile, ensure_ascii=False),
            messages_count=int(profile.get("messages_count", 0)),
            is_active=True,
            created_at=datetime.utcnow(),
        )
        session.add(record)
        await session.commit()
    return profile


async def sample_messages(limit: int = 20) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Message)
                .where(Message.is_mine.is_(True))
                .order_by(Message.timestamp.desc())
                .limit(limit)
            )
        ).scalars().all()
    return [
        {
            "id": r.id,
            "chat_name": r.chat_name,
            "text": r.text,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in rows
    ]
