from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import Message, TrainingPair


URL_ONLY_RE = re.compile(r"^\s*https?://\S+\s*$")
INSTRUCTION = "Ответь на сообщение в моём стиле"
PAIR_WINDOW = timedelta(minutes=30)


def _is_valid_reply(text: str) -> bool:
    if not text or not text.strip():
        return False
    if URL_ONLY_RE.match(text):
        return False
    if text.startswith("/"):
        return False
    words = [w for w in text.split() if w.strip()]
    if len(words) < 3:
        return False
    return True


def _is_valid_input(text: str) -> bool:
    if not text or not text.strip():
        return False
    if text.startswith("/"):
        return False
    return True


async def collect_pairs(session: AsyncSession) -> list[dict]:
    result = await session.execute(select(Message).order_by(Message.chat_id, Message.timestamp))
    msgs = list(result.scalars().all())

    by_chat: dict[int, list[Message]] = {}
    for m in msgs:
        by_chat.setdefault(m.chat_id, []).append(m)

    pairs: list[dict] = []
    for chat_msgs in by_chat.values():
        for i, m in enumerate(chat_msgs):
            if not m.is_mine or not _is_valid_reply(m.text):
                continue
            for j in range(i - 1, -1, -1):
                prev = chat_msgs[j]
                if prev.is_mine:
                    continue
                if (m.timestamp - prev.timestamp) > PAIR_WINDOW:
                    break
                if not _is_valid_input(prev.text):
                    break
                pairs.append({
                    "instruction": INSTRUCTION,
                    "input": prev.text,
                    "output": m.text,
                    "chat_id": m.chat_id,
                    "timestamp": m.timestamp.isoformat(),
                })
                break
    return pairs


async def upsert_training_pairs(session: AsyncSession, pairs: list[dict]) -> int:
    existing = await session.execute(select(TrainingPair))
    existing_set = {
        (p.input_text, p.output_text) for p in existing.scalars().all()
    }
    new_count = 0
    for p in pairs:
        key = (p["input"], p["output"])
        if key in existing_set:
            continue
        session.add(TrainingPair(
            input_text=p["input"],
            output_text=p["output"],
            chat_id=p["chat_id"],
            timestamp=datetime.fromisoformat(p["timestamp"]),
        ))
        existing_set.add(key)
        new_count += 1
    if new_count:
        await session.commit()
    return new_count


def _next_version(directory: Path) -> int:
    n = 1
    while (directory / f"dataset_v{n}.jsonl").exists():
        n += 1
    return n


async def build_dataset_file(session: AsyncSession) -> dict:
    pairs = await collect_pairs(session)
    await upsert_training_pairs(session, pairs)

    if not pairs:
        return {
            "total_pairs": 0,
            "avg_input_len": 0,
            "avg_output_len": 0,
            "date_range": None,
            "path": None,
            "version": None,
        }

    directory = settings.training_data_dir
    version = _next_version(directory)
    out_path = directory / f"dataset_v{version}.jsonl"

    with out_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps({
                "instruction": p["instruction"],
                "input": p["input"],
                "output": p["output"],
            }, ensure_ascii=False))
            f.write("\n")

    timestamps = [datetime.fromisoformat(p["timestamp"]) for p in pairs]
    return {
        "total_pairs": len(pairs),
        "avg_input_len": round(mean(len(p["input"]) for p in pairs), 1),
        "avg_output_len": round(mean(len(p["output"]) for p in pairs), 1),
        "date_range": {
            "from": min(timestamps).isoformat(),
            "to": max(timestamps).isoformat(),
        },
        "path": str(out_path),
        "version": version,
    }


def latest_dataset_path() -> Optional[Path]:
    directory = settings.training_data_dir
    candidates = sorted(directory.glob("dataset_v*.jsonl"))
    return candidates[-1] if candidates else None


async def dataset_stats(session: AsyncSession) -> dict:
    result = await session.execute(select(TrainingPair))
    pairs = list(result.scalars().all())
    latest = latest_dataset_path()
    return {
        "total_pairs": len(pairs),
        "latest_dataset": str(latest) if latest else None,
        "avg_input_len": round(mean(len(p.input_text) for p in pairs), 1) if pairs else 0,
        "avg_output_len": round(mean(len(p.output_text) for p in pairs), 1) if pairs else 0,
    }
