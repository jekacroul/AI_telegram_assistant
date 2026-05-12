from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import (
    DialogBackup,
    DialogBackupMessage,
    Message,
    SessionLocal,
    get_setting,
    set_setting,
)

log = logging.getLogger(__name__)


DEFAULT_INTERVAL_MINUTES = 24 * 60
SETTING_EXCLUDED = "dialog_backup_excluded"
SETTING_INTERVAL = "dialog_backup_interval_minutes"
LEGACY_SETTING_INTERVAL_HOURS = "dialog_backup_interval_hours"
SETTING_LAST_RUN = "dialog_backup_last_run"


@dataclass
class BackupRunResult:
    started_at: datetime
    finished_at: datetime
    chats_processed: int
    new_versions: int
    skipped: int
    excluded: int


async def get_excluded_chats(session: AsyncSession) -> set[int]:
    raw = await get_setting(session, SETTING_EXCLUDED, "")
    out: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.add(int(item))
        except ValueError:
            continue
    return out


async def set_excluded_chats(session: AsyncSession, chat_ids: list[int]) -> None:
    csv = ",".join(str(int(x)) for x in chat_ids)
    await set_setting(session, SETTING_EXCLUDED, csv)


async def get_interval_minutes(session: AsyncSession) -> int:
    raw = await get_setting(session, SETTING_INTERVAL, "")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = DEFAULT_INTERVAL_MINUTES
        return max(1, value)

    legacy_hours = await get_setting(session, LEGACY_SETTING_INTERVAL_HOURS, "")
    if legacy_hours:
        try:
            value = int(legacy_hours) * 60
        except ValueError:
            value = DEFAULT_INTERVAL_MINUTES
        return max(1, value)

    return DEFAULT_INTERVAL_MINUTES


def _compute_signature(rows: list[Message]) -> str:
    h = hashlib.sha256()
    for row in rows:
        key = f"{row.message_id or row.id}|{row.timestamp.isoformat() if row.timestamp else ''}|{row.text}|{int(row.is_mine)}"
        h.update(key.encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


async def _list_chats_with_counts(session: AsyncSession) -> list[dict]:
    result = await session.execute(
        select(
            Message.chat_id,
            func.max(Message.chat_name).label("chat_name"),
            func.count(Message.id).label("count"),
        )
        .where(Message.deleted == False)  # noqa: E712
        .group_by(Message.chat_id)
    )
    rows = result.all()
    return [
        {"chat_id": r.chat_id, "chat_name": r.chat_name or "", "count": r.count}
        for r in rows
    ]


async def _latest_backup_for_chat(
    session: AsyncSession, chat_id: int
) -> Optional[DialogBackup]:
    result = await session.execute(
        select(DialogBackup)
        .where(DialogBackup.chat_id == chat_id)
        .order_by(desc(DialogBackup.version))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def backup_chat(session: AsyncSession, chat_id: int) -> Optional[DialogBackup]:
    """Create new backup version for a chat if its state changed.

    Returns the new DialogBackup row if a version was created, None if state
    matches the latest version (no-op).
    """
    msg_q = await session.execute(
        select(Message)
        .where(Message.chat_id == chat_id, Message.deleted == False)  # noqa: E712
        .order_by(asc(Message.timestamp), asc(Message.id))
    )
    messages = list(msg_q.scalars().all())
    if not messages:
        return None

    signature = _compute_signature(messages)
    latest = await _latest_backup_for_chat(session, chat_id)
    if latest and latest.signature == signature:
        return None

    next_version = (latest.version + 1) if latest else 1
    chat_name = next(
        (m.chat_name for m in reversed(messages) if m.chat_name), ""
    )
    backup = DialogBackup(
        chat_id=chat_id,
        chat_name=chat_name,
        version=next_version,
        created_at=datetime.utcnow(),
        message_count=len(messages),
        signature=signature,
    )
    session.add(backup)
    await session.flush()

    for m in messages:
        session.add(
            DialogBackupMessage(
                backup_id=backup.id,
                chat_id=m.chat_id,
                sender_id=m.sender_id,
                sender_name=m.sender_name,
                is_mine=m.is_mine,
                text=m.text,
                timestamp=m.timestamp,
                message_id=m.message_id,
            )
        )

    await session.commit()
    return backup


async def run_backup_pass() -> BackupRunResult:
    started = datetime.utcnow()
    new_versions = 0
    skipped = 0
    excluded_count = 0
    chats_total = 0

    async with SessionLocal() as session:
        excluded = await get_excluded_chats(session)
        chats = await _list_chats_with_counts(session)
        chats_total = len(chats)

    for chat in chats:
        if chat["chat_id"] in excluded:
            excluded_count += 1
            continue
        try:
            async with SessionLocal() as session:
                created = await backup_chat(session, chat["chat_id"])
                if created is not None:
                    new_versions += 1
                else:
                    skipped += 1
        except Exception:  # noqa: BLE001
            log.exception("backup failed for chat %s", chat["chat_id"])

    finished = datetime.utcnow()
    async with SessionLocal() as session:
        await set_setting(session, SETTING_LAST_RUN, finished.isoformat())

    return BackupRunResult(
        started_at=started,
        finished_at=finished,
        chats_processed=chats_total,
        new_versions=new_versions,
        skipped=skipped,
        excluded=excluded_count,
    )


class DialogBackupScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self.last_result: Optional[BackupRunResult] = None
        self.last_error: str = ""

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="dialog-backup")

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    def trigger(self) -> None:
        self._wake_event.set()

    async def run_now(self) -> BackupRunResult:
        try:
            result = await run_backup_pass()
            self.last_result = result
            self.last_error = ""
            return result
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.exception("dialog backup run failed")
            raise

    async def _loop(self) -> None:
        log.info("dialog backup scheduler started")
        while not self._stop_event.is_set():
            try:
                async with SessionLocal() as session:
                    interval_minutes = await get_interval_minutes(session)
                await self.run_now()
            except Exception:  # noqa: BLE001
                interval_minutes = DEFAULT_INTERVAL_MINUTES
            sleep_seconds = max(60, interval_minutes * 60)
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()
        log.info("dialog backup scheduler stopped")


scheduler = DialogBackupScheduler()


async def mark_messages_deleted(
    chat_id: int, telegram_message_ids: list[int]
) -> int:
    """Mark messages as deleted by their telegram message_id within a chat."""
    if not telegram_message_ids:
        return 0
    async with SessionLocal() as session:
        result = await session.execute(
            select(Message).where(
                Message.chat_id == chat_id,
                Message.message_id.in_(telegram_message_ids),
                Message.deleted == False,  # noqa: E712
            )
        )
        rows = list(result.scalars().all())
        for row in rows:
            row.deleted = True
        await session.commit()
        return len(rows)
