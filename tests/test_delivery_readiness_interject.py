"""The mid-turn delivery predicate, which is narrower than `safe` and never wider.

`delivery_state == "blocked"` covers a dozen unrelated situations and only one
of them is "the agent is busy". The rest are an approval dialog, a model picker,
an elicitation, a rate limit, a retired transcript, a remote shell. Writing into
any of those is corruption, not urgency - so `interject_state` does not ask
whether a block is overridable. It asks for a positive, corroborated reading that
a turn is running and nothing else is true, and every test below states what it
still refuses.
"""

from __future__ import annotations

import time
from typing import Any

from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.models import MuxEvent
from tests.support.detection_replay import ReplaySession, VirtualClock

# What Claude and Codex both paint while a turn runs; the screen rules in
# `session.py` read it as `working`, and read an approval dialog or a picker as
# something else *first*, which is what closes the window between a dialog
# appearing and the daemon recording `awaiting`.
WORKING_SCREEN = b"\x1b[?1049h\r\n  Thinking... (esc to interrupt)\r\n"
APPROVAL_SCREEN = b"\x1b[?1049h\r\n  Do you want to proceed?\r\n  enter to confirm\r\n"
PICKER_SCREEN = b"\x1b[?1049h\r\n  Select a model\r\n  enter to select  esc to cancel\r\n"


def _event(event_type: str, **payload: Any) -> MuxEvent:
    return MuxEvent(
        ts=time.time(),
        session_id="replay-session",
        source="transcript",
        type=event_type,
        payload=payload,
    )


def _working_agent(
    *, screen: bytes = WORKING_SCREEN
) -> tuple[ReplaySession, DeliveryReadinessTracker, VirtualClock]:
    """A session whose root turn completed once and is now running another."""
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    session.screen.feed(b"\x1b[?1049h")
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed"), session)
    clock.advance(5.0)
    # The operator submits the next prompt, then the CLI starts the turn.
    session.input_revision += 1
    session.last_input_event_ts = clock.monotonic()
    session.record.state = "working"
    session.scrollback.data = screen
    tracker.observe(_event("turn_started"), session)
    clock.advance(30.0)
    return session, tracker, clock


def test_a_running_turn_is_blocked_for_delivery_and_safe_to_interject() -> None:
    session, tracker, _clock = _working_agent()
    result = tracker.evaluate(session)
    assert result["delivery_state"] == "blocked"
    assert result["reasons"] == ["root_agent_working"]
    assert result["interject_state"] == "safe"
    assert result["interject_reasons"] == []


def test_an_idle_session_takes_an_interject_as_an_ordinary_delivery() -> None:
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    session.screen.feed(b"\x1b[?1049h")
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed"), session)
    clock.advance(5.0)
    result = tracker.evaluate(session)
    assert result["delivery_state"] == "safe"
    assert result["interject_state"] == "safe"


def test_an_approval_prompt_is_never_interjectable() -> None:
    """The dangerous case, and the reason the screen has to corroborate.

    There is a window where the CLI has painted an approval dialog and the
    daemon has not yet recorded `awaiting`. Lifecycle evidence still says
    `working`; text pasted then *answers the dialog*. The screen rules classify
    the dialog before any working marker is considered, so the window closes.
    """
    session, tracker, _clock = _working_agent(screen=APPROVAL_SCREEN)
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "screen_does_not_show_a_running_turn" in result["interject_reasons"]

    # And once the daemon does know, the block is named directly too.
    tracker.observe(_event("approval_detected", kind="approval"), session)
    after = tracker.evaluate(session)
    assert after["interject_state"] == "blocked"
    assert "approval_required" in after["interject_reasons"]


def test_a_picker_or_viewer_is_not_a_running_turn() -> None:
    session, tracker, _clock = _working_agent(screen=PICKER_SCREEN)
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "screen_does_not_show_a_running_turn" in result["interject_reasons"]


def test_an_unreadable_screen_is_not_corroboration() -> None:
    """Absence of evidence is not evidence: a tail with no working marker blocks."""
    session, tracker, _clock = _working_agent(screen=b"\x1b[?1049h\r\nsome output\r\n")
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "screen_does_not_show_a_running_turn" in result["interject_reasons"]


def test_an_operator_draft_typed_during_the_turn_blocks() -> None:
    """The composer boundary a mid-turn write would land on top of.

    `input_revision_at_completion` is None during a turn by construction, so the
    ordinary `partial_input_absent` check cannot see this; the interject
    predicate keeps its own boundary, taken when the turn started.
    """
    session, tracker, clock = _working_agent()
    session.input_revision += 1
    session.last_input_event_ts = clock.monotonic()
    clock.advance(30.0)
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "composer_touched_since_turn_start" in result["interject_reasons"]


def test_an_operator_typing_right_now_blocks_on_the_ordinary_debounce_too() -> None:
    session, tracker, clock = _working_agent()
    session.last_input_event_ts = clock.monotonic()
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "operator_recently_typed" in result["interject_reasons"]


def test_a_rate_limited_session_is_not_interjectable() -> None:
    session, tracker, _clock = _working_agent()
    tracker.observe(_event("rate_limited"), session)
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "provider_rate_limit" in result["interject_reasons"]


def test_a_retired_transcript_blocks_a_mid_turn_write() -> None:
    session, tracker, _clock = _working_agent()
    session.record.observation_stale_since = time.time()
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "transcript_stale" in result["interject_reasons"]


def test_a_remote_terminal_boundary_blocks_a_mid_turn_write() -> None:
    session, tracker, _clock = _working_agent()
    session.record.runtime_boundary = "remote"
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "remote_terminal_boundary" in result["interject_reasons"]


def test_stale_lifecycle_evidence_blocks_even_though_the_screen_agrees() -> None:
    """A screen saying "working" cannot stand in for knowing the turn is live.

    An observation pipeline that died mid-turn leaves a claim nobody is
    updating, and the last frame it painted keeps saying `working` forever.
    """
    session, tracker, clock = _working_agent()
    clock.advance(3600.0)
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "lifecycle_evidence_stale" in result["interject_reasons"]


def test_a_replaced_run_blocks_a_mid_turn_write() -> None:
    """A new conversation discards the memory the predicate is built out of.

    The tracker mints fresh memory for a new `agent_run_id`, so the turn-start
    composer boundary the interject check needs is gone with it — the successor
    has to prove itself before anything writes into it, which is the same rule
    the ordinary readiness contract applies to a run replacement.
    """
    session, tracker, _clock = _working_agent()
    session.record.agent_run_id = "run-replaced"
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "turn_start_input_boundary_unknown" in result["interject_reasons"]


def test_an_unproven_observation_channel_blocks_a_mid_turn_write() -> None:
    """No hook and no transcript has ever spoken for this session.

    A screen marker alone is exactly the evidence tier the codebase refuses to
    grant safety on, and a mid-turn write is the least forgiving thing to grant.
    """
    session, tracker, _clock = _working_agent()
    session.record.parser_events_seen = 0
    tracker._sessions["replay-session"].source = "pty"
    tracker._sessions["replay-session"].observation_proven = False
    result = tracker.evaluate(session)
    assert result["interject_state"] == "blocked"
    assert "observation_capability_unknown" in result["interject_reasons"]
