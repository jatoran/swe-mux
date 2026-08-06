"""Windows expires waitable timers on a global tick, and this codebase waits below it."""

from __future__ import annotations

import statistics
import sys
import threading
import time

import pytest

from swe_mux.timer_resolution import (
    TIMER_PERIOD_MS,
    raise_timer_resolution,
    release_timer_resolution,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows timer behaviour")


def _median_wait(requested: float, rounds: int = 25) -> float:
    """Median wall time for `threading.Event.wait`, the PTY reader's primitive."""
    event = threading.Event()
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        event.wait(requested)
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


def test_raising_the_period_is_idempotent_and_releasable() -> None:
    assert raise_timer_resolution() is True
    assert raise_timer_resolution() is True, "a second call must not double-acquire"
    release_timer_resolution()
    release_timer_resolution()  # releasing twice is not an error
    assert raise_timer_resolution() is True
    release_timer_resolution()


def test_a_sub_tick_wait_is_actually_short_once_the_period_is_raised() -> None:
    """The measurement the ladder in `pty_host` depends on being true.

    Its active rung asks for 0.5ms. On the default ~15.625ms tick that wait costs
    15.5ms, which collapses all three rungs into one value and makes the cheap rung
    exactly as expensive as the idle one. Tuning intervals below the tick is not
    tuning at all.
    """
    release_timer_resolution()
    assert raise_timer_resolution(TIMER_PERIOD_MS) is True
    try:
        raised = _median_wait(0.0005)
    finally:
        release_timer_resolution()

    # Generous against a loaded CI box: the effect being pinned is 15.5ms to ~1.2ms,
    # so anything under half a tick proves the period was honoured.
    assert raised < 0.0078, f"a 0.5ms wait took {raised * 1000:.2f}ms with the period raised"
