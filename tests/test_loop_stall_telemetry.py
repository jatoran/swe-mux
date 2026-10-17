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
async def test_the_diagnosis_round_trips_and_an_oversized_one_stays_valid_json(
    tmp_path: Path,
) -> None:
    store = OperationalTelemetryStore(tmp_path / "mux.db")
    try:
        diagnosis = {
            "verdict": "gil_convoy",
            "loop_cpu_share": 0.12,
            "top_threads": [{"name": "asyncio_3", "ident": 7, "cpu_seconds": 2.5, "share": 0.6}],
            "gil_holder": {"source": "cpu", "name": "asyncio_3"},
        }
        await store.record_loop_stall(
            {
                "started_at": time.time(),
                "duration_seconds": 4.0,
                "busy_threads": [{"name": "w", "ident": 1, "frames": [], "cpu_seconds": 1.25}],
                "diagnosis": diagnosis,
            }
        )
        # Far over the column budget: must be trimmed to a parseable object, not cut.
        huge = {
            "verdict": "loop_saturated",
            "blocked_threads": [f"thread-{i}-" + "x" * 80 for i in range(500)],
        }
        await store.record_loop_stall(
            {"started_at": time.time() + 1, "duration_seconds": 5.0, "diagnosis": huge}
        )
        newest, older = await store.recent_loop_stalls()
        assert older["diagnosis"] == diagnosis
        assert older["busy_threads"][0]["cpu_seconds"] == 1.25
        assert newest["diagnosis"]["verdict"] == "loop_saturated", (
            "an oversized diagnosis keeps its verdict instead of reading back as {}"
        )
        assert 0 < len(newest["diagnosis"]["blocked_threads"]) < 500
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_pre_schema_10_stall_table_gains_the_diagnosis_column(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "mux.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE loop_stalls (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL NOT NULL,"
        "duration_seconds REAL NOT NULL, canary_starved INTEGER NOT NULL, dumps INTEGER NOT NULL,"
        "main_leaf TEXT, main_frames_json TEXT NOT NULL, busy_threads_json TEXT NOT NULL,"
        "host_json TEXT NOT NULL, trace_path TEXT)"
    )
    db.execute(
        "INSERT INTO loop_stalls(started_at,duration_seconds,canary_starved,dumps,"
        "main_frames_json,busy_threads_json,host_json) VALUES(?,?,?,?,?,?,?)",
        (time.time(), 6.0, 0, 1, "[]", "[]", "{}"),
    )
    db.commit()
    db.close()
    store = OperationalTelemetryStore(path)
    try:
        (row,) = await store.recent_loop_stalls()
        assert row["duration_seconds"] == pytest.approx(6.0)
        assert row["diagnosis"] == {}, "a stall recorded before verdicts existed has none"
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
