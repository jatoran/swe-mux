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
_MARKER = b"\x1b[\x3f"  # ESC [ ?
# A private-mode introducer plus the longest parameter this cares about.
_MAX_PARTIAL = len(_MARKER) + 5


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
        self._tail = _carry(data)
        return self.mode


def _carry(data: bytes) -> bytes:
    """Retain only a private-mode sequence that may be split across chunks.

    Bounded by construction: an unterminated introducer longer than the longest
    parameter this matches can no longer become one of these toggles, and a
    complete-but-different sequence (``\\x1b[?25l``, hide cursor) cannot combine
    with the next chunk to form one either.
    """
    start = data.rfind(_MARKER)
    if start >= 0 and len(data) - start <= _MAX_PARTIAL and not SCREEN_TOGGLE.search(data[start:]):
        return data[start:]
    return next(
        (data[-size:] for size in range(len(_MARKER) - 1, 0, -1) if data.endswith(_MARKER[:size])),
        b"",
    )
