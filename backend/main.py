from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional


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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Integer, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .bot import telegram_service
from .config import ROOT_DIR, settings
from .logging_setup import setup_logging
from .database import (
    DialogBackup,
    DialogBackupMessage,
    Message,
    SessionLocal,
    TrainingPair,
    TrainingRun,
    get_session,
    get_setting,
    init_db,
    set_setting,
)
from .dataset_builder import build_dataset_file, dataset_stats
from .dialog_backup import (
    DEFAULT_INTERVAL_HOURS,
    SETTING_INTERVAL,
    SETTING_LAST_RUN,
    get_excluded_chats,
    get_interval_hours,
    scheduler as dialog_backup_scheduler,
    set_excluded_chats,
)
from .event_bus import message_bus, training_bus
from .llm_engine import LLMUnavailableError, get_client
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
from .style_engine import (
    get_latest_profile,
    reanalyze_and_store,
    save_manual_profile,
)
from .trainer import (
    activate_adapter,
    cancel_training,
    list_runs,
    start_training,
    training_state,
)


setup_logging(settings.logs_dir, level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.notify_chat_id:
        async with SessionLocal() as session:
            current = await get_setting(session, SETTING_NOTIFY_CHAT_ID, "")
            if not current:
                await set_setting(session, SETTING_NOTIFY_CHAT_ID, settings.notify_chat_id)
    token = settings.telegram_bot_token
    if token:
        try:
            await telegram_service.setup(token)
        except Exception:  # noqa: BLE001
            log.exception("bot setup failed at startup")
    else:
        log.warning("TELEGRAM_BOT_TOKEN is not set; bot will be inactive")
    dialog_backup_scheduler.start()
    yield
    await dialog_backup_scheduler.stop()
    await telegram_service.shutdown()


app = FastAPI(title="Telegram Local AI Assistant", lifespan=lifespan)
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
            await get_setting(session, "auto_reply", "1" if settings.auto_reply else "0")
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
    return {"ok": True}


@app.get("/api/settings")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict:
    auto_reply = (
        await get_setting(session, "auto_reply", "1" if settings.auto_reply else "0")
    ) in ("1", "true", "True")
    monitored_csv = await get_setting(session, "monitored_chats", "")
    monitored = [int(x) for x in monitored_csv.split(",") if x.strip()]
    llm_model = await get_setting(session, "llm_model", settings.openai_model)
    return {
        "auto_reply": auto_reply,
        "monitored_chats": monitored,
        "llm_model": llm_model,
    }


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
    chat_id = await get_setting(session, SETTING_NOTIFY_CHAT_ID, settings.notify_chat_id)
    enabled = (
        await get_setting(session, SETTING_NOTIFY_ENABLED, "1")
    ) in ("1", "true", "True")
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
        await set_setting(session, SETTING_NOTIFY_ENABLED, "1" if payload.enabled else "0")
    return {"ok": True}


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
        .order_by(desc(Message.timestamp)).limit(100)
    )
    return [_message_to_dict(m) for m in result.scalars().all()]


@app.get("/api/messages/recent")
async def recent(limit: int = 100, session: AsyncSession = Depends(get_session)) -> list[dict]:
    result = await session.execute(
        select(Message).order_by(desc(Message.timestamp)).limit(limit)
    )
    return [_message_to_dict(m) for m in result.scalars().all()]


def _message_to_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "chat_id": m.chat_id,
        "chat_name": m.chat_name,
        "sender_id": m.sender_id,
        "sender_name": m.sender_name,
        "is_mine": m.is_mine,
        "text": m.text,
        "timestamp": _iso_utc(m.timestamp),
        "message_id": m.message_id,
        "replied": m.replied,
        "reply_text": m.reply_text,
        "pending_reason": m.pending_reason,
    }


class GenerateIn(BaseModel):
    message_id: int


@app.post("/api/reply/generate")
async def generate_reply(
    payload: GenerateIn, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await session.execute(select(Message).where(Message.id == payload.message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    profile = await get_latest_profile(session)
    history_q = await session.execute(
        select(Message).where(Message.chat_id == msg.chat_id)
        .order_by(desc(Message.timestamp)).limit(8)
    )
    history = list(history_q.scalars().all())[::-1]
    history_dicts = [
        {"sender_name": m.sender_name, "is_mine": m.is_mine, "text": m.text} for m in history
    ]
    try:
        variants = await get_client().generate_reply(
            incoming_text=msg.text,
            sender_name=msg.sender_name,
            style_profile=profile,
            chat_history=history_dicts,
        )
    except LLMUnavailableError as e:
        raise HTTPException(503, str(e))
    return {"variants": variants}


class ApproveIn(BaseModel):
    message_id: int
    text: str


@app.post("/api/reply/approve")
async def approve_reply(
    payload: ApproveIn, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await session.execute(select(Message).where(Message.id == payload.message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    try:
        await telegram_service.send_and_record(
            msg.chat_id, payload.text, reply_to=msg.message_id, original_id=msg.id,
            business_connection_id=msg.business_connection_id,
        )
    except TelegramBadRequest as e:
        raise HTTPException(400, _telegram_error_message(e))
    session.add(TrainingPair(
        input_text=msg.text,
        output_text=payload.text,
        chat_id=msg.chat_id,
        timestamp=datetime.utcnow(),
        feedback="good",
    ))
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
            payload.chat_id, payload.text,
            reply_to=payload.reply_to, original_id=payload.original_id,
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
    result = await session.execute(select(Message).where(Message.id == payload.message_id))
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(404, "message not found")
    out_text = payload.corrected_text or msg.reply_text or ""
    if payload.feedback == "bad" and payload.corrected_text:
        if telegram_service.is_configured:
            try:
                await telegram_service.send_reply(
                    msg.chat_id, payload.corrected_text,
                    business_connection_id=msg.business_connection_id,
                )
            except Exception:  # noqa: BLE001
                log.exception("re-send failed")
        msg.reply_text = payload.corrected_text
    session.add(TrainingPair(
        input_text=msg.text,
        output_text=out_text,
        chat_id=msg.chat_id,
        timestamp=datetime.utcnow(),
        feedback=payload.feedback,
    ))
    await session.commit()
    return {"ok": True}


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
async def llm_test(payload: TestIn, session: AsyncSession = Depends(get_session)) -> dict:
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
                "text": m.text,
                "timestamp": _iso_utc(m.timestamp),
                "message_id": m.message_id,
            }
            for m in rows
        ],
    }


@app.get("/api/dialogs/settings")
async def dialogs_settings(session: AsyncSession = Depends(get_session)) -> dict:
    excluded = sorted(await get_excluded_chats(session))
    interval = await get_interval_hours(session)
    last_run = await get_setting(session, SETTING_LAST_RUN, "")
    last_run_iso: Optional[str] = None
    if last_run:
        try:
            last_run_iso = _iso_utc(datetime.fromisoformat(last_run))
        except ValueError:
            last_run_iso = last_run
    return {
        "excluded_chats": list(excluded),
        "interval_hours": interval,
        "last_run_at": last_run_iso,
        "running": dialog_backup_scheduler.last_result is not None,
        "last_error": dialog_backup_scheduler.last_error,
    }


class DialogSettingsIn(BaseModel):
    excluded_chats: Optional[list[int]] = None
    interval_hours: Optional[int] = None


@app.post("/api/dialogs/settings")
async def save_dialogs_settings(
    payload: DialogSettingsIn, session: AsyncSession = Depends(get_session)
) -> dict:
    if payload.excluded_chats is not None:
        await set_excluded_chats(session, payload.excluded_chats)
    if payload.interval_hours is not None:
        value = max(1, int(payload.interval_hours))
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


@app.get("/api/stats/overview")
async def stats_overview(session: AsyncSession = Depends(get_session)) -> dict:
    received = (await session.execute(
        select(func.count(Message.id)).where(Message.is_mine == False)  # noqa: E712
    )).scalar() or 0
    sent = (await session.execute(
        select(func.count(Message.id)).where(Message.is_mine == True)  # noqa: E712
    )).scalar() or 0
    replied = (await session.execute(
        select(func.count(Message.id)).where(
            Message.is_mine == False, Message.replied == True  # noqa: E712
        )
    )).scalar() or 0
    approved_manual = (await session.execute(
        select(func.count(TrainingPair.id)).where(TrainingPair.feedback == "good")
    )).scalar() or 0
    rejected = (await session.execute(
        select(func.count(TrainingPair.id)).where(TrainingPair.feedback == "bad")
    )).scalar() or 0
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
            func.sum(func.cast(Message.is_mine == False, Integer)).label("received"),  # noqa: E712
            func.sum(func.cast(Message.is_mine == True, Integer)).label("sent"),  # noqa: E712
        )
        .where(Message.timestamp >= since)
        .group_by(day)
        .order_by(day)
    )
    rows = {r.day: {"received": int(r.received or 0), "sent": int(r.sent or 0)} for r in result.all()}
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
            func.max(Message.chat_name).label("chat_name"),
            func.count(Message.id).label("count"),
        )
        .group_by(Message.chat_id)
        .order_by(desc("count"))
        .limit(10)
    )
    return [
        {"chat_id": r.chat_id, "chat_name": r.chat_name or "", "count": r.count}
        for r in result.all()
    ]


@app.get("/api/stats/model-quality")
async def stats_model_quality(session: AsyncSession = Depends(get_session)) -> list[dict]:
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
        good = (await session.execute(
            select(func.count(TrainingPair.id)).where(
                *cond, TrainingPair.feedback == "good"
            )
        )).scalar() or 0
        bad = (await session.execute(
            select(func.count(TrainingPair.id)).where(
                *cond, TrainingPair.feedback == "bad"
            )
        )).scalar() or 0
        total = good + bad
        approval_rate = (good / total) if total else 0.0
        rejection_rate = (bad / total) if total else 0.0
        out.append({
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
        })
    return out


@app.get("/api/stats/response-time")
async def stats_response_time(session: AsyncSession = Depends(get_session)) -> list[dict]:
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
        out.append({
            "chat_id": chat_id,
            "chat_name": chat_names.get(chat_id, ""),
            "avg_seconds": agg["total"] / agg["count"],
            "replies": agg["count"],
        })
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
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
