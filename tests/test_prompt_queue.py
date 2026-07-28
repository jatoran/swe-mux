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
    await harness.store.prune(90)
    assert await harness.store.message(sent["id"]) is None
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
    assert ("POST", "/api/queue/messages/{message_id}/cancel") in routes
    assert ("GET", "/api/queue/messages/{message_id}/deliveries") in routes
    assert ("POST", "/api/queue/send-next") in routes
    assert ("GET", "/api/queue/export") in routes


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
