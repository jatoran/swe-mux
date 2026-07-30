"""What delivery readiness treats as evidence, and what it refuses to.

These pin the 2026-07-30 correction. The gate had four preconditions that a real
session could not satisfy, so `safe` was unreachable and every queued message
had to be sent with the operator's explicit override — which trains the operator
to click through the one prompt that is supposed to mean something:

- an attached browser and an exclusive input owner were *required*, so any
  session the operator was not looking at was blocked, which is the entire
  population a queue exists for;
- the alternate screen was treated as danger, but Claude Code draws its prompt
  there and never leaves;
- lifecycle evidence expired after five minutes, so an agent parked at its
  prompt — the most deliverable state there is — decayed to unknown.

Each fix is a loosening, so each test below states what still blocks.
"""

from __future__ import annotations

import time
from typing import Any

from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.models import MuxEvent
from tests.support.detection_replay import ReplaySession, VirtualClock


def _event(event_type: str, **payload: Any) -> MuxEvent:
    return MuxEvent(
        ts=time.time(),
        session_id="replay-session",
        source="transcript",
        type=event_type,
        payload=payload,
    )


def _settled(session: ReplaySession, *, records: int) -> None:
    """What the observer leaves behind when its catch-up settles to idle."""
    session.observation_state["catchup_settled"] = {
        "records": records,
        "input_revision": session.input_revision,
        "screen": session.screen.mode,
    }


def _idle_agent(
    backend: str = "claude", *, screen: bytes = b""
) -> tuple[ReplaySession, DeliveryReadinessTracker, VirtualClock]:
    """A session whose root turn completed and which nothing has touched since.

    ``screen`` is PTY output fed before the turn ends, so it becomes the screen
    the CLI was on when it finished — the baseline a later change is measured
    against.
    """
    clock = VirtualClock()
    session = ReplaySession(backend, clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    session.screen.feed(screen)
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed"), session)
    clock.advance(5.0)
    return session, tracker, clock


def test_an_unwatched_session_is_deliverable() -> None:
    """No browser, no input owner — a daemon PTY write does not need either.

    This is the regression: delivery is the daemon writing to a PTY it owns, and
    requiring a rendering pane made the queue useless for exactly the sessions
    the operator had left running while working somewhere else.
    """
    session, tracker, _clock = _idle_agent()
    session.screen.feed(b"\x1b[?1049h")
    session.subscribers = set()
    session.input_owner = None

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "safe"
    assert evaluation["evidence"]["terminal_observer_connected"] is False


def test_the_alternate_screen_is_where_claude_lives() -> None:
    session, tracker, _clock = _idle_agent()
    session.screen.feed(b"\x1b[?1049h")

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "safe"
    assert evaluation["evidence"]["screen_mode"] == "alternate"
    assert evaluation["evidence"]["screen_source"] == "daemon"


def test_a_claude_session_off_its_own_screen_is_blocked() -> None:
    """The check still bites, in the direction that means something.

    Claude Code entered the alternate screen at startup; the normal buffer means
    its TUI is no longer what this PTY is showing.
    """
    session, tracker, _clock = _idle_agent()
    session.screen.feed(b"\x1b[?1049h")
    session.screen.feed(b"\x1b[?1049l")

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "screen_not_at_agent_prompt" in evaluation["reasons"]


def test_codex_leaving_its_screen_after_the_turn_is_blocked() -> None:
    """Mux launches Codex with `tui.alternate_screen="never"` (`codex_tui.py`)."""
    session, tracker, _clock = _idle_agent("codex")
    session.screen.feed(b"\x1b[?1049h")

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "screen_not_at_agent_prompt" in evaluation["reasons"]


def test_the_screen_at_completion_outranks_the_adapters_declaration() -> None:
    """A CLI configured against mux's default is not a takeover.

    Codex accepts an explicit `tui.alternate_screen` override, and a session that
    finished its turn on the alternate screen is a session whose prompt is there.
    Anchoring to what was on screen when the turn completed needs no per-version
    or per-configuration knowledge to get that right.
    """
    session, tracker, _clock = _idle_agent("codex", screen=b"\x1b[?1049h")

    evaluation = tracker.evaluate(session)
    assert evaluation["evidence"]["completion_screen"] == "alternate"
    assert evaluation["delivery_state"] == "safe"


def test_a_screen_change_after_the_turn_completed_is_blocked() -> None:
    """The takeover this check exists for, on the evidence that can see it."""
    session, tracker, _clock = _idle_agent(screen=b"\x1b[?1049h")
    session.screen.feed(b"\x1b[?1049l")

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "screen_not_at_agent_prompt" in evaluation["reasons"]


def test_the_daemons_screen_reading_outranks_a_browser_report() -> None:
    """A pane that detached mid-pager must not keep vouching for the screen."""
    session, tracker, clock = _idle_agent()
    session.screen.feed(b"\x1b[?1049l")
    session.terminal_mode = "alternate"
    session.terminal_mode_updated_at = clock.monotonic()

    evaluation = tracker.evaluate(session)
    assert evaluation["evidence"]["screen_source"] == "daemon"
    assert evaluation["delivery_state"] == "blocked"


def test_a_browser_report_alone_never_blocks() -> None:
    """xterm reports the buffer *its own replay* selected, not the child's.

    Measured on a live Claude session after a daemon restart: the child had been
    on the alternate screen since startup, but its `?1049h` had long scrolled out
    of the retained scrollback, so the reattached pane replayed a stream that
    never entered it and reported `normal`. Blocking on that would strand exactly
    the long-running sessions this whole correction is for.
    """
    session, tracker, clock = _idle_agent()
    session.terminal_mode = "normal"
    session.terminal_mode_updated_at = clock.monotonic()

    evaluation = tracker.evaluate(session)
    assert evaluation["evidence"]["screen_source"] == "browser"
    assert evaluation["evidence"]["screen_mode"] == "normal"
    assert evaluation["delivery_state"] == "safe"


def test_absent_screen_evidence_is_not_a_block() -> None:
    """Missing evidence is missing, not damning — the other checks carry it."""
    session, tracker, _clock = _idle_agent()

    evaluation = tracker.evaluate(session)
    assert evaluation["evidence"]["screen_mode"] == "unknown"
    assert evaluation["delivery_state"] == "safe"


def test_an_agent_parked_at_its_prompt_does_not_go_stale() -> None:
    session, tracker, clock = _idle_agent()
    session.screen.feed(b"\x1b[?1049h")
    clock.advance(4 * 3600)

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "safe"
    assert evaluation["checks"]["lifecycle_evidence_fresh"] is True


def test_a_working_session_still_goes_stale() -> None:
    """The freshness bound exists for claims nobody is updating; keep it there."""
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    tracker.observe(_event("turn_started"), session)
    session.record.state = "idle"  # the status layer moved on; the tracker did not
    clock.advance(4 * 3600)

    evaluation = tracker.evaluate(session)
    assert evaluation["checks"]["lifecycle_evidence_fresh"] is False
    assert evaluation["delivery_state"] == "blocked"


def test_catchup_restores_readiness_for_a_session_idle_since_before_the_restart() -> None:
    """The tracker's memory dies with the daemon; the transcript does not.

    Readiness is held in process, so after a restart a session that was already
    idle had no record that its last root turn finished and no live record to
    prove its parser works — and could not produce either without a new turn. So
    every queued message to a session left running over a restart wanted the
    operator's override, which is the same failure as the four above wearing a
    different hat.
    """
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.screen.feed(b"\x1b[?1049h")
    _settled(session, records=5)

    # Adopted at the first read, then held for the ordinary settle debounce.
    assert tracker.evaluate(session)["reason"] == "readiness_debounce_pending"
    clock.advance(5.0)
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "safe"
    assert evaluation["evidence"]["root_reason"] == "root_turn_settled_after_catchup"


def test_the_settle_is_read_however_late_the_reader_arrives() -> None:
    """Adoption catches observers up long before the fleet subscribes to the bus.

    The conclusion is left on the session precisely so that ordering cannot lose
    it, which an announcement did: the one live session whose observer settled
    during startup emitted its event to no subscriber at all.
    """
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    session.screen.feed(b"\x1b[?1049h")
    _settled(session, records=5)
    clock.advance(3600.0)
    # A tracker that did not exist when any of that happened.
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)

    tracker.evaluate(session)
    clock.advance(5.0)
    assert tracker.evaluate(session)["delivery_state"] == "safe"


def test_the_settles_input_revision_is_the_one_from_when_it_settled() -> None:
    """Text typed between the settle and the first read still blocks."""
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.screen.feed(b"\x1b[?1049h")
    _settled(session, records=5)
    session.input_revision += 1
    clock.advance(5.0)

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "terminal_input_after_completion" in evaluation["reasons"]


def test_a_catchup_over_nothing_proves_nothing() -> None:
    """An empty transcript replacement is not evidence the parser works."""
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    _settled(session, records=0)
    clock.advance(5.0)

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "unknown"
    assert "no_root_lifecycle_evidence" in evaluation["reasons"]


def test_catchup_never_overrules_evidence_the_tracker_already_has() -> None:
    """It fills a gap. A live turn is not a gap."""
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    tracker.observe(_event("turn_started"), session)
    _settled(session, records=5)
    clock.advance(5.0)

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "root_agent_working" in evaluation["reasons"]


def test_text_typed_after_completion_still_blocks() -> None:
    """The composer-collision guard is what the loosened checks now rest on."""
    session, tracker, clock = _idle_agent()
    session.screen.feed(b"\x1b[?1049h")
    session.input_revision += 1
    clock.advance(5.0)

    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "terminal_input_after_completion" in evaluation["reasons"]
