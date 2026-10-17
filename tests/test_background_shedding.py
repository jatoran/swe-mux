"""Lag-driven load shedding: what waits while the event loop drowns, and for how long.

`DAEMON_LOAD_ISOLATION.md` item 11. Each test drives a supervisor against a lag
reading it controls, so "the loop is drowning" is a value rather than a host state.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from swe_mux import background_tasks
from swe_mux.background_tasks import (
    NEVER_DEFERRABLE,
    SHED_LAG_SECONDS,
    TaskSupervisor,
    background,
    defer_allowance,
)
from swe_mux.loop_lag import LoopLagMonitor

pytestmark = pytest.mark.anyio


class Lag:
    """A lag reading the test sets: `recent_max` answers whatever `value` holds."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value
        self.asked: list[float] = []

    def recent_max(self, seconds: float) -> float:
        self.asked.append(seconds)
        return self.value


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the step so a deferred wait re-reads the lag every 10 ms."""
    monkeypatch.setattr(background_tasks, "SHED_STEP_SECONDS", 0.01)


def test_recent_max_reads_only_the_window() -> None:
    now = [100.0]
    monitor = LoopLagMonitor(monotonic=lambda: now[0])
    monitor.observe(2.0)  # at t=100
    now[0] = 103.0
    monitor.observe(0.05)
    now[0] = 106.0
    monitor.observe(0.01)
    assert monitor.recent_max(5.0) == 0.05, "the 2 s stall is older than the window"
    assert monitor.recent_max(10.0) == 2.0
    now[0] = 120.0
    assert monitor.recent_max(5.0) == 0.0, "nothing sampled recently reads as no lag"


async def test_nothing_is_deferred_until_a_reading_is_attached(quick: None) -> None:
    supervisor = TaskSupervisor()
    assert not supervisor.shedding()
    started = time.perf_counter()
    await supervisor.pace("git-sweep", 0.01, deferrable=True)
    assert time.perf_counter() - started < 0.5
    assert supervisor.governor()["deferrals"] == 0
    assert supervisor.governor()["attached"] is False


async def test_a_deferrable_loop_waits_out_its_allowance_while_the_loop_drowns(
    quick: None,
) -> None:
    supervisor = TaskSupervisor()
    supervisor.attach_lag(Lag(SHED_LAG_SECONDS * 4))
    cadence = 0.02
    started = time.perf_counter()
    await supervisor.pace("usage-refresh", cadence, deferrable=True)
    elapsed = time.perf_counter() - started
    # The cadence, then the allowance (three cadences): bounded, never forever.
    assert elapsed >= cadence + defer_allowance(cadence) * 0.9
    assert elapsed < 2.0
    record = supervisor.governor()["deferred"]["usage-refresh"]
    assert record["count"] == 1
    assert record["seconds"] >= defer_allowance(cadence) * 0.9
    assert record["waiting"] is False


async def test_a_deferred_loop_resumes_soon_after_the_lag_clears(
    quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An allowance of ten seconds for a 10 ms cadence, which the test must not wait
    # out: only the lag clearing can end this wait in time.
    monkeypatch.setattr(background_tasks, "SHED_MAX_STRETCH", 1000.0)
    assert defer_allowance(0.01) == 10.0
    supervisor = TaskSupervisor()
    lag = Lag(SHED_LAG_SECONDS * 4)
    supervisor.attach_lag(lag)
    asyncio.get_running_loop().call_later(0.1, setattr, lag, "value", 0.0)
    started = time.perf_counter()
    await asyncio.wait_for(supervisor.pace("ghost-window-sweep", 0.01, deferrable=True), 5.0)
    assert 0.09 <= time.perf_counter() - started < 1.0
    record = supervisor.governor()["deferred"]["ghost-window-sweep"]
    assert record["count"] == 1 and record["seconds"] < 1.0


async def test_a_loop_that_is_not_deferrable_keeps_its_cadence(quick: None) -> None:
    supervisor = TaskSupervisor()
    supervisor.attach_lag(Lag(SHED_LAG_SECONDS * 4))
    started = time.perf_counter()
    await supervisor.pace("push-sender", 0.01)
    assert time.perf_counter() - started < 0.5
    assert supervisor.governor()["deferred"] == {}


@pytest.mark.parametrize("name", sorted(NEVER_DEFERRABLE))
async def test_a_protected_loop_is_never_deferred_whatever_it_asks(
    name: str, quick: None
) -> None:
    supervisor = TaskSupervisor()
    supervisor.attach_lag(Lag(SHED_LAG_SECONDS * 4))
    started = time.perf_counter()
    await supervisor.pace(name, 0.01, deferrable=True)
    assert time.perf_counter() - started < 0.5
    assert supervisor.defer(name, overdue=0.0, cadence=60.0) is False
    assert supervisor.governor()["deferred"] == {}


def test_defer_skips_due_work_only_inside_its_allowance() -> None:
    supervisor = TaskSupervisor()
    lag = Lag(SHED_LAG_SECONDS * 4)
    supervisor.attach_lag(lag)
    assert supervisor.defer("git-sweep", overdue=10.0, cadence=60.0) is True
    assert supervisor.defer("git-sweep", overdue=defer_allowance(60.0), cadence=60.0) is False, (
        "work that has waited its allowance runs, lag or not"
    )
    lag.value = 0.0
    assert supervisor.defer("git-sweep", overdue=0.0, cadence=60.0) is False
    record = supervisor.governor()["deferred"]["git-sweep"]
    assert (record["count"], record["longest_seconds"]) == (1, 10.0)


def test_the_allowance_is_bounded_in_absolute_terms() -> None:
    assert defer_allowance(5.0) == 15.0
    assert defer_allowance(60 * 60) == background_tasks.SHED_MAX_EXTRA_SECONDS


def test_a_broken_reading_never_sheds() -> None:
    class Broken:
        def recent_max(self, seconds: float) -> float:
            raise RuntimeError("probe gone")

    supervisor = TaskSupervisor()
    supervisor.attach_lag(Broken())
    assert supervisor.shedding() is False


def test_the_governor_is_on_the_health_report() -> None:
    supervisor = TaskSupervisor()
    supervisor.attach_lag(Lag(SHED_LAG_SECONDS))
    governor = supervisor.health()["governor"]
    assert governor["shedding"] is True
    assert governor["lag_threshold_seconds"] == SHED_LAG_SECONDS
    assert governor["recent_max_lag_seconds"] == SHED_LAG_SECONDS


def test_accounted_work_ranks_beside_the_loops_and_keeps_its_faults() -> None:
    clock = [0.0]
    supervisor = TaskSupervisor(clock=lambda: 1000.0, monotonic=lambda: clock[0])
    with supervisor.account("session:transcript-observer"):
        clock[0] += 0.25
    with pytest.raises(ValueError), supervisor.account("session:transcript-observer"):
        clock[0] += 0.5
        raise ValueError("the caller's own error")
    entry = next(
        item
        for item in supervisor.health()["loops"]
        if item["name"] == "session:transcript-observer"
    )
    assert entry["aggregate"] is True
    assert entry["busy_seconds"] == 0.75
    assert entry["iterations"] == 2
    assert entry["faults"] == 0, "accounting is not supervision: the fault is the caller's"
    assert "session:transcript-observer" not in supervisor.health()["degraded"]


def test_the_protected_names_are_the_names_those_loops_use() -> None:
    from swe_mux import (
        auto_delivery,
        readiness_watch,
        server,
        session,
        session_recovery,
        status_timeline,
    )

    assert NEVER_DEFERRABLE == {
        server.LIFECYCLE_HEARTBEAT_LOOP,
        session.STATE_WATCHDOG_LOOP,
        session_recovery.SESSION_RECOVERY_FLUSH_LOOP,
        status_timeline.STATUS_TIMELINE_FLUSH_LOOP,
        server.LOOP_LAG_LOOP,
        readiness_watch.READINESS_WATCH_LOOP,
        auto_delivery.AUTO_DELIVERY_LOOP,
    }


async def test_a_git_sweep_waits_while_the_loop_drowns_and_a_change_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux.git_monitor import GIT_SWEEP_DEFERRAL, GitMonitor

    from .test_git_monitor_watching import ROOT, _changed, _monitor, _Session

    monkeypatch.setattr(background, "_lag", Lag(SHED_LAG_SECONDS * 4))
    monkeypatch.setattr(background, "_deferrals", {})
    monitor, reads, _ = _monitor(monkeypatch, [_Session(ROOT, attached=True)])
    sweep = GitMonitor.ATTACHED_SWEEP_SECONDS
    await monitor._poll(now=0.0)
    assert len(reads) == 1
    await monitor._poll(now=sweep)
    assert len(reads) == 1, "the sweep is due, and waits"
    _changed(monitor)
    await monitor._poll(now=sweep + 1)
    assert len(reads) == 2, "a change the watch reported is read regardless"
    await monitor._poll(now=sweep + 1 + sweep + defer_allowance(sweep))
    assert len(reads) == 3, "and a sweep that has waited its allowance runs"
    assert background.governor()["deferred"][GIT_SWEEP_DEFERRAL]["count"] >= 1


async def test_a_process_read_keeps_a_stale_sample_while_shedding_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from typing import cast

    from swe_mux import processes
    from swe_mux.event_bus import EventBus
    from swe_mux.processes import PROCESS_INSPECTOR_LOOP, ProcessInspector

    monkeypatch.setattr(background, "_lag", Lag(SHED_LAG_SECONDS * 4))
    monkeypatch.setattr(background, "_deferrals", {})
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    collected: list[bool] = []
    monkeypatch.setattr(inspector, "_collect_all", lambda *_: collected.append(True) or [])
    monkeypatch.setattr(processes.time, "monotonic", lambda: 1000.0)
    inspector._last_collect = 1000.0 - inspector.cadence - 1.0  # due, and only just

    await inspector._ensure_sampled()
    assert collected == [], "the background pass is deferred, and a read must not replace it"
    assert background.governor()["deferred"][PROCESS_INSPECTOR_LOOP]["count"] == 1
    await inspector._ensure_sampled(force=True)
    assert collected == [True], "an action that needs live state still gets it"
