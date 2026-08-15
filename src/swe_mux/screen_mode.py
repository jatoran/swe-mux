"""Alternate-screen tracking read from the PTY stream itself.

The browser also reports xterm's active buffer, but only from the connection
that owns input, and only while a pane is attached — so a session nobody is
looking at has no screen evidence at all, and readiness could never conclude
anything about it. The daemon sees every byte the child writes, and the switch
is an unambiguous DEC private mode, so it can hold this fact itself:

- ``\\x1b[?1049h`` / ``?1047h`` / ``?47h`` enter the alternate screen
- the matching ``l`` forms return to the normal screen

Standalone so ``delivery_readiness`` stays a leaf module.
"""

from __future__ import annotations

import re

SCREEN_MODES = ("normal", "alternate")
# 1049 is what every modern TUI uses; 47 and 1047 are the older spellings, kept
# because a child that uses them is doing exactly the same thing.
SCREEN_TOGGLE = re.compile(rb"\x1b\[\?(?:1049|1047|47)([hl])")
BRACKETED_PASTE_TOGGLE = re.compile(rb"\x1b\[\?2004([hl])")
_MARKER = b"\x1b[\x3f"  # ESC [ ?
# A private-mode introducer plus the longest parameter this cares about.
_MAX_PARTIAL = len(_MARKER) + 5

# Any DEC private mode set, including the `;`-separated form a child may use to set
# several at once (`ESC [ ? 1000 ; 1006 h`).
PRIVATE_MODE_SET = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")

# Modes an agent CLI turns on once at startup and never restates, so a bounded attach
# replay of a long session carries no trace of them and a reconnecting pane — which
# resets its terminal first — silently loses every one.
#
# Measured from a real Claude Code start (2026-08-14): each of these appears exactly
# once, inside the first 130 bytes of the session.
#
# The mouse group is the reason this exists. Without 1000/1002/1003/1006 the browser's
# terminal reports no mouse at all, so a phone drag has nothing to forward and falls
# through to scrolling xterm's own buffer — which on the alternate screen has no
# scrollback, so the swipe moves nothing and the pane reads as frozen. It only ever
# happened on sessions deep enough to outgrow the replay window, which is exactly why
# it presented as intermittent.
#
# What is deliberately NOT on this list:
#   - 1049 (alternate screen) and 2004 (bracketed paste) have their own trackers, whose
#     values are read as facts elsewhere rather than only restated on attach.
#   - 9001 (win32 input mode) changes how the terminal *encodes keys*, not what it
#     reports back. Restoring a reporting channel the child already asked for is safe;
#     changing the input encoding of a live session is a different kind of act, and
#     nothing observed points at its loss causing harm.
STICKY_PRIVATE_MODES: tuple[int, ...] = (1000, 1002, 1003, 1004, 1006, 2031)

# Long enough to carry `ESC [ ? 1000;1002;1003;1006 h` across a chunk boundary.
_MAX_STICKY_PARTIAL = len(_MARKER) + 32


class ScreenModeParser:
    """Incrementally track which screen buffer a PTY child is drawing to.

    ``mode`` is None until the child says something about it. Nothing here
    decays: unlike a browser report, the buffer a child selected stays selected
    until that child changes it, so this is durable state rather than a
    perishable observation.
    """

    __slots__ = ("mode", "_tail")

    def __init__(self) -> None:
        self.mode: str | None = None
        self._tail = b""

    def feed(self, chunk: bytes) -> str | None:
        """Apply every toggle in ``chunk`` and return the resulting mode."""
        data = self._tail + chunk
        # Only the last toggle in a chunk describes where the child ended up.
        last = None
        for match in SCREEN_TOGGLE.finditer(data):
            last = match
        if last is not None:
            self.mode = "alternate" if last.group(1) == b"h" else "normal"
        self._tail = _carry(data, SCREEN_TOGGLE)
        return self.mode


class BracketedPasteParser:
    """Incrementally track whether the child has bracketed paste enabled.

    Durable for the same reason the screen buffer is: the child owns the mode
    until it changes it. What makes this worth tracking is that agent CLIs set it
    **once and never restate it** — Claude Code emits ``\\x1b[?2004h`` 64 bytes
    into its very first output and not again — so a bounded attach replay of a
    long-running session carries no trace of it at all.

    That matters because a reconnecting pane resets its terminal, which clears the
    mode. Without it xterm sends a paste unwrapped *and* rewrites every newline to
    a carriage return, so the CLI receives one Enter per line and submits the paste
    line by line instead of inserting it — leaving only the text after the final
    newline behind.
    """

    __slots__ = ("enabled", "_tail")

    def __init__(self) -> None:
        self.enabled: bool | None = None
        self._tail = b""

    def feed(self, chunk: bytes) -> bool | None:
        """Apply every toggle in ``chunk`` and return the resulting mode."""
        data = self._tail + chunk
        last = None
        for match in BRACKETED_PASTE_TOGGLE.finditer(data):
            last = match
        if last is not None:
            self.enabled = last.group(1) == b"h"
        self._tail = _carry(data, BRACKETED_PASTE_TOGGLE)
        return self.enabled


class StickyModeParser:
    """Track the reporting modes a child enabled once, so an attach can restate them.

    Durable for the same reason the screen buffer and bracketed paste are: the child
    owns each mode until it changes it. Unlike those two, nothing reads this as a fact
    about the session — its only consumer is the attach preamble, which is why it holds
    a plain map rather than a named flag per mode.

    Each tracked mode is independent. A child that enabled 1002 and never 1003 gets
    exactly 1002 back, because restating a mode the child did not ask for would have it
    reporting events it has no handler for.
    """

    __slots__ = ("enabled", "_tail")

    def __init__(self) -> None:
        self.enabled: dict[int, bool] = {}
        self._tail = b""

    def feed(self, chunk: bytes) -> dict[int, bool]:
        """Apply every tracked toggle in ``chunk`` and return the resulting state."""
        data = self._tail + chunk
        for mode, state in _sticky_toggles(data):
            self.enabled[mode] = state
        self._tail = _sticky_carry(data)
        return self.enabled

    def preamble(self, replay: bytes) -> bytes:
        """Bytes that restore the modes ``replay`` cannot speak for itself.

        A window that carries a toggle for a mode already says what that mode is, and
        re-stating it could contradict the child's own most recent word. Only modes the
        window never mentions are restored, and only when they are on.
        """
        mentioned = {mode for mode, _ in _sticky_toggles(replay)}
        return b"".join(
            b"\x1b[?%dh" % mode
            for mode in STICKY_PRIVATE_MODES
            if self.enabled.get(mode) and mode not in mentioned
        )


def _sticky_toggles(data: bytes) -> list[tuple[int, bool]]:
    """Every tracked private-mode toggle in ``data``, in order, expanding `;` lists."""
    found: list[tuple[int, bool]] = []
    for match in PRIVATE_MODE_SET.finditer(data):
        state = match.group(2) == b"h"
        for param in match.group(1).split(b";"):
            if not param.isdigit():
                continue
            mode = int(param)
            if mode in STICKY_PRIVATE_MODES:
                found.append((mode, state))
    return found


def _sticky_carry(data: bytes) -> bytes:
    """Retain a private-mode sequence that may be split across chunks.

    Bounded by construction, like `_carry`, but with room for the multi-parameter form.
    """
    start = data.rfind(_MARKER)
    if (
        start >= 0
        and len(data) - start <= _MAX_STICKY_PARTIAL
        and not PRIVATE_MODE_SET.search(data[start:])
    ):
        return data[start:]
    return next(
        (data[-size:] for size in range(len(_MARKER) - 1, 0, -1) if data.endswith(_MARKER[:size])),
        b"",
    )


def _carry(data: bytes, toggle: re.Pattern[bytes]) -> bytes:
    """Retain only a private-mode sequence that may be split across chunks.

    Bounded by construction: an unterminated introducer longer than the longest
    parameter this matches can no longer become one of these toggles, and a
    complete-but-different sequence (``\\x1b[?25l``, hide cursor) cannot combine
    with the next chunk to form one either.
    """
    start = data.rfind(_MARKER)
    if start >= 0 and len(data) - start <= _MAX_PARTIAL and not toggle.search(data[start:]):
        return data[start:]
    return next(
        (data[-size:] for size in range(len(_MARKER) - 1, 0, -1) if data.endswith(_MARKER[:size])),
        b"",
    )
