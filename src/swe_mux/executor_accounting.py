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

The label is computed on the submitting thread (a few attribute reads), the CPU
readings on the worker. Windows accounts thread CPU at clock-tick granularity, so a
single short item reads as zero or one tick; sums over many items are what to read.
"""

from __future__ import annotations

import contextvars
import functools
import threading
import time
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
        }


#: The ledger every daemon executor records into.
LEDGER = WorkLedger()


def _accounted(
    ledger: WorkLedger,
    label: str,
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    token = ledger.begin(label)
    try:
        return fn(*args, **kwargs)
    finally:
        ledger.end(label, *token)


class AccountedThreadPoolExecutor(ThreadPoolExecutor):
    """A `ThreadPoolExecutor` whose work items are accounted in a `WorkLedger`."""

    def __init__(self, *args: Any, ledger: WorkLedger | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._ledger = ledger if ledger is not None else LEDGER

    def submit[**P, T](self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        label = work_label(fn, args)
        return super().submit(_accounted, self._ledger, label, fn, args, kwargs)
