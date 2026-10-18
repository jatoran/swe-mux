"""Executor work is accounted per callable, and a stall names the work that burned."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest

from swe_mux import executor_accounting as accounting
from swe_mux.executor_accounting import (
    OVERFLOW_LABEL,
    AccountedThreadPoolExecutor,
    WorkLedger,
    work_label,
)
from swe_mux.sqlite_store import run_sqlite_operation

ROOT = Path(__file__).resolve().parents[1]


def plain_function() -> None:
    return None


class Store:
    def method(self) -> None:
        return None

    def submits(self) -> object:
        def op() -> None:
            return None

        return op


def test_labels_name_the_callable_through_every_wrapper() -> None:
    store = Store()
    assert work_label(plain_function) == "plain_function"
    assert work_label(store.method) == "Store.method"
    assert work_label(functools.partial(store.method)) == "Store.method"
    import contextvars

    # `asyncio.to_thread` submits partial(Context.run, func, *args).
    to_thread_shape = functools.partial(contextvars.copy_context().run, store.method)
    assert work_label(to_thread_shape) == "Store.method"
    op = store.submits()
    assert work_label(op) == "Store.submits", "anonymous store closures take their method's name"
    assert (
        work_label(run_sqlite_operation, (None, None, op)) == "run_sqlite_operation:Store.submits"
    ), "every store submission would otherwise share one label"


def _burn(seconds: float) -> None:
    started = time.thread_time()
    while time.thread_time() - started < seconds:
        sum(range(2_000))


def test_cpu_is_accounted_to_the_label_that_used_it() -> None:
    ledger = WorkLedger()
    executor = AccountedThreadPoolExecutor(max_workers=1, ledger=ledger)
    try:
        before = time.perf_counter()
        executor.submit(_burn, 0.1).result(timeout=30)
        executor.submit(plain_function).result(timeout=30)
        after = time.perf_counter()
    finally:
        executor.shutdown(wait=True)
    snapshot = ledger.snapshot()
    top = snapshot["since_start"][0]
    assert top["label"] == "_burn" and top["count"] == 1
    assert top["cpu_seconds"] >= 0.09
    assert {row["label"] for row in snapshot["recent"]} == {"_burn", "plain_function"}
    window = ledger.window(before, after)
    assert window["by_label"]["_burn"][1] >= 0.09
    (per_thread,) = window["by_thread"].values()
    assert per_thread["_burn"] >= 0.09
    assert ledger.window(after + 1, after + 2)["by_label"] == {}, "outside the window"


def test_an_item_straddling_the_window_counts_its_overlapping_share() -> None:
    ledger = WorkLedger()
    # (ended, started, label, ident, cpu): four seconds of wall, four of CPU.
    ledger._recent.append((10.0, 6.0, "straddler", 1, 4.0))
    assert ledger.window(8.0, 9.0)["by_label"]["straddler"] == [1, pytest.approx(1.0)]
    assert ledger.window(5.0, 7.0)["by_label"]["straddler"] == [1, pytest.approx(1.0)], (
        "an item that finished after the window still names what ran inside it"
    )
    assert ledger.window(11.0, 12.0)["by_label"] == {}


def test_work_still_running_is_visible_by_thread() -> None:
    ledger = WorkLedger()
    release = threading.Event()
    executor = AccountedThreadPoolExecutor(max_workers=1, ledger=ledger)
    try:
        future = executor.submit(release.wait, 10)
        deadline = time.monotonic() + 5
        running: dict[int, tuple[str, float]] = {}
        while not running and time.monotonic() < deadline:
            running = ledger.window(0, time.perf_counter())["running"]
            time.sleep(0.01)
        assert [label for label, _since in running.values()] == ["Event.wait"]
        release.set()
        future.result(timeout=5)
        assert ledger.window(0, time.perf_counter())["running"] == {}
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_labels_past_the_cap_fold_into_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounting, "MAX_LABELS", 2)
    ledger = WorkLedger()
    for label in ("a", "b", "c", "d"):
        ledger.end(label, *ledger.begin(label))
    labels = {row["label"] for row in ledger.snapshot()["since_start"]}
    assert labels == {"a", "b", OVERFLOW_LABEL}


def test_to_thread_on_the_accounted_default_executor_names_the_function() -> None:
    ledger = WorkLedger()

    async def main() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(AccountedThreadPoolExecutor(max_workers=2, ledger=ledger))
        await asyncio.to_thread(Store().method)

    asyncio.run(main())
    assert [row["label"] for row in ledger.snapshot()["since_start"]] == ["Store.method"]


def test_a_pool_reports_its_peak_occupancy_and_how_long_work_waited() -> None:
    """What sizing a pool needs: how many ran at once, how many queued, how long."""
    ledger = WorkLedger()
    pool = AccountedThreadPoolExecutor(
        max_workers=2, thread_name_prefix="sizing", ledger=ledger
    )
    release = threading.Event()
    try:
        blocked = [pool.submit(release.wait, 10) for _ in range(2)]
        queued = [pool.submit(plain_function) for _ in range(3)]
        assert queued[-1].cancel(), "a queued item can still be cancelled"
        reading = pool.occupancy()
        assert reading["running"] == 2 and reading["queued"] == 2
        time.sleep(0.05)
        release.set()
        for future in (*blocked, *queued[:-1]):
            future.result(timeout=5)
    finally:
        release.set()
        pool.shutdown(wait=True)

    (reading,) = [row for row in ledger.snapshot()["pools"] if row["name"] == "sizing"]
    assert reading["max_workers"] == 2
    assert (reading["running"], reading["queued"]) == (0, 0), "the cancelled item is not queued"
    assert reading["peak_running"] == 2
    # The three behind the blocked pair, plus however many of that pair a worker
    # had not picked up yet when the next was submitted.
    assert 3 <= reading["peak_queued"] <= 5
    assert reading["submitted"] == 5
    assert reading["max_wait_ms"] >= 40, "the queued items waited for the blocked ones"
    assert reading["wait_ms"]["p99"] >= reading["wait_ms"]["p50"]


def _held_pool(
    ledger: WorkLedger, *, shared: bool = False, name: str = "held"
) -> tuple[AccountedThreadPoolExecutor, threading.Event, list[Future[Any]]]:
    """A one-worker pool whose worker is busy, with one more item waiting behind it."""
    pool = AccountedThreadPoolExecutor(
        max_workers=1, thread_name_prefix=name, ledger=ledger, shared=shared
    )
    release = threading.Event()
    running = pool.submit(release.wait, 30)
    deadline = time.monotonic() + 10
    while pool.pressure().running != 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    waiting = pool.submit(plain_function)
    return pool, release, [running, waiting]


def test_a_pool_says_how_long_its_oldest_item_has_waited_and_what_holds_it() -> None:
    ledger = WorkLedger()
    pool, release, futures = _held_pool(ledger)
    try:
        later = time.perf_counter() + 7.0
        pressure = pool.pressure(later)
        assert (pressure.running, pressure.queued) == (1, 1)
        assert pressure.oldest_wait >= 7.0
        assert [label for label, _seconds in pressure.longest_running] == ["Event.wait"]
        assert pressure.saturated, "every worker busy and the oldest waited past the bound"
        assert not pool.pressure().saturated, "a wait of milliseconds is not saturation"
        reading = pool.occupancy()
        assert reading["longest_running"][0]["label"] == "Event.wait"
    finally:
        release.set()
        for future in futures:
            future.result(timeout=10)
        pool.shutdown(wait=True)
    assert pool.pressure().queued == 0
    assert pool.pressure().longest_running == ()


def test_the_monitor_reports_a_saturation_once_restates_it_and_says_when_it_clears(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The 2026-10-18 outage logged nothing: the loop turned while the pool starved."""
    ledger = WorkLedger()
    pool, release, futures = _held_pool(ledger, name="starved")
    offset = {"seconds": 0.0}
    monitor = accounting.SaturationMonitor(
        ledger, clock=lambda: time.perf_counter() + offset["seconds"]
    )
    try:
        with caplog.at_level(logging.INFO, logger="swe_mux.executor_accounting"):
            monitor.check()
            offset["seconds"] = 10.0
            monitor.check()
            monitor.check()
            offset["seconds"] = 10.0 + accounting.SATURATION_REPEAT_SECONDS + 1
            monitor.check()
            release.set()
            for future in futures:
                future.result(timeout=10)
            monitor.check()
    finally:
        release.set()
        pool.shutdown(wait=True)
    messages = [record.getMessage() for record in caplog.records]

    def lines(event: str) -> list[str]:
        return [m for m in messages if m.startswith(f"{event} pool=starved")]

    started = lines("executor_saturated")
    assert len(started) == 1 and "longest=Event.wait" in started[0]
    assert len(lines("executor_still_saturated")) == 1
    assert len(lines("executor_saturation_cleared")) == 1
    assert monitor.saturations == 1


def test_a_long_item_on_a_shared_pool_is_named_once(caplog: pytest.LogCaptureFixture) -> None:
    ledger = WorkLedger()
    pool, release, futures = _held_pool(ledger, shared=True, name="shared")
    private, private_release, private_futures = _held_pool(ledger, name="private")
    offset = {"seconds": accounting.LONG_RUNNING_SECONDS + 5}
    monitor = accounting.SaturationMonitor(
        ledger, clock=lambda: time.perf_counter() + offset["seconds"]
    )
    try:
        with caplog.at_level(logging.WARNING, logger="swe_mux.executor_accounting"):
            monitor.check()
            offset["seconds"] += 30
            monitor.check()
    finally:
        release.set()
        private_release.set()
        for future in (*futures, *private_futures):
            future.result(timeout=10)
        pool.shutdown(wait=True)
        private.shutdown(wait=True)
    long_running = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("executor_item_long_running")
    ]
    assert len(long_running) == 1, "named once, on the shared pool only"
    assert "pool=shared" in long_running[0] and "label=Event.wait" in long_running[0]


def test_the_monitor_runs_on_its_own_thread_and_stops() -> None:
    monitor = accounting.SaturationMonitor(WorkLedger(), interval=0.01)
    monitor.start()
    try:
        assert any(thread.name == "executor-saturation-monitor" for thread in threading.enumerate())
    finally:
        monitor.stop(timeout=5)
    assert not any(thread.name == "executor-saturation-monitor" for thread in threading.enumerate())


def test_every_daemon_executor_is_accounted() -> None:
    """A new bare executor would put its work back beyond the stall diagnosis's reach."""
    construction = re.compile(r"(?<![\w.])ThreadPoolExecutor\(")
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "src" / "swe_mux").rglob("*.py"))
        if path.name != "executor_accounting.py"
        and construction.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"construct AccountedThreadPoolExecutor instead: {offenders}"
    serve = (ROOT / "src" / "swe_mux" / "__main__.py").read_text(encoding="utf-8")
    assert "set_default_executor(" in serve and "AccountedThreadPoolExecutor(" in serve


def test_a_stall_names_the_executor_work_that_burned(tmp_path: Path) -> None:
    from swe_mux.host_metrics import ThreadCpuReader
    from swe_mux.stall_watchdog import StallWatchdog

    reader = ThreadCpuReader()
    if not reader.supported:
        pytest.skip("per-thread CPU time is not readable on this host")
    ledger = WorkLedger()
    watchdog = StallWatchdog(
        tmp_path / "loop-stalls.log", threshold=60.0, cpu_reader=reader, work_ledger=ledger
    )
    executor = AccountedThreadPoolExecutor(
        max_workers=1, thread_name_prefix="test-work", ledger=ledger
    )
    try:
        # A warm worker thread exists before the baseline, as in a running daemon.
        executor.submit(plain_function).result(timeout=30)
        assert watchdog._sample_cpu(watchdog.clock()) is not None
        started = watchdog.clock()
        executor.submit(_burn, 0.6).result(timeout=60)
        record = watchdog.explain(watchdog.clock() - started)
    finally:
        executor.shutdown(wait=True)
        watchdog.close()
    diagnosis = record.diagnosis
    assert diagnosis["top_work"][0]["label"] == "_burn"
    assert diagnosis["top_work"][0]["cpu_seconds"] >= 0.55
    worker = next(item for item in diagnosis["top_threads"] if item["name"].startswith("test-work"))
    assert worker["work"] == "_burn", "the burner is named by what it ran"
