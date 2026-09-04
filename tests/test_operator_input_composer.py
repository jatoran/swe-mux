"""What an operator write does to a queue delivery the CLI never submitted.

The mark that stops deliveries piling into one composer has to retire on real
evidence or it becomes a jam of its own. There are exactly two kinds of proof: a
turn opening (`test_delivery_readiness_evidence.py`), and an operator write that
submits or discards the composer — which is what actually rescued three of the
2026-09-04 messages, a person pressing Enter over them.

The exclusion is the load-bearing half. The queue's own carriage return is
classified as a submit by the same rules whether or not the CLI took it, so
retiring the mark on it would retire it on precisely the evidence that was wrong.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from swe_mux.composer_input import ComposerState, note_unsubmitted_delivery
from swe_mux.routes import terminal as terminal_routes

NOW = 1_800_000_000.0


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))


def _session(backend: str = "codex") -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(id="s1", backend=backend),
        composer=ComposerState(),
        state_transitions=[],
        pending_submit=None,
    )


def _write(session: Any, data: str, source: str) -> None:
    terminal_routes._note_composer_write(_Events(), session, data, source)


def test_an_operator_submit_retires_an_unsubmitted_delivery() -> None:
    session = _session()
    note_unsubmitted_delivery(session, "msg-1", 4186, NOW)

    _write(session, "\r", "browser")

    assert session.pending_submit is None


def test_clearing_the_composer_retires_it_too() -> None:
    session = _session()
    note_unsubmitted_delivery(session, "msg-1", 4186, NOW)

    _write(session, "\x03", "browser")

    assert session.pending_submit is None


def test_ordinary_typing_leaves_it_standing() -> None:
    """A draft typed beside the stuck body does not mean the body went anywhere."""
    session = _session()
    note_unsubmitted_delivery(session, "msg-1", 4186, NOW)

    _write(session, "wait, what happened", "browser")

    assert session.pending_submit is not None


def test_the_queues_own_carriage_return_does_not_retire_it() -> None:
    session = _session()
    note_unsubmitted_delivery(session, "msg-1", 4186, NOW)

    _write(session, "\r", "queue")

    standing = session.pending_submit
    assert standing is not None and standing.message_id == "msg-1"
