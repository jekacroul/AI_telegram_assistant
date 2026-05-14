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
# When stdout is piped, Windows defaults it to cp1252 and chews up our
# Russian status messages. Force UTF-8 so the parent (text=True Popen
# with encoding='utf-8') gets clean bytes.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except (AttributeError, OSError):
    pass
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
    """Decide how to split the fp16 base across GPU + CPU (NO disk).

    Disk offload triggers a PEFT bug (KeyError in _update_offload because
    of an extra "model." prefix when the base has offloaded layers), so
    we deliberately do not pass offload_folder. The model must fit in
    GPU+CPU budgets — if it doesn't, we'd rather fail fast and tell the
    user to free RAM than start a multi-minute load that crashes.

    Returns (max_memory_dict or None for CPU-only, human readable plan).

    Budgets can be overridden via env vars:
      MERGE_GPU_BUDGET_GIB — VRAM reservation in GiB
      MERGE_CPU_BUDGET_GIB — RAM reservation in GiB
    """
    # Mistral-Nemo 12B in fp16: ~24.5 GiB of param weights + ~1.3 GiB
    # embed_tokens (vocab 131k × hidden 5120) + ~1.3 GiB lm_head +
    # accelerate's per-layer overhead ≈ 28 GiB.
    model_size_gib = 28.0

    cuda_ok = torch_module.cuda.is_available()
    free_vram_gib = 0.0
    total_vram_gib = 0.0
    if cuda_ok:
        free_vram, total_vram = torch_module.cuda.mem_get_info()
        free_vram_gib = free_vram / (1024 ** 3)
        total_vram_gib = total_vram / (1024 ** 3)

    # No forward pass happens during from_pretrained, so 1 GiB on the GPU
    # is plenty for accelerate's scratch space and the per-layer matmuls
    # in merge_and_unload (which work on small r×N×N tiles).
    gpu_budget_gib = max(0.0, free_vram_gib - 1.0) if cuda_ok else 0.0

    try:
        import psutil  # type: ignore
        avail_ram_gib = psutil.virtual_memory().available / (1024 ** 3)
        ram_source = "psutil"
    except ImportError:
        avail_ram_gib = 16.0
        ram_source = "fallback (psutil не установлен)"

    # Reserve ~6 GiB for OS + python + transformers/peft + temp buffers.
    # Tight but workable on a 32 GB box if the user closed LM Studio and
    # other heavy apps. Most peak temporary load is during weight copy.
    cpu_budget_gib = max(2.0, avail_ram_gib - 6.0)

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

    total_budget = gpu_budget_gib + cpu_budget_gib
    if total_budget < model_size_gib:
        return None, (
            f"FAIL: суммарный бюджет {total_budget:.1f} GiB меньше, чем размер "
            f"модели ~{model_size_gib:.0f} GiB. Свободно: GPU {free_vram_gib:.1f}/{total_vram_gib:.1f} GiB, "
            f"RAM {avail_ram_gib:.1f} GiB. Закрой LM Studio, браузер и другие "
            f"тяжёлые приложения и попробуй снова."
        )

    if not cuda_ok or gpu_budget_gib < 1.0:
        # Pure-CPU path. Need full model + buffers on RAM.
        return None, (
            f"CPU-only режим. RAM available {avail_ram_gib:.1f} GiB "
            f"(потребуется ~{model_size_gib:.0f} GiB на модель)."
        )

    plan = {
        0: f"{int(gpu_budget_gib)}GiB",
        "cpu": f"{int(cpu_budget_gib)}GiB",
    }
    summary = (
        f"GPU budget {int(gpu_budget_gib)} GiB (free {free_vram_gib:.1f}/"
        f"{total_vram_gib:.1f}), CPU budget {int(cpu_budget_gib)} GiB "
        f"(available {avail_ram_gib:.1f}, {ram_source}). "
        f"Сумма {int(total_budget)} GiB ≥ модель {int(model_size_gib)} GiB — "
        f"disk-offload не понадобится."
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


def _patch_peft_update_offload(peft_module) -> None:
    """PEFT (at least up through 0.19) has multiple bugs in _update_offload
    when the base model is accelerate-dispatched across GPU+CPU. Seen so
    far:
      • KeyError 'base_model.model.model.lm_head' — wrong "model." depth
      • KeyError '...q_proj.base_layer.weight' — looks up a parameter path
        in a module dict (forgets to strip .weight)

    `_update_offload` is only meaningful when weights actually live on
    disk. With our current load (GPU + CPU only, no offload_folder), the
    adapter weights have already been applied to in-memory LoRA layers
    by set_peft_model_state_dict — which runs BEFORE _update_offload.
    Skip the buggy call and let merge_and_unload work with the in-memory
    state."""
    try:
        from peft.peft_model import PeftModel as _PeftModel
        orig = _PeftModel._update_offload
    except (ImportError, AttributeError) as e:
        log(f"  не смог найти PeftModel._update_offload для патча: {e}")
        return

    if getattr(orig, "_patched_by_us", False):
        return

    def patched(self, offload_index, adapters_weights):
        try:
            return orig(self, offload_index, adapters_weights)
        except Exception as e:  # noqa: BLE001
            log(
                f"  PEFT _update_offload пропущен из-за {type(e).__name__}: "
                f"{e}. Адаптерные веса уже применены через "
                f"set_peft_model_state_dict до этой точки; "
                f"merge_and_unload должен отработать корректно."
            )
            return

    patched._patched_by_us = True  # type: ignore[attr-defined]
    _PeftModel._update_offload = patched
    log("  применён monkey-patch для PEFT _update_offload (broad)")


def _fail_if_memory_error(e: Exception, stage: str) -> bool:
    """Return True (after emitting + exiting) if the exception looks like
    accelerate complaining the model does not fit. Caller should re-raise
    otherwise. `stage` is a short label for the log."""
    msg = str(e)
    looks_like_oom = (
        "offload_dir" in msg
        or "offload_folder" in msg
        or ("doesn" in msg and "fit" in msg.lower())
        or "out of memory" in msg.lower()
        or "OutOfMemoryError" in type(e).__name__
    )
    if not looks_like_oom:
        return False

    free_ram_now = 0.0
    try:
        import psutil  # type: ignore
        free_ram_now = psutil.virtual_memory().available / (1024 ** 3)
    except ImportError:
        pass
    emit({
        "phase": "error",
        "error": (
            f"Модель ~28 GiB не помещается в GPU+CPU без disk-offload "
            f"(не прошло на этапе: {stage}). Сейчас свободно RAM "
            f"{free_ram_now:.1f} GiB. На 32 GB машине этого мало для "
            f"merge: закрой LM Studio, браузер, IDE — свободной RAM "
            f"нужно минимум 26-28 GiB.\n\n"
            f"Проще и надёжнее: нажми «LoRA → GGUF» вместо «Merge → GGUF». "
            f"Она вообще не загружает базовую модель, выдаёт файл ~100 MB, "
            f"который LM Studio цепляет поверх базы."
        ),
    })
    log(f"FATAL ({stage}): model doesn't fit: {e}")
    sys.exit(2)


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
    import peft
    from peft import PeftModel
    log(f"  peft {getattr(peft, '__version__', '?')}")
    _patch_peft_update_offload(peft)

    emit({"phase": "loading_base", "base_model": base_model})
    log(f"step:load_base_fp16 base_model={base_model}")

    max_memory, plan = _compute_max_memory(torch)
    log(f"  plan: {plan}")

    if plan.startswith("FAIL"):
        emit({"phase": "error", "error": plan[5:].strip()})
        log("FATAL: insufficient memory for merge")
        sys.exit(2)

    device_map: object = "auto" if max_memory is not None else {"": "cpu"}
    log(f"  device_map={device_map} (disk-offload отключён, PEFT с ним падает)")

    load_kwargs = dict(
        torch_dtype=torch.float16,
        device_map=device_map,
        low_cpu_mem_usage=True,
    )
    if max_memory is not None:
        load_kwargs["max_memory"] = max_memory

    try:
        base = AutoModelForCausalLM.from_pretrained(base_model, **load_kwargs)
    except (RuntimeError, ValueError) as e:
        _fail_if_memory_error(e, stage="load_base")
        raise
    log("step:base_loaded")

    emit({"phase": "loading_adapter", "adapter_dir": str(adapter_dir)})
    log(f"step:load_adapter {adapter_dir}")
    try:
        model = PeftModel.from_pretrained(base, str(adapter_dir))
    except (RuntimeError, ValueError) as e:
        _fail_if_memory_error(e, stage="load_adapter")
        raise

    emit({"phase": "merging"})
    log("step:merge_and_unload")
    try:
        merged = model.merge_and_unload()
    except (RuntimeError, ValueError) as e:
        _fail_if_memory_error(e, stage="merge_and_unload")
        raise

    output_dir.mkdir(parents=True, exist_ok=True)
    emit({"phase": "saving_merged", "output_dir": str(output_dir)})
    log(f"step:save_pretrained {output_dir}")
    merged.save_pretrained(str(output_dir), safe_serialization=True)

    log("step:save_tokenizer")
    tokenizer = AutoTokenizer.from_pretrained(base_model, use_fast=True)
    tokenizer.save_pretrained(str(output_dir))
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
