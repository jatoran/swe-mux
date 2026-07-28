from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from swe_mux import automation_registry as registry
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent
from swe_mux.observation import bounded_detail, parse_test_outcome, tool_result_evidence
from swe_mux.project_files import (
    _validate_observations,
    append_observation,
    parse_project_config,
    project_automations,
    read_observations,
    serialize_project_config,
    write_observations,
)
from swe_mux.tier0_store import (
    Tier0Context,
    Tier0Store,
    _fact_from_event,
    bound_detail_payload,
)

# ---- Enablement registry / DAG ----------------------------------------------


def test_registry_is_acyclic_and_dependencies_exist() -> None:
    # Import-time validation already runs; re-invoke to assert it stays valid.
    registry._validate_registry()
    for automation in registry.REGISTRY.values():
        for dependency in automation.requires:
            assert dependency in registry.REGISTRY


def test_consumer_blocked_until_substrate_opted_in() -> None:
    # provenance_graph requires tier0 which requires raw_store.
    resolution = registry.resolve({"provenance_graph"})
    assert not resolution.is_enabled("provenance_graph")
    assert set(resolution.blocked["provenance_graph"]) == {"raw_store", "tier0"}

    resolution = registry.resolve({"provenance_graph", "tier0", "raw_store"})
    assert resolution.is_enabled("provenance_graph")
    assert resolution.blocked == {}


def test_disabling_substrate_disables_dependents() -> None:
    # Drop tier0: provenance_graph must fall back to blocked (effectively off).
    resolution = registry.resolve({"provenance_graph", "raw_store"})
    assert not resolution.is_enabled("provenance_graph")
    assert "tier0" in resolution.blocked["provenance_graph"]


def test_no_dependency_consumer_enables_alone() -> None:
    resolution = registry.resolve({"observation_inbox"})
    assert resolution.is_enabled("observation_inbox")


def test_defaults_are_inherited_and_overridden() -> None:
    defaults = {"raw_store": True, "tier0": True}
    # Project inherits substrate from defaults, adds a consumer.
    resolution = registry.resolve_config({"provenance_graph": True}, defaults)
    assert resolution.is_enabled("provenance_graph")
    # Project can override a default off, disabling dependents.
    resolution = registry.resolve_config({"tier0": False, "provenance_graph": True}, defaults)
    assert not resolution.is_enabled("provenance_graph")


def test_unknown_ids_are_ignored() -> None:
    assert registry.requested_from_config({"not_a_real_automation": True}) == set()


# ---- Per-project config field ------------------------------------------------


def test_project_config_round_trips_automations() -> None:
    data = serialize_project_config({"automations": {"tier0": True, "raw_store": True}})
    parsed = parse_project_config(data)
    assert parsed["automations"] == {"tier0": True, "raw_store": True}


def test_project_config_rejects_unknown_automation() -> None:
    with pytest.raises(ValueError, match="unknown automations"):
        parse_project_config(b'version = 1\nautomations = { bogus = true }\n')


def test_project_config_rejects_non_boolean_automation() -> None:
    with pytest.raises(ValueError, match="table of boolean"):
        parse_project_config(b'version = 1\nautomations = { tier0 = "yes" }\n')


def test_project_automations_reads_root(tmp_path: Path) -> None:
    mux_dir = tmp_path / ".swe-mux"
    mux_dir.mkdir()
    (mux_dir / "config.toml").write_bytes(
        serialize_project_config({"automations": {"raw_store": True, "tier0": True}})
    )
    assert project_automations(tmp_path) == {"raw_store": True, "tier0": True}
    # A project with no config opts into nothing.
    assert project_automations(tmp_path / "nope") == {}


# ---- Tier 0 fact extraction (pure) ------------------------------------------


def test_fact_from_event_classifies_file_write() -> None:
    event = MuxEvent(1.0, "s1", "transcript", "tool_use", {"tool": "Edit", "path": "a/b.py"}, seq=7)
    fact = _fact_from_event(event)
    assert fact is not None
    assert fact["kind"] == "file_write"
    assert fact["target"] == "a/b.py"
    assert fact["source_seq"] == 7
    assert fact["fingerprint"]


def test_fact_from_event_uses_normalized_target_and_content_hash() -> None:
    # The adapter now emits a normalized target + parse-time content hash.
    event = MuxEvent(
        1.0,
        "s1",
        "transcript",
        "tool_use",
        {"tool": "Edit", "target": "src/x.py", "content_hash": "abc123"},
    )
    fact = _fact_from_event(event)
    assert fact is not None
    assert fact["target"] == "src/x.py"
    assert fact["content_hash"] == "abc123"


def test_content_hash_changes_the_fingerprint() -> None:
    # Same edit target, different written content → different fingerprint (progress).
    base = {"tool": "Edit", "target": "x.py"}
    same = _fact_from_event(MuxEvent(1.0, "s", "t", "tool_use", {**base, "content_hash": "h1"}))
    other = _fact_from_event(MuxEvent(2.0, "s", "t", "tool_use", {**base, "content_hash": "h2"}))
    repeat = _fact_from_event(MuxEvent(3.0, "s", "t", "tool_use", {**base, "content_hash": "h1"}))
    assert same is not None and other is not None and repeat is not None
    assert same["fingerprint"] != other["fingerprint"]
    assert same["fingerprint"] == repeat["fingerprint"]  # identical edit repeated = loop signal


def test_tool_call_evidence_hashes_write_payload_race_free() -> None:
    from swe_mux.observation import tool_call_evidence

    target, content_hash = tool_call_evidence({"file_path": "a/b.py", "new_string": "hello"})
    assert target == "a/b.py"
    assert content_hash is not None
    # A JSON-string argument (Codex function_call shape) parses too.
    codex_target, codex_hash = tool_call_evidence('{"command": "pytest -q"}')
    assert codex_target == "pytest -q"
    assert codex_hash is None  # a command has no written content to hash


def test_fact_from_event_ignores_uncaptured_types() -> None:
    assert _fact_from_event(MuxEvent(1.0, "s1", "mux", "turn_started", {})) is None


def test_fingerprint_stable_across_volatile_detail() -> None:
    first = _fact_from_event(
        MuxEvent(1.0, "s", "t", "tool_use", {"tool": "Bash", "command": "pytest", "exit_code": 0})
    )
    second = _fact_from_event(
        MuxEvent(9.0, "s", "t", "tool_use", {"tool": "Bash", "command": "pytest", "exit_code": 0})
    )
    assert first is not None and second is not None
    assert first["fingerprint"] == second["fingerprint"]


# ---- Tier 0: tool_result classification and fingerprints ---------------------


def _result_fact(**payload: object) -> dict[str, object]:
    fact = _fact_from_event(MuxEvent(1.0, "s", "transcript", "tool_result", dict(payload)))
    assert fact is not None
    return fact


def test_tool_result_kind_follows_the_tool_not_the_event_type() -> None:
    """Results used to keep kind='tool_result' for every tool, contradicting the
    documented vocabulary and collapsing per-action queries."""
    assert _result_fact(tool="Bash", success=True, exit_code=0)["kind"] == "command_result"
    assert _result_fact(tool="Edit", success=True, exit_code=None)["kind"] == "file_write_result"
    assert _result_fact(tool="Read", success=True, exit_code=None)["kind"] == "file_read_result"
    # An unrecognized tool keeps the generic kind.
    assert _result_fact(tool="Mystery", success=True)["kind"] == "tool_result"


def test_result_fingerprint_separates_success_from_failure() -> None:
    """`exit_code: None` was stringified as the literal class "None", so every
    Claude tool_result — success and failure alike — shared one fingerprint."""
    ok = _result_fact(tool="Read", call_id="a", success=True, exit_code=None)
    failed = _result_fact(tool="Read", call_id="b", success=False, exit_code=None)
    assert ok["fingerprint"] != failed["fingerprint"]


def test_result_fingerprint_is_per_action_not_per_exit_class() -> None:
    read = _result_fact(tool="Read", target="a.py", success=True, exit_code=None)
    write = _result_fact(tool="Edit", target="a.py", success=True, exit_code=None)
    other_target = _result_fact(tool="Read", target="b.py", success=True, exit_code=None)
    assert len({read["fingerprint"], write["fingerprint"], other_target["fingerprint"]}) == 3


def test_test_results_fingerprint_on_the_failing_set() -> None:
    """The loop detector's no-progress gate is "the failing-test set didn't
    shrink" — so identical failures repeat a fingerprint and progress breaks it."""
    outcome = {"framework": "pytest", "passed": 8, "failed": 2, "errors": 0, "skipped": 0}
    first = _result_fact(
        tool="Bash",
        success=False,
        exit_code=1,
        test_outcome={**outcome, "failing_tests": ["a", "b"]},
    )
    # Same failures, listed in a different order and after unrelated output churn.
    repeat = _result_fact(
        tool="Bash",
        success=False,
        exit_code=1,
        test_outcome={**outcome, "failing_tests": ["b", "a"]},
    )
    progress = _result_fact(
        tool="Bash",
        success=False,
        exit_code=1,
        test_outcome={**outcome, "failed": 1, "failing_tests": ["a"]},
    )
    assert first["kind"] == "test_result"
    assert first["fingerprint"] == repeat["fingerprint"]
    assert first["fingerprint"] != progress["fingerprint"]


def test_git_fact_carries_commit_identity_and_dirty_set() -> None:
    payload = {"head": "a" * 40, "dirty_hash": "d1", "target": "C:/repo", "content_hash": "a" * 40}
    fact = _fact_from_event(MuxEvent(1.0, "s", "daemon", "git_changed", payload))
    assert fact is not None
    assert fact["kind"] == "git"
    assert fact["content_hash"] == "a" * 40
    moved = _fact_from_event(
        MuxEvent(2.0, "s", "daemon", "git_changed", {**payload, "dirty_hash": "d2"})
    )
    assert moved is not None
    assert fact["fingerprint"] != moved["fingerprint"]


# ---- Tier 0: bounded detail --------------------------------------------------


def test_bound_detail_payload_always_round_trips() -> None:
    bounded = bound_detail_payload({"detail": "x" * 50_000, "tool": "Bash", "exit_code": 1})
    encoded = json.dumps(bounded)
    assert len(encoded) <= 4096
    assert json.loads(encoded)["tool"] == "Bash"


def test_bound_detail_payload_keeps_structure_and_reports_drops() -> None:
    # Many wide values: bounding each one is not enough, so whole keys are
    # dropped widest-first — and the drop is recorded rather than silent.
    payload: dict[str, object] = {f"k{index}": "x" * 3000 for index in range(10)}
    payload["tool"] = "Bash"
    payload["exit_code"] = 2
    bounded = bound_detail_payload(payload)
    assert len(json.dumps(bounded)) <= 4096
    assert bounded["_truncated"] is True
    assert bounded["_dropped_keys"]
    # Protected structural keys survive the drop pass.
    assert bounded["tool"] == "Bash"
    assert bounded["exit_code"] == 2


def test_bounded_detail_keeps_the_tail_where_verdicts_live() -> None:
    text = "start" + "-" * 20_000 + "1 failed, 2 passed in 0.5s"
    bounded = bounded_detail(text, limit=400)
    assert len(bounded) <= 400
    assert bounded.startswith("start")
    assert bounded.endswith("1 failed, 2 passed in 0.5s")


# ---- Tier 0: structured test facts -------------------------------------------


def test_parse_test_outcome_pytest() -> None:
    outcome = parse_test_outcome(
        "FAILED tests/test_a.py::test_one - AssertionError\n"
        "ERROR tests/test_b.py::test_two\n"
        "===== 1 failed, 693 passed, 1 skipped, 1 error in 48.80s =====\n"
    )
    assert outcome is not None
    assert outcome["framework"] == "pytest"
    assert (outcome["passed"], outcome["failed"], outcome["errors"], outcome["skipped"]) == (
        693,
        1,
        1,
        1,
    )
    assert outcome["failing_tests"] == ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    assert outcome["ok"] is False


def test_parse_test_outcome_pytest_all_green() -> None:
    outcome = parse_test_outcome("===== 12 passed in 1.20s =====")
    assert outcome is not None
    assert outcome["ok"] is True
    assert outcome["failing_tests"] == []


def test_parse_test_outcome_jest_and_vitest() -> None:
    jest = parse_test_outcome(
        "  ● Widget › renders\n\nTests:       2 failed, 8 passed, 10 total\nSnapshots:   0 total\n"
    )
    assert jest is not None
    assert (jest["failed"], jest["passed"]) == (2, 8)
    assert jest["failing_tests"] == ["Widget › renders"]

    vitest = parse_test_outcome(" Tests  1 failed | 4 passed (5)\n")
    assert vitest is not None
    assert (vitest["failed"], vitest["passed"]) == (1, 4)


def test_parse_test_outcome_go_and_cargo() -> None:
    go = parse_test_outcome("--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.01s)\nFAIL\n")
    assert go is not None
    assert go["framework"] == "go"
    assert go["failing_tests"] == ["TestB"]

    cargo = parse_test_outcome(
        "test tests::broken ... FAILED\n"
        "test result: FAILED. 3 passed; 1 failed; 2 ignored; 0 measured\n"
    )
    assert cargo is not None
    assert cargo["framework"] == "cargo"
    assert (cargo["passed"], cargo["failed"], cargo["skipped"]) == (3, 1, 2)
    assert cargo["failing_tests"] == ["tests::broken"]


def test_parse_test_outcome_unittest() -> None:
    outcome = parse_test_outcome(
        "FAIL: test_one (pkg.Case)\nERROR: test_two (pkg.Case)\n"
        "Ran 5 tests in 0.10s\n\nFAILED (failures=1, errors=1)\n"
    )
    assert outcome is not None
    assert outcome["framework"] == "unittest"
    assert (outcome["passed"], outcome["failed"], outcome["errors"]) == (3, 1, 1)
    assert outcome["failing_tests"] == ["test_one (pkg.Case)", "test_two (pkg.Case)"]


def test_parse_test_outcome_ignores_ordinary_output() -> None:
    assert parse_test_outcome("") is None
    assert parse_test_outcome("Wrote 12 lines to src/app.ts") is None
    assert parse_test_outcome("error: cannot find module 'x'") is None


def test_tool_result_evidence_hashes_full_text_and_parses_tests() -> None:
    content_hash, outcome = tool_result_evidence("===== 2 passed in 0.1s =====")
    assert content_hash is not None
    assert outcome is not None and outcome["passed"] == 2
    # Identical output hashes identically (the no-progress signal); different
    # output does not.
    again, _ = tool_result_evidence("===== 2 passed in 0.1s =====")
    changed, _ = tool_result_evidence("===== 3 passed in 0.1s =====")
    assert content_hash == again
    assert content_hash != changed


# ---- Tier 0 store ------------------------------------------------------------


@pytest.fixture
def tier0_path() -> Path:
    path = Path(__file__).parent / f".tier0-{uuid.uuid4().hex}.db"
    yield path
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


async def test_record_and_query_fact(tier0_path: Path) -> None:
    store = Tier0Store(tier0_path)
    try:
        await store.record_fact(session_id="s1", kind="file_write", target="x.py", source_seq=3)
        facts = await store.recent_facts("s1")
        assert len(facts) == 1
        assert facts[0]["kind"] == "file_write"
        assert facts[0]["source_seq"] == 3
    finally:
        store.close()


async def test_prune_drops_old_facts(tier0_path: Path) -> None:
    store = Tier0Store(tier0_path, retention_days=1)
    try:
        await store.record_fact(session_id="s1", kind="tool", created_at=0.0)
        await store.record_fact(session_id="s1", kind="tool")
        removed = await store.prune()
        assert removed == 1
        assert len(await store.recent_facts("s1")) == 1
    finally:
        store.close()


# ---- Observation inbox -------------------------------------------------------


def test_validate_observations_enforces_bounds() -> None:
    with pytest.raises(ValueError, match="1–2000 characters"):
        _validate_observations([{"id": "a", "body": ""}])
    with pytest.raises(ValueError, match="unique"):
        _validate_observations([{"id": "a", "body": "x"}, {"id": "a", "body": "y"}])
    cleaned = _validate_observations([{"id": "abc", "body": "note", "done": True}])
    assert cleaned[0]["done"] is True


async def test_observation_append_read_and_replace(tmp_path: Path) -> None:
    await append_observation(tmp_path, "account row wraps at 320px")
    await append_observation(tmp_path, "  spinner never stops on error  ")
    state = await read_observations(tmp_path)
    assert [item["body"] for item in state["observations"]] == [
        "account row wraps at 320px",
        "spinner never stops on error",
    ]
    assert (tmp_path / ".swe-mux" / "observations.json").is_file()

    # Mark the first done and drop the second via a revision-checked replace.
    kept = [{**state["observations"][0], "done": True}]
    replaced = await write_observations(tmp_path, kept, state["revision"])
    assert len(replaced["observations"]) == 1
    assert replaced["observations"][0]["done"] is True

    # A stale revision is rejected rather than clobbering newer data.
    with pytest.raises(ValueError, match="changed externally"):
        await write_observations(tmp_path, [], state["revision"])


async def test_observation_append_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        await append_observation(tmp_path, "   ")


async def test_gated_capture_respects_enablement(tier0_path: Path) -> None:
    store = Tier0Store(tier0_path)
    events = EventBus()
    enabled = {"on"}

    async def resolve_context(session_id: str) -> Tier0Context | None:
        if session_id not in enabled:
            return None
        return Tier0Context(agent_run_id="run-1", project_id="proj-1")

    try:
        store.start(events, resolve_context=resolve_context)
        await events.emit("tool_use", session_id="on", source="transcript", tool="Edit", path="a")
        await events.emit("tool_use", session_id="off", source="transcript", tool="Edit", path="b")
        # Let the capture consumer drain both events.
        assert store._event_queue is not None
        await store._event_queue.join()
        captured = await store.recent_facts("on")
        assert len(captured) == 1
        assert await store.recent_facts("off") == []
        # Ownership must be stamped: per-run and per-project queries are the
        # substrate's entire purpose and cannot be recovered from session_id.
        assert captured[0]["agent_run_id"] == "run-1"
        assert captured[0]["project_id"] == "proj-1"
        assert store.capture_stats()["captured"] == 1
        assert store.capture_stats()["dropped"] == 0
    finally:
        await store.stop()
        store.close()


async def test_large_payload_is_captured_not_silently_dropped(tier0_path: Path) -> None:
    """A long tool_result must survive capture and re-parse.

    Truncating the *serialized* JSON produced a string `json.loads` could not
    read, and the capture guard swallowed the error: exactly the long
    test/build results the substrate exists for were destroyed, silently.
    """
    store = Tier0Store(tier0_path)
    events = EventBus()

    async def resolve_context(session_id: str) -> Tier0Context | None:
        del session_id
        return Tier0Context()

    try:
        store.start(events, resolve_context=resolve_context)
        await events.emit(
            "tool_result",
            session_id="s1",
            source="transcript",
            tool="Bash",
            success=False,
            exit_code=1,
            detail="E" * 40_000,
        )
        assert store._event_queue is not None
        await store._event_queue.join()
        facts = await store.recent_facts("s1")
        assert len(facts) == 1, "the fact was dropped instead of bounded"
        assert store.capture_stats()["dropped"] == 0
        detail = json.loads(facts[0]["detail_json"])  # must always re-parse
        assert len(detail["detail"]) < 40_000
        # Structure the consumers key on is never the part that gets dropped.
        assert detail["tool"] == "Bash"
        assert detail["exit_code"] == 1
        assert len(facts[0]["detail_json"]) <= 4096
    finally:
        await store.stop()
        store.close()


async def test_capture_failure_is_counted_and_reported(tier0_path: Path) -> None:
    """A capture failure must be observable; a bare `pass` made loss invisible."""
    store = Tier0Store(tier0_path)
    events = EventBus()

    async def resolve_context(session_id: str) -> Tier0Context | None:
        del session_id
        raise RuntimeError("resolver exploded")

    try:
        store.start(events, resolve_context=resolve_context)
        await events.emit("tool_use", session_id="s1", source="transcript", tool="Edit")
        assert store._event_queue is not None
        await store._event_queue.join()
        stats = store.capture_stats()
        assert stats["dropped"] == 1
        assert "resolver exploded" in str(stats["last_error"])
        assert stats["running"] is True  # capture must never break the event loop
    finally:
        await store.stop()
        store.close()


# ---- Per-project opt-in surface ---------------------------------------------


@pytest.mark.asyncio
async def test_automation_toggle_surface_reports_the_dependency_graph(tmp_path: Path) -> None:
    # "Do not ship a fourth consumer without the toggle": enabling one today
    # means hand-editing .swe-mux/config.toml, and a flat checkbox list would not
    # be enough — a consumer whose substrate is off must show *what it needs*.
    from types import SimpleNamespace

    from swe_mux.server import get_project_automations, put_project_automations

    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))
    emitted: list[str] = []

    class Events:
        async def emit(self, kind: str, **_payload: object) -> None:
            emitted.append(kind)

    def request(body: dict[str, object] | None = None) -> object:
        return SimpleNamespace(
            match_info={"project_id": "p1"},
            app={"projects": SimpleNamespace(projects={"p1": project}), "events": Events()},
            json=lambda: _resolved(body or {}),
        )

    async def _resolved(value: dict[str, object]) -> dict[str, object]:
        return value

    response = await get_project_automations(request())  # type: ignore[arg-type]
    payload = json.loads(response.body)
    by_id = {item["id"]: item for item in payload["automations"]}
    assert payload["requested"] == {}
    assert by_id["loop_detection"]["requires"] == ["tier0"]
    assert by_id["tier0"]["requires"] == ["raw_store"]
    assert by_id["scan_timeline"]["implemented"] is False

    # A consumer without its substrate resolves as blocked, naming what is missing.
    partial = await put_project_automations(  # type: ignore[arg-type]
        request({"automations": {"loop_detection": True}, "revision": payload["revision"]})
    )
    blocked = json.loads(partial.body)
    assert blocked["enabled"] == []
    assert blocked["blocked"]["loop_detection"] == ["raw_store", "tier0"]

    full = await put_project_automations(  # type: ignore[arg-type]
        request(
            {
                "automations": {"loop_detection": True, "tier0": True, "raw_store": True},
                "revision": blocked["revision"],
            }
        )
    )
    enabled = json.loads(full.body)
    assert set(enabled["enabled"]) == {"loop_detection", "tier0", "raw_store"}
    assert enabled["blocked"] == {}
    # The file stays the source of truth.
    assert "automations" in (tmp_path / ".swe-mux" / "config.toml").read_text(encoding="utf-8")
    assert emitted.count("project_configuration_changed") == 2


@pytest.mark.asyncio
async def test_an_unimplemented_automation_cannot_be_switched_on(tmp_path: Path) -> None:
    # A toggle that reads "on" while nothing runs behind it is worse than an
    # absent toggle: it makes the user believe a project is covered.
    from types import SimpleNamespace

    from swe_mux.server import put_project_automations

    project = SimpleNamespace(id="p1", name="Main", root=str(tmp_path))

    async def body() -> dict[str, object]:
        return {"automations": {"scan_timeline": True, "tier0": True, "raw_store": True}}

    response = await put_project_automations(  # type: ignore[arg-type]
        SimpleNamespace(
            match_info={"project_id": "p1"},
            app={"projects": SimpleNamespace(projects={"p1": project}), "events": None},
            json=body,
        )
    )
    assert response.status == 409
    assert json.loads(response.body)["code"] == "automation_not_implemented"
