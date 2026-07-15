from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.fleet_intelligence import FleetIntelligence
from swe_mux.models import SessionRecord
from swe_mux.processes import OwnedProcess


def session(identity: str, *, scope: str, branch: str) -> Any:
    record = SessionRecord(
        identity,
        identity,
        "default",
        "claude",
        f"native-{identity}",
        ".",
        "claude",
        [],
    )
    record.agent_run_id = f"run-{identity}"
    record.run_project_scope_id = scope
    record.state = "working"
    record.git.branch = branch
    return SimpleNamespace(
        record=record,
        transcript_path=None,
        subscribers=set(),
        output_window=deque(),
        last_input_event_ts=0.0,
        input_owner=None,
    )


def fleet(
    tmp_path: Path,
    sessions: dict[str, Any],
    *,
    phase7_enabled: bool = False,
) -> tuple[FleetIntelligence, EventBus, AutomationStore, Any]:
    bus = EventBus()
    store = AutomationStore(tmp_path / "mux.db")
    processes = SimpleNamespace(owned={})
    previews = SimpleNamespace(items={})
    intelligence = FleetIntelligence(
        SimpleNamespace(sessions=sessions),  # type: ignore[arg-type]
        bus,
        store,
        processes,  # type: ignore[arg-type]
        previews,  # type: ignore[arg-type]
        Config(data_dir=tmp_path, phase7_observers_enabled=phase7_enabled),
    )
    return intelligence, bus, store, processes


def preview(session_id: str, *, port: int) -> Any:
    return SimpleNamespace(host="127.0.0.1", port=port, session_id=session_id)


async def test_repeated_tool_failures_emit_explainable_spiral(tmp_path: Path) -> None:
    current = session("a", scope="scope-a", branch="main")
    intelligence, bus, store, _ = fleet(tmp_path, {"a": current})
    monitor = bus.subscribe()
    intelligence._queue = bus.subscribe()
    consumer = asyncio.create_task(intelligence._consume())
    try:
        for index in range(3):
            await bus.emit(
                "tool_result",
                session_id="a",
                source="transcript",
                tool="pytest",
                success=False,
                exit_code=1,
                detail=f"failure {index}",
            )
        spiral = None
        for _ in range(10):
            item = await asyncio.wait_for(monitor.get(), timeout=0.2)
            if item.type == "stalled":
                spiral = item
                break
        assert spiral is not None
        assert spiral.payload["subtype"] == "spiral"
        assert spiral.payload["confidence"] == 0.9
        assert spiral.payload["evidence"][0]["signal"] == "repeated_tool_failure"
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        store.close()


async def test_cross_session_dev_server_interlock_uses_owned_connections(
    tmp_path: Path,
) -> None:
    provider = session("provider", scope="scope-a", branch="main")
    consumer = session("consumer", scope="scope-b", branch="feature")
    intelligence, bus, store, processes = fleet(
        tmp_path, {"provider": provider, "consumer": consumer}
    )
    processes.owned = {
        (10, 1.0): OwnedProcess(
            10,
            None,
            "provider",
            "node",
            "vite",
            1.0,
            None,
            0,
            1,
            [{"host": "127.0.0.1", "port": 5173}],
            [],
        ),
        (20, 2.0): OwnedProcess(
            20,
            None,
            "consumer",
            "browser",
            "browser",
            2.0,
            None,
            0,
            1,
            [],
            [],
            [
                {
                    "local_host": "127.0.0.1",
                    "local_port": 55000,
                    "remote_host": "127.0.0.1",
                    "remote_port": 5173,
                }
            ],
        ),
    }
    monitor = bus.subscribe()

    await intelligence._interlocks(time.time())

    emitted = [await monitor.get() for _ in range(monitor.qsize())]
    interlock = next(item for item in emitted if item.type == "environment_interlock")
    assert interlock.payload["kind"] == "cross_session_dev_server"
    assert interlock.payload["sessions"] == ["consumer", "provider"]
    notifications = await store.notifications()
    assert notifications[0]["kind"] == "environment_interlock"
    store.close()


async def test_same_branch_and_port_collision_interlocks_are_explainable(
    tmp_path: Path,
) -> None:
    first = session("a", scope="scope-a", branch="main")
    second = session("b", scope="scope-a", branch="main")
    intelligence, bus, store, processes = fleet(tmp_path, {"a": first, "b": second})
    processes.owned = {
        (10, 1.0): OwnedProcess(
            10, None, "a", "node", "vite", 1.0, None, 0, 1,
            [{"host": "127.0.0.1", "port": 5173}], [],
        ),
        (20, 2.0): OwnedProcess(
            20, None, "b", "node", "vite", 2.0, None, 0, 1,
            [{"host": "127.0.0.1", "port": 5173}], [],
        ),
    }
    monitor = bus.subscribe()

    await intelligence._interlocks(time.time())

    events = [await monitor.get() for _ in range(monitor.qsize())]
    interlocks = {
        item.payload["kind"]: item for item in events if item.type == "environment_interlock"
    }
    assert set(interlocks) == {"same_branch", "port_collision"}
    assert interlocks["same_branch"].payload["evidence"][1] == {
        "signal": "git_branch",
        "value": "main",
    }
    assert interlocks["port_collision"].payload["evidence"][1] == {
        "signal": "listener_port",
        "value": 5173,
    }
    store.close()


async def test_registered_previews_participate_in_port_collision_evidence(
    tmp_path: Path,
) -> None:
    first = session("a", scope="scope-a", branch="main")
    second = session("b", scope="scope-b", branch="feature")
    intelligence, bus, store, _ = fleet(tmp_path, {"a": first, "b": second})
    intelligence.previews.items = {
        "preview-a": preview("a", port=4173),
        "preview-b": preview("b", port=4173),
    }
    monitor = bus.subscribe()

    await intelligence._interlocks(time.time())

    events = [await monitor.get() for _ in range(monitor.qsize())]
    collision = next(
        item
        for item in events
        if item.type == "environment_interlock" and item.payload["kind"] == "port_collision"
    )
    assert collision.payload["evidence"][2] == {
        "signal": "registered_preview_sessions",
        "value": ["a", "b"],
    }
    store.close()


async def test_completion_claim_without_test_emits_claim_unverified(tmp_path: Path) -> None:
    current = session("a", scope="scope-a", branch="main")
    current.transcript_path = (
        Path(__file__).parent / "fixtures" / "transcripts" / "v1" / "claude.jsonl"
    )
    intelligence, bus, store, _ = fleet(tmp_path, {"a": current})
    intelligence._turn_started["a"] = 10
    monitor = bus.subscribe()

    await intelligence._claim_check("a", 20)

    emitted = [await monitor.get() for _ in range(monitor.qsize())]
    claim = next(item for item in emitted if item.type == "claim_unverified")
    assert claim.payload["confidence"] == 0.75
    assert claim.payload["evidence"][1] == {
        "signal": "test_tool_seen_in_turn",
        "value": False,
    }
    store.close()


async def test_prior_experience_is_suggested_as_annotation_only(tmp_path: Path) -> None:
    current = session("a", scope="scope-a", branch="main")
    intelligence, bus, store, _ = fleet(tmp_path, {"a": current})
    await store.add_experience(
        project_scope_id="scope-a",
        backend="codex",
        error="module parser failed",
        resolution="regenerate the parser fixture",
        source_run_id="older-run",
        confidence=0.8,
    )
    monitor = bus.subscribe()
    intelligence._queue = bus.subscribe()
    consumer = asyncio.create_task(intelligence._consume())
    try:
        await bus.emit(
            "tool_result",
            session_id="a",
            source="transcript",
            tool="pytest",
            success=False,
            detail="module parser failed",
        )
        for _ in range(20):
            if await store.annotations(agent_run_id="run-a"):
                break
            await asyncio.sleep(0)
        annotations = await store.annotations(agent_run_id="run-a")
        assert annotations[0]["tag"] == "prior-resolution"
        assert annotations[0]["provenance"] == "experience_index"
        assert "regenerate the parser fixture" in annotations[0]["content"]
        emitted = [await monitor.get() for _ in range(monitor.qsize())]
        assert any(item.type == "annotation_created" for item in emitted)
        assert not any(item.type in {"terminal_input", "session_spawned"} for item in emitted)
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        store.close()


async def test_digest_is_persisted_and_injection_gate_never_authorizes(
    tmp_path: Path,
) -> None:
    current = session("a", scope="scope-a", branch="main")
    current.record.state = "idle"
    intelligence, _, store, _ = fleet(tmp_path, {"a": current}, phase7_enabled=True)
    await store.notify(
        agent_run_id=current.record.agent_run_id,
        session_id="a",
        rule_id=None,
        kind="approval",
        title="Approval",
        message="Review required",
        severity="warning",
    )
    intelligence._last_digest = 0

    await intelligence.inspect()

    notifications = await store.notifications()
    assert any(item["kind"] == "attention_digest" for item in notifications)
    safety = intelligence.injection_safety()
    assert safety["research_only"] is True
    assert safety["authorizes_actuation"] is False
    assert safety["sessions"][0]["candidate_safe"] is False
    assert safety["sessions"][0]["authorized"] is False
    store.close()


async def test_absence_report_uses_persisted_last_activity_checkpoint(tmp_path: Path) -> None:
    current = session("a", scope="scope-a", branch="main")
    checkpoint_ts = time.time() - 1
    current.record.last_activity_ts = time.time()
    intelligence, _, store, _ = fleet(tmp_path, {"a": current})
    await store.set_checkpoint("fleet:last_user_activity", {"ts": checkpoint_ts})
    await store.create_annotation(
        agent_run_id="run-a",
        session_id="a",
        tag="turn-summary",
        content="Completed the requested parser update.",
        source_event_seq=1,
        rule_id="summary",
        rule_revision="r1",
        provenance="fixture",
    )

    report = await intelligence.absence_report()

    assert report["since"] == checkpoint_ts
    assert report["sessions"][0]["session_id"] == "a"
    assert report["annotations"][0]["content"].startswith("Completed")
    store.close()


async def test_lineage_is_idempotent_and_batch_records_have_public_shape(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    first = await store.add_lineage("run-a", "run-b", "review", {"reviewed": True})
    second = await store.add_lineage("run-a", "run-b", "review", {"reviewed": False})

    assert second == first
    assert (await store.lineage("run-a"))[0] == first

    batch_id = await store.create_batch("experience", ["run-a", "run-b"])
    await store.finish_batch(
        batch_id,
        status="completed",
        preview=[{"run_id": "run-a", "result": {"summary": "done"}}],
        calls=1,
        tokens=12,
        cost_usd=0.01,
    )
    batch = (await store.batches())[0]
    assert batch["run_ids"] == ["run-a", "run-b"]
    assert "selection_json" not in batch
    assert "preview_json" not in batch
    store.close()


async def test_accumulators_pruned_on_session_exit(tmp_path: Path) -> None:
    current = session("a", scope="scope-a", branch="main")
    intelligence, bus, store, _ = fleet(tmp_path, {"a": current})
    intelligence._queue = bus.subscribe()
    consumer = asyncio.create_task(intelligence._consume())
    try:
        await bus.emit("turn_started", session_id="a")
        await bus.emit("turn_ended", session_id="a")
        await bus.emit("tool_result", session_id="a", tool="pytest", success=False, exit_code=1)
        await bus.emit("tool_result", session_id="a", tool="pytest", success=True)
        for _ in range(50):
            await asyncio.sleep(0)
            if all(
                "a" in acc
                for acc in (
                    intelligence._failures,
                    intelligence._turn_started,
                    intelligence._last_turn,
                    intelligence._last_test,
                )
            ):
                break
        assert "a" in intelligence._failures
        assert "a" in intelligence._turn_started
        assert "a" in intelligence._last_turn
        assert "a" in intelligence._last_test

        await bus.emit("session_exited", session_id="a")
        for _ in range(50):
            await asyncio.sleep(0)
            if "a" not in intelligence._failures:
                break
        assert "a" not in intelligence._failures
        assert "a" not in intelligence._turn_started
        assert "a" not in intelligence._last_turn
        assert "a" not in intelligence._last_test
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        store.close()


async def test_session_crashed_also_prunes_accumulators(tmp_path: Path) -> None:
    current = session("a", scope="scope-a", branch="main")
    intelligence, bus, store, _ = fleet(tmp_path, {"a": current})
    intelligence._queue = bus.subscribe()
    consumer = asyncio.create_task(intelligence._consume())
    try:
        await bus.emit("turn_started", session_id="a")
        for _ in range(50):
            await asyncio.sleep(0)
            if "a" in intelligence._turn_started:
                break
        assert "a" in intelligence._turn_started
        await bus.emit("session_crashed", session_id="a")
        for _ in range(50):
            await asyncio.sleep(0)
            if "a" not in intelligence._turn_started:
                break
        assert "a" not in intelligence._turn_started
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        store.close()
