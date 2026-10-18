"""Delivering an already-decided approval as a keystroke.

The decision is never made here — it is made from the structured
`PermissionRequest` payload by `auto_approval_decision`. These tests pin the
gates between that decision and a key reaching the PTY, because the failure they
prevent is a keystroke landing in whatever replaced the dialog.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest

from swe_mux import observation
from swe_mux.event_bus import EventBus
from swe_mux.harness import HARNESSES
from swe_mux.models import ApprovalPolicy
from swe_mux.observation import apply_hook_observation

from .support.detection_replay import ReplaySession
from .support.settle import until

pytestmark = pytest.mark.anyio


class Recorder:
    """Stands in for the server's operator-input accounting."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str]] = []

    def __call__(self, data: str, source: str) -> None:
        self.writes.append((data, source))


def _session(monkeypatch: pytest.MonkeyPatch, screen: str, backend: str = "claude") -> Any:
    session = cast(Any, ReplaySession(backend))
    session.observation_replay = False
    session.approval_stabilization_seconds = 30.0     # never commits during a test
    session.approval_keystroke_poll_seconds = 0.01
    session.approval_keystroke_window_seconds = 0.4
    session.approval_input_sink = Recorder()
    session.record.approval_policy = ApprovalPolicy(
        mode="allow_all",
        run_id=session.record.agent_run_id,
        expires_at=time.time() + 3600,
        max_auto=100,
    )
    # The classifier reads a real scrollback; the screen is the one input these
    # gates turn on, so it is injected rather than synthesized as terminal bytes.
    monkeypatch.setattr(observation, "session_pty_state", lambda _s: screen)
    return session


async def _permission(session: Any, events: EventBus, tool: str = "Read", **ti: Any) -> Any:
    return await apply_hook_observation(
        session,
        "PermissionRequest",
        {"tool_name": tool, "tool_input": ti or {"file_path": "/x"}, "tool_use_id": "toolu_1"},
        events,
    )


async def _settle(seconds: float = 0.2) -> None:
    await asyncio.sleep(seconds)


# -- the happy path ----------------------------------------------------------


async def test_a_decided_approval_is_typed_once_the_dialog_is_on_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, "approval")
    await _permission(session, EventBus())
    await _settle()
    assert session.approval_input_sink.writes == [("\r", "approval-auto")]


async def test_delivery_retires_the_visible_approval_it_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the operator is shown a prompt that has already been answered."""
    session = _session(monkeypatch, "approval")
    await _permission(session, EventBus())
    await _settle()
    assert observation._observation_state(session).get("pending_approval") is None
    assert session.record.state != "awaiting"


async def test_delivery_is_ledgered(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(monkeypatch, "approval")
    await _permission(session, EventBus())
    await _settle()
    entry = next(
        e for e in session.state_transitions if e["kind"] == "approval_keystroke_delivery"
    )
    assert entry["outcome"] == "delivered"
    assert entry["tool_use_id"] == "toolu_1"


# -- the gates ---------------------------------------------------------------


async def test_nothing_is_typed_while_the_screen_is_not_showing_an_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole design rests on this: no dialog on screen, no keystroke."""
    session = _session(monkeypatch, "working")
    await _permission(session, EventBus())
    await _settle(0.6)
    assert session.approval_input_sink.writes == []


async def test_a_screen_the_classifier_cannot_read_is_never_typed_into(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for screen in ("unknown", "uninformative", "idle"):
        session = _session(monkeypatch, screen)
        await _permission(session, EventBus())
        await _settle(0.6)
        assert session.approval_input_sink.writes == [], screen


async def test_the_visible_approval_stands_when_delivery_never_happens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed delivery must degrade to an ordinary prompt, not to silence.

    This is the arrangement that makes the feature safe to leave on: the
    stabilization timer is armed *underneath* the watcher, so a session whose
    keystroke never lands still raises the approval on its usual boundary.
    """
    session = _session(monkeypatch, "working")
    session.approval_stabilization_seconds = 0.05
    await _permission(session, EventBus())
    await _settle(0.3)
    assert session.approval_input_sink.writes == []
    assert session.record.state == "awaiting"
    assert session.record.awaiting_reason == "approval"


async def test_a_session_with_no_write_path_types_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, "approval")
    session.approval_input_sink = None
    await _permission(session, EventBus())
    await _settle()
    entry = next(
        e for e in session.state_transitions if e["kind"] == "approval_keystroke_delivery"
    )
    assert entry["outcome"] == "no_input_sink"


async def test_an_ended_session_is_never_typed_into(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(monkeypatch, "approval")
    await _permission(session, EventBus())
    session.record.state = "exited"
    await _settle(0.3)
    assert session.approval_input_sink.writes == []


async def test_the_watcher_gives_up_rather_than_waiting_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, "working")
    await _permission(session, EventBus())
    await _settle(0.6)
    entry = next(
        e for e in session.state_transitions if e["kind"] == "approval_keystroke_delivery"
    )
    assert entry["outcome"] == "expired"


async def test_the_decided_tool_running_retires_the_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its approval was answered, so a dialog drawn afterwards is not its dialog."""
    screen = {"value": "working"}
    session = _session(monkeypatch, "working")
    monkeypatch.setattr(observation, "session_pty_state", lambda _s: screen["value"])
    session.approval_keystroke_window_seconds = 5.0
    events = EventBus()
    await _permission(session, events)
    await apply_hook_observation(
        session,
        "PostToolUse",
        {"tool_name": "Read", "tool_use_id": "toolu_1", "tool_response": {"content": "x"}},
        events,
    )
    assert "pending_auto_delivery" not in observation._observation_state(session)
    screen["value"] = "approval"
    # A quiet window: a watcher still running would type into this dialog.
    await _settle(0.3)
    assert session.approval_input_sink.writes == []
    assert not any(
        entry["kind"] == "approval_keystroke_delivery" for entry in session.state_transitions
    ), "retired, not expired and not delivered"


async def test_another_tool_running_leaves_the_watcher_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, "working")
    session.approval_keystroke_window_seconds = 5.0
    events = EventBus()
    await _permission(session, events)
    await apply_hook_observation(
        session,
        "PostToolUse",
        {"tool_name": "Read", "tool_use_id": "toolu_other", "tool_response": {"content": "x"}},
        events,
    )
    assert observation._observation_state(session)["pending_auto_delivery"] == "toolu_1"
    # Let the watcher go rather than leave it polling past the test.
    observation._observation_state(session).pop("pending_auto_delivery")
    await _settle(0.1)


async def test_ending_the_wait_for_its_approval_retires_the_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(monkeypatch, "working")
    session.approval_keystroke_window_seconds = 5.0
    await _permission(session, EventBus())
    assert observation.cancel_pending_approval(session, "terminal_input")
    assert "pending_auto_delivery" not in observation._observation_state(session)
    await _settle(0.1)
    assert session.approval_input_sink.writes == []


async def test_the_watcher_backs_off_after_its_fast_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dialog that never comes is looked for a bounded number of times."""
    looks: list[float] = []

    def screen(_session: Any) -> str:
        looks.append(time.monotonic())
        return "working"

    session = _session(monkeypatch, "working")
    monkeypatch.setattr(observation, "session_pty_state", screen)
    monkeypatch.setattr(observation, "APPROVAL_KEYSTROKE_FAST_SECONDS", 0.1)
    monkeypatch.setattr(observation, "APPROVAL_KEYSTROKE_SLOW_POLL_SECONDS", 0.2)
    session.approval_keystroke_window_seconds = 1.0
    await _permission(session, EventBus())
    await until(
        lambda: any(
            entry["kind"] == "approval_keystroke_delivery" for entry in session.state_transitions
        ),
        seconds=10,
        what="the watcher expired",
    )
    # A fixed 0.01 s poll would have looked about 100 times in the window; the fast
    # tenth of a second allows about ten, and the backed-off rest about six more.
    assert len(looks) < 40, len(looks)
    gaps = [later - earlier for earlier, later in zip(looks, looks[1:], strict=False)]
    assert max(gaps) >= 0.15, "the interval grew past the fast poll"


# -- what may never arm it ---------------------------------------------------


async def test_a_refused_request_arms_no_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """A floored request is escalated, so nothing may be typed for it."""
    session = _session(monkeypatch, "approval")
    await _permission(session, EventBus(), "Bash", command="git push origin master")
    await _settle(0.5)
    assert session.approval_input_sink.writes == []


async def test_wait_mode_arms_no_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(monkeypatch, "approval")
    session.record.approval_policy = ApprovalPolicy()
    await _permission(session, EventBus())
    await _settle(0.5)
    assert session.approval_input_sink.writes == []


async def test_a_notification_only_approval_arms_no_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate that separates this from a blind Enter at the screen.

    A trust dialog, a `/clear` confirmation, a login, and a startup dialog raise
    no structured permission request, so no watcher exists for them and no key
    can reach them — even with `allow_all` set and an approval on screen.
    """
    session = _session(monkeypatch, "approval")
    await apply_hook_observation(
        session,
        "Notification",
        {"notification_type": "permission_prompt", "message": "Trust this folder?"},
        EventBus(),
    )
    await _settle(0.5)
    assert session.approval_input_sink.writes == []


async def test_replay_never_types(monkeypatch: pytest.MonkeyPatch) -> None:
    """Catch-up replays historical records; typing then would answer the past."""
    session = _session(monkeypatch, "approval")
    session.observation_replay = True
    assert observation._keystroke_delivery_key(session) is None


# -- scoping -----------------------------------------------------------------


async def test_delivery_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(monkeypatch, "approval")
    session.approval_keystroke_delivery = False
    assert observation._keystroke_delivery_key(session) is None
    await _permission(session, EventBus())
    await _settle(0.3)
    assert session.approval_input_sink.writes == []


def test_only_a_harness_with_a_measured_accept_key_may_be_typed_into() -> None:
    """Unmeasured means silent. Guessing the key is how a deny gets approved."""
    keys = {name: h.approval_accept_key for name, h in HARNESSES.items()}
    assert keys["claude"] == "\r"
    assert all(value is None for name, value in keys.items() if name != "claude")


def test_a_harness_that_can_answer_through_its_hook_needs_no_keystroke() -> None:
    """Both may be declared, and Claude declares both today because its CLI
    publishes the request and ignores the answer. The watcher self-retires when
    that changes: a CLI that honours the decision never draws the dialog, and the
    tool it then runs retires the watcher the moment its `PostToolUse` arrives."""
    for harness in HARNESSES.values():
        if harness.approval_accept_key is not None:
            assert harness.hook_approval_decisions, harness.name
