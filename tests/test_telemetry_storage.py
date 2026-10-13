"""Segment seals, last-write stamps, refused times, and the ledger's thread split.

The loop these guard against: a month that kept receiving late entity evidence was
resealed - a full integrity check and SHA-256 of a multi-gigabyte file - after every
write that unsealed it, because a seal was judged by how old the month's evidence
was rather than by when its file was last written, and evidence that turned out to
be a duplicate unsealed it anyway. A zero timestamp opened a `1970-01` segment that
went through the same loop fifteen thousand times.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import telemetry_ledger
from swe_mux.event_bus import EventBus
from swe_mux.telemetry_ledger import (
    SEAL_FAILURE_BACKOFF_SECONDS,
    CanonicalTelemetryLedger,
    LedgerShared,
)
from swe_mux.telemetry_service import CanonicalTelemetryService
from tests.support.settle import until
from tests.test_telemetry_ledger import dimensions, event

DAY = 1_767_225_600.0  # 2026-01-01T00:00:00Z
FEB = 1_769_904_000.0  # 2026-02-01T00:00:00Z
WEEK = 7 * 86400


class Clock:
    """A ledger clock a test moves by hand."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _catalog_rows(root: Path, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(root / "catalog.sqlite3")) as catalog:
        catalog.row_factory = sqlite3.Row
        return catalog.execute(sql, args).fetchall()


def _segment_row(root: Path, period: str) -> sqlite3.Row:
    [row] = _catalog_rows(root, "SELECT * FROM ledger_segments WHERE period=?", (period,))
    return row


def _rebuild_all_days(ledger: CanonicalTelemetryLedger, now: float) -> None:
    while ledger.rebuild_next_closed_day(now=now) is not None:
        pass


def test_a_segment_seals_only_after_a_grace_period_with_no_writes(tmp_path: Path) -> None:
    root = tmp_path / "telemetry"
    clock = Clock(DAY + 60)
    ledger = CanonicalTelemetryLedger(root, clock=clock)
    ledger.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dimensions())
    # January's evidence is old by February 20th, but its file is written then.
    written = FEB + 19 * 86400
    clock.now = written
    ledger.record_event(event("tool_use", ts=DAY + 20, tool="Grep", call_id="call-2"), dimensions())
    _rebuild_all_days(ledger, FEB)

    clock.now = written + WEEK - 1
    assert ledger.seal_next_segment(grace_days=7) is None
    clock.now = written + WEEK + 1
    sealed = ledger.seal_next_segment(grace_days=7)
    assert sealed is not None
    assert sealed["period"] == "2026-01"
    path = root / "segments" / "2026-01.sqlite3"
    assert sealed["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    row = _segment_row(root, "2026-01")
    assert row["last_written_at"] == written
    assert row["sealed_at"] == sealed["sealed_at"]
    assert ledger.seals == 1
    assert ledger.last_seal_seconds is not None
    ledger.close()


def test_evidence_already_recorded_neither_unseals_nor_restamps_its_segment(
    tmp_path: Path,
) -> None:
    root = tmp_path / "telemetry"
    clock = Clock(DAY + 60)
    ledger = CanonicalTelemetryLedger(root, clock=clock)
    observation = event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1")
    ledger.record_event(observation, dimensions())
    _rebuild_all_days(ledger, DAY + 86400)
    clock.now = DAY + 10 * 86400
    sealed = ledger.seal_next_segment()
    assert sealed is not None

    # A reconciliation reading the same record again: nothing is written.
    clock.now = DAY + 11 * 86400
    assert ledger.record_events([(observation, dimensions())]) == 0
    row = _segment_row(root, "2026-01")
    assert (row["sealed_at"], row["sha256"]) == (sealed["sealed_at"], sealed["sha256"])
    assert row["last_written_at"] == DAY + 60
    assert ledger.seal_invalidations == 0
    seals = _catalog_rows(root, "SELECT invalidated_at FROM segment_seals")
    assert [tuple(item) for item in seals] == [(None,)]
    assert _catalog_rows(root, "SELECT day FROM rollup_dirty_days") == []
    ledger.close()


def test_late_entity_evidence_unseals_its_home_month_and_waits_out_the_grace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "telemetry"
    clock = Clock(DAY + 60)
    ledger = CanonicalTelemetryLedger(root, clock=clock)
    dims = dimensions()
    ledger.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dims)
    _rebuild_all_days(ledger, DAY + 86400)
    clock.now = DAY + 10 * 86400
    assert ledger.seal_next_segment() is not None

    # February: the result arrives for a call whose row lives in January.
    written = FEB + 3600
    clock.now = written
    ledger.record_event(
        event("tool_result", ts=FEB + 60, tool="Read", call_id="call-1", success=True), dims
    )
    january = _segment_row(root, "2026-01")
    assert january["sealed_at"] is None
    assert january["last_written_at"] == written
    reasons = _catalog_rows(
        root, "SELECT invalidation_reason FROM segment_seals WHERE period='2026-01'"
    )
    assert [row["invalidation_reason"] for row in reasons] == ["late entity evidence"]
    assert ledger.seal_invalidations == 1

    _rebuild_all_days(ledger, FEB + 2 * 86400)
    clock.now = written + WEEK - 60
    assert ledger.seal_next_segment() is None
    clock.now = written + WEEK + 60
    first = ledger.seal_next_segment()
    second = ledger.seal_next_segment()
    assert first is not None and second is not None
    assert [first["period"], second["period"]] == ["2026-01", "2026-02"]
    assert ledger.seal_next_segment() is None
    assert ledger.seals == 3
    ledger.close()


def test_a_write_during_the_seal_checks_wins_over_the_seal(tmp_path: Path) -> None:
    root = tmp_path / "telemetry"
    shared = LedgerShared()
    clock = Clock(DAY + 60)
    writer = CanonicalTelemetryLedger(root, shared=shared, clock=clock)
    dims = dimensions()
    writer.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dims)
    _rebuild_all_days(writer, DAY + 86400)

    class WrittenDuringTheChecks(CanonicalTelemetryLedger):
        def _seal_candidate(self, cutoff: float) -> sqlite3.Row | None:
            row = super()._seal_candidate(cutoff)
            clock.now += 1
            writer.record_event(
                event("tool_use", ts=DAY + 30, tool="Grep", call_id="call-2"), dims
            )
            return row

    clock.now = DAY + 10 * 86400
    sealer = WrittenDuringTheChecks(root, shared=shared, clock=clock)
    assert sealer.seal_next_segment() is None
    row = _segment_row(root, "2026-01")
    assert row["sealed_at"] is None
    assert row["last_written_at"] == DAY + 10 * 86400 + 1
    assert _catalog_rows(root, "SELECT * FROM segment_seals") == []
    assert sealer.seals == 0
    sealer.close()
    writer.close()


class _ReportsCorruption:
    """A segment connection whose integrity check fails, counting each check."""

    def __init__(self, connection: sqlite3.Connection, checks: list[str]) -> None:
        self._connection = connection
        self._checks = checks

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        if sql == "PRAGMA quick_check":
            self._checks.append(sql)
            return self._connection.execute("SELECT '*** in database main *** Page 7 corrupt'")
        return self._connection.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def test_a_failed_integrity_check_backs_off_instead_of_rereading_every_tick(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "telemetry"
    clock = Clock(DAY + 60)
    checks: list[str] = []

    class Corrupt(CanonicalTelemetryLedger):
        def _segment(self, period: str) -> Any:
            return _ReportsCorruption(super()._segment(period), checks)

    ledger = Corrupt(root, clock=clock)
    ledger.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dimensions())
    _rebuild_all_days(ledger, DAY + 86400)
    clock.now = DAY + 10 * 86400
    with caplog.at_level(logging.ERROR, logger="swe_mux.telemetry_ledger"):
        assert ledger.seal_next_segment() is None
    assert "2026-01 failed quick_check" in caplog.text
    assert len(checks) == 1

    clock.now += 3600
    assert ledger.seal_next_segment() is None
    assert len(checks) == 1
    clock.now += SEAL_FAILURE_BACKOFF_SECONDS
    assert ledger.seal_next_segment() is None
    assert len(checks) == 2
    assert _segment_row(root, "2026-01")["sealed_at"] is None
    ledger.close()


def test_a_seal_is_deferred_while_a_reader_pins_the_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(telemetry_ledger, "_BUSY_TIMEOUT_SECONDS", 0.05)
    root = tmp_path / "telemetry"
    clock = Clock(DAY + 60)
    ledger = CanonicalTelemetryLedger(root, clock=clock)
    ledger.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dimensions())
    _rebuild_all_days(ledger, DAY + 86400)
    clock.now = DAY + 10 * 86400
    with closing(sqlite3.connect(root / "segments" / "2026-01.sqlite3")) as reader:
        reader.execute("BEGIN")
        assert reader.execute("SELECT COUNT(*) FROM telemetry_evidence").fetchone() == (1,)
        assert ledger.seal_next_segment() is None
        assert _segment_row(root, "2026-01")["sealed_at"] is None
        reader.rollback()
    assert ledger.seal_next_segment() is not None
    ledger.close()


def test_implausible_times_are_refused_counted_and_never_open_a_segment(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "telemetry"
    clock = Clock(1_788_000_000.0)  # 2026-08-29
    ledger = CanonicalTelemetryLedger(root, clock=clock)
    dims = dimensions()
    with caplog.at_level(logging.WARNING, logger="swe_mux.telemetry_ledger"):
        inserted = ledger.record_events(
            [
                (event("tool_use", ts=0.0, tool="Read", call_id="epoch"), dims),
                (event("tool_use", ts=1_000_000_000.0, tool="Read", call_id="2001"), dims),
                (event("tool_use", ts=float("nan"), tool="Read", call_id="nan"), dims),
                (event("tool_use", ts=clock.now + 3 * 86400, tool="Read", call_id="ahead"), dims),
                (event("tool_use", ts=clock.now, tool="Read", call_id="now"), dims),
            ]
        )
        ledger.record_event(event("tool_use", ts=0.0, tool="Grep", call_id="again"), dims)
    assert inserted == 1
    assert sorted(path.stem for path in (root / "segments").glob("*.sqlite3")) == ["2026-08"]
    assert ledger.rejected_observations == 5
    summary = {
        (row["reason"], row["event_type"], row["source_kind"]): (
            row["occurrences"],
            row["last_claimed_at"],
        )
        for row in ledger.rejected_observation_summary()
    }
    assert summary == {
        ("before_floor", "tool_use", "transcript"): (4, 0.0),
        ("in_future", "tool_use", "transcript"): (1, clock.now + 3 * 86400),
    }
    warnings = [record for record in caplog.records if "implausible time" in record.getMessage()]
    assert len(warnings) == 1
    assert ledger.storage_status()["rejected_observations"] == 5
    ledger.close()


def test_storage_status_reads_the_catalog_and_two_directory_listings(tmp_path: Path) -> None:
    root = tmp_path / "telemetry"
    ledger = CanonicalTelemetryLedger(root, clock=Clock(DAY + 60))
    ledger.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dimensions())
    # A month an epoch-zero producer opened before times were checked stays on
    # disk, and must not become the ledger's oldest observation.
    with closing(sqlite3.connect(root / "catalog.sqlite3")) as catalog:
        catalog.execute(
            "INSERT INTO ledger_segments(period,relative_path,first_observed_at,last_observed_at) "
            "VALUES('1970-01','segments/1970-01.sqlite3',0,5)"
        )
        catalog.commit()
    status = ledger.storage_status()
    expected = sum(
        entry.stat().st_size
        for directory in (root, root / "segments")
        for entry in directory.iterdir()
        if entry.is_file()
    )
    assert status["bytes"] == expected
    assert status["oldest_at"] == DAY + 10
    assert status["segments"] == 2
    assert status["rejected_observations"] == 0
    ledger.close()


def test_ingestion_proceeds_during_a_rollup_read_and_the_day_it_dirtied_stays_dirty(
    tmp_path: Path,
) -> None:
    root = tmp_path / "telemetry"
    shared = LedgerShared()
    clock = Clock(DAY + 60)
    dims = dimensions()
    live = CanonicalTelemetryLedger(root, shared=shared, clock=clock)
    live.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dims)
    reading = threading.Event()
    release = threading.Event()

    class PausedInItsRead(CanonicalTelemetryLedger):
        def _bucket_rows(
            self, period: str, start: float, end: float
        ) -> tuple[list[tuple[Any, ...]], list[dict[str, Any]], list[tuple[Any, ...]]]:
            rows = super()._bucket_rows(period, start, end)
            reading.set()
            release.wait(30)
            return rows

    maintenance = PausedInItsRead(root, shared=shared, clock=clock)
    rebuilt: list[str | None] = []
    rollup = threading.Thread(
        target=lambda: rebuilt.append(maintenance.rebuild_next_closed_day(now=DAY + 86400))
    )
    rollup.start()
    try:
        assert reading.wait(30)
        clock.now = DAY + 120
        written = threading.Event()

        def ingest() -> None:
            live.record_event(
                event("tool_use", ts=DAY + 30, tool="Grep", call_id="call-2"), dims
            )
            written.set()

        writer = threading.Thread(target=ingest)
        writer.start()
        # The live write completes while the rollup still sits inside its read.
        assert written.wait(30)
        writer.join(30)
    finally:
        release.set()
        rollup.join(30)
    assert rebuilt == ["2026-01-01"]
    # The rollup read the day before call-2 arrived, so the day keeps its mark ...
    dirty = _catalog_rows(root, "SELECT day,dirtied_at FROM rollup_dirty_days")
    assert [tuple(row) for row in dirty] == [("2026-01-01", DAY + 120)]
    # ... and the next rebuild takes it.
    assert maintenance.rebuild_next_closed_day(now=DAY + 86400) == "2026-01-01"
    assert _catalog_rows(root, "SELECT day FROM rollup_dirty_days") == []
    maintenance.close()
    live.close()


def test_a_version_four_catalog_gains_write_stamps_and_reconciliation_cursors(
    tmp_path: Path,
) -> None:
    root = tmp_path / "telemetry"
    clock = Clock(FEB + 86400)
    ledger = CanonicalTelemetryLedger(root, clock=clock)
    dims = dimensions()
    ledger.record_event(event("tool_use", ts=DAY + 10, tool="Read", call_id="call-1"), dims)
    ledger.record_event(event("tool_use", ts=FEB + 10, tool="Read", call_id="call-2"), dims)
    _rebuild_all_days(ledger, FEB + 2 * 86400)
    clock.now = FEB + 9 * 86400 + 3600
    sealed = ledger.seal_next_segment()
    assert sealed is not None and sealed["period"] == "2026-01"
    ledger.close()
    cursor_columns = (
        "cursor_offset",
        "cursor_line",
        "cursor_head_sha256",
        "cursor_tail_sha256",
        "cursor_truncated",
    )
    with closing(sqlite3.connect(root / "catalog.sqlite3")) as catalog:
        catalog.execute("ALTER TABLE ledger_segments DROP COLUMN last_written_at")
        for column in cursor_columns:
            catalog.execute(f"ALTER TABLE native_reconciliations DROP COLUMN {column}")
        catalog.execute("UPDATE ledger_meta SET value='4' WHERE key='schema_version'")
        catalog.commit()

    before = time.time()
    reopened = CanonicalTelemetryLedger(root, clock=clock)
    after = time.time()
    assert reopened.schema_status()["migrations"]["catalog"] == {"found": 4, "applied": 7}
    january = _segment_row(root, "2026-01")
    february = _segment_row(root, "2026-02")
    # A sealed month was last written no later than its seal; anything else waits
    # one grace period from the upgrade before its first seal.
    assert january["last_written_at"] == january["sealed_at"] == sealed["sealed_at"]
    assert before <= february["last_written_at"] <= after
    columns = _catalog_rows(root, "PRAGMA table_info(native_reconciliations)")
    assert set(cursor_columns) <= {row["name"] for row in columns}
    reopened.close()


async def test_the_service_ingests_and_answers_while_maintenance_is_busy(
    tmp_path: Path,
) -> None:
    record = SimpleNamespace(
        id="session-1",
        agent_run_id="run-1",
        agent_run_started_at=1_788_000_000.0,
        created_at=1_788_000_000.0,
        native_session_id="native-1",
        active_turn_id="turn-1",
        project_id="project-1",
        backend="claude",
        model="model-1",
    )
    session = SimpleNamespace(record=record, transcript_path=Path("native.jsonl"))
    service = CanonicalTelemetryService(tmp_path / "telemetry")
    events = EventBus(clock=lambda: 1_788_000_000.0)
    service.start(events, sessions=SimpleNamespace(sessions={"session-1": session}))
    gate = threading.Event()
    busy = asyncio.get_running_loop().run_in_executor(
        service._maintenance_executor, gate.wait, 30
    )
    try:
        await events.emit(
            "tool_use", session_id="session-1", source="transcript", tool="Read", call_id="call-1"
        )
        await until(
            lambda: service.health()["accepted"] == 1,
            seconds=10,
            what="ingestion while the maintenance thread is busy",
        )
        audit = await service.run_audit("run-1")
        assert audit is not None
    finally:
        gate.set()
        await busy
        await service.stop()
        service.close()
    health = service.health()
    for key in ("seals", "seal_invalidations", "rejected_observations", "last_seal_seconds"):
        assert key in health
