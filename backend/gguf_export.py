"""Convert a PEFT LoRA adapter to GGUF for llama.cpp / LM Studio.

LM Studio runs llama.cpp and only accepts adapters in GGUF. The trainer
produces PEFT (safetensors). llama.cpp ships convert_lora_to_gguf.py which
takes the adapter dir and writes a single .gguf file usable as a LoRA in
LM Studio's "Configuration → Load adapter" UI.

This module just shells out to that script with the right args. It's a
best-effort step run after training succeeds: if llama.cpp isn't
configured we log and skip — training itself is not affected.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


def _find_convert_script(llama_cpp_path: Path) -> Optional[Path]:
    candidates = [
        llama_cpp_path / "convert_lora_to_gguf.py",
        llama_cpp_path / "convert-lora-to-gguf.py",  # older name
        llama_cpp_path / "scripts" / "convert_lora_to_gguf.py",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def convert_adapter_to_gguf(
    adapter_dir: Path,
    llama_cpp_path: Optional[str],
    lm_studio_adapters_dir: Optional[str],
) -> Optional[Path]:
    """Run llama.cpp's convert_lora_to_gguf.py against `adapter_dir`.

    Returns the path to the resulting .gguf file on success, or None when
    conversion was skipped or failed (already logged).
    """
    if not llama_cpp_path:
        log.info(
            "LLAMA_CPP_PATH not set; skipping GGUF conversion. "
            "Clone llama.cpp and set the path in .env to enable auto-export."
        )
        return None

    llama_root = Path(llama_cpp_path).expanduser()
    if not llama_root.is_dir():
        log.warning("LLAMA_CPP_PATH=%s does not exist; skipping GGUF conversion.", llama_root)
        return None

    script = _find_convert_script(llama_root)
    if script is None:
        log.warning(
            "convert_lora_to_gguf.py not found under %s; skipping GGUF conversion. "
            "Make sure the llama.cpp checkout is up to date.",
            llama_root,
        )
        return None

    out_file = adapter_dir / "adapter.gguf"
    cmd = [sys.executable, str(script), str(adapter_dir), "--outfile", str(out_file)]
    log.info("converting LoRA to GGUF: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(llama_root),
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("GGUF conversion crashed: %s", e)
        return None

    if proc.returncode != 0:
        log.warning(
            "GGUF conversion failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        return None

    if not out_file.is_file():
        log.warning("GGUF conversion reported success but %s is missing.", out_file)
        return None

    log.info("LoRA adapter exported to %s (%.1f MB)",
             out_file, out_file.stat().st_size / (1024 * 1024))

    # Optional: also drop a copy into LM Studio's adapters dir so the user
    # doesn't have to navigate the file picker each time.
    if lm_studio_adapters_dir:
        target_dir = Path(lm_studio_adapters_dir).expanduser()
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file = target_dir / f"{adapter_dir.name}.gguf"
            shutil.copy2(out_file, target_file)
            log.info("copied adapter to LM Studio dir: %s", target_file)
        except OSError as e:
            log.warning("failed to copy adapter into LM_STUDIO_ADAPTERS_DIR: %s", e)

    return out_file
