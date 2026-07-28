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

This module owns *daemon background tasks*. It is unrelated to `supervisor.py`,
which owns PTY host processes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# A loop that survived this long before failing gets its backoff reset: a fault
# after an hour of healthy running is a new incident, not an escalating one.
_BACKOFF_RESET_SECONDS = 300.0
_DEFAULT_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0
_MAX_FAULT_CHARS = 400


@dataclass(slots=True)
class TaskHealth:
    """Observable state of one supervised loop."""

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

    def as_dict(self, now: float) -> dict[str, Any]:
        return {
            "name": self.name,
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
            "uptime_seconds": round(now - self.started_at, 3),
        }


class TaskSupervisor:
    """Registry of supervised background loops and their health."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._health: dict[str, TaskHealth] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

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
        """
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - a background loop must outlive its faults
            self.note_fault(name, exc)
        else:
            self.note_progress(name)

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
        return {
            "loops": entries,
            "degraded": degraded,
            "total_faults": sum(int(item["faults"]) for item in entries),
            "total_restarts": sum(int(item["restarts"]) for item in entries),
        }

    def reset(self) -> None:
        """Drop all registered state. For tests and daemon re-initialization."""
        self._health.clear()
        self._tasks.clear()


# The daemon's single background-task registry. A module-level instance keeps
# eight independently-constructed managers reporting into one health surface
# without threading a supervisor through every constructor.
background = TaskSupervisor()
