from __future__ import annotations

import json
import subprocess
from pathlib import Path

from swe_mux.git_provenance_backfill import backfill_git_provenance
from swe_mux.history import HistoryIndex
from swe_mux.models import ProjectRecord, SessionRecord


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


async def test_backfill_is_read_only_by_default_and_idempotent_when_applied(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "Test Author")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "change.txt").write_text("content\n", encoding="utf-8")
    _git(repository, "add", "change.txt")
    _git(repository, "commit", "-m", "Backfilled commit")
    oid = _git(repository, "rev-parse", "HEAD")
    committed_at = float(_git(repository, "show", "-s", "--format=%ct", "HEAD"))

    transcript = tmp_path / "claude.jsonl"
    records = [
        {
            "type": "assistant",
            "timestamp": committed_at,
            "cwd": str(repository),
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "commit-call",
                        "name": "Bash",
                        "input": {"command": 'git commit -m "Backfilled commit"'},
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": committed_at,
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "commit-call",
                        "content": f"[master {oid[:7]}] Backfilled commit",
                    }
                ]
            },
        },
    ]
    transcript.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )

    database = tmp_path / "mux.db"
    history = HistoryIndex(database)
    await history.upsert_project(ProjectRecord("project-1", "Project", str(repository), 0))
    session = SessionRecord(
        id="run-1",
        name="Builder",
        project_id="project-1",
        backend="claude",
        native_session_id="native-1",
        cwd=str(repository),
        exe="claude",
        args=[],
        project_label="Project",
        project_root=str(repository),
        agent_run_id="run-1",
    )
    await history.session_started(session, str(transcript))
    history.close()

    dry_run = await backfill_git_provenance(database, "Project")
    assert dry_run["dry_run"] is True
    assert dry_run["records_planned"] == 1
    assert dry_run["exact_records"] == 1
    history = HistoryIndex(database)
    assert await history.git_provenance(project_id="project-1") == []
    history.close()

    applied = await backfill_git_provenance(database, "project-1", apply=True)
    repeated = await backfill_git_provenance(database, "project-1", apply=True)
    assert applied["records_written"] == 1
    assert repeated["records_written"] == 1
    history = HistoryIndex(database)
    provenance = await history.git_provenance(project_id="project-1")
    assert len(provenance) == 1
    assert provenance[0]["commit_oid"] == oid
    assert provenance[0]["session_id"] == "run-1"
    assert provenance[0]["agent_run_id"] == "run-1"
    assert provenance[0]["confidence"] == "exact"
    assert provenance[0]["source"] == "transcript_backfill:output_hash"
    assert provenance[0]["tool_call_id"] == "commit-call"
    history.close()
