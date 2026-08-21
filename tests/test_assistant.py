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
    CANCEL_WINDOW_MAX_SECONDS,
    MAX_MODEL_CALLS_PER_TURN,
    AssistantError,
    AssistantService,
    AssistantStore,
    action_snapshot,
    apply_note_edit,
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
    def __init__(
        self, spent_usd: float = 0.0, titles: dict[str, str] | None = None
    ) -> None:
        self.spent_usd = spent_usd
        self.titles = titles or {}
        self.started: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.spend_rows: list[dict[str, Any]] = []

    async def spend(self, *, rule_id: str | None = None) -> dict[str, float | int]:
        assert rule_id == ASSISTANT_RULE_ID
        return {"tokens": 0, "cost_usd": self.spent_usd}

    async def annotations(
        self,
        *,
        agent_run_ids: Any = None,
        tag: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        assert tag == "title"
        wanted = set(agent_run_ids or [])
        return [
            {"agent_run_id": run_id, "content": title}
            for run_id, title in self.titles.items()
            if run_id in wanted
        ]

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
    """Scripted turns: each call pops the next one; runs past the script fail.

    `messages` is snapshotted per call. The turn loop mutates one list across
    rounds - appending tool results, and replacing the round-budget line - so
    recording the reference made every call look like the last one, and any
    assertion about what the model saw *at a given round* silently read the
    final state instead.
    """

    def __init__(self, turns: list[OpenRouterToolTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def complete_tools(self, **kwargs: Any) -> OpenRouterToolTurn:
        recorded = dict(kwargs)
        if isinstance(recorded.get("messages"), list):
            recorded["messages"] = list(recorded["messages"])
        self.calls.append(recorded)
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

    async def removed_project_for_root(_root: str) -> Any:
        return None

    projects = SimpleNamespace(
        projects={"p1": project},
        ordered_projects=lambda: [project],
        history=SimpleNamespace(removed_project_for_root=removed_project_for_root),
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


def last_tool_result(provider: ToolProviderStub, call: int = 1) -> dict[str, Any]:
    """The newest tool result the model was shown on a given call.

    Located by role rather than by position: the prompt also carries a trailing
    system line stating how many tool rounds remain, so "the last message" is
    not the tool result and asserting on it was incidental.
    """
    messages = provider.calls[call]["messages"]
    tool_messages = [item for item in messages if item.get("role") == "tool"]
    assert tool_messages, "the model was shown no tool result"
    return cast(dict[str, Any], json.loads(str(tool_messages[-1]["content"])))


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


async def test_apply_note_edit_covers_every_action_and_refusal() -> None:
    body = "# Notes\nfirst item\nsecond item"
    # insert_line: the text *becomes* that line (the live ask: "on the second
    # line please add 'asdf'").
    inserted = apply_note_edit(body, {"action": "insert_line", "line": 2, "text": "asdf"})
    assert inserted.split("\n")[1] == "asdf"
    assert inserted.split("\n")[2] == "first item"
    # A line past the end appends rather than raising.
    tail = apply_note_edit(body, {"action": "insert_line", "line": 99, "text": "end"})
    assert tail.split("\n")[-1] == "end"
    assert apply_note_edit(body, {"action": "append", "text": "new"}).endswith("new\n")
    assert apply_note_edit(body, {"action": "prepend", "text": "top"}).startswith("top\n\n# Notes")
    replaced = apply_note_edit(body, {"action": "replace_text", "find": "second", "text": "2nd"})
    assert "2nd item" in replaced
    with pytest.raises(AssistantError, match="not found"):
        apply_note_edit(body, {"action": "replace_text", "find": "absent", "text": "x"})
    with pytest.raises(AssistantError, match="appears 2 times"):
        apply_note_edit(body, {"action": "replace_text", "find": "item", "text": "x"})
    with pytest.raises(AssistantError, match="line number"):
        apply_note_edit(body, {"action": "insert_line", "text": "x"})
    with pytest.raises(AssistantError, match="must not be empty"):
        apply_note_edit(body, {"action": "append", "text": "  "})
    with pytest.raises(AssistantError, match="unknown note edit"):
        apply_note_edit(body, {"action": "obliterate", "text": "x"})


async def test_note_edit_is_reversible_and_routes_through_the_closure(tmp_path: Path) -> None:
    edits: list[tuple[str, str | None, dict[str, Any]]] = []

    async def note_edit(
        project_id: str, note: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        edits.append((project_id, note, payload))
        return {"title": "swe-mux notes", "bytes": 42}

    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "edit_project_note",
            "arguments": json.dumps(
                {"project": "pixel lab", "action": "insert_line", "line": 2, "text": "asdf"}
            ),
        },
    }
    service, _emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Inserted.")], trust="confirm"
    )
    service.note_edit = note_edit
    try:
        assert service._classify("edit_project_note", {}) == ACTION_CLASS_REVERSIBLE
        dialog_id = await run_turn(service, "add asdf on line 2 of the pixel lab notes")
        actions = await service.store.actions(dialog_id)
        assert actions[0]["status"] == "pending"  # confirm trust: nothing ran yet
        assert "line 2" in str(actions[0]["restatement"])
        assert edits == []
        outcome = await service.confirm_action(str(actions[0]["id"]))
        assert outcome["action"]["status"] == "executed"
        assert edits[0][0] == "p1" and edits[0][2]["line"] == 2
    finally:
        service.store.close()


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
        exact, _ = await service.resolve_session("backend agent")
        assert exact is not None and exact.record.id == "s1"
        unique, _ = await service.resolve_session("worker")
        assert unique is not None and unique.record.id == "s2"
        none, candidates = await service.resolve_session("backend")
        assert none is None
        assert len(candidates) == 2
    finally:
        service.store.close()


async def test_display_titles_reach_the_snapshot_and_resolution(tmp_path: Path) -> None:
    """The assistant must speak and accept the same names the UI shows.

    The live gap this pins: sessions listed by their spawn ids instead of their
    generated titles, and a title the model then quoted failing to resolve.
    """
    ledger = LedgerStub(titles={"run-1": "Top Districts Mississippi West"})
    service, _events, _queue, _effects = make_service(tmp_path, ledger=ledger)
    try:
        snapshot = await service.fleet_snapshot()
        names = {row["name"] for row in snapshot["sessions"]}
        assert "Top Districts Mississippi West" in names
        assert "backend agent" not in names  # the spawn name is superseded
        by_title, _ = await service.resolve_session("top districts mississippi west")
        assert by_title is not None and by_title.record.id == "s1"
        by_substring, _ = await service.resolve_session("mississippi")
        assert by_substring is not None and by_substring.record.id == "s1"
        # A rename is the human overriding the generator: the title stops winning.
        service.sessions.sessions["s1"].record.auto_named = False
        renamed = await service.fleet_snapshot()
        assert "backend agent" in {row["name"] for row in renamed["sessions"]}
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
        # Creating a project mints one empty folder inside the configured parent
        # and a tombstoning removal undoes the registration; same class as spawn.
        assert classify("create_project", {}) == ACTION_CLASS_REVERSIBLE
    finally:
        service.store.close()


# --------------------------------------------------------------------------- #
# create_project
# --------------------------------------------------------------------------- #


async def test_create_project_preflight_refuses_before_anything_pends(
    tmp_path: Path,
) -> None:
    service, _events, _queue, _effects = make_service(tmp_path)
    try:
        # No configured parent: the refusal names the setting, never guesses.
        refusal = await service._preflight_mutation("create_project", {"name": "scraper"})
        assert refusal is not None and "New project location" in refusal["error"]

        parent = tmp_path / "projects-home"
        update_config(service.config, {"new_project_parent": str(parent)})
        # Configured but missing on disk: honest failure pointing at the setting.
        refusal = await service._preflight_mutation("create_project", {"name": "scraper"})
        assert refusal is not None and "does not exist" in refusal["error"]

        parent.mkdir()
        assert await service._preflight_mutation("create_project", {"name": ""}) is not None
        # A name that normalizes to nothing is a refusal, not an empty folder.
        refusal = await service._preflight_mutation("create_project", {"name": "- - -"})
        assert refusal is not None and "folder name" in refusal["error"]
        # A Windows device name survives normalization and is refused loudly.
        refusal = await service._preflight_mutation("create_project", {"name": "COM1"})
        assert refusal is not None and "reserved by Windows" in refusal["error"]

        # Existing non-empty folder: adoption belongs to the Add-project dialog.
        crowded = parent / "occupied"
        crowded.mkdir()
        (crowded / "keep.txt").write_text("x", encoding="utf-8")
        refusal = await service._preflight_mutation("create_project", {"name": "occupied"})
        assert refusal is not None and "not empty" in refusal["error"]

        # Already registered: named as the project it is, not a path error.
        service.projects.projects["p1"].root = str((parent / "scraper").resolve())
        refusal = await service._preflight_mutation("create_project", {"name": "scraper"})
        assert refusal is not None and "pixel lab" in refusal["error"]
        assert not (parent / "scraper").exists()
    finally:
        service.store.close()


async def test_create_project_preflight_resolves_the_exact_path_for_the_card(
    tmp_path: Path,
) -> None:
    service, _events, _queue, _effects = make_service(tmp_path)
    try:
        parent = tmp_path / "projects-home"
        parent.mkdir()
        update_config(service.config, {"new_project_parent": str(parent)})
        arguments: dict[str, Any] = {"name": "  vault spaces ", "git": True}
        assert await service._preflight_mutation("create_project", arguments) is None
        # The spoken name normalizes deterministically and the absolute result is
        # stamped into the arguments, so the restated card is fully informed.
        assert arguments["name"] == "vault spaces"
        assert arguments["root"] == str((parent / "vault-spaces").resolve())
        restated = service._restate("create_project", arguments)
        assert arguments["root"] in restated
        assert "empty git repository" in restated
        assert "restores" not in restated

        async def removed(root: str) -> Any:
            assert root == arguments["root"]
            return SimpleNamespace(name="old vault")

        service.projects.history.removed_project_for_root = removed
        revived: dict[str, Any] = {"name": "vault spaces"}
        assert await service._preflight_mutation("create_project", revived) is None
        assert revived["restores"] == "old vault"
        assert "old vault" in service._restate("create_project", revived)
    finally:
        service.store.close()


async def test_create_project_executes_through_the_wired_operation(tmp_path: Path) -> None:
    parent = tmp_path / "projects-home"
    parent.mkdir()
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "create_project",
            "arguments": json.dumps({"name": "vault spaces", "git": True}),
        },
    }
    service, _events, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Created.")], trust="confirm"
    )
    created: list[dict[str, Any]] = []

    async def create_project_op(arguments: dict[str, Any]) -> dict[str, Any]:
        created.append(arguments)
        return {"created": True, "project": arguments["name"], "root": arguments["root"]}

    service.create_project_op = create_project_op
    try:
        update_config(service.config, {"new_project_parent": str(parent)})
        dialog_id = await run_turn(service, "make a project called vault spaces")
        actions = await service.store.actions(dialog_id)
        assert actions[0]["status"] == "pending"  # confirm trust: nothing ran yet
        assert created == []
        assert str((parent / "vault-spaces").resolve()) in str(actions[0]["restatement"])
        outcome = await service.confirm_action(str(actions[0]["id"]))
        assert outcome["action"]["status"] == "executed"
        assert created[0]["root"] == str((parent / "vault-spaces").resolve())
        assert created[0]["git"] is True
    finally:
        service.store.close()


async def test_create_project_without_a_wired_operation_fails_closed(tmp_path: Path) -> None:
    parent = tmp_path / "projects-home"
    parent.mkdir()
    service, _events, _queue, _effects = make_service(tmp_path, trust="auto")
    try:
        update_config(service.config, {"new_project_parent": str(parent)})
        dialog = await service.store.create_dialog()
        result = await service._run_tool(
            dialog["id"], "t1", "create_project", {"name": "scraper"}
        )
        assert "not wired" in result["error"]
    finally:
        service.store.close()


async def test_context_snapshot_carries_computed_ages(tmp_path: Path) -> None:
    service, _events, _queue, _effects = make_service(tmp_path)
    try:
        service.sessions.sessions["s2"].record.state_since = 0.0  # unknown, not "now"
        snapshot = await service.fleet_snapshot()
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
        tool_result = last_tool_result(provider)
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
        tool_result = last_tool_result(provider)
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


async def wait_for_dispatched(emitted: list[MuxEvent], kind: str) -> MuxEvent:
    for _ in range(200):
        for event in emitted:
            if (
                event.type == "assistant_action"
                and event.payload.get("status") == "dispatched"
                and event.payload.get("kind") == kind
            ):
                return event
        await asyncio.sleep(0.02)
    raise AssertionError(f"no dispatched {kind} action appeared")


async def test_type_into_session_dispatches_composer_typing_to_the_device(
    tmp_path: Path,
) -> None:
    """The Route A contract: the daemon never types into a PTY — the operator's
    device stages the text in the mounted composer, without a carriage return."""
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "type_into_session",
            "arguments": json.dumps({"session": "backend agent", "text": "one, two, three"}),
        },
    }
    service, emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Typed it.")], trust="auto"
    )
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(
            dialog["id"], "type one two three into the agent", {"client_id": "tab-1"}
        )
        event = await wait_for_dispatched(emitted, "type_into_session")
        # The dispatch names the resolved session and the originating tab, so
        # exactly one device types into exactly one pane. (`target_session_id`
        # because `session_id` is a first-class MuxEvent field, not payload.)
        assert event.payload["target_session_id"] == "s1"
        arguments = event.payload["arguments"]
        assert arguments["client_id"] == "tab-1"
        assert arguments["text"] == "one, two, three"
        assert "without sending" in str(event.payload["restatement"])
        action_id = str(event.payload["id"])
        assert service.report_ui_result(
            action_id, {"ok": True, "detail": "typed into the composer without sending"}
        )
        task = service._turn_tasks.get(dialog["id"])
        assert task is not None
        await asyncio.wait_for(task, timeout=10)
        row = await service.store.action(action_id)
        assert row is not None and row["status"] == "executed"
        assert "not sent" in str(row["result"])
    finally:
        service.store.close()


async def test_type_into_session_reports_an_unmounted_terminal_as_failure(
    tmp_path: Path,
) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "type_into_session",
            "arguments": json.dumps({"session": "backend agent", "text": "hello"}),
        },
    }
    service, emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("It failed.")], trust="auto"
    )
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(dialog["id"], "type hello", {"client_id": "tab-1"})
        event = await wait_for_dispatched(emitted, "type_into_session")
        action_id = str(event.payload["id"])
        service.report_ui_result(
            action_id, {"ok": False, "detail": "The target terminal is not mounted."}
        )
        task = service._turn_tasks.get(dialog["id"])
        assert task is not None
        await asyncio.wait_for(task, timeout=10)
        row = await service.store.action(action_id)
        assert row is not None and row["status"] == "failed"
        assert "not mounted" in str(row["result"])
    finally:
        service.store.close()


async def test_submit_composer_is_consequential_and_dispatches_on_confirm(
    tmp_path: Path,
) -> None:
    """Pressing Enter is a send: the trust knob never applies, and the Enter
    itself still runs on the operator's device through the mounted pane."""
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "submit_session_composer",
            "arguments": json.dumps({"session": "backend agent"}),
        },
    }
    service, emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Awaiting your confirmation.")],
        trust="auto",
    )
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(dialog["id"], "send it", {"client_id": "tab-1"})
        task = service._turn_tasks.get(dialog["id"])
        assert task is not None
        await asyncio.wait_for(task, timeout=10)
        pending = [
            event for event in emitted
            if event.type == "assistant_action" and event.payload.get("status") == "pending"
        ]
        assert pending and pending[0].payload["kind"] == "submit_session_composer"
        action_id = str(pending[0].payload["id"])
        confirm = asyncio.create_task(service.confirm_action(action_id))
        event = await wait_for_dispatched(emitted, "submit_session_composer")
        assert event.payload["target_session_id"] == "s1"
        service.report_ui_result(action_id, {"ok": True, "detail": "pressed Enter"})
        outcome = await asyncio.wait_for(confirm, timeout=10)
        assert outcome["result"]["submitted"] is True
        row = await service.store.action(action_id)
        assert row is not None and row["status"] == "executed"
    finally:
        service.store.close()


async def test_spawn_with_a_client_goes_through_the_device_pane(tmp_path: Path) -> None:
    """A turn from a connected workspace spawns through that device's launch
    path (tab in the active pane), never the daemon's layout-default spawn —
    and never both, which is why there is no daemon fallback on failure."""
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
    service, emitted, _queue, effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Spawned.")], trust="auto"
    )
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(dialog["id"], "spawn a claude", {"client_id": "tab-1"})
        event = await wait_for_dispatched(emitted, "spawn_session")
        assert event.payload["project_id"] == "p1"
        assert event.payload["backend"] == "claude"
        assert event.payload["seed_text"] == "fix the tests"
        action_id = str(event.payload["id"])
        service.report_ui_result(
            action_id, {"ok": True, "detail": "spawned agent 2 into the active pane"}
        )
        task = service._turn_tasks.get(dialog["id"])
        assert task is not None
        await asyncio.wait_for(task, timeout=10)
        assert effects["spawned"] == []  # the daemon spawn path must not also run
        row = await service.store.action(action_id)
        assert row is not None and row["status"] == "executed"
    finally:
        service.store.close()


def test_new_kinds_classify_under_the_trust_policy() -> None:
    assert AssistantService._classify("type_into_session", {}) == "reversible"
    assert AssistantService._classify("submit_session_composer", {}) == "consequential"


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


# --------------------------------------------------------------------------- #
# Speaking a turn: what is said, once, and when
# --------------------------------------------------------------------------- #


def note_call(text: str = "ship the thing", call_id: str = "call-1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "append_project_note",
            "arguments": json.dumps({"project": "pixel lab", "text": text}),
        },
    }


def note_service(
    tmp_path: Path, turns: list[OpenRouterToolTurn], *, trust: str = "confirm"
) -> tuple[AssistantService, list[MuxEvent], list[str]]:
    appended: list[str] = []

    async def note_append(project_id: str, text: str) -> dict[str, Any]:
        appended.append(f"{project_id}:{text}")
        return {"title": "pixel lab notes", "bytes": len(text)}

    service, emitted, _queue, _effects = make_service(tmp_path, turns, trust=trust)
    service.note_append = note_append
    return service, emitted, appended


async def test_a_card_speaks_once_and_the_model_does_not_repeat_it(
    tmp_path: Path,
) -> None:
    """The card is the spoken statement; the model's paraphrase of it is not.

    Both used to be spoken, and because starting one stream halts the other, the
    operator heard a truncated card, a silence, and then the same thing again.
    Suppression is structural rather than prompted: a model that ignores the
    instruction still must not double-speak.
    """
    service, emitted, appended = note_service(
        tmp_path,
        [
            tool_turn("Adding that now.", [note_call()]),
            tool_turn("I've proposed appending that to the pixel lab note; say confirm."),
        ],
    )
    try:
        await run_turn(service, "add a note to pixel lab")
        assert appended == []
        sentences = [event for event in emitted if event.type == "assistant_sentence"]
        # Everything before the card keeps its voice; everything after it is
        # display-only.
        assert sentences[0].payload["speech"]
        assert sentences[0].payload["speech_suppressed"] is False
        assert all(
            event.payload["speech"] == "" and event.payload["speech_suppressed"] is True
            for event in sentences[1:]
        )
        done = [event for event in emitted if event.type == "assistant_turn_done"][0]
        assert done.payload["speech_suppressed"] is True
        # The display still records everything the model said; only the speech
        # drops the part the card already covers.
        assert "say confirm" in done.payload["display"]
        assert "say confirm" not in done.payload["speech"]
        assert done.payload["speech"].startswith("Adding that now")
    finally:
        service.store.close()


async def test_the_spoken_card_line_omits_the_text_it_is_about_to_write(
    tmp_path: Path,
) -> None:
    """Reading a note body aloud to announce that a note is about to be written
    is the slowest way to say nothing new: synthesis time tracks characters, and
    the operator can already read the preview on the card."""
    body = "Ship the redeploy checklist and mention the supervisor bundle caveat"
    service, emitted, _appended = note_service(
        tmp_path, [tool_turn("", [note_call(body)]), tool_turn("Proposed.")]
    )
    try:
        await run_turn(service, "note that")
        card = [
            event for event in emitted
            if event.type == "assistant_action" and event.payload.get("status") == "pending"
        ][0]
        assert body in str(card.payload["restatement"]), "the card still shows it"
        announcement = str(card.payload["announcement"])
        assert body not in announcement
        assert announcement == "Append to the pixel lab project note. Confirm or cancel?"
    finally:
        service.store.close()


async def test_a_scheduled_card_is_announced_as_stoppable_not_confirmable(
    tmp_path: Path,
) -> None:
    """Wording is the trust policy talking. A cancel-window action runs on its
    own, so telling the operator to confirm it describes a decision they do not
    have - and the action lands before the sentence finishes either way."""
    service, emitted, _appended = note_service(
        tmp_path,
        [tool_turn("", [note_call()]), tool_turn("Proposed.")],
        trust="cancel_window",
    )
    try:
        await run_turn(service, "note that")
        card = [
            event for event in emitted
            if event.type == "assistant_action"
            and event.payload.get("status") == "scheduled"
        ][0]
        assert str(card.payload["announcement"]).endswith("Say cancel to stop it.")
    finally:
        for task in service._window_tasks.values():
            task.cancel()
        service.store.close()


async def test_announcing_a_scheduled_card_restarts_its_cancel_window(
    tmp_path: Path,
) -> None:
    """The window has to outlast the operator *learning about it*. Spoken, that
    means synthesizing and then reading a sentence, which the original six
    seconds is not long enough to cover."""
    service, emitted, _appended = note_service(
        tmp_path,
        [tool_turn("", [note_call()]), tool_turn("Proposed.")],
        trust="cancel_window",
    )
    try:
        await run_turn(service, "note that")
        card = [
            event for event in emitted
            if event.type == "assistant_action"
            and event.payload.get("status") == "scheduled"
        ][0]
        action_id = str(card.payload["id"])
        before = float(card.payload["expires_at"])
        outcome = await service.announce_action(action_id)
        assert outcome["extended"] is True
        assert float(outcome["action"]["expires_at"]) > before
        row = await service.store.action(action_id)
        assert row is not None
        ceiling = float(row["created_at"]) + CANCEL_WINDOW_MAX_SECONDS
        assert float(row["expires_at"]) <= ceiling + 0.001
    finally:
        for task in service._window_tasks.values():
            task.cancel()
        service.store.close()


async def test_a_card_is_announced_exactly_once(tmp_path: Path) -> None:
    """The loop this closes ran in production on 2026-08-20.

    Extending re-emits the card so its countdown stays honest, and a device
    announces a card when it sees one - so an extension that can happen twice is
    a cycle: emit, announce, extend, emit. It produced 80 extensions about 25 ms
    apart, each spawning its own speech clip, which then played for minutes after
    the operator had closed the microphone. The second announcement must change
    nothing and, above all, must emit nothing.
    """
    service, emitted, _appended = note_service(
        tmp_path,
        [tool_turn("", [note_call()]), tool_turn("Proposed.")],
        trust="cancel_window",
    )
    try:
        await run_turn(service, "note that")
        card = [
            event for event in emitted
            if event.type == "assistant_action"
            and event.payload.get("status") == "scheduled"
        ][0]
        action_id = str(card.payload["id"])
        before = len([e for e in emitted if e.type == "assistant_action"])

        assert (await service.announce_action(action_id))["extended"] is True
        after_first = len([e for e in emitted if e.type == "assistant_action"])
        assert after_first == before + 1, "the extension re-emits the card once"
        deadline = float((await service.store.action(action_id) or {})["expires_at"])

        for _ in range(10):
            assert (await service.announce_action(action_id))["extended"] is False
        assert len([e for e in emitted if e.type == "assistant_action"]) == after_first
        final = await service.store.action(action_id)
        assert final is not None
        assert float(final["expires_at"]) == deadline, "and never moves it again"
    finally:
        for task in service._window_tasks.values():
            task.cancel()
        service.store.close()


async def test_announcing_anything_not_scheduled_changes_nothing(
    tmp_path: Path,
) -> None:
    # A confirm-trust card has no window to extend, and an unknown id is an
    # error rather than a silent success.
    service, emitted, _appended = note_service(
        tmp_path, [tool_turn("", [note_call()]), tool_turn("Proposed.")]
    )
    try:
        await run_turn(service, "note that")
        card = [
            event for event in emitted
            if event.type == "assistant_action" and event.payload.get("status") == "pending"
        ][0]
        outcome = await service.announce_action(str(card.payload["id"]))
        assert outcome["extended"] is False
        with pytest.raises(AssistantError, match="unknown action"):
            await service.announce_action("no-such-action")
    finally:
        service.store.close()


async def test_a_confirmed_write_is_not_proposed_again(tmp_path: Path) -> None:
    """The failure this closes: a spoken "confirm" the closed grammar did not
    recognize reaches the model as an ordinary turn, the model sees its own
    unanswered "say confirm" and calls the tool again - and the paragraph lands
    in the note twice."""
    service, emitted, appended = note_service(
        tmp_path,
        [
            tool_turn("", [note_call()]),
            tool_turn("Proposed."),
            tool_turn("", [note_call(call_id="call-2")]),
            tool_turn("Already done."),
        ],
    )
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(dialog["id"], "note that", {})
        await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        card = [
            event for event in emitted
            if event.type == "assistant_action" and event.payload.get("status") == "pending"
        ][0]
        await service.confirm_action(str(card.payload["id"]))
        assert appended == ["p1:ship the thing"]

        await service.start_turn(dialog["id"], "yes do that one", {})
        await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        assert appended == ["p1:ship the thing"], "the write must not repeat"
        cards = [
            row for row in await service.store.actions(dialog["id"])
            if row["status"] in {"pending", "scheduled"}
        ]
        assert cards == [], "and no second card may pend"
    finally:
        service.store.close()


async def test_an_unanswered_card_is_not_duplicated(tmp_path: Path) -> None:
    # Two cards for one intent is always wrong: answering either leaves the
    # other armed, which is what "it popped up the confirm again" looked like.
    service, emitted, _appended = note_service(
        tmp_path,
        [
            tool_turn("", [note_call()]),
            tool_turn("Proposed."),
            tool_turn("", [note_call(call_id="call-2")]),
            tool_turn("Still waiting on you."),
        ],
    )
    try:
        dialog = await service.store.create_dialog()
        for _ in range(2):
            await service.start_turn(dialog["id"], "note that", {})
            await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        pending = [
            row for row in await service.store.actions(dialog["id"])
            if row["status"] == "pending"
        ]
        assert len(pending) == 1
        assert len([e for e in emitted if e.type == "assistant_action"]) == 1
    finally:
        service.store.close()


async def test_a_repeated_spawn_is_allowed_because_repetition_is_the_ask(
    tmp_path: Path,
) -> None:
    """The executed-duplicate guard covers only kinds where repeating *is* the
    damage. Two identical sessions is a thing operators genuinely want, so
    spawning stays unguarded while note writes do not."""
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "spawn_session",
            "arguments": json.dumps({"project": "pixel lab", "backend": "claude"}),
        },
    }
    service, _emitted, _queue, effects = make_service(
        tmp_path,
        [
            tool_turn("", [call]),
            tool_turn("Started."),
            tool_turn("", [dict(call, id="call-2")]),
            tool_turn("Started another."),
        ],
        trust="auto",
    )
    try:
        dialog = await service.store.create_dialog()
        for _ in range(2):
            await service.start_turn(dialog["id"], "spawn a claude in pixel lab", {})
            await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        assert len(effects["spawned"]) == 2
    finally:
        service.store.close()


async def test_the_turn_prompt_carries_what_already_happened(tmp_path: Path) -> None:
    """A confirmation is a button or a spoken word, never a turn, so nothing in
    the message log records that the operator said yes. The action ledger is
    where the model learns it."""
    service, emitted, _appended = note_service(
        tmp_path,
        [
            tool_turn("", [note_call()]),
            tool_turn("Proposed."),
            tool_turn("It is already saved."),
        ],
    )
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(dialog["id"], "note that", {})
        await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        card = [
            event for event in emitted
            if event.type == "assistant_action" and event.payload.get("status") == "pending"
        ][0]
        await service.confirm_action(str(card.payload["id"]))
        await service.start_turn(dialog["id"], "did that save?", {})
        await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        provider = cast(Any, service.provider)
        context = str(provider.calls[-1]["messages"][1]["content"])
        assert "Actions already proposed in this conversation" in context
        assert "executed" in context
        assert "append to the pixel lab project note" in context
    finally:
        service.store.close()


async def test_a_streamed_reply_speaks_sentence_by_sentence(tmp_path: Path) -> None:
    """Streaming exists so the first sentence can be spoken while the model is
    still writing the second. The daemon does the splitting because a delta is
    not a sentence and half a sentence is not speakable."""

    class StreamingProviderStub:
        def __init__(self, chunks: list[str]) -> None:
            self.chunks = chunks
            self.calls: list[dict[str, Any]] = []

        async def complete_tools(self, **kwargs: Any) -> OpenRouterToolTurn:
            self.calls.append(kwargs)
            on_content = kwargs.get("on_content")
            assert on_content is not None, "the assistant must opt into streaming"
            for chunk in self.chunks:
                await on_content(chunk)
            return tool_turn("".join(self.chunks))

    service, emitted, _queue, _effects = make_service(tmp_path, [])
    service.provider = cast(
        Any,
        StreamingProviderStub(
            ["Three sess", "ions are working. ", "Nothing is waiting", " on you."]
        ),
    )
    try:
        await run_turn(service, "how is the fleet")
        sentences = [
            str(event.payload["display"])
            for event in emitted
            if event.type == "assistant_sentence"
        ]
        # Split at the boundary, not at the delta: the first sentence is released
        # whole and before the second one exists.
        assert sentences == ["Three sessions are working.", "Nothing is waiting on you."]
        assert all(
            event.payload["speech"]
            for event in emitted
            if event.type == "assistant_sentence"
        )
        done = [event for event in emitted if event.type == "assistant_turn_done"][0]
        assert done.payload["sentence_count"] == 2
    finally:
        service.store.close()


async def test_an_unstreamed_reply_still_publishes_its_sentences(
    tmp_path: Path,
) -> None:
    # The provider may refuse to stream, and the config knob turns it off. Either
    # way the sentence contract holds, so the client has one path to speak from.
    service, emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("One done. Two next.")]
    )
    service.config.assistant_stream_replies = False
    try:
        await run_turn(service, "status")
        assert [
            str(event.payload["display"])
            for event in emitted
            if event.type == "assistant_sentence"
        ] == ["One done.", "Two next."]
    finally:
        service.store.close()


# --------------------------------------------------------------------------- #
# Multi-step turns: rounds, batching, and not losing what the operator said
# --------------------------------------------------------------------------- #


async def test_a_turn_that_runs_out_of_rounds_says_so(tmp_path: Path) -> None:
    """The measured failure (2026-08-20): asked to open three sessions with a
    note staged in each, the turn spent its rounds, stopped in the middle, and
    reported "Ready when you are." Running out is now part of what it says."""
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "spawn_session",
            "arguments": json.dumps({"project": "pixel lab", "backend": "claude"}),
        },
    }
    # Every round asks for another tool, so the ceiling is always reached.
    service, emitted, _queue, _effects = make_service(
        tmp_path,
        [tool_turn("", [dict(call, id=f"call-{index}")]) for index in range(20)],
        trust="auto",
    )
    try:
        await run_turn(service, "spawn a claude in pixel lab, over and over")
        done = [event for event in emitted if event.type == "assistant_turn_done"][0]
        assert done.payload["exhausted"] is True
        assert "ran out of tool rounds" in str(done.payload["display"])
        # And it is *spoken*, not merely displayed: the operator asking by voice
        # is exactly the one who cannot see a half-finished turn.
        assert "ran out of tool rounds" in str(done.payload["speech"])
        notice = [
            event for event in emitted
            if event.type == "assistant_sentence"
            and "ran out of tool rounds" in str(event.payload["display"])
        ]
        assert notice and notice[0].payload["speech_suppressed"] is False
        assert done.payload["usage"]["calls"] == MAX_MODEL_CALLS_PER_TURN
    finally:
        service.store.close()


async def test_a_turn_that_finishes_is_not_marked_exhausted(tmp_path: Path) -> None:
    service, emitted, _queue, _effects = make_service(tmp_path, [tool_turn("All done.")])
    try:
        await run_turn(service, "status")
        done = [event for event in emitted if event.type == "assistant_turn_done"][0]
        assert done.payload["exhausted"] is False
        assert "ran out" not in str(done.payload["display"])
    finally:
        service.store.close()


async def test_the_model_is_told_how_many_rounds_remain(tmp_path: Path) -> None:
    """Rounds were spent blindly before, which is how a turn came to re-read a
    note it already had and then stop mid-task."""
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "session_detail",
            "arguments": json.dumps({"session": "backend agent"}),
        },
    }
    service, _emitted, _queue, _effects = make_service(
        tmp_path, [tool_turn("", [call]), tool_turn("Looked.")]
    )
    try:
        await run_turn(service, "how is backend agent")
        provider = cast(ToolProviderStub, service.provider)
        # First call carries no budget line; the second does, and it is last so
        # it sits closest to what the model is about to decide.
        first = provider.calls[0]["messages"]
        assert not any("Tool rounds remaining" in str(item.get("content")) for item in first)
        second = provider.calls[1]["messages"]
        assert "Tool rounds remaining" in str(second[-1]["content"])
        assert str(second[-1]["role"]) == "system"
        assert "Batch independent calls" in str(second[-1]["content"])
    finally:
        service.store.close()


async def test_the_budget_line_is_replaced_not_stacked(tmp_path: Path) -> None:
    # One budget, always. A stack of stale ones would both grow the prompt and
    # leave the model reading a number that is no longer true.
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "session_detail",
            "arguments": json.dumps({"session": "backend agent"}),
        },
    }
    service, _emitted, _queue, _effects = make_service(
        tmp_path,
        [
            tool_turn("", [dict(call, id="call-1")]),
            tool_turn("", [dict(call, id="call-2")]),
            tool_turn("", [dict(call, id="call-3")]),
            tool_turn("Done."),
        ],
    )
    try:
        await run_turn(service, "how is backend agent")
        provider = cast(ToolProviderStub, service.provider)
        for messages in provider.calls:
            budgets = [
                item for item in messages["messages"]
                if "Tool rounds remaining" in str(item.get("content"))
            ]
            assert len(budgets) <= 1
    finally:
        service.store.close()


async def test_the_last_rounds_tell_the_model_to_wind_up(tmp_path: Path) -> None:
    call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "session_detail",
            "arguments": json.dumps({"session": "backend agent"}),
        },
    }
    service, _emitted, _queue, _effects = make_service(
        tmp_path,
        [tool_turn("", [dict(call, id=f"call-{index}")]) for index in range(20)],
    )
    try:
        await run_turn(service, "keep looking")
        provider = cast(ToolProviderStub, service.provider)
        # The very last call carries no budget line — the loop breaks on the
        # ceiling before writing one — so the wind-up warning is on the rounds
        # just before it, which is the point: it arrives while there is still
        # room to act on it.
        budgets = [
            str(item.get("content"))
            for call in provider.calls
            for item in call["messages"]
            if "Tool rounds remaining" in str(item.get("content"))
        ]
        assert any("Start no new work" in line for line in budgets)
        assert any("Batch independent calls" in line for line in budgets)
    finally:
        service.store.close()


async def test_a_card_plus_other_work_still_speaks_the_summary(tmp_path: Path) -> None:
    """Suppression is for the case where the card *is* the whole outcome.

    Suppressing whenever any card opened also swallowed "I opened two of the
    three and one needs your confirmation" - information the card cannot carry
    and the operator has no other way to hear.
    """
    spawn = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "spawn_session",
            "arguments": json.dumps({"project": "pixel lab", "backend": "claude"}),
        },
    }
    send = {
        "id": "call-2",
        "type": "function",
        "function": {
            "name": "send_to_session",
            "arguments": json.dumps(
                {"session": "backend agent", "text": "go", "deliver": True}
            ),
        },
    }
    # spawn executes (reversible under auto trust); the armed send is on the
    # consequential floor and pends. One card, but the turn also did something.
    service, emitted, _queue, _effects = make_service(
        tmp_path,
        [tool_turn("", [spawn, send]), tool_turn("I started the session; the send needs you.")],
        trust="auto",
    )
    try:
        await run_turn(service, "spawn one and message the other")
        done = [event for event in emitted if event.type == "assistant_turn_done"][0]
        assert done.payload["speech_suppressed"] is False
        assert "I started the session" in str(done.payload["speech"])
    finally:
        service.store.close()


async def test_a_lone_card_still_suppresses_the_paraphrase(tmp_path: Path) -> None:
    # The original double-speak case must stay fixed.
    service, emitted, _appended = note_service(
        tmp_path,
        [tool_turn("", [note_call()]), tool_turn("I've proposed appending that note.")],
    )
    try:
        await run_turn(service, "note that")
        done = [event for event in emitted if event.type == "assistant_turn_done"][0]
        assert done.payload["speech_suppressed"] is True
        assert done.payload["speech"] == ""
    finally:
        service.store.close()


async def test_speaking_over_a_running_turn_queues_instead_of_losing_it(
    tmp_path: Path,
) -> None:
    """The reported failure: interrupting mid-reply and having to repeat
    yourself, because the turn was refused and the client had nowhere to put
    the refusal. Nothing the operator said may be dropped."""
    gate = asyncio.Event()

    class SlowProvider(ToolProviderStub):
        async def complete_tools(self, **kwargs: Any) -> OpenRouterToolTurn:
            await gate.wait()
            return await super().complete_tools(**kwargs)

    service, emitted, _queue, _effects = make_service(tmp_path)
    service.provider = cast(Any, SlowProvider([tool_turn("First."), tool_turn("Second.")]))
    try:
        dialog = await service.store.create_dialog()
        first = await service.start_turn(dialog["id"], "the first thing", {})
        second = await service.start_turn(dialog["id"], "the second thing", {})
        assert second != first
        assert service.turn_queued(second) is True
        queued = [event for event in emitted if event.type == "assistant_turn_queued"]
        assert queued and queued[0].payload["text"] == "the second thing"

        gate.set()
        await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
        for _ in range(50):
            await asyncio.sleep(0.01)
            if service.turn_running(dialog["id"]) or not service._queue_starters:
                break
        running = service._turn_tasks.get(dialog["id"])
        if running is not None:
            await asyncio.wait_for(running, timeout=10)
        # Both turns ran, in order, and both are in the conversation.
        said = [
            str(row["display"])
            for row in await service.store.messages(dialog["id"])
            if row["role"] == "user"
        ]
        assert said == ["the first thing", "the second thing"]
    finally:
        service.store.close()


async def test_two_breaths_of_one_thought_become_one_turn(tmp_path: Path) -> None:
    """A sentence finished in two breaths is one request. Answering the first
    fragment produced the observed split, where "I have three" was answered and
    the rest of the sentence opened a different conversation."""
    gate = asyncio.Event()

    class SlowProvider(ToolProviderStub):
        async def complete_tools(self, **kwargs: Any) -> OpenRouterToolTurn:
            await gate.wait()
            return await super().complete_tools(**kwargs)

    service, emitted, _queue, _effects = make_service(tmp_path)
    service.provider = cast(Any, SlowProvider([tool_turn("First."), tool_turn("Second.")]))
    try:
        dialog = await service.store.create_dialog()
        await service.start_turn(dialog["id"], "busy", {})
        one = await service.start_turn(dialog["id"], "I have three", {})
        two = await service.start_turn(dialog["id"], "groups of notes to split", {})
        assert one == two, "the fragments must share one turn, not become two"
        merged = [
            event for event in emitted
            if event.type == "assistant_turn_queued" and event.payload.get("merged")
        ]
        assert merged
        assert merged[-1].payload["text"] == "I have three groups of notes to split"
        gate.set()
        await asyncio.wait_for(service._turn_tasks[dialog["id"]], timeout=10)
    finally:
        service.store.close()


async def test_seed_text_tells_the_model_what_it_is_for(tmp_path: Path) -> None:
    # It was an undescribed string, and a model asked to open sessions with text
    # waiting in them passed "" twice. The capability was invisible.
    service, _emitted, _queue, _effects = make_service(tmp_path)
    try:
        spawn = [
            item for item in service._tool_definitions()
            if item["function"]["name"] == "spawn_session"
        ][0]
        seed = spawn["function"]["parameters"]["properties"]["seed_text"]
        assert "without" in seed["description"] and "sending" in seed["description"]
    finally:
        service.store.close()
