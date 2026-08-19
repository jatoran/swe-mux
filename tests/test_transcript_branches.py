"""Reading a Claude transcript as the DAG it is, rather than as a list of lines.

A Claude transcript is append-only and *branching*: ``parentUuid`` names the record
a record answers, so a retry, a ``/rewind``, or a resend after a failed request
appends a new sibling under the same parent and leaves the previous attempt in the
file forever. Read in file order, one prompt resent eight times through an outage is
eight prompts, and history indexes eight copies of it.

The shapes here were taken from a real transcript
(``2064dd08-b382-4d1f-a506-c06f95c56e98``, Claude 2.1.233, 2026-08-18): eight
siblings under one ``/model`` command record, two of them carrying the 529 the
outage produced, and sixteen unrelated forks that are parallel tool batches rather
than branches. That second shape is why this file exists as well as the fix: a
reader that walked only the parent chain would drop every tool result but the last
of each batch, which is a worse bug than the one being fixed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from swe_mux.transcript_view import (
    conversation_cut_points,
    conversation_view,
    parse_transcript,
    transcript_message_page,
)

HUMAN = {"origin": {"kind": "human"}, "promptSource": "typed"}


def write_jsonl(path: Path, events: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return path


def user(uuid: str, parent: str | None, text: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:23Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        **HUMAN,
        **fields,
    }


def assistant(uuid: str, parent: str | None, text: str, **fields: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:30Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        **fields,
    }


def tool_call(uuid: str, parent: str | None, call_id: str, name: str = "Bash") -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": "2026-08-18T16:31:31Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}],
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
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "ok"}],
        },
    }


def texts(view: dict[str, Any], *, abandoned: bool | None = None) -> list[str]:
    return [
        str(message["text"])
        for message in view["messages"]
        if abandoned is None or bool(message.get("abandoned")) is abandoned
    ]


def retried_conversation(path: Path, attempts: int = 8) -> Path:
    """One prompt resent ``attempts`` times, only the last of which ran.

    The shape an outage produces: every attempt is a child of the same record, and
    only the newest one has a reply under it.
    """
    events: list[dict[str, Any]] = [user("root", None, "set up the review")]
    for index in range(attempts - 1):
        events.append(user(f"try{index}", "root", "review the session"))
    events.append(user("live", "root", "review the session"))
    events.append(assistant("reply", "live", "reading it now"))
    return write_jsonl(path, events)


def test_a_resent_prompt_is_one_message_to_everything_that_indexes_it(tmp_path: Path) -> None:
    path = retried_conversation(tmp_path / "claude.jsonl")

    messages = parse_transcript(path, "claude")

    said = [
        block["text"]
        for message in messages
        for block in message["content"]
        if block.get("type") == "text"
    ]
    assert said == ["set up the review", "review the session", "reading it now"]


def test_the_reader_keeps_the_abandoned_attempts_and_marks_them(tmp_path: Path) -> None:
    path = retried_conversation(tmp_path / "claude.jsonl")

    view = conversation_view(path, "claude")

    # Seven attempts were left behind; the reader shows all of them, folded.
    assert view["abandoned_messages"] == 7
    assert texts(view, abandoned=False) == [
        "set up the review",
        "review the session",
        "reading it now",
    ]
    assert texts(view, abandoned=True) == ["review the session"] * 7


def test_a_branch_the_conversation_left_is_not_offered_as_a_fork_point(tmp_path: Path) -> None:
    path = retried_conversation(tmp_path / "claude.jsonl")

    points = conversation_cut_points(path, "claude")

    assert points is not None
    assert [point.role for point in points] == ["user", "user", "assistant"]


def test_the_paged_reader_answers_with_the_live_branch_and_counts_the_rest(
    tmp_path: Path,
) -> None:
    path = retried_conversation(tmp_path / "claude.jsonl")

    page = transcript_message_page(
        path,
        "claude",
        direction="head",
        anchor=None,
        max_bytes=1_000_000,
        max_messages=50,
    )

    assert [message["text"] for message in page["messages"]] == [
        "set up the review",
        "review the session",
        "reading it now",
    ]
    assert page["abandoned_messages"] == 7


def test_a_parallel_tool_batch_is_not_a_branch(tmp_path: Path) -> None:
    """Every result but the last hangs off an ancestor, not off the chain.

    Claude writes one record per call in a batch and parents each ``tool_result``
    to the record whose call it answers. A reader that took the parent chain as the
    conversation would drop the first result of every batch it ever read.
    """
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "check both files"),
            tool_call("call_a", "root", "id_a"),
            tool_call("call_b", "call_a", "id_b"),
            tool_result("res_a", "call_a", "id_a"),
            tool_result("res_b", "call_b", "id_b"),
            assistant("reply", "res_b", "both are fine"),
        ],
    )

    view = conversation_view(path, "claude")

    assert view["abandoned_messages"] == 0
    assert texts(view) == ["check both files", "both are fine"]
    # The batch is still counted as work between the two messages.
    assert view["messages"][1]["preceding_tool_calls"] == 2


def test_a_subagent_turn_is_not_a_branch(tmp_path: Path) -> None:
    """Sidechain records hang off their spawning record the same way a result does.

    They are excluded from the conversation for their own reason - they are another
    agent's turns - but they must not be excluded as *abandoned*, because that would
    make their unanswered calls look like an interrupted branch to the fork reader.
    """
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "delegate the sweep"),
            tool_call("spawn", "root", "id_task", name="Task"),
            user("side_a", "spawn", "sweep the repo", isSidechain=True),
            assistant("side_b", "side_a", "swept", isSidechain=True),
            tool_result("res", "spawn", "id_task"),
            assistant("reply", "res", "the sweep is done"),
        ],
    )

    view = conversation_view(path, "claude")
    points = conversation_cut_points(path, "claude")

    assert view["abandoned_messages"] == 0
    assert texts(view) == ["delegate the sweep", "the sweep is done"]
    assert points is not None
    assert [point.open_tool_calls for point in points] == [0, 0]


def test_a_call_abandoned_mid_turn_does_not_retire_branching(tmp_path: Path) -> None:
    """The failure this guard exists for, and the reason it is not merely cosmetic.

    An outage or an interrupt between a ``tool_use`` and its result leaves that call
    id unanswered for the rest of the file. Counted, it makes every later boundary
    look dirty, and a conversation that can never be forked again is the result -
    silently, because an illegal cut point renders as an ordinary unavailable one.
    """
    live_branch = [
        user("live", "root", "start the audit"),
        assistant("reply", "live", "auditing"),
        tool_call("live_call", "reply", "id_live"),
        tool_result("live_res", "live_call", "id_live"),
        assistant("done", "live_res", "the audit is clean"),
    ]
    retried = write_jsonl(
        tmp_path / "retried.jsonl",
        [
            user("root", None, "open the audit"),
            # The abandoned attempt: it asked for a tool and never got an answer.
            tool_call("dead_call", "root", "id_dead"),
            *live_branch,
        ],
    )
    clean = write_jsonl(
        tmp_path / "clean.jsonl",
        [user("root", None, "open the audit"), *live_branch],
    )

    points = conversation_cut_points(retried, "claude")

    # The invariant, stated as the comparison it is: an abandoned branch changes
    # nothing about where the live one can be cut.
    assert points is not None
    assert [(point.role, point.open_tool_calls) for point in points] == [
        (point.role, point.open_tool_calls)
        for point in conversation_cut_points(clean, "claude") or []
    ]
    assert [point.open_tool_calls for point in points] == [0, 0, 0, 0]


def test_a_rewind_abandons_the_whole_subtree_it_branched_from(tmp_path: Path) -> None:
    """A rewind mid-conversation leaves an entire direction of work behind.

    The eight-retry case is the small version. The one that matters is a reader
    scrolling past a hundred abandoned messages of a wrong approach as though the
    agent had done that work and then contradicted itself.
    """
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "pick an approach"),
            assistant("plan", "root", "I will rewrite the parser"),
            user("wrong", "plan", "do the rewrite"),
            assistant("wrong_a", "wrong", "rewriting the parser"),
            assistant("wrong_b", "wrong_a", "the rewrite is half done"),
            # Rewound to the plan and asked for something else instead.
            user("right", "plan", "patch it instead"),
            assistant("right_a", "right", "patched"),
        ],
    )

    view = conversation_view(path, "claude")

    assert texts(view, abandoned=False) == [
        "pick an approach",
        "I will rewrite the parser",
        "patch it instead",
        "patched",
    ]
    assert texts(view, abandoned=True) == [
        "do the rewrite",
        "rewriting the parser\n\nthe rewrite is half done",
    ]


def test_an_abandoned_reply_never_merges_into_the_live_one(tmp_path: Path) -> None:
    """Two attempts at one turn are two messages, however they are split.

    Streaming fragments merge; a branch boundary is not a streaming split. Merging
    across it produces one message that says both things, with no seam to see.
    """
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "summarise it"),
            assistant("dead", "root", "It is a caching bug."),
            assistant("live", "root", "It is a parsing bug."),
        ],
    )

    view = conversation_view(path, "claude")

    assert texts(view, abandoned=True) == ["It is a caching bug."]
    assert texts(view, abandoned=False) == ["summarise it", "It is a parsing bug."]


def test_an_abandoned_turns_tool_calls_are_not_credited_to_the_live_one(
    tmp_path: Path,
) -> None:
    """A count of "what happened between these two messages" must mean this branch."""
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            user("root", None, "look into it"),
            assistant("dead", "root", "checking the cache"),
            tool_call("dead_call", "dead", "id_dead"),
            user("live", "root", "look into it"),
            assistant("reply", "live", "checking the parser"),
        ],
    )

    view = conversation_view(path, "claude")
    live = [message for message in view["messages"] if not message.get("abandoned")]

    assert [message["preceding_tool_calls"] for message in live] == [0, 0, 0]


def test_a_transcript_with_no_record_linkage_is_read_exactly_as_before(
    tmp_path: Path,
) -> None:
    """The absence of ``uuid`` is a declared "cannot answer", not "nothing branched".

    Older transcripts, other dialects, and every fixture written before this feature
    have no linkage to read. They must keep reading as one flat conversation rather
    than having the newest record's ancestry guessed at.
    """
    path = write_jsonl(
        tmp_path / "claude.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-08-18T16:31:23Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "first"}]},
                **HUMAN,
            },
            {
                "type": "assistant",
                "timestamp": "2026-08-18T16:31:30Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "second"}]},
            },
        ],
    )

    view = conversation_view(path, "claude")

    assert view["abandoned_messages"] == 0
    assert texts(view) == ["first", "second"]
