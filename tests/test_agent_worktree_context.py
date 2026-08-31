from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import agent_worktree_context
from swe_mux.agent_worktree_context import (
    WorktreeContextRefusal,
    bound_worktree_root,
    resolve_land_worktree,
    session_occupies_worktree,
    use_worktree,
    worktree_context,
)
from swe_mux.models import SessionRecord


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, Path]:
    primary = tmp_path / "repo"
    primary.mkdir()
    _git(primary, "init", "-b", "master")
    _git(primary, "config", "user.email", "test@example.com")
    _git(primary, "config", "user.name", "Test User")
    (primary / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(primary, "add", "tracked.txt")
    _git(primary, "commit", "-m", "base")
    worktree = tmp_path / "feature"
    _git(primary, "worktree", "add", "-b", "feature", str(worktree))
    return primary, worktree


class Caller:
    def __init__(self, record: SessionRecord) -> None:
        self.record = record
        self.updates = 0

    def publish_update(self) -> None:
        self.updates += 1


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.items.append((event_type, payload))


def _caller(cwd: Path, *, sid: str = "s1", run_id: str = "run-1") -> Caller:
    return Caller(
        SessionRecord(
            id=sid,
            name=sid,
            project_id="p1",
            backend="codex",
            native_session_id=sid,
            cwd=str(cwd),
            exe="codex",
            args=[],
            spawn_cwd=str(cwd),
            agent_run_id=run_id,
            state="idle",
        )
    )


def _sessions(*callers: Caller) -> SimpleNamespace:
    return SimpleNamespace(
        sessions={caller.record.id: caller for caller in callers},
        events=Events(),
    )


@pytest.mark.asyncio
async def test_live_linked_worktree_remains_authoritative_for_claude(
    repository: tuple[Path, Path], tmp_path: Path
) -> None:
    primary, live_worktree = repository
    selected_worktree = tmp_path / "selected"
    _git(primary, "worktree", "add", "-b", "selected", str(selected_worktree))
    caller = _caller(live_worktree)
    sessions = _sessions(caller)

    await use_worktree(caller, str(primary), str(selected_worktree), sessions)
    context = await worktree_context(caller, str(primary), sessions)
    resolved = await resolve_land_worktree(caller, str(primary), sessions)

    assert context["source"] == "live_cwd"
    assert Path(context["worktree_root"]) == live_worktree
    assert Path(context["binding"]["worktree_root"]) == selected_worktree
    assert Path(resolved.root) == live_worktree
    assert resolved.source == "live_cwd"


@pytest.mark.asyncio
async def test_primary_cwd_uses_a_run_bound_codex_selection_and_persists_it(
    repository: tuple[Path, Path]
) -> None:
    primary, worktree = repository
    caller = _caller(primary)
    sessions = _sessions(caller)

    context = await use_worktree(caller, str(primary), str(worktree), sessions)
    assert context["source"] == "bound"
    assert Path(context["worktree_root"]) == worktree
    assert context["branch"] == "feature"
    assert caller.updates == 1
    assert sessions.events.items[0][0] == "agent_worktree_bound"

    restored = Caller(SessionRecord.from_snapshot(caller.record.snapshot()))
    restored_sessions = _sessions(restored)
    resolved = await resolve_land_worktree(restored, str(primary), restored_sessions)
    assert Path(resolved.root) == worktree
    assert resolved.branch == "feature"
    assert resolved.source == "bound"


@pytest.mark.asyncio
async def test_primary_cwd_without_a_selection_gets_actionable_context(
    repository: tuple[Path, Path]
) -> None:
    primary, _worktree = repository
    caller = _caller(primary)
    sessions = _sessions(caller)

    context = await worktree_context(caller, str(primary), sessions)
    assert context["source"] == "primary_cwd"
    assert context["landable"] is False
    assert context["code"] == "worktree_context_required"
    assert "use_worktree" in context["message"]
    with pytest.raises(WorktreeContextRefusal) as caught:
        await resolve_land_worktree(caller, str(primary), sessions)
    assert caught.value.code == "worktree_context_required"


@pytest.mark.asyncio
async def test_project_registered_inside_primary_checkout_still_resolves_trunk_correctly(
    repository: tuple[Path, Path]
) -> None:
    primary, worktree = repository
    project_root = primary / "nested-project"
    project_root.mkdir()
    caller = _caller(primary)
    sessions = _sessions(caller)

    context = await worktree_context(caller, str(project_root), sessions)
    assert context["code"] == "worktree_context_required"
    with pytest.raises(WorktreeContextRefusal) as caught:
        await use_worktree(caller, str(project_root), str(primary), sessions)
    assert caught.value.code == "primary_checkout"

    selected = await use_worktree(caller, str(project_root), str(worktree), sessions)
    assert selected["source"] == "bound"


@pytest.mark.asyncio
async def test_selection_refuses_primary_detached_unlisted_and_live_owned_targets(
    repository: tuple[Path, Path], tmp_path: Path
) -> None:
    primary, worktree = repository
    caller = _caller(primary)
    other = _caller(worktree, sid="other")
    sessions = _sessions(caller, other)

    with pytest.raises(WorktreeContextRefusal) as primary_refusal:
        await use_worktree(caller, str(primary), str(primary), sessions)
    assert primary_refusal.value.code == "primary_checkout"

    with pytest.raises(WorktreeContextRefusal) as owned_refusal:
        await use_worktree(caller, str(primary), str(worktree), sessions)
    assert owned_refusal.value.code == "worktree_in_use"

    detached = tmp_path / "detached"
    _git(primary, "worktree", "add", "--detach", str(detached))
    with pytest.raises(WorktreeContextRefusal) as detached_refusal:
        await use_worktree(caller, str(primary), str(detached), _sessions(caller))
    assert detached_refusal.value.code == "detached_worktree"
    detached_caller = _caller(detached, sid="detached")
    detached_context = await worktree_context(
        detached_caller, str(primary), _sessions(detached_caller)
    )
    assert detached_context["landable"] is False
    assert detached_context["code"] == "detached_worktree"

    with pytest.raises(WorktreeContextRefusal) as absent_refusal:
        await use_worktree(caller, str(primary), str(tmp_path / "absent"), _sessions(caller))
    assert absent_refusal.value.code == "worktree_not_found"


@pytest.mark.asyncio
async def test_changed_branch_and_changed_run_invalidate_the_selection(
    repository: tuple[Path, Path]
) -> None:
    primary, worktree = repository
    caller = _caller(primary)
    sessions = _sessions(caller)
    await use_worktree(caller, str(primary), str(worktree), sessions)

    caller.record.agent_run_id = "run-2"
    expired = await worktree_context(caller, str(primary), sessions)
    assert expired["code"] == "worktree_binding_expired"
    assert bound_worktree_root(caller.record) == ""

    caller.record.agent_run_id = "run-1"
    _git(worktree, "switch", "-c", "changed")
    stale = await worktree_context(caller, str(primary), sessions)
    assert stale["code"] == "worktree_binding_stale"


@pytest.mark.asyncio
async def test_clearing_selection_is_persisted_and_stops_claiming_the_worktree(
    repository: tuple[Path, Path]
) -> None:
    primary, worktree = repository
    caller = _caller(primary)
    sessions = _sessions(caller)
    await use_worktree(caller, str(primary), str(worktree), sessions)
    assert session_occupies_worktree(caller.record, str(worktree))

    context = await use_worktree(caller, str(primary), None, sessions)
    assert context["code"] == "worktree_context_required"
    assert context["binding"] is None
    assert not session_occupies_worktree(caller.record, str(worktree))
    assert caller.updates == 2
    assert [item[0] for item in sessions.events.items] == [
        "agent_worktree_bound",
        "agent_worktree_unbound",
    ]


@pytest.mark.asyncio
async def test_clearing_selection_succeeds_when_git_can_no_longer_read_it(
    repository: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, worktree = repository
    caller = _caller(primary)
    sessions = _sessions(caller)
    await use_worktree(caller, str(primary), str(worktree), sessions)

    async def unreadable(_root: str) -> dict[str, dict[str, Any]]:
        raise ValueError("registry locked")

    monkeypatch.setattr(agent_worktree_context, "listed_worktree_entries", unreadable)
    result = await use_worktree(caller, str(primary), None, sessions)
    assert result["source"] == "unavailable"
    assert result["binding"] is None
    assert caller.record.land_worktree_root is None
    assert caller.updates == 2
