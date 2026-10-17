"""Which threads are waiting for work right now, as the threads themselves say.

The stall watchdog samples every thread's stack and has to decide which of them
were doing something during a stall. It used to decide from the leaf frame alone
(`stall_watchdog.IDLE_LEAVES`), and that guess fails exactly where it matters: a
worker blocked in `SimpleQueue.get` has a C leaf and no Python frame below its own
loop, and a thread inside watchfiles' Rust `watch` looks like a busy generator. The
2026-10-16 stall lines blamed both on every stall, beside a real culprit nobody could
tell apart from them.

So a thread that blocks waiting for work marks itself here for the duration of the
wait, and the sampler reads the mark beside the stack. A mark is a claim the thread
makes about itself, which is the only party that knows.

One reason is not idle: `db_lock_wait` is a thread queued for the shared `mux.db`
operation lock (`sqlite_store.run_sqlite_operation`). It is not doing work, but it is
not idle either - a stall with a dozen of them is lock contention, and the watchdog
reports them as their own count rather than hiding them or blaming them.

Cheap by construction: a mark is two dict operations, each atomic under the GIL, so
it costs nothing on the paths that take it thousands of times a second.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

#: A thread queued for the shared SQLite operation lock.
DB_LOCK_WAIT = "db_lock_wait"

_PARKED: dict[int, str] = {}


@contextmanager
def parked(reason: str) -> Iterator[None]:
    """Mark the calling thread as waiting for ``reason`` until the block exits.

    Nests: an inner mark replaces the outer one for its duration and the outer one
    is restored afterwards, so a parked wait that calls something which parks again
    still reads correctly at every moment.
    """
    ident = threading.get_ident()
    previous = _PARKED.get(ident)
    _PARKED[ident] = reason
    try:
        yield
    finally:
        if previous is None:
            _PARKED.pop(ident, None)
        else:
            _PARKED[ident] = previous


def parked_threads() -> dict[int, str]:
    """A copy of the current marks, by thread ident."""
    return dict(_PARKED)
