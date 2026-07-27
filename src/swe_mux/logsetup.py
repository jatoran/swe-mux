"""Rolling file logs, crash tracebacks, and runtime log-level control.

The console redirect held by whoever spawned the daemon (``desktop-daemon.log``
/ ``daemon-relaunch.log``) is only a crash catcher for early startup and native
stderr; structured logging goes to rotating files in the data dir so a noisy
day can never grow an unbounded log:

- ``daemon.log`` — the application log (root logger), 10 MB x 5.
- ``access.log`` — the aiohttp request log, isolated (``propagate=False``) so
  request spam cannot drown signal in ``daemon.log`` or the console.
- ``crash.log`` — faulthandler output on hard native crashes (segfault /
  access violation). External kills (a Job closing, taskkill) cannot be
  observed in-process at all; those are covered by the lifecycle ledger.

The root logger level is the runtime debug toggle: ``log_level`` in config is
the startup default, ``POST /api/debug/log-level`` flips it on a live daemon.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
from pathlib import Path
from typing import IO

DAEMON_LOG_NAME = "daemon.log"
ACCESS_LOG_NAME = "access.log"
CRASH_LOG_NAME = "crash.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DAEMON_LOG_BYTES = 10 * 1024 * 1024
DAEMON_LOG_BACKUPS = 5
ACCESS_LOG_BYTES = 10 * 1024 * 1024
ACCESS_LOG_BACKUPS = 3

# Keeps the faulthandler target open for the process lifetime.
_crash_log_file: IO[str] | None = None


def rotating_handler(path: Path, *, max_bytes: int, backups: int) -> logging.Handler:
    """A size-rotated file handler that degrades gracefully.

    ``delay=True`` defers opening until the first emit, and a rotation rename
    that loses a race (a predecessor daemon briefly holding the old file during
    a session-preserving restart) is swallowed by ``Handler.handleError`` and
    retried on the next emit rather than breaking logging.
    """
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8", delay=True
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def normalize_level(level: str) -> str:
    candidate = str(level).strip().upper()
    if candidate not in LOG_LEVELS:
        raise ValueError(f"log level must be one of {', '.join(LOG_LEVELS)}")
    return candidate


def setup_daemon_logging(data_dir: Path, level: str = "INFO") -> None:
    """Configure the daemon process's logging: console + rotating files."""
    data_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(normalize_level(level))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(console)
    root.addHandler(
        rotating_handler(
            data_dir / DAEMON_LOG_NAME,
            max_bytes=DAEMON_LOG_BYTES,
            backups=DAEMON_LOG_BACKUPS,
        )
    )
    access = logging.getLogger("aiohttp.access")
    access.propagate = False
    access.setLevel(logging.INFO)
    access.addHandler(
        rotating_handler(
            data_dir / ACCESS_LOG_NAME,
            max_bytes=ACCESS_LOG_BYTES,
            backups=ACCESS_LOG_BACKUPS,
        )
    )


def set_log_level(level: str) -> str:
    """Apply a new root-logger level (runtime toggle); returns the normalized name."""
    name = normalize_level(level)
    logging.getLogger().setLevel(name)
    return name


def current_log_level() -> str:
    return logging.getLevelName(logging.getLogger().getEffectiveLevel())


def enable_crash_tracebacks(data_dir: Path) -> None:
    """Dump Python tracebacks into crash.log when the process dies hard.

    Catches segfaults/access violations in native extensions (pywinpty,
    sqlite3) that otherwise leave zero trace. Multiple processes appending to
    the same file is fine: faulthandler only writes when a crash happens.
    """
    global _crash_log_file
    if _crash_log_file is not None:
        return
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _crash_log_file = (data_dir / CRASH_LOG_NAME).open("a", encoding="utf-8")
        faulthandler.enable(file=_crash_log_file)
    except OSError:
        _crash_log_file = None
