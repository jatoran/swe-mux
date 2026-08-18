"""What the hook ingress answers, and what it refuses to answer.

These are the integration-level assertions for the decision path: the same
`apply_hook_observation` the daemon calls, driven with a real `ApprovalPolicy`
on the record.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest

from swe_mux.event_bus import EventBus
from swe_mux.models import ApprovalPolicy
from swe_mux.observation import apply_hook_observation, auto_approval_decision

from .support.detection_replay import ReplaySession

pytestmark = pytest.mark.anyio


def drain(queue: asyncio.Queue[Any]) -> list[Any]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _claude(mode: str = "wait", rules: list[str] | None = None) -> Any:
    session = cast(Any, ReplaySession("claude"))
    session.approval_stabilization_seconds = 0.01
    # The replay harness suppresses transitions and fanout; the decision path is
    # explicitly disabled under replay, so these tests exercise it as a live
    # session would.
    session.observation_replay = False
    # This file is about the *hook decision* channel — what mux answers and what
    # it refuses to answer. Keystroke delivery is the separate fallback for a CLI
    # that ignores that answer, and arming it also arms the stabilization safety
    # net underneath; `test_approval_keystroke.py` owns those interactions.
    session.approval_keystroke_delivery = False
    if mode != "wait":
        session.record.approval_policy = ApprovalPolicy(
            mode=mode,
            run_id=session.record.agent_run_id,
            expires_at=time.time() + 3600,
            granted_at=time.time(),
            rules=list(rules or []),
            max_auto=100,
        )
    return session


async def _permission(session: Any, events: EventBus, tool: str, **tool_input: Any) -> Any:
    return await apply_hook_observation(
        session,
        "PermissionRequest",
        {"tool_name": tool, "tool_input": tool_input, "tool_use_id": "toolu_1"},
        events,
    )


# -- the default changes nothing ---------------------------------------------


async def test_a_session_with_no_grant_answers_nothing_and_raises_the_approval() -> None:
    session = _claude()
    events = EventBus()
    queue = events.subscribe()
    decision = await _permission(session, events, "Bash", command="npm run build")
    assert decision is None
    assert "approval_detected" in [item.type for item in drain(queue)]


async def test_the_decision_path_is_inert_under_replay() -> None:
    """Historical catch-up must never answer a prompt that was already resolved."""
    session = _claude("allow_all")
    session.observation_replay = True
    reason, outcome = auto_approval_decision(session, {"tool_name": "Read"})
    assert outcome is None
    assert reason == "replay"


# -- allow -------------------------------------------------------------------


async def test_an_allowlisted_request_is_answered_and_never_becomes_attention() -> None:
    session = _claude("allowlisted", ["Read"])
    events = EventBus()
    queue = events.subscribe()
    decision = await _permission(session, events, "Read", file_path="/repo/src/main.py")
    assert decision == {
        "hookEventName": "PermissionRequest",
        "decision": "allow",
        "reason": "swe-mux approval mode: matched Read",
    }
    # The point of the feature: no stabilization timer starts, so nothing
    # downstream (sidebar attention, completion sound, web push) ever fires.
    emitted = [item.type for item in drain(queue)]
    assert "approval_detected" not in emitted
    assert "approval_needed" not in emitted
    assert "approval_auto_approved" in emitted
    assert session.record.state != "awaiting"
    await asyncio.sleep(0.03)
    assert session.record.state != "awaiting"


async def test_an_unmatched_request_still_reaches_the_human() -> None:
    session = _claude("allowlisted", ["Read"])
    events = EventBus()
    queue = events.subscribe()
    decision = await _permission(session, events, "Bash", command="npm run build")
    assert decision is None
    assert "approval_detected" in [item.type for item in drain(queue)]


async def test_allow_all_answers_an_ordinary_command() -> None:
    session = _claude("allow_all")
    events = EventBus()
    decision = await _permission(session, events, "Bash", command="npm run build")
    assert decision is not None
    assert decision["decision"] == "allow"


# -- the floor, live ---------------------------------------------------------


async def test_allow_all_still_escalates_a_push() -> None:
    session = _claude("allow_all")
    events = EventBus()
    queue = events.subscribe()
    decision = await _permission(session, events, "Bash", command="git push origin master")
    assert decision is None
    assert "approval_detected" in [item.type for item in drain(queue)]
    assert session.record.approval_policy.floor_deferred == 1


async def test_a_floor_deferral_is_ledgered_so_it_is_not_read_as_a_bug() -> None:
    session = _claude("allow_all")
    await _permission(session, EventBus(), "Read", file_path="/home/u/.ssh/id_rsa")
    entry = next(
        item for item in session.state_transitions if item["kind"] == "approval_auto_decision"
    )
    assert entry["decision"] == "ask"
    assert entry["floor"]


# -- bounds ------------------------------------------------------------------


async def test_an_expired_grant_stops_answering() -> None:
    session = _claude("allow_all")
    session.record.approval_policy.expires_at = time.time() - 1
    assert await _permission(session, EventBus(), "Read", file_path="/x") is None


async def test_a_grant_made_for_another_conversation_does_not_apply() -> None:
    session = _claude("allow_all")
    session.record.approval_policy.run_id = "some-other-run"
    assert await _permission(session, EventBus(), "Read", file_path="/x") is None


async def test_a_grant_stops_answering_once_its_budget_is_spent() -> None:
    session = _claude("allow_all")
    session.record.approval_policy.max_auto = 2
    events = EventBus()
    assert await _permission(session, events, "Read", file_path="/a") is not None
    assert await _permission(session, events, "Read", file_path="/b") is not None
    assert await _permission(session, events, "Read", file_path="/c") is None
    assert session.record.approval_policy.auto_approved == 2


async def test_every_answer_is_counted_and_the_last_one_is_named() -> None:
    session = _claude("allow_all")
    await _permission(session, EventBus(), "Bash", command="npm run build")
    policy = session.record.approval_policy
    assert policy.auto_approved == 1
    assert policy.last_request == "Bash(npm run build)"
    assert policy.last_decision_at is not None


# -- harness capability ------------------------------------------------------


async def test_a_harness_that_cannot_answer_is_never_handed_a_decision() -> None:
    """Codex reports permission requests and cannot resolve one.

    Answering here would leave the daemon believing it had approved a dialog
    that is still sitting on the user's screen.
    """
    session = cast(Any, ReplaySession("codex"))
    session.observation_replay = False
    session.approval_stabilization_seconds = 0.01
    session.record.approval_policy = ApprovalPolicy(
        mode="allow_all",
        run_id=session.record.agent_run_id,
        expires_at=time.time() + 3600,
        max_auto=100,
    )
    reason, outcome = auto_approval_decision(session, {"tool_name": "Read"})
    assert outcome is None
    assert reason == "harness_cannot_decide"
    assert await _permission(session, EventBus(), "Read", file_path="/x") is None


# -- run-scope revocation ----------------------------------------------------


async def test_a_grant_is_dropped_from_the_record_at_a_run_identity_seam() -> None:
    from swe_mux.session import revoke_approval_policy

    session = _claude("allow_all")
    assert revoke_approval_policy(session, evidence="conversation_rolled:test")
    assert session.record.approval_policy.mode == "wait"
    assert any(
        item["kind"] == "approval_mode_revoked" for item in session.state_transitions
    )
    # Idempotent: a second seam on an already-cleared record ledgers nothing.
    assert not revoke_approval_policy(session, evidence="conversation_rolled:test")
