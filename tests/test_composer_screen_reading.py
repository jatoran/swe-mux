"""A browser's screen reading of the composer, and what the terminal core lets it say.

The reported failure: an operator typed into a session, deleted it again, and a
message another session queued to it was then refused for as long as that
session lived - "you typed in this terminal" - because the only composer evidence
delivery readiness would act on was an input revision that only ever grows. The
screen is the one place the composer's contents are visible, so a pane now reports
what it shows (`frontend/src/composerText.ts`), and the core keeps that reading
only while it is *current*:

- taken in the stream the core's ring counts, at exactly the position the ring
  ends at - nothing has been drawn since;
- after the last write that could have put text into the composer
  (`write_may_add_text`) - so a keystroke still on its way through the CLI can
  never be hidden behind a screen drawn before it;
- at the PTY's own size - a pane laid out at another width wraps the composer
  somewhere the CLI did not.

Everything here is a rule for *when a reading may be believed*, because a reading
believed at the wrong moment is a delivery pasted onto the operator's text.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from swe_mux.composer_input import (
    ComposerScreenReading,
    ComposerState,
    merge_screen_readings,
    parse_screen_reading,
    reanchor_composer,
    screen_composer_empty,
    write_may_add_text,
)
from swe_mux.terminal_core import TerminalCore, TerminalStream, WriteCondition, WriteRefused
from tests.support.settle import until
from tests.support.terminal_rig import LocalRig, RecordingPty, RemoteRig, Rig, descriptor

STREAM = "s1@test"


def _core() -> tuple[TerminalCore, RecordingPty]:
    pty = RecordingPty()
    stream = TerminalStream(
        64 * 1024,
        attach_replay_bytes=None,
        stream_id=STREAM,
        remote_stream=lambda: "",
        rebuilt=lambda: False,
    )
    core = TerminalCore(stream, pty=lambda: pty, descriptor=descriptor(), tasks=set())
    core.geometry = (100, 30)
    return core, pty


def _frame(core: TerminalCore, state: str = "empty", **changes: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "type": "composer_reading",
        "state": state,
        "chars": 5 if state == "draft" else 0,
        "position": core.stream.scrollback.position,
        "stream": core.stream.stream_id,
        "cols": 100,
        "rows": 30,
    }
    frame.update(changes)
    return frame


# --- which writes can hide text behind a screen ------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        "\x7f",  # backspace
        "\x7f\x7f\x7f",  # held backspace
        "\x08",
        "\x1b\x7f",  # word erase
        "\x17",  # Ctrl+W
        "\x15",  # Ctrl+U
        "\x0b",  # Ctrl+K
        "\x01",  # Ctrl+A
        "\x05",  # Ctrl+E
        "\x1b[3~",  # Delete
        "\x1b[D",  # Left
        "\x1b[C\x1b[C",  # Right, twice
        "\x1b[1;5D",  # Ctrl+Left
        "\x1bOH",  # Home, application mode
        "\x1b[4~",  # End, VT220
        "\x1bb",  # Alt+B
        "\x1b",  # Esc
        "\x1b\x1b",  # Claude's double-Esc clear
        "\x1b[I",  # focus in
        "\x1b[O",  # focus out
        "\x1b[<0;10;5M",  # SGR mouse press
        "\x1b[<64;10;5M",  # wheel
    ],
)
def test_keys_that_only_remove_or_move_cannot_hide_text(data: str) -> None:
    assert write_may_add_text(data) is False


@pytest.mark.parametrize(
    "data",
    [
        "a",
        "hello",
        "\x7fa",  # an erase followed by a character
        "\r",  # a submit draws something else entirely
        "\t",  # completion
        "\x1b[A",  # history recall
        "\x1b[B",
        "\x19",  # Ctrl+Y yanks
        "\x1b[200~pasted\x1b[201~",
        "\x1b\r",  # the composer newline
        "\x1b[Z",  # Shift+Tab
        "\x1bx",  # an Alt chord nothing has measured
    ],
)
def test_anything_else_counts_as_able_to_add_text(data: str) -> None:
    assert write_may_add_text(data) is True


def test_an_empty_write_adds_nothing() -> None:
    assert write_may_add_text("") is False
    assert write_may_add_text(b"") is False
    assert write_may_add_text(b"\x7f") is False
    assert write_may_add_text(b"x") is True


# --- the frame itself -----------------------------------------------------------------


def test_a_well_formed_frame_parses_and_carries_a_count_never_text() -> None:
    reading = parse_screen_reading(
        {"state": "draft", "chars": 12, "position": 40, "stream": "s"}
    )
    assert reading == ComposerScreenReading("draft", 12, 40, "s")
    # A count only belongs to a draft.
    assert parse_screen_reading({"state": "empty", "chars": 9, "position": 0, "stream": "s"}) == (
        ComposerScreenReading("empty", 0, 0, "s")
    )


@pytest.mark.parametrize(
    "frame",
    [
        {"state": "typed", "position": 1, "stream": "s"},
        {"state": "empty", "position": -1, "stream": "s"},
        {"state": "empty", "position": True, "stream": "s"},
        {"state": "empty", "position": "1", "stream": "s"},
        {"state": "empty", "position": 1, "stream": ""},
        {"state": "empty", "position": 1, "stream": 7},
        {"state": "draft", "position": 1, "stream": "s", "chars": -1},
        {"state": "draft", "position": 1, "stream": "s", "chars": 10**9},
    ],
)
def test_a_malformed_frame_is_dropped(frame: dict[str, Any]) -> None:
    assert parse_screen_reading(frame) is None


def test_disagreeing_readings_of_one_screen_keep_the_one_that_blocks() -> None:
    empty = ComposerScreenReading("empty", 0, 10, "s")
    placeholder = ComposerScreenReading("placeholder", 0, 10, "s")
    draft = ComposerScreenReading("draft", 3, 10, "s")
    unreadable = ComposerScreenReading("unreadable", 0, 10, "s")
    assert merge_screen_readings(empty, draft) == draft
    assert merge_screen_readings(draft, empty) == draft
    assert merge_screen_readings(empty, placeholder) == placeholder
    assert merge_screen_readings(unreadable, empty) == empty
    assert merge_screen_readings(empty, unreadable) == empty
    # A reading of a newer screen replaces whatever described the old one.
    newer = ComposerScreenReading("empty", 0, 11, "s")
    assert merge_screen_readings(draft, newer) == newer


# --- what a reading proves -------------------------------------------------------------


def test_what_each_reading_proves_about_the_composer() -> None:
    idle = ComposerState()
    typed = ComposerState(chars=4)
    empty = ComposerScreenReading("empty", 0, 1, "s")
    draft = ComposerScreenReading("draft", 2, 1, "s")
    placeholder = ComposerScreenReading("placeholder", 0, 1, "s")
    unreadable = ComposerScreenReading("unreadable", 0, 1, "s")
    assert screen_composer_empty(None, idle) is None
    assert screen_composer_empty(empty, typed) is True
    assert screen_composer_empty(draft, idle) is False
    assert screen_composer_empty(unreadable, idle) is None
    # The hint's wording is what identified a placeholder, and an operator can type
    # those words: it proves empty only when the input side agrees.
    assert screen_composer_empty(placeholder, idle) is True
    assert screen_composer_empty(placeholder, typed) is None


def test_a_reading_corrects_the_estimate_in_both_directions() -> None:
    # Over-count: a word delete the estimate saw as one key.
    state = ComposerState(chars=7, since=1.0)
    assert reanchor_composer(state, ComposerScreenReading("empty", 0, 1, "s"), 5.0) == "cleared"
    assert (state.chars, state.since) == (0, 0.0)
    # Under-count: a history recall it never counted.
    assert reanchor_composer(state, ComposerScreenReading("draft", 9, 2, "s"), 6.0) == "pending"
    assert (state.chars, state.since) == (9, 6.0)
    # A draft that only changes length is not a crossing, and keeps its age.
    assert reanchor_composer(state, ComposerScreenReading("draft", 3, 3, "s"), 7.0) is None
    assert (state.chars, state.since) == (3, 6.0)
    # A placeholder over typed text is the one reading the screen cannot settle.
    assert reanchor_composer(state, ComposerScreenReading("placeholder", 0, 4, "s"), 8.0) is None
    assert state.chars == 3
    assert reanchor_composer(state, ComposerScreenReading("unreadable", 0, 5, "s"), 9.0) is None
    assert state.chars == 3


# --- when the core believes one --------------------------------------------------------


def test_a_reading_of_the_screen_as_it_stands_is_kept() -> None:
    core, _pty = _core()
    core.stream.feed(b"prompt")
    kept = core.note_composer_reading(_frame(core))
    assert kept == ComposerScreenReading("empty", 0, 6, STREAM)
    assert core.composer_screen() == kept


def test_a_reading_of_an_older_or_another_screen_is_refused() -> None:
    core, _pty = _core()
    core.stream.feed(b"prompt")
    # Behind the ring: output it had not parsed yet.
    assert core.note_composer_reading(_frame(core, position=3)) is None
    # Another stream's position, however plausible.
    assert core.note_composer_reading(_frame(core, stream="other@stream")) is None
    # Laid out at a width the CLI is not drawing at.
    assert core.note_composer_reading(_frame(core, cols=80)) is None
    assert core.composer_screen() is None


def test_output_after_the_reading_retires_it() -> None:
    core, _pty = _core()
    core.stream.feed(b"prompt")
    core.note_composer_reading(_frame(core))
    core.stream.feed(b"x")
    assert core.composer_screen() is None
    assert core.note_output() is True
    assert core.composer_reading is None


def test_a_keystroke_retires_the_reading_until_the_cli_draws_after_it() -> None:
    """The hazard this whole contract exists for: a keystroke written, not yet drawn.

    Claude Code is silent for seconds while it takes in a large paste, so a screen
    read in that window still shows the composer empty with the paste on its way.
    """
    core, pty = _core()
    core.stream.feed(b"prompt")
    core.note_composer_reading(_frame(core))
    core.write_operator_input("\x1b[200~a large paste\x1b[201~")
    assert pty.writes
    assert core.composer_screen() is None
    # A reading of the same, pre-echo screen arrives late: refused.
    assert core.note_composer_reading(_frame(core)) is None
    # The CLI draws; a reading of *that* screen is believed.
    core.stream.feed(b"[Pasted text #1]")
    assert core.note_composer_reading(_frame(core, "draft")) is not None


def test_an_erase_that_draws_nothing_leaves_the_reading_standing() -> None:
    """Measured on Claude Code v2.1.302: a backspace on an empty composer emits no
    output at all. If every keystroke needed a fresh screen, "typed it, held
    backspace a little long" - the case that was reported - could never clear."""
    core, _pty = _core()
    core.stream.feed(b"prompt")
    reading = core.note_composer_reading(_frame(core))
    core.write("\x7f\x7f\x7f")
    assert core.composer_screen() == reading


def test_a_write_condition_can_require_the_screen_it_was_judged_on() -> None:
    core, pty = _core()
    core.stream.feed(b"prompt")
    core.note_composer_reading(_frame(core))
    judged = core.stream.scrollback.position
    # Still standing: the write goes.
    core.write("hello", condition=WriteCondition(screen_position=judged))
    assert pty.writes == ["hello"]
    # That write could have added text, so the reading it relied on is gone, and a
    # second write judged on the same screen is refused.
    with pytest.raises(WriteRefused) as refused:
        core.write("again", condition=WriteCondition(screen_position=judged))
    assert refused.value.reason == "composer_screen_moved"


def test_a_repaint_between_judgement_and_write_refuses_the_write() -> None:
    core, pty = _core()
    core.stream.feed(b"prompt")
    core.note_composer_reading(_frame(core))
    judged = core.stream.scrollback.position
    core.stream.feed(b"repaint")
    with pytest.raises(WriteRefused) as refused:
        core.write("hello", condition=WriteCondition(screen_position=judged))
    assert refused.value.reason == "composer_screen_moved"
    assert pty.writes == []


def test_a_reseeded_ring_starts_over() -> None:
    core, _pty = _core()
    core.stream.feed(b"prompt")
    core.note_composer_reading(_frame(core))
    core.stream.reseed(b"replacement", 5000)
    core.note_stream_reseeded()
    assert core.composer_screen() is None
    # A write in flight before the reseed may not be drawn into the new ring, so the
    # first reading has to come from a screen drawn after it.
    assert core.note_composer_reading(_frame(core)) is None
    core.stream.feed(b"!")
    assert core.note_composer_reading(_frame(core)) is not None


# --- through a browser, wherever the core runs ------------------------------------------


@pytest.fixture(params=["local", "remote"])
async def rig(request: pytest.FixtureRequest) -> AsyncIterator[Rig]:
    built: Rig = LocalRig() if request.param == "local" else RemoteRig()
    await built.start()
    try:
        yield built
    finally:
        await built.stop()


async def test_a_pane_reading_reaches_the_daemon_and_is_retired_by_output(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    rig.feed(b"prompt")
    await until(
        lambda: rig.core.stream.scrollback.position == 6, what="the core's ring took the output"
    )
    end = browser.frames("replay_end")[-1]
    browser.send(
        {
            "type": "composer_reading",
            "state": "empty",
            "chars": 0,
            "position": end["position"] + 6,
            "stream": end["stream"],
            "cols": 100,
            "rows": 30,
        }
    )
    await until(lambda: bool(rig.host.readings), what="the daemon heard the reading")
    assert rig.host.readings[-1].state == "empty"
    await until(lambda: rig.terminal.composer_screen() is not None, what="the daemon can read it")

    # Drawing again retires it on the daemon's side too, which in the gateway case
    # is the gateway noticing the output and saying so in its next `state`.
    rig.feed(b"more")
    await until(lambda: rig.terminal.composer_screen() is None, what="the reading was retired")


async def test_a_keystroke_retires_a_reading_wherever_the_core_runs(rig: Rig) -> None:
    browser = rig.connect("c1")
    await browser.attach()
    await browser.claim()
    rig.feed(b"prompt")
    await until(lambda: rig.core.stream.scrollback.position == 6, what="the ring took the output")
    end = browser.frames("replay_end")[-1]
    browser.send(
        {
            "type": "composer_reading",
            "state": "empty",
            "chars": 0,
            "position": end["position"] + 6,
            "stream": end["stream"],
            "cols": 100,
            "rows": 30,
        }
    )
    await until(lambda: rig.terminal.composer_screen() is not None, what="the daemon can read it")
    browser.type("x")
    await until(lambda: rig.terminal.composer_screen() is None, what="the keystroke retired it")
    # And nothing was drawn, so nothing can bring it back.
    await asyncio.sleep(0.05)
    assert rig.core.composer_screen() is None
