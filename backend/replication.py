from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import ROOT_DIR, settings
from .database import (
    ReplicationRun,
    SessionLocal,
    get_setting,
    set_setting,
)
from .event_bus import replication_bus

log = logging.getLogger(__name__)


SETTING_ENABLED = "replication_enabled"
SETTING_INTERVAL = "replication_interval_minutes"
SETTING_TARGET_DIR = "replication_target_dir"
SETTING_RETENTION = "replication_retention"
SETTING_DELETE_PROTECTION = "replication_delete_protection"
SETTING_LAST_RUN = "replication_last_run"

DEFAULT_INTERVAL_MINUTES = 60
DEFAULT_RETENTION = 10
DEFAULT_TARGET_SUBDIR = "replicas"


def _iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ReplicationSettings:
    enabled: bool
    interval_minutes: int
    target_dir: str
    retention: int
    delete_protection: bool


def _default_target_dir() -> Path:
    return (ROOT_DIR / DEFAULT_TARGET_SUBDIR).resolve()


def _resolve_target_dir(raw: str) -> Path:
    raw = (raw or "").strip()
    if not raw:
        return _default_target_dir()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path.resolve()


def _source_db_path() -> Path:
    raw = Path(settings.db_path)
    if not raw.is_absolute():
        raw = ROOT_DIR / raw
    return raw.resolve()


async def load_settings(session: AsyncSession) -> ReplicationSettings:
    enabled_raw = await get_setting(session, SETTING_ENABLED, "0")
    interval_raw = await get_setting(
        session, SETTING_INTERVAL, str(DEFAULT_INTERVAL_MINUTES)
    )
    target_raw = await get_setting(session, SETTING_TARGET_DIR, "")
    retention_raw = await get_setting(
        session, SETTING_RETENTION, str(DEFAULT_RETENTION)
    )
    protection_raw = await get_setting(session, SETTING_DELETE_PROTECTION, "1")

    try:
        interval = max(1, int(interval_raw))
    except ValueError:
        interval = DEFAULT_INTERVAL_MINUTES
    try:
        retention = max(1, int(retention_raw))
    except ValueError:
        retention = DEFAULT_RETENTION

    return ReplicationSettings(
        enabled=enabled_raw in ("1", "true", "True"),
        interval_minutes=interval,
        target_dir=str(_resolve_target_dir(target_raw)),
        retention=retention,
        delete_protection=protection_raw in ("1", "true", "True"),
    )


async def save_settings(
    session: AsyncSession,
    *,
    enabled: Optional[bool] = None,
    interval_minutes: Optional[int] = None,
    target_dir: Optional[str] = None,
    retention: Optional[int] = None,
    delete_protection: Optional[bool] = None,
) -> ReplicationSettings:
    if enabled is not None:
        await set_setting(session, SETTING_ENABLED, "1" if enabled else "0")
    if interval_minutes is not None:
        await set_setting(
            session, SETTING_INTERVAL, str(max(1, int(interval_minutes)))
        )
    if target_dir is not None:
        await set_setting(session, SETTING_TARGET_DIR, target_dir.strip())
    if retention is not None:
        await set_setting(session, SETTING_RETENTION, str(max(1, int(retention))))
    if delete_protection is not None:
        await set_setting(
            session, SETTING_DELETE_PROTECTION, "1" if delete_protection else "0"
        )
    return await load_settings(session)


class ReplicationState:
    def __init__(self) -> None:
        self.running: bool = False
        self.cancelled: bool = False
        self.current_run_id: Optional[int] = None
        self.last_event: dict = {}

    def reset(self) -> None:
        self.running = False
        self.cancelled = False
        self.current_run_id = None


replication_state = ReplicationState()


async def _emit(event: dict) -> None:
    replication_state.last_event = event
    await replication_bus.publish("replication", event)


def _format_target_file(target_dir: Path, started: datetime) -> Path:
    stamp = started.strftime("%Y%m%d_%H%M%S")
    return target_dir / f"database_{stamp}.db"


def _sqlite_backup(
    src_path: Path,
    dst_path: Path,
    progress_cb,
    cancel_cb,
) -> tuple[int, int]:
    """Copy SQLite DB using its online backup API.

    Returns (source_bytes, copied_bytes).
    """
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dst_path))
    try:
        total_pages = 0
        copied_pages = 0
        page_size = 4096

        # Pull initial page-size from the source to compute byte progress.
        try:
            page_size = src.execute("PRAGMA page_size").fetchone()[0] or 4096
        except sqlite3.DatabaseError:
            pass

        def _progress(status, remaining, page_count):
            nonlocal total_pages, copied_pages
            total_pages = page_count or total_pages
            copied_pages = max(0, (page_count or 0) - (remaining or 0))
            try:
                progress_cb(copied_pages, total_pages, page_size)
            except Exception:  # noqa: BLE001
                log.exception("replication progress callback failed")
            if cancel_cb():
                raise _ReplicationCancelled()

        src.backup(dst, pages=64, progress=_progress)
        copied_bytes = copied_pages * page_size
        source_bytes = total_pages * page_size
        return source_bytes, copied_bytes
    finally:
        with _suppress():
            dst.close()
        with _suppress():
            src.close()


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True


class _ReplicationCancelled(Exception):
    pass


def _prune_old_replicas(target_dir: Path, keep: int) -> int:
    files = sorted(
        (p for p in target_dir.glob("database_*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for path in files[keep:]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            log.exception("failed to prune replica %s", path)
    return removed


async def run_replication(trigger: str = "manual") -> dict:
    if replication_state.running:
        return {"started": False, "reason": "уже выполняется"}

    async with SessionLocal() as session:
        cfg = await load_settings(session)

    source = _source_db_path()
    if not source.exists():
        return {"started": False, "reason": f"исходная БД не найдена: {source}"}

    target_dir = _resolve_target_dir(cfg.target_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"started": False, "reason": f"нельзя создать папку: {e}"}

    started = datetime.utcnow()
    target_file = _format_target_file(target_dir, started)

    async with SessionLocal() as session:
        run = ReplicationRun(
            started_at=started,
            status="running",
            trigger=trigger,
            target_path=str(target_file),
            protected=cfg.delete_protection,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    replication_state.running = True
    replication_state.cancelled = False
    replication_state.current_run_id = run_id

    asyncio.create_task(_run_one(run_id, source, target_file, cfg, trigger))
    return {"started": True, "run_id": run_id, "target": str(target_file)}


async def _run_one(
    run_id: int,
    source: Path,
    target_file: Path,
    cfg: ReplicationSettings,
    trigger: str,
) -> None:
    loop = asyncio.get_running_loop()
    started_ts = time.monotonic()

    await _emit({
        "phase": "starting",
        "run_id": run_id,
        "trigger": trigger,
        "source": str(source),
        "target": str(target_file),
    })

    def progress_cb(copied_pages: int, total_pages: int, page_size: int) -> None:
        evt = {
            "phase": "copying",
            "run_id": run_id,
            "copied_pages": copied_pages,
            "total_pages": total_pages,
            "page_size": page_size,
            "copied_bytes": copied_pages * page_size,
            "total_bytes": total_pages * page_size,
            "percent": (
                (copied_pages / total_pages * 100.0) if total_pages else 0.0
            ),
        }
        asyncio.run_coroutine_threadsafe(_emit(evt), loop)

    def cancel_cb() -> bool:
        return replication_state.cancelled

    error_message: Optional[str] = None
    source_bytes = 0
    copied_bytes = 0
    pruned = 0

    try:
        source_bytes, copied_bytes = await asyncio.to_thread(
            _sqlite_backup, source, target_file, progress_cb, cancel_cb
        )
        if replication_state.cancelled:
            raise _ReplicationCancelled()
        pruned = _prune_old_replicas(target_file.parent, cfg.retention)
    except _ReplicationCancelled:
        error_message = "отменено пользователем"
        with _suppress():
            target_file.unlink()
    except Exception as e:  # noqa: BLE001
        log.exception("replication failed")
        error_message = str(e) or e.__class__.__name__
        with _suppress():
            target_file.unlink()

    duration_ms = int((time.monotonic() - started_ts) * 1000)
    finished = datetime.utcnow()
    final_phase = (
        "cancelled"
        if error_message == "отменено пользователем"
        else "error"
        if error_message
        else "done"
    )

    async with SessionLocal() as session:
        res = await session.execute(
            select(ReplicationRun).where(ReplicationRun.id == run_id)
        )
        run = res.scalar_one_or_none()
        if run is not None:
            run.finished_at = finished
            run.status = final_phase
            run.source_bytes = source_bytes
            run.copied_bytes = copied_bytes
            run.duration_ms = duration_ms
            run.error = error_message
            await session.commit()
        await set_setting(session, SETTING_LAST_RUN, finished.isoformat())

    await _emit({
        "phase": final_phase,
        "run_id": run_id,
        "target": str(target_file) if final_phase == "done" else None,
        "source_bytes": source_bytes,
        "copied_bytes": copied_bytes,
        "duration_ms": duration_ms,
        "pruned": pruned,
        "error": error_message,
    })

    replication_state.reset()


async def cancel_replication() -> bool:
    if not replication_state.running:
        return False
    replication_state.cancelled = True
    return True


def _is_safe_replica_path(path: Path, target_dir: Path) -> bool:
    try:
        path.resolve().relative_to(target_dir.resolve())
        return True
    except ValueError:
        return False


def _read_text_tail(path: Path, max_bytes: int = 256_000) -> str:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        data = f.read()
    return data.decode("utf-8", errors="replace")


def _candidate_app_logs(around: Optional[datetime]) -> list[Path]:
    logs_dir = settings.logs_dir
    files = [p for p in logs_dir.rglob("*.log") if p.is_file()]
    if around is not None:
        ts = around.timestamp()
        files.sort(key=lambda p: (abs(p.stat().st_mtime - ts), -p.stat().st_mtime))
        return files[:20]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:20]


def _filter_replication_lines(
    text: str,
    started: Optional[datetime],
    finished: Optional[datetime],
    max_lines: int = 200,
) -> list[str]:
    keywords = ("replication", "replica", "backend.replication", "репликац")
    selected: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        lower = line.lower()
        if not any(k in lower for k in keywords):
            continue
        selected.append(line)
    return selected[-max_lines:]


async def get_run_log(run_id: int) -> dict:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReplicationRun).where(ReplicationRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return {"found": False, "error": "запись не найдена", "excerpt": "", "log_path": None}

    summary_lines: list[str] = []
    summary_lines.append(
        f"Запуск #{run.id} — {run.status} ({run.trigger})"
    )
    summary_lines.append(
        f"Начало: {_iso_utc(run.started_at) or '—'}    "
        f"Конец:  {_iso_utc(run.finished_at) or '—'}    "
        f"Длительность: {run.duration_ms} мс"
    )
    summary_lines.append(
        f"Файл-реплика: {run.target_path or '—'}"
    )
    summary_lines.append(
        f"Размер источника: {run.source_bytes} Б    "
        f"Скопировано: {run.copied_bytes} Б"
    )
    if run.error:
        summary_lines.append(f"Ошибка: {run.error}")
    summary_lines.append("")

    log_path: Optional[Path] = None
    excerpt_lines: list[str] = []
    for path in _candidate_app_logs(run.started_at):
        try:
            text = _read_text_tail(path)
        except OSError:
            continue
        lines = _filter_replication_lines(text, run.started_at, run.finished_at)
        if lines:
            log_path = path
            excerpt_lines = lines
            break

    summary = run.error or (
        "репликация выполнена успешно" if run.status == "done" else run.status
    )
    body = "\n".join(summary_lines)
    if excerpt_lines:
        body += "--- app log ---\n" + "\n".join(excerpt_lines)
    else:
        body += "(подробные записи в app-логе не найдены)"

    return {
        "found": True,
        "error": summary,
        "excerpt": body,
        "log_path": str(log_path) if log_path else None,
    }


async def list_runs(limit: int = 50) -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReplicationRun)
            .order_by(desc(ReplicationRun.id))
            .limit(limit)
        )
        return [_run_to_dict(r) for r in result.scalars().all()]


def _run_to_dict(r: ReplicationRun) -> dict:
    return {
        "id": r.id,
        "started_at": _iso_utc(r.started_at),
        "finished_at": _iso_utc(r.finished_at),
        "status": r.status,
        "trigger": r.trigger,
        "target_path": r.target_path,
        "source_bytes": r.source_bytes,
        "copied_bytes": r.copied_bytes,
        "duration_ms": r.duration_ms,
        "error": r.error,
        "protected": r.protected,
        "exists": _replica_exists(r.target_path),
    }


def _replica_exists(target: str) -> bool:
    if not target:
        return False
    try:
        return Path(target).is_file()
    except OSError:
        return False


async def delete_run(run_id: int, confirm: bool) -> tuple[bool, str]:
    async with SessionLocal() as session:
        cfg = await load_settings(session)
        result = await session.execute(
            select(ReplicationRun).where(ReplicationRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return False, "запись не найдена"
        if replication_state.current_run_id == run_id and replication_state.running:
            return False, "идёт репликация — отмените сначала"

        if cfg.delete_protection and run.protected and not confirm:
            return False, "включена защита: подтвердите удаление"

        target = Path(run.target_path) if run.target_path else None
        target_root = _resolve_target_dir(cfg.target_dir)
        await session.delete(run)
        await session.commit()

    if target and target.exists() and _is_safe_replica_path(target, target_root):
        with _suppress():
            target.unlink()
    return True, "удалено"


class ReplicationScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._wake_event: Optional[asyncio.Event] = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="db-replication")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._wake_event:
            self._wake_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    def trigger(self) -> None:
        if self._wake_event:
            self._wake_event.set()

    async def _loop(self) -> None:
        log.info("replication scheduler started")
        assert self._stop_event is not None
        assert self._wake_event is not None
        while not self._stop_event.is_set():
            async with SessionLocal() as session:
                cfg = await load_settings(session)

            if cfg.enabled and not replication_state.running:
                try:
                    await run_replication(trigger="schedule")
                except Exception:  # noqa: BLE001
                    log.exception("scheduled replication failed to start")

            sleep_seconds = max(60, cfg.interval_minutes * 60)
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=sleep_seconds
                )
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()
        log.info("replication scheduler stopped")


scheduler = ReplicationScheduler()


async def status() -> dict:
    async with SessionLocal() as session:
        cfg = await load_settings(session)
        last_run = await get_setting(session, SETTING_LAST_RUN, "")

    last_run_iso: Optional[str] = None
    if last_run:
        try:
            last_run_iso = _iso_utc(datetime.fromisoformat(last_run))
        except ValueError:
            last_run_iso = last_run

    source = _source_db_path()
    source_size = source.stat().st_size if source.exists() else 0

    runs = await list_runs(limit=1)
    last = runs[0] if runs else None

    return {
        "settings": {
            "enabled": cfg.enabled,
            "interval_minutes": cfg.interval_minutes,
            "target_dir": cfg.target_dir,
            "retention": cfg.retention,
            "delete_protection": cfg.delete_protection,
        },
        "source_path": str(source),
        "source_bytes": source_size,
        "running": replication_state.running,
        "current_run_id": replication_state.current_run_id,
        "last_run_at": last_run_iso,
        "last_run": last,
    }
