"""Schema-3 analytics: provider metrics, approval waits, hourly and entity rollups,
audits, cohorts, reviews, shadow comparison, and the adaptive-change guard."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from swe_mux.telemetry_ledger import CanonicalTelemetryLedger
from swe_mux.telemetry_otlp import (
    METRIC_ALLOWLIST,
    METRIC_ATTRIBUTES,
    otlp_reduction,
    provider_capabilities,
)
from swe_mux.telemetry_queries import (
    AdaptiveChangeRefused,
    finding_key,
    propose_adaptive_change,
)
from tests.test_telemetry_ledger import dimensions, event, figures

FIXTURES = Path(__file__).parent / "fixtures" / "telemetry"
METRICS_CAPTURE = FIXTURES / "otlp-codex-0.153.0-metrics.json"
DAY = 1_767_225_600.0  # 2026-01-01T00:00:00Z
WINDOW = {"from_ts": 1_787_999_000, "to_ts": 1_788_001_000}


def _codex_metrics_events() -> list[Any]:
    events: list[Any] = []
    for payload in json.loads(METRICS_CAPTURE.read_text(encoding="utf-8")):
        reduction = otlp_reduction(payload, session_id="session-1", backend="codex")
        assert all(recognised for (_name, recognised) in reduction.signatures)
        events.extend(reduction.events)
    return events


# -- provider metrics -----------------------------------------------------------------


def test_codex_metrics_export_reduces_to_allowlisted_points_only() -> None:
    events = _codex_metrics_events()
    metrics = [item for item in events if item.type == "provider_metric"]
    names = {item.payload["metric"] for item in metrics}
    assert names <= METRIC_ALLOWLIST
    assert {"codex.tool.call", "codex.turn.token_usage", "codex.guardian.review"} <= names
    for item in metrics:
        assert set(item.payload["attributes"]) <= METRIC_ATTRIBUTES
        assert "user.email" not in json.dumps(item.payload)
        assert item.payload["harness_version"] == "0.153.0"
    tool_calls = [item for item in metrics if item.payload["metric"] == "codex.tool.call"]
    assert sum(int(item.payload["sum"]) for item in tool_calls) == 6
    assert {item.payload["attributes"]["tool"] for item in tool_calls} == {"exec", "exec_command"}
    guardian = next(item for item in metrics if item.payload["metric"] == "codex.guardian.review")
    assert guardian.payload["agent_id"] == "root"
    assert guardian.payload["attributes"]["decision"] == "approved"
    histogram = next(
        item for item in metrics if item.payload["metric"] == "codex.tool.call.duration_ms"
    )
    assert histogram.payload["kind"] == "histogram"
    assert histogram.payload["count"] == 1 and histogram.payload["sum"] > 0


def test_provider_metrics_are_kept_beside_entities_and_compared_per_run(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions(backend="codex")
    inserted = ledger.record_events((item, dims) for item in _codex_metrics_events())
    assert inserted > 0
    rows = ledger.provider_metrics()
    assert rows and {row["temporality"] for row in rows} == {"delta"}
    # The provider says six tool calls for this run; the ledger holds none yet.
    summary = ledger.metric_summary(from_ts=0, to_ts=2_000_000_000)
    agreement = summary["tool_call_agreement"]
    assert agreement["runs"] == 1 and agreement["provider_more"] == 1
    assert agreement["examples"][0]["provider_reported"] == 6
    for index in range(6):
        ledger.record_event(
            event("tool_use", ts=1_788_458_260 + index, tool="exec", call_id=f"c-{index}"), dims
        )
    agreement = ledger.metric_summary(from_ts=0, to_ts=2_000_000_000)["tool_call_agreement"]
    assert agreement["agree"] == 1 and agreement["provider_more"] == 0
    audit = ledger.run_audit("run-1")
    assert audit is not None
    assert any(item["metric_name"] == "codex.tool.call" for item in audit["provider_metrics"])
    page = ledger.entity_page(
        kind="provider_metrics", from_ts=0, to_ts=2_000_000_000, limit=5,
        filters={"metric_name": "codex.tool.call"},
    )
    assert page["matching"] == 3 and len(page["items"]) == 3
    ledger.close()


def test_capabilities_distinguish_unmeasured_from_impossible() -> None:
    capabilities = provider_capabilities()
    assert capabilities["codex"]["runtime_parent"] == "measured"
    assert capabilities["claude"]["runtime_parent"] == "unmeasured"
    assert capabilities["claude"]["tool_duration"] == "measured"
    assert capabilities["omp"]["tool_duration"] == "no_native_telemetry"
    assert capabilities["pi"]["skill_activation"] == "no_native_telemetry"


# -- approval waits and run start provenance -------------------------------------------


def test_approval_wait_is_measured_from_request_to_resolution_or_result(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    ledger.record_event(event("tool_use", tool="Bash", call_id="call-1"), dims)
    ledger.record_event(
        event("approval_needed", ts=1_788_000_001, source="hook", detail="Bash", call_id="call-1"),
        dims,
    )
    ledger.record_event(
        event(
            "approval_resolved", ts=1_788_000_031, source="otel", tool="Bash", call_id="call-1",
            decision="accept",
        ),
        dims,
    )
    ledger.record_event(
        event("tool_result", ts=1_788_000_040, tool="Bash", call_id="call-1", success=True), dims
    )
    # A second call is only ever resolved by its result.
    ledger.record_event(event("tool_use", ts=1_788_000_050, tool="Edit", call_id="call-2"), dims)
    ledger.record_event(
        event("approval_needed", ts=1_788_000_051, source="hook", detail="Edit", call_id="call-2"),
        dims,
    )
    ledger.record_event(
        event("tool_result", ts=1_788_000_171, tool="Edit", call_id="call-2", success=True), dims
    )
    # A third call resolved with no recorded request stays unknown, never estimated.
    ledger.record_event(
        event("approval_resolved", ts=1_788_000_200, source="otel", tool="Read", call_id="call-3"),
        dims,
    )
    waits = {row["native_call_id"]: row["approval_wait_ms"] for row in ledger.tool_calls()}
    assert waits == {"call-1": 30_000, "call-2": 120_000, "call-3": None}
    summary = ledger.tool_summary(**WINDOW)
    assert summary["approval_wait"] == {"measured": 2, "average_ms": 75_000}
    workload = ledger.workload_summary(**WINDOW)["dimensions"][0]
    assert workload["approval_wait_count"] == 2
    assert workload["average_approval_wait_ms"] == 75_000
    audit = ledger.tool_audit(next(row["tool_call_id"] for row in ledger.tool_calls()
                                    if row["native_call_id"] == "call-1"))
    assert audit is not None
    assert {item["contribution"] for item in audit["evidence"]} == {
        "request", "approval_request", "approval_resolution", "result"
    }
    ledger.close()


def test_a_run_start_is_declared_or_first_evidence_and_never_estimated(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    estimated = dimensions(run_id="run-late")
    estimated.pop("run_started_at", None)
    ledger.record_event(event("tool_use", ts=1_788_000_500, tool="Read", call_id="c-1"), estimated)
    ledger.record_event(event("tool_use", ts=1_788_000_100, tool="Read", call_id="c-0"), estimated)
    run = ledger.run_audit("run-late")
    assert run is not None
    assert run["run"]["started_at_source"] == "first_evidence"
    assert run["run"]["started_at"] == 1_788_000_100  # the earliest evidence, not the first seen
    # The history row arrives later with the real start; it replaces the estimate.
    declared = dict(estimated, run_started_at=1_787_999_900.0)
    ledger.record_event(
        event("agent_run_started", ts=1_787_999_900, source="legacy", legacy_history_id="x"),
        declared,
    )
    run = ledger.run_audit("run-late")
    assert run is not None
    assert run["run"]["started_at_source"] == "declared"
    assert run["run"]["started_at"] == 1_787_999_900
    assert {item["contribution"] for item in run["evidence"]} == {"agent_run_started"}
    quality = ledger.quality_summary(from_ts=1_787_999_000, to_ts=1_788_001_000)
    assert quality["runs"] == {
        "runs": 1, "declared_start": 1, "first_evidence_start": 0, "ended": 0
    }
    ledger.close()


# -- rollups -------------------------------------------------------------------------


def _seed_two_days(ledger: CanonicalTelemetryLedger) -> None:
    for index in range(40):
        backend = "claude" if index % 2 else "codex"
        dims = dimensions(
            backend=backend, run_id=f"run-{backend}-{index // 10}", turn_id=f"t-{index}"
        )
        ts = DAY + index * 3600 - 7200  # from 22:00 the day before to the next day 15:00
        ledger.record_event(event("turn_started", ts=ts, turn_id=f"t-{index}"), dims)
        ledger.record_event(event("tool_use", ts=ts + 1, tool="Read", call_id=f"c-{index}"), dims)
        ledger.record_event(
            event(
                "tool_result",
                ts=ts + 2,
                tool="Read",
                call_id=f"c-{index}",
                success=index % 5 != 0,
                duration_ms=10,
                test_outcome=(
                    {"framework": "pytest", "passed": 1, "failed": 0, "errors": 0}
                    if index % 4 == 0
                    else None
                ),
            )
            if index % 4 == 0
            else event(
                "tool_result", ts=ts + 2, tool="Read", call_id=f"c-{index}",
                success=index % 5 != 0, duration_ms=10,
            ),
            dims,
        )
        ledger.record_event(
            event("skill_invoked", ts=ts + 3, skill="docs", call_id=f"s-{index}"), dims
        )
        ledger.record_event(
            event("context_compacted", ts=ts + 4, compaction_id=f"k-{index}", trigger="auto"),
            dims,
        )
        ledger.record_event(
            event("turn_ended", ts=ts + 5, turn_id=f"t-{index}", duration_ms=5000), dims
        )


def test_hourly_rollups_answer_a_sub_day_window_exactly(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    _seed_two_days(ledger)
    # A 24-hour window ending mid-afternoon on day two: two partial days, no full day.
    window = {"from_ts": DAY + 14 * 3600 + 1800, "to_ts": DAY + 38 * 3600 + 1800}
    raw_tools = ledger.tool_summary(**window)
    raw_workload = ledger.workload_summary(**window)
    # Both partial days are in January, so the raw reads merge into one span.
    assert raw_tools["coverage"] == {
        "rolled_days": 0, "rolled_hours": 0, "raw_spans": 1, "raw_seconds": 86400.0
    }
    now = DAY + 40 * 3600
    assert ledger.rebuild_next_closed_day(now=now) == "2025-12-31"
    assert ledger.rebuild_next_closed_day(now=now) == "2026-01-01"
    assert ledger.rebuild_next_closed_day(now=now) is None
    hours = 0
    while ledger.rebuild_next_closed_hour(now=now) is not None:
        hours += 1
    assert hours > 0
    rolled_tools = ledger.tool_summary(**window)
    rolled_workload = ledger.workload_summary(**window)
    assert figures(rolled_tools) == figures(raw_tools)
    assert figures(rolled_workload) == figures(raw_workload)
    coverage = rolled_tools["coverage"]
    assert coverage["rolled_hours"] > 0
    assert coverage["raw_seconds"] < 3600 * 2  # only the two half-hours at the edges
    # Late evidence on a rolled-up hour dirties that hour, and the answer stays exact.
    dims = dimensions(backend="codex", run_id="run-codex-2", turn_id="t-28")
    ledger.record_event(
        event(
            "canonical_tool_result", ts=DAY + 26 * 3600 + 10, source="otel", tool="Read",
            call_id="c-28", success=False, error_type="Late",
        ),
        dims,
    )
    dirty = ledger.tool_summary(**window)
    assert dirty["totals"]["failed"] == raw_tools["totals"]["failed"] + 1
    while ledger.rebuild_next_closed_hour(now=now) is not None:
        pass
    assert figures(ledger.tool_summary(**window)) == figures(dirty)
    ledger.close()


def test_skill_verification_and_compaction_rollups_stay_exact(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    _seed_two_days(ledger)
    window = {"from_ts": DAY - 86400, "to_ts": DAY + 2 * 86400}
    before = {
        "skills": figures(ledger.skill_summary(**window)),
        "verifications": figures(ledger.verification_summary(**window)),
        "compactions": figures(ledger.compaction_summary(**window)),
    }
    assert before["skills"]["matching_invocations"] == 40
    assert before["verifications"]["totals"]["verifications"] == 10
    assert before["compactions"]["total"] == 40
    now = DAY + 3 * 86400
    while ledger.rebuild_next_closed_day(now=now) is not None:
        pass
    after = {
        "skills": figures(ledger.skill_summary(**window)),
        "verifications": figures(ledger.verification_summary(**window)),
        "compactions": figures(ledger.compaction_summary(**window)),
    }
    assert after == before
    assert ledger.skill_summary(**window)["coverage"]["rolled_days"] == 3
    codex = ledger.skill_summary(**window, filters={"backend": "codex"})
    assert codex["matching_invocations"] == 20
    ledger.close()


# -- pages, audits, cohorts, reviews ----------------------------------------------


def test_entity_pages_and_audits_walk_from_aggregate_to_evidence(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    _seed_two_days(ledger)
    window = {"from_ts": DAY - 86400, "to_ts": DAY + 2 * 86400}
    runs = ledger.entity_page(kind="runs", limit=10, **window)
    assert runs["matching"] == 8 and len(runs["items"]) == 8
    run_id = runs["items"][0]["run_id"]
    turns = ledger.entity_page(kind="turns", limit=100, filters={"run_id": run_id}, **window)
    assert turns["matching"] == 5
    turn_id = turns["items"][0]["turn_id"]
    turn = ledger.turn_audit(turn_id)
    assert turn is not None and len(turn["tool_calls"]) == 1
    call = turn["tool_calls"][0]
    assert ledger.tool_audit(call["tool_call_id"]) is not None
    calls = ledger.tool_page(limit=3, raw_name="Read", run_id=run_id, **window)
    assert calls["matching_calls"] == 5 and calls["next_cursor"]
    skills = ledger.entity_page(kind="skills", limit=3, filters={"skill_name": "docs"}, **window)
    assert skills["matching"] == 40 and len(skills["items"]) == 3
    verifications = ledger.entity_page(kind="verifications", limit=50, **window)
    assert verifications["matching"] == 10
    with pytest.raises(ValueError):
        ledger.entity_page(kind="secrets", limit=1, **window)
    audit = ledger.run_audit(run_id)
    assert audit is not None
    assert audit["tool_calls"]["total"] == 5
    assert audit["tool_calls"]["by_quality"] == {"transcript": 5}
    assert len(audit["turns"]) == 5
    ledger.close()


def test_cohorts_compare_only_when_the_other_dimensions_are_fixed(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    _seed_two_days(ledger)
    window = {"from_ts": DAY - 86400, "to_ts": DAY + 2 * 86400}
    by_backend = ledger.compare_cohorts(split="backend", **window)
    assert by_backend["comparable"] is True
    assert {item["cohort"] for item in by_backend["cohorts"]} == {"claude", "codex"}
    cohorts = {item["cohort"]: item for item in by_backend["cohorts"]}
    for cohort in cohorts.values():
        assert cohort["runs"] == 4
        assert cohort["skill_activations_per_run"] == 5
        assert 0 < cohort["tool_failure_rate"] < 1
    # Only the even indexes verified, and those are all codex: claude's rate is
    # None over a zero denominator, never a number.
    assert cohorts["codex"]["verification_success_rate"] == 1.0
    assert cohorts["claude"]["verifications"] == 0
    assert cohorts["claude"]["verification_success_rate"] is None
    # Splitting on project while backends differ is not a comparison of projects.
    by_project = ledger.compare_cohorts(split="project_id", **window)
    assert by_project["comparable"] is False
    assert by_project["why_not_comparable"]
    fixed = ledger.compare_cohorts(split="project_id", filters={"backend": "codex"}, **window)
    assert fixed["comparable"] is True
    with pytest.raises(ValueError):
        ledger.compare_cohorts(split="raw_name", **window)
    ledger.close()


def test_findings_carry_keys_reviews_and_the_adaptive_change_guard(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    for index in range(6):
        ledger.record_event(
            event("tool_use", ts=1_788_000_000 + index, tool="Grep", call_id=f"g-{index}",
                  target="same pattern"),
            dims,
        )
        ledger.record_event(
            event("tool_result", ts=1_788_000_000.5 + index, tool="Grep", call_id=f"g-{index}",
                  success=False),
            dims,
        )
    result = ledger.inefficiency_findings(**WINDOW)
    kinds = {finding["kind"] for finding in result["findings"]}
    assert {"high_failure_rate", "repeated_identical_calls"} <= kinds
    repeated = next(f for f in result["findings"] if f["kind"] == "repeated_identical_calls")
    assert repeated["evidence"]["max_repeats"] == 6
    failure = next(f for f in result["findings"] if f["kind"] == "high_failure_rate")
    assert failure["finding_key"] == finding_key("high_failure_rate", failure["tool"])
    assert failure["review"] is None
    assert result["adaptive_changes"]["offered"] == 0

    review = ledger.review_finding(
        finding_key=failure["finding_key"],
        kind="high_failure_rate",
        verdict="noise",
        note="expected",
    )
    assert review["verdict"] == "noise"
    reviewed = ledger.inefficiency_findings(**WINDOW)
    assert next(f for f in reviewed["findings"] if f["kind"] == "high_failure_rate")["review"][
        "verdict"
    ] == "noise"
    hidden = ledger.inefficiency_findings(**WINDOW, include_reviewed=False)
    assert "high_failure_rate" not in {f["kind"] for f in hidden["findings"]}
    assert hidden["reviewed"] == 1
    with pytest.raises(ValueError):
        ledger.review_finding(finding_key="short", kind="x", verdict="useful", note=None)
    with pytest.raises(ValueError):
        ledger.review_finding(
            finding_key=failure["finding_key"], kind="x", verdict="meh", note=None
        )

    # No adaptive change without a useful verdict, a window, and a rollback rule.
    with pytest.raises(AdaptiveChangeRefused):
        propose_adaptive_change(failure, review=review, comparison_window_days=7, rollback_rule="x")
    useful = ledger.review_finding(
        finding_key=failure["finding_key"], kind="high_failure_rate", verdict="useful", note=None
    )
    with pytest.raises(AdaptiveChangeRefused):
        propose_adaptive_change(failure, review=useful, comparison_window_days=0, rollback_rule="x")
    with pytest.raises(AdaptiveChangeRefused):
        propose_adaptive_change(failure, review=useful, comparison_window_days=7, rollback_rule=" ")
    proposal = propose_adaptive_change(
        failure, review=useful, comparison_window_days=7, rollback_rule="revert if failures rise"
    )
    assert proposal["status"] == "proposed_not_applied"
    ledger.close()


def test_shadow_comparison_classifies_every_disagreement(tmp_path: Path) -> None:
    source = tmp_path / "mux.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE history (id TEXT PRIMARY KEY, spawned_at REAL);
            CREATE TABLE tool_events (
              agent_run_id TEXT, raw_tool TEXT, kind TEXT
            );
            INSERT INTO history VALUES('run-1', 1788000000.0);
            INSERT INTO tool_events VALUES('run-1','Bash','tool_use');
            INSERT INTO tool_events VALUES('run-1','Bash','tool_result');
            INSERT INTO tool_events VALUES('run-1','Read','tool_use');
            INSERT INTO tool_events VALUES('run-1','Read','tool_use');
            INSERT INTO tool_events VALUES('run-1','Edit','tool_use');
            """
        )
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    dims["run_started_at"] = 1_788_000_000.0
    ledger.record_event(event("tool_use", tool="Bash", call_id="b-1"), dims)  # agree
    ledger.record_event(event("tool_use", ts=1_788_000_001, tool="Read", call_id="r-1"), dims)
    # legacy has two Read uses, ledger one: not yet imported
    ledger.record_event(
        event("canonical_tool_result", ts=1_788_000_002, source="otel", tool="Grep",
              call_id="grep-1", success=True),
        dims,
    )  # canonical only, native
    comparison = ledger.shadow_comparison(source, from_ts=1_787_999_000, to_ts=1_788_001_000)
    assert comparison["runs"] == 1
    assert comparison["classes"] == {
        "agree": 1,
        "canonical_native_only": 1,
        "canonical_more": 0,
        "legacy_more_not_yet_imported": 1,
        "legacy_only": 1,
        "canonical_only": 0,
    }
    assert {item["raw_tool"] for item in comparison["examples"]} == {"Read", "Edit", "Grep"}
    ledger.close()


def test_quality_reports_per_version_denominators_and_capabilities(tmp_path: Path) -> None:
    ledger = CanonicalTelemetryLedger(tmp_path / "telemetry")
    dims = dimensions()
    dims["harness_version"] = None
    ledger.record_event(event("tool_use", tool="Read", call_id="c-1"), dims)
    ledger.record_event(
        event("canonical_tool_result", ts=1_788_000_001, source="otel", tool="Read",
              call_id="c-1", success=True, harness_version="2.1.259", duration_ms=3),
        dims,
    )
    ledger.record_event(event("tool_use", ts=1_788_000_002, tool="Read", call_id="c-2"), dims)
    quality = ledger.quality_summary(**WINDOW)
    versions = {(row["backend"], row["harness_version"]): row for row in quality["versions"]}
    assert versions[("claude", "2.1.259")]["with_duration"] == 1
    assert versions[("claude", "unknown")]["calls"] == 1
    assert quality["capabilities"]["claude"]["executed_input"] == "measured"
    assert ledger.tool_summary(**WINDOW)["qualities"] == {"native": 1, "none": 1}
    assert ledger.tool_summary(**WINDOW, filters={"evidence_quality": "native"})[
        "matching_calls"
    ] == 1
    ledger.close()
