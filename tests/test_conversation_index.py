"""The shared incremental conversation reader (`conversation_index`).

Every human-facing reader of a conversation - the Transcript tab, the branch picker,
read-aloud, Copy reply - now reads one index per conversation instead of decoding the
whole file per request. The index is only worth having if it says exactly what a full
read says, so the first group of tests compares the two on the shapes the projections
care about. The rest pin what makes it cheap and safe: only appended bytes are decoded,
a rewritten file is noticed and rebuilt, concurrent readers share one decode, the
whole fleet stays inside a memory budget, and a client can be told what changed.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from swe_mux import conversation_index
from swe_mux import transcript_view as tv
from swe_mux.conversation_index import (
    ConversationIndex,
    ConversationIndexRegistry,
    IndexBuilding,
    calls_at,
    codex_line_is_skippable,
    indexed_parse,
    parsed_tool_calls,
    public_message,
)

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
HUMAN = {"origin": {"kind": "human"}, "promptSource": "typed"}


def line(event: dict[str, Any]) -> str:
    # Compact, the way Codex and Claude write: the pre-decode sniff keys on it.
    return json.dumps(event, separators=(",", ":")) + "\n"


def write(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_bytes("".join(line(event) for event in events).encode())
    return path


def append(path: Path, *events: dict[str, Any], raw: str = "") -> None:
    with path.open("ab") as handle:
        handle.write(("".join(line(event) for event in events) + raw).encode())


def counters(registry: ConversationIndexRegistry) -> dict[str, Any]:
    return registry.stats.snapshot()


# --------------------------------------------------------------------------
# Fixtures in each dialect


def user(uuid: str, parent: str | None, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:23Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        **HUMAN,
    }


def assistant(uuid: str, parent: str | None, text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:30Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def tool_call(uuid: str, parent: str | None, call_id: str, command: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:31Z",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"running {command}"},
                {"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": command}},
            ],
        },
    }


def tool_result(uuid: str, parent: str | None, call_id: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:32Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "x" * 400}],
        },
    }


def claude_conversation() -> list[dict[str, Any]]:
    """Machinery, a retried prompt, a tool batch, a sidechain and streamed fragments."""
    return [
        {"type": "summary", "summary": "not a message"},
        user("root", None, "set up the review"),
        {
            "type": "user",
            "uuid": "meta",
            "parentUuid": "root",
            "isMeta": True,
            "timestamp": "2026-08-18T16:31:24Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "skill body"}]},
        },
        user("try0", "meta", "review the session"),
        user("live", "meta", "review the session <system-reminder>hidden</system-reminder>"),
        tool_call("call_a", "live", "id_a", "ls"),
        tool_call("call_b", "call_a", "id_b", "pwd"),
        tool_result("res_a", "call_a", "id_a"),
        tool_result("res_b", "call_b", "id_b"),
        {**assistant("side", "res_b", "sidechain words"), "isSidechain": True},
        assistant("reply1", "res_b", "first half"),
        assistant("reply2", "reply1", "second half"),
        tool_call("call_c", "reply2", "id_c", "git status"),
    ]


def codex_event(kind: str, **payload: Any) -> dict[str, Any]:
    # Key order as Codex writes it, which is what the pre-decode sniff anchors on.
    return {
        "timestamp": "2026-08-18T16:31:00Z",
        "type": "event_msg",
        "payload": {"type": kind, **payload},
    }


def codex_message(role: str, text: str, phase: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "message",
        "role": role,
        "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
    }
    if phase is not None:
        payload["phase"] = phase
    return {"timestamp": "2026-08-18T16:31:01Z", "type": "response_item", "payload": payload}


def codex_noise() -> list[dict[str, Any]]:
    return [
        {
            "type": "response_item",
            "payload": {"type": "reasoning", "summary": [], "encrypted_content": "z" * 300},
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "c", "output": "o" * 300},
        },
        codex_event("token_count", info={"total": 1}),
    ]


def codex_turn(name: str, *, complete: bool = True) -> list[dict[str, Any]]:
    events = [
        codex_event("task_started", turn_id=name),
        codex_message("user", f"question {name}"),
        codex_event("user_message", message=f"question {name}"),
        *codex_noise(),
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "call_id": f"call-{name}",
                "arguments": json.dumps({"command": ["ls", name]}),
            },
        },
        {
            # Both ids and both input keys: the reader's details and the indexing
            # parse prefer different ones, and both must read the input back right.
            "timestamp": "2026-08-18T16:31:02Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "id": f"ctc-{name}",
                "call_id": f"custom-{name}",
                "name": "apply_patch",
                "input": f"*** Begin Patch {name}",
                "arguments": json.dumps({"patch": name}),
            },
        },
        codex_message("assistant", f"progress {name}", "commentary"),
        codex_message("assistant", f"answer {name}", "final_answer"),
        codex_event("agent_message", message=f"answer {name}"),
    ]
    if complete:
        events.append(codex_event("task_complete", turn_id=name))
    return events


def codex_meta(native: str, **kwargs: Any) -> dict[str, Any]:
    return {"type": "session_meta", "payload": {"id": native, "source": "cli", **kwargs}}


def pi_conversation() -> list[dict[str, Any]]:
    def message(role: str, content: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": "message",
            "timestamp": "2026-08-18T16:31:00Z",
            "message": {"role": role, "content": content},
        }

    return [
        {"type": "session", "id": "pi-1"},
        message("user", [{"type": "text", "text": "hello pi"}]),
        message(
            "assistant",
            [
                {"type": "text", "text": "looking"},
                {
                    "type": "toolCall",
                    "id": "pi-call",
                    "name": "read",
                    "arguments": {"path": "a.py"},
                },
            ],
        ),
        message("toolResult", [{"type": "text", "text": "file body"}]),
        message("assistant", [{"type": "text", "text": "done"}]),
    ]


# --------------------------------------------------------------------------
# A full read and the index say the same thing


def reference(path: Path, backend: str) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """The projections over a full decode of the file, as the readers used to run them."""
    events = tv.conversation_events(path, backend)
    records, hidden, trailing = tv._conversation_records(events, backend)
    return tv._merge_assistant_segments(records), hidden, trailing


def comparable(message: dict[str, Any]) -> dict[str, Any]:
    shown = public_message(message, 0)
    shown.pop("ordinal")
    return shown


CONVERSATIONS = [
    ("claude", claude_conversation()),
    (
        "codex",
        [
            codex_meta(PARENT),
            *codex_turn("A"),
            *codex_turn("B"),
            codex_event("thread_rolled_back", num_turns=1),
            *codex_turn("C", complete=False),
        ],
    ),
    ("omp", pi_conversation()),
]


@pytest.mark.parametrize(("backend", "events"), CONVERSATIONS)
def test_the_index_reads_what_a_full_read_reads(
    tmp_path: Path, backend: str, events: list[dict[str, Any]]
) -> None:
    path = write(tmp_path / "conversation.jsonl", events)
    expected, hidden, trailing = reference(path, backend)

    reading = ConversationIndexRegistry().snapshot(path, backend)

    assert [comparable(message) for message in reading.messages] == [
        comparable(message) for message in expected
    ]
    assert reading.hidden == hidden
    assert [conversation_index.public_tool(tool) for tool in reading.trailing_tools] == [
        conversation_index.public_tool(tool) for tool in trailing
    ]
    # Inputs are not carried, but every one of them can be read back exactly.
    for message in expected:
        if message["preceding_tools"]:
            assert calls_at(reading, message["message_id"]) == [
                {"id": tool["id"], "name": tool["name"], "input": tool["input"]}
                for tool in message["preceding_tools"]
            ]
    assert calls_at(reading, None) == [
        {"id": tool["id"], "name": tool["name"], "input": tool["input"]} for tool in trailing
    ]
    assert expected, "the fixture must exercise something"


@pytest.mark.parametrize(("backend", "events"), CONVERSATIONS)
@pytest.mark.parametrize("shared", [True, False])
def test_the_indexed_parse_is_the_full_parse_exactly(
    tmp_path: Path, backend: str, events: list[dict[str, Any]], shared: bool
) -> None:
    """History reads its conversations here (`history.read_history_conversation`).

    Its index and its tool-call pages are keyed on the full parse's output, inputs
    included, so the two must be indistinguishable - not merely close.
    """
    path = write(tmp_path / "conversation.jsonl", events)
    full = tv.parse_transcript_with_watermark(path, backend)

    indexed = indexed_parse(path, backend, shared=shared)

    assert indexed == full
    with_tools = [
        ordinal
        for ordinal, message in enumerate(full.messages)
        if any(block.get("type") == "tool_use" for block in message["content"])
    ]
    assert with_tools
    assert parsed_tool_calls(path, backend, ordinals=with_tools) == {
        ordinal: [
            {"id": block["id"], "name": block["name"], "input": block["input"]}
            for block in full.messages[ordinal]["content"]
            if block.get("type") == "tool_use"
        ]
        for ordinal in with_tools
    }


def test_a_sweep_reads_without_evicting_or_pinning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The History indexer walks thousands of conversations through `shared=False`."""
    registry = ConversationIndexRegistry(max_indexes=1)
    monkeypatch.setattr(conversation_index, "REGISTRY", registry)
    live = write(tmp_path / "live.jsonl", claude_conversation())
    swept = write(tmp_path / "swept.jsonl", claude_conversation())
    reading = registry.snapshot(live, "claude")

    indexed_parse(swept, "claude", shared=False)
    # The live reader's index is still the one held, untouched by the sweep...
    assert registry.peek(live, "claude", None) is not None
    assert registry.peek(swept, "claude", None) is None
    assert registry.status()["indexes"] == 1
    assert counters(registry)["sweep_builds"] == 1
    # ...and a sweep of a conversation a reader holds reuses that index.
    before = registry.peek(live, "claude", None)
    indexed_parse(live, "claude", shared=False)
    assert registry.peek(live, "claude", None) is before
    assert counters(registry)["sweep_builds"] == 1
    assert registry.snapshot(live, "claude") is reading


def test_a_kept_parse_is_reused_until_the_conversation_changes(tmp_path: Path) -> None:
    path = write(tmp_path / "claude.jsonl", claude_conversation())
    registry = ConversationIndexRegistry()
    index = registry.index_for(path, "claude", None)
    index.refresh()
    before = index.estimated_bytes
    first, _ = index.parsed_with_inputs(keep=True)
    kept = index.estimated_bytes
    assert kept > before  # what it pins is counted against the budget
    second, _ = index.parsed_with_inputs(keep=True)
    assert second is first
    # A sweep's ask neither returns nor replaces the kept one.
    swept, _ = index.parsed_with_inputs(keep=False)
    assert swept is not first and swept == first
    assert index.estimated_bytes == kept
    append(path, user("later", "call_c", "and then"))
    index.refresh()
    # The change drops the kept parse and its bytes; one short record is far smaller.
    assert index.estimated_bytes < kept
    third, _ = index.parsed_with_inputs(keep=True)
    assert third is not first
    assert third[-1]["content"][0]["text"] == "and then"


def test_a_codex_fork_reads_its_parent_prefix_with_stable_identity(tmp_path: Path) -> None:
    parent = write(tmp_path / f"rollout-{PARENT}.jsonl", [codex_meta(PARENT), *codex_turn("A")])
    boundary = parent.stat().st_size
    child = write(
        tmp_path / f"rollout-{CHILD}.jsonl",
        [
            codex_meta(CHILD, history_base={"thread_id": PARENT, "end_byte_offset": boundary}),
            *codex_turn("B"),
        ],
    )
    registry = ConversationIndexRegistry()
    first = registry.snapshot(child, "codex")
    expected, _hidden, _trailing = reference(child, "codex")
    assert [comparable(m) for m in first.messages] == [comparable(m) for m in expected]
    assert [m["text"] for m in first.messages][:3] == ["question A", "progress A", "answer A"]
    assert all(m["message_id"].startswith("inherited:") for m in first.messages[:3])
    # Inputs read back from the parent's file, through the fork.
    assert calls_at(first, first.messages[1]["message_id"]) == [
        {"id": "call-A", "name": "shell", "input": {"command": ["ls", "A"]}},
        {"id": "ctc-A", "name": "apply_patch", "input": "*** Begin Patch A"},
    ]

    # The parent growing past the pinned boundary changes nothing the fork reads.
    append(parent, *codex_turn("late"))
    assert registry.snapshot(child, "codex").token == first.token

    # The pinned prefix itself changing is a different conversation: rebuild.
    parent.write_bytes(parent.read_bytes().replace(b"question A", b"question Z"))
    rebuilt = registry.snapshot(child, "codex")
    assert rebuilt.token != first.token
    assert rebuilt.messages[0]["text"] == "question Z"


# --------------------------------------------------------------------------
# Decoding only what was appended


def test_codex_records_no_projection_reads_are_skipped_without_decoding() -> None:
    assert codex_line_is_skippable(
        line({"type": "response_item", "payload": {"type": "reasoning"}}).encode()
    )
    assert codex_line_is_skippable(
        line({"type": "event_msg", "payload": {"type": "token_count"}}).encode()
    )
    for kept in (
        "message",
        "function_call",
        "custom_tool_call",
        "user_message",
        "agent_message",
        "task_started",
        "task_complete",
        "thread_rolled_back",
    ):
        assert not codex_line_is_skippable(
            line({"type": "response_item", "payload": {"type": kept}}).encode()
        ), kept
    # The 2026-10 envelope puts an ordinal between the timestamp and the kind, and
    # scalar members in either place are stepped over.
    assert codex_line_is_skippable(
        b'{"type":"response_item","timestamp":"t","payload":{"type":"reasoning"}}\n'
    )
    assert codex_line_is_skippable(
        b'{"timestamp":"2026-10-12T05:40:29.917Z","ordinal":7,"type":"response_item",'
        b'"payload":{"type":"reasoning","summary":[]}}\n'
    )
    assert not codex_line_is_skippable(
        b'{"timestamp":"2026-10-12T05:40:29.917Z","ordinal":8,"type":"response_item",'
        b'"payload":{"type":"message","role":"user"}}\n'
    )
    # A record kind no projection reads skips whatever its payload says.
    for kind in ("token_usage_record", "turn_context", "world_state", "compacted", "session_meta"):
        assert codex_line_is_skippable(
            line({"timestamp": "2026-10-07T11:50:17Z", "type": kind, "payload": {}}).encode()
        ), kind
    assert codex_line_is_skippable(
        line(
            {"timestamp": "t", "type": "event_msg", "payload": {"type": "exec_command_end"}}
        ).encode()
    )
    # Anything the sniff cannot read with certainty is decoded: the safe direction.
    assert not codex_line_is_skippable(
        b'{"type":"response_item","payload": {"type": "reasoning"}}\n'
    )
    assert not codex_line_is_skippable(
        b'{"type":"response_item","payload":{"id":"x","type":"reasoning"}}\n'
    )
    # A non-scalar member before the payload is not stepped over, so it is decoded.
    assert not codex_line_is_skippable(
        b'{"type":"response_item","meta":{"a":1},"payload":{"type":"reasoning"}}\n'
    )
    assert not codex_line_is_skippable(b' {"type":"session_meta"}\n')
    # Anchored at the first byte: a nested "payload" is never mistaken for the record's.
    assert not codex_line_is_skippable(
        b'{"timestamp":"t","type":"event_msg","meta":{"payload":{"type":"reasoning"}},'
        b'"payload":{"type":"user_message"}}\n'
    )
    assert not codex_line_is_skippable(b" " * 600 + b'"payload":{"type":"reasoning"}\n')


def test_an_append_decodes_only_the_appended_lines(tmp_path: Path) -> None:
    path = write(tmp_path / "codex.jsonl", [codex_meta(PARENT), *codex_turn("A")])
    registry = ConversationIndexRegistry()
    registry.snapshot(path, "codex")
    built = counters(registry)
    # Session metadata, reasoning, tool output and a token count are never decoded.
    assert built["lines_sniffed"] == 4
    assert built["lines_decoded"] == len(codex_turn("A")) - 3

    unchanged = registry.snapshot(path, "codex")
    assert counters(registry)["lines_decoded"] == built["lines_decoded"]
    assert counters(registry)["unchanged"] == 1

    append(path, *codex_turn("B"))
    grown = registry.snapshot(path, "codex")
    after = counters(registry)
    assert after["lines_decoded"] - built["lines_decoded"] == len(codex_turn("B")) - 3
    assert after["lines_sniffed"] - built["lines_sniffed"] == 3
    assert after.get("rebuilds", 0) == 0
    assert after["extends"] == 1
    assert grown.token == unchanged.token
    assert grown.seq > unchanged.seq
    assert [m["text"] for m in grown.messages][-1] == "answer B"


def test_a_line_still_being_written_is_read_provisionally_and_committed_once(
    tmp_path: Path,
) -> None:
    path = write(tmp_path / "claude.jsonl", [user("root", None, "first")])
    registry = ConversationIndexRegistry()
    registry.snapshot(path, "claude")

    # A whole record without its newline yet: shown, but not committed.
    append(path, raw=line(assistant("reply", "root", "an answer")).rstrip("\n"))
    provisional = registry.snapshot(path, "claude")
    assert [m["text"] for m in provisional.messages] == ["first", "an answer"]

    # Half a record: nothing to show for it, and nothing broken either.
    append(path, raw="\n" + line(user("next", "reply", "second"))[:25])
    partial = registry.snapshot(path, "claude")
    assert [m["text"] for m in partial.messages] == ["first", "an answer"]

    append(path, raw=line(user("next", "reply", "second"))[25:])
    whole = registry.snapshot(path, "claude")
    assert [m["text"] for m in whole.messages] == ["first", "an answer", "second"]
    assert counters(registry).get("rebuilds", 0) == 0
    # The provisional record kept its identity when it was committed.
    assert whole.messages[1]["message_id"] == provisional.messages[1]["message_id"]


# --------------------------------------------------------------------------
# Noticing a replaced file


def test_a_truncated_file_is_rebuilt_under_a_new_generation(tmp_path: Path) -> None:
    path = write(
        tmp_path / "claude.jsonl", [user("root", None, "one"), assistant("a", "root", "two")]
    )
    registry = ConversationIndexRegistry()
    first = registry.snapshot(path, "claude")
    write(path, [user("root", None, "one")])
    second = registry.snapshot(path, "claude")
    assert second.token != first.token
    assert [m["text"] for m in second.messages] == ["one"]
    assert counters(registry)["rebuilds"] == 1


def test_a_same_size_rewrite_with_a_frozen_mtime_is_noticed(tmp_path: Path) -> None:
    """Claude's cancel/revert rewrites in place, and Windows freezes an open file's mtime."""
    path = write(
        tmp_path / "claude.jsonl", [user("root", None, "alpha"), assistant("a", "root", "beta")]
    )
    stamp = path.stat()
    registry = ConversationIndexRegistry()
    first = registry.snapshot(path, "claude")
    path.write_bytes(path.read_bytes().replace(b"alpha", b"ALPHA"))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    second = registry.snapshot(path, "claude")
    assert second.token != first.token
    assert second.messages[0]["text"] == "ALPHA"


def test_a_rewrite_past_the_prefix_sample_is_caught_at_the_boundary(tmp_path: Path) -> None:
    """The bytes just before the read position are re-checked, not only the first 4 KiB."""
    events = [user("root", None, "p" * 5000)] + [
        assistant(f"a{index}", f"a{index - 1}" if index else "root", f"reply {index}")
        for index in range(5)
    ]
    path = write(tmp_path / "claude.jsonl", events)
    registry = ConversationIndexRegistry()
    first = registry.snapshot(path, "claude")
    body = path.read_bytes()
    assert body.index(b"reply 4") > conversation_index.SAMPLE_BYTES
    path.write_bytes(body.replace(b"reply 4", b"REPLY 4"))
    append(path, user("more", "a4", "and more"))
    second = registry.snapshot(path, "claude")
    assert second.token != first.token
    assert "REPLY 4" in second.messages[1]["text"]


# --------------------------------------------------------------------------
# Telling a client what changed


def test_a_completed_codex_turn_restamps_the_messages_it_completed(tmp_path: Path) -> None:
    path = write(
        tmp_path / "codex.jsonl",
        [codex_meta(PARENT), *codex_turn("A"), *codex_turn("B", complete=False)],
    )
    registry = ConversationIndexRegistry()
    before = registry.snapshot(path, "codex")
    open_turn = [m for m in before.messages if m.get("turn_id") == "B"]
    assert open_turn and not any(m["turn_complete"] for m in open_turn)

    append(path, codex_event("task_complete", turn_id="B"))
    after = registry.snapshot(path, "codex")
    delta = after.delta(before.seq, before.messages[0]["message_id"])

    assert after.token == before.token
    assert delta is not None
    assert delta["replace_from"] == open_turn[0]["message_id"]
    assert [m["text"] for m in delta["messages"]] == [m["text"] for m in open_turn]
    assert all(m["turn_complete"] for m in delta["messages"])
    # Turn A did not change, so it is not resent.
    assert all(m.get("turn_id") == "B" for m in delta["messages"])


def test_a_claude_rewind_resends_from_the_first_message_it_abandoned(tmp_path: Path) -> None:
    path = write(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "start"),
            assistant("a1", "root", "first try"),
            user("u2", "a1", "go on"),
        ],
    )
    registry = ConversationIndexRegistry()
    before = registry.snapshot(path, "claude")
    assert not any(m.get("abandoned") for m in before.messages)

    # A new reply under `root` leaves "first try" and everything after it behind.
    append(path, assistant("a2", "root", "second try"))
    after = registry.snapshot(path, "claude")
    delta = after.delta(before.seq, before.messages[0]["message_id"])

    assert delta is not None
    assert delta["replace_from"] == before.messages[1]["message_id"]
    assert [(m["text"], bool(m.get("abandoned"))) for m in delta["messages"]] == [
        ("first try", True),
        ("go on", True),
        ("second try", False),
    ]


def test_a_delta_against_a_reading_it_never_reached_asks_for_a_reset(tmp_path: Path) -> None:
    path = write(tmp_path / "claude.jsonl", [user("root", None, "start")])
    reading = ConversationIndexRegistry().snapshot(path, "claude")
    assert reading.delta(reading.seq + 5, None) is None
    assert reading.delta(reading.seq, "offset:999") is None
    assert reading.page(10, "offset:999") is None
    unchanged = reading.delta(reading.seq, None)
    assert unchanged is not None
    assert unchanged["messages"] == []
    assert unchanged["replace_from"] is None


def test_a_new_index_never_reuses_a_generation(tmp_path: Path) -> None:
    """After an eviction or a daemon restart the counters start again; the token does not."""
    path = write(tmp_path / "claude.jsonl", [user("root", None, "start")])
    first = ConversationIndexRegistry().snapshot(path, "claude")
    second = ConversationIndexRegistry().snapshot(path, "claude")
    assert (first.generation, first.seq) == (second.generation, second.seq)
    assert first.token != second.token


def test_older_pages_walk_back_in_whole_conversation_ordinals(tmp_path: Path) -> None:
    events = [user("u0", None, "m0")]
    for index in range(1, 7):
        events.append(user(f"u{index}", f"u{index - 1}", f"m{index}"))
    path = write(tmp_path / "claude.jsonl", events)
    reading = ConversationIndexRegistry().snapshot(path, "claude")

    tail = reading.page(3)
    assert tail is not None
    assert [(m["ordinal"], m["text"]) for m in tail["messages"]] == [
        (4, "m4"),
        (5, "m5"),
        (6, "m6"),
    ]
    assert tail["has_older"] is True
    older = reading.page(3, tail["messages"][0]["message_id"])
    assert older is not None
    assert [m["ordinal"] for m in older["messages"]] == [1, 2, 3]
    oldest = reading.page(3, older["messages"][0]["message_id"])
    assert oldest is not None
    assert [m["text"] for m in oldest["messages"]] == ["m0"]
    assert oldest["has_older"] is False


# --------------------------------------------------------------------------
# Tool inputs read back on demand


def test_a_tool_input_is_not_read_from_a_record_that_moved(tmp_path: Path) -> None:
    """A reading outlived by a rewrite must answer "no input", never another call's."""
    path = write(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "go"),
            tool_call("c", "root", "id_a", "ls"),
            assistant("r", "c", "done"),
        ],
    )
    reading = ConversationIndexRegistry().snapshot(path, "claude")
    target = reading.messages[-1]["message_id"]
    assert calls_at(reading, target) == [{"id": "id_a", "name": "Bash", "input": {"command": "ls"}}]

    path.write_bytes(path.read_bytes().replace(b'"id_a"', b'"id_b"').replace(b'"ls"', b'"rm"'))
    assert calls_at(reading, target) == [{"id": "id_a", "name": "Bash", "input": None}]
    assert calls_at(reading, "offset:12345") is None


# --------------------------------------------------------------------------
# Sharing and bounding


def test_the_registry_drops_the_least_recently_read_over_budget(tmp_path: Path) -> None:
    paths = [
        write(tmp_path / f"c{index}.jsonl", [user("root", None, "w" * 20_000)])
        for index in range(3)
    ]
    # Room for two of them, whatever one is estimated at.
    probe = ConversationIndexRegistry()
    probe.snapshot(paths[0], "claude")
    one = probe.status()["estimated_bytes"]
    assert one > 0
    registry = ConversationIndexRegistry(budget_bytes=int(one * 2.5))
    for path in paths:
        registry.snapshot(path, "claude")
    status = registry.status()
    assert status["indexes"] == 2
    assert status["evictions"] == 1
    assert status["estimated_bytes"] <= status["budget_bytes"]
    # The one dropped was the oldest read; reading it again rebuilds it.
    registry.snapshot(paths[0], "claude")
    assert counters(registry)["builds"] == 4

    capped = ConversationIndexRegistry(max_indexes=1)
    capped.snapshot(paths[0], "claude")
    capped.snapshot(paths[1], "claude")
    assert capped.status()["indexes"] == 1


async def test_concurrent_readers_share_one_refresh_and_a_slow_one_says_indexing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write(tmp_path / "claude.jsonl", [user("root", None, "start")])
    registry = ConversationIndexRegistry()
    release = threading.Event()
    calls = 0
    original = ConversationIndex.refresh

    def slow_refresh(index: ConversationIndex) -> Any:
        nonlocal calls
        calls += 1
        release.wait(10)
        return original(index)

    monkeypatch.setattr(ConversationIndex, "refresh", slow_refresh)

    try:
        with pytest.raises(IndexBuilding) as building:
            await registry.snapshot_async(path, "claude", wait_seconds=0.05)
        assert set(building.value.progress) == {"read_bytes", "total_bytes", "building"}

        # The first refresh was not abandoned: these two join it rather than queueing.
        waiting = [
            asyncio.create_task(registry.snapshot_async(path, "claude", wait_seconds=10))
            for _ in range(2)
        ]
        # One turn of the loop runs both tasks up to their await, which is where
        # they join; nothing here waits on time.
        await asyncio.sleep(0)
        assert counters(registry)["joined"] == 2
        release.set()
        first, second = await asyncio.gather(*waiting)
        # A finished read is not kept as "in flight": it would pin its reading for ever.
        assert registry.status()["in_flight"] == 0
    finally:
        release.set()
        registry.close()
    assert first is second
    assert calls == 1
    assert [m["text"] for m in first.messages] == ["start"]
