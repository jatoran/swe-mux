"""Fail the test that leaks an `aiohttp.ClientSession`, not the one after it.

An unclosed `ClientSession` is the one resource leak in this suite that
`filterwarnings = ["error"]` cannot catch. aiohttp's `__del__` does raise a
`ResourceWarning`, but `ResourceWarning` is the single documented exception in
`pyproject.toml` - it fires at collection time and would redden the gate over
machine load - and the message an operator actually sees
("Unclosed client session" / "Unclosed connector") is not a warning at all. It is
the event loop's exception handler printing to stderr from a finalizer, long
after the run that caused it, against whichever test the collector happened to
interrupt. So a leak here costs nothing until the day it costs a whole day.

That is not hypothetical. The daemon shipped for a week with a teardown that
stopped no service at all (see `tests/test_shutdown_teardown.py`), and the only
trace anywhere was those four lines after an unrelated tier finished.

The check is deliberately construction-scoped rather than a garbage-collector
scan. `CLAUDE.md` records that both obvious scans were measured and neither
works: forcing `gc.collect()` in teardown finds nothing, because the loop still
holds the object, and scanning at session finish finds nothing, because it has
already been collected. Recording each session as it is built - with the stack
that built it - is what survives both, and it is what makes the failure message
name the code that leaked rather than the code that noticed.
"""

from __future__ import annotations

import traceback
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from aiohttp import ClientSession

#: How much of the creating stack to keep. Deep enough to cross the daemon's
#: composition root and reach the service that opened the session, short enough
#: that a failure message stays readable.
_STACK_FRAMES = 12


@contextmanager
def no_leaked_client_sessions(subject: str) -> Iterator[None]:
    """Assert every `ClientSession` built inside this block was closed inside it.

    Sessions are tracked weakly, so one that has already been collected cannot
    resurrect the check; the point is the ones that are still alive, because
    those are exactly the ones that will print from a finalizer later.

    The block's own exception wins if there is one - a leak is a consequence of a
    failure at least as often as it is a cause, and reporting it over the real
    error would bury the real error.
    """
    created: weakref.WeakSet[ClientSession] = weakref.WeakSet()
    stacks: dict[int, str] = {}
    original = ClientSession.__init__

    def tracked(self: ClientSession, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        created.add(self)
        stacks[id(self)] = "".join(traceback.format_stack()[-_STACK_FRAMES:-1])

    ClientSession.__init__ = tracked  # type: ignore[method-assign]
    try:
        yield
    finally:
        ClientSession.__init__ = original  # type: ignore[method-assign]
    leaked = [session for session in created if not session.closed]
    assert not leaked, (
        f"{len(leaked)} aiohttp ClientSession(s) opened inside {subject} were still "
        "open when it finished. Each one holds a live TCP connector, and each will "
        'print "Unclosed client session" from a finalizer against some later, '
        "unrelated test. Close it where it was opened:\n\n"
        + "\n".join(
            f"--- leaked {session!r}\n{stacks.get(id(session), '<no recorded stack>')}"
            for session in leaked
        )
    )
