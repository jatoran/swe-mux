"""How late the event loop is running the work it promised to run.

A daemon can be fully healthy by every other measure and still feel slow, because
the thing users experience is not CPU or memory but *when their keystroke gets
serviced*. Everything on this loop shares one thread: a single synchronous call that
takes 40 ms delays every terminal write, websocket frame, and HTTP response behind it
by 40 ms, and no per-subsystem metric reports that. Only the loop can.

The measurement is a sleep that knows what it asked for. `asyncio.sleep(interval)`
resolves no earlier than `interval`, so anything beyond it is time the loop was not
free to run this callback: some other coroutine was occupying the thread, or a
synchronous call inside one was. That excess is the lag, and it is exactly the
quantity a user feels.

This exists because the alternative was finding such stalls by accident. Codex rollout
discovery ran a 36 ms filesystem walk synchronously on this loop every 2 s
(`adapters/codex.py`), and nothing in the daemon reported it -- it was found by
attaching a sampling profiler while looking for something else. A stall that recurs
every couple of seconds is precisely what this makes obvious and cheap to watch.

Deliberately not a general metrics facility: one loop, one bounded window, read
through `/api/diagnostics/background`.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .timer_resolution import effective_period_seconds

#: Cadence of the probe. Short enough to catch a stall that recurs every couple of
#: seconds, long enough that the probe is never itself a meaningful load.
SAMPLE_INTERVAL_SECONDS = 0.5
#: Retained samples. At the cadence above this is about four minutes of history, which
#: is the window in which "is it slow *right now*" is answerable.
SAMPLE_WINDOW = 512
#: Lag beyond which a sample is counted as a stall rather than as scheduling noise.
#: A tenth of a second is unambiguously something blocking, is far above the timer
#: quantization documented below, and is roughly where a human starts to feel a
#: keystroke arrive late.
STALL_THRESHOLD_SECONDS = 0.1

#: Windows wakes a timer on a ~15.625 ms tick unless a process has raised the global
#: resolution, so a sleep that asked for 0.5 s routinely returns up to that late with
#: nothing whatsoever wrong.
#:
#: This is not a small caveat, it is most of the distribution. Measured 2026-08-05 on an
#: **empty** event loop, which by construction has nothing to be late for: p50 13.5 ms,
#: p95 15.4 ms, max 15.5 ms. An idle daemon read *worse* here than a busy one, purely
#: because system activity raises the global timer resolution and sharpens everyone's
#: sleeps.
#:
#: So `p50_seconds` and `p95_seconds` are only meaningful *above* this floor, and a
#: reading near it means "nothing is blocking the loop" rather than "the loop is 13 ms
#: behind". What survives quantization, and what an investigation should actually read,
#: is `max_seconds`, `worst_seconds` and `stalls`: the tick bounds the noise, so
#: anything well past it is real.
#:
#: Read from `timer_resolution` rather than hardcoded, because swe-mux raises the period
#: for its own processes: with it raised the floor is 1 ms and those same percentiles
#: stop being noise and start being signal. A diagnostic still quoting 15.6 ms after that
#: would wave away real stalls as scheduling.
def timer_quantization_seconds() -> float:
    return effective_period_seconds()


class LoopLagMonitor:
    """Samples event-loop scheduling delay and reports its distribution."""

    def __init__(
        self,
        *,
        interval: float = SAMPLE_INTERVAL_SECONDS,
        window: int = SAMPLE_WINDOW,
        stall_threshold: float = STALL_THRESHOLD_SECONDS,
        monotonic: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._interval = interval
        self._stall_threshold = stall_threshold
        self._monotonic = monotonic
        self._samples: deque[float] = deque(maxlen=window)
        self._worst = 0.0
        self._worst_at: float | None = None
        self._stalls = 0
        self._observed = 0

    def observe(self, lag_seconds: float) -> None:
        """Record one measured lag. Separated from the loop so it can be tested."""
        lag = max(0.0, lag_seconds)
        self._observed += 1
        self._samples.append(lag)
        if lag > self._worst:
            self._worst = lag
            self._worst_at = time.time()
        if lag >= self._stall_threshold:
            self._stalls += 1

    async def sample(self) -> float:
        """Sleep one interval and return how much later than that the loop woke us.

        Split from `observe` so the caller can run the recording under the background
        supervisor's `iteration()` guard: this loop must report its own liveness like
        every other, and a lag monitor that silently died is worse than none. The sleep
        itself stays outside that guard, since timing it would only measure this
        probe's own sleep rather than anything blocking the loop.
        """
        started = self._monotonic()
        await asyncio.sleep(self._interval)
        return self._monotonic() - started - self._interval

    def snapshot(self) -> dict[str, Any]:
        samples = sorted(self._samples)
        quantization = timer_quantization_seconds()
        if not samples:
            return {
                "samples": 0,
                "observed": self._observed,
                "stalls": self._stalls,
                "stall_threshold_seconds": self._stall_threshold,
                "timer_quantization_seconds": timer_quantization_seconds(),
            }
        return {
            "samples": len(samples),
            "observed": self._observed,
            "min_seconds": round(samples[0], 5),
            "p50_seconds": round(_at(samples, 0.50), 5),
            "p95_seconds": round(_at(samples, 0.95), 5),
            "p99_seconds": round(_at(samples, 0.99), 5),
            "max_seconds": round(samples[-1], 5),
            # The floor below which the percentiles above are the OS timer tick rather
            # than congestion. Reported so a reader can tell the two apart without
            # having to know this platform's scheduling behaviour.
            "timer_quantization_seconds": quantization,
            "quantization_bound": samples[-1] <= quantization,
            # Retained across the whole process lifetime, unlike the window above: the
            # single worst stall since boot is the one a report should not lose just
            # because it happened a few minutes ago.
            "worst_seconds": round(self._worst, 5),
            "worst_at": self._worst_at,
            "stalls": self._stalls,
            "stall_threshold_seconds": self._stall_threshold,
        }


def _at(ordered: list[float], fraction: float) -> float:
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]
