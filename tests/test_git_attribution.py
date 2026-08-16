"""Commit attribution against a real repository (roadmap Phase 7.8).

The join these cover is the one place the design can be wrong in a way stubs
would never show: what git actually prints for a commit's changed files, and
whether the digest of a stored blob equals the digest the adapter took of the
bytes an agent wrote.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from swe_mux.git_monitor import (
    parse_raw_changes,
    read_blob_digest,
    read_commit_changes,
    read_commit_range,
)
from swe_mux.git_provenance import candidate_writes, resolve_contributors
from swe_mux.git_provenance_backfill import backfill_git_provenance
from swe_mux.history import HistoryIndex
from swe_mux.models import ProjectRecord, SessionRecord
from swe_mux.tier0_store import Tier0Store

CONTENT = "first line\nsecond line\n"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init")
    _git(root, "config", "user.name", "Test Author")
    _git(root, "config", "user.email", "test@example.invalid")


def _write(root: Path, name: str, text: str) -> None:
    # Bytes, not `write_text`: on Windows that translates "\n" to "\r\n", which
    # would silently change the very digest under test.
    (root / name).write_bytes(text.encode("utf-8"))


def test_parse_raw_changes_reads_renames_deletions_and_additions() -> None:
    output = (
        ":000000 100644 " + "0" * 40 + " " + "1" * 40 + " A\0added.py\0"
        ":100644 000000 " + "2" * 40 + " " + "0" * 40 + " D\0gone.py\0"
        ":100644 100644 " + "3" * 40 + " " + "4" * 40 + " R100\0old.py\0new.py\0"
    )
    changes = {change.path: change for change in parse_raw_changes(output)}

    assert changes["added.py"].blob == "1" * 40
    # A deletion stores nothing, so it carries no post-image object.
    assert changes["gone.py"].blob is None
    # A rename is credited to the path the commit now holds.
    assert set(changes) == {"added.py", "gone.py", "new.py"}
    assert changes["new.py"].blob == "4" * 40


async def test_readers_isolate_one_commit_and_hash_what_it_stored(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repository(root)
    _write(root, "one.py", "base\n")
    _git(root, "add", "one.py")
    _git(root, "commit", "-m", "Base")
    base = _git(root, "rev-parse", "HEAD")
    _write(root, "one.py", CONTENT)
    _git(root, "add", "one.py")
    _git(root, "commit", "-m", "Second")
    _write(root, "two.py", "other\n")
    _git(root, "add", "two.py")
    _git(root, "commit", "-m", "Third")
    head = _git(root, "rev-parse", "HEAD")

    moved = await read_commit_range(str(root), base, head)
    assert [item.subject for item in moved] == ["Third", "Second"]
    assert moved[0].parents[0] == moved[1].oid

    changes = await read_commit_changes(str(root), moved[1].oid)
    assert [change.path for change in changes] == ["one.py"]
    assert changes[0].blob is not None

    digest = await read_blob_digest(str(root), changes[0].blob or "")
    # The equality the contributor join rests on: the bytes git stored are the
    # bytes the agent wrote, so their SHA-256 digests match. Git's own object id
    # is not comparable — it is SHA-1 over a `blob <len>\0` header.
    assert digest == hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()

    matched = resolve_contributors(
        candidate_writes(
            changes,
            [
                {
                    "id": "fact-1",
                    "session_id": "writer",
                    "agent_run_id": "run-1",
                    "target": str(root / "one.py"),
                    "content_hash": digest,
                    "created_at": 1.0,
                }
            ],
            worktree_root=str(root),
            session_roots={},
        ),
        {changes[0].path: digest},
    )
    assert [item.session_id for item in matched] == ["writer"]
    assert matched[0].content_matched is True


async def test_backfill_attributes_contributors_across_all_projects(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repository(root)
    _write(root, "feature.py", CONTENT)
    _git(root, "add", "feature.py")
    _git(root, "commit", "-m", "Committed by one session")
    oid = _git(root, "rev-parse", "HEAD")
    committed_at = float(_git(root, "show", "-s", "--format=%ct", "HEAD"))

    database = tmp_path / "mux.db"
    history = HistoryIndex(database)
    await history.upsert_project(ProjectRecord("project-1", "Project", str(root), 0))
    await history.session_started(
        SessionRecord(
            id="run-1",
            name="Writer",
            project_id="project-1",
            backend="claude",
            native_session_id="native-1",
            cwd=str(root),
            exe="claude",
            args=[],
            project_label="Project",
            project_root=str(root),
            agent_run_id="run-1",
        ),
        None,
    )
    # The commit itself was observed by a different session, which is the case the
    # contributor join exists for: the work in it is not the committer's.
    await history.record_git_provenance(
        session_id="committer",
        session_name="Committer",
        agent_run_id="run-2",
        project_id="project-1",
        worktree_root=str(root),
        commit_oid=oid,
        relationship="created",
        confidence="exact",
        ambiguous=False,
        source="session_tool",
        evidence_rank=70,
        observed_at=committed_at,
        role="committer",
        match_method="command_range",
    )
    history.close()

    tier0 = Tier0Store(database)
    await tier0.record_fact(
        session_id="run-1",
        kind="file_write",
        target=str(root / "feature.py"),
        content_hash=hashlib.sha256(CONTENT.encode("utf-8")).hexdigest(),
        project_id="project-1",
        agent_run_id="run-1",
        created_at=committed_at - 5,
    )
    tier0.close()

    dry_run = await backfill_git_provenance(database, None)
    assert dry_run["dry_run"] is True
    assert dry_run["projects_scanned"] == 1
    assert dry_run["contributor_records"] == 1

    applied = await backfill_git_provenance(database, None, apply=True)
    repeated = await backfill_git_provenance(database, None, apply=True)
    assert applied["records_written"] == repeated["records_written"]

    history = HistoryIndex(database)
    rows = {
        row["session_id"]: row
        for row in await history.git_provenance(project_id="project-1")
    }
    history.close()

    assert rows["committer"]["role"] == "committer"
    assert rows["run-1"]["role"] == "contributor"
    assert rows["run-1"]["confidence"] == "exact"
    assert rows["run-1"]["match_method"] == "write_content"
    assert rows["run-1"]["contributed_paths"] == ["feature.py"]
    assert rows["run-1"]["session_name"] == "Writer"
