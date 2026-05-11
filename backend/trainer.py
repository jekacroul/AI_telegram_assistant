from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import SessionLocal, TrainingPair, TrainingRun
from .event_bus import training_bus
from .train_worker import EVENT_PREFIX


log = logging.getLogger(__name__)

MIN_PAIRS = 50


class TrainingState:
    def __init__(self) -> None:
        self.running: bool = False
        self.cancelled: bool = False
        self.current_run_id: Optional[int] = None
        self.last_event: dict = {}
        self.cancel_file: Optional[Path] = None
        self.process: Optional[asyncio.subprocess.Process] = None

    def reset(self) -> None:
        self.running = False
        self.cancelled = False
        self.current_run_id = None
        self.last_event = {}
        self.cancel_file = None
        self.process = None


training_state = TrainingState()


def _next_version() -> int:
    base = settings.models_dir
    n = 1
    while (base / f"lora_adapter_v{n}").exists():
        n += 1
    return n


async def _emit(event: dict) -> None:
    training_state.last_event = event
    await training_bus.publish("training", event)


async def _load_pairs(session: AsyncSession) -> list[dict]:
    result = await session.execute(select(TrainingPair))
    pairs = list(result.scalars().all())
    return [
        {
            "instruction": "Ответь на сообщение в моём стиле",
            "input": p.input_text,
            "output": p.output_text,
        }
        for p in pairs
    ]


async def start_training() -> dict:
    if training_state.running:
        return {"started": False, "reason": "already running"}

    async with SessionLocal() as session:
        pairs = await _load_pairs(session)
        if len(pairs) < MIN_PAIRS:
            return {
                "started": False,
                "reason": f"need at least {MIN_PAIRS} pairs, have {len(pairs)}",
            }

        version = _next_version()
        run = TrainingRun(
            version=version,
            started_at=datetime.utcnow(),
            pair_count=len(pairs),
            adapter_path=str(settings.models_dir / f"lora_adapter_v{version}"),
            is_active=False,
            status="running",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    training_state.running = True
    training_state.cancelled = False
    training_state.current_run_id = run_id

    asyncio.create_task(_run_training(run_id, pairs, version))
    return {"started": True, "run_id": run_id, "version": version}


async def _run_training(run_id: int, pairs: list[dict], version: int) -> None:
    output_dir = settings.models_dir / f"lora_adapter_v{version}"
    output_dir.mkdir(parents=True, exist_ok=True)

    pairs_file = output_dir / ".pairs.json"
    pairs_file.write_text(
        json.dumps(pairs, ensure_ascii=False), encoding="utf-8"
    )
    cancel_file = output_dir / ".cancel"
    if cancel_file.exists():
        cancel_file.unlink()
    training_state.cancel_file = cancel_file

    await _emit({"phase": "starting", "version": version, "pairs": len(pairs)})

    final_result: dict = {}
    error_message: Optional[str] = None

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-m",
            "backend.train_worker",
            "--pairs-file", str(pairs_file),
            "--output-dir", str(output_dir),
            "--base-model", settings.hf_base_model,
            "--cancel-file", str(cancel_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        training_state.process = proc

        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(EVENT_PREFIX):
                payload = line[len(EVENT_PREFIX):]
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    log.warning("malformed train event: %s", payload[:200])
                    continue
                phase = event.get("phase")
                if phase == "result":
                    final_result = {k: v for k, v in event.items() if k != "phase"}
                    continue
                if phase == "error":
                    error_message = event.get("error") or "training error"
                await _emit(event)
            elif line:
                log.info("[train_worker] %s", line)

        rc = await proc.wait()
        training_state.process = None

        if error_message is None and rc != 0 and not final_result:
            log_path = output_dir / "train_worker.log"
            hint = (
                "native crash (access violation, segfault, missing CUDA DLL)"
                if rc in (3221225477, -1073741819) else
                "OOM or non-zero exit"
            )
            error_message = (
                f"training process exited with code {rc} ({hint}). "
                f"See per-step log: {log_path}"
            )

        if error_message:
            raise RuntimeError(error_message)

        adapter_path = final_result.get("adapter_path") or str(output_dir)
        was_cancelled = bool(final_result.get("cancelled")) or training_state.cancelled

        gguf_path: Optional[Path] = None
        if not was_cancelled:
            from .gguf_export import merge_and_export_gguf
            await _emit({"phase": "merging_and_exporting_gguf", "version": version})
            gguf_path = await asyncio.to_thread(
                merge_and_export_gguf,
                Path(adapter_path),
                settings.hf_base_model,
                settings.llama_cpp_path,
                settings.lm_studio_models_dir,
                settings.gguf_quant,
            )

        async with SessionLocal() as session:
            res = await session.execute(
                select(TrainingRun).where(TrainingRun.id == run_id)
            )
            run = res.scalar_one()
            run.finished_at = datetime.utcnow()
            run.final_loss = final_result.get("final_loss")
            run.adapter_path = adapter_path
            run.status = "cancelled" if was_cancelled else "done"
            await session.commit()

            if not was_cancelled:
                pair_rows = await session.execute(select(TrainingPair))
                for p in pair_rows.scalars().all():
                    p.used_in_training = True
                await session.commit()

        await _emit({
            "phase": "cancelled" if was_cancelled else "done",
            "adapter_path": adapter_path,
            "gguf_path": str(gguf_path) if gguf_path else None,
            "final_loss": final_result.get("final_loss"),
            "version": version,
        })
    except Exception as e:
        log.exception("training failed")
        async with SessionLocal() as session:
            res = await session.execute(
                select(TrainingRun).where(TrainingRun.id == run_id)
            )
            run = res.scalar_one()
            run.finished_at = datetime.utcnow()
            run.status = "failed"
            await session.commit()
        if error_message is None:
            await _emit({"phase": "error", "error": str(e)})
    finally:
        with contextlib.suppress(FileNotFoundError):
            cancel_file.unlink()
        with contextlib.suppress(FileNotFoundError):
            pairs_file.unlink()
        training_state.reset()


async def cancel_training() -> bool:
    if not training_state.running:
        return False
    training_state.cancelled = True
    if training_state.cancel_file is not None:
        try:
            training_state.cancel_file.touch(exist_ok=True)
        except OSError:
            log.exception("failed to create cancel sentinel")
    return True


async def activate_adapter(run_id: int) -> bool:
    async with SessionLocal() as session:
        result = await session.execute(select(TrainingRun))
        all_runs = list(result.scalars().all())
        target = next((r for r in all_runs if r.id == run_id), None)
        if not target or not target.adapter_path:
            return False
        for r in all_runs:
            r.is_active = (r.id == run_id)
        await session.commit()
    log.info(
        "marked adapter v%s active at %s; load it manually in LM Studio",
        target.version, target.adapter_path,
    )
    return True


async def list_runs() -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(select(TrainingRun).order_by(TrainingRun.id.desc()))
        return [
            {
                "id": r.id,
                "version": r.version,
                "started_at": _iso_utc(r.started_at),
                "finished_at": _iso_utc(r.finished_at),
                "pair_count": r.pair_count,
                "final_loss": r.final_loss,
                "adapter_path": r.adapter_path,
                "is_active": r.is_active,
                "status": r.status,
            }
            for r in result.scalars().all()
        ]
