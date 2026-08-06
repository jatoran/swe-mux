"""Background-loop supervision and event-bus drop accounting.

Regression coverage for the audited failure mode: an unguarded `while True:`
loop dies permanently and silently on its first exception, and a full subscriber
queue drops durable evidence with nothing recording that it happened.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from swe_mux.background_tasks import TaskSupervisor
from swe_mux.event_bus import EventBus


def test_iteration_guard_records_the_fault_and_keeps_the_loop_alive() -> None:
    supervisor = TaskSupervisor()
    survived = False
    for index in range(3):
        with supervisor.iteration("demo"):
            if index == 1:
                raise RuntimeError("transient sqlite lock")
        survived = True
    assert survived
    entry = next(item for item in supervisor.health()["loops"] if item["name"] == "demo")
    assert entry["faults"] == 1
    assert entry["iterations"] == 2
    assert "transient sqlite lock" in str(entry["last_fault"])


def test_iteration_guard_does_not_swallow_cancellation() -> None:
    supervisor = TaskSupervisor()
    with pytest.raises(asyncio.CancelledError):
        with supervisor.iteration("demo"):
            raise asyncio.CancelledError


async def test_supervised_loop_restarts_after_an_uncaught_exception() -> None:
    supervisor = TaskSupervisor()
    starts = 0
    running = asyncio.Event()

    async def flaky() -> None:
        nonlocal starts
        starts += 1
        if starts == 1:
            raise ValueError("loop died")
        running.set()
        await asyncio.sleep(3600)

    supervisor.start("flaky", flaky, backoff_seconds=0.01)
    try:
        await asyncio.wait_for(running.wait(), timeout=5)
    finally:
        await supervisor.stop_all()
    assert starts == 2
    entry = next(item for item in supervisor.health()["loops"] if item["name"] == "flaky")
    assert entry["restarts"] == 1
    assert "loop died" in str(entry["last_fault"])


async def test_health_reports_a_loop_that_finished_and_one_that_is_running() -> None:
    supervisor = TaskSupervisor()

    async def once() -> None:
        return None

    async def forever() -> None:
        await asyncio.sleep(3600)

    task = supervisor.start("once", once)
    supervisor.start("forever", forever)
    await asyncio.gather(task, return_exceptions=True)
    try:
        health = supervisor.health()
        loops = {item["name"]: item for item in health["loops"]}
        assert loops["once"]["running"] is False
        assert loops["forever"]["running"] is True
    finally:
        await supervisor.stop_all()


async def test_stop_marks_the_loop_stopped_rather_than_degraded() -> None:
    supervisor = TaskSupervisor()

    async def forever() -> None:
        await asyncio.sleep(3600)

    supervisor.start("forever", forever)
    await supervisor.stop("forever")
    entry = next(item for item in supervisor.health()["loops"] if item["name"] == "forever")
    assert entry["stopped"] is True
    assert supervisor.health()["degraded"] == []


async def test_event_bus_counts_drops_per_subscriber() -> None:
    """A slow consumer's losses must be attributable, not silent."""
    bus = EventBus()
    slow = bus.subscribe(name="slow-consumer")
    fast = bus.subscribe(name="fast-consumer")
    for index in range(slow.maxsize + 5):
        await bus.emit("tool_use", session_id="s1", tool=f"t{index}")
        # The fast consumer keeps draining; the slow one never does.
        fast.get_nowait()

    stats = bus.drop_stats()
    assert stats["dropped"]["slow-consumer"] == 5
    assert "fast-consumer" not in stats["dropped"]
    assert stats["dropped_total"] == 5
    assert stats["queue_depth"]["slow-consumer"] == slow.maxsize
    assert stats["last_drop_ts"]["slow-consumer"] > 0
    bus.unsubscribe(slow)
    bus.unsubscribe(fast)
    # Losses stay visible after the subscriber goes away.
    assert bus.drop_stats()["dropped"]["slow-consumer"] == 5


def test_every_long_lived_daemon_loop_is_supervised() -> None:
    """No `while True:` daemon loop may run unguarded.

    An unsupervised loop dies permanently and silently on its first exception.
    This caught four the first supervision pass missed, including the lifecycle
    heartbeat, whose death makes every later daemon start report a false "died
    without a clean shutdown" for a daemon that was in fact running fine.

    A loop that genuinely should not join the registry (one scoped to a single
    connection or session, or one already guarded in place) opts out with an
    inline `# unsupervised-loop-ok:` marker stating why, so the exemption is
    reviewable at the site rather than in a list that quietly grows.
    """
    source_root = Path(__file__).parents[1] / "src" / "swe_mux"
    exempt_modules = {
        "background_tasks.py",  # the supervisor's own restart loop
        "supervisor.py",  # separate process; has no daemon registry
        "supervisor_client.py",  # framing read loop, tied to one connection
        "pty_host.py",  # OS reader thread, not asyncio
    }
    loop_headers = {"while True:", "async for _ in ws:"}

    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        if path.name in exempt_modules:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.strip() not in loop_headers:
                continue
            preamble = "\n".join(lines[max(0, index - 6) : index])
            if "unsupervised-loop-ok:" in preamble:
                continue
            body = "\n".join(lines[index : index + 40])
            if "background.iteration(" in body or "background.note_" in body:
                continue
            enclosing = ""
            for back in range(index, -1, -1):
                match = re.match(r"\s*(?:async )?def (\w+)", lines[back])
                if match:
                    enclosing = match.group(1)
                    break
            offenders.append(f"{path.name}:{index + 1} in {enclosing}()")

    assert not offenders, (
        "unsupervised loop(s) found. Wrap the iteration in background.iteration(NAME), "
        "or add an inline `# unsupervised-loop-ok: <reason>` marker if it is scoped to "
        f"one connection/session or already guarded in place: {offenders}"
    )




def test_loop_health_ranks_by_cost_not_by_how_often_a_loop_ticks() -> None:
    """The metric that made a 45%-of-CPU loop look like the cheapest in the daemon.

    `process-inspector` ran 0.15 iterations/sec, second-least frequent of 27 loops,
    while consuming 45.2% of the daemon's samples. Iteration counts ranked it last.
    `busy_share` is what ranks an expensive rare loop above a cheap frequent one.
    """
    from swe_mux.background_tasks import TaskSupervisor

    wall = [1000.0]
    ticks = [0.0]
    supervisor = TaskSupervisor(clock=lambda: wall[0], monotonic=lambda: ticks[0])

    # A loop that ticks constantly and costs nothing.
    for _ in range(100):
        with supervisor.iteration("chatty"):
            ticks[0] += 0.0001

    # A loop that ticks twice and eats a second each time.
    for _ in range(2):
        with supervisor.iteration("expensive"):
            ticks[0] += 1.0

    wall[0] += 20.0
    health = supervisor.health()
    loops = {item["name"]: item for item in health["loops"]}

    assert loops["chatty"]["iterations"] == 100
    assert loops["expensive"]["iterations"] == 2
    # The whole point: frequency says "chatty", cost says "expensive".
    assert loops["expensive"]["busy_seconds"] > loops["chatty"]["busy_seconds"] * 50
    assert health["costliest"][0]["name"] == "expensive"
    assert loops["expensive"]["busy_share"] > loops["chatty"]["busy_share"]
    assert loops["expensive"]["p95_seconds"] == 1.0
    assert loops["expensive"]["slowest_seconds"] == 1.0


def test_a_failing_iteration_is_still_timed() -> None:
    """A loop burning the daemon and then raising is the worst case to leave unmeasured."""
    from swe_mux.background_tasks import TaskSupervisor

    ticks = [0.0]
    supervisor = TaskSupervisor(clock=lambda: 5.0, monotonic=lambda: ticks[0])

    with supervisor.iteration("doomed"):
        ticks[0] += 0.5
        raise RuntimeError("boom")

    entry = supervisor.health()["loops"][0]
    assert entry["faults"] == 1
    assert entry["iterations"] == 0
    assert entry["busy_seconds"] == 0.5


def test_loop_lag_reports_the_delay_a_user_actually_feels() -> None:
    """A 40ms synchronous call delays every frame, request and keystroke behind it.

    Codex rollout discovery did exactly that every 2s and nothing reported it; it was
    found by attaching a profiler while looking for something else.
    """
    from swe_mux.loop_lag import LoopLagMonitor

    monitor = LoopLagMonitor(stall_threshold=0.1)
    empty = monitor.snapshot()
    assert empty["samples"] == 0 and empty["stalls"] == 0

    for lag in (0.001, 0.002, 0.001, 0.36, 0.002):
        monitor.observe(lag)

    snapshot = monitor.snapshot()
    assert snapshot["samples"] == 5
    assert snapshot["stalls"] == 1, "only the 360ms sample is a stall"
    assert snapshot["max_seconds"] == 0.36
    assert snapshot["worst_seconds"] == 0.36
    assert snapshot["p50_seconds"] <= 0.002, "one stall must not move the median"

    # Negative lag is impossible to act on and only ever means clock noise.
    monitor.observe(-5.0)
    assert monitor.snapshot()["worst_seconds"] == 0.36


def test_loop_lag_keeps_the_worst_stall_after_it_leaves_the_window() -> None:
    """The window answers "is it slow now"; the worst stall must survive being old."""
    from swe_mux.loop_lag import LoopLagMonitor

    monitor = LoopLagMonitor(window=4)
    monitor.observe(2.0)
    for _ in range(8):
        monitor.observe(0.001)

    snapshot = monitor.snapshot()
    assert snapshot["samples"] == 4
    assert snapshot["max_seconds"] == 0.001, "the window has rolled past the stall"
    assert snapshot["worst_seconds"] == 2.0, "but the report must not lose it"
    assert snapshot["stalls"] == 1


@pytest.mark.asyncio
async def test_loop_lag_sample_measures_delay_beyond_the_interval_it_asked_for() -> None:
    """`asyncio.sleep` never returns early, so everything past the interval is lag."""
    from swe_mux.loop_lag import LoopLagMonitor

    monitor = LoopLagMonitor(interval=0.01)
    lag = await monitor.sample()

    assert lag >= 0.0
    # An unblocked loop wakes within a few milliseconds of the interval it requested.
    assert lag < 0.5


def test_a_wait_inside_the_guard_is_reported_as_the_loop_s_own_cost() -> None:
    """Pins the trap, because the metric cannot tell an await from work.

    The first live reading of this instrumentation ranked `status-timeline-flush` the
    most expensive loop in the daemon at a 1.02s p95, entirely because its batching
    sleep sat inside `iteration()` while the event loop was free throughout. The rule
    that follows is "wrap the work, not the waiting", and this is what makes the
    consequence of breaking it visible rather than a mystery in a diagnostic.
    """
    from swe_mux.background_tasks import TaskSupervisor

    ticks = [0.0]
    supervisor = TaskSupervisor(clock=lambda: 100.0, monotonic=lambda: ticks[0])

    with supervisor.iteration("guarded-wait"):
        ticks[0] += 1.0  # stands in for `await asyncio.sleep(...)` inside the guard

    assert supervisor.health()["loops"][0]["busy_seconds"] == 1.0


def test_loop_lag_flags_a_distribution_that_is_only_the_timer_tick() -> None:
    """Sub-tick percentiles are quantization, and reading them as congestion misleads.

    Windows wakes a timer on a ~15.625ms tick, so a sleep that asked for 0.5s routinely
    returns that late with nothing wrong. Measured on an *empty* event loop, which by
    construction has nothing to be late for: p50 13.5ms, p95 15.4ms, max 15.5ms. An idle
    daemon therefore reads worse here than a busy one, because system activity raises
    the global timer resolution and sharpens everyone's sleeps.
    """
    from swe_mux.loop_lag import LoopLagMonitor, timer_quantization_seconds

    quiet = LoopLagMonitor()
    for lag in (0.0002, 0.0135, 0.0154, 0.0089, 0.0155):
        quiet.observe(lag)
    snapshot = quiet.snapshot()
    assert snapshot["quantization_bound"] is True, "an all-under-tick window is not congestion"
    assert snapshot["timer_quantization_seconds"] == timer_quantization_seconds()
    assert snapshot["min_seconds"] == 0.0002
    assert snapshot["stalls"] == 0

    # One sample past the tick is the whole difference: the tick bounds the noise, so
    # anything well beyond it is real and the flag must drop.
    quiet.observe(0.4)
    assert quiet.snapshot()["quantization_bound"] is False
    assert quiet.snapshot()["stalls"] == 1
