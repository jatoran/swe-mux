"""Supervision and health reporting for long-lived background loops.

An unguarded `while True:` loop dies permanently on its first uncaught exception:
no log at failure time, no restart, no health signal. In a daemon designed to run
for weeks behind the PTY supervisor that is not a transient degradation — quota
polling, telemetry retention, fleet attention detection or config hot reload
simply stop for the rest of the process lifetime and nothing says so.

Two layers, both required:

- `TaskSupervisor.iteration(name)` guards one loop *iteration*. The fault is
  attributed, counted and logged; the loop keeps its cadence and its next
  iteration runs. This is the layer that matters — a transient SQLite lock must
  cost one cycle, not the feature.
- `TaskSupervisor.start(name, factory)` guards the *loop itself*. If an exception
  escapes anyway (raised by the sleep, the loop setup, or a guard bug) the task is
  restarted with capped exponential backoff instead of vanishing.

`health()` is what makes both observable; it is surfaced at
`/api/diagnostics/background`. Nothing here may raise into a caller: supervision
that can fail is not supervision.

It also owns load shedding (`pace`, `defer`): while the event loop has recently
lagged, housekeeping that can wait does, by a bounded amount, so the loop's time
goes to the terminals and requests a person is waiting on. Nothing is deferred
forever, and loops whose lateness is itself harm are never deferred at all
(`NEVER_DEFERRABLE`).

This module owns *daemon background tasks*. It is unrelated to `supervisor.py`,
which owns PTY host processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

#: Recent event-loop lag at or above which deferrable work waits. Two and a half
#: times the lag monitor's stall threshold: one late keystroke is not a drowning loop.
SHED_LAG_SECONDS = 0.25
#: How far back "recent" reaches. A loop that stalled this recently is assumed to be
#: still drowning; one that has run clean this long is not.
SHED_WINDOW_SECONDS = 5.0
#: A deferred wait re-reads the lag this often, so the work resumes soon after the
#: loop recovers rather than at the end of its whole allowance.
SHED_STEP_SECONDS = 1.0
#: Deferred work waits at most this many of its own cadences past its due time...
SHED_MAX_STRETCH = 3.0
#: ...and never more than this, so a loop with an hourly cadence is not put off by
#: hours: under sustained lag it runs at worst this late, every time.
SHED_MAX_EXTRA_SECONDS = 120.0

#: Loops whose lateness is itself the harm, which `pace` and `defer` never stretch
#: whatever a caller asks: desktop recovery reads the lifecycle heartbeat's age as
#: liveness, the state watchdog and the two flushes are how state and evidence
#: survive, the lag probe is the governor's own reading, and readiness and
#: auto-delivery are what a person waiting on a session sees. (The listener guard
#: belongs here too and is not under this supervisor at all.)
#: `tests/test_background_shedding.py` keeps the names in step with the constants
#: their modules define.
NEVER_DEFERRABLE = frozenset(
    {
        "lifecycle-heartbeat",
        "state-watchdog",
        "session-recovery-flush",
        "status-timeline-flush",
        "loop-lag",
        "readiness-watch",
        "auto-delivery",
    }
)


class LagReading(Protocol):
    """What the governor reads: the loop's worst recent lag (`LoopLagMonitor`)."""

    def recent_max(self, seconds: float) -> float: ...


def defer_allowance(cadence: float) -> float:
    """The most a pass of work with this cadence may be put off past its due time."""
    return max(0.0, min(cadence * SHED_MAX_STRETCH, SHED_MAX_EXTRA_SECONDS))


@dataclass(slots=True)
class Deferral:
    """How often, and for how long, one name's work has waited for the loop."""

    #: Passes that waited.
    count: int = 0
    #: Total time `pace` held passes back (`defer` skips a tick and adds nothing).
    seconds: float = 0.0
    #: The longest single wait, or the most overdue a `defer` skip let work become.
    longest_seconds: float = 0.0
    #: A `pace` wait is in progress right now.
    waiting: bool = False
    last_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "seconds": round(self.seconds, 3),
            "longest_seconds": round(self.longest_seconds, 3),
            "waiting": self.waiting,
            "last_at": self.last_at,
        }

# A loop that survived this long before failing gets its backoff reset: a fault
# after an hour of healthy running is a new incident, not an escalating one.
_BACKOFF_RESET_SECONDS = 300.0
_DEFAULT_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0
_MAX_FAULT_CHARS = 400
# Per-loop duration samples retained for percentiles. Enough that p95 means something
# for the slowest loops (a ~6.5s cadence covers ~28 minutes), small enough that 27 loops
# cost a few tens of kilobytes on a daemon that runs for weeks.
_DURATION_SAMPLES = 256


@dataclass(slots=True)
class TaskHealth:
    """Observable state of one supervised loop.

    **Iterations alone are not a cost signal, and reading them as one hides the
    expensive work by construction.** A loop that ticks rarely and costs a great deal
    per tick ranks *last* by iteration count. Measured 2026-08-05: `process-inspector`
    ran 0.15 iterations/sec, the second-least frequent of 27 loops, while consuming
    45.2% of the daemon's CPU samples. Nothing here said so, and the audit that had
    already optimized that file read this endpoint and moved on. So every loop is
    timed, and `busy_share` is what ranks them.
    """

    name: str
    started_at: float
    running: bool = True
    restarts: int = 0
    faults: int = 0
    iterations: int = 0
    last_fault: str | None = None
    last_fault_ts: float | None = None
    last_progress_ts: float | None = None
    stopped: bool = False
    #: Work timed by `account` across every session that runs it, rather than one
    #: supervised loop: never started, restarted or stopped here.
    aggregate: bool = False
    #: Total wall time inside `iteration()` bodies, awaits included. Not CPU, and not
    #: proof of blocking: see `iteration()` and `loop_lag`.
    busy_seconds: float = 0.0
    slowest_seconds: float = 0.0
    #: Recent per-iteration durations, for percentiles. Bounded because this is a
    #: diagnostic on a daemon that runs for weeks, not a metrics pipeline.
    recent_seconds: deque[float] = field(default_factory=lambda: deque(maxlen=_DURATION_SAMPLES))

    def observe(self, seconds: float) -> None:
        self.busy_seconds += seconds
        self.slowest_seconds = max(self.slowest_seconds, seconds)
        self.recent_seconds.append(seconds)

    def as_dict(self, now: float) -> dict[str, Any]:
        uptime = max(now - self.started_at, 0.0)
        samples = sorted(self.recent_seconds)
        return {
            "name": self.name,
            "aggregate": self.aggregate,
            "running": self.running,
            "stopped": self.stopped,
            "restarts": self.restarts,
            "faults": self.faults,
            "iterations": self.iterations,
            "last_fault": self.last_fault,
            "last_fault_ts": self.last_fault_ts,
            "last_progress_ts": self.last_progress_ts,
            "seconds_since_progress": (
                round(now - self.last_progress_ts, 3) if self.last_progress_ts else None
            ),
            "uptime_seconds": round(uptime, 3),
            "busy_seconds": round(self.busy_seconds, 4),
            # The ranking metric: what fraction of this loop's life it spent working.
            # A cheap loop reads ~0 however often it ticks; an expensive one reads high
            # however rarely it does.
            "busy_share": round(self.busy_seconds / uptime, 5) if uptime > 0 else None,
            "mean_seconds": (
                round(self.busy_seconds / self.iterations, 5) if self.iterations else None
            ),
            "p50_seconds": round(_percentile(samples, 0.50), 5) if samples else None,
            "p95_seconds": round(_percentile(samples, 0.95), 5) if samples else None,
            "slowest_seconds": round(self.slowest_seconds, 5),
        }


def _percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty sample."""
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


class TaskSupervisor:
    """Registry of supervised background loops and their health."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._clock = clock
        # Separate from `_clock` on purpose: wall time is what a timestamp means and
        # what tests inject, while a *duration* must come from a monotonic source that
        # a clock adjustment cannot make negative.
        self._monotonic = monotonic
        self._health: dict[str, TaskHealth] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lag: LagReading | None = None
        self._deferrals: dict[str, Deferral] = {}

    # -- health bookkeeping ---------------------------------------------------

    def _entry(self, name: str) -> TaskHealth:
        entry = self._health.get(name)
        if entry is None:
            entry = TaskHealth(name=name, started_at=self._clock())
            self._health[name] = entry
        return entry

    def note_fault(self, name: str, exc: BaseException) -> None:
        entry = self._entry(name)
        entry.faults += 1
        entry.last_fault = f"{type(exc).__name__}: {exc}"[:_MAX_FAULT_CHARS]
        entry.last_fault_ts = self._clock()
        # Loud on the first fault and then rate-limited: a loop failing every
        # cycle must not turn the log into the incident.
        if entry.faults == 1 or entry.faults % 50 == 0:
            log.exception("background loop %r iteration failed (%d faults)", name, entry.faults)

    def note_progress(self, name: str) -> None:
        entry = self._entry(name)
        entry.iterations += 1
        entry.last_progress_ts = self._clock()

    @contextlib.contextmanager
    def iteration(self, name: str) -> Generator[None]:
        """Guard one loop iteration: record the fault, keep the loop alive.

        Deliberately does not catch `BaseException`, so cancellation still tears
        the loop down at shutdown.

        The iteration is timed whether it succeeded or failed: a loop burning the
        daemon and then raising is the case least worth leaving unmeasured.

        **What is timed is wall time inside this block, awaits included**, which makes
        where a loop puts its waits part of its reported cost. A loop that awaits its
        batching window or its I/O inside the guard reports that time as its own even
        though the event loop was free throughout — `status_timeline._flush_loop` did
        exactly that and sat at the top of `costliest` with a 1.02s p95 while doing
        nothing. Keep waits outside the guard and wrap only the work. For the separate
        question of whether a loop *blocks* the daemon rather than merely taking a
        while, `loop_lag` is the authority: it is the only measurement that can tell
        an await from a stall.
        """
        started = self._monotonic()
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - a background loop must outlive its faults
            self._entry(name).observe(self._monotonic() - started)
            self.note_fault(name, exc)
        else:
            self._entry(name).observe(self._monotonic() - started)
            self.note_progress(name)

    @contextlib.contextmanager
    def account(self, name: str) -> Generator[None]:
        """Time work under ``name`` without supervising it.

        For per-session tasks (the transcript observer, the switch watcher, discovery
        and exit checks), which have their own lifecycles and error handling: their
        cost lands beside the supervised loops in `costliest`, summed over every
        session running one, which is the figure that ranks them. A fault
        propagates untouched. As with `iteration`, keep waits outside the block.
        """
        started = self._monotonic()
        try:
            yield
        finally:
            entry = self._entry(name)
            entry.aggregate = True
            entry.observe(self._monotonic() - started)
            entry.iterations += 1
            entry.last_progress_ts = self._clock()

    # -- load shedding --------------------------------------------------------

    def attach_lag(self, reading: LagReading | None) -> None:
        """Give the governor its reading; until then nothing is ever deferred."""
        self._lag = reading

    def recent_lag(self) -> float:
        reading = self._lag
        if reading is None:
            return 0.0
        try:
            return float(reading.recent_max(SHED_WINDOW_SECONDS))
        except Exception:  # noqa: BLE001 - a broken reading must not stop a loop
            log.debug("loop lag reading failed; not shedding", exc_info=True)
            return 0.0

    def shedding(self) -> bool:
        """Whether the event loop has lagged badly enough, recently enough, to shed."""
        return self.recent_lag() >= SHED_LAG_SECONDS

    def _deferrable(self, name: str, deferrable: bool) -> bool:
        return deferrable and name not in NEVER_DEFERRABLE

    async def pace(self, name: str, cadence: float, *, deferrable: bool = False) -> None:
        """Wait out one cadence between a loop's passes, longer while the loop drowns.

        The replacement for a loop's own `asyncio.sleep(cadence)`. A deferrable
        loop that finds the event loop lagging past `SHED_LAG_SECONDS` keeps
        waiting, re-reading every `SHED_STEP_SECONDS`, until the lag clears or its
        allowance (`defer_allowance`) runs out - so under sustained lag it still
        runs, at most that late, and it resumes within a step of recovery. A loop
        that is not deferrable, or is in `NEVER_DEFERRABLE`, sleeps the cadence and
        nothing more.
        """
        await asyncio.sleep(cadence)
        if not self._deferrable(name, deferrable) or not self.shedding():
            return
        record = self._deferrals.setdefault(name, Deferral())
        allowance = defer_allowance(cadence)
        started = self._monotonic()
        record.waiting = True
        try:
            while self.shedding():
                remaining = allowance - (self._monotonic() - started)
                if remaining <= 0:
                    break
                await asyncio.sleep(min(SHED_STEP_SECONDS, remaining))
        finally:
            waited = self._monotonic() - started
            record.waiting = False
            record.count += 1
            record.seconds += waited
            record.longest_seconds = max(record.longest_seconds, waited)
            record.last_at = self._clock()

    def defer(self, name: str, *, overdue: float, cadence: float) -> bool:
        """Whether due work should skip this pass of a loop that ticks for it.

        For loops that wake often and decide inside the tick what is due (the Git
        sweep): ``overdue`` is how far past its due time the work already is. True
        while the loop is shedding and the work is still inside its allowance; once
        it has waited that long it runs, lag or not.
        """
        if not self._deferrable(name, True) or not self.shedding():
            return False
        if overdue >= defer_allowance(cadence):
            return False
        record = self._deferrals.setdefault(name, Deferral())
        record.count += 1
        record.longest_seconds = max(record.longest_seconds, overdue)
        record.last_at = self._clock()
        return True

    def governor(self) -> dict[str, Any]:
        """The shedding state for `/api/diagnostics/background`."""
        lag = self.recent_lag()
        return {
            "attached": self._lag is not None,
            "shedding": lag >= SHED_LAG_SECONDS,
            "recent_max_lag_seconds": round(lag, 4),
            "lag_threshold_seconds": SHED_LAG_SECONDS,
            "window_seconds": SHED_WINDOW_SECONDS,
            "max_stretch": SHED_MAX_STRETCH,
            "max_extra_seconds": SHED_MAX_EXTRA_SECONDS,
            "deferrals": sum(record.count for record in self._deferrals.values()),
            "deferred": {
                name: record.as_dict() for name, record in sorted(self._deferrals.items())
            },
        }

    # -- task supervision -----------------------------------------------------

    def start(
        self,
        name: str,
        factory: Callable[[], Awaitable[None]],
        *,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
        max_backoff_seconds: float = _MAX_BACKOFF_SECONDS,
    ) -> asyncio.Task[None]:
        """Run `factory()` under supervision, restarting it on failure.

        `factory` is called again on every restart, so it must be a *factory*
        (`lambda: self._run()`), not an already-created coroutine — a coroutine
        object cannot be awaited twice.
        """
        existing = self._tasks.get(name)
        if existing is not None and not existing.done():
            return existing
        entry = self._entry(name)
        entry.started_at = self._clock()
        entry.running = True
        entry.stopped = False
        task = asyncio.create_task(
            self._supervise(name, factory, backoff_seconds, max_backoff_seconds),
            name=name,
        )
        self._tasks[name] = task
        return task

    async def _supervise(
        self,
        name: str,
        factory: Callable[[], Awaitable[None]],
        backoff_seconds: float,
        max_backoff_seconds: float,
    ) -> None:
        entry = self._entry(name)
        delay = backoff_seconds
        while True:
            started = self._clock()
            try:
                await factory()
            except asyncio.CancelledError:
                entry.running = False
                entry.stopped = True
                raise
            except Exception as exc:  # noqa: BLE001 - the whole point of supervision
                entry.restarts += 1
                self.note_fault(name, exc)
                if self._clock() - started >= _BACKOFF_RESET_SECONDS:
                    delay = backoff_seconds
                log.warning("restarting background loop %r in %.1fs", name, delay)
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    entry.running = False
                    entry.stopped = True
                    raise
                delay = min(max_backoff_seconds, delay * 2)
                continue
            # A supervised loop that returns normally is finished on purpose.
            entry.running = False
            return

    async def stop(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        entry = self._health.get(name)
        if entry is not None:
            entry.running = False
            entry.stopped = True
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def stop_all(self) -> None:
        for name in tuple(self._tasks):
            await self.stop(name)

    # -- reporting ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        now = self._clock()
        for name, task in self._tasks.items():
            entry = self._entry(name)
            if task.done() and not entry.stopped:
                entry.running = False
        entries = [entry.as_dict(now) for entry in self._health.values()]
        entries.sort(key=lambda item: str(item["name"]))
        degraded = [
            item["name"]
            for item in entries
            if not item["stopped"] and (not item["running"] or item["faults"])
        ]
        # Named separately from `degraded` because a loop can be perfectly healthy and
        # still be the reason the daemon is slow. This is the list to read first when
        # the question is cost rather than correctness, and it is ordered by the metric
        # that ranks an expensive rare loop above a cheap frequent one.
        costliest = [
            {
                "name": item["name"],
                "busy_share": item["busy_share"],
                "busy_seconds": item["busy_seconds"],
                "iterations": item["iterations"],
                "p95_seconds": item["p95_seconds"],
                "slowest_seconds": item["slowest_seconds"],
            }
            for item in sorted(
                entries, key=lambda item: item["busy_share"] or 0.0, reverse=True
            )[:10]
        ]
        return {
            "loops": entries,
            "degraded": degraded,
            "costliest": costliest,
            "total_faults": sum(int(item["faults"]) for item in entries),
            "total_restarts": sum(int(item["restarts"]) for item in entries),
            "total_busy_seconds": round(sum(float(item["busy_seconds"]) for item in entries), 3),
            "governor": self.governor(),
        }

    def reset(self) -> None:
        """Drop all registered state. For tests and daemon re-initialization."""
        self._health.clear()
        self._tasks.clear()
        self._deferrals.clear()
        self._lag = None


# The daemon's single background-task registry. A module-level instance keeps
# eight independently-constructed managers reporting into one health surface
# without threading a supervisor through every constructor.
background = TaskSupervisor()
