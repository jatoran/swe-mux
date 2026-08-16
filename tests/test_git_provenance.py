from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from multidict import MultiDict

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
        _write_fact("f1", "writer", "C:/repo/src/one.py", 10.0, content_hash="digest-one"),
        _write_fact("f2", "early", "C:/repo/src/two.py", 11.0),
        _write_fact("f3", "late", "C:/repo/src/two.py", 12.0),
        _write_fact("f4", "elsewhere", "C:/other/src/one.py", 12.5),
    ]
    candidates = candidate_writes(
        changes, facts, worktree_root="C:/repo", session_roots={}
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


def test_contributor_join_places_a_relative_write_by_its_session_checkout() -> None:
    changes = (GitCommitChange(path="src/one.py", status="M", blob="1" * 40),)
    facts = [
        _write_fact("f1", "codex", "src/one.py", 10.0),
        _write_fact("f2", "stranger", "src/one.py", 11.0),
    ]

    unplaced = candidate_writes(changes, facts, worktree_root="C:/repo", session_roots={})
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


def _session(session_id: str = "session-1") -> Any:
    record = SimpleNamespace(
        id=session_id,
        name="Builder",
        agent_run_id="run-1",
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
) -> None:
    async def position(_cwd: str) -> GitPosition:
        return GitPosition("C:/repo", head)

    async def commit_range(_cwd: str, _base: str | None, _head: str, **_kwargs: Any) -> Any:
        return commits

    async def metadata(_cwd: str, oid: str) -> GitCommitMetadata | None:
        return next((item for item in commits if item.oid == oid), None)

    monkeypatch.setattr(git_provenance, "read_git_position", position)
    monkeypatch.setattr(git_provenance, "read_commit_range", commit_range)
    monkeypatch.setattr(git_provenance, "read_commit_metadata", metadata)


async def _commit_command(service: GitProvenanceService, session_id: str, call_id: str) -> None:
    await service.handle_event(
        MuxEvent(
            1.0,
            session_id,
            "transcript",
            "tool_use",
            {"tool": "Bash", "call_id": call_id, "target": 'git commit -m "Add provenance"'},
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


async def test_monitor_bulk_reference_move_is_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())
    _patch_git(
        monkeypatch,
        commits=(_commit(SIBLING, (NEW,), 12.0, "Merged"), _commit(NEW, (OLD,), 11.0, "Base")),
    )

    await service.handle_event(
        MuxEvent(
            2.0,
            session.record.id,
            "daemon",
            "git_changed",
            {"git": {"root": "C:/repo"}, "head": SIBLING, "previous_head": OLD},
            9,
        )
    )
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["confidence"] == "ambiguous"
    assert rows[0]["match_method"] == "monitor_range"
    history.close()


async def test_provenance_api_validates_and_filters() -> None:
    history = SimpleNamespace(
        git_provenance=lambda **_kwargs: None,
    )

    async def rows(**kwargs: object) -> list[dict[str, object]]:
        assert kwargs["project_id"] == "p"
        assert kwargs["session_id"] == "s"
        return [{"commit_oid": NEW, "role": "committer", "confidence": "exact"}]

    history.git_provenance = rows
    request = SimpleNamespace(
        query=MultiDict({"project_id": "p", "session_id": "s"}),
        app={
            "projects": SimpleNamespace(projects={"p": SimpleNamespace(id="p")}),
            "history": history,
        },
    )
    response = await server.git_provenance(request)
    payload = json.loads(response.body)

    assert response.status == 200
    assert payload["items"] == [{"commit_oid": NEW, "role": "committer", "confidence": "exact"}]
    assert payload["commits"][0]["commit_oid"] == NEW
    assert payload["commits"][0]["attribution"] == "exact"
