"""SQLite database replication.

Copies the live SQLite database to a configured destination using the native
SQLite Online Backup API (sqlite3.Connection.backup), which is safe to run
while the database is in use. Progress is emitted as events to the
replication_bus and the latest event is mirrored in `replication_state` for
new SSE subscribers.

History of runs is persisted in the `replication_runs` table. Runs are
created with `protected=True` by default — clients must explicitly disable
protection (or pass a confirmation token) before they can be deleted, so a
stray DELETE cannot wipe out backup history.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import ROOT_DIR, settings
from .database import (
    ReplicationRun,
    SessionLocal,
    get_setting,
    set_setting,
)
from .event_bus import EventBus


log = logging.getLogger(__name__)

replication_bus = EventBus()


SETTING_ENABLED = "replication_enabled"
SETTING_DESTINATION = "replication_destination"
SETTING_INTERVAL_MINUTES = "replication_interval_minutes"
SETTING_KEEP = "replication_keep_last"
SETTING_LAST_RUN = "replication_last_run"

DEFAULT_INTERVAL_MINUTES = 24 * 60
DEFAULT_KEEP_LAST = 10
MIN_INTERVAL_MINUTES = 5
MAX_KEEP_LAST = 200

# Token clients must echo back to defeat the deletion protection on
# individual rows. Kept simple — the goal is to make accidental DELETE
# impossible, not to defend against a malicious caller (the API has no
# auth boundary either way).
DELETE_CONFIRM_TOKEN = "CONFIRM-DELETE"


@dataclass
class ReplicationSettings:
    enabled: bool
    destination: str
    interval_minutes: int
    keep_last: int


def _default_destination() -> str:
    return str(ROOT_DIR / "backups" / "replicas")


async def get_replication_settings(session: AsyncSession) -> ReplicationSettings:
    enabled_raw = await get_setting(session, SETTING_ENABLED, "0")
    destination = await get_setting(session, SETTING_DESTINATION, "")
    interval_raw = await get_setting(
        session, SETTING_INTERVAL_MINUTES, str(DEFAULT_INTERVAL_MINUTES)
    )
    keep_raw = await get_setting(session, SETTING_KEEP, str(DEFAULT_KEEP_LAST))

    try:
        interval = max(MIN_INTERVAL_MINUTES, int(interval_raw))
    except ValueError:
        interval = DEFAULT_INTERVAL_MINUTES
    try:
        keep = max(1, min(MAX_KEEP_LAST, int(keep_raw)))
    except ValueError:
        keep = DEFAULT_KEEP_LAST

    return ReplicationSettings(
        enabled=enabled_raw in ("1", "true", "True"),
        destination=destination or _default_destination(),
        interval_minutes=interval,
        keep_last=keep,
    )


async def save_replication_settings(
    session: AsyncSession,
    *,
    enabled: Optional[bool] = None,
    destination: Optional[str] = None,
    interval_minutes: Optional[int] = None,
    keep_last: Optional[int] = None,
) -> ReplicationSettings:
    if enabled is not None:
        await set_setting(session, SETTING_ENABLED, "1" if enabled else "0")
    if destination is not None:
        await set_setting(session, SETTING_DESTINATION, destination.strip())
    if interval_minutes is not None:
        value = max(MIN_INTERVAL_MINUTES, int(interval_minutes))
        await set_setting(session, SETTING_INTERVAL_MINUTES, str(value))
    if keep_last is not None:
        value = max(1, min(MAX_KEEP_LAST, int(keep_last)))
        await set_setting(session, SETTING_KEEP, str(value))
    return await get_replication_settings(session)


def _source_db_path() -> Path:
    raw = settings.db_path
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _resolve_destination(destination: str) -> Path:
    path = Path(destination) if destination else Path(_default_destination())
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


class ReplicationState:
    def __init__(self) -> None:
        self.running: bool = False
        self.cancel_requested: bool = False
        self.current_run_id: Optional[int] = None
        self.last_event: dict = {}

    def reset(self) -> None:
        self.running = False
        self.cancel_requested = False
        self.current_run_id = None


replication_state = ReplicationState()


async def _emit(event: dict) -> None:
    replication_state.last_event = event
    await replication_bus.publish("replication", event)


class _CancelledError(RuntimeError):
    pass


def _do_backup(
    source: Path,
    destination_file: Path,
    on_progress,
    is_cancelled,
) -> tuple[int, int]:
    """Run SQLite backup in a thread. Returns (bytes_copied, bytes_total)."""
    destination_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = destination_file.with_suffix(destination_file.suffix + ".part")
    if tmp_file.exists():
        tmp_file.unlink()

    # ~1 MB chunks at SQLite's default 4 KiB page size.
    pages_per_chunk = 256
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(tmp_file))
        try:
            def _progress(status, remaining, total):
                if total > 0:
                    done = total - remaining
                    on_progress(done, total)
                if is_cancelled():
                    raise _CancelledError()

            src.backup(dst, pages=pages_per_chunk, progress=_progress)
        finally:
            dst.close()
    finally:
        src.close()

    size = tmp_file.stat().st_size
    if destination_file.exists():
        destination_file.unlink()
    tmp_file.rename(destination_file)
    return size, size


def _rotation_targets(directory: Path, keep_last: int) -> list[Path]:
    if not directory.exists():
        return []
    files = sorted(
        (p for p in directory.glob("replica_*.db") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[keep_last:]


async def _rotate_old_files(directory: Path, keep_last: int) -> int:
    removed = 0
    for path in _rotation_targets(directory, keep_last):
        try:
            path.unlink()
            removed += 1
        except OSError:
            log.exception("failed to rotate replica %s", path)
    return removed


async def run_replication(triggered_by: str = "manual") -> dict:
    """Execute a replication pass. Returns a summary dict.

    Concurrency: only one replication may run at a time. A second caller
    while one is in flight gets `{"started": False, "reason": "..."}`.
    """
    if replication_state.running:
        return {"started": False, "reason": "already running"}

    source = _source_db_path()
    if not source.exists():
        return {"started": False, "reason": f"source DB not found: {source}"}

    async with SessionLocal() as session:
        cfg = await get_replication_settings(session)

    destination_dir = _resolve_destination(cfg.destination)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    destination_file = destination_dir / f"replica_{timestamp}.db"

    async with SessionLocal() as session:
        run = ReplicationRun(
            started_at=datetime.utcnow(),
            status="running",
            destination_path=str(destination_file),
            bytes_total=source.stat().st_size,
            triggered_by=triggered_by,
            protected=True,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    replication_state.running = True
    replication_state.cancel_requested = False
    replication_state.current_run_id = run_id

    await _emit({
        "phase": "starting",
        "run_id": run_id,
        "destination": str(destination_file),
        "bytes_total": source.stat().st_size,
    })

    start = time.monotonic()
    loop = asyncio.get_running_loop()
    last_emit = {"t": 0.0}

    def on_progress(done: int, total: int) -> None:
        now = time.monotonic()
        # Throttle to ~5 Hz to avoid flooding the SSE bus.
        if now - last_emit["t"] < 0.2 and done < total:
            return
        last_emit["t"] = now
        asyncio.run_coroutine_threadsafe(
            _emit({
                "phase": "copying",
                "run_id": run_id,
                "bytes_copied": int(done),
                "bytes_total": int(total),
                "progress": (done / total) if total else 0.0,
            }),
            loop,
        )

    def is_cancelled() -> bool:
        return replication_state.cancel_requested

    error_message: Optional[str] = None
    cancelled = False
    bytes_copied = 0
    bytes_total = source.stat().st_size

    try:
        bytes_copied, bytes_total = await asyncio.to_thread(
            _do_backup, source, destination_file, on_progress, is_cancelled
        )
    except _CancelledError:
        cancelled = True
        # Clean up partial output.
        for stray in (destination_file, destination_file.with_suffix(destination_file.suffix + ".part")):
            try:
                stray.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                log.exception("failed to remove partial replica %s", stray)
    except Exception as e:
        error_message = f"{type(e).__name__}: {e}"
        log.exception("replication failed")
        for stray in (destination_file, destination_file.with_suffix(destination_file.suffix + ".part")):
            try:
                stray.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    duration_ms = int((time.monotonic() - start) * 1000)
    finished = datetime.utcnow()

    if cancelled:
        status = "cancelled"
    elif error_message:
        status = "failed"
    else:
        status = "done"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ReplicationRun).where(ReplicationRun.id == run_id)
        )
        row = result.scalar_one()
        row.finished_at = finished
        row.status = status
        row.bytes_copied = bytes_copied
        row.bytes_total = bytes_total
        row.duration_ms = duration_ms
        row.error_message = error_message
        await session.commit()

        if status == "done":
            await set_setting(session, SETTING_LAST_RUN, finished.isoformat())

    rotated = 0
    if status == "done":
        rotated = await _rotate_old_files(destination_dir, cfg.keep_last)

    summary = {
        "phase": status,
        "run_id": run_id,
        "destination": str(destination_file) if status == "done" else None,
        "bytes_copied": bytes_copied,
        "bytes_total": bytes_total,
        "duration_ms": duration_ms,
        "error": error_message,
        "rotated": rotated,
    }
    await _emit(summary)
    replication_state.reset()
    return {"started": True, **summary}


async def cancel_replication() -> bool:
    if not replication_state.running:
        return False
    replication_state.cancel_requested = True
    return True


async def list_runs(limit: int = 50) -> list[dict]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReplicationRun)
            .order_by(desc(ReplicationRun.id))
            .limit(limit)
        )
        rows = list(result.scalars().all())
    return [_run_to_dict(r) for r in rows]


def _run_to_dict(run: ReplicationRun) -> dict:
    return {
        "id": run.id,
        "started_at": run.started_at.isoformat() + "Z" if run.started_at else None,
        "finished_at": run.finished_at.isoformat() + "Z" if run.finished_at else None,
        "status": run.status,
        "destination_path": run.destination_path,
        "bytes_copied": run.bytes_copied,
        "bytes_total": run.bytes_total,
        "duration_ms": run.duration_ms,
        "error_message": run.error_message,
        "triggered_by": run.triggered_by,
        "protected": run.protected,
    }


async def set_run_protected(run_id: int, protected: bool) -> Optional[dict]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(ReplicationRun).where(ReplicationRun.id == run_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        row.protected = bool(protected)
        await session.commit()
        return _run_to_dict(row)


async def delete_run(run_id: int, confirm_token: str) -> tuple[bool, str]:
    """Delete a run record and its replica file.

    Protected runs cannot be deleted. The caller must additionally pass the
    `DELETE_CONFIRM_TOKEN` value — this is the second guardrail against
    accidental deletes from refreshes / clicks.
    """
    if confirm_token != DELETE_CONFIRM_TOKEN:
        return False, "confirmation token required"

    async with SessionLocal() as session:
        result = await session.execute(
            select(ReplicationRun).where(ReplicationRun.id == run_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False, "not found"
        if row.protected:
            return False, "run is protected; disable protection first"
        if row.status == "running":
            return False, "cannot delete a running replication"

        # Refuse to delete the last successful replica — there must always
        # be at least one good backup on disk if any exist.
        if row.status == "done":
            successful = await session.execute(
                select(func.count(ReplicationRun.id)).where(
                    ReplicationRun.status == "done"
                )
            )
            count = successful.scalar() or 0
            if count <= 1:
                return False, "cannot delete the only successful replica"

        path_str = row.destination_path
        await session.delete(row)
        await session.commit()

    if path_str:
        path = Path(path_str)
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            log.exception("failed to remove replica file %s", path_str)

    return True, "deleted"


class ReplicationScheduler:
    """Periodic auto-replication. Mirrors DialogBackupScheduler shape."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="db-replication")

    async def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    def trigger(self) -> None:
        self._wake_event.set()

    async def _loop(self) -> None:
        log.info("replication scheduler started")
        # On startup, wait a short grace period so the app is warm.
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        self._wake_event.clear()

        while not self._stop_event.is_set():
            try:
                async with SessionLocal() as session:
                    cfg = await get_replication_settings(session)
            except Exception:  # noqa: BLE001
                cfg = ReplicationSettings(
                    enabled=False,
                    destination=_default_destination(),
                    interval_minutes=DEFAULT_INTERVAL_MINUTES,
                    keep_last=DEFAULT_KEEP_LAST,
                )

            if cfg.enabled and not replication_state.running:
                try:
                    await run_replication(triggered_by="scheduled")
                except Exception:  # noqa: BLE001
                    log.exception("scheduled replication failed")

            sleep_seconds = max(MIN_INTERVAL_MINUTES * 60, cfg.interval_minutes * 60)
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()

        log.info("replication scheduler stopped")


scheduler = ReplicationScheduler()
