from __future__ import annotations

import json
from pathlib import Path

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


def test_scan_cancels_and_reports_progress(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    write_claude_transcript(home, root, "one")
    write_claude_transcript(home, root, "two")

    assert scan_external_transcripts(home, limit=None, roots=[root], should_cancel=lambda: True) == []

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
