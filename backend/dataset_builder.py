"""Build LoRA training pairs from collected Telegram messages."""
from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .config import get_settings
from .database import Message, SessionLocal, TrainingPair, TrainingRun

log = logging.getLogger(__name__)

REPLY_WINDOW = timedelta(minutes=30)
MIN_OUTPUT_WORDS = 3

URL_RE = re.compile(r"https?://\S+")
INSTRUCTION = "Reply to this Telegram message in my style"


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _is_link_only(text: str) -> bool:
    stripped = URL_RE.sub("", text).strip(" \n\t-—.,")
    return not stripped


def _is_forward(text: str) -> bool:
    return text.lstrip().startswith(("Forwarded from", "Переслано от", ">>"))


async def build_pairs(
    persist: bool = True, source: str = "auto"
) -> dict[str, Any]:
    """Find (incoming_message, my_reply) pairs and persist them as TrainingPair rows."""
    async with SessionLocal() as session:
        messages = (
            await session.execute(select(Message).order_by(Message.chat_id, Message.timestamp))
        ).scalars().all()

        existing = (
            await session.execute(select(TrainingPair.input_text, TrainingPair.output_text))
        ).all()
        seen = {(row[0], row[1]) for row in existing}

        new_pairs: list[TrainingPair] = []
        avg_lengths: list[int] = []
        date_min = None
        date_max = None

        by_chat: dict[int, list[Message]] = {}
        for msg in messages:
            by_chat.setdefault(msg.chat_id, []).append(msg)

        for chat_id, msgs in by_chat.items():
            msgs.sort(key=lambda m: m.timestamp)
            for idx, msg in enumerate(msgs):
                if not msg.is_mine:
                    continue
                if _word_count(msg.text) < MIN_OUTPUT_WORDS:
                    continue
                if _is_link_only(msg.text) or _is_forward(msg.text):
                    continue

                incoming: Message | None = None
                if msg.reply_to_message_id:
                    for prev in reversed(msgs[:idx]):
                        if prev.message_id == msg.reply_to_message_id and not prev.is_mine:
                            incoming = prev
                            break
                if incoming is None:
                    for prev in reversed(msgs[:idx]):
                        if prev.is_mine:
                            break
                        if msg.timestamp - prev.timestamp <= REPLY_WINDOW:
                            incoming = prev
                            break
                        else:
                            break
                if incoming is None:
                    continue
                if _is_link_only(incoming.text) or _is_forward(incoming.text):
                    continue
                key = (incoming.text.strip(), msg.text.strip())
                if key in seen:
                    continue
                seen.add(key)
                pair = TrainingPair(
                    input_text=key[0],
                    output_text=key[1],
                    chat_id=chat_id,
                    timestamp=msg.timestamp,
                    source=source,
                )
                new_pairs.append(pair)
                avg_lengths.append(_word_count(msg.text))
                date_min = msg.timestamp if date_min is None else min(date_min, msg.timestamp)
                date_max = msg.timestamp if date_max is None else max(date_max, msg.timestamp)

        if persist and new_pairs:
            session.add_all(new_pairs)
            await session.commit()

        total_pairs = (
            await session.execute(select(TrainingPair))
        ).scalars().all()

    stats = {
        "new_pairs": len(new_pairs),
        "total_pairs": len(total_pairs),
        "avg_reply_words": round(sum(avg_lengths) / len(avg_lengths), 2) if avg_lengths else 0.0,
        "date_min": date_min.isoformat() if date_min else None,
        "date_max": date_max.isoformat() if date_max else None,
    }
    log.info("dataset_builder stats: %s", stats)
    return stats


async def export_jsonl(version: int | None = None) -> dict[str, Any]:
    """Export all unused-or-all training pairs to training_data/dataset_v{N}.jsonl."""
    settings = get_settings()
    out_dir = Path(settings.training_data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if version is None:
        async with SessionLocal() as session:
            existing_runs = (await session.execute(select(TrainingRun))).scalars().all()
            version = (max((r.version for r in existing_runs), default=0) or 0) + 1
    path = out_dir / f"dataset_v{version}.jsonl"

    async with SessionLocal() as session:
        pairs = (await session.execute(select(TrainingPair))).scalars().all()

    with path.open("w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(
                json.dumps(
                    {
                        "instruction": INSTRUCTION,
                        "input": pair.input_text,
                        "output": pair.output_text,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return {"path": str(path), "count": len(pairs), "version": version}
