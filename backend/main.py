from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from .bot import telegram_service
from .config import ROOT_DIR, settings
from .database import (
    Message,
    SessionLocal,
    TrainingPair,
    get_session,
    get_setting,
    init_db,
    set_setting,
)
from .dataset_builder import build_dataset_file, dataset_stats
from .event_bus import message_bus, training_bus
from .llm_engine import OllamaUnavailableError, get_client
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


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        token = await get_setting(session, "telegram_bot_token", settings.telegram_bot_token)
    if token:
        try:
            await telegram_service.setup(token)
        except Exception:  # noqa: BLE001
            log.exception("bot setup failed at startup")
    yield
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
    ollama_ok = await get_client().health()
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
        ollama_model = await get_setting(session, "ollama_model", settings.ollama_model)
    return {
        "ollama": ollama_ok,
        "bot": bot_ok,
        "db": db_ok,
        "auto_reply": auto_reply,
        "ollama_model": ollama_model,
        "user_name": settings.user_name,
        "last_update_at": (
            telegram_service.last_update_at.isoformat()
            if telegram_service.last_update_at else None
        ),
        "last_update_kind": telegram_service.last_update_kind,
        "update_count": telegram_service.update_count,
        "last_error": telegram_service.last_error,
    }


@app.get("/api/webhook/info")
async def webhook_info() -> dict:
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    return await telegram_service.get_webhook_info()


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
            "last": r.last.isoformat() if r.last else None,
            "monitored": (r.chat_id in monitored) if monitored else True,
        }
        for r in rows
    ]


class SettingsIn(BaseModel):
    telegram_bot_token: Optional[str] = None
    auto_reply: Optional[bool] = None
    monitored_chats: Optional[list[int]] = None
    ollama_model: Optional[str] = None


@app.post("/api/settings")
async def save_settings(
    payload: SettingsIn, session: AsyncSession = Depends(get_session)
) -> dict:
    if payload.telegram_bot_token is not None:
        await set_setting(session, "telegram_bot_token", payload.telegram_bot_token)
        if payload.telegram_bot_token:
            try:
                await telegram_service.setup(payload.telegram_bot_token)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(400, f"bot setup failed: {e}")
    if payload.auto_reply is not None:
        await set_setting(session, "auto_reply", "1" if payload.auto_reply else "0")
    if payload.monitored_chats is not None:
        csv = ",".join(str(x) for x in payload.monitored_chats)
        await set_setting(session, "monitored_chats", csv)
    if payload.ollama_model is not None:
        await set_setting(session, "ollama_model", payload.ollama_model)
        get_client().model = payload.ollama_model
    return {"ok": True}


@app.get("/api/settings")
async def get_settings(session: AsyncSession = Depends(get_session)) -> dict:
    token = await get_setting(session, "telegram_bot_token", settings.telegram_bot_token)
    masked = (token[:6] + "..." + token[-4:]) if len(token) > 12 else ("set" if token else "")
    auto_reply = (
        await get_setting(session, "auto_reply", "1" if settings.auto_reply else "0")
    ) in ("1", "true", "True")
    monitored_csv = await get_setting(session, "monitored_chats", "")
    monitored = [int(x) for x in monitored_csv.split(",") if x.strip()]
    ollama_model = await get_setting(session, "ollama_model", settings.ollama_model)
    return {
        "telegram_bot_token_masked": masked,
        "telegram_bot_token_set": bool(token),
        "auto_reply": auto_reply,
        "monitored_chats": monitored,
        "ollama_model": ollama_model,
    }


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
        "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        "message_id": m.message_id,
        "replied": m.replied,
        "reply_text": m.reply_text,
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
    except OllamaUnavailableError as e:
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
    await telegram_service.send_and_record(
        msg.chat_id, payload.text, reply_to=msg.message_id, original_id=msg.id
    )
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


@app.post("/api/reply/send")
async def send_reply(payload: SendIn) -> dict:
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    await telegram_service.send_and_record(
        payload.chat_id, payload.text,
        reply_to=payload.reply_to, original_id=payload.original_id,
    )
    return {"ok": True}


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
                await telegram_service.send_reply(msg.chat_id, payload.corrected_text)
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
    except OllamaUnavailableError as e:
        raise HTTPException(503, str(e))
    return {"prompt": payload.text, "variants": variants}


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


class WebhookIn(BaseModel):
    url: str


@app.post("/api/webhook/set")
async def webhook_set(payload: WebhookIn) -> dict:
    if not telegram_service.is_configured:
        raise HTTPException(400, "bot not configured")
    await telegram_service.set_webhook(payload.url)
    return {"ok": True, "url": payload.url}


@app.post("/api/webhook/remove")
async def webhook_remove() -> dict:
    await telegram_service.remove_webhook()
    return {"ok": True}


frontend_dist = ROOT_DIR / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
