from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import Message, SessionLocal, TrainingPair


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


# ---------------------------------------------------------------------------
# Telegram Desktop export import
# ---------------------------------------------------------------------------


@dataclass
class TelegramExportResult:
    total_pairs: int
    chats_count: int
    date_range: Optional[dict]
    sample_pairs: list
    output_path: Optional[str]
    avg_reply_length: int = 0
    top_chats: list = field(default_factory=list)


@dataclass
class DatasetConfig:
    use_bot_pairs: bool = True
    bot_weight: float = 0.3
    telegram_export_paths: list = field(default_factory=list)
    export_weight: float = 0.7
    total_max_samples: int = 10000
    shuffle: bool = True


@dataclass
class CombinedDatasetResult:
    total_pairs: int
    breakdown: dict
    output_path: Optional[str]
    warnings: list


def _flatten_text(value) -> str:
    """Telegram stores text as a plain string or a list of fragments/entities."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


def _parse_export_date(msg: dict) -> Optional[datetime]:
    unixtime = msg.get("date_unixtime")
    if unixtime:
        try:
            return datetime.utcfromtimestamp(int(unixtime))
        except (ValueError, TypeError, OverflowError):
            pass
    raw = msg.get("date")
    if raw:
        try:
            return datetime.fromisoformat(str(raw))
        except ValueError:
            pass
    return None


def _export_chats(data) -> list[dict]:
    if not isinstance(data, dict):
        return []
    chats_obj = data.get("chats")
    if isinstance(chats_obj, dict) and isinstance(chats_obj.get("list"), list):
        return [c for c in chats_obj["list"] if isinstance(c, dict)]
    if isinstance(data.get("messages"), list):
        return [data]
    return []


def _owner_from_id(data: dict, chats: list[dict]) -> Optional[str]:
    info = data.get("personal_information") or {}
    uid = info.get("user_id")
    if uid is not None:
        return f"user{uid}"
    # Heuristic fallback: the owner is the participant present in most chats.
    counts: dict[str, int] = {}
    for chat in chats:
        seen: set[str] = set()
        for m in chat.get("messages", []):
            if isinstance(m, dict) and m.get("from_id"):
                seen.add(str(m["from_id"]))
        for fid in seen:
            counts[fid] = counts.get(fid, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return None


def _is_valid_export_reply(text: str, min_reply_length: int) -> bool:
    if not text or not text.strip():
        return False
    if URL_ONLY_RE.match(text):
        return False
    if text.startswith("/"):
        return False
    words = [w for w in text.split() if w.strip()]
    if len(words) < min_reply_length:
        return False
    return True


def _parse_export_pairs(
    json_path: str,
    min_reply_length: int,
    max_delay_minutes: int,
) -> dict:
    path = Path(json_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {json_path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    chats = _export_chats(data)
    if not chats:
        raise ValueError("В файле нет чатов — это не экспорт Telegram")

    owner_id = _owner_from_id(data, chats)
    delay = timedelta(minutes=max_delay_minutes)

    pairs: list[dict] = []
    per_chat: dict[str, int] = {}

    for chat in chats:
        ctype = chat.get("type") or ""
        cname = chat.get("name") or "Без названия"
        # Skip the owner's "Saved Messages" (owner-to-owner).
        if ctype == "saved_messages" or cname == "Saved Messages":
            continue

        norm: list[dict] = []
        for m in chat.get("messages", []):
            if not isinstance(m, dict) or m.get("type") != "message":
                continue
            ts = _parse_export_date(m)
            if ts is None:
                continue
            norm.append({
                "ts": ts,
                "owner": owner_id is not None and str(m.get("from_id")) == owner_id,
                "forwarded": "forwarded_from" in m,
                "sticker": m.get("media_type") == "sticker" or bool(m.get("sticker_emoji")),
                "text": _flatten_text(m.get("text")),
            })

        for i, m in enumerate(norm):
            if not m["owner"] or m["forwarded"] or m["sticker"]:
                continue
            if not _is_valid_export_reply(m["text"], min_reply_length):
                continue
            for j in range(i - 1, -1, -1):
                prev = norm[j]
                if prev["owner"]:
                    continue
                if (m["ts"] - prev["ts"]) > delay:
                    break
                if prev["forwarded"] or prev["sticker"]:
                    break
                if not _is_valid_input(prev["text"]):
                    break
                pairs.append({
                    "instruction": INSTRUCTION,
                    "input": prev["text"],
                    "output": m["text"],
                    "chat": cname,
                    "timestamp": m["ts"],
                })
                per_chat[cname] = per_chat.get(cname, 0) + 1
                break

    return {"pairs": pairs, "per_chat": per_chat, "chats_count": len(per_chat)}


def validate_telegram_export(file_path: str) -> dict:
    """Check that file_path is a readable, valid Telegram Desktop export."""
    path = Path(file_path)
    if not path.exists():
        return {"valid": False, "error": "Файл не найден", "format_version": None}
    if not path.is_file():
        return {"valid": False, "error": "Путь не является файлом", "format_version": None}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return {"valid": False, "error": f"Невалидный JSON: {exc}", "format_version": None}
    except OSError as exc:
        return {"valid": False, "error": f"Не удалось прочитать файл: {exc}", "format_version": None}

    if not isinstance(data, dict):
        return {"valid": False, "error": "Не похоже на экспорт Telegram", "format_version": None}
    chats = _export_chats(data)
    if not chats:
        return {
            "valid": False,
            "error": "В файле нет чатов — выгрузи личные сообщения в формате JSON",
            "format_version": None,
        }
    fmt = "telegram-desktop-full" if data.get("personal_information") else "telegram-desktop"
    return {"valid": True, "error": None, "format_version": fmt}


async def parse_telegram_export(
    json_path: str,
    min_reply_length: int = 3,
    max_delay_minutes: int = 30,
) -> TelegramExportResult:
    """Parse a Telegram Desktop export (result.json) into Alpaca-style pairs."""
    parsed = _parse_export_pairs(json_path, min_reply_length, max_delay_minutes)
    pairs = parsed["pairs"]

    digest = hashlib.md5(str(json_path).encode("utf-8")).hexdigest()[:10]
    out_path = settings.training_data_dir / f"tg_export_{digest}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps({
                "instruction": p["instruction"],
                "input": p["input"],
                "output": p["output"],
            }, ensure_ascii=False))
            f.write("\n")

    date_range = None
    avg_reply = 0
    if pairs:
        timestamps = [p["timestamp"] for p in pairs]
        date_range = {
            "from": min(timestamps).date().isoformat(),
            "to": max(timestamps).date().isoformat(),
        }
        avg_reply = round(mean(len(p["output"].split()) for p in pairs))

    top = sorted(parsed["per_chat"].items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_chats = [{"name": name, "pairs": count} for name, count in top]
    sample_pairs = [
        {"instruction": p["instruction"], "input": p["input"], "output": p["output"]}
        for p in pairs[:5]
    ]

    return TelegramExportResult(
        total_pairs=len(pairs),
        chats_count=parsed["chats_count"],
        date_range=date_range,
        sample_pairs=sample_pairs,
        output_path=str(out_path),
        avg_reply_length=avg_reply,
        top_chats=top_chats,
    )


async def build_combined_dataset(config: DatasetConfig) -> CombinedDatasetResult:
    """Merge bot-collected pairs and Telegram export pairs using weights."""
    warnings: list[str] = []

    bot_pairs: list[dict] = []
    if config.use_bot_pairs:
        async with SessionLocal() as session:
            bot_pairs = await collect_pairs(session)
        if not bot_pairs:
            warnings.append("Пар из бота не найдено")

    export_pairs: list[dict] = []
    for raw_path in config.telegram_export_paths:
        if not raw_path:
            continue
        try:
            parsed = _parse_export_pairs(raw_path, 3, 30)
            export_pairs.extend(parsed["pairs"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"Не удалось разобрать {raw_path}: {exc}")

    w_bot = config.bot_weight if (config.use_bot_pairs and bot_pairs) else 0.0
    w_exp = config.export_weight if export_pairs else 0.0
    total_w = w_bot + w_exp
    if total_w <= 0:
        warnings.append("Нет данных для сборки датасета")
        return CombinedDatasetResult(0, {"bot": 0, "export": 0}, None, warnings)

    frac_bot = w_bot / total_w
    frac_exp = w_exp / total_w

    limits = [max(0, int(config.total_max_samples))]
    if frac_bot > 0:
        limits.append(int(len(bot_pairs) / frac_bot))
    if frac_exp > 0:
        limits.append(int(len(export_pairs) / frac_exp))
    total = min(limits)

    bot_n = min(len(bot_pairs), round(total * frac_bot))
    exp_n = min(len(export_pairs), round(total * frac_exp))

    if config.shuffle:
        random.shuffle(bot_pairs)
        random.shuffle(export_pairs)

    selected = bot_pairs[:bot_n] + export_pairs[:exp_n]
    if config.shuffle:
        random.shuffle(selected)

    output_path = None
    if selected:
        directory = settings.training_data_dir
        version = _next_version(directory)
        out_path = directory / f"dataset_v{version}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for p in selected:
                f.write(json.dumps({
                    "instruction": p["instruction"],
                    "input": p["input"],
                    "output": p["output"],
                }, ensure_ascii=False))
                f.write("\n")
        output_path = str(out_path)

    if selected and config.use_bot_pairs and bot_n / len(selected) < 0.10:
        warnings.append(
            "Мало пар из бота — твой текущий стиль может быть недопредставлен"
        )

    return CombinedDatasetResult(
        total_pairs=len(selected),
        breakdown={"bot": bot_n, "export": exp_n},
        output_path=output_path,
        warnings=warnings,
    )
