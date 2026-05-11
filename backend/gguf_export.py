"""Merge a PEFT LoRA adapter into its base model and export a single GGUF.

The result is a self-contained model file you load in LM Studio as a normal
model (not as an adapter). Pipeline:

  1. Spawn `backend.merge_worker` in a subprocess to load the fp16 base
     model, apply the adapter, and save the merged weights as HF safetensors.
     Subprocess isolation keeps fp16 model loads (~14-24 GB) from
     destabilizing the FastAPI process.
  2. Call `llama.cpp/convert_hf_to_gguf.py` against the merged dir to
     produce an fp16 GGUF.
  3. Quantize that fp16 GGUF with `llama-quantize` (Q8_0 by default).
  4. Optionally copy the quantized file into LM Studio's models dir.
  5. Delete intermediates (merged HF dir, fp16 GGUF) — they're large and
     only used as build artifacts.

If `LLAMA_CPP_PATH` is not configured, the whole step is skipped with a log
message; training itself is unaffected.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _find_convert_script(llama_cpp_path: Path) -> Optional[Path]:
    candidates = [
        llama_cpp_path / "convert_hf_to_gguf.py",
        llama_cpp_path / "convert-hf-to-gguf.py",  # older name
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _find_quantize_binary(llama_cpp_path: Path) -> Optional[Path]:
    exe = ".exe" if os.name == "nt" else ""
    candidates = [
        llama_cpp_path / "build" / "bin" / f"llama-quantize{exe}",
        llama_cpp_path / "build" / "bin" / "Release" / f"llama-quantize{exe}",
        llama_cpp_path / f"llama-quantize{exe}",
        # legacy name pre-rename
        llama_cpp_path / "build" / "bin" / f"quantize{exe}",
        llama_cpp_path / f"quantize{exe}",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _run_merge_subprocess(
    adapter_dir: Path, base_model: str, merged_dir: Path
) -> bool:
    cmd = [
        sys.executable, "-u", "-m", "backend.merge_worker",
        "--adapter-dir", str(adapter_dir),
        "--base-model", base_model,
        "--output-dir", str(merged_dir),
    ]
    log.info("merging LoRA into base: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("merge subprocess crashed: %s", e)
        return False

    if proc.returncode != 0:
        log.warning(
            "merge subprocess failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        return False

    config_file = merged_dir / "config.json"
    if not config_file.is_file():
        log.warning("merge reported success but %s is missing.", config_file)
        return False
    return True


def merge_and_export_gguf(
    adapter_dir: Path,
    base_model: str,
    llama_cpp_path: Optional[str],
    lm_studio_models_dir: Optional[str],
    quant: str = "Q8_0",
) -> Optional[Path]:
    """Produce a single self-contained GGUF for LM Studio.

    Returns the path to the final quantized .gguf on success, or None if
    the step was skipped or failed (already logged).
    """
    if not llama_cpp_path:
        log.info(
            "LLAMA_CPP_PATH not set; skipping GGUF export. "
            "Clone llama.cpp and set the path in .env to enable auto-export."
        )
        return None

    llama_root = Path(llama_cpp_path).expanduser()
    if not llama_root.is_dir():
        log.warning("LLAMA_CPP_PATH=%s does not exist; skipping GGUF export.", llama_root)
        return None

    convert_script = _find_convert_script(llama_root)
    if convert_script is None:
        log.warning(
            "convert_hf_to_gguf.py not found under %s; skipping GGUF export. "
            "Make sure the llama.cpp checkout is up to date.",
            llama_root,
        )
        return None

    quantize_bin = _find_quantize_binary(llama_root)
    if quantize_bin is None:
        log.warning(
            "llama-quantize binary not found under %s/build/bin. "
            "Build llama.cpp (cmake -B build && cmake --build build --config Release) "
            "to enable quantization. Skipping GGUF export.",
            llama_root,
        )
        return None

    merged_dir = adapter_dir / "merged"
    fp16_gguf = adapter_dir / "merged.f16.gguf"
    final_gguf = adapter_dir / f"merged.{quant.lower()}.gguf"

    # 1. merge weights via isolated subprocess
    if not _run_merge_subprocess(adapter_dir, base_model, merged_dir):
        return None

    # 2. HF merged dir -> fp16 GGUF
    convert_cmd = [
        sys.executable, str(convert_script), str(merged_dir),
        "--outfile", str(fp16_gguf),
        "--outtype", "f16",
    ]
    log.info("converting merged model to GGUF: %s", " ".join(convert_cmd))
    try:
        proc = subprocess.run(
            convert_cmd,
            capture_output=True,
            text=True,
            cwd=str(llama_root),
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("convert_hf_to_gguf crashed: %s", e)
        shutil.rmtree(merged_dir, ignore_errors=True)
        return None

    if proc.returncode != 0 or not fp16_gguf.is_file():
        log.warning(
            "convert_hf_to_gguf failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        shutil.rmtree(merged_dir, ignore_errors=True)
        return None

    # 3. quantize fp16 -> target quant
    quant_cmd = [str(quantize_bin), str(fp16_gguf), str(final_gguf), quant]
    log.info("quantizing GGUF: %s", " ".join(quant_cmd))
    try:
        proc = subprocess.run(
            quant_cmd,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("llama-quantize crashed: %s", e)
        shutil.rmtree(merged_dir, ignore_errors=True)
        fp16_gguf.unlink(missing_ok=True)
        return None

    if proc.returncode != 0 or not final_gguf.is_file():
        log.warning(
            "llama-quantize failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        shutil.rmtree(merged_dir, ignore_errors=True)
        fp16_gguf.unlink(missing_ok=True)
        return None

    log.info(
        "merged model exported to %s (%.1f MB)",
        final_gguf, final_gguf.stat().st_size / (1024 * 1024),
    )

    # 4. optionally drop into LM Studio's models dir. LM Studio's layout is
    # <models_dir>/<publisher>/<repo>/<file>.gguf — putting it in a
    # per-adapter subdir keeps the picker tidy and avoids name collisions.
    if lm_studio_models_dir:
        target_root = Path(lm_studio_models_dir).expanduser()
        target_dir = target_root / "local-finetune" / adapter_dir.name
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / f"{adapter_dir.name}.{quant.lower()}.gguf"
            shutil.copy2(final_gguf, target_file)
            log.info("copied merged model to LM Studio dir: %s", target_file)
        except OSError as e:
            log.warning("failed to copy merged model into LM_STUDIO_MODELS_DIR: %s", e)

    # 5. clean up build artifacts — the final GGUF is all we need
    shutil.rmtree(merged_dir, ignore_errors=True)
    fp16_gguf.unlink(missing_ok=True)

    return final_gguf
