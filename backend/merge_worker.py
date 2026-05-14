"""Subprocess entry point for merging a PEFT LoRA adapter into its base model.

Runs in an isolated process so loading a fp16 7B/12B model (~14-24 GB)
cannot crash the FastAPI backend. The merged model is saved as Hugging
Face safetensors which `llama.cpp/convert_hf_to_gguf.py` can then turn
into a single self-contained GGUF.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
try:
    import pyarrow  # noqa: F401
    import pyarrow.lib  # noqa: F401
except ImportError:
    pass

EVENT_PREFIX = "__MERGE_EVENT__ "

_log_file = None  # type: ignore[var-annotated]


def _open_log(output_dir: Path) -> None:
    global _log_file
    output_dir.mkdir(parents=True, exist_ok=True)
    _log_file = open(output_dir / "merge_worker.log", "w", encoding="utf-8", buffering=1)


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    if _log_file is not None:
        _log_file.write(line + "\n")
        _log_file.flush()
        try:
            os.fsync(_log_file.fileno())
        except OSError:
            pass


def emit(event: dict) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_merge(adapter_dir: Path, base_model: str, output_dir: Path) -> dict:
    log("step:import_torch")
    import torch
    log(f"  torch {torch.__version__} cuda_avail={torch.cuda.is_available()}")

    log("step:import_transformers")
    from transformers import AutoModelForCausalLM, AutoTokenizer
    log("step:import_peft")
    from peft import PeftModel

    emit({"phase": "loading_base", "base_model": base_model})
    log(f"step:load_base_fp16 base_model={base_model}")
    # Merge is a one-shot weight op, no training-grade compute. Force the
    # base model onto CPU even when CUDA is available: a 12B fp16 model
    # is ~24 GB which does not fit in 12 GB VRAM, and accelerate's mixed
    # GPU/CPU dispatch on Windows reliably triggers an access violation
    # (exit 3221225477) somewhere around 50-60% of weight load. Disk
    # offload covers machines without enough RAM either.
    offload_dir = output_dir.parent / ".offload"
    offload_dir.mkdir(parents=True, exist_ok=True)
    log(f"  device_map=cpu offload_folder={offload_dir}")
    base = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=torch.float16,
        device_map={"": "cpu"},
        low_cpu_mem_usage=True,
        offload_folder=str(offload_dir),
    )
    log("step:base_loaded")

    emit({"phase": "loading_adapter", "adapter_dir": str(adapter_dir)})
    log(f"step:load_adapter {adapter_dir}")
    model = PeftModel.from_pretrained(base, str(adapter_dir))

    emit({"phase": "merging"})
    log("step:merge_and_unload")
    merged = model.merge_and_unload()

    output_dir.mkdir(parents=True, exist_ok=True)
    emit({"phase": "saving_merged", "output_dir": str(output_dir)})
    log(f"step:save_pretrained {output_dir}")
    merged.save_pretrained(str(output_dir), safe_serialization=True)

    log("step:save_tokenizer")
    tokenizer = AutoTokenizer.from_pretrained(base_model, use_fast=True)
    tokenizer.save_pretrained(str(output_dir))

    # Drop the offload scratch dir — it can be tens of GB and is only
    # useful while the model object is alive.
    import shutil
    shutil.rmtree(offload_dir, ignore_errors=True)
    log("step:done")

    return {"merged_dir": str(output_dir)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-dir", required=True)
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    _open_log(output_dir)
    log(f"merge_worker started pid={os.getpid()} python={sys.executable}")

    try:
        result = run_merge(
            adapter_dir=Path(args.adapter_dir),
            base_model=args.base_model,
            output_dir=output_dir,
        )
    except Exception as e:
        log(f"FATAL python exception: {type(e).__name__}: {e}")
        log(traceback.format_exc())
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
