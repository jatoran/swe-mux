"""Waiting for something the daemon does on its own, without betting on a clock.

The gate runs pytest across every core (`-n auto`, see CLAUDE.md § Verification),
so a test's coroutine shares a machine with fifteen other workers. A fixed
`await asyncio.sleep(0.01)` before a *positive* assertion is a bet that the loop
gets to the handler inside ten milliseconds, and under that load the bet loses:
the test reddens the gate over machine scheduling rather than over the code.

Both helpers here poll instead, which is also faster than sleeping on an idle
machine - they return on the turn the condition holds rather than serving out a
window chosen for the worst case.

None of this applies to a sleep guarding a *negative* assertion ("no pulse
fired", "the candidate was not committed"). That is a real quiet window and
load only makes it safer; leave those alone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

__all__ = ["drained_until", "until"]


async def until(predicate: Callable[[], bool], *, seconds: float = 5.0, what: str = "") -> None:
    """Wait until `predicate` holds, failing the test if it never does.

    Polls rather than waiting on an `asyncio.Event` because the state being
    watched is a plain list or attribute that production code mutates without
    signalling anyone; there is nothing to subscribe to short of instrumenting
    the daemon for the tests, which would be a worse trade than a 5ms tick.
    """
    try:
        async with asyncio.timeout(seconds):
            while not predicate():  # noqa: ASYNC110 - no event to wait on; see docstring
                await asyncio.sleep(0.005)
    except TimeoutError:
        raise AssertionError(f"condition never held: {what or predicate}") from None


async def drained_until(
    queue: asyncio.Queue[Any], kind: str, *, seconds: float = 5.0
) -> list[Any]:
    """Drain an `EventBus` subscription until an event of `kind` has arrived.

    Returns everything drained, so a test can still assert on how *many* of each
    kind it saw. `kind` should be the last event the path under test emits -
    waiting on an earlier one would return before the rest were queued and make
    a count assertion read low.
    """
    emitted: list[Any] = []

    def collected() -> bool:
        while not queue.empty():
            emitted.append(queue.get_nowait())
        return any(item.type == kind for item in emitted)

    try:
        async with asyncio.timeout(seconds):
            while not collected():  # noqa: ASYNC110 - no event to wait on; see `until`
                await asyncio.sleep(0.005)
    except TimeoutError:
        raise AssertionError(
            f"no {kind!r} event arrived; saw {[item.type for item in emitted]}"
        ) from None
    return emitted
