from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.harness import HARNESSES, HarnessLevel
from tests.support.detection_replay import DetectionReplay, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "detection" / "v1"
INVENTORY = FIXTURES / "edge_case_inventory.json"
SCENARIO_FLOOR = {
    HarnessLevel.observed: 1,
    HarnessLevel.hooked: 3,
    HarnessLevel.managed: 5,
}


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda path: path.stem)
async def test_versioned_detection_replay_golden(path: Path, tmp_path: Path) -> None:
    if path.name == INVENTORY.name:
        pytest.skip("inventory registry, not a replay fixture")
    manifest = load_manifest(path)
    result = await DetectionReplay(manifest["backend"], tmp_path).run(manifest)

    assert result["events"] == manifest["expected"]["events"]
    assert result["parser"] == manifest["expected"]["parser"]
    assert result["readiness"]["delivery_state"] == manifest["expected"]["delivery_state"]
    assert result["readiness"]["reason"] == manifest["expected"]["delivery_reason"]
    assert result["readiness"]["authorized"] is False
    assert [item["actual"] for item in result["checkpoints"]] == [
        item["expected"] for item in result["checkpoints"]
    ]
    assert [item["actual"] for item in result["path_checkpoints"]] == [
        item["expected"] for item in result["path_checkpoints"]
    ]
    # Phase 3.5: the user-visible status stream is a golden output with the same
    # no-drift protection as delivery_state. Every fixture must pin it.
    assert result["states"] == manifest["expected"]["states"]
    for checkpoint in result["state_checkpoints"]:
        assert checkpoint["actual_state"] == checkpoint["expected_state"]
        if checkpoint["expected_awaiting"] is not None:
            assert checkpoint["actual_awaiting"] == checkpoint["expected_awaiting"]
    # Standing-activity annotations are golden output too: expect_standing steps
    # pin the set mid-run, expected.standing pins the final set.
    for checkpoint in result["standing_checkpoints"]:
        assert checkpoint["actual"] == checkpoint["expected"]
    if "standing" in manifest["expected"]:
        assert result["standing_activity"] == manifest["expected"]["standing"]


async def test_replay_oracles_have_no_false_safe_or_false_blocked_decisions(
    tmp_path: Path,
) -> None:
    false_safe = 0
    false_blocked = 0
    exercised = 0
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = load_manifest(path)
        result = await DetectionReplay(manifest["backend"], tmp_path / path.stem).run(manifest)
        for checkpoint in result["checkpoints"]:
            exercised += 1
            actual_safe = checkpoint["actual"] == "safe"
            oracle_safe = checkpoint["oracle_safe"]
            false_safe += int(actual_safe and not oracle_safe)
            false_blocked += int(oracle_safe and not actual_safe)

    assert exercised >= 12
    assert false_safe == 0
    assert false_blocked == 0


async def test_replay_corpus_covers_phase1_evidence_and_lifecycle_matrix(
    tmp_path: Path,
) -> None:
    observed = {
        name: harness
        for name, harness in HARNESSES.items()
        if harness.level >= HarnessLevel.observed
    }
    fixture_counts = dict.fromkeys(observed, 0)
    harness_step_kinds = {name: set() for name in observed}
    harness_state_sources = {name: set() for name in observed}
    step_kinds: set[str] = set()
    event_types: set[str] = set()
    delivery_states: set[str] = set()
    parser_states: set[str] = set()
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = load_manifest(path)
        backend = manifest["backend"]
        assert backend in observed, f"{path.name}: unregistered or unobserved backend {backend}"
        fixture_counts[backend] += 1
        fixture_steps = {str(step["kind"]) for step in manifest["steps"]}
        step_kinds.update(fixture_steps)
        harness_step_kinds[backend].update(fixture_steps)
        result = await DetectionReplay(backend, tmp_path / path.stem).run(manifest)
        event_types.update(item["type"] for item in result["events"])
        delivery_states.update(item["actual"] for item in result["checkpoints"])
        parser_states.add(result["parser"]["status"])
        harness_state_sources[backend].update(item["source"] for item in result["states"])

    for name, harness in observed.items():
        floor = max(
            count for level, count in SCENARIO_FLOOR.items() if harness.level >= level
        )
        assert fixture_counts[name] >= floor, f"{name} needs at least {floor} scenarios"
        if harness.level >= HarnessLevel.hooked:
            assert "hook" in harness_step_kinds[name], f"{name} needs hook-step coverage"
        if "transcript" in harness.state_sources:
            assert "transcript" in harness_step_kinds[name]
            assert "transcript" in harness_state_sources[name]
        if "hook" in harness.state_sources:
            assert "hook" in harness_state_sources[name]
        if "pty" in harness.state_sources:
            assert harness_step_kinds[name] & {"pty", "pty_tail", "terminal"}
            assert harness_state_sources[name] & {"pty", "watchdog-pty"}
        if "cli_state" in harness.state_sources:
            assert "process" in harness_step_kinds[name]

    required_step_kinds = {
        "event",
        "focus",
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
    if any("hook" in harness.state_sources for harness in observed.values()):
        required_step_kinds.add("hook")
    assert step_kinds >= required_step_kinds

    normalized_events = {
        event
        for harness in observed.values()
        for event in harness.normalized_events
    }
    assert event_types >= normalized_events | {
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


async def test_every_observed_harness_has_a_replay_fixture() -> None:
    fixture_backends = {
        load_manifest(path)["backend"]
        for path in FIXTURES.glob("*.json")
        if path.name != INVENTORY.name
    }
    observed = {
        name
        for name, harness in HARNESSES.items()
        if harness.level >= HarnessLevel.observed
    }
    assert fixture_backends >= observed


async def test_replay_corpus_covers_phase35_status_matrix(tmp_path: Path) -> None:
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
    standing_adds: set[str] = set()
    standing_clears: set[str] = set()
    contract_violations = 0
    authority_contract_violations = 0
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = load_manifest(path)
        step_kinds.update(str(step["kind"]) for step in manifest["steps"])
        result = await DetectionReplay(manifest["backend"], tmp_path / path.stem).run(manifest)
        for item in result["states"]:
            states_seen.add(item["state"])
            proofs.add(item["proof"])
            sources.add(item["source"])
            if "awaiting_reason" in item:
                awaiting_reasons.add(item["awaiting_reason"])
        for entry in result["standing_ledger"]:
            if entry["action"] == "added":
                standing_adds.add(entry["activity"])
            elif entry["action"] in {"removed", "expired"}:
                standing_clears.add(entry["activity"])
        watchdog_actions.update(result["health"]["watchdog_recovery_actions"])
        contract_violations += result["health"]["counters"].get("contract_violations", 0)
        authority_contract_violations += result["health"]["counters"].get(
            "authority_contract_violations", 0
        )

    # Every standing-activity kind must appear with at least one add and one
    # clear (positive removal or TTL expiry) somewhere in the corpus.
    assert standing_adds >= {"loop", "cron", "background_tasks", "subagents"}
    assert standing_clears >= {"loop", "cron", "background_tasks", "subagents"}
    assert states_seen >= {"working", "idle", "awaiting", "crashed", "exited"}
    assert awaiting_reasons >= {"approval", "question", "elicitation", "rate_limit"}
    assert proofs == {"proven", "inferred"}
    observed = [
        harness for harness in HARNESSES.values() if harness.level >= HarnessLevel.observed
    ]
    required_sources = {source for source in ("transcript", "hook") if any(
        source in harness.state_sources for harness in observed
    )}
    if any("pty" in harness.state_sources for harness in observed):
        required_sources.update({"watchdog", "watchdog-pty", "pty"})
    assert sources >= required_sources
    assert watchdog_actions >= {
        "transcript_tail_terminal",
        "pty_idle_prompt",
        "pty_working_after_awaiting",
    }
    assert step_kinds >= {"watchdog", "catchup", "pty_tail", "transcript_tail", "exit"}
    # The status contract holds across the whole corpus: no state was ever set
    # by a source outside its allowed evidence set.
    assert contract_violations == 0
    assert authority_contract_violations == 0


async def test_status_matrix_is_covered_per_harness_not_just_corpus_wide(
    tmp_path: Path,
) -> None:
    """Each observed harness must exercise the status surface it declares.

    The corpus-wide matrix is a union, and Claude's fixtures satisfy most of it on
    their own. A newly added harness could therefore clear its scenario floor and
    every existing assertion while never producing an `awaiting` state, a proof
    class, or a watchdog recovery of its own - which is precisely the coverage a new
    harness most needs, because its evidence plumbing is the part nobody has
    exercised yet.

    Every requirement below is derived from what the harness itself declares, so a
    capability it genuinely lacks costs nothing: pi has no native approval flow, does
    not declare `approval_needed`, and is therefore never asked for an `awaiting`
    scenario. Exempting a harness means changing its descriptor, not weakening this.
    """
    observed = {
        name: harness
        for name, harness in HARNESSES.items()
        if harness.level >= HarnessLevel.observed
    }
    states: dict[str, set[str]] = {name: set() for name in observed}
    awaiting: dict[str, set[str]] = {name: set() for name in observed}
    proofs: dict[str, set[str]] = {name: set() for name in observed}
    watchdog: dict[str, set[str]] = {name: set() for name in observed}
    events: dict[str, set[str]] = {name: set() for name in observed}
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY.name:
            continue
        manifest = load_manifest(path)
        backend = manifest["backend"]
        result = await DetectionReplay(backend, tmp_path / path.stem).run(manifest)
        for item in result["states"]:
            states[backend].add(item["state"])
            proofs[backend].add(item["proof"])
            if "awaiting_reason" in item:
                awaiting[backend].add(item["awaiting_reason"])
        watchdog[backend].update(result["health"]["watchdog_recovery_actions"])
        events[backend].update(item["type"] for item in result["events"])

    for name, harness in observed.items():
        # The two states every observed harness must be able to reach and leave.
        # A harness that only ever reports one of them is not being observed, it is
        # being guessed at.
        assert {"working", "idle"} <= states[name], f"{name}: no working/idle coverage"

        # Every normalized event the harness declares it can emit must appear
        # somewhere in its own fixtures. Declaring an event kind the corpus never
        # produces is a promise nothing keeps.
        assert set(harness.normalized_events) <= events[name], (
            f"{name}: declares {sorted(set(harness.normalized_events) - events[name])} "
            f"but no fixture produces them"
        )

        # An approval-capable harness owes a scenario that actually blocks on one,
        # because "awaiting" is the state whose false readings strand a queue.
        if "approval_needed" in harness.normalized_events:
            assert "awaiting" in states[name], f"{name}: no awaiting scenario"
            assert "approval" in awaiting[name], f"{name}: no approval sub-reason"

        # Hook and transcript evidence is direct, so it proves a state. A PTY reading
        # is a screen classification, so it infers one. A harness declaring a PTY
        # source owes at least one inferred reading and at least one watchdog
        # recovery driven by it; a hooks-only harness (opencode) owes neither and is
        # not asked, because for it every reading is proof.
        assert "proven" in proofs[name], f"{name}: no proven state reading"
        if "pty" in harness.state_sources:
            assert "inferred" in proofs[name], f"{name}: declares pty but infers nothing"
            assert watchdog[name], f"{name}: declares pty but no watchdog recovery"
        else:
            assert proofs[name] == {"proven"}, (
                f"{name}: declares no pty source yet produced an inferred reading"
            )


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
