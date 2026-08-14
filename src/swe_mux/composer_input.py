"""Unsent text in a session's composer, tracked from the bytes mux writes to it.

The CLI's input box is not readable: the daemon holds a write log, not a terminal
cell grid, and no harness publishes its composer contents. What the daemon does
hold is every byte any operator path writes to the PTY, so the composer's
*emptiness* is derivable from the input side alone — count what was typed, drop
the count on the keys that submit or discard it.

That estimate exists for one purpose: telling a human "you left something
half-typed here" on a row they are scanning, from any device, including the one
they did not type it on. It is display evidence and nothing else.

- **It never relaxes a delivery gate.** `delivery_readiness.py` keeps blocking on
  its own `input_revision` boundary, which cannot be talked out of a block by an
  estimate. A flag that says "empty" while the composer holds a line would turn a
  refused send into a corrupted one, and a false *safe* is the dangerous
  direction (`.docs/design/features/delivery-readiness.md`).
- **It is run-scoped and process-scoped.** Nothing here survives a daemon
  restart: the PTY does (the supervisor owns it), the byte history does not, so a
  rebuilt session starts at "composer empty". Absent is the honest reading of
  "not observed", and the next keystroke re-establishes it.

Accuracy, measured against what the keys actually do in Claude Code and Codex:

- Bracketed-paste bodies are extracted before anything else, so a pasted
  newline counts as composed text rather than as a submit. Bracketed paste
  exists precisely so a multi-line paste does not run; reading its `\\r` as a
  submit is how the count would zero itself on the largest thing anyone ever
  puts in a composer. The open/closed state rides `ComposerState` rather than
  being re-derived per frame, because a paste large enough to matter is the one
  most likely to arrive split across writes — and a continuation frame carries
  no opening marker of its own.
- A carriage return or newline outside a paste is a submit, whatever else rode
  the same frame.
- `Ctrl+C`, bare `Esc`, and `Ctrl+U` discard the composer in every harness mux
  drives, so any of them clears the count.
- Backspace and delete decrement it, which is what stops "typed a word, thought
  better of it" from leaving a flag standing until the next turn.
- Cursor keys, function keys, and mode toggles (`shift+tab`) move nothing into
  the composer and are ignored. History recall (`↑`) does put text there and is
  deliberately NOT counted: inventing a count for bytes the operator never sent
  is worse than missing one, because the mark's whole value is that it means
  something when it is drawn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PASTE_START = "\x1b[200~"
_PASTE_END = "\x1b[201~"
# Operating-system commands (titles, hyperlinks) carry arbitrary text that is
# addressed to the terminal, never to the composer.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
# Control sequences: cursor movement, function keys, SS3-encoded arrows, and the
# private-mode toggles a client emits around its own probes.
_ESCAPE = re.compile(r"\x1b\[[0-9;:?<=>]*[A-Za-z@`~]|\x1b[NO][A-Za-z0-9]|\x1b[=>()#][0-9A-Za-z]?")

_ERASE_KEYS = ("\x7f", "\x08")
# Keys that discard the composer outright rather than editing it.
_CLEAR_KEYS = ("\x03", "\x1b", "\x15")
_SUBMIT_KEYS = ("\r", "\n")


@dataclass(frozen=True, slots=True)
class ComposerWrite:
    """What one PTY write did to the composer.

    ``kind`` is the verdict; ``typed``/``erased`` carry the size of an ``edit``
    so the caller never re-parses the frame. ``in_paste`` is the bracketed-paste
    state the *next* write starts from.
    """

    kind: str  # 'submit' | 'clear' | 'edit' | 'none'
    typed: int = 0
    erased: int = 0
    in_paste: bool = False


@dataclass(slots=True)
class ComposerState:
    """Estimated unsent composer contents for one session.

    ``chars`` is an estimate and is deliberately not published: a number that
    looks exact would be read as one. ``since`` is the instant the composer last
    went from empty to non-empty, in epoch seconds, and is what a row renders.
    ``in_paste`` carries an unterminated bracketed paste into the next write.
    """

    chars: int = 0
    since: float = 0.0
    in_paste: bool = False

    @property
    def pending(self) -> bool:
        return self.chars > 0


def _composable_length(text: str) -> int:
    """Characters in ``text`` that occupy the composer.

    Newlines count: inside a paste they are content, and a composer holding
    three lines is holding something. Tab does not — every harness mux drives
    binds it to completion or mode cycling, not to inserting whitespace.
    """
    return sum(1 for char in text if char == "\n" or (char >= " " and char != "\x7f"))


def _split_pastes(data: str, in_paste: bool) -> tuple[str, int, bool]:
    """Separate paste bodies from ordinary keystrokes.

    Returns the text outside any paste, the composable length of everything
    inside one, and whether a paste is still open when the frame ends.
    """
    plain: list[str] = []
    pasted = 0
    cursor = 0
    while cursor < len(data):
        if in_paste:
            end = data.find(_PASTE_END, cursor)
            if end < 0:
                pasted += _composable_length(data[cursor:])
                break
            pasted += _composable_length(data[cursor:end])
            cursor = end + len(_PASTE_END)
            in_paste = False
            continue
        start = data.find(_PASTE_START, cursor)
        if start < 0:
            plain.append(data[cursor:])
            break
        plain.append(data[cursor:start])
        cursor = start + len(_PASTE_START)
        in_paste = True
    return "".join(plain), pasted, in_paste


def classify_composer_write(data: str, in_paste: bool = False) -> ComposerWrite:
    """Classify one write to the PTY, without touching any session state."""
    if not data:
        return ComposerWrite("none", in_paste=in_paste)
    remainder, pasted, in_paste = _split_pastes(data, in_paste)
    remainder = _ESCAPE.sub("", _OSC.sub("", remainder))
    if any(key in remainder for key in _SUBMIT_KEYS):
        return ComposerWrite("submit", in_paste=in_paste)
    if any(key in remainder for key in _CLEAR_KEYS):
        return ComposerWrite("clear", in_paste=in_paste)
    typed = pasted + _composable_length(remainder)
    erased = sum(remainder.count(key) for key in _ERASE_KEYS)
    if not typed and not erased:
        return ComposerWrite("none", in_paste=in_paste)
    return ComposerWrite("edit", typed=typed, erased=erased, in_paste=in_paste)


def note_composer_write(state: ComposerState, data: str, now: float) -> str | None:
    """Apply one write to ``state``.

    Returns ``'pending'`` or ``'cleared'`` when the composer crossed between
    empty and non-empty, and ``None`` when it did not. Only the crossing is
    reportable: it is one fact per composer cycle rather than one per keystroke,
    which is what keeps this off the event bus and out of the ledger on a fast
    typist's every character.
    """
    write = classify_composer_write(data, state.in_paste)
    state.in_paste = write.in_paste
    if write.kind == "none":
        return None
    was_pending = state.pending
    if write.kind in {"submit", "clear"}:
        state.chars = 0
    else:
        state.chars = max(0, state.chars + write.typed - write.erased)
    if state.pending == was_pending:
        return None
    state.since = now if state.pending else 0.0
    return "pending" if state.pending else "cleared"


def clear_composer(state: ComposerState) -> bool:
    """Drop the composer estimate at a seam that empties it by definition.

    A turn starting proves the composer was submitted however the submit
    reached the PTY, and an ended session has no composer at all. Returns
    whether anything was standing.
    """
    state.in_paste = False
    if not state.pending:
        return False
    state.chars = 0
    state.since = 0.0
    return True
