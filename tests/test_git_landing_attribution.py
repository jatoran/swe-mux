"""Attribution across a landing merge, against a real repository.

The shape here is the one this repository actually lands branches in, and it is
the one every stub gets wrong by construction: session A commits on a branch
inside its own worktree, `master` advances, and session B - an orchestrator, not
the branch's agent - runs `git merge master` in A's checkout, resolves the
conflict by hand, commits the merge, and fast-forwards master onto it.

Everything the attribution must get right is a property of the real objects:
a merge really has two parents, `diff-tree` really says nothing about it, the
combined diff really names only the file the merge decided, and the branch side
really is what the first parent reaches and the second does not.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

from swe_mux.git_monitor import (
    parse_combined_changes,
    parse_raw_changes,
    read_blob_digest,
    read_commit_changes,
    read_excluded_range,
    read_merge_resolution_changes,
)
from swe_mux.git_provenance_backfill import backfill_git_provenance
from swe_mux.history import HistoryIndex
from swe_mux.models import ProjectRecord, SessionRecord
from swe_mux.tier0_store import Tier0Store

#: What B typed into the conflicted file. Nothing else in the merge is B's.
RESOLUTION = "written by A\nwritten on master\n"


#: Commit timestamps, minutes apart, because a landing's ordering is what the
#: contributor window is measured against and a test that commits everything
#: inside one second is not the shape being tested. Set explicitly rather than
#: slept for, so the ordering is exact and the suite stays fast - and anchored to
#: now rather than to a fixed epoch, because the sweep only considers write facts
#: from the last `--since-days` and a fixed date silently ages out of it.
_BASE_TIME = int(time.time()) - 7200


def _at(offset: int) -> dict[str, str]:
    stamp = f"{_BASE_TIME + offset} +0000"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def _git(root: Path | str, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **env} if env else None,
    )
    return result.stdout.strip()


def _repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init")
    _git(root, "config", "user.name", "Test Author")
    _git(root, "config", "user.email", "test@example.invalid")


def _write(root: Path | str, name: str, text: str) -> None:
    # Bytes, not `write_text`: on Windows that translates "\n" to "\r\n", which
    # would silently change the very digest under test.
    (Path(root) / name).write_bytes(text.encode("utf-8"))


def _landing(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "repo"
    _repository(root)
    _write(root, "shared.py", "base\n")
    _write(root, "only-a.py", "base\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Base", env=_at(0))
    base = _git(root, "rev-parse", "HEAD")

    tree = tmp_path / "wt"
    _git(root, "worktree", "add", "-b", "feature", str(tree), base)
    _write(tree, "shared.py", "written by A\n")
    _write(tree, "only-a.py", "also written by A\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-m", "A's branch work", env=_at(600))
    branch_commit = _git(tree, "rev-parse", "HEAD")

    _write(root, "shared.py", "written on master\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "Trunk moved", env=_at(1200))
    trunk = _git(root, "rev-parse", "HEAD")

    # B reconciles inside A's checkout. The merge conflicts, and B decides it.
    subprocess.run(
        ["git", "-C", str(tree), "merge", "master"],
        capture_output=True,
        text=True,
        check=False,
    )
    _write(tree, "shared.py", RESOLUTION)
    _git(tree, "add", "shared.py")
    _git(tree, "commit", "--no-edit", env=_at(1800))
    merge = _git(tree, "rev-parse", "HEAD")
    _git(root, "merge", "--ff-only", "feature")

    return {
        "root": str(root),
        "tree": str(tree),
        "base": base,
        "branch_commit": branch_commit,
        "trunk": trunk,
        "merge": merge,
    }


def test_parse_combined_changes_reads_the_merge_raw_format() -> None:
    """The combined format is not the ordinary one, and the ordinary parser reads
    it as nothing at all - which is indistinguishable from "a merge changed no
    files", the answer this replaces."""
    output = (
        "::100644 100644 100644 " + "1" * 40 + " " + "2" * 40 + " " + "3" * 40 + " MM\0"
        "resolved.py\0"
        "::100644 100644 000000 " + "4" * 40 + " " + "5" * 40 + " " + "0" * 40 + " DD\0"
        "dropped.py\0"
    )
    changes = {change.path: change for change in parse_combined_changes(output)}

    assert set(changes) == {"resolved.py", "dropped.py"}
    assert changes["resolved.py"].blob == "3" * 40
    assert changes["resolved.py"].status == "MM"
    # A path the merge removed stores nothing, so it has no post-image object.
    assert changes["dropped.py"].blob is None
    # The single-parent parser sees no records here at all.
    assert parse_raw_changes(output) == ()


async def test_a_merge_reports_only_what_it_resolved(tmp_path: Path) -> None:
    landing = _landing(tmp_path)
    tree = landing["tree"]

    # The plain reader is silent for a merge, which is why the bytes B decided
    # used to reach nobody and the branch B carried reached only B.
    assert await read_commit_changes(tree, landing["merge"]) == ()

    resolved = await read_merge_resolution_changes(tree, landing["merge"])
    # Exactly the conflicted file. `only-a.py` came wholesale off A's side and is
    # not something the merge decided, so B must never be credited with it - that
    # is the scoping rule, measured against a real merge object.
    assert [change.path for change in resolved] == ["shared.py"]
    digest = await read_blob_digest(tree, resolved[0].blob or "")
    assert digest == hashlib.sha256(RESOLUTION.encode("utf-8")).hexdigest()

    parents = _git(tree, "rev-list", "-1", "--parents", landing["merge"]).split()[1:]
    side = await read_excluded_range(tree, parents[0], (parents[1],))
    # The merge's own line of development is A's branch, and only A's branch.
    assert side == (landing["branch_commit"].lower(),)
    assert await read_excluded_range(tree, parents[1], (parents[0],)) == (
        landing["trunk"].lower(),
    )


async def _seed_ledger(tmp_path: Path, landing: dict[str, str]) -> Path:
    """The ledger as the live daemon would have written it before this change.

    A is the committer of its own branch commit; B is recorded as having made the
    merge commit, because B ran the command mux observed. Nothing yet says whose
    branch the merge carries, which is the whole defect.
    """
    database = tmp_path / "mux.db"
    history = HistoryIndex(database)
    await history.upsert_project(ProjectRecord("project-1", "Project", landing["root"], 0))
    for session_id, name, cwd in (
        ("run-a", "Branch agent", landing["tree"]),
        ("run-b", "Orchestrator", landing["root"]),
    ):
        await history.session_started(
            SessionRecord(
                id=session_id,
                name=name,
                project_id="project-1",
                backend="claude",
                native_session_id=f"native-{session_id}",
                cwd=cwd,
                exe="claude",
                args=[],
                project_label="Project",
                project_root=landing["root"],
                agent_run_id=session_id,
            ),
            None,
        )
    branch_at = float(_git(landing["tree"], "show", "-s", "--format=%ct", landing["branch_commit"]))
    merge_at = float(_git(landing["tree"], "show", "-s", "--format=%ct", landing["merge"]))
    await history.record_git_provenance(
        session_id="run-a",
        session_name="Branch agent",
        agent_run_id="run-a",
        project_id="project-1",
        worktree_root=landing["tree"],
        commit_oid=landing["branch_commit"],
        parent_oids=(landing["base"],),
        subject="A's branch work",
        committed_at=branch_at,
        previous_head=landing["base"],
        relationship="created",
        confidence="exact",
        ambiguous=False,
        source="session_tool",
        evidence_rank=70,
        observed_at=branch_at,
        role="committer",
        match_method="command_range",
    )
    await history.record_git_provenance(
        session_id="run-b",
        session_name="Orchestrator",
        agent_run_id="run-b",
        project_id="project-1",
        worktree_root=landing["tree"],
        commit_oid=landing["merge"],
        parent_oids=(landing["branch_commit"], landing["trunk"]),
        subject="Merge branch 'master' into feature",
        committed_at=merge_at,
        previous_head=landing["branch_commit"],
        relationship="created",
        confidence="exact",
        ambiguous=False,
        source="session_tool",
        evidence_rank=70,
        observed_at=merge_at,
        role="committer",
        match_method="command_range",
    )
    # A was sitting in the checkout while B merged in it, which is exactly the
    # occupancy the repair pass withdraws as bystanding - and must not withdraw
    # once the same row is promoted to name A as the branch's author.
    await history.record_git_provenance(
        session_id="run-a",
        session_name="Branch agent",
        agent_run_id="run-a",
        project_id="project-1",
        worktree_root=landing["tree"],
        commit_oid=landing["merge"],
        parent_oids=(landing["branch_commit"], landing["trunk"]),
        subject="Merge branch 'master' into feature",
        committed_at=merge_at,
        previous_head=landing["branch_commit"],
        relationship="observed",
        confidence="correlated",
        ambiguous=False,
        source="git_monitor",
        evidence_rank=20,
        observed_at=merge_at,
        role="observer",
        match_method="monitor_merged",
    )
    history.close()

    tier0 = Tier0Store(database)
    # B's conflict resolution, and A's branch writes. Both are real, and only the
    # first is in the merge commit.
    await tier0.record_fact(
        session_id="run-b",
        kind="file_write",
        target=str(Path(landing["tree"]) / "shared.py"),
        content_hash=hashlib.sha256(RESOLUTION.encode("utf-8")).hexdigest(),
        project_id="project-1",
        agent_run_id="run-b",
        created_at=merge_at - 60,
    )
    await tier0.record_fact(
        session_id="run-a",
        kind="file_write",
        target=str(Path(landing["tree"]) / "only-a.py"),
        content_hash=hashlib.sha256(b"also written by A\n").hexdigest(),
        project_id="project-1",
        agent_run_id="run-a",
        created_at=branch_at - 60,
    )
    tier0.close()
    return database


async def test_a_landing_merge_names_the_merger_and_the_branchs_author(
    tmp_path: Path,
) -> None:
    """The defect, and its fix, on the exact production shape.

    Before: the merge commit named only B, as `committer`/`created`/`exact` - the
    ledger's strongest claim - on the commit that carries A's whole branch onto
    the trunk, while B's real work in it (the conflict resolution) was recorded
    nowhere at all. Both sessions are true answers here, and each gets its own.
    """
    landing = _landing(tmp_path)
    database = await _seed_ledger(tmp_path, landing)

    dry_run = await backfill_git_provenance(database, None)
    assert dry_run["dry_run"] is True
    assert dry_run["integrator_records"] == 1
    assert dry_run["branch_author_records"] == 1

    applied = await backfill_git_provenance(database, None, apply=True)
    assert applied["records_written"] >= 2

    history = HistoryIndex(database)
    rows = {
        (row["session_id"], row["commit_oid"]): row
        for row in await history.git_provenance(project_id="project-1")
    }
    history.close()

    merger = rows[("run-b", landing["merge"])]
    assert merger["role"] == "integrator"
    assert merger["relationship"] == "merged"
    assert merger["confidence"] == "exact"
    # B is credited with what B actually decided, and with nothing else. A's
    # branch content is not B's, however much of it this commit carries.
    assert merger["contributed_paths"] == ["shared.py"]

    author = rows[("run-a", landing["merge"])]
    assert author["role"] == "branch_author"
    assert author["relationship"] == "authored_branch"
    assert author["confidence"] == "exact"
    assert author["match_method"] == "merge_branch_line"
    # Named, not withdrawn. The occupancy row A had on this commit is the row
    # being promoted, and the bystander rule must not take it back.
    assert author["retracted_at"] is None
    # No path claim: A's files are in A's own commit, and copying them here would
    # be the mirror image of the defect.
    assert author["contributed_paths"] == []

    # A keeps the branch commit outright, and B never appears on it.
    branch = rows[("run-a", landing["branch_commit"])]
    assert branch["role"] == "committer"
    assert ("run-b", landing["branch_commit"]) not in rows


async def test_the_landing_pass_is_idempotent(tmp_path: Path) -> None:
    landing = _landing(tmp_path)
    database = await _seed_ledger(tmp_path, landing)

    async def merge_rows() -> dict[str, tuple[str, str, list[str]]]:
        history = HistoryIndex(database)
        try:
            return {
                str(row["session_id"]): (
                    str(row["role"]),
                    str(row["relationship"]),
                    list(row["contributed_paths"]),
                )
                for row in await history.git_provenance(project_id="project-1")
                if row["commit_oid"] == landing["merge"]
            }
        finally:
            history.close()

    await backfill_git_provenance(database, None, apply=True)
    first = await merge_rows()
    second_report = await backfill_git_provenance(database, None, apply=True)
    # Every write goes through the ranked upsert, so a second run re-plans the
    # same answer and replaces nothing with anything weaker. The *counts* differ
    # legitimately - the reattribution reports a row as already current the second
    # time - so the ledger's own state is what idempotence means here.
    assert await merge_rows() == first
    assert second_report["retractions_written"] == 0
    assert {role for role, _relationship, _paths in first.values()} == {
        "integrator",
        "branch_author",
    }
