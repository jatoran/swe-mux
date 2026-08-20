from __future__ import annotations

import json
import subprocess
from pathlib import Path

from swe_mux import git_provenance
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


async def test_repair_withdraws_the_landing_and_keeps_the_checkout_that_made_it(
    tmp_path: Path,
) -> None:
    """The whole reported defect, end to end on a real repository.

    A worktree reconciles with `git merge master`, creating a merge commit, and
    the branch is then landed into the primary checkout with `--ff-only`. Every
    session in the primary checkout has its HEAD dragged onto work it never
    touched, and each used to get a row saying a merge or a rebase had happened
    and no single commit belonged to it.

    The two answers this pass has to keep apart: the worktree *made* that merge
    commit, and the primary checkout merely received it.
    """
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-b", "master")
    _git(repository, "config", "user.name", "Test Author")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "Base")
    base = _git(repository, "rev-parse", "HEAD")

    worktree = tmp_path / "feature"
    _git(repository, "worktree", "add", "-b", "feature", str(worktree))
    (worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(worktree, "add", "feature.txt")
    _git(worktree, "commit", "-m", "Feature work")
    feature = _git(worktree, "rev-parse", "HEAD")

    # master moves on, so the reconcile really is a merge rather than a no-op.
    (repository / "trunk.txt").write_text("trunk\n", encoding="utf-8")
    _git(repository, "add", "trunk.txt")
    _git(repository, "commit", "-m", "Trunk work")
    trunk = _git(repository, "rev-parse", "HEAD")

    _git(worktree, "merge", "master", "-m", "Merge branch 'master' into feature")
    merge = _git(worktree, "rev-parse", "HEAD")
    merged_at = float(_git(repository, "show", "-s", "--format=%ct", merge))
    _git(repository, "merge", "--ff-only", "feature")
    assert _git(repository, "rev-parse", "HEAD") == merge

    database = tmp_path / "mux.db"
    history = HistoryIndex(database)
    await history.upsert_project(ProjectRecord("project-1", "Project", str(repository), 0))

    async def old_monitor_row(
        session_id: str, root: Path, previous_head: str, head: str, observed_at: float
    ) -> None:
        """Exactly what the monitor used to write for a multi-commit move."""
        await history.record_git_provenance(
            session_id=session_id,
            session_name=session_id,
            agent_run_id=session_id,
            project_id="project-1",
            worktree_root=str(root),
            commit_oid=head,
            previous_head=previous_head,
            relationship="observed",
            confidence="ambiguous",
            ambiguous=True,
            source="git_monitor",
            evidence_rank=git_provenance.MONITOR_RANGE_RANK,
            observed_at=observed_at,
            role="observer",
            match_method="monitor_range",
        )

    # The worktree session watched its own commit and then its own merge. The
    # monitor sees each within seconds of its being written, so each row is dated
    # from the commit it observed.
    feature_at = float(_git(repository, "show", "-s", "--format=%ct", feature))
    await old_monitor_row("worktree", worktree, base, feature, feature_at)
    await old_monitor_row("worktree", worktree, feature, merge, merged_at)
    # Three sessions merely had the primary checkout open when it landed.
    for bystander in ("bystander-a", "bystander-b", "bystander-c"):
        await old_monitor_row(bystander, repository, trunk, merge, merged_at + 60)
    history.close()

    report = await backfill_git_provenance(database, "project-1", apply=True)
    assert report["retractions_written"] == 3

    history = HistoryIndex(database)
    standing = {
        (row["session_id"], row["commit_oid"]): row
        for row in await history.git_provenance(project_id="project-1")
    }
    # No bystander is left claiming anything about the commit that landed on them.
    assert not any(session.startswith("bystander") for session, _ in standing)
    kept = standing[("worktree", merge)]
    assert kept["ambiguous"] is False
    assert kept["confidence"] == "correlated"
    assert kept["match_method"] == "monitor_merged"

    withdrawn = [
        row
        for row in await history.git_provenance(project_id="project-1", include_retracted=True)
        if row["retracted_at"]
    ]
    assert len(withdrawn) == 3
    assert {row["retracted_reason"] for row in withdrawn} == {"arrival:fast_forward"}

    # The move itself is not lost: it is recorded against the checkout it happened
    # to, which is what it was a fact about all along.
    moves = {
        (row["worktree_root"], row["commit_oid"]): row
        for row in await history.git_ref_moves(project_id="project-1")
    }
    landing = moves[(str(repository).replace("\\", "/"), merge)]
    assert landing["kind"] == "fast_forward"
    assert landing["authored_count"] == 0
    reconcile = moves[(str(worktree).replace("\\", "/"), merge)]
    assert reconcile["kind"] == "merged"
    assert reconcile["authored_count"] == 1

    # Re-running changes nothing, and in particular does not re-retract the row it
    # decided to keep or resurrect the ones it withdrew.
    #
    # That second half is not a formality. The arrival oracle reads *when* each
    # checkout first held a commit, and a pass that dropped its own withdrawn rows
    # from that reading forgot the worktree had it first — so the next run read
    # the landing as authorship and restored all three bystanders. An observation
    # time survives the withdrawal of the claim built on it.
    history.close()
    again = await backfill_git_provenance(database, "project-1", apply=True)
    assert again["retractions_written"] == 0
    assert again["restorations_written"] == 0
    history = HistoryIndex(database)
    assert len(await history.git_provenance(project_id="project-1")) == len(standing)
    repeated = {
        (row["worktree_root"], row["commit_oid"]): row
        for row in await history.git_ref_moves(project_id="project-1")
    }
    assert repeated[(str(repository).replace("\\", "/"), merge)]["kind"] == "fast_forward"
    history.close()
