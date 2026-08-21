"""User-triggered native-history scan: scoping, progress, and cancellation.

The startup reconcile is silent and bounded; this is the interruptible version a
user runs for a first import, which a real machine can make expensive. These tests
drive it against a real on-disk transcript under an injected home.

Cancellation is pinned here too, in both halves of a scan, because "interruptible"
is the whole reason this surface exists and the walk is the half that ignored it.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux.config import Config
from swe_mux.harness import HARNESSES
from swe_mux.history import HistoryIndex
from swe_mux.history_scan import HistoryScanManager
from swe_mux.reconcile import (
    ExternalTranscript,
    ScanProgress,
    reconcile_external_history,
    scan_external_transcripts,
    scan_external_transcripts_async,
)


def _write_claude(home: Path, native_id: str, cwd: str) -> None:
    directory = home / ".claude" / "projects" / "encoded-demo"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{native_id}.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": native_id,
                "cwd": cwd,
                "timestamp": "2026-08-11T00:00:00.000Z",
                "message": {"content": [{"type": "text", "text": "hello"}]},
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "sessionId": native_id,
                "cwd": cwd,
                "timestamp": "2026-08-11T00:00:01.000Z",
                "message": {
                    "model": "claude-opus-4-8",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "content": [{"type": "text", "text": "hi"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


async def test_reconcile_reports_progress_and_scopes_to_backends(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _write_claude(home, "claude-scan-1", str(cwd))

    history = HistoryIndex(tmp_path / "mux.db")
    try:
        phases: list[str] = []
        last = ScanProgress()

        def on_progress(progress: ScanProgress) -> None:
            phases.append(progress.phase)
            last.phase = progress.phase
            last.scanned = progress.scanned
            last.processed = progress.processed
            last.imported = progress.imported

        scanned = await reconcile_external_history(
            history, home, backends={"claude"}, on_progress=on_progress
        )

        assert scanned == 1
        # Progress walks scanning -> indexing, and the one transcript is imported.
        assert "scanning" in phases and phases[-1] == "indexing"
        assert last.scanned == 1
        assert last.processed == 1
        assert last.imported == 1
        assert (await history.native_history_ids()).get(("claude", "claude-scan-1"))
    finally:
        history.close()


async def test_reconcile_stops_promptly_when_cancelled(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _write_claude(home, "claude-scan-2", str(cwd))

    history = HistoryIndex(tmp_path / "mux.db")
    try:
        scanned = await reconcile_external_history(
            history, home, backends={"claude"}, should_cancel=lambda: True
        )
        # A pre-cancelled scan indexes nothing and leaves no history row behind.
        assert scanned == 0
        assert not (await history.native_history_ids()).get(("claude", "claude-scan-2"))
    finally:
        history.close()


async def test_scan_manager_runs_once_and_reports_completion(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    _write_claude(home, "claude-scan-3", str(cwd))

    history = HistoryIndex(tmp_path / "mux.db")
    config = Config(data_dir=tmp_path)
    # Enable claude explicitly so the scoped scan does not depend on machine detection.
    config.harness_enabled = {
        name: name == "claude" for name in ("claude", "codex", "omp", "pi", "opencode")
    }
    manager = HistoryScanManager(history, config, home=home)
    try:
        started = manager.start()
        assert started["status"] == "running"
        assert started["backends"] == ["claude"]
        # A second start while one runs returns the in-flight job, not a second scan.
        assert manager.start()["status"] == "running"

        assert manager._task is not None
        await manager._task

        done = manager.status()
        assert done["status"] == "completed"
        assert done["imported"] == 1
        assert (await history.native_history_ids()).get(("claude", "claude-scan-3"))
    finally:
        await manager.stop()
        history.close()


def test_the_discovery_walk_polls_cancellation_as_often_as_the_indexing_pass(
    tmp_path: Path,
) -> None:
    """Both halves of a scan poll the token, not just the half that reads files.

    The walk is the expensive half on a real machine - tens of thousands of files
    under `~/.claude/projects` - and it is the half a shutdown lands in. It used to
    poll nothing at all, so a cancelled scan kept walking to the end.
    """
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    native_ids = [f"claude-walk-{index}" for index in range(12)]
    for native_id in native_ids:
        _write_claude(home, native_id, str(cwd))

    polls = 0

    def should_cancel() -> bool:
        nonlocal polls
        polls += 1
        return False

    found = scan_external_transcripts(home, backends={"claude"}, should_cancel=should_cancel)

    assert len(found) == len(native_ids)
    # Once per file while walking and once per file while indexing. Before the
    # walk polled, this was exactly one poll per file and a cancelled scan still
    # enumerated every directory it had been given.
    assert polls >= 2 * len(native_ids)


def test_a_cancelled_scan_walk_stops_before_the_next_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    for index in range(12):
        _write_claude(home, f"claude-abort-{index}", str(cwd))

    polls = 0

    def should_cancel() -> bool:
        nonlocal polls
        polls += 1
        # False through the per-harness polls that open the loop, whichever
        # harness the registry happens to list first, then True inside the walk.
        return polls > len(HARNESSES)

    found = scan_external_transcripts(home, backends={"claude"}, should_cancel=should_cancel)

    # Nothing discovered and nothing indexed: the walk returned where it was told
    # to. While only the indexing pass polled, the same token let the walk
    # enumerate all twelve files first and then stopped part-way through reading
    # them, which is a partial import rather than an abort.
    assert found == []
    assert polls == len(HARNESSES) + 1


async def test_cancelling_the_scan_releases_its_worker_thread(monkeypatch: Any) -> None:
    """A cancelled scan hands its token to the thread instead of abandoning it.

    `asyncio.to_thread` cannot interrupt a worker, and the loop joins every one of
    them in `shutdown_default_executor` at the end of shutdown. An abandoned walk
    therefore made the daemon - and every in-process app test that built one - wait
    out a whole transcript-tree walk after the last log line claimed a clean stop.
    """
    entered = threading.Event()
    released = threading.Event()

    def blocking_scan(
        home: Path | None = None,
        *,
        should_cancel: Any = None,
        **_rest: Any,
    ) -> list[ExternalTranscript]:
        entered.set()
        # Bounded so a regression fails this test rather than hanging the
        # interpreter: the default executor's threads are not daemons, so a
        # worker that spins forever is joined at exit and never reports.
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                released.set()
                break
            time.sleep(0.01)
        return []

    monkeypatch.setattr("swe_mux.reconcile.scan_external_transcripts", blocking_scan)
    task = asyncio.create_task(scan_external_transcripts_async(None, backends={"claude"}))
    assert await asyncio.to_thread(entered.wait, 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The worker notices on its next poll rather than at the end of its walk.
    assert await asyncio.to_thread(released.wait, 10)
