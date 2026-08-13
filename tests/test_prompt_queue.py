"""Phase 4: persistent manual prompt queue.

What these pin is the roadmap's load-bearing rules: durable order and state
across store restarts with no duplicate delivery; strict head-of-line; the
exact revision the user saw is the revision delivered; every delivery is an
explicit act with an auditable attempt record that never carries the prompt
body; ended/replaced targets strand pending work instead of losing or
retargeting it; and blocked/unknown readiness needs explicit confirmation,
with the approval/Q&A/identity protections never overridable at all.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.prompt_queue import (
    ARGV_SEED_MAX_CHARS,
    BRACKETED_PASTE_END,
    BRACKETED_PASTE_START,
    SUBMIT_SEQUENCE,
    PromptQueueService,
    PromptQueueStore,
    QueueError,
    paste_payload,
    stage_seed_argv,
)


def record(sid: str, **kw: Any) -> Any:
    defaults = dict(
        id=sid,
        name=f"claude-{sid}",
        backend="claude",
        state="idle",
        awaiting_reason=None,
        agent_run_id=f"run-{sid}",
        project_id="p1",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def live_session(sid: str, **kw: Any) -> Any:
    return SimpleNamespace(record=record(sid, **kw))


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
    def __init__(self, state: str = "safe", reasons: list[str] | None = None) -> None:
        self.state = state
        self.reasons = reasons if reasons is not None else ["all_required_evidence_positive"]

    def evaluate(self, session: Any) -> dict[str, Any]:
        return {"delivery_state": self.state, "reasons": list(self.reasons)}


class Harness:
    def __init__(self, tmp_path: Path, *sessions: Any) -> None:
        self.store = PromptQueueStore(tmp_path / "queue.db")
        self.events = EventsStub()
        self.readiness = ReadinessStub()
        self.manager = SimpleNamespace(
            sessions={session.record.id: session for session in sessions}
        )
        self.writes: list[tuple[str, str]] = []
        self.service = PromptQueueService(
            self.store,
            self.manager,
            self.events,
            self.readiness,
            lambda session, data: self.writes.append((session.record.id, data)),
            submit_delay=0.0,
        )


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    built = Harness(tmp_path, live_session("s1"), live_session("s2"))
    yield built
    built.store.close()


# ------------------------------------------------------------- message model


@pytest.mark.asyncio
async def test_enqueue_orders_and_inserts_after(harness: Harness) -> None:
    first = await harness.service.enqueue(target_session_id="s1", body="one")
    second = await harness.service.enqueue(target_session_id="s1", body="two")
    inserted = await harness.service.enqueue(
        target_session_id="s1", body="between", insert_after=first["id"]
    )
    view = await harness.service.target_view("s1")
    bodies = [item["body"] for item in view["messages"]]
    assert bodies == ["one", "between", "two"]
    assert [item["position"] for item in view["messages"]] == [0, 1, 2]
    assert view["pending"] == 3
    assert second["state"] == "draft"
    assert inserted["target_agent_run_id"] == "run-s1"
    assert inserted["target_label"] == "claude-s1"


@pytest.mark.asyncio
async def test_queues_target_agent_sessions_only(tmp_path: Path) -> None:
    harness = Harness(tmp_path, live_session("sh", backend="shell"))
    try:
        with pytest.raises(QueueError) as caught:
            await harness.service.enqueue(target_session_id="sh", body="rm -rf /")
        assert caught.value.code == "not_agent_target"
        with pytest.raises(QueueError) as missing:
            await harness.service.enqueue(target_session_id="ghost", body="x")
        assert missing.value.status == 404
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_edit_increments_revision_and_send_checks_it(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="v1")
    edited = await harness.service.edit(message["id"], revision=1, body="v2")
    assert edited["revision"] == 2
    with pytest.raises(QueueError) as stale_edit:
        await harness.service.edit(message["id"], revision=1, body="v3")
    assert stale_edit.value.code == "revision_conflict"
    with pytest.raises(QueueError) as stale_send:
        await harness.service.send_next(message["id"], revision=1)
    assert stale_send.value.code == "revision_conflict"
    # The revision the user last saw is the revision delivered.
    result = await harness.service.send_next(message["id"], revision=2)
    assert result["status"] == "sent"
    assert paste_payload("v2") in [data for _, data in harness.writes]


@pytest.mark.asyncio
async def test_sent_messages_are_immutable(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="done")
    await harness.service.send_next(message["id"], revision=1)
    for operation in (
        harness.service.edit(message["id"], revision=1, body="x"),
        harness.service.set_armed(message["id"], True),
        harness.service.move(message["id"], after=None),
        harness.service.cancel(message["id"]),
    ):
        with pytest.raises(QueueError) as caught:
            await operation
        assert caught.value.code == "immutable_state"


@pytest.mark.asyncio
async def test_arm_unarm_and_reorder(harness: Harness) -> None:
    first = await harness.service.enqueue(target_session_id="s1", body="a")
    second = await harness.service.enqueue(target_session_id="s1", body="b", armed=True)
    assert second["state"] == "armed"
    unarmed = await harness.service.set_armed(second["id"], False)
    assert unarmed["state"] == "draft"
    moved = await harness.service.move(second["id"], after=None)
    assert moved["position"] == 0
    view = await harness.service.target_view("s1")
    assert [item["id"] for item in view["messages"]] == [second["id"], first["id"]]


# ------------------------------------------------------------- head of line


@pytest.mark.asyncio
async def test_strict_head_of_line_blocks_armed_later_items(harness: Harness) -> None:
    head = await harness.service.enqueue(target_session_id="s1", body="first")
    later = await harness.service.enqueue(target_session_id="s1", body="second", armed=True)
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(later["id"], revision=1)
    assert caught.value.code == "head_of_line_blocked"
    assert caught.value.payload["blocking_message_id"] == head["id"]
    # Skipping the head explicitly releases the line.
    await harness.service.cancel(head["id"], kind="skipped")
    result = await harness.service.send_next(later["id"], revision=1)
    assert result["status"] == "sent"
    skipped = await harness.store.message(head["id"])
    assert skipped is not None and skipped["state"] == "cancelled"
    assert skipped["cancel_kind"] == "skipped"


@pytest.mark.asyncio
async def test_delete_erases_any_non_delivering_item_and_releases_the_head(
    harness: Harness,
) -> None:
    head = await harness.service.enqueue(target_session_id="s1", body="remove me", armed=True)
    later = await harness.service.enqueue(target_session_id="s1", body="send me", armed=True)

    deleted = await harness.service.delete(head["id"])
    assert deleted["previous_state"] == "armed"
    view = await harness.service.target_view("s1")
    assert [item["id"] for item in view["messages"]] == [later["id"]]
    assert view["messages"][0]["position"] == 0
    assert view["pending"] == 1
    assert (await harness.service.send_next(later["id"], revision=1))["status"] == "sent"

    tombstone = await harness.store.message(head["id"])
    assert tombstone is not None
    assert tombstone["state"] == "deleted"
    assert tombstone["body"] == ""
    assert tombstone["deleted_at"] is not None
    assert (await harness.service.delete(head["id"]))["already_deleted"] is True
    assert any(
        event == "queue_updated" and payload.get("state") == "deleted"
        for event, payload in harness.events.emitted
    )


@pytest.mark.asyncio
async def test_delete_removes_closed_items_but_keeps_delivery_audit(harness: Harness) -> None:
    sent = await harness.service.enqueue(target_session_id="s1", body="delivered body")
    await harness.service.send_next(sent["id"], revision=1)
    skipped = await harness.service.enqueue(target_session_id="s1", body="skipped body")
    await harness.service.cancel(skipped["id"], kind="skipped")
    assert len(await harness.store.deliveries(sent["id"])) == 1

    await harness.service.delete(sent["id"])
    await harness.service.delete(skipped["id"])
    assert await harness.service.export_target("s1", redact_secrets=False) == {
        "target_session_id": "s1",
        "messages": [],
    }
    audit = await harness.store.deliveries(sent["id"])
    assert len(audit) == 1 and audit[0]["outcome"] == "sent"


@pytest.mark.asyncio
async def test_delete_rejects_a_message_already_claimed_for_delivery(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="in flight")
    await harness.store.claim_for_delivery(message["id"], revision=1, idempotency_key=None)

    with pytest.raises(QueueError) as caught:
        await harness.service.delete(message["id"])
    assert caught.value.code == "delivery_in_progress"
    assert caught.value.status == 409


@pytest.mark.asyncio
async def test_deleted_correlation_tombstone_prevents_retry_resurrection(
    harness: Harness,
) -> None:
    original = await harness.service.enqueue(
        target_session_id="s1",
        body="agent message",
        sender_kind="agent",
        sender_id="s2",
        correlation_id="retry-1",
    )
    await harness.service.delete(original["id"])

    retry = await harness.service.enqueue(
        target_session_id="s1",
        body="agent message retried",
        sender_kind="agent",
        sender_id="s2",
        correlation_id="retry-1",
    )
    assert retry["id"] == original["id"]
    assert retry["state"] == "deleted"
    assert retry["deduplicated"] is True
    assert (await harness.service.target_view("s1"))["messages"] == []


@pytest.mark.asyncio
async def test_queues_are_per_target(harness: Harness) -> None:
    await harness.service.enqueue(target_session_id="s1", body="s1 head")
    other = await harness.service.enqueue(target_session_id="s2", body="s2 head")
    result = await harness.service.send_next(other["id"], revision=1)
    assert result["status"] == "sent"
    assert harness.writes[0][0] == "s2"


# ----------------------------------------------------------------- delivery


@pytest.mark.asyncio
async def test_delivery_writes_paste_then_submit_and_audits_without_body(
    harness: Harness,
) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="line1\nline2")
    result = await harness.service.send_next(message["id"], revision=1)
    assert result["status"] == "sent"
    assert harness.writes == [
        ("s1", f"{BRACKETED_PASTE_START}line1\rline2{BRACKETED_PASTE_END}"),
        ("s1", SUBMIT_SEQUENCE),
    ]
    deliveries = await harness.store.deliveries(message["id"])
    assert len(deliveries) == 1
    attempt = deliveries[0]
    assert attempt["outcome"] == "sent"
    assert attempt["delivery_state"] == "safe"
    # Audit without duplication: no prompt text in the attempt record or events.
    assert "line1" not in str(attempt)
    assert all("line1" not in str(payload) for _, payload in harness.events.emitted)
    kinds = [name for name, _ in harness.events.emitted]
    assert "queue_delivery" in kinds and "queue_updated" in kinds


@pytest.mark.asyncio
async def test_delivery_leaves_the_authorship_mark_the_observer_reads(
    harness: Harness,
) -> None:
    """The CLI is about to fire a submit hook that looks exactly like typing.

    Authorship exists at delivery and nowhere downstream, so the observer needs
    it left behind here to decide whether the prompt refreshes
    `last_human_prompt_at` (`observation._note_prompt_authorship`).
    """
    session = harness.manager.sessions["s1"]

    human = await harness.service.enqueue(target_session_id="s1", body="mine")
    await harness.service.send_next(human["id"], revision=1)
    assert session.queue_delivery_mark is not None
    assert session.queue_delivery_mark[1] is True

    agent = await harness.service.enqueue(
        target_session_id="s1", body="theirs", sender_kind="agent", sender_id="peer",
    )
    await harness.service.send_next(agent["id"], revision=1)
    assert session.queue_delivery_mark is not None
    assert session.queue_delivery_mark[1] is False


@pytest.mark.asyncio
async def test_idempotency_key_never_delivers_twice(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="once")
    first = await harness.service.send_next(
        message["id"], revision=1, idempotency_key="key-1"
    )
    assert first["status"] == "sent"
    second = await harness.service.send_next(
        message["id"], revision=1, idempotency_key="key-1"
    )
    assert second["status"] == "duplicate"
    assert second["outcome"] == "sent"
    assert len([write for write in harness.writes if write[1] == SUBMIT_SEQUENCE]) == 1


@pytest.mark.asyncio
async def test_blocked_readiness_requires_explicit_confirmation(harness: Harness) -> None:
    harness.readiness.state = "blocked"
    harness.readiness.reasons = ["root_agent_working"]
    message = await harness.service.enqueue(target_session_id="s1", body="wait")
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(message["id"], revision=1)
    assert caught.value.code == "delivery_not_safe"
    blocked = await harness.store.message(message["id"])
    assert blocked is not None and blocked["state"] == "blocked"
    assert blocked["blocked_reasons"] == ["root_agent_working"]
    deliveries = await harness.store.deliveries(message["id"])
    assert deliveries[0]["outcome"] == "refused"
    # The refused attempt did not write the PTY.
    assert harness.writes == []
    result = await harness.service.send_next(message["id"], revision=1, confirm=True)
    assert result["status"] == "sent"
    assert result["confirmed"] is True


@pytest.mark.asyncio
async def test_a_refusal_does_not_consume_the_idempotency_key(harness: Harness) -> None:
    # The key guards duplicate deliveries; a refusal never wrote the PTY, so the
    # confirm retry with the same key must attempt delivery, not replay the refusal.
    harness.readiness.state = "blocked"
    harness.readiness.reasons = ["root_agent_working"]
    message = await harness.service.enqueue(target_session_id="s1", body="retry me")
    with pytest.raises(QueueError):
        await harness.service.send_next(message["id"], revision=1, idempotency_key="key-r")
    result = await harness.service.send_next(
        message["id"], revision=1, idempotency_key="key-r", confirm=True
    )
    assert result["status"] == "sent"
    # A replay of the *delivered* attempt is still a duplicate.
    replay = await harness.service.send_next(
        message["id"], revision=1, idempotency_key="key-r", confirm=True
    )
    assert replay["status"] == "duplicate"


@pytest.mark.asyncio
async def test_unknown_readiness_also_requires_confirmation(harness: Harness) -> None:
    harness.readiness.state = "unknown"
    harness.readiness.reasons = ["no_root_lifecycle_evidence"]
    message = await harness.service.enqueue(target_session_id="s1", body="x")
    with pytest.raises(QueueError):
        await harness.service.send_next(message["id"], revision=1)
    result = await harness.service.send_next(message["id"], revision=1, confirm=True)
    assert result["status"] == "sent"


@pytest.mark.asyncio
async def test_approval_protection_is_never_overridable(tmp_path: Path) -> None:
    session = live_session("s1", state="awaiting", awaiting_reason="approval")
    harness = Harness(tmp_path, session)
    harness.readiness.state = "blocked"
    harness.readiness.reasons = ["approval_required"]
    try:
        message = await harness.service.enqueue(target_session_id="s1", body="yes")
        with pytest.raises(QueueError) as caught:
            await harness.service.send_next(message["id"], revision=1, confirm=True)
        assert caught.value.code == "delivery_protected"
        assert caught.value.payload["protected"] is True
        assert harness.writes == []
        blocked = await harness.store.message(message["id"])
        assert blocked is not None and blocked["state"] == "blocked"
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_failed_write_marks_failed_not_sent(tmp_path: Path) -> None:
    harness = Harness(tmp_path, live_session("s1"))

    def explode(session: Any, data: str) -> None:
        raise OSError("pty write failed")

    harness.service._write = explode
    try:
        message = await harness.service.enqueue(target_session_id="s1", body="x")
        with pytest.raises(QueueError) as caught:
            await harness.service.send_next(message["id"], revision=1)
        assert caught.value.code == "delivery_failed"
        failed = await harness.store.message(message["id"])
        assert failed is not None and failed["state"] == "failed"
        deliveries = await harness.store.deliveries(message["id"])
        assert deliveries[0]["outcome"] == "failed"
    finally:
        harness.store.close()


# ---------------------------------------------------- stranding and identity


@pytest.mark.asyncio
async def test_session_end_strands_pending_and_fails_delivering(harness: Harness) -> None:
    pending = await harness.service.enqueue(target_session_id="s1", body="a")
    await harness.service.enqueue(target_session_id="s1", body="b", armed=True)
    await harness.store.claim_for_delivery(pending["id"], 1, None)
    await harness.service._strand("s1", "target session ended")
    view = await harness.service.target_view("s1")
    states = {item["body"]: item["state"] for item in view["messages"]}
    assert states == {"a": "failed", "b": "stranded"}
    stranded = next(item for item in view["messages"] if item["body"] == "b")
    assert stranded["stranded_reason"] == "target session ended"


@pytest.mark.asyncio
async def test_send_to_ended_target_strands(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="x")
    harness.manager.sessions["s1"].record.state = "exited"
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(message["id"], revision=1, confirm=True)
    assert caught.value.code == "target_ended"
    stranded = await harness.store.message(message["id"])
    assert stranded is not None and stranded["state"] == "stranded"
    assert harness.writes == []


@pytest.mark.asyncio
async def test_replaced_run_strands_instead_of_retargeting(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="x")
    harness.manager.sessions["s1"].record.agent_run_id = "run-other"
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(message["id"], revision=1, confirm=True)
    assert caught.value.code == "target_run_replaced"
    stranded = await harness.store.message(message["id"])
    assert stranded is not None and stranded["state"] == "stranded"


@pytest.mark.asyncio
async def test_binds_to_first_run_of_a_starting_session(tmp_path: Path) -> None:
    session = live_session("s1", agent_run_id=None, state="starting")
    harness = Harness(tmp_path, session)
    try:
        message = await harness.service.enqueue(target_session_id="s1", body="seed")
        assert message["target_agent_run_id"] is None
        session.record.agent_run_id = "run-first"
        session.record.state = "idle"
        result = await harness.service.send_next(message["id"], revision=1)
        assert result["status"] == "sent"
        bound = await harness.store.message(message["id"])
        assert bound is not None and bound["target_agent_run_id"] == "run-first"
    finally:
        harness.store.close()


@pytest.mark.asyncio
async def test_retarget_is_explicit_and_only_for_stranded(harness: Harness) -> None:
    message = await harness.service.enqueue(target_session_id="s1", body="orphan")
    with pytest.raises(QueueError):
        await harness.service.retarget(message["id"], target_session_id="s2")
    await harness.service._strand("s1", "target session ended")
    await harness.service.enqueue(target_session_id="s2", body="existing")
    moved = await harness.service.retarget(message["id"], target_session_id="s2")
    assert moved["state"] == "draft"
    assert moved["target_session_id"] == "s2"
    assert moved["target_agent_run_id"] == "run-s2"
    assert moved["retargeted_from"]["session_id"] == "s1"
    view = await harness.service.target_view("s2")
    assert [item["body"] for item in view["messages"]] == ["existing", "orphan"]


# --------------------------------------------------- restart survival


@pytest.mark.asyncio
async def test_queue_survives_store_restart_without_duplicate_delivery(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, live_session("s1"))
    first = await harness.service.enqueue(target_session_id="s1", body="ordered-1")
    await harness.service.enqueue(target_session_id="s1", body="ordered-2", armed=True)
    await harness.service.send_next(first["id"], revision=1, idempotency_key="idem-1")
    harness.store.close()

    reopened = Harness(tmp_path, live_session("s1"))
    try:
        view = await reopened.service.target_view("s1")
        assert [item["body"] for item in view["messages"]] == ["ordered-1", "ordered-2"]
        assert [item["state"] for item in view["messages"]] == ["sent", "armed"]
        # A retried send for the already-delivered item replays the recorded
        # outcome instead of writing the PTY again.
        replay = await reopened.service.send_next(
            first["id"], revision=1, idempotency_key="idem-1"
        )
        assert replay["status"] == "duplicate"
        assert reopened.writes == []
    finally:
        reopened.store.close()


@pytest.mark.asyncio
async def test_startup_reconcile_strands_missing_targets_and_fails_delivering(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, live_session("s1"), live_session("s2"))
    gone = await harness.service.enqueue(target_session_id="s1", body="lost target")
    mid = await harness.service.enqueue(target_session_id="s2", body="mid delivery")
    await harness.store.claim_for_delivery(mid["id"], 1, None)
    harness.store.close()

    # The next daemon adopts only s2; s1 did not survive.
    reopened = Harness(tmp_path, live_session("s2"))
    try:
        await reopened.service._reconcile_startup()
        stranded = await reopened.store.message(gone["id"])
        assert stranded is not None and stranded["state"] == "stranded"
        interrupted = await reopened.store.message(mid["id"])
        assert interrupted is not None and interrupted["state"] == "failed"
        deliveries = await reopened.store.deliveries(mid["id"])
        assert deliveries[0]["outcome"] == "failed"
        assert "restart" in str(deliveries[0]["error"])
    finally:
        reopened.store.close()


@pytest.mark.asyncio
async def test_event_loop_strands_on_session_exit(tmp_path: Path) -> None:
    harness = Harness(tmp_path, live_session("s1"))
    try:
        await harness.service.enqueue(target_session_id="s1", body="pending")
        harness.service._queue = asyncio.Queue()
        harness.service._queue.put_nowait(
            SimpleNamespace(type="session_exited", session_id="s1")
        )
        consumer = asyncio.create_task(harness.service._consume())
        await asyncio.sleep(0.05)
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        view = await harness.service.target_view("s1")
        assert view["messages"][0]["state"] == "stranded"
    finally:
        harness.store.close()


# ------------------------------------------------------- history and export


@pytest.mark.asyncio
async def test_export_redacts_credential_shaped_bodies_by_choice(harness: Harness) -> None:
    secret = "api_key = 'sk-ant-abcdefghijklmnopqrstuvwx'"
    await harness.service.enqueue(target_session_id="s1", body=secret)
    redacted = await harness.service.export_target("s1", redact_secrets=True)
    assert "sk-ant" not in str(redacted)
    assert redacted["messages"][0]["redacted"] is True
    verbatim = await harness.service.export_target("s1", redact_secrets=False)
    assert verbatim["messages"][0]["body"] == secret


@pytest.mark.asyncio
async def test_summary_groups_by_target(harness: Harness) -> None:
    await harness.service.enqueue(target_session_id="s1", body="a")
    await harness.service.enqueue(target_session_id="s1", body="b")
    await harness.service.enqueue(target_session_id="s2", body="c")
    rows = {row["target_session_id"]: row for row in await harness.service.summary()}
    assert rows["s1"]["pending"] == 2
    assert rows["s2"]["pending"] == 1
    assert rows["s1"]["live"] is True


@pytest.mark.asyncio
async def test_prune_ages_out_terminal_items_only(harness: Harness) -> None:
    sent = await harness.service.enqueue(target_session_id="s1", body="old sent")
    await harness.service.send_next(sent["id"], revision=1)
    keep = await harness.service.enqueue(target_session_id="s1", body="still pending")
    deleted = await harness.service.enqueue(target_session_id="s1", body="old tombstone")
    await harness.service.delete(deleted["id"])

    def age(message_id: str) -> None:
        harness.store._db.execute(
            "UPDATE queue_messages SET updated_at=? WHERE id=?",
            (time.time() - 400 * 86400, message_id),
        )
        harness.store._db.commit()

    await asyncio.get_running_loop().run_in_executor(
        harness.store._executor, age, sent["id"]
    )
    await asyncio.get_running_loop().run_in_executor(
        harness.store._executor, age, keep["id"]
    )
    await asyncio.get_running_loop().run_in_executor(
        harness.store._executor, age, deleted["id"]
    )
    await harness.store.prune(90)
    assert await harness.store.message(sent["id"]) is None
    assert await harness.store.message(deleted["id"]) is None
    survivor = await harness.store.message(keep["id"])
    assert survivor is not None and survivor["state"] == "draft"


# ---------------------------------------------------------------- routes


def test_queue_routes_are_registered(tmp_path: Path) -> None:
    from swe_mux.config import Config
    from swe_mux.server import create_app

    app = create_app(Config(data_dir=tmp_path))
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/queue") in routes
    assert ("GET", "/api/queue/messages") in routes
    assert ("POST", "/api/queue/messages") in routes
    assert ("PATCH", "/api/queue/messages/{message_id}") in routes
    assert ("DELETE", "/api/queue/messages/{message_id}") in routes
    assert ("POST", "/api/queue/messages/{message_id}/cancel") in routes
    assert ("GET", "/api/queue/messages/{message_id}/deliveries") in routes
    assert ("POST", "/api/queue/send-next") in routes
    assert ("GET", "/api/queue/export") in routes
    # Phase 5 surface.
    assert ("GET", "/api/queue/auto") in routes
    assert ("POST", "/api/queue/auto/pause") in routes
    assert ("PUT", "/api/queue/auto/sessions/{sid}") in routes
    assert ("POST", "/api/queue/auto/report-unsafe") in routes
    assert ("GET", "/api/queue/mailbox") in routes
    assert (
        "POST",
        "/api/projects/{project_id}/observations/{observation_id}/decide",
    ) in routes


# ------------------------------------------------------------- schema v3


@pytest.mark.asyncio
async def test_a_v1_database_migrates_in_place(tmp_path: Path) -> None:
    """A queue written by a Phase 4 build must keep working after the upgrade.

    `CREATE TABLE IF NOT EXISTS` no-ops on the old table, so without the
    migration the Phase 5 columns would exist only in fresh databases and the
    first insert naming one would fail.
    """
    import sqlite3

    path = tmp_path / "queue.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        """
        CREATE TABLE queue_messages (
          id TEXT PRIMARY KEY, target_session_id TEXT NOT NULL, target_agent_run_id TEXT,
          target_backend TEXT, target_label TEXT, project_id TEXT, position INTEGER NOT NULL,
          state TEXT NOT NULL, body TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
          sender_kind TEXT NOT NULL DEFAULT 'user', sender_id TEXT, origin_json TEXT,
          payload_json TEXT, constraints_json TEXT, blocked_reasons_json TEXT,
          stranded_reason TEXT, cancel_kind TEXT, retargeted_from_json TEXT,
          created_at REAL NOT NULL, updated_at REAL NOT NULL, edited_at REAL,
          armed_at REAL, sent_at REAL
        );
        CREATE TABLE queue_deliveries (
          id TEXT PRIMARY KEY, message_id TEXT NOT NULL, idempotency_key TEXT,
          revision INTEGER NOT NULL, target_session_id TEXT NOT NULL,
          target_agent_run_id TEXT, delivery_state TEXT, reasons_json TEXT,
          confirmed INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL, error TEXT,
          bytes INTEGER, created_at REAL NOT NULL, completed_at REAL
        );
        """
    )
    legacy.execute(
        "INSERT INTO queue_messages(id,target_session_id,position,state,body,revision,"
        "sender_kind,created_at,updated_at) VALUES('m1','s1',0,'armed','legacy',1,'user',1,1)"
    )
    legacy.commit()
    legacy.close()

    store = PromptQueueStore(path)
    try:
        kept = await store.message("m1")
        assert kept is not None and kept["body"] == "legacy"
        assert kept["chain_depth"] == 0 and kept["correlation_id"] is None
        assert kept["deleted_at"] is None
        fresh = await store.create_message(
            target_session_id="s1",
            target_agent_run_id="run-s1",
            target_backend="claude",
            target_label="claude-s1",
            project_id="p1",
            body="after the upgrade",
            armed=True,
            sender_kind="agent",
            sender_id="s2",
            correlation_id="corr-1",
            chain_depth=1,
        )
        assert fresh["chain_depth"] == 1
        # The legacy item still holds the head of the line, exactly as it did
        # before the upgrade.
        await store.cancel("m1", kind="skipped")
        claim = await store.claim_for_delivery(fresh["id"], 1, None, initiator="auto")
        assert claim["status"] == "claimed"
        rows = await store.deliveries(fresh["id"])
        assert rows[0]["initiator"] == "auto"
    finally:
        store.close()


# -------------------------------------------------------------- seed staging


def test_paste_payload_matches_the_browser_wrapper() -> None:
    expected = f"{BRACKETED_PASTE_START}a\rb\rc\rd{BRACKETED_PASTE_END}"
    assert paste_payload("a\r\nb\rc\nd") == expected


def test_stage_seed_argv_inlines_short_bodies(tmp_path: Path) -> None:
    assert stage_seed_argv(str(tmp_path), "fix the bug") == "fix the bug"
    assert stage_seed_argv(str(tmp_path), "-starts like a flag") == " -starts like a flag"
    assert not (tmp_path / ".swe-mux").exists()


def test_stage_seed_argv_stages_long_bodies_in_the_workspace(tmp_path: Path) -> None:
    body = "x" * (ARGV_SEED_MAX_CHARS + 1)
    prompt = stage_seed_argv(str(tmp_path), body)
    assert len(prompt) < 400
    seeds = list((tmp_path / ".swe-mux" / "seeds").glob("seed-*.md"))
    assert len(seeds) == 1
    assert seeds[0].read_text(encoding="utf-8") == body
    assert seeds[0].relative_to(tmp_path).as_posix() in prompt
    assert (tmp_path / ".swe-mux" / "seeds" / ".gitignore").read_text() == "*\n"
