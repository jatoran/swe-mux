"""Unsent text in a session's composer: an estimate from the bytes mux writes to
it, corrected by what a browser read off the screen.

The daemon holds a write log, not a terminal cell grid, and no harness publishes
its composer contents. Two sources answer "is something sitting in the composer",
and they fail in opposite places:

- **The estimate** (`ComposerState`) counts what every operator path wrote - typed
  text adds, erase keys subtract, a submit or a declared clear key zeroes it. It
  exists for every session whether or not anyone is watching, and it is wrong
  wherever a key's effect is the CLI's business: a word delete, a history recall,
  a slash menu that consumed the keys.
- **A screen reading** (`ComposerScreenReading`) is what a browser's own xterm
  showed in the composer's rectangle (`frontend/src/composerText.ts`). It is the
  truth about what was drawn, and it exists only while some browser has the pane.
  The terminal core accepts one only while it is *current* - nothing was drawn
  after the screen it describes, and nothing that could put text in the composer
  was written since the CLI last drew (`write_may_add_text`) - and drops it the
  moment either stops being true.

How each is used, and why the asymmetry:

- **A current screen reading decides.** `empty` is positive evidence the composer
  holds nothing, and it is what lets delivery readiness stop refusing a session
  whose operator typed and then deleted (`delivery-readiness.md`). `draft` blocks.
  `placeholder` is a screen showing only the harness's empty-composer hint
  without the dim attribute that normally marks it, which is evidence of empty
  only together with an estimate that agrees.
- **The estimate can only ever block.** Pending text with no screen reading keeps
  a delivery out; an estimate of zero proves nothing on its own, because the
  places it is wrong include text it never counted. A false *safe* is the
  dangerous direction (`.docs/design/features/delivery-readiness.md`).
- **A screen reading re-anchors the estimate** (`reanchor_composer`), so the count
  a row shows after the browser leaves starts from what was actually on screen
  rather than from a drift the screen already contradicted.
- **Both are run-scoped and process-scoped.** Nothing here survives a daemon
  restart: the PTY does (the supervisor owns it), the byte history does not, so a
  rebuilt session starts at "composer empty". Absent is the honest reading of
  "not observed", and the next keystroke or screen reading re-establishes it.

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
  something when it is drawn. A screen reading is what corrects it.
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

# Keys that can only take text out of a composer or move within it: erase, word
# and line kills, cursor movement, and the reports a terminal sends on the same
# channel (focus changes, mouse). A write made only of these cannot leave text
# behind that the screen does not yet show, which is the one thing a screen
# reading has to be protected from (`write_may_add_text`).
#
# Everything absent from this list counts as able to add text, including keys
# that add none on most harnesses but are not known never to: Tab completes,
# `↑`/`↓` recall history, Ctrl+Y yanks. Measured 2026-10-18 against Claude Code
# v2.1.302 and Codex v0.163.0: an extra backspace on an empty composer draws
# nothing at all on Claude, so a rule that wanted fresh output after *every*
# keystroke would have refused exactly the "typed it, held backspace" case.
_NON_ADDITIVE_KEY = re.compile(
    r"\x1b[\x7f\x08]"  # Alt/Ctrl+Backspace: word erase
    r"|[\x7f\x08]"  # Backspace
    r"|[\x01\x05\x0b\x15\x17]"  # Ctrl+A, Ctrl+E, Ctrl+K, Ctrl+U, Ctrl+W
    r"|\x1b\[3(?:;\d{1,3})?~"  # Delete, with modifiers
    r"|\x1b\[(?:1;\d{1,3})?[CDHF]"  # Right, Left, Home, End, with modifiers
    r"|\x1bO[CDHF]"  # the same, application cursor mode
    r"|\x1b\[[1478]~"  # Home and End, VT220 spellings
    r"|\x1b[bf]"  # Alt+B, Alt+F: word movement
    r"|\x1b\[[IO]"  # focus in, focus out
    r"|\x1b\[<\d{1,4};\d{1,5};\d{1,5}[Mm]"  # SGR mouse report
    r"|\x1b\[\d{1,4};\d{1,5};\d{1,5}M"  # urxvt mouse report
    r"|\x1b\[M[\x20-\xff]{3}"  # X10 mouse report
)
_NON_ADDITIVE_WRITE = re.compile(f"(?:{_NON_ADDITIVE_KEY.pattern})+")
# A bare Esc, or several: Claude's double-Esc clear is two of them in one write.
_ESCAPES_ONLY = re.compile(r"\x1b+")


def write_may_add_text(data: str | bytes) -> bool:
    """Whether a PTY write could put text in a composer that the screen does not
    yet show.

    False only for a write made entirely of keys that remove or move
    (`_NON_ADDITIVE_KEY`), or of bare Esc presses. A screen reading taken before
    such a write still describes the composer after it: the write could have
    emptied the composer further, or opened something else entirely, and both of
    those draw - which retires the reading on its own - but neither can leave text
    the reading did not see. Every other write retires the reading at once,
    because the text it carried may still be on its way through the CLI.
    """
    if not data:
        return False
    text = data.decode("latin-1") if isinstance(data, bytes) else data
    if _ESCAPES_ONLY.fullmatch(text):
        return False
    return _NON_ADDITIVE_WRITE.fullmatch(text) is None


#: What a browser can report about one composer, read off its own cell grid
#: (`frontend/src/composerText.ts`). ``placeholder`` is a screen showing only the
#: harness's empty-composer hint, undimmed and with the caret parked before it -
#: what Claude Code v2.1.302 draws after a full repaint. ``unreadable`` is a screen
#: whose composer the browser could not find, a dialog or a picker most often.
SCREEN_COMPOSER_STATES = frozenset({"empty", "placeholder", "draft", "unreadable"})
#: The longest draft a reading may claim, in characters. A count, never content.
MAX_SCREEN_COMPOSER_CHARS = 1_000_000


@dataclass(frozen=True, slots=True)
class ComposerScreenReading:
    """What one browser saw in a session's composer, and where in the stream.

    ``position`` is the output ring position the reading's screen was parsed up
    to, in ``stream`` (`TerminalStream.stream_id`); the core holding the terminal
    accepts the reading only when that is where its ring ends now. ``chars`` is the
    visible draft's length for ``draft`` and zero otherwise - a count and never
    the text, which never leaves the browser.
    """

    state: str
    chars: int
    position: int
    stream: str


def parse_screen_reading(frame: dict[str, object]) -> ComposerScreenReading | None:
    """A `composer_reading` frame from a browser, or None when it is malformed."""
    state = frame.get("state")
    position = frame.get("position")
    stream = frame.get("stream")
    chars = frame.get("chars", 0)
    if state not in SCREEN_COMPOSER_STATES:
        return None
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        return None
    if not isinstance(stream, str) or not stream or len(stream) > 256:
        return None
    if (
        not isinstance(chars, int)
        or isinstance(chars, bool)
        or not 0 <= chars <= MAX_SCREEN_COMPOSER_CHARS
    ):
        return None
    return ComposerScreenReading(
        state=str(state),
        chars=chars if state == "draft" else 0,
        position=position,
        stream=stream,
    )


# Two browsers reading the same screen can disagree only if one of them parsed it
# wrong, and the merge takes the reading that stops a delivery: text over a
# placeholder over empty, and any of those over a browser that could not read it.
_SCREEN_STATE_WEIGHT = {"unreadable": 0, "empty": 1, "placeholder": 2, "draft": 3}


def merge_screen_readings(
    standing: ComposerScreenReading | None, incoming: ComposerScreenReading
) -> ComposerScreenReading:
    """The reading to keep when ``incoming`` arrives for the screen ``standing``
    may already describe."""
    if (
        standing is None
        or standing.position != incoming.position
        or standing.stream != incoming.stream
    ):
        return incoming
    if _SCREEN_STATE_WEIGHT[incoming.state] > _SCREEN_STATE_WEIGHT[standing.state]:
        return incoming
    if incoming.state == standing.state == "draft" and incoming.chars > standing.chars:
        return incoming
    return standing


def screen_reading_wire(reading: ComposerScreenReading | None) -> dict[str, object] | None:
    if reading is None:
        return None
    return {
        "state": reading.state,
        "chars": reading.chars,
        "position": reading.position,
        "stream": reading.stream,
    }


def screen_composer_empty(
    reading: ComposerScreenReading | None, estimate: ComposerState | None
) -> bool | None:
    """What a current screen reading proves about the composer.

    True: it is empty. False: text is sitting in it. None: the screen proves
    nothing, and the caller falls back to what the input side says. A
    ``placeholder`` reading proves empty only beside an estimate of zero - the
    hint's wording is what identified it, and an operator could type that wording
    and move the caret to the front; requiring the estimate to agree means both
    sources have to be wrong together for that to read as empty.
    """
    if reading is None:
        return None
    if reading.state == "empty":
        return True
    if reading.state == "draft":
        return False
    if reading.state == "placeholder":
        return None if estimate is not None and estimate.pending else True
    return None


def reanchor_composer(
    state: ComposerState, reading: ComposerScreenReading, now: float
) -> str | None:
    """Correct the estimate from a current screen reading.

    Returns ``'pending'`` or ``'cleared'`` on an empty/non-empty crossing, exactly
    as a write does (`apply_composer_write`). ``empty`` zeroes the count; ``draft``
    replaces it with the visible length, which is what undoes an over-count (a word
    delete the estimate saw as one key) and an under-count (a history recall it
    never counted). A ``placeholder`` over a pending estimate is the one reading
    the screen cannot settle, so it changes nothing.
    """
    was_pending = state.pending
    if reading.state == "empty" or (reading.state == "placeholder" and not was_pending):
        state.chars = 0
    elif reading.state == "draft":
        state.chars = max(1, reading.chars)
    else:
        return None
    if state.pending == was_pending:
        return None
    state.since = now if state.pending else 0.0
    return "pending" if state.pending else "cleared"


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

    ``submits`` counts every write classified as a submit and ``last_submit_at``
    dates the latest, in epoch seconds. They are the switch watcher's evidence
    that the CLI was handed a line it could act on (`/new`, `/resume`, a prompt):
    a conversation cannot move without one, so an idle pane with no submit since
    its transcript was last written has nothing to look for
    (`SessionManager._switch_evidence_at`). Counted whether or not the CLI took it,
    because over-reporting costs one cheap probe and under-reporting a missed switch.
    """

    chars: int = 0
    since: float = 0.0
    in_paste: bool = False
    submits: int = 0
    last_submit_at: float = 0.0

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

    Called from the three seams that *prove* the composer no longer holds the body:
    a turn opening (whatever wrote the carriage return), an operator write that
    submits or discards the composer, and a current screen reading of an empty
    composer (`operator_input.note_composer_reading`). Nothing expires it on a timer - an
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
    if write.kind == "submit":
        state.submits += 1
        state.last_submit_at = now
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
