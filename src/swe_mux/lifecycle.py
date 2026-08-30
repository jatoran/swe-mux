"""Cross-restart daemon death forensics.

Nothing in-process can observe an external TerminateProcess (a kill-on-close
Job going down, taskkill): the process just stops, with no traceback and no
shutdown log. So the daemon leaves breadcrumbs *outside* itself:

- ``daemon-heartbeat.json`` — a single rewritten record ``{pid, started_at,
  heartbeat_at, clean_exit, intent, planned_intent, planned_at}`` refreshed
  every ~10 s. On startup, a record without a clean exit whose pid is gone is
  reported as an unexpected death, with the last-heartbeat age bounding the
  time of death.
- ``planned_intent`` is what stops that report firing on a *planned* restart.
  A clean exit is written last, after the whole teardown drain; the redeploy
  script asks the daemon to detach, watches health, and terminates the process
  as soon as it stops answering - which is several seconds before that write.
  So the predecessor died with ``clean_exit`` false every single time, and every
  successor reported a crash: 39 of them in one log, none of them real. A
  warning that is right 0% of the time is worse than no warning, so the intent
  is now recorded when it is *decided* rather than when the exit completes, by
  whichever endpoint decided it.
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


def pid_running(pid: int) -> bool:
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


def heartbeat_pid(data_dir: Path) -> int:
    """The pid the current heartbeat record names, or -1 when there is none."""
    return _record_pid(read_heartbeat(data_dir))


def _record_owned(data_dir: Path) -> bool:
    """Whether this process may rewrite the heartbeat record.

    During a session-preserving restart the successor daemon starts while the
    predecessor is still unwinding; plain last-writer-wins would let the
    exiting daemon clobber the successor's fresh record.
    """
    pid = _record_pid(read_heartbeat(data_dir))
    return pid <= 0 or pid == os.getpid() or not pid_running(pid)


def planned_handoff(data_dir: Path, intent: str) -> None:
    """Record that this daemon has been *asked* to stop, and why.

    Called by the endpoint that decides the intent - the desktop shutdown the
    redeploy script drives, and the session-preserving self-restart - which is
    the last moment at which this process is guaranteed to still be running.
    Waiting for the clean-exit write is what made every planned restart look
    like a crash: the process is usually terminated before it gets there.

    A record is not proof the daemon shut down cleanly, and is not treated as
    one: the successor reports the handoff and its intent instead of a crash,
    and `daemon.log` still holds whatever the teardown managed to say.
    """
    ledger(data_dir, f"daemon pid {os.getpid()} planned {intent} handoff requested")
    if not _record_owned(data_dir):
        return
    record = read_heartbeat(data_dir) or {"started_at": time.time()}
    record.update(
        {
            "pid": os.getpid(),
            "heartbeat_at": time.time(),
            "planned_intent": intent,
            "planned_at": time.time(),
        }
    )
    _write_heartbeat(data_dir, record)


def _planned_intent(record: dict[str, object]) -> str:
    value = record.get("planned_intent")
    return value.strip() if isinstance(value, str) and value.strip() else ""


def daemon_started(data_dir: Path, log: logging.Logger) -> bool:
    """Record this daemon's start; report a predecessor that died uncleanly.

    Returns whether the predecessor died *unplanned* - no clean exit, no
    recorded handoff intent, pid gone. That verdict feeds the startup database
    check: an external kill or a hard crash is the one cross-restart signal
    that says the file's history is suspect, so it forces the full integrity
    probe regardless of when the last one passed. A planned handoff is
    deliberately not that signal - the restart terminates the predecessor
    before its drain finishes on every session-preserving restart, so treating
    it as a crash would re-run the full probe on exactly the frequent path the
    conditional check exists to spare.
    """
    died_uncleanly = False
    previous = read_heartbeat(data_dir)
    if previous is not None and not bool(previous.get("clean_exit")):
        pid = _record_pid(previous)
        if pid > 0 and pid != os.getpid() and not pid_running(pid):
            beat = previous.get("heartbeat_at")
            last_beat = float(beat) if isinstance(beat, (int, float)) else 0.0
            age = f"{time.time() - last_beat:.0f}s" if last_beat else "unknown time"
            planned = _planned_intent(previous)
            if planned:
                message = (
                    f"previous daemon pid {pid} ended a planned {planned} handoff "
                    f"without recording a clean exit; last heartbeat {age} before "
                    "this start (expected: the restart terminates it once it stops "
                    "answering health, which is before its drain finishes)"
                )
                log.info(message)
            else:
                died_uncleanly = True
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
            "planned_intent": None,
            "planned_at": None,
        },
    )
    ledger(data_dir, f"daemon pid {os.getpid()} started")
    return died_uncleanly


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
