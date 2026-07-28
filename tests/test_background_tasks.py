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


