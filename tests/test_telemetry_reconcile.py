"""Direct native-store reconciliation into the canonical reducer.

Every registered transcript harness has a production-shaped fixture under
`tests/fixtures/telemetry/v1/` (records with content and identities replaced), and
each is parsed by the same dialect scanner the legacy store uses and reduced into
the ledger without the legacy `tool_events` hop.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from swe_mux.harness import HARNESSES, require_backend
from swe_mux.operational_telemetry import scan_native_telemetry
from swe_mux.telemetry_ledger import CanonicalTelemetryLedger
from swe_mux.telemetry_reconcile import native_parser_version, scan_to_events

FIXTURES = Path(__file__).parent / "fixtures" / "telemetry" / "v1"
TRANSCRIPT_HARNESSES = tuple(
    name for name, harness in HARNESSES.items() if harness.transcript_dialect is not None
)


def _fixture(backend: str) -> dict[str, Any]:
    loaded = json.loads((FIXTURES / f"{backend}.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _transcript(tmp_path: Path, backend: str) -> tuple[Path, dict[str, Any]]:
    fixture = _fixture(backend)
    path = tmp_path / f"{backend}.jsonl"
    path.write_text(
        "\n".join(json.dumps(record) for record in fixture["records"]) + "\n",
        encoding="utf-8",
    )
    return path, fixture


def test_every_transcript_harness_has_a_production_shaped_fixture() -> None:
    """A harness added to the registry must bring a fixture here, or this fails."""

    missing = [name for name in TRANSCRIPT_HARNESSES if not (FIXTURES / f"{name}.json").is_file()]
    assert not missing, f"no reconciliation fixture for {missing}"
    assert set(TRANSCRIPT_HARNESSES) == {"claude", "codex", "omp", "pi", "opencode"}


@pytest.mark.parametrize("backend", TRANSCRIPT_HARNESSES)
def test_each_harness_fixture_reconciles_directly_into_the_ledger(
    tmp_path: Path, backend: str
) -> None:
    path, fixture = _transcript(tmp_path, backend)
    expected = fixture["expected"]
    source: Path | list[dict[str, Any]] = path
    if HARNESSES[backend].conversation_store_file is not None:
        # A store-backed harness hands the scanner projected records, not a file.
        source = list(fixture["records"])
    scan = scan_native_telemetry(source, require_backend(backend), "run-1", "project-1", None)
    assert len(scan["tools"]) == expected["tools"]
    assert scan["unknown"] == expected["unknown"]

    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    rows = [
        {
            "id": "run-1",
            "backend": backend,
            "project_id": "project-1",
            "model": None,
            "transcript_path": None if isinstance(source, list) else str(path),
            "native_id": "native-1",
            "external": 0,
            "spawned_at": 1_786_147_000.0,
            "note_id": "session-1",
        }
    ]
    if isinstance(source, list):
        # Feed the projected records through the same path the daemon uses for a
        # store-backed conversation, by bypassing the store lookup.
        scan["parser_version"] = native_parser_version(backend)
        events = scan_to_events(scan, session_id="session-1", run_id="run-1")
        dimensions = {
            "session_id": "session-1",
            "run_id": "run-1",
            "backend": backend,
            "project_id": "project-1",
            "origin": "mux_owned",
            "agent_id": "root",
            "run_started_at": 1_786_147_000.0,
        }
        inserted = ledger.record_events((event, dimensions) for event in events)
        assert inserted == len(events)
    else:
        summary = ledger.reconcile_native_rows(rows)
        assert summary["scanned"] == 1 and summary["errors"] == 0
        assert summary["inserted"] > 0
        # Unchanged since: the watermark and parser revision match, nothing is re-read.
        again = ledger.reconcile_native_rows(rows)
        assert again == {"scanned": 0, "skipped": 1, "errors": 0, "inserted": 0}
        record = ledger.native_reconciliation_for("run-1")
        assert record is not None
        assert record["status"] == "ready"
        assert record["parser_version"] == native_parser_version(backend)
        assert record["tool_events"] == expected["tools"]
        assert record["skill_events"] == expected["skills"]

    calls = ledger.tool_calls()
    assert calls, backend
    assert {row["request_source"] or row["result_source"] for row in calls} == {
        "reconciled_transcript"
    }
    assert {row["evidence_quality"] for row in calls} <= {"reconciled", "none"}
    if "errors" in expected:
        assert sum(1 for row in calls if row["status"] == "failed") == expected["errors"]
    assert len(ledger.skills()) == expected["skills"]
    compactions = ledger.compaction_summary(from_ts=0, to_ts=2_000_000_000, origin=None)
    assert compactions["total"] == expected["compactions"]
    run = ledger.run_audit("run-1")
    assert run is not None
    assert run["run"]["started_at_source"] == "declared"
    assert run["run"]["origin"] == "mux_owned"
    ledger.close()


def test_reconciliation_ranks_below_live_evidence_and_fills_what_it_missed(
    tmp_path: Path,
) -> None:
    """Reconciled evidence never overrides what the observer saw live."""

    from tests.test_telemetry_ledger import dimensions, event

    path, _fixture_data = _transcript(tmp_path, "claude")
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(run_id="run-1")
    dims["session_id"] = "session-1"
    # The observer saw one of the fixture's calls live, with a hook duration.
    live_scan = scan_native_telemetry(path, require_backend("claude"), "run-1", None, None)
    live_call = next(item for item in live_scan["tools"] if item["kind"] == "tool_result")
    call_id = str(live_call["source_identity"]).rsplit(":", 1)[-1]
    ledger.record_event(
        event(
            "tool_result",
            ts=float(live_call["observed_at"]),
            source="hook",
            tool="Bash",
            call_id=call_id,
            success=False,
            duration_ms=99,
        ),
        dims,
    )
    ledger.reconcile_native_rows(
        [
            {
                "id": "run-1",
                "backend": "claude",
                "project_id": "project-1",
                "model": None,
                "transcript_path": str(path),
                "native_id": "native-1",
                "external": 0,
                "spawned_at": 1_786_147_000.0,
                "note_id": "session-1",
            }
        ]
    )
    merged = next(row for row in ledger.tool_calls() if row["native_call_id"] == call_id)
    assert merged["result_source"] == "hook"
    assert merged["status"] == "failed"
    assert merged["duration_ms"] == 99
    assert merged["evidence_count"] >= 2
    ledger.close()


def test_a_missing_transcript_is_recorded_as_unavailable_not_as_empty(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    summary = ledger.reconcile_native_rows(
        [
            {
                "id": "run-gone",
                "backend": "claude",
                "project_id": None,
                "model": None,
                "transcript_path": str(tmp_path / "missing.jsonl"),
                "native_id": None,
                "external": 0,
                "spawned_at": 1.0,
                "note_id": None,
            }
        ]
    )
    assert summary == {"scanned": 0, "skipped": 0, "errors": 1, "inserted": 0}
    record = ledger.native_reconciliation_for("run-gone")
    assert record is not None and record["status"] == "unavailable"
    assert ledger.native_reconciliation_status()["runs"] == 1
    ledger.close()


def test_reconciliation_never_writes_to_the_native_transcript(tmp_path: Path) -> None:
    path, _fixture_data = _transcript(tmp_path, "codex")
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    ledger.reconcile_native_rows(
        [
            {
                "id": "run-1",
                "backend": "codex",
                "project_id": None,
                "model": None,
                "transcript_path": str(path),
                "native_id": None,
                "external": 1,
                "spawned_at": 1.0,
                "note_id": None,
            }
        ]
    )
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp
    assert {row["origin"] for row in ledger.tool_calls()} == {"imported"}
    ledger.close()


def test_the_legacy_database_is_opened_read_only_by_every_importer(tmp_path: Path) -> None:
    """Migration cannot change session history: the importers cannot write at all."""

    source = tmp_path / "mux.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE history (
              id TEXT PRIMARY KEY, note_id TEXT, native_id TEXT, backend TEXT, project_id TEXT,
              model TEXT, external INTEGER, transcript_path TEXT, spawned_at REAL, exited_at REAL,
              exit_reason TEXT, tokens_in INTEGER, tokens_out INTEGER, tokens_cache_read INTEGER,
              tokens_cache_write INTEGER, cost_usd REAL, final_context_pct REAL,
              peak_context_pct REAL, measurement_source TEXT, agent_visible INTEGER
            );
            CREATE TABLE tool_events (
              id TEXT PRIMARY KEY, event_seq INTEGER, source_identity TEXT, session_id TEXT,
              agent_run_id TEXT, project_id TEXT, backend TEXT, model TEXT, observed_at REAL,
              source TEXT, kind TEXT, raw_tool TEXT, taxonomy TEXT, success INTEGER,
              exit_code INTEGER, duration_ms REAL, parser_version TEXT, explicit_skill TEXT
            );
            CREATE TABLE context_compactions (
              id TEXT PRIMARY KEY, event_seq INTEGER, session_id TEXT, agent_run_id TEXT,
              project_id TEXT, backend TEXT, model TEXT, observed_at REAL, source TEXT,
              capability TEXT, confidence TEXT, parser_version TEXT
            );
            CREATE TABLE tier0_facts (
              id TEXT PRIMARY KEY, session_id TEXT, agent_run_id TEXT, project_id TEXT,
              kind TEXT, call_id TEXT, source_seq INTEGER, created_at REAL, detail_json TEXT
            );
            CREATE TABLE status_timeline (
              session_id TEXT, agent_run_id TEXT, ts REAL, kind TEXT, entry_json TEXT
            );
            INSERT INTO history VALUES('run-1','session-1','native-1','claude','project-1',
              'model-1',0,'native.jsonl',1788000000.0,1788000100.0,'complete',10,5,0,0,0.0,
              0.1,0.2,'transcript',1);
            INSERT INTO tool_events VALUES('t1',1,'native:session-1:tool_use:call-1','session-1',
              'run-1','project-1','claude','model-1',1788000001.0,'transcript','tool_use','Bash',
              'shell',NULL,NULL,NULL,'legacy-v1',NULL);
            INSERT INTO context_compactions VALUES('c1',2,'session-1','run-1','project-1',
              'claude','model-1',1788000002.0,'transcript','native','explicit','legacy-v1');
            INSERT INTO tier0_facts VALUES('f1','session-1','run-1','project-1','test_result',
              'call-1',3,1788000003.0,'{"test_outcome":{"framework":"pytest","passed":1,
              "failed":0,"errors":0},"tool":"Bash","success":true}');
            INSERT INTO status_timeline VALUES('session-1','run-1',1788000004.0,'transition',
              '{"previous":"idle","state":"working","turn_seq":1}');
            INSERT INTO status_timeline VALUES('session-1','run-1',1788000009.0,'transition',
              '{"previous":"working","state":"idle","turn_seq":1}');
            """
        )
    before = source.read_bytes()
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    for importer in (
        ledger.import_legacy_runs_batch,
        ledger.import_legacy_turns_batch,
        ledger.import_legacy_compactions_batch,
        ledger.import_legacy_verifications_batch,
        ledger.import_legacy_batch,
    ):
        result = importer(source, batch_size=100)
        assert result["completed"] is True
    ledger.close()
    assert source.read_bytes() == before
    assert not (tmp_path / "mux.db-wal").exists() or (tmp_path / "mux.db-wal").stat().st_size == 0
