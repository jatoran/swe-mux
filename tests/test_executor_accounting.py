"""Executor work is accounted per callable, and a stall names the work that burned."""

from __future__ import annotations

import asyncio
import functools
import re
import threading
import time
from pathlib import Path

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
