"""Folding a conversation's duplicate history rows back into one entry.

One conversation is one entry. Rows opened per resume broke that: the conversation
appeared several times in the list, its messages were indexed once per row (so
search returned the same conversation repeatedly and the index carried N copies of
every message), and because ownership of the file was decided by whichever row a
query happened to return first, the content could sit on any of them — one entry
showing the conversation and its twins showing nothing.

The resume path no longer creates them. These tests pin the repair for the ones
already on disk: what the keeper inherits, what is deleted, and the two cases the
repair must refuse — a live pane's row, and a dry run.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.server import repair_history_duplicates

CONVERSATION = "019fd562-bd56-7d70-aade-00891aee99ed"
BASE = 1_785_000_000.0


def pane(row_id: str, *, created_at: float, name: str = "UI Updates", auto_named: int = 1) -> Any:
    record = SessionRecord(
        row_id, name, "default", "codex", CONVERSATION, r"D:\PROJECTS\swe-mux",
        "codex.exe", ["resume", CONVERSATION],
    )
    record.created_at = created_at
    record.agent_run_id = row_id
    record.auto_named = bool(auto_named)
    return record


async def conversation_with_duplicates(tmp_path: Path) -> tuple[HistoryIndex, str]:
    """The shape the bug produced: one rollout, three rows, content on a duplicate."""
    history = HistoryIndex(tmp_path / "mux.db")
    transcript = str(tmp_path / "rollout-2026-08-05T23-44-05.jsonl")
    original = pane("pane-1", created_at=BASE)
    await history.session_started(original, transcript)
    original.last_activity_ts = BASE + 60
    await history.session_ended(original, "killed")

    first_resume = pane("pane-2", created_at=BASE + 3600)
    await history.session_started(first_resume, "")
    first_resume.tokens_in = 32_000
    first_resume.tokens_out = 900
    first_resume.context_window = 272_000
    first_resume.context_pct = 0.42
    first_resume.context_peak_pct = 0.55
    first_resume.model = "gpt-5-codex"
    first_resume.measurement_source = "codex-transcript"
    first_resume.last_activity_ts = BASE + 4000
    await history.session_ended(first_resume, "process_exit")
    # The reconciler indexed the conversation's one file into whichever row it
    # picked, and that was not the conversation's own.
    await history.replace_history_messages(
        "pane-2",
        [
            {
                "role": "user",
                "ts": BASE + 10,
                "content": [{"type": "text", "text": "resize the rail"}],
            },
            {"role": "assistant", "ts": BASE + 20, "content": [{"type": "text", "text": "done"}]},
        ],
        mtime_ns=1234,
        size=5292392,
    )

    second_resume = pane("pane-3", created_at=BASE + 7200, name="Rail sizing", auto_named=0)
    await history.session_started(second_resume, "")
    second_resume.last_activity_ts = BASE + 7300
    await history.session_ended(second_resume, "process_exit")
    return history, transcript


async def test_the_conversations_own_row_owns_its_transcript(tmp_path: Path) -> None:
    # Ordered to the id, so the row a reconcile indexes into is the earliest one --
    # the conversation's own, and the one a resume now inherits. Ordered only by
    # `external`, this was whatever SQLite returned first.
    history, _ = await conversation_with_duplicates(tmp_path)

    assert (await history.native_history_ids())[("codex", CONVERSATION)] == "pane-1"


async def test_duplicates_are_reported_with_where_the_content_actually_sits(
    tmp_path: Path,
) -> None:
    history, _ = await conversation_with_duplicates(tmp_path)

    report = await history.duplicate_conversation_rows()

    assert len(report) == 1
    assert report[0]["keeper"] == "pane-1"
    assert [row["id"] for row in report[0]["rows"]] == ["pane-1", "pane-2", "pane-3"]
    assert [row["indexed_messages"] for row in report[0]["rows"]] == [0, 2, 0]


async def test_a_dry_run_reports_the_merge_and_writes_nothing(tmp_path: Path) -> None:
    history, _ = await conversation_with_duplicates(tmp_path)

    plan = await history.merge_duplicate_conversation_rows()

    assert plan["dry_run"] is True
    assert plan["merged"][0]["removed"] == ["pane-2", "pane-3"]
    assert plan["merged"][0]["messages_moved_from"] == "pane-2"
    assert len(await history.history()) == 3, "a dry run may not touch a single row"


async def test_the_merge_keeps_the_conversation_and_everything_it_measured(
    tmp_path: Path,
) -> None:
    history, transcript = await conversation_with_duplicates(tmp_path)

    await history.merge_duplicate_conversation_rows(dry_run=False)

    rows = await history.history()
    assert [row["id"] for row in rows] == ["pane-1"]
    entry = rows[0]
    # The conversation's own start, and its own file.
    assert entry["spawned_at"] == BASE
    assert entry["transcript_path"] == transcript
    # The name the user pinned in a later pane outranks the keeper's auto title.
    assert entry["name"] == "Rail sizing"
    assert entry["auto_named"] == 0
    # Token and context figures are cumulative in the transcript, so the last pane
    # to observe the conversation holds its current numbers.
    assert entry["tokens_in"] == 32_000
    assert entry["context_window"] == 272_000
    assert entry["model"] == "gpt-5-codex"
    # The conversation ended when its last pane did.
    assert entry["exit_reason"] == "process_exit"
    assert entry["exited_at"] == BASE + 7300
    # Searchable text moved rather than being reparsed, and only one copy remains.
    matches = await history.history_message_matches("pane-1", "rail", "all")
    assert [match["role"] for match in matches] == ["user"]
    assert await history.history_message_matches("pane-2", "rail", "all") == []
    assert (await history.message_index_watermarks()).get("pane-1") is not None
    assert "pane-2" not in await history.message_index_watermarks()


async def test_a_conversation_a_live_pane_is_writing_to_is_left_alone(
    tmp_path: Path,
) -> None:
    # That pane still records into its row. Stranding its writes to tidy the list is
    # the wrong trade, and the group merges cleanly once it exits.
    history, _ = await conversation_with_duplicates(tmp_path)

    result = await history.merge_duplicate_conversation_rows(
        live_run_ids=frozenset({"pane-3"}), dry_run=False
    )

    assert result["merged"] == []
    assert result["skipped"][0]["reason"] == "live_run"
    assert result["skipped"][0]["live_rows"] == ["pane-3"]
    assert len(await history.history()) == 3


def repair_request(history: HistoryIndex, *, live_run: str | None, body: Any) -> Any:
    """A `/api/history/duplicates/repair` request over stubbed collaborators."""
    sessions = {}
    if live_run is not None:
        record = pane("live-pane", created_at=BASE + 7200)
        record.agent_run_id = live_run
        record.state = "idle"
        sessions["live-pane"] = SimpleNamespace(record=record)

    async def read_json() -> Any:
        return body

    async def emit(_event_type: str, **_payload: Any) -> None:
        return None

    return SimpleNamespace(
        app={
            "history": history,
            "sessions": SimpleNamespace(sessions=sessions),
            "events": SimpleNamespace(emit=emit),
        },
        can_read_body=body is not None,
        json=read_json,
    )


async def test_the_endpoint_is_dry_unless_asked_otherwise(tmp_path: Path) -> None:
    # No body is the shape a curious `curl -X POST` takes, and it must not rewrite
    # history: merging entries has no undo.
    history, _ = await conversation_with_duplicates(tmp_path)

    response = await repair_history_duplicates(
        cast(Any, repair_request(history, live_run=None, body=None))
    )

    assert json.loads(response.body)["dry_run"] is True
    assert len(await history.history()) == 3


async def test_the_endpoint_protects_the_row_a_live_pane_is_writing_to(
    tmp_path: Path,
) -> None:
    # The live set is read from the panes themselves, keyed the way every other
    # history write is keyed: by run, not by mux session id.
    history, _ = await conversation_with_duplicates(tmp_path)

    response = await repair_history_duplicates(
        cast(Any, repair_request(history, live_run="pane-2", body={"dry_run": False}))
    )

    result = json.loads(response.body)
    assert result["merged"] == []
    assert result["skipped"][0]["live_rows"] == ["pane-2"]
    assert len(await history.history()) == 3


async def test_a_quarantined_row_is_neither_a_duplicate_nor_a_keeper(
    tmp_path: Path,
) -> None:
    # A quarantine is an audit record of proven misattribution. It is invisible, so
    # it is not one of the conversation's entries, and merging must not resurrect,
    # absorb, or delete it.
    history, _ = await conversation_with_duplicates(tmp_path)
    await history.quarantine_misattributed_agent_run("pane-2", "root_identity_reconciled")

    await history.merge_duplicate_conversation_rows(dry_run=False)

    assert [row["id"] for row in await history.history()] == ["pane-1"]
    quarantined = await history.history_entry("pane-2")
    assert quarantined is not None and quarantined["agent_visible"] == 0
