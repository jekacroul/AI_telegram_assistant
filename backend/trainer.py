"""Local LoRA fine-tuning pipeline using HF PEFT + transformers + bitsandbytes."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from .config import get_settings
from .database import SessionLocal, TrainingPair, TrainingRun
from .dataset_builder import INSTRUCTION, build_pairs, export_jsonl

log = logging.getLogger(__name__)


@dataclass
class TrainingProgress:
    status: str = "idle"  # idle | preparing | running | finished | failed | cancelled
    version: int | None = None
    current_epoch: int = 0
    total_epochs: int = 0
    current_step: int = 0
    total_steps: int = 0
    last_loss: float | None = None
    final_loss: float | None = None
    eta_seconds: float | None = None
    pair_count: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    adapter_path: str | None = None
    message: str = ""
    history: list[dict[str, float]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "version": self.version,
            "current_epoch": self.current_epoch,
            "total_epochs": self.total_epochs,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "last_loss": self.last_loss,
            "final_loss": self.final_loss,
            "eta_seconds": self.eta_seconds,
            "pair_count": self.pair_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "adapter_path": self.adapter_path,
            "message": self.message,
            "history": self.history[-200:],
        }


class TrainerService:
    def __init__(self) -> None:
        self.progress = TrainingProgress()
        self._task: asyncio.Task | None = None
        self._cancel = False
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._lock = asyncio.Lock()

    def add_listener(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[dict[str, Any]], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _emit(self) -> None:
        snap = self.progress.snapshot()
        for fn in list(self._listeners):
            try:
                fn(snap)
            except Exception:
                log.exception("Listener failed")

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def cancel(self) -> bool:
        if not self.is_running:
            return False
        self._cancel = True
        return True

    async def start(
        self, epochs: int = 3, batch_size: int = 4, learning_rate: float = 2e-4
    ) -> dict[str, Any]:
        async with self._lock:
            if self.is_running:
                return {"started": False, "message": "Training already running."}
            settings = get_settings()
            stats = await build_pairs(persist=True)
            if stats["total_pairs"] < settings.min_pairs_for_training:
                return {
                    "started": False,
                    "message": (
                        f"Need at least {settings.min_pairs_for_training} pairs, "
                        f"have {stats['total_pairs']}."
                    ),
                }
            export = await export_jsonl()
            self._cancel = False
            self.progress = TrainingProgress(
                status="preparing",
                version=export["version"],
                pair_count=export["count"],
                total_epochs=epochs,
                started_at=time.time(),
                message="Loading base model and tokenizer…",
            )
            self._emit()

            async with SessionLocal() as session:
                run = TrainingRun(
                    version=export["version"],
                    pair_count=export["count"],
                    started_at=datetime.utcnow(),
                    base_model=settings.hf_base_model,
                    status="running",
                )
                session.add(run)
                await session.commit()
                run_id = run.id

            self._task = asyncio.create_task(
                self._run(
                    run_id=run_id,
                    dataset_path=Path(export["path"]),
                    version=export["version"],
                    epochs=epochs,
                    batch_size=batch_size,
                    learning_rate=learning_rate,
                )
            )
            return {
                "started": True,
                "version": export["version"],
                "pair_count": export["count"],
            }

    async def _run(
        self,
        run_id: int,
        dataset_path: Path,
        version: int,
        epochs: int,
        batch_size: int,
        learning_rate: float,
    ) -> None:
        try:
            result = await asyncio.to_thread(
                self._train_sync,
                str(dataset_path),
                version,
                epochs,
                batch_size,
                learning_rate,
            )
            self.progress.status = "finished"
            self.progress.finished_at = time.time()
            self.progress.final_loss = result.get("final_loss")
            self.progress.adapter_path = result.get("adapter_path")
            self.progress.message = "Training complete."
            self._emit()
            async with SessionLocal() as session:
                run = await session.get(TrainingRun, run_id)
                if run is not None:
                    run.finished_at = datetime.utcnow()
                    run.final_loss = result.get("final_loss")
                    run.adapter_path = result.get("adapter_path")
                    run.status = "finished"
                    await session.commit()
                pairs = (await session.execute(select(TrainingPair))).scalars().all()
                for pair in pairs:
                    pair.used_in_training = True
                await session.commit()
        except _CancelledTraining:
            self.progress.status = "cancelled"
            self.progress.finished_at = time.time()
            self.progress.message = "Training cancelled."
            self._emit()
            async with SessionLocal() as session:
                run = await session.get(TrainingRun, run_id)
                if run is not None:
                    run.status = "cancelled"
                    run.finished_at = datetime.utcnow()
                    await session.commit()
        except Exception as exc:
            log.exception("Training failed")
            self.progress.status = "failed"
            self.progress.finished_at = time.time()
            self.progress.message = f"Training failed: {exc}"
            self._emit()
            async with SessionLocal() as session:
                run = await session.get(TrainingRun, run_id)
                if run is not None:
                    run.status = "failed"
                    run.finished_at = datetime.utcnow()
                    run.notes = str(exc)
                    await session.commit()

    def _train_sync(
        self,
        dataset_path: str,
        version: int,
        epochs: int,
        batch_size: int,
        learning_rate: float,
    ) -> dict[str, Any]:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainerCallback,
            TrainingArguments,
        )

        settings = get_settings()
        out_dir = Path(settings.training_output_dir) / f"lora_adapter_v{version}"
        out_dir.mkdir(parents=True, exist_ok=True)

        use_cuda = torch.cuda.is_available()
        load_in_4bit = bool(settings.use_4bit and use_cuda)

        self.progress.message = (
            f"Loading {settings.hf_base_model} ({'4bit GPU' if load_in_4bit else 'CPU/fp32 fallback'})…"
        )
        self._emit()

        tokenizer = AutoTokenizer.from_pretrained(
            settings.hf_base_model, use_fast=True, trust_remote_code=True
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        model_kwargs: dict[str, Any] = {"trust_remote_code": True}
        if load_in_4bit:
            from transformers import BitsAndBytesConfig

            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )
            model_kwargs["device_map"] = "auto"
        else:
            model_kwargs["torch_dtype"] = torch.float32
        base = AutoModelForCausalLM.from_pretrained(settings.hf_base_model, **model_kwargs)
        if load_in_4bit:
            base = prepare_model_for_kbit_training(base)

        lora_config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "v_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(base, lora_config)

        ds = load_dataset("json", data_files=dataset_path, split="train")

        def format_example(example: dict[str, Any]) -> dict[str, Any]:
            text = (
                f"<s>[INST] {example.get('instruction', INSTRUCTION)}\n\n"
                f"{example.get('input', '')} [/INST] {example.get('output', '')}</s>"
            )
            tokens = tokenizer(
                text,
                truncation=True,
                max_length=1024,
                padding="max_length",
            )
            tokens["labels"] = tokens["input_ids"].copy()
            return tokens

        ds = ds.map(format_example, remove_columns=ds.column_names)

        steps_per_epoch = max(1, len(ds) // max(1, batch_size))
        total_steps = max(1, steps_per_epoch * epochs)
        self.progress.total_steps = total_steps
        self.progress.status = "running"
        self.progress.message = "Starting training…"
        self._emit()

        progress_ref = self.progress
        emit_ref = self._emit
        cancel_attr = lambda: self._cancel  # noqa: E731

        class ProgressCallback(TrainerCallback):
            def on_log(self, args, state, control, logs=None, **kwargs):  # noqa: D401
                if not logs:
                    return
                step = state.global_step or 0
                progress_ref.current_step = step
                progress_ref.current_epoch = int(state.epoch or 0) + 1
                if "loss" in logs:
                    progress_ref.last_loss = float(logs["loss"])
                    progress_ref.history.append(
                        {"step": step, "loss": float(logs["loss"]), "epoch": float(state.epoch or 0)}
                    )
                if progress_ref.started_at and step:
                    elapsed = time.time() - progress_ref.started_at
                    rate = elapsed / step
                    remaining = max(0, progress_ref.total_steps - step)
                    progress_ref.eta_seconds = rate * remaining
                emit_ref()

            def on_step_end(self, args, state, control, **kwargs):
                if cancel_attr():
                    control.should_training_stop = True
                return control

        training_args = TrainingArguments(
            output_dir=str(out_dir / "checkpoints"),
            num_train_epochs=epochs,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=1,
            learning_rate=learning_rate,
            logging_steps=1,
            save_strategy="no",
            report_to=[],
            fp16=load_in_4bit,
            optim="paged_adamw_8bit" if load_in_4bit else "adamw_torch",
            warmup_ratio=0.03,
            lr_scheduler_type="cosine",
        )
        collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=ds,
            data_collator=collator,
            callbacks=[ProgressCallback()],
        )

        train_result = trainer.train()
        if self._cancel:
            raise _CancelledTraining()

        model.save_pretrained(str(out_dir))
        tokenizer.save_pretrained(str(out_dir))
        meta = {
            "version": version,
            "base_model": settings.hf_base_model,
            "pair_count": len(ds),
            "final_loss": float(train_result.training_loss),
            "saved_at": datetime.utcnow().isoformat(),
        }
        (out_dir / "adapter_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        return {
            "final_loss": float(train_result.training_loss),
            "adapter_path": str(out_dir),
        }


class _CancelledTraining(RuntimeError):
    pass


async def list_runs() -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(TrainingRun).order_by(TrainingRun.version.desc()))
        ).scalars().all()
    return [
        {
            "id": r.id,
            "version": r.version,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "pair_count": r.pair_count,
            "final_loss": r.final_loss,
            "adapter_path": r.adapter_path,
            "status": r.status,
            "base_model": r.base_model,
        }
        for r in rows
    ]


trainer_service = TrainerService()
