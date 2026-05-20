from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
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
from sqlalchemy import select, update

from . import admin_bot, meeting_flow
from .config import settings
from .database import (
    ChatSummary,
    Message,
    QualityLog,
    SessionLocal,
    get_setting,
    set_setting,
)
from .delay import get_delay_settings
from .dialog_backup import mark_messages_deleted
from .notifications import SETTING_LAST_PRIVATE_CHAT_ID
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
from .quality_filter import is_good_response
from .rag_engine import get_rag_context, rag_engine
from .style_engine import get_profile_for_chat, reanalyze_and_store, reanalyze_chat_persona
from .whisper_engine import convert_to_wav, whisper_engine

VOICE_REPLY_MODE_TEXT = "text"
VOICE_REPLY_MODE_SKIP = "skip"
VOICE_REPLY_MODE_PENDING = "pending"
DEFAULT_VOICE_REPLY_MODE = VOICE_REPLY_MODE_TEXT

log = logging.getLogger(__name__)


TELEGRAM_MESSAGE_LIMIT = 4096

# How long to wait for the contact to stop sending messages before the bot
# composes one reply for the whole burst. 0 disables batching.
DEFAULT_SETTLE_SECONDS = 12
MAX_SETTLE_SECONDS = 120
# Recent raw messages fed to the model as short-term context. Older messages
# are folded into the rolling per-chat summary instead.
DEFAULT_REPLY_HISTORY_LIMIT = 20
# Cap on messages folded into the summary in a single update (bounds the
# first summary build for a long-existing chat and keeps the summary
# prompt inside the model's context window).
SUMMARY_BATCH_LIMIT = 40
# Per-message length cap when feeding text into the summary prompt.
SUMMARY_LINE_MAX_CHARS = 400
# Only summarize messages from roughly the last month.
SUMMARY_MAX_AGE_DAYS = 31
# Max separate Telegram messages one reply may be split into.
MAX_REPLY_MESSAGES = 5

# Placeholder strings that carry no semantic content worth embedding.
RAG_SKIP_TEXTS = {
    "(фото)",
    "(видео)",
    "(кружок)",
    "(голосовое)",
    "(стикер)",
    "(гиф)",
    "(аудио)",
    "(документ)",
    "Приватное сообщение",
}


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


_TRUE_SETTING_VALUES = {"1", "true", "True", "yes", "on"}


def _setting_bool(value: object) -> bool:
    return str(value).strip() in _TRUE_SETTING_VALUES


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


async def _load_reply_temperatures(session) -> list[float]:
    """Return the configured per-variant temperatures, falling back to the
    defaults when the setting is missing or malformed."""
    raw = await get_setting(session, "reply_temperatures", "[0.7, 0.85, 1.0]")
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return [0.7, 0.85, 1.0]
    if not isinstance(parsed, list) or len(parsed) != 3:
        return [0.7, 0.85, 1.0]
    out: list[float] = []
    for v in parsed:
        try:
            t = float(v)
        except (TypeError, ValueError):
            return [0.7, 0.85, 1.0]
        out.append(max(0.0, min(2.0, t)))
    return out


def _split_reply_messages(text: str) -> list[str]:
    """Split a composed reply into separate Telegram messages on line breaks,
    so the bot can answer a burst with a few short messages like a human."""
    if not text:
        return [text]
    parts = [ln.strip() for ln in text.split("\n")]
    parts = [p for p in parts if p]
    if not parts:
        stripped = text.strip()
        return [stripped] if stripped else [text]
    return parts[:MAX_REPLY_MESSAGES]


class TelegramService:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self._token: str = ""
        self._lock = asyncio.Lock()
        self._messages_since_reanalyze = 0
        self._chat_new_my_messages: dict[int, int] = {}
        self.last_update_at: Optional[datetime] = None
        self.last_update_kind: str = ""
        self.update_count: int = 0
        self.last_error: str = ""
        self._delayed_reply_tasks: dict[int, asyncio.Task] = {}
        # Latest reply context per chat, consumed by the debounced reply task
        # once the contact's message burst settles.
        self._pending_reply_ctx: dict[int, dict] = {}

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
        # Admin handlers must be registered first so owner commands take
        # precedence; non-owner updates fall through to the handlers below.
        admin_bot.register(dp)

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

    @staticmethod
    def _extract_media(tg_msg: TgMessage) -> tuple[Optional[str], Optional[str]]:
        if getattr(tg_msg, "photo", None):
            photo_sizes = tg_msg.photo or []
            if photo_sizes:
                return "photo", photo_sizes[-1].file_id
        if getattr(tg_msg, "video", None) is not None:
            return "video", tg_msg.video.file_id
        if getattr(tg_msg, "video_note", None) is not None:
            return "video_note", tg_msg.video_note.file_id
        return None, None

    @staticmethod
    def _extract_voice(tg_msg: TgMessage) -> tuple[Optional[str], Optional[int]]:
        """Return (file_id, duration_seconds) for voice/audio messages."""
        voice = getattr(tg_msg, "voice", None)
        if voice is not None:
            return voice.file_id, int(getattr(voice, "duration", 0) or 0)
        audio = getattr(tg_msg, "audio", None)
        if audio is not None:
            return audio.file_id, int(getattr(audio, "duration", 0) or 0)
        return None, None

    async def _download_voice(
        self, file_id: str, message_id: int
    ) -> Optional[str]:
        if not self.bot:
            return None
        temp_dir = settings.media_dir.parent / "data" / "temp"
        # If MEDIA_PATH already points inside data/, temp lives there too.
        # Fall back to a stable path under the project root otherwise.
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            temp_dir = settings.media_dir / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
        target = temp_dir / f"voice_{message_id}.ogg"
        try:
            await self.bot.download(file_id, destination=target)
            return str(target)
        except Exception:  # noqa: BLE001
            log.exception("failed to download voice file")
            return None

    async def _transcribe_voice_file(
        self, src_path: str, message_id: int
    ) -> "TranscribeResult":
        from .whisper_engine import TranscribeResult  # local import for typing

        wav_path = str(Path(src_path).with_suffix(".wav"))
        converted = await asyncio.to_thread(convert_to_wav, src_path, wav_path)
        try:
            async with SessionLocal() as session:
                model_name = await get_setting(
                    session, "whisper_model", "large-v3"
                )
                language = await get_setting(
                    session, "whisper_language", "ru"
                )
                lazy_setting = await get_setting(
                    session, "whisper_lazy_load", "0"
                )
                lazy = lazy_setting in ("1", "true", "True")
            target = wav_path if converted else src_path
            result = await whisper_engine.transcribe_voice(
                target,
                language=language,
                model_name=model_name,
                lazy_load=lazy,
            )
            return result
        finally:
            for path in (src_path, wav_path):
                try:
                    if path and Path(path).exists():
                        Path(path).unlink()
                except Exception:  # noqa: BLE001
                    log.error("failed to remove temp voice file %s", path)

    async def _download_media(
        self, file_id: str, media_type: str, tg_msg: TgMessage
    ) -> tuple[Optional[str], bool]:
        if not self.bot:
            return None, True
        chat_folder = settings.media_dir / str(tg_msg.chat.id)
        chat_folder.mkdir(parents=True, exist_ok=True)
        ext = {"photo": ".jpg", "video": ".mp4", "video_note": ".mp4"}.get(
            media_type, ""
        )
        file_name = f"{tg_msg.message_id}_{media_type}{ext}"
        target = chat_folder / file_name
        try:
            await self.bot.download(file_id, destination=target)
            relative = Path("media") / str(tg_msg.chat.id) / file_name
            return f"/{relative.as_posix()}", False
        except TelegramBadRequest:
            return None, True
        except Exception:
            log.exception("failed to download media")
            return None, True

    async def handle_incoming(self, tg_msg: TgMessage) -> None:
        try:
            if tg_msg.from_user and tg_msg.from_user.is_bot:
                return
            if tg_msg.chat.type == ChatType.CHANNEL:
                return

            sender = tg_msg.from_user
            sender_id = sender.id if sender else 0

            content_text = self._extract_message_content(tg_msg)
            voice_file_id, voice_duration = self._extract_voice(tg_msg)
            is_voice = voice_file_id is not None
            if not content_text and not is_voice:
                return
            media_type, media_file_id = self._extract_media(tg_msg)
            media_path = None
            media_private = False
            if media_type and media_file_id:
                media_path, media_private = await self._download_media(
                    media_file_id, media_type, tg_msg
                )
            media_placeholders = {"(фото)", "(видео)", "(кружок)"}
            if content_text in media_placeholders and media_path:
                content_text = ""
            elif content_text in media_placeholders and media_private:
                content_text = "Приватное сообщение"
            if is_voice:
                content_text = "(голосовое)"

            business_connection_id = getattr(tg_msg, "business_connection_id", None)
            is_business = business_connection_id is not None

            owner_id = (
                await self._business_owner_id(business_connection_id)
                if is_business
                else None
            )
            sender_name = sender.full_name if sender else "unknown"

            is_mine = bool(
                is_business and owner_id is not None and sender_id == owner_id
            )

            chat_id = tg_msg.chat.id

            # When the owner messages the bot directly, Telegram delivers the
            # same message twice: as a business update (chat.id == the bot's
            # own id) and as a regular private message. Drop the business copy
            # and let the regular @dp.message() handler reply — only it has a
            # chat.id and message id valid for a normal (non-business) reply.
            if is_business and owner_id is not None and sender_id == owner_id:
                bot_id = None
                if self.bot:
                    try:
                        bot_id = (await self.bot.me()).id
                    except Exception:  # noqa: BLE001
                        bot_id = None
                if bot_id is not None and chat_id == bot_id:
                    log.info(
                        "handle_incoming: dropped owner->bot business copy "
                        "(regular update handles the reply)"
                    )
                    return
            chat_username = getattr(tg_msg.chat, "username", None) or (
                sender.username if sender else ""
            )
            chat_full_name = (getattr(tg_msg.chat, "full_name", None) or "").strip()
            chat_first = (getattr(tg_msg.chat, "first_name", None) or "").strip()
            chat_last = (getattr(tg_msg.chat, "last_name", None) or "").strip()
            chat_person_name = f"{chat_first} {chat_last}".strip()
            chat_name = (
                tg_msg.chat.title
                or chat_full_name
                or chat_person_name
                or chat_username
                or str(chat_id)
            )
            if is_business and not is_mine and chat_name:
                sender_name = chat_name

            self._cancel_delayed_reply(chat_id)

            should_reply = not is_mine
            is_group_chat = not is_business and tg_msg.chat.type in (
                ChatType.GROUP,
                ChatType.SUPERGROUP,
            )
            mentioned = False
            if is_group_chat:
                me = None
                if self.bot:
                    try:
                        me = await self.bot.me()
                    except Exception:  # noqa: BLE001
                        me = None
                handle = f"@{me.username}".lower() if me and me.username else ""
                if handle and tg_msg.text and handle in tg_msg.text.lower():
                    mentioned = True
                if handle and tg_msg.caption and handle in tg_msg.caption.lower():
                    mentioned = True
                if (
                    tg_msg.reply_to_message
                    and tg_msg.reply_to_message.from_user
                    and me
                    and tg_msg.reply_to_message.from_user.id == me.id
                ):
                    mentioned = True
                should_reply = mentioned
                if mentioned and handle and content_text:
                    cleaned = re.sub(
                        re.escape(handle), "", content_text, flags=re.IGNORECASE
                    )
                    cleaned = " ".join(cleaned.split())
                    if cleaned:
                        content_text = cleaned

            tg_ts = _naive_utc(getattr(tg_msg, "date", None))
            async with SessionLocal() as session:
                row = Message(
                    chat_id=chat_id,
                    chat_name=chat_name,
                    chat_username=chat_username,
                    sender_id=sender_id,
                    sender_name=settings.display_name if is_mine else sender_name,
                    is_mine=is_mine,
                    text=content_text,
                    timestamp=tg_ts,
                    message_id=tg_msg.message_id,
                    business_connection_id=business_connection_id,
                    media_type=media_type,
                    media_path=media_path,
                    media_private=media_private,
                    is_voice=is_voice,
                    voice_duration=voice_duration if is_voice else None,
                    voice_file_id=voice_file_id if is_voice else None,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
                msg_id = row.id

                # Index into RAG vector memory. Fire-and-forget so reply
                # generation is never blocked by embedding work.
                self._schedule_rag_index(
                    msg_id,
                    content_text,
                    chat_id,
                    chat_name,
                    row.sender_name,
                    is_mine,
                    tg_ts,
                )

                if is_group_chat:
                    group_reply_mode = await get_setting(
                        session, "group_reply_mode", "mention"
                    )
                    should_reply = (
                        True if group_reply_mode == "all" else mentioned
                    )

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
                whisper_enabled = (
                    await get_setting(session, "whisper_enabled", "1")
                ) in ("1", "true", "True")
                voice_reply_mode = await get_setting(
                    session, "voice_reply_mode", DEFAULT_VOICE_REPLY_MODE
                )
                if voice_reply_mode not in (
                    VOICE_REPLY_MODE_TEXT,
                    VOICE_REPLY_MODE_SKIP,
                    VOICE_REPLY_MODE_PENDING,
                ):
                    voice_reply_mode = DEFAULT_VOICE_REPLY_MODE
                within_schedule = await is_within_schedule(session)
                if auto_reply and should_reply and not within_schedule:
                    row.pending_reason = "schedule"
                    should_reply = False
                    await session.commit()

            await admin_bot.on_schedule_check(within_schedule)

            transcription_text: Optional[str] = None
            reply_input_text = content_text
            voice_transcribed_ok = False
            voice_download_failed = False
            voice_low_confidence = False

            # Transcribe all voice messages (both incoming and own) so the
            # dataset and history contain real text instead of "(голосовое)".
            # Reply routing happens further down only for not-is_mine messages.
            if is_voice:
                if not whisper_enabled:
                    log.info(
                        "voice msg %s skipped: whisper_enabled=False", msg_id
                    )
                elif not voice_file_id:
                    log.warning(
                        "voice msg %s skipped: no voice_file_id", msg_id
                    )
                elif voice_reply_mode == VOICE_REPLY_MODE_SKIP:
                    log.info(
                        "voice msg %s skipped: voice_reply_mode=skip", msg_id
                    )
            if (
                is_voice
                and whisper_enabled
                and voice_file_id
                and voice_reply_mode != VOICE_REPLY_MODE_SKIP
            ):
                log.info("transcribing voice msg %s (is_mine=%s)", msg_id, is_mine)
                src = await self._download_voice(voice_file_id, msg_id)
                if src:
                    result = await self._transcribe_voice_file(src, msg_id)
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.transcription = result.text
                            row_db.transcription_confidence = result.confidence
                            row_db.transcription_low_confidence = (
                                result.low_confidence
                            )
                            row_db.transcription_error = result.error
                            if not result.error and result.text:
                                row_db.text = result.text
                            await session.commit()
                            transcription_text = result.text
                            if result.text:
                                reply_input_text = result.text
                            voice_transcribed_ok = bool(
                                result.text and not result.error
                            )
                            voice_low_confidence = result.low_confidence
                    # Notify the dashboard the moment the transcription is
                    # persisted, so the card doesn't sit empty while reply
                    # routing / LLM generation finishes.
                    await message_bus.publish(
                        "transcribed",
                        {
                            "id": msg_id,
                            "chat_id": chat_id,
                            "transcription": result.text,
                            "confidence": result.confidence,
                            "low_confidence": result.low_confidence,
                            "error": result.error,
                        },
                    )
                    # Voice messages were stored as "(голосовое)"; index the
                    # real transcription now that it is available.
                    if result.text and not result.error:
                        self._schedule_rag_index(
                            msg_id,
                            result.text,
                            chat_id,
                            chat_name,
                            settings.display_name if is_mine else sender_name,
                            is_mine,
                            tg_ts,
                        )
                else:
                    voice_download_failed = True
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.transcription_error = (
                                "voice download failed"
                            )
                            await session.commit()
                    await message_bus.publish(
                        "transcribed",
                        {
                            "id": msg_id,
                            "chat_id": chat_id,
                            "transcription": "",
                            "error": "voice download failed",
                        },
                    )

            # Reply routing for incoming voice messages (skip own here).
            if is_voice and not is_mine:
                if voice_reply_mode == VOICE_REPLY_MODE_SKIP:
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.pending_reason = "voice_skipped"
                            await session.commit()
                    row.pending_reason = "voice_skipped"
                    should_reply = False
                elif not whisper_enabled:
                    reason = (
                        "voice_pending"
                        if voice_reply_mode == VOICE_REPLY_MODE_PENDING
                        else "voice_no_transcription"
                    )
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.pending_reason = reason
                            await session.commit()
                    row.pending_reason = reason
                    should_reply = False
                elif voice_download_failed or not voice_transcribed_ok:
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.pending_reason = (
                                "voice_transcription_error"
                            )
                            await session.commit()
                    row.pending_reason = "voice_transcription_error"
                    should_reply = False
                elif voice_low_confidence:
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.pending_reason = "voice_low_confidence"
                            await session.commit()
                    row.pending_reason = "voice_low_confidence"
                    should_reply = False
                elif voice_reply_mode == VOICE_REPLY_MODE_PENDING:
                    async with SessionLocal() as session:
                        sres = await session.execute(
                            select(Message).where(Message.id == msg_id)
                        )
                        row_db = sres.scalar_one_or_none()
                        if row_db:
                            row_db.pending_reason = "voice_pending"
                            await session.commit()
                    row.pending_reason = "voice_pending"
                    should_reply = False

            await message_bus.publish(
                "incoming",
                {
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
                    "is_voice": is_voice,
                    "voice_duration": voice_duration,
                    "transcription": transcription_text,
                },
            )

            if is_mine:
                log.info(
                    "handle_incoming: no reply, message is_mine (msg_id=%s "
                    "chat_type=%s chat_id=%s sender_id=%s biz_conn=%s owner_id=%s)",
                    msg_id,
                    tg_msg.chat.type,
                    tg_msg.chat.id,
                    sender_id,
                    business_connection_id,
                    owner_id,
                )
                await self._clear_queue_after_manual_reply(
                    chat_id, msg_id, content_text
                )
                await self._maybe_reanalyze(chat_id=chat_id)
                return

            # Calendar: handle a recurring proposal first ("каждый день до
            # конца недели"), so a single confirmation flow doesn't also
            # try to create a one-off at the same time.
            series_created = False
            try:
                series = await meeting_flow.process_series_proposal(
                    chat_id, sender_name, reply_input_text
                )
                if series and series.get("created"):
                    series_created = True
                    await admin_bot.notify_series_created(
                        series["created"],
                        chat_name=chat_name,
                        sender_name=sender_name,
                    )
            except Exception:  # noqa: BLE001
                log.exception("meeting series flow failed")

            # Calendar: a short "да / в среду" may confirm a meeting the bot
            # proposed earlier — turn it into a real event and notify the owner.
            if not series_created:
                try:
                    event = await meeting_flow.process_incoming_confirmation(
                        chat_id, sender_name, reply_input_text
                    )
                    if event:
                        await admin_bot.notify_meeting_created(event, chat_name)
                except Exception:  # noqa: BLE001
                    log.exception("meeting confirmation flow failed")

            # Calendar: an "отмени встречи" request actually deletes the
            # assistant-created events from the calendar (and drops any open
            # negotiation), instead of the bot only saying it will.
            try:
                cancelled = await meeting_flow.process_cancellation(
                    chat_id, sender_name, reply_input_text
                )
                if cancelled and (
                    cancelled.get("cancelled") or cancelled.get("rescheduled")
                ):
                    await admin_bot.notify_meetings_cancelled(
                        cancelled.get("cancelled", []),
                        rescheduled=cancelled.get("rescheduled", []),
                        chat_name=chat_name,
                        sender_name=sender_name,
                    )
            except Exception:  # noqa: BLE001
                log.exception("meeting cancellation flow failed")

            if not should_reply:
                log.info(
                    "handle_incoming: no reply, should_reply=False "
                    "(msg_id=%s reason=%s)",
                    msg_id,
                    row.pending_reason,
                )
                if row.pending_reason:
                    await message_bus.publish(
                        "pending", {"id": msg_id, "reason": row.pending_reason}
                    )
                await admin_bot.notify_pending(msg_id)
                return

            if not auto_reply:
                log.info("handle_incoming: no reply, auto_reply disabled (msg_id=%s)", msg_id)

            if auto_reply:
                # Don't reply per-message. Record the latest burst context and
                # (re)arm a debounced task: it waits for the contact to stop
                # sending, then composes one reply for the whole chain.
                self._pending_reply_ctx[chat_id] = {
                    "msg_id": msg_id,
                    "text": reply_input_text,
                    "sender_name": sender_name,
                    "chat_name": chat_name,
                    "business_connection_id": business_connection_id,
                    "is_voice": is_voice,
                }
                self._cancel_delayed_reply(chat_id)
                task = asyncio.create_task(self._run_debounced_reply(chat_id))
                self._delayed_reply_tasks[chat_id] = task
            else:
                await message_bus.publish(
                    "pending", {"id": msg_id, "reason": "auto_reply_disabled"}
                )
                await admin_bot.notify_pending(msg_id)

            await self._maybe_reanalyze(chat_id=chat_id)
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            log.exception("handle_incoming error")

    def _schedule_rag_index(
        self,
        msg_id: int,
        text: str,
        chat_id: int,
        chat_name: str,
        sender_name: str,
        is_mine: bool,
        timestamp: datetime,
    ) -> None:
        content = (text or "").strip()
        if not content or content in RAG_SKIP_TEXTS:
            return
        ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        metadata = {
            "chat_id": chat_id,
            "chat_name": chat_name,
            "sender_name": sender_name,
            "is_mine": bool(is_mine),
            "timestamp": ts.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        try:
            asyncio.create_task(
                rag_engine.index_message(msg_id, content, metadata)
            )
        except RuntimeError:
            # No running loop (shouldn't happen inside handlers) — skip.
            pass

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
        self,
        text: str,
        sender_name: str,
        chat_id: int,
        is_voice: bool = False,
    ) -> tuple[list[str], str]:
        async with SessionLocal() as session:
            profile = await get_profile_for_chat(session, chat_id)
            history_limit = _coerce_int(
                await get_setting(
                    session, "reply_history_limit", str(DEFAULT_REPLY_HISTORY_LIMIT)
                ),
                DEFAULT_REPLY_HISTORY_LIMIT,
            )
            history_limit = max(2, min(80, history_limit))
            history_q = await session.execute(
                select(Message)
                .where(Message.chat_id == chat_id)
                .order_by(Message.timestamp.desc())
                .limit(history_limit)
            )
            history = list(history_q.scalars().all())[::-1]
            history_dicts = [
                {"sender_name": m.sender_name, "is_mine": m.is_mine, "text": m.text}
                for m in history
            ]
            recent_min_id = min((m.id for m in history), default=0)
            summary = await self._update_chat_summary(
                session, chat_id, recent_min_id
            )
            quality_enabled = (
                await get_setting(session, "quality_filter_enabled", "1")
            ) in ("1", "true", "True")
            temperatures = await _load_reply_temperatures(session)
            try:
                rag_context, _ = await get_rag_context(session, text, chat_id)
            except Exception:  # noqa: BLE001
                log.exception("RAG context retrieval failed; replying without it")
                rag_context = ""
        client = get_client()
        # Memo of calendar actions we just performed for this chat — pulled
        # once so retries keep seeing it.
        recent_actions = meeting_flow.pop_recent_actions(chat_id)
        extra_system_context = meeting_flow.format_recent_actions(recent_actions)
        if not quality_enabled:
            variants = await client.generate_reply(
                incoming_text=text,
                sender_name=sender_name,
                style_profile=profile,
                chat_history=history_dicts,
                is_voice=is_voice,
                rag_context=rag_context,
                summary=summary,
                extra_system_context=extra_system_context,
                temperatures=temperatures,
            )
            return (variants, "ok") if variants else ([], "no_variants")
        last_reason = "no_variants"
        logged_rejection = False
        for _ in range(3):
            variants = await client.generate_reply(
                incoming_text=text,
                sender_name=sender_name,
                style_profile=profile,
                chat_history=history_dicts,
                is_voice=is_voice,
                rag_context=rag_context,
                summary=summary,
                extra_system_context=extra_system_context,
                temperatures=temperatures,
            )
            accepted: list[str] = []
            async with SessionLocal() as session:
                for variant in variants:
                    ok, reason = is_good_response(variant, text, profile)
                    if ok:
                        accepted.append(variant)
                    else:
                        last_reason = reason
                        logged_rejection = True
                        session.add(
                            QualityLog(
                                reason=reason,
                                incoming_text=text,
                                rejected_text=variant,
                            )
                        )
                await session.commit()
            if accepted:
                return accepted, "ok"
        if not logged_rejection:
            async with SessionLocal() as session:
                session.add(
                    QualityLog(
                        reason=last_reason,
                        incoming_text=text,
                        rejected_text="",
                    )
                )
                await session.commit()
        return [], last_reason

    async def _update_chat_summary(
        self, session, chat_id: int, recent_min_id: int
    ) -> str:
        """Roll the per-chat summary forward to cover messages that have aged
        out of the recent window. Returns the current summary text."""
        if not _setting_bool(await get_setting(session, "summary_enabled", "1")):
            return ""
        result = await session.execute(
            select(ChatSummary).where(ChatSummary.chat_id == chat_id)
        )
        row = result.scalar_one_or_none()
        last_id = row.last_message_id if row else 0
        prev = row.summary if row else ""

        cutoff = datetime.utcnow() - timedelta(days=SUMMARY_MAX_AGE_DAYS)
        conds = [
            Message.chat_id == chat_id,
            Message.id > last_id,
            Message.deleted == False,  # noqa: E712
            Message.timestamp >= cutoff,
        ]
        if recent_min_id:
            conds.append(Message.id < recent_min_id)
        pending_q = await session.execute(
            select(Message)
            .where(*conds)
            .order_by(Message.id.desc())
            .limit(SUMMARY_BATCH_LIMIT)
        )
        pending = list(pending_q.scalars().all())[::-1]
        if not pending:
            return prev

        lines: list[str] = []
        for m in pending:
            who = "Я" if m.is_mine else (m.sender_name or "собеседник")
            txt = (m.text or "").replace("\n", " ").strip()
            if txt:
                if len(txt) > SUMMARY_LINE_MAX_CHARS:
                    txt = txt[:SUMMARY_LINE_MAX_CHARS] + "…"
                lines.append(f"{who}: {txt}")
        new_last = max(m.id for m in pending)
        if lines:
            prev = await get_client().summarize(prev, lines) or prev

        if row:
            row.summary = prev
            row.last_message_id = new_last
            row.updated_at = datetime.utcnow()
        else:
            session.add(
                ChatSummary(
                    chat_id=chat_id,
                    summary=prev,
                    last_message_id=new_last,
                    updated_at=datetime.utcnow(),
                )
            )
        await session.commit()
        return prev

    async def _run_debounced_reply(self, chat_id: int) -> None:
        """Wait out the contact's message burst, then compose one reply for
        the whole chain. Re-armed (and the prior task cancelled) on every new
        incoming message, so only the final quiet state is answered."""
        ctx: Optional[dict] = None
        try:
            async with SessionLocal() as session:
                settle = _coerce_int(
                    await get_setting(
                        session, "reply_settle_seconds", str(DEFAULT_SETTLE_SECONDS)
                    ),
                    DEFAULT_SETTLE_SECONDS,
                )
            settle = max(0, min(MAX_SETTLE_SECONDS, settle))
            if settle:
                await asyncio.sleep(settle)

            ctx = self._pending_reply_ctx.get(chat_id)
            if not ctx:
                return
            msg_id = ctx["msg_id"]
            chat_name = ctx["chat_name"]
            sender_name = ctx["sender_name"]
            reply_input_text = ctx["text"]

            variants, reject_reason = await self._generate_variants(
                reply_input_text, sender_name, chat_id, is_voice=ctx["is_voice"]
            )
            chosen = pick_auto_variant(variants)
            if not chosen:
                log.info(
                    "debounced reply: no usable variant (chat=%s reason=%s)",
                    chat_id,
                    reject_reason,
                )
                await self._mark_pending_quality(msg_id, reject_reason)
                await self._maybe_reanalyze(chat_id=chat_id)
                return

            await self._delay_before_auto_reply(chat_id, chat_name)
            parts = _split_reply_messages(chosen)
            for idx, part in enumerate(parts):
                await self.send_reply(
                    chat_id,
                    part,
                    business_connection_id=ctx["business_connection_id"],
                )
                if idx + 1 < len(parts):
                    await asyncio.sleep(random.uniform(0.8, 2.2))

            await self._record_reply(
                msg_id, chosen, sender_name, chat_id, chat_name
            )
            # If the contact asked for a meeting, remember the slots offered
            # so a later confirmation can be matched to a concrete time.
            await meeting_flow.maybe_record_pending_meeting(
                chat_id, msg_id, sender_name, reply_input_text
            )
            await admin_bot.notify_auto_reply(
                chat_name,
                sender_name,
                reply_input_text,
                chosen,
                message_id=msg_id,
            )
            await self._maybe_reanalyze(chat_id=chat_id)
        except asyncio.CancelledError:
            log.info("debounced reply cancelled for chat %s", chat_id)
            return
        except LLMUnavailableError as e:
            log.error("LLM unavailable: %s", e)
            self.last_error = str(e)
        except TelegramBadRequest as e:
            if "BUSINESS_PEER_INVALID" in str(e):
                log.error(
                    "auto reply blocked by business privacy for chat %s: %s",
                    chat_id,
                    e,
                )
                self.last_error = (
                    "Telegram business privacy blocks the bot for this chat "
                    "(BUSINESS_PEER_INVALID). Check Settings → Business → "
                    "Chatbots and include this contact."
                )
                if ctx:
                    await message_bus.publish("pending", {"id": ctx["msg_id"]})
            else:
                log.exception("auto reply failed: %s", e)
                self.last_error = str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("debounced auto reply failed: %s", e)
            self.last_error = str(e)
        finally:
            current = asyncio.current_task()
            if self._delayed_reply_tasks.get(chat_id) is current:
                self._delayed_reply_tasks.pop(chat_id, None)
            if ctx is not None and self._pending_reply_ctx.get(chat_id) is ctx:
                self._pending_reply_ctx.pop(chat_id, None)

    async def _mark_pending_quality(self, msg_id: int, reason: str) -> None:
        async with SessionLocal() as session:
            result = await session.execute(select(Message).where(Message.id == msg_id))
            row = result.scalar_one_or_none()
            if row:
                row.pending_reason = "quality_filter"
                await session.commit()
        log.warning(
            "quality filter rejected all attempts for message %s: %s", msg_id, reason
        )
        await message_bus.publish(
            "pending",
            {"id": msg_id, "reason": "quality_filter", "quality_reason": reason},
        )
        await admin_bot.notify_pending(msg_id)

    async def _clear_queue_after_manual_reply(
        self, chat_id: int, owner_msg_id: int, owner_text: str
    ) -> None:
        """Mark earlier unanswered incoming messages as handled.

        When the owner replies manually in Telegram, the bot sees the message
        as ``is_mine`` but never sent it itself. Without this, those incoming
        messages stay in the pending queue forever even though they were
        already answered.
        """
        async with SessionLocal() as session:
            result = await session.execute(
                select(Message.id).where(
                    Message.chat_id == chat_id,
                    Message.is_mine == False,  # noqa: E712
                    Message.replied == False,  # noqa: E712
                    Message.id < owner_msg_id,
                )
            )
            ids = [row[0] for row in result.all()]
            if not ids:
                return
            await session.execute(
                update(Message)
                .where(Message.id.in_(ids))
                .values(replied=True, pending_reason=None, reply_text=owner_text)
            )
            await session.commit()
        log.info(
            "manual reply cleared %s queued message(s) in chat %s",
            len(ids),
            chat_id,
        )
        await message_bus.publish(
            "queue_cleared", {"chat_id": chat_id, "ids": ids}
        )

    async def _record_reply(
        self,
        original_id: int,
        reply_text: str,
        sender_name: str,
        chat_id: int,
        chat_name: str,
    ) -> None:
        # One reply answers the whole burst: mark every still-unanswered
        # incoming message up to the one replied to as handled, not just the
        # last. Otherwise earlier messages of the chain stay "pending".
        cleared_ids: list[int] = []
        async with SessionLocal() as session:
            result = await session.execute(
                select(Message).where(
                    Message.chat_id == chat_id,
                    Message.is_mine == False,  # noqa: E712
                    Message.replied == False,  # noqa: E712
                    Message.id <= original_id,
                )
            )
            burst = list(result.scalars().all())
            for m in burst:
                m.replied = True
                m.reply_text = reply_text
                m.pending_reason = None
                if m.id != original_id:
                    cleared_ids.append(m.id)
            session.add(
                Message(
                    chat_id=chat_id,
                    chat_name=chat_name,
                    sender_id=0,
                    sender_name=settings.display_name,
                    is_mine=True,
                    text=reply_text,
                    timestamp=datetime.utcnow(),
                )
            )
            await session.commit()
        await message_bus.publish(
            "sent",
            {
                "chat_id": chat_id,
                "chat_name": chat_name,
                "to": sender_name,
                "text": reply_text,
                "timestamp": _iso_utc_now(),
                "original_id": original_id,
            },
        )
        if cleared_ids:
            await message_bus.publish(
                "queue_cleared", {"chat_id": chat_id, "ids": cleared_ids}
            )

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
        self,
        chat_id: int,
        text: str,
        reply_to: Optional[int] = None,
        original_id: Optional[int] = None,
        business_connection_id: Optional[str] = None,
    ) -> None:
        await self.send_reply(
            chat_id,
            text,
            reply_to=reply_to,
            business_connection_id=business_connection_id,
        )
        async with SessionLocal() as session:
            chat_name = ""
            if original_id is not None:
                result = await session.execute(
                    select(Message).where(Message.id == original_id)
                )
                original = result.scalar_one_or_none()
                if original:
                    chat_name = original.chat_name
                    original.replied = True
                    original.reply_text = text
            session.add(
                Message(
                    chat_id=chat_id,
                    chat_name=chat_name,
                    sender_id=0,
                    sender_name=settings.display_name,
                    is_mine=True,
                    text=text,
                    timestamp=datetime.utcnow(),
                )
            )
            await session.commit()
        await message_bus.publish(
            "sent",
            {
                "chat_id": chat_id,
                "text": text,
                "timestamp": _iso_utc_now(),
                "original_id": original_id,
            },
        )

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
            "last_error_date": (
                info.last_error_date.isoformat() if info.last_error_date else None
            ),
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

    async def _maybe_reanalyze(self, chat_id: int) -> None:
        self._chat_new_my_messages[chat_id] = self._chat_new_my_messages.get(chat_id, 0) + 1
        if self._chat_new_my_messages[chat_id] >= 10:
            self._chat_new_my_messages[chat_id] = 0
            try:
                async with SessionLocal() as session:
                    await reanalyze_chat_persona(session, chat_id)
            except Exception:  # noqa: BLE001
                log.exception("chat persona reanalyze failed")
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
