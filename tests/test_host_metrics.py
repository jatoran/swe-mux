"""Host readings the daemon takes from worker threads without holding the GIL."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

import pytest

from swe_mux.host_metrics import (
    ThreadCpuReader,
    host_memory_percent,
    host_process_count,
    process_exited,
    process_start_stamp,
)


def test_a_reader_that_says_it_is_supported_reads_the_calling_thread() -> None:
    # The macOS CI leg's lesson: a reader that claimed support while its ids could never
    # match a native id failed only downstream, as stall explanations naming no thread.
    reader = ThreadCpuReader()
    try:
        if not reader.supported:
            pytest.skip("per-thread CPU time is not readable on this host")
        native = threading.get_native_id()
        seconds = reader.read({native: id(threading.current_thread())})
        assert native in seconds, "a supported reader reads the thread that asked"
        assert seconds[native] >= 0.0
    finally:
        reader.close()


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


@pytest.mark.skipif(sys.platform != "win32", reason="the probe answers only on Windows")
def test_the_exit_probe_tells_a_running_process_from_an_exited_one() -> None:
    assert process_exited(os.getpid()) is False
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert process_exited(child.pid) is False
    finally:
        child.kill()
        child.wait()
    # `child` still holds its process handle, so the pid still opens: this is the
    # case psutil enumerates the host for, and the probe must call it exited.
    assert process_exited(child.pid) is True
    assert process_exited(2**31 - 2) is True, "no process object has this pid"


def test_the_exit_probe_declines_what_it_cannot_answer() -> None:
    assert process_exited(0) is None
    assert process_exited(-1) is None
    if sys.platform != "win32":
        assert process_exited(os.getpid()) is None


def test_host_readings_are_plausible() -> None:
    memory = host_memory_percent()
    assert memory is not None and 0.0 < memory <= 100.0
    count = host_process_count()
    assert count is not None and count > 1
