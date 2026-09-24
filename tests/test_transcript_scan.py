"""The shared off-loop transcript scan: one directory walk serves a whole cwd.

Pinned because the failure it prevents is silent at small scale: with one
session per directory the cache changes nothing, and with seven it is the
difference between a daemon that answers and one the tray kills (2026-09-14).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import transcript_scan
from swe_mux.transcript_scan import (
    RESOLVED_PATH_CACHE_LIMIT,
    TranscriptScanCache,
    reset_resolved_path_cache,
    resolve_path_cached,
)


class CountingAdapter:
    """An adapter whose scan counts calls and records the thread it ran on."""

    name = "claude"

    def __init__(self, listing: list[tuple[float, Path, str]], delay: float = 0.0) -> None:
        self.listing = listing
        self.delay = delay
        self.calls: list[tuple[str, float, str]] = []
        self.fail: BaseException | None = None

    def recent_transcripts(self, cwd: Path, created_at: float) -> list[tuple[float, Path, str]]:
        self.calls.append((str(cwd), created_at, threading.current_thread().name))
        if self.delay:
            time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        return [item for item in self.listing if item[0] + 2 >= created_at]


def listing(tmp_path: Path) -> list[tuple[float, Path, str]]:
    return [
        (1000.0, tmp_path / "old.jsonl", "old"),
        (2000.0, tmp_path / "mid.jsonl", "mid"),
        (3000.0, tmp_path / "new.jsonl", "new"),
    ]


async def test_the_scan_runs_off_the_loop_and_is_shared_across_a_directory(
    tmp_path: Path,
) -> None:
    adapter = CountingAdapter(listing(tmp_path))
    now = [0.0]
    cache = TranscriptScanCache(clock=lambda: now[0])
    first = await cache.recent(adapter, tmp_path, 1500.0)
    assert [item[2] for item in first] == ["mid", "new"]
    # Not the loop's thread: the walk is in a worker.
    assert adapter.calls[0][2] != threading.current_thread().name
    # A later-started sibling in the same directory is answered from the cache,
    # narrowed to its own start, without a second walk.
    second = await cache.recent(adapter, tmp_path, 2500.0)
    assert [item[2] for item in second] == ["new"]
    assert len(adapter.calls) == 1
    # An earlier start needs a wider listing than the cache holds, so it scans -
    # and from then on the wider listing serves everyone.
    third = await cache.recent(adapter, tmp_path, 500.0)
    assert [item[2] for item in third] == ["old", "mid", "new"]
    assert len(adapter.calls) == 2
    assert adapter.calls[1][1] == 500.0
    await cache.recent(adapter, tmp_path, 2500.0)
    assert len(adapter.calls) == 2
    snapshot = cache.snapshot()
    assert snapshot["hits"] == 2
    assert snapshot["misses"] == 2
    assert snapshot["directories"] == 1


async def test_the_listing_expires_and_a_different_directory_is_its_own_scan(
    tmp_path: Path,
) -> None:
    adapter = CountingAdapter(listing(tmp_path))
    now = [0.0]
    cache = TranscriptScanCache(ttl_seconds=1.0, clock=lambda: now[0])
    await cache.recent(adapter, tmp_path, 0.0)
    await cache.recent(adapter, tmp_path / "other", 0.0)
    assert len(adapter.calls) == 2
    now[0] = 0.9
    await cache.recent(adapter, tmp_path, 0.0)
    assert len(adapter.calls) == 2
    now[0] = 1.1
    await cache.recent(adapter, tmp_path, 0.0)
    assert len(adapter.calls) == 3
    # Two spellings the platform treats as one directory share, without a resolve
    # syscall to prove it. Where `normcase` folds case (Windows) that includes an
    # upper-cased path; elsewhere it is a different directory and its own scan.
    upper = Path(str(tmp_path).upper())
    await cache.recent(adapter, upper, 0.0)
    folds_case = os.path.normcase(str(upper)) == os.path.normcase(str(tmp_path))
    assert len(adapter.calls) == (3 if folds_case else 4)


async def test_concurrent_askers_join_one_inflight_scan(tmp_path: Path) -> None:
    adapter = CountingAdapter(listing(tmp_path), delay=0.05)
    cache = TranscriptScanCache()
    results = await asyncio.gather(
        *(cache.recent(adapter, tmp_path, 1500.0 + offset) for offset in range(6))
    )
    assert len(adapter.calls) == 1
    assert all([item[2] for item in result] == ["mid", "new"] for result in results)
    assert cache.snapshot()["joined"] == 5


async def test_a_failed_scan_is_raised_to_every_asker_and_not_cached(tmp_path: Path) -> None:
    adapter = CountingAdapter(listing(tmp_path), delay=0.02)
    adapter.fail = OSError("directory vanished")
    cache = TranscriptScanCache()
    outcomes = await asyncio.gather(
        cache.recent(adapter, tmp_path, 0.0),
        cache.recent(adapter, tmp_path, 0.0),
        return_exceptions=True,
    )
    assert all(isinstance(outcome, OSError) for outcome in outcomes)
    assert len(adapter.calls) == 1
    adapter.fail = None
    assert [item[2] for item in await cache.recent(adapter, tmp_path, 0.0)] == [
        "old",
        "mid",
        "new",
    ]
    assert cache.snapshot()["inflight"] == 0


async def test_a_cancelled_leader_does_not_cancel_the_sessions_that_joined_it(
    tmp_path: Path,
) -> None:
    adapter = CountingAdapter(listing(tmp_path), delay=0.05)
    cache = TranscriptScanCache()
    leader = asyncio.create_task(cache.recent(adapter, tmp_path, 0.0))
    await asyncio.sleep(0.005)
    joiner = asyncio.create_task(cache.recent(adapter, tmp_path, 0.0))
    await asyncio.sleep(0.005)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    # The joiner's own session did not stop; it scans on its own behalf.
    result = await joiner
    assert [item[2] for item in result] == ["old", "mid", "new"]
    assert len(adapter.calls) == 2


async def test_a_slow_scan_is_reported_with_its_measurements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(transcript_scan, "SLOW_SCAN_WARN_SECONDS", 0.01)
    adapter = CountingAdapter(listing(tmp_path), delay=0.03)
    cache = TranscriptScanCache()
    with caplog.at_level(logging.WARNING, logger="swe_mux.transcript_scan"):
        await cache.recent(adapter, tmp_path, 0.0)
    slow = [record for record in caplog.records if "transcript_scan_slow" in record.message]
    assert len(slow) == 1
    fields: Any = slow[0]
    assert fields.scan_backend == "claude"
    assert fields.scan_files == 3
    assert fields.scan_elapsed_s >= 0.03
    assert cache.snapshot()["slow_scans"] == 1
    assert cache.snapshot()["worst_seconds"] >= 0.03


def test_resolved_paths_are_remembered_and_bounded(tmp_path: Path) -> None:
    reset_resolved_path_cache()
    target = tmp_path / "real"
    target.mkdir()
    now = [0.0]
    first = resolve_path_cached(target, now=now[0])
    assert first == str(target.resolve())
    # A missing path answers itself and is not remembered.
    missing = tmp_path / "missing"
    assert resolve_path_cached(missing, now=now[0]) == str(missing.resolve())
    # The cache is bounded rather than a leak.
    for index in range(RESOLVED_PATH_CACHE_LIMIT + 5):
        resolve_path_cached(tmp_path / f"p{index}", now=now[0])
    assert len(transcript_scan._RESOLVED) <= RESOLVED_PATH_CACHE_LIMIT
    reset_resolved_path_cache()
    assert transcript_scan._RESOLVED == {}


def test_session_manager_path_helpers_read_through_the_cache(tmp_path: Path) -> None:
    from swe_mux.session import SessionManager

    reset_resolved_path_cache()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    record = SimpleNamespace(run_cwd=None, cwd=str(tmp_path / "a" / ".." / "a" / "b"))
    assert SessionManager._resolved_cwd(record) == nested.resolve()  # type: ignore[arg-type]
    assert SessionManager._path_key(nested) == str(nested.resolve()).casefold()
    assert SessionManager._path_key(str(nested)) == SessionManager._path_key(nested)
