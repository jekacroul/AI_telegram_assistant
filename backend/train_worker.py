"""Subprocess entry point for LoRA training.

Runs in an isolated process so that Rust panics, CUDA OOM aborts, or other
non-recoverable failures cannot bring down the FastAPI backend.

Communication with the parent is one-way: events are emitted to stdout as JSON
lines prefixed with EVENT_PREFIX. Cancellation is signaled by the parent
creating a sentinel file whose path is passed via --cancel-file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

EVENT_PREFIX = "__TRAIN_EVENT__ "


def emit(event: dict) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _format_alpaca(example: dict) -> str:
    return (
        f"### Инструкция:\n{example['instruction']}\n\n"
        f"### Вход:\n{example['input']}\n\n"
        f"### Ответ:\n{example['output']}"
    )


def _preflight() -> None:
    try:
        import torch
    except ImportError as e:
        emit({"phase": "error", "error": f"PyTorch is not installed: {e}"})
        sys.exit(2)
    if not torch.cuda.is_available():
        emit({
            "phase": "error",
            "error": (
                "CUDA is not available. LoRA training with 4-bit quantization "
                "requires an NVIDIA GPU with a working CUDA build of PyTorch. "
                "Install a CUDA-enabled torch wheel and verify torch.cuda.is_available()."
            ),
        })
        sys.exit(2)


def run_training(
    pairs: list[dict],
    output_dir: Path,
    base_model: str,
    cancel_file: Path,
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

    tokenizer = AutoTokenizer.from_pretrained(base_model, use_fast=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )

    try:
        model = AutoModelForCausalLM.from_pretrained(
            base_model,
            quantization_config=bnb_config,
            device_map="cuda:0",
            attn_implementation="flash_attention_2",
        )
    except Exception:
        model = AutoModelForCausalLM.from_pretrained(
            base_model,
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

    def _is_cancelled() -> bool:
        return cancel_file.exists()

    class StreamCallback(TrainerCallback):
        def on_log(self, args, ctrl, st, logs=None, **kw):
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
            emit({
                "epoch": state["epoch"],
                "step": step,
                "max_steps": total,
                "loss": state["loss"],
                "eta_seconds": eta,
                "phase": "training",
            })

        def on_step_end(self, args, ctrl, st, **kw):
            if _is_cancelled():
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

    return {
        "adapter_path": str(output_dir),
        "final_loss": state.get("loss"),
        "cancelled": _is_cancelled(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--cancel-file", required=True)
    args = parser.parse_args()

    _preflight()

    try:
        pairs = json.loads(Path(args.pairs_file).read_text(encoding="utf-8"))
    except Exception as e:
        emit({"phase": "error", "error": f"failed to read pairs: {e}"})
        return 2

    try:
        result = run_training(
            pairs=pairs,
            output_dir=Path(args.output_dir),
            base_model=args.base_model,
            cancel_file=Path(args.cancel_file),
        )
    except Exception as e:
        emit({
            "phase": "error",
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        })
        return 1

    emit({"phase": "result", **result})
    return 0


if __name__ == "__main__":
    sys.exit(main())
