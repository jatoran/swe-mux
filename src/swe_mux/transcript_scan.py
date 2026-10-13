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

The resolve cache never re-resolves on the event loop. An expired entry asked
for from the loop thread answers its previous value and refreshes in a worker
(stale-while-revalidate), because "re-read the path every 30 s" is a freshness
bound, not a reason to block every terminal: the 2026-10-12 stall dumps had the
loop inside an expiry-driven `realpath` for 140 s in one day. Only a path the
daemon has never resolved costs a synchronous syscall, and async callers take
even that off the loop with `resolve_paths_async` before reading keys.

Both are measured rather than trusted: a scan slower than
`SLOW_SCAN_WARN_SECONDS` is a WARNING naming the backend, the directory and the
file count, a synchronous resolve on the loop slower than
`SLOW_LOOP_RESOLVE_WARN_SECONDS` is a WARNING naming the path, and `snapshot()`
reports hit and miss counts on the diagnostics endpoint so a cache that stops
sharing is visible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
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
#: fifty sessions the working set is a few hundred keys. Eviction is least
#: recently used, one entry at a time: clearing the whole map at the bound made
#: every live session's next key a synchronous miss at once.
RESOLVED_PATH_CACHE_LIMIT = 4096

#: A synchronous resolve on the event loop slower than this is reported. It can
#: only be a first-ever resolution (expired entries refresh off the loop), so a
#: recurring one names a caller that should be priming with `resolve_paths_async`.
SLOW_LOOP_RESOLVE_WARN_SECONDS = 0.25

Listing = list[tuple[float, Path, str]]


class ScansTranscripts(Protocol):
    """The slice of a backend adapter this cache reads."""

    name: str

    def recent_transcripts(self, cwd: Path, created_at: float) -> Listing: ...


_RESOLVED_GUARD = threading.Lock()
_RESOLVED: OrderedDict[str, tuple[float, str]] = OrderedDict()
#: Keys with a background refresh in flight, so one expiry schedules one refresh
#: however many callers ask during it.
_REFRESHING: set[str] = set()
_RESOLVED_STATS: dict[str, Any] = {
    "hits": 0,
    "misses": 0,
    "stale_served": 0,
    "refreshes": 0,
    "loop_misses": 0,
    "primed": 0,
    "worst_loop_miss_seconds": 0.0,
}


def _on_loop_thread() -> asyncio.AbstractEventLoop | None:
    """The running loop when called from its own thread, else None."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _resolve_uncached(key: str) -> str | None:
    try:
        return str(Path(key).resolve())
    except OSError:
        return None


def _remember(key: str, moment: float, resolved: str) -> None:
    with _RESOLVED_GUARD:
        _RESOLVED[key] = (moment, resolved)
        _RESOLVED.move_to_end(key)
        while len(_RESOLVED) > RESOLVED_PATH_CACHE_LIMIT:
            _RESOLVED.popitem(last=False)


def _refresh_in_background(loop: asyncio.AbstractEventLoop, key: str) -> None:
    """Re-resolve ``key`` in the loop's default executor, at most once at a time."""
    with _RESOLVED_GUARD:
        if key in _REFRESHING:
            return
        _REFRESHING.add(key)

    def refresh() -> None:
        try:
            resolved = _resolve_uncached(key)
            if resolved is not None:
                _remember(key, time.monotonic(), resolved)
            with _RESOLVED_GUARD:
                _RESOLVED_STATS["refreshes"] += 1
        finally:
            with _RESOLVED_GUARD:
                _REFRESHING.discard(key)

    try:
        loop.run_in_executor(None, refresh)
    except RuntimeError:
        # The loop is closing (shutdown, or a test tearing down). The stale answer
        # is still the right one to give; the refresh simply does not happen.
        with _RESOLVED_GUARD:
            _REFRESHING.discard(key)


def resolve_path_cached(path: Path | str, *, now: float | None = None) -> str:
    """`str(Path(path).resolve())`, remembered for `RESOLVED_PATH_CACHE_SECONDS`.

    Keyed by the exact string asked about (not normcased): two spellings that
    resolve to one file both land on the same answer, and asking twice costs one
    syscall rather than two. A resolve that fails with `OSError` answers the
    input unchanged, exactly as the uncached callers did, and is not remembered -
    a missing file may exist on the next ask.

    An expired entry asked for on the event loop's own thread answers its
    previous value and is refreshed in a worker, so the loop never pays for the
    freshness bound. Off the loop (a worker thread, or no loop at all) an expired
    entry is re-resolved inline, which is the old behaviour and costs nobody else.
    """
    key = str(path)
    moment = time.monotonic() if now is None else now
    loop = _on_loop_thread()
    with _RESOLVED_GUARD:
        cached = _RESOLVED.get(key)
        if cached is not None:
            _RESOLVED.move_to_end(key)
            if moment - cached[0] < RESOLVED_PATH_CACHE_SECONDS:
                _RESOLVED_STATS["hits"] += 1
                return cached[1]
            if loop is not None:
                _RESOLVED_STATS["stale_served"] += 1
        else:
            _RESOLVED_STATS["misses"] += 1
            if loop is not None:
                _RESOLVED_STATS["loop_misses"] += 1
    if cached is not None and loop is not None:
        _refresh_in_background(loop, key)
        return cached[1]
    started = time.perf_counter()
    resolved = _resolve_uncached(key)
    elapsed = time.perf_counter() - started
    if loop is not None and cached is None:
        with _RESOLVED_GUARD:
            _RESOLVED_STATS["worst_loop_miss_seconds"] = max(
                _RESOLVED_STATS["worst_loop_miss_seconds"], elapsed
            )
        if elapsed >= SLOW_LOOP_RESOLVE_WARN_SECONDS:
            log.warning(
                "resolve_on_loop_slow path=%s elapsed_s=%.2f",
                key,
                elapsed,
                extra={"resolve_path": key, "resolve_elapsed_s": round(elapsed, 3)},
            )
    if resolved is None:
        return key
    _remember(key, moment, resolved)
    return resolved


def cached_resolution(path: Path | str) -> str | None:
    """The remembered resolution of ``path`` (fresh or stale), without a syscall."""
    with _RESOLVED_GUARD:
        cached = _RESOLVED.get(str(path))
    return cached[1] if cached is not None else None


def _resolve_missing(keys: list[str]) -> int:
    resolved = 0
    for key in keys:
        value = _resolve_uncached(key)
        if value is not None:
            _remember(key, time.monotonic(), value)
            resolved += 1
    return resolved


async def resolve_paths_async(paths: Iterable[Path | str]) -> None:
    """Resolve every path not yet remembered, in one worker call, off the loop.

    For async callers about to ask `resolve_path_cached` about a set of paths
    synchronously (claim checks, sibling comparisons): priming first means those
    reads are cache hits rather than first-ever resolutions on the loop. Paths
    already remembered, fresh or stale, cost one dict lookup here.
    """
    with _RESOLVED_GUARD:
        missing = list(dict.fromkeys(str(p) for p in paths if str(p) not in _RESOLVED))
    if not missing:
        return
    primed = await asyncio.to_thread(_resolve_missing, missing)
    with _RESOLVED_GUARD:
        _RESOLVED_STATS["primed"] += primed


def resolved_path_snapshot() -> dict[str, Any]:
    """Resolve-cache counters for `/api/diagnostics/background`."""
    with _RESOLVED_GUARD:
        return {
            **{
                key: (round(value, 3) if isinstance(value, float) else value)
                for key, value in _RESOLVED_STATS.items()
            },
            "entries": len(_RESOLVED),
            "refreshing": len(_REFRESHING),
            "ttl_seconds": RESOLVED_PATH_CACHE_SECONDS,
        }


def reset_resolved_path_cache() -> None:
    """Forget every remembered path. For tests that rename what they resolved."""
    with _RESOLVED_GUARD:
        _RESOLVED.clear()
        _REFRESHING.clear()


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
            # The resolve cache the same callers read keys through. `loop_misses`
            # counts first-ever resolutions taken on the loop thread; a number that
            # keeps climbing names a caller that should prime off the loop.
            "resolved_paths": resolved_path_snapshot(),
        }
