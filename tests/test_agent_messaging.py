"""Phase 5: bounded agent-to-agent messages and drafted spawn requests.

What these pin: a notify is an ordinary queue item with provenance, not a
delivery; the receiver's policy decides whether it arrives armed; every relay
bound (Project scope, self, size, per-origin budget, target backlog, chain
depth, cycle) is enforced in the daemon operation rather than in the MCP layer;
retries with a correlation id do not duplicate; and `request_spawn` writes an
inert draft that starts nothing.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.agent_messaging import AgentMessagingService
from swe_mux.auto_delivery import AutoDeliveryController
from swe_mux.config import Config
from swe_mux.git_projects import ProjectIdentity
from swe_mux.mcp import McpService
from swe_mux.project_files import append_observation, read_observations
from swe_mux.prompt_queue import PromptQueueService, PromptQueueStore, QueueError


def record(sid: str, **kw: Any) -> Any:
    defaults = dict(
        id=sid,
        name=f"claude-{sid}",
        backend="claude",
        state="idle",
        awaiting_reason=None,
        agent_run_id=f"run-{sid}",
        project_id="p1",
        project_scope_id="scope-1",
        cwd="C:/repo",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def live_session(sid: str, **kw: Any) -> Any:
    return SimpleNamespace(record=record(sid, **kw), mcp_token=f"tok-{sid}")


class EventsStub:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def subscribe(self, *, name: str = "anonymous") -> asyncio.Queue[Any]:
        return asyncio.Queue()

    def unsubscribe(self, queue: Any) -> None:
        pass


class ReadinessStub:
    def evaluate(self, session: Any) -> dict[str, Any]:
        return {"delivery_state": "safe", "reasons": ["all_required_evidence_positive"]}


class SessionsStub:
    def __init__(self, *sessions: Any) -> None:
        self.sessions = {session.record.id: session for session in sessions}

    def resolve(self, identity: str) -> Any:
        if identity in self.sessions:
            return self.sessions[identity]
        for session in self.sessions.values():
            if session.record.name == identity:
                return session
        raise KeyError(identity)


class Harness:
    def __init__(self, tmp_path: Path, *sessions: Any, **config_overrides: Any) -> None:
        self.root = tmp_path / "project"
        self.root.mkdir(parents=True, exist_ok=True)
        self.identity = ProjectIdentity("scope-1", "project", str(self.root), "registered")
        self.store = PromptQueueStore(tmp_path / "queue.db")
        self.events = EventsStub()
        self.manager = SessionsStub(*sessions)
        self.writes: list[tuple[str, str]] = []
        self.service = PromptQueueService(
            self.store,
            self.manager,
            self.events,
            ReadinessStub(),
            lambda session, data: self.writes.append((session.record.id, data)),
            submit_delay=0.0,
        )
        self.config = Config(**config_overrides)
        self.projects = SimpleNamespace(
            projects={"p1": SimpleNamespace(id="p1", name="project", root=str(self.root))}
        )
        self.auto = AutoDeliveryController(self.service, self.manager, self.config)
        self.messaging = AgentMessagingService(
            self.service,
            self.manager,
            self.projects,
            self.config,
            self.auto,
            append_observation=lambda cwd, body, **kw: append_observation(
                cwd, body, project=self.identity, **kw
            ),
        )

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def harness(tmp_path: Path):  # type: ignore[no-untyped-def]
    built = Harness(tmp_path, live_session("s1"), live_session("s2"))
    yield built
    built.close()


@pytest.mark.asyncio
async def test_a_notify_is_an_inert_draft_until_the_receiver_opts_in(
    harness: Harness,
) -> None:
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="I finished the migration"
    )
    assert result["state"] == "draft"
    assert not harness.writes
    message = await harness.store.message(result["message_id"])
    assert message is not None
    assert message["sender_kind"] == "agent"
    assert message["sender_id"] == "s1"
    assert message["origin"]["from_session"] == "s1"
    assert message["origin"]["path"] == ["s1"]
    assert message["chain_depth"] == 1
    assert message["constraints"]["expires_at"] > time.time()

    await harness.auto.set_accept_agent_messages("s2", True)
    armed = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="second"
    )
    assert armed["state"] == "armed"


@pytest.mark.asyncio
async def test_the_event_names_the_sender_without_carrying_the_body(
    harness: Harness,
) -> None:
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="secret plan"
    )
    received = [item for item in harness.events.emitted if item[0] == "queue_message_received"]
    assert received
    payload = received[0][1]
    assert payload["from_session"] == "s1" and payload["chain_depth"] == 1
    assert "secret plan" not in str(payload)


@pytest.mark.asyncio
async def test_targets_outside_the_callers_project_do_not_exist(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        live_session("s1"),
        live_session("s2", project_id="other", project_scope_id="scope-2"),
    )
    try:
        with pytest.raises(QueueError) as caught:
            await harness.messaging.notify(
                harness.manager.sessions["s1"], target="s2", body="hello"
            )
        assert caught.value.code == "unknown_target"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_self_notify_and_shell_targets_are_refused(tmp_path: Path) -> None:
    harness = Harness(tmp_path, live_session("s1"), live_session("s3", backend="shell"))
    try:
        with pytest.raises(QueueError) as self_notify:
            await harness.messaging.notify(
                harness.manager.sessions["s1"], target="s1", body="hi"
            )
        assert self_notify.value.code == "self_notify"
        with pytest.raises(QueueError) as shell:
            await harness.messaging.notify(
                harness.manager.sessions["s1"], target="s3", body="hi"
            )
        assert shell.value.code == "not_agent_target"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_size_budget_and_backlog_bounds(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        live_session("s1"),
        live_session("s2"),
        agent_message_max_chars=20,
        agent_message_hourly_budget=2,
        agent_message_pending_per_target=1,
    )
    try:
        caller = harness.manager.sessions["s1"]
        with pytest.raises(QueueError) as oversized:
            await harness.messaging.notify(caller, target="s2", body="x" * 21)
        assert oversized.value.code == "body_too_large"
        await harness.messaging.notify(caller, target="s2", body="one")
        with pytest.raises(QueueError) as backlog:
            await harness.messaging.notify(caller, target="s2", body="two")
        assert backlog.value.code == "target_backlog_full"
        # Clearing the backlog leaves the hourly budget as the next bound.
        view = await harness.store.messages_for_target("s2")
        await harness.service.cancel(str(view["messages"][0]["id"]), kind="cancelled")
        await harness.messaging.notify(caller, target="s2", body="three")
        view = await harness.store.messages_for_target("s2")
        await harness.service.cancel(str(view["messages"][-1]["id"]), kind="cancelled")
        with pytest.raises(QueueError) as budget:
            await harness.messaging.notify(caller, target="s2", body="four")
        assert budget.value.code == "origin_budget_exhausted"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_chain_depth_and_cycles_are_bounded(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        live_session("s1"),
        live_session("s2"),
        live_session("s3"),
        agent_message_max_chain_depth=2,
    )
    try:
        first = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="hop one"
        )
        # The relay context is derived from *delivered* messages, so mark it sent.
        await harness.store.finalize_delivery(
            "delivery-1",
            first["message_id"],
            outcome="sent",
            message_state="sent",
        )
        second = await harness.messaging.notify(
            harness.manager.sessions["s2"], target="s3", body="hop two"
        )
        assert second["chain_depth"] == 2
        await harness.store.finalize_delivery(
            "delivery-2",
            second["message_id"],
            outcome="sent",
            message_state="sent",
        )
        with pytest.raises(QueueError) as deep:
            await harness.messaging.notify(
                harness.manager.sessions["s3"], target="s1", body="hop three"
            )
        assert deep.value.code == "chain_depth_exceeded"
        # And a cycle is refused even inside the depth budget.
        with pytest.raises(QueueError) as cycle:
            await harness.messaging.notify(
                harness.manager.sessions["s2"], target="s1", body="back to the start"
            )
        assert cycle.value.code == "relay_cycle"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_a_retried_notify_does_not_duplicate(harness: Harness) -> None:
    caller = harness.manager.sessions["s1"]
    first = await harness.messaging.notify(
        caller, target="s2", body="deploy is green", correlation_id="corr-1"
    )
    again = await harness.messaging.notify(
        caller, target="s2", body="deploy is green", correlation_id="corr-1"
    )
    assert again["message_id"] == first["message_id"]
    assert again["deduplicated"] is True
    view = await harness.store.messages_for_target("s2")
    assert len(view["messages"]) == 1


@pytest.mark.asyncio
async def test_messaging_can_be_disabled_entirely(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path, live_session("s1"), live_session("s2"), agent_messaging_enabled=False
    )
    try:
        with pytest.raises(QueueError) as caught:
            await harness.messaging.notify(
                harness.manager.sessions["s1"], target="s2", body="hi"
            )
        assert caught.value.code == "agent_messaging_disabled"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_request_spawn_writes_an_inert_draft_and_starts_nothing(
    harness: Harness,
) -> None:
    result = await harness.messaging.request_spawn(
        harness.manager.sessions["s1"],
        prompt="Continue the migration in a fresh session",
        name="migration",
        reason="context is nearly full",
    )
    assert result["status"] == "drafted"
    inbox = await read_observations(harness.root, project=harness.identity)
    item = inbox["observations"][0]
    assert item["kind"] == "spawn_request"
    assert item["request"]["status"] == "pending"
    assert item["request"]["prompt"].startswith("Continue the migration")
    assert item["request"]["from_session"] == "s1"
    assert item["done"] is False
    # Nothing was queued and nothing was written to any terminal.
    assert not harness.writes
    assert await harness.store.mailbox() == []


@pytest.mark.asyncio
async def test_request_spawn_can_be_disabled(tmp_path: Path) -> None:
    harness = Harness(tmp_path, live_session("s1"), request_spawn_enabled=False)
    try:
        with pytest.raises(QueueError) as caught:
            await harness.messaging.request_spawn(
                harness.manager.sessions["s1"], prompt="anything"
            )
        assert caught.value.code == "request_spawn_disabled"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_the_mailbox_separates_inbox_from_outbox(harness: Harness) -> None:
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="from an agent"
    )
    await harness.service.enqueue(target_session_id="s2", body="from me", armed=False)
    inbox = await harness.messaging.mailbox(role="inbox")
    outbox = await harness.messaging.mailbox(role="outbox")
    assert [item["sender_kind"] for item in inbox["messages"]] == ["agent"]
    assert [item["sender_kind"] for item in outbox["messages"]] == ["user"]
    assert inbox["messages"][0]["target_label"] == "claude-s2"


@pytest.mark.asyncio
async def test_the_mcp_tools_derive_the_sender_from_the_token(harness: Harness) -> None:
    service = McpService(harness.manager, SimpleNamespace(), harness.messaging)
    caller = service.resolve_caller("Bearer tok-s1")
    assert caller.record.id == "s1"
    response = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "notify",
                # A forged sender argument is not in the schema and is ignored:
                # identity comes from the token, never from the call.
                "arguments": {"target": "s2", "body": "hello", "from_session": "s2"},
            },
        },
    )
    assert response is not None
    assert response["result"]["isError"] is False
    message = (await harness.store.mailbox(role="inbox"))[0]
    assert message["sender_id"] == "s1"


@pytest.mark.asyncio
async def test_a_refused_write_is_a_typed_result_not_a_protocol_error(
    harness: Harness,
) -> None:
    service = McpService(harness.manager, SimpleNamespace(), harness.messaging)
    caller = service.resolve_caller("Bearer tok-s1")
    response = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "notify", "arguments": {"target": "s1", "body": "hi"}},
        },
    )
    assert response is not None
    assert response["result"]["isError"] is True
    assert "self_notify" in response["result"]["content"][0]["text"]
    assert "error" not in response
