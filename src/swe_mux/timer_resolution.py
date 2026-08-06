"""Ask Windows for a timer it can actually honour.

Windows expires waitable timers on a global tick, ~15.625 ms by default. Anything that
sleeps for less than that does not sleep for less than that, and this codebase is built
out of short waits:

- the PTY reader's poll ladder (`pty_host.read_poll_interval`) asks for 0.5 ms while
  output is flowing, and got 15.5 ms
- its deep-idle rung asks for 40 ms and got 46.6 ms, rounded up to the next tick
- every `asyncio.sleep` in the daemon, and therefore every round trip that crosses the
  event loop between a keystroke and its echo

Measured 2026-08-05, median of 40 waits per row:

    requested   Event.wait   asyncio.sleep      Event.wait   asyncio.sleep
                     (default)                    (timeBeginPeriod(1))
      0.5 ms      15.54 ms       15.64 ms          1.17 ms        1.96 ms
     10.0 ms      15.57 ms       15.58 ms         10.61 ms       10.20 ms
     40.0 ms      46.56 ms       46.62 ms         40.62 ms       41.33 ms

So the reader's three-rung ladder collapsed into a single value, and the rung that was
supposed to be the cheap one was the same cost as the expensive one. Tuning intervals
below the tick is not tuning at all.

`time.sleep` is deliberately absent from that table: CPython already backs it with a
high-resolution waitable timer on Windows, so it was accurate throughout. Only the
primitives this code actually waits on were affected.

**Why raising it is polite here.** Before Windows 10 2004 `timeBeginPeriod` changed the
tick for the whole system, which is why it has a reputation. Since 2004 it applies to the
calling process, so this asks for a sharper timer for swe-mux and leaves everything else
alone. The cost is a slightly higher timer interrupt rate for this process, which is the
right trade for a process whose job is to carry keystrokes.

Idempotent and best-effort: a platform without `winmm`, or a refusal, leaves the process
running on the default tick with nothing worse than the latency it already had.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import sys
import threading

log = logging.getLogger(__name__)

#: 1 ms is the finest period Windows accepts here and the finest any of these waits
#: needs; asking for 0 is rejected, and nothing in this codebase waits below 0.5 ms.
TIMER_PERIOD_MS = 1

_lock = threading.Lock()
_active: int | None = None


def raise_timer_resolution(period_ms: int = TIMER_PERIOD_MS) -> bool:
    """Request a finer timer period for this process. True when it was granted.

    Safe to call more than once: the period is raised at most once per process and
    released at exit, so callers do not have to coordinate.
    """
    global _active
    if sys.platform != "win32":
        return False
    with _lock:
        if _active is not None:
            return True
        try:
            if ctypes.WinDLL("winmm").timeBeginPeriod(period_ms) != 0:
                log.debug("timeBeginPeriod(%d) refused; staying on the default tick", period_ms)
                return False
        except (OSError, AttributeError) as exc:
            log.debug("timer resolution unavailable: %s", exc)
            return False
        _active = period_ms
        atexit.register(release_timer_resolution)
        log.info("timer resolution raised to %dms for this process", period_ms)
        return True


#: What Windows falls back to when nobody has asked for better.
DEFAULT_TICK_SECONDS = 0.015625


def effective_period_seconds() -> float:
    """The finest interval a wait in this process can actually resolve to.

    Anything measuring latency here has to know this or it reports the scheduler as
    congestion: on the default tick a wait of any length under ~15.6 ms costs ~15.6 ms,
    so percentiles below that are quantization. Once the period is raised the same
    percentiles become real signal, and a diagnostic that kept quoting the old floor
    would dismiss genuine stalls as noise.
    """
    period = _active
    return period / 1000 if period is not None else DEFAULT_TICK_SECONDS


def release_timer_resolution() -> None:
    """Give the period back. Never raises: this runs at interpreter exit."""
    global _active
    with _lock:
        period = _active
        _active = None
    if period is None:
        return
    try:
        ctypes.WinDLL("winmm").timeEndPeriod(period)
    except Exception:  # noqa: BLE001 - nothing at exit may fail loudly
        pass
