"""Worktree creation from a repository with no commits fails with a named fix.

A freshly initialized repository (`POST /api/git/init` deliberately makes no
commit) has an unborn HEAD, and `git worktree add` answers that with a raw
``fatal: invalid reference: HEAD``. The endpoint refuses first, with a typed
code and a message naming the actual fix, and never over-blocks a repository
that has commits.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from swe_mux import server
from swe_mux.config import load_config


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


class Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **payload: Any) -> None:
        self.emitted.append((name, payload))


class Request:
    def __init__(self, tmp_path: Path, body: Any) -> None:
        self._body = body
        self.app = {
            "config": load_config(tmp_path / "config.toml"),
            "events": Events(),
        }

    async def json(self) -> Any:
        return self._body


@pytest.mark.asyncio
async def test_worktree_create_names_the_missing_first_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    target = tmp_path / "trees" / "wt-a"
    target.parent.mkdir()

    request = Request(tmp_path, {"cwd": str(repo), "path": str(target)})
    response = await server.create_worktree(request)  # type: ignore[arg-type]
    assert response.status == 400
    payload = json.loads(response.text)
    assert payload["code"] == "repository_has_no_commits"
    assert "first commit" in payload["error"]
    assert not target.exists()

    # With a commit the same request goes through: the guard names the unborn
    # state and nothing else.
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "init")
    response = await server.create_worktree(
        Request(tmp_path, {"cwd": str(repo), "path": str(target), "branch": "wt-a"})
    )  # type: ignore[arg-type]
    assert response.status == 201
    assert json.loads(response.text)["ok"] is True
    assert target.is_dir()
