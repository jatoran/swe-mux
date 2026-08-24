"""What holds the frozen app bundle open against a redeploy swap.

The staged redeploy's one non-retryable step is renaming ``dist/swe-mux`` to
``dist/swe-mux.prev``: Windows refuses to rename a directory while any process
anchors it — an exe/DLL image mapped from inside it, a working directory in
it, or an open file handle. The redeploy script can stop everything that runs
the app's *own* image (the WebView shell, the daemon, in-session hook
helpers), but a foreign process it must never kill — typically a dev server
behind a Preview tab, or a terminal whose cwd landed inside ``dist/swe-mux``
via spawn-chain inheritance — holds the swap hostage: the build succeeds, the
app is stopped, the rename fails, and the old bundle is relaunched. Measured
live 2026-08-02: two consecutive redeploys failed exactly this way.

This module answers "who would block the swap" *before* anything is built or
stopped, so both the CLI script and ``POST /api/daemon/redeploy`` can refuse
with the offending processes named instead of failing after minutes of build.

Exclusions, and why they are safe:

- Processes named ``swe-mux.exe`` — the redeploy's own stop machinery
  terminates these (escalating to the whole image when a lock demands it).
- Descendants of a ``swe-mux.exe`` process — the WebView2 browser children
  and any daemon-owned conhost die with the shell/daemon they belong to.

What remains is a genuine blocker: session-spawned processes descend from the
*supervisor* (``swe-mux-supervisor.exe``, a sibling bundle that deliberately
survives every redeploy), so nothing in the redeploy will make them let go.

The scan reads each process's exe and cwd only. Memory-mapped DLLs are not
scanned (only executables inside the bundle load the bundle's DLLs, and those
are caught by the exe check), and neither are open *file* handles: a foreign
process has no reason to hold a file open inside the app bundle, and
enumerating every process's handle table measured 10× the whole scan's cost
(54 s of a 60 s scan) for that exotic case — which the swap's own
retry/rollback path still backstops. Prefix matching appends a path separator
so the sibling ``dist/swe-mux-supervisor`` tree can never match
``dist/swe-mux``.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_IMAGE_NAME = "swe-mux.exe"
# Bounded so a pathological parent chain (or a psutil loop) cannot hang a gate.
_ANCESTOR_WALK_LIMIT = 16

#: The single-flight lock both redeploy entry points claim, and the marker that
#: identifies the process it names.
#:
#: The lock is deliberately never removed on exit: a crash mid-redeploy would
#: otherwise leave a file claiming a run that is not happening, and the design
#: instead makes the *process* the authority, so a dead one releases the lock for
#: free. That reasoning is right and the implementation of it was not.
#:
#: **A pid is not an identity on Windows.** Pids are recycled aggressively, and a
#: `pid_exists` check therefore says "live" forever once something unrelated
#: inherits the number. Measured on the primary host 2026-08-24: a redeploy that
#: completed successfully at 18:35 the previous day left its lock naming pid
#: 50760, which by morning was an `svchost`. Every redeploy since had been
#: refused - and the refusal exits 0, so nothing upstream noticed. The same
#: hazard is already recorded for sessions (`SessionRecord.root_started_at`).
#:
#: So the lock names the process *and* its creation time, which settles recycling
#: exactly: the same number with a different start is a different process.
#:
#: Deliberately **not** also checked against the process's command line, though
#: that would have caught this particular lock on its own. A `cmdline()` read can
#: be slow or refused on Windows, and a false negative there means "no redeploy
#: is running" while one is - which starts a second redeploy racing the first for
#: the same staging tree and the same swap. That is a worse failure than the one
#: being fixed, and single-flight is the whole reason this lock exists.
#:
#: A lock written by an older build carries only a pid and falls back to plain
#: liveness, so an in-flight redeploy from the previous bundle is still
#: respected across the upgrade that introduces this. Exactly one such lock can
#: exist per machine, and a stale one is cleared by hand once.
REDEPLOY_LOCK_NAME = "redeploy.lock"
#: Tolerance on the creation-time comparison. A float survives a decimal
#: round-trip well inside this, and no two processes share a pid within a second
#: of each other.
_CREATED_AT_TOLERANCE_SECONDS = 1.0


def write_redeploy_lock(path: Path, pid: int) -> None:
    """Claim `path` for `pid`, recording the identity a reader will check.

    Written as ``"<pid> <create_time>"``. The creation time is best effort: a
    process that has already exited, or whose creation time cannot be read,
    yields a pid-only lock, which is the older format and still readable.
    """
    stamp = _process_created_at(pid)
    body = str(pid) if stamp is None else f"{pid} {stamp:.6f}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="ascii")


def live_redeploy_lock_pid(path: Path) -> int | None:
    """PID of a redeploy that is genuinely in flight, or None.

    None covers every way a lock can fail to mean anything: missing, empty
    (claimed by `O_EXCL` a moment before the pid was written), unparseable, or
    naming a process that has exited, been recycled, or is plainly not a
    redeploy.
    """
    try:
        raw = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if not raw:
        return None
    parts = raw.split()
    try:
        pid = int(parts[0])
    except ValueError:
        return None
    recorded = None
    if len(parts) > 1:
        try:
            recorded = float(parts[1])
        except ValueError:
            recorded = None
    if not _is_redeploy_process(pid, recorded):
        return None
    return pid


def _process_created_at(pid: int) -> float | None:
    try:
        import psutil

        return float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - identity is an optimization, never a gate
        return None


def _is_redeploy_process(pid: int, recorded_created_at: float | None) -> bool:
    """Is `pid` still the process this lock was written for?

    With a recorded creation time this is exact. Without one - a lock from a
    build that predates the stamp - it degrades to plain liveness, which is what
    that build meant by it, so an upgrade cannot decide an in-flight redeploy has
    stopped.
    """
    import psutil

    if recorded_created_at is None:
        return bool(psutil.pid_exists(pid))
    try:
        actual = float(psutil.Process(pid).create_time())
    except Exception:  # noqa: BLE001 - gone or unreadable is not live
        return False
    return abs(actual - recorded_created_at) <= _CREATED_AT_TOLERANCE_SECONDS


def frozen_bundle_root() -> Path | None:
    """The bundle directory when this process runs frozen, else None."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        return Path(sys.executable).resolve().parent
    except OSError:
        return None


def classify_bundle_holder(
    bundle: Path,
    *,
    name: str,
    exe: str | None,
    cwd: str | None,
    open_paths: Iterable[str] = (),
) -> tuple[str, str] | None:
    """``(via, path)`` when this process would block renaming ``bundle``.

    Pure classification over one process's observable anchors; the psutil
    enumeration lives in :func:`bundle_lock_holders`. ``None`` means it either
    holds nothing in the bundle or is the app's own image (which the redeploy
    stops itself).
    """
    if name.casefold() == APP_IMAGE_NAME:
        return None
    root = os.path.normcase(str(bundle))
    prefix = root + os.sep

    def within(path: str) -> bool:
        normalized = os.path.normcase(path)
        return normalized == root or normalized.startswith(prefix)

    if exe and within(exe):
        return ("exe", exe)
    if cwd and within(cwd):
        return ("cwd", cwd)
    for path in open_paths:
        if path and within(path):
            return ("open_file", path)
    return None


def _has_app_ancestor(process: Any) -> bool:
    """Whether any ancestor is the app image (so it dies with the app stop)."""
    import psutil

    try:
        parents = process.parents()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False
    for parent in parents[:_ANCESTOR_WALK_LIMIT]:
        try:
            if str(parent.name() or "").casefold() == APP_IMAGE_NAME:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return False


def bundle_lock_holders(bundle: Path) -> list[dict[str, Any]]:
    """Processes that would survive the redeploy stop and block the swap.

    Each entry: ``{pid, name, via: exe|cwd|open_file, path}``. Empty when the
    bundle does not exist (source-only checkouts) or nothing foreign holds it.
    Enumeration is best-effort: a process that denies access cannot be proven
    to hold anything and is skipped — the swap's own retry/rollback path
    remains the backstop for what a scan cannot see.
    """
    import psutil

    try:
        resolved = bundle.resolve()
    except OSError:
        resolved = bundle
    if not resolved.exists():
        return []
    holders: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            name = str(process.info.get("name") or "")
            try:
                exe: str | None = process.exe()
            except (psutil.AccessDenied, OSError):
                exe = None
            try:
                cwd: str | None = process.cwd()
            except (psutil.AccessDenied, OSError):
                cwd = None
            verdict = classify_bundle_holder(resolved, name=name, exe=exe, cwd=cwd)
            if verdict is None:
                continue
            if _has_app_ancestor(process):
                continue
            via, path = verdict
            holders.append(
                {"pid": int(process.pid), "name": name, "via": via, "path": path}
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return holders


def describe_holders(holders: list[dict[str, Any]]) -> str:
    """One human-readable line naming the blockers (bounded)."""
    shown = [
        f"pid {holder['pid']} {holder['name']} ({holder['via']}: {holder['path']})"
        for holder in holders[:5]
    ]
    extra = f"; and {len(holders) - 5} more" if len(holders) > 5 else ""
    return "; ".join(shown) + extra
