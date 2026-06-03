from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock


class HourlyDailyFileHandler(logging.Handler):
    """Writes log records to <base_dir>/<YYYY-MM-DD>/<HH>.log.

    A new file is opened automatically when the local hour rolls over, so each
    day produces a folder with up to 24 hourly log files.
    """

    def __init__(self, base_dir: Path, encoding: str = "utf-8") -> None:
        super().__init__()
        self.base_dir = Path(base_dir)
        self.encoding = encoding
        self._stream = None
        self._current_key: tuple[int, int, int, int] | None = None
        self._lock = Lock()

    def _open_for(self, now: datetime):
        day_dir = self.base_dir / now.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        file_path = day_dir / f"{now.strftime('%H')}.log"
        return open(file_path, "a", encoding=self.encoding)

    def _ensure_stream(self):
        now = datetime.now()
        key = (now.year, now.month, now.day, now.hour)
        if key != self._current_key or self._stream is None:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
            self._stream = self._open_for(now)
            self._current_key = key
        return self._stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self._lock:
                stream = self._ensure_stream()
                msg = self.format(record)
                stream.write(msg + "\n")
                stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        super().close()


_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


class _UvicornAccessErrorOnlyFilter(logging.Filter):
    """Drop uvicorn access log records for non-error responses.

    Uvicorn emits access records with args of the form
    (client_addr, method, full_path, http_version, status_code). We keep
    only records whose status code is >= 400 so successful 2xx/3xx
    responses don't flood the logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not args:
            return True
        try:
            status_code = int(args[-1])
        except (ValueError, TypeError, IndexError):
            return True
        return status_code >= 400


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    """Configure root logger with hourly file + stderr handlers. Idempotent."""
    global _configured
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT, _DATE_FORMAT)

    file_handler = HourlyDailyFileHandler(logs_dir)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)

    if _configured:
        for h in list(root.handlers):
            if isinstance(h, HourlyDailyFileHandler):
                root.removeHandler(h)
                h.close()
        root.addHandler(file_handler)
        return

    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(stream_handler)
    root.addHandler(file_handler)

    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.error").propagate = True
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.propagate = True
    if not any(isinstance(f, _UvicornAccessErrorOnlyFilter) for f in access_logger.filters):
        access_logger.addFilter(_UvicornAccessErrorOnlyFilter())

    _configured = True
