from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.meta_hooks import HookRule, MetaHookEngine
from swe_mux.models import SessionRecord
from swe_mux.observation import (
    JsonlTailer,
    _claude,
    _codex,
    _record_parser_observation,
    _remember_user_prompt,
    apply_hook_observation,
    classify_transcript_event,
    observe_transcript,
    restore_pending_approval,
    transcript_tail_turn_state,
)
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.session import Session
from tests.support.detection_replay import ReplaySession


def screen(data: bytes) -> ScrollbackBuffer:
    """A real retention buffer holding one screen.

    The real class, not a stub: every screen reader goes through `tail_bytes`, whose
    whole point is that it walks the chunk deque instead of joining the buffer, and a
    hand-rolled `bytes`-only stand-in would route around exactly that.
    """
    buffer = ScrollbackBuffer(1 << 20)
    buffer.append(data)
    return buffer


def record(backend: str) -> SessionRecord:
    return SessionRecord(
        "mux-id", "builder-one", "default", backend, "native-id", ".", f"{backend}.exe", []
    )


def drain(queue: asyncio.Queue[Any]) -> list[Any]:
    emitted = []
    while not queue.empty():
        emitted.append(queue.get_nowait())
    return emitted


def test_first_root_prompt_is_pinned_and_later_ones_only_update_the_latest() -> None:
    """The title is what the run is for, so it reads the opening request, not the newest.

    Later prompts are steps inside that job. Titling from one — which is what a
    retried or restarted title attempt used to do — renames the tab after a detour.
    Subagent prompts are not the user's request at all and count for neither slot.
    """
    session = cast(Session, SimpleNamespace(first_user_prompt=None, last_user_prompt=None))

    _remember_user_prompt(session, {"prompt": "  fix the flaky login test  "})
    _remember_user_prompt(session, {"prompt": "now check the deploy logs"})
    _remember_user_prompt(session, {"prompt": "summarize this file", "isSidechain": True})

    assert session.first_user_prompt == "fix the flaky login test"
    assert session.last_user_prompt == "now check the deploy logs"


async def test_claude_parser_tracks_tools_completion_and_current_context() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude")))
    events = EventBus()
    queue = events.subscribe()
    await _claude(
        session,
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "tool-1", "name": "Bash"}],
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 300,
                    "output_tokens": 20,
                },
            },
        },
        events,
    )
    await _claude(
        session,
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "is_error": True,
                        "content": "pytest failed",
                    }
                ]
            },
        },
        events,
    )
    await _claude(
        session,
        {"type": "system", "subtype": "turn_duration", "durationMs": 50},
        events,
    )
    assert session.record.state == "idle"
    assert session.record.state_detail is None
    assert session.record.tokens_in == 600
    assert session.record.tokens_out == 20
    assert session.record.context_window == 1_000_000
    assert session.record.context_pct == 0.0006
    emitted = []
    tool_result = None
    while not queue.empty():
        item = await queue.get()
        emitted.append(item.type)
        if item.type == "tool_result":
            tool_result = item
    assert "tool_use" in emitted
    assert tool_result is not None
    assert tool_result.payload == {
        "tool": "Bash",
        "success": False,
        "exit_code": None,
        "detail": "pytest failed",
        "scope": "root",
        "call_id": "tool-1",
        "target": None,
        # The exact bytes the agent saw, hashed before the detail bound is
        # applied: the read-side hash Tier 0 provenance joins on.
        "content_hash": hashlib.sha256(b"pytest failed").hexdigest(),
        "test_outcome": None,
        "parser_version": "2",
    }
    assert "turn_ended" in emitted


async def test_claude_parser_finishes_on_end_turn_without_duration_record() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude")))
    session.record.state = "working"
    events = EventBus()
    queue = events.subscribe()
    await _claude(
        session,
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn",
                "model": "claude-opus-4-8",
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 19_769,
                    "cache_read_input_tokens": 21_141,
                    "output_tokens": 24,
                },
            },
        },
        events,
    )
    assert session.record.state == "idle"
    assert session.record.context_pct == 0.040912
    assert "turn_ended" in [(await queue.get()).type for _ in range(queue.qsize())]


async def test_native_completion_closes_a_hook_started_turn() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude"), state_source_priority=-1))
    session.transition = lambda state, detail, **kw: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, **kw
    )
    session.publish_update = lambda: None
    events = EventBus()

    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    assert session.record.state == "working"
    assert session.state_source_priority == 2
    await _claude(
        session,
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
            },
        },
        events,
    )

    assert session.record.state == "idle"
    assert session.record.state_detail is None


async def test_new_turn_releases_previous_source_priority() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude"), state_source_priority=-1))
    session.transition = lambda state, detail, **kw: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, **kw
    )
    session.publish_update = lambda: None
    events = EventBus()

    await apply_hook_observation(session, "SessionStart", {}, events)
    assert session.state_source_priority == 2
    await _claude(session, {"type": "user", "message": {"content": "next turn"}}, events)

    assert session.record.state == "working"
    assert session.state_source_priority == 1


async def test_codex_parser_normalizes_turn_completion() -> None:
    session = cast(Any, SimpleNamespace(record=record("codex")))
    events = EventBus()
    await _codex(session, {"type": "event_msg", "payload": {"type": "task_started"}}, events)
    assert session.record.state == "working"
    await _codex(session, {"type": "event_msg", "payload": {"type": "task_complete"}}, events)
    assert session.record.state == "idle"
    await _codex(
        session,
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 900_000, "output_tokens": 10_000},
                    "last_token_usage": {"input_tokens": 75_000, "output_tokens": 10},
                    "model_context_window": 300_000,
                },
            },
        },
        events,
    )
    assert session.record.tokens_in == 900_000
    assert session.record.context_window == 300_000
    assert session.record.context_pct == 0.25


async def test_codex_parser_correlates_tool_result_and_exit_code() -> None:
    session = cast(Any, SimpleNamespace(record=record("codex")))
    events = EventBus()
    queue = events.subscribe()
    await _codex(
        session,
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "call-1",
                "name": "exec_command",
            },
        },
        events,
    )
    await _codex(
        session,
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "exit_code": 2,
                "output": "command failed",
            },
        },
        events,
    )
    emitted = [await queue.get() for _ in range(queue.qsize())]
    result = next(item for item in emitted if item.type == "tool_result")
    assert result.payload == {
        "tool": "exec_command",
        "success": False,
        "exit_code": 2,
        "detail": "command failed",
        "scope": "root",
        "call_id": "call-1",
        "target": None,
        "content_hash": hashlib.sha256(b"command failed").hexdigest(),
        "test_outcome": None,
        "duration_ms": None,
        "parser_version": "2",
    }


def test_meta_hook_glob_matching(tmp_path: Path) -> None:
    session = SimpleNamespace(record=record("claude"))
    manager = cast(Any, SimpleNamespace(sessions={"mux-id": session}))
    engine = MetaHookEngine(tmp_path / "hooks.toml", EventBus(), manager)
    event = SimpleNamespace(type="approval_needed", source="hook", session_id="mux-id", payload={})
    rule = HookRule({"type": "approval_*", "session_name": "builder-*"}, {"kind": "notify"})
    assert engine._matches(rule, cast(Any, event))


async def test_semantic_events_are_deduplicated_across_hook_and_transcript() -> None:
    events = EventBus()
    queue = events.subscribe()
    first = await events.emit("tool_use", session_id="mux-id", source="transcript", tool="Bash")
    duplicate = await events.emit("tool_use", session_id="mux-id", source="hook", tool="Bash")

    assert duplicate is first
    assert queue.qsize() == 1


async def test_claude_sidechain_end_turn_never_completes_root() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude")))
    session.record.state = "working"
    events = EventBus()
    queue = events.subscribe()

    await _claude(
        session,
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {
                "content": [{"type": "text", "text": "child complete"}],
                "stop_reason": "end_turn",
            },
        },
        events,
    )

    emitted = [await queue.get() for _ in range(queue.qsize())]
    assert session.record.state == "working"
    assert [item.type for item in emitted] == ["subagent_activity"]
    assert emitted[0].payload["scope"] == "subagent"


async def test_codex_task_started_and_user_message_emit_one_root_start() -> None:
    session = cast(
        Any,
        SimpleNamespace(
            record=record("codex"), first_user_prompt=None, last_user_prompt=None
        ),
    )
    events = EventBus()
    queue = events.subscribe()

    await _codex(session, {"type": "event_msg", "payload": {"type": "task_started"}}, events)
    await _codex(
        session,
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "fix the login race"},
        },
        events,
    )

    emitted = [await queue.get() for _ in range(queue.qsize())]
    assert [item.type for item in emitted].count("turn_started") == 1
    assert [item.type for item in emitted].count("transcript_message") == 1
    assert session.first_user_prompt == "fix the login race"


async def test_parser_drift_degrades_after_sustained_unknown_ratio() -> None:
    session = cast(Any, SimpleNamespace(record=record("codex"), publish_update=lambda: None))
    session.record.parser_status = "watching"
    events = EventBus()
    queue = events.subscribe()
    unknown = {"type": "future_outer", "payload": {"type": "future_payload"}}

    for _ in range(20):
        recognized, signature = classify_transcript_event("codex", unknown)
        await _record_parser_observation(session, events, recognized, signature)

    assert session.record.parser_status == "degraded"
    assert session.record.parser_unknown_events == 20
    assert session.record.parser_unknown_signatures == {"codex:future_outer:future_payload": 20}
    emitted = [await queue.get() for _ in range(queue.qsize())]
    assert [item.type for item in emitted] == ["capability_degraded"]


async def test_claude_local_command_records_never_begin_turns() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude")))
    session.record.state = "idle"
    events = EventBus()
    queue = events.subscribe()
    for content in (
        "<command-name>/copy</command-name>",
        "<command-message>learn</command-message>\n<command-name>/learn</command-name>",
        "<local-command-stdout>copied</local-command-stdout>",
    ):
        await _claude(session, {"type": "user", "message": {"content": content}}, events)
    await _claude(
        session,
        {"type": "user", "isMeta": True, "message": {"content": "caveat text"}},
        events,
    )
    assert session.record.state == "idle"
    assert drain(queue) == []


async def test_claude_interrupt_record_aborts_turn_even_after_hook_authority() -> None:
    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    queue = events.subscribe()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    assert session.record.state == "working"
    assert session.state_source_priority == 2

    await _claude(
        session,
        {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]},
        },
        events,
    )

    assert session.record.state == "idle"
    emitted = [item.type for item in drain(queue)]
    assert "turn_aborted" in emitted


async def test_idle_prompt_notification_reads_as_ready_not_awaiting() -> None:
    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    queue = events.subscribe()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    assert session.record.state == "working"
    # "Claude is waiting for your input" fires when the agent has finished.
    await apply_hook_observation(
        session,
        "Notification",
        {"notification_type": "idle_prompt", "message": "Claude is waiting for your input"},
        events,
    )
    assert session.record.state == "idle"
    assert "approval_needed" not in [item.type for item in drain(queue)]


async def test_permission_prompt_notification_still_awaits_approval() -> None:
    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    queue = events.subscribe()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(
        session,
        "Notification",
        {"notification_type": "permission_prompt", "message": "Claude needs permission"},
        events,
    )
    assert session.record.state == "awaiting"
    assert "approval_needed" in [item.type for item in drain(queue)]


async def test_short_approval_is_cancelled_before_status_or_notification() -> None:
    session = cast(Any, ReplaySession("codex"))
    session.approval_stabilization_seconds = 0.05
    events = EventBus()
    queue = events.subscribe()
    await _codex(
        session,
        {"type": "event_msg", "payload": {"type": "task_started"}},
        events,
    )
    await apply_hook_observation(
        session,
        "PermissionRequest",
        {"tool_name": "shell"},
        events,
    )
    assert session.record.state == "working"
    assert [item.type for item in drain(queue)] == [
        "state_changed",
        "turn_started",
        "approval_detected",
    ]

    await apply_hook_observation(session, "PostToolUse", {}, events)
    await asyncio.sleep(0.07)

    assert session.record.state == "working"
    assert "approval_needed" not in [item.type for item in drain(queue)]
    assert any(
        item.get("kind") == "approval_stabilization_cancelled"
        for item in session.state_transitions
    )


async def test_stable_approval_becomes_visible_once_after_the_window() -> None:
    session = cast(Any, ReplaySession("codex"))
    session.approval_stabilization_seconds = 0.01
    events = EventBus()
    queue = events.subscribe()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    drain(queue)

    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    assert session.record.state == "working"
    assert [item.type for item in drain(queue)] == ["approval_detected"]

    await asyncio.sleep(0.03)

    assert session.record.state == "awaiting"
    emitted = [item.type for item in drain(queue)]
    assert emitted.count("state_changed") == 1
    assert emitted.count("approval_needed") == 1


async def test_pending_approval_restores_after_daemon_reload() -> None:
    session = cast(Any, ReplaySession("codex"))
    session.approval_stabilization_seconds = 0.05
    session.approval_candidate = {
        "started_at": time.time() - 0.04,
        "source": "hook",
        "evidence": "hook:PermissionRequest",
        "detail": "shell",
    }
    mirrored: list[dict[str, Any] | None] = []
    session.meta_sink = lambda: mirrored.append(session.approval_candidate)
    events = EventBus()
    queue = events.subscribe()

    assert await restore_pending_approval(session, events) is True
    assert [item.type for item in drain(queue)] == ["approval_detected"]
    await asyncio.sleep(0.03)

    assert session.record.state == "awaiting"
    emitted = [item.type for item in drain(queue)]
    assert emitted.count("state_changed") == 1
    assert emitted.count("approval_needed") == 1
    assert session.approval_candidate is None
    assert mirrored[-1] is None


async def test_immediate_question_replaces_a_pending_approval_candidate() -> None:
    session = cast(Any, ReplaySession("codex"))
    session.approval_stabilization_seconds = 0.05
    events = EventBus()
    queue = events.subscribe()
    await _codex(
        session,
        {"type": "event_msg", "payload": {"type": "task_started"}},
        events,
    )
    drain(queue)

    await apply_hook_observation(session, "PermissionRequest", {}, events)
    drain(queue)
    await _codex(
        session,
        {"type": "event_msg", "payload": {"type": "request_user_input"}},
        events,
    )
    await asyncio.sleep(0.07)

    assert session.record.state == "awaiting"
    assert session.record.awaiting_reason == "question"
    emitted = [item for item in drain(queue) if item.type == "approval_needed"]
    assert len(emitted) == 1
    assert emitted[0].payload["kind"] == "input"


async def test_session_start_during_an_active_codex_turn_is_not_a_boundary() -> None:
    session = cast(Any, ReplaySession("codex"))
    events = EventBus()
    queue = events.subscribe()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    drain(queue)

    await apply_hook_observation(
        session,
        "SessionStart",
        {"session_id": session.record.native_session_id, "source": "compact"},
        events,
    )

    assert session.record.state == "working"
    assert "turn_ended" not in [item.type for item in drain(queue)]
    assert any(
        item.get("kind") == "session_start_state_ignored"
        for item in session.state_transitions
    )


async def test_idle_prompt_never_clobbers_a_pending_approval() -> None:
    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(
        session,
        "Notification",
        {"notification_type": "permission_prompt", "message": "approve?"},
        events,
    )
    assert session.record.state == "awaiting"
    await apply_hook_observation(
        session,
        "Notification",
        {"notification_type": "idle_prompt", "message": "waiting"},
        events,
    )
    assert session.record.state == "awaiting"


async def test_claude_tool_use_interrupt_variant_also_aborts() -> None:
    session = cast(Any, SimpleNamespace(record=record("claude")))
    session.record.state = "working"
    session.observation_state = {
        "root_turn_active": True,
        "root_completion_seen": False,
        "codex_scope": "root",
    }
    events = EventBus()
    await _claude(
        session,
        {
            "type": "user",
            "message": {
                "content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]
            },
        },
        events,
    )
    assert session.record.state == "idle"


async def test_local_command_closes_empty_hook_turn_but_not_active_turns() -> None:
    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    command = {"type": "user", "message": {"content": "<command-name>/copy</command-name>"}}

    # A hook-started turn with no model activity is closed by its own command record.
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    assert session.record.state == "working"
    await _claude(session, command, events)
    assert session.record.state == "idle"

    # A turn that has produced model activity is not closed by a queued command.
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(session, "PreToolUse", {"tool_name": "Bash"}, events)
    await _claude(session, command, events)
    assert session.record.state == "working"


async def test_healthy_transcript_gates_late_tool_hooks_from_reopening_working() -> None:
    # When the transcript observer is authoritative, an out-of-order tool hook
    # arriving after the turn's end_turn must not resurrect "working" — the root
    # cause of sessions stuck blinking after they finished.
    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    events = EventBus()
    await _claude(session, {"type": "user", "message": {"content": "do it"}}, events)
    assert session.record.state == "working"
    await _claude(
        session,
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
        },
        events,
    )
    assert session.record.state == "idle"
    for event_type, payload in (
        ("PostToolUse", {"tool_name": "Bash"}),
        ("PreToolUse", {"tool_name": "Bash"}),
        ("UserPromptSubmit", {}),
    ):
        await apply_hook_observation(session, event_type, payload, events)
        assert session.record.state == "idle", f"{event_type} reopened a finished turn"


async def test_degraded_transcript_still_lets_hooks_drive_state() -> None:
    # The gate is a fallback contract: when the parser is not authoritative,
    # hooks must remain the source of truth so state never freezes.
    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "degraded"
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    assert session.record.state == "working"
    await apply_hook_observation(session, "PostToolUse", {"tool_name": "Bash"}, events)
    assert session.record.state == "working"


async def test_transcript_close_latch_blocks_hook_reopen_until_new_transcript_turn() -> None:
    # #2 backstop: independent of the apply_hook gate, a transcript-closed turn
    # cannot be reopened by a hook-sourced begin; only fresh transcript activity
    # starts the next turn and clears the latch.
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    events = EventBus()
    await _begin_root_turn(session, events, source="transcript")
    assert session.record.state == "working"
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.state == "idle"
    assert session.observation_state["closed_by_transcript"] is True

    await _begin_root_turn(session, events, source="hook")
    assert session.record.state == "idle"

    await _begin_root_turn(session, events, source="transcript")
    assert session.record.state == "working"
    assert session.observation_state["closed_by_transcript"] is False


def test_watchdog_recovers_proven_ended_turns_faster_than_pty_backstop() -> None:
    # #4: a tail that proves the turn ended is high-confidence, so recovery no
    # longer waits the full stall window; the ambiguous PTY backstop still does.
    from swe_mux.session import (
        STATE_WATCHDOG_ENDED_STUCK_SECONDS,
        STATE_WATCHDOG_PTY_STUCK_SECONDS,
    )

    assert STATE_WATCHDOG_ENDED_STUCK_SECONDS <= 8.0
    assert STATE_WATCHDOG_ENDED_STUCK_SECONDS < STATE_WATCHDOG_PTY_STUCK_SECONDS


def _open_tail_transcript(path: Path) -> None:
    # Tail is a tool_result with no closing marker -> transcript_tail_turn_state
    # classifies it "open" (the model still "owes" a response by the record). This
    # is what a turn interrupted/crashed before its marker lands looks like, and
    # what an observer stuck on the wrong sibling transcript reads.
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "t1", "name": "Read", "input": {}}],
                    "stop_reason": "tool_use",
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _watchdog_session(
    path: Path | None, now: float, scrollback: bytes, *, last_hook_ts: float | None = None
) -> Any:
    session = SimpleNamespace(
        record=record("claude"),
        observation_replay=False,
        last_state_change_ts=now - 100.0,  # stalled well past the PTY stuck window
        transcript_path=path,
        # Witnessed by default: these fixtures pin the *stall-gated* recoveries,
        # which exist for a session whose sources have gone quiet. A session with
        # no source at all takes the unwitnessed pair instead and is pinned
        # separately, so leaving this at 0 would silently retarget them.
        last_hook_ts=now - 100.0 if last_hook_ts is None else last_hook_ts,
        scrollback=screen(scrollback),
        observation_state={"root_turn_active": False, "root_completion_seen": True},
        note_watchdog_recovery=lambda _reason, **_kw: None,
    )
    session.record.state = "working"
    return session


def _fake_manager() -> Any:
    from swe_mux.session import SessionManager

    async def noop_drain(_session: Any) -> None:
        return None

    mgr = SimpleNamespace(
        hook_spool_dir=None,
        events=SimpleNamespace(emit=lambda *_a, **_k: asyncio.sleep(0)),
        _drain_hook_spool=noop_drain,
    )
    # Reuse the real PTY screen classifier so the test exercises it end to end.
    mgr._pty_tail_state = SessionManager._pty_tail_state
    mgr._pty_appears_idle = lambda s: SessionManager._pty_appears_idle(cast(Any, mgr), s)
    mgr._check_unwitnessed_pty_turn = lambda s, n: SessionManager._check_unwitnessed_pty_turn(
        cast(Any, mgr), s, n
    )
    return mgr


async def test_watchdog_pty_backstop_idles_open_tail_at_idle_prompt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The core fix: a turn left with an "open" transcript tail (no terminal record,
    # no Stop hook, or an observer on the wrong sibling file) must still recover
    # once the CLI has provably sat at its idle prompt for the stall window.
    from swe_mux.session import SessionManager

    calls: list[dict[str, Any]] = []

    async def fake_finish(_session: Any, _events: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("swe_mux.observation._finish_root_turn", fake_finish)

    path = tmp_path / "native.jsonl"
    _open_tail_transcript(path)
    now = time.time() + 10.0  # ensure the transcript reads as quiet (mtime is old)
    session = _watchdog_session(path, now, b"idle output\n? for shortcuts\n")
    mgr = _fake_manager()

    await SessionManager._watchdog_check_session(mgr, session, now)

    assert len(calls) == 1
    assert calls[0]["source"] == "watchdog-pty"
    assert calls[0]["inferred"] is True
    # Re-opened the turn so the forced close always lands.
    assert session.observation_state["root_turn_active"] is True


async def test_watchdog_pty_backstop_spares_open_tail_while_cli_busy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Safety: an "open" tail whose CLI still shows "esc to interrupt" is a genuine
    # in-flight tool call and must never be cut short.
    from swe_mux.session import SessionManager

    calls: list[dict[str, Any]] = []

    async def fake_finish(_session: Any, _events: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("swe_mux.observation._finish_root_turn", fake_finish)

    path = tmp_path / "native.jsonl"
    _open_tail_transcript(path)
    now = time.time() + 10.0
    session = _watchdog_session(path, now, b"running a tool...\nesc to interrupt\n")
    mgr = _fake_manager()

    await SessionManager._watchdog_check_session(mgr, session, now)

    assert calls == []
    assert session.record.state == "working"


async def test_watchdog_pty_backstop_covers_a_missing_transcript(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The documented promise is that a wrong-or-missing transcript still recovers
    # through the PTY backstop. Returning early on "no transcript" made exactly
    # that case unreachable — and it is the case with no other recovery path.
    from swe_mux.session import SessionManager

    calls: list[dict[str, Any]] = []

    async def fake_finish(_session: Any, _events: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("swe_mux.observation._finish_root_turn", fake_finish)

    now = time.time() + 10.0
    session = _watchdog_session(None, now, b"idle output\n? for shortcuts\n")
    await SessionManager._watchdog_check_session(_fake_manager(), session, now)
    assert [call["source"] for call in calls] == ["watchdog-pty"]

    # ...and an unreadable transcript path behaves the same way.
    calls.clear()
    session = _watchdog_session(tmp_path / "gone.jsonl", now, b"idle\n? for shortcuts\n")
    await SessionManager._watchdog_check_session(_fake_manager(), session, now)
    assert [call["source"] for call in calls] == ["watchdog-pty"]


async def test_watchdog_missing_transcript_still_spares_a_busy_cli(
    monkeypatch: Any,
) -> None:
    from swe_mux.session import SessionManager

    calls: list[dict[str, Any]] = []

    async def fake_finish(_session: Any, _events: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("swe_mux.observation._finish_root_turn", fake_finish)

    now = time.time() + 10.0
    session = _watchdog_session(None, now, b"running a tool...\nesc to interrupt\n")
    await SessionManager._watchdog_check_session(_fake_manager(), session, now)
    assert calls == []
    assert session.record.state == "working"


async def test_observe_transcript_suppresses_historical_replay_then_tracks_live(
    tmp_path: Path,
) -> None:
    path = tmp_path / "native.jsonl"
    old = "2026-07-19T01:00:00Z"
    history_records = [
        {"type": "user", "timestamp": old, "message": {"content": "old question"}},
        {
            "type": "assistant",
            "timestamp": old,
            "message": {
                "content": [{"type": "text", "text": "old answer"}],
                "stop_reason": "end_turn",
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 1_000, "output_tokens": 50},
            },
        },
        {"type": "system", "subtype": "turn_duration", "timestamp": old, "durationMs": 10},
        # A trailing user record used to leave a resumed session stuck "working".
        {"type": "user", "timestamp": old, "message": {"content": "unanswered question"}},
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in history_records), encoding="utf-8")
    session = cast(Any, SimpleNamespace(record=record("claude"), publish_update=lambda: None))
    session.record.state = "starting"
    events = EventBus()
    queue = events.subscribe()
    stop = asyncio.Event()
    task = asyncio.create_task(observe_transcript(session, path, events, stop))

    await asyncio.sleep(0.6)
    assert session.record.state == "idle"
    assert session.record.tokens_in == 1_000
    emitted = [item.type for item in drain(queue)]
    assert "turn_started" not in emitted
    assert "turn_ended" not in emitted
    assert "tool_use" not in emitted

    import datetime as dt

    live_ts = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"type": "user", "timestamp": live_ts, "message": {"content": "live question"}}
            )
            + "\n"
        )
    for _ in range(20):
        await asyncio.sleep(0.1)
        if session.record.state == "working":
            break
    assert session.record.state == "working"
    assert "turn_started" in [item.type for item in drain(queue)]
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_observe_transcript_resumes_working_for_recent_open_turn(tmp_path: Path) -> None:
    import datetime as dt

    path = tmp_path / "native.jsonl"
    fresh = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    path.write_text(
        json.dumps({"type": "user", "timestamp": fresh, "message": {"content": "just sent"}})
        + "\n",
        encoding="utf-8",
    )
    session = cast(Any, SimpleNamespace(record=record("claude"), publish_update=lambda: None))
    session.record.state = "starting"
    events = EventBus()
    stop = asyncio.Event()
    task = asyncio.create_task(observe_transcript(session, path, events, stop))
    await asyncio.sleep(0.6)
    assert session.record.state == "working"
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_jsonl_tailer_waits_for_complete_lines_and_clears_partial_on_truncate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"type":"user"')
    stop = asyncio.Event()
    collected: list[dict[str, Any]] = []

    async def collect() -> None:
        async for item, _historical in JsonlTailer(path).events(stop):
            if item is None:
                continue
            collected.append(item)
            if len(collected) == 1:
                stop.set()

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.3)
    assert collected == []
    # Truncation must discard the old unterminated prefix instead of joining it to
    # the replacement file's first record.
    path.write_bytes(b'{"type":"system","subtype":"turn_duration"}\n')
    await asyncio.wait_for(task, timeout=2)
    assert collected == [{"type": "system", "subtype": "turn_duration"}]


async def test_jsonl_tailer_labels_preexisting_content_historical(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')
    stop = asyncio.Event()
    seen: list[tuple[dict[str, Any] | None, bool]] = []

    async def collect() -> None:
        async for item, historical in JsonlTailer(path).events(stop):
            seen.append((item, historical))
            if item == {"n": 3}:
                stop.set()

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.4)
    with path.open("ab") as handle:
        handle.write(b'{"n":3}\n')
    await asyncio.wait_for(task, timeout=2)
    assert seen == [({"n": 1}, True), ({"n": 2}, True), (None, False), ({"n": 3}, False)]


async def test_transcript_rewrite_reconciles_an_active_turn(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_text("", encoding="utf-8")
    session = cast(
        Any,
        SimpleNamespace(
            record=record("claude"),
            state_source_priority=-1,
            publish_update=lambda: None,
        ),
    )
    session.transition = lambda state, detail, **kw: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, **kw
    )
    events = EventBus()
    stop = asyncio.Event()
    task = asyncio.create_task(observe_transcript(session, path, events, stop))
    await asyncio.sleep(0.3)

    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "temporary prompt"}}) + "\n",
        encoding="utf-8",
    )
    await asyncio.sleep(0.4)
    assert session.record.state == "working"

    # Claude cancel/revert can truncate the active turn out of the transcript.
    path.write_text("", encoding="utf-8")
    await asyncio.sleep(0.5)
    assert session.record.state == "idle"
    assert session.record.state_detail is None

    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_codex_rollback_finishes_the_active_turn() -> None:
    session = cast(Any, SimpleNamespace(record=record("codex"), state_source_priority=-1))
    session.transition = lambda state, detail, **kw: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, **kw
    )
    session.publish_update = lambda: None
    events = EventBus()

    await _codex(session, {"type": "event_msg", "payload": {"type": "task_started"}}, events)
    await _codex(
        session,
        {"type": "event_msg", "payload": {"type": "thread_rolled_back"}},
        events,
    )

    assert session.record.state == "idle"
    assert session.record.state_detail == "rolled_back"


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def test_transcript_tail_terminal_signals(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    # end_turn text followed by the trailing metadata records the provider appends.
    _write_jsonl(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "done"}],
                },
            },
            {"type": "last-prompt"},
            {"type": "ai-title"},
            {"type": "permission-mode"},
        ],
    )
    assert transcript_tail_turn_state("claude", path) == "ended"
    _write_jsonl(path, [{"type": "system", "subtype": "turn_duration", "durationMs": 5}])
    assert transcript_tail_turn_state("claude", path) == "ended"
    _write_jsonl(path, [{"type": "user", "message": {"content": "[Request interrupted by user]"}}])
    assert transcript_tail_turn_state("claude", path) == "ended"


def test_transcript_tail_leaves_in_flight_turns_open(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    # A long-running tool: last record is the assistant tool_use, no result yet.
    _write_jsonl(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "prev"}],
                },
            },
            {"type": "user", "message": {"content": "next"}},
            {
                "type": "assistant",
                "message": {
                    "stop_reason": "tool_use",
                    "content": [{"type": "tool_use", "name": "Bash"}],
                },
            },
        ],
    )
    assert transcript_tail_turn_state("claude", path) == "open"
    # A tool result the model still owes a response to is also open.
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]
                },
            }
        ],
    )
    assert transcript_tail_turn_state("claude", path) == "open"


def test_transcript_tail_reads_only_the_tail_of_a_large_file(tmp_path: Path) -> None:
    path = tmp_path / "big.jsonl"
    filler = [
        {
            "type": "assistant",
            "message": {
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "name": "Read"}],
            },
        }
        for _ in range(5000)
    ]
    _write_jsonl(path, [*filler, {"type": "system", "subtype": "turn_duration"}])
    assert path.stat().st_size > 131_072
    assert transcript_tail_turn_state("claude", path) == "ended"


def test_transcript_tail_codex_states(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    _write_jsonl(path, [{"type": "event_msg", "payload": {"type": "task_complete"}}])
    assert transcript_tail_turn_state("codex", path) == "ended"
    _write_jsonl(path, [{"type": "event_msg", "payload": {"type": "function_call", "name": "x"}}])
    assert transcript_tail_turn_state("codex", path) == "open"
    assert transcript_tail_turn_state("codex", tmp_path / "missing.jsonl") == "unknown"


# --- The unwitnessed pair: a session whose PTY screen is the only witness -------


def _unwitnessed_session(backend: str = "codex") -> Any:
    """A fresh agent pane: no transcript bound, no hook ever received."""
    session = _watchdog_session(None, time.time(), b"", last_hook_ts=0.0)
    session.record.backend = backend
    session.record.state = "idle"
    session.record.awaiting_reason = None
    return session


def test_a_fresh_codex_pane_is_unwitnessed_and_a_bound_or_hooked_one_is_not(
    tmp_path: Path,
) -> None:
    from swe_mux.session import session_is_unwitnessed

    session = _unwitnessed_session()
    assert session_is_unwitnessed(session) is True
    # Either channel appearing ends it, and neither ever comes back.
    bound = _unwitnessed_session()
    bound.transcript_path = tmp_path / "rollout.jsonl"
    assert session_is_unwitnessed(bound) is False
    hooked = _unwitnessed_session()
    hooked.last_hook_ts = time.time()
    assert session_is_unwitnessed(hooked) is False


def test_a_shell_is_never_unwitnessed() -> None:
    # It has no agent state to derive, and its prompt is not an agent's idle marker.
    from swe_mux.session import session_is_unwitnessed

    session = _unwitnessed_session("shell")
    assert session_is_unwitnessed(session) is False


async def test_the_pty_starts_and_ends_the_first_turn_when_nothing_else_can(
    monkeypatch: Any,
) -> None:
    """The reported bug: a Codex pane reads "ready · turn complete" while working.

    Codex cannot name its thread until `agent-turn-complete`, so for the whole first
    turn there is no transcript and no hook — and `working` was reachable from
    neither. Measured live at 200 s with the rollout's own `task_started` written 4 s
    after spawn.
    """
    from swe_mux.session import SessionManager

    session = _unwitnessed_session()
    manager = _fake_manager()

    session.scrollback = screen(b"\xe2\x9d\xaf  ? for shortcuts")
    await SessionManager._watchdog_check_session(manager, session, time.time())
    assert session.record.state == "idle"

    session.scrollback = screen(b"? for shortcuts\n\xe2\x80\xa2 Working (esc to interrupt)")
    await SessionManager._watchdog_check_session(manager, session, time.time())
    assert session.record.state == "idle"

    session.observation_state["unwitnessed_turn_armed"] = True
    await SessionManager._watchdog_check_session(manager, session, time.time())
    assert session.record.state == "working"

    session.scrollback = screen(b"\xe2\x80\xa2 Working (esc to interrupt)\n? for shortcuts")
    await SessionManager._watchdog_check_session(manager, session, time.time())
    assert session.record.state == "idle"


async def test_the_pty_stands_down_for_good_once_any_real_source_speaks() -> None:
    """One hook is enough, forever: the channel exists, so the PTY is not the only
    witness any more and a temporary silence on it is the stall-gated recoveries'
    job rather than this one's."""
    from swe_mux.session import SessionManager

    session = _unwitnessed_session()
    session.last_hook_ts = time.time()
    session.scrollback = screen(b"\xe2\x80\xa2 esc to interrupt")
    await SessionManager._watchdog_check_session(_fake_manager(), session, time.time())
    assert session.record.state == "idle"


def test_the_unwitnessed_pair_never_acts_on_an_approval_screen() -> None:
    """Both directions need their own marker *last* on screen, and a dialog is
    neither — so this can no more start a turn on top of an unanswered prompt than
    it can close one that is still blocked."""
    from swe_mux.session import watchdog_decision

    for state in ("idle", "working"):
        assert (
            watchdog_decision(
                cast(Any, state),
                stalled_seconds=0.0,
                tail_verdict=None,
                pty_state="approval",
                unwitnessed=True,
            )
            == "none"
        )


def test_the_unwitnessed_pair_is_inert_on_an_unreadable_screen() -> None:
    from swe_mux.session import watchdog_decision

    for state in ("idle", "working"):
        assert (
            watchdog_decision(
                cast(Any, state),
                stalled_seconds=0.0,
                tail_verdict=None,
                pty_state="unknown",
                unwitnessed=True,
            )
            == "none"
        )


def test_a_witnessed_session_never_takes_the_unwitnessed_pair() -> None:
    from swe_mux.session import watchdog_decision

    assert (
        watchdog_decision(
            "idle",
            stalled_seconds=999.0,
            tail_verdict=None,
            pty_state="working",
            unwitnessed=False,
        )
        == "none"
    )


async def test_a_pty_started_turn_emits_the_same_boundary_as_any_other(
    monkeypatch: Any,
) -> None:
    """It opens a real turn, not a bare state poke: delivery readiness, the queue,
    and every turn consumer key off `turn_started`, and a half-open turn would also
    make the eventual close a no-op."""
    from swe_mux.session import apply_watchdog_recovery

    session = _unwitnessed_session()
    session.state_source_priority = -1
    session.state_transitions = deque(maxlen=8)
    session.state_changes = deque(maxlen=8)
    session.status_health_counters = {}
    session.terminal_latencies = deque(maxlen=8)
    session.last_evidence_ts = time.time()
    events: list[tuple[str, dict[str, Any]]] = []

    class Bus:
        async def emit(self, event_type: str, **payload: Any) -> None:
            events.append((event_type, payload))

    session.transition = lambda state, detail, **kw: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, **kw
    )
    session.publish_update = lambda: None
    await apply_watchdog_recovery(session, cast(Any, Bus()), "begin_pty_turn")
    assert session.record.state == "working"
    assert session.observation_state["root_turn_active"] is True
    assert [name for name, _ in events if name == "turn_started"] == ["turn_started"]
