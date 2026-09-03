from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent
from swe_mux.telemetry_ledger import CanonicalTelemetryLedger
from swe_mux.telemetry_schema import classify_tool
from swe_mux.telemetry_service import CanonicalTelemetryService


def dimensions(
    *,
    backend: str = "claude",
    run_id: str = "run-1",
    turn_id: str | None = "turn-1",
) -> dict[str, Any]:
    return {
        "session_id": "session-1",
        "run_id": run_id,
        "native_conversation_id": "native-1",
        "turn_id": turn_id,
        "agent_id": "root",
        "project_id": "project-1",
        "backend": backend,
        "model": "model-1",
        "origin": "mux_owned",
        "harness_version": "1.2.3",
        "source_locator": "C:/provider/transcript.jsonl",
    }


def event(
    event_type: str,
    *,
    ts: float = 1_788_000_000.0,
    source: str = "transcript",
    **payload: Any,
) -> MuxEvent:
    return MuxEvent(ts, "session-1", source, event_type, payload, seq=42)


def test_tool_classification_is_multidimensional() -> None:
    assert classify_tool("exec", backend="codex", source="transcript") == {
        "family": "shell",
        "operation": "execute",
        "transport": "code_mode",
        "server": None,
        "tool": "exec",
    }


def test_high_frequency_presentation_events_do_not_enter_the_ledger(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    ledger.record_event(event("state_changed", state="working"), dimensions())
    assert not list((tmp_path / "telemetry" / "segments").glob("*.sqlite3"))
    ledger.close()
    assert classify_tool(
        "mcp__mux__read_transcript", backend="claude", source="transcript"
    ) == {
        "family": "read",
        "operation": "read_transcript",
        "transport": "mcp",
        "server": "mux",
        "tool": "read_transcript",
    }


def test_call_and_result_merge_without_persisting_output(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(
        event(
            "tool_use",
            tool="Bash",
            call_id="call-1",
            target="pytest -q",
            content_hash="input-hash",
        ),
        dims,
    )
    secret_output = "SECRET result body that must never enter canonical telemetry"
    ledger.record_event(
        event(
            "tool_result",
            ts=1_788_000_002.0,
            tool="Bash",
            call_id="call-1",
            success=True,
            duration_ms=1250,
            detail=secret_output,
        ),
        dims,
    )

    rows = ledger.tool_calls()
    assert len(rows) == 1
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["duration_ms"] == 1250
    assert rows[0]["output_chars"] == len(secret_output)
    assert rows[0]["output_sha256"] == hashlib.sha256(secret_output.encode()).hexdigest()
    segment = next((tmp_path / "telemetry" / "segments").glob("*.sqlite3"))
    assert secret_output.encode() not in segment.read_bytes()
    ledger.close()


def test_full_result_hash_does_not_mislabel_a_bounded_preview_as_full_size(
    tmp_path: Path,
) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(
        event(
            "tool_result",
            tool="Read",
            call_id="call-1",
            success=True,
            content_hash="full-result-hash",
            detail="bounded preview",
        ),
        dims,
    )

    row = ledger.tool_calls()[0]
    assert row["output_sha256"] == "full-result-hash"
    assert row["output_chars"] is None
    assert row["output_bytes"] is None
    assert row["output_measurement"] == "full_hash_size_unknown"
    ledger.close()


def test_codex_model_and_nested_runtime_calls_are_not_counted_as_peers(
    tmp_path: Path,
) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(backend="codex")
    ledger.record_event(
        event("tool_use", tool="exec", call_id="outer", source="transcript"), dims
    )
    ledger.record_event(
        event("tool_use", tool="Bash", call_id="inner", source="hook"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            tool="exec",
            call_id="outer",
            source="transcript",
            success=True,
        ),
        dims,
    )

    rows = sorted(ledger.tool_calls(), key=lambda row: row["invocation_layer"])
    assert [(row["raw_name"], row["invocation_layer"]) for row in rows] == [
        ("exec", "model"),
        ("Bash", "runtime"),
    ]
    summary = ledger.tool_summary(from_ts=1_787_999_000, to_ts=1_788_001_000)
    assert summary["totals"] == {
        "model_calls": 1,
        "runtime_calls": 1,
        "completed": 1,
        "succeeded": 1,
        "failed": 0,
        "denied": 0,
        "interrupted": 0,
        "abandoned": 0,
    }
    ledger.close()


def test_higher_quality_result_enriches_existing_call(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(
        event(
            "tool_result",
            tool="Bash",
            call_id="call-1",
            source="transcript",
            success=True,
        ),
        dims,
    )
    ledger.record_event(
        event(
            "tool_result",
            tool="Bash",
            call_id="call-1",
            source="otel",
            success=False,
            duration_ms=500,
            error_type="ShellError",
        ),
        dims,
    )

    row = ledger.tool_calls()[0]
    assert row["status"] == "failed"
    assert row["duration_ms"] == 500
    assert row["error_type"] == "ShellError"
    assert row["result_source"] == "otel"
    assert row["evidence_count"] == 2
    audit = ledger.tool_audit(row["tool_call_id"])
    assert audit is not None
    assert sum(int(item["conflict"]) for item in audit["evidence"]) == 1
    ledger.close()


def test_lower_rank_source_can_fill_duration_without_replacing_status(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(
        event(
            "tool_result",
            tool="Bash",
            call_id="call-1",
            source="transcript",
            success=False,
            error_type="TranscriptError",
        ),
        dims,
    )
    ledger.record_event(
        event(
            "canonical_tool_result",
            source="hook",
            tool="Bash",
            call_id="call-1",
            success=True,
            duration_ms=750,
        ),
        dims,
    )

    row = ledger.tool_calls()[0]
    assert row["status"] == "failed"
    assert row["status_source"] == "transcript"
    assert row["duration_ms"] == 750
    assert row["duration_source"] == "hook"
    ledger.close()


def test_turns_skills_and_verification_are_separate_entities(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(event("turn_started", turn_id="turn-1"), dims)
    ledger.record_event(
        event(
            "skill_invoked",
            tool="Skill",
            call_id="skill-call",
            skill="documentation",
            invocation_trigger="proactive",
            skill_source="user",
        ),
        dims,
    )
    ledger.record_event(
        event(
            "canonical_model_request",
            ts=1_788_000_002,
            request_id="request-1",
            duration_ms=500,
            success=True,
            attempts=1,
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=40,
        ),
        dims,
    )
    ledger.record_event(
        event(
            "context_compacted",
            ts=1_788_000_002.5,
            compaction_id="compact-1",
            trigger="auto",
            success=True,
            duration_ms=250,
            tokens_before=90_000,
            tokens_after=30_000,
        ),
        dims,
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=1_788_000_003,
            tool="Bash",
            call_id="test-call",
            success=True,
            test_outcome={
                "framework": "pytest",
                "passed": 12,
                "failed": 0,
                "errors": 0,
                "skipped": 1,
            },
        ),
        dims,
    )
    ledger.record_event(
        event("turn_ended", ts=1_788_000_004, turn_id="turn-1", duration_ms=4000), dims
    )

    assert ledger.turns()[0]["status"] == "completed"
    assert ledger.skills()[0]["skill_name"] == "documentation"
    assert ledger.verifications()[0]["framework"] == "pytest"
    workload = ledger.workload_summary(
        from_ts=1_787_999_000, to_ts=1_788_001_000
    )["dimensions"][0]
    assert workload["model_requests"] == 1
    assert workload["average_model_wait_ms"] == 500
    assert workload["request_input_tokens"] == 100
    compaction = ledger.compaction_summary(
        from_ts=1_787_999_000, to_ts=1_788_001_000
    )["groups"][0]
    assert compaction["count"] == 1
    assert compaction["average_duration_ms"] == 250
    assert compaction["average_tokens_reclaimed"] == 60_000
    ledger.close()


def test_month_segments_preserve_exact_cross_segment_totals(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    january = 1_769_000_000.0
    february = 1_771_500_000.0
    for index in range(650):
        ts = january if index < 325 else february
        ledger.record_event(
            event(
                "tool_use",
                ts=ts + index,
                tool="Read",
                call_id=f"call-{index}",
            ),
            dims,
        )

    summary = ledger.tool_summary(from_ts=january - 1, to_ts=february + 1000)
    assert summary["totals"]["model_calls"] == 650
    assert summary["matching_calls"] == 650
    page = ledger.tool_calls(limit=25)
    assert len(page) == 25
    assert summary["matching_calls"] > len(page)
    first_page = ledger.tool_page(
        from_ts=january - 1, to_ts=february + 1000, limit=400
    )
    second_page = ledger.tool_page(
        from_ts=january - 1,
        to_ts=february + 1000,
        limit=400,
        cursor=first_page["next_cursor"],
    )
    assert first_page["matching_calls"] == second_page["matching_calls"] == 650
    assert len(first_page["items"]) == 400
    assert len(second_page["items"]) == 250
    assert {
        item["tool_call_id"] for item in first_page["items"]
    }.isdisjoint(item["tool_call_id"] for item in second_page["items"])
    assert len(list((tmp_path / "telemetry" / "segments").glob("*.sqlite3"))) == 2
    ledger.close()


def test_call_result_crossing_a_month_boundary_remains_one_entity(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    january = 1_769_903_999.0
    february = 1_769_904_001.0
    ledger.record_event(
        event("tool_use", ts=january, tool="Bash", call_id="long-call"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=february,
            tool="Bash",
            call_id="long-call",
            success=True,
        ),
        dims,
    )

    calls = ledger.tool_calls()
    assert len(calls) == 1
    assert calls[0]["status"] == "succeeded"
    assert calls[0]["evidence_count"] == 2
    ledger.close()


def test_dimension_provider_accepts_a_live_session_shape(tmp_path: Path) -> None:
    record = SimpleNamespace(
        id="session-1",
        agent_run_id="run-2",
        native_session_id="native-2",
        active_turn_id="turn-2",
        project_id="project-2",
        backend="claude",
        model="opus",
    )
    session = cast(Any, SimpleNamespace(record=record, transcript_path=Path("native.jsonl")))
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    resolved = ledger.dimensions_from_session(session)
    assert resolved["run_id"] == "run-2"
    assert resolved["source_locator"].endswith("native.jsonl")
    ledger.close()


async def test_service_batches_event_bus_ingestion_off_loop(tmp_path: Path) -> None:
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
    manager = SimpleNamespace(sessions={"session-1": session})
    service = CanonicalTelemetryService(tmp_path / "telemetry")
    events = EventBus(clock=lambda: 1_788_000_000.0)
    service.start(events, sessions=manager)

    await events.emit(
        "tool_use",
        session_id="session-1",
        source="transcript",
        tool="Read",
        call_id="call-1",
    )
    await events.emit(
        "tool_result",
        session_id="session-1",
        source="transcript",
        tool="Read",
        call_id="call-1",
        success=True,
    )
    await service.stop()

    assert service.health()["accepted"] == 2
    assert service.ledger.tool_calls()[0]["status"] == "succeeded"
    service.close()


def test_legacy_import_is_incremental_idempotent_and_non_destructive(tmp_path: Path) -> None:
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
    connection.execute(
        "INSERT INTO history VALUES(?,?,?,?,?)",
        ("run-1", 0, "native-1", "native.jsonl", 1_788_000_000.0),
    )
    for kind, success in (("tool_use", None), ("tool_result", 1)):
        connection.execute(
            "INSERT INTO tool_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                kind,
                None,
                f"native:session-1:{kind}:call-1",
                "session-1",
                "run-1",
                "project-1",
                "claude",
                "model-1",
                1_788_000_000.0,
                "reconciled_transcript",
                kind,
                "Bash",
                "shell",
                success,
                None,
                123.0 if success else None,
                "legacy-v1",
                None,
            ),
        )
    connection.commit()
    connection.close()

    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    first = ledger.import_legacy_batch(source, batch_size=10)
    second = ledger.import_legacy_batch(source, batch_size=10)

    assert first == {"imported": 2, "cursor": 2, "completed": True}
    assert second == {"imported": 0, "cursor": 2, "completed": True}
    assert ledger.tool_calls()[0]["status"] == "succeeded"
    with sqlite3.connect(source) as unchanged:
        assert unchanged.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0] == 2
    ledger.close()


def test_workload_keeps_wall_turn_tool_wait_and_verification_axes_separate(
    tmp_path: Path,
) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(event("turn_started", turn_id="turn-1", turn_epoch=1), dims)
    ledger.record_event(event("approval_needed", ts=1_788_000_001), dims)
    ledger.record_event(event("stalled", ts=1_788_000_001.5), dims)
    ledger.record_event(
        event("tool_use", ts=1_788_000_002, tool="Bash", call_id="call-1"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=1_788_000_004,
            tool="Bash",
            call_id="call-1",
            success=True,
            duration_ms=2000,
            approval_wait_ms=500,
            test_outcome={"framework": "pytest", "passed": 2, "failed": 0, "errors": 0},
        ),
        dims,
    )
    ledger.record_event(
        event("turn_ended", ts=1_788_000_005, turn_id="turn-1", turn_epoch=1, duration_ms=5000),
        dims,
    )
    ledger.record_event(event("session_exited", ts=1_788_000_006, reason="complete"), dims)

    row = ledger.workload_summary(
        from_ts=1_787_999_000, to_ts=1_788_001_000
    )["dimensions"][0]
    assert row["runs"] == row["ended_runs"] == 1
    assert row["average_wall_duration_s"] == 6
    assert row["turns"] == row["completed_turns"] == 1
    assert row["average_turn_duration_ms"] == 5000
    assert row["model_tool_calls"] == row["completed_tool_calls"] == 1
    assert row["average_tool_duration_ms"] == 2000
    assert row["approval_wait_ms"] == 500
    assert row["approval_events"] == row["stall_events"] == 1
    assert row["verifications"] == row["successful_verifications"] == 1
    ledger.close()


def test_closed_day_rollup_stays_exact_after_late_evidence(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    day_start = 1_767_225_600.0
    call_at = day_start + 3600
    ledger.record_event(
        event("tool_use", ts=call_at, tool="Read", call_id="call-1"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=call_at + 1,
            tool="Read",
            call_id="call-1",
            success=True,
            duration_ms=10,
        ),
        dims,
    )
    before = ledger.tool_summary(from_ts=day_start, to_ts=day_start + 86400)

    assert ledger.rebuild_next_closed_day(now=day_start + 2 * 86400) == "2026-01-01"
    rolled = ledger.tool_summary(from_ts=day_start, to_ts=day_start + 86400)
    assert rolled == before

    ledger.record_event(
        event(
            "canonical_tool_result",
            ts=call_at + 2,
            source="otel",
            tool="Read",
            call_id="call-1",
            success=False,
            error_type="ReadError",
        ),
        dims,
    )
    dirty = ledger.tool_summary(from_ts=day_start, to_ts=day_start + 86400)
    assert dirty["totals"]["failed"] == 1
    assert dirty["totals"]["succeeded"] == 0
    assert ledger.rebuild_next_closed_day(now=day_start + 2 * 86400) == "2026-01-01"
    assert ledger.tool_summary(from_ts=day_start, to_ts=day_start + 86400) == dirty
    ledger.close()


def test_tool_audit_exposes_provenance_without_content(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(
        event("tool_use", tool="Read", call_id="call-1", target="src/private.py"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=1_788_000_001,
            tool="Read",
            call_id="call-1",
            success=True,
            detail="private contents",
        ),
        dims,
    )
    call = ledger.tool_calls()[0]

    audit = ledger.tool_audit(call["tool_call_id"])

    assert audit is not None
    assert audit["call"]["target_preview"] == "src/private.py"
    assert len(audit["evidence"]) == 2
    assert "private contents" not in str(audit)
    ledger.close()


def test_inefficiency_findings_require_measured_denominators(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    for index in range(10):
        ledger.record_event(
            event(
                "tool_use",
                ts=1_788_000_000 + index * 2,
                tool="wait",
                call_id=f"call-{index}",
            ),
            dims,
        )
        ledger.record_event(
            event(
                "tool_result",
                ts=1_788_000_001 + index * 2,
                tool="wait",
                call_id=f"call-{index}",
                success=index >= 5,
                duration_ms=40_000,
                detail="x" * 120_000,
            ),
            dims,
        )

    result = ledger.inefficiency_findings(
        from_ts=1_787_999_000, to_ts=1_788_001_000
    )
    kinds = {finding["kind"] for finding in result["findings"]}
    assert kinds == {"frequent_polling", "high_failure_rate", "large_results", "slow_tool"}
    failure = next(
        finding for finding in result["findings"] if finding["kind"] == "high_failure_rate"
    )
    assert failure["evidence"] == {"failed": 5, "completed": 10}
    assert failure["coverage"] == 1
    ledger.close()


def test_run_close_marks_only_incomplete_calls_and_turns_abandoned(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(event("turn_started", turn_id="turn-1", turn_epoch=1), dims)
    ledger.record_event(event("tool_use", tool="Read", call_id="open-call"), dims)
    ledger.record_event(
        event("tool_use", ts=1_788_000_001, tool="Read", call_id="done-call"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=1_788_000_002,
            tool="Read",
            call_id="done-call",
            success=True,
        ),
        dims,
    )
    ledger.record_event(event("agent_run_ended", ts=1_788_000_003), dims)

    calls = {row["native_call_id"]: row["status"] for row in ledger.tool_calls()}
    assert calls == {"open-call": "abandoned", "done-call": "succeeded"}
    assert ledger.turns()[0]["status"] == "abandoned"
    ledger.close()


def test_quality_reports_each_missing_field_denominator_separately(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(backend="codex")
    ledger.record_event(
        event("tool_use", source="hook", tool="Bash", call_id="runtime-call"), dims
    )
    ledger.record_event(
        event("tool_use", tool="exec", call_id="model-call"), dims
    )
    ledger.record_event(
        event(
            "tool_result",
            ts=1_788_000_001,
            tool="exec",
            call_id="model-call",
            success=True,
            content_hash="result-hash",
        ),
        dims,
    )

    quality = ledger.quality_summary(
        from_ts=1_787_999_000, to_ts=1_788_001_000
    )["totals"]
    assert quality["calls"] == 2
    assert quality["with_request"] == 2
    assert quality["with_result"] == 1
    assert quality["with_duration"] == 0
    assert quality["with_output_hash"] == 1
    assert quality["runtime_parent_unavailable"] == 1
    ledger.close()


def test_segment_seal_is_hashed_and_late_evidence_invalidates_it(tmp_path: Path) -> None:
    root = tmp_path / "telemetry"
    ledger = CanonicalTelemetryLedger(root)
    dims = dimensions()
    day_start = 1_767_225_600.0
    ledger.record_event(
        event("tool_use", ts=day_start + 10, tool="Read", call_id="call-1"), dims
    )
    assert ledger.rebuild_next_closed_day(now=day_start + 2 * 86400) == "2026-01-01"

    sealed = ledger.seal_next_segment(now=day_start + 15 * 86400, grace_days=7)
    assert sealed is not None
    assert sealed["period"] == "2026-01"
    assert len(sealed["sha256"]) == 64

    ledger.record_event(
        event(
            "canonical_tool_result",
            ts=day_start + 20,
            source="otel",
            tool="Read",
            call_id="call-1",
            success=True,
        ),
        dims,
    )
    with sqlite3.connect(root / "catalog.sqlite3") as catalog:
        assert catalog.execute(
            "SELECT sealed_at FROM ledger_segments WHERE period='2026-01'"
        ).fetchone()[0] is None
        invalidated = catalog.execute(
            "SELECT invalidated_at FROM segment_seals WHERE period='2026-01'"
        ).fetchone()[0]
        assert invalidated is not None
    ledger.close()


def test_cumulative_skill_metrics_are_reduced_to_deltas(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(backend="codex")
    for index, count in enumerate((3, 5)):
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
                metric_start_time="100",
            ),
            dims,
        )

    summary = ledger.skill_summary(
        from_ts=1_787_999_000, to_ts=1_788_001_000
    )
    assert summary["matching_invocations"] == 5
    assert summary["groups"][0]["invocations"] == 5
    ledger.close()
