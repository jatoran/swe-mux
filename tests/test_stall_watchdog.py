"""The stall watchdog names the frame the loop was stuck in, from outside the loop.

Two things a lag number cannot say: *where* the thread was, and whether the cause
was Python on the loop or a native call holding the GIL somewhere else. The first
comes from a C watchdog that needs no GIL; the second from a canary thread whose
own lateness is the discriminator.
"""

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


def _wait_for_dump(path: Path, deadline: float) -> str:
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            if "Timeout (" in text:
                return text
        time.sleep(0.05)
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def test_a_blocked_loop_is_dumped_without_the_gil_and_explained_afterwards(
    tmp_path: Path,
) -> None:
    """The C watchdog fires while this thread holds the GIL in a busy loop.

    A Python thread could never have observed that: it needs the GIL the stall is
    holding. The canary is starved for the same reason, which is exactly the
    signal that distinguishes a native or GIL-bound stall from synchronous work on
    the loop thread.
    """
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=0.3)
    watchdog.start()
    try:
        watchdog.beat()
        # Hold the GIL, in this (main) thread, for longer than the threshold. A
        # pure-Python busy loop releases the GIL every switch interval, so the
        # canary would run; sys.setswitchinterval makes the hold real.
        import sys

        previous = sys.getswitchinterval()
        sys.setswitchinterval(5.0)
        try:
            started = time.perf_counter()
            while time.perf_counter() - started < 1.0:
                pass
        finally:
            sys.setswitchinterval(previous)
        lag = time.perf_counter() - started
        text = _wait_for_dump(watchdog.trace_path, time.monotonic() + 2.0)
        assert "Timeout (" in text, "the dump must fire while the loop never re-armed"
        assert "test_a_blocked_loop_is_dumped" in text, "and it names this frame"

        record = watchdog.explain(lag)
        assert record.duration_seconds == lag
        assert record.dumps >= 1
        assert any("test_a_blocked_loop_is_dumped" in frame for frame in record.main_thread), (
            record.main_thread
        )
        assert record.canary_starved is True, (
            "the canary thread could not run either, so this reads as a GIL-held stall"
        )
        assert record.trace_path == str(watchdog.trace_path)
        snapshot = watchdog.snapshot()
        assert snapshot["stalls_explained"] == 1
        assert snapshot["recent"][0]["main_leaf"] == record.main_leaf
        assert snapshot["armed"] is True
        after = watchdog.trace_path.read_text(encoding="utf-8", errors="replace")
        assert END_MARKER in after, "the marker is what the next explanation reads after"
    finally:
        watchdog.close()
    assert watchdog.snapshot()["armed"] is False


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
