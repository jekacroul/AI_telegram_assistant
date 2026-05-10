"""Telethon-based userbot wrapper for the local Telegram AI assistant."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.tl.types import User

from .config import get_settings
from .database import Message, MonitoredChat, SessionLocal

log = logging.getLogger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class TelegramService:
    def __init__(self) -> None:
        self._client: TelegramClient | None = None
        self._lock = asyncio.Lock()
        self._listeners: list[EventHandler] = []
        self._monitored_chat_ids: set[int] = set()
        self._me_id: int | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._client is not None and self._client.is_connected()

    @property
    def me_id(self) -> int | None:
        return self._me_id

    def add_listener(self, handler: EventHandler) -> None:
        self._listeners.append(handler)

    def remove_listener(self, handler: EventHandler) -> None:
        if handler in self._listeners:
            self._listeners.remove(handler)

    async def _build_client(self) -> TelegramClient:
        settings = get_settings()
        if not (settings.telegram_api_id and settings.telegram_api_hash):
            raise RuntimeError("Telegram API credentials are not configured.")
        client = TelegramClient(
            settings.telegram_session_name,
            int(settings.telegram_api_id),
            settings.telegram_api_hash,
        )
        await client.connect()
        if not await client.is_user_authorized():
            if not settings.telegram_phone:
                raise RuntimeError(
                    "Telegram session is not authorized and no phone number is configured."
                )
            await client.send_code_request(settings.telegram_phone)
            raise RuntimeError(
                "Telegram session needs interactive authorization. Run "
                "`python -m backend.telegram_login` once to sign in."
            )
        return client

    async def start(self) -> None:
        async with self._lock:
            if self._connected:
                return
            try:
                self._client = await self._build_client()
            except Exception as exc:
                log.warning("Telegram client did not start: %s", exc)
                self._client = None
                self._connected = False
                return
            me = await self._client.get_me()
            self._me_id = me.id if me else None
            await self._refresh_monitored_chats()
            self._client.add_event_handler(self._on_new_message, events.NewMessage(incoming=True))
            self._connected = True
            log.info("Telegram client connected as %s", getattr(me, "username", None) or self._me_id)

    async def stop(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.disconnect()
            self._client = None
            self._connected = False

    async def _refresh_monitored_chats(self) -> None:
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(MonitoredChat).where(MonitoredChat.enabled.is_(True))
                )
            ).scalars().all()
            self._monitored_chat_ids = {row.chat_id for row in rows}

    async def set_monitored_chats(self, chat_ids: list[int], names: dict[int, str]) -> None:
        async with SessionLocal() as session:
            existing = (await session.execute(select(MonitoredChat))).scalars().all()
            for row in existing:
                row.enabled = row.chat_id in chat_ids
            for chat_id in chat_ids:
                if not any(r.chat_id == chat_id for r in existing):
                    session.add(
                        MonitoredChat(
                            chat_id=chat_id,
                            chat_name=names.get(chat_id, str(chat_id)),
                            enabled=True,
                        )
                    )
            await session.commit()
        self._monitored_chat_ids = set(chat_ids)

    async def list_chats(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self._client:
            return []
        result = []
        async for dialog in self._client.iter_dialogs(limit=limit):
            entity = dialog.entity
            kind = "user"
            if dialog.is_group:
                kind = "group"
            elif dialog.is_channel:
                kind = "channel"
            result.append(
                {
                    "chat_id": dialog.id,
                    "name": dialog.name or getattr(entity, "username", str(dialog.id)),
                    "type": kind,
                    "unread": dialog.unread_count,
                    "monitored": dialog.id in self._monitored_chat_ids,
                }
            )
        return result

    async def collect_history(self, chat_ids: list[int], my_message_limit: int = 1000) -> int:
        """Pull recent message history for selected chats and store both sides.

        Returns the number of new rows inserted.
        """
        if not self._client:
            return 0
        inserted = 0
        async with SessionLocal() as session:
            for chat_id in chat_ids:
                try:
                    entity = await self._client.get_entity(chat_id)
                except Exception as exc:
                    log.warning("Failed to resolve chat %s: %s", chat_id, exc)
                    continue
                chat_name = getattr(entity, "title", None) or getattr(
                    entity, "first_name", None
                ) or str(chat_id)
                mine_count = 0
                async for msg in self._client.iter_messages(entity, limit=5000):
                    if msg.text is None:
                        continue
                    is_mine = bool(msg.out)
                    if is_mine:
                        mine_count += 1
                    sender_name = "Me" if is_mine else self._sender_label(msg.sender)
                    sender_id = self._me_id if is_mine else (msg.sender_id or 0)
                    row = Message(
                        chat_id=chat_id,
                        chat_name=str(chat_name),
                        sender_id=int(sender_id or 0),
                        sender_name=sender_name,
                        is_mine=is_mine,
                        text=msg.text,
                        timestamp=msg.date.replace(tzinfo=None) if msg.date else datetime.utcnow(),
                        message_id=msg.id,
                        reply_to_message_id=getattr(msg, "reply_to_msg_id", None),
                        handled=True,
                    )
                    session.add(row)
                    try:
                        await session.flush()
                        inserted += 1
                    except Exception:
                        await session.rollback()
                        continue
                    if mine_count >= my_message_limit and is_mine:
                        break
            await session.commit()
        return inserted

    @staticmethod
    def _sender_label(sender: Any) -> str:
        if sender is None:
            return "Unknown"
        if isinstance(sender, User):
            parts = [sender.first_name or "", sender.last_name or ""]
            name = " ".join(p for p in parts if p).strip()
            return name or sender.username or str(sender.id)
        return getattr(sender, "title", None) or str(getattr(sender, "id", "Unknown"))

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        try:
            chat_id = event.chat_id
            if self._monitored_chat_ids and chat_id not in self._monitored_chat_ids:
                return
            if event.message.text is None:
                return
            sender = await event.get_sender()
            chat = await event.get_chat()
            chat_name = getattr(chat, "title", None) or getattr(
                chat, "first_name", None
            ) or str(chat_id)
            sender_name = self._sender_label(sender)
            row = Message(
                chat_id=chat_id,
                chat_name=str(chat_name),
                sender_id=int(getattr(sender, "id", 0) or 0),
                sender_name=sender_name,
                is_mine=False,
                text=event.message.text,
                timestamp=event.message.date.replace(tzinfo=None)
                if event.message.date
                else datetime.utcnow(),
                message_id=event.message.id,
                reply_to_message_id=getattr(event.message, "reply_to_msg_id", None),
                handled=False,
            )
            async with SessionLocal() as session:
                session.add(row)
                try:
                    await session.commit()
                except Exception:
                    await session.rollback()
                    return
                await session.refresh(row)
            payload = {
                "type": "incoming_message",
                "id": row.id,
                "chat_id": row.chat_id,
                "chat_name": row.chat_name,
                "sender_name": row.sender_name,
                "text": row.text,
                "timestamp": row.timestamp.isoformat(),
                "message_id": row.message_id,
            }
            for handler in list(self._listeners):
                try:
                    await handler(payload)
                except Exception as exc:
                    log.warning("Listener error: %s", exc)
        except Exception as exc:
            log.exception("Failed to handle incoming message: %s", exc)

    async def send_reply(
        self,
        chat_id: int,
        reply_text: str,
        reply_to_message_id: int | None = None,
    ) -> int:
        if not self._client:
            raise RuntimeError("Telegram client is not connected.")
        sent = await self._client.send_message(
            chat_id, reply_text, reply_to=reply_to_message_id
        )
        async with SessionLocal() as session:
            session.add(
                Message(
                    chat_id=chat_id,
                    chat_name=str(getattr(sent.chat, "title", "") or chat_id),
                    sender_id=self._me_id or 0,
                    sender_name="Me",
                    is_mine=True,
                    text=reply_text,
                    timestamp=sent.date.replace(tzinfo=None)
                    if sent.date
                    else datetime.utcnow(),
                    message_id=sent.id,
                    reply_to_message_id=reply_to_message_id,
                    handled=True,
                )
            )
            await session.commit()
        return sent.id


telegram_service = TelegramService()
