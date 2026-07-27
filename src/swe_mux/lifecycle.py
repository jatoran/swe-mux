"""Cross-restart daemon death forensics.

Nothing in-process can observe an external TerminateProcess (a kill-on-close
Job going down, taskkill): the process just stops, with no traceback and no
shutdown log. So the daemon leaves breadcrumbs *outside* itself:

- ``daemon-heartbeat.json`` — a single rewritten record ``{pid, started_at,
  heartbeat_at, clean_exit, intent}`` refreshed every ~10 s. On startup, a
  record without a clean exit whose pid is gone is reported as an unexpected
  death, with the last-heartbeat age bounding the time of death.
- ``lifecycle.log`` — a small append-only ledger of lifecycle events (daemon
  starts, clean exits, unclean-death reports, tray-observed daemon exit
  codes, Job-membership warnings), written by both the daemon and the tray.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

HEARTBEAT_NAME = "daemon-heartbeat.json"
LEDGER_NAME = "lifecycle.log"
LEDGER_MAX_BYTES = 1024 * 1024
HEARTBEAT_INTERVAL_SECONDS = 10.0


def ledger(data_dir: Path, message: str) -> None:
    """Best-effort timestamped append; keeps one rotated generation."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        path = data_dir / LEDGER_NAME
        try:
            if path.stat().st_size > LEDGER_MAX_BYTES:
                os.replace(path, path.with_suffix(".log.1"))
        except OSError:
            pass
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def read_heartbeat(data_dir: Path) -> dict[str, object] | None:
    try:
        raw = json.loads((data_dir / HEARTBEAT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_heartbeat(data_dir: Path, record: dict[str, object]) -> None:
    path = data_dir / HEARTBEAT_NAME
    temporary = path.with_suffix(".json.tmp")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(record), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:
        return False


def _record_pid(record: dict[str, object] | None) -> int:
    value = None if record is None else record.get("pid")
    if isinstance(value, (int, float, str)):
        try:
            return int(value)
        except ValueError:
            return -1
    return -1


def _record_owned(data_dir: Path) -> bool:
    """Whether this process may rewrite the heartbeat record.

    During a session-preserving restart the successor daemon starts while the
    predecessor is still unwinding; plain last-writer-wins would let the
    exiting daemon clobber the successor's fresh record.
    """
    pid = _record_pid(read_heartbeat(data_dir))
    return pid <= 0 or pid == os.getpid() or not _pid_running(pid)


def daemon_started(data_dir: Path, log: logging.Logger) -> None:
    """Record this daemon's start; report a predecessor that died uncleanly."""
    previous = read_heartbeat(data_dir)
    if previous is not None and not bool(previous.get("clean_exit")):
        pid = _record_pid(previous)
        if pid > 0 and pid != os.getpid() and not _pid_running(pid):
            beat = previous.get("heartbeat_at")
            last_beat = float(beat) if isinstance(beat, (int, float)) else 0.0
            age = f"{time.time() - last_beat:.0f}s" if last_beat else "unknown time"
            message = (
                f"previous daemon pid {pid} died without a clean shutdown; last "
                f"heartbeat {age} before this start (external kill or hard crash "
                "— see crash.log and lifecycle.log)"
            )
            log.warning(message)
            ledger(data_dir, message)
    now = time.time()
    _write_heartbeat(
        data_dir,
        {
            "pid": os.getpid(),
            "started_at": now,
            "heartbeat_at": now,
            "clean_exit": False,
            "intent": None,
        },
    )
    ledger(data_dir, f"daemon pid {os.getpid()} started")


def heartbeat(data_dir: Path) -> None:
    if not _record_owned(data_dir):
        return
    record = read_heartbeat(data_dir) or {}
    record.update({"pid": os.getpid(), "heartbeat_at": time.time(), "clean_exit": False})
    record.setdefault("started_at", time.time())
    _write_heartbeat(data_dir, record)


def daemon_clean_exit(data_dir: Path, intent: str) -> None:
    ledger(data_dir, f"daemon pid {os.getpid()} clean exit (intent={intent})")
    if not _record_owned(data_dir):
        return
    record = read_heartbeat(data_dir) or {"started_at": time.time()}
    record.update(
        {
            "pid": os.getpid(),
            "heartbeat_at": time.time(),
            "clean_exit": True,
            "intent": intent,
        }
    )
    _write_heartbeat(data_dir, record)
