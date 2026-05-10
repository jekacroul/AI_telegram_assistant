from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import SessionLocal, TrainingPair, TrainingRun
from .event_bus import training_bus


log = logging.getLogger(__name__)

MIN_PAIRS = 50


class TrainingState:
    def __init__(self) -> None:
        self.running: bool = False
        self.cancelled: bool = False
        self.current_run_id: Optional[int] = None
        self.last_event: dict = {}

    def reset(self) -> None:
        self.running = False
        self.cancelled = False
        self.current_run_id = None
        self.last_event = {}


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


def _format_alpaca(example: dict) -> str:
    return (
        f"### Инструкция:\n{example['instruction']}\n\n"
        f"### Вход:\n{example['input']}\n\n"
        f"### Ответ:\n{example['output']}"
    )


def _train_blocking(
    pairs: list[dict],
    output_dir: Path,
    on_event,
    is_cancelled,
) -> dict:
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainerCallback,
        TrainingArguments,
    )
    from trl import SFTTrainer

    model_name = settings.hf_base_model
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    attn = "flash_attention_2"
    try:
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="cuda:0",
            attn_implementation=attn,
        )
    except Exception:  # noqa: BLE001
        model = AutoModelForCausalLM.from_pretrained(
            model_name,
            quantization_config=bnb_config,
            device_map="cuda:0",
        )

    model = prepare_model_for_kbit_training(model)

    lora_config = LoraConfig(
        r=16,
        lora_alpha=32,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_config)

    texts = [_format_alpaca(p) for p in pairs]
    ds = Dataset.from_dict({"text": texts})

    output_dir.mkdir(parents=True, exist_ok=True)
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=3,
        per_device_train_batch_size=4,
        gradient_accumulation_steps=2,
        learning_rate=2e-4,
        logging_steps=1,
        save_strategy="no",
        report_to=[],
        fp16=True,
        optim="paged_adamw_8bit",
    )

    state: dict = {"loss": None, "step": 0, "epoch": 0.0, "start": time.time()}

    class StreamCallback(TrainerCallback):
        def on_log(self, args, ctrl, st, logs=None, **kw):  # type: ignore[override]
            if logs is None:
                return
            step = st.global_step
            total = max(st.max_steps, 1)
            if "loss" in logs:
                state["loss"] = float(logs["loss"])
            state["step"] = step
            state["epoch"] = float(logs.get("epoch", st.epoch or 0.0))
            elapsed = time.time() - state["start"]
            eta = (elapsed / step) * (total - step) if step > 0 else None
            on_event({
                "epoch": state["epoch"],
                "step": step,
                "max_steps": total,
                "loss": state["loss"],
                "eta_seconds": eta,
                "phase": "training",
            })

        def on_step_end(self, args, ctrl, st, **kw):  # type: ignore[override]
            if is_cancelled():
                ctrl.should_training_stop = True
            return ctrl

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        tokenizer=tokenizer,
        dataset_text_field="text",
        max_seq_length=1024,
        callbacks=[StreamCallback()],
    )

    trainer.train()
    trainer.model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))

    final_loss = state.get("loss")
    return {"adapter_path": str(output_dir), "final_loss": final_loss}


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
    loop = asyncio.get_running_loop()

    def on_event(ev: dict) -> None:
        asyncio.run_coroutine_threadsafe(_emit(ev), loop)

    def is_cancelled() -> bool:
        return training_state.cancelled

    await _emit({"phase": "starting", "version": version, "pairs": len(pairs)})

    try:
        result = await asyncio.to_thread(
            _train_blocking, pairs, output_dir, on_event, is_cancelled
        )
        async with SessionLocal() as session:
            res = await session.execute(select(TrainingRun).where(TrainingRun.id == run_id))
            run = res.scalar_one()
            run.finished_at = datetime.utcnow()
            run.final_loss = result.get("final_loss")
            run.adapter_path = result.get("adapter_path")
            run.status = "cancelled" if training_state.cancelled else "done"
            await session.commit()

            pair_rows = await session.execute(select(TrainingPair))
            for p in pair_rows.scalars().all():
                p.used_in_training = True
            await session.commit()

        await _emit({
            "phase": "done",
            "adapter_path": result.get("adapter_path"),
            "final_loss": result.get("final_loss"),
            "version": version,
        })
        await create_ollama_modelfile(output_dir, version)
    except Exception as e:  # noqa: BLE001
        log.exception("training failed")
        async with SessionLocal() as session:
            res = await session.execute(select(TrainingRun).where(TrainingRun.id == run_id))
            run = res.scalar_one()
            run.finished_at = datetime.utcnow()
            run.status = "failed"
            await session.commit()
        await _emit({"phase": "error", "error": str(e)})
    finally:
        training_state.reset()


async def create_ollama_modelfile(adapter_dir: Path, version: int) -> None:
    """Write Modelfile so adapter can be hot-loaded into Ollama."""
    modelfile = adapter_dir / "Modelfile"
    content = (
        f"FROM {settings.ollama_model}\n"
        f"ADAPTER {adapter_dir}\n"
    )
    modelfile.write_text(content, encoding="utf-8")
    log.info("wrote Modelfile at %s", modelfile)


async def cancel_training() -> bool:
    if not training_state.running:
        return False
    training_state.cancelled = True
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

    import httpx
    adapter_dir = Path(target.adapter_path)
    modelfile = adapter_dir / "Modelfile"
    if not modelfile.exists():
        await create_ollama_modelfile(adapter_dir, target.version)
    name = f"{settings.ollama_model}-style-v{target.version}"
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            await client.post(
                f"{settings.ollama_host}/api/create",
                json={"name": name, "modelfile": modelfile.read_text("utf-8")},
            )
        from .llm_engine import get_client as get_llm
        get_llm().model = name
    except Exception:  # noqa: BLE001
        log.exception("hot-reload to Ollama failed")
        return False
    return True


async def list_runs() -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(select(TrainingRun).order_by(TrainingRun.id.desc()))
        return [
            {
                "id": r.id,
                "version": r.version,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "pair_count": r.pair_count,
                "final_loss": r.final_loss,
                "adapter_path": r.adapter_path,
                "is_active": r.is_active,
                "status": r.status,
            }
            for r in result.scalars().all()
        ]
