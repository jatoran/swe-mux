"""Off-loop, shared transcript directory scans and cached path resolution.

Two filesystem costs the session manager used to pay on the event loop, per
session, on every tick of its discovery and switch-watch loops:

- `adapter.recent_transcripts(cwd, created_at)`: a glob plus a stat per file
  over the harness's per-cwd transcript directory. Idle it is milliseconds;
  under the load of several CLIs starting at once it was measured at 5.4s
  (2026-09-14, 836 files, seven fresh Claude sessions in one cwd). Seven
  sessions each running it every 0.5s saturated the loop with syscalls, request
  latency crossed the tray's 1s health timeout for 45s, and the tray terminated
  a daemon that was serving traffic.
- `Path.resolve()` on a session's cwd and on every live session's transcript
  path, which `_same_cwd_siblings` and `_live_transcript_claims` repeat across
  the whole fleet on each call: a realpath syscall per session per caller, tens
  of thousands a minute at fifty sessions, all on the loop.

`TranscriptScanCache` runs the adapter's scan in a worker thread and shares one
result per (backend, cwd) across every session standing in that directory for
`TRANSCRIPT_SCAN_CACHE_SECONDS`, deduplicating concurrent requests onto one
in-flight scan. `resolve_path_cached` remembers a resolved path for
`RESOLVED_PATH_CACHE_SECONDS`; a working directory's identity does not change
under a running session, so the syscall answers the same question every time.

Both are measured rather than trusted: a scan slower than
`SLOW_SCAN_WARN_SECONDS` is a WARNING naming the backend, the directory and the
file count, and `snapshot()` reports hit and miss counts on the diagnostics
endpoint so a cache that stops sharing is visible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: How long one directory listing serves every session in that directory. The
#: discovery loop ticks at 0.5s and the switch watcher at 2s, so this bounds the
#: scan rate per directory to one per second however many sessions share it, at a
#: cost of at most one extra second before a brand-new transcript is noticed -
#: which is dominated by the CLI's own time to write its first record.
TRANSCRIPT_SCAN_CACHE_SECONDS = 1.0

#: A scan slower than this is reported, because it is the leading indicator of
#: the failure this module exists to prevent.
SLOW_SCAN_WARN_SECONDS = 1.0

#: How long a resolved path is remembered. A session's cwd and a transcript's
#: location are stable for far longer; the bound exists so a renamed symlink or
#: a re-mounted drive is eventually re-read rather than never.
RESOLVED_PATH_CACHE_SECONDS = 30.0

#: The resolve cache is bounded by entry count rather than evicted by age, because
#: an unbounded dict keyed by every path the daemon ever asked about is a leak. At
#: fifty sessions the working set is a few hundred keys.
RESOLVED_PATH_CACHE_LIMIT = 4096

Listing = list[tuple[float, Path, str]]


class ScansTranscripts(Protocol):
    """The slice of a backend adapter this cache reads."""

    name: str

    def recent_transcripts(self, cwd: Path, created_at: float) -> Listing: ...


_RESOLVED_GUARD = threading.Lock()
_RESOLVED: dict[str, tuple[float, str]] = {}


def resolve_path_cached(path: Path | str, *, now: float | None = None) -> str:
    """`str(Path(path).resolve())`, remembered for `RESOLVED_PATH_CACHE_SECONDS`.

    Keyed by the exact string asked about (not normcased): two spellings that
    resolve to one file both land on the same answer, and asking twice costs one
    syscall rather than two. A resolve that fails with `OSError` answers the
    input unchanged, exactly as the uncached callers did, and is not remembered -
    a missing file may exist on the next ask.
    """
    key = str(path)
    moment = time.monotonic() if now is None else now
    with _RESOLVED_GUARD:
        cached = _RESOLVED.get(key)
        if cached is not None and moment - cached[0] < RESOLVED_PATH_CACHE_SECONDS:
            return cached[1]
    try:
        resolved = str(Path(key).resolve())
    except OSError:
        return key
    with _RESOLVED_GUARD:
        if len(_RESOLVED) >= RESOLVED_PATH_CACHE_LIMIT:
            _RESOLVED.clear()
        _RESOLVED[key] = (moment, resolved)
    return resolved


def reset_resolved_path_cache() -> None:
    """Forget every remembered path. For tests that rename what they resolved."""
    with _RESOLVED_GUARD:
        _RESOLVED.clear()


@dataclass
class _ScanEntry:
    """One directory's listing and the `created_at` it was computed for."""

    at: float
    created_at: float
    listing: Listing
    elapsed: float


class TranscriptScanCache:
    """Per-(backend, cwd) transcript listings, scanned off the loop and shared.

    The adapters all apply the same monotone filter first - a file whose mtime
    plus two seconds is older than `created_at` is dropped before anything else
    is read - so a listing computed for an *earlier* `created_at` is a superset
    of one computed for a later one, and can be narrowed to it exactly. That is
    what lets sessions spawned seconds apart share one scan: the earliest asker
    pays, everyone later filters.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = TRANSCRIPT_SCAN_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        run_in_thread: Callable[..., Any] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._run_in_thread = run_in_thread or asyncio.to_thread
        self._entries: dict[tuple[str, str], _ScanEntry] = {}
        self._inflight: dict[tuple[str, str], tuple[float, asyncio.Future[_ScanEntry]]] = {}
        self._hits = 0
        self._misses = 0
        self._joined = 0
        self._slow_scans = 0
        self._worst_seconds = 0.0

    @staticmethod
    def _key(adapter: ScansTranscripts, cwd: Path) -> tuple[str, str]:
        # normcase rather than resolve: the point is to avoid a syscall here, and
        # two sessions recorded with the same cwd string share the same directory.
        return adapter.name, os.path.normcase(str(cwd))

    @staticmethod
    def _narrow(listing: Listing, created_at: float) -> Listing:
        return [item for item in listing if item[0] + 2 >= created_at]

    async def recent(self, adapter: ScansTranscripts, cwd: Path, created_at: float) -> Listing:
        """`adapter.recent_transcripts(cwd, created_at)`, shared and off the loop.

        Raises whatever the adapter raises (callers already handle `OSError`);
        a failed scan is not cached, so the next tick asks again.
        """
        key = self._key(adapter, cwd)
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None and now - entry.at < self._ttl and created_at >= entry.created_at:
            self._hits += 1
            return self._narrow(entry.listing, created_at)
        pending = self._inflight.get(key)
        if pending is not None and created_at >= pending[0]:
            self._joined += 1
            try:
                joined = await asyncio.shield(pending[1])
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                # The leader's own task was cancelled (its session stopped), which
                # says nothing about this one: scan on our own behalf below.
            else:
                return self._narrow(joined.listing, created_at)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[_ScanEntry] = loop.create_future()
        self._inflight[key] = (created_at, future)
        self._misses += 1
        try:
            fresh = await self._scan(adapter, cwd, created_at)
        except asyncio.CancelledError:
            if not future.done():
                future.cancel()
            raise
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            # A joiner awaiting this future re-raises it; nobody else reads it.
            future.exception()
            raise
        else:
            self._entries[key] = fresh
            if not future.done():
                future.set_result(fresh)
            return self._narrow(fresh.listing, created_at)
        finally:
            if self._inflight.get(key, (None, None))[1] is future:
                del self._inflight[key]

    async def _scan(self, adapter: ScansTranscripts, cwd: Path, created_at: float) -> _ScanEntry:
        started = time.perf_counter()
        listing: Listing = await self._run_in_thread(adapter.recent_transcripts, cwd, created_at)
        elapsed = time.perf_counter() - started
        self._worst_seconds = max(self._worst_seconds, elapsed)
        if elapsed >= SLOW_SCAN_WARN_SECONDS:
            self._slow_scans += 1
            log.warning(
                "transcript_scan_slow backend=%s cwd=%s files=%d elapsed_s=%.2f",
                adapter.name,
                cwd,
                len(listing),
                elapsed,
                extra={
                    "scan_backend": adapter.name,
                    "scan_cwd": str(cwd),
                    "scan_files": len(listing),
                    "scan_elapsed_s": round(elapsed, 3),
                },
            )
        else:
            log.debug(
                "transcript_scan backend=%s cwd=%s files=%d elapsed_ms=%.1f",
                adapter.name,
                cwd,
                len(listing),
                elapsed * 1000,
            )
        return _ScanEntry(self._clock(), created_at, listing, elapsed)

    def forget(self, adapter: ScansTranscripts, cwd: Path) -> None:
        """Drop one directory's listing so the next ask rescans."""
        self._entries.pop(self._key(adapter, cwd), None)

    def snapshot(self) -> dict[str, Any]:
        """Counters for `/api/diagnostics/background`."""
        return {
            "directories": len(self._entries),
            "inflight": len(self._inflight),
            "hits": self._hits,
            "misses": self._misses,
            "joined": self._joined,
            "slow_scans": self._slow_scans,
            "worst_seconds": round(self._worst_seconds, 3),
            "ttl_seconds": self._ttl,
        }
