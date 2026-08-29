from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import observation
from swe_mux.event_bus import EventBus
from swe_mux.meta_hooks import HookRule, MetaHookEngine
from swe_mux.models import SessionRecord
from swe_mux.observation import (
    _TAIL_CHUNK_BYTES,
    _TAIL_IDENTITY_PROBE_SECONDS,
    JsonlTailer,
    _claude,
    _codex,
    _omp,
    _record_parser_observation,
    _remember_user_prompt,
    _transcript_authoritative,
    apply_hook_observation,
    classify_transcript_event,
    observe_transcript,
    restore_pending_approval,
    transcript_tail_turn_state,
)
from swe_mux.scrollback import ScrollbackBuffer
from swe_mux.session import Session
from tests.support.detection_replay import ReplaySession
from tests.support.settle import drained_until, until


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


async def test_omp_parser_tracks_exact_usage_context_cost_and_turns() -> None:
    session = ReplaySession("omp")
    events = EventBus()
    queue = events.subscribe()
    await _omp(
        session,  # type: ignore[arg-type]
        {
            "type": "message",
            "id": "user-1",
            "parentId": None,
            "timestamp": "2026-08-06T22:53:20Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        },
        events,
    )
    await _omp(
        session,  # type: ignore[arg-type]
        {
            "type": "message",
            "id": "assistant-1",
            "parentId": "user-1",
            "timestamp": "2026-08-06T22:53:21Z",
            "message": {
                "role": "assistant",
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "content": [{"type": "text", "text": "done"}],
                "stopReason": "stop",
                "usage": {
                    "input": 4,
                    "output": 9,
                    "cacheRead": 29_248,
                    "cacheWrite": 42_183,
                    "cost": {"total": 0.436699},
                },
                "contextSnapshot": {"promptTokens": 35_493},
            },
        },
        events,
    )
    await _omp(
        session,  # type: ignore[arg-type]
        {
            "type": "credential_pin",
            "id": "pin-1",
            "parentId": "assistant-1",
            "provider": "anthropic",
            "hash": "a" * 64,
        },
        events,
    )

    assert session.record.state == "idle"
    assert session.record.tokens_in == 4
    assert session.record.tokens_out == 9
    assert session.record.tokens_cache_read == 29_248
    assert session.record.tokens_cache_write == 42_183
    assert session.record.cost_usd == 0.436699
    assert session.record.context_window == 1_000_000
    assert session.record.context_pct == 0.035493
    assert session.record.provider == "anthropic"
    assert session.record.provider_account_hashes == {"anthropic": "a" * 64}
    assert session.record.model == "claude-opus-4-8"
    assert session.record.measurement_source == "omp-transcript"
    assert [item.type for item in drain(queue)] == [
        "state_changed",
        "turn_started",
        "transcript_message",
        "state_changed",
        "turn_ended",
    ]


async def test_omp_parser_maps_tools_standing_activity_and_interrupted_exit() -> None:
    session = ReplaySession("omp")
    events = EventBus()
    queue = events.subscribe()
    records = [
        {
            "type": "message",
            "id": "u",
            "parentId": None,
            "message": {"role": "user", "content": "delegate"},
        },
        {
            "type": "message",
            "id": "a",
            "parentId": "u",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "call-1",
                        "name": "task",
                        "arguments": {"prompt": "inspect"},
                    }
                ],
                "stopReason": "toolUse",
            },
        },
        {
            "type": "custom",
            "id": "start",
            "parentId": "a",
            "customType": "tool_execution_start",
            "data": {"toolCallId": "call-1", "toolName": "task"},
        },
        {
            "type": "custom",
            "id": "exit",
            "parentId": "start",
            "customType": "session_exit",
            "data": {
                "reason": "process_exit",
                "kind": "process_exit",
                "pendingToolCalls": [{"toolCallId": "call-1", "toolName": "task"}],
            },
        },
    ]
    for item in records:
        await _omp(session, item, events)  # type: ignore[arg-type]

    emitted = drain(queue)
    assert sum(item.type == "tool_use" for item in emitted) == 1
    assert any(
        item.type == "tool_result" and item.payload["success"] is False for item in emitted
    )
    assert any(item.type == "turn_aborted" for item in emitted)
    assert session.record.state == "idle"
    assert session.record.standing_activity == []


async def test_omp_parser_follows_active_branch_and_clear_is_not_rollover() -> None:
    session = ReplaySession("omp")
    events = EventBus()
    records = [
        {"type": "message", "id": "u", "parentId": None, "message": {"role": "user"}},
        {
            "type": "message",
            "id": "old",
            "parentId": "u",
            "message": {"role": "assistant", "content": "old", "stopReason": "stop"},
        },
        {
            "type": "branch_summary",
            "id": "branch",
            "parentId": "u",
            "fromId": "old",
            "summary": "alternate path",
        },
    ]
    for item in records:
        await _omp(session, item, events)  # type: ignore[arg-type]
    assert session.record.state == "working"

    await _omp(
        session,  # type: ignore[arg-type]
        {"type": "reset_boundary", "id": "clear", "parentId": "branch"},
        events,
    )
    assert session.record.state == "idle"
    # The point of the assertion is that `/clear` does NOT roll the conversation:
    # omp appends `reset_boundary` in the same file under the same id. Compared
    # against the session's starting id rather than a literal, because omp mints
    # its own conversation id and therefore starts on the mux placeholder - the
    # replay harness now models that from the registry instead of special-casing
    # codex as the only self-minting backend.
    assert session.record.native_session_id == session.record.id


async def test_omp_parser_tracks_hub_background_jobs_until_explicit_stop() -> None:
    session = ReplaySession("omp")
    events = EventBus()
    records = [
        {
            "type": "message",
            "id": "u1",
            "parentId": None,
            "message": {"role": "user", "content": "start the server"},
        },
        {
            "type": "message",
            "id": "a1",
            "parentId": "u1",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "hub-start",
                        "name": "hub",
                        "arguments": {
                            "op": "start",
                            "name": "dev-server",
                            "command": "npm run dev",
                        },
                    }
                ],
                "stopReason": "toolUse",
            },
        },
        {
            "type": "message",
            "id": "r1",
            "parentId": "a1",
            "message": {
                "role": "toolResult",
                "toolCallId": "hub-start",
                "toolName": "hub",
                "content": "started",
            },
        },
        {
            "type": "message",
            "id": "done1",
            "parentId": "r1",
            "message": {"role": "assistant", "content": "running", "stopReason": "stop"},
        },
    ]
    for item in records:
        await _omp(session, item, events)  # type: ignore[arg-type]

    assert [(item.kind, item.count) for item in session.record.standing_activity] == [
        ("background_tasks", 1)
    ]

    stop_records = [
        {
            "type": "message",
            "id": "u2",
            "parentId": "done1",
            "message": {"role": "user", "content": "stop it"},
        },
        {
            "type": "message",
            "id": "a2",
            "parentId": "u2",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "hub-stop",
                        "name": "hub",
                        "arguments": {"op": "stop", "name": "dev-server"},
                    }
                ],
                "stopReason": "toolUse",
            },
        },
    ]
    for item in stop_records:
        await _omp(session, item, events)  # type: ignore[arg-type]

    assert session.record.standing_activity == []


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


async def test_codex_model_comes_from_turn_context() -> None:
    """The CLI records the model per turn, not in `session_meta` or `token_count`.

    Both of those older sources are gone from current rollouts, so every Codex
    session reported no model at all while Claude sessions reported one. Reading
    it per turn also means a mid-conversation `/model` is picked up.
    """
    session = cast(Any, SimpleNamespace(record=record("codex")))
    events = EventBus()
    await _codex(session, {"type": "session_meta", "payload": {"id": "native-1"}}, events)
    assert session.record.model is None

    await _codex(
        session,
        {"type": "turn_context", "payload": {"cwd": ".", "model": "gpt-5.6-sol"}},
        events,
    )
    assert session.record.model == "gpt-5.6-sol"

    await _codex(
        session,
        {"type": "turn_context", "payload": {"cwd": ".", "model": "gpt-5.6-codex"}},
        events,
    )
    assert session.record.model == "gpt-5.6-codex"

    # A context with no model must not blank an already-known one.
    await _codex(session, {"type": "turn_context", "payload": {"cwd": "."}}, events)
    assert session.record.model == "gpt-5.6-codex"


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


async def test_codex_item_completed_envelope_does_not_degrade_parser() -> None:
    session = cast(Any, SimpleNamespace(record=record("codex"), publish_update=lambda: None))
    session.record.parser_status = "watching"
    events = EventBus()
    item_completed = {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "item": {"type": "CommandExecution", "id": "item-1"},
            "turn_id": "turn-1",
        },
    }

    for _ in range(20):
        recognized, signature = classify_transcript_event("codex", item_completed)
        await _record_parser_observation(session, events, recognized, signature)

    assert session.record.parser_status == "ready"
    assert session.record.parser_events_seen == 20
    assert session.record.parser_unknown_events == 0
    assert session.record.parser_unknown_signatures == {}


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

    emitted = [item.type for item in await drained_until(queue, "approval_needed")]

    assert session.record.state == "awaiting"
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
    emitted = [item.type for item in await drained_until(queue, "approval_needed")]

    assert session.record.state == "awaiting"
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
    drained = await drained_until(queue, "approval_needed")

    assert session.record.state == "awaiting"
    assert session.record.awaiting_reason == "question"
    emitted = [item for item in drained if item.type == "approval_needed"]
    assert len(emitted) == 1
    assert emitted[0].payload["kind"] == "input"


def _delegated_codex_session() -> Any:
    """A Codex session whose approvals are answered by the CLI's auto reviewer."""
    session = cast(Any, ReplaySession("codex"))
    session.approval_stabilization_seconds = 0.01
    session.approval_escalation_ceiling_seconds = 0.2
    session.approval_screen_poll_seconds = 0.01
    return session


async def _apply_turn_context(session: Any, events: EventBus, reviewer: str) -> None:
    await _codex(
        session,
        {
            "type": "turn_context",
            "payload": {"approval_policy": "on-request", "approvals_reviewer": reviewer},
        },
        events,
    )


async def test_auto_reviewed_approval_stays_invisible_while_the_tool_runs() -> None:
    """The reported flicker: a tool the auto reviewer approved but that outran the
    stabilization window used to surface as attention before its own completion
    could cancel it."""
    session = _delegated_codex_session()
    events = EventBus()
    queue = events.subscribe()
    await _apply_turn_context(session, events, "auto_review")
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    drain(queue)

    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    assert [item.type for item in drain(queue)] == ["approval_detected"]
    # Longer than the stabilization window, shorter than the escalation ceiling:
    # the window alone would already have committed by now.
    await asyncio.sleep(0.05)
    assert session.record.state == "working"

    await apply_hook_observation(session, "PostToolUse", {}, events)
    await asyncio.sleep(0.25)

    assert session.record.state == "working"
    assert "approval_needed" not in [item.type for item in drain(queue)]
    assert any(
        item.get("kind") == "approval_stabilization_cancelled"
        for item in session.state_transitions
    )


async def test_auto_reviewed_approval_surfaces_once_the_dialog_is_on_screen() -> None:
    session = _delegated_codex_session()
    events = EventBus()
    queue = events.subscribe()
    await _apply_turn_context(session, events, "auto_review")
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    drain(queue)

    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    session.scrollback.data = b"> \nAllow Codex to run npm test?\n  Yes  No\n"
    emitted = [item.type for item in await drained_until(queue, "approval_needed")]

    assert session.record.state == "awaiting"
    assert session.record.awaiting_reason == "approval"
    assert "approval_needed" in emitted
    assert any(
        item.get("kind") == "approval_stabilization_committed" and item.get("gate") == "screen"
        for item in session.state_transitions
    )


async def test_auto_reviewed_approval_surfaces_when_the_screen_never_speaks() -> None:
    """A classifier that cannot read the screen must not hide a block forever."""
    session = _delegated_codex_session()
    events = EventBus()
    queue = events.subscribe()
    await _apply_turn_context(session, events, "auto_review")
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    drain(queue)

    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    # The escalation ceiling is a real wait, so this one is generous: what must not
    # be fixed is the beat *after* it, which is what a loaded worker overshoots.
    emitted = [item.type for item in await drained_until(queue, "approval_needed")]

    assert session.record.state == "awaiting"
    assert "approval_needed" in emitted
    assert any(
        item.get("kind") == "approval_stabilization_committed" and item.get("gate") == "ceiling"
        for item in session.state_transitions
    )


async def test_a_user_reviewed_approval_keeps_the_plain_stabilization_window() -> None:
    session = _delegated_codex_session()
    events = EventBus()
    queue = events.subscribe()
    await _apply_turn_context(session, events, "user")
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    drain(queue)

    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    await until(lambda: session.record.state == "awaiting", what="the window committed")

    assert any(
        item.get("kind") == "approval_stabilization_committed" and item.get("gate") == "stabilized"
        for item in session.state_transitions
    )


async def test_tool_completion_retires_an_approval_the_transcript_is_driving() -> None:
    """`PostToolUse` cancels even when transcript authority makes it a no-op."""
    session = _delegated_codex_session()
    session.transcript_path = Path("rollout.jsonl")
    events = EventBus()
    await _apply_turn_context(session, events, "auto_review")
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    assert isinstance(session.observation_state.get("pending_approval"), dict)
    # A transcript that has grown since the last turn hook owns this session's
    # state, which is what used to make `PostToolUse` return before cancelling.
    session.transcript_growth_ts = time.time() + 60.0
    assert _transcript_authoritative(session)

    await apply_hook_observation(session, "PostToolUse", {}, events)

    assert session.observation_state.get("pending_approval") is None


async def test_an_approval_on_screen_survives_a_parallel_tool_completing() -> None:
    session = _delegated_codex_session()
    events = EventBus()
    await _apply_turn_context(session, events, "auto_review")
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(session, "PermissionRequest", {"tool_name": "shell"}, events)
    session.scrollback.data = b"> \nAllow Codex to run rm -rf build?\n  Yes  No\n"

    await apply_hook_observation(session, "PostToolUse", {}, events)

    assert isinstance(session.observation_state.get("pending_approval"), dict)


async def test_parallel_tool_completion_cannot_cancel_another_approval_candidate() -> None:
    session = cast(Any, ReplaySession("claude"))
    session.approval_stabilization_seconds = 0.05
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(
        session,
        "PermissionRequest",
        {"tool_name": "Read", "tool_use_id": "read-pending"},
        events,
    )
    assert isinstance(session.observation_state.get("pending_approval"), dict)

    # The screen cache can still read working in the narrow race before Claude's
    # cli-state poll observes `waiting`. Tool identity must independently keep an
    # unrelated Bash completion from retiring the Read prompt.
    session.scrollback.data = b"working spinner..."
    await apply_hook_observation(
        session,
        "PostToolUse",
        {"tool_name": "Bash", "tool_use_id": "bash-finished"},
        events,
    )

    assert isinstance(session.observation_state.get("pending_approval"), dict)
    await until(lambda: session.record.state == "awaiting", what="the Read approval committed")
    assert session.record.awaiting_reason == "approval"


async def test_matching_tool_completion_retires_its_approval_candidate() -> None:
    session = cast(Any, ReplaySession("claude"))
    session.approval_stabilization_seconds = 0.05
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(
        session,
        "PermissionRequest",
        {"tool_name": "Read", "tool_use_id": "read-pending"},
        events,
    )
    session.cli_state = {"status": "waiting"}

    await apply_hook_observation(
        session,
        "PostToolUse",
        {"tool_name": "Read", "tool_use_id": "read-pending"},
        events,
    )
    await asyncio.sleep(0.07)

    assert session.observation_state.get("pending_approval") is None
    assert session.record.state == "working"


async def test_parallel_tool_completion_cannot_clear_a_visible_approval_by_identity() -> None:
    session = cast(Any, ReplaySession("claude"))
    session.approval_stabilization_seconds = 0.01
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    await apply_hook_observation(
        session,
        "PermissionRequest",
        {"tool_name": "Read", "tool_use_id": "read-pending"},
        events,
    )
    await until(lambda: session.record.state == "awaiting", what="the Read approval committed")

    # Exercise the identity guard independently of both dialog text and the
    # cli-state poll. The unrelated completion cannot retire this Read approval.
    session.scrollback.data = b"parallel Bash spinner..."
    session.cli_state = None
    await apply_hook_observation(
        session,
        "PostToolUse",
        {"tool_name": "Bash", "tool_use_id": "bash-finished"},
        events,
    )
    assert session.record.state == "awaiting"
    assert session.record.awaiting_reason == "approval"

    await apply_hook_observation(
        session,
        "PostToolUse",
        {"tool_name": "Read", "tool_use_id": "read-pending"},
        events,
    )
    assert session.record.state == "working"
    assert session.record.awaiting_reason is None


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
    session.transcript_growth_ts = session.clock.wall()
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


async def test_a_non_reporting_transcript_still_lets_hooks_drive_state() -> None:
    # Measurement confidence does not grant state authority. With no observed
    # transcript growth, hooks remain the source that keeps state moving.
    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "degraded"
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {}, events)
    assert session.record.state == "working"


async def test_degraded_parser_withholds_new_measurements() -> None:
    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "degraded"
    session.record.tokens_in = 7
    session.record.tokens_out = 3
    session.record.context_pct = 0.25
    session.record.model = "previous"

    await _claude(
        session,
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "model": "new-model",
                "usage": {"input_tokens": 900, "output_tokens": 100},
            },
        },
        EventBus(),
    )

    assert session.record.tokens_in == 7
    assert session.record.tokens_out == 3
    assert session.record.context_pct == 0.25
    assert session.record.model == "previous"


async def test_transcript_close_latch_blocks_hook_reopen_until_new_transcript_turn() -> None:
    # #2 backstop: independent of the apply_hook gate, a transcript-closed turn
    # cannot be reopened by a hook-sourced begin; only fresh transcript activity
    # starts the next turn and clears the latch.
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
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


async def test_completed_turn_records_its_duration_on_the_record() -> None:
    """A ready row reports how long the last turn took, so the turn must be timed.

    The measurement lands on the record rather than only on the event because a
    browser that connects after the turn ended still has to render the number.
    """
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    assert session.record.last_turn_ms is None

    await _begin_root_turn(session, events, source="transcript")
    session.clock.advance(72.0)
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.last_turn_ms == 72_000.0
    assert session.record.state_since == session.clock.wall()


async def test_harness_reported_duration_outranks_the_daemon_wall_clock() -> None:
    """The harness times the turn itself; the daemon also times its own latency."""
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    await _begin_root_turn(session, events, source="transcript")
    session.clock.advance(90.0)
    await _finish_root_turn(session, events, source="transcript", duration_ms=41_000)
    assert session.record.last_turn_ms == 41_000.0


async def test_implausibly_long_turn_is_discarded_rather_than_reported() -> None:
    """A missed completion leaves the turn open; closing it hours later is not a turn.

    Keeping the previous measurement is the honest failure: an overnight-idle
    session must not claim its last turn took nine hours.
    """
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    await _begin_root_turn(session, events, source="transcript")
    session.clock.advance(12.0)
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.last_turn_ms == 12_000.0

    await _begin_root_turn(session, events, source="transcript")
    session.clock.advance(9 * 60 * 60)
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.last_turn_ms == 12_000.0


async def test_instantaneous_turn_is_discarded_rather_than_reported() -> None:
    """The other end of the same rule: a turn is a model round trip, not an instant.

    A boundary pair landing on one moment is an artifact of how it was observed,
    and publishing it renders as the literal `0s` a duration column exists to
    avoid. Keeping the previous measurement is the same honest failure as the
    six-hour case.
    """
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    await _begin_root_turn(session, events, source="transcript")
    session.clock.advance(31.0)
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.last_turn_ms == 31_000.0

    await _begin_root_turn(session, events, source="transcript")
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.last_turn_ms == 31_000.0


async def test_a_refused_close_leaves_the_turn_running() -> None:
    """Closing a turn is the arbiter's call, so nothing may be dismantled first.

    A `Stop` hook arbitrated away while the transcript owns boundaries used to
    take the turn down anyway: the session stayed `working` with no turn, the row
    fell back to ageing the state (which restarts on every tool call), and the
    next tool call reopened the turn and restamped its start — a timer reset with
    no state change to explain it.
    """
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    await _begin_root_turn(session, events, source="transcript")
    started = session.record.turn_started_at
    session.clock.advance(240.0)

    # Refuse whatever the close proposes, the way arbitration does for a hook
    # that has lost to the ordered transcript.
    session.transition = lambda *args, **kwargs: False
    await _finish_root_turn(session, events, source="hook", evidence="hook:Stop")

    assert session.record.state == "working"
    assert session.record.turn_started_at == started
    assert session.observation_state["root_turn_active"] is True
    assert session.record.last_turn_ms is None


async def test_interrupted_task_complete_closes_even_when_provider_will_continue() -> None:
    """OMP/pi continuation is about the next run, not this aborted run."""
    session = cast(Any, ReplaySession("omp"))
    events = EventBus()
    queue = events.subscribe()

    await apply_hook_observation(
        session, "task_started", {"turn_id": "omp-root-1"}, events
    )
    session.clock.advance(4.0)
    await apply_hook_observation(
        session,
        "task_complete",
        {
            "turn_id": "omp-root-1",
            "outcome": "interrupted",
            "stop_reason": "aborted",
            "will_continue": True,
        },
        events,
    )

    assert session.record.state == "idle"
    assert session.record.turn_started_at is None
    assert session.record.active_turn_id is None
    aborted = [event for event in drain(queue) if event.type == "turn_aborted"]
    assert len(aborted) == 1
    assert aborted[0].payload["outcome"] == "interrupted"
    assert aborted[0].payload["turn_id"] == "omp-root-1"


async def test_interrupt_intent_is_pending_until_terminal_evidence_arrives() -> None:
    from swe_mux.observation import _begin_root_turn, _finish_root_turn, note_interrupt_intent

    session = cast(Any, ReplaySession("codex"))
    events = EventBus()
    await _begin_root_turn(
        session, events, source="transcript", turn_id="turn-1"
    )
    session.clock.advance(9.0)

    assert note_interrupt_intent(session, "\x03", source="voice") is True
    requested_at = session.record.interrupt_pending_at
    assert requested_at == session.clock.wall()
    assert session.record.state == "working"
    assert session.record.turn_started_at is not None

    session.clock.advance(1.0)
    await _finish_root_turn(
        session,
        events,
        source="transcript",
        outcome="interrupted",
        turn_id="turn-1",
    )
    assert session.record.interrupt_pending_at is None
    assert session.record.interrupt_pending_source is None
    assert session.record.state == "idle"


async def test_continuation_hint_cannot_reopen_a_turn_closed_by_transcript() -> None:
    """A late agent_end must not recreate working with no active turn or timer."""
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("omp"))
    events = EventBus()
    await _begin_root_turn(
        session, events, source="transcript", turn_id="omp-root-1"
    )
    session.clock.advance(4.0)
    await _finish_root_turn(
        session, events, source="transcript", turn_id="omp-root-1"
    )

    await apply_hook_observation(
        session,
        "task_complete",
        {"turn_id": "omp-root-1", "will_continue": True},
        events,
    )

    assert session.record.state == "idle"
    assert session.record.turn_started_at is None
    assert session.observation_state["root_turn_active"] is False


async def test_new_prompt_repairs_missing_terminal_boundary_and_resets_timer() -> None:
    """A later root request proves the prior request is no longer in flight."""
    from swe_mux.observation import _begin_root_turn

    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    queue = events.subscribe()
    await _begin_root_turn(
        session,
        events,
        source="transcript",
        evidence="user_prompt_record",
        logical_root=True,
        prompt="first request",
    )
    first_started = session.record.turn_started_at
    first_epoch = session.record.turn_epoch
    session.observation_state["turn_saw_activity"] = True
    session.clock.advance(47.0)

    await _begin_root_turn(
        session,
        events,
        source="transcript",
        evidence="user_prompt_record",
        logical_root=True,
        prompt="second request",
    )

    assert session.record.state == "working"
    assert session.record.turn_epoch == first_epoch + 1
    assert session.record.turn_started_at == session.clock.wall()
    assert session.record.turn_started_at != first_started
    assert session.status_health_counters["turn_boundary_recovered"] == 1
    emitted = drain(queue)
    recovered = [event for event in emitted if event.type == "turn_aborted"]
    assert len(recovered) == 1
    assert recovered[0].payload["outcome"] == "superseded"
    assert recovered[0].payload["recovered_boundary"] is True


async def test_hook_and_transcript_prompt_evidence_share_one_turn_generation() -> None:
    """Two sources reporting one submission must not look like two turns."""
    from swe_mux.observation import _begin_root_turn

    session = cast(Any, ReplaySession("claude"))
    events = EventBus()
    await _begin_root_turn(
        session,
        events,
        source="hook",
        logical_root=True,
        prompt="same request",
    )
    started = session.record.turn_started_at
    epoch = session.record.turn_epoch
    session.clock.advance(2.0)

    await _begin_root_turn(
        session,
        events,
        source="transcript",
        logical_root=True,
        prompt="same request",
    )

    assert session.record.turn_epoch == epoch
    assert session.record.turn_started_at == started
    assert "turn_boundary_recovered" not in session.status_health_counters


async def test_stale_terminal_id_cannot_close_the_new_turn() -> None:
    """A retried completion for generation N cannot terminate generation N+1."""
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("codex"))
    events = EventBus()
    await _begin_root_turn(
        session, events, source="transcript", turn_id="turn-1"
    )
    session.clock.advance(10.0)
    await _begin_root_turn(
        session, events, source="transcript", turn_id="turn-2"
    )
    second_started = session.record.turn_started_at

    await _finish_root_turn(
        session, events, source="transcript", turn_id="turn-1"
    )

    assert session.record.state == "working"
    assert session.record.active_turn_id == "turn-2"
    assert session.record.turn_started_at == second_started
    assert session.status_health_counters["stale_turn_terminal_ignored"] == 1


async def test_a_human_submit_dates_the_last_prompt() -> None:
    from swe_mux.observation import apply_hook_observation

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    events = EventBus()
    assert session.record.last_human_prompt_at is None

    await apply_hook_observation(session, "UserPromptSubmit", {"prompt": "do it"}, events)
    assert session.record.last_human_prompt_at == session.clock.wall()


async def test_an_agent_authored_delivery_does_not_count_as_you_asking() -> None:
    """A teammate's message opens a turn; it does not reset "since you asked".

    This is the whole point of the field: a session fed by siblings is minutes
    into a fresh turn and an hour past anything its operator said.
    """
    from swe_mux.observation import apply_hook_observation

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    events = EventBus()
    await apply_hook_observation(session, "UserPromptSubmit", {"prompt": "do it"}, events)
    asked_at = session.record.last_human_prompt_at

    session.clock.advance(600.0)
    session.queue_delivery_mark = (session.clock.wall(), False)
    await apply_hook_observation(
        session, "UserPromptSubmit", {"prompt": "teammate says hello"}, events
    )
    assert session.record.last_human_prompt_at == asked_at


async def test_a_queued_message_you_wrote_still_counts_as_you_asking() -> None:
    """Authorship, not delivery mechanism: your queued message is you speaking."""
    from swe_mux.observation import apply_hook_observation

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    events = EventBus()
    session.clock.advance(600.0)
    session.queue_delivery_mark = (session.clock.wall(), True)
    await apply_hook_observation(session, "UserPromptSubmit", {"prompt": "queued"}, events)
    assert session.record.last_human_prompt_at == session.clock.wall()


async def test_a_stale_delivery_mark_cannot_disown_a_typed_prompt() -> None:
    """A delivery whose hook never arrived must not silence the next real one."""
    from swe_mux.observation import (
        QUEUE_DELIVERY_ATTRIBUTION_SECONDS,
        apply_hook_observation,
    )

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    events = EventBus()
    session.queue_delivery_mark = (session.clock.wall(), False)
    session.clock.advance(QUEUE_DELIVERY_ATTRIBUTION_SECONDS + 1.0)
    await apply_hook_observation(session, "UserPromptSubmit", {"prompt": "typed"}, events)
    assert session.record.last_human_prompt_at == session.clock.wall()


def _iso(ts: float) -> str:
    """A transcript record's own stamp, in the shape every harness writes it."""
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


async def test_turn_length_comes_from_the_records_not_from_the_daemons_clock() -> None:
    """The bug this exists for: replaying a transcript re-derives the real length.

    The clock deliberately never advances here — that is what catch-up after a
    daemon restart looks like, a whole conversation arriving in one read. Dating
    both ends from the wall clock measured how fast the replay ran and wrote a
    turn of `0.0` ms, which the sidebar renders as nothing, or of a millisecond
    or two, which it renders as the literal `0s` that started this.
    """
    from swe_mux.observation import _dispatch_transcript_event

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    opened = session.clock.wall() - 4_000.0

    await _dispatch_transcript_event(
        "claude",
        session,
        {"timestamp": _iso(opened), "type": "user", "message": {"content": "do the thing"}},
        events,
    )
    assert session.record.turn_started_at == opened

    await _dispatch_transcript_event(
        "claude",
        session,
        {
            "timestamp": _iso(opened + 95.0),
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"},
        },
        events,
    )
    assert session.record.last_turn_ms == 95_000.0


async def test_record_dated_turns_are_shared_by_every_transcript_harness() -> None:
    """The rule lives at the shared dispatch, so a harness cannot opt out of it.

    claude, codex, omp and pi all write a top-level ISO `timestamp`; dating the
    boundary from it is one rule at `_dispatch_transcript_event` rather than four
    inside the readers, and this pins that a non-Claude harness gets it too.
    """
    from swe_mux.observation import _dispatch_transcript_event

    session = cast(Any, ReplaySession("codex"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    opened = session.clock.wall() - 900.0

    await _dispatch_transcript_event(
        "codex",
        session,
        {"timestamp": _iso(opened), "type": "event_msg", "payload": {"type": "task_started"}},
        events,
    )
    assert session.record.turn_started_at == opened

    await _dispatch_transcript_event(
        "codex",
        session,
        {
            "timestamp": _iso(opened + 42.0),
            "type": "event_msg",
            "payload": {"type": "task_complete"},
        },
        events,
    )
    assert session.record.last_turn_ms == 42_000.0


async def test_record_stamp_does_not_outlive_the_record_being_dispatched() -> None:
    """It describes the record in flight and nothing else.

    Leaking past the dispatch would date a live hook boundary — or the next
    record, which carries no stamp of its own — with a timestamp that belongs to
    something already handled.
    """
    from swe_mux.observation import _dispatch_transcript_event

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()

    await _dispatch_transcript_event(
        "claude",
        session,
        {
            "timestamp": _iso(session.clock.wall() - 500.0),
            "type": "user",
            "message": {"content": "hello"},
        },
        events,
    )
    assert getattr(session, "observation_record_ts", None) is None


async def test_a_future_dated_record_falls_back_to_the_clock() -> None:
    """A stamp the daemon cannot have observed yet is corrupt, not evidence."""
    from swe_mux.observation import _dispatch_transcript_event

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()

    await _dispatch_transcript_event(
        "claude",
        session,
        {
            "timestamp": _iso(session.clock.wall() + 86_400.0),
            "type": "user",
            "message": {"content": "hello"},
        },
        events,
    )
    assert session.record.turn_started_at == session.clock.wall()


async def test_catchup_readopts_an_open_turn_at_the_time_the_records_say() -> None:
    """A session working across a restart keeps ageing from the work, not the restart.

    Catch-up finding the transcript's last turn still open used to restamp it as
    beginning now, so every working row in the fleet read `0s` the moment the
    daemon came back and then counted up from the restart.
    """
    from swe_mux.observation import _dispatch_transcript_event, _finish_transcript_catchup

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    opened = session.clock.wall() - 610.0

    session.observation_replay = True
    await _dispatch_transcript_event(
        "claude",
        session,
        {"timestamp": _iso(opened), "type": "user", "message": {"content": "long job"}},
        events,
    )
    await _finish_transcript_catchup(
        session,
        events,
        attach_ts=session.clock.wall(),
        last_historical_ts=session.clock.wall() - 5.0,
        historical_seen=4,
    )

    assert session.record.state == "working"
    assert session.record.turn_started_at == opened
    assert session.observation_state["turn_started_at"] == opened


async def test_catchup_that_settles_leaves_no_turn_dated_behind_it() -> None:
    """Nothing may still be dated as if a turn were running once none is.

    The record survives a session-preserving restart, so a stamp left here is one
    the next `working` reading would age from — a turn that ended yesterday
    explaining how long today's work has taken.
    """
    from swe_mux.observation import _dispatch_transcript_event, _finish_transcript_catchup

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()

    session.observation_replay = True
    await _dispatch_transcript_event(
        "claude",
        session,
        {
            "timestamp": _iso(session.clock.wall() - 90_000.0),
            "type": "user",
            "message": {"content": "yesterday's job"},
        },
        events,
    )
    assert session.record.turn_started_at is not None

    await _finish_transcript_catchup(
        session,
        events,
        attach_ts=session.clock.wall(),
        last_historical_ts=session.clock.wall() - 90_000.0,
        historical_seen=4,
    )

    assert session.record.state == "idle"
    assert session.record.turn_started_at is None
    assert not session.observation_state["turn_started_at"]


async def test_out_of_order_boundary_does_not_publish_a_negative_turn_as_zero() -> None:
    """A close dated before its own start is not a turn that took no time.

    It used to clamp to zero and publish, which is the shape of a real
    measurement. Records can arrive out of order across a rollover, and the row
    must not report one as a completed instant.
    """
    from swe_mux.observation import _begin_root_turn, _finish_root_turn

    session = cast(Any, ReplaySession("claude"))
    session.record.parser_status = "ready"
    session.transcript_growth_ts = session.clock.wall()
    events = EventBus()
    await _begin_root_turn(session, events, source="transcript")
    session.observation_state["turn_started_at"] = session.clock.wall() + 90.0
    await _finish_root_turn(session, events, source="transcript")
    assert session.record.last_turn_ms is None


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
    mgr._pty_tail_explanation = SessionManager._pty_tail_explanation
    mgr._note_pty_tail_readings = SessionManager._note_pty_tail_readings
    mgr._pty_tail_state = SessionManager._pty_tail_state
    mgr._pty_appears_idle = lambda s: SessionManager._pty_appears_idle(cast(Any, mgr), s)
    mgr._check_unwitnessed_pty_turn = lambda s, n: SessionManager._check_unwitnessed_pty_turn(
        cast(Any, mgr), s, n
    )
    return mgr


async def test_ssh_prompt_enters_and_clears_typed_authentication_wait() -> None:
    from swe_mux.session import SessionManager

    now = time.time()
    session = _watchdog_session(
        None, now, b"builder@example.test's password: ", last_hook_ts=0.0
    )
    session.record.backend = "shell"
    session.record.state = "running"
    session.state_source_priority = -1
    session.last_state_change_monotonic = time.monotonic()
    session.last_evidence_ts = now
    session.tasks = set()
    session.publish_update = lambda: None
    manager = _fake_manager()

    handled = await SessionManager._check_ssh_boundary_state(manager, session, now)
    await asyncio.gather(*session.tasks)

    assert handled is True
    assert session.record.runtime_boundary == "remote"
    assert session.record.remote_authority == "unknown"
    assert session.record.state == "awaiting"
    assert session.record.awaiting_reason == "authentication"
    assert session.record.state_detail == "SSH authentication required"

    session.scrollback = screen(b"remote shell ready")
    handled = await SessionManager._check_ssh_boundary_state(
        manager, session, now + 1
    )
    assert handled is False
    assert session.record.state == "running"
    assert session.record.awaiting_reason is None
    assert session.record.remote_transport_state == "connected"

    session.scrollback = screen(b"client_loop: send disconnect: Broken pipe")
    handled = await SessionManager._check_ssh_boundary_state(
        manager, session, now + 2
    )
    assert handled is True
    assert session.record.runtime_boundary == "remote"
    assert session.record.remote_transport_state == "ended"
    assert session.record.state == "running"


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


async def test_parallel_tool_idle_repaint_preserves_turn_and_timer(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from swe_mux.session import SessionManager

    calls: list[dict[str, Any]] = []

    async def fake_finish(_session: Any, _events: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("swe_mux.observation._finish_root_turn", fake_finish)

    path = tmp_path / "native.jsonl"
    _open_tail_transcript(path)
    now = time.time() + 10.0
    session = _watchdog_session(
        path,
        now,
        b"Ruff finished\nPytest still running\n>\n(shift+tab to cycle)\n",
    )
    started_at = now - 100.0
    session.cli_state = {"status": "busy"}
    session.record.turn_started_at = started_at
    session.observation_state["turn_started_at"] = started_at

    await SessionManager._watchdog_check_session(_fake_manager(), session, now)

    assert calls == []
    assert session.record.state == "working"
    assert session.record.turn_started_at == started_at
    assert session.observation_state["turn_started_at"] == started_at
    assert session.layer_readings["pty_tail_screen"] == "idle"
    assert session.layer_readings["pty_tail"] == "working"
    assert session.layer_readings["pty_tail_arbitration"] == "cli_state_busy"


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


async def test_historical_catchup_retains_the_newest_record_timestamp(tmp_path: Path) -> None:
    """A late first attach must retain when the completed turn was written."""
    import datetime as dt

    path = tmp_path / "rollout.jsonl"
    completed = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    path.write_text(
        json.dumps(
            {
                "timestamp": completed.isoformat().replace("+00:00", "Z"),
                "type": "event_msg",
                "payload": {"type": "task_complete"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = ReplaySession("codex")
    events = EventBus()
    stop = asyncio.Event()
    task = asyncio.create_task(observe_transcript(session, path, events, stop))

    await asyncio.sleep(0.6)
    assert session.transcript_growth_ts == 0.0
    assert abs(session.transcript_record_ts - completed.timestamp()) < 0.01
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


async def drain_tailer(
    path: Path, growth: list[float], *, settle: float = 0.5, then: Any = None
) -> None:
    """Tail `path`, recording every growth report, running `then` partway through."""
    stop = asyncio.Event()

    async def collect() -> None:
        async for _item, _historical in JsonlTailer(
            path, on_growth=lambda: growth.append(time.time())
        ).events(stop):
            pass

    task = asyncio.create_task(collect())
    try:
        await asyncio.sleep(settle)
        if then is not None:
            then()
            await asyncio.sleep(settle)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)


async def test_the_tailer_reports_bytes_written_after_it_attached(tmp_path: Path) -> None:
    # The daemon's only first-hand evidence that the transcript it follows is still
    # being written. It cannot come from `stat().st_mtime`: on Windows a live file's
    # last-write time can stay frozen at its creation for hours (measured 2026-08-06
    # across five Codex rollouts, every Win32 timestamp API agreeing), while
    # `st_size` — which this loop already polls — stays accurate.
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n')
    growth: list[float] = []

    def append() -> None:
        with path.open("ab") as handle:
            handle.write(b'{"n":2}\n')

    await drain_tailer(path, growth, then=append)

    assert len(growth) == 1


async def test_replaying_the_attach_snapshot_is_not_growth(tmp_path: Path) -> None:
    # Catching up on bytes that were already there proves nothing about a live
    # writer, and counting it would suppress staleness detection for the whole
    # window after every daemon restart — exactly the sessions the fail-closed
    # guard exists for.
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n{"n":3}\n')
    growth: list[float] = []

    await drain_tailer(path, growth)

    assert growth == []


async def test_appended_bytes_count_as_growth_even_when_unparseable(
    tmp_path: Path,
) -> None:
    # The reason growth is tracked separately from record reads at all. A partial
    # line, or one the parser rejects, is still proof the file is alive — and
    # `_record_parser_observation`, the other thing that retracts a staleness claim,
    # never fires for it.
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n')
    growth: list[float] = []

    def append_garbage() -> None:
        with path.open("ab") as handle:
            handle.write(b"not json at all, and no newline either")

    await drain_tailer(path, growth, then=append_garbage)

    assert len(growth) == 1


async def test_a_rewritten_transcript_counts_as_growth(tmp_path: Path) -> None:
    # Claude's cancel/revert truncates and rewrites the file. Whoever did that is
    # demonstrably still writing it.
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')
    growth: list[float] = []

    def rewrite() -> None:
        path.write_bytes(b'{"n":1}\n')

    await drain_tailer(path, growth, then=rewrite)

    assert growth != []


def synthetic_transcript(path: Path, target_bytes: int) -> int:
    """Write a transcript of roughly `target_bytes`, returning its record count.

    Shaped like a real Claude assistant record rather than `{"n":1}`, because the
    cost this exercises is the per-line `json.loads` of a nested object, not the
    read.
    """
    line = (
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-19T01:00:00Z",
                "message": {
                    "content": [{"type": "text", "text": "x" * 400}],
                    "stop_reason": "end_turn",
                    "model": "claude-opus-4-8",
                    "usage": {"input_tokens": 1_000, "output_tokens": 50},
                },
            }
        )
        + "\n"
    ).encode("utf-8")
    count = max(1, target_bytes // len(line))
    with path.open("wb") as handle:
        for _ in range(count):
            handle.write(line)
    return count


async def collect_tail(
    path: Path, seen: list[tuple[dict[str, Any] | None, bool]], stop: asyncio.Event
) -> None:
    """Record everything a tailer yields, boundary markers included."""
    async for item in JsonlTailer(path).events(stop):
        seen.append(item)


def window_spy(monkeypatch: pytest.MonkeyPatch, lengths: list[int]) -> None:
    """Record the length of every window the tailer asks the filesystem for."""
    real = observation._read_transcript_window

    def spy(path: Path, offset: int, length: int, prefix_len: int) -> tuple[bytes, bytes]:
        lengths.append(length)
        return real(path, offset, length, prefix_len)

    monkeypatch.setattr(observation, "_read_transcript_window", spy)


async def test_a_large_attach_replay_keeps_the_event_loop_serviced(tmp_path: Path) -> None:
    """Replay must not be one uninterruptible span, however big the transcript is.

    A resumed Claude conversation's transcript is routinely tens of MB, and the
    daemon re-reads all of it on every attach and every rebind. Measured on the
    primary host while this read the whole file and decoded it in one go: a 24 MiB
    transcript held the loop for 290ms and a 48 MiB one for 691ms, and a heartbeat
    task got exactly *zero* turns in between - nothing else in the daemon ran, for
    any session, for the duration.

    The assertion is a count rather than a duration on purpose: the gate runs
    across every core, so a wall-clock bound would be a bet on scheduling, while
    "the loop was serviced at least once per window" is true no matter how loaded
    the machine is - each window crosses a thread boundary, and crossing one hands
    the loop back.
    """
    path = tmp_path / "big.jsonl"
    written = synthetic_transcript(path, 16 * 1024 * 1024)
    windows = -(-path.stat().st_size // _TAIL_CHUNK_BYTES)
    assert windows > 8, "the file must be large enough for windowing to be the claim"

    ticks = 0
    replayed = 0
    caught_up = False
    stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0)
            ticks += 1

    async def collect() -> None:
        nonlocal replayed, caught_up
        async for item, historical in JsonlTailer(path).events(stop):
            if item is None:
                caught_up = not historical
                return
            assert historical, "the attach snapshot is history, not live behavior"
            replayed += 1

    beat = asyncio.create_task(heartbeat())
    task = asyncio.create_task(collect())
    try:
        await until(lambda: caught_up, seconds=60, what="the attach replay finished")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)
        await asyncio.wait_for(beat, timeout=10)

    assert replayed == written
    assert ticks >= windows


async def test_attach_replay_reads_one_bounded_window_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Peak memory is a window and its records, not the whole file.

    The bound is what makes the cost of attaching to a 100 MB transcript the same
    as attaching to a 1 MB one; asserting on the requested lengths states it
    directly instead of inferring it from an RSS reading.
    """
    path = tmp_path / "big.jsonl"
    synthetic_transcript(path, 4 * 1024 * 1024)
    size = path.stat().st_size
    lengths: list[int] = []
    window_spy(monkeypatch, lengths)

    stop = asyncio.Event()
    seen: list[tuple[dict[str, Any] | None, bool]] = []
    task = asyncio.create_task(collect_tail(path, seen, stop))
    try:
        await until(
            lambda: (None, False) in seen, seconds=60, what="the attach snapshot was replayed"
        )
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=10)

    assert lengths, "the replay read nothing"
    assert max(lengths) <= _TAIL_CHUNK_BYTES
    assert sum(lengths) >= size


async def test_a_record_split_across_windows_keeps_the_replay_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windowing may not move the historical/live line, not even mid-record.

    A window boundary lands wherever the byte count says, so it routinely falls
    inside a JSON line. Five-byte windows put one inside every record here; the
    sequence must still be the one an unwindowed read produced, byte position for
    byte position.
    """
    monkeypatch.setattr(observation, "_TAIL_CHUNK_BYTES", 5)
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')
    stop = asyncio.Event()
    seen: list[tuple[dict[str, Any] | None, bool]] = []
    task = asyncio.create_task(collect_tail(path, seen, stop))
    try:
        await until(lambda: (None, False) in seen, what="the attach snapshot was replayed")
        with path.open("ab") as handle:
            handle.write(b'{"n":3}\n')
        await until(lambda: ({"n": 3}, False) in seen, what="the live record arrived")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    assert seen == [({"n": 1}, True), ({"n": 2}, True), (None, False), ({"n": 3}, False)]


async def test_a_same_length_rewrite_is_read_as_a_replacement(tmp_path: Path) -> None:
    """Claude's cancel/revert can land on the length it replaced.

    `size < offset` catches the common case where the rewrite is shorter, and
    nothing else about the file's *size* can betray a rewrite that is not. The
    leading bytes are the identity that does.
    """
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')
    stop = asyncio.Event()
    seen: list[tuple[dict[str, Any] | None, bool]] = []
    task = asyncio.create_task(collect_tail(path, seen, stop))
    try:
        await until(lambda: (None, False) in seen, what="the attach snapshot was replayed")
        replacement = b'{"n":9}\n{"n":8}\n'
        assert len(replacement) == path.stat().st_size
        path.write_bytes(replacement)
        await until(lambda: ({"n": 8}, True) in seen, what="the replacement snapshot was replayed")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    assert seen.index((None, True)) < seen.index(({"n": 9}, True))


async def _poll_once(tailer: JsonlTailer, stop: asyncio.Event) -> list[Any]:
    return [item async for item in tailer._poll(stop)]


async def test_a_rewrite_stat_cannot_see_is_still_caught_by_the_prefix_backstop(
    tmp_path: Path,
) -> None:
    """The Windows trap the identity check is built around.

    A transcript held open by its writer reports its *creation* time as
    `st_mtime` for as long as the handle lives - measured 2026-08-06 across five
    Codex rollouts, with every Win32 timestamp API agreeing - so a rewrite is
    allowed to leave every `stat()` field this tailer may trust exactly as it
    found them. `os.utime` reproduces that here. The identity check therefore
    only ever *skips* work, never concludes from it that the file is unchanged:
    the prefix backstop is what closes the case, and this drives the poll
    directly so the interval is a decision rather than a wall-clock wait.
    """
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n{"n":2}\n')
    stop = asyncio.Event()
    tailer = JsonlTailer(path)
    assert await _poll_once(tailer, stop) == [
        ({"n": 1}, True),
        ({"n": 2}, True),
        (None, False),
    ]
    identity = tailer._identity
    assert identity is not None

    path.write_bytes(b'{"n":9}\n{"n":8}\n')
    os.utime(path, ns=(identity[3], identity[3]))
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (identity[0], identity[3])

    # Inside the interval the tailer takes stat() at its word and touches nothing.
    assert await _poll_once(tailer, stop) == []
    tailer._prefix_checked_at -= _TAIL_IDENTITY_PROBE_SECONDS
    assert await _poll_once(tailer, stop) == [
        (None, True),
        ({"n": 9}, True),
        ({"n": 8}, True),
        (None, False),
    ]


async def test_an_idle_transcript_is_not_reopened_on_every_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The poll used to cost an open and a 64-byte read per session per 250ms.

    It bought exactly one thing: catching a rewrite that `stat()` reports
    identically. That is now the backstop's job, on a much longer clock, so a
    quiet transcript costs one `stat()` a tick and no opens at all.

    The quiet window *is* the claim here, so the fixed sleep is the point rather
    than the hazard the settle helpers exist for (see `tests/support/settle.py`) -
    a loaded machine polls fewer times, never more.
    """
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n')
    lengths: list[int] = []
    window_spy(monkeypatch, lengths)

    stop = asyncio.Event()
    seen: list[tuple[dict[str, Any] | None, bool]] = []
    task = asyncio.create_task(collect_tail(path, seen, stop))
    try:
        await until(lambda: (None, False) in seen, what="the attach snapshot was replayed")
        lengths.clear()
        await asyncio.sleep(1.0)
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    # Four polls a second for a second: four opens before, at most one backstop now.
    assert len(lengths) <= 1


async def test_the_observer_stamps_growth_onto_the_session(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'{"n":1}\n')
    session = cast(
        Any,
        SimpleNamespace(
            record=record("codex"),
            publish_update=lambda: None,
            transcript_growth_ts=0.0,
        ),
    )
    stop = asyncio.Event()

    task = asyncio.create_task(observe_transcript(session, path, EventBus(), stop))
    try:
        await asyncio.sleep(0.5)
        assert session.transcript_growth_ts == 0.0
        with path.open("ab") as handle:
            handle.write(b'{"n":2}\n')
        # The quiet window above is the claim; this half only has to happen, so it
        # waits for the stamp rather than for another fixed half-second.
        await until(lambda: session.transcript_growth_ts > 0.0, what="growth was stamped")
    finally:
        stop.set()
        await asyncio.wait_for(task, timeout=2)


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


async def test_the_watchdog_skips_a_session_with_no_process(tmp_path: Path) -> None:
    """Nothing the watchdog does can apply to a terminal session.

    The terminal latch already refuses every transition it would attempt, so the
    work was only ever wasted - but it stopped being *only* wasted once ended
    panes started being retained and a crash could restore dozens of cold
    sessions at boot, each re-reading a buffer that will never change, twice a
    pass, forever.
    """
    from swe_mux.session import SessionManager

    path = tmp_path / "native.jsonl"
    _open_tail_transcript(path)
    now = time.time() + 10.0
    for state in ("exited", "crashed"):
        session = _watchdog_session(path, now, b"> \n")
        session.record.state = state
        session.record.standing_activity = []
        await SessionManager._watchdog_check_session(_fake_manager(), session, now)
        # Untouched: no layer readings taken, no state moved, nothing published.
        assert session.record.state == state
        assert not getattr(session, "layer_readings", {})


def _model_session(requested: str, observed: str, *, backend: str = "omp") -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(
            id="s1",
            backend=backend,
            model_requested=requested,
            model=observed,
            provider="anthropic",
        ),
        model_divergence_noted="",
        observation_replay=False,
        publish_update=lambda: None,
    )


def test_a_session_running_a_different_model_than_it_was_launched_on_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operator's channel for the case where nobody thought to look.

    The read surfaces answer this on demand, but a session quietly answering on
    the wrong model costs money and quality with nothing on screen to say so. It
    is checked in `_publish_update` rather than at each of the five sites that
    assign `record.model`, so a new measurement source cannot arrive without it.
    """
    caplog.set_level(logging.WARNING, logger="swe_mux.observation")
    session = _model_session("opus", "claude-sonnet-4-5")
    observation._publish_update(session)
    assert "session_model_divergent" in caplog.text
    assert "requested=opus" in caplog.text and "observed=claude-sonnet-4-5" in caplog.text


def test_the_divergence_is_logged_once_per_model_rather_than_per_update(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Observation updates arrive every few seconds; a model switch is a new fact.

    Once per session would miss a mid-conversation switch, and once per update
    would write the same line hundreds of times for one wrong model.
    """
    caplog.set_level(logging.WARNING, logger="swe_mux.observation")
    session = _model_session("opus", "claude-sonnet-4-5")
    for _ in range(3):
        observation._publish_update(session)
    assert caplog.text.count("session_model_divergent") == 1
    session.record.model = "claude-haiku-4-5"
    observation._publish_update(session)
    assert caplog.text.count("session_model_divergent") == 2


def test_a_session_that_is_running_what_it_asked_for_says_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silence and agreement have to be silent, or the line stops being read.

    Three quiet cases: the model matched, the harness has not reported one yet,
    and the launch named a mode (`opusplan`) that no observed id can confirm.
    """
    caplog.set_level(logging.WARNING, logger="swe_mux.observation")
    for session in (
        _model_session("opus", "claude-opus-5", backend="claude"),
        _model_session("opus", "", backend="claude"),
        _model_session("opusplan", "claude-sonnet-5", backend="claude"),
    ):
        observation._publish_update(session)
    assert "session_model_divergent" not in caplog.text
