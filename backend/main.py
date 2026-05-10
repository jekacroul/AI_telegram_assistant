"""FastAPI entry point for the local Telegram AI assistant."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sse_starlette.sse import EventSourceResponse

from .config import get_settings, update_env
from .database import (
    Message,
    MonitoredChat,
    SessionLocal,
    Setting,
    init_db,
)
from .dataset_builder import build_pairs, export_jsonl
from .llm_engine import OllamaUnavailable, llm_engine
from .style_engine import (
    get_active_profile,
    reanalyze,
    sample_messages,
    save_manual_profile,
)
from .telegram_client import telegram_service
from .trainer import list_runs, trainer_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("backend.main")


_event_queues: list[asyncio.Queue] = []
_training_queues: list[asyncio.Queue] = []


async def _broadcast_incoming(payload: dict[str, Any]) -> None:
    for q in list(_event_queues):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def _broadcast_training(snapshot: dict[str, Any]) -> None:
    for q in list(_training_queues):
        try:
            q.put_nowait(snapshot)
        except asyncio.QueueFull:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    telegram_service.add_listener(_broadcast_incoming)
    trainer_service.add_listener(_broadcast_training)
    asyncio.create_task(telegram_service.start())
    try:
        yield
    finally:
        await telegram_service.stop()


app = FastAPI(title="Telegram Local AI", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Schemas ----------

class GenerateReplyIn(BaseModel):
    message_id: int | None = None
    chat_id: int | None = None
    text: str | None = None
    n_variants: int = 3
    temperature: float = 0.85


class SendReplyIn(BaseModel):
    chat_id: int
    text: str
    reply_to_message_id: int | None = None
    incoming_message_id: int | None = None


class ApprovePairIn(BaseModel):
    input_text: str
    output_text: str
    chat_id: int | None = None


class ChatsSelectIn(BaseModel):
    chat_ids: list[int]
    names: dict[int, str] = Field(default_factory=dict)


class StartTrainingIn(BaseModel):
    epochs: int = 3
    batch_size: int = 4
    learning_rate: float = 2e-4


class SettingsIn(BaseModel):
    telegram_api_id: str | None = None
    telegram_api_hash: str | None = None
    telegram_phone: str | None = None
    ollama_model: str | None = None
    ollama_host: str | None = None


class TestModelIn(BaseModel):
    prompt: str


# ---------- Health / status ----------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "telegram_connected": telegram_service.connected}


@app.get("/api/llm/status")
async def llm_status() -> dict[str, Any]:
    return await llm_engine.status()


@app.post("/api/llm/test")
async def llm_test(body: TestModelIn) -> dict[str, Any]:
    try:
        out = await llm_engine.test_prompt(body.prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"response": out}


@app.post("/api/llm/pull")
async def llm_pull() -> dict[str, Any]:
    try:
        await llm_engine.ensure_model()
    except OllamaUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


# ---------- Chats ----------

@app.get("/api/chats")
async def list_chats() -> list[dict[str, Any]]:
    if not telegram_service.connected:
        await telegram_service.start()
    return await telegram_service.list_chats()


@app.post("/api/chats/select")
async def select_chats(body: ChatsSelectIn) -> dict[str, Any]:
    await telegram_service.set_monitored_chats(body.chat_ids, body.names)
    inserted = 0
    if body.chat_ids:
        try:
            inserted = await telegram_service.collect_history(body.chat_ids)
        except Exception as exc:
            log.warning("History collection failed: %s", exc)
    return {"ok": True, "monitored": body.chat_ids, "messages_imported": inserted}


@app.get("/api/chats/monitored")
async def monitored_chats() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(MonitoredChat))).scalars().all()
    return [
        {"chat_id": r.chat_id, "chat_name": r.chat_name, "enabled": r.enabled}
        for r in rows
    ]


# ---------- Messages ----------

@app.get("/api/messages/pending")
async def pending_messages(limit: int = 50) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Message)
                .where(Message.is_mine.is_(False), Message.handled.is_(False))
                .order_by(desc(Message.timestamp))
                .limit(limit)
            )
        ).scalars().all()
    return [
        {
            "id": r.id,
            "chat_id": r.chat_id,
            "chat_name": r.chat_name,
            "sender_name": r.sender_name,
            "text": r.text,
            "timestamp": r.timestamp.isoformat(),
            "message_id": r.message_id,
        }
        for r in rows
    ]


@app.post("/api/reply/generate")
async def generate_reply(body: GenerateReplyIn) -> dict[str, Any]:
    incoming_text = body.text
    chat_id = body.chat_id
    history: list[dict[str, Any]] = []
    if body.message_id is not None:
        async with SessionLocal() as session:
            msg = await session.get(Message, body.message_id)
            if msg is None:
                raise HTTPException(status_code=404, detail="Message not found")
            incoming_text = msg.text
            chat_id = msg.chat_id
            recent = (
                await session.execute(
                    select(Message)
                    .where(Message.chat_id == msg.chat_id, Message.timestamp <= msg.timestamp)
                    .order_by(desc(Message.timestamp))
                    .limit(10)
                )
            ).scalars().all()
            history = [
                {
                    "is_mine": m.is_mine,
                    "sender_name": m.sender_name,
                    "text": m.text,
                }
                for m in reversed(recent)
            ]
    if not incoming_text:
        raise HTTPException(status_code=400, detail="No incoming message text provided.")

    style = await get_active_profile()
    persona = await _get_setting("persona")
    try:
        result = await llm_engine.generate_reply(
            incoming_text,
            style_profile=style,
            conversation_history=history,
            persona=persona,
            n_variants=body.n_variants,
            temperature=body.temperature,
        )
    except OllamaUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"chat_id": chat_id, "incoming_text": incoming_text, **result}


@app.post("/api/reply/send")
async def send_reply(body: SendReplyIn) -> dict[str, Any]:
    if not telegram_service.connected:
        raise HTTPException(status_code=503, detail="Telegram is not connected.")
    sent_id = await telegram_service.send_reply(
        body.chat_id, body.text, body.reply_to_message_id
    )
    if body.incoming_message_id is not None:
        async with SessionLocal() as session:
            msg = await session.get(Message, body.incoming_message_id)
            if msg is not None:
                msg.handled = True
                await session.commit()
    return {"ok": True, "message_id": sent_id}


@app.post("/api/reply/approve")
async def approve_reply(body: ApprovePairIn) -> dict[str, Any]:
    from .database import TrainingPair

    async with SessionLocal() as session:
        pair = TrainingPair(
            input_text=body.input_text,
            output_text=body.output_text,
            chat_id=body.chat_id or 0,
            timestamp=datetime.utcnow(),
            source="approved",
        )
        session.add(pair)
        await session.commit()
    return {"ok": True}


# ---------- Training ----------

@app.get("/api/training/status")
async def training_status() -> dict[str, Any]:
    runs = await list_runs()
    settings = get_settings()
    async with SessionLocal() as session:
        from .database import TrainingPair

        total = (await session.execute(select(TrainingPair))).scalars().all()
    last_run = runs[0] if runs else None
    return {
        "ollama_model": settings.ollama_model,
        "base_model": settings.hf_base_model,
        "min_pairs_for_training": settings.min_pairs_for_training,
        "total_training_pairs": len(total),
        "active_adapter": llm_engine.active_adapter,
        "last_run": last_run,
        "runs": runs,
        "progress": trainer_service.progress.snapshot(),
    }


@app.post("/api/training/dataset/build")
async def training_dataset_build() -> dict[str, Any]:
    stats = await build_pairs(persist=True)
    return stats


@app.post("/api/training/dataset/export")
async def training_dataset_export() -> dict[str, Any]:
    return await export_jsonl()


@app.post("/api/training/start")
async def training_start(body: StartTrainingIn) -> dict[str, Any]:
    return await trainer_service.start(
        epochs=body.epochs,
        batch_size=body.batch_size,
        learning_rate=body.learning_rate,
    )


@app.post("/api/training/cancel")
async def training_cancel() -> dict[str, Any]:
    ok = await trainer_service.cancel()
    return {"ok": ok}


@app.post("/api/training/activate/{version}")
async def training_activate(version: int) -> dict[str, Any]:
    runs = await list_runs()
    target = next((r for r in runs if r["version"] == version), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Adapter version not found")
    if not target["adapter_path"]:
        raise HTTPException(status_code=400, detail="No adapter path stored for this run")
    llm_engine.set_active_adapter(target["adapter_path"])
    await _set_setting("active_adapter_version", str(version))
    return {"ok": True, "version": version, "adapter_path": target["adapter_path"]}


@app.get("/api/training/progress")
async def training_progress():
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _training_queues.append(queue)

    async def gen():
        try:
            yield {"event": "progress", "data": json.dumps(trainer_service.progress.snapshot())}
            while True:
                snap = await queue.get()
                yield {"event": "progress", "data": json.dumps(snap)}
        finally:
            if queue in _training_queues:
                _training_queues.remove(queue)

    return EventSourceResponse(gen())


# ---------- Style ----------

@app.get("/api/style/profile")
async def style_profile() -> dict[str, Any]:
    profile = await get_active_profile()
    samples = await sample_messages(limit=15)
    return {"profile": profile, "samples": samples}


@app.post("/api/style/reanalyze")
async def style_reanalyze() -> dict[str, Any]:
    profile = await reanalyze()
    return {"profile": profile}


@app.post("/api/style/manual")
async def style_manual(profile: dict[str, Any] = Body(...)) -> dict[str, Any]:
    saved = await save_manual_profile(profile)
    return {"profile": saved}


# ---------- Settings ----------

async def _get_setting(key: str) -> str | None:
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Setting).where(Setting.key == key))
        ).scalar_one_or_none()
        return row.value if row else None


async def _set_setting(key: str, value: str) -> None:
    async with SessionLocal() as session:
        row = (
            await session.execute(select(Setting).where(Setting.key == key))
        ).scalar_one_or_none()
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
        await session.commit()


@app.get("/api/settings")
async def get_settings_view() -> dict[str, Any]:
    settings = get_settings()
    persona = await _get_setting("persona")
    return {
        "telegram_api_id": settings.telegram_api_id,
        "telegram_api_hash": "***" if settings.telegram_api_hash else "",
        "telegram_phone": settings.telegram_phone,
        "ollama_model": settings.ollama_model,
        "ollama_host": settings.ollama_host,
        "persona": persona or "",
    }


@app.post("/api/settings")
async def update_settings_view(body: SettingsIn) -> dict[str, Any]:
    updates: dict[str, str] = {}
    if body.telegram_api_id is not None:
        updates["TELEGRAM_API_ID"] = body.telegram_api_id
    if body.telegram_api_hash is not None and body.telegram_api_hash != "***":
        updates["TELEGRAM_API_HASH"] = body.telegram_api_hash
    if body.telegram_phone is not None:
        updates["TELEGRAM_PHONE"] = body.telegram_phone
    if body.ollama_model is not None:
        updates["OLLAMA_MODEL"] = body.ollama_model
    if body.ollama_host is not None:
        updates["OLLAMA_HOST"] = body.ollama_host
    if updates:
        update_env(updates)
        if "OLLAMA_MODEL" in updates:
            llm_engine.set_model(updates["OLLAMA_MODEL"])
        if "OLLAMA_HOST" in updates:
            llm_engine.host = updates["OLLAMA_HOST"]
    return {"ok": True}


@app.post("/api/settings/persona")
async def update_persona(persona: str = Body(..., embed=True)) -> dict[str, Any]:
    await _set_setting("persona", persona)
    return {"ok": True}


# ---------- SSE streams ----------

@app.get("/api/stream/events")
async def stream_events():
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _event_queues.append(queue)

    async def gen():
        try:
            yield {"event": "ping", "data": json.dumps({"ts": datetime.utcnow().isoformat()})}
            while True:
                payload = await queue.get()
                yield {"event": payload.get("type", "message"), "data": json.dumps(payload)}
        finally:
            if queue in _event_queues:
                _event_queues.remove(queue)

    return EventSourceResponse(gen())
