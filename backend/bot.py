from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional


def _naive_utc(dt: Optional[datetime]) -> datetime:
    """Return a tz-naive datetime in UTC for storage in a naive DateTime column."""
    if dt is None:
        return datetime.utcnow()
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BusinessMessagesDeleted, Message as TgMessage
from aiogram.types import Update
from sqlalchemy import select

from .config import settings
from .database import Message, SessionLocal, get_setting, set_setting
from .delay import get_delay_settings
from .dialog_backup import mark_messages_deleted
from .notifications import (
    SETTING_LAST_PRIVATE_CHAT_ID,
    SETTING_NOTIFY_CHAT_ID,
    notify_owner,
)
from .schedule import is_within_schedule

ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "business_connection",
    "business_message",
    "edited_business_message",
    "deleted_business_messages",
    "callback_query",
]
from .event_bus import message_bus
from .llm_engine import LLMUnavailableError, get_client, pick_auto_variant
from .style_engine import get_latest_profile, reanalyze_and_store


log = logging.getLogger(__name__)


TELEGRAM_MESSAGE_LIMIT = 4096


def _split_for_telegram(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split text into chunks that fit in a single Telegram message."""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class TelegramService:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self._token: str = ""
        self._lock = asyncio.Lock()
        self._messages_since_reanalyze = 0
        self.last_update_at: Optional[datetime] = None
        self.last_update_kind: str = ""
        self.update_count: int = 0
        self.last_error: str = ""
        self._delayed_reply_tasks: dict[int, asyncio.Task] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.bot and self._token)

    async def setup(self, token: str) -> None:
        async with self._lock:
            if self.bot and self._token == token:
                return
            if self.bot:
                try:
                    await self.bot.session.close()
                except Exception:  # noqa: BLE001
                    pass
            self._token = token
            self.bot = Bot(
                token=token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
            self.dp = Dispatcher()
            self._register_handlers(self.dp)

    def _register_handlers(self, dp: Dispatcher) -> None:
        @dp.message(Command("start"))
        async def cmd_start(message: TgMessage) -> None:
            await self._remember_private_chat(message)
            await message.answer("Локальный AI-ассистент готов. Отвечаю в твоём стиле.")

        @dp.message()
        async def on_message(message: TgMessage) -> None:
            self._mark_update("message")
            await self._remember_private_chat(message)
            await self.handle_incoming(message)

        @dp.business_message()
        async def on_business_message(message: TgMessage) -> None:
            self._mark_update("business_message")
            await self.handle_incoming(message)

        @dp.edited_business_message()
        async def on_business_edit(message: TgMessage) -> None:
            self._mark_update("edited_business_message")
            await self.handle_incoming(message)

        @dp.deleted_business_messages()
        async def on_business_deleted(event: BusinessMessagesDeleted) -> None:
            self._mark_update("deleted_business_messages")
            try:
                chat_id = event.chat.id if event.chat else None
                ids = list(event.message_ids or [])
                if chat_id is not None and ids:
                    await mark_messages_deleted(chat_id, ids)
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                log.exception("deleted_business_messages handling failed")

    def _mark_update(self, kind: str) -> None:
        self.last_update_at = datetime.utcnow()
        self.last_update_kind = kind
        self.update_count += 1

    @staticmethod
    async def _remember_private_chat(tg_msg: TgMessage) -> None:
        try:
            if tg_msg.chat.type != ChatType.PRIVATE:
                return
            if getattr(tg_msg, "business_connection_id", None):
                return
            if tg_msg.from_user and tg_msg.from_user.is_bot:
                return
            async with SessionLocal() as session:
                await set_setting(
                    session, SETTING_LAST_PRIVATE_CHAT_ID, str(tg_msg.chat.id)
                )
        except Exception:  # noqa: BLE001
            log.exception("failed to remember last private chat")

    @staticmethod
    def _extract_message_content(tg_msg: TgMessage) -> str:
        text = (tg_msg.text or "").strip()
        if text:
            return text
        caption = (tg_msg.caption or "").strip()
        if caption:
            return caption
        sticker = getattr(tg_msg, "sticker", None)
        if sticker is not None:
            emoji_char = (getattr(sticker, "emoji", None) or "").strip()
            if emoji_char:
                return emoji_char
            return "(стикер)"
        animation = getattr(tg_msg, "animation", None)
        if animation is not None:
            return "(гиф)"
        if getattr(tg_msg, "photo", None):
            return "(фото)"
        if getattr(tg_msg, "video", None) is not None:
            return "(видео)"
        if getattr(tg_msg, "voice", None) is not None:
            return "(голосовое)"
        if getattr(tg_msg, "video_note", None) is not None:
            return "(кружок)"
        if getattr(tg_msg, "audio", None) is not None:
            return "(аудио)"
        if getattr(tg_msg, "document", None) is not None:
            return "(документ)"
        return ""

    async def handle_incoming(self, tg_msg: TgMessage) -> None:
        try:
            if tg_msg.from_user and tg_msg.from_user.is_bot:
                return
            if tg_msg.chat.type == ChatType.CHANNEL:
                return
            content_text = self._extract_message_content(tg_msg)
            if not content_text:
                return

            business_connection_id = getattr(tg_msg, "business_connection_id", None)
            is_business = business_connection_id is not None

            me = None
            if self.bot:
                try:
                    me = await self.bot.me()
                except Exception:  # noqa: BLE001
                    me = None

            owner_id = await self._business_owner_id(business_connection_id) if is_business else None
            sender = tg_msg.from_user
            sender_id = sender.id if sender else 0
            sender_name = sender.full_name if sender else "unknown"

            is_mine = bool(
                is_business and owner_id is not None and sender_id == owner_id
            )

            chat_id = tg_msg.chat.id
            chat_username = getattr(tg_msg.chat, "username", None) or (
                sender.username if sender else ""
            )
            chat_name = (
                tg_msg.chat.title
                or (sender.full_name if sender else "")
                or chat_username
                or str(chat_id)
            )

            self._cancel_delayed_reply(chat_id)

            should_reply = not is_mine
            if not is_business and tg_msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                mentioned = False
                if me and tg_msg.text and f"@{me.username}" in tg_msg.text:
                    mentioned = True
                if me and tg_msg.caption and f"@{me.username}" in tg_msg.caption:
                    mentioned = True
                if (
                    tg_msg.reply_to_message
                    and tg_msg.reply_to_message.from_user
                    and me
                    and tg_msg.reply_to_message.from_user.id == me.id
                ):
                    mentioned = True
                should_reply = mentioned

            tg_ts = _naive_utc(getattr(tg_msg, "date", None))
            async with SessionLocal() as session:
                row = Message(
                    chat_id=chat_id,
                    chat_name=chat_name,
                    chat_username=chat_username,
                    sender_id=sender_id,
                    sender_name=settings.user_name if is_mine else sender_name,
                    is_mine=is_mine,
                    text=content_text,
                    timestamp=tg_ts,
                    message_id=tg_msg.message_id,
                    business_connection_id=business_connection_id,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                msg_id = row.id

                monitored = await get_setting(session, "monitored_chats", "")
                allowed: list[int] = []
                if monitored:
                    try:
                        allowed = [int(x) for x in monitored.split(",") if x.strip()]
                    except ValueError:
                        allowed = []
                if allowed and chat_id not in allowed:
                    should_reply = False

                auto_reply_setting = await get_setting(
                    session, "auto_reply", "1" if settings.auto_reply else "0"
                )
                auto_reply = auto_reply_setting in ("1", "true", "True")
                within_schedule = await is_within_schedule(session)
                if auto_reply and should_reply and not within_schedule:
                    row.pending_reason = "schedule"
                    should_reply = False
                    await session.commit()

            await message_bus.publish("incoming", {
                "id": msg_id,
                "chat_id": chat_id,
                "chat_name": chat_name,
                "sender_name": row.sender_name,
                "text": content_text,
                "is_mine": is_mine,
                "business": is_business,
                "timestamp": _iso_utc_now(),
                "auto_reply": auto_reply,
                "will_reply": should_reply,
                "pending_reason": row.pending_reason,
            })

            if is_mine:
                await self._maybe_reanalyze()
                return

            if not should_reply:
                if row.pending_reason:
                    await message_bus.publish(
                        "pending", {"id": msg_id, "reason": row.pending_reason}
                    )
                return

            if auto_reply:
                try:
                    variants = await self._generate_variants(
                        content_text, sender_name, chat_id
                    )
                    chosen = pick_auto_variant(variants) or "ок"
                    await self._delay_before_auto_reply(chat_id, chat_name)
                    await self.send_reply(
                        chat_id, chosen,
                        reply_to=tg_msg.message_id,
                        business_connection_id=business_connection_id,
                    )
                    await self._record_reply(msg_id, chosen, sender_name, chat_id, chat_name)
                    await notify_owner(chat_name, sender_name, content_text, chosen)
                except asyncio.CancelledError:
                    log.info("Отменяю отложенный ответ в %s", chat_name)
                    return
                except LLMUnavailableError as e:
                    log.warning("LLM unavailable: %s", e)
                    self.last_error = str(e)
                except TelegramBadRequest as e:
                    if "BUSINESS_PEER_INVALID" in str(e):
                        log.warning(
                            "auto reply blocked by business privacy for chat %s: %s",
                            chat_id, e,
                        )
                        self.last_error = (
                            "Telegram business privacy blocks the bot for this chat "
                            "(BUSINESS_PEER_INVALID). Check Settings → Business → "
                            "Chatbots and include this contact."
                        )
                        await message_bus.publish("pending", {"id": msg_id})
                    else:
                        log.exception("auto reply failed: %s", e)
                        self.last_error = str(e)
                except Exception as e:  # noqa: BLE001
                    log.exception("auto reply failed: %s", e)
                    self.last_error = str(e)
            else:
                await message_bus.publish(
                    "pending", {"id": msg_id, "reason": "auto_reply_disabled"}
                )

            await self._maybe_reanalyze()
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.exception("handle_incoming error")


    def _cancel_delayed_reply(self, chat_id: int) -> None:
        task = self._delayed_reply_tasks.get(chat_id)
        current_task = asyncio.current_task()
        if task and task is not current_task and not task.done():
            task.cancel()

    async def _delay_before_auto_reply(self, chat_id: int, chat_name: str) -> None:
        async with SessionLocal() as session:
            delay = await get_delay_settings(session)
        if not delay.enabled:
            return

        seconds = random.uniform(delay.min_seconds, delay.max_seconds)
        display_seconds = int(round(seconds))
        log.info("Жду %s сек перед отправкой в %s", display_seconds, chat_name)

        task = asyncio.current_task()
        if task is None:
            await asyncio.sleep(seconds)
            return

        self._delayed_reply_tasks[chat_id] = task
        try:
            await asyncio.sleep(seconds)
        finally:
            if self._delayed_reply_tasks.get(chat_id) is task:
                self._delayed_reply_tasks.pop(chat_id, None)

    async def _business_owner_id(self, connection_id: Optional[str]) -> Optional[int]:
        if not connection_id or not self.bot:
            return None
        cache = getattr(self, "_business_owner_cache", None)
        if cache is None:
            cache = {}
            self._business_owner_cache = cache
        if connection_id in cache:
            return cache[connection_id]
        try:
            conn = await self.bot.get_business_connection(connection_id)
            owner_id = conn.user.id if conn and conn.user else None
        except Exception:  # noqa: BLE001
            owner_id = None
        cache[connection_id] = owner_id
        return owner_id

    async def _generate_variants(
        self, text: str, sender_name: str, chat_id: int
    ) -> list[str]:
        async with SessionLocal() as session:
            profile = await get_latest_profile(session)
            history_q = await session.execute(
                select(Message).where(Message.chat_id == chat_id)
                .order_by(Message.timestamp.desc()).limit(8)
            )
            history = list(history_q.scalars().all())[::-1]
            history_dicts = [
                {"sender_name": m.sender_name, "is_mine": m.is_mine, "text": m.text}
                for m in history
            ]
        client = get_client()
        return await client.generate_reply(
            incoming_text=text,
            sender_name=sender_name,
            style_profile=profile,
            chat_history=history_dicts,
        )

    async def _record_reply(
        self,
        original_id: int,
        reply_text: str,
        sender_name: str,
        chat_id: int,
        chat_name: str,
    ) -> None:
        async with SessionLocal() as session:
            result = await session.execute(select(Message).where(Message.id == original_id))
            original = result.scalar_one_or_none()
            if original:
                original.replied = True
                original.reply_text = reply_text
            session.add(Message(
                chat_id=chat_id,
                chat_name=chat_name,
                sender_id=0,
                sender_name=settings.user_name,
                is_mine=True,
                text=reply_text,
                timestamp=datetime.utcnow(),
            ))
            await session.commit()
        await message_bus.publish("sent", {
            "chat_id": chat_id,
            "chat_name": chat_name,
            "to": sender_name,
            "text": reply_text,
            "timestamp": _iso_utc_now(),
            "original_id": original_id,
        })

    async def send_reply(
        self,
        chat_id: int,
        text: str,
        reply_to: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> None:
        if not self.bot:
            raise RuntimeError("Bot is not configured")
        kwargs: dict = {}
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        if business_connection_id is not None:
            kwargs["business_connection_id"] = business_connection_id
        for chunk in _split_for_telegram(text):
            await self.bot.send_message(chat_id, chunk, **kwargs)
            kwargs.pop("reply_to_message_id", None)

    async def send_and_record(
        self, chat_id: int, text: str, reply_to: Optional[int] = None,
        original_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> None:
        await self.send_reply(
            chat_id, text, reply_to=reply_to,
            business_connection_id=business_connection_id,
        )
        async with SessionLocal() as session:
            chat_name = ""
            if original_id is not None:
                result = await session.execute(select(Message).where(Message.id == original_id))
                original = result.scalar_one_or_none()
                if original:
                    chat_name = original.chat_name
                    original.replied = True
                    original.reply_text = text
            session.add(Message(
                chat_id=chat_id,
                chat_name=chat_name,
                sender_id=0,
                sender_name=settings.user_name,
                is_mine=True,
                text=text,
                timestamp=datetime.utcnow(),
            ))
            await session.commit()
        await message_bus.publish("sent", {
            "chat_id": chat_id,
            "text": text,
            "timestamp": _iso_utc_now(),
            "original_id": original_id,
        })

    async def feed_update(self, raw: dict) -> None:
        if not self.dp or not self.bot:
            raise RuntimeError("Bot not configured")
        update = Update.model_validate(raw, context={"bot": self.bot})
        await self.dp.feed_update(self.bot, update)

    async def set_webhook(self, url: str) -> None:
        if not self.bot:
            raise RuntimeError("Bot not configured")
        await self.bot.set_webhook(
            url,
            drop_pending_updates=False,
            allowed_updates=ALLOWED_UPDATES,
        )

    async def get_webhook_info(self) -> dict:
        if not self.bot:
            raise RuntimeError("Bot not configured")
        info = await self.bot.get_webhook_info()
        return {
            "url": info.url,
            "has_custom_certificate": info.has_custom_certificate,
            "pending_update_count": info.pending_update_count,
            "ip_address": info.ip_address,
            "last_error_date": info.last_error_date.isoformat() if info.last_error_date else None,
            "last_error_message": info.last_error_message,
            "max_connections": info.max_connections,
            "allowed_updates": info.allowed_updates,
        }

    async def remove_webhook(self) -> None:
        if not self.bot:
            return
        await self.bot.delete_webhook(drop_pending_updates=False)

    async def start_polling(self) -> None:
        if not (self.dp and self.bot):
            raise RuntimeError("Bot not configured")
        await self.dp.start_polling(self.bot, allowed_updates=ALLOWED_UPDATES)

    async def _maybe_reanalyze(self) -> None:
        self._messages_since_reanalyze += 1
        if self._messages_since_reanalyze < 20:
            return
        self._messages_since_reanalyze = 0
        try:
            async with SessionLocal() as session:
                await reanalyze_and_store(session)
        except Exception:  # noqa: BLE001
            log.exception("style reanalyze failed")

    async def shutdown(self) -> None:
        if self.bot:
            try:
                await self.bot.session.close()
            except Exception:  # noqa: BLE001
                pass


telegram_service = TelegramService()
