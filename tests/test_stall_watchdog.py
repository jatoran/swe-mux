"""Safe stack sampling, honest GIL-starvation coverage, and bounded trace retention."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from pathlib import Path

import pytest

from swe_mux.host_metrics import ThreadCpuReader
from swe_mux.parked_threads import DB_LOCK_WAIT, parked, parked_threads
from swe_mux.stall_watchdog import (
    END_MARKER,
    CpuSample,
    ExternalStackDumper,
    StallWatchdog,
    classify_threads,
    describe_dump,
    diagnose,
    parse_faulthandler_dumps,
    parse_py_spy_dump,
    parse_stall_dumps,
)
from tests.support.settle import until

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


# -- the parked registry ---------------------------------------------------------


def test_a_parked_mark_lasts_exactly_as_long_as_its_block_and_nests() -> None:
    ident = threading.get_ident()
    assert ident not in parked_threads()
    with parked("outer"):
        assert parked_threads()[ident] == "outer"
        with parked(DB_LOCK_WAIT):
            assert parked_threads()[ident] == DB_LOCK_WAIT
        assert parked_threads()[ident] == "outer", "the inner wait restores the outer one"
    assert ident not in parked_threads()
    with pytest.raises(RuntimeError), parked("raises"):
        raise RuntimeError("boom")
    assert ident not in parked_threads(), "an exception still clears the mark"


PARKED_DUMP = """\
# capture=python_gil_required pid=7
Timeout (0:00:00)!
# ready_queue=37
Thread 0x00000001 (most recent call first):
  File "C:\\py\\asyncio\\windows_events.py", line 774 in _poll
Thread 0x0000000a (most recent call first):
  File "C:\\app\\swe_mux\\session.py", line 1447 in _normalize_tail_text
Thread 0x0000000b (most recent call first):
  File "C:\\app\\swe_mux\\win_process_files.py", line 241 in _run
Thread 0x0000000c [parked:open_file_names_inbox] (most recent call first):
  File "C:\\app\\swe_mux\\win_process_files.py", line 241 in _run
Thread 0x0000000d [parked:db_lock_wait] (most recent call first):
  File "C:\\app\\swe_mux\\sqlite_store.py", line 739 in run_sqlite_operation
Thread 0x0000000e (most recent call first):
  File "C:\\py\\concurrent\\futures\\thread.py", line 90 in _worker
"""


def test_the_capture_format_carries_parked_marks_and_the_ready_queue() -> None:
    (dump,) = parse_stall_dumps(PARKED_DUMP)
    assert dump.ready_queue == 37
    assert dump.parked == {0xC: "open_file_names_inbox", 0xD: DB_LOCK_WAIT}
    assert set(dump.threads) == {0x1, 0xA, 0xB, 0xC, 0xD, 0xE}
    assert parse_faulthandler_dumps(PARKED_DUMP) == [dump.threads], (
        "the legacy reader sees the same threads through the new header"
    )


def test_a_capture_fires_only_once_the_loop_is_a_threshold_late(tmp_path: Path) -> None:
    """Beats come every probe interval, so silence for `threshold` is not yet a stall.

    Before the interval was counted, a loop running half a second slow was captured
    at a 0.75 s threshold, and 35 dumps landed for four explained stalls - each one
    later read as the first dump of whichever stall came next.
    """
    now = [100.0]
    watchdog = StallWatchdog(
        tmp_path / "loop-stalls.log",
        threshold=3.0,
        beat_interval=0.5,
        monotonic=lambda: now[0],
        cpu_reader=_NoCpu(),
    )
    watchdog.start()
    try:
        watchdog._canary_stop.set()  # drive captures by hand, not from the thread
        watchdog.beat()
        now[0] = 103.2  # silent 3.2 s: only 2.7 s late
        watchdog._capture_if_stalled(now[0])
        assert "Timeout (" not in watchdog.trace_path.read_text(encoding="utf-8")
        now[0] = 103.6  # 3.1 s late: a stall
        watchdog._capture_if_stalled(now[0])
        (dump,) = parse_stall_dumps(watchdog.trace_path.read_text(encoding="utf-8"))
        assert dump.captured_at == pytest.approx(103.6)
    finally:
        watchdog.close()


def test_an_explanation_reads_only_the_dumps_captured_inside_its_stall() -> None:
    text = (
        "Timeout (0:00:00)!\n# captured_at=50.0\n"
        'Thread 0x00000001 (most recent call first):\n  File "a.py", line 1 in earlier\n'
        "Timeout (0:00:00)!\n# captured_at=103.6\n"
        'Thread 0x00000001 (most recent call first):\n  File "b.py", line 2 in this_one\n'
        "Timeout (0:00:00)!\n"
        'Thread 0x00000001 (most recent call first):\n  File "c.py", line 3 in legacy\n'
    )
    dumps = parse_stall_dumps(text, full=1, window=(100.0, 105.0))
    assert [dump.captured_at for dump in dumps] == [103.6, None], (
        "a stamped capture from another stretch is dropped; an unstamped one is kept"
    )
    assert dumps[0].threads[1][0][2] == "this_one", "the kept dump is the one parsed fully"


def test_threads_are_sorted_into_busy_blocked_parked_and_lock_waiters() -> None:
    (dump,) = parse_stall_dumps(PARKED_DUMP)
    names = {0xA: "asyncio_1", 0xB: "open-file-names", 0xC: "open-file-names", 0xD: "store"}
    result = classify_threads(
        dump, main_ident=0x1, names=names, cpu={0xA: 2.4, 0xB: 0.0}, window=4.0
    )
    assert result.main == ["_poll (windows_events.py:774)"]
    assert [item.name for item in result.busy] == ["asyncio_1"]
    assert result.busy[0].cpu_seconds == 2.4
    assert result.blocked == ["open-file-names"], (
        "an unmarked thread that used no CPU was blocked, not busy"
    )
    assert result.parked == 2, "one by its own mark, one by its idle leaf"
    assert result.db_lock_waiters == 1, "a lock waiter is counted, never blamed"


def test_without_cpu_data_every_unparked_thread_still_reads_busy() -> None:
    (dump,) = parse_stall_dumps(PARKED_DUMP)
    result = classify_threads(dump, main_ident=0x1, names={})
    assert {item.ident for item in result.busy} == {0xA, 0xB}
    assert result.blocked == []


def test_a_live_capture_writes_each_threads_mark_and_the_loop_ready_depth(
    tmp_path: Path,
) -> None:
    from collections import deque
    from types import SimpleNamespace
    from typing import Any, cast

    stop = threading.Event()
    marked = threading.Event()

    def waiter() -> None:
        with parked("test_inbox"):
            marked.set()
            while not stop.is_set():
                time.sleep(0.01)  # a leaf no idle rule knows: only the mark parks it

    thread = threading.Thread(target=waiter, name="test-waiter", daemon=True)
    thread.start()
    assert marked.wait(2)
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=0.2)
    watchdog.attach_loop(cast(Any, SimpleNamespace(_ready=deque(range(9)))))
    watchdog.start()
    try:
        text = _wait_for_dump(watchdog.trace_path, time.monotonic() + 3)
        dumps = parse_stall_dumps(text)
        assert dumps, "the unbeaten watchdog captured"
        assert dumps[0].parked.get(thread.ident or 0) == "test_inbox"
        assert dumps[0].ready_queue == 9
        record = watchdog.explain(0.5)
        assert all(item["ident"] != thread.ident for item in record.busy_threads)
        assert record.diagnosis["parked_threads"] >= 1
    finally:
        stop.set()
        watchdog.close()
        thread.join(timeout=2)


# -- CPU attribution and verdicts -------------------------------------------------


def test_a_thread_that_exited_inside_the_stall_still_counts_through_the_process_total(
    tmp_path: Path,
) -> None:
    reader = ThreadCpuReader()
    if not reader.supported:
        pytest.skip("per-thread CPU time is not readable on this host")
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=60.0, cpu_reader=reader)
    try:
        assert watchdog._sample_cpu(watchdog._monotonic()) is not None, "the baseline sample"
        burned = threading.Event()

        def burner() -> None:
            # Burn a fixed amount of this thread's own CPU, however loaded the host is.
            while time.thread_time() < 0.6:
                sum(range(2_000))
            burned.set()

        started = time.perf_counter()
        thread = threading.Thread(target=burner, name="test-burner", daemon=True)
        thread.start()
        assert burned.wait(60)
        thread.join(timeout=5)
        record = watchdog.explain(time.perf_counter() - started)
    finally:
        watchdog.close()
    diagnosis = record.diagnosis
    # The burner exited before the explanation, so no thread row carries its CPU;
    # the process total still does, and the loop is not charged with it.
    assert diagnosis["others_cpu_cores"] is not None
    assert diagnosis["others_cpu_cores"] * diagnosis["window_seconds"] >= 0.55
    assert diagnosis["loop_cpu_share"] < diagnosis["others_cpu_cores"], (
        "the loop thread slept while the burner ran"
    )


def test_a_live_burner_is_named_by_the_ranking(tmp_path: Path) -> None:
    reader = ThreadCpuReader()
    if not reader.supported:
        pytest.skip("per-thread CPU time is not readable on this host")
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=60.0, cpu_reader=reader)
    stop = threading.Event()
    burned = threading.Event()

    def burner() -> None:
        while not stop.is_set():
            sum(range(2_000))
            if time.thread_time() >= 0.6:
                burned.set()

    thread = threading.Thread(target=burner, name="test-burner", daemon=True)
    try:
        assert watchdog._sample_cpu(watchdog._monotonic()) is not None
        started = time.perf_counter()
        thread.start()
        assert burned.wait(60)
        record = watchdog.explain(time.perf_counter() - started)
    finally:
        stop.set()
        thread.join(timeout=5)
        watchdog.close()
    top = record.diagnosis["top_threads"]
    assert top and top[0]["name"] == "test-burner", top
    assert top[0]["cpu_seconds"] >= 0.55


def test_the_baseline_is_the_last_sample_before_the_stall(tmp_path: Path) -> None:
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", cpu_reader=_NoCpu())
    samples = [
        CpuSample(at=at, threads={}, names={}, process=0.0, host=None) for at in (1, 2, 3, 9)
    ]
    watchdog._cpu_samples.extend(samples)
    end = CpuSample(at=10.0, threads={}, names={}, process=0.0, host=None)
    assert watchdog._baseline(started=5.0, end=end) is samples[2]
    assert watchdog._sample_after(8.5) is samples[3], "the canary's sample just after the end"
    assert watchdog._sample_after(5.0) is None, "nothing within an interval of the end"
    watchdog._cpu_samples.clear()
    watchdog._cpu_samples.append(samples[3])
    assert watchdog._baseline(started=5.0, end=end) is None, (
        "one second inside a five-second stall measures too little of it"
    )
    watchdog._cpu_samples.appendleft(CpuSample(at=6.0, threads={}, names={}, process=0, host=None))
    assert watchdog._baseline(started=5.0, end=end).at == 6.0  # type: ignore[union-attr]


class _NoCpu(ThreadCpuReader):
    def __init__(self) -> None:
        self._windows = None
        self._psutil_process = None
        self.supported = False


@pytest.mark.parametrize(
    ("kwargs", "verdict"),
    [
        (
            {"window": 4.0, "loop_cpu": 3.2, "others_cpu": 0.1, "ready_queue_max": 5},
            "loop_blocking_call",
        ),
        (
            {"window": 4.0, "loop_cpu": 3.2, "others_cpu": 0.1, "ready_queue_max": 400},
            "loop_saturated",
        ),
        ({"window": 4.0, "loop_cpu": 0.4, "others_cpu": 3.0, "host_share": 0.5}, "gil_convoy"),
        ({"window": 4.0, "loop_cpu": 0.1, "others_cpu": 1.2, "host_share": 0.5}, "gil_convoy"),
        ({"window": 4.0, "loop_cpu": 0.2, "others_cpu": 0.2, "host_share": 0.97}, "host_starved"),
        ({"window": 4.0, "loop_cpu": 0.2, "others_cpu": 0.2, "host_share": 0.3}, "blocked_wait"),
        ({"window": 4.0, "loop_cpu": 0.2, "others_cpu": 0.2}, "blocked_wait"),
        ({"window": None, "loop_cpu": None, "others_cpu": None}, "unsampled"),
    ],
)
def test_every_stall_gets_a_verdict(kwargs: dict[str, object], verdict: str) -> None:
    assert diagnose(**kwargs).verdict == verdict  # type: ignore[arg-type]


def test_a_convoy_names_the_top_other_thread_and_a_busy_loop_names_itself() -> None:
    top = [
        {"name": "MainThread", "ident": 1, "cpu_seconds": 0.3, "share": 0.07},
        {"name": "asyncio_4", "ident": 9, "cpu_seconds": 2.9, "share": 0.72},
    ]
    top.sort(key=lambda item: -float(item["cpu_seconds"]))
    convoy = diagnose(window=4.0, loop_cpu=0.3, others_cpu=3.0, top_threads=top, loop_ident=1)
    assert convoy.gil_holder is not None
    assert convoy.gil_holder["name"] == "asyncio_4"
    assert convoy.gil_holder["source"] == "cpu"
    busy = diagnose(window=4.0, loop_cpu=3.6, others_cpu=0.0, loop_ident=1)
    assert busy.gil_holder == {"source": "cpu", "name": "MainThread", "ident": 1, "share": 0.9}
    idle = diagnose(window=4.0, loop_cpu=0.1, others_cpu=0.1, host_share=0.2)
    assert idle.gil_holder is None, "nobody ran, so nobody is named"


PY_SPY_OUTPUT = (
    'Process \x1b[33m\x1b[1m34260\x1b[0m: "python.exe" -m swe_mux\n'
    "Python v\x1b[1m3.12.11\x1b[0m (python.exe)\n"
    "\n"
    'Thread \x1b[33m\x1b[1m94092\x1b[0m (idle): "MainThread"\n'
    "    \x1b[32m_poll\x1b[0m (\x1b[36mwindows_events.py\x1b[0m:\x1b[2m774\x1b[0m)\n"
    'Thread \x1b[33m\x1b[1m57576\x1b[0m (active+gil): "asyncio_3"\n'
    "    \x1b[32m_collect_all\x1b[0m (\x1b[36mprocesses.py\x1b[0m:\x1b[2m1446\x1b[0m)\n"
    "    \x1b[32mrun\x1b[0m (\x1b[36mthread.py\x1b[0m:\x1b[2m59\x1b[0m)\n"
    'Thread 0x1F00 (active): "store"\n'
    "    execute (sqlite_store.py:744)\n"
)


def test_a_py_spy_dump_is_parsed_through_its_colour_codes() -> None:
    threads = parse_py_spy_dump(PY_SPY_OUTPUT)
    assert [item["name"] for item in threads] == ["MainThread", "asyncio_3", "store"]
    assert threads[1] == {
        "native_id": 57576,
        "state": "active+gil",
        "name": "asyncio_3",
        "frames": ["_collect_all (processes.py:1446)", "run (thread.py:59)"],
    }
    assert threads[2]["native_id"] == 0x1F00


def test_the_external_dump_runs_once_per_interval_and_names_the_gil_holder() -> None:
    import subprocess

    now = [100.0]
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=PY_SPY_OUTPUT, stderr="")

    dumper = ExternalStackDumper(
        "py-spy", source="path", min_interval=60.0, run=run, monotonic=lambda: now[0]
    )
    assert dumper.trigger(4321) is True
    result = dumper.result_since(99.0, wait=5.0)
    assert result is not None and result["status"] == "ok"
    assert calls == [["py-spy", "dump", "--pid", "4321", "--nonblocking"]]
    assert [item["name"] for item in result["threads"]] == ["asyncio_3", "store"], (
        "only active threads, the GIL holder first"
    )
    assert dumper.result_since(101.0, wait=0.0) is None, "a dump older than the stall is not its"
    now[0] = 130.0
    assert dumper.trigger(4321) is False, "rate-limited"
    now[0] = 161.0
    assert dumper.trigger(4321) is True
    assert dumper.result_since(160.0, wait=5.0) is not None
    diagnosis = diagnose(
        window=4.0, loop_cpu=0.2, others_cpu=3.0, external=dumper.result_since(160.0, wait=0)
    )
    assert diagnosis.gil_holder is not None
    assert diagnosis.gil_holder["source"] == "py-spy"
    assert diagnosis.gil_holder["name"] == "asyncio_3"


def test_a_failed_external_dump_is_reported_not_raised() -> None:
    import subprocess

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 15)

    dumper = ExternalStackDumper("py-spy", source="path", run=run, monotonic=lambda: 5.0)
    assert dumper.trigger(1)
    result = dumper.result_since(4.0, wait=5.0)
    assert result is not None and result["status"] == "error"
    assert dumper.status()["failures"] == 1


def test_the_external_dumper_resolves_from_the_environment(tmp_path: Path) -> None:
    assert ExternalStackDumper.from_environment({"MUX_STALL_PROFILER": "off"}).source == "off"
    missing = ExternalStackDumper.from_environment({"MUX_STALL_PROFILER": str(tmp_path / "x")})
    assert (missing.source, missing.available) == ("env_not_found", False)
    present = tmp_path / "py-spy-copy"
    present.write_text("", encoding="utf-8")
    configured = ExternalStackDumper.from_environment({"MUX_STALL_PROFILER": str(present)})
    assert (configured.source, configured.executable) == ("env", str(present))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 3.0), ("1.5", 1.5), ("0.1", 3.0), ("600", 3.0), ("soon", 3.0), ("nan", 3.0)],
)
def test_the_threshold_override_is_bounded(raw: str, expected: float) -> None:
    from swe_mux.stall_watchdog import threshold_from_environment

    assert threshold_from_environment({"MUX_STALL_THRESHOLD_SECONDS": raw}) == expected


def test_a_stalled_loop_never_starts_an_external_dump_without_one_configured(
    tmp_path: Path,
) -> None:
    watchdog = StallWatchdog(tmp_path / "loop-stalls.log", threshold=0.2)
    watchdog.start()
    try:
        _wait_for_dump(watchdog.trace_path, time.monotonic() + 3)
        status = watchdog.profiler_status()["external_dump"]
        assert status["available"] is False and status["source"] == "disabled"
    finally:
        watchdog.close()


def test_the_lag_probe_keeps_beating_while_a_stall_is_explained() -> None:
    """An explanation can wait seconds for an external dump; the probe must not.

    A probe parked on the explanation stopped beating, so the canary captured a
    running loop as stalled (a 1.02 s stall recorded five dumps) and the lag
    window went unsampled for as long as the explanation took.
    """
    from swe_mux.server import _loop_lag_loop
    from swe_mux.stall_watchdog import StallRecord

    release = threading.Event()

    class Monitor:
        def __init__(self) -> None:
            self.lags = iter([2.0, 0.0, 0.0, 0.0, 0.0, 3.0])
            self.observed: list[float] = []

        async def sample(self) -> float:
            await asyncio.sleep(0.005)
            return next(self.lags, 0.0)

        def observe(self, lag: float) -> None:
            self.observed.append(lag)

    class Watchdog:
        threshold = 1.0

        def __init__(self) -> None:
            self.beats = 0
            self.explained: list[float] = []

        def beat(self) -> None:
            self.beats += 1

        def clock(self) -> float:
            return time.perf_counter()

        def explain(self, lag: float, *, ended: float) -> StallRecord:
            assert release.wait(10), "the test releases the explanation"
            self.explained.append(lag)
            return StallRecord(
                started_at=0.0,
                duration_seconds=lag,
                canary_starved=False,
                dumps=0,
                main_thread=[],
                busy_threads=[],
            )

    async def main() -> tuple[int, list[float]]:
        monitor, watchdog = Monitor(), Watchdog()
        task = asyncio.create_task(_loop_lag_loop(monitor, watchdog, None))  # type: ignore[arg-type]
        try:
            await until(lambda: watchdog.beats >= 6, seconds=10, what="probe beats")
            beats_while_blocked = watchdog.beats
            assert watchdog.explained == [], "the first explanation is still blocked"
            release.set()
            await until(lambda: len(watchdog.explained) >= 2, seconds=10, what="explanations")
            return beats_while_blocked, list(watchdog.explained)
        finally:
            release.set()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    beats, explained = asyncio.run(main())
    assert beats >= 6, "the probe went on sampling while the explanation waited"
    assert explained == [2.0, 3.0], "a stall that ended meanwhile is explained next, in order"


def test_the_stall_line_leads_with_the_verdict_and_lists_only_busy_threads() -> None:
    from swe_mux.server import _loop_stall_line
    from swe_mux.stall_watchdog import StallRecord

    record = StallRecord(
        started_at=0.0,
        duration_seconds=4.2,
        canary_starved=False,
        dumps=1,
        main_thread=["_poll (windows_events.py:774)"],
        busy_threads=[
            {"name": "asyncio_3", "ident": 9, "frames": ["x (y.py:1)"], "cpu_seconds": 2.9}
        ],
        host={"memory_percent": 80.0, "process_count": 1200},
        diagnosis=diagnose(
            window=4.4,
            loop_cpu=0.3,
            others_cpu=3.1,
            host_share=0.96,
            top_threads=[{"name": "asyncio_3", "ident": 9, "cpu_seconds": 2.9, "share": 0.66}],
            loop_ident=1,
        ).as_dict(),
    )
    line = _loop_stall_line(record)
    assert line.startswith("loop_stall duration_s=4.20 verdict=gil_convoy ")
    assert "gil=asyncio_3(cpu)" in line
    assert "top=[asyncio_3:0.66]" in line
    assert "busy=[asyncio_3(2.9s):x (y.py:1)]" in line
    assert "host_cpu=0.96" in line
