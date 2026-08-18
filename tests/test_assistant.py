"""Mux assistant (Phase 10.6): store, trust policy, tool bridge, turn loop."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.assistant import (
    ACTION_CLASS_CONSEQUENTIAL,
    ACTION_CLASS_NAVIGATION,
    ACTION_CLASS_READ,
    ACTION_CLASS_REVERSIBLE,
    ASSISTANT_RULE_ID,
    AssistantError,
    AssistantService,
    AssistantStore,
    action_snapshot,
    speech_form,
    split_sentences,
)
from swe_mux.config import load_config, update_config
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent, ProjectRecord, SessionRecord
from swe_mux.openrouter import OpenRouterToolTurn

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


class LedgerStub:
    def __init__(self, spent_usd: float = 0.0) -> None:
        self.spent_usd = spent_usd
        self.started: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.spend_rows: list[dict[str, Any]] = []

    async def spend(self, *, rule_id: str | None = None) -> dict[str, float | int]:
        assert rule_id == ASSISTANT_RULE_ID
        return {"tokens": 0, "cost_usd": self.spent_usd}

    async def observer_started(self, **kwargs: Any) -> str:
        self.started.append(kwargs)
        return f"call-{len(self.started)}"

    async def observer_finished(self, call_id: str, **kwargs: Any) -> None:
        self.finished.append({"call_id": call_id, **kwargs})

    async def add_spend(self, **kwargs: Any) -> None:
        self.spend_rows.append(kwargs)


def tool_turn(
    content: str = "", tool_calls: list[dict[str, Any]] | None = None
) -> OpenRouterToolTurn:
    calls = tool_calls or []
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return OpenRouterToolTurn(
        generation_id="gen-1",
        requested_model="test/assistant-model",
        resolved_model="test/assistant-model",
        content=content,
        tool_calls=calls,
        message=message,
        finish_reason="stop",
        input_tokens=200,
        output_tokens=50,
        cost_usd=0.001,
        latency_ms=300,
    )


class ToolProviderStub:
    """Scripted turns: each call pops the next one; runs past the script fail."""

    def __init__(self, turns: list[OpenRouterToolTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def complete_tools(self, **kwargs: Any) -> OpenRouterToolTurn:
        self.calls.append(kwargs)
        if not self.turns:
            raise AssertionError("provider called past its script")
        return self.turns.pop(0)


class QueueStub:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []

    async def enqueue(self, **kwargs: Any) -> dict[str, Any]:
        self.enqueued.append(kwargs)
        return {"id": "m-1", "armed": kwargs.get("armed", False), "state": "pending"}


def make_service(
    tmp_path: Path,
    turns: list[OpenRouterToolTurn] | None = None,
    *,
    enabled: bool = True,
    trust: str = "confirm",
    ledger: LedgerStub | None = None,
) -> tuple[AssistantService, list[MuxEvent], QueueStub, dict[str, Any]]:
    config = load_config(tmp_path / "config.toml")
    update_config(
        config,
        {
            "assistant_enabled": enabled,
            "assistant_model": "test/assistant-model",
            "assistant_trust_reversible": trust,
        },
    )
    emitted: list[MuxEvent] = []
    events = EventBus()
    original_emit = events.emit

    async def capture(event_type: str, **kwargs: Any) -> MuxEvent:
        event = await original_emit(event_type, **kwargs)
        emitted.append(event)
        return event

    events.emit = capture  # type: ignore[method-assign]
    record = SessionRecord(
        id="s1",
        name="backend agent",
        project_id="p1",
        backend="claude",
        native_session_id="native-1",
        cwd=str(tmp_path),
        exe="claude.exe",
        args=[],
        state="idle",
        agent_run_id="run-1",
    )
    other = SessionRecord(
        id="s2",
        name="backend worker",
        project_id="p1",
        backend="codex",
        native_session_id="native-2",
        cwd=str(tmp_path),
        exe="codex.exe",
        args=[],
        state="working",
    )
    sessions = SimpleNamespace(
        sessions={
            "s1": SimpleNamespace(record=record, transcript_path=None),
            "s2": SimpleNamespace(record=other, transcript_path=None),
        }
    )
    project = ProjectRecord(id="p1", name="pixel lab", root=str(tmp_path), position=0)
    projects = SimpleNamespace(
        projects={"p1": project}, ordered_projects=lambda: [project]
    )
    store = AssistantStore(config.database_path)
    queue = QueueStub()
    side_effects: dict[str, Any] = {"spawned": [], "interrupted": [], "ended": []}

    async def spawn_op(body: dict[str, Any]) -> Any:
        side_effects["spawned"].append(body)
        return SimpleNamespace(record=SimpleNamespace(name="new session"))

    async def interrupt_op(session: Any) -> None:
        side_effects["interrupted"].append(session.record.id)

    async def end_op(session: Any, reason: str) -> None:
        side_effects["ended"].append((session.record.id, reason))

    service = AssistantService(
        config,
        events,
        cast(Any, sessions),
        cast(Any, projects),
        store,
        cast(Any, ledger or LedgerStub()),
        cast(Any, ToolProviderStub(turns or [])),
        prompt_queue=cast(Any, queue),
        spawn_op=spawn_op,
        interrupt_op=interrupt_op,
        end_op=end_op,
    )
    return service, emitted, queue, side_effects


async def run_turn(service: AssistantService, text: str) -> str:
    dialog = await service.store.create_dialog()
    await service.start_turn(dialog["id"], text, {})
    task = service._turn_tasks.get(dialog["id"])
    assert task is not None
    await asyncio.wait_for(task, timeout=10)
    return dialog["id"]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


async def test_split_sentences_and_speech_form() -> None:
    assert split_sentences("One done. Two next! Ready?") == [
        "One done.",
        "Two next!",
        "Ready?",
    ]
    assert split_sentences("   ") == []
    spoken = speech_form("Use `foo()` in [the docs](https://example.com).")
    assert "https://" not in spoken and "`" not in spoken


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


async def test_store_roundtrip_and_restart_expires_pending_actions(tmp_path: Path) -> None:
    store = AssistantStore(tmp_path / "mux.db")
    try:
        dialog = await store.create_dialog("hello")
        await store.add_action(
            {
                "id": "a1",
                "dialog_id": dialog["id"],
                "turn_id": "t1",
                "created_at": 1.0,
                "kind": "send_to_session",
                "class": ACTION_CLASS_REVERSIBLE,
                "restatement": "queue a draft",
                "arguments": json.dumps({"session": "x", "text": "hi"}),
                "status": "pending",
                "expires_at": None,
                "resolved_at": None,
                "result": None,
            }
        )
        snapshot = action_snapshot((await store.action("a1")) or {})
        assert snapshot["action_class"] == ACTION_CLASS_REVERSIBLE
        assert snapshot["arguments"] == {"session": "x", "text": "hi"}
    finally:
        store.close()
    # A new daemon can never execute a confirmation minted by the old one.
    reopened = AssistantStore(tmp_path / "mux.db")
    try:
        row = await reopened.action("a1")
        assert row is not None and row["status"] == "expired"
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Resolution and classification
# --------------------------------------------------------------------------- #


async def test_session_resolution_exact_unique_and_ambiguous(tmp_path: Path) -> None:
    service, _events, _queue, _effects = make_service(tmp_path)
    try:
        exact, _ = service.resolve_session("backend agent")
        assert exact is not None and exact.record.id == "s1"
        unique, _ = service.resolve_session("worker")
        assert unique is not None and unique.record.id == "s2"
        none, candidates = service.resolve_session("backend")
        assert none is None
        assert len(candidates) == 2
    finally:
        service.store.close()


async def test_action_classes_split_by_consequence(tmp_path: Path) -> None:
    service, _events, _queue, _effects = make_service(tmp_path)
    try:
        classify = service._classify
        assert classify("session_detail", {}) == ACTION_CLASS_READ
        assert classify("run_ui_command", {}) == ACTION_CLASS_NAVIGATION
        assert classify("send_to_session", {"deliver": False}) == ACTION_CLASS_REVERSIBLE
        assert classify("send_to_session", {"deliver": True}) == ACTION_CLASS_CONSEQUENTIAL
        assert classify("interrupt_session", {}) == ACTION_CLASS_CONSEQUENTIAL
        assert classify("end_session", {}) == ACTION_CLASS_CONSEQUENTIAL
        assert classify("spawn_session", {}) == ACTION_CLASS_REVERSIBLE
    finally:
        service.store.close()


async def test_context_snapshot_carries_computed_ages(tmp_path: Path) -> None:
    service, _events, _queue, _effects = make_service(tmp_path)
    try:
        service.sessions.sessions["s2"].record.state_since = 0.0  # unknown, not "now"
        snapshot = service.fleet_snapshot()
        names = {row["name"] for row in snapshot["sessions"]}
        assert names == {"backend agent", "backend worker"}
        assert snapshot["projects"][0]["name"] == "pixel lab"
        worker = next(row for row in snapshot["sessions"] if row["name"] == "backend worker")
        assert worker["state"] == "working"
        assert worker["state_age"] is None  # 0.0 means unknown, never "just now"
    finally:
        service.store.close()


# --------------------------------------------------------------------------- #
# Turn loop
# --------------------------------------------------------------------------- #


async def test_plain_answer_turn_emits_sentences_and_done(tmp_path: Path) -> None:
    ledger = LedgerStub()
    service, emitted, _queue, _effects = make_service(
        tmp_path,
        [tool_turn("Two sessions are live. Nothing needs you.")],
        ledger=ledger,
    )
    try:
        dialog_id = await run_turn(service, "what needs me")
        types = [event.type for event in emitted]
        assert types[0] == "assistant_turn_started"
        assert types.count("assistant_sentence") == 2
        assert types[-1] == "assistant_turn_done"
        done = emitted[-1]
        assert done.payload["display"].startswith("Two sessions are live.")
        assert done.payload["speech"]
        messages = await service.store.messages(dialog_id)
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert ledger.spend_rows and ledger.spend_rows[0]["cost_usd"] == pytest.approx(0.001)
        assert ledger.started[0]["rule_id"] == ASSISTANT_RULE_ID
    finally:
        service.store.close()


def send_call(deliver: bool) -> dict[str, Any]:
    return {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "send_to_session",
            "arguments": json.dumps(
                {"session": "backend agent", "text": "scope the tests", "deliver": deliver}
            ),
        },
    }


async def test_reversible_mutation_pends_under_confirm_trust(tmp_path: Path) -> None:
    service, emitted, queue, _effects = make_service(
        tmp_path,
        [
            tool_turn("", [send_call(deliver=False)]),
            tool_turn("Queued once you confirm."),
        ],
        trust="confirm",
    )
    try:
        dialog_id = await run_turn(service, "queue that to backend agent")
        assert queue.enqueued == []  # nothing executed without the human
        actions = await service.store.actions(dialog_id)
        assert len(actions) == 1 and actions[0]["status"] == "pending"
        # The model was told, in the tool result, that the action is pending.
        provider = cast(ToolProviderStub, service.provider)
        tool_result = json.loads(provider.calls[1]["messages"][-1]["content"])
        assert tool_result["pending_confirmation"] is True
        outcome = await service.confirm_action(str(actions[0]["id"]))
        assert outcome["action"]["status"] == "executed"
        assert queue.enqueued[0]["target_session_id"] == "s1"
        assert queue.enqueued[0]["armed"] is False
        assert "assistant_action" in {event.type for event in emitted}
    finally:
        service.store.close()


async def test_reversible_mutation_executes_under_auto_trust(tmp_path: Path) -> None:
    service, _emitted, queue, _effects = make_service(
        tmp_path,
        [tool_turn("", [send_call(deliver=False)]), tool_turn("Queued.")],
        trust="auto",
    )
    try:
        dialog_id = await run_turn(service, "queue that")
        assert queue.enqueued and queue.enqueued[0]["sender_label"] == "Mux assistant"
        actions = await service.store.actions(dialog_id)
        assert actions[0]["status"] == "executed"
    finally:
        service.store.close()


async def test_consequential_send_always_confirms_even_under_auto(tmp_path: Path) -> None:
    service, _emitted, queue, _effects = make_service(
        tmp_path,
        [tool_turn("", [send_call(deliver=True)]), tool_turn("Pending your confirm.")],
        trust="auto",
    )
    try:
        dialog_id = await run_turn(service, "send it now")
        assert queue.enqueued == []
        actions = await service.store.actions(dialog_id)
        assert actions[0]["status"] == "pending"
        assert actions[0]["class"] == ACTION_CLASS_CONSEQUENTIAL
    finally:
        service.store.close()


async def test_cancelled_action_never_executes(tmp_path: Path) -> None:
    service, _emitted, queue, _effects = make_service(
        tmp_path,
        [tool_turn("", [send_call(deliver=False)]), tool_turn("Waiting.")],
        trust="confirm",
    )
    try:
        dialog_id = await run_turn(service, "queue that")
        actions = await service.store.actions(dialog_id)
        cancelled = await service.cancel_action(str(actions[0]["id"]))
        assert cancelled["action"]["status"] == "cancelled"
        with pytest.raises(AssistantError, match="already cancelled"):
            await service.confirm_action(str(actions[0]["id"]))
        assert queue.enqueued == []
    finally:
        service.store.close()


async def test_unresolved_target_answers_with_candidates_not_a_pending_card(
    tmp_path: Path,
) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "send_to_session",
            "arguments": json.dumps({"session": "backend", "text": "hi"}),
        },
    }
    service, _emitted, queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Which one?")], trust="auto"
    )
    try:
        dialog_id = await run_turn(service, "message backend")
        provider = cast(ToolProviderStub, service.provider)
        tool_result = json.loads(provider.calls[1]["messages"][-1]["content"])
        assert tool_result["error"] == "session did not resolve"
        assert len(tool_result["candidates"]) == 2
        assert queue.enqueued == []
        assert await service.store.actions(dialog_id) == []
    finally:
        service.store.close()


async def test_budget_exhaustion_fails_the_turn_closed(tmp_path: Path) -> None:
    service, emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("never reached")], ledger=LedgerStub(spent_usd=100.0)
    )
    try:
        dialog_id = await run_turn(service, "hello")
        failed = [event for event in emitted if event.type == "assistant_turn_failed"]
        assert failed and "budget" in str(failed[0].payload["error"])
        messages = await service.store.messages(dialog_id)
        assert messages[-1]["status"] == "failed"
        provider = cast(ToolProviderStub, service.provider)
        assert provider.calls == []  # no model call was even attempted
    finally:
        service.store.close()


async def test_disabled_assistant_refuses_turns(tmp_path: Path) -> None:
    service, _emitted, _queue, _effects = make_service(tmp_path, enabled=False)
    try:
        dialog = await service.store.create_dialog()
        with pytest.raises(AssistantError, match="disabled"):
            await service.start_turn(dialog["id"], "hello", {})
    finally:
        service.store.close()


async def test_ui_command_dispatch_waits_for_the_device_ack(tmp_path: Path) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "run_ui_command",
            "arguments": json.dumps({"command": "go to pixel lab"}),
        },
    }
    service, emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Opened it.")]
    )
    try:
        dialog = await service.store.create_dialog()
        turn_id = await service.start_turn(dialog["id"], "open pixel lab", {})
        assert turn_id
        # Wait for the dispatched action to appear, then acknowledge as the device.
        for _ in range(100):
            dispatched = [
                event for event in emitted
                if event.type == "assistant_action" and event.payload.get("status") == "dispatched"
            ]
            if dispatched:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("no dispatched UI action appeared")
        action_id = str(dispatched[0].payload["id"])
        assert service.report_ui_result(action_id, {"ok": True, "detail": "ran Focus pixel lab"})
        task = service._turn_tasks.get(dialog["id"])
        assert task is not None
        await asyncio.wait_for(task, timeout=10)
        row = await service.store.action(action_id)
        assert row is not None and row["status"] == "executed"
    finally:
        service.store.close()


async def test_spawn_uses_the_ordinary_spawn_path(tmp_path: Path) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "spawn_session",
            "arguments": json.dumps(
                {"project": "pixel lab", "backend": "claude", "seed_text": "fix the tests"}
            ),
        },
    }
    service, _emitted, _queue, effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Spawned.")], trust="auto"
    )
    try:
        await run_turn(service, "spawn a claude in pixel lab")
        assert effects["spawned"] == [
            {"project_id": "p1", "backend": "claude", "seed_text": "fix the tests"}
        ]
    finally:
        service.store.close()
