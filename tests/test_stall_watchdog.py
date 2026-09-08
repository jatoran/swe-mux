"""Safe stack sampling, honest GIL-starvation coverage, and bounded trace retention."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from swe_mux.stall_watchdog import (
    END_MARKER,
    StallWatchdog,
    describe_dump,
    parse_faulthandler_dumps,
)

SAMPLE_DUMP = """\
# watchdog armed threshold=3.0s at=2026-09-01 21:22:00
Timeout (0:00:03)!
Thread 0x00001234 (most recent call first):
  File "C:\\app\\_internal\\swe_mux\\processes.py", line 1446 in _collect_all
  File "C:\\py\\concurrent\\futures\\thread.py", line 59 in run
  File "C:\\py\\concurrent\\futures\\thread.py", line 93 in _worker
Thread 0x00000042 (most recent call first):
  File "C:\\py\\concurrent\\futures\\thread.py", line 90 in _worker
  File "C:\\py\\threading.py", line 1012 in run
Thread 0x00000001 (most recent call first):
  File "C:\\py\\asyncio\\windows_events.py", line 774 in _poll
  File "C:\\py\\asyncio\\windows_events.py", line 445 in select
  File "C:\\py\\asyncio\\base_events.py", line 1961 in _run_once
Timeout (0:00:06)!
Thread 0x00000001 (most recent call first):
  File "C:\\app\\_internal\\swe_mux\\session.py", line 1447 in _normalize_tail_text
  File "C:\\app\\_internal\\swe_mux\\session.py", line 1495 in pty_tail_explain
"""


def test_parse_splits_faulthandler_output_into_dumps_leaf_first() -> None:
    dumps = parse_faulthandler_dumps(SAMPLE_DUMP)
    assert len(dumps) == 2, "one dump per Timeout header; the banner belongs to none"
    first, second = dumps
    assert set(first) == {0x1234, 0x42, 0x1}
    assert first[0x1234][0] == (
        "C:\\app\\_internal\\swe_mux\\processes.py",
        "1446",
        "_collect_all",
    ), "frames are kept in faulthandler's order, most recent call first"
    assert second[0x1][0][2] == "_normalize_tail_text"


def test_describe_names_the_main_thread_and_drops_parked_workers() -> None:
    dumps = parse_faulthandler_dumps(SAMPLE_DUMP)
    main, busy = describe_dump(dumps[0], main_ident=0x1, names={0x1234: "asyncio_7"})
    assert main[0] == "_poll (windows_events.py:774)"
    assert [item.name for item in busy] == ["asyncio_7"], (
        "the idle executor thread parked in _worker is noise, the sampling pass is not"
    )
    assert busy[0].frames[0] == "_collect_all (processes.py:1446)"
    assert busy[0].ident == 0x1234


def test_parse_tolerates_text_that_is_not_a_dump() -> None:
    assert parse_faulthandler_dumps("") == []
    assert parse_faulthandler_dumps(f"{END_MARKER} duration_s=4.0\nnonsense\n") == []


def test_sampler_never_arms_native_frame_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import faulthandler

    def unsafe(*args: object, **kwargs: object) -> None:
        pytest.fail("automatic native stack traversal can crash the interpreter")

    monkeypatch.setattr(faulthandler, "dump_traceback_later", unsafe)
    monkeypatch.setattr(faulthandler, "cancel_dump_traceback_later", unsafe)
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=0.1)
    watchdog.start()
    try:
        text = _wait_for_dump(watchdog.trace_path, time.monotonic() + 2)
        assert "test_sampler_never_arms_native_frame_traversal" in text
        watchdog.beat()
        assert watchdog.explain(0.2).dumps > 0
    finally:
        watchdog.close()


def test_heartbeat_does_not_wait_for_diagnostic_io(tmp_path: Path) -> None:
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", monotonic=lambda: 42.0)
    finished = threading.Event()

    def beat() -> None:
        watchdog.beat()
        finished.set()

    with watchdog._trace_lock:
        thread = threading.Thread(target=beat)
        thread.start()
        assert finished.wait(1), "the event loop must not wait for trace-file locks"
    thread.join(timeout=1)
    assert watchdog._last_beat == 42.0


def _wait_for_dump(path: Path, deadline: float) -> str:
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            if "Timeout (" in text:
                return text
        time.sleep(0.05)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def test_a_gil_held_stall_reports_missing_stack_coverage(tmp_path: Path) -> None:
    import sys

    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=0.3)
    watchdog.start()
    try:
        deadline = time.monotonic() + 2.0
        while watchdog._canary_sleeping_since is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert watchdog._canary_sleeping_since is not None
        previous = sys.getswitchinterval()
        sys.setswitchinterval(5.0)
        try:
            started = time.perf_counter()
            while time.perf_counter() - started < 1.0:
                pass
            lag = time.perf_counter() - started
            watchdog.beat()  # Resume before releasing the GIL: no post-stall fake sample.
        finally:
            sys.setswitchinterval(previous)
        record = watchdog.explain(lag)
        assert record.canary_starved is True
        assert record.dumps == 0
        assert record.main_thread == []
        assert watchdog.snapshot()["gil_held_stacks_available"] is False
        assert END_MARKER in watchdog.trace_path.read_text()
    finally:
        watchdog.close()
    assert watchdog.snapshot()["armed"] is False


def test_a_canary_still_asleep_past_its_due_time_counts_as_starved(tmp_path: Path) -> None:
    """The record the history cannot hold yet: the canary has not woken.

    ``explain()`` runs as soon as the loop resumes, and the canary the stall
    starved is still queued for the GIL at that moment (macOS CI hit exactly
    this: a real 1.0s GIL hold, ``canary_starved=False``). Its current sleep's
    start is enough to know how late it already is.
    """
    now = 100.0
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=60.0, monotonic=lambda: now)
    # No thread: the sleep-start is set by hand, as the canary would have just
    # before the stall took the GIL, and never cleared because it has not run.
    watchdog._canary_sleeping_since = 99.0
    assert watchdog.canary_starved_since(started=99.0, minimum=0.5) is True, (
        "due at 99.25, still asleep at 100.0: 0.75s overdue"
    )
    assert watchdog.canary_starved_since(started=99.0, minimum=0.8) is False, (
        "not yet overdue by the minimum the stall's length demands"
    )
    watchdog._canary_sleeping_since = None
    assert watchdog.canary_starved_since(started=99.0, minimum=0.5) is False, (
        "between sleeps, only the recorded history speaks"
    )


def test_a_stall_that_blocks_only_the_loop_leaves_the_canary_running(
    tmp_path: Path,
) -> None:
    """Synchronous work on the loop thread with the GIL released between bytecodes.

    The canary keeps its cadence, so the record says the loop thread itself was
    the one occupied - the dump's main-thread frames are then the answer.
    """
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=0.2)
    watchdog.start()
    try:
        watchdog.beat()
        # Give the canary thread a moment to establish its cadence.
        time.sleep(0.3)
        started = time.perf_counter()
        while time.perf_counter() - started < 0.6:
            pass  # the interpreter switches threads every 5 ms; the canary runs
        lag = time.perf_counter() - started
        _wait_for_dump(watchdog.trace_path, time.monotonic() + 1.5)
        record = watchdog.explain(lag)
        assert record.canary_starved is False
        assert record.dumps >= 1
    finally:
        watchdog.close()


def test_explain_reads_only_dumps_written_since_the_last_explanation(tmp_path: Path) -> None:
    path = tmp_path / "loop-stalls.log"
    watchdog = StallWatchdog(path, threshold=60.0)
    watchdog.start()
    try:
        # A dump the watchdog did not write itself, standing in for a previous stall.
        main_ident = threading.main_thread().ident or 0
        with open(path, "ab") as handle:
            handle.write(
                (
                    "Timeout (0:01:00)!\n"
                    f"Thread 0x{main_ident:08x} (most recent call first):\n"
                    '  File "x.py", line 1 in first_stall\n'
                ).encode()
            )
        first = watchdog.explain(61.0)
        assert first.main_thread == ["first_stall (x.py:1)"]
        second = watchdog.explain(62.0)
        assert second.dumps == 0 and second.main_thread == [], (
            "the same dump must not explain two stalls"
        )
    finally:
        watchdog.close()


def test_trace_file_rotates_once_over_its_budget(tmp_path: Path) -> None:
    path = tmp_path / "loop-stalls.log"
    watchdog = StallWatchdog(path, threshold=60.0, rotate_bytes=200)
    watchdog.start()
    try:
        with open(path, "ab") as handle:
            handle.write(b"x" * 400)
        watchdog.explain(61.0)
        assert path.with_name("loop-stalls.log.1").exists()
        assert path.stat().st_size < 200
        assert watchdog.snapshot()["armed"] is True, "rotation re-arms the dump"
    finally:
        watchdog.close()


@pytest.mark.parametrize("threshold", [0.5, 3.0])
def test_snapshot_reports_the_threshold_and_trace_path_before_any_stall(
    tmp_path: Path, threshold: float
) -> None:
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=threshold)
    snapshot = watchdog.snapshot()
    assert snapshot["threshold_seconds"] == threshold
    assert snapshot["recent"] == []
    assert snapshot["armed"] is False
