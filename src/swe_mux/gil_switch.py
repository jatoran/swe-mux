"""The interpreter's GIL switch interval, as the daemon configures it.

A thread that wants the GIL waits up to the switch interval before asking the holder
to give it up. The event loop releases the GIL around every system call it makes, so
whenever a worker thread is runnable each of those round trips can cost the loop up
to this long to win the lock back: the convoy `DAEMON_LOAD_ISOLATION.md` item 2
measured, and item 5 chose the default against.

Daemon-only on purpose. `timer_resolution.py` would be the natural neighbour, but it
is in the PTY supervisor's hash-gated source closure (`packaging/build_desktop.py`),
and a change there forces the supervisor update that reaps every session. The
supervisor keeps CPython's default.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def apply_gil_switch_interval(milliseconds: float) -> float:
    """Set the switch interval for this process; returns the milliseconds in force.

    Process-wide and immediate, so a live config change applies at once. On Windows
    the wait it bounds is a timed wait, only as fine as the process's timer period
    (`timer_resolution`), so the value in force can be finer than what a waiter sees.
    """
    sys.setswitchinterval(milliseconds / 1000)
    in_force = gil_switch_interval_ms()
    log.info("gil switch interval %.3gms", in_force)
    return in_force


def gil_switch_interval_ms() -> float:
    """The switch interval in force, in milliseconds."""
    return round(sys.getswitchinterval() * 1000, 3)
