from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from swe_mux import automation_registry as registry
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent
from swe_mux.project_files import (
    _validate_observations,
    append_observation,
    parse_project_config,
    project_automations,
    read_observations,
    serialize_project_config,
    write_observations,
)
from swe_mux.tier0_store import Tier0Store, _fact_from_event

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

    async def resolve_enabled(session_id: str) -> bool:
        return session_id in enabled

    try:
        store.start(events, resolve_enabled=resolve_enabled)
        await events.emit("tool_use", session_id="on", source="transcript", tool="Edit", path="a")
        await events.emit("tool_use", session_id="off", source="transcript", tool="Edit", path="b")
        # Let the capture consumer drain both events.
        assert store._event_queue is not None
        await store._event_queue.join()
        assert len(await store.recent_facts("on")) == 1
        assert await store.recent_facts("off") == []
    finally:
        await store.stop()
        store.close()
