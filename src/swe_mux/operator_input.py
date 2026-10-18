"""Operator input: every write into a session's terminal that is not a browser
keystroke, with the accounting that delivery readiness and the composer estimate
depend on.

The REST input route, broadcast, voice, approvals, the agent-control interrupt and
graceful end, the prompt queue and staged spawn text all write through
`record_operator_input`, which writes through the session's terminal core
(`TerminalCore.write_operator_input`) - the only writer a PTY has. A browser's own
keystrokes take the core's connection path instead, which accounts per keystroke
kind (`terminal_core.connection`). Automation `write_pty` and process-action
interrupts call `TerminalCore.write` unaccounted: they are not operator input, and
advancing the operator-quiet clock for them would mask a different hole
(`delivery-readiness.md`).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .composer_input import (
    DEFAULT_CLEAR_KEYS,
    DEFAULT_NEWLINE_KEYS,
    ComposerScreenReading,
    apply_composer_write,
    classify_composer_write,
    clear_pending_submit,
    composer_insertion,
    reanchor_composer,
)
from .event_bus import EventBus
from .harness import HARNESSES, composer_insertion_rules
from .observation import note_interrupt_intent
from .session import note_remote_shell_submission
from .terminal_core import WriteCondition

log = logging.getLogger(__name__)


def composer_insertion_for(backend: object, text: str) -> str:
    """The bytes that leave ``text`` unsent in this backend's composer.

    One resolver for every daemon-side path that stages text rather than typing
    it, so a harness quirk is declared once in the registry instead of being
    re-derived per call site.
    """
    newline_keys, lift = composer_insertion_rules(backend)
    return composer_insertion(text, newline_keys=newline_keys, lift_leading_newline=lift)


def note_composer_write(events: EventBus, session: Any, data: str | bytes, source: str) -> None:
    """Track what one operator write left sitting in the composer.

    Every path that writes operator text to a PTY calls this, for the same reason
    every one of them advances `input_revision`: text inserted by a voice append,
    a send-to-agent, or a mobile draft is as unsent as text someone typed, and a
    row that only lit up for keystrokes would be silent on exactly the paths that
    stage text and walk away.

    Only the empty/non-empty crossing is announced. A keystroke-rate event would
    put one fanout on the bus per character typed, which is the traffic the
    throttled `terminal_input` event already exists to avoid.
    """
    composer = getattr(session, "composer", None)
    if composer is None:
        return
    text = data.decode("utf-8", "ignore") if isinstance(data, bytes) else data
    # Which keys empty a composer is the harness's fact, not this module's. An
    # unregistered backend keeps the historical Ctrl+U.
    harness = HARNESSES.get(session.record.backend)
    clear_keys = harness.composer_clear_keys if harness else DEFAULT_CLEAR_KEYS
    newline_keys = harness.composer_newline if harness else DEFAULT_NEWLINE_KEYS
    write = classify_composer_write(text, composer.in_paste, clear_keys, newline_keys)
    # A write from anywhere *but* the queue that submits or discards the composer
    # is proof the body a failed delivery left there is gone: the operator either
    # pressed Enter over it themselves (which is how three of the 2026-09-04
    # messages finally reached Codex) or cleared it. The queue's own carriage
    # return is excluded deliberately — it classifies as a submit whether or not
    # the CLI took it, so trusting it here would retire the mark on exactly the
    # evidence that was wrong in the first place.
    if source != "queue" and write.kind in {"submit", "clear"}:
        retired = clear_pending_submit(session)
        if retired is not None:
            log.info(
                "session %s: queue delivery %s left the composer by %s (%s)",
                session.record.id,
                retired.message_id,
                write.kind,
                source,
            )
    change = apply_composer_write(composer, write, time.time())
    if change is not None:
        _announce_composer_change(events, session, change, source)


def note_composer_reading(events: EventBus, session: Any, reading: ComposerScreenReading) -> None:
    """Correct the composer estimate from a current screen reading.

    The terminal core has already established that ``reading`` describes the
    composer as it is now (`TerminalCore.composer_screen`), so this is the screen
    overruling a count wherever the two disagree: a word delete the estimate saw as
    one key, a history recall it never counted, a slash menu that consumed the
    keys. A reading of ``empty`` is also proof that the body a failed delivery left
    in the composer is gone, the same proof an operator's own submit or clear is.
    """
    composer = getattr(session, "composer", None)
    if composer is None:
        return
    if reading.state == "empty":
        retired = clear_pending_submit(session)
        if retired is not None:
            log.info(
                "session %s: queue delivery %s is no longer in the composer (screen read empty)",
                session.record.id,
                retired.message_id,
            )
    change = reanchor_composer(composer, reading, time.time())
    if change is not None:
        _announce_composer_change(events, session, change, "screen")


def _announce_composer_change(events: EventBus, session: Any, change: str, source: str) -> None:
    """Ledger, log and announce one empty/non-empty crossing of the composer."""
    composer = session.composer
    ledger = getattr(session, "state_transitions", None)
    if ledger is not None:
        ledger.append(
            {"ts": time.time(), "kind": "composer", "action": change, "source": source}
        )
    log.debug(
        "composer %s for session %s (source %s)", change, session.record.id, source
    )
    events.emit_background(
        "composer_input_changed",
        session_id=session.record.id,
        source=source,
        pending=composer.pending,
    )


def session_accepts_input(session: Any) -> bool:
    """Whether this session still has a child that could receive keystrokes.

    An ended pane stays visible until it is dismissed, and a cold one is visible
    from the moment the daemon starts, so both are panes a person can click into
    and type at. Neither has a PTY: `PtyHost.write` raises for a released or
    never-spawned pseudoterminal, which arrives as a 500 on the HTTP paths and as
    a dropped socket on the WebSocket one. Refusing here makes it an ordinary,
    explainable "this session has ended" instead.
    """
    return str(getattr(session.record, "state", "")) not in {"exited", "crashed"}


def record_operator_input(
    events: EventBus,
    session: Any,
    data: str,
    *,
    source: str,
    input_owner: bool = True,
) -> None:
    """Write operator-originated text to a PTY with full evidence accounting.

    Every human-input path must advance `input_revision`/`last_input_event_ts`
    and emit `terminal_input`, or delivery-readiness reports
    `partial_input_absent`/`operator_quiet` as satisfied for text the operator
    just sent — corrupting the shadow-metric baseline Phase 5 promotion is
    validated against. The browser path does its own (throttled) accounting; this
    is the shared path for everything else.

    Does not wait, wherever the terminal's core runs: a write to the terminal
    gateway is sent in order with every other one (`remote.RemoteTerminal`).
    """
    if not session_accepts_input(session):
        raise ValueError("session has ended and cannot accept input")
    session.terminal.write_operator_input(data)
    _account_operator_input(events, session, data, source=source, input_owner=input_owner)


async def deliver_operator_input(
    events: EventBus,
    session: Any,
    data: str,
    *,
    source: str,
    input_owner: bool = True,
    condition: WriteCondition | None = None,
) -> None:
    """`record_operator_input` for a write that carries the verdict it was decided on.

    The condition is checked where the keystrokes arrive - in process, or in the
    terminal gateway - with nothing able to land between the check and the
    write; when it no longer holds nothing is written, `WriteRefused` is raised,
    and none of the accounting happens.
    """
    if not session_accepts_input(session):
        raise ValueError("session has ended and cannot accept input")
    await session.terminal.write_when(data, condition=condition, accounted=True)
    _account_operator_input(events, session, data, source=source, input_owner=input_owner)


def _account_operator_input(
    events: EventBus, session: Any, data: str, *, source: str, input_owner: bool
) -> None:
    """What every operator write owes after it is written."""
    note_remote_shell_submission(session, data)
    note_composer_write(events, session, data, source)
    note_interrupt_intent(session, data, source=source)
    events.emit_background(
        "terminal_input",
        session_id=session.record.id,
        source=source,
        input_owner=input_owner,
        bytes=len(data.encode("utf-8")),
    )
