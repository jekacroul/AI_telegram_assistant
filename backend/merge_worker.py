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
from typing import Optional

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


def _compute_max_memory(torch_module) -> tuple[Optional[dict], str]:
    """Decide how to split the fp16 base across GPU + CPU + disk offload.

    Returns (max_memory_dict or None for CPU-only, human readable plan).
    None means CUDA is not available — caller falls back to CPU-only.

    Budgets can be overridden via env vars:
      MERGE_GPU_BUDGET_GIB — VRAM reservation in GiB
      MERGE_CPU_BUDGET_GIB — RAM reservation in GiB
    """
    if not torch_module.cuda.is_available():
        return None, "CUDA недоступен → грузим целиком на CPU"

    free_vram, total_vram = torch_module.cuda.mem_get_info()
    free_vram_gib = free_vram / (1024 ** 3)
    # Hold back ~3 GiB on the GPU for activations during merge_and_unload
    # plus accelerate's own scratch space. Cap at 10 GiB so a 12 GB card
    # still has ~2 GiB free for the python process itself.
    gpu_budget_gib = min(10.0, max(0.0, free_vram_gib - 3.0))

    try:
        import psutil  # type: ignore
        avail_ram_gib = psutil.virtual_memory().available / (1024 ** 3)
        ram_source = "psutil"
    except ImportError:
        avail_ram_gib = 16.0
        ram_source = "fallback (psutil не установлен)"

    # Reserve ~10 GiB for OS + python + transformers/peft + temporary
    # buffers during weight load. Even on a 32 GB box that leaves only
    # ~18 GiB for the model copy on CPU — anything beyond that should
    # spill to disk via offload_folder, which is slow but does not
    # crash the way mmap+swap does on Windows.
    cpu_budget_gib = max(2.0, avail_ram_gib - 10.0)
    cpu_budget_gib = min(cpu_budget_gib, 18.0)

    # Env-var overrides for advanced tuning.
    if os.environ.get("MERGE_GPU_BUDGET_GIB"):
        try:
            gpu_budget_gib = float(os.environ["MERGE_GPU_BUDGET_GIB"])
        except ValueError:
            pass
    if os.environ.get("MERGE_CPU_BUDGET_GIB"):
        try:
            cpu_budget_gib = float(os.environ["MERGE_CPU_BUDGET_GIB"])
        except ValueError:
            pass

    if gpu_budget_gib < 1.0:
        return None, (
            f"свободно {free_vram_gib:.1f} GiB VRAM — слишком мало для mixed"
            f", грузим на CPU"
        )

    plan = {
        0: f"{int(gpu_budget_gib)}GiB",
        "cpu": f"{int(cpu_budget_gib)}GiB",
    }
    summary = (
        f"GPU budget {int(gpu_budget_gib)} GiB (free {free_vram_gib:.1f}/"
        f"{total_vram/(1024**3):.1f}), CPU budget {int(cpu_budget_gib)} GiB "
        f"(available {avail_ram_gib:.1f}, {ram_source}). "
        f"Остаток ~{max(0, 24 - int(gpu_budget_gib) - int(cpu_budget_gib))} GiB "
        f"уйдёт в disk-offload."
    )
    return plan, summary


def _precheck_memory(torch_module) -> Optional[str]:
    """Verify we have enough free VRAM/RAM to attempt the merge before any
    weights are touched. Returns an error message on failure, None on OK.
    Numbers tuned for a 12B fp16 base (~24 GB on disk)."""
    if torch_module.cuda.is_available():
        free_vram, total_vram = torch_module.cuda.mem_get_info()
        free_vram_gib = free_vram / (1024 ** 3)
        # Need at least ~4 GiB free on the GPU to be worth using; below
        # that mixed mode is pointless and we'd want pure CPU anyway.
        if free_vram_gib < 4.0:
            log(f"  VRAM tight: free {free_vram_gib:.1f} GiB — будем грузить полностью на CPU")
    try:
        import psutil  # type: ignore
        avail_ram_gib = psutil.virtual_memory().available / (1024 ** 3)
        log(f"  free RAM available: {avail_ram_gib:.1f} GiB")
        # 12B fp16 model is ~24 GB on disk. With mixed CPU+GPU split and
        # offload_folder we can survive on much less, but we still need
        # ~10 GiB free RAM to load shards plus python/transformers
        # overhead. Below that the load gets unstable.
        if avail_ram_gib < 10.0:
            return (
                f"Недостаточно свободной RAM: доступно {avail_ram_gib:.1f} GiB. "
                f"Закрой LM Studio, браузер и другие тяжёлые приложения, чтобы "
                f"освободить минимум 10 GiB, и попробуй снова."
            )
    except ImportError:
        log("  psutil не установлен, пропускаем RAM-precheck")
    return None


def run_merge(adapter_dir: Path, base_model: str, output_dir: Path) -> dict:
    log("step:import_torch")
    import torch
    log(f"  torch {torch.__version__} cuda_avail={torch.cuda.is_available()}")

    log("step:precheck_memory")
    err = _precheck_memory(torch)
    if err is not None:
        emit({"phase": "error", "error": err})
        log(f"FATAL: {err}")
        sys.exit(2)

    log("step:import_transformers")
    from transformers import AutoModelForCausalLM, AutoTokenizer
    log("step:import_peft")
    from peft import PeftModel

    emit({"phase": "loading_base", "base_model": base_model})
    log(f"step:load_base_fp16 base_model={base_model}")

    offload_dir = output_dir.parent / ".offload"
    offload_dir.mkdir(parents=True, exist_ok=True)

    # Split the fp16 base across GPU + CPU based on what's actually free
    # right now. A 12B fp16 model is ~24 GB. Pure CPU on a 32 GB box gets
    # close to the page-file boundary and hits an access violation
    # (exit 3221225477) somewhere in the middle of weight load. Letting
    # the GPU eat ~10 GB of weights drops CPU pressure to ~14 GB, which
    # leaves comfortable headroom. offload_folder is a final safety net
    # for setups where the budget still does not fit.
    max_memory, plan = _compute_max_memory(torch)
    device_map: object = "auto" if max_memory is not None else {"": "cpu"}
    log(f"  plan: {plan}")
    log(f"  device_map={device_map} offload_folder={offload_dir}")

    load_kwargs = dict(
        torch_dtype=torch.float16,
        device_map=device_map,
        low_cpu_mem_usage=True,
        offload_folder=str(offload_dir),
    )
    if max_memory is not None:
        load_kwargs["max_memory"] = max_memory

    base = AutoModelForCausalLM.from_pretrained(base_model, **load_kwargs)
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
