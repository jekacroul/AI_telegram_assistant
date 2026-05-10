from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import Message as TgMessage
from aiogram.types import Update
from sqlalchemy import select

from .config import settings
from .database import Message, SessionLocal, get_setting, set_setting
from .event_bus import message_bus
from .llm_engine import OllamaUnavailableError, get_client
from .style_engine import get_latest_profile, reanalyze_and_store


log = logging.getLogger(__name__)


class TelegramService:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self._token: str = ""
        self._lock = asyncio.Lock()
        self._messages_since_reanalyze = 0

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
            await message.answer("Локальный AI-ассистент готов. Отвечаю в твоём стиле.")

        @dp.message()
        async def on_message(message: TgMessage) -> None:
            await self.handle_incoming(message)

    async def handle_incoming(self, tg_msg: TgMessage) -> None:
        try:
            if tg_msg.from_user and tg_msg.from_user.is_bot:
                return
            if tg_msg.chat.type == ChatType.CHANNEL:
                return
            if not tg_msg.text or not tg_msg.text.strip():
                return

            chat_id = tg_msg.chat.id
            chat_name = (
                tg_msg.chat.title
                or tg_msg.chat.username
                or (tg_msg.from_user.full_name if tg_msg.from_user else str(chat_id))
            )
            sender = tg_msg.from_user
            sender_id = sender.id if sender else 0
            sender_name = sender.full_name if sender else "unknown"

            should_reply = True
            me = None
            if self.bot:
                me = await self.bot.me()
            if tg_msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
                mentioned = False
                if me and tg_msg.text and f"@{me.username}" in tg_msg.text:
                    mentioned = True
                if (
                    tg_msg.reply_to_message
                    and tg_msg.reply_to_message.from_user
                    and me
                    and tg_msg.reply_to_message.from_user.id == me.id
                ):
                    mentioned = True
                should_reply = mentioned

            async with SessionLocal() as session:
                row = Message(
                    chat_id=chat_id,
                    chat_name=chat_name,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    is_mine=False,
                    text=tg_msg.text,
                    timestamp=datetime.utcnow(),
                    message_id=tg_msg.message_id,
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

            await message_bus.publish("incoming", {
                "id": msg_id,
                "chat_id": chat_id,
                "chat_name": chat_name,
                "sender_name": sender_name,
                "text": tg_msg.text,
                "timestamp": datetime.utcnow().isoformat(),
                "auto_reply": auto_reply,
                "will_reply": should_reply,
            })

            if not should_reply:
                return

            if auto_reply:
                try:
                    variants = await self._generate_variants(
                        tg_msg.text, sender_name, chat_id
                    )
                    chosen = variants[0] if variants else "ок"
                    await self.send_reply(chat_id, chosen, reply_to=tg_msg.message_id)
                    await self._record_reply(msg_id, chosen, sender_name, chat_id, chat_name)
                except OllamaUnavailableError as e:
                    log.warning("Ollama unavailable: %s", e)
                except Exception as e:  # noqa: BLE001
                    log.exception("auto reply failed: %s", e)
            else:
                await message_bus.publish("pending", {"id": msg_id})

            await self._maybe_reanalyze()
        except Exception:
            log.exception("handle_incoming error")

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
            "timestamp": datetime.utcnow().isoformat(),
            "original_id": original_id,
        })

    async def send_reply(self, chat_id: int, text: str, reply_to: Optional[int] = None) -> None:
        if not self.bot:
            raise RuntimeError("Bot is not configured")
        await self.bot.send_message(chat_id, text, reply_to_message_id=reply_to)

    async def send_and_record(
        self, chat_id: int, text: str, reply_to: Optional[int] = None,
        original_id: Optional[int] = None,
    ) -> None:
        await self.send_reply(chat_id, text, reply_to=reply_to)
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
            "timestamp": datetime.utcnow().isoformat(),
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
        await self.bot.set_webhook(url, drop_pending_updates=False)

    async def remove_webhook(self) -> None:
        if not self.bot:
            return
        await self.bot.delete_webhook(drop_pending_updates=False)

    async def start_polling(self) -> None:
        if not (self.dp and self.bot):
            raise RuntimeError("Bot not configured")
        await self.dp.start_polling(self.bot)

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
