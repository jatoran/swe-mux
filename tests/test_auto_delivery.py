"""Phase 5: gated auto-delivery.

What these pin is the gate, not the delivery: delivery itself is the Phase 4
queue operation and is already covered. Here: the install master switch plus
default-on bounded conversation grants, an
expiry, a consecutive-send cap that a human send resets, a stability window
that a single safe sample cannot satisfy, quiet hours, the persisted emergency
pause, schedule constraints honoured by both paths, the never-overrides rule,
and fail-closed behaviour after a failed delivery.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.auto_delivery import (
    COUNTER_SENT,
    COUNTER_UNSAFE,
    FAILED_DELIVERY_REASON,
    AutoDeliveryController,
    in_quiet_window,
    promotion_status,
)
from swe_mux.config import Config
from swe_mux.prompt_queue import (
    SEND_CAP_REASON,
    PromptQueueService,
    PromptQueueStore,
    QueueError,
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
        project_scope_id="scope-1",
        cwd="C:/repo",
        # The grant is measured against this, so a session is "in use" by
        # default and a test that wants a lapse has to say so.
        last_activity_ts=time.time(),
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
    def __init__(self, state: str = "safe", *, interject_state: str = "blocked") -> None:
        self.state = state
        # Defaults to blocked: a test that says nothing about mid-turn delivery
        # must not accidentally authorize one.
        self.interject_state = interject_state

    def evaluate(self, session: Any) -> dict[str, Any]:
        return {
            "delivery_state": self.state,
            "reasons": ["all_required_evidence_positive"],
            "interject_state": self.interject_state,
            "interject_reasons": [],
        }


class Harness:
    def __init__(self, tmp_path: Path, *sessions: Any, **config_overrides: Any) -> None:
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
        defaults: dict[str, Any] = {
            "auto_delivery_enabled": True,
            # Comfortably under the settle sleep below: asyncio may fire a timer
            # up to the platform clock resolution *early* (~15 ms on Windows),
            # so a window close to the sleep makes the wait order-dependent.
            "auto_delivery_stable_seconds": 0.01,
            "auto_delivery_max_consecutive": 2,
            "auto_delivery_refusal_backoff_seconds": 0.0,
        }
        defaults.update(config_overrides)
        self.config = Config(**defaults)
        self.auto = AutoDeliveryController(self.service, self.manager, self.config)

    async def settle(self) -> None:
        """One tick that only starts the stability window, then one that may send."""
        await self.auto.tick()
        await asyncio.sleep(float(self.config.auto_delivery_stable_seconds) + 0.05)
        return None

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def harness(tmp_path: Path):  # type: ignore[no-untyped-def]
    built = Harness(tmp_path, live_session("s1"))
    yield built
    built.close()


@pytest.mark.asyncio
async def test_master_switch_is_required_but_agent_conversations_default_on(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, live_session("s1"), auto_delivery_enabled=False)
    message = await harness.service.enqueue(
        target_session_id="s1", body="go", armed=True
    )
    try:
        await harness.settle()
        assert await harness.auto.tick() == []
        policy = await harness.store.auto_policy("s1")
        assert policy is not None and policy["enabled"]
        assert policy["updated_by"] == "conversation-default"
        assert not harness.writes

        harness.config.auto_delivery_enabled = True
        await harness.settle()
        assert await harness.auto.tick() == [message["id"]]
        assert harness.writes
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_explicit_opt_out_is_sticky_for_the_current_conversation(
    harness: Harness,
) -> None:
    await harness.auto.disable_session("s1")
    await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
    await harness.settle()
    assert await harness.auto.tick() == []
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and not policy["enabled"]
    assert policy["agent_run_id"] == "run-s1"
    assert policy["disabled_reason"] == "disabled by user"


@pytest.mark.asyncio
async def test_only_an_armed_user_message_is_eligible(harness: Harness) -> None:
    await harness.auto.enable_session("s1")
    draft = await harness.service.enqueue(target_session_id="s1", body="draft", armed=False)
    await harness.settle()
    assert await harness.auto.tick() == []
    # An unarmed head also blocks everything behind it (strict head-of-line),
    # so arming the second message must not jump the queue.
    await harness.service.enqueue(target_session_id="s1", body="second", armed=True)
    await harness.settle()
    assert await harness.auto.tick() == []
    armed = await harness.service.set_armed(draft["id"], True)
    await harness.settle()
    assert await harness.auto.tick() == [armed["id"]]


@pytest.mark.asyncio
async def test_an_agent_message_is_accepted_armed_by_the_default_grant(
    harness: Harness,
) -> None:
    """`accept_agent_messages` rides along with the per-run conversation default.

    No explicit opt-in here: the grant the controller materializes for a live agent
    run is what authorizes an agent-authored head, exactly as it authorizes a human
    one. The install master switch is still the thing that decides anything sends.
    """
    message = await harness.service.enqueue(
        target_session_id="s1",
        body="from another agent",
        armed=True,
        sender_kind="agent",
        sender_id="s2",
    )
    await harness.settle()
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and policy["accept_agent_messages"]
    assert policy["updated_by"] == "conversation-default"
    assert await harness.auto.tick() == [message["id"]]


@pytest.mark.asyncio
async def test_an_agent_message_is_refused_once_the_receiver_opts_out(
    harness: Harness,
) -> None:
    await harness.auto.tick()  # materialize the run-bound conversation default
    await harness.auto.set_accept_agent_messages("s1", False)
    message = await harness.service.enqueue(
        target_session_id="s1",
        body="from another agent",
        armed=True,
        sender_kind="agent",
        sender_id="s2",
    )
    await harness.settle()
    assert await harness.auto.tick() == []
    await harness.auto.set_accept_agent_messages("s1", True)
    await harness.settle()
    assert await harness.auto.tick() == [message["id"]]


@pytest.mark.asyncio
async def test_re_arming_a_grant_leaves_the_agent_message_opt_out_alone(
    harness: Harness,
) -> None:
    """Arming is authorization; auto-delivery is who presses send.

    Cycling the auto-delivery toggle is a statement about the second one, so it must
    not quietly undo a separate decision the user made about the first.
    """
    await harness.auto.tick()
    await harness.auto.set_accept_agent_messages("s1", False)
    await harness.auto.disable_session("s1")
    await harness.auto.enable_session("s1")
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and policy["enabled"]
    assert not policy["accept_agent_messages"]


@pytest.mark.asyncio
async def test_one_safe_sample_is_not_enough(harness: Harness) -> None:
    await harness.auto.enable_session("s1")
    await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
    # First observation only opens the window.
    assert await harness.auto.tick() == []
    # Readiness flapping to not-safe resets it; the next safe sample starts over.
    harness.readiness.state = "unknown"
    await asyncio.sleep(0.06)
    assert await harness.auto.tick() == []
    harness.readiness.state = "safe"
    assert await harness.auto.tick() == []
    await asyncio.sleep(0.06)
    assert len(await harness.auto.tick()) == 1


@pytest.mark.asyncio
async def test_not_safe_is_never_overridden(harness: Harness) -> None:
    await harness.auto.enable_session("s1")
    await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
    harness.readiness.state = "blocked"
    await harness.settle()
    assert await harness.auto.tick() == []
    assert not harness.writes
    # And the queue operation refuses a non-human initiator that tries anyway.
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(
            "whatever", revision=1, confirm=True, initiator="auto"
        )
    assert caught.value.code == "confirm_requires_user"


@pytest.mark.asyncio
async def test_the_consecutive_cap_disables_the_grant_and_a_human_send_resets_it(
    harness: Harness,
) -> None:
    await harness.auto.enable_session("s1", max_sends=1)
    first = await harness.service.enqueue(target_session_id="s1", body="one", armed=True)
    await harness.settle()
    assert await harness.auto.tick() == [first["id"]]
    second = await harness.service.enqueue(target_session_id="s1", body="two", armed=True)
    await harness.settle()
    assert await harness.auto.tick() == []
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and not policy["enabled"]
    assert policy["disabled_reason"] == SEND_CAP_REASON
    # A manual delivery is evidence of attention: it resets the budget *and*
    # restores the grant the budget switched off. Resetting the count alone was
    # the 2026-08-19 bug — the conversation-default pass deliberately refuses to
    # restore anything but a lapse, so the grant stayed off for the whole run
    # while `sends_used` read 0 and the operator hand-pumped every send.
    await harness.service.send_next(second["id"], revision=second["revision"])
    refreshed = await harness.store.auto_policy("s1")
    assert refreshed is not None
    assert refreshed["sends_used"] == 0
    assert refreshed["enabled"]
    assert refreshed["disabled_reason"] is None
    # And the restored grant actually delivers again, which is the only thing
    # the operator cares about.
    third = await harness.service.enqueue(target_session_id="s1", body="three", armed=True)
    await harness.settle()
    assert await harness.auto.tick() == [third["id"]]


@pytest.mark.asyncio
async def test_a_grant_capped_by_an_older_build_still_recovers(harness: Harness) -> None:
    """The reason string was renamed; rows written by the previous build remain.

    Every install that hit this bug has rows carrying the old spelling, and a
    predicate that only knew the new one would leave exactly those grants off
    forever — the fix appearing to do nothing on the machine that needed it.
    """
    for legacy in ("reached 3 consecutive automatic sends", "automatic send budget exhausted"):
        await harness.auto.enable_session("s1")
        await harness.store.set_auto_policy(
            "s1", enabled=0, disabled_reason=legacy, sends_used=3
        )
        message = await harness.service.enqueue(
            target_session_id="s1", body="by hand", armed=True
        )
        await harness.service.send_next(message["id"], revision=message["revision"])
        restored = await harness.store.auto_policy("s1")
        assert restored is not None, legacy
        assert restored["enabled"], legacy
        assert restored["disabled_reason"] is None, legacy


@pytest.mark.asyncio
async def test_a_decision_is_not_cleared_by_attention(harness: Harness) -> None:
    """Only the cap clears on evidence. An opt-out and a failed-delivery hold
    each record something a human has to resolve, and a manual send is not that
    resolution — it says the operator is present, not that they verified the
    terminal or changed their mind."""
    for reason in ("disabled by user", FAILED_DELIVERY_REASON):
        await harness.auto.enable_session("s1")
        await harness.store.set_auto_policy("s1", enabled=0, disabled_reason=reason)
        message = await harness.service.enqueue(
            target_session_id="s1", body="by hand", armed=True
        )
        await harness.service.send_next(message["id"], revision=message["revision"])
        held = await harness.store.auto_policy("s1")
        assert held is not None, reason
        assert not held["enabled"], reason
        assert held["disabled_reason"] == reason
        # The count still resets: that part was never the decision.
        assert held["sends_used"] == 0


@pytest.mark.asyncio
async def test_the_controller_delivers_a_mid_turn_item_into_a_working_session(
    harness: Harness,
) -> None:
    """Every other gate still applies; "the target is working" stops being the
    thing that ends the pass, and only for an item that asked for it."""
    harness.readiness.state = "blocked"
    harness.readiness.interject_state = "safe"
    ordinary = await harness.service.enqueue(
        target_session_id="s1",
        body="whenever",
        armed=True,
        sender_kind="agent",
        sender_id="s2",
    )
    await harness.settle()
    assert await harness.auto.tick() == []

    await harness.service.cancel(ordinary["id"])
    urgent = await harness.service.enqueue(
        target_session_id="s1",
        body="stop using the v1 endpoint",
        armed=True,
        sender_kind="agent",
        sender_id="s2",
        constraints={"delivery": "now"},
    )
    await harness.settle()
    assert await harness.auto.tick() == [urgent["id"]]


@pytest.mark.asyncio
async def test_the_receiver_switch_and_the_master_each_stop_a_mid_turn_item(
    harness: Harness,
) -> None:
    harness.readiness.state = "blocked"
    harness.readiness.interject_state = "safe"
    urgent = await harness.service.enqueue(
        target_session_id="s1",
        body="urgent",
        armed=True,
        sender_kind="agent",
        sender_id="s2",
        constraints={"delivery": "now"},
    )
    await harness.settle()
    await harness.auto.set_accept_agent_interjections("s1", False)
    assert await harness.auto.tick() == []

    await harness.auto.set_accept_agent_interjections("s1", True)
    harness.config.agent_interject_enabled = False
    await harness.settle()
    assert await harness.auto.tick() == []

    harness.config.agent_interject_enabled = True
    await harness.settle()
    assert await harness.auto.tick() == [urgent["id"]]


@pytest.mark.asyncio
async def test_a_mid_turn_item_still_answers_to_the_stability_window(
    harness: Harness,
) -> None:
    """One reading that the turn is running is a race; a held window is evidence,
    exactly as it is for the ordinary path."""
    harness.readiness.state = "blocked"
    harness.readiness.interject_state = "safe"
    urgent = await harness.service.enqueue(
        target_session_id="s1",
        body="urgent",
        armed=True,
        sender_kind="agent",
        sender_id="s2",
        constraints={"delivery": "now"},
    )
    # First tick only opens the window.
    assert await harness.auto.tick() == []
    assert not harness.writes
    await asyncio.sleep(float(harness.config.auto_delivery_stable_seconds) + 0.05)
    assert await harness.auto.tick() == [urgent["id"]]


@pytest.mark.asyncio
async def test_an_idle_conversation_lapses_and_a_new_run_defaults_on(
    harness: Harness,
) -> None:
    record = harness.manager.sessions["s1"].record
    await harness.auto.enable_session("s1")
    # Nobody has touched this conversation for longer than the window.
    idle_seconds = harness.auto.config.auto_delivery_session_ttl_minutes * 60 + 60
    record.last_activity_ts = time.time() - idle_seconds
    await harness.store.set_auto_policy("s1", enabled_at=time.time() - idle_seconds)
    await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
    await harness.settle()
    assert await harness.auto.tick() == []
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and not policy["enabled"]
    assert "lapsed" in str(policy["disabled_reason"])

    await harness.auto.enable_session("s1")
    harness.manager.sessions["s1"].record.agent_run_id = "run-replaced"
    status = await harness.auto.status()
    replaced = await harness.store.auto_policy("s1")
    assert replaced is not None and replaced["enabled"]
    assert replaced["agent_run_id"] == "run-replaced"
    assert replaced["sends_used"] == 0
    assert next(row for row in status["sessions"] if row["session_id"] == "s1")["run_matches"]


@pytest.mark.asyncio
async def test_a_conversation_in_use_keeps_its_grant_past_the_written_expiry(
    harness: Harness,
) -> None:
    """The bound is idleness, not the conversation's age.

    Measuring it from the grant's own creation is what silently disabled
    auto-delivery on every long-lived session in the fleet (observed live
    2026-08-13 at `sends_used: 0`), so an agent-authored message arrived armed
    and then waited for a human forever.
    """
    await harness.auto.enable_session("s1")
    await harness.store.set_auto_policy("s1", expires_at=time.time() - 1)
    await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
    await harness.settle()
    # Delivered rather than lapsed, and the window moved with the conversation.
    assert await harness.auto.tick() != []
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and policy["enabled"]
    assert float(policy["expires_at"]) > time.time()


@pytest.mark.asyncio
async def test_a_lapsed_grant_returns_when_the_conversation_is_used_again(
    harness: Harness,
) -> None:
    """A lapse records that time passed, not a decision, so it is recoverable.

    Every other disabled state is a decision and must stay until a human clears
    it; a grant that ran down while nobody was looking is the conversation
    default, and the default comes back when the conversation does.
    """
    record = harness.manager.sessions["s1"].record
    await harness.auto.enable_session("s1")
    idle_seconds = harness.auto.config.auto_delivery_session_ttl_minutes * 60 + 60
    record.last_activity_ts = time.time() - idle_seconds
    await harness.store.set_auto_policy("s1", enabled_at=time.time() - idle_seconds)
    await harness.auto.tick()
    lapsed = await harness.store.auto_policy("s1")
    assert lapsed is not None and not lapsed["enabled"]

    record.last_activity_ts = time.time()
    await harness.auto.status()
    restored = await harness.store.auto_policy("s1")
    assert restored is not None and restored["enabled"]
    assert restored["disabled_reason"] is None


@pytest.mark.asyncio
async def test_a_grant_the_previous_build_disabled_is_restored_too(
    harness: Harness,
) -> None:
    """The upgrade has to reach rows the old rule already wrote.

    Every conversation on the machine that needed this fix was already sitting
    at the previous build's `grant expired`, so recognising only the new reason
    would have left them disabled forever and made the fix look inert.
    """
    from swe_mux.auto_delivery import LEGACY_EXPIRED_REASON

    await harness.auto.enable_session("s1")
    await harness.auto.disable_session("s1", reason=LEGACY_EXPIRED_REASON, by="controller")
    await harness.auto.status()
    restored = await harness.store.auto_policy("s1")
    assert restored is not None and restored["enabled"]


@pytest.mark.asyncio
async def test_an_explicit_opt_out_is_never_restored_by_the_conversation_default(
    harness: Harness,
) -> None:
    await harness.auto.disable_session("s1", reason="disabled by user")
    await harness.auto.status()
    policy = await harness.store.auto_policy("s1")
    assert policy is not None and not policy["enabled"]
    assert policy["disabled_reason"] == "disabled by user"


@pytest.mark.asyncio
async def test_the_emergency_pause_is_persisted_and_stops_everything(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, live_session("s1"))
    try:
        await harness.auto.enable_session("s1")
        await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
        await harness.auto.set_paused(True)
        await harness.settle()
        assert await harness.auto.tick() == []
    finally:
        harness.close()
    # A restart re-reads the pause from SQLite: an emergency stop that a daemon
    # restart clears is not an emergency stop.
    revived = Harness(tmp_path, live_session("s1"))
    try:
        assert await revived.auto.paused() is True
        assert await revived.auto.tick() == []
    finally:
        revived.close()


@pytest.mark.asyncio
async def test_quiet_hours_pause_automatic_sends_only() -> None:
    # Window helper, independent of the loop, including the midnight wrap.
    midday = time.struct_time((2026, 7, 29, 12, 0, 0, 2, 210, -1))
    night = time.struct_time((2026, 7, 29, 23, 30, 0, 2, 210, -1))
    assert in_quiet_window("23:00", "07:00", midday) is False
    assert in_quiet_window("23:00", "07:00", night) is True
    assert in_quiet_window("", "", night) is False


@pytest.mark.asyncio
async def test_quiet_hours_block_the_controller(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        live_session("s1"),
        auto_delivery_quiet_start="00:00",
        auto_delivery_quiet_end="23:59",
    )
    try:
        await harness.auto.enable_session("s1")
        message = await harness.service.enqueue(
            target_session_id="s1", body="go", armed=True
        )
        await harness.settle()
        assert await harness.auto.tick() == []
        # A manual send is unaffected by quiet hours.
        result = await harness.service.send_next(message["id"], revision=message["revision"])
        assert result["status"] == "sent"
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_a_scheduled_message_waits_for_its_time_on_both_paths(
    harness: Harness,
) -> None:
    await harness.auto.enable_session("s1")
    message = await harness.service.enqueue(
        target_session_id="s1",
        body="later",
        armed=True,
        constraints={"delay_seconds": 3600},
    )
    await harness.settle()
    assert await harness.auto.tick() == []
    # Manual send refuses without confirmation, and leaves the item armed.
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(message["id"], revision=message["revision"])
    assert caught.value.code == "delivery_not_due"
    still = await harness.store.message(message["id"])
    assert still is not None and still["state"] == "armed"
    # "Send now" is an explicit human override of the clock.
    result = await harness.service.send_next(
        message["id"], revision=message["revision"], confirm=True
    )
    assert result["status"] == "sent"


@pytest.mark.asyncio
async def test_an_expired_message_is_cancelled_not_delivered(harness: Harness) -> None:
    message = await harness.service.enqueue(
        target_session_id="s1",
        body="stale",
        armed=True,
        constraints={"expires_at": time.time() + 0.05},
    )
    await asyncio.sleep(0.06)
    await harness.auto.tick()  # the sweep runs on the first tick
    stored = await harness.store.message(message["id"])
    assert stored is not None
    assert stored["state"] == "cancelled" and stored["cancel_kind"] == "expired"
    with pytest.raises(QueueError) as caught:
        await harness.service.send_next(message["id"], revision=message["revision"])
    assert caught.value.code == "invalid_state"


@pytest.mark.asyncio
async def test_a_failed_delivery_disables_the_opt_in(tmp_path: Path) -> None:
    session = live_session("s1")
    harness = Harness(tmp_path, session)

    def explode(target: Any, data: str) -> None:
        raise OSError("the pipe is gone")

    harness.service._write = explode  # type: ignore[assignment]
    try:
        await harness.auto.enable_session("s1")
        await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
        await harness.settle()
        assert await harness.auto.tick() == []
        policy = await harness.store.auto_policy("s1")
        assert policy is not None and not policy["enabled"]
        assert "verify the terminal" in str(policy["disabled_reason"])
        # An ambiguous write is the one disabled state that intentionally
        # survives a conversation replacement until a human verifies it.
        harness.manager.sessions["s1"].record.agent_run_id = "run-replaced"
        await harness.auto.status()
        policy = await harness.store.auto_policy("s1")
        assert policy is not None and not policy["enabled"]
        assert policy["agent_run_id"] == "run-s1"
        counters = await harness.store.counters()
        assert counters.get("auto_failed") == 1
    finally:
        harness.close()


@pytest.mark.asyncio
async def test_delivery_audit_records_who_pressed_send(harness: Harness) -> None:
    await harness.auto.enable_session("s1")
    message = await harness.service.enqueue(target_session_id="s1", body="go", armed=True)
    await harness.settle()
    await harness.auto.tick()
    deliveries = await harness.store.deliveries(message["id"])
    assert [row["initiator"] for row in deliveries] == ["auto"]
    manual = await harness.service.enqueue(target_session_id="s1", body="second", armed=True)
    await harness.service.send_next(manual["id"], revision=manual["revision"])
    assert (await harness.store.deliveries(manual["id"]))[0]["initiator"] == "user"


@pytest.mark.asyncio
async def test_an_unsafe_report_resets_the_proving_period_and_pauses(
    harness: Harness,
) -> None:
    await harness.auto.enable_session("s1")
    counters = await harness.store.counters()
    assert counters.get("proving_since")
    await harness.auto.report_unsafe("typed into an approval prompt")
    assert await harness.auto.paused() is True
    status = await harness.auto.status()
    assert status["promotion"]["criteria"]["no_false_safe"] is False
    assert status["promotion"]["met"] is False


@pytest.mark.asyncio
async def test_the_tick_reads_only_live_sessions_not_the_whole_policy_history(
    tmp_path: Path,
) -> None:
    """Policy rows are never deleted, so the table holds one row per session ever
    granted. The controller polls every second; before the live-session filter it
    scanned all of them — 106 rows for 12 live sessions on a real install."""
    harness = Harness(tmp_path, live_session("s1"))
    try:
        # A long history of dead sessions, each with a persisted policy row.
        for ghost in range(40):
            await harness.store.set_auto_policy(
                f"dead-{ghost}", enabled=True, agent_run_id=f"run-dead-{ghost}"
            )
        rows = await harness.auto._policies_with_conversation_defaults()
        assert {row["session_id"] for row in rows} == {"s1"}
        # The store-level filter is what makes that cheap, and an empty filter
        # must mean "no sessions", never "no filter".
        assert await harness.store.auto_policies([]) == []
        both = await harness.store.auto_policies(["s1", "dead-3"])
        assert {row["session_id"] for row in both} == {"s1", "dead-3"}
    finally:
        harness.close()


def test_promotion_criteria_are_quantitative() -> None:
    fresh = promotion_status({})
    assert fresh["met"] is False
    proven = promotion_status(
        {
            COUNTER_SENT: 60.0,
            COUNTER_UNSAFE: 0.0,
            "proving_since": time.time() - 20 * 86400,
        }
    )
    assert proven["criteria"] == {
        "no_false_safe": True,
        "min_sends": True,
        "min_days": True,
    }
    assert proven["met"] is True
    tainted = promotion_status(
        {
            COUNTER_SENT: 60.0,
            COUNTER_UNSAFE: 1.0,
            "proving_since": time.time() - 20 * 86400,
        }
    )
    assert tainted["met"] is False
