"""The readiness watcher: what it announces, what it stays quiet about, and why.

Every property here is a cost or a safety property rather than a feature, because
the loop's whole design question was whether a per-second evaluation of live
sessions could be afforded and trusted:

- edge-triggered, so an unchanged fleet emits nothing;
- silent with no browser attached, so a headless daemon pays nothing;
- transient, so a per-second event type cannot evict the capped `events` history;
- read-only against the tracker, so watching cannot change what is watched;
- and scoped to sessions a surface can actually be reading.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent
from swe_mux.readiness_watch import BROWSER_SUBSCRIBER, EVENT_TYPE, ReadinessWatcher
from tests.support.detection_replay import ReplaySession, VirtualClock


def _event(event_type: str, **payload: Any) -> MuxEvent:
    return MuxEvent(
        ts=time.time(),
        session_id="replay-session",
        source="transcript",
        type=event_type,
        payload=payload,
    )


class _Store:
    """The one queue read the watcher makes: which targets have pending items."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls = 0

    async def summary(self) -> list[dict[str, Any]]:
        self.calls += 1
        return self.rows


def _watched_agent(
    session_id: str = "replay-session", *, attached: bool = True
) -> tuple[ReplaySession, DeliveryReadinessTracker, VirtualClock]:
    """An idle agent whose last root turn completed, with a pane attached."""

    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    session.record.id = session_id
    session.record.parser_status = "ready"
    session.screen.feed(b"\x1b[?1049h")
    session.subscribers = {"pane"} if attached else set()
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed"), session)
    clock.advance(5.0)
    return session, tracker, clock


def _watcher(
    session: ReplaySession,
    tracker: DeliveryReadinessTracker,
    bus: EventBus,
    store: _Store | None = None,
) -> ReadinessWatcher:
    sessions = SimpleNamespace(sessions={session.record.id: session})
    queue = SimpleNamespace(store=store or _Store())
    return ReadinessWatcher(sessions, tracker, queue, bus)


def _collect(queue: Any) -> list[MuxEvent]:
    drained: list[MuxEvent] = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    return drained


async def test_nothing_is_announced_while_no_browser_is_attached() -> None:
    """The only consumer is a browser, so with none the tick is free.

    Not merely quiet — the whole pass is skipped, including the queue read and the
    per-session screen classification that is the loop's real cost.
    """

    session, tracker, _clock = _watched_agent()
    bus = EventBus()
    store = _Store()
    watcher = _watcher(session, tracker, bus, store)
    daemon_consumer = bus.subscribe(name="telemetry")

    assert await watcher.tick() == []
    assert store.calls == 0
    assert _collect(daemon_consumer) == []


async def test_the_first_sighting_establishes_a_baseline_without_announcing() -> None:
    """A client's REST load already carries this verdict.

    Announcing it again would make every newly watched session emit a spurious
    change, which on a fleet is a burst of frames saying nothing at exactly the
    moment the client is busiest.
    """

    session, tracker, _clock = _watched_agent()
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    browser = bus.subscribe(name=BROWSER_SUBSCRIBER)

    assert await watcher.tick() == []
    assert _collect(browser) == []


async def test_an_unchanged_fleet_announces_nothing() -> None:
    """Edge-triggered is what makes a one-second loop affordable."""

    session, tracker, clock = _watched_agent()
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    browser = bus.subscribe(name=BROWSER_SUBSCRIBER)

    await watcher.tick()
    for _ in range(5):
        clock.advance(1.0)
        assert await watcher.tick() == []
    assert _collect(browser) == []


async def test_a_changed_verdict_is_announced_once_with_the_whole_reading() -> None:
    """Once, not once per tick, and carrying enough to render without a refetch."""

    session, tracker, clock = _watched_agent()
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    browser = bus.subscribe(name=BROWSER_SUBSCRIBER)
    await watcher.tick()

    session.input_revision += 1
    clock.advance(5.0)
    assert await watcher.tick() == [session.record.id]
    clock.advance(1.0)
    assert await watcher.tick() == []

    (announced,) = _collect(browser)
    assert announced.type == EVENT_TYPE
    assert announced.session_id == session.record.id
    readiness = announced.payload["readiness"]
    assert readiness["state"] == "blocked"
    assert "terminal_input_after_completion" in readiness["reasons"]
    assert readiness["authorized"] is False
    assert readiness["observed_at"] > 0


async def test_announcements_are_transient_and_never_persisted() -> None:
    """A per-second event type in the capped ledger evicts real history.

    `events` is swept to the newest 100k rows, so this is not a write-cost
    argument: it is that the git-provenance and incident-forensics window would
    shrink to whatever fits beside a readiness stream.
    """

    session, tracker, clock = _watched_agent()
    persisted: list[MuxEvent] = []

    async def sink(event: MuxEvent) -> int:
        persisted.append(event)
        return len(persisted)

    bus = EventBus(sink=sink)
    watcher = _watcher(session, tracker, bus)
    bus.subscribe(name=BROWSER_SUBSCRIBER)
    await watcher.tick()
    session.input_revision += 1
    clock.advance(5.0)
    await watcher.tick()

    assert persisted == []


async def test_a_clock_driven_transition_is_announced_although_no_event_fired() -> None:
    """The reason this is a loop rather than another event subscription.

    `operator_recently_typed` clearing is the *absence* of typing. Nothing emits
    when a debounce elapses, so before this loop a session that became deliverable
    while the operator watched it kept reading blocked until some unrelated event
    happened to trigger a fleet refresh.
    """

    session, tracker, clock = _watched_agent()
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    browser = bus.subscribe(name=BROWSER_SUBSCRIBER)

    session.input_revision += 1
    session.last_input_event_ts = clock.monotonic()
    await watcher.tick()
    _collect(browser)

    # A turn boundary clears the composer guard; the debounce is then all that is
    # left, and only the clock resolves it.
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed"), session)
    clock.advance(0.2)
    await watcher.tick()
    blocked = _collect(browser)
    assert blocked and blocked[-1].payload["readiness"]["state"] != "safe"

    clock.advance(30.0)
    assert await watcher.tick() == [session.record.id]
    (settled,) = _collect(browser)
    assert settled.payload["readiness"]["state"] == "safe"


async def test_watching_a_session_cannot_change_its_verdict() -> None:
    """The loop evaluates with `adopt=False`, so it observes without deciding.

    `evaluate` snapshots the live screen as the completion baseline when it fills a
    lifecycle gap; a watcher running every second would take that snapshot at the
    earliest legal instant rather than at the operator's first look, and a Claude
    session watched before it wrote `?1049h` would be remembered as having finished
    on the normal screen — blocking it for the rest of the run.
    """

    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    tracker.observe(_event("session_spawned", scope="root"), session)
    session.observation_state["session_start_seen"] = True
    session.record.state = "idle"
    session.subscribers = {"pane"}
    clock.advance(60.0)

    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    bus.subscribe(name=BROWSER_SUBSCRIBER)
    for _ in range(5):
        clock.advance(1.0)
        await watcher.tick()

    # Still unadopted after five passes; the operator's first look is what adopts.
    assert tracker.evaluate(session, adopt=False)["evidence"]["completion_screen"] is None
    session.screen.feed(b"\x1b[?1049h")
    assert tracker.evaluate(session)["evidence"]["completion_screen"] == "alternate"


async def test_watching_does_not_inflate_the_shadow_metrics() -> None:
    """Those counters describe delivery attempts, and a watch is not one.

    They are the distribution behind the auto-delivery promotion argument, so a
    loop evaluating every live session every second would swamp them within
    minutes and make the proving period meaningless.
    """

    session, tracker, clock = _watched_agent()
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    bus.subscribe(name=BROWSER_SUBSCRIBER)
    for _ in range(10):
        clock.advance(1.0)
        await watcher.tick()

    assert tracker.metrics()["evaluations"] == {}
    assert tracker.metrics()["reasons"] == {}


async def test_a_session_with_no_pane_and_no_queue_is_not_followed() -> None:
    """Following a fleet nobody is reading spends real time on nobody's behalf."""

    session, tracker, clock = _watched_agent(attached=False)
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    browser = bus.subscribe(name=BROWSER_SUBSCRIBER)

    await watcher.tick()
    session.input_revision += 1
    clock.advance(5.0)
    assert await watcher.tick() == []
    assert _collect(browser) == []


async def test_a_pending_queue_item_is_enough_to_be_followed() -> None:
    """An armed message nobody can deliver is exactly what a person waits on.

    So a target with something pending is followed whether or not its pane is
    open — the answer being waited for is this session's readiness.
    """

    session, tracker, clock = _watched_agent(attached=False)
    store = _Store([{"target_session_id": session.record.id, "pending": 2}])
    bus = EventBus()
    watcher = _watcher(session, tracker, bus, store)
    bus.subscribe(name=BROWSER_SUBSCRIBER)

    await watcher.tick()
    session.input_revision += 1
    clock.advance(5.0)
    assert await watcher.tick() == [session.record.id]


async def test_a_target_whose_queue_drains_stops_being_followed_and_re_baselines() -> None:
    """Leaving scope forgets the verdict, so re-entering is a baseline not a change.

    Otherwise a session that drained its queue, changed twice unobserved, and then
    had a pane opened would announce a "change" from a verdict the client never
    held.
    """

    session, tracker, clock = _watched_agent(attached=False)
    store = _Store([{"target_session_id": session.record.id, "pending": 1}])
    bus = EventBus()
    watcher = _watcher(session, tracker, bus, store)
    browser = bus.subscribe(name=BROWSER_SUBSCRIBER)

    await watcher.tick()
    store.rows = [{"target_session_id": session.record.id, "pending": 0}]
    clock.advance(1.0)
    await watcher.tick()

    session.input_revision += 1
    clock.advance(5.0)
    store.rows = [{"target_session_id": session.record.id, "pending": 3}]
    assert await watcher.tick() == []
    assert _collect(browser) == []


async def test_an_ended_session_is_dropped_rather_than_followed() -> None:
    """Nothing about a dead session can change, and its memory must not linger."""

    session, tracker, clock = _watched_agent()
    bus = EventBus()
    watcher = _watcher(session, tracker, bus)
    bus.subscribe(name=BROWSER_SUBSCRIBER)
    await watcher.tick()

    session.record.state = "exited"
    clock.advance(1.0)
    assert await watcher.tick() == []


async def test_a_failing_queue_read_does_not_stop_the_watch() -> None:
    """Panes are the other half of the scope, and they need no database."""

    session, tracker, clock = _watched_agent()

    class _Broken(_Store):
        async def summary(self) -> list[dict[str, Any]]:
            raise RuntimeError("database is locked")

    bus = EventBus()
    watcher = _watcher(session, tracker, bus, _Broken())
    bus.subscribe(name=BROWSER_SUBSCRIBER)

    await watcher.tick()
    session.input_revision += 1
    clock.advance(5.0)
    assert await watcher.tick() == [session.record.id]
