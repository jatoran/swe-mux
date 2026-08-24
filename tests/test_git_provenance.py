from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from multidict import MultiDict

from swe_mux import app_keys as keys
from swe_mux import git_provenance, server
from swe_mux.event_bus import EventBus
from swe_mux.git_monitor import GitCommitChange, GitCommitMetadata, GitPosition
from swe_mux.git_provenance import (
    GitProvenanceService,
    candidate_writes,
    classify_git_commit_command,
    commit_message_subject,
    resolve_contributors,
    select_commit,
    summarize_git_provenance,
)
from swe_mux.history import HistoryIndex
from swe_mux.models import GitState, MuxEvent

from .host_paths import ABS_ROOT, OTHER_ABS_ROOT, abs_path

OLD = "a" * 40
NEW = "b" * 40
SIBLING = "c" * 40


def _commit(
    oid: str, parents: tuple[str, ...], committed_at: float, subject: str
) -> GitCommitMetadata:
    return GitCommitMetadata(oid=oid, parents=parents, committed_at=committed_at, subject=subject)


def test_git_commit_classifier_is_narrow_and_marks_amend() -> None:
    assert classify_git_commit_command("Bash", "git status") is None
    assert classify_git_commit_command("Read", "git commit -m nope") is None
    assert classify_git_commit_command("Bash", "echo git commit -m nope") is None
    assert classify_git_commit_command("shell_command", "git -C ../other commit -m nope") is None
    assert classify_git_commit_command("shell_command", "git commit -m subject") is not None
    assert classify_git_commit_command("PowerShell", "  git.exe commit -m subject") is not None
    amend = classify_git_commit_command("exec_command", "git commit --amend --no-edit")
    assert amend is not None
    assert amend.relationship == "rewrote"


def test_classifier_sees_every_command_that_can_create_a_commit() -> None:
    """`git commit` was never the only one, and in a worktree flow it is not even
    the common one: reconciling and landing are both `git merge`."""
    kinds = {
        "git merge master": ("merge", "created"),
        "git merge --ff-only worktree-feature": ("merge", "created"),
        "git cherry-pick abc1234": ("cherry_pick", "created"),
        "git revert HEAD": ("revert", "created"),
        "git rebase master": ("rebase", "rewrote"),
        "git rebase --continue": ("rebase", "rewrote"),
        "git am patch.mbox": ("am", "created"),
    }
    for command, (kind, relationship) in kinds.items():
        result = classify_git_commit_command("Bash", command)
        assert result is not None, command
        assert (result.kind, result.relationship) == (kind, relationship), command

    # Resolving or abandoning an operation creates nothing, and neither does a
    # merge told explicitly not to commit.
    for command in (
        "git rebase --abort",
        "git merge --abort",
        "git cherry-pick --quit",
        "git merge --no-commit master",
        "git rebase --skip",
    ):
        assert classify_git_commit_command("Bash", command) is None, command
    # Still narrow in form.
    assert classify_git_commit_command("Bash", "git merge-base --is-ancestor a b") is None
    assert classify_git_commit_command("Bash", "git -C ../other merge master") is None


def test_classify_ref_move_separates_authorship_from_arrival() -> None:
    """The distinction the whole module turns on, in isolation."""
    merge = _commit(SIBLING, (OLD, NEW), 100.0, "Merge")
    landed = _commit(NEW, (OLD,), 10.0, "Landed")

    made = git_provenance.classify_ref_move(
        (merge,),
        head_oid=SIBLING,
        head_parents=(OLD, NEW),
        forward=True,
        backward=False,
        window_start=90.0,
        window_end=110.0,
    )
    assert made.kind == "merged"
    assert made.authored == (merge,)

    arrival = git_provenance.classify_ref_move(
        (landed,),
        head_oid=NEW,
        head_parents=(OLD,),
        forward=True,
        backward=False,
        window_start=90.0,
        window_end=110.0,
    )
    assert arrival.kind == "fast_forward"
    assert arrival.is_arrival

    # Recent enough to look fresh, but the ledger already holds it elsewhere.
    known = git_provenance.classify_ref_move(
        (merge,),
        head_oid=SIBLING,
        head_parents=(OLD, NEW),
        forward=True,
        backward=False,
        window_start=90.0,
        window_end=110.0,
        known_elsewhere=frozenset({SIBLING}),
    )
    assert known.kind == "fast_forward"
    assert known.is_arrival

    rewound = git_provenance.classify_ref_move(
        (landed,),
        head_oid=NEW,
        head_parents=(OLD,),
        forward=False,
        backward=True,
        window_start=0.0,
        window_end=1e12,
    )
    assert rewound.kind == "reset"
    assert rewound.is_arrival

    rebased = git_provenance.classify_ref_move(
        (merge,),
        head_oid=SIBLING,
        head_parents=(OLD,),
        forward=False,
        backward=False,
        window_start=90.0,
        window_end=110.0,
    )
    assert rebased.kind == "rebased"
    assert rebased.authored == (merge,)

    # Git declining to place the move must never read as authorship.
    blind = git_provenance.classify_ref_move(
        (merge,),
        head_oid=SIBLING,
        head_parents=(OLD,),
        forward=None,
        backward=None,
        window_start=90.0,
        window_end=110.0,
    )
    assert blind.kind == "unknown"
    assert blind.is_arrival


def test_commit_message_subject_reads_the_forms_a_command_uses() -> None:
    assert commit_message_subject('git commit -m "Fix the join"') == "Fix the join"
    assert commit_message_subject("git commit -m 'Fix the join'") == "Fix the join"
    assert commit_message_subject('git commit -m "Subject\\n\\nBody"') == "Subject"
    assert commit_message_subject("git commit <<'EOF'\nHeredoc subject\nbody\nEOF") == (
        "Heredoc subject"
    )
    assert commit_message_subject("git commit --amend --no-edit") is None
    assert commit_message_subject(None) is None


def test_select_commit_isolates_a_commit_from_a_concurrent_sibling() -> None:
    mine = _commit(NEW, (OLD,), 100.0, "Mine")
    sibling = _commit(SIBLING, (NEW,), 101.0, "Theirs")

    alone = select_commit((mine,), subject=None, window_start=0.0, window_end=200.0)
    assert alone.commit is mine
    assert alone.method == "command_range"
    assert alone.ambiguous is False

    # The sibling committed on top, so reading HEAD back would name *their* commit.
    by_subject = select_commit(
        (sibling, mine), subject="Mine", window_start=0.0, window_end=200.0
    )
    assert by_subject.commit is mine
    assert by_subject.method == "command_subject"

    by_window = select_commit(
        (sibling, mine), subject=None, window_start=99.0, window_end=100.5
    )
    assert by_window.commit is mine
    assert by_window.method == "command_window"

    undecidable = select_commit(
        (sibling, mine), subject=None, window_start=0.0, window_end=200.0
    )
    assert undecidable.ambiguous is True
    assert undecidable.reason == "concurrent_commits"
    assert undecidable.candidates == 2

    assert select_commit((), subject=None, window_start=0.0, window_end=1.0).commit is None


def _write_fact(
    fact_id: str,
    session_id: str,
    target: str,
    created_at: float,
    content_hash: str | None = None,
    agent_run_id: str = "run-1",
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "session_id": session_id,
        "agent_run_id": agent_run_id,
        "target": target,
        "content_hash": content_hash,
        "created_at": created_at,
    }


def test_contributor_join_prefers_content_and_falls_back_to_the_last_write() -> None:
    changes = (
        GitCommitChange(path="src/one.py", status="M", blob="1" * 40),
        GitCommitChange(path="src/two.py", status="M", blob="2" * 40),
    )
    facts = [
        _write_fact("f1", "writer", abs_path("src/one.py"), 10.0, content_hash="digest-one"),
        _write_fact("f2", "early", abs_path("src/two.py"), 11.0),
        _write_fact("f3", "late", abs_path("src/two.py"), 12.0),
        _write_fact("f4", "elsewhere", f"{OTHER_ABS_ROOT}/src/one.py", 12.5),
    ]
    candidates = candidate_writes(
        changes, facts, worktree_root=ABS_ROOT, session_roots={}
    )
    contributors = {
        item.session_id: item
        for item in resolve_contributors(candidates, {"src/one.py": "digest-one"})
    }

    assert contributors["writer"].content_matched is True
    assert contributors["writer"].confidence == "exact"
    assert contributors["writer"].paths == ("src/one.py",)
    # The later write replaced the earlier one, so only it is in the commit.
    assert contributors["late"].content_matched is False
    assert contributors["late"].confidence == "correlated"
    assert "early" not in contributors
    # A write inside another checkout is not this commit's work.
    assert "elsewhere" not in contributors


def test_a_result_fact_contributes_only_when_its_content_proves_it() -> None:
    """The codex shape: the write's only targeted fact is its result.

    A codex `apply_patch` runs through the shell/exec tool, so the call classifies
    as a command and only `patch_apply_end` carries the written path — with a hash
    of the bytes it wrote. Other harnesses put their result message's hash there
    instead, so a result is admitted on content equality alone, never on its path.
    """
    changes = (
        GitCommitChange(path="src/one.py", status="M", blob="1" * 40),
        GitCommitChange(path="src/two.py", status="M", blob="2" * 40),
    )
    facts = [
        _write_fact("f1", "codex", "C:/repo/src/one.py", 10.0, content_hash="real-bytes"),
        _write_fact("f2", "claude", "C:/repo/src/two.py", 11.0, content_hash="a success message"),
    ]
    for fact in facts:
        fact["kind"] = "file_write_result"
    candidates = candidate_writes(
        changes, facts, worktree_root="C:/repo", session_roots={"codex": "C:/repo"}
    )
    contributors = resolve_contributors(candidates, {"src/one.py": "real-bytes"})

    assert [item.session_id for item in contributors] == ["codex"]
    assert contributors[0].content_matched is True
    # The other result named a file in this checkout and was still not credited:
    # its hash is a message about the write, not the write.
    assert all(not candidate.positional for candidate in candidates)


def test_contributor_join_places_a_relative_write_by_its_session_checkout() -> None:
    changes = (GitCommitChange(path="src/one.py", status="M", blob="1" * 40),)
    facts = [
        _write_fact("f1", "codex", "src/one.py", 10.0),
        _write_fact("f2", "stranger", "src/one.py", 11.0),
    ]

    unplaced = candidate_writes(changes, facts, worktree_root=ABS_ROOT, session_roots={})
    # Neither write can be placed and neither carries content, so nothing is claimed.
    assert resolve_contributors(unplaced, {}) == []

    placed = candidate_writes(
        changes,
        facts,
        worktree_root="C:/repo",
        session_roots={"codex": "C:/repo/src", "stranger": "C:/elsewhere"},
    )
    contributors = resolve_contributors(placed, {})
    assert [item.session_id for item in contributors] == ["codex"]


def test_summarize_rolls_rows_up_into_one_attribution_per_commit() -> None:
    rows = [
        {
            "commit_oid": NEW,
            "session_id": "one",
            "session_name": "Builder",
            "role": "committer",
            "confidence": "exact",
            "ambiguous": False,
            "contributed_paths": ["src/one.py"],
        },
        {
            "commit_oid": NEW,
            "session_id": "two",
            "session_name": "Helper",
            "role": "contributor",
            "confidence": "correlated",
            "ambiguous": False,
            "contributed_paths": ["src/two.py"],
        },
        {
            "commit_oid": SIBLING,
            "session_id": "three",
            "session_name": "Watcher",
            "role": "observer",
            "confidence": "correlated",
            "ambiguous": False,
            "contributed_paths": [],
        },
    ]
    summary = {item["commit_oid"]: item for item in summarize_git_provenance(rows)}

    assert summary[NEW]["attribution"] == "exact"
    assert summary[NEW]["committer"]["session_id"] == "one"
    assert {item["session_id"] for item in summary[NEW]["contributors"]} == {"one", "two"}
    # Occupancy alone attributes nothing: it is a commit mux never saw the work for.
    assert summary[SIBLING]["attribution"] == "ambiguous"
    assert summary[SIBLING]["contributors"] == []


def test_summarize_keeps_a_landing_merges_three_answers_apart() -> None:
    """A merge commit has more than one true answer and one slot forced a choice.

    It always chose the merger, so a land read as authorship of the branch it
    landed. Integration, branch authorship, and the bytes the merge itself decided
    are three separate questions with three separate answers.
    """
    rows = [
        {
            "commit_oid": SIBLING,
            "session_id": "merger",
            "session_name": "Orchestrator",
            "role": "integrator",
            "confidence": "exact",
            "ambiguous": False,
            "contributed_paths": ["shared.py"],
        },
        {
            "commit_oid": SIBLING,
            "session_id": "branch-agent",
            "session_name": "Branch agent",
            "role": "branch_author",
            "confidence": "exact",
            "ambiguous": False,
            "contributed_paths": [],
        },
    ]
    summary = {item["commit_oid"]: item for item in summarize_git_provenance(rows)}

    # An integration is observed evidence of who made the commit, so the commit is
    # answered - it is simply answered with the right verb.
    assert summary[SIBLING]["attribution"] == "exact"
    assert summary[SIBLING]["committer"] is None
    assert summary[SIBLING]["integrator"]["session_id"] == "merger"
    assert [item["session_id"] for item in summary[SIBLING]["branch_authors"]] == [
        "branch-agent"
    ]
    # The merger contributed the resolution, and only the resolution.
    assert [item["session_id"] for item in summary[SIBLING]["contributors"]] == ["merger"]
    assert summary[SIBLING]["contributors"][0]["paths"] == ["shared.py"]


async def test_the_ledger_can_be_searched_by_commit_subject(tmp_path: Path) -> None:
    """An indexed LIKE over one Project's ledger: instant, and about observed commits only.

    It complements `git log --grep` rather than replacing it - Git has read every commit
    message, this has read only the ones swe-mux watched happen - and what it adds is
    that it knows which session made each of them.
    """
    history = HistoryIndex(tmp_path / "mux.db")
    common = {
        "session_id": "session-1",
        "session_name": "Builder",
        "agent_run_id": "run-1",
        "project_id": "project-1",
        "worktree_root": "C:/repo",
        "parent_oids": (),
        "committed_at": 10.0,
        "previous_head": OLD,
        "source_event_seq": 7,
        "tool_call_id": None,
        "observed_at": 12.0,
        "relationship": "created",
        "confidence": "exact",
        "ambiguous": False,
        "source": "session_tool",
        "evidence_rank": 70,
        "role": "committer",
    }
    await history.record_git_provenance(
        **common, commit_oid=NEW, subject="Teach the rail to overflow"
    )
    await history.record_git_provenance(
        **common, commit_oid=SIBLING, subject="A 100% literal _subject_"
    )

    # Case-insensitive by SQLite's own `LIKE`, which is what a search box means.
    assert [row["commit_oid"] for row in await history.git_provenance(
        project_id="project-1", subject_query="RAIL"
    )] == [NEW]
    # `%` and `_` are LIKE wildcards, so a subject containing them must be matched
    # literally: without the escape, "100%" would match everything.
    assert [row["commit_oid"] for row in await history.git_provenance(
        project_id="project-1", subject_query="100% literal"
    )] == [SIBLING]
    assert await history.git_provenance(
        project_id="project-1", subject_query="100xliteral"
    ) == []
    # An empty query is not a filter at all, rather than a `%%` that matches everything
    # through a scan.
    assert len(await history.git_provenance(project_id="project-1", subject_query="  ")) == 2


async def test_store_promotes_observed_evidence_to_exact_without_duplication(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    common = {
        "session_id": "session-1",
        "session_name": "Builder",
        "agent_run_id": "run-1",
        "project_id": "project-1",
        "worktree_root": "C:/repo",
        "commit_oid": NEW,
        "parent_oids": (OLD,),
        "subject": "Commit provenance",
        "committed_at": 10.0,
        "previous_head": OLD,
        "source_event_seq": 7,
        "tool_call_id": None,
        "observed_at": 12.0,
    }
    observed = await history.record_git_provenance(
        **common,
        relationship="observed",
        confidence="correlated",
        ambiguous=False,
        source="git_monitor",
        evidence_rank=20,
        role="observer",
        contributed_paths=("src/one.py",),
    )
    exact = await history.record_git_provenance(
        **{
            **common,
            "parent_oids": (),
            "subject": "",
            "committed_at": None,
            "tool_call_id": "call-1",
            "observed_at": 13.0,
        },
        relationship="created",
        confidence="exact",
        ambiguous=False,
        source="session_tool",
        evidence_rank=70,
        role="committer",
        match_method="command_range",
    )
    rows = await history.git_provenance(project_id="project-1")

    assert observed["relationship"] == "observed"
    assert exact["relationship"] == "created"
    assert exact["confidence"] == "exact"
    assert exact["tool_call_id"] == "call-1"
    assert exact["observed_at"] == 12.0
    assert exact["parent_oids"] == [OLD]
    assert exact["subject"] == "Commit provenance"
    assert exact["committed_at"] == 10.0
    assert exact["role"] == "committer"
    assert exact["match_method"] == "command_range"
    # Paths are evidence, not classification: stronger evidence that identified
    # none of them must not erase the ones already proved.
    assert exact["contributed_paths"] == ["src/one.py"]
    assert len(rows) == 1
    history.close()


async def test_a_checkout_spelled_two_ways_is_one_row(tmp_path: Path) -> None:
    """The checkout is part of the uniqueness key, so it needs one spelling.

    Git prints forward slashes and pathlib prints backslashes for one directory.
    Both spellings in the table made the daemon's row and the backfill's row for
    one session and one commit into two rows, and the commit then read as having
    contributed to itself twice.
    """
    database = tmp_path / "mux.db"
    history = HistoryIndex(database)
    common: dict[str, Any] = {
        "session_id": "session-1",
        "session_name": "Builder",
        "agent_run_id": "run-1",
        "project_id": "project-1",
        "commit_oid": NEW,
        "ambiguous": False,
    }
    await history.record_git_provenance(
        **common,
        worktree_root="D:/repo",
        relationship="created",
        confidence="exact",
        source="session_tool",
        evidence_rank=70,
        role="committer",
    )
    await history.record_git_provenance(
        **common,
        worktree_root="D:\\repo",
        relationship="contributed",
        confidence="correlated",
        source="tier0_write",
        evidence_rank=60,
        role="contributor",
        contributed_paths=("src/one.py",),
    )
    rows = await history.git_provenance(project_id="project-1")
    assert len(rows) == 1
    assert rows[0]["role"] == "committer"
    assert rows[0]["worktree_root"] == "D:/repo"
    assert rows[0]["contributed_paths"] == ["src/one.py"]

    # A database written before the canonical form still holds both spellings, so
    # opening it must collapse them rather than leave the duplicate in place.
    history._db.execute(  # noqa: SLF001 - the migration is what is under test
        "INSERT INTO git_provenance(id,session_id,session_name,agent_run_id,project_id,"
        "worktree_root,commit_oid,relationship,confidence,source,evidence_rank,"
        "observed_at,updated_at,role) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy",
            "session-1",
            "Builder",
            "run-1",
            "project-1",
            "D:\\repo",
            NEW,
            "observed",
            "ambiguous",
            "git_monitor",
            10,
            1.0,
            1.0,
            "observer",
        ),
    )
    history._db.commit()  # noqa: SLF001
    history.close()

    reopened = HistoryIndex(database)
    repaired = await reopened.git_provenance(project_id="project-1")
    assert len(repaired) == 1
    assert repaired[0]["role"] == "committer"
    assert repaired[0]["worktree_root"] == "D:/repo"
    reopened.close()


def _session(
    session_id: str = "session-1", name: str = "Builder", run_id: str = "run-1"
) -> Any:
    record = SimpleNamespace(
        id=session_id,
        name=name,
        agent_run_id=run_id,
        project_id="project-1",
        git_cwd="C:/repo",
        git=GitState(root="C:/repo", head=OLD),
        state="working",
    )
    return SimpleNamespace(record=record)


def _patch_git(
    monkeypatch: pytest.MonkeyPatch,
    *,
    head: str = NEW,
    commits: tuple[GitCommitMetadata, ...] = (),
    first_parent: tuple[GitCommitMetadata, ...] | None = None,
    forward: bool | None = True,
    backward: bool | None = False,
) -> None:
    """Stand in for the repository.

    `commits` is the full-ancestry range and `first_parent` the reference's own
    line; they differ exactly when a merge is involved, which is the case the
    classifier exists to tell apart, so the fake has to be able to express it.
    """

    async def position(_cwd: str) -> GitPosition:
        return GitPosition("C:/repo", head)

    async def commit_range(
        _cwd: str, _base: str | None, _head: str, **kwargs: Any
    ) -> Any:
        if kwargs.get("first_parent") and first_parent is not None:
            return first_parent
        return commits

    async def metadata(_cwd: str, oid: str) -> GitCommitMetadata | None:
        return next((item for item in commits if item.oid == oid), None)

    async def is_ancestor(_cwd: str, ancestor: str, descendant: str) -> bool | None:
        return forward if descendant == head else backward

    monkeypatch.setattr(git_provenance, "read_git_position", position)
    monkeypatch.setattr(git_provenance, "read_commit_range", commit_range)
    monkeypatch.setattr(git_provenance, "read_commit_metadata", metadata)
    monkeypatch.setattr(git_provenance, "read_is_ancestor", is_ancestor)


async def _run_command(
    service: GitProvenanceService, session_id: str, call_id: str, command: str
) -> None:
    await service.handle_event(
        MuxEvent(
            1.0,
            session_id,
            "transcript",
            "tool_use",
            {"tool": "Bash", "call_id": call_id, "target": command},
            1,
        )
    )
    await service.handle_event(
        MuxEvent(
            2.0,
            session_id,
            "transcript",
            "tool_result",
            {"tool": "Bash", "call_id": call_id, "success": True},
            2,
        )
    )


async def _commit_command(service: GitProvenanceService, session_id: str, call_id: str) -> None:
    await _run_command(service, session_id, call_id, 'git commit -m "Add provenance"')


async def test_successful_session_git_commit_records_exact_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(monkeypatch, commits=(_commit(NEW, (OLD,), 11.0, "Add provenance"),))

    await _commit_command(service, session.record.id, "call-1")
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["commit_oid"] == NEW
    assert rows[0]["parent_oids"] == [OLD]
    assert rows[0]["relationship"] == "created"
    assert rows[0]["confidence"] == "exact"
    assert rows[0]["role"] == "committer"
    assert rows[0]["match_method"] == "command_range"
    history.close()


async def test_shared_checkout_still_records_an_exact_committer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared HEAD is a fact about the starting point, not about the commit.

    This is the defect Phase 7.8 exists to remove: the same evidence used to be
    downgraded to `ambiguous` purely because another session had the directory open.
    """
    first, second = _session("one"), _session("two")
    manager = cast(Any, SimpleNamespace(sessions={"one": first, "two": second}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(monkeypatch, commits=(_commit(NEW, (OLD,), 11.0, "Add provenance"),))

    await _commit_command(service, "one", "call-1")
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["session_id"] == "one"
    assert rows[0]["ambiguous"] is False
    assert rows[0]["confidence"] == "exact"
    history.close()


async def test_amend_attributes_the_rewritten_commit_to_the_amending_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An amend replaces the head, so the rewritten commit is what the range holds."""
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    # OLD is no longer an ancestor after the amend; the rewritten commit carries the
    # replaced commit's own parent.
    _patch_git(monkeypatch, commits=(_commit(NEW, (SIBLING,), 11.0, "Amended subject"),))

    await service.handle_event(
        MuxEvent(
            1.0,
            session.record.id,
            "transcript",
            "tool_use",
            {"tool": "Bash", "call_id": "amend", "target": "git commit --amend --no-edit"},
            1,
        )
    )
    await service.handle_event(
        MuxEvent(
            2.0,
            session.record.id,
            "transcript",
            "tool_result",
            {"tool": "Bash", "call_id": "amend", "success": True},
            2,
        )
    )
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["relationship"] == "rewrote"
    assert rows[0]["confidence"] == "exact"
    assert rows[0]["role"] == "committer"
    # The diff base is the rewritten commit's own parent, not the replaced head.
    assert rows[0]["previous_head"] == SIBLING
    history.close()


async def test_concurrent_commit_commands_stay_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    # Two commits landed in the range and neither the subject nor the window can
    # say which one this command produced.
    _patch_git(
        monkeypatch,
        head=SIBLING,
        commits=(
            _commit(SIBLING, (NEW,), 1.5, "Something else"),
            _commit(NEW, (OLD,), 1.5, "Also not it"),
        ),
    )

    await _commit_command(service, session.record.id, "call-1")
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["confidence"] == "ambiguous"
    assert rows[0]["ambiguous"] is True
    assert rows[0]["match_method"] == "command_ambiguous"
    history.close()


async def test_failed_commit_and_unchanged_head_record_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(monkeypatch, head=OLD)

    for call_id, success in (("failed", False), ("unchanged", True)):
        await service.handle_event(
            MuxEvent(
                1.0,
                session.record.id,
                "transcript",
                "tool_use",
                {"tool": "Bash", "call_id": call_id, "target": "git commit -m x"},
            )
        )
        await service.handle_event(
            MuxEvent(
                2.0,
                session.record.id,
                "transcript",
                "tool_result",
                {"tool": "Bash", "call_id": call_id, "success": success},
            )
        )
    assert await history.git_provenance(project_id="project-1") == []
    history.close()


async def _no_digest(_cwd: str, _blob: str) -> str | None:
    return None


class _FakeTier0:
    def __init__(self, facts: list[dict[str, Any]]) -> None:
        self.facts = facts
        self.calls: list[tuple[float, float]] = []

    async def write_facts_for_project(
        self, project_id: str, *, since: float, until: float, limit: int = 2000
    ) -> list[dict[str, Any]]:
        self.calls.append((since, until))
        return [
            fact
            for fact in self.facts
            if since <= float(fact["created_at"]) <= until and fact["project_id"] == project_id
        ]


async def test_commit_records_every_contributing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shared-index case: one session staged, another ran the commit.

    Git keeps one author, so the plural answer is one no git tool records.
    """
    committer, helper = _session("committer"), _session("helper")
    manager = cast(
        Any, SimpleNamespace(sessions={"committer": committer, "helper": helper})
    )
    history = HistoryIndex(tmp_path / "mux.db")
    tier0 = _FakeTier0(
        [
            {
                "id": "f1",
                "project_id": "project-1",
                "session_id": "committer",
                "agent_run_id": "run-1",
                "target": "C:/repo/src/mine.py",
                "content_hash": None,
                "created_at": 9.0,
            },
            {
                "id": "f2",
                "project_id": "project-1",
                "session_id": "helper",
                "agent_run_id": "run-2",
                "target": "C:/repo/src/theirs.py",
                "content_hash": "written-digest",
                "created_at": 9.5,
            },
        ]
    )
    service = GitProvenanceService(history, manager, EventBus(), cast(Any, tier0))
    _patch_git(monkeypatch, commits=(_commit(NEW, (OLD,), 11.0, "Add provenance"),))

    async def changes(_cwd: str, _oid: str, **_kwargs: Any) -> Any:
        return (
            GitCommitChange(path="src/mine.py", status="M", blob="1" * 40),
            GitCommitChange(path="src/theirs.py", status="M", blob="2" * 40),
        )

    async def digest(_cwd: str, blob: str) -> str | None:
        return "written-digest" if blob == "2" * 40 else None

    monkeypatch.setattr(git_provenance, "read_commit_changes", changes)
    monkeypatch.setattr(git_provenance, "read_blob_digest", digest)

    await _commit_command(service, "committer", "call-1")
    rows = {row["session_id"]: row for row in await history.git_provenance(project_id="project-1")}

    assert rows["committer"]["role"] == "committer"
    assert rows["committer"]["confidence"] == "exact"
    assert rows["committer"]["contributed_paths"] == ["src/mine.py"]
    assert rows["helper"]["role"] == "contributor"
    assert rows["helper"]["relationship"] == "contributed"
    assert rows["helper"]["confidence"] == "exact"
    assert rows["helper"]["match_method"] == "write_content"
    assert rows["helper"]["contributed_paths"] == ["src/theirs.py"]
    assert rows["helper"]["agent_run_id"] == "run-2"

    summary = summarize_git_provenance(list(rows.values()))
    assert summary[0]["attribution"] == "exact"
    assert {item["session_id"] for item in summary[0]["contributors"]} == {"committer", "helper"}
    history.close()


async def test_monitor_head_transition_records_correlated_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    session.record.git = GitState(root="C:/repo", head=NEW)
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(monkeypatch, commits=(_commit(NEW, (OLD,), 11.0, "Observed"),))

    await service.handle_event(
        MuxEvent(
            2.0,
            session.record.id,
            "daemon",
            "git_changed",
            {"git": {"root": "C:/repo"}, "head": NEW, "previous_head": OLD},
            9,
        )
    )
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["relationship"] == "observed"
    assert rows[0]["confidence"] == "correlated"
    assert rows[0]["role"] == "observer"
    assert rows[0]["source_event_seq"] == 9
    history.close()


async def _git_change(
    service: GitProvenanceService, session_id: str, head: str, previous_head: str, ts: float = 2.0
) -> None:
    await service.handle_event(
        MuxEvent(
            ts,
            session_id,
            "daemon",
            "git_changed",
            {"git": {"root": "C:/repo"}, "head": head, "previous_head": previous_head},
            9,
        )
    )


async def test_landing_fast_forward_claims_nothing_for_the_sessions_it_moves_under(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this whole pass exists to remove.

    `git merge --ff-only` in the primary checkout drags every attached session's
    HEAD onto commits written in a worktree minutes earlier. Each of those
    sessions used to get a row saying a merge or a rebase had happened and no
    single commit belonged to it — a sentence about a commit none of them had
    touched. The move is a fact about the checkout and is now recorded as one.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    landed = (_commit(SIBLING, (NEW,), -9000.0, "Landed"), _commit(NEW, (OLD,), -9100.0, "Base"))
    _patch_git(monkeypatch, head=SIBLING, commits=landed, first_parent=landed)

    await _git_change(service, session.record.id, SIBLING, OLD)

    assert await history.git_provenance(project_id="project-1") == []
    moves = await history.git_ref_moves(project_id="project-1")
    assert len(moves) == 1
    assert moves[0]["kind"] == "fast_forward"
    assert moves[0]["commit_count"] == 2
    assert moves[0]["authored_count"] == 0
    assert moves[0]["worktree_root"] == "C:/repo"
    history.close()


async def test_arrival_is_recognized_from_the_ledger_without_asking_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit already recorded under another checkout arrived in this one.

    This is what makes a landing honest for free: mux saw the worktree session
    make the commit, so the checkout it lands in claims none of it — even though
    the commit is recent enough that the time window alone would call it fresh.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")

    async def seed(observed_at: float) -> None:
        await history.record_git_provenance(
            session_id="worktree-session",
            session_name="Worktree",
            agent_run_id="run-w",
            project_id="project-1",
            worktree_root="C:/repo/.worktrees/feature",
            commit_oid=NEW,
            relationship="created",
            confidence="exact",
            ambiguous=False,
            source="session_tool",
            evidence_rank=git_provenance.COMMITTER_EXACT_RANK,
            observed_at=observed_at,
            role="committer",
            match_method="command_range",
        )

    await seed(1.0)
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(
        monkeypatch,
        commits=(_commit(NEW, (OLD,), 11.0, "Made in the worktree"),),
        first_parent=(_commit(NEW, (OLD,), 11.0, "Made in the worktree"),),
    )

    await _git_change(service, session.record.id, NEW, OLD, ts=2.0)

    rows = await history.git_provenance(project_id="project-1")
    assert [row["session_id"] for row in rows] == ["worktree-session"]
    moves = await history.git_ref_moves(project_id="project-1")
    assert moves[0]["kind"] == "fast_forward"
    history.close()


async def test_the_arrival_oracle_only_points_backwards_in_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The checkout that *made* a commit must not read its own work as an arrival.

    After a landing both checkouts hold the commit, so "recorded under another
    checkout" is symmetric and, used alone, retracts the one true answer along
    with the noise. Measured on real data: it stamped the worktree session that
    ran `git merge` as a bystander to its own merge commit.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    # The other checkout recorded it *after* this observation — it landed there.
    await history.record_git_provenance(
        session_id="downstream",
        session_name="Primary checkout",
        agent_run_id="run-d",
        project_id="project-1",
        worktree_root="C:/repo-primary",
        commit_oid=NEW,
        relationship="observed",
        confidence="correlated",
        ambiguous=False,
        source="git_monitor",
        evidence_rank=git_provenance.MONITOR_OBSERVED_RANK,
        observed_at=500.0,
        role="observer",
    )
    service = GitProvenanceService(history, manager, EventBus())
    made = (_commit(NEW, (OLD,), 11.0, "Made here"),)
    _patch_git(monkeypatch, commits=made, first_parent=made)

    await _git_change(service, session.record.id, NEW, OLD, ts=20.0)

    rows = {row["session_id"]: row for row in await history.git_provenance(project_id="project-1")}
    assert rows[session.record.id]["match_method"] == "monitor_created"
    moves = await history.git_ref_moves(project_id="project-1")
    assert moves[0]["kind"] == "created"
    assert moves[0]["authored_count"] == 1
    history.close()


async def test_merge_command_records_the_session_that_ran_it_as_the_integrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git merge` creates a commit, and full ancestry hides that it created one.

    The merge absorbs the side branch, so `previous..head` counts the commits it
    pulled in and reads as a bulk arrival. The reference's own first-parent line
    gained exactly one commit, and that commit is this session's — as an
    *integration*, which is what a merge commit is. The stronger word is reserved
    for a commit whose content the session wrote, because a landing merge carries
    somebody else's branch and `committed` claimed all of it.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    merge = _commit(SIBLING, (OLD, NEW), 12.0, "Merge branch 'master' into feature")
    _patch_git(
        monkeypatch,
        head=SIBLING,
        # Full ancestry sees two; the first-parent line sees only the merge.
        commits=(merge, _commit(NEW, (OLD,), 11.0, "From master")),
        first_parent=(merge,),
    )

    await _run_command(service, session.record.id, "call-merge", "git merge master")
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["commit_oid"] == SIBLING
    assert rows[0]["role"] == "integrator"
    assert rows[0]["relationship"] == "merged"
    assert rows[0]["confidence"] == "exact"
    assert rows[0]["ambiguous"] is False
    # Picked the same way any single authored commit is: it was the only one the
    # command created. That it was a merge is in the row's own two parents, and
    # in the move recorded for the checkout.
    assert rows[0]["match_method"] == "command_range"
    assert rows[0]["parent_oids"] == [OLD, NEW]
    moves = await history.git_ref_moves(project_id="project-1")
    assert moves[0]["kind"] == "merged"
    assert moves[0]["authored_count"] == 1
    history.close()


async def test_a_land_names_the_merger_and_the_branch_it_carries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B merges master inside A's worktree: the exact shape a land takes here.

    The merge commit used to name B alone, as the committer of a commit whose
    whole content is A's branch. Both sessions belong on it, in their own roles:
    B integrated it and decided the conflict, and A wrote the branch it carries.
    """
    branch_commit = "d" * 40
    merger, author = _session("merger", "Orchestrator"), _session("author", "Branch agent")
    manager = cast(Any, SimpleNamespace(sessions={"merger": merger, "author": author}))
    history = HistoryIndex(tmp_path / "mux.db")
    # The ledger already holds A's own commit, which is the only evidence the
    # branch-author derivation reads. Nothing is inferred from a directory, a
    # branch name, or a timestamp.
    await history.record_git_provenance(
        session_id="author",
        session_name="Branch agent",
        agent_run_id="run-a",
        project_id="project-1",
        worktree_root="C:/repo",
        commit_oid=branch_commit,
        relationship="created",
        confidence="exact",
        ambiguous=False,
        source="session_tool",
        evidence_rank=git_provenance.COMMITTER_EXACT_RANK,
        observed_at=5.0,
        role="committer",
        match_method="command_range",
    )
    tier0 = _FakeTier0(
        [
            {
                "id": "f1",
                "project_id": "project-1",
                "session_id": "merger",
                "agent_run_id": "run-b",
                "target": "C:/repo/shared.py",
                "content_hash": None,
                "created_at": 11.5,
            }
        ]
    )
    service = GitProvenanceService(history, manager, EventBus(), cast(Any, tier0))
    merge = _commit(SIBLING, (branch_commit, NEW), 12.0, "Merge branch 'master' into feature")
    _patch_git(
        monkeypatch,
        head=SIBLING,
        commits=(merge, _commit(branch_commit, (OLD,), 6.0, "A's branch work"),
                 _commit(NEW, (OLD,), 11.0, "From master")),
        first_parent=(merge,),
    )

    async def plain_changes(_cwd: str, _oid: str, **_kwargs: Any) -> Any:
        # `diff-tree` says nothing about a merge, and the service must not ask it.
        raise AssertionError("a merge must be read with the combined diff")

    async def resolution(_cwd: str, _oid: str, **_kwargs: Any) -> Any:
        return (GitCommitChange(path="shared.py", status="MM", blob="9" * 40),)

    async def side(_cwd: str, include: str, exclude: tuple[str, ...], **_kwargs: Any) -> Any:
        assert include == branch_commit and exclude == (NEW,)
        return (branch_commit,)

    monkeypatch.setattr(git_provenance, "read_commit_changes", plain_changes)
    monkeypatch.setattr(git_provenance, "read_merge_resolution_changes", resolution)
    monkeypatch.setattr(git_provenance, "read_excluded_range", side)
    monkeypatch.setattr(git_provenance, "read_blob_digest", _no_digest)

    await _run_command(service, "merger", "call-merge", "git merge master")
    rows = {
        (row["session_id"], row["commit_oid"]): row
        for row in await history.git_provenance(project_id="project-1")
    }

    integrator = rows[("merger", SIBLING)]
    assert integrator["role"] == "integrator"
    assert integrator["relationship"] == "merged"
    assert integrator["confidence"] == "exact"
    # Only what the merge itself decided. A's branch content is carried by this
    # commit and is not B's, which is the whole scoping rule.
    assert integrator["contributed_paths"] == ["shared.py"]

    named = rows[("author", SIBLING)]
    assert named["role"] == "branch_author"
    assert named["relationship"] == "authored_branch"
    assert named["confidence"] == "exact"
    assert named["match_method"] == "merge_branch_line"
    assert named["source"] == "ledger_branch_line"
    # No path claim: naming A here must not move A's files onto B's commit or
    # B's onto A's.
    assert named["contributed_paths"] == []

    # A keeps its own commit outright, and B never appears on it.
    assert rows[("author", branch_commit)]["role"] == "committer"
    assert ("merger", branch_commit) not in rows
    assert service.status()["branch_authors"] == 1
    history.close()


async def test_a_merge_of_work_mux_never_saw_names_nobody_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty ledger for the branch side produces no branch author, not a guess.

    Branch authorship is a re-reading of rows mux already wrote. With nothing
    recorded for the commits the merge unified there is no answer, and inventing
    one from the checkout the merge happened in is exactly what this replaces.
    """
    branch_commit = "d" * 40
    session = _session("merger", "Orchestrator")
    manager = cast(Any, SimpleNamespace(sessions={"merger": session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    merge = _commit(SIBLING, (branch_commit, NEW), 12.0, "Merge branch 'master' into feature")
    _patch_git(monkeypatch, head=SIBLING, commits=(merge,), first_parent=(merge,))

    async def side(_cwd: str, _include: str, _exclude: tuple[str, ...], **_kwargs: Any) -> Any:
        return (branch_commit,)

    monkeypatch.setattr(git_provenance, "read_excluded_range", side)

    await _run_command(service, "merger", "call-merge", "git merge master")
    rows = await history.git_provenance(project_id="project-1")

    assert [row["role"] for row in rows] == ["integrator"]
    history.close()


async def test_ff_only_land_records_a_move_and_no_committer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recognized command that authored nothing claims nothing.

    Recognizing `git merge` is not the same as believing every `git merge` made a
    commit. Argv cannot tell a fast-forward from a merge, so the outcome decides.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    landed = (_commit(SIBLING, (NEW,), -9000.0, "Landed"), _commit(NEW, (OLD,), -9100.0, "Base"))
    _patch_git(monkeypatch, head=SIBLING, commits=landed, first_parent=landed)

    await _run_command(
        service, session.record.id, "call-land", "git merge --ff-only worktree-feature"
    )

    assert await history.git_provenance(project_id="project-1") == []
    moves = await history.git_ref_moves(project_id="project-1")
    assert moves[0]["kind"] == "fast_forward"
    history.close()


async def test_rebase_credits_every_commit_it_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replay's commits all belong to the session that ran it.

    There is nothing to disambiguate between them, which is why a run of them is
    recorded rather than reduced to one answer plus an apology.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    replayed = (_commit(SIBLING, (NEW,), 12.0, "Second"), _commit(NEW, (OLD,), 11.0, "First"))
    _patch_git(
        monkeypatch,
        head=SIBLING,
        commits=replayed,
        first_parent=replayed,
        # A rebase leaves the old position unreachable in both directions.
        forward=False,
        backward=False,
    )

    await _run_command(service, session.record.id, "call-rebase", "git rebase master")
    rows = {row["commit_oid"]: row for row in await history.git_provenance(project_id="project-1")}

    assert set(rows) == {NEW, SIBLING}
    assert all(row["role"] == "committer" for row in rows.values())
    assert all(row["relationship"] == "rewrote" for row in rows.values())
    assert all(row["match_method"] == "command_rebased" for row in rows.values())
    moves = await history.git_ref_moves(project_id="project-1")
    assert moves[0]["kind"] == "rebased"
    history.close()


async def test_reset_authors_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(
        monkeypatch,
        head=OLD,
        commits=(_commit(OLD, (), 11.0, "Rewound to"),),
        first_parent=(_commit(OLD, (), 11.0, "Rewound to"),),
        forward=False,
        backward=True,
    )

    await _git_change(service, session.record.id, OLD, NEW)

    assert await history.git_provenance(project_id="project-1") == []
    moves = await history.git_ref_moves(project_id="project-1")
    assert moves[0]["kind"] == "reset"
    history.close()


async def test_bystander_occupancy_is_not_recorded_once_a_committer_is_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answered question does not need eleven more rows saying nothing.

    A commit made in a shared checkout moves every attached session's HEAD. Once
    one of them is known to have run the command, the others' occupancy adds
    nothing — and used to bury the answer under ten rows in the ledger view.
    """
    committer = _session()
    bystander = _session(session_id="bystander", name="Bystander", run_id="run-2")
    manager = cast(
        Any,
        SimpleNamespace(
            sessions={committer.record.id: committer, bystander.record.id: bystander}
        ),
    )
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    made = (_commit(NEW, (OLD,), 11.0, "Add provenance"),)
    _patch_git(monkeypatch, commits=made, first_parent=made)

    await _commit_command(service, committer.record.id, "call-1")
    await _git_change(service, bystander.record.id, NEW, OLD)

    rows = await history.git_provenance(project_id="project-1")
    assert [row["session_id"] for row in rows] == [committer.record.id]
    assert service.status()["suppressed"] == 1
    history.close()


async def test_monitor_records_occupancy_for_a_commit_authored_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Occupancy still counts when nothing else explains the commit.

    A commit made by a form the command recognizer does not match leaves the
    session that was in the checkout as the only evidence there is, and that is
    worth recording — as `correlated` occupancy, never as an answer.
    """
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    made = (_commit(NEW, (OLD,), 11.0, "Committed by a script"),)
    _patch_git(monkeypatch, commits=made, first_parent=made)

    await _git_change(service, session.record.id, NEW, OLD)
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["role"] == "observer"
    assert rows[0]["confidence"] == "correlated"
    assert rows[0]["ambiguous"] is False
    assert rows[0]["match_method"] == "monitor_created"
    history.close()


async def test_retraction_withdraws_a_row_and_stronger_evidence_restores_it(
    tmp_path: Path,
) -> None:
    """The ledger's only weakening operation, and the one thing that undoes it.

    Every other field is gated on `evidence_rank >=`, so before retraction existed
    a row that turned out to record occupancy had no way out: "this session had
    nothing to do with it" is not a stronger claim than the one it replaces.
    """
    history = HistoryIndex(tmp_path / "mux.db")

    async def record(rank: int, role: str, paths: tuple[str, ...] = ()) -> dict[str, Any]:
        return await history.record_git_provenance(
            session_id="session-1",
            session_name="Builder",
            agent_run_id="run-1",
            project_id="project-1",
            worktree_root="C:/repo",
            commit_oid=NEW,
            relationship="observed",
            confidence="correlated",
            ambiguous=False,
            source="git_monitor",
            evidence_rank=rank,
            role=role,
            contributed_paths=paths,
        )

    await record(git_provenance.MONITOR_OBSERVED_RANK, "observer")
    row = (await history.git_provenance(project_id="project-1"))[0]
    assert await history.retract_git_provenance([row["id"]], reason="arrival") == 1
    assert await history.git_provenance(project_id="project-1") == []
    withheld = await history.git_provenance(project_id="project-1", include_retracted=True)
    assert withheld[0]["retracted_reason"] == "arrival"

    # Re-observing the same thing must not undo a repair.
    await record(git_provenance.MONITOR_OBSERVED_RANK, "observer")
    assert await history.git_provenance(project_id="project-1") == []

    # Proof that the session's bytes are in the commit must.
    await record(git_provenance.CONTRIBUTOR_PATH_RANK, "contributor", ("src/one.py",))
    restored = await history.git_provenance(project_id="project-1")
    assert len(restored) == 1
    assert restored[0]["role"] == "contributor"
    assert restored[0]["retracted_at"] is None
    history.close()


def _provenance_request(
    query: MultiDict[str],
    items: list[dict[str, Any]],
    *,
    live: dict[str, Any] | None = None,
    naming_rows: dict[str, dict[str, Any]] | None = None,
    titles: list[dict[str, Any]] | None = None,
    moves: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    """A request whose app carries the three stores identity decoration reads."""

    async def rows(**_kwargs: object) -> list[dict[str, Any]]:
        return items

    async def ref_moves(**_kwargs: object) -> list[dict[str, Any]]:
        return list(moves or [])

    async def naming(ids: object) -> dict[str, dict[str, Any]]:
        return dict(naming_rows or {})

    async def annotations(**_kwargs: object) -> list[dict[str, Any]]:
        return list(titles or [])

    return SimpleNamespace(
        query=query,
        app={
            keys.PROJECTS: SimpleNamespace(projects={"p": SimpleNamespace(id="p")}),
            keys.HISTORY: SimpleNamespace(
                git_provenance=rows,
                git_ref_moves=ref_moves,
                history_naming_rows=naming,
            ),
            keys.SESSIONS: SimpleNamespace(sessions=dict(live or {})),
            keys.AUTOMATION_STORE: SimpleNamespace(annotations=annotations),
        },
    )


async def test_provenance_api_validates_and_filters() -> None:
    captured: dict[str, object] = {}

    async def rows(**kwargs: object) -> list[dict[str, Any]]:
        captured.update(kwargs)
        return [{"commit_oid": NEW, "role": "committer", "confidence": "exact"}]

    request = _provenance_request(MultiDict({"project_id": "p", "session_id": "s"}), [])
    request.app[keys.HISTORY].git_provenance = rows
    response = await server.git_provenance(request)
    payload = json.loads(response.body)

    assert response.status == 200
    assert captured["project_id"] == "p"
    assert captured["session_id"] == "s"
    assert payload["items"][0]["commit_oid"] == NEW
    assert payload["commits"][0]["commit_oid"] == NEW
    assert payload["commits"][0]["attribution"] == "exact"


async def test_provenance_rows_carry_the_current_name_beside_the_recorded_one() -> None:
    """The ledger keeps what the session was called; the reader sees what it is called.

    Three resolutions in one read, because a fleet has all three at once: a live session
    that has since been titled, an ended one whose name lives in History, and one mux has
    no record of at all.
    """
    live_record = SimpleNamespace(
        id="live", name="claude-0e7d93", agent_run_id="run-live", auto_named=True
    )
    request = _provenance_request(
        MultiDict({"project_id": "p"}),
        [
            {
                "commit_oid": NEW, "session_id": "live", "agent_run_id": "run-live",
                "session_name": "claude-0e7d93", "role": "committer", "confidence": "exact",
            },
            {
                "commit_oid": OLD, "session_id": "ended", "agent_run_id": "run-ended",
                "session_name": "claude-aa11bb", "role": "committer", "confidence": "exact",
            },
            {
                "commit_oid": SIBLING, "session_id": "forgotten", "agent_run_id": "",
                "session_name": "claude-ffeedd", "role": "observer", "confidence": "correlated",
            },
        ],
        live={"live": SimpleNamespace(record=live_record)},
        naming_rows={
            "run-ended": {
                "id": "run-ended", "note_id": "ended", "name": "claude-aa11bb", "auto_named": 1
            },
        },
        titles=[
            {"agent_run_id": "run-live", "content": "Fix the parser"},
            {"agent_run_id": "run-ended", "content": "Land the migration"},
        ],
    )
    payload = json.loads((await server.git_provenance(request)).body)
    by_commit = {item["commit_oid"]: item for item in payload["items"]}

    assert by_commit[NEW]["display_name"] == "Fix the parser"
    assert by_commit[NEW]["history_id"] == "run-live"
    assert by_commit[OLD]["display_name"] == "Land the migration"
    assert by_commit[OLD]["history_id"] == "run-ended"
    # No live session and no History row: the recorded name is all there is, and the row
    # names no conversation to open rather than pointing at one that does not exist.
    assert by_commit[SIBLING]["display_name"] == "claude-ffeedd"
    assert "history_id" not in by_commit[SIBLING]
    # The snapshot is evidence and is never rewritten by the resolution.
    assert by_commit[NEW]["session_name"] == "claude-0e7d93"


async def test_a_renamed_session_keeps_its_name_over_a_later_generated_title() -> None:
    request = _provenance_request(
        MultiDict({"project_id": "p"}),
        [
            {
                "commit_oid": NEW, "session_id": "live", "agent_run_id": "run-live",
                "session_name": "release prep", "role": "committer", "confidence": "exact",
            },
            {
                "commit_oid": OLD, "session_id": "ended", "agent_run_id": "run-ended",
                "session_name": "hand-named", "role": "committer", "confidence": "exact",
            },
        ],
        live={
            "live": SimpleNamespace(
                record=SimpleNamespace(
                    id="live", name="release prep", agent_run_id="run-live", auto_named=False
                )
            )
        },
        naming_rows={
            "run-ended": {
                "id": "run-ended", "note_id": "ended", "name": "hand-named", "auto_named": 0
            },
        },
        titles=[
            {"agent_run_id": "run-live", "content": "Fix the parser"},
            {"agent_run_id": "run-ended", "content": "Land the migration"},
        ],
    )
    payload = json.loads((await server.git_provenance(request)).body)

    assert [item["display_name"] for item in payload["items"]] == ["release prep", "hand-named"]
