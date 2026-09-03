"""Migrations, provenance fills, rollups, exports, filters, and catch-up imports."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from swe_mux.telemetry_ledger import CanonicalTelemetryLedger
from swe_mux.telemetry_schema import (
    LEDGER_SCHEMA_VERSION,
    SEGMENT_MIGRATIONS,
    LedgerSchemaError,
)
from tests.test_telemetry_ledger import dimensions, event, figures

DAY = 1_767_225_600.0  # 2026-01-01T00:00:00Z
WINDOW = {"from_ts": 1_787_999_000, "to_ts": 1_788_001_000}
_V1_MISSING_CATALOG_TABLES = (
    "workload_daily",
    "parser_signatures",
    "tool_hourly",
    "workload_hourly",
    "skill_daily",
    "verification_daily",
    "compaction_daily",
    "rollup_hours",
    "rollup_dirty_hours",
    "finding_reviews",
    "native_reconciliations",
)
_SEGMENT_STEPS = [
    step
    for version in sorted(SEGMENT_MIGRATIONS)
    for step in SEGMENT_MIGRATIONS[version]
]


def _drop_to_v1_shape(root: Path) -> None:
    """Rewrite a fresh ledger into the 2026-09-02 (version 1) shape."""

    with sqlite3.connect(root / "catalog.sqlite3") as catalog:
        catalog.execute("DELETE FROM ledger_meta WHERE key='schema_version'")
        for table in _V1_MISSING_CATALOG_TABLES:
            catalog.execute(f"DROP TABLE {table}")
    for segment in (root / "segments").glob("*.sqlite3"):
        with sqlite3.connect(segment) as connection:
            connection.execute("DROP TABLE segment_meta")
            connection.execute("DROP TABLE telemetry_provider_metrics")
            for step in _SEGMENT_STEPS:
                if isinstance(step, str):
                    tokens = step.split()
                    connection.execute(f"ALTER TABLE {tokens[2]} DROP COLUMN {tokens[5]}")


def test_a_version_one_ledger_is_migrated_additively_on_open(tmp_path: Path) -> None:
    root = tmp_path / "telemetry"
    ledger = CanonicalTelemetryLedger(root)
    ledger.record_event(event("tool_use", tool="Read", call_id="call-1"), dimensions())
    ledger.close()
    _drop_to_v1_shape(root)
    [segment] = (root / "segments").glob("*.sqlite3")
    with sqlite3.connect(segment) as old:
        old_columns = {row[1] for row in old.execute("PRAGMA table_info(telemetry_tool_calls)")}
    assert "output_truncated" not in old_columns
    assert "evidence_quality" not in old_columns

    reopened = CanonicalTelemetryLedger(root)
    status = reopened.schema_status()
    assert status["version"] == LEDGER_SCHEMA_VERSION == 3
    assert status["drift"] == []
    assert status["migrations"][segment.stem] == {
        "found": 0,
        "applied": len(_SEGMENT_STEPS),
    }
    # The v3 backfill classified the surviving v1 row from its result provenance.
    assert reopened.tool_calls()[0]["evidence_quality"] == "none"
    # The migrated file accepts the current write path and keeps the old row.
    reopened.record_event(
        event(
            "canonical_tool_result",
            ts=1_788_000_001,
            source="otel",
            tool="Read",
            call_id="call-1",
            success=True,
            output_truncated=True,
            provider_sequence=7,
        ),
        dimensions(),
    )
    row = reopened.tool_calls()[0]
    assert row["status"] == "succeeded"
    assert row["output_truncated"] == 1
    assert row["provider_sequence"] == 7
    reopened.close()
    # A second open finds version 2 and applies nothing.
    again = CanonicalTelemetryLedger(root)
    assert again.schema_status()["migrations"] == {}
    again.close()


def test_a_newer_ledger_file_is_refused_rather_than_misread(tmp_path: Path) -> None:
    root = tmp_path / "telemetry"
    CanonicalTelemetryLedger(root).close()
    with sqlite3.connect(root / "catalog.sqlite3") as catalog:
        catalog.execute("UPDATE ledger_meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(LedgerSchemaError):
        CanonicalTelemetryLedger(root)


def test_provider_result_fills_size_measurement_and_harness_version(tmp_path: Path) -> None:
    """A later native result completes a row a transcript request opened.

    The measurement is the description of the value, so the row that had no
    output size and said so takes the provider's size *and* its measurement,
    rather than a size labelled `unknown`.
    """

    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    dims["harness_version"] = None
    ledger.record_event(event("tool_use", tool="Read", call_id="call-1", target="a.py"), dims)
    ledger.record_event(
        event(
            "canonical_tool_result",
            ts=1_788_000_001,
            source="otel",
            tool="Read",
            call_id="call-1",
            success=True,
            duration_ms=7,
            harness_version="2.1.259",
            output_bytes=14,
            output_measurement="provider_size_only",
            executed_input_bytes=88,
            executed_input_sha256="executed-hash",
            executed_input_measurement="full",
        ),
        dims,
    )

    row = ledger.tool_calls()[0]
    assert row["harness_version"] == "2.1.259"
    assert row["output_bytes"] == 14
    assert row["output_measurement"] == "provider_size_only"
    assert row["output_source"] == "otel"
    # The requested target and the executed arguments are two hashes, kept apart.
    assert row["input_sha256"] == hashlib.sha256(b"a.py").hexdigest()
    assert row["executed_input_sha256"] == "executed-hash"
    assert row["executed_input_measurement"] == "full"
    assert row["executed_input_source"] == "otel"
    quality = ledger.quality_summary(**WINDOW)["totals"]
    assert quality["with_executed_input_hash"] == 1
    assert quality["with_provider_result"] == 1
    assert quality["with_harness_version"] == 1
    ledger.close()


def test_codex_native_runtime_layer_merges_with_the_hook_entity(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(backend="codex")
    ledger.record_event(
        event("tool_use", source="hook", tool="exec_command", call_id="exec-1"), dims
    )
    ledger.record_event(
        event(
            "canonical_tool_result",
            ts=1_788_000_001,
            source="otel",
            tool="exec_command",
            call_id="exec-1",
            invocation_layer="runtime",
            success=True,
            duration_ms=842,
        ),
        dims,
    )
    rows = ledger.tool_calls()
    assert len(rows) == 1
    assert rows[0]["invocation_layer"] == "runtime"
    assert rows[0]["duration_ms"] == 842
    assert rows[0]["result_source"] == "otel"
    ledger.close()


def test_cumulative_skill_counter_restart_counts_the_whole_new_value(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(backend="codex")
    for index, (count, start) in enumerate(((3, "100"), (5, "100"), (2, "200"))):
        ledger.record_event(
            event(
                "canonical_skill_invoked",
                ts=1_788_000_000 + index,
                source="otel",
                skill="openai-docs",
                invocation_id=f"point-{index}",
                invocation_trigger="implicit",
                count=count,
                metric_temporality="cumulative",
                metric_series_id="series-1",
                metric_start_time=start,
            ),
            dims,
        )
    summary = ledger.skill_summary(**WINDOW)
    # 3, then +2 against the same series start, then a restart worth 2 on its own.
    assert summary["matching_invocations"] == 7
    ledger.close()


def test_imported_history_is_a_separate_cohort_from_mux_owned_activity(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    owned = dimensions(run_id="run-owned")
    imported = dimensions(run_id="run-imported")
    imported["origin"] = "imported"
    ledger.record_event(event("tool_use", tool="Read", call_id="owned-1"), owned)
    ledger.record_event(
        event("tool_use", ts=1_788_000_001, tool="Read", call_id="imported-1"), imported
    )
    ledger.record_event(
        event("context_compacted", ts=1_788_000_002, compaction_id="c-1"), imported
    )

    assert ledger.tool_summary(**WINDOW)["matching_calls"] == 1
    assert ledger.tool_summary(**WINDOW, origin=None)["matching_calls"] == 2
    assert ledger.tool_summary(**WINDOW, origin="imported")["matching_calls"] == 1
    assert ledger.workload_summary(**WINDOW)["dimensions"][0]["origin"] == "mux_owned"
    assert len(ledger.workload_summary(**WINDOW, origin=None)["dimensions"]) == 2
    assert ledger.compaction_summary(**WINDOW)["total"] == 0
    assert ledger.compaction_summary(**WINDOW, origin=None)["total"] == 1
    page = ledger.tool_page(**WINDOW, limit=10)
    assert [item["native_call_id"] for item in page["items"]] == ["owned-1"]
    ledger.close()


def test_filters_apply_identically_to_raw_days_and_rolled_up_days(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    for index, backend in enumerate(("claude", "codex", "claude")):
        dims = dimensions(backend=backend, run_id=f"run-{backend}")
        ledger.record_event(
            event("turn_started", ts=DAY + index, turn_id=f"turn-{index}"), dims
        )
        ledger.record_event(
            event("tool_use", ts=DAY + 10 + index, tool="Read", call_id=f"c-{index}"),
            dims,
        )
        ledger.record_event(
            event(
                "tool_result",
                ts=DAY + 20 + index,
                tool="Read",
                call_id=f"c-{index}",
                success=True,
                duration_ms=5,
            ),
            dims,
        )
    window = {"from_ts": DAY, "to_ts": DAY + 86400}
    raw_tools = ledger.tool_summary(**window, filters={"backend": "claude"})
    raw_workload = ledger.workload_summary(**window, filters={"backend": "claude"})
    assert raw_tools["matching_calls"] == 2
    assert [row["backend"] for row in raw_workload["dimensions"]] == ["claude"]
    assert raw_workload["dimensions"][0]["runs"] == 1
    assert raw_workload["dimensions"][0]["model_tool_calls"] == 2

    assert ledger.rebuild_next_closed_day(now=DAY + 2 * 86400) == "2026-01-01"
    assert figures(ledger.tool_summary(**window, filters={"backend": "claude"})) == figures(
        raw_tools
    )
    assert figures(
        ledger.workload_summary(**window, filters={"backend": "claude"})
    ) == figures(raw_workload)
    assert ledger.workload_summary(**window, filters={"backend": "pi"})["dimensions"] == []
    ledger.close()


def test_workload_rollup_is_rebuilt_when_a_run_ends_on_a_later_day(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    dims["run_started_at"] = DAY + 100
    ledger.record_event(event("turn_started", ts=DAY + 100, turn_id="turn-1"), dims)
    window = {"from_ts": DAY, "to_ts": DAY + 86400}
    assert ledger.rebuild_next_closed_day(now=DAY + 3 * 86400) == "2026-01-01"
    rolled = ledger.workload_summary(**window)["dimensions"][0]
    assert rolled["runs"] == 1
    assert rolled["ended_runs"] == 0
    assert rolled["completed_turns"] == 0

    # The run ends the next day: its rollup day is the day it started.
    ledger.record_event(
        event("turn_ended", ts=DAY + 86400 + 50, turn_id="turn-1", duration_ms=1000), dims
    )
    ledger.record_event(event("session_exited", ts=DAY + 86400 + 60, reason="complete"), dims)
    dirty = ledger.workload_summary(**window)["dimensions"][0]
    assert dirty["ended_runs"] == 1
    assert dirty["completed_turns"] == 1
    assert dirty["average_wall_duration_s"] == 86400 + 60 - 100
    assert ledger.rebuild_next_closed_day(now=DAY + 3 * 86400) == "2026-01-01"
    assert ledger.rebuild_next_closed_day(now=DAY + 3 * 86400) == "2026-01-02"
    assert ledger.workload_summary(**window)["dimensions"][0] == dirty
    ledger.close()


def test_exports_page_ascending_across_segments_with_provenance(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    january = 1_769_000_000.0
    february = 1_771_500_000.0
    for index, ts in enumerate((january, january + 1, february)):
        ledger.record_event(
            event("tool_use", ts=ts, tool="Read", call_id=f"call-{index}"), dims
        )
    window = {"from_ts": january - 1, "to_ts": february + 1}
    first = ledger.export_page(kind="tool_calls", limit=2, **window)
    second = ledger.export_page(
        kind="tool_calls", limit=2, cursor=first["next_cursor"], **window
    )

    assert [item["native_call_id"] for item in first["items"]] == ["call-0", "call-1"]
    assert first["next_cursor"]
    assert [item["native_call_id"] for item in second["items"]] == ["call-2"]
    assert second["next_cursor"] is None
    evidence = ledger.export_page(kind="evidence", limit=10, **window)["items"]
    assert len(evidence) == 3
    assert all(item["source_locator"] for item in evidence)
    with pytest.raises(ValueError):
        ledger.export_page(kind="secrets", limit=1, **window)
    ledger.close()


def test_parser_signatures_are_kept_per_harness_version(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    ledger.record_parser_signatures(
        backend="claude",
        harness_version="2.1.259",
        parser_version="otlp-json-v2",
        signatures={("tool_result", True): 2, ("future_event", False): 1},
        now=1_788_000_000,
    )
    ledger.record_parser_signatures(
        backend="claude",
        harness_version="2.1.259",
        parser_version="otlp-json-v2",
        signatures={("future_event", False): 4},
        now=1_788_000_100,
    )
    rows = {
        (row["event_name"], row["recognized"]): row for row in ledger.parser_signatures()
    }
    assert rows[("tool_result", 1)]["occurrences"] == 2
    assert rows[("future_event", 0)]["occurrences"] == 5
    assert rows[("future_event", 0)]["last_seen_at"] == 1_788_000_100
    assert ledger.quality_summary(from_ts=0, to_ts=1)["parsers"]
    ledger.close()


def test_legacy_import_keeps_catching_up_after_it_completes(tmp_path: Path) -> None:
    source = tmp_path / "mux.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE history (
          id TEXT PRIMARY KEY, external INTEGER, native_id TEXT,
          transcript_path TEXT, spawned_at REAL
        );
        CREATE TABLE tool_events (
          id TEXT PRIMARY KEY, event_seq INTEGER, source_identity TEXT,
          session_id TEXT, agent_run_id TEXT, project_id TEXT, backend TEXT,
          model TEXT, observed_at REAL, source TEXT, kind TEXT, raw_tool TEXT,
          taxonomy TEXT, success INTEGER, exit_code INTEGER, duration_ms REAL,
          parser_version TEXT, explicit_skill TEXT
        );
        """
    )

    def legacy_row(identity: str, ts: float) -> None:
        connection.execute(
            "INSERT INTO tool_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                None,
                f"native:session-1:tool_use:{identity}",
                "session-1",
                "run-1",
                "project-1",
                "claude",
                "model-1",
                ts,
                "reconciled_transcript",
                "tool_use",
                "Bash",
                "shell",
                None,
                None,
                None,
                "legacy-v1",
                None,
            ),
        )
        connection.commit()

    legacy_row("first", 1_788_000_000.0)
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    assert ledger.import_legacy_batch(source, batch_size=10)["completed"] is True
    # The legacy store reconciles another transcript later; the next pass takes it.
    legacy_row("second", 1_788_000_010.0)
    later = ledger.import_legacy_batch(source, batch_size=10)
    assert later == {"imported": 1, "cursor": 2, "completed": True}
    assert len(ledger.tool_calls()) == 2
    connection.close()
    ledger.close()
