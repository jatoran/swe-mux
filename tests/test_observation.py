from __future__ import annotations

import asyncio
import json
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
    apply_hook_observation,
    classify_transcript_event,
    observe_transcript,
)
from swe_mux.session import Session
from tests.support.detection_replay import ReplaySession


def record(backend: str) -> SessionRecord:
    return SessionRecord(
        "mux-id", "builder-one", "default", backend, "native-id", ".", f"{backend}.exe", []
    )


def drain(queue: asyncio.Queue[Any]) -> list[Any]:
    emitted = []
    while not queue.empty():
        emitted.append(queue.get_nowait())
    return emitted


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
    session.transition = lambda state, detail, source: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, source=source
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
    session.transition = lambda state, detail, source: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, source=source
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
    session = cast(Any, SimpleNamespace(record=record("codex")))
    events = EventBus()
    queue = events.subscribe()

    await _codex(session, {"type": "event_msg", "payload": {"type": "task_started"}}, events)
    await _codex(session, {"type": "event_msg", "payload": {"type": "user_message"}}, events)

    emitted = [await queue.get() for _ in range(queue.qsize())]
    assert [item.type for item in emitted].count("turn_started") == 1


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
    session.transition = lambda state, detail, source: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, source=source
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
    session.transition = lambda state, detail, source: Session.transition(  # type: ignore[attr-defined]
        session, state, detail, source=source
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
