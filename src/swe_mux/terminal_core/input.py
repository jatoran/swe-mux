"""Classifying what a browser terminal sends before it reaches the PTY."""

from __future__ import annotations

import re

_OSC_DEFAULT_COLOR_RESPONSE = re.compile(
    r"(?:\x1b\](?:10|11);"
    r"(?:rgb:[0-9a-f]{1,4}(?:/[0-9a-f]{1,4}){2}"
    r"|rgba:[0-9a-f]{1,4}(?:/[0-9a-f]{1,4}){3})"
    r"(?:\x07|\x1b\\))+",
    re.IGNORECASE,
)


# Mouse reports, in the three encodings a browser terminal can emit: SGR (1006,
# what xterm.js sends and by far the common case), urxvt (1015), and X10, whose
# three payload bytes are raw and so must not be constrained to digits.
_MOUSE_REPORT_BODY = (
    r"\x1b\[(?:<(\d{1,4});\d{1,5};\d{1,5}[Mm]"
    r"|(\d{1,4});\d{1,5};\d{1,5}M"
    r"|M([\x20-\xff]{3}))"
)


_MOUSE_REPORT = re.compile(_MOUSE_REPORT_BODY)


_MOUSE_REPORT_ONLY = re.compile(f"(?:{_MOUSE_REPORT_BODY})+")


def is_default_color_response(data: str) -> bool:
    """Whether a payload is only xterm's reply to an OSC 10/11 color query.

    A harness whose startup palette probe accepts these replies for only a bounded
    interval declares `suppresses_late_color_response`, and browser/WS latency can
    deliver an otherwise valid reply after that probe has ended, where the CLI
    mistakes it for prompt input.
    """
    return _OSC_DEFAULT_COLOR_RESPONSE.fullmatch(data) is not None


def pointer_report_kind(data: str) -> str | None:
    """Classify a payload that consists only of mouse reports.

    ``"motion"`` for a pointer that merely moved across the pane, ``"button"``
    for a press, release, or wheel notch, and None for anything that is not
    purely mouse reports.

    This matters because these arrive on the same channel as typing. xterm hands
    every mouse report to `onData` once the child enables tracking, so a pointer
    crossing an agent's pane produced ~8 "keystrokes" a second — enough to keep
    `input_revision` climbing forever, which delivery readiness reads as *the
    operator has half-typed something into the composer*. A mouse report cannot
    put text in a composer, so it must not advance that revision; a motion report
    is not even presence, since the pointer can cross a pane on its way somewhere
    else.
    """
    if not data or not _MOUSE_REPORT_ONLY.fullmatch(data):
        return None
    for match in _MOUSE_REPORT.finditer(data):
        sgr, urxvt, x10 = match.groups()
        # X10 offsets every field by 32 so the report stays printable.
        button = int(sgr or urxvt) if sgr or urxvt else ord(x10[0]) - 32
        # Bit 5 is the motion flag. Wheel notches set bit 6 instead, and those
        # are a deliberate act, so they count as presence like a click does.
        if not button & 32:
            return "button"
    return "motion"
