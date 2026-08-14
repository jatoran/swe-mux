"""Attention read/unread: the semantic turn counter and the read mark on it.

The sidebar's unread tier used to compare `last_activity_ts` - a PTY-byte
timestamp - against a mark held in one browser's memory. Both halves were wrong,
and each failed in a different direction: a resize repainted every attached TUI
and lit up a whole project, while a reload silently marked the entire fleet read.
These tests pin the replacement contract from both ends.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.models import SessionRecord
from swe_mux.session import (
    acknowledge_turns,
    apply_state_transition,
    is_turn_completion,
    mark_unread,
)


def _session() -> Any:
    """The smallest thing `apply_state_transition` accepts, with a real record."""
    return SimpleNamespace(
        record=SessionRecord(
            "s1", "codex-s1", "p1", "codex", "native-s1", ".", "codex.exe", [], state="starting"
        ),
        state_source_priority=-1,
        state_transitions=[],
        state_changes=[],
        status_health_counters={},
        terminal_latencies=[],
        observation_state={},
        last_state_change_ts=0.0,
        last_state_change_monotonic=0.0,
        last_evidence_ts=0.0,
    )


def _settle(session: Any, state: str, *, source: str = "hook", evidence: str = "Stop") -> bool:
    return apply_state_transition(
        session, state, None, source=source, evidence=evidence  # type: ignore[arg-type]
    )


# --- which transitions are turns -------------------------------------------


@pytest.mark.parametrize(
    ("previous", "state", "expected"),
    [
        # The agent finished speaking, or wants an approval. Both are news.
        ("working", "idle", True),
        ("working", "awaiting", True),
        ("idle", "awaiting", True),
        ("starting", "awaiting", True),
        # The CLI finished booting. Counting this made every freshly spawned
        # session unread before it had said anything.
        ("starting", "idle", False),
        # The human answered the approval: their own action, not news for them.
        ("awaiting", "idle", False),
        # An approval whose detail changed is still the same approval.
        ("awaiting", "awaiting", False),
        # Nothing has settled yet.
        ("idle", "working", False),
        ("working", "working", False),
        # Death is not a turn; an ended session carries its own muted styling.
        ("working", "exited", False),
        ("working", "crashed", False),
    ],
)
def test_only_the_agent_yielding_the_floor_counts_as_a_turn(
    previous: str, state: str, expected: bool
) -> None:
    assert is_turn_completion(previous, state) is expected  # type: ignore[arg-type]


# --- the counter, driven through the real transition contract ---------------


def test_a_finished_turn_advances_the_counter_and_ledgers_its_evidence() -> None:
    session = _session()
    assert session.record.turn_seq == 0
    # Boot: ready for its first prompt, which is not a turn.
    _settle(session, "idle", source="pty", evidence="idle_prompt")
    assert session.record.turn_seq == 0

    _settle(session, "working", evidence="UserPromptSubmit")
    assert session.record.turn_seq == 0
    _settle(session, "idle", evidence="Stop")
    assert session.record.turn_seq == 1
    assert session.record.last_turn_end_ts > 0
    assert session.record.last_turn_evidence == "hook:Stop"

    # Carried on the transition that completed the turn rather than as a second
    # ledger entry, so "why did this row light up?" is answerable from the state
    # log without doubling the durable timeline's write rate.
    entry = session.state_transitions[-1]
    assert entry["turn_seq"] == 1
    assert (entry["previous"], entry["state"]) == ("working", "idle")
    assert entry["evidence"] == "Stop"
    # Every other transition stays exactly as wide as it was.
    assert all("turn_seq" not in item for item in session.state_transitions[:-1])


def test_repeated_evidence_for_one_turn_counts_it_once() -> None:
    # The hook and the transcript both report the same turn ending, milliseconds
    # apart. The second is a no-op transition, so it must not be a second turn.
    session = _session()
    _settle(session, "working", evidence="UserPromptSubmit")
    _settle(session, "idle", source="hook", evidence="Stop")
    _settle(session, "idle", source="transcript", evidence="turn_ended")
    assert session.record.turn_seq == 1


def test_an_approval_counts_from_any_state_but_itself() -> None:
    session = _session()
    _settle(session, "working", evidence="UserPromptSubmit")
    apply_state_transition(
        session, "awaiting", None, source="hook", evidence="tool", awaiting_reason="approval"
    )
    assert session.record.turn_seq == 1
    # A detail change on the same approval is not a new one.
    apply_state_transition(
        session, "awaiting", "Bash", source="hook", evidence="tool", awaiting_reason="approval"
    )
    assert session.record.turn_seq == 1
    # Answering it is the human's own action.
    _settle(session, "idle", evidence="PostToolUse")
    assert session.record.turn_seq == 1


def test_a_refused_transition_never_counts_a_turn() -> None:
    # Terminal latch: a late hook must not resurrect a dead session, and must
    # certainly not announce a turn on one.
    session = _session()
    apply_state_transition(session, "exited", None, source="daemon", evidence="process_exit")
    before = session.record.turn_seq
    _settle(session, "idle", evidence="Stop")
    assert session.record.state == "exited"
    assert session.record.turn_seq == before


def test_a_lower_priority_source_cannot_count_a_turn_it_lost() -> None:
    # Arbitration rejects the PTY's guess while the hook owns the state; a
    # rejected transition is not a turn either.
    session = _session()
    _settle(session, "working", source="hook", evidence="UserPromptSubmit")
    refused = apply_state_transition(session, "idle", None, source="pty", evidence="idle_prompt")
    assert refused is False
    assert session.record.turn_seq == 0


def test_the_counter_survives_a_daemon_restart() -> None:
    # Live sessions are adopted from the supervisor's snapshot, so the counter
    # and its acknowledgement have to round-trip through it - otherwise every
    # reload of the daemon marks the fleet read.
    session = _session()
    _settle(session, "working", evidence="UserPromptSubmit")
    _settle(session, "idle", evidence="Stop")
    acknowledge_turns(session.record, 1)
    adopted = SessionRecord.from_snapshot(session.record.snapshot())
    assert adopted.turn_seq == 1
    assert adopted.read_turn_seq == 1
    assert adopted.last_turn_evidence == "hook:Stop"


def test_a_snapshot_from_an_older_daemon_adopts_as_caught_up() -> None:
    # Neither field exists in metadata written before this shipped. Defaulting
    # both to zero reads as "nothing unacknowledged", which is the safe
    # direction: a wall of false unread on upgrade would train the user to
    # ignore the tier.
    record = SessionRecord.from_snapshot(
        {"id": "s1", "name": "n", "project_id": "p", "backend": "codex",
         "native_session_id": "x", "cwd": ".", "exe": "codex.exe", "args": []}
    )
    assert record.turn_seq == 0
    assert record.read_turn_seq == 0


# --- acknowledgement --------------------------------------------------------


def test_acknowledgement_is_monotone_and_clamped_to_reality() -> None:
    record = SessionRecord("s1", "n", "p", "codex", "x", ".", "codex.exe", [])
    record.turn_seq = 3

    assert acknowledge_turns(record, 2) is True
    assert record.read_turn_seq == 2
    assert record.read_at is not None

    # A device that is behind cannot un-read what another already cleared.
    assert acknowledge_turns(record, 1) is False
    assert record.read_turn_seq == 2

    # Nor can a client acknowledge a turn that has not happened: doing so would
    # swallow the next real one silently.
    assert acknowledge_turns(record, 99) is True
    assert record.read_turn_seq == 3
    record.turn_seq = 4
    assert record.read_turn_seq == 3

    # No argument means "everything counted so far".
    assert acknowledge_turns(record) is True
    assert record.read_turn_seq == 4
    assert acknowledge_turns(record) is False


def test_a_hand_marked_session_stays_unread_until_the_user_or_a_turn_clears_it() -> None:
    record = SessionRecord("s1", "n", "p", "codex", "x", ".", "codex.exe", [])
    record.turn_seq = 3
    assert acknowledge_turns(record) is True
    assert record.read_turn_seq == 3

    # The one thing allowed to move the mark backwards, and only back to just
    # before the latest turn: "I have not read the last thing this agent said".
    assert mark_unread(record) is True
    assert record.unread_pin is True
    assert record.read_turn_seq == 2
    assert record.read_at is None
    # Idempotent, so a second click publishes nothing.
    assert mark_unread(record) is False

    # The dwell timer looking at the same pane must not undo it.
    assert acknowledge_turns(record, 3) is False
    assert record.read_turn_seq == 2
    assert record.unread_pin is True

    # The user saying so does.
    assert acknowledge_turns(record, 3, explicit=True) is True
    assert record.unread_pin is False
    assert record.read_turn_seq == 3


def test_an_explicit_read_clears_the_pin_even_with_no_turn_to_advance_over() -> None:
    # A session marked unread before it ever completed a turn has nothing for the
    # mark to move over, so clearing the pin is the whole state change - and it
    # has to report as one, or the row stays lit with no way out.
    record = SessionRecord("s1", "n", "p", "codex", "x", ".", "codex.exe", [])
    assert mark_unread(record) is True
    assert (record.unread_pin, record.read_turn_seq) == (True, 0)
    assert acknowledge_turns(record, explicit=True) is True
    assert record.unread_pin is False
    assert acknowledge_turns(record, explicit=True) is False


def test_a_new_turn_retires_a_hand_set_unread_mark() -> None:
    # The mark is about the turns the user has already been offered. Leaving the
    # pin set once the agent speaks again would suppress the dwell
    # acknowledgement of every future turn as well.
    session = _session()
    session.record.state = "working"
    mark_unread(session.record)
    assert _settle(session, "idle") is True
    assert session.record.turn_seq == 1
    assert session.record.unread_pin is False
    # And the ordinary counter comparison keeps the row unread anyway.
    assert session.record.read_turn_seq < session.record.turn_seq


# --- the endpoint -----------------------------------------------------------


def _read_request(session: Any, body: Any, events: Any) -> Any:
    class SessionsStub:
        def resolve(self, identity: str) -> Any:
            return session

    async def json_body() -> Any:
        return body

    return SimpleNamespace(
        app={"sessions": SessionsStub(), "events": events},
        match_info={"sid": "s1"},
        body_exists=body is not None,
        json=json_body,
    )


async def test_the_read_endpoint_acknowledges_publishes_and_stays_idempotent() -> None:
    from swe_mux.server import mark_session_read

    emitted: list[tuple[str, dict[str, Any]]] = []
    published: list[int] = []

    class EventsStub:
        async def emit(self, event_type: str, **payload: Any) -> None:
            emitted.append((event_type, payload))

    session = _session()
    session.publish_update = lambda: published.append(1)
    session.record.turn_seq = 2
    events = EventsStub()

    response = await mark_session_read(_read_request(session, {"turn_seq": 2}, events))
    payload = json.loads(response.text)
    assert payload["read_turn_seq"] == 2
    assert payload["turn_seq"] == 2
    # Other devices hold their own copy of the mark; the event is what converges
    # them, and the pane update is what refreshes this one.
    assert published == [1]
    assert emitted[-1][0] == "session_read"
    assert emitted[-1][1]["turn_seq"] == 2

    # A replayed or duplicate acknowledgement is a no-op, not a second event.
    response = await mark_session_read(_read_request(session, {"turn_seq": 2}, events))
    assert json.loads(response.text)["read_turn_seq"] == 2
    assert published == [1]
    assert len(emitted) == 1


async def test_the_read_endpoint_rejects_a_nonsense_cursor() -> None:
    from swe_mux.server import mark_session_read

    class EventsStub:
        async def emit(self, event_type: str, **payload: Any) -> None:
            return None

    session = _session()
    session.publish_update = lambda: None
    session.record.turn_seq = 2

    for body in (
        {"turn_seq": -1},
        {"turn_seq": "2"},
        {"turn_seq": True},
        {"read": "true"},
        {"read": 1},
        [],
    ):
        with pytest.raises(ValueError):
            await mark_session_read(_read_request(session, body, EventsStub()))

    # An empty body means "everything counted so far", which is what a client
    # with no cursor of its own sends.
    session.record.read_turn_seq = 0
    await mark_session_read(_read_request(session, None, EventsStub()))
    assert session.record.read_turn_seq == 2


async def test_the_read_endpoint_separates_the_dwell_timer_from_the_user() -> None:
    """The two writers of this endpoint must not be able to impersonate each other.

    `{"turn_seq": N}` is the dwell timer catching up an on-screen pane;
    `{"read": false}` and `{"read": true}` are the menu item. Only the latter may
    move the mark backwards, and only the latter may clear a mark it set.
    """
    from swe_mux.server import mark_session_read

    emitted: list[tuple[str, dict[str, Any]]] = []

    class EventsStub:
        async def emit(self, event_type: str, **payload: Any) -> None:
            emitted.append((event_type, payload))

    session = _session()
    session.publish_update = lambda: None
    session.record.turn_seq = 3
    session.record.read_turn_seq = 3
    events = EventsStub()

    payload = json.loads(
        (await mark_session_read(_read_request(session, {"read": False}, events))).text
    )
    assert (payload["unread_pin"], payload["read_turn_seq"]) == (True, 2)
    assert emitted[-1][1]["unread"] is True

    # The pane is still on screen, so the dwell timer keeps firing. It must not
    # win, and it must not emit - a converging device would show the row read.
    before = len(emitted)
    await mark_session_read(_read_request(session, {"turn_seq": 3}, events))
    assert session.record.read_turn_seq == 2
    assert session.record.unread_pin is True
    assert len(emitted) == before

    payload = json.loads(
        (await mark_session_read(_read_request(session, {"read": True}, events))).text
    )
    assert (payload["unread_pin"], payload["read_turn_seq"]) == (False, 3)
    assert emitted[-1][1]["unread"] is False
