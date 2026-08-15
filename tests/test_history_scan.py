"""User-triggered native-history scan: scoping, progress, and cancellation.

The startup reconcile is silent and bounded; this is the interruptible version a
user runs for a first import, which a real machine can make expensive. These tests
drive it against a real on-disk transcript under an injected home.
"""

from __future__ import annotations

import json
from pathlib import Path

from swe_mux.config import Config
from swe_mux.history import HistoryIndex
from swe_mux.history_scan import HistoryScanManager
from swe_mux.reconcile import ScanProgress, reconcile_external_history


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
