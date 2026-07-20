from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.detection_replay import DetectionReplay, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "detection" / "v1"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
async def test_versioned_detection_replay_golden(path: Path) -> None:
    manifest = load_manifest(path)
    result = await DetectionReplay(manifest["backend"]).run(manifest)

    assert result["events"] == manifest["expected"]["events"]
    assert result["parser"] == manifest["expected"]["parser"]
    assert result["readiness"]["delivery_state"] == manifest["expected"]["delivery_state"]
    assert result["readiness"]["reason"] == manifest["expected"]["delivery_reason"]
    assert result["readiness"]["authorized"] is False
    assert [item["actual"] for item in result["checkpoints"]] == [
        item["expected"] for item in result["checkpoints"]
    ]


async def test_replay_oracles_have_no_false_safe_or_false_blocked_decisions() -> None:
    false_safe = 0
    false_blocked = 0
    exercised = 0
    for path in sorted(FIXTURES.glob("*.json")):
        manifest = load_manifest(path)
        result = await DetectionReplay(manifest["backend"]).run(manifest)
        for checkpoint in result["checkpoints"]:
            exercised += 1
            actual_safe = checkpoint["actual"] == "safe"
            oracle_safe = checkpoint["oracle_safe"]
            false_safe += int(actual_safe and not oracle_safe)
            false_blocked += int(oracle_safe and not actual_safe)

    assert exercised >= 12
    assert false_safe == 0
    assert false_blocked == 0


async def test_replay_corpus_covers_phase1_evidence_and_lifecycle_matrix() -> None:
    fixture_counts = {"claude": 0, "codex": 0}
    step_kinds: set[str] = set()
    event_types: set[str] = set()
    delivery_states: set[str] = set()
    parser_states: set[str] = set()
    for path in sorted(FIXTURES.glob("*.json")):
        manifest = load_manifest(path)
        fixture_counts[manifest["backend"]] += 1
        step_kinds.update(str(step["kind"]) for step in manifest["steps"])
        result = await DetectionReplay(manifest["backend"]).run(manifest)
        event_types.update(item["type"] for item in result["events"])
        delivery_states.update(item["actual"] for item in result["checkpoints"])
        parser_states.add(result["parser"]["status"])

    assert fixture_counts["claude"] >= 5
    assert fixture_counts["codex"] >= 5
    assert step_kinds >= {
        "event",
        "focus",
        "hook",
        "input",
        "process",
        "restart",
        "session",
        "terminal",
        "terminal_response",
        "timer",
        "transcript",
        "transcript_chunk",
    }
    assert event_types >= {
        "approval_needed",
        "backend_demoted",
        "backend_detected",
        "capability_degraded",
        "context_compacted",
        "process_observed",
        "rate_limited",
        "session_exited",
        "stalled",
        "subagent_activity",
        "turn_aborted",
        "turn_ended",
        "turn_started",
    }
    assert delivery_states == {"blocked", "safe", "unknown"}
    assert parser_states >= {"degraded", "ready", "watching"}
