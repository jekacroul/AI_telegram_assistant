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

import json
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional


EventCallback = Callable[[dict], None]


def _noop_event(_event: dict) -> None:
    return None

log = logging.getLogger(__name__)

CONVERT_SCRIPT_URL = (
    "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/convert_hf_to_gguf.py"
)
LORA_CONVERT_SCRIPT_URL = (
    "https://raw.githubusercontent.com/ggml-org/llama.cpp/master/convert_lora_to_gguf.py"
)


def _find_convert_script(llama_cpp_path: Path) -> Optional[Path]:
    names = ("convert_hf_to_gguf.py", "convert-hf-to-gguf.py")
    for name in names:
        direct = llama_cpp_path / name
        if direct.is_file():
            return direct
    for name in names:
        try:
            for found in llama_cpp_path.rglob(name):
                if found.is_file():
                    return found
        except OSError:
            continue
    return None


def _ensure_convert_script(llama_cpp_path: Path) -> tuple[Optional[Path], Optional[str]]:
    """Locate convert_hf_to_gguf.py, downloading it from llama.cpp upstream
    when the user only has prebuilt binaries. The fetched copy is dropped
    into llama_cpp_path so future runs reuse it without re-downloading."""
    found = _find_convert_script(llama_cpp_path)
    if found is not None:
        return found, None

    target = llama_cpp_path / "convert_hf_to_gguf.py"
    try:
        log.info("convert_hf_to_gguf.py not found, fetching from %s", CONVERT_SCRIPT_URL)
        req = urllib.request.Request(
            CONVERT_SCRIPT_URL, headers={"User-Agent": "ai-telegram-assistant"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        target.write_bytes(data)
    except (urllib.error.URLError, OSError) as e:
        return None, (
            f"convert_hf_to_gguf.py не найден в {llama_cpp_path} и не получилось "
            f"скачать его с github ({e}). Скачай вручную: {CONVERT_SCRIPT_URL} "
            f"и положи в {llama_cpp_path}."
        )

    # The script needs the `gguf` python package. Check now so the user
    # gets a precise message instead of a stack trace from the subprocess.
    try:
        import gguf  # noqa: F401
    except ImportError:
        return None, (
            "convert_hf_to_gguf.py скачан, но в окружении python нет пакета `gguf`. "
            "Установи его: pip install gguf"
        )

    return target, None


def _ensure_lora_convert_script(llama_cpp_path: Path) -> tuple[Optional[Path], Optional[str]]:
    """Locate convert_lora_to_gguf.py, downloading it from upstream if missing."""
    direct = llama_cpp_path / "convert_lora_to_gguf.py"
    if direct.is_file():
        return direct, None
    try:
        for found in llama_cpp_path.rglob("convert_lora_to_gguf.py"):
            if found.is_file():
                return found, None
    except OSError:
        pass

    target = llama_cpp_path / "convert_lora_to_gguf.py"
    try:
        log.info("convert_lora_to_gguf.py not found, fetching from %s", LORA_CONVERT_SCRIPT_URL)
        req = urllib.request.Request(
            LORA_CONVERT_SCRIPT_URL, headers={"User-Agent": "ai-telegram-assistant"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        target.write_bytes(data)
    except (urllib.error.URLError, OSError) as e:
        return None, (
            f"convert_lora_to_gguf.py не найден в {llama_cpp_path} и не получилось "
            f"скачать его с github ({e}). Скачай вручную: {LORA_CONVERT_SCRIPT_URL} "
            f"и положи в {llama_cpp_path}."
        )

    try:
        import gguf  # noqa: F401
    except ImportError:
        return None, (
            "convert_lora_to_gguf.py скачан, но в окружении python нет пакета `gguf`. "
            "Установи его: pip install gguf"
        )
    return target, None


def export_lora_only_gguf(
    adapter_dir: Path,
    base_model: str,
    llama_cpp_path: Optional[str],
    on_event: EventCallback = _noop_event,
) -> tuple[Optional[Path], Optional[str]]:
    """Convert a PEFT LoRA adapter into a standalone .gguf LoRA file.

    Unlike merge_and_export_gguf, this never loads the fp16 base — it just
    repacks the LoRA deltas (tens of MB) into GGUF format. Loaded on top
    of a base GGUF in LM Studio.
    """
    if not llama_cpp_path:
        reason = "LLAMA_CPP_PATH не задан в .env."
        log.info(reason)
        return None, reason

    llama_root = Path(llama_cpp_path).expanduser()
    if not llama_root.is_dir():
        reason = f"путь LLAMA_CPP_PATH={llama_root} не существует."
        log.warning(reason)
        return None, reason

    script, script_reason = _ensure_lora_convert_script(llama_root)
    if script is None:
        return None, script_reason

    on_event({"phase": "converting_lora_to_gguf"})
    out_gguf = adapter_dir / f"{adapter_dir.name}.lora.f16.gguf"
    cmd = [
        sys.executable, str(script), str(adapter_dir),
        "--outfile", str(out_gguf),
        "--outtype", "f16",
        "--base-model-id", base_model,
    ]
    log.info("converting LoRA adapter to GGUF: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(llama_root), timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"convert_lora_to_gguf не запустился: {e}"

    if proc.returncode != 0 or not out_gguf.is_file():
        log.error(
            "convert_lora_to_gguf failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        return None, (
            f"convert_lora_to_gguf завершился с кодом {proc.returncode} "
            f"(см. логи backend)"
        )

    log.info(
        "LoRA exported to %s (%.1f MB)",
        out_gguf, out_gguf.stat().st_size / (1024 * 1024),
    )
    return out_gguf, None


def _find_quantize_binary(llama_cpp_path: Path) -> Optional[Path]:
    exe = ".exe" if os.name == "nt" else ""
    names = (f"llama-quantize{exe}", f"quantize{exe}")
    # Common cmake/MSBuild output layouts — keep them as fast direct hits.
    candidates: list[Path] = []
    for name in names:
        candidates += [
            llama_cpp_path / name,
            llama_cpp_path / "build" / name,
            llama_cpp_path / "build" / "bin" / name,
            llama_cpp_path / "build" / "bin" / "Release" / name,
            llama_cpp_path / "build" / "Release" / name,
            llama_cpp_path / "bin" / name,
            llama_cpp_path / "bin" / "Release" / name,
        ]
    for p in candidates:
        if p.is_file():
            return p
    # Fall back to a recursive scan: covers custom build dirs, prebuilt
    # archives extracted into nested folders, etc.
    for name in names:
        try:
            for found in llama_cpp_path.rglob(name):
                if found.is_file():
                    return found
        except OSError:
            continue
    return None


MERGE_EVENT_PREFIX = "__MERGE_EVENT__ "


def _run_merge_subprocess(
    adapter_dir: Path,
    base_model: str,
    merged_dir: Path,
    on_event: EventCallback,
) -> tuple[bool, Optional[str]]:
    """Run merge_worker as a subprocess. Returns (success, error_reason).
    Streams phase events to on_event and captures any explicit error
    message the worker emitted so callers can surface it to the user."""
    cmd = [
        sys.executable, "-u", "-m", "backend.merge_worker",
        "--adapter-dir", str(adapter_dir),
        "--base-model", base_model,
        "--output-dir", str(merged_dir),
    ]
    log.info("merging LoRA into base: %s", " ".join(cmd))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        log.error("merge subprocess failed to start: %s", e)
        return False, f"merge subprocess не запустился: {e}"

    captured: list[str] = []
    worker_error: Optional[str] = None
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\r\n")
        captured.append(line)
        idx = line.find(MERGE_EVENT_PREFIX)
        if idx >= 0:
            payload = line[idx + len(MERGE_EVENT_PREFIX):]
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            phase = event.get("phase")
            if phase == "error":
                worker_error = event.get("error") or worker_error
                continue
            if phase and phase != "result":
                on_event(event)
    rc = proc.wait()

    class _Result:
        returncode = rc
        stdout = "\n".join(captured)
        stderr = ""

    proc = _Result  # type: ignore[assignment]

    if proc.returncode != 0:
        log.error(
            "merge subprocess failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        if worker_error:
            if "_update_offload" in worker_error or "KeyError" in worker_error:
                return False, (
                    f"{worker_error}\n\nИзвестный баг PEFT с offload_index при "
                    f"диспатченной модели. Должен ловиться monkey-patch'ем в "
                    f"merge_worker — если ты видишь это сообщение, патч не "
                    f"применился. Сообщи разработчику."
                )
            return False, worker_error
        if proc.returncode in (3221225477, -1073741819):
            return False, (
                f"merge_worker завершился с access violation (код {proc.returncode}). "
                f"Скорее всего не хватило RAM/VRAM. Закрой LM Studio и другие "
                f"тяжёлые приложения, проверь свободную память и попробуй снова."
            )
        return False, f"merge_worker завершился с кодом {proc.returncode} (см. логи backend)"

    config_file = merged_dir / "config.json"
    if not config_file.is_file():
        log.error("merge reported success but %s is missing.", config_file)
        return False, "merge сообщил об успехе, но config.json не появился"
    return True, None


def merge_and_export_gguf(
    adapter_dir: Path,
    base_model: str,
    llama_cpp_path: Optional[str],
    quant: str = "Q8_0",
    on_event: EventCallback = _noop_event,
) -> tuple[Optional[Path], Optional[str]]:
    """Produce a single self-contained GGUF for LM Studio.

    Returns (path, reason). On success: (final_gguf_path, None). On skip or
    failure: (None, human readable reason in Russian). The reason is also
    written to logs so it stays inspectable from the server side.
    """
    if not llama_cpp_path:
        reason = (
            "LLAMA_CPP_PATH не задан в .env. Склонируй llama.cpp и пропиши "
            "путь, чтобы экспорт GGUF выполнялся автоматически."
        )
        log.info(reason)
        return None, reason

    llama_root = Path(llama_cpp_path).expanduser()
    if not llama_root.is_dir():
        reason = (
            f"путь LLAMA_CPP_PATH={llama_root} не существует. "
            f"Проверь .env (часто бывает опечатка: пробел vs подчёркивание)."
        )
        log.warning(reason)
        return None, reason

    convert_script, convert_reason = _ensure_convert_script(llama_root)
    if convert_script is None:
        log.warning(convert_reason)
        return None, convert_reason

    quantize_bin = _find_quantize_binary(llama_root)
    if quantize_bin is None:
        exe = ".exe" if os.name == "nt" else ""
        reason = (
            f"llama-quantize{exe} не найден нигде внутри {llama_root}. "
            f"Собери llama.cpp (cmake -B build && cmake --build build --config Release) "
            f"или скачай готовые бинарники с github.com/ggerganov/llama.cpp/releases "
            f"и распакуй их в эту папку."
        )
        log.warning(reason)
        return None, reason

    merged_dir = adapter_dir / "merged"
    fp16_gguf = adapter_dir / "merged.f16.gguf"
    final_gguf = adapter_dir / f"merged.{quant.lower()}.gguf"

    # 1. merge weights via isolated subprocess
    merge_ok, merge_err = _run_merge_subprocess(
        adapter_dir, base_model, merged_dir, on_event
    )
    if not merge_ok:
        return None, merge_err or "не удалось слить LoRA с базовой моделью (см. логи backend)"

    # 2. HF merged dir -> fp16 GGUF
    on_event({"phase": "converting_to_gguf"})
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
        log.error("convert_hf_to_gguf crashed: %s", e)
        shutil.rmtree(merged_dir, ignore_errors=True)
        return None, f"convert_hf_to_gguf не запустился: {e}"

    if proc.returncode != 0 or not fp16_gguf.is_file():
        log.error(
            "convert_hf_to_gguf failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        shutil.rmtree(merged_dir, ignore_errors=True)
        return None, f"convert_hf_to_gguf завершился с кодом {proc.returncode} (см. логи backend)"

    # 3. quantize fp16 -> target quant
    on_event({"phase": "quantizing", "quant": quant})
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
        log.error("llama-quantize crashed: %s", e)
        shutil.rmtree(merged_dir, ignore_errors=True)
        fp16_gguf.unlink(missing_ok=True)
        return None, f"llama-quantize не запустился: {e}"

    if proc.returncode != 0 or not final_gguf.is_file():
        log.error(
            "llama-quantize failed (exit %s):\nstdout: %s\nstderr: %s",
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:],
        )
        shutil.rmtree(merged_dir, ignore_errors=True)
        fp16_gguf.unlink(missing_ok=True)
        return None, f"llama-quantize завершился с кодом {proc.returncode} (см. логи backend)"

    log.info(
        "merged model exported to %s (%.1f MB)",
        final_gguf, final_gguf.stat().st_size / (1024 * 1024),
    )

    # Clean up build artifacts — the final GGUF is all we need.
    shutil.rmtree(merged_dir, ignore_errors=True)
    fp16_gguf.unlink(missing_ok=True)

    return final_gguf, None
