"""Keep the daemon schedulable while the fleet saturates the host.

The daemon is one mostly idle process whose only job is to answer the person at the
keyboard within a few milliseconds. The fleet it carries is dozens of processes that
each want every core - cargo, rustc, pytest, node - and on 2026-09-01 three worktree
agents building concurrently froze the daemon for 46 s and 53 s inside three minutes
while the small, hot PTY supervisor beside it kept running. At equal priority the OS
has no reason to prefer the process a human is waiting on.

Two knobs, both scheduling-class only, and both a no-op on an idle host:

- **Session trees run below normal.** Applied to the root at spawn (its children
  inherit the class) and enforced on every descendant by the process inspector's
  regular pass, because a child can raise itself and a session adopted from a
  previous daemon was spawned before the policy existed. Only ever *lowers*: a
  process already at or below the target is left alone, so an agent that chose to
  run its own gate at idle priority is not raised back.
- **The daemon runs above normal.** Raised once at start. Above-normal needs no
  elevation on Windows; on POSIX a negative nice needs root and is reported rather
  than attempted twice.

The supervisor stays at normal on purpose: a session root is spawned by it and
inherits its class, so raising it would hand every new agent above-normal for the
milliseconds before the spawn path lowers it - and at normal it already wins
against a below-normal fleet.

I/O priority is deliberately not touched. Windows' background I/O class is far
harsher than one CPU step and would make an agent's build crawl whenever the
operator opened a file.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a base dependency
    psutil = None

log = logging.getLogger(__name__)

SESSION_PRIORITIES: tuple[str, ...] = ("normal", "below_normal")
DAEMON_PRIORITIES: tuple[str, ...] = ("normal", "above_normal")

#: Windows scheduling classes, lowest first. Names are psutil constants resolved at
#: call time because they exist only on Windows builds of the module.
_WINDOWS_ORDER: tuple[str, ...] = (
    "IDLE_PRIORITY_CLASS",
    "BELOW_NORMAL_PRIORITY_CLASS",
    "NORMAL_PRIORITY_CLASS",
    "ABOVE_NORMAL_PRIORITY_CLASS",
    "HIGH_PRIORITY_CLASS",
    "REALTIME_PRIORITY_CLASS",
)
_WINDOWS_NAMES: dict[str, str] = {
    "IDLE_PRIORITY_CLASS": "idle",
    "BELOW_NORMAL_PRIORITY_CLASS": "below_normal",
    "NORMAL_PRIORITY_CLASS": "normal",
    "ABOVE_NORMAL_PRIORITY_CLASS": "above_normal",
    "HIGH_PRIORITY_CLASS": "high",
    "REALTIME_PRIORITY_CLASS": "realtime",
}
#: POSIX nice values standing in for the same names. Only the two the policy uses.
_POSIX_NICE: dict[str, int] = {"below_normal": 5, "normal": 0, "above_normal": -5}

#: Outcomes, so a caller can count them without parsing a message.
LOWERED = "lowered"
RAISED = "raised"
UNCHANGED = "unchanged"
UNSUPPORTED = "unsupported"
DENIED = "denied"
GONE = "gone"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _windows_rank(value: int) -> int | None:
    if psutil is None:
        return None
    for rank, name in enumerate(_WINDOWS_ORDER):
        if getattr(psutil, name, None) == value:
            return rank
    return None


def _windows_value(name: str) -> int | None:
    if psutil is None:
        return None
    for constant, label in _WINDOWS_NAMES.items():
        if label == name:
            return getattr(psutil, constant, None)
    return None


def priority_name(process: Any) -> str | None:
    """The process's current scheduling class by the names this module uses."""
    if psutil is None:
        return None
    try:
        value = process.nice()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    if _is_windows():
        rank = _windows_rank(int(value))
        return _WINDOWS_NAMES[_WINDOWS_ORDER[rank]] if rank is not None else None
    nice = int(value)
    if nice >= _POSIX_NICE["below_normal"]:
        return "below_normal"
    if nice <= _POSIX_NICE["above_normal"]:
        return "above_normal"
    return "normal"


def lower_process(process: Any, target: str) -> str:
    """Lower ``process`` to ``target`` if it is currently scheduled above it.

    Never raises a process. Returns one of the outcome constants; ``UNSUPPORTED``
    when psutil or the platform cannot express the class, ``DENIED`` when the OS
    refused (another user's process, or a protected one), ``GONE`` when it exited
    between the walk and this call.
    """
    if target == "normal":
        return UNCHANGED
    if psutil is None or target not in SESSION_PRIORITIES:
        return UNSUPPORTED
    try:
        current = process.nice()
        if _is_windows():
            wanted = _windows_value(target)
            current_rank = _windows_rank(int(current))
            wanted_rank = _windows_rank(wanted) if wanted is not None else None
            if wanted is None or wanted_rank is None:
                return UNSUPPORTED
            if current_rank is None or current_rank <= wanted_rank:
                return UNCHANGED
            process.nice(wanted)
            return LOWERED
        wanted_nice = _POSIX_NICE[target]
        if int(current) >= wanted_nice:
            return UNCHANGED
        process.nice(wanted_nice)
        return LOWERED
    except psutil.NoSuchProcess:
        return GONE
    except (psutil.AccessDenied, PermissionError):
        return DENIED
    except OSError as exc:
        log.debug("priority change failed pid=%s error=%s", getattr(process, "pid", "?"), exc)
        return DENIED


def apply_session_root(pid: int, target: str) -> str:
    """Lower a freshly spawned session root; its children inherit the class."""
    if psutil is None or pid <= 0 or target == "normal":
        return UNCHANGED if target == "normal" else UNSUPPORTED
    try:
        process = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return GONE
    outcome = lower_process(process, target)
    log.log(
        logging.INFO if outcome == LOWERED else logging.DEBUG,
        "session_process_priority pid=%d target=%s outcome=%s",
        pid,
        target,
        outcome,
    )
    return outcome


def raise_self(target: str) -> str:
    """Raise this process to ``target``; reports rather than retries a refusal."""
    if target == "normal":
        return UNCHANGED
    if psutil is None or target not in DAEMON_PRIORITIES:
        return UNSUPPORTED
    me = psutil.Process(os.getpid())
    try:
        if _is_windows():
            wanted = _windows_value(target)
            current_rank = _windows_rank(int(me.nice()))
            wanted_rank = _windows_rank(wanted) if wanted is not None else None
            if wanted is None or wanted_rank is None:
                return UNSUPPORTED
            if current_rank is not None and current_rank >= wanted_rank:
                return UNCHANGED
            me.nice(wanted)
            return RAISED
        wanted_nice = _POSIX_NICE[target]
        if int(me.nice()) <= wanted_nice:
            return UNCHANGED
        me.nice(wanted_nice)
        return RAISED
    except (psutil.AccessDenied, PermissionError):
        return DENIED
    except OSError as exc:
        log.debug("self priority change failed error=%s", exc)
        return DENIED
