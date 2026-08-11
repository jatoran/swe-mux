"""Reading opencode conversations out of its database.

opencode is the one registered harness whose conversation is rows rather than an
append-only file. Everything above the reader (Transcript tab, copy-reply, history
search, read-aloud, MCP, observers) reads a record stream, so the reader's job is to
produce the same stream from SQL. These tests exercise that boundary against a real
SQLite database rather than a hand-built record list, because the failure this whole
path is guarding against is a reader that exists and returns nothing.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from swe_mux.opencode_store import conversation_records, conversation_watermark
from swe_mux.transcript_view import (
    conversation_is_readable,
    conversation_view,
    final_exchange,
    parse_transcript,
    transcript_time_summary,
)

SESSION = "ses_01dd88b2eeffYzU5NopqCTr0Mn"
OTHER = "ses_00000000000000000000000000"


def _database(root: Path) -> Path:
    """A database with the columns the reader selects, and nothing more.

    Deliberately not a mirror of opencode's full schema: a fixture that tracked its
    migrations would drift while proving nothing extra, and every column here is one
    the reader names.
    """
    root.mkdir(parents=True, exist_ok=True)
    path = root / "opencode.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, time_updated INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
            " time_created INTEGER NOT NULL, data TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL,"
            " session_id TEXT NOT NULL, data TEXT NOT NULL)"
        )
        connection.commit()
    finally:
        connection.close()
    return path


def _add_message(
    path: Path,
    session_id: str,
    message_id: str,
    role: str,
    parts: list[dict[str, object]],
    *,
    created: int,
) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO session VALUES (?, ?)", (session_id, created)
        )
        connection.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?)",
            (message_id, session_id, created, json.dumps({"role": role})),
        )
        for index, part in enumerate(parts):
            connection.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?)",
                (f"{message_id}-{index}", message_id, session_id, json.dumps(part)),
            )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A populated database, with opencode's data home pointed at it."""
    root = tmp_path / "opencode-data"
    path = _database(root)
    _add_message(
        path, SESSION, "msg_1", "user", [{"type": "text", "text": "run the tests"}], created=1000
    )
    _add_message(
        path,
        SESSION,
        "msg_2",
        "assistant",
        [
            {"type": "step-start"},
            {"type": "reasoning", "text": "private working"},
            {
                "type": "tool",
                "tool": "bash",
                "callID": "call-1",
                "state": {
                    "status": "completed",
                    "input": {"command": "pytest"},
                    "time": {"start": 2000, "end": 2500},
                },
            },
            {"type": "step-finish"},
        ],
        created=2000,
    )
    _add_message(
        path, SESSION, "msg_3", "assistant", [{"type": "text", "text": "all green"}], created=3000
    )
    # A second conversation in the same database, to prove selection is scoped.
    _add_message(
        path, OTHER, "msg_x", "assistant", [{"type": "text", "text": "somebody else"}], created=9000
    )
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(root))
    return path


def test_records_are_scoped_to_one_conversation_and_carry_their_parts(store: Path) -> None:
    records = conversation_records(store, SESSION)
    assert [item["id"] for item in records] == ["msg_1", "msg_2", "msg_3"]
    assert [item["message"]["role"] for item in records] == ["user", "assistant", "assistant"]
    assert [part["type"] for part in records[1]["parts"]] == [
        "step-start",
        "reasoning",
        "tool",
        "step-finish",
    ]
    # A sibling conversation in the same database never bleeds in. For the
    # file-backed harnesses this is guaranteed by the file boundary; here it is the
    # WHERE clause, and cross-attribution is the failure it prevents.
    assert [item["id"] for item in conversation_records(store, OTHER)] == ["msg_x"]
    assert conversation_records(store, "ses_absent") == []
    assert conversation_records(store, "") == []


def test_a_bounded_read_takes_the_newest_messages_in_order(store: Path) -> None:
    records = conversation_records(store, SESSION, max_messages=2)
    assert [item["id"] for item in records] == ["msg_2", "msg_3"]


def test_the_watermark_moves_on_a_new_message_and_on_an_edit(store: Path, tmp_path: Path) -> None:
    """Both halves are load-bearing, which is why it is a pair.

    A file's watermark is (mtime, size) for the same reason: either half alone lets
    a change through, and a stale watermark is trusted by every later `unchanged`
    check.
    """
    before = conversation_watermark(store, SESSION)
    assert before == (3000, 3)

    _add_message(
        store, SESSION, "msg_4", "assistant", [{"type": "text", "text": "more"}], created=3000
    )
    # Same millisecond, so only the count distinguishes them.
    assert conversation_watermark(store, SESSION) == (3000, 4)

    connection = sqlite3.connect(store)
    try:
        connection.execute("UPDATE session SET time_updated = ? WHERE id = ?", (4000, SESSION))
        connection.commit()
    finally:
        connection.close()
    # Same count, so only the time distinguishes them: an edit to an existing row,
    # which is what a streamed assistant message completing looks like.
    assert conversation_watermark(store, SESSION) == (4000, 4)

    assert conversation_watermark(store, "ses_absent") is None
    assert conversation_watermark(tmp_path / "missing.db", SESSION) is None


def test_parsed_messages_carry_text_and_tool_calls(store: Path) -> None:
    messages = parse_transcript(None, "opencode", native_id=SESSION)
    assert [item["role"] for item in messages] == ["user", "assistant", "assistant"]
    assert messages[0]["content"] == [{"type": "text", "text": "run the tests"}]
    # A turn that only ran tools is still a turn, and its reasoning is not rendered:
    # every other dialect shows the reply rather than the model's private working.
    assert messages[1]["content"] == [
        {"type": "tool_use", "name": "bash", "input": {"command": "pytest"}}
    ]
    assert messages[2]["content"] == [{"type": "text", "text": "all green"}]


def test_the_conversation_view_hides_tool_turns_and_counts_them(store: Path) -> None:
    view = conversation_view(None, "opencode", native_id=SESSION)
    assert [item["role"] for item in view["messages"]] == ["user", "assistant"]
    assert view["hidden"] == 1
    # The reader is told how much work happened in the gap rather than left to infer
    # that two messages written either side of a tool call were one thought.
    assert view["messages"][-1]["preceding_tool_calls"] == 1
    # Identity is opencode's own primary key, which survives compaction; a byte
    # offset would not.
    assert view["messages"][-1]["message_id"] == "record:msg_3"


def test_copy_reply_and_time_summary_read_the_same_conversation(store: Path) -> None:
    prompt, reply = final_exchange(None, "opencode", native_id=SESSION)
    assert prompt == "run the tests"
    assert reply == "all green"

    summary = transcript_time_summary(None, "opencode", native_id=SESSION)
    assert summary["native_started_ts"] == 1000
    assert summary["last_message_ts"] == 3000
    assert summary["last_message_role"] == "assistant"
    # The watermark travels in the same columns a file's stat does, holding the
    # store's own pair rather than a file mtime.
    assert (summary["mtime_ns"], summary["size"]) == (3000, 3)


def test_readability_rejects_a_placeholder_id_and_a_missing_store(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The predicate that replaced `path.is_file()` everywhere.

    A session carries its mux id as a placeholder until its CLI reports a real one.
    Reading on a placeholder would render an empty conversation as though it were
    the session's own, so the predicate refuses anything the store does not hold.
    """
    assert conversation_is_readable(None, "opencode", SESSION) is True
    assert conversation_is_readable(None, "opencode", "mux-placeholder-id") is False
    assert conversation_is_readable(None, "opencode", None) is False
    # A path is never consulted for a store-backed harness, even a real one.
    assert conversation_is_readable(store, "opencode", "mux-placeholder-id") is False

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "absent"))
    assert conversation_is_readable(None, "opencode", SESSION) is False
    assert parse_transcript(None, "opencode", native_id=SESSION) == []


def test_a_file_backed_harness_still_answers_on_its_path(tmp_path: Path) -> None:
    """The store branch must not have changed how the other harnesses read."""
    path = tmp_path / "claude.jsonl"
    path.write_text(
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
        + "\n",
        encoding="utf-8",
    )
    assert conversation_is_readable(path, "claude", None) is True
    assert conversation_is_readable(tmp_path / "absent.jsonl", "claude", None) is False
    messages = parse_transcript(path, "claude")
    assert [item["role"] for item in messages] == ["assistant"]


def test_telemetry_extraction_reads_tool_parts(store: Path) -> None:
    """Historical tool telemetry, from the same records the Transcript tab renders.

    A tool part carries its own completion, so one part yields both the use and the
    result; there is no separate result record to correlate by id the way the
    file-backed dialects need.
    """
    from swe_mux.operational_telemetry import scan_native_telemetry

    records = conversation_records(store, SESSION)
    scan = scan_native_telemetry(records, "opencode", "run-1", "project-1", None)
    kinds = [(item["kind"], item["raw_tool"]) for item in scan["tools"]]
    assert kinds == [("tool_use", "bash"), ("tool_result", "bash")]
    assert scan["tools"][1]["success"] == 1
    assert scan["tools"][1]["duration_ms"] == 500
    assert scan["unknown"] == 0
