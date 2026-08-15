"""A pane that spawned dead is a failure, reported in the harness's own words.

Resume and Branch both hand back a pane that continues an existing conversation.
A CLI that refuses that conversation starts, prints one line and exits about a
second later — after the response that announced success — so without this the
operator gets a grey pane and no reason. These tests pin the three properties the
flows depend on: the window catches a death, positive proof ends it early, and a
pane that will not come up is removed rather than returned.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from swe_mux.spawn_probe import pane_text, settle_pane, spawn_settled

REFUSAL = (
    b"\x1b[?25l\x1b[2K\r\x1b[31mSession 724f0d90 is currently running as a background\r\n"
    b"agent (bg). Use `claude agents` to find and attach to it.\x1b[0m\r\n"
)


def pane(state: str = "idle", *, pid: int = 4242, output: bytes = b"") -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(id="pane-1", pid=pid, state=state, exit_code=1),
        scrollback=SimpleNamespace(tail_bytes=lambda count: output[-count:]),
    )


def test_pane_text_keeps_the_message_and_drops_the_terminal() -> None:
    text = pane_text(REFUSAL)

    # Colour, cursor and erase sequences are gone; the sentence, wrapped over two
    # physical lines by the terminal, reads as one.
    assert "\x1b" not in text
    assert text == (
        "Session 724f0d90 is currently running as a background "
        "agent (bg). Use `claude agents` to find and attach to it."
    )


def test_pane_text_survives_output_that_is_not_text() -> None:
    # A pane that printed nothing, or printed bytes that are not UTF-8, must yield
    # a diagnostic rather than an exception: this runs while explaining a failure.
    assert pane_text(b"") == ""
    assert pane_text(b"\x1b[2J\x1b[H") == ""
    assert "ok" in pane_text(b"\xff\xfe ok\n")


async def test_a_pane_that_dies_inside_the_window_is_reported() -> None:
    # The pane reaches a live state first and dies a beat later, which is why the
    # check waits the window out rather than sampling once.
    dying = pane("idle", output=REFUSAL)

    async def die_after_a_beat() -> None:
        await asyncio.sleep(0.25)
        dying.record.state = "crashed"

    async with asyncio.TaskGroup() as group:
        group.create_task(die_after_a_beat())
        settled = group.create_task(settle_pane(dying, 2.0))
    failure = settled.result()

    assert failure is not None
    assert failure.state == "crashed"
    assert "background" in failure.text
    assert "exit code 1" in failure.describe()


async def test_proof_of_life_ends_the_window_early() -> None:
    # Without this the whole window is a fixed cost on every success.
    calls = 0

    def alive(_: Any) -> bool:
        nonlocal calls
        calls += 1
        return True

    assert await settle_pane(pane(), 30.0, alive=alive) is None
    assert calls == 1


async def test_a_pane_that_will_not_come_up_is_discarded_and_retried() -> None:
    spawned: list[Any] = []
    stopped: list[str] = []

    class Manager:
        sessions: dict[str, Any] = {}

        async def spawn(self, **kwargs: Any) -> Any:
            del kwargs
            item = pane("crashed", output=REFUSAL)
            spawned.append(item)
            self.sessions[item.record.id] = item
            return item

        async def stop(self, sid: str) -> None:
            stopped.append(sid)

    manager = Manager()
    session, attempts, failure = await spawn_settled(
        manager, flow="test", settle_seconds=0.5, attempts=2, backend="claude"
    )

    assert session is None
    assert attempts == 2
    assert failure is not None and "background" in failure.text
    # Handing back a dead pane is the defect, so neither attempt is left attached.
    assert len(spawned) == 2
    assert stopped == ["pane-1", "pane-1"]
    assert manager.sessions == {}
