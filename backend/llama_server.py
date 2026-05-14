"""Manage a llama-server.exe subprocess that serves the active fine-tuned
model + LoRA adapter over an OpenAI-compatible HTTP API on
http://127.0.0.1:<port>/v1.

The backend reaches this process the same way it used to reach LM Studio
(via OPENAI_BASE_URL). The lifecycle is owned by the backend so the user
does not have to launch llama-server.exe manually, and so the server can
be restarted automatically when a different LoRA adapter is activated."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select

from .config import settings
from .database import SessionLocal, TrainingRun, get_setting, set_setting

SETTING_AUTO_RESUME = "llama_server_auto_resume"

log = logging.getLogger(__name__)


class LlamaServerState:
    def __init__(self) -> None:
        self.process: Optional[asyncio.subprocess.Process] = None
        self.cmd: list[str] = []
        self.active_lora: Optional[Path] = None
        self.starting: bool = False
        self.stopping: bool = False
        self.last_error: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None


server_state = LlamaServerState()


def _resolve_binary() -> Optional[Path]:
    if settings.llama_server_bin:
        p = Path(settings.llama_server_bin).expanduser()
        if p.is_file():
            return p
    if settings.llama_cpp_path:
        root = Path(settings.llama_cpp_path).expanduser()
        exe = ".exe" if os.name == "nt" else ""
        candidates = [
            root / f"llama-server{exe}",
            root / "build" / "bin" / f"llama-server{exe}",
            root / "build" / "bin" / "Release" / f"llama-server{exe}",
            root / "build" / "Release" / f"llama-server{exe}",
            root / "bin" / f"llama-server{exe}",
            root / "bin" / "Release" / f"llama-server{exe}",
        ]
        for c in candidates:
            if c.is_file():
                return c
        try:
            for found in root.rglob(f"llama-server{exe}"):
                if found.is_file():
                    return found
        except OSError:
            pass
    return None


async def _find_active_lora_path() -> Optional[Path]:
    async with SessionLocal() as session:
        res = await session.execute(
            select(TrainingRun).where(TrainingRun.is_active == True)  # noqa: E712
        )
        run = res.scalar_one_or_none()
    if run is None or not run.adapter_path:
        return None
    adapter_dir = Path(run.adapter_path)
    candidate = adapter_dir / f"{adapter_dir.name}.lora.f16.gguf"
    if candidate.is_file():
        return candidate
    log.warning(
        "active adapter run %s has no .lora.f16.gguf at %s — running base only",
        run.id, candidate,
    )
    return None


def _build_cmd(binary: Path, base_gguf: Path, lora: Optional[Path]) -> list[str]:
    cmd = [
        str(binary),
        "-m", str(base_gguf),
        "--host", "127.0.0.1",
        "--port", str(settings.llama_server_port),
        "-ngl", str(settings.llama_server_ngl),
        "-c", str(settings.llama_server_ctx),
    ]
    if lora is not None:
        cmd += ["--lora", str(lora)]
    return cmd


async def _wait_for_health(port: int, timeout: float = 120.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    async with httpx.AsyncClient(timeout=2.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            if server_state.process is not None and server_state.process.returncode is not None:
                return False
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return True
            except (httpx.RequestError, ValueError):
                pass
            await asyncio.sleep(1.0)
    return False


async def _drain_output(proc: asyncio.subprocess.Process) -> None:
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line:
            log.info("[llama-server] %s", line)


async def _watch_exit(proc: asyncio.subprocess.Process) -> None:
    rc = await proc.wait()
    if proc is server_state.process:
        server_state.process = None
    if rc != 0 and not server_state.stopping:
        server_state.last_error = f"llama-server упал (код {rc})"
        log.warning(server_state.last_error)


async def start() -> dict:
    async with server_state._lock:
        if server_state.running:
            return {"started": False, "reason": "уже запущен"}

        binary = _resolve_binary()
        if binary is None:
            reason = (
                "не нашёл llama-server. Установи LLAMA_CPP_PATH в .env (или "
                "LLAMA_SERVER_BIN с прямым путём к llama-server.exe)."
            )
            server_state.last_error = reason
            return {"started": False, "reason": reason}

        if not settings.llama_base_model_gguf:
            reason = "не задан LLAMA_BASE_MODEL_GGUF в .env"
            server_state.last_error = reason
            return {"started": False, "reason": reason}

        base_gguf = Path(settings.llama_base_model_gguf).expanduser()
        if not base_gguf.is_file():
            reason = f"базовая модель не найдена: {base_gguf}"
            server_state.last_error = reason
            return {"started": False, "reason": reason}

        lora = await _find_active_lora_path()
        cmd = _build_cmd(binary, base_gguf, lora)

        server_state.starting = True
        server_state.last_error = None
        server_state.active_lora = lora
        server_state.cmd = cmd
        log.info("starting llama-server: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as e:
            server_state.starting = False
            server_state.last_error = f"не удалось запустить llama-server: {e}"
            return {"started": False, "reason": server_state.last_error}

        server_state.process = proc
        asyncio.create_task(_drain_output(proc))
        asyncio.create_task(_watch_exit(proc))

    ok = await _wait_for_health(settings.llama_server_port, timeout=120.0)
    server_state.starting = False

    if not ok:
        server_state.last_error = (
            "llama-server не ответил /health за 120 секунд (модель не "
            "загрузилась, см. логи backend)"
        )
        await stop()
        return {"started": False, "reason": server_state.last_error}

    log.info(
        "llama-server ready on :%s lora=%s",
        settings.llama_server_port, lora,
    )
    return {
        "started": True,
        "port": settings.llama_server_port,
        "lora": str(lora) if lora else None,
    }


async def stop() -> dict:
    proc = server_state.process
    if proc is None or proc.returncode is not None:
        server_state.process = None
        return {"stopped": False, "reason": "не запущен"}

    server_state.stopping = True
    try:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        server_state.process = None
        return {"stopped": True}
    finally:
        server_state.stopping = False


async def restart() -> dict:
    if server_state.running:
        await stop()
    return await start()


async def restart_in_background() -> None:
    """Fire-and-forget restart used when an adapter is activated/deactivated."""
    async def _go() -> None:
        try:
            await restart()
        except Exception:
            log.exception("background llama-server restart failed")
    asyncio.create_task(_go())


async def get_auto_resume() -> bool:
    """User-toggleable: after a training run or merge job finishes, should
    we restart llama-server automatically (if it was running before)?"""
    async with SessionLocal() as session:
        raw = await get_setting(session, SETTING_AUTO_RESUME, "1")
    return raw.strip() not in {"0", "false", "False", ""}


async def set_auto_resume(enabled: bool) -> None:
    async with SessionLocal() as session:
        await set_setting(session, SETTING_AUTO_RESUME, "1" if enabled else "0")


async def status_async() -> dict:
    return {
        "running": server_state.running,
        "starting": server_state.starting,
        "stopping": server_state.stopping,
        "port": settings.llama_server_port,
        "base_model": settings.llama_base_model_gguf,
        "lora_path": str(server_state.active_lora) if server_state.active_lora else None,
        "last_error": server_state.last_error,
        "configured": bool(settings.llama_base_model_gguf),
        "auto_start": settings.llama_server_auto_start,
        "auto_resume": await get_auto_resume(),
    }
