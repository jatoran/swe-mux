"""An explained loop stall is kept durably, bounded, and pruned with the rest."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from swe_mux.operational_telemetry import OperationalTelemetryStore
from swe_mux.stall_watchdog import StallRecord


@pytest.mark.asyncio
async def test_a_stall_record_round_trips_through_the_store(tmp_path: Path) -> None:
    store = OperationalTelemetryStore(tmp_path / "mux.db")
    try:
        record = StallRecord(
            started_at=time.time() - 50,
            duration_seconds=49.84,
            canary_starved=True,
            dumps=16,
            main_thread=["_poll (windows_events.py:774)", "select (windows_events.py:445)"],
            busy_threads=[
                {
                    "name": "asyncio_7",
                    "ident": 0x1234,
                    "frames": ["_collect_all (processes.py:1446)"],
                }
            ],
            host={"cpu_percent": 91.5, "memory_percent": 61.0, "process_count": 512},
            trace_path=str(tmp_path / "loop-stalls.log"),
        )
        await store.record_loop_stall(record.as_dict())
        rows = await store.recent_loop_stalls(limit=5)
        assert len(rows) == 1
        row = rows[0]
        assert row["duration_seconds"] == pytest.approx(49.84)
        assert row["canary_starved"] is True
        assert row["dumps"] == 16
        assert row["main_leaf"] == "_poll (windows_events.py:774)"
        assert row["main_thread"] == record.main_thread
        assert row["busy_threads"][0]["frames"] == ["_collect_all (processes.py:1446)"]
        assert row["host"]["process_count"] == 512
        assert row["trace_path"] == record.trace_path
    finally:
        store.close()


@pytest.mark.asyncio
async def test_frames_are_bounded_and_a_zero_duration_is_refused(tmp_path: Path) -> None:
    store = OperationalTelemetryStore(tmp_path / "mux.db")
    try:
        await store.record_loop_stall(
            {
                "started_at": time.time(),
                "duration_seconds": 4.0,
                "main_thread": [f"frame{i} (x.py:{i})" for i in range(200)],
                "busy_threads": [{"name": "w", "ident": 1, "frames": ["a (b.py:1)"]}] * 40,
            }
        )
        row = (await store.recent_loop_stalls())[0]
        assert len(row["main_thread"]) == 40
        assert len(row["busy_threads"]) == 12
        with pytest.raises(ValueError, match="duration_seconds"):
            await store.record_loop_stall({"started_at": time.time(), "duration_seconds": 0})
    finally:
        store.close()


@pytest.mark.asyncio
async def test_old_stalls_are_pruned_on_the_telemetry_retention_window(tmp_path: Path) -> None:
    store = OperationalTelemetryStore(tmp_path / "mux.db", retention_days=1)
    try:
        await store.record_loop_stall(
            {"started_at": time.time() - 3 * 86400, "duration_seconds": 5.0}
        )
        await store.record_loop_stall({"started_at": time.time(), "duration_seconds": 5.0})
        deleted = await store.prune()
        assert deleted["loop_stalls"] == 1
        assert len(await store.recent_loop_stalls()) == 1
    finally:
        store.close()
