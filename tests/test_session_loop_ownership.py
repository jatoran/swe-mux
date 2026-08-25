"""W4.5.2 - a session owns its fanout and ticker, and drains them when it ends.

The defect these cover produced 48 `ERROR asyncio: Task was destroyed but it is
pending!` lines between 2026-08-19 and the D3 soak, every one of them a
`fanout-<sid>` blocked on `output_queue.get()`. Two halves: the task was
registered without the discard callback every sibling site has, and nothing ever
ended a fanout whose PTY died without delivering the end-of-output sentinel - so
it waited forever and the ERROR appeared whenever the `Session` was collected.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.session import SessionManager


def _session(sid: str = "s1") -> Any:
    return SimpleNamespace(record=SimpleNamespace(id=sid), tasks=set())


def _manager() -> Any:
    return cast(Any, SessionManager.__new__(SessionManager))


async def test_an_owned_loop_leaves_the_set_when_it_ends() -> None:
    """Without the discard callback `session.tasks` only ever grows."""
    manager, session = _manager(), _session()

    async def done() -> None:
        return None

    task = SessionManager._start_session_task(manager, session, done(), "fanout-s1")
    assert task in session.tasks
    await task
    await asyncio.sleep(0)  # done callbacks run on the next loop pass
    assert session.tasks == set()


async def test_draining_waits_for_a_fanout_that_is_about_to_finish() -> None:
    """The sentinel is ordered behind the pane's last bytes.

    Cancelling on sight would drop that final output out of the scrollback, so
    the drain waits first and only cancels what is still there afterwards.
    """
    manager, session = _manager(), _session()
    seen: list[bytes] = []
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    await queue.put(b"last output")
    await queue.put(b"")

    async def fanout() -> None:
        while chunk := await queue.get():
            seen.append(chunk)

    task = SessionManager._start_session_task(manager, session, fanout(), "fanout-s1")
    await SessionManager._drain_session_loops(manager, session)

    assert seen == [b"last output"]
    assert task.done() and not task.cancelled()
    assert session.tasks == set()


async def test_draining_cancels_a_fanout_nothing_will_ever_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 48-ERROR case: the PTY is gone and the sentinel is never coming."""
    monkeypatch.setattr("swe_mux.session.SESSION_LOOP_DRAIN_SECONDS", 0.05)
    manager, session = _manager(), _session()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def fanout() -> None:
        while await queue.get():
            pass

    task = SessionManager._start_session_task(manager, session, fanout(), "fanout-s1")
    await SessionManager._drain_session_loops(manager, session)

    assert task.cancelled()
    assert session.tasks == set()


async def test_draining_never_cancels_the_task_that_asked_for_it() -> None:
    """`_mark_ended` runs inside the fanout on the ordinary end path.

    Without the `current_task` exclusion, a session ending on its own sentinel
    would cancel the very task doing the ending, half way through persisting it.
    """
    manager, session = _manager(), _session()
    finished = False

    async def fanout() -> None:
        nonlocal finished
        await SessionManager._drain_session_loops(manager, session)
        finished = True

    task = SessionManager._start_session_task(manager, session, fanout(), "fanout-s1")
    await asyncio.wait_for(task, timeout=1)
    assert finished and not task.cancelled()


async def test_draining_leaves_a_session_s_other_work_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the two PTY-scoped loops are cancelled.

    Everything else in `session.tasks` is bounded bookkeeping that ends on its
    own - a registration write, a cwd telemetry post - and cutting those off at
    the end is how an end stops being durable.
    """
    monkeypatch.setattr("swe_mux.session.SESSION_LOOP_DRAIN_SECONDS", 0.05)
    manager, session = _manager(), _session()
    completed = False

    async def bookkeeping() -> None:
        nonlocal completed
        await asyncio.sleep(0.15)
        completed = True

    async def forever() -> None:
        await asyncio.Event().wait()

    other = SessionManager._start_session_task(manager, session, bookkeeping(), "register-s1")
    fanout = SessionManager._start_session_task(manager, session, forever(), "fanout-s1")
    ticker = SessionManager._start_session_task(manager, session, forever(), "ticker-s1")

    await SessionManager._drain_session_loops(manager, session)
    assert fanout.cancelled() and ticker.cancelled()
    assert not other.done()

    await asyncio.wait_for(other, timeout=1)
    assert completed


async def test_draining_a_session_with_nothing_running_is_free() -> None:
    manager, session = _manager(), _session()
    await SessionManager._drain_session_loops(manager, session)
    assert session.tasks == set()
