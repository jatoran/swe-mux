"""Contained runs, against real child processes.

The claims here are about what the OS does to a process - which scheduling class it
runs in, whether its descendants are held back too, whether a straggler survives the
run - so they are read back from the processes themselves rather than from the calls
that were supposed to cause them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from typing import Any

import psutil
import pytest

from swe_mux import background_containment, bounded_subprocess
from swe_mux.background_containment import (
    BACKGROUND_BATCH,
    ContainedRun,
    ContainmentPolicy,
    PreparedSpawn,
    ResourceSample,
)
from swe_mux.bounded_subprocess import run_bounded

WINDOWS = sys.platform == "win32"


def python_argv(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


@pytest.fixture(autouse=True)
def _fresh_state() -> None:
    bounded_subprocess.reset_log_windows()
    background_containment.reset_wrapper_cache()


async def _gone(pid: int, *, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(pid)
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        await asyncio.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# Policy and bookkeeping                                                      #
# --------------------------------------------------------------------------- #


def test_a_cpu_share_outside_one_to_a_hundred_is_refused() -> None:
    with pytest.raises(ValueError):
        ContainmentPolicy(cpu_percent=0)
    with pytest.raises(ValueError):
        ContainmentPolicy(cpu_percent=101)


class _ScriptedBackend:
    def __init__(self, readings: list[tuple[float | None, int | None, int | None]]) -> None:
        self._readings = list(readings)
        self.closed = False

    def poll(self) -> tuple[float | None, int | None, int | None]:
        return self._readings.pop(0) if len(self._readings) > 1 else self._readings[0]

    def peak_memory(self) -> int | None:
        return 4096

    def close(self) -> None:
        self.closed = True


def test_progress_counts_growth_and_ignores_a_member_leaving() -> None:
    backend = _ScriptedBackend(
        [
            (0.5, 100, 2),
            (0.5, 100, 2),  # nothing moved
            (0.5, 100, 1),  # a member exited: fewer processes is not progress
            (0.7, 100, 1),  # CPU moved
            (0.7, 900, 1),  # reads moved
        ]
    )
    run = ContainedRun(pid=1, mechanism="test", cpu_percent=25, _backend=backend)
    readings = [run.poll().progress for _ in range(5)]
    assert readings == [1, 1, 1, 2, 3]


def test_close_reports_final_accounting_once_and_releases_the_backend() -> None:
    backend = _ScriptedBackend([(1.25, 2048, 3)])
    run = ContainedRun(pid=1, mechanism="job", cpu_percent=25, _backend=backend)
    run.degraded.append("cpu_rate_cap_failed:5")

    first = run.close()
    second = run.close()

    assert first is second
    assert backend.closed is True
    assert first["mechanism"] == "job"
    assert first["cpu_percent_cap"] == 25
    assert first["cpu_seconds"] == 1.25
    assert first["read_bytes"] == 2048
    assert first["peak_memory_bytes"] == 4096
    assert first["processes"] == 3
    assert first["degraded"] == ["cpu_rate_cap_failed:5"]


def test_an_unmeasurable_sample_is_not_measured() -> None:
    assert not ResourceSample(None, None, None, 0).measured
    assert ResourceSample(0.0, None, None, 1).measured
    assert ResourceSample(None, 0, None, 1).measured


# --------------------------------------------------------------------------- #
# POSIX wrappers                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(WINDOWS, reason="POSIX wrappers; Windows uses a Job object")
def test_posix_wraps_with_every_usable_priority_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background_containment.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(background_containment, "_probe_wrapper", lambda _name, prefix: prefix)

    prepared = background_containment.prepare_spawn(["ccusage", "daily"], BACKGROUND_BATCH)

    if sys.platform == "darwin":
        assert prepared.argv == ["/bin/taskpolicy", "-b", "ccusage", "daily"]
        assert prepared.mechanism == background_containment.MECHANISM_TASKPOLICY
    else:
        assert prepared.argv == [
            "/bin/ionice",
            "-c",
            "3",
            "-t",
            "/bin/nice",
            "-n",
            "19",
            "ccusage",
            "daily",
        ]
        assert prepared.mechanism == background_containment.MECHANISM_NICE
    assert prepared.creationflags == 0
    assert prepared.degraded == ()


@pytest.mark.skipif(WINDOWS, reason="POSIX wrappers; Windows uses a Job object")
def test_a_wrapper_that_cannot_run_is_dropped_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(background_containment.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(background_containment, "_probe_wrapper", lambda _name, _prefix: None)

    prepared = background_containment.prepare_spawn(["ccusage"], BACKGROUND_BATCH)

    # The command still runs; it just runs without the guarantee, and says so.
    assert prepared.argv == ["ccusage"]
    assert prepared.mechanism == background_containment.MECHANISM_NONE
    assert "nice_unavailable" in prepared.degraded


@pytest.mark.skipif(WINDOWS, reason="POSIX scheduling")
async def test_a_contained_posix_child_runs_held_back() -> None:
    outcome = await run_bounded(
        python_argv("import os\nprint(os.nice(0))\n"),
        label="contained-nice",
        timeout_seconds=60,
        output_limit=4096,
        containment=BACKGROUND_BATCH,
    )
    assert outcome.exit_code == 0
    assert outcome.resources is not None
    mechanism = outcome.resources["mechanism"]
    if mechanism == background_containment.MECHANISM_TASKPOLICY:
        # Background QoS is a Darwin policy, not a nice value; the wrapper ran.
        assert sys.platform == "darwin"
    else:
        assert mechanism == background_containment.MECHANISM_NICE
        assert int(outcome.stdout.strip()) == 19


# --------------------------------------------------------------------------- #
# Windows Job object                                                          #
# --------------------------------------------------------------------------- #

_REPORT_SELF_AND_CHILD = """
import json, subprocess, sys
import psutil
me = psutil.Process()
probe = "import json, psutil; p = psutil.Process(); print(json.dumps([p.nice(), p.ionice()]))"
child = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
print(json.dumps({"self": [me.nice(), me.ionice()], "child": json.loads(child.stdout)}))
"""


@pytest.mark.skipif(not WINDOWS, reason="Windows Job object")
async def test_a_contained_windows_tree_is_idle_with_very_low_io() -> None:
    outcome = await run_bounded(
        python_argv(_REPORT_SELF_AND_CHILD),
        label="contained-tree",
        timeout_seconds=120,
        output_limit=64 * 1024,
        containment=BACKGROUND_BATCH,
    )

    assert outcome.exit_code == 0, outcome.stderr
    report: dict[str, Any] = json.loads(outcome.stdout)
    idle = psutil.IDLE_PRIORITY_CLASS
    # The class is the job's limit, so it holds for a member the poll never saw.
    assert report["self"][0] == idle
    assert report["child"][0] == idle
    # The root's I/O priority is lowered before its first instruction runs, and a
    # child inherits it at creation (measured 2026-10-31), before any poll could.
    assert report["self"][1] == psutil.IOPRIO_VERYLOW
    assert report["child"][1] == psutil.IOPRIO_VERYLOW
    resources = outcome.resources
    assert resources is not None
    assert resources["mechanism"] == background_containment.MECHANISM_JOB
    assert resources["cpu_percent_cap"] == BACKGROUND_BATCH.cpu_percent
    assert resources["degraded"] == []
    assert resources["processes"] >= 2
    assert resources["cpu_seconds"] > 0
    assert resources["read_bytes"] > 0


@pytest.mark.skipif(not WINDOWS, reason="Windows Job object")
async def test_closing_the_job_ends_a_member_that_outlived_the_run() -> None:
    # A detached grandchild leaves the parent chain entirely, so only job membership
    # still names it - which is exactly the straggler kill-on-close exists for.
    source = (
        "import subprocess, sys\n"
        "flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],"
        " creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,"
        " stderr=subprocess.DEVNULL)\n"
        "print(child.pid, flush=True)\n"
    )
    outcome = await run_bounded(
        python_argv(source),
        label="contained-straggler",
        timeout_seconds=120,
        output_limit=4096,
        containment=BACKGROUND_BATCH,
    )

    assert outcome.exit_code == 0, outcome.stderr
    straggler = int(outcome.stdout.strip())
    assert await _gone(straggler)


@pytest.mark.skipif(not WINDOWS, reason="Windows Job object")
def test_a_child_that_cannot_be_resumed_raises_for_the_caller_to_reap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(_handle: int) -> int:
        return -1073741823  # STATUS_UNSUCCESSFUL

    monkeypatch.setattr(background_containment, "_NtResumeProcess", refuse)
    child = psutil.Popen(
        [sys.executable, "-c", "pass"],
        creationflags=background_containment._CREATE_SUSPENDED,
    )
    try:
        prepared = PreparedSpawn([], 0, background_containment.MECHANISM_JOB)
        with pytest.raises(OSError, match="NtResumeProcess"):
            background_containment.attach(child.pid, prepared, BACKGROUND_BATCH)
        # Releasing the job on the way out ended the child it was holding.
        assert child.wait(30) is not None
    finally:
        with contextlib.suppress(psutil.NoSuchProcess):
            child.kill()
        child.wait(30)


# --------------------------------------------------------------------------- #
# Bounds on a contained run (every host)                                      #
# --------------------------------------------------------------------------- #


async def test_a_contained_run_that_stops_progressing_is_stopped_as_stalled() -> None:
    samples: list[ResourceSample] = []
    started = time.monotonic()
    outcome = await run_bounded(
        python_argv("import time\ntime.sleep(300)\n"),
        label="contained-stall",
        timeout_seconds=300,
        output_limit=4096,
        containment=BACKGROUND_BATCH,
        stall_seconds=3.0,
        on_sample=samples.append,
    )

    assert outcome.stalled is True
    assert outcome.timed_out is False
    assert outcome.exit_code is None
    # Stopped by the stall window, not by the 300s ceiling.
    assert time.monotonic() - started < 120
    # Samples were handed back to the caller's loop while it ran.
    assert samples and samples[-1].measured
    assert outcome.resources is not None


async def test_a_working_contained_run_is_not_mistaken_for_a_stall() -> None:
    # Busy for longer than several polls. The window is wide because the child runs
    # at idle/nice-19 and a gate saturating the host starves it between the OS's
    # anti-starvation boosts; a gap between two of those is the realistic worst case.
    source = "import time\nend = time.time() + 6\nwhile time.time() < end:\n    pass\nprint('ok')\n"
    outcome = await run_bounded(
        python_argv(source),
        label="contained-busy",
        timeout_seconds=300,
        output_limit=4096,
        containment=BACKGROUND_BATCH,
        stall_seconds=15.0,
    )

    assert outcome.exit_code == 0
    assert outcome.stalled is False
    assert outcome.stdout.strip() == b"ok"


async def test_the_ceiling_still_bounds_a_contained_run() -> None:
    outcome = await run_bounded(
        python_argv("import time\ntime.sleep(300)\n"),
        label="contained-ceiling",
        timeout_seconds=1.5,
        output_limit=4096,
        containment=BACKGROUND_BATCH,
    )

    assert outcome.timed_out is True
    assert outcome.stalled is False
    assert outcome.exit_code is None


async def test_an_uncontained_run_reports_no_resources() -> None:
    outcome = await run_bounded(
        python_argv("print('plain')"),
        label="plain",
        timeout_seconds=60,
        output_limit=4096,
    )
    assert outcome.exit_code == 0
    assert outcome.resources is None
    assert outcome.stalled is False


@pytest.mark.skipif(not WINDOWS, reason="Windows priority API")
@pytest.mark.parametrize("exit_code", [0, 259, None])
def test_priority_failures_are_ignored_only_after_a_confirmed_exit(monkeypatch, exit_code):
    import ctypes
    from ctypes import wintypes

    monkeypatch.setattr(
        background_containment, "_NtSetInformationProcess", lambda *args: 0xC0000022
    )

    def deny_memory(*args):
        ctypes.set_last_error(5)
        return False

    def read_exit(handle, result):
        if exit_code is None:
            return False
        ctypes.cast(result, ctypes.POINTER(wintypes.DWORD)).contents.value = exit_code
        return True

    monkeypatch.setattr(background_containment, "_SetProcessInformation", deny_memory)
    monkeypatch.setattr(background_containment, "_GetExitCodeProcess", read_exit)
    failures = background_containment.lower_process_priorities(123)
    expected = [] if exit_code == 0 else [
        "io_priority_failed:0xc0000022", "memory_priority_failed:5",
    ]
    assert failures == expected


@pytest.mark.skipif(not WINDOWS, reason="Windows priority API")
def test_terminating_member_needs_no_further_priority_or_exit_query(monkeypatch):
    monkeypatch.setattr(
        background_containment, "_NtSetInformationProcess", lambda *args: 0xC000010A
    )

    def unexpected(*args):
        raise AssertionError("the kernel already confirmed that this process is terminating")

    monkeypatch.setattr(background_containment, "_SetProcessInformation", unexpected)
    monkeypatch.setattr(background_containment, "_GetExitCodeProcess", unexpected)
    assert background_containment.lower_process_priorities(123) == []
