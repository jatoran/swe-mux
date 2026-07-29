"""Phase 5: the quantitative promotion criteria for auto-delivery.

Auto-delivery is gated on Phase 1 shadow evidence, and "shadow evidence" is
only meaningful if the bar is written down and machine-checked. The bar
(`auto_delivery.promotion_status`):

1. **Zero known false-safe deliveries.** Enforced two ways — here, over the
   fixture classes below, and at runtime by the operator's `report_unsafe`,
   which resets the proving clock.
2. At least ``PROVING_MIN_SENDS`` automatic deliveries, and
3. at least ``PROVING_MIN_DAYS`` days of operator-reviewed running.

This module owns (1). Each class is checked twice: directly against
``DeliveryReadinessTracker`` (deterministic, and the only way to express run
replacement cleanly), and across the golden replay corpus, where a checkpoint
the oracle calls unsafe must never evaluate ``safe``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux.auto_delivery import PROVING_FIXTURE_CLASSES, promotion_status
from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.models import MuxEvent
from tests.support.detection_replay import ReplaySession, VirtualClock, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "detection" / "v1"
INVENTORY = "edge_case_inventory.json"


def _event(event_type: str, **payload: Any) -> MuxEvent:
    return MuxEvent(
        ts=time.time(),
        session_id="replay-session",
        source="transcript",
        type=event_type,
        payload=payload,
    )


def _ready_session() -> tuple[ReplaySession, DeliveryReadinessTracker, VirtualClock]:
    """A session the tracker considers safe — the baseline each class must break."""
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    session.terminal_mode = "normal"
    session.terminal_mode_updated_at = clock.monotonic()
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed"), session)
    clock.advance(5.0)
    session.terminal_mode_updated_at = clock.monotonic()
    return session, tracker, clock


def test_the_baseline_fixture_is_actually_safe() -> None:
    # Without this the class assertions below would pass vacuously.
    session, tracker, _clock = _ready_session()
    assert tracker.evaluate(session)["delivery_state"] == "safe"


def test_no_false_safe_in_the_approval_class() -> None:
    session, tracker, clock = _ready_session()
    tracker.observe(_event("approval_needed", kind="approval"), session)
    session.record.state = "awaiting"
    session.record.awaiting_reason = "approval"
    clock.advance(5.0)
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] != "safe"
    assert "approval_required" in evaluation["reasons"]


@pytest.mark.parametrize("kind", ["input", "question", "elicitation"])
def test_no_false_safe_in_the_question_class(kind: str) -> None:
    session, tracker, clock = _ready_session()
    tracker.observe(_event("approval_needed", kind=kind), session)
    session.record.state = "awaiting"
    session.record.awaiting_reason = "question"
    clock.advance(5.0)
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] != "safe"
    assert "awaiting_user_input" in evaluation["reasons"]


def test_no_false_safe_in_the_rate_limit_class() -> None:
    session, tracker, clock = _ready_session()
    tracker.observe(_event("rate_limited"), session)
    clock.advance(5.0)
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] != "safe"
    assert "provider_rate_limit" in evaluation["reasons"]


def test_no_false_safe_from_subagent_completion() -> None:
    """A subagent finishing is not the root turn finishing.

    The dangerous shape: the root turn is still working, a subagent completes,
    and a naive tracker reads that completion as "the session is idle, deliver".
    """
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    session.terminal_mode = "normal"
    session.terminal_mode_updated_at = clock.monotonic()
    tracker.observe(_event("turn_started"), session)
    tracker.observe(_event("turn_ended", outcome="completed", scope="subagent"), session)
    tracker.observe(_event("subagent_activity"), session)
    clock.advance(5.0)
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] != "safe"
    assert evaluation["evidence"]["subagent_events_ignored"] >= 1


def test_no_false_safe_while_the_operator_is_typing() -> None:
    session, tracker, clock = _ready_session()
    # Text typed after root completion: the composer is not empty, so a paste
    # would concatenate with whatever the human started.
    session.input_revision += 1
    session.last_input_event_ts = clock.monotonic()
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] != "safe"
    assert {"terminal_input_after_completion", "operator_recently_typed"} & set(
        evaluation["reasons"]
    )


def test_no_false_safe_after_the_run_is_replaced() -> None:
    session, tracker, clock = _ready_session()
    # A resumed/branched conversation is a different run; the readiness memory
    # keyed to the old run must not carry over as evidence about the new one.
    session.record.agent_run_id = "run-2"
    clock.advance(1.0)
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] != "safe"


def test_the_reported_fixture_classes_match_what_is_checked_here() -> None:
    """The criteria the API reports and the criteria this module enforces are one list."""
    checked = {
        "approval_required": test_no_false_safe_in_the_approval_class,
        "awaiting_user_input": test_no_false_safe_in_the_question_class,
        "rate_limited": test_no_false_safe_in_the_rate_limit_class,
        "subagent_activity": test_no_false_safe_from_subagent_completion,
        "active_operator_input": test_no_false_safe_while_the_operator_is_typing,
        "run_replacement": test_no_false_safe_after_the_run_is_replaced,
    }
    assert set(PROVING_FIXTURE_CLASSES) == set(checked)
    assert promotion_status({})["fixture_classes"] == list(PROVING_FIXTURE_CLASSES)


async def test_the_replay_corpus_still_covers_every_promotion_class() -> None:
    """The golden corpus must keep exercising each class end to end.

    The unit checks above pin the tracker; this pins that real transcript/hook
    traffic for each class still exists, so a fixture deletion cannot quietly
    shrink the evidence the promotion criteria rest on.
    """
    from tests.support.detection_replay import DetectionReplay

    covered: set[str] = set()
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name == INVENTORY:
            continue
        manifest = load_manifest(path)
        result = await DetectionReplay(manifest["backend"]).run(manifest)
        steps = [str(step["kind"]) for step in manifest["steps"]]
        for item in result["events"]:
            if item["type"] == "approval_needed":
                kind = str(item.get("kind") or "approval")
                covered.add(
                    "awaiting_user_input"
                    if kind in {"input", "question", "elicitation"}
                    else "approval_required"
                )
            if item["type"] == "rate_limited":
                covered.add("rate_limited")
            if item["type"] == "subagent_activity" or item.get("scope") == "subagent":
                covered.add("subagent_activity")
            if item["type"] in {"backend_detected", "backend_demoted"}:
                covered.add("run_replacement")
        if "input" in steps:
            covered.add("active_operator_input")
        # No checkpoint the oracle calls unsafe may evaluate safe.
        for checkpoint in result["checkpoints"]:
            assert not (checkpoint["actual"] == "safe" and not checkpoint["oracle_safe"]), (
                f"{path.name}: false-safe checkpoint ({checkpoint['reason']})"
            )
    assert set(PROVING_FIXTURE_CLASSES) <= covered
