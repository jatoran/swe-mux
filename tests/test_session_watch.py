"""Session-settle watches: the fire rules, the bounds, and the failsafe.

What these pin is the contract, not the wording. A watch reads a target and
produces exactly one deterministic `rule`-sender notice into the *watcher's own*
queue; it fires on an ended target unconditionally, on a working -> settled edge
that holds, or on the timeout - and the timeout is the one that must never be
skippable, because it is what makes a hung worker impossible to confuse with
silence.

The clock is injected everywhere, so the 120-second settle hold and multi-minute
timeouts are exercised in microseconds and no test sleeps.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import push, session
from swe_mux.prompt_queue import (
    HUMAN_SENDER_KINDS,
    SELF_ARMING_SENDER_KINDS,
    QueueError,
)
from swe_mux.session_watch import (
    ARMING_REASON_DISABLED,
    ARMING_REASON_OK,
    ARMING_REASON_ROLLED,
    ARMING_REASON_UNKNOWN_RUN,
    AWAIT_MAX_PER_SESSION,
    RUNNING_ACTIVITY_KINDS,
    SETTLE_HOLD_SECONDS,
    SessionWatchService,
    WatchRefusal,
    describe_state,
    running_work,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeConfig:
    session_watch_enabled = True
    session_watch_max_per_session = 8
    session_watch_max_minutes = 240


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeQueue:
    """Stands in for the Phase 5 prompt queue, recording what it was handed.

    It applies the queue's own arming floor rather than echoing the request,
    because the service reads the arming back off the returned row: a stub that
    always said `armed` would pin the ask instead of the outcome, and the whole
    point of the read-back is that the queue gets the last word.
    """

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        #: Set to model a watcher that vanished between the sweep and the enqueue,
        #: which `server._session_watch_notice` answers with an empty row.
        self.target_gone = False

    async def __call__(self, **kwargs: Any) -> dict[str, Any]:
        if self.fail_with is not None:
            raise self.fail_with
        self.messages.append(kwargs)
        if self.target_gone:
            return {}
        armed = bool(kwargs.get("armed")) and (
            str(kwargs.get("sender_kind") or "") in HUMAN_SENDER_KINDS
            or str(kwargs.get("sender_kind") or "") in SELF_ARMING_SENDER_KINDS
            or bool(kwargs.get("solicited_by"))
        )
        return {
            "id": f"msg_{len(self.messages)}",
            "state": "armed" if armed else "draft",
        }


class FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


def make_session(
    sid: str,
    *,
    backend: str = "claude",
    state: str = "working",
    project_id: str = "p1",
    scope_id: str = "scope-1",
    run_id: str | None = None,
    idle_reason: str | None = None,
    awaiting_reason: str | None = None,
    standing: tuple[str, ...] = (),
    name: str | None = None,
) -> Any:
    record = SimpleNamespace(
        id=sid,
        name=name or f"{backend}-{sid}",
        backend=backend,
        state=state,
        project_id=project_id,
        project_scope_id=scope_id,
        agent_run_id=run_id if run_id is not None else f"run-{sid}",
        idle_reason=idle_reason,
        awaiting_reason=awaiting_reason,
        standing_activity=[SimpleNamespace(kind=kind) for kind in standing],
    )
    return SimpleNamespace(record=record)


class FakeSessions:
    def __init__(self, *entries: Any) -> None:
        self.sessions = {entry.record.id: entry for entry in entries}


class FakeProjects:
    def __init__(self) -> None:
        self.projects: dict[str, Any] = {
            "p1": SimpleNamespace(id="p1", name="Work", root="D:/work"),
            "p2": SimpleNamespace(id="p2", name="Other", root="D:/other"),
        }

    def ordered_projects(self) -> list[Any]:
        return list(self.projects.values())


def build(
    *entries: Any,
    config: Any = None,
    clock: FakeClock | None = None,
) -> tuple[SessionWatchService, FakeQueue, FakeClock, FakeEvents]:
    queue = FakeQueue()
    events = FakeEvents()
    tick = clock or FakeClock()
    service = SessionWatchService(
        sessions=FakeSessions(*entries),
        projects=FakeProjects(),
        config=config or FakeConfig(),
        events=events,
        queue_message=queue,
        clock=tick,
    )
    return service, queue, tick, events


# -- the fire rules ----------------------------------------------------------


async def test_settle_needs_a_working_edge_and_the_hold() -> None:
    """The central rule: leaving working, and staying left."""
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)

    result = await service.watch(watcher, target="t", timeout_minutes=60)
    assert result["status"] == "watching"
    assert result["timeout_minutes"] == 60

    # Still working: nothing resolves.
    clock.advance(30)
    assert await service.tick() == []

    # It settles, but the hold has not elapsed yet.
    target.record.state = "idle"
    assert await service.tick() == []
    clock.advance(SETTLE_HOLD_SECONDS - 1)
    assert await service.tick() == []
    assert queue.messages == []

    clock.advance(2)
    resolved = await service.tick()
    assert [item["case"] for item in resolved] == ["settled"]
    assert len(queue.messages) == 1
    body = queue.messages[0]["body"]
    assert "left working" in body
    assert "State now: idle" in body
    # One-shot: the watch is gone and a second sweep produces nothing.
    assert await service.tick() == []
    assert len(queue.messages) == 1


async def test_a_flap_back_to_working_restarts_the_hold() -> None:
    """89 of 211 measured idle transitions flapped back inside 120 s.

    Firing on the first idle edge would tell an orchestrator its worker had
    finished and be wrong about two times in five, which is the entire reason the
    hold exists.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    target.record.state = "idle"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS - 10)
    target.record.state = "working"
    await service.tick()
    target.record.state = "idle"
    await service.tick()
    # The hold restarted: the old elapsed time buys nothing.
    clock.advance(SETTLE_HOLD_SECONDS - 10)
    assert await service.tick() == []
    assert queue.messages == []
    clock.advance(20)
    assert [item["case"] for item in await service.tick()] == ["settled"]


async def test_awaiting_settles_and_the_notice_names_the_sub_reason() -> None:
    """`awaiting` is settled, and is exactly the case that must not read as done."""
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    target.record.state = "awaiting"
    target.record.awaiting_reason = "approval"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS + 1)
    assert [item["case"] for item in await service.tick()] == ["settled"]
    body = queue.messages[0]["body"]
    assert "awaiting · approval" in body
    assert "blocked on a person" in body


async def test_idle_with_running_background_work_is_not_settled() -> None:
    """The turn ended; the agent did not.

    An idle session with live subagents resumes itself, so reporting it as
    settled is the false "done" the whole feature would otherwise ship. The
    timeout is what keeps the suppression from becoming silence.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t", timeout_minutes=10)

    target.record.state = "idle"
    target.record.standing_activity = [SimpleNamespace(kind="subagents")]
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS * 2)
    assert await service.tick() == []

    # `idle_reason` is the second source, and suppresses on its own.
    target.record.standing_activity = []
    target.record.idle_reason = "waiting_on_background"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS * 2)
    assert await service.tick() == []
    assert queue.messages == []

    # It really finishes: the annotation clears and the hold runs.
    target.record.idle_reason = None
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS + 1)
    assert [item["case"] for item in await service.tick()] == ["settled"]


async def test_ended_fires_immediately_without_a_working_edge() -> None:
    """A session that exited will never work again; a hold would prove nothing."""
    watcher = make_session("w")
    target = make_session("t", state="starting")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    target.record.state = "exited"
    assert [item["case"] for item in await service.tick()] == ["ended"]
    assert "the session ended" in queue.messages[0]["body"]


async def test_a_target_gone_from_the_registry_reads_as_ended() -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    service._sessions.sessions.pop("t")
    assert [item["case"] for item in await service.tick()] == ["ended"]
    assert "ended (session gone)" in queue.messages[0]["body"]


async def test_timeout_fires_first_when_nothing_settles() -> None:
    """The failsafe the operator asked for by name.

    A worker that hangs in `working` forever must still produce exactly one
    message, and that message must say the timeout is what fired rather than
    dressing an expiry up as a result.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, events = build(watcher, target)
    await service.watch(watcher, target="t", timeout_minutes=5)

    clock.advance(4 * 60)
    assert await service.tick() == []
    clock.advance(2 * 60)
    resolved = await service.tick()
    assert [item["case"] for item in resolved] == ["timeout"]
    body = queue.messages[0]["body"]
    assert "timed out" in body
    assert "5-minute timeout" in body
    assert "State now: working" in body
    assert "unresolved rather than done" in body
    assert ("session_watch_resolved", ) in [(name,) for name, _ in events.emitted]


async def test_timeout_still_fires_while_a_settle_hold_is_running() -> None:
    """An expiry mid-hold reports the timeout and the state it actually saw.

    The alternative - extending the watch until the hold completes - would make
    the timeout a suggestion, and the operator asked for a bound.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t", timeout_minutes=2)

    clock.advance(90)
    target.record.state = "idle"
    await service.tick()
    clock.advance(40)
    assert [item["case"] for item in await service.tick()] == ["timeout"]
    assert "State now: idle" in queue.messages[0]["body"]


async def test_a_target_settled_at_arming_is_answered_by_the_timeout() -> None:
    """A settle is an edge, and the result says so before the caller waits."""
    watcher = make_session("w")
    target = make_session("t", state="idle")
    service, queue, clock, _events = build(watcher, target)

    result = await service.watch(watcher, target="t", timeout_minutes=3)
    assert result["already_settled"] is True
    assert "timeout is what will answer you" in result["note"]

    clock.advance(SETTLE_HOLD_SECONDS * 3)
    assert [item["case"] for item in await service.tick()] == ["timeout"]


async def test_starting_is_not_working() -> None:
    """Startup `idle` is inferred from PTY quiet and is not even input-ready.

    Counting `starting` as working would fire "your worker finished" before the
    seed prompt had run a single turn.
    """
    watcher = make_session("w")
    target = make_session("t", state="starting")
    service, queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t", timeout_minutes=30)

    target.record.state = "idle"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS * 2)
    assert await service.tick() == []
    assert queue.messages == []


# -- the notice --------------------------------------------------------------


async def test_the_notice_is_a_rule_sender_addressed_to_the_watcher() -> None:
    """Deterministic template, self-addressed, correlation-keyed on the watch."""
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    armed = await service.watch(watcher, target="t")

    target.record.state = "exited"
    await service.tick()
    message = queue.messages[0]
    assert message["target_session_id"] == "w"
    assert message["sender_kind"] == "rule"
    assert message["sender_id"] == "session_watch"
    assert message["correlation_id"] == armed["watch_id"]
    # No third session is ever addressed and nothing beyond the arming pair is
    # passed: the notice carries no constraints, no thread, and no payload.
    assert set(message) == {
        "target_session_id",
        "body",
        "armed",
        "solicited_by",
        "sender_kind",
        "sender_id",
        "sender_label",
        "correlation_id",
    }


async def test_the_result_says_the_notice_will_be_staged_armed() -> None:
    """`queued` alone is unactionable, so the delivery consequence is stated.

    The notice is the bounded answer to a request this session made, so it is
    staged armed - and the result says that, along with the fact that armed is
    not delivered, rather than letting a caller infer either half.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(watcher, target)
    result = await service.watch(watcher, target="t")
    assert result["notice_delivery"]["auto_delivery"] is True
    waits_for = result["notice_delivery"]["waits_for"]
    assert "staged armed" in waits_for
    assert "Armed is not delivered" in waits_for


# -- arming ------------------------------------------------------------------


async def test_a_matured_notice_arrives_armed_and_names_the_watch() -> None:
    """The watch is the consent, and `solicited_by` is what records it.

    A `rule` sender is not self-arming in general; this one arms by naming the
    request the *target itself* made, which is the narrowing the land handback
    established (`land-queue.md`). Without it the notice reached the session
    that asked for it as an inert draft nobody delivered.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, events = build(watcher, target)
    armed = await service.watch(watcher, target="t")

    target.record.state = "exited"
    [outcome] = await service.tick()
    message = queue.messages[0]
    assert message["armed"] is True
    assert message["solicited_by"] == armed["watch_id"]
    assert outcome["armed"] is True
    assert outcome["arming_reason"] == ARMING_REASON_OK
    assert service.status()["armed_notices"] == 1
    resolved = next(
        payload for kind, payload in events.emitted if kind == "session_watch_resolved"
    )
    assert resolved["armed"] is True
    assert resolved["arming_reason"] == ARMING_REASON_OK


async def test_the_settle_notice_arms_the_same_way_the_ended_one_does() -> None:
    """Arming is a property of the watch, not of which rule matured it."""
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    await service.tick()
    target.record.state = "idle"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS + 1)
    [outcome] = await service.tick()
    assert outcome["case"] == "settled"
    assert outcome["armed"] is True
    assert queue.messages[0]["armed"] is True


async def test_a_rolled_over_run_gets_a_draft_rather_than_an_armed_notice() -> None:
    """The consent belongs to the conversation that gave it, and to no successor.

    The sweep drops a rolled watch outright, so the case that reaches a notice at
    all is the `stop()` flush - which does not go through the sweep. The run
    binding is therefore re-checked at write time, and the notice is staged as
    the draft it always used to be rather than withheld.
    """
    watcher = make_session("w", run_id="run-a")
    target = make_session("t", state="working")
    service, queue, _clock, events = build(watcher, target)
    await service.watch(watcher, target="t")

    watcher.record.agent_run_id = "run-b"
    await service.stop()
    assert len(queue.messages) == 1
    assert queue.messages[0]["armed"] is False
    assert queue.messages[0]["solicited_by"] is None
    assert service.status()["armed_notices"] == 0
    resolved = next(
        payload for kind, payload in events.emitted if kind == "session_watch_resolved"
    )
    # Named apart from "could not be identified": one is a successor that never
    # asked, the other is a run nothing could read, and they are different bugs.
    assert resolved["arming_reason"] == ARMING_REASON_ROLLED


async def test_a_watcher_with_no_identifiable_run_gets_a_draft() -> None:
    """A check that could not be made is not a check that passed.

    The sweep's drop guard needs a run on both sides, so a watch armed before its
    watcher had one is never dropped for rolling - and would otherwise arm on a
    consent nothing can attribute.
    """
    watcher = make_session("w", run_id="")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    target.record.state = "exited"
    [outcome] = await service.tick()
    assert outcome["armed"] is False
    assert outcome["arming_reason"] == ARMING_REASON_UNKNOWN_RUN
    assert queue.messages[0]["armed"] is False


async def test_turning_watches_off_mid_flight_stops_the_unattended_half() -> None:
    """The switch is read when the notice is written, not when the watch was armed.

    An operator who turns watches off is turning off the thing they can see; a
    watch already open must not keep the authority it was granted under. The
    notice is still enqueued - refusing arming never refuses the message.
    """

    class Switch(FakeConfig):
        session_watch_enabled = True

    config = Switch()
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target, config=config)
    await service.watch(watcher, target="t")

    config.session_watch_enabled = False
    target.record.state = "exited"
    [outcome] = await service.tick()
    assert outcome["armed"] is False
    assert outcome["arming_reason"] == ARMING_REASON_DISABLED
    assert len(queue.messages) == 1
    assert queue.messages[0]["armed"] is False


async def test_the_arming_reported_is_the_one_the_queue_applied() -> None:
    """Read back off the row, never echoed from the ask.

    A retry dedupes into a message staged under different conditions, and the
    queue applies its own floor on top of this one; reporting the request would
    record an arming that never happened.
    """
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")
    queue.target_gone = True

    target.record.state = "exited"
    [outcome] = await service.tick()
    assert queue.messages[0]["armed"] is True
    assert outcome["armed"] is False
    assert "gone before the notice was staged" in outcome["arming_reason"]
    assert service.status()["armed_notices"] == 0


async def test_a_failed_notice_records_that_nothing_was_armed() -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")
    queue.fail_with = QueueError("boom", "the queue said no")

    target.record.state = "exited"
    [outcome] = await service.tick()
    assert outcome["armed"] is False
    assert outcome["arming_reason"] == "the notice could not be enqueued"


async def test_the_daemon_stop_flush_arms_on_the_run_that_asked() -> None:
    """The flush is the case a restart produces, and it is still a solicited reply."""
    watcher = make_session("w", run_id="run-a")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    watch = await service.watch(watcher, target="t")

    await service.stop()
    assert queue.messages[0]["armed"] is True
    assert queue.messages[0]["solicited_by"] == watch["watch_id"]


async def test_a_failed_notice_does_not_wedge_the_sweep() -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")
    queue.fail_with = QueueError("boom", "the queue said no")

    target.record.state = "crashed"
    assert [item["case"] for item in await service.tick()] == ["ended"]
    assert service.status()["notice_failures"] == 1
    assert service.status()["open"] == 0


# -- lifetime ----------------------------------------------------------------


async def test_a_watch_dies_with_its_watcher_and_says_nothing() -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    watcher.record.state = "exited"
    assert await service.tick() == []
    assert queue.messages == []
    assert service.status()["dropped"] == {"watcher_ended": 1}


async def test_a_watch_dies_when_the_watchers_conversation_rolls_over() -> None:
    """After an in-CLI `/clear` the successor never armed this watch.

    A notice delivered there would read as a recollection the new conversation
    does not have, which is the same rule every run-attributed surface follows.
    """
    watcher = make_session("w", run_id="run-a")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    watcher.record.agent_run_id = "run-b"
    assert await service.tick() == []
    assert queue.messages == []
    assert service.status()["dropped"] == {"watcher_rolled": 1}


async def test_stopping_the_daemon_flushes_open_watches_as_notices() -> None:
    """A restart is routine here, so a dropped watch must not be a silent one."""
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, _clock, _events = build(watcher, target)
    await service.watch(watcher, target="t")

    await service.stop()
    assert len(queue.messages) == 1
    body = queue.messages[0]["body"]
    assert "the daemon stopped" in body
    assert "do not survive a restart" in body
    assert service.status()["open"] == 0


# -- bounds ------------------------------------------------------------------


async def test_re_arming_the_same_target_returns_the_existing_watch() -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)
    first = await service.watch(watcher, target="t", timeout_minutes=60)
    clock.advance(30)
    second = await service.watch(watcher, target="t", timeout_minutes=5)

    assert second["status"] == "already_watching"
    assert second["watch_id"] == first["watch_id"]
    # The original timeout stands; a re-arm neither extends nor shortens it.
    assert second["timeout_minutes"] == 60
    assert service.status()["armed"] == 1

    target.record.state = "exited"
    await service.tick()
    assert len(queue.messages) == 1


async def test_the_per_watcher_ceiling_refuses_rather_than_dropping() -> None:
    class Narrow(FakeConfig):
        session_watch_max_per_session = 2

    watcher = make_session("w")
    targets = [make_session(f"t{index}", state="working") for index in range(3)]
    service, _queue, _clock, _events = build(watcher, *targets, config=Narrow())
    await service.watch(watcher, target="t0")
    await service.watch(watcher, target="t1")
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="t2")
    assert excinfo.value.code == "watch_limit_reached"
    assert excinfo.value.status == 429


@pytest.mark.parametrize("minutes", [0, -5, 241, "soon"])
async def test_the_timeout_is_bounded(minutes: Any) -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(watcher, target)
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="t", timeout_minutes=minutes)
    assert excinfo.value.code == "invalid_timeout"


async def test_an_omitted_timeout_takes_the_default() -> None:
    watcher = make_session("w")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(watcher, target)
    result = await service.watch(watcher, target="t")
    assert result["timeout_minutes"] == 30


async def test_self_watch_is_refused() -> None:
    watcher = make_session("w", state="working")
    service, _queue, _clock, _events = build(watcher)
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="w")
    assert excinfo.value.code == "self_watch"


async def test_a_shell_is_never_a_watch_target_or_a_watcher() -> None:
    watcher = make_session("w")
    shell = make_session("sh", backend="shell", state="running")
    service, _queue, _clock, _events = build(watcher, shell)
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="sh")
    assert excinfo.value.code == "not_agent_target"

    shell_watcher = make_session("sh2", backend="shell", state="running")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(shell_watcher, target)
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(shell_watcher, target="t")
    assert excinfo.value.code == "not_agent_watcher"


async def test_an_already_ended_target_is_refused_with_its_final_state() -> None:
    """Answered now rather than as a notice half a second later."""
    watcher = make_session("w")
    target = make_session("t", state="exited")
    service, _queue, _clock, _events = build(watcher, target)
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="t")
    assert excinfo.value.code == "target_ended"
    assert excinfo.value.payload["target_state"] == "exited"


async def test_the_kill_switch_refuses_everywhere() -> None:
    class Off(FakeConfig):
        session_watch_enabled = False

    watcher = make_session("w")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(watcher, target, config=Off())
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="t")
    assert excinfo.value.code == "session_watch_disabled"


# -- scope -------------------------------------------------------------------


async def test_another_projects_session_needs_the_widening() -> None:
    """A scope miss and a true miss answer identically, and name the argument."""
    watcher = make_session("w", project_id="p1", scope_id="scope-1")
    other = make_session(
        "t", project_id="p2", scope_id="scope-2", state="working", name="worker"
    )
    service, _queue, _clock, _events = build(watcher, other)

    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="worker")
    assert excinfo.value.code == "unknown_target"
    assert 'project:"fleet"' in str(excinfo.value)

    widened = await service.watch(watcher, target="worker", project="fleet")
    assert widened["target_session_id"] == "t"
    assert widened["project_scope"] == "fleet"


async def test_one_name_matching_twice_names_the_candidates() -> None:
    watcher = make_session("w", project_id="p1", scope_id="scope-1")
    first = make_session(
        "t1", project_id="p1", scope_id="scope-1", state="working", name="backend"
    )
    second = make_session(
        "t2", project_id="p2", scope_id="scope-2", state="working", name="backend"
    )
    service, _queue, _clock, _events = build(watcher, first, second)
    with pytest.raises(WatchRefusal) as excinfo:
        await service.watch(watcher, target="backend", project="fleet")
    assert excinfo.value.code == "ambiguous_target"
    assert excinfo.value.payload["candidates"] == ["t1", "t2"]


# -- shared vocabulary -------------------------------------------------------


def test_running_activity_kinds_match_the_session_definition() -> None:
    """`session.py` owns the split; this module and `push.py` copy it.

    A copy that drifts would make an idle session with live subagents read as
    finished on one surface and unfinished on another.
    """
    assert RUNNING_ACTIVITY_KINDS == session.RUNNING_ACTIVITY_KINDS
    assert RUNNING_ACTIVITY_KINDS == push.RUNNING_ACTIVITY_KINDS


def test_the_settle_hold_matches_the_measured_notification_hold() -> None:
    """Same phenomenon, same measurement, so the two must not drift apart."""
    assert SETTLE_HOLD_SECONDS == push.WAITING_SETTLE_SECONDS


def test_describe_state_disambiguates_the_three_meanings_of_idle() -> None:
    finished = make_session("a", state="idle").record
    handed_off = make_session(
        "b", state="idle", idle_reason="waiting_on_background", standing=("subagents",)
    ).record
    blocked = make_session("c", state="awaiting", awaiting_reason="question").record
    assert describe_state(finished) == "idle"
    assert describe_state(blocked) == "awaiting · question"
    assert "waiting_on_background" in describe_state(handed_off)
    assert "background work running: subagents" in describe_state(handed_off)
    assert running_work(handed_off) and not running_work(finished)


# -- synchronous waits (`await_session`) --------------------------------------
#
# Same fire rules as a watch, different sink: the answer fulfils the blocked
# call, so nothing below touches the queue at all - `queue.messages` staying
# empty is part of what these tests pin.


async def _drive(
    service: SessionWatchService, clock: FakeClock, coro: Any, steps: list[float]
) -> dict[str, Any]:
    """Start the wait, then advance the clock and sweep until it resolves."""
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0)  # let the awaiter register before the first sweep
    for seconds in steps:
        clock.advance(seconds)
        await service.tick()
        await asyncio.sleep(0)
        if task.done():
            break
    return await asyncio.wait_for(task, timeout=1)


async def test_await_resolves_on_the_same_edge_and_hold_a_watch_uses() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)

    task = asyncio.ensure_future(
        service.await_settle(watcher, target="t", timeout_seconds=600)
    )
    await asyncio.sleep(0)
    target.record.state = "idle"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS + 1)
    await service.tick()
    result = await asyncio.wait_for(task, timeout=1)

    assert result["case"] == "settled"
    assert result["target_state"].startswith("idle")
    assert result["target_session_id"] == "t"
    # The answer travelled in-band: nothing was staged, armed, or delivered.
    assert queue.messages == []


async def test_await_timeout_is_a_result_carrying_the_current_state() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, queue, clock, _events = build(watcher, target)

    result = await _drive(
        service,
        clock,
        service.await_settle(watcher, target="t", timeout_seconds=30),
        [31.0],
    )

    assert result["case"] == "timeout"
    assert result["target_state"].startswith("working")
    assert "normal" in result["note"]
    assert queue.messages == []


async def test_await_answers_an_already_ended_target_immediately() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="exited")
    service, _queue, _clock, _events = build(watcher, target)

    result = await service.await_settle(watcher, target="t")

    assert result["case"] == "ended"
    assert result["waited_seconds"] == 0


async def test_await_answers_a_target_that_already_held_a_settle_immediately() -> None:
    """`state_since` rescues the between-polls edge: a worker that finished
    while nobody was waiting answers now rather than only at the timeout."""
    watcher = make_session("w", state="working")
    target = make_session("t", state="idle")
    service, _queue, clock, _events = build(watcher, target)
    target.record.state_since = clock.now - SETTLE_HOLD_SECONDS - 5

    result = await service.await_settle(watcher, target="t")

    assert result["case"] == "already_settled"
    assert result["waited_seconds"] == 0


async def test_await_ignores_a_recent_settle_that_has_not_held() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="idle")
    service, _queue, clock, _events = build(watcher, target)
    target.record.state_since = clock.now - 5  # settled five seconds ago: could flap

    result = await _drive(
        service,
        clock,
        service.await_settle(watcher, target="t", timeout_seconds=30),
        [31.0],
    )

    assert result["case"] == "timeout"


async def test_await_until_ended_ignores_a_settle() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, _queue, clock, _events = build(watcher, target)

    task = asyncio.ensure_future(
        service.await_settle(watcher, target="t", until=["ended"], timeout_seconds=600)
    )
    await asyncio.sleep(0)
    target.record.state = "idle"
    await service.tick()
    clock.advance(SETTLE_HOLD_SECONDS + 10)
    await service.tick()
    await asyncio.sleep(0)
    assert not task.done()
    target.record.state = "crashed"
    await service.tick()
    result = await asyncio.wait_for(task, timeout=1)
    assert result["case"] == "ended"


async def test_await_refusals_are_typed() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(watcher, target)

    with pytest.raises(WatchRefusal) as bad_until:
        await service.await_settle(watcher, target="t", until=["blocked"])
    assert bad_until.value.code == "invalid_until"

    with pytest.raises(WatchRefusal) as bad_timeout:
        await service.await_settle(watcher, target="t", timeout_seconds=100000)
    assert bad_timeout.value.code == "invalid_timeout"
    assert "watch_session" in str(bad_timeout.value)

    with pytest.raises(WatchRefusal) as self_wait:
        await service.await_settle(watcher, target="w")
    assert self_wait.value.code == "self_watch"


async def test_awaits_are_bounded_per_caller() -> None:
    watcher = make_session("w", state="working")
    targets = [
        make_session(f"t{n}", state="working")
        for n in range(AWAIT_MAX_PER_SESSION + 1)
    ]
    service, _queue, clock, _events = build(watcher, *targets)

    tasks = [
        asyncio.ensure_future(
            service.await_settle(watcher, target=f"t{n}", timeout_seconds=30)
        )
        for n in range(AWAIT_MAX_PER_SESSION)
    ]
    await asyncio.sleep(0)
    with pytest.raises(WatchRefusal) as caught:
        await service.await_settle(
            watcher, target=f"t{AWAIT_MAX_PER_SESSION}", timeout_seconds=30
        )
    assert caught.value.code == "await_limit_reached"
    clock.advance(31)
    await service.tick()
    for task in tasks:
        assert (await asyncio.wait_for(task, timeout=1))["case"] == "timeout"


async def test_daemon_stop_resolves_open_awaits_instead_of_abandoning_them() -> None:
    """An abandoned future raises from a finalizer at some later test's expense
    (`filterwarnings = error`), so the stop flush answers every open wait."""
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, _queue, _clock, _events = build(watcher, target)

    task = asyncio.ensure_future(
        service.await_settle(watcher, target="t", timeout_seconds=600)
    )
    await asyncio.sleep(0)
    await service.stop()
    result = await asyncio.wait_for(task, timeout=1)
    assert result["case"] == "daemon_stopped"


# -- open watches as reply-window evidence ------------------------------------


async def test_origin_windows_reports_open_watches_bounded_by_their_deadline() -> None:
    watcher = make_session("w", state="working")
    target = make_session("t", state="working")
    service, _queue, clock, _events = build(watcher, target)
    await service.watch(watcher, target="t", timeout_minutes=60)

    windows = await service.origin_windows(["w", "unrelated"], since=0.0)

    assert set(windows) == {"w"}
    entry = windows["w"]
    assert entry["kind"] == "watch"
    assert entry["target_session_id"] == "t"
    # Bounded by the watch's own deadline plus the delivery grace, never open-ended.
    assert entry["expires_at"] == pytest.approx(
        clock.now + 60 * 60 + SessionWatchService.WINDOW_GRACE_SECONDS
    )

    # A resolved watch stops holding anything.
    target.record.state = "crashed"
    await service.tick()
    assert await service.origin_windows(["w"], since=0.0) == {}
