"""Spawning a session into a freshly created git worktree.

The contract that matters here is failure containment: `POST /api/git/worktrees` creates
the worktree first, and the worktree is the durable artefact. A spawn that is rejected or
that blows up must be *reported*, never raised, or the caller would see the whole request
fail and be unable to tell that the worktree exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import server
from swe_mux.config import Config
from swe_mux.worktree_setup import WorktreeSetupResult

# The helper never touches the app on any path under test; typed Any so mypy accepts it.
APP: Any = SimpleNamespace()


@pytest.fixture
def worktree(tmp_path: Path) -> str:
    path = tmp_path / ".worktrees" / "repo" / "agent-a"
    path.mkdir(parents=True)
    return str(path)


@pytest.mark.asyncio
async def test_spawn_is_skipped_when_the_body_is_not_an_object(worktree: str) -> None:
    result = await server._spawn_into_worktree(APP, "claude", worktree)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_spawn_requires_a_project_id(worktree: str) -> None:
    result = await server._spawn_into_worktree(APP, {"backend": "claude"}, worktree)
    assert result["status"] == "error"
    assert "project_id" in result["error"]


@pytest.mark.asyncio
async def test_cwd_is_forced_to_the_new_worktree(
    monkeypatch: pytest.MonkeyPatch, worktree: str
) -> None:
    seen: dict[str, Any] = {}

    async def fake_spawn(app: Any, body: dict[str, Any]) -> Any:
        seen.update(body)
        return SimpleNamespace(
            record=SimpleNamespace(id="sess-1", snapshot=lambda: {"id": "sess-1", "cwd": worktree})
        )

    monkeypatch.setattr(server, "_spawn_from_body", fake_spawn)
    # A caller trying to redirect the session elsewhere must not be able to.
    result = await server._spawn_into_worktree(
        APP, {"project_id": "p1", "cwd": "C:/somewhere/else"}, worktree
    )
    assert result == {
        "status": "spawned",
        "session_id": "sess-1",
        "cwd": worktree,
        "session": {"id": "sess-1", "cwd": worktree},
    }
    assert seen["cwd"] == worktree
    assert seen["project_id"] == "p1"


@pytest.mark.asyncio
async def test_a_rejected_spawn_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, worktree: str
) -> None:
    async def refuse(app: Any, body: dict[str, Any]) -> Any:
        raise ValueError("unknown project: p1")

    monkeypatch.setattr(server, "_spawn_from_body", refuse)
    result = await server._spawn_into_worktree(APP, {"project_id": "p1"}, worktree)
    assert result["status"] == "error"
    assert "unknown project" in result["error"]
    assert Path(worktree).is_dir(), "the worktree must survive a rejected spawn"


@pytest.mark.asyncio
async def test_setup_output_is_seeded_before_spawn_and_failure_does_not_block_session(
    monkeypatch: pytest.MonkeyPatch, worktree: str
) -> None:
    seen: dict[str, Any] = {}

    async def fake_spawn(
        app: Any, body: dict[str, Any], *, initial_output: bytes | None = None
    ) -> Any:
        del app, body
        seen["initial_output"] = initial_output
        return SimpleNamespace(
            record=SimpleNamespace(id="sess-1", snapshot=lambda: {"id": "sess-1"})
        )

    monkeypatch.setattr(server, "_spawn_from_body", fake_spawn)
    setup = WorktreeSetupResult(
        "failed", "convention", ".worktree-setup", 7, 50, b"dependency error\n"
    )

    result = await server._spawn_into_worktree(
        APP, {"project_id": "p1", "backend": "claude"}, worktree, setup
    )

    assert result["status"] == "spawned"
    assert result["setup"]["status"] == "failed"
    assert b"dependency error" in seen["initial_output"]
    assert b"not bootstrapped" in seen["initial_output"]


@pytest.mark.asyncio
async def test_an_unexpected_failure_still_leaves_the_worktree(
    monkeypatch: pytest.MonkeyPatch, worktree: str
) -> None:
    async def explode(app: Any, body: dict[str, Any]) -> Any:
        raise RuntimeError("pty supervisor unreachable")

    monkeypatch.setattr(server, "_spawn_from_body", explode)
    result = await server._spawn_into_worktree(APP, {"project_id": "p1"}, worktree)
    assert result["status"] == "error"
    assert "RuntimeError" in result["error"]
    assert Path(worktree).is_dir(), "the worktree must survive an unexpected spawn failure"


def test_missing_parents_are_created_below_the_configured_worktree_root(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path / "data")
    target = config.resolved_worktree_root / "project-12345678" / "feature"

    server._ensure_worktree_parent(config, target)

    assert target.parent.is_dir()


def test_missing_parents_outside_the_configured_worktree_root_are_refused(
    tmp_path: Path,
) -> None:
    config = Config(data_dir=tmp_path / "data")
    target = tmp_path / "other" / "project" / "feature"

    with pytest.raises(ValueError, match="parent directory does not exist"):
        server._ensure_worktree_parent(config, target)
    assert not target.parent.exists()


@pytest.mark.asyncio
async def test_existing_worktree_session_endpoint_runs_setup_before_spawn(
    monkeypatch: pytest.MonkeyPatch, worktree: str
) -> None:
    calls: list[str] = []
    setup = WorktreeSetupResult("succeeded", "convention", ".worktree-setup", 0, 20)

    async def prepare(app: Any, spawn_body: Any, path: str) -> WorktreeSetupResult:
        del app
        assert spawn_body == {"project_id": "p1", "backend": "claude"}
        assert path == str(Path(worktree).resolve())
        calls.append("setup")
        return setup

    async def spawn(
        app: Any, spawn_body: Any, path: str, prepared: WorktreeSetupResult
    ) -> dict[str, Any]:
        del app, spawn_body, path
        assert prepared is setup
        calls.append("spawn")
        return {"status": "spawned", "session_id": "sess-1"}

    class Request:
        app: Any = APP

        async def json(self) -> dict[str, Any]:
            return {
                "path": worktree,
                "spawn": {"project_id": "p1", "backend": "claude"},
            }

    monkeypatch.setattr(server, "_prepare_worktree_setup", prepare)
    monkeypatch.setattr(server, "_spawn_into_worktree", spawn)

    response = await server.spawn_worktree_session(Request())  # type: ignore[arg-type]

    assert calls == ["setup", "spawn"]
    assert json.loads(response.text)["session_id"] == "sess-1"
