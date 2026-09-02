from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux import git_operations, worktree_mutation
from swe_mux.bounded_subprocess import ProcessOutcome
from swe_mux.git_operations import GitMutationResult
from swe_mux.routes import git as git_routes


def _outcome(**overrides: Any) -> ProcessOutcome:
    fields: dict[str, Any] = {
        "exit_code": 0,
        "stdout": b"",
        "stderr": b"",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "duration_ms": 1.0,
        "timed_out": False,
    }
    fields.update(overrides)
    return ProcessOutcome(**fields)


@pytest.mark.asyncio
async def test_git_mutation_timeout_is_reported_as_124(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reap itself is the bounded runner's contract (`test_bounded_subprocess`);
    what this module owes is the mapping, on the interactive lane."""
    seen: list[dict[str, Any]] = []

    async def fake_run_bounded(argv: Any, **kwargs: Any) -> ProcessOutcome:
        seen.append({"argv": tuple(argv), **kwargs})
        return _outcome(exit_code=None, timed_out=True)

    monkeypatch.setattr(git_operations, "run_bounded", fake_run_bounded)
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
    assert seen[0]["argv"] == ("git", "-C", "C:/repo", "worktree", "remove", "C:/repo/wt")
    assert seen[0]["lane"] == "interactive", "a person is waiting on this one"
    assert seen[0]["operation_id"] == "op-timeout"


@pytest.mark.asyncio
async def test_git_mutation_reads_stderr_on_failure_and_stdout_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(
        [
            _outcome(exit_code=0, stdout=b"Preparing worktree\n", stderr=b"noise"),
            _outcome(exit_code=128, stdout=b"", stderr=b"fatal: already exists\n"),
        ]
    )

    async def fake_run_bounded(argv: Any, **kwargs: Any) -> ProcessOutcome:
        return next(answers)

    monkeypatch.setattr(git_operations, "run_bounded", fake_run_bounded)
    ok = await git_operations.run_git_mutation(
        "C:/repo", "worktree", "add", "x", operation="worktree_add", operation_id="a"
    )
    failed = await git_operations.run_git_mutation(
        "C:/repo", "worktree", "add", "x", operation="worktree_add", operation_id="b"
    )
    assert (ok.code, ok.output) == (0, "Preparing worktree")
    assert (failed.code, failed.output) == (128, "fatal: already exists")


@pytest.mark.asyncio
async def test_client_cancellation_does_not_cancel_git_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()
    finished = asyncio.Event()

    async def fake_run_bounded(argv: Any, **kwargs: Any) -> ProcessOutcome:
        await release.wait()
        finished.set()
        return _outcome(stdout=b"done")

    monkeypatch.setattr(git_operations, "run_bounded", fake_run_bounded)
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
    monkeypatch.setattr(worktree_mutation, "run_git_mutation", mutate)
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
    monkeypatch.setattr(worktree_mutation, "run_git_mutation", mutate)
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
    monkeypatch.setattr(worktree_mutation, "run_git_mutation", mutate)
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
    monkeypatch.setattr(worktree_mutation, "run_git_mutation", mutate)
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
