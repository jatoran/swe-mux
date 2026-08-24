"""The shared bounded runner, against real child processes.

Every claim this module makes is about what happens to a *process* - it is killed,
its descendants are killed, its output stops being read at a cap - so these spawn
real ones. A mocked `create_subprocess_exec` would assert that the code calls the
functions it calls, which is the one thing that was never in doubt.
"""

from __future__ import annotations

import asyncio
import os
import sys

import psutil
import pytest

from swe_mux import bounded_subprocess
from swe_mux.bounded_subprocess import bounded_read, run_bounded


def python_argv(source: str) -> tuple[str, ...]:
    return (sys.executable, "-c", source)


@pytest.fixture(autouse=True)
def _quiet_rate_limiter() -> None:
    bounded_subprocess.reset_log_windows()


class FakeStream:
    """An `asyncio.StreamReader` shaped just enough for `bounded_read`."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _size: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


async def test_bounded_read_keeps_the_head_and_the_tail_within_the_limit() -> None:
    stream = FakeStream([b"A" * 100, b"B" * 100, b"C" * 100])

    data, truncated = await bounded_read(stream, 100, label="probe")  # type: ignore[arg-type]

    assert truncated is True
    # Both ends survive: the head carries a command's banner and first error, the
    # tail carries how it ended, and a cap that kept one of them would have to be
    # argued for per call site.
    assert data.startswith(b"A" * 50)
    assert data.endswith(b"C" * 50)
    assert b"probe output omitted" in data
    # The retained bytes are bounded by the limit plus the one marker line.
    assert len(data) < 100 + 80


async def test_bounded_read_leaves_an_under_limit_capture_exactly_as_written() -> None:
    stream = FakeStream([b"one\n", b"two\n"])

    data, truncated = await bounded_read(stream, 1024, label="probe")  # type: ignore[arg-type]

    assert (data, truncated) == (b"one\ntwo\n", False)


async def test_an_observer_that_raises_does_not_abandon_the_pipe() -> None:
    seen: list[int] = []

    def observer(chunk: bytes) -> None:
        seen.append(len(chunk))
        raise RuntimeError("the observer is the buggy one here")

    stream = FakeStream([b"one\n", b"two\n"])

    data, truncated = await bounded_read(
        stream, 1024, label="probe", on_chunk=observer  # type: ignore[arg-type]
    )

    assert (data, truncated) == (b"one\ntwo\n", False)
    assert seen == [4, 4]


async def test_output_over_the_cap_is_truncated_while_reading_not_after() -> None:
    """The cap bounds memory, which is what `usage.py` buffering first did not."""
    outcome = await run_bounded(
        python_argv("import sys\nsys.stdout.write('x' * 200000)\n"),
        label="flood",
        timeout_seconds=60,
        output_limit=4096,
    )

    assert outcome.exit_code == 0
    assert outcome.stdout_truncated is True
    assert outcome.truncated is True
    assert len(outcome.stdout) < 4096 + 200
    assert b"flood output omitted" in outcome.stdout


async def test_stderr_is_captured_separately_and_capped_on_its_own() -> None:
    outcome = await run_bounded(
        python_argv(
            "import sys\nsys.stdout.write('out')\nsys.stderr.write('e' * 50000)\n"
        ),
        label="split",
        timeout_seconds=60,
        output_limit=1024 * 1024,
        stderr_limit=2048,
    )

    assert outcome.stdout == b"out"
    assert outcome.stdout_truncated is False
    assert outcome.stderr_truncated is True
    assert outcome.stderr.startswith(b"e")


async def test_merge_stderr_folds_both_streams_into_stdout() -> None:
    outcome = await run_bounded(
        python_argv("import sys\nsys.stderr.write('diagnostic')\nsys.stderr.flush()\n"),
        label="merged",
        timeout_seconds=60,
        output_limit=1024,
        merge_stderr=True,
    )

    assert outcome.stderr == b""
    assert b"diagnostic" in outcome.stdout


async def test_a_timeout_reports_no_exit_code_and_kills_the_child() -> None:
    outcome = await run_bounded(
        python_argv("import time\nprint('before', flush=True)\ntime.sleep(120)\n"),
        label="sleeper",
        timeout_seconds=1.0,
        output_limit=1024,
    )

    assert outcome.timed_out is True
    # `None` and not zero: "the command did not run to completion" and "it
    # succeeded" are the two answers that must never be conflated.
    assert outcome.exit_code is None
    assert b"before" in outcome.stdout


async def _has_gone(process: psutil.Process, *, deadline_seconds: float = 20.0) -> bool:
    """Whether `process` is gone, waiting for the kill to land rather than sleeping."""
    for _ in range(int(deadline_seconds / 0.05)):
        try:
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_cancelling_the_caller_reaps_the_whole_process_tree() -> None:
    """The gap the audit found: all three callers reaped on timeout, none on cancel."""
    source = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(300)\n"
    )

    started = asyncio.Event()
    pids: list[int] = []

    async def run() -> None:
        # Watch the pid land before cancelling, so the cancel cannot race the spawn.
        def note(chunk: bytes) -> None:
            for line in chunk.split():
                if line.strip().isdigit():
                    pids.append(int(line))
                    started.set()

        await run_bounded(
            python_argv(source),
            label="tree",
            timeout_seconds=300,
            output_limit=4096,
            on_chunk=note,
        )

    task = asyncio.create_task(run())
    await asyncio.wait_for(started.wait(), 30)
    grandchild = psutil.Process(pids[0])
    parent = grandchild.parent()
    assert parent is not None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Both generations are gone. `process.kill()` alone would have left the
    # grandchild alive holding the daemon's pipes.
    assert await _has_gone(parent)
    assert await _has_gone(grandchild)


async def test_a_program_that_cannot_be_started_raises_rather_than_reporting_zero() -> None:
    with pytest.raises(OSError):
        await run_bounded(
            ("swe-mux-no-such-program-anywhere",),
            label="missing",
            timeout_seconds=5,
            output_limit=1024,
        )


async def test_env_of_none_inherits_and_an_explicit_env_replaces() -> None:
    source = "import os\nprint(os.environ.get('MUX_BOUNDED_PROBE', 'absent'))\n"
    os.environ["MUX_BOUNDED_PROBE"] = "inherited"
    try:
        inherited = await run_bounded(
            python_argv(source), label="env", timeout_seconds=60, output_limit=1024
        )
        replaced = await run_bounded(
            python_argv(source),
            label="env",
            timeout_seconds=60,
            output_limit=1024,
            env={**os.environ, "MUX_BOUNDED_PROBE": "explicit"},
        )
    finally:
        os.environ.pop("MUX_BOUNDED_PROBE", None)

    assert b"inherited" in inherited.stdout
    assert b"explicit" in replaced.stdout


async def test_stdin_is_closed_so_a_prompting_program_fails_instead_of_hanging() -> None:
    outcome = await run_bounded(
        python_argv("import sys\nprint(repr(sys.stdin.read()))\n"),
        label="stdin",
        timeout_seconds=30,
        output_limit=1024,
    )

    assert outcome.timed_out is False
    assert b"''" in outcome.stdout


async def test_repeated_warnings_for_one_label_are_rate_limited() -> None:
    """A 4s Git query on a 5s poll would otherwise write 720 warnings an hour."""
    lines: list[str] = []

    def capture(message: str, *args: object) -> None:
        lines.append(message % args)

    original = bounded_subprocess.log.warning
    bounded_subprocess.log.warning = capture  # type: ignore[method-assign]
    try:
        for _ in range(4):
            bounded_subprocess._rate_limited("timeout", "git", "probe label=%s", "git")
        bounded_subprocess._rate_limited("timeout", "other", "probe label=%s", "other")
    finally:
        bounded_subprocess.log.warning = original  # type: ignore[method-assign]

    # One line for `git` and one for `other`; the three suppressed `git` lines are
    # counted rather than written, and surface on the next window.
    assert lines == ["probe label=git", "probe label=other"]
    assert bounded_subprocess._log_windows[("git", "timeout")][1] == 3
