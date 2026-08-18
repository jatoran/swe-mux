from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import git_init, git_review, server
from swe_mux.models import ProjectRecord


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
    def __init__(self, project: ProjectRecord, body: Any) -> None:
        self._body = body
        self.query: dict[str, str] = {}
        self.match_info: dict[str, str] = {}
        self.app = {
            "projects": SimpleNamespace(projects={project.id: project}),
            "events": Events(),
        }

    async def json(self) -> Any:
        return self._body


def payload(response: Any) -> Any:
    return json.loads(response.text)


# --------------------------------------------------------------------------- #
# The starter ignore file                                                      #
# --------------------------------------------------------------------------- #


def test_starter_gitignore_always_covers_secrets_and_os_noise(tmp_path: Path) -> None:
    content = git_init.starter_gitignore(tmp_path)
    assert ".env" in content
    assert "!.env.example" in content
    assert ".DS_Store" in content
    # An empty folder gets no language section it cannot justify.
    assert "node_modules/" not in content
    assert "__pycache__/" not in content


def test_starter_gitignore_follows_what_the_folder_actually_holds(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    content = git_init.starter_gitignore(tmp_path)
    assert "__pycache__/" in content
    assert ".ruff_cache/" in content
    assert "node_modules/" not in content

    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    both = git_init.starter_gitignore(tmp_path)
    assert "node_modules/" in both
    assert "__pycache__/" in both


# --------------------------------------------------------------------------- #
# Initialization                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_initialize_creates_the_repository_without_staging_anything(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    result = await git_init.initialize_repository(str(tmp_path), operation_id="op-init")

    assert (tmp_path / ".git").exists()
    assert result.gitignore == "created"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").startswith("# Written by swe-mux")
    # The whole point of not committing: history is empty and the tree is untouched.
    assert git(tmp_path, "rev-list", "--all", "--count") == "0"
    assert git(tmp_path, "diff", "--cached", "--name-only") == ""
    assert "app.py" in git(tmp_path, "status", "--porcelain")


@pytest.mark.asyncio
async def test_initialize_never_rewrites_an_existing_ignore_file(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("mine-only\n", encoding="utf-8")
    result = await git_init.initialize_repository(str(tmp_path), operation_id="op-keep")
    assert result.gitignore == "existing"
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "mine-only\n"


@pytest.mark.asyncio
async def test_initialize_names_the_default_branch(tmp_path: Path) -> None:
    result = await git_init.initialize_repository(str(tmp_path), operation_id="op-branch")
    # Whatever the host's `init.defaultBranch` says, the repository reports a real branch
    # rather than the empty string an unborn HEAD would give a naive rev-parse.
    assert result.branch
    assert result.branch == git(tmp_path, "symbolic-ref", "--short", "HEAD")


# --------------------------------------------------------------------------- #
# The endpoint and the state that reaches it                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_repository_identity_reports_a_folder_with_no_repository(tmp_path: Path) -> None:
    with pytest.raises(git_review.GitReviewError) as caught:
        await git_review.repository_identity(str(tmp_path))
    # The Git tab branches on this exact code to offer initialization; a generic
    # `git_error` there would send the user back to reading Git's `fatal:`.
    assert caught.value.code == "not_git_repository"
    assert caught.value.status == 404


@pytest.mark.asyncio
async def test_init_endpoint_creates_the_repository_and_announces_the_change(
    tmp_path: Path,
) -> None:
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    request = Request(project, {"project_id": project.id})
    body = payload(await server.init_repository(request))  # type: ignore[arg-type]

    assert body["ok"] is True
    assert body["gitignore"] == "created"
    assert (tmp_path / ".git").exists()
    assert ("git_changed", {"project_id": project.id}) in request.app["events"].emitted


@pytest.mark.asyncio
async def test_init_endpoint_refuses_a_folder_git_already_tracks(tmp_path: Path) -> None:
    git(tmp_path, "init")
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    with pytest.raises(git_review.GitReviewError) as caught:
        await server.init_repository(Request(project, {"project_id": project.id}))  # type: ignore[arg-type]
    # Re-checked in the handler rather than trusted from the client, because `git init`
    # on a tracked folder reinitializes a repository the user still has.
    assert caught.value.code == "already_initialized"
    assert caught.value.status == 409


@pytest.mark.asyncio
async def test_init_endpoint_refuses_a_folder_that_is_gone(tmp_path: Path) -> None:
    project = ProjectRecord("project", "Project", str(tmp_path / "missing"), 0)
    with pytest.raises(git_review.GitReviewError) as caught:
        await server.init_repository(Request(project, {"project_id": project.id}))  # type: ignore[arg-type]
    assert caught.value.code == "root_unavailable"
