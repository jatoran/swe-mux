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
- `Ctrl+C` discards the composer in every harness mux drives, so it clears the
  count. What *else* discards it is a per-harness fact the caller supplies from
  ``HarnessDescriptor.composer_clear_keys``, because the obvious answer is wrong
  on the harness most people use: measured 2026-08-20 against Claude Code
  v2.1.238 on a four-line draft, `Ctrl+U` killed one line and left the other
  three, and a bare `Esc` did nothing to the draft at all. Only a double `Esc`
  cleared it. This module previously counted all three as clears, so a Claude
  operator who reached for `Ctrl+U` left the estimate reading "empty" over a
  standing draft — the false *safe* this docstring warns about, in the one place
  that can act on it.
- The harness's composer-newline key (``HarnessDescriptor.composer_newline``,
  ESC+CR on both measured agents) counts as one composed character. It has to be
  named explicitly because it is not a control sequence the escape stripper
  matches, so its bare CR used to survive and classify the whole write as a
  submit — a false *empty* over a standing draft, fired by the rail's own
  Markdown divider and code-fence buttons.
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

BRACKETED_PASTE_START = "\x1b[200~"
BRACKETED_PASTE_END = "\x1b[201~"
_PASTE_START = BRACKETED_PASTE_START
_PASTE_END = BRACKETED_PASTE_END
# Operating-system commands (titles, hyperlinks) carry arbitrary text that is
# addressed to the terminal, never to the composer.
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
# Control sequences: cursor movement, function keys, SS3-encoded arrows, and the
# private-mode toggles a client emits around its own probes.
_ESCAPE = re.compile(r"\x1b\[[0-9;:?<=>]*[A-Za-z@`~]|\x1b[NO][A-Za-z0-9]|\x1b[=>()#][0-9A-Za-z]?")

_ERASE_KEYS = ("\x7f", "\x08")
# Ctrl+C discards the composer in every harness mux drives. What else does is
# per-harness and arrives as ``clear_keys``: this tuple used to also hold Esc and
# Ctrl+U, and both were wrong for Claude Code (measured against v2.1.238 on a
# four-line draft — Ctrl+U killed one line, a bare Esc did nothing at all).
_CLEAR_KEYS = ("\x03",)
# What a harness clears with when the caller does not say. Ctrl+U, which the
# shells mux drives implement and which every consumer assumed before the harness
# registry began declaring the answer.
DEFAULT_CLEAR_KEYS = "\x15"
# What a harness inserts a newline with when the caller does not say. ESC+CR
# (Alt+Enter), which is what the browser sent every agent before
# ``HarnessDescriptor.composer_newline`` existed.
DEFAULT_NEWLINE_KEYS = "\x1b\r"
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


@dataclass(frozen=True, slots=True)
class PendingSubmit:
    """A queue delivery whose bytes are in the composer and were never submitted.

    Not an estimate, unlike :class:`ComposerState`: it is recorded only when the
    delivery path pressed Enter, watched for a turn to open, and saw none — so it
    is a positive, witnessed fact about a specific message, and it is exactly the
    fact the 2026-09-04 incident had nowhere to put. Four messages sat in a Codex
    composer while the queue reported them sent, and the deliveries that followed
    pasted on top of them; the CLI eventually received three separate peer
    messages as one prompt.

    It is deliberately per-session rather than per-message: what it protects is
    the *composer*, and a second delivery into one already holding an unsubmitted
    body is the thing that merges them.
    """

    message_id: str
    at: float
    byte_count: int


def note_unsubmitted_delivery(
    session: object, message_id: str, byte_count: int, now: float
) -> None:
    """Mark this session's composer as holding a delivery the CLI never took."""
    if hasattr(session, "pending_submit"):
        session.pending_submit = PendingSubmit(
            message_id=message_id, at=now, byte_count=byte_count
        )


def clear_pending_submit(session: object) -> PendingSubmit | None:
    """Retire the mark, returning what it held. ``None`` when nothing was standing.

    Called from the two seams that *prove* the composer no longer holds the body:
    a turn opening (whatever wrote the carriage return), and an operator write
    that submits or discards the composer. Nothing expires it on a timer — an
    unsubmitted paste does not become submitted by waiting, and a mark that
    lapsed on its own would restore exactly the silent pile-up it exists to stop.
    """
    standing = getattr(session, "pending_submit", None)
    if standing is None:
        return None
    session.pending_submit = None  # type: ignore[attr-defined]
    return standing if isinstance(standing, PendingSubmit) else None


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


def classify_composer_write(
    data: str,
    in_paste: bool = False,
    clear_keys: str = DEFAULT_CLEAR_KEYS,
    newline_keys: str = DEFAULT_NEWLINE_KEYS,
) -> ComposerWrite:
    """Classify one write to the PTY, without touching any session state.

    ``clear_keys`` is the harness's declared whole-composer clear
    (``HarnessDescriptor.composer_clear_keys``). It is a parameter rather than a
    constant because the constant was wrong: Ctrl+U kills a *line* in Claude
    Code, so counting it as a clear reported an empty composer while three lines
    of a four-line draft were still standing. That is a false "empty", which is
    the direction this module's own docstring names as the dangerous one.

    ``newline_keys`` is the harness's declared composer newline
    (``HarnessDescriptor.composer_newline``) and is counted as one composed
    character each. It has to be recognised explicitly: ESC+CR is not a control
    sequence ``_ESCAPE`` matches, so the bare CR survived the strip and every
    such write classified as a **submit**. That is the same false "empty" — and
    it was already live, because the rail's Markdown divider and code-fence
    buttons have always sent exactly these bytes.
    """
    if not data:
        return ComposerWrite("none", in_paste=in_paste)
    remainder, pasted, in_paste = _split_pastes(data, in_paste)
    # Before the escape strip and before the submit test, for both reasons above.
    composed_newlines = 0
    if newline_keys:
        composed_newlines = remainder.count(newline_keys)
        if composed_newlines:
            remainder = remainder.replace(newline_keys, "")
    remainder = _ESCAPE.sub("", _OSC.sub("", remainder))
    if any(key in remainder for key in _SUBMIT_KEYS):
        return ComposerWrite("submit", in_paste=in_paste)
    if any(key in remainder for key in _CLEAR_KEYS):
        return ComposerWrite("clear", in_paste=in_paste)
    # Matched against the *raw* frame rather than the stripped remainder. Claude's
    # sequence is a double Esc, and the escape stripper above has already eaten it
    # out of ``remainder`` — matching there would never fire.
    if clear_keys and clear_keys in data:
        return ComposerWrite("clear", in_paste=in_paste)
    typed = pasted + composed_newlines + _composable_length(remainder)
    erased = sum(remainder.count(key) for key in _ERASE_KEYS)
    if not typed and not erased:
        return ComposerWrite("none", in_paste=in_paste)
    return ComposerWrite("edit", typed=typed, erased=erased, in_paste=in_paste)


def note_composer_write(
    state: ComposerState,
    data: str,
    now: float,
    clear_keys: str = DEFAULT_CLEAR_KEYS,
    newline_keys: str = DEFAULT_NEWLINE_KEYS,
) -> str | None:
    """Apply one write to ``state``.

    Returns ``'pending'`` or ``'cleared'`` when the composer crossed between
    empty and non-empty, and ``None`` when it did not. Only the crossing is
    reportable: it is one fact per composer cycle rather than one per keystroke,
    which is what keeps this off the event bus and out of the ledger on a fast
    typist's every character.
    """
    write = classify_composer_write(data, state.in_paste, clear_keys, newline_keys)
    return apply_composer_write(state, write, now)


def apply_composer_write(state: ComposerState, write: ComposerWrite, now: float) -> str | None:
    """Apply an already-classified write, reporting the same crossing.

    Split out from :func:`note_composer_write` for the one caller that needs the
    verdict as well as the crossing: the daemon's operator-input path has to know
    whether a write *submitted or discarded* the composer, because that is what
    retires a queue delivery the CLI never took (`prompt_queue.PendingSubmit`).
    Classifying a multi-kilobyte paste twice to recover a field the first pass
    already computed is the kind of small waste that ends up in a hot path.
    """
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


def composer_insertion(
    text: str,
    *,
    newline_keys: str = DEFAULT_NEWLINE_KEYS,
    lift_leading_newline: bool = False,
) -> str:
    """The PTY bytes that put ``text`` into a composer without submitting it.

    The body travels as a bracketed paste with newlines as CR, which is what
    xterm writes for a real paste and what every agent TUI mux drives reads as
    content rather than as a run of submits.

    ``lift_leading_newline`` is the harness's
    ``paste_leading_newline_submits`` verdict, and it exists because the paste
    wrapper does not protect the *first* character. Measured 2026-08-22 against
    Codex v0.149.0 over a real pseudoterminal: a paste carrying interior
    newlines lands as a three-line draft, while the same paste with one leading
    CR **submits whatever the composer already held** and then pastes the rest.
    The live "Tree" prompt template begins with a newline, so pressing its button
    over a half-typed draft sent that draft to the model. A leading LF is not a
    repair - Codex drops it, concatenating the paste onto the standing draft.

    So the leading newline run is lifted out of the paste and sent as
    ``newline_keys`` presses ahead of it, which is exactly what the author of a
    template beginning with a newline meant: start below what is already there.
    One write, not two: measured as a single `ESC+CR` + bracketed paste, Codex
    keeps the draft and opens the paste on line two.

    Everything else is unchanged, which is what keeps an unmeasured harness on
    the bytes mux has always sent it.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lifted = ""
    if lift_leading_newline and newline_keys:
        body = normalized.lstrip("\n")
        lifted = newline_keys * (len(normalized) - len(body))
        normalized = body
    if not normalized:
        return lifted
    pasted = normalized.replace("\n", "\r")
    return f"{lifted}{BRACKETED_PASTE_START}{pasted}{BRACKETED_PASTE_END}"


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
