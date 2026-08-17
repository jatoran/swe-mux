"""Read-only on-disk footprint accounting for swe-mux's own data.

This measures the bytes swe-mux itself stores, in two places:

* the global data directory (``~/.mux``), grouped into named buckets
  (``database``, ``webview``, ``logs``, ``worktrees``, ...); and
* each Project's ``.swe-mux`` directory.

It deliberately does **not** report the host drive's free/used capacity. That
is a machine fact, not swe-mux's footprint, and reporting it would make the
figure machine-specific rather than "how much space swe-mux uses". Nothing here
deletes, prunes, or vacuums; measurement only.

The walk is I/O-heavy -- the WebView2 cache alone is hundreds of megabytes of
many small files -- so it must never run on a poll cadence. Results are cached
for ``CACHE_TTL_SECONDS`` behind a lock (so concurrent requests walk once, not
N times), and every caller is expected to run :meth:`StorageUsage.snapshot` off
the event loop via ``asyncio.to_thread``. Sizes come from ``st_size`` only;
directory mtime is never consulted, because on Windows a directory's mtime does
not move when a nested file grows and an open file can report a frozen mtime for
hours -- a time-based cache is honest where mtime invalidation would silently
lie.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

#: How long a computed report is reused before the next request re-walks. The
#: modal that consumes this offers a manual refresh (``force=True``) for an
#: immediate re-measure; this only bounds how stale a passive read can be.
CACHE_TTL_SECONDS = 30.0

#: Rotated or plain log files all collapse into the ``logs`` bucket.
_LOG_RE = re.compile(r"\.log(\.\d+)?$")

#: The single, mutually exclusive bucket a top-level ``~/.mux`` entry belongs
#: to. Order is irrelevant; every entry matches exactly one rule, falling
#: through to ``other`` (small config/state files: config.toml, the JSON stores,
#: tokens, notes, prompts, and the like).
_DATABASE_ENTRIES = {"mux.db", "mux.db-wal", "mux.db-shm"}
_NAMED_DIR_BUCKETS = {
    "webview": "webview",
    "worktrees": "worktrees",
    "voice": "voice",
    "media": "media",
    "sessions": "sessions",
    # Terminal checkpoints for cold session recovery. Its own bucket rather than
    # folded into `sessions`, because it is bounded by a separate budget and is
    # the one people will want to see the size of after a run of crashes.
    "recovery": "recovery",
    ".trash": "trash",
}


def _classify(name: str) -> str:
    """Map a top-level ``~/.mux`` entry name to its bucket."""
    if name in _DATABASE_ENTRIES:
        return "database"
    if name in _NAMED_DIR_BUCKETS:
        return _NAMED_DIR_BUCKETS[name]
    if _LOG_RE.search(name):
        return "logs"
    return "other"


def _walk_size(root: Path) -> tuple[int, int]:
    """Recursively sum ``st_size`` under ``root``; return ``(bytes, files)``.

    Best-effort: any entry that cannot be stat-ed (permission, race, dead
    junction) is skipped rather than aborting the whole walk. Symlinks and
    Windows junctions are not followed, so a reparse point cannot inflate the
    total or send the walk outside ``root``.
    """
    total = 0
    files = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files


def _entry_size(path: Path) -> tuple[int, int]:
    """Size of one filesystem entry: ``st_size`` for a file, a walk for a dir."""
    try:
        if path.is_symlink():
            return 0, 0
        if path.is_dir():
            return _walk_size(path)
        return path.stat().st_size, 1
    except OSError:
        return 0, 0


def _child_breakdown(root: Path) -> list[dict[str, object]]:
    """Size each immediate child of ``root``, largest first.

    Directories are walked recursively and reported as one entry; files are
    reported individually. This is the per-area breakdown of a single ``.swe-mux``
    folder (``notes``, ``attachments``, ``preview-shots``, ``config.toml``, ...)
    -- the same idea as the data-dir buckets, one level down.
    """
    children: list[dict[str, object]] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return children
    for entry in entries:
        size, files = _entry_size(Path(entry.path))
        if files == 0:
            continue
        children.append({"name": entry.name, "bytes": size, "files": files})
    children.sort(key=lambda item: cast(int, item["bytes"]), reverse=True)
    return children


@dataclass(frozen=True)
class ProjectFootprintTarget:
    """The subset of a Project a footprint read needs."""

    id: str
    label: str
    root: str


class StorageUsage:
    """TTL-cached, lock-guarded footprint reader for the data dir and Projects.

    ``projects`` is a zero-argument callable returning the current Project set,
    read fresh on every (uncached) walk so a newly added or removed Project is
    reflected without re-wiring. It is a callable rather than a captured list
    because the Project set changes over the daemon's life.
    """

    def __init__(
        self,
        data_dir: Path,
        projects: Callable[[], list[ProjectFootprintTarget]],
        *,
        ttl: float = CACHE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._projects = projects
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: dict[str, object] | None = None
        self._cached_at: float = 0.0

    def snapshot(self, *, force: bool = False) -> dict[str, object]:
        """Return the footprint report, recomputing only when stale or forced.

        Serialized by a lock so concurrent callers share one walk: a second
        caller blocks, then re-checks freshness inside the lock and returns the
        report the first caller just produced instead of walking again.
        """
        with self._lock:
            now = self._clock()
            if (
                not force
                and self._cached is not None
                and (now - self._cached_at) < self._ttl
            ):
                fresh = dict(self._cached)
                fresh["cached"] = True
                fresh["age_seconds"] = round(now - self._cached_at, 3)
                return fresh
            report = self._compute()
            self._cached = report
            self._cached_at = self._clock()
            fresh = dict(report)
            fresh["cached"] = False
            fresh["age_seconds"] = 0.0
            return fresh

    def _compute(self) -> dict[str, object]:
        started = self._clock()
        global_section = self._compute_global()
        projects_section = self._compute_projects()
        return {
            "generated_at": time.time(),
            "duration_ms": round((self._clock() - started) * 1000, 1),
            "data_dir": str(self._data_dir),
            "global": global_section,
            "projects": projects_section,
        }

    def _compute_global(self) -> dict[str, object]:
        buckets: dict[str, dict[str, int]] = {}
        total_bytes = 0
        total_files = 0
        try:
            entries = list(os.scandir(self._data_dir))
        except OSError as exc:
            return {
                "present": False,
                "error": str(exc),
                "total_bytes": 0,
                "total_files": 0,
                "buckets": [],
            }
        for entry in entries:
            size, files = _entry_size(Path(entry.path))
            total_bytes += size
            total_files += files
            bucket = buckets.setdefault(_classify(entry.name), {"bytes": 0, "files": 0})
            bucket["bytes"] += size
            bucket["files"] += files
        ordered = sorted(
            (
                {"name": name, "bytes": data["bytes"], "files": data["files"]}
                for name, data in buckets.items()
                if data["files"] > 0
            ),
            key=lambda item: cast(int, item["bytes"]),
            reverse=True,
        )
        return {
            "present": True,
            "total_bytes": total_bytes,
            "total_files": total_files,
            "buckets": ordered,
        }

    def _compute_projects(self) -> dict[str, object]:
        items: list[dict[str, object]] = []
        total_bytes = 0
        for project in self._projects():
            mux_dir = Path(project.root) / ".swe-mux"
            present = mux_dir.is_dir()
            size, files = _entry_size(mux_dir) if present else (0, 0)
            total_bytes += size
            items.append(
                {
                    "project_id": project.id,
                    "label": project.label,
                    "root": project.root,
                    "present": present,
                    "bytes": size,
                    "files": files,
                    "buckets": _child_breakdown(mux_dir) if present else [],
                }
            )
        items.sort(key=lambda item: cast(int, item["bytes"]), reverse=True)
        return {"total_bytes": total_bytes, "items": items}
