from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux import git_operations, worktree_mutation
from swe_mux.git_operations import GitMutationResult
from swe_mux.routes import git as git_routes


@pytest.mark.asyncio
async def test_git_mutation_timeout_reaps_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowProcess:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.Event().wait()
            return b"", b""

    process = SlowProcess()
    reaped = 0

    async def spawn(*args: object, **kwargs: object) -> SlowProcess:
        del args, kwargs
        return process

    async def reap(target: object) -> None:
        nonlocal reaped
        assert target is process
        reaped += 1
        process.returncode = -9

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(git_operations, "reap_process_tree", reap)
    result = await git_operations.run_git_mutation(
        "C:/repo",
        "worktree",
        "remove",
        "C:/repo/wt",
        operation="worktree_remove",
        operation_id="op-timeout",
        timeout_seconds=0.01,
    )
    assert result.timed_out is True
    assert result.code == 124
    assert reaped == 1


@pytest.mark.asyncio
async def test_client_cancellation_does_not_cancel_git_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    finished = asyncio.Event()

    class SlowProcess:
        returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            await release.wait()
            self.returncode = 0
            finished.set()
            return b"done", b""

    process = SlowProcess()

    async def spawn(*args: object, **kwargs: object) -> SlowProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    caller = asyncio.create_task(
        git_operations.run_git_mutation(
            "C:/repo",
            "worktree",
            "remove",
            "C:/repo/wt",
            operation="worktree_remove",
            operation_id="op-cancel",
        )
    )
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert git_operations._active_mutations
    release.set()
    await asyncio.wait_for(finished.wait(), 1)
    await asyncio.sleep(0)
    assert not git_operations._active_mutations


class FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **payload: Any) -> None:
        self.emitted.append((name, payload))


def request(body: dict[str, Any], events: FakeEvents) -> Any:
    async def read_json() -> dict[str, Any]:
        return body

    return SimpleNamespace(json=read_json, app={keys.EVENTS: events})


@pytest.mark.asyncio
async def test_prunable_remove_repairs_exact_path_then_forces_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree = tmp_path / "broken"
    worktree.mkdir()
    calls = 0

    async def listed(cwd: str) -> dict[str, dict[str, Any]]:
        nonlocal calls
        assert cwd == str(tmp_path)
        calls += 1
        entry: dict[str, Any] = {"worktree": str(worktree)}
        if calls == 1:
            entry["prunable"] = "gitdir file points to non-existent location"
        return {str(worktree.resolve()).casefold(): entry}

    mutations: list[tuple[tuple[str, ...], str, str]] = []

    async def mutate(
        cwd: str, *args: str, operation: str, operation_id: str
    ) -> GitMutationResult:
        assert cwd == str(tmp_path)
        mutations.append((args, operation, operation_id))
        if operation == "worktree_repair":
            (worktree / ".git").write_text("gitdir: fake\n", encoding="utf-8")
        return GitMutationResult(0, "")

    async def root_matches(path: str, expected: str) -> bool:
        assert path == expected == str(worktree)
        return True

    monkeypatch.setattr(worktree_mutation, "listed_worktree_entries", listed)
    monkeypatch.setattr(git_routes, "run_git_mutation", mutate)
    monkeypatch.setattr(worktree_mutation, "worktree_root_matches", root_matches)
    events = FakeEvents()
    response = await git_routes.remove_worktree(
        request(
            {"cwd": str(tmp_path), "path": str(worktree), "force": True},
            events,
        )
    )
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["repaired"] is True
    assert mutations[0][0] == ("worktree", "repair", str(worktree))
    assert mutations[1][0] == ("worktree", "remove", "--force", str(worktree))
    assert mutations[0][2] == mutations[1][2] == payload["operation_id"]
    assert events.emitted == [
        (
            "worktree_removed",
            {"source": "user", "cwd": str(tmp_path), "path": str(worktree)},
        )
    ]


@pytest.mark.asyncio
async def test_repaired_dirty_worktree_requires_explicit_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree = tmp_path / "broken"
    worktree.mkdir()
    calls = 0

    async def listed(cwd: str) -> dict[str, dict[str, Any]]:
        nonlocal calls
        del cwd
        calls += 1
        entry: dict[str, Any] = {"worktree": str(worktree)}
        if calls == 1:
            entry["prunable"] = "missing gitdir"
        return {str(worktree.resolve()).casefold(): entry}

    async def mutate(
        cwd: str, *args: str, operation: str, operation_id: str
    ) -> GitMutationResult:
        del cwd, args, operation_id
        if operation == "worktree_repair":
            (worktree / ".git").write_text("gitdir: fake\n", encoding="utf-8")
            return GitMutationResult(0, "")
        return GitMutationResult(1, "working tree contains modified or untracked files")

    async def root_matches(path: str, expected: str) -> bool:
        assert path == expected == str(worktree)
        return True

    monkeypatch.setattr(worktree_mutation, "listed_worktree_entries", listed)
    monkeypatch.setattr(git_routes, "run_git_mutation", mutate)
    monkeypatch.setattr(worktree_mutation, "worktree_root_matches", root_matches)
    response = await git_routes.remove_worktree(
        request({"cwd": str(tmp_path), "path": str(worktree)}, FakeEvents())
    )
    payload = json.loads(response.text)
    assert response.status == 400
    assert payload["code"] == "git_error"
    assert payload["repaired"] is True


@pytest.mark.asyncio
async def test_nonzero_repair_continues_when_post_state_is_exact_and_usable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree = tmp_path / "broken"
    worktree.mkdir()
    calls = 0

    async def listed(cwd: str) -> dict[str, dict[str, Any]]:
        nonlocal calls
        assert cwd == str(tmp_path)
        calls += 1
        entry: dict[str, Any] = {"worktree": str(worktree)}
        if calls == 1:
            entry["prunable"] = "missing gitdir"
        return {str(worktree.resolve()).casefold(): entry}

    mutations: list[str] = []

    async def mutate(
        cwd: str, *args: str, operation: str, operation_id: str
    ) -> GitMutationResult:
        del cwd, args, operation_id
        mutations.append(operation)
        if operation == "worktree_repair":
            (worktree / ".git").write_text("gitdir: fake\n", encoding="utf-8")
            return GitMutationResult(1, "repair reported another broken worktree")
        return GitMutationResult(0, "")

    async def root_matches(path: str, expected: str) -> bool:
        assert path == expected == str(worktree)
        return True

    monkeypatch.setattr(worktree_mutation, "listed_worktree_entries", listed)
    monkeypatch.setattr(git_routes, "run_git_mutation", mutate)
    monkeypatch.setattr(worktree_mutation, "worktree_root_matches", root_matches)
    response = await git_routes.remove_worktree(
        request(
            {"cwd": str(tmp_path), "path": str(worktree), "force": True},
            FakeEvents(),
        )
    )
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["repaired"] is True
    assert mutations == ["worktree_repair", "worktree_remove"]


@pytest.mark.asyncio
async def test_remove_quarantines_orphan_after_git_drops_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    worktree = tmp_path / "unsupported-reparse-tree"
    worktree.mkdir()
    (worktree / "leftover.txt").write_text("survived Git cleanup\n", encoding="utf-8")
    calls = 0

    async def listed(cwd: str) -> dict[str, dict[str, Any]]:
        nonlocal calls
        assert cwd == str(tmp_path)
        calls += 1
        if calls == 1:
            return {
                str(worktree.resolve()).casefold(): {"worktree": str(worktree)}
            }
        return {}

    async def mutate(
        cwd: str, *args: str, operation: str, operation_id: str
    ) -> GitMutationResult:
        del args, operation_id
        assert cwd == str(tmp_path)
        assert operation == "worktree_remove"
        return GitMutationResult(
            255,
            f"error: failed to delete '{worktree}': Function not implemented",
        )

    monkeypatch.setattr(worktree_mutation, "listed_worktree_entries", listed)
    monkeypatch.setattr(git_routes, "run_git_mutation", mutate)
    events = FakeEvents()
    response = await git_routes.remove_worktree(
        request({"cwd": str(tmp_path), "path": str(worktree)}, events)
    )
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["cleanup"]["status"] == "quarantined"
    quarantined = Path(payload["cleanup"]["path"])
    assert not worktree.exists()
    assert (quarantined / "leftover.txt").read_text(encoding="utf-8") == (
        "survived Git cleanup\n"
    )
    assert events.emitted == [
        (
            "worktree_removed",
            {"source": "user", "cwd": str(tmp_path), "path": str(worktree)},
        )
    ]
