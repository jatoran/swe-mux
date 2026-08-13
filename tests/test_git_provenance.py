from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from multidict import MultiDict

from swe_mux import git_provenance, server
from swe_mux.event_bus import EventBus
from swe_mux.git_monitor import GitCommitMetadata, GitPosition
from swe_mux.git_provenance import GitProvenanceService, classify_git_commit_command
from swe_mux.history import HistoryIndex
from swe_mux.models import GitState, MuxEvent

OLD = "a" * 40
NEW = "b" * 40


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
        evidence_rank=50,
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


async def test_successful_session_git_commit_records_exact_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())

    async def current(_cwd: str) -> GitPosition:
        return GitPosition("C:/repo", NEW)

    async def metadata(_cwd: str, oid: str) -> GitCommitMetadata:
        assert oid == NEW
        return GitCommitMetadata(NEW, (OLD,), 11.0, "Add provenance")

    monkeypatch.setattr(git_provenance, "read_git_position", current)
    monkeypatch.setattr(git_provenance, "read_commit_metadata", metadata)
    await service.handle_event(
        MuxEvent(
            1.0,
            session.record.id,
            "transcript",
            "tool_use",
            {"tool": "shell_command", "call_id": "call-1", "target": "git commit -m x"},
            1,
        )
    )
    await service.handle_event(
        MuxEvent(
            2.0,
            session.record.id,
            "transcript",
            "tool_result",
            {"tool": "shell_command", "call_id": "call-1", "success": True},
            2,
        )
    )
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["commit_oid"] == NEW
    assert rows[0]["parent_oids"] == [OLD]
    assert rows[0]["relationship"] == "created"
    assert rows[0]["confidence"] == "exact"
    history.close()


async def test_failed_commit_and_unchanged_head_record_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())

    async def unchanged(_cwd: str) -> GitPosition:
        return GitPosition("C:/repo", OLD)

    monkeypatch.setattr(git_provenance, "read_git_position", unchanged)
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


async def test_shared_checkout_never_claims_exact_authorship(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _session("one"), _session("two")
    manager = cast(Any, SimpleNamespace(sessions={"one": first, "two": second}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())

    async def current(_cwd: str) -> GitPosition:
        return GitPosition("C:/repo", NEW)

    async def metadata(_cwd: str, _oid: str) -> None:
        return None

    monkeypatch.setattr(git_provenance, "read_git_position", current)
    monkeypatch.setattr(git_provenance, "read_commit_metadata", metadata)
    await service.handle_event(
        MuxEvent(
            1.0,
            "one",
            "transcript",
            "tool_use",
            {"tool": "Bash", "call_id": "c", "target": "git commit -m x"},
        )
    )
    await service.handle_event(
        MuxEvent(2.0, "one", "transcript", "tool_result", {"call_id": "c", "success": True})
    )
    rows = await history.git_provenance(project_id="project-1")
    assert rows[0]["ambiguous"] is True
    assert rows[0]["confidence"] == "ambiguous"
    history.close()


async def test_monitor_head_transition_records_correlated_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session()
    session.record.git = GitState(root="C:/repo", head=NEW)
    manager = cast(Any, SimpleNamespace(sessions={session.record.id: session}))
    history = HistoryIndex(tmp_path / "mux.db")
    service = GitProvenanceService(history, manager, EventBus())

    async def metadata(_cwd: str, _oid: str) -> None:
        return None

    monkeypatch.setattr(git_provenance, "read_commit_metadata", metadata)
    await service.handle_event(
        MuxEvent(
            2.0,
            session.record.id,
            "daemon",
            "git_changed",
            {
                "git": {"root": "C:/repo"},
                "head": NEW,
                "previous_head": OLD,
            },
            9,
        )
    )
    rows = await history.git_provenance(project_id="project-1")

    assert len(rows) == 1
    assert rows[0]["relationship"] == "observed"
    assert rows[0]["confidence"] == "correlated"
    assert rows[0]["source_event_seq"] == 9
    history.close()


async def test_provenance_api_validates_and_filters() -> None:
    history = SimpleNamespace(
        git_provenance=lambda **_kwargs: None,
    )

    async def rows(**kwargs: object) -> list[dict[str, object]]:
        assert kwargs["project_id"] == "p"
        assert kwargs["session_id"] == "s"
        return [{"commit_oid": NEW}]

    history.git_provenance = rows
    request = SimpleNamespace(
        query=MultiDict({"project_id": "p", "session_id": "s"}),
        app={
            "projects": SimpleNamespace(projects={"p": SimpleNamespace(id="p")}),
            "history": history,
        },
    )
    response = await server.git_provenance(request)
    assert response.status == 200
    assert json.loads(response.body) == {"items": [{"commit_oid": NEW}]}
