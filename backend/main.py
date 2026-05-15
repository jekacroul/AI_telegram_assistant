from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime as ISO 8601 UTC with a Z suffix.

    The DB stores naive UTC (from datetime.utcnow() and Telegram's UTC
    message.date). The Z marker is required so JS new Date() parses the
    string as UTC instead of local time.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


from aiogram.exceptions import TelegramBadRequest
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Integer, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .bot import telegram_service
from .config import ROOT_DIR, settings
from .logging_setup import setup_logging
from .database import (
    DialogBackup,
    DialogBackupMessage,
    Message,
    QualityLog,
    QuickReply,
    SessionLocal,
    TrainingPair,
    TrainingRun,
    get_session,
    get_setting,
    init_db,
    set_setting,
)
from .dataset_builder import build_dataset_file, dataset_stats
from .delay import delay_to_dict, get_delay_settings, save_delay_settings
from .dialog_backup import (
    SETTING_INTERVAL,
    SETTING_LAST_RUN,
    get_excluded_chats,
    get_interval_minutes,
    scheduler as dialog_backup_scheduler,
    set_excluded_chats,
)
from .event_bus import message_bus, replication_bus, training_bus
from .replication import (
    cancel_replication as cancel_replication_run,
    delete_run as delete_replication_run,
    get_run_log as get_replication_run_log,
    list_runs as list_replication_runs,
    load_settings as load_replication_settings,
    replication_state,
    run_replication,
    save_settings as save_replication_settings,
    scheduler as replication_scheduler,
    status as replication_status,
)
from .llm_engine import LLMUnavailableError, get_client
from .quality_filter import is_good_response
from .notifications import (
    SETTING_LAST_PRIVATE_CHAT_ID,
    SETTING_NOTIFY_CHAT_ID,
    SETTING_NOTIFY_ENABLED,
)
from .schedule import (
    DEFAULT_SCHEDULE_DAYS,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SCHEDULE_END,
    DEFAULT_SCHEDULE_START,
    DEFAULT_SCHEDULE_TIMEZONE,
    get_schedule_settings,
    save_schedule_settings,
    schedule_to_dict,
    validate_schedule_payload,
)
from .whisper_engine import (
    DEFAULT_MODEL as WHISPER_DEFAULT_MODEL,
    DEFAULT_LANGUAGE as WHISPER_DEFAULT_LANGUAGE,
    whisper_engine,
)
from .style_engine import (
    get_profile_for_chat,
    get_latest_profile,
    list_chat_personas,
    reanalyze_chat_persona,
    reanalyze_and_store,
    save_manual_profile,
)
from .trainer import (
    activate_adapter,
    cancel_training,
    deactivate_adapter,
    delete_training_run,
    export_gguf_for_run,
    export_lora_gguf_for_run,
    get_training_run_error_log,
    list_runs,
    start_training,
    training_state,
)
from . import llama_server

setup_logging(settings.logs_dir, level=logging.INFO)
log = logging.getLogger(__name__)


TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
MEDIA_PLACEHOLDERS = {"(фото)", "(видео)", "(кружок)"}


def _normalize_username(username: Optional[str]) -> str:
    return (username or "").strip().lstrip("@")


def _is_usable_contact_name(name: Optional[str], username: str = "") -> bool:
    value = (name or "").strip()
    if not value:
        return False
    lowered = value.casefold()
    if lowered in {"unknown", settings.user_name.casefold()}:
        return False
    normalized_username = _normalize_username(username).casefold()
    return not normalized_username or lowered != normalized_username


def _name_rank(name: str) -> tuple[int, int, int]:
    stripped = name.strip()
    return (
        int(" " in stripped),
        int(not TELEGRAM_USERNAME_RE.fullmatch(stripped)),
        len(stripped),
    )


async def _best_contact_name(
    session: AsyncSession, chat_id: int, username: str
) -> Optional[str]:
    candidates: list[str] = []

    messages = await session.execute(
        select(Message.sender_name)
        .where(
            Message.chat_id == chat_id,
            Message.is_mine == False,  # noqa: E712
        )
        .order_by(Message.timestamp.desc())
        .limit(100)
    )
    candidates.extend(row.sender_name for row in messages.all())

    backups = await session.execute(
        select(DialogBackupMessage.sender_name)
        .where(
            DialogBackupMessage.chat_id == chat_id,
            DialogBackupMessage.is_mine == False,  # noqa: E712
        )
        .order_by(DialogBackupMessage.timestamp.desc())
        .limit(100)
    )
    candidates.extend(row.sender_name for row in backups.all())

    usable = [
        name.strip() for name in candidates if _is_usable_contact_name(name, username)
    ]
    if not usable:
        return None
    return max(usable, key=_name_rank)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.notify_chat_id:
        async with SessionLocal() as session:
            current = await get_setting(session, SETTING_NOTIFY_CHAT_ID, "")
            if not current:
                await set_setting(
                    session, SETTING_NOTIFY_CHAT_ID, settings.notify_chat_id
                )
    token = settings.telegram_bot_token
    if token:
        try:
            await telegram_service.setup(token)
        except Exception:  # noqa: BLE001
            log.exception("bot setup failed at startup")
    else:
        log.warning("TELEGRAM_BOT_TOKEN is not set; bot will be inactive")
    dialog_backup_scheduler.start()
    replication_scheduler.start()
    if settings.llama_server_auto_start and settings.llama_base_model_gguf:
        asyncio.create_task(_auto_start_llama_server())
    yield
    await replication_scheduler.stop()
    await dialog_backup_scheduler.stop()
    await telegram_service.shutdown()
    await llama_server.stop()


async def _auto_start_llama_server() -> None:
    try:
        result = await llama_server.start()
        if not result.get("started"):
            log.warning("auto-start llama-server failed: %s", result.get("reason"))
    except Exception:
        log.exception("auto-start llama-server crashed")


app = FastAPI(title="Telegram Local AI Assistant", lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(settings.media_dir)), name="media")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def status() -> dict:
    llm_ok = await get_client().health()
    bot_ok = telegram_service.is_configured
    db_ok = True
    try:
        async with SessionLocal() as session:
            await session.execute(select(func.count(Message.id)))
    except Exception:  # noqa: BLE001
        db_ok = False
    async with SessionLocal() as session:
        auto_reply = (
            await get_setting(
                session, "auto_reply", "1" if settings.auto_reply else "0"
            )
        ) in ("1", "true", "True")
        llm_model = await get_setting(session, "llm_model", settings.openai_model)
    return {
        "llm": llm_ok,
        "bot": bot_ok,
        "db": db_ok,
        "auto_reply": auto_reply,
        "llm_model": llm_model,
        "user_name": settings.user_name,
        "last_update_at": _iso_utc(telegram_service.last_update_at),
        "last_update_kind": telegram_service.last_update_kind,
        "update_count": telegram_service.update_count,
        "last_error": telegram_service.last_error,
    }


@app.get("/api/chats")
async def list_chats(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(
            Message.chat_id,
            Message.chat_name,
            func.count(Message.id).label("count"),
            func.max(Message.timestamp).label("last"),
        ).group_by(Message.chat_id, Message.chat_name)
    )
    rows = result.all()
    monitored_csv = await get_setting(session, "monitored_chats", "")
    monitored = {int(x) for x in monitored_csv.split(",") if x.strip()}
    return [
        {
            "chat_id": r.chat_id,
            "chat_name": r.chat_name,
            "count": r.count,
            "last": _iso_utc(r.last),
            "monitored": (r.chat_id in monitored) if monitored else True,
        }
        for r in rows
    ]


class SettingsIn(BaseModel):
    auto_reply: Optional[bool] = None
    monitored_chats: Optional[list[int]] = None
    llm_model: Optional[str] = None
    persona_mode: Optional[str] = None
    group_reply_mode: Optional[str] = None


class QuickReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=255)
    category: str = Field(default="general", min_length=1, max_length=64)


class QuickReplyUpdateIn(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=255)
    category: Optional[str] = Field(default=None, min_length=1, max_length=64)


def _quick_reply_to_dict(reply: QuickReply) -> dict:
    return {
        "id": reply.id,
        "text": reply.text,
        "category": reply.category,
        "usage_count": reply.usage_count,
        "created_at": _iso_utc(reply.created_at),
    }


@app.get("/api/quick-replies")
async def list_quick_replies(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(QuickReply).order_by(desc(QuickReply.usage_count), desc(QuickReply.created_at))
    )
    return [_quick_reply_to_dict(row) for row in result.scalars().all()]


@app.post("/api/quick-replies")
async def create_quick_reply(
    payload: QuickReplyIn, session: AsyncSession = Depends(get_session)
) -> dict:
    reply = QuickReply(text=payload.text.strip(), category=payload.category.strip())
    session.add(reply)
    await session.commit()
    await session.refresh(reply)
    return _quick_reply_to_dict(reply)


@app.put("/api/quick-replies/{reply_id}")
async def update_quick_reply(
    reply_id: int, payload: QuickReplyUpdateIn, session: AsyncSession = Depends(get_session)
) -> dict:
    reply = await session.get(QuickReply, reply_id)
    if not reply:
        raise HTTPException(404, "Quick reply not found")
    if payload.text is not None:
        reply.text = payload.text.strip()
    if payload.category is not None:
        reply.category = payload.category.strip()
    await session.commit()
    await session.refresh(reply)
    return _quick_reply_to_dict(reply)


@app.delete("/api/quick-replies/{reply_id}", status_code=204)
async def delete_quick_reply(reply_id: int, session: AsyncSession = Depends(get_session)) -> None:
    reply = await session.get(QuickReply, reply_id)
    if not reply:
        raise HTTPException(404, "Quick reply not found")
    await session.delete(reply)
    await session.commit()


@app.post("/api/quick-replies/{reply_id}/use")
async def use_quick_reply(reply_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    reply = await session.get(QuickReply, reply_id)
    if not reply:
        raise HTTPException(404, "Quick reply not found")
    reply.usage_count += 1
    await session.commit()
    await session.refresh(reply)
    return _quick_reply_to_dict(reply)


class DelayIn(BaseModel):
    delay_enabled: bool = False
    delay_min_seconds: int = Field(default=60, ge=30, le=600)
    delay_max_seconds: int = Field(default=180, ge=30, le=600)


class ScheduleIn(BaseModel):
    enabled: bool = DEFAULT_SCHEDULE_ENABLED
    timezone: str = DEFAULT_SCHEDULE_TIMEZONE
    days: list[int] = Field(default_factory=lambda: DEFAULT_SCHEDULE_DAYS.copy())
    start: str = DEFAULT_SCHEDULE_START
    end: str = DEFAULT_SCHEDULE_END


@app.post("/api/settings")
async def save_settings(
    payload: SettingsIn, session: AsyncSession = Depends(get_session)
) -> dict:
    if payload.auto_reply is not None:
        await set_setting(session, "auto_reply", "1" if payload.auto_reply else "0")
    if payload.monitored_chats is not None:
        csv = ",".join(str(x) for x in payload.monitored_chats)
        await set_setting(session, "monitored_chats", csv)
    if payload.llm_model is not None:
        await set_setting(session, "llm_model", payload.llm_model)
        get_client().model = payload.llm_model
    if payload.persona_mode is not None:
        if payload.persona_mode not in ("global", "per_chat"):
            raise HTTPException(400, "persona_mode must be global or per_chat")
        await set_setting(session, "persona_mode", payload.persona_mode)
    if payload.group_reply_mode is not None:
        if payload.group_reply_mode not in ("mention", "all"):
            raise HTTPException(400, "group_reply_mode must be mention or all")
        await set_setting(session, "group_reply_mode", payload.group_reply_mode)
    return {"ok": True}


@app.get("/api/settings")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict:
    auto_reply = (
        await get_setting(session, "auto_reply", "1" if settings.auto_reply else "0")
    ) in ("1", "true", "True")
    monitored_csv = await get_setting(session, "monitored_chats", "")
    monitored = [int(x) for x in monitored_csv.split(",") if x.strip()]
    llm_model = await get_setting(session, "llm_model", settings.openai_model)
    persona_mode = await get_setting(session, "persona_mode", "global")
    group_reply_mode = await get_setting(session, "group_reply_mode", "mention")
    return {
        "auto_reply": auto_reply,
        "monitored_chats": monitored,
        "llm_model": llm_model,
        "persona_mode": persona_mode,
        "group_reply_mode": group_reply_mode,
    }


@app.get("/api/delay")
async def get_delay(session: AsyncSession = Depends(get_session)) -> dict:
    delay = await get_delay_settings(session)
    return delay_to_dict(delay)


@app.post("/api/delay")
async def save_delay(
    payload: DelayIn, session: AsyncSession = Depends(get_session)
) -> dict:
    delay = await save_delay_settings(
        session,
        payload.delay_enabled,
        payload.delay_min_seconds,
        payload.delay_max_seconds,
    )
    return {"ok": True, **delay_to_dict(delay)}


@app.get("/api/schedule")
async def get_schedule(session: AsyncSession = Depends(get_session)) -> dict:
    schedule = await get_schedule_settings(session)
    return schedule_to_dict(schedule)


@app.post("/api/schedule")
async def save_schedule(
    payload: ScheduleIn, session: AsyncSession = Depends(get_session)
) -> dict:
    try:
        schedule = validate_schedule_payload(
            enabled=payload.enabled,
            timezone=payload.timezone,
            days=payload.days,
            start=payload.start,
            end=payload.end,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await save_schedule_settings(session, schedule)
    return {"ok": True, **schedule_to_dict(schedule)}


class NotifyChatIn(BaseModel):
    chat_id: Optional[str] = None
    enabled: Optional[bool] = None


@app.get("/api/settings/notify-chat")
async def get_notify_chat(session: AsyncSession = Depends(get_session)) -> dict:
    chat_id = await get_setting(
        session, SETTING_NOTIFY_CHAT_ID, settings.notify_chat_id
    )
    enabled = (await get_setting(session, SETTING_NOTIFY_ENABLED, "1")) in (
        "1",
        "true",
        "True",
    )
    return {"chat_id": chat_id, "enabled": enabled}


@app.post("/api/settings/notify-chat")
async def save_notify_chat(
    payload: NotifyChatIn, session: AsyncSession = Depends(get_session)
) -> dict:
    if payload.chat_id is not None:
        value = payload.chat_id.strip()
        if value:
            try:
                int(value)
            except ValueError:
                raise HTTPException(400, "chat_id must be an integer")
        await set_setting(session, SETTING_NOTIFY_CHAT_ID, value)
    if payload.enabled is not None:
        await set_setting(
            session, SETTING_NOTIFY_ENABLED, "1" if payload.enabled else "0"
        )
    return {"ok": True}


VOICE_REPLY_MODES = {"text", "skip", "pending"}
WHISPER_MODEL_CHOICES = {"tiny", "base", "small", "medium", "large-v3"}


class WhisperSettingsIn(BaseModel):
    whisper_enabled: Optional[bool] = None
    whisper_model: Optional[str] = None
    whisper_language: Optional[str] = None
    voice_reply_mode: Optional[str] = None
    whisper_lazy_load: Optional[bool] = None


class WhisperTranscribeIn(BaseModel):
    message_id: int


@app.get("/api/whisper/status")
async def whisper_status() -> dict:
    return {
        "model_loaded": whisper_engine.is_loaded,
        "model_name": whisper_engine.model_name or None,
        "device": whisper_engine.device,
        "vram_used_mb": whisper_engine.vram_used_mb(),
        "ffmpeg_available": whisper_engine.ffmpeg_available(),
        "last_error": whisper_engine.last_error or None,
    }


@app.post("/api/whisper/unload")
async def whisper_unload() -> dict:
    whisper_engine.unload()
    return {"ok": True}


async def _transcribe_message(
    session: AsyncSession, message_id: int
) -> dict:
    result = await session.execute(select(Message).where(Message.id == message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    if not getattr(msg, "is_voice", False) or not msg.voice_file_id:
        raise HTTPException(400, "not a voice message")
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    src = await telegram_service._download_voice(msg.voice_file_id, msg.id)
    if not src:
        raise HTTPException(500, "voice download failed")
    tr = await telegram_service._transcribe_voice_file(src, msg.id)
    msg.transcription = tr.text
    msg.transcription_confidence = tr.confidence
    msg.transcription_low_confidence = tr.low_confidence
    msg.transcription_error = tr.error
    if not tr.error and tr.text:
        msg.text = tr.text
    await session.commit()
    await message_bus.publish(
        "transcribed",
        {
            "id": msg.id,
            "chat_id": msg.chat_id,
            "transcription": tr.text,
            "confidence": tr.confidence,
            "low_confidence": tr.low_confidence,
            "error": tr.error,
        },
    )
    return {
        "transcription": tr.text,
        "confidence": tr.confidence,
        "duration": tr.duration,
        "low_confidence": tr.low_confidence,
        "error": tr.error,
    }


@app.post("/api/whisper/transcribe")
async def whisper_transcribe(
    payload: WhisperTranscribeIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _transcribe_message(session, payload.message_id)


@app.post("/api/whisper/retranscribe")
async def whisper_retranscribe(
    payload: WhisperTranscribeIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await _transcribe_message(session, payload.message_id)


@app.get("/api/settings/whisper")
async def get_whisper_settings(
    session: AsyncSession = Depends(get_session),
) -> dict:
    enabled = (
        await get_setting(session, "whisper_enabled", "1")
    ) in ("1", "true", "True")
    model = await get_setting(session, "whisper_model", WHISPER_DEFAULT_MODEL)
    language = await get_setting(
        session, "whisper_language", WHISPER_DEFAULT_LANGUAGE
    )
    mode = await get_setting(session, "voice_reply_mode", "text")
    if mode not in VOICE_REPLY_MODES:
        mode = "text"
    lazy = (
        await get_setting(session, "whisper_lazy_load", "0")
    ) in ("1", "true", "True")
    return {
        "whisper_enabled": enabled,
        "whisper_model": model,
        "whisper_language": language,
        "voice_reply_mode": mode,
        "whisper_lazy_load": lazy,
        "device": whisper_engine.device,
        "model_loaded": whisper_engine.is_loaded,
        "ffmpeg_available": whisper_engine.ffmpeg_available(),
    }


@app.post("/api/settings/whisper")
async def save_whisper_settings(
    payload: WhisperSettingsIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.whisper_enabled is not None:
        await set_setting(
            session, "whisper_enabled", "1" if payload.whisper_enabled else "0"
        )
    if payload.whisper_model is not None:
        if payload.whisper_model not in WHISPER_MODEL_CHOICES:
            raise HTTPException(
                400,
                f"whisper_model must be one of {sorted(WHISPER_MODEL_CHOICES)}",
            )
        await set_setting(session, "whisper_model", payload.whisper_model)
    if payload.whisper_language is not None:
        await set_setting(session, "whisper_language", payload.whisper_language)
    if payload.voice_reply_mode is not None:
        if payload.voice_reply_mode not in VOICE_REPLY_MODES:
            raise HTTPException(
                400, "voice_reply_mode must be one of: text, skip, pending"
            )
        await set_setting(
            session, "voice_reply_mode", payload.voice_reply_mode
        )
    if payload.whisper_lazy_load is not None:
        await set_setting(
            session,
            "whisper_lazy_load",
            "1" if payload.whisper_lazy_load else "0",
        )
    return {"ok": True}


@app.get("/api/stats/voice")
async def voice_stats(session: AsyncSession = Depends(get_session)) -> dict:
    total_q = await session.execute(
        select(func.count(Message.id)).where(Message.is_voice == True)  # noqa: E712
    )
    transcribed_q = await session.execute(
        select(func.count(Message.id)).where(
            Message.is_voice == True,  # noqa: E712
            Message.transcription.is_not(None),
            Message.transcription != "",
        )
    )
    low_q = await session.execute(
        select(func.count(Message.id)).where(
            Message.is_voice == True,  # noqa: E712
            Message.transcription_low_confidence == True,  # noqa: E712
        )
    )
    avg_q = await session.execute(
        select(func.avg(Message.transcription_confidence)).where(
            Message.is_voice == True,  # noqa: E712
            Message.transcription_confidence.is_not(None),
        )
    )
    total = total_q.scalar() or 0
    transcribed = transcribed_q.scalar() or 0
    low = low_q.scalar() or 0
    avg_conf = float(avg_q.scalar() or 0.0)
    return {
        "voice_received": total,
        "voice_transcribed": transcribed,
        "voice_low_confidence": low,
        "avg_confidence": round(avg_conf, 3),
    }


@app.get("/api/settings/notify-chat/detect")
async def detect_notify_chat(session: AsyncSession = Depends(get_session)) -> dict:
    chat_id = await get_setting(session, SETTING_LAST_PRIVATE_CHAT_ID, "")
    if not chat_id:
        raise HTTPException(
            404,
            "Не удалось определить chat_id. Напишите боту /start в личку и попробуйте снова.",
        )
    return {"chat_id": chat_id}


@app.get("/api/messages/pending")
async def pending(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(Message)
        .where(Message.is_mine == False, Message.replied == False)  # noqa: E712
        .order_by(desc(Message.timestamp))
        .limit(100)
    )
    return [_message_to_dict(m) for m in result.scalars().all()]


@app.get("/api/messages/recent")
async def recent(
    limit: int = 100, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    result = await session.execute(
        select(Message).order_by(desc(Message.timestamp)).limit(limit)
    )
    return [_message_to_dict(m) for m in result.scalars().all()]


def _message_to_dict(m: Message) -> dict:
    text = m.text
    if text in MEDIA_PLACEHOLDERS and m.media_path:
        text = ""
    return {
        "id": m.id,
        "chat_id": m.chat_id,
        "chat_name": m.chat_name,
        "chat_username": m.chat_username,
        "sender_id": m.sender_id,
        "sender_name": m.sender_name,
        "is_mine": m.is_mine,
        "text": text,
        "timestamp": _iso_utc(m.timestamp),
        "message_id": m.message_id,
        "replied": m.replied,
        "reply_text": m.reply_text,
        "pending_reason": m.pending_reason,
        "media_type": m.media_type,
        "media_path": m.media_path,
        "media_private": m.media_private,
        "is_voice": bool(getattr(m, "is_voice", False)),
        "voice_duration": getattr(m, "voice_duration", None),
        "voice_file_id": getattr(m, "voice_file_id", None),
        "transcription": getattr(m, "transcription", None),
        "transcription_confidence": getattr(m, "transcription_confidence", None),
        "transcription_low_confidence": bool(
            getattr(m, "transcription_low_confidence", False)
        ),
        "transcription_error": getattr(m, "transcription_error", None),
    }


class GenerateIn(BaseModel):
    message_id: int
    transcription_override: Optional[str] = None


@app.post("/api/reply/generate")
async def generate_reply(
    payload: GenerateIn, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await session.execute(
        select(Message).where(Message.id == payload.message_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    profile = await get_profile_for_chat(session, msg.chat_id)
    history_q = await session.execute(
        select(Message)
        .where(Message.chat_id == msg.chat_id)
        .order_by(desc(Message.timestamp))
        .limit(8)
    )
    history = list(history_q.scalars().all())[::-1]
    history_dicts = [
        {"sender_name": m.sender_name, "is_mine": m.is_mine, "text": m.text}
        for m in history
    ]
    incoming_text = msg.text
    is_voice = bool(getattr(msg, "is_voice", False))
    if is_voice and getattr(msg, "transcription", None):
        incoming_text = msg.transcription
    if is_voice and payload.transcription_override is not None:
        override = payload.transcription_override.strip()
        if override:
            incoming_text = override
            msg.transcription = override
            msg.text = override
            await session.commit()
    try:
        variants, reason = await _generate_quality_variants(
            incoming_text=incoming_text,
            sender_name=msg.sender_name,
            style_profile=profile,
            chat_history=history_dicts,
            is_voice=is_voice,
        )
    except LLMUnavailableError as e:
        raise HTTPException(503, str(e))
    if not variants:
        msg.pending_reason = "quality_filter"
        await session.commit()
        await message_bus.publish(
            "pending",
            {"id": msg.id, "reason": "quality_filter", "quality_reason": reason},
        )
        return {"variants": [], "pending": True, "reason": reason}
    return {"variants": variants}


async def _generate_quality_variants(
    incoming_text: str,
    sender_name: str,
    style_profile: dict | None,
    chat_history: list[dict],
    is_voice: bool = False,
) -> tuple[list[str], str]:
    client = get_client()
    last_reason = "no_variants"
    logged_rejection = False
    for _ in range(3):
        variants = await client.generate_reply(
            incoming_text=incoming_text,
            sender_name=sender_name,
            style_profile=style_profile,
            chat_history=chat_history,
            is_voice=is_voice,
        )
        accepted: list[str] = []
        for variant in variants:
            ok, reason = is_good_response(variant, incoming_text, style_profile)
            if ok:
                accepted.append(variant)
            else:
                last_reason = reason
                logged_rejection = True
                async with SessionLocal() as log_session:
                    log_session.add(
                        QualityLog(
                            reason=reason,
                            incoming_text=incoming_text,
                            rejected_text=variant,
                        )
                    )
                    await log_session.commit()
        if accepted:
            return accepted, "ok"
    if not logged_rejection:
        async with SessionLocal() as log_session:
            log_session.add(
                QualityLog(
                    reason=last_reason,
                    incoming_text=incoming_text,
                    rejected_text="",
                )
            )
            await log_session.commit()
    return [], last_reason


class ApproveIn(BaseModel):
    message_id: int
    text: str


@app.post("/api/reply/approve")
async def approve_reply(
    payload: ApproveIn, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await session.execute(
        select(Message).where(Message.id == payload.message_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    try:
        await telegram_service.send_and_record(
            msg.chat_id,
            payload.text,
            reply_to=msg.message_id,
            original_id=msg.id,
            business_connection_id=msg.business_connection_id,
        )
    except TelegramBadRequest as e:
        raise HTTPException(400, _telegram_error_message(e))
    session.add(
        TrainingPair(
            input_text=msg.text,
            output_text=payload.text,
            chat_id=msg.chat_id,
            timestamp=datetime.utcnow(),
            feedback="good",
        )
    )
    await session.commit()
    return {"ok": True}


class SendIn(BaseModel):
    chat_id: int
    text: str
    reply_to: Optional[int] = None
    original_id: Optional[int] = None
    business_connection_id: Optional[str] = None


@app.post("/api/reply/send")
async def send_reply(payload: SendIn) -> dict:
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    business_connection_id = payload.business_connection_id
    if business_connection_id is None and payload.original_id is not None:
        async with SessionLocal() as session:
            result = await session.execute(
                select(Message).where(Message.id == payload.original_id)
            )
            original = result.scalar_one_or_none()
            if original:
                business_connection_id = original.business_connection_id
    try:
        await telegram_service.send_and_record(
            payload.chat_id,
            payload.text,
            reply_to=payload.reply_to,
            original_id=payload.original_id,
            business_connection_id=business_connection_id,
        )
    except TelegramBadRequest as e:
        raise HTTPException(400, _telegram_error_message(e))
    return {"ok": True}


def _telegram_error_message(exc: TelegramBadRequest) -> str:
    raw = str(exc)
    if "BUSINESS_PEER_INVALID" in raw:
        return (
            "Telegram business privacy blocks the bot for this chat "
            "(BUSINESS_PEER_INVALID). Open Telegram → Settings → Business → "
            "Chatbots and make sure this contact is included."
        )
    return raw


class FeedbackIn(BaseModel):
    message_id: int
    feedback: str
    corrected_text: Optional[str] = None


@app.post("/api/reply/feedback")
async def reply_feedback(
    payload: FeedbackIn, session: AsyncSession = Depends(get_session)
) -> dict:
    if payload.feedback not in ("good", "bad"):
        raise HTTPException(400, "feedback must be good or bad")
    result = await session.execute(
        select(Message).where(Message.id == payload.message_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    out_text = payload.corrected_text or msg.reply_text or ""
    if payload.feedback == "bad" and payload.corrected_text:
        if telegram_service.is_configured:
            try:
                await telegram_service.send_reply(
                    msg.chat_id,
                    payload.corrected_text,
                    business_connection_id=msg.business_connection_id,
                )
            except Exception:  # noqa: BLE001
                log.exception("re-send failed")
        msg.reply_text = payload.corrected_text
    session.add(
        TrainingPair(
            input_text=msg.text,
            output_text=out_text,
            chat_id=msg.chat_id,
            timestamp=datetime.utcnow(),
            feedback=payload.feedback,
        )
    )
    await session.commit()
    return {"ok": True}


@app.get("/api/quality/stats")
async def quality_stats(session: AsyncSession = Depends(get_session)) -> dict:
    rejected_total = await session.execute(select(func.count(QualityLog.id)))
    accepted_total = await session.execute(
        select(func.count(Message.id)).where(Message.reply_text.is_not(None))
    )
    reason_rows = await session.execute(
        select(QualityLog.reason, func.count(QualityLog.id))
        .group_by(QualityLog.reason)
        .order_by(desc(func.count(QualityLog.id)))
    )
    total_rejected = rejected_total.scalar() or 0
    total_accepted = accepted_total.scalar() or 0
    return {
        "total_generated": total_accepted + total_rejected,
        "total_rejected": total_rejected,
        "reasons": [
            {"reason": reason, "count": count} for reason, count in reason_rows.all()
        ],
    }


@app.get("/api/training/status")
async def training_status(session: AsyncSession = Depends(get_session)) -> dict:
    stats = await dataset_stats(session)
    runs = await list_runs()
    last = runs[0] if runs else None
    active = next((r for r in runs if r.get("is_active")), None)
    msg_total = await session.execute(select(func.count(Message.id)))
    return {
        "messages_collected": msg_total.scalar() or 0,
        "training_pairs": stats["total_pairs"],
        "latest_dataset": stats["latest_dataset"],
        "last_run": last,
        "active_adapter": active,
        "running": training_state.running,
        "current_run_id": training_state.current_run_id,
    }


@app.post("/api/training/build-dataset")
async def build_dataset(session: AsyncSession = Depends(get_session)) -> dict:
    return await build_dataset_file(session)


@app.post("/api/training/start")
async def training_start() -> dict:
    return await start_training()


@app.post("/api/training/cancel")
async def training_cancel() -> dict:
    cancelled = await cancel_training()
    return {"cancelled": cancelled}


@app.post("/api/training/activate/{run_id}")
async def training_activate(run_id: int) -> dict:
    ok = await activate_adapter(run_id)
    return {"ok": ok}


@app.post("/api/training/deactivate")
async def training_deactivate() -> dict:
    ok = await deactivate_adapter()
    return {"ok": ok}


@app.delete("/api/training/runs/{run_id}")
async def training_delete_run(run_id: int) -> dict:
    ok, reason = await delete_training_run(run_id)
    if not ok:
        status_code = 404 if reason == "not found" else 409
        raise HTTPException(status_code=status_code, detail=reason)
    return {"ok": True}


@app.get("/api/training/runs/{run_id}/error-log")
async def training_run_error_log(run_id: int) -> dict:
    log_info = await get_training_run_error_log(run_id)
    if log_info.get("error") == "run not found":
        raise HTTPException(status_code=404, detail="run not found")
    return log_info


@app.post("/api/training/runs/{run_id}/export-gguf")
async def training_export_gguf(run_id: int) -> dict:
    return await export_gguf_for_run(run_id)


@app.post("/api/training/runs/{run_id}/export-lora-gguf")
async def training_export_lora_gguf(run_id: int) -> dict:
    return await export_lora_gguf_for_run(run_id)


@app.get("/api/llama-server/status")
async def llama_server_status() -> dict:
    return await llama_server.status_async()


@app.post("/api/llama-server/auto-resume")
async def llama_server_set_auto_resume(body: dict) -> dict:
    enabled = bool(body.get("enabled", True))
    await llama_server.set_auto_resume(enabled)
    return {"ok": True, "auto_resume": enabled}


@app.post("/api/llama-server/start")
async def llama_server_start() -> dict:
    return await llama_server.start()


@app.post("/api/llama-server/stop")
async def llama_server_stop() -> dict:
    return await llama_server.stop()


@app.post("/api/llama-server/restart")
async def llama_server_restart() -> dict:
    asyncio.create_task(llama_server.restart())
    return {"ok": True, "note": "рестарт запущен в фоне; следи через /status"}


@app.get("/api/training/runs")
async def training_runs() -> list[dict]:
    return await list_runs()


@app.get("/api/training/progress")
async def training_progress(request: Request) -> EventSourceResponse:
    q = training_bus.subscribe()

    async def gen():
        if training_state.last_event:
            yield {"data": json.dumps(training_state.last_event, ensure_ascii=False)}
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"data": item}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            training_bus.unsubscribe(q)

    return EventSourceResponse(gen())


@app.get("/api/style/profile")
async def get_style_profile(session: AsyncSession = Depends(get_session)) -> dict:
    profile = await get_latest_profile(session)
    return profile or {}


@app.put("/api/style/profile")
async def put_style_profile(
    payload: dict = Body(...), session: AsyncSession = Depends(get_session)
) -> dict:
    await save_manual_profile(session, payload)
    return {"ok": True}


@app.post("/api/style/reanalyze")
async def reanalyze_style(session: AsyncSession = Depends(get_session)) -> dict:
    return await reanalyze_and_store(session)


@app.get("/api/personas")
async def personas_list(session: AsyncSession = Depends(get_session)) -> list[dict]:
    mode = await get_setting(session, "persona_mode", "global")
    if mode == "per_chat":
        mine_chats_q = await session.execute(
            select(Message.chat_id)
            .where(Message.is_mine == True)  # noqa: E712
            .group_by(Message.chat_id)
        )
        for (chat_id,) in mine_chats_q.all():
            existing = await session.execute(
                select(Message.id)
                .where(Message.chat_id == chat_id)
                .limit(1)
            )
            if existing.first() is None:
                continue
            from .database import ChatPersona
            row = await session.execute(
                select(ChatPersona.id).where(ChatPersona.chat_id == chat_id)
            )
            if row.first() is None:
                await reanalyze_chat_persona(session, chat_id)

    rows = await list_chat_personas(session)
    return [
        {
            **r,
            "updated_at": _iso_utc(r["updated_at"]),
        }
        for r in rows
    ]


@app.get("/api/personas/{chat_id}")
async def persona_get(chat_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    profile = await get_profile_for_chat(session, chat_id)
    return profile or {}


@app.put("/api/personas/{chat_id}")
async def persona_put(chat_id: int, payload: dict = Body(...), session: AsyncSession = Depends(get_session)) -> dict:
    from .database import ChatPersona
    row = (await session.execute(select(ChatPersona).where(ChatPersona.chat_id == chat_id))).scalar_one_or_none()
    if row:
        row.profile_json = json.dumps(payload, ensure_ascii=False)
        row.updated_at = datetime.utcnow()
    else:
        session.add(ChatPersona(chat_id=chat_id, chat_name="", profile_json=json.dumps(payload, ensure_ascii=False), updated_at=datetime.utcnow(), messages_count=int(payload.get("messages_count", 0))))
    await session.commit()
    return {"ok": True}


@app.post("/api/personas/{chat_id}/reanalyze")
async def persona_reanalyze(chat_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    return await reanalyze_chat_persona(session, chat_id)


@app.delete("/api/personas/{chat_id}", status_code=204)
async def persona_delete(chat_id: int, session: AsyncSession = Depends(get_session)) -> None:
    from .database import ChatPersona
    row = (await session.execute(select(ChatPersona).where(ChatPersona.chat_id == chat_id))).scalar_one_or_none()
    if row:
        await session.delete(row)
        await session.commit()


@app.get("/api/stream/events")
async def stream_events(request: Request) -> EventSourceResponse:
    q = message_bus.subscribe()

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"data": item}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            message_bus.unsubscribe(q)

    return EventSourceResponse(gen())


class TestIn(BaseModel):
    text: str = "Привет, как дела?"


@app.get("/api/llm/models")
async def llm_models() -> dict:
    client = get_client()
    models = await client.list_models()
    return {"models": models, "current": client.model}


@app.post("/api/llm/test")
async def llm_test(
    payload: TestIn, session: AsyncSession = Depends(get_session)
) -> dict:
    profile = await get_latest_profile(session)
    try:
        variants = await get_client().generate_reply(
            incoming_text=payload.text,
            sender_name="Тестовый собеседник",
            style_profile=profile,
            chat_history=[],
        )
    except LLMUnavailableError as e:
        raise HTTPException(503, str(e))
    return {"prompt": payload.text, "variants": variants}


@app.get("/api/dialogs/chats")
async def dialogs_chats(session: AsyncSession = Depends(get_session)) -> list[dict]:
    chats_result = await session.execute(
        select(
            Message.chat_id,
            func.max(Message.chat_name).label("chat_name"),
            func.count(Message.id).label("count"),
            func.max(Message.timestamp).label("last"),
        )
        .where(Message.deleted == False)  # noqa: E712
        .group_by(Message.chat_id)
    )
    chats = chats_result.all()

    backups_result = await session.execute(
        select(
            DialogBackup.chat_id,
            func.count(DialogBackup.id).label("versions"),
            func.max(DialogBackup.version).label("latest_version"),
            func.max(DialogBackup.created_at).label("latest_backup_at"),
        ).group_by(DialogBackup.chat_id)
    )
    by_chat = {
        r.chat_id: {
            "versions": r.versions,
            "latest_version": r.latest_version,
            "latest_backup_at": _iso_utc(r.latest_backup_at),
        }
        for r in backups_result.all()
    }
    excluded = await get_excluded_chats(session)

    return [
        {
            "chat_id": c.chat_id,
            "chat_name": c.chat_name or "",
            "message_count": c.count,
            "last_message_at": _iso_utc(c.last),
            "excluded": c.chat_id in excluded,
            "versions": by_chat.get(c.chat_id, {}).get("versions", 0),
            "latest_version": by_chat.get(c.chat_id, {}).get("latest_version"),
            "latest_backup_at": by_chat.get(c.chat_id, {}).get("latest_backup_at"),
        }
        for c in chats
    ]


@app.get("/api/dialogs/{chat_id}/versions")
async def dialogs_versions(
    chat_id: int, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    result = await session.execute(
        select(DialogBackup)
        .where(DialogBackup.chat_id == chat_id)
        .order_by(desc(DialogBackup.version))
    )
    rows = result.scalars().all()
    return [
        {
            "id": b.id,
            "chat_id": b.chat_id,
            "chat_name": b.chat_name,
            "version": b.version,
            "created_at": _iso_utc(b.created_at),
            "message_count": b.message_count,
        }
        for b in rows
    ]


@app.get("/api/dialogs/backup/{backup_id}")
async def dialogs_backup_content(
    backup_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    backup_q = await session.execute(
        select(DialogBackup).where(DialogBackup.id == backup_id)
    )
    backup = backup_q.scalar_one_or_none()
    if not backup:
        raise HTTPException(404, "backup not found")
    msg_q = await session.execute(
        select(DialogBackupMessage)
        .where(DialogBackupMessage.backup_id == backup_id)
        .order_by(DialogBackupMessage.timestamp, DialogBackupMessage.id)
    )
    rows = msg_q.scalars().all()
    return {
        "backup": {
            "id": backup.id,
            "chat_id": backup.chat_id,
            "chat_name": backup.chat_name,
            "version": backup.version,
            "created_at": _iso_utc(backup.created_at),
            "message_count": backup.message_count,
        },
        "messages": [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "sender_name": m.sender_name,
                "is_mine": m.is_mine,
                "text": "" if (m.text in MEDIA_PLACEHOLDERS and m.media_path) else m.text,
                "timestamp": _iso_utc(m.timestamp),
                "message_id": m.message_id,
                "media_type": m.media_type,
                "media_path": m.media_path,
                "media_private": m.media_private,
            }
            for m in rows
        ],
    }


def _format_dialog_backup_text(
    backup: DialogBackup, messages: list[DialogBackupMessage]
) -> str:
    chat_title = backup.chat_name or f"chat {backup.chat_id}"
    lines = [
        f"Диалог: {chat_title}",
        f"chat_id: {backup.chat_id}",
        f"Версия: v{backup.version}",
        f"Создано: {_iso_utc(backup.created_at) or ''}",
        f"Сообщений: {backup.message_count}",
        "",
        "=" * 48,
        "",
    ]
    for message in messages:
        author = (
            settings.user_name
            if message.is_mine
            else (message.sender_name or "собеседник")
        )
        sent_at = _iso_utc(message.timestamp) or ""
        text = (message.text or "").strip()
        lines.append(f"[{sent_at}] {author}:")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _download_filename(backup: DialogBackup) -> str:
    safe_chat = re.sub(
        r"[^A-Za-z0-9А-Яа-я_.-]+",
        "_",
        backup.chat_name or f"chat_{backup.chat_id}",
    ).strip("_")
    if not safe_chat:
        safe_chat = f"chat_{backup.chat_id}"
    return f"dialog_{safe_chat}_v{backup.version}.txt"


@app.get("/api/dialogs/backup/{backup_id}/export")
async def dialogs_backup_export(
    backup_id: int, session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    backup_q = await session.execute(
        select(DialogBackup).where(DialogBackup.id == backup_id)
    )
    backup = backup_q.scalar_one_or_none()
    if not backup:
        raise HTTPException(404, "backup not found")
    msg_q = await session.execute(
        select(DialogBackupMessage)
        .where(DialogBackupMessage.backup_id == backup_id)
        .order_by(DialogBackupMessage.timestamp, DialogBackupMessage.id)
    )
    messages = list(msg_q.scalars().all())
    filename = _download_filename(backup)
    return PlainTextResponse(
        _format_dialog_backup_text(backup, messages),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; filename=dialog_backup.txt; "
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@app.delete("/api/dialogs/backup/{backup_id}")
async def dialogs_delete_backup(
    backup_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    backup_q = await session.execute(
        select(DialogBackup).where(DialogBackup.id == backup_id)
    )
    backup = backup_q.scalar_one_or_none()
    if not backup:
        raise HTTPException(404, "backup not found")
    chat_id = backup.chat_id
    await session.execute(
        delete(DialogBackupMessage).where(DialogBackupMessage.backup_id == backup_id)
    )
    await session.delete(backup)
    await session.commit()
    return {"ok": True, "chat_id": chat_id}


@app.delete("/api/dialogs/{chat_id}/history")
async def dialogs_delete_chat_history(
    chat_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    backups_q = await session.execute(
        select(DialogBackup.id).where(DialogBackup.chat_id == chat_id)
    )
    backup_ids = [row.id for row in backups_q.all()]
    if backup_ids:
        await session.execute(
            delete(DialogBackupMessage).where(
                DialogBackupMessage.backup_id.in_(backup_ids)
            )
        )
        await session.execute(
            delete(DialogBackup).where(DialogBackup.id.in_(backup_ids))
        )

    messages_result = await session.execute(
        delete(Message).where(Message.chat_id == chat_id)
    )
    await session.commit()
    return {
        "ok": True,
        "deleted_versions": len(backup_ids),
        "deleted_messages": messages_result.rowcount or 0,
    }


@app.get("/api/dialogs/settings")
async def dialogs_settings(session: AsyncSession = Depends(get_session)) -> dict:
    excluded = sorted(await get_excluded_chats(session))
    interval = await get_interval_minutes(session)
    last_run = await get_setting(session, SETTING_LAST_RUN, "")
    last_run_iso: Optional[str] = None
    if last_run:
        try:
            last_run_iso = _iso_utc(datetime.fromisoformat(last_run))
        except ValueError:
            last_run_iso = last_run
    return {
        "excluded_chats": list(excluded),
        "interval_minutes": interval,
        "last_run_at": last_run_iso,
        "running": dialog_backup_scheduler.last_result is not None,
        "last_error": dialog_backup_scheduler.last_error,
    }


class DialogSettingsIn(BaseModel):
    excluded_chats: Optional[list[int]] = None
    interval_minutes: Optional[int] = None
    interval_hours: Optional[int] = None


@app.post("/api/dialogs/settings")
async def save_dialogs_settings(
    payload: DialogSettingsIn, session: AsyncSession = Depends(get_session)
) -> dict:
    if payload.excluded_chats is not None:
        await set_excluded_chats(session, payload.excluded_chats)
    interval_value = payload.interval_minutes
    if interval_value is None and payload.interval_hours is not None:
        interval_value = int(payload.interval_hours) * 60
    if interval_value is not None:
        value = max(1, int(interval_value))
        await set_setting(session, SETTING_INTERVAL, str(value))
        dialog_backup_scheduler.trigger()
    return {"ok": True}


@app.post("/api/dialogs/run-backup")
async def dialogs_run_backup() -> dict:
    result = await dialog_backup_scheduler.run_now()
    return {
        "started_at": _iso_utc(result.started_at),
        "finished_at": _iso_utc(result.finished_at),
        "chats_processed": result.chats_processed,
        "new_versions": result.new_versions,
        "skipped": result.skipped,
        "excluded": result.excluded,
    }


class ReplicationSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    target_dir: Optional[str] = Field(default=None, max_length=1024)
    retention: Optional[int] = Field(default=None, ge=1, le=1000)
    delete_protection: Optional[bool] = None


@app.get("/api/replication/status")
async def replication_status_endpoint() -> dict:
    return await replication_status()


@app.post("/api/replication/settings")
async def replication_save_settings(
    payload: ReplicationSettingsIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    cfg = await save_replication_settings(
        session,
        enabled=payload.enabled,
        interval_minutes=payload.interval_minutes,
        target_dir=payload.target_dir,
        retention=payload.retention,
        delete_protection=payload.delete_protection,
    )
    replication_scheduler.trigger()
    return {
        "enabled": cfg.enabled,
        "interval_minutes": cfg.interval_minutes,
        "target_dir": cfg.target_dir,
        "retention": cfg.retention,
        "delete_protection": cfg.delete_protection,
    }


@app.post("/api/replication/run")
async def replication_run_now() -> dict:
    return await run_replication(trigger="manual")


@app.post("/api/replication/cancel")
async def replication_cancel_endpoint() -> dict:
    cancelled = await cancel_replication_run()
    return {"cancelled": cancelled}


@app.get("/api/replication/runs")
async def replication_runs_endpoint() -> list[dict]:
    return await list_replication_runs()


@app.delete("/api/replication/runs/{run_id}")
async def replication_delete_run_endpoint(
    run_id: int, confirm: bool = False
) -> dict:
    if replication_state.running and replication_state.current_run_id == run_id:
        raise HTTPException(status_code=409, detail="идёт репликация")
    ok, reason = await delete_replication_run(run_id, confirm=confirm)
    if not ok:
        status_code = 404 if reason == "запись не найдена" else 409
        raise HTTPException(status_code=status_code, detail=reason)
    return {"ok": True}


@app.get("/api/replication/runs/{run_id}/log")
async def replication_run_log_endpoint(run_id: int) -> dict:
    log_info = await get_replication_run_log(run_id)
    if log_info.get("error") == "запись не найдена":
        raise HTTPException(status_code=404, detail="запись не найдена")
    return log_info


@app.get("/api/replication/progress")
async def replication_progress(request: Request) -> EventSourceResponse:
    q = replication_bus.subscribe()

    async def gen():
        if replication_state.last_event:
            yield {
                "data": json.dumps(
                    replication_state.last_event, ensure_ascii=False
                )
            }
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"data": item}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            replication_bus.unsubscribe(q)

    return EventSourceResponse(gen())


@app.get("/api/stats/overview")
async def stats_overview(session: AsyncSession = Depends(get_session)) -> dict:
    received = (
        await session.execute(
            select(func.count(Message.id)).where(Message.is_mine == False)  # noqa: E712
        )
    ).scalar() or 0
    sent = (
        await session.execute(
            select(func.count(Message.id)).where(Message.is_mine == True)  # noqa: E712
        )
    ).scalar() or 0
    replied = (
        await session.execute(
            select(func.count(Message.id)).where(
                Message.is_mine == False, Message.replied == True  # noqa: E712
            )
        )
    ).scalar() or 0
    approved_manual = (
        await session.execute(
            select(func.count(TrainingPair.id)).where(TrainingPair.feedback == "good")
        )
    ).scalar() or 0
    rejected = (
        await session.execute(
            select(func.count(TrainingPair.id)).where(TrainingPair.feedback == "bad")
        )
    ).scalar() or 0
    total_feedback = approved_manual + rejected
    approval_rate = (approved_manual / total_feedback) if total_feedback else 0.0
    return {
        "received": received,
        "sent": sent,
        "replied": replied,
        "approved_manual": approved_manual,
        "rejected": rejected,
        "approval_rate": approval_rate,
    }


@app.get("/api/stats/activity")
async def stats_activity(session: AsyncSession = Depends(get_session)) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=30)
    day = func.strftime("%Y-%m-%d", Message.timestamp).label("day")
    result = await session.execute(
        select(
            day,
            func.sum(func.cast(Message.is_mine == False, Integer)).label(
                "received"
            ),  # noqa: E712
            func.sum(func.cast(Message.is_mine == True, Integer)).label(
                "sent"
            ),  # noqa: E712
        )
        .where(Message.timestamp >= since)
        .group_by(day)
        .order_by(day)
    )
    rows = {
        r.day: {"received": int(r.received or 0), "sent": int(r.sent or 0)}
        for r in result.all()
    }
    out: list[dict] = []
    today = datetime.utcnow().date()
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        stats = rows.get(d, {"received": 0, "sent": 0})
        out.append({"day": d, **stats})
    return out


@app.get("/api/stats/top-chats")
async def stats_top_chats(session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(
            Message.chat_id,
            func.count(Message.id).label("count"),
        )
        .group_by(Message.chat_id)
        .order_by(desc("count"))
        .limit(10)
    )
    rows = result.all()

    out: list[dict] = []
    for row in rows:
        chat_id = row.chat_id

        latest = (
            await session.execute(
                select(Message.chat_name, Message.chat_username)
                .where(Message.chat_id == chat_id)
                .order_by(Message.timestamp.desc())
                .limit(1)
            )
        ).first()

        stored_name = latest.chat_name if latest else ""
        stored_username = _normalize_username(latest.chat_username if latest else "")
        username = stored_username or (
            stored_name if TELEGRAM_USERNAME_RE.fullmatch(stored_name or "") else ""
        )
        display_name = (
            await _best_contact_name(session, chat_id, username)
            or stored_name
            or f"chat {chat_id}"
        )

        out.append(
            {
                "chat_id": chat_id,
                "chat_name": display_name,
                "chat_username": username,
                "count": row.count,
            }
        )
    return out


@app.get("/api/stats/model-quality")
async def stats_model_quality(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    runs_q = await session.execute(
        select(TrainingRun)
        .where(TrainingRun.finished_at.is_not(None))
        .order_by(TrainingRun.finished_at)
    )
    runs = list(runs_q.scalars().all())
    out: list[dict] = []
    for i, run in enumerate(runs):
        start = run.finished_at
        end = runs[i + 1].finished_at if i + 1 < len(runs) else None
        cond = [TrainingPair.timestamp >= start]
        if end is not None:
            cond.append(TrainingPair.timestamp < end)
        good = (
            await session.execute(
                select(func.count(TrainingPair.id)).where(
                    *cond, TrainingPair.feedback == "good"
                )
            )
        ).scalar() or 0
        bad = (
            await session.execute(
                select(func.count(TrainingPair.id)).where(
                    *cond, TrainingPair.feedback == "bad"
                )
            )
        ).scalar() or 0
        total = good + bad
        approval_rate = (good / total) if total else 0.0
        rejection_rate = (bad / total) if total else 0.0
        out.append(
            {
                "version": run.version,
                "run_id": run.id,
                "is_active": run.is_active,
                "status": run.status,
                "finished_at": _iso_utc(run.finished_at),
                "approved": good,
                "rejected": bad,
                "total": total,
                "approval_rate": approval_rate,
                "rejection_rate": rejection_rate,
            }
        )
    return out


@app.get("/api/stats/response-time")
async def stats_response_time(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    result = await session.execute(
        select(
            Message.chat_id,
            Message.chat_name,
            Message.is_mine,
            Message.timestamp,
        ).order_by(Message.chat_id, Message.timestamp)
    )
    by_chat: dict[int, dict] = {}
    pending_ts: dict[int, datetime] = {}
    chat_names: dict[int, str] = {}
    for chat_id, chat_name, is_mine, ts in result.all():
        if chat_name:
            chat_names[chat_id] = chat_name
        if not is_mine:
            if chat_id not in pending_ts:
                pending_ts[chat_id] = ts
        else:
            start = pending_ts.pop(chat_id, None)
            if start is not None and ts > start:
                delta = (ts - start).total_seconds()
                entry = by_chat.setdefault(chat_id, {"total": 0.0, "count": 0})
                entry["total"] += delta
                entry["count"] += 1
    out: list[dict] = []
    for chat_id, agg in by_chat.items():
        if agg["count"] == 0:
            continue
        out.append(
            {
                "chat_id": chat_id,
                "chat_name": chat_names.get(chat_id, ""),
                "avg_seconds": agg["total"] / agg["count"],
                "replies": agg["count"],
            }
        )
    out.sort(key=lambda x: x["avg_seconds"])
    return out


@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request) -> JSONResponse:
    if not telegram_service.is_configured or telegram_service._token != token:
        raise HTTPException(403, "unknown token")
    raw = await request.json()
    try:
        await telegram_service.feed_update(raw)
    except Exception:  # noqa: BLE001
        log.exception("webhook processing failed")
    return JSONResponse({"ok": True})


frontend_dist = ROOT_DIR / "frontend" / "dist"
if frontend_dist.exists():
    app.mount(
        "/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend"
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
