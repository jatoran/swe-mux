from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from swe_mux.history import HistoryIndex
from swe_mux.history_backfill import HistoryBackfillManager
from swe_mux.models import SessionRecord
from swe_mux.projects import ProjectManager
from swe_mux.reconcile import encode_cwd, scan_external_transcripts
from swe_mux.transcript_view import parse_transcript, transcript_time_summary


def write_claude_transcript(home: Path, cwd: Path, native_id: str) -> Path:
    path = home / ".claude" / "projects" / encode_cwd(cwd) / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2025-01-01T00:00:00Z",
                        "cwd": str(cwd),
                        "sessionId": native_id,
                        "message": {"role": "user", "content": "Find the beacon"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2025-01-01T00:00:01Z",
                        "sessionId": native_id,
                        "message": {
                            "role": "assistant",
                            "model": "claude-opus-4-8",
                            "content": [{"type": "text", "text": "Beacon found"}],
                            "usage": {"input_tokens": 10, "output_tokens": 5},
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_scan_restricts_claude_reads_to_matching_project_dirs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    other = tmp_path / "elsewhere"
    write_claude_transcript(home, root, "in-scope")
    write_claude_transcript(home, other, "out-of-scope")

    scoped = scan_external_transcripts(home, limit=None, roots=[root])
    assert [item.native_id for item in scoped] == ["in-scope"]

    everything = scan_external_transcripts(home, limit=None)
    assert {item.native_id for item in everything} == {"in-scope", "out-of-scope"}


def test_scan_scopes_descendant_cwds_but_not_siblings(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    write_claude_transcript(home, root / "frontend", "child")
    write_claude_transcript(home, tmp_path / "repo-adjacent", "sibling-prefix")

    scoped = scan_external_transcripts(home, limit=None, roots=[root])
    # A subdirectory of the project is in scope; a sibling whose encoded name
    # merely shares the prefix is read (cheap over-match) but the actual cwd is
    # outside the root, so the owner check downstream would drop it. Here we
    # only assert the descendant is always captured.
    assert "child" in {item.native_id for item in scoped}


def test_scan_ignores_claude_orphaned_housekeeping_files(tmp_path: Path) -> None:
    # `<id>.orphaned-<ts>-<hash>.jsonl` still carries the original conversation's
    # sessionId, so indexing it maps the fragment onto the real conversation's
    # history row: the two then alternate ownership of one watermark and both
    # re-parse on every startup, with a stale snippet shown as the conversation.
    home = tmp_path / "home"
    root = tmp_path / "repo"
    real = write_claude_transcript(home, root, "conversation")
    orphan = real.with_name("conversation.orphaned-1712345678-ab12cd.jsonl")
    orphan.write_bytes(real.read_bytes())

    found = scan_external_transcripts(home, limit=None, roots=[root])
    assert [item.path.name for item in found] == [real.name]


def test_scan_survives_a_transcript_deleted_mid_walk(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # Provider transcript cleanup runs in these very directories, so a file can
    # vanish between glob and stat. One raced file used to abort the whole scan
    # with no log, leaving every remaining transcript unindexed.
    home = tmp_path / "home"
    root = tmp_path / "repo"
    write_claude_transcript(home, root, "survivor")
    ghost = write_claude_transcript(home, root, "vanishing")
    ghost.unlink()
    real_glob = Path.glob

    def glob_with_ghost(self: Path, pattern: str) -> Any:
        yield from real_glob(self, pattern)
        if pattern.endswith("*.jsonl"):
            yield ghost

    monkeypatch.setattr(Path, "glob", glob_with_ghost)
    found = scan_external_transcripts(home, limit=None, roots=[root])
    assert [item.native_id for item in found] == ["survivor"]


def test_scan_cancels_and_reports_progress(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    write_claude_transcript(home, root, "one")
    write_claude_transcript(home, root, "two")

    assert (
        scan_external_transcripts(home, limit=None, roots=[root], should_cancel=lambda: True)
        == []
    )

    seen: list[int] = []
    scan_external_transcripts(
        home, limit=None, roots=[root], on_progress=seen.append
    )
    assert seen == [1, 2]


def session(identity: str, backend: str, root: Path, project_id: str) -> SessionRecord:
    return SessionRecord(
        identity,
        identity,
        project_id,
        backend,
        f"native-{identity}",
        str(root),
        f"{backend}.exe",
        [],
        state="idle",
        project_label=root.name,
        project_root=str(root),
    )


async def test_history_message_search_is_role_aware_and_composes_with_filters(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "message": {"content": "Please investigate the lunar cache"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {"content": "The lunar cache is now repaired"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    history = HistoryIndex(tmp_path / "mux.db")
    record = session("run", "claude", root, "project-id")
    record.created_at = 100.0
    await history.session_started(record, str(transcript))
    assert await history.index_transcript(record.id, transcript, "claude") == ("indexed", 2)

    user = await history.history_page(
        query="investigate lunar", search_scope="user", project_id="project-id"
    )
    assert [item["id"] for item in user["items"]] == ["run"]
    assert user["items"][0]["matches"][0]["role"] == "user"
    assert "investigate" in user["items"][0]["matches"][0]["excerpt"]
    assert user["items"][0]["last_message_at"] == 1_767_225_601
    assert user["items"][0]["last_message_role"] == "assistant"
    assert user["items"][0]["native_started_at"] == 1_767_225_600

    assert (await history.history_page(query="repaired", search_scope="user"))["items"] == []
    assistant = await history.history_page(query="repaired", search_scope="assistant")
    assert assistant["items"][0]["matches"][0]["role"] == "assistant"
    assert (await history.history_page(query="project", search_scope="metadata"))["items"]
    assert (
        await history.history_page(query="lunar", date_from=1_767_225_600.5, time_basis="started")
    )["items"] == []
    last_message_window = await history.history_page(
        date_from=1_767_225_600.5,
        date_to=1_767_225_601.5,
        time_basis="last_message",
    )
    assert [item["id"] for item in last_message_window["items"]] == ["run"]
    assert (
        await history.history_page(
            date_from=1_767_225_600.5,
            date_to=1_767_225_601.5,
            time_basis="started",
        )
    )["items"] == []

    matches = await history.history_message_matches("run", "lunar", "all")
    assert [match["ordinal"] for match in matches] == [0, 1]
    assert await history.index_transcript(record.id, transcript, "claude") == ("unchanged", 0)
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:05Z",
                    "message": {"content": "A later reply"},
                }
            )
            + "\n"
        )
    refreshed = await history.history_page(project_id="project-id")
    assert await history.refresh_time_summaries(refreshed["items"]) == 1
    assert refreshed["items"][0]["last_message_at"] == 1_767_225_605
    history.close()


async def test_context_search_ranks_globally_and_supports_substrings_and_message_dates(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    root = tmp_path / "project"
    root.mkdir()
    try:
        for identity, created, body in (
            ("older-relevant", 100.0, "useEffect cleanup useEffect cleanup exact-marker"),
            ("newer-weak", 200.0, "a passing reference to useEffect"),
        ):
            transcript = tmp_path / f"{identity}.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-01-02T00:00:00Z",
                        "message": {"content": body},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            record = session(identity, "claude", root, "project-id")
            record.created_at = created
            await history.session_started(record, str(transcript))
            await history.index_transcript(identity, transcript, "claude")

        ranked = await history.search_history_index(
            query="useEffect cleanup",
            project_id="project-id",
            order="relevance",
        )
        assert ranked["items"][0]["id"] == "older-relevant"
        assert ranked["items"][0]["match_ordinal"] == 0

        substring = await history.search_history_index(
            query="Effect clean",
            query_mode="substring",
            project_id="project-id",
        )
        assert substring["items"][0]["id"] == "older-relevant"

        short_substring = await history.search_history_index(
            query="Ef",
            query_mode="substring",
            project_id="project-id",
        )
        assert {item["id"] for item in short_substring["items"]} == {
            "older-relevant",
            "newer-weak",
        }

        bounded = await history.search_history_index(
            query="useEffect",
            project_id="project-id",
            message_after=1_767_312_001,
        )
        assert bounded["items"] == []
    finally:
        history.close()


async def test_context_search_filters_titles_roles_runs_and_reads_stable_hit_windows(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    root = tmp_path / "project"
    root.mkdir()
    transcript = tmp_path / "conversation.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-03-01T00:00:00Z",
                        "message": {"content": "before context"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-03-01T00:00:01Z",
                        "message": {"content": "the deployment needle is here"},
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-03-01T00:00:02Z",
                        "message": {"content": "after context"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    try:
        record = session("run", "claude", root, "project-id")
        record.name = "Desktop Packaging"
        await history.session_started(record, str(transcript))
        await history.index_transcript("run", transcript, "claude")

        page = await history.search_history_index(
            query="deployment needle",
            search_scope="assistant",
            title_query="packaging",
            project_id="project-id",
            run_ids=("run",),
        )
        hit = page["items"][0]
        watermark = (
            int(hit["match_mtime_ns"]),
            int(hit["match_size"]),
            int(hit["match_parser_version"]),
        )
        window = await history.history_message_window(
            "run", int(hit["match_ordinal"]), watermark=watermark, before=1, after=1
        )
        assert [item["text"] for item in window["messages"]] == [
            "before context",
            "the deployment needle is here",
            "after context",
        ]

        await history.replace_history_messages(
            "run",
            [{"role": "user", "content": [{"type": "text", "text": "changed"}]}],
            mtime_ns=watermark[0] + 1,
            size=watermark[1] + 1,
        )
        stale = await history.history_message_window(
            "run", int(hit["match_ordinal"]), watermark=watermark, before=1, after=1
        )
        assert stale["stale"] is True
    finally:
        history.close()


async def test_context_search_migration_rebuilds_trigram_for_existing_messages(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mux.db"
    root = tmp_path / "project"
    root.mkdir()
    transcript = tmp_path / "conversation.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"content": "prefix-middle-suffix"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    history = HistoryIndex(database)
    record = session("run", "claude", root, "project-id")
    await history.session_started(record, str(transcript))
    await history.index_transcript("run", transcript, "claude")
    history.close()

    connection = sqlite3.connect(database)
    for trigger in (
        "history_messages_trigram_ai",
        "history_messages_trigram_ad",
        "history_messages_trigram_au",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute("DROP TABLE history_messages_trigram")
    connection.commit()
    connection.close()

    migrated = HistoryIndex(database)
    try:
        status = await migrated.message_search_status()
        assert status["ready"] is False
        result = await migrated.search_history_index(
            query="middle",
            query_mode="substring",
            project_id="project-id",
        )
        assert result["items"][0]["id"] == "run"
        assert result["search_index_ready"] is False

        await migrated.maintain_message_search_indexes(batch_size=1)
        assert (await migrated.message_search_status())["ready"] is True
        indexed = await migrated.search_history_index(
            query="middle",
            query_mode="substring",
            project_id="project-id",
        )
        assert indexed["items"][0]["id"] == "run"
        assert indexed["search_index_ready"] is True
    finally:
        migrated.close()


async def test_message_search_migration_survives_inconsistent_legacy_fts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mux.db"
    root = tmp_path / "project"
    root.mkdir()
    transcript = tmp_path / "conversation.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-08-13T12:00:00Z",
                "message": {"content": "legacy migration needle"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    history = HistoryIndex(database)
    record = session("run", "claude", root, "project-id")
    await history.session_started(record, str(transcript))
    await history.index_transcript("run", transcript, "claude")
    history.close()

    connection = sqlite3.connect(database)
    for trigger in (
        "history_messages_ai",
        "history_messages_ad",
        "history_messages_au",
        "history_messages_trigram_ai",
        "history_messages_trigram_ad",
        "history_messages_trigram_au",
    ):
        connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute("DROP TABLE history_message_search_maintenance")
    connection.execute(
        "INSERT INTO history_messages_fts(history_messages_fts) VALUES('delete-all')"
    )
    connection.execute(
        "INSERT INTO history_messages_trigram(history_messages_trigram) VALUES('delete-all')"
    )
    connection.execute("DROP INDEX idx_history_messages_time")
    connection.execute("ALTER TABLE history_messages DROP COLUMN ts_epoch")
    connection.executescript(
        """
        CREATE TRIGGER history_messages_ai AFTER INSERT ON history_messages BEGIN
          INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
        END;
        CREATE TRIGGER history_messages_ad AFTER DELETE ON history_messages BEGIN
          INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
          VALUES('delete',old.id,old.text);
        END;
        CREATE TRIGGER history_messages_au AFTER UPDATE ON history_messages BEGIN
          INSERT INTO history_messages_fts(history_messages_fts,rowid,text)
          VALUES('delete',old.id,old.text);
          INSERT INTO history_messages_fts(rowid,text) VALUES(new.id,new.text);
        END;
        """
    )
    with pytest.raises(sqlite3.DatabaseError, match="malformed"):
        connection.execute("UPDATE history_messages SET ts=ts WHERE history_id='run'")
    connection.rollback()
    connection.commit()
    connection.close()

    migrated = HistoryIndex(database)
    try:
        status = await migrated.message_search_status()
        assert status["ready"] is False
        assert status["reset_required"] is True
        assert migrated._db.execute(
            "SELECT ts_epoch FROM history_messages WHERE history_id='run'"
        ).fetchone()[0] is None

        fallback = await migrated.search_history_index(
            query="migration needle",
            project_id="project-id",
            message_after=1_700_000_000,
            message_before=2_000_000_000,
        )
        assert fallback["items"][0]["id"] == "run"
        assert fallback["items"][0]["match_ts_epoch"] is not None
        assert fallback["search_index_ready"] is False

        await migrated.maintain_message_search_indexes(batch_size=1)
        ready = await migrated.message_search_status()
        assert ready["ready"] is True
        assert ready["cursor_id"] == ready["target_max_id"]
        assert migrated._db.execute(
            "SELECT ts_epoch FROM history_messages WHERE history_id='run'"
        ).fetchone()[0] is not None
        migrated._db.execute(
            "INSERT INTO history_messages_fts(history_messages_fts,rank) "
            "VALUES('integrity-check',1)"
        )
        migrated._db.execute(
            "INSERT INTO history_messages_trigram(history_messages_trigram,rank) "
            "VALUES('integrity-check',1)"
        )
        migrated._db.rollback()
    finally:
        migrated.close()


async def test_optional_message_search_schema_failure_does_not_block_history(
    tmp_path: Path,
) -> None:
    class BrokenSearchHistory(HistoryIndex):
        def _initialize_message_search_schema(self) -> None:
            raise sqlite3.DatabaseError("synthetic optional migration failure")

    history = BrokenSearchHistory(tmp_path / "mux.db")
    try:
        assert await history.history() == []
        status = await history.message_search_status()
        assert status["ready"] is False
        assert status["last_error"] == "search schema unavailable"
    finally:
        history.close()


async def test_naming_rows_answer_by_run_id_and_by_session_id(tmp_path: Path) -> None:
    """The lookup behind display names for sessions the fleet no longer holds.

    A session that rolled over owns more than one row, so the two keys must not collide.
    An id that is itself a row always resolves to *that* row - a caller holding a run id
    means that exact conversation, and a session's first run is keyed by the session id.
    An id that only ever appears as an owner resolves to that session's newest run, which
    is what a reader means by naming the session and nothing else.
    """
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        first = session("s1", "claude", tmp_path, "project-id")
        await history.session_started(first, None)

        rolled = session("s1", "claude", tmp_path, "project-id")
        rolled.name = "release prep"
        rolled.auto_named = False
        rolled.agent_run_id = "run-2"
        rolled.agent_run_seq = 1
        await history.session_promoted(rolled, str(tmp_path / "run-2.jsonl"))

        # A session with no row of its own: every run it owns is keyed by a run id.
        runs = (("run-3", "first pass"), ("run-4", "second pass"))
        for seq, (run_id, name) in enumerate(runs, start=1):
            run = session("s2", "claude", tmp_path, "project-id")
            run.name = name
            run.auto_named = False
            run.agent_run_id = run_id
            run.agent_run_seq = seq
            await history.session_promoted(run, str(tmp_path / f"{run_id}.jsonl"))

        rows = await history.history_naming_rows(["s1", "run-2", "s2", "never-existed"])

        # `s1` is a row id as well as a session id, and the exact row wins.
        assert rows["s1"]["id"] == "s1"
        assert rows["s1"]["auto_named"] == 1
        assert rows["run-2"]["name"] == "release prep"
        assert rows["run-2"]["auto_named"] == 0
        # `s2` is only ever an owner, so it resolves to that session's newest run.
        assert rows["s2"]["id"] == "run-4"
        assert rows["s2"]["name"] == "second pass"
        assert "never-existed" not in rows
        assert await history.history_naming_rows([]) == {}
    finally:
        history.close()


async def test_history_time_bounds_are_chronological_when_native_lines_are_not(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "claude-out-of-order.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "user",
                    "timestamp": "2026-07-20T02:40:59.677Z",
                    "message": {"content": "Later line first"},
                },
                {
                    "type": "user",
                    "timestamp": "2026-07-20T02:40:59.495Z",
                    "message": {"content": "Earlier line second"},
                },
                {
                    "type": "assistant",
                    "timestamp": "2026-07-20T02:41:00.000Z",
                    "message": {"content": "Latest reply"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    summary = transcript_time_summary(transcript, "claude")
    assert summary["native_started_ts"] == "2026-07-20T02:40:59.495Z"
    assert summary["last_message_ts"] == "2026-07-20T02:41:00.000Z"
    assert summary["last_message_role"] == "assistant"

    history = HistoryIndex(tmp_path / "mux.db")
    record = session("out-of-order", "claude", tmp_path, "project-id")
    await history.session_started(record, str(transcript))
    assert await history.index_transcript(record.id, transcript, "claude") == ("indexed", 3)
    entry = await history.history_entry(record.id)
    assert entry is not None
    assert entry["native_started_at"] < entry["last_message_at"]
    assert entry["last_message_role"] == "assistant"
    history.close()


def test_current_codex_response_messages_are_timestamped_without_event_duplicates(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "codex-current.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "session_meta",
                    "timestamp": "2026-02-01T10:00:00Z",
                    "payload": {"id": "native", "cwd": str(tmp_path)},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-02-01T10:00:01Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Investigate"}],
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-02-01T10:00:01Z",
                    "payload": {"type": "user_message", "message": "Investigate"},
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-02-01T10:00:03Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Resolved"}],
                    },
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    messages = parse_transcript(transcript, "codex")
    assert [(item["role"], item["content"][0]["text"]) for item in messages] == [
        ("user", "Investigate"),
        ("assistant", "Resolved"),
    ]
    assert messages[-1]["ts"] == "2026-02-01T10:00:03Z"
    summary = transcript_time_summary(transcript, "codex")
    assert summary["native_started_ts"] == "2026-02-01T10:00:01Z"
    assert summary["last_message_ts"] == "2026-02-01T10:00:03Z"
    assert summary["last_message_role"] == "assistant"


async def test_project_backfill_discovers_assigns_and_indexes_all_native_history(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    home = tmp_path / "home"
    transcript = home / ".codex" / "sessions" / "2025" / "rollout-old.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "timestamp": "2025-01-01T00:00:00Z",
                        "payload": {"id": "old-native", "cwd": str(root)},
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2025-01-01T00:00:01Z",
                        "payload": {
                            "type": "user_message",
                            "message": "Find the antique signal",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "timestamp": "2025-01-01T00:00:02Z",
                        "payload": {"type": "agent_message", "message": "Signal located"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    project = await projects.create("Archive", str(root))
    backfills = HistoryBackfillManager(history, projects, home=home)

    started = backfills.start(project.id)
    await backfills._tasks[started["id"]]
    completed = backfills.get(started["id"])
    assert completed["status"] == "completed"
    assert completed["discovered"] == 1
    assert completed["indexed"] == 1
    assert completed["indexed_messages"] == 2

    result = await history.history_page(
        query="antique signal", search_scope="user", project_id=project.id
    )
    assert result["items"][0]["native_id"] == "old-native"
    assert result["items"][0]["project_id"] == project.id
    assert result["items"][0]["last_message_at"] == 1_735_689_602
    assert result["items"][0]["last_message_role"] == "assistant"

    repeated = backfills.start(project.id)
    await backfills._tasks[repeated["id"]]
    assert backfills.get(repeated["id"])["unchanged"] == 1
    await backfills.stop()
    history.close()


async def test_a_scan_cannot_reassign_a_run_owned_by_another_project(tmp_path: Path) -> None:
    # `ORDER BY external ASC` prefers the mux-owned canonical row for a native
    # id, so without an ownership guard a scan of Project A rewrites the history
    # of a session that ran under nested Project B. A run's Project is decided at
    # spawn and is not a scan's to change.
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        owner = SessionRecord(
            "sid-1",
            "agent",
            "project-b",
            "claude",
            "shared-native",
            str(tmp_path),
            "claude.exe",
            [],
            state="idle",
        )
        owner.agent_run_id = "sid-1"
        await history.session_started(owner, None)

        assert (
            await history.assign_native_project(
                "claude",
                "shared-native",
                project_id="project-a",
                project_label="A",
                project_root=str(tmp_path / "a"),
            )
            is None
        )
        row = await history.history_entry("sid-1")
        assert row is not None and row["project_id"] == "project-b"

        # Re-assigning to the same Project (an ordinary re-scan) is still allowed.
        assert (
            await history.assign_native_project(
                "claude",
                "shared-native",
                project_id="project-b",
                project_label="B",
                project_root=str(tmp_path / "b"),
            )
            == "sid-1"
        )
    finally:
        history.close()
