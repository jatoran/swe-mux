"""Phase 5: bounded agent-to-agent messages and drafted spawn requests.

What these pin: a notify is an ordinary queue item with provenance, not a
delivery; the receiver's policy decides whether it arrives armed; every relay
bound (Project scope, self, size, per-origin budget, target backlog, chain
depth, thread turns, rings) is enforced in the daemon operation rather than in
the MCP layer; a reply to the session that messaged you is an ordinary threaded
turn rather than a cycle; retries with a correlation id do not duplicate; and
`request_spawn` writes an inert draft that starts nothing.
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
            read_observations=read_observations,
        )

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def harness(tmp_path: Path):  # type: ignore[no-untyped-def]
    built = Harness(tmp_path, live_session("s1"), live_session("s2"))
    yield built
    built.close()


@pytest.mark.asyncio
async def test_a_notify_arrives_armed_unless_the_receiver_opted_out(
    harness: Harness,
) -> None:
    """Armed is the receiver's per-run default; opting out downgrades to a draft.

    Either way the message only *waits*: armed still needs head-of-line order,
    delivery readiness, and either a human "Send now" or the receiver's own
    auto-delivery grant under the install master switch.
    """
    await harness.auto.set_accept_agent_messages("s2", False)
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
    assert result["correlation_id"] == message["correlation_id"]
    assert f'message_id: {result["message_id"]}' in message["body"]
    assert f'correlation_id: {result["correlation_id"]}' in message["body"]
    assert "from_session: s1" in message["body"]
    assert "from_run: run-s1" in message["body"]
    assert "from_name: claude-s1" in message["body"]
    assert message["body"].endswith("\n\nI finished the migration")
    assert message["payload"] == {"kind": "agent_notify", "version": 2}

    await harness.auto.set_accept_agent_messages("s2", True)
    armed = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="second"
    )
    assert armed["state"] == "armed"


@pytest.mark.asyncio
async def test_the_receivers_conversation_default_is_what_arms_a_notify(
    harness: Harness,
) -> None:
    """No opt-in call anywhere: one controller tick is the whole authorization."""
    await harness.auto.tick()
    result = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="I finished the migration"
    )
    assert result["state"] == "armed"
    # Armed is not sent. Nothing reached the PTY on the strength of the default.
    assert not harness.writes


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
async def test_chain_depth_bounds_propagation_not_conversation(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        live_session("s1"),
        live_session("s2"),
        live_session("s3"),
        live_session("s4"),
        agent_message_max_chain_depth=2,
    )
    try:
        first = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="hop one"
        )
        # The relay context is derived from *delivered* messages, so mark it sent.
        await harness.store.finalize_delivery(
            "delivery-1", first["message_id"], outcome="sent", message_state="sent"
        )
        second = await harness.messaging.notify(
            harness.manager.sessions["s2"], target="s3", body="hop two"
        )
        assert second["chain_depth"] == 2
        await harness.store.finalize_delivery(
            "delivery-2", second["message_id"], outcome="sent", message_state="sent"
        )
        # Reaching a session that has not spoken in the thread is propagation,
        # and that is what the depth budget bounds.
        with pytest.raises(QueueError) as deep:
            await harness.messaging.notify(
                harness.manager.sessions["s3"], target="s4", body="hop three"
            )
        assert deep.value.code == "chain_depth_exceeded"
        # Answering the session that just spoke to you reaches nobody new, so it
        # is allowed even though the depth budget is already spent. Depth still
        # records that three sessions have now spoken in the thread.
        reply = await harness.messaging.notify(
            harness.manager.sessions["s3"], target="s2", body="answering you"
        )
        assert reply["is_reply"] is True
        assert reply["chain_depth"] == 3
        assert reply["thread_id"] == second["thread_id"]
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_the_envelope_states_whether_a_human_released_the_message(
    tmp_path: Path,
) -> None:
    """A peer's instruction and one a human approved must not look identical.

    A relayed "your operator says go ahead" is indistinguishable from a prompt
    injection unless the receiver can tell whether a person saw it. Observed
    2026-08-13: a session correctly refused a relayed release because it had no
    fact to weigh against its own operator's instruction.
    """
    harness = Harness(tmp_path, live_session("s1"), live_session("s2"))
    try:
        # No standing grant: the message waits for a human to arm it, so its
        # delivery *is* a person's act and the envelope says so.
        held = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="go"
        )
        drafted = await harness.store.message(held["message_id"])
        assert drafted is not None
        assert "a person saw this message" in str(drafted["body"])

        await harness.auto.enable_session("s2")
        await harness.auto.set_accept_agent_messages("s2", True)
        auto_delivered = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="go again"
        )
        stored = await harness.store.message(auto_delivered["message_id"])
        assert stored is not None
        assert "no human reviewed it" in str(stored["body"])
        assert "overrides an instruction your operator gave you" in str(stored["body"])
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_the_shipped_default_carries_a_relay_across_a_whole_fleet(
    tmp_path: Path,
) -> None:
    """A hand-off passed down every session is the shape the default must allow.

    The previous default of 3 refused the fourth hop, which killed an
    operator-authored relay silently in the middle rather than at the point it
    was written. Breadth stays bounded by the hourly budget, the per-target
    backlog, and the ring detector; this is depth.
    """
    sessions = [live_session(f"s{index}") for index in range(1, 7)]
    harness = Harness(tmp_path, *sessions)
    try:
        for index in range(1, 6):
            sender = harness.manager.sessions[f"s{index}"]
            message = await harness.messaging.notify(
                sender, target=f"s{index + 1}", body=f"relay hop {index}"
            )
            assert message["chain_depth"] == index
            await harness.store.finalize_delivery(
                f"delivery-{index}",
                message["message_id"],
                outcome="sent",
                message_state="sent",
            )
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_a_reply_to_the_sender_is_allowed_and_threaded(tmp_path: Path) -> None:
    """The case that made replies impossible: A→B→A is a conversation, not a ring."""
    harness = Harness(tmp_path, live_session("s1"), live_session("s2"))
    try:
        first = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="here is what I found"
        )
        await harness.store.finalize_delivery(
            "delivery-1", first["message_id"], outcome="sent", message_state="sent"
        )
        reply = await harness.messaging.notify(
            harness.manager.sessions["s2"], target="s1", body="acknowledged, one correction"
        )
        assert reply["is_reply"] is True
        assert reply["thread_id"] == first["thread_id"]
        # The receiver is told the reply channel exists, in the one surface they see.
        body = (await harness.store.messages_for_target("s2"))["messages"][0]["body"]
        assert 'reply_with: notify(target="s1")' in body
        # And the exchange can continue back the other way.
        await harness.store.finalize_delivery(
            "delivery-2", reply["message_id"], outcome="sent", message_state="sent"
        )
        third = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="understood"
        )
        assert third["thread_id"] == first["thread_id"]
        assert third["is_reply"] is True
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_a_thread_has_a_turn_budget(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        live_session("s1"),
        live_session("s2"),
        agent_message_max_thread_turns=2,
        agent_message_pending_per_target=10,
    )
    try:
        first = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="one"
        )
        await harness.store.finalize_delivery(
            "delivery-1", first["message_id"], outcome="sent", message_state="sent"
        )
        second = await harness.messaging.notify(
            harness.manager.sessions["s2"], target="s1", body="two"
        )
        assert second["thread_messages_remaining"] == 0
        await harness.store.finalize_delivery(
            "delivery-2", second["message_id"], outcome="sent", message_state="sent"
        )
        with pytest.raises(QueueError) as spent:
            await harness.messaging.notify(
                harness.manager.sessions["s1"], target="s2", body="three"
            )
        assert spent.value.code == "thread_budget_exhausted"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_a_ring_around_the_chain_is_still_refused(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path, live_session("s1"), live_session("s2"), live_session("s3")
    )
    try:
        first = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="hop one"
        )
        await harness.store.finalize_delivery(
            "delivery-1", first["message_id"], outcome="sent", message_state="sent"
        )
        second = await harness.messaging.notify(
            harness.manager.sessions["s2"], target="s3", body="hop two"
        )
        await harness.store.finalize_delivery(
            "delivery-2", second["message_id"], outcome="sent", message_state="sent"
        )
        # s3 may answer s2, but reaching past it to s1 closes a ring.
        with pytest.raises(QueueError) as cycle:
            await harness.messaging.notify(
                harness.manager.sessions["s3"], target="s1", body="around the back"
            )
        assert cycle.value.code == "relay_cycle"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_an_unrelated_deep_chain_does_not_wedge_a_reply(tmp_path: Path) -> None:
    """Relay context follows the peer, so one deep thread cannot block another."""
    harness = Harness(
        tmp_path,
        live_session("s1"),
        live_session("s2"),
        live_session("s3"),
        live_session("s4"),
        agent_message_max_chain_depth=2,
    )
    try:
        # A two-hop chain s1→s2→s4 leaves s4 sitting at the depth limit.
        hop1 = await harness.messaging.notify(
            harness.manager.sessions["s1"], target="s2", body="chain one"
        )
        await harness.store.finalize_delivery(
            "delivery-1", hop1["message_id"], outcome="sent", message_state="sent"
        )
        hop2 = await harness.messaging.notify(
            harness.manager.sessions["s2"], target="s4", body="chain two"
        )
        await harness.store.finalize_delivery(
            "delivery-2", hop2["message_id"], outcome="sent", message_state="sent"
        )
        # A separate, shallow thread reaches s4 from s3.
        other = await harness.messaging.notify(
            harness.manager.sessions["s3"], target="s4", body="unrelated question"
        )
        await harness.store.finalize_delivery(
            "delivery-3", other["message_id"], outcome="sent", message_state="sent"
        )
        answer = await harness.messaging.notify(
            harness.manager.sessions["s4"], target="s3", body="unrelated answer"
        )
        assert answer["thread_id"] == other["thread_id"]
        assert answer["chain_depth"] == 2
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
    fleet = await harness.messaging.mailbox(author="non_human")
    assert [item["id"] for item in fleet["spawn_requests"]] == [result["request_id"]]
    assert fleet["spawn_requests"][0]["status"] == "pending"


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
async def test_the_mailbox_separates_non_human_from_human_authors(harness: Harness) -> None:
    await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="from an agent"
    )
    await harness.service.enqueue(target_session_id="s2", body="from me", armed=False)
    non_human = await harness.messaging.mailbox(author="non_human")
    human = await harness.messaging.mailbox(author="human")
    assert [item["sender_kind"] for item in non_human["messages"]] == ["agent"]
    assert [item["sender_kind"] for item in human["messages"]] == ["user"]
    assert non_human["messages"][0]["target_label"] == "claude-s2"
    assert {target["target_session_id"] for target in human["targets"]} == {"s2"}

    # Compatibility only: old callers may still use the misleading directional names.
    assert (await harness.messaging.mailbox(role="inbox"))["author"] == "non_human"
    assert (await harness.messaging.mailbox(role="outbox"))["author"] == "human"


@pytest.mark.asyncio
async def test_the_mailbox_filters_before_its_result_limit(tmp_path: Path) -> None:
    built = Harness(
        tmp_path,
        live_session("s1", project_id="p1"),
        live_session("s2", project_id="p1"),
        live_session("s3", project_id="p2"),
    )
    try:
        await built.service.enqueue(target_session_id="s2", body="project one", armed=False)
        await built.service.enqueue(target_session_id="s3", body="project two", armed=False)

        by_project = await built.messaging.mailbox(
            author="human", project_id="p1", limit=1
        )
        by_session = await built.messaging.mailbox(
            author="human", target_session_id="s3", limit=1
        )

        assert [item["body"] for item in by_project["messages"]] == ["project one"]
        assert [item["body"] for item in by_session["messages"]] == ["project two"]
        assert {target["target_session_id"] for target in by_project["targets"]} == {"s2", "s3"}
    finally:
        built.close()


@pytest.mark.asyncio
async def test_message_status_is_visible_only_to_the_notify_sender(harness: Harness) -> None:
    sent = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="check status"
    )

    status = await harness.messaging.message_status(
        harness.manager.sessions["s1"], sent["message_id"]
    )

    assert status["status"] == "drafted"
    assert status["queue_state"] == "draft"
    assert "body" not in status
    with pytest.raises(QueueError) as caught:
        await harness.messaging.message_status(
            harness.manager.sessions["s2"], sent["message_id"]
        )
    assert caught.value.code == "unknown_message"


@pytest.mark.asyncio
async def test_message_status_preserves_expired_after_queue_cleanup(harness: Harness) -> None:
    sent = await harness.messaging.notify(
        harness.manager.sessions["s1"], target="s2", body="expire me"
    )
    await harness.service.cancel(sent["message_id"], kind="expired")

    status = await harness.messaging.message_status(
        harness.manager.sessions["s1"], sent["message_id"]
    )

    assert status["status"] == "expired"
    assert status["queue_state"] == "cancelled"
    assert status["cancel_kind"] == "expired"


@pytest.mark.asyncio
async def test_spawn_request_status_lists_only_the_callers_requests(harness: Harness) -> None:
    own = await harness.messaging.request_spawn(
        harness.manager.sessions["s1"], prompt="continue my work"
    )
    await harness.messaging.request_spawn(
        harness.manager.sessions["s2"], prompt="continue other work"
    )

    result = await harness.messaging.spawn_requests(harness.manager.sessions["s1"])

    assert [item["id"] for item in result["requests"]] == [own["request_id"]]
    assert result["requests"][0]["from_session"] == "s1"


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
