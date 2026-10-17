"""One stat pass per tick for every transcript the daemon follows.

Each observed session's transcript tailer used to take its own `stat` in a worker
thread every 250 ms, and poll `exists` the same way every 200 ms while it waited
for the file to appear. Each of those is a round trip through the default
executor - a submit, a worker waking for the GIL, and a wakeup back into the loop -
so at 36 sessions it was about 150 hops a second for what is a few microseconds of
syscall each (`DAEMON_LOAD_ISOLATION.md` item 6).

Here every request made during one tick is answered by a single pass in a single
worker hop at the next tick boundary. Boundaries are aligned to the loop clock, so
pollers that started at different moments converge on the same passes instead of
spreading across the interval, and awaiting a stat is what paces a poller: it
needs no sleep of its own.

There is no long-lived task: a batch schedules its flush when its first request
arrives, so nothing is left pending when an event loop closes.
"""

from __future__ import annotations

import asyncio
import math
import os
import weakref
from collections.abc import Sequence
from pathlib import Path

#: The tailers' poll interval, which this now owns.
TICK_SECONDS = 0.25

type StatAnswer = os.stat_result | OSError | ValueError


def _stat_all(paths: Sequence[Path]) -> list[StatAnswer]:
    """Stat every path, keeping each failure as that path's answer."""
    answers: list[StatAnswer] = []
    for path in paths:
        try:
            answers.append(path.stat())
        except (OSError, ValueError) as exc:
            answers.append(exc)
    return answers


class StatTicker:
    """Coalesces `stat` requests on one event loop into one pass per tick."""

    def __init__(self, loop: asyncio.AbstractEventLoop, tick: float = TICK_SECONDS) -> None:
        self._loop = loop
        self._tick = tick
        self._pending: dict[Path, list[asyncio.Future[os.stat_result | None]]] = {}
        self._scheduled: asyncio.TimerHandle | None = None
        self.passes = 0
        self.stats = 0

    def request(self, path: Path) -> asyncio.Future[os.stat_result | None]:
        future: asyncio.Future[os.stat_result | None] = self._loop.create_future()
        self._pending.setdefault(path, []).append(future)
        if self._scheduled is None:
            boundary = (math.floor(self._loop.time() / self._tick) + 1) * self._tick
            self._scheduled = self._loop.call_at(boundary, self._flush)
        return future

    def _flush(self) -> None:
        self._scheduled = None
        batch, self._pending = self._pending, {}
        live = {
            path: waiters
            for path, waiters in batch.items()
            if any(not waiter.done() for waiter in waiters)
        }
        if not live:
            return
        self.passes += 1
        self.stats += len(live)
        paths = tuple(live)
        try:
            work = self._loop.run_in_executor(None, _stat_all, paths)
        except RuntimeError as exc:  # the default executor is shut down: the loop is closing
            for waiters in live.values():
                for waiter in waiters:
                    if not waiter.done():
                        waiter.set_exception(exc)
            return

        def resolve(done: asyncio.Future[list[StatAnswer]]) -> None:
            if done.cancelled():
                for waiters in live.values():
                    for waiter in waiters:
                        waiter.cancel()
                return
            failure = done.exception()
            for index, path in enumerate(paths):
                answer: StatAnswer | BaseException = (
                    failure if failure is not None else done.result()[index]
                )
                for waiter in live[path]:
                    if waiter.done():
                        continue
                    if isinstance(answer, FileNotFoundError):
                        waiter.set_result(None)
                    elif isinstance(answer, BaseException):
                        waiter.set_exception(answer)
                    else:
                        waiter.set_result(answer)

        work.add_done_callback(resolve)

    def snapshot(self) -> dict[str, int]:
        return {"passes": self.passes, "stats": self.stats}


_TICKERS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, StatTicker] = (
    weakref.WeakKeyDictionary()
)


def ticker() -> StatTicker:
    """The running loop's ticker, made on first use."""
    loop = asyncio.get_running_loop()
    found = _TICKERS.get(loop)
    if found is None:
        found = _TICKERS[loop] = StatTicker(loop)
    return found


async def batched_stat(path: Path) -> os.stat_result | None:
    """`path.stat()` at the next tick, or None when the file does not exist.

    Any other failure raises exactly as `Path.stat` would have.
    """
    return await ticker().request(path)
