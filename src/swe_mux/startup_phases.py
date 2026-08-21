"""Per-phase daemon startup timing, published while it is still happening.

The daemon used to build its entire runtime before binding a listener, and it
said nothing while doing so. A start with 30 live sessions measured 226.6s, of
which ~170s was two completely silent stretches in `daemon.log`: one between the
predecessor-death warning and "PTY supervisor connected", another between
session reattachment and the first process-ownership diagnostic. A healthy but
slow deploy was therefore indistinguishable from a hung one, and the redeploy
script's health ceiling rolled back a perfectly good bundle because of it.

Two things fix that, and this module is the first. Every phase is named, timed,
and logged on completion, and a watchdog reports a phase that is *still running*
so nothing can go quiet for minutes again - the completion line alone would not
have helped, because the problem was never knowing what was in flight. The
second is that the listeners now bind first (`server.runtime_context`), which is
what lets `snapshot()` be served to a client as "starting, phase X" instead of
refusing the connection.

Nothing here decides anything. It measures, and the measurement is the product:
a phase that drifts is visible in `daemon.log` and `lifecycle.log` as a number
rather than as an inference from the gap between two unrelated timestamps.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

# How long one phase (or one unnamed stretch between phases) may run before the
# watchdog says so. Well under the shortest silence this was built to expose,
# and well above any phase that is expected to be instant.
SLOW_PHASE_SECONDS = 15.0

# A completed phase at or above this is worth a WARNING rather than an INFO
# line: it is a phase that will dominate the next start unless something changes.
SLOW_PHASE_WARNING_SECONDS = 10.0

# The name a stretch of startup that is not inside any phase is reported under.
# It exists so unwrapped work cannot be silent - the failure this module was
# written for was exactly code that no one had named.
UNNAMED_PHASE = "(unnamed)"


@dataclass(frozen=True)
class PhaseRecord:
    """One completed startup phase."""

    name: str
    seconds: float


class StartupTimeline:
    """Names, times, and publishes the phases of one daemon start.

    Not thread-safe and not meant to be: startup is one coroutine, and the
    watchdog only reads. `snapshot()` is the whole external contract - the
    health endpoint serves it while the build runs, so it must stay cheap and
    must never raise.
    """

    def __init__(
        self,
        log: logging.Logger,
        *,
        ledger: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        slow_phase_seconds: float = SLOW_PHASE_SECONDS,
    ) -> None:
        self._log = log
        self._ledger = ledger
        self._clock = clock
        self._slow_phase_seconds = max(1.0, float(slow_phase_seconds))
        self._started = clock()
        self._phase: str | None = None
        self._phase_started = self._started
        self._reported_at = self._started
        self._completed: list[PhaseRecord] = []
        self._status = "starting"
        self._error: str = ""

    # ---- measurement --------------------------------------------------------

    def mark(self, name: str) -> None:
        """Start phase `name`, closing and logging whatever was running.

        The primary API, because the thing being measured is a linear
        composition root: one long function that builds handles in a fixed
        order. A scoping context manager would have required re-indenting all of
        it, and a re-indent is exactly the kind of diff that hides a change in
        behaviour among hundreds of lines of moved whitespace.
        """
        self._close_open_phase()
        self._phase = name
        self._phase_started = self._clock()
        self._reported_at = self._phase_started

    @asynccontextmanager
    async def phase(self, name: str) -> AsyncIterator[None]:
        """Scoped form of `mark`, for a region that stands on its own."""
        self.mark(name)
        try:
            yield
        finally:
            self._close_open_phase()

    def _close_open_phase(self) -> None:
        """Record and log the in-flight phase, if there is one.

        Unnamed stretches are recorded too. The whole failure this module exists
        for was work nobody had named, so silently folding an untimed gap into
        its neighbours would reintroduce it in miniature.
        """
        seconds = self._clock() - self._phase_started
        name = self._phase
        self._phase = None
        self._phase_started = self._clock()
        self._reported_at = self._phase_started
        if name is None:
            if self._completed and seconds >= 0.5:
                self._completed.append(PhaseRecord(UNNAMED_PHASE, seconds))
            return
        self._completed.append(PhaseRecord(name, seconds))
        self._log.log(
            logging.WARNING if seconds >= SLOW_PHASE_WARNING_SECONDS else logging.INFO,
            "startup_phase name=%s elapsed=%.2fs total=%.1fs",
            name,
            seconds,
            self.total_seconds,
        )
        self._write_ledger(
            f"startup phase {name} took {seconds:.1f}s ({self.total_seconds:.1f}s in)"
        )

    def finish(self, note: str = "") -> float:
        """Mark the runtime built; returns the total startup duration."""
        self._close_open_phase()
        total = self.total_seconds
        self._status = "ready"
        self._write_ledger(f"daemon runtime ready in {total:.1f}s{f'; {note}' if note else ''}")
        return total

    def fail(self, error: BaseException | str) -> None:
        """Mark the build failed so a probe reads a reason rather than a stall.

        Closes the in-flight phase first: which phase was running when the build
        died, and for how long, is the first question anyone asks, and it is
        gone if the exception path skips the bookkeeping.
        """
        self._close_open_phase()
        self._status = "failed"
        self._error = str(error) or error.__class__.__name__
        self._write_ledger(
            f"daemon runtime build FAILED after {self.total_seconds:.1f}s: {self._error}"
        )

    def _write_ledger(self, message: str) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger(message)
        except Exception:  # noqa: BLE001 - a forensic sink must never break startup
            self._log.debug("startup ledger write failed", exc_info=True)

    # ---- publication --------------------------------------------------------

    @property
    def total_seconds(self) -> float:
        return self._clock() - self._started

    @property
    def ready(self) -> bool:
        return self._status == "ready"

    @property
    def status(self) -> str:
        return self._status

    def snapshot(self) -> dict[str, Any]:
        """What a client asking "is it up yet" is told.

        `phase` is deliberately absent rather than stale once the build is over:
        a name left behind after readiness reads as a phase that never ended.
        """
        phase = self._phase if self._status == "starting" else None
        payload: dict[str, Any] = {
            "status": self._status,
            "phase": phase,
            "phase_seconds": round(self._clock() - self._phase_started, 2) if phase else 0.0,
            "elapsed_seconds": round(self.total_seconds, 2),
            "phases": [
                {"name": record.name, "seconds": round(record.seconds, 2)}
                for record in self._completed
            ],
        }
        if self._error:
            payload["error"] = self._error
        return payload

    # ---- watchdog -----------------------------------------------------------

    def overdue(self) -> tuple[str, float] | None:
        """The in-flight phase that has gone unreported too long, if any.

        Reports the unnamed stretch between phases too. A phase nobody wrapped is
        exactly the kind that goes silent for minutes, so it cannot be exempt
        from the rule that made this module necessary.
        """
        if self._status != "starting":
            return None
        elapsed = self._clock() - self._reported_at
        if elapsed < self._slow_phase_seconds:
            return None
        name = self._phase or UNNAMED_PHASE
        return name, self._clock() - self._phase_started

    def report_overdue(self) -> bool:
        """Log the in-flight phase if it is overdue; True when a line was written."""
        overdue = self.overdue()
        if overdue is None:
            return False
        name, running = overdue
        self._reported_at = self._clock()
        self._log.warning(
            "startup_phase_running name=%s elapsed=%.1fs total=%.1fs (still working)",
            name,
            running,
            self.total_seconds,
        )
        return True

    async def watchdog(self, *, interval: float = 5.0) -> None:
        """Keep reporting whatever is in flight until the build ends.

        Runs as its own task so it reports even when a phase is one long await.
        It cannot report a phase that blocks the event loop outright - that is
        why the expensive blocking work on this path (the SQLite integrity probe)
        was moved off the loop rather than merely being named.
        """
        while self._status == "starting":
            await asyncio.sleep(interval)
            self.report_overdue()
