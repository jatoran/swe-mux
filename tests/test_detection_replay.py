from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.detection_replay import DetectionReplay, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "detection" / "v1"
INVENTORY = FIXTURES / "edge_case_inventory.json"


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
async def test_versioned_detection_replay_golden(path: Path) -> None:
    if path.name == INVENTORY.name:
        pytest.skip("inventory registry, not a replay fixture")
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
    # Phase 3.5: the user-visible status stream is a golden output with the same
    # no-drift protection as delivery_state. Every fixture must pin it.
    assert result["states"] == manifest["expected"]["states"]
    for checkpoint in result["state_checkpoints"]:
        assert checkpoint["actual_state"] == checkpoint["expected_state"]
        if checkpoint["expected_awaiting"] is not None:
            assert checkpoint["actual_awaiting"] == checkpoint["expected_awaiting"]


async def test_replay_oracles_have_no_false_safe_or_false_blocked_decisions() -> None:
    false_safe = 0
    false_blocked = 0
    exercised = 0
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
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
        if path.name == INVENTORY.name:
            continue
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


async def test_replay_corpus_covers_phase35_status_matrix() -> None:
    """The corpus must exercise the full user-visible status surface.

    Every SessionState reachable by observation, every awaiting sub-reason,
    both proof classes, both watchdog recovery paths, and the watchdog step
    kinds must appear somewhere in the corpus — removing coverage fails here
    even if every remaining fixture still passes its own golden.
    """
    states_seen: set[str] = set()
    awaiting_reasons: set[str] = set()
    proofs: set[str] = set()
    sources: set[str] = set()
    watchdog_actions: set[str] = set()
    step_kinds: set[str] = set()
    contract_violations = 0
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = load_manifest(path)
        step_kinds.update(str(step["kind"]) for step in manifest["steps"])
        result = await DetectionReplay(manifest["backend"]).run(manifest)
        for item in result["states"]:
            states_seen.add(item["state"])
            proofs.add(item["proof"])
            sources.add(item["source"])
            if "awaiting_reason" in item:
                awaiting_reasons.add(item["awaiting_reason"])
        watchdog_actions.update(result["health"]["watchdog_recovery_actions"])
        contract_violations += result["health"]["counters"].get("contract_violations", 0)

    assert states_seen >= {"working", "idle", "awaiting", "crashed", "exited"}
    assert awaiting_reasons >= {"approval", "question", "elicitation", "rate_limit"}
    assert proofs == {"proven", "inferred"}
    assert sources >= {"transcript", "hook", "watchdog", "watchdog-pty", "pty"}
    assert watchdog_actions >= {
        "transcript_tail_terminal",
        "pty_idle_prompt",
        "pty_working_after_awaiting",
    }
    assert step_kinds >= {"watchdog", "catchup", "pty_tail", "transcript_tail", "exit"}
    # The status contract holds across the whole corpus: no state was ever set
    # by a source outside its allowed evidence set.
    assert contract_violations == 0


async def test_every_fixture_pins_the_status_stream() -> None:
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = load_manifest(path)
        assert "states" in manifest["expected"], f"{path.name} missing expected.states"


async def test_edge_case_inventory_is_closed_and_consistent() -> None:
    """Closing an edge case means fixture + guard both exist; losing either fails.

    The inventory is the tracked list of every known status edge case with its
    reproducing fixture, the guard that closes it, and a one-line root cause.
    Fixtures tagged with an edge_case must be registered, and every registered
    fixture/guard must still exist.
    """
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert inventory["schema_version"] == 1
    entries = inventory["edge_cases"]
    assert entries, "edge-case inventory must not be empty"

    registered_fixtures: set[str] = set()
    for name, entry in entries.items():
        assert entry.get("root_cause"), f"{name}: missing root cause"
        fixture = entry.get("fixture")
        assert fixture, f"{name}: missing reproducing fixture"
        fixture_path = FIXTURES / fixture
        assert fixture_path.is_file(), f"{name}: fixture {fixture} does not exist"
        registered_fixtures.add(fixture)
        guard = entry.get("guard")
        assert guard, f"{name}: missing guard"
        assert _guard_exists(guard), f"{name}: guard {guard} not found"

    # Every fixture that declares itself an edge case must be registered.
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("edge_case"):
            assert path.name in registered_fixtures, (
                f"{path.name} is tagged edge_case but not in the inventory"
            )


def _guard_exists(guard: str) -> bool:
    """A guard is `tests/<file>::<test_name>` or `<module>::<symbol>`."""
    location, _, symbol = guard.partition("::")
    if location.startswith("tests/"):
        test_path = Path(__file__).parent.parent / location
        if not test_path.is_file():
            return False
        return symbol in test_path.read_text(encoding="utf-8")
    import importlib

    try:
        module = importlib.import_module(location)
    except ImportError:
        return False
    return hasattr(module, symbol)
