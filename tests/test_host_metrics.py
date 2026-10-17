"""Host readings the daemon takes from worker threads without holding the GIL."""

from __future__ import annotations

import os
import subprocess
import sys

from swe_mux.host_metrics import host_memory_percent, host_process_count, process_start_stamp


def test_a_process_start_stamp_identifies_one_process() -> None:
    own = process_start_stamp(os.getpid())
    assert isinstance(own, int) and own > 0
    assert process_start_stamp(os.getpid()) == own, "the same process, the same stamp"

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        stamp = process_start_stamp(child.pid)
        assert stamp is not None and stamp != own, "a different process, a different stamp"
    finally:
        child.kill()
        child.wait()


def test_a_process_that_is_not_there_has_no_stamp() -> None:
    assert process_start_stamp(0) is None
    assert process_start_stamp(-1) is None
    # Windows pids are multiples of four and Linux pid_max is at most 2**22, so
    # this one names no process on either.
    assert process_start_stamp(2**31 - 2) is None


def test_host_readings_are_plausible() -> None:
    memory = host_memory_percent()
    assert memory is not None and 0.0 < memory <= 100.0
    count = host_process_count()
    assert count is not None and count > 1
