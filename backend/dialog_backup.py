from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
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


SETTING_EXCLUDED = "dialog_backup_excluded"


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


async def _get_or_create_backup(
    session: AsyncSession, chat_id: int, chat_name: str, now: datetime
) -> DialogBackup:
    result = await session.execute(
        select(DialogBackup).where(DialogBackup.chat_id == chat_id)
    )
    backup = result.scalar_one_or_none()
    if backup is None:
        backup = DialogBackup(
            chat_id=chat_id,
            chat_name=chat_name or "",
            created_at=now,
            updated_at=now,
            message_count=0,
        )
        session.add(backup)
        await session.flush()
    elif chat_name and backup.chat_name != chat_name:
        backup.chat_name = chat_name
    return backup


async def record_message(message_id_pk: int) -> None:
    """Append a single Message row to its chat's backup.

    Called after the message has been committed to the ``messages`` table.
    If a backup row with the same telegram ``message_id`` already exists for
    this chat, the new row is marked ``edited=True`` so both versions stay.
    Excluded chats are skipped.
    """
    async with SessionLocal() as session:
        try:
            excluded = await get_excluded_chats(session)
            msg_q = await session.execute(
                select(Message).where(Message.id == message_id_pk)
            )
            msg = msg_q.scalar_one_or_none()
            if msg is None:
                return
            if msg.chat_id in excluded:
                return

            now = datetime.utcnow()
            backup = await _get_or_create_backup(
                session, msg.chat_id, msg.chat_name or "", now
            )

            edited = False
            if msg.message_id is not None:
                prior = await session.execute(
                    select(DialogBackupMessage.id)
                    .where(
                        DialogBackupMessage.chat_id == msg.chat_id,
                        DialogBackupMessage.message_id == msg.message_id,
                    )
                    .limit(1)
                )
                if prior.first() is not None:
                    edited = True

            session.add(
                DialogBackupMessage(
                    backup_id=backup.id,
                    chat_id=msg.chat_id,
                    sender_id=msg.sender_id,
                    sender_name=msg.sender_name,
                    is_mine=msg.is_mine,
                    text=msg.text,
                    timestamp=msg.timestamp or now,
                    message_id=msg.message_id,
                    edited=edited,
                    media_type=msg.media_type,
                    media_path=msg.media_path,
                    media_private=msg.media_private,
                )
            )
            backup.message_count = (backup.message_count or 0) + 1
            backup.updated_at = now
            await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("dialog backup append failed for message %s", message_id_pk)


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
            # A deleted incoming message can no longer be answered — drop it
            # from the pending queue and treat it as handled.
            if not row.is_mine and not row.replied:
                row.replied = True
                row.pending_reason = None
        await session.commit()
        return len(rows)


def _format_backup_text(
    backup: DialogBackup,
    messages: list[DialogBackupMessage],
    display_name: str,
) -> str:
    chat_title = backup.chat_name or f"chat {backup.chat_id}"
    lines = [
        f"Диалог: {chat_title}",
        f"chat_id: {backup.chat_id}",
        f"Создан: {backup.created_at.isoformat() if backup.created_at else ''}",
        f"Обновлён: {backup.updated_at.isoformat() if backup.updated_at else ''}",
        f"Сообщений: {backup.message_count}",
        "",
        "=" * 48,
        "",
    ]
    for m in messages:
        author = display_name if m.is_mine else (m.sender_name or "собеседник")
        sent_at = m.timestamp.isoformat() if m.timestamp else ""
        tag = " (изменено)" if m.edited else ""
        lines.append(f"[{sent_at}] {author}{tag}:")
        lines.append((m.text or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
