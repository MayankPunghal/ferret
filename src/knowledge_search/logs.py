"""Application logs: console + rotating file (data/logs) + an in-memory buffer for the admin Logs page.

Log records never contain document text or query text (callers log identifiers and outcomes only), so the
"Copy for support" bundle is safe to share.
"""

import logging
import platform
import sys
import traceback
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_buffer: "deque[dict]" = deque(maxlen=2000)
_installed: set[str] = set()


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            tb = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else ""
            _buffer.append({
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
                "level": record.levelname, "logger": record.name, "message": record.getMessage(),
                "traceback": tb,
            })
        except Exception:  # noqa: BLE001 - logging must never raise
            pass


class WindowsDisconnectNoise(logging.Filter):
    """Drop asyncio's 'ConnectionResetError in _call_connection_lost' on Windows: logged when a browser
    closes an idle keep-alive connection (CPython Proactor quirk); harmless, but alarming in the console."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":  # uvicorn logs normal startup lines under this alarming name
            record.name = "uvicorn"
        exc = record.exc_info[1] if record.exc_info else None
        return not (record.name == "asyncio" and isinstance(exc, ConnectionResetError)
                    and "_call_connection_lost" in record.getMessage())


def install(data_dir: Path | str | None = None) -> None:
    """Idempotent: attach the file + buffer handlers and the noise filter to the root logger."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format=FORMAT)
    if "buffer" not in _installed:
        h = _BufferHandler(level=logging.INFO)
        root.addHandler(h)
        _installed.add("buffer")
    noise = WindowsDisconnectNoise()
    for h in root.handlers:
        if not any(isinstance(f, WindowsDisconnectNoise) for f in h.filters):
            h.addFilter(noise)
    if data_dir is not None and "file" not in _installed:
        try:
            d = Path(data_dir) / "logs"
            d.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(d / "knowledge-search.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(logging.Formatter(FORMAT))
            fh.addFilter(noise)
            root.addHandler(fh)
            _installed.add("file")
        except OSError as e:  # read-only location etc.: keep running with console + buffer
            logging.getLogger(__name__).warning("cannot write log file: %s", e)
    for noisy in ("pixeltable_pgserver", "httpx", "huggingface_hub"):  # embedded DB and download chatter
        logging.getLogger(noisy).setLevel(logging.WARNING)


LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def records(min_level: str = "WARNING", limit: int = 500) -> list[dict]:
    lvl = LEVELS.get(min_level.upper(), 30)
    out = [r for r in _buffer if LEVELS.get(r["level"], 0) >= lvl]
    return out[-limit:]


def diagnostics(extra: dict | None = None) -> dict:
    from importlib.metadata import PackageNotFoundError, version

    def v(pkg: str) -> str:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return "?"

    return {
        "app_version": v("knowledge-search"), "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()} ({platform.version()})", "machine": platform.machine(),
        "fastembed": v("fastembed"), "onnxruntime": v("onnxruntime"), "pgserver": v("pixeltable-pgserver"),
        **(extra or {}),
    }
