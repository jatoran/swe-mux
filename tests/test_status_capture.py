"""The capture → golden-fixture pipeline scrubs evidence and replays deterministically."""

from __future__ import annotations

import json

from tests.support.detection_replay import DetectionReplay
from tests.support.status_capture import (
    SCRUBBED,
    build_status_fixture,
    fill_expected,
    scrub_state_log,
)

SECRET_PROMPT = "please read my api key sk-SECRET-12345 from .env"
SECRET_OUTPUT = "\x1b[31mAPI_KEY=sk-SECRET-12345\x1b[0m\r\n"


def _claude_capture() -> list[dict[str, object]]:
    return [
        {
            "type": "user",
            "uuid": "real-uuid-1",
            "timestamp": "2026-07-24T10:00:00Z",
            "cwd": "C:/Users/someone/secret-project",
            "isSidechain": False,
            "message": {"content": SECRET_PROMPT},
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "content": [
                    {"type": "text", "text": "Reading the env file now."},
                    {
                        "type": "tool_use",
                        "id": "toolu_native_abc123",
                        "name": "Read",
                        "input": {"file_path": "C:/Users/someone/secret-project/.env"},
                    },
                ],
                "stop_reason": "tool_use",
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 1000, "output_tokens": 50},
            },
        },
        {
            "type": "user",
            "isSidechain": False,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_native_abc123",
                        "content": SECRET_OUTPUT,
                        "is_error": False,
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "content": [{"type": "text", "text": "The key is sk-SECRET-12345."}],
                "stop_reason": "end_turn",
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 1200, "output_tokens": 30},
            },
        },
        {"type": "system", "subtype": "turn_duration", "durationMs": 4200},
    ]


async def test_capture_scrubs_bodies_ids_and_terminal_bytes() -> None:
    manifest = build_status_fixture(
        "claude",
        "captured stuck session",
        _claude_capture(),
        hook_events=[("UserPromptSubmit", {"prompt": SECRET_PROMPT})],
        state_log={
            "transitions": [
                {
                    "kind": "transition",
                    "previous": "idle",
                    "state": "working",
                    "source": "hook",
                    "proof": "proven",
                    "evidence": "hook:UserPromptSubmit",
                    "detail": None,
                    "ts": 1_800_000_000.0,
                },
                {"kind": "watchdog_recovery", "action": "pty_idle_prompt"},
            ]
        },
        edge_case="captured_example",
    )
    serialized = json.dumps(manifest)
    assert "sk-SECRET-12345" not in serialized
    assert "secret-project" not in serialized
    assert "toolu_native_abc123" not in serialized
    assert "real-uuid-1" not in serialized
    assert "\\u001b" not in serialized and "\x1b" not in serialized
    assert SCRUBBED in serialized
    # Structural signal survives: the observer sees the same turn shape.
    kinds = [step["kind"] for step in manifest["steps"]]
    assert kinds == ["hook", "transcript", "transcript", "transcript", "transcript", "transcript"]
    assert manifest["captured_transitions"] == [
        {
            "previous": "idle",
            "state": "working",
            "source": "hook",
            "proof": "proven",
            "evidence": "hook:UserPromptSubmit",
        }
    ]


async def test_captured_fixture_replays_deterministically_with_expected() -> None:
    manifest = build_status_fixture("claude", "captured", _claude_capture())
    await fill_expected(manifest)
    assert manifest["expected"]["states"] == [
        {"previous": "idle", "state": "working", "source": "transcript", "proof": "proven"},
        {"previous": "working", "state": "idle", "source": "transcript", "proof": "proven"},
    ]
    assert manifest["expected"]["parser"]["status"] == "ready"
    # Token telemetry survives scrubbing (numbers are not sensitive).
    replay = DetectionReplay("claude")
    result = await replay.run(manifest)
    assert result["states"] == manifest["expected"]["states"]
    assert result["events"] == manifest["expected"]["events"]
    assert replay.session.record.tokens_in == 1200
    assert replay.session.record.model == "claude-opus-4-8"


async def test_capture_preserves_interrupt_and_local_command_semantics() -> None:
    records = [
        {"type": "user", "isSidechain": False, "message": {"content": SECRET_PROMPT}},
        {
            "type": "user",
            "isSidechain": False,
            "message": {"content": "[Request interrupted by user for tool use] and secrets"},
        },
        {
            "type": "user",
            "isSidechain": False,
            "message": {"content": "<command-name>/model secret-args</command-name>"},
        },
    ]
    manifest = build_status_fixture("claude", "interrupt capture", records)
    await fill_expected(manifest)
    serialized = json.dumps(manifest)
    assert "secrets" not in serialized
    assert "secret-args" not in serialized
    # The interrupt marker aborts the turn even after scrubbing.
    assert manifest["expected"]["states"] == [
        {"previous": "idle", "state": "working", "source": "transcript", "proof": "proven"},
        {"previous": "working", "state": "idle", "source": "transcript", "proof": "proven"},
    ]
    assert any(item["type"] == "turn_aborted" for item in manifest["expected"]["events"])


async def test_codex_capture_scrubs_payloads() -> None:
    records = [
        {
            "type": "session_meta",
            "payload": {"id": "native-real", "cwd": "D:/secret", "model": "gpt-5.2-codex"},
        },
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "call_id": "call_native_9",
                "arguments": json.dumps({"command": ["cat", ".env"]}),
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_native_9",
                "exit_code": 0,
                "output": SECRET_OUTPUT,
            },
        },
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-real"}},
    ]
    manifest = build_status_fixture("codex", "codex capture", records)
    await fill_expected(manifest)
    serialized = json.dumps(manifest)
    assert "native-real" not in serialized
    assert "call_native_9" not in serialized
    assert ".env" not in serialized
    assert "sk-SECRET" not in serialized and "\x1b" not in serialized
    assert manifest["expected"]["states"][-1]["state"] == "idle"
    assert manifest["expected"]["states"][-1]["proof"] == "proven"


def test_scrub_state_log_drops_non_transitions() -> None:
    shaped = scrub_state_log({"transitions": [{"kind": "observer_fault", "error": "boom"}]})
    assert shaped == []
