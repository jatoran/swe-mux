"""Incremental transcript reads for both telemetry reconcilers.

Every reconciliation pass used to parse each changed transcript whole (its last
32 MiB, for a larger one), and a working session changes every pass. These tests pin
the replacement: a cursor that continues where the last read stopped, starts over
when the file was rewritten, numbers lines from the start of the file, and leaves the
evidence a set of reads produces identical to what one whole read would.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from swe_mux import operational_telemetry, telemetry_reconcile
from swe_mux.harness import require_backend
from swe_mux.operational_telemetry import OperationalTelemetryStore, reduce_native_records
from swe_mux.telemetry_cursor import AppendedRecords, read_appended, resumed_cursor
from swe_mux.telemetry_ledger import CanonicalTelemetryLedger
from swe_mux.telemetry_reconcile import scan_to_events

BASE = 1_788_000_000.0  # 2026-08-29


def _at(offset: float) -> str:
    return datetime.fromtimestamp(BASE + offset, UTC).isoformat()


def _use(call_id: str, name: str, offset: float) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": _at(offset),
        "message": {"content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}]},
    }


def _result(call_id: str, offset: float, *, error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": _at(offset),
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": call_id, "is_error": error, "content": "x"}
            ]
        },
    }


def _line(record: dict[str, Any]) -> bytes:
    return (json.dumps(record) + "\n").encode()


def _append(path: Path, *records: dict[str, Any], partial: bytes = b"") -> None:
    with path.open("ab") as handle:
        for record in records:
            handle.write(_line(record))
        handle.write(partial)


def _row(path: Path) -> dict[str, Any]:
    return {
        "id": "run-1",
        "backend": "claude",
        "project_id": "project-1",
        "model": None,
        "transcript_path": str(path),
        "native_id": "native-1",
        "external": 0,
        "spawned_at": BASE,
        "note_id": "session-1",
    }


def _evidence_ids(root: Path) -> set[str]:
    found: set[str] = set()
    for segment in (root / "segments").glob("*.sqlite3"):
        with closing(sqlite3.connect(segment)) as connection:
            found.update(
                str(row[0])
                for row in connection.execute("SELECT evidence_id FROM telemetry_evidence")
            )
    return found


# -- the cursor -------------------------------------------------------------------


def test_an_append_is_read_from_the_cursor_and_a_partial_line_waits(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _append(path, _use("a", "Read", 1), _result("a", 2), _use("b", "Grep", 3))
    first = read_appended(path, None)
    assert first.reason == "initial"
    assert [line for line, _record in first.records] == [0, 1, 2]
    assert first.cursor.offset == path.stat().st_size
    assert first.cursor.line == 3

    complete = _line(_result("b", 4))
    _append(path, _result("b", 4), partial=b'{"type": "user", "timest')
    second = read_appended(path, first.cursor)
    assert second.reason == "append"
    assert second.start_offset == first.cursor.offset
    assert second.bytes_read == len(complete)
    assert [line for line, _record in second.records] == [3]

    # The half-written line is the only thing new: nothing to read yet.
    assert read_appended(path, second.cursor).reason == "unchanged"
    _append(path, partial=b'amp": "' + _at(5).encode() + b'"}\n')
    third = read_appended(path, second.cursor)
    assert third.reason == "append"
    assert [line for line, _record in third.records] == [4]
    unchanged = read_appended(path, third.cursor)
    assert (unchanged.reason, unchanged.bytes_read, unchanged.cursor) == (
        "unchanged",
        0,
        third.cursor,
    )


def test_a_rewritten_or_shrunk_transcript_is_read_again_from_the_start(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    _append(path, _use("a", "Read", 1), _result("a", 2))
    cursor = read_appended(path, None).cursor

    # Same length, different head: a replacement.
    path.write_bytes(_line(_use("z", "Read", 1)) + _line(_result("z", 2)))
    assert path.stat().st_size == cursor.offset
    replaced = read_appended(path, cursor)
    assert replaced.reason == "replaced" and replaced.rescanned
    assert [line for line, _record in replaced.records] == [0, 1]

    # Same head, rewritten just before the cursor (a rewind that re-appended).
    head = _line(_use("z", "Read", 1))
    path.write_bytes(head + _line(_result("z", 9)) + _line(_use("y", "Bash", 10)))
    rewound = read_appended(path, replaced.cursor)
    assert rewound.reason == "replaced"
    assert len(rewound.records) == 3

    path.write_bytes(head)
    shrunk = read_appended(path, rewound.cursor)
    assert shrunk.reason == "shrunk"
    assert [line for line, _record in shrunk.records] == [0]


def test_a_first_read_of_a_long_transcript_numbers_lines_from_the_file_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    records = [_use(f"call-{index:03d}", "Read", index) for index in range(40)]
    _append(path, *records)
    width = len(_line(records[0]))
    assert all(len(_line(record)) == width for record in records)
    whole = read_appended(path, None)
    # A window that begins exactly on a line boundary keeps that line.
    window = read_appended(path, None, tail_bytes=10 * width)
    assert window.cursor.truncated and not whole.cursor.truncated
    assert [line for line, _record in window.records] == list(range(30, 40))
    assert window.records == whole.records[30:]
    assert window.cursor.offset == whole.cursor.offset
    assert window.cursor.line == whole.cursor.line == 40
    # Mid-line, the window starts at the next record rather than half of one.
    mid = read_appended(path, None, tail_bytes=10 * width + 5)
    assert [line for line, _record in mid.records] == list(range(30, 40))

    # Identities built from those lines agree, so a record read in a truncated first
    # read and one read whole are one observation, not two.
    claude = require_backend("claude")
    events_whole = scan_to_events(
        reduce_native_records(whole.records, claude, "run-1", base_time=BASE),
        session_id="session-1",
        run_id="run-1",
    )
    events_window = scan_to_events(
        reduce_native_records(window.records, claude, "run-1", base_time=BASE, truncated=True),
        session_id="session-1",
        run_id="run-1",
    )
    assert [(item.seq, item.payload) for item in events_window] == [
        (item.seq, item.payload) for item in events_whole[30:]
    ]


def test_resumed_cursor_continues_a_whole_file_read_at_its_recorded_size(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    _append(path, _use("a", "Read", 1), _result("a", 2))
    size = path.stat().st_size
    _append(path, _use("b", "Grep", 3))
    cursor = resumed_cursor(path, size)
    assert cursor is not None
    # The line is counted only when something is appended, not for every file an
    # upgrade resumes.
    assert (cursor.offset, cursor.line, cursor.truncated) == (size, None, False)
    at_end = resumed_cursor(path, path.stat().st_size)
    assert at_end is not None
    unchanged = read_appended(path, at_end)
    assert (unchanged.reason, unchanged.cursor.line) == ("unchanged", None)
    continued = read_appended(path, cursor)
    assert continued.reason == "append"
    assert [line for line, _record in continued.records] == [2]
    assert continued.cursor.line == 3
    assert resumed_cursor(path, size - 3) is None  # mid-line
    assert resumed_cursor(path, path.stat().st_size + 1) is None  # longer than the file


# -- the canonical reconciler -------------------------------------------------------


class _Reads:
    """Every read a reconciler made: (reason, start offset, bytes read)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
        self.seen: list[tuple[str, int, int]] = []
        original = module.scan_native_telemetry_since

        def recorded(*args: Any) -> tuple[dict[str, Any], AppendedRecords]:
            scan, read = original(*args)
            self.seen.append((read.reason, read.start_offset, read.bytes_read))
            return scan, read

        monkeypatch.setattr(module, "scan_native_telemetry_since", recorded)


def test_reconciliation_parses_only_appends_and_matches_a_whole_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _Reads(monkeypatch, telemetry_reconcile)
    path = tmp_path / "t.jsonl"
    _append(path, _use("call-1", "Read", 1), _result("call-1", 2))
    ledger = CanonicalTelemetryLedger(tmp_path / "incremental")
    first = ledger.reconcile_native_rows([_row(path)])
    assert first["scanned"] == 1 and first["inserted"] > 0
    size_one = path.stat().st_size

    # The next pass sees only the call; the one after only its result, which the
    # parser cannot name without the call's record.
    _append(path, _use("call-2", "Bash", 3))
    size_two = path.stat().st_size
    assert ledger.reconcile_native_rows([_row(path)])["scanned"] == 1
    _append(path, _result("call-2", 4, error=True))
    assert ledger.reconcile_native_rows([_row(path)])["scanned"] == 1
    assert ledger.reconcile_native_rows([_row(path)]) == {
        "scanned": 0,
        "skipped": 1,
        "errors": 0,
        "inserted": 0,
    }
    assert reads.seen == [
        ("initial", 0, size_one),
        ("append", size_one, size_two - size_one),
        ("append", size_two, path.stat().st_size - size_two),
        ("unchanged", path.stat().st_size, 0),
    ]
    record = ledger.native_reconciliation_for("run-1")
    assert record is not None
    assert record["tool_events"] == 4
    assert record["recognized"] == 4
    assert record["cursor_offset"] == path.stat().st_size
    assert record["cursor_line"] == 4
    failed = next(row for row in ledger.tool_calls() if row["native_call_id"] == "call-2")
    assert (failed["raw_name"], failed["status"]) == ("Bash", "failed")

    whole = CanonicalTelemetryLedger(tmp_path / "whole")
    whole.reconcile_native_rows([_row(path)])
    assert _evidence_ids(tmp_path / "incremental") == _evidence_ids(tmp_path / "whole")
    assert record["inserted"] == len(_evidence_ids(tmp_path / "whole"))
    whole.close()
    ledger.close()


def test_a_replaced_transcript_is_reconciled_again_from_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _Reads(monkeypatch, telemetry_reconcile)
    path = tmp_path / "t.jsonl"
    _append(path, _use("call-1", "Read", 1), _result("call-1", 2), _use("call-2", "Grep", 3))
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    ledger.reconcile_native_rows([_row(path)])
    path.write_bytes(_line(_use("call-9", "Bash", 5)))
    assert ledger.reconcile_native_rows([_row(path)])["scanned"] == 1
    assert [reason for reason, _start, _read in reads.seen] == ["initial", "shrunk"]
    record = ledger.native_reconciliation_for("run-1")
    assert record is not None
    # A rescan replaces what the record says it read; it does not add to it.
    assert record["tool_events"] == 1
    assert record["cursor_offset"] == path.stat().st_size
    ledger.close()


def test_a_reconciliation_recorded_before_cursors_resumes_at_its_recorded_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _Reads(monkeypatch, telemetry_reconcile)
    path = tmp_path / "t.jsonl"
    _append(path, _use("call-1", "Read", 1), _result("call-1", 2))
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    ledger.reconcile_native_rows([_row(path)])
    size = path.stat().st_size
    # What a schema 4 catalog holds: the watermark, and no cursor.
    ledger._catalog.execute(
        "UPDATE native_reconciliations SET cursor_offset=NULL,cursor_line=NULL,"
        "cursor_head_sha256=NULL,cursor_tail_sha256=NULL"
    )
    ledger._catalog.commit()
    _append(path, _use("call-2", "Bash", 3))
    assert ledger.reconcile_native_rows([_row(path)])["scanned"] == 1
    assert reads.seen[-1] == ("append", size, path.stat().st_size - size)
    record = ledger.native_reconciliation_for("run-1")
    assert record is not None
    assert record["tool_events"] == 3
    assert record["cursor_offset"] == path.stat().st_size
    ledger.close()


# -- the legacy reconciler ----------------------------------------------------------


def _legacy_store(tmp_path: Path, path: Path) -> OperationalTelemetryStore:
    store = OperationalTelemetryStore(tmp_path / "mux.db")
    store.sessions = SimpleNamespace(sessions={})
    store.history = SimpleNamespace(
        telemetry_history_rows=AsyncMock(return_value=[_row(path)]),
        set_context_compaction_summary=AsyncMock(),
    )
    return store


def _legacy_tools(store: OperationalTelemetryStore) -> list[tuple[str, str, str]]:
    return [
        (str(row["kind"]), str(row["raw_tool"]), str(row["source"]))
        for row in store._db.execute(
            "SELECT kind,raw_tool,source FROM tool_events ORDER BY observed_at,kind"
        ).fetchall()
    ]


async def test_legacy_reconciliation_reads_appends_and_names_earlier_calls_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _Reads(monkeypatch, operational_telemetry)
    path = tmp_path / "t.jsonl"
    _append(path, _use("call-1", "Read", 1))
    store = _legacy_store(tmp_path, path)
    assert await store.reconcile_transcripts() == {"scanned": 1, "skipped": 0, "errors": 0}
    size = path.stat().st_size
    _append(path, _result("call-1", 2))
    assert await store.reconcile_transcripts() == {"scanned": 1, "skipped": 0, "errors": 0}
    assert await store.reconcile_transcripts() == {"scanned": 0, "skipped": 1, "errors": 0}
    assert [reason for reason, _start, _read in reads.seen] == ["initial", "append", "unchanged"]
    assert reads.seen[1][1] == size
    assert _legacy_tools(store) == [
        ("tool_use", "Read", "reconciled_transcript"),
        ("tool_result", "Read", "reconciled_transcript"),
    ]
    coverage = store._db.execute(
        "SELECT tool_events,recognized_records,cursor_offset,cursor_line "
        "FROM transcript_telemetry_coverage WHERE session_id='run-1'"
    ).fetchone()
    assert tuple(coverage) == (2, 2, path.stat().st_size, 2)
    # The compaction summary is written once, not on every pass that found none.
    assert store.history.set_context_compaction_summary.await_count == 1
    store.close()


async def test_legacy_result_no_pass_can_name_still_matches_its_live_twin(
    tmp_path: Path,
) -> None:
    """A hook saw the call and its result live, with no call id to join on.

    The transcript's call is skipped as the hook's duplicate, so when the result
    arrives in a later pass there is no reconciled call to name it from. It is
    matched to the hook's result by kind and time, as a whole read would have
    matched it by name, rather than filed as a second, unnamed result.
    """

    path = tmp_path / "t.jsonl"
    _append(path, _use("call-1", "Read", 1))
    store = _legacy_store(tmp_path, path)
    for kind, offset in (("tool_use", 1.0), ("tool_result", 2.0)):
        store._db.execute(
            "INSERT INTO tool_events(id,event_seq,source_identity,session_id,agent_run_id,"
            "project_id,backend,model,observed_at,source,kind,raw_tool,taxonomy,success,"
            "exit_code,duration_ms,parser_version,explicit_skill) "
            "VALUES(?,NULL,?,'run-1','run-1','project-1','claude',NULL,?,'hook',?,'Read',"
            "'read',1,NULL,NULL,'claude:2',NULL)",
            (f"hook-{kind}", f"run-1:{kind}:hook:{offset}", BASE + offset, kind),
        )
    store._db.commit()
    await store.reconcile_transcripts()
    _append(path, _result("call-1", 2))
    await store.reconcile_transcripts()
    assert _legacy_tools(store) == [
        ("tool_use", "Read", "hook"),
        ("tool_result", "Read", "hook"),
    ]
    store.close()


async def test_legacy_coverage_recorded_before_cursors_resumes_at_its_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads = _Reads(monkeypatch, operational_telemetry)
    path = tmp_path / "t.jsonl"
    _append(path, _use("call-1", "Read", 1), _result("call-1", 2))
    store = _legacy_store(tmp_path, path)
    await store.reconcile_transcripts()
    size = path.stat().st_size
    store._db.execute(
        "UPDATE transcript_telemetry_coverage SET cursor_offset=NULL,cursor_line=NULL,"
        "cursor_head_sha256=NULL,cursor_tail_sha256=NULL"
    )
    store._db.commit()
    _append(path, _use("call-2", "Bash", 3))
    assert await store.reconcile_transcripts() == {"scanned": 1, "skipped": 0, "errors": 0}
    assert reads.seen[-1] == ("append", size, path.stat().st_size - size)
    coverage = store._db.execute(
        "SELECT tool_events,cursor_offset FROM transcript_telemetry_coverage"
    ).fetchone()
    assert tuple(coverage) == (3, path.stat().st_size)
    store.close()
