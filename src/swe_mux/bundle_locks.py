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
