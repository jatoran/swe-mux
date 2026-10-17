"""What the daemon's executor threads spend CPU on, by the callable they ran.

The stall watchdog can say which *thread* burned CPU during a stall, but in a convoy
that thread is usually one of the default executor's thirty-two interchangeable
workers: the first harness run with verdicts named `asyncio_21` and `asyncio_1` as the
GIL holders of two convoys, and both had finished their burst - and gone back to
waiting - before the stack capture looked at them. A thread name is not actionable.
The callable is.

So every executor the daemon owns is an `AccountedThreadPoolExecutor`: each work item
runs between two `time.thread_time()` readings, and the ledger keeps the CPU it used
against a label naming the callable (`HistoryIndex.recent_runs`,
`run_sqlite_operation:TelemetryStore.record_loop_stall`, `ProcessInspector._collect`).
That answers two questions:

- **during a stall**, which work ran on the thread that burned CPU (`window`), and
- **over a run**, which work costs the daemon the most CPU off the loop (`snapshot`),
  which is the ranking the load-isolation plan orders its work by.

Each pool also reports its occupancy: how many items ran at once at the peak, how
many waited for a worker, and for how long. That is what sizes a pool - too many
workers let CPU-bound Python contend with the loop for the GIL, too few turn one
slow blocking call into a wait for every unrelated item queued behind it.

The label is computed on the submitting thread (a few attribute reads), the CPU
readings on the worker. Windows accounts thread CPU at clock-tick granularity, so a
single short item reads as zero or one tick; sums over many items are what to read.
"""

from __future__ import annotations

import contextvars
import functools
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

#: Completed work items kept for stall windows: several minutes of a busy daemon.
RECENT_WORK = 8192
#: Distinct labels tracked before the rest are folded into one.
MAX_LABELS = 2000
OVERFLOW_LABEL = "(other)"
TOP_WORK = 15
#: Queue waits kept per pool for the percentiles in `snapshot`.
RECENT_WAITS = 2048
#: Trailing name parts that say nothing: store closures are all `op`.
_ANONYMOUS_PARTS = frozenset({"op", "fn", "<lambda>"})

#: Wrappers whose real work is one of their positional arguments, by index.
_WRAPPERS: dict[Callable[..., Any], int] = {}


def register_wrapper(wrapper: Callable[..., Any], argument_index: int) -> None:
    """Label work submitted through ``wrapper`` by the callable at ``argument_index``.

    `run_sqlite_operation(db, lock, op)` is every store's submission, so without this
    every store operation would carry the same label.
    """
    _WRAPPERS[wrapper] = argument_index


def short_name(target: object) -> str:
    """``Class.method`` for a callable, dropping ``<locals>`` and anonymous closures."""
    name = (
        getattr(target, "__qualname__", None)
        or getattr(target, "__name__", None)
        or type(target).__qualname__
    )
    parts = [part for part in str(name).split(".") if part and part != "<locals>"]
    while len(parts) > 1 and parts[-1] in _ANONYMOUS_PARTS:
        parts.pop()
    return ".".join(parts[-2:]) if parts else str(name)


def work_label(fn: Callable[..., Any], args: tuple[Any, ...] = ()) -> str:
    """The name a work item is accounted under.

    Unwraps `functools.partial` and the `Context.run` that `asyncio.to_thread`
    submits, then any registered wrapper.
    """
    for _ in range(4):
        if isinstance(fn, functools.partial):
            args = (*fn.args, *args)
            fn = fn.func
            continue
        owner = getattr(fn, "__self__", None)
        if isinstance(owner, contextvars.Context) and getattr(fn, "__name__", "") == "run":
            if not args:
                break
            fn, args = args[0], tuple(args[1:])
            continue
        break
    try:
        index = _WRAPPERS.get(fn)
    except TypeError:  # an unhashable callable is never a registered wrapper
        index = None
    if index is not None and len(args) > index and callable(args[index]):
        return f"{short_name(fn)}:{work_label(args[index])}"
    return short_name(fn)


class WorkLedger:
    """CPU and wall time per work label, overall and for recent items."""

    def __init__(
        self, *, recent: int = RECENT_WORK, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        #: (ended, started, label, thread ident, cpu seconds)
        self._recent: deque[tuple[float, float, str, int, float]] = deque(maxlen=recent)
        #: label -> [count, cpu seconds, wall seconds]
        self._totals: dict[str, list[float]] = {}
        #: thread ident -> (label, started)
        self._running: dict[int, tuple[str, float]] = {}
        self._pools: weakref.WeakSet[AccountedThreadPoolExecutor] = weakref.WeakSet()

    def register_pool(self, pool: AccountedThreadPoolExecutor) -> None:
        with self._lock:
            self._pools.add(pool)

    def begin(self, label: str) -> tuple[float, float]:
        started = self._clock()
        self._running[threading.get_ident()] = (label, started)
        return started, time.thread_time()

    def end(self, label: str, started: float, cpu_started: float) -> None:
        cpu = max(0.0, time.thread_time() - cpu_started)
        ended = self._clock()
        ident = threading.get_ident()
        with self._lock:
            self._running.pop(ident, None)
            self._recent.append((ended, started, label, ident, cpu))
            totals = self._totals.get(label)
            if totals is None:
                if len(self._totals) >= MAX_LABELS:
                    label = OVERFLOW_LABEL
                totals = self._totals.setdefault(label, [0, 0.0, 0.0])
            totals[0] += 1
            totals[1] += cpu
            totals[2] += ended - started

    def window(self, start: float, end: float) -> dict[str, Any]:
        """Finished work that overlapped ``[start, end]``, and what is running now.

        ``by_label`` maps a label to ``[count, cpu]``; ``by_thread`` maps a thread
        ident to its CPU per label. An item straddling either edge contributes the
        share of its CPU that its overlap is of its wall time, which assumes it
        burned evenly. An item that ended after ``end`` still counts: in the first
        harness run with labels, a worker that held the GIL for 80% of a stall
        finished just after the window closed and was otherwise left unnamed.
        """
        by_label: dict[str, list[float]] = {}
        by_thread: dict[int, dict[str, float]] = {}
        with self._lock:
            items = [item for item in self._recent if item[0] >= start and item[1] <= end]
            running = dict(self._running)
        for ended, started, label, ident, total in items:
            wall = ended - started
            overlap = min(ended, end) - max(started, start)
            cpu = total if wall <= 0 else total * max(0.0, min(1.0, overlap / wall))
            entry = by_label.setdefault(label, [0, 0.0])
            entry[0] += 1
            entry[1] += cpu
            per_thread = by_thread.setdefault(ident, {})
            per_thread[label] = per_thread.get(label, 0.0) + cpu
        return {"by_label": by_label, "by_thread": by_thread, "running": running}

    def snapshot(self, *, recent_seconds: float = 60.0) -> dict[str, Any]:
        """The costliest labels since start and over the last ``recent_seconds``."""
        now = self._clock()
        with self._lock:
            totals = {label: list(values) for label, values in self._totals.items()}
            recent = [item for item in self._recent if item[0] >= now - recent_seconds]
            running = len(self._running)
            pools = list(self._pools)
        recent_by_label: dict[str, list[float]] = {}
        for _ended, started, label, _ident, cpu in recent:
            entry = recent_by_label.setdefault(label, [0, 0.0, 0.0])
            entry[0] += 1
            entry[1] += cpu
            entry[2] += _ended - started

        def top(table: dict[str, list[float]]) -> list[dict[str, Any]]:
            ranked = sorted(table.items(), key=lambda item: -item[1][1])[:TOP_WORK]
            return [
                {
                    "label": label,
                    "count": int(count),
                    "cpu_seconds": round(cpu, 3),
                    "wall_seconds": round(wall, 3),
                }
                for label, (count, cpu, wall) in ranked
            ]

        return {
            "labels": len(totals),
            "running": running,
            "since_start": top(totals),
            "recent_seconds": recent_seconds,
            "recent": top(recent_by_label),
            "pools": sorted(
                (pool.occupancy() for pool in pools), key=lambda item: str(item["name"])
            ),
        }


#: The ledger every daemon executor records into.
LEDGER = WorkLedger()


class _Occupancy:
    """How full one pool is, now and at its peak, and how long work waited."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queued = 0
        self.running = 0
        self.peak_queued = 0
        self.peak_running = 0
        self.submitted = 0
        self.max_wait = 0.0
        self.waits: deque[float] = deque(maxlen=RECENT_WAITS)

    def submitted_one(self) -> None:
        with self.lock:
            self.submitted += 1
            self.queued += 1
            self.peak_queued = max(self.peak_queued, self.queued)

    def started(self, waited: float) -> None:
        with self.lock:
            self.queued -= 1
            self.running += 1
            self.peak_running = max(self.peak_running, self.running)
            self.waits.append(waited)
            self.max_wait = max(self.max_wait, waited)

    def finished(self) -> None:
        with self.lock:
            self.running -= 1

    def abandoned(self) -> None:
        """A queued item was cancelled before any worker took it."""
        with self.lock:
            self.queued -= 1


def _accounted(
    ledger: WorkLedger,
    occupancy: _Occupancy,
    submitted: float,
    label: str,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    occupancy.started(time.perf_counter() - submitted)
    token = ledger.begin(label)
    try:
        return fn(*args, **kwargs)
    finally:
        ledger.end(label, *token)
        occupancy.finished()


class AccountedThreadPoolExecutor(ThreadPoolExecutor):
    """A `ThreadPoolExecutor` whose work items are accounted in a `WorkLedger`."""

    def __init__(self, *args: Any, ledger: WorkLedger | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ledger = ledger if ledger is not None else LEDGER
        self._occupancy = _Occupancy()
        self._ledger.register_pool(self)

    def submit[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        label = work_label(fn, args)
        occupancy = self._occupancy
        occupancy.submitted_one()
        try:
            future = super().submit(
                _accounted,
                self._ledger,
                occupancy,
                time.perf_counter(),
                label,
                fn,
                args,
                kwargs,
            )
        except BaseException:
            occupancy.abandoned()
            raise
        future.add_done_callback(_count_if_abandoned(occupancy))
        return future

    def occupancy(self) -> dict[str, Any]:
        stats = self._occupancy
        with stats.lock:
            waits = sorted(stats.waits)
            reading = {
                "name": self._thread_name_prefix or "(unnamed)",
                "max_workers": self._max_workers,
                "threads": len(self._threads),
                "running": stats.running,
                "queued": stats.queued,
                "peak_running": stats.peak_running,
                "peak_queued": stats.peak_queued,
                "submitted": stats.submitted,
                "max_wait_ms": round(stats.max_wait * 1000, 1),
            }
        if waits:
            reading["wait_ms"] = {
                "p50": round(waits[len(waits) // 2] * 1000, 2),
                "p99": round(waits[min(len(waits) - 1, int(len(waits) * 0.99))] * 1000, 2),
            }
        return reading


def _count_if_abandoned(occupancy: _Occupancy) -> Callable[[Future[Any]], None]:
    def done(future: Future[Any]) -> None:
        if future.cancelled():
            occupancy.abandoned()

    return done
