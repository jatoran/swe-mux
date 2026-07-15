from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.event_bus import EventBus
from swe_mux.meta_hooks import HookRule, MetaHookEngine
from swe_mux.models import SessionRecord
from swe_mux.observation import _claude, _codex


def record(backend: str) -> SessionRecord:
    return SessionRecord(
        "mux-id", "builder-one", "default", backend, "native-id", ".", f"{backend}.exe", []
    )


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
