"""The readable conversation view behind the drawer's Transcript tab.

The harnesses write their own machinery into transcripts as ``user`` records, so
these tests are mostly about what must NOT reach a reader: skill bodies, slash
command expansions, shell escapes, interrupt markers, environment blocks. The
field shapes asserted here were taken from real transcripts (Claude 2.1.220,
current Codex rollouts, and OMP session version 3); when a CLI changes them,
this is the file that should fail rather than the drawer quietly emptying.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swe_mux import transcript_view as tv
from swe_mux.transcript_view import (
    conversation_view,
    conversation_view_cached,
    final_reply_text,
)


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return path


def claude_user(text: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-08-02T10:00:00Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        **fields,
    }


def claude_assistant(text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-08-02T10:00:01Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


HUMAN = {"origin": {"kind": "human"}, "promptSource": "typed"}


def test_claude_keeps_human_turns_and_drops_cli_machinery(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user("ship the drawer tab", **HUMAN),
            # A skill body: injected, and stamped `isMeta` for it.
            claude_user("Base directory for this skill: C:/skills/learn", isMeta=True),
            # A slash command expansion and its output, which predate `origin`.
            claude_user("<command-name>/model</command-name>"),
            claude_user("<local-command-stdout>Set model to Fable 5</local-command-stdout>"),
            # The `!` shell escape.
            claude_user("<bash-input>git add -A</bash-input>"),
            claude_user("<bash-stdout>120 files changed</bash-stdout>"),
            # An abandoned turn.
            claude_user("[Request interrupted by user]", interruptedMessageId="msg_1"),
            # A harness injection that *does* carry promptSource, so only the origin
            # kind tells it from a typed prompt.
            claude_user(
                "<task-notification><task-id>x</task-id></task-notification>",
                origin={"kind": "task-notification"},
                promptSource="system",
            ),
            claude_assistant("shipped"),
        ],
    )
    view = conversation_view(path, "claude")
    assert [(item["role"], item["text"]) for item in view["messages"]] == [
        ("user", "ship the drawer tab"),
        ("assistant", "shipped"),
    ]
    assert view["hidden"] == 7
    assert view["truncated"] is False


def test_claude_strips_system_reminders_without_losing_the_prompt(tmp_path: Path) -> None:
    """The reminder rides along on a real prompt, so it is stripped, not hidden."""
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user(
                "fix the parser\n<system-reminder>Contents of CLAUDE.md…</system-reminder>",
                **HUMAN,
            ),
            claude_user("<system-reminder>background context</system-reminder>", **HUMAN),
        ],
    )
    view = conversation_view(path, "claude")
    assert [item["text"] for item in view["messages"]] == ["fix the parser"]
    # The reminder-only record survives classification and is dropped for being
    # empty once stripped, which still counts as something withheld.
    assert view["hidden"] == 1


def test_claude_fails_open_when_a_record_carries_no_provenance(tmp_path: Path) -> None:
    """An older CLI writes no `origin`; its prompts must still be shown.

    Hiding a message the user typed is the one failure this view must not have,
    so an unrecognised shape is shown rather than withheld.
    """
    path = write_jsonl(tmp_path / "claude.jsonl", [claude_user("what changed in the layout?")])
    view = conversation_view(path, "claude")
    assert [item["text"] for item in view["messages"]] == ["what changed in the layout?"]
    assert view["hidden"] == 0


def claude_tool_use(count: int = 1) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-08-02T10:00:02Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Read", "input": {}}] * count,
        },
    }


def test_a_tool_call_ends_a_message_and_the_boundary_is_counted(tmp_path: Path) -> None:
    """Copying a reply must copy the reply, not the narration before a tool call."""
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user("audit the drawer", **HUMAN),
            claude_assistant("Let me read the registry."),
            claude_tool_use(3),
            claude_assistant("Nine tabs, all registered."),
            claude_user("thanks", **HUMAN),
        ],
    )
    view = conversation_view(path, "claude")
    assert [(item["role"], item["text"]) for item in view["messages"]] == [
        ("user", "audit the drawer"),
        ("assistant", "Let me read the registry."),
        ("assistant", "Nine tabs, all registered."),
        ("user", "thanks"),
    ]
    # The count is what tells a reader the two agent messages are not one thought.
    assert [item["preceding_tool_calls"] for item in view["messages"]] == [0, 0, 3, 0]
    assert final_reply_text(path, "claude") == "Nine tabs, all registered."


def test_a_streaming_split_with_no_tool_between_is_still_one_message(tmp_path: Path) -> None:
    """The other reason a reply arrives in pieces. Half a sentence is not a message."""
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user("explain the cache", **HUMAN),
            claude_assistant("It is keyed on mtime and size."),
            claude_assistant("Both, because Windows mtime is a timer tick."),
        ],
    )
    view = conversation_view(path, "claude")
    assert [item["text"] for item in view["messages"]] == [
        "explain the cache",
        "It is keyed on mtime and size.\n\nBoth, because Windows mtime is a timer tick.",
    ]
    assert final_reply_text(path, "claude").startswith("It is keyed on")


def test_a_reply_that_resumes_after_a_tool_is_copied_whole(tmp_path: Path) -> None:
    """Only the segment is cut at the boundary; streaming inside it still merges."""
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user("check it", **HUMAN),
            claude_assistant("Looking."),
            claude_tool_use(),
            claude_assistant("## Verdict"),
            claude_assistant("It holds."),
        ],
    )
    assert final_reply_text(path, "claude") == "## Verdict\n\nIt holds."


def test_a_provider_control_acknowledgement_is_not_the_reply(tmp_path: Path) -> None:
    """A synthetic ack appended after the real turn is machinery, not an answer."""
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user("finish it", **HUMAN),
            claude_assistant("Implemented it and all checks pass."),
            claude_assistant("No response requested."),
        ],
    )
    view = conversation_view(path, "claude")
    assert [item["text"] for item in view["messages"]] == [
        "finish it",
        "Implemented it and all checks pass.",
    ]
    assert view["hidden"] == 1
    assert final_reply_text(path, "claude") == "Implemented it and all checks pass."


def test_a_conversation_with_no_agent_reply_has_no_final_reply(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "claude.jsonl", [claude_user("are you there?", **HUMAN)])
    assert final_reply_text(path, "claude") == ""


def test_claude_sidechain_records_are_not_the_conversation(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            claude_user("go", **HUMAN),
            {**claude_assistant("subagent chatter"), "isSidechain": True},
            claude_assistant("done"),
        ],
    )
    view = conversation_view(path, "claude")
    assert [item["text"] for item in view["messages"]] == ["go", "done"]


def codex_message(role: str, text: str) -> dict[str, Any]:
    return {
        "type": "response_item",
        "timestamp": "2026-08-02T10:00:00Z",
        "payload": {"type": "message", "role": role, "content": [{"type": "text", "text": text}]},
    }


def omp_message(role: str, content: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    return {
        "type": "message",
        "id": f"omp-{role}",
        "parentId": None,
        "timestamp": "2026-08-06T23:52:17.407Z",
        "message": {"role": role, "content": content, **fields},
    }


def test_omp_keeps_conversation_text_and_drops_protocol_messages_and_tools(
    tmp_path: Path,
) -> None:
    path = write_jsonl(
        tmp_path / "omp.jsonl",
        [
            {"type": "session", "version": 3, "id": "native-omp"},
            omp_message(
                "user",
                [{"type": "text", "text": "verify the harness"}],
                attribution="user",
            ),
            omp_message(
                "developer",
                [{"type": "text", "text": "internal extension context"}],
            ),
            omp_message(
                "assistant",
                [
                    {"type": "text", "text": "Checking."},
                    {"type": "toolCall", "id": "call-1", "name": "read"},
                ],
                stopReason="toolUse",
            ),
            omp_message(
                "toolResult",
                [{"type": "text", "text": "private tool output"}],
                toolCallId="call-1",
            ),
            omp_message(
                "assistant",
                [{"type": "text", "text": "Verified."}],
                stopReason="stop",
            ),
        ],
    )
    view = conversation_view(path, "omp")
    # OMP puts the narration and the call it introduces in ONE record, so the
    # boundary is inside a kept message rather than between two of them.
    assert [(item["role"], item["text"]) for item in view["messages"]] == [
        ("user", "verify the harness"),
        ("assistant", "Checking."),
        ("assistant", "Verified."),
    ]
    assert [item["preceding_tool_calls"] for item in view["messages"]] == [0, 0, 1]
    assert view["hidden"] == 0
    assert final_reply_text(path, "omp") == "Verified."


def test_codex_drops_tool_calls_and_harness_blocks_but_keeps_its_instructions(
    tmp_path: Path,
) -> None:
    """The opening AGENTS.md block stays: it is the brief the run was given."""
    path = write_jsonl(
        tmp_path / "rollout.jsonl",
        [
            codex_message("user", "<environment_context><cwd>D:/x</cwd></environment_context>"),
            codex_message("user", "# AGENTS.md instructions for D:/x\n<INSTRUCTIONS>rules"),
            codex_message("user", "$learn"),
            codex_message("user", "<skill> <name>learn</name> <path>C:/skills</path>"),
            {
                "type": "response_item",
                "timestamp": "2026-08-02T10:00:03Z",
                "payload": {"type": "function_call", "name": "shell", "arguments": "{}"},
            },
            codex_message("assistant", "reading the docs"),
        ],
    )
    view = conversation_view(path, "codex")
    assert [(item["role"], item["text"]) for item in view["messages"]] == [
        ("user", "# AGENTS.md instructions for D:/x\n<INSTRUCTIONS>rules"),
        ("user", "$learn"),
        ("assistant", "reading the docs"),
    ]
    assert view["hidden"] == 2
    # Codex names the call in the payload type rather than a content block.
    assert [item["preceding_tool_calls"] for item in view["messages"]] == [0, 0, 1]


def test_codex_legacy_event_copies_do_not_double_the_conversation(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "rollout.jsonl",
        [
            codex_message("user", "start"),
            {
                "type": "event_msg",
                "timestamp": "2026-08-02T10:00:00Z",
                "payload": {"type": "user_message", "message": "start"},
            },
            codex_message("assistant", "started"),
            {
                "type": "event_msg",
                "timestamp": "2026-08-02T10:00:01Z",
                "payload": {"type": "agent_message", "message": "started"},
            },
        ],
    )
    view = conversation_view(path, "codex")
    assert [item["text"] for item in view["messages"]] == ["start", "started"]


def test_only_the_newest_messages_are_returned_and_the_cut_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [claude_user(f"turn {index}", **HUMAN) for index in range(6)],
    )
    view = conversation_view(path, "claude", limit=2)
    assert [item["text"] for item in view["messages"]] == ["turn 4", "turn 5"]
    assert view["truncated"] is True
    # Ordinals number the returned window, so they are display keys and not the
    # position of a message in the conversation.
    assert [item["ordinal"] for item in view["messages"]] == [0, 1]

    previous_last = view["messages"][-1]["message_id"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(claude_user("turn 6", **HUMAN)) + "\n")
    moved = conversation_view(path, "claude", limit=2)
    assert [item["text"] for item in moved["messages"]] == ["turn 5", "turn 6"]
    assert moved["messages"][0]["ordinal"] == 0
    assert moved["messages"][0]["message_id"] == previous_last
    assert len({item["message_id"] for item in moved["messages"]}) == 2


def test_an_unchanged_transcript_is_parsed_once(tmp_path: Path, monkeypatch: Any) -> None:
    path = write_jsonl(tmp_path / "claude.jsonl", [claude_user("hello", **HUMAN)])
    calls = 0
    real = tv.conversation_view

    def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tv, "conversation_view", counting)
    first = conversation_view_cached(path, "claude")
    second = conversation_view_cached(path, "claude")
    assert calls == 1
    assert first is second
    # An append is a different (mtime, size), so the next read reparses.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(claude_assistant("hi")) + "\n")
    third = conversation_view_cached(path, "claude")
    assert calls == 2
    assert [item["text"] for item in third["messages"]] == ["hello", "hi"]
