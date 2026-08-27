from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux import git_init, git_review
from swe_mux.config import Config
from swe_mux.models import ProjectRecord
from swe_mux.routes import git as git_routes


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
            keys.PROJECTS: SimpleNamespace(projects={project.id: project}),
            keys.EVENTS: Events(),
            keys.CONFIG: Config(
                data_dir=Path(project.root),
                config_path=Path(project.root) / "mux-config.toml",
            ),
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
async def test_initialize_can_explicitly_append_whole_swe_mux_ignore(tmp_path: Path) -> None:
    (tmp_path / ".swe-mux").mkdir()
    (tmp_path / ".swe-mux" / "config.toml").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("mine-only\r\n", encoding="utf-8", newline="")

    result = await git_init.initialize_repository(
        str(tmp_path), operation_id="op-ignore", ignore_swe_mux_files=True
    )

    assert result.swe_mux_ignore == "added"
    assert (tmp_path / ".gitignore").read_bytes() == (
        b"mine-only\r\n# Keep all swe-mux Project files local to this checkout.\r\n"
        b"/.swe-mux/\r\n"
    )
    assert git(tmp_path, "check-ignore", ".swe-mux/config.toml") == ".swe-mux/config.toml"


@pytest.mark.asyncio
async def test_whole_swe_mux_ignore_refuses_tracked_files(tmp_path: Path) -> None:
    git(tmp_path, "init")
    mux = tmp_path / ".swe-mux"
    mux.mkdir()
    (mux / "config.toml").write_text("version = 1\n", encoding="utf-8")
    git(tmp_path, "add", ".swe-mux/config.toml")

    status = await git_init.repository_setup_status(str(tmp_path), operation_id="op-status")
    assert status.tracked is True
    assert status.ignored is False
    with pytest.raises(git_init.RepositoryInitError, match="already tracked"):
        await git_init.ignore_swe_mux(str(tmp_path), operation_id="op-refuse")
    assert not (tmp_path / ".gitignore").exists()


@pytest.mark.asyncio
async def test_whole_swe_mux_ignore_is_idempotent(tmp_path: Path) -> None:
    git(tmp_path, "init")
    mux = tmp_path / ".swe-mux"
    mux.mkdir()
    (mux / "config.toml").write_text("version = 1\n", encoding="utf-8")

    assert await git_init.ignore_swe_mux(str(tmp_path), operation_id="op-first") == "added"
    assert (
        await git_init.ignore_swe_mux(str(tmp_path), operation_id="op-second")
        == "already_ignored"
    )
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count("/.swe-mux/") == 1


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
    body = payload(await git_routes.init_repository(request))  # type: ignore[arg-type]

    assert body["ok"] is True
    assert body["gitignore"] == "created"
    assert body["swe_mux_ignore"] == "not_requested"
    assert (tmp_path / ".git").exists()
    assert request.app[keys.CONFIG].git_swe_mux_prompt_decisions == {
        project.id: "keep_visible"
    }
    assert ("git_changed", {"project_id": project.id}) in request.app[keys.EVENTS].emitted


@pytest.mark.asyncio
async def test_setup_endpoint_records_a_project_choice_without_touching_gitignore(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init")
    mux = tmp_path / ".swe-mux"
    mux.mkdir()
    (mux / "config.toml").write_text("version = 1\n", encoding="utf-8")
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    request = Request(project, {})
    request.query = {"project_id": project.id}

    initial = payload(await git_routes.get_swe_mux_setup(request))  # type: ignore[arg-type]
    assert initial == {
        "show": True,
        "reason": "available",
        "decision": "unseen",
        "can_ignore": True,
        "tracked": False,
    }

    request._body = {"project_id": project.id, "decision": "keep_visible"}
    decided = payload(await git_routes.decide_swe_mux_setup(request))  # type: ignore[arg-type]
    assert decided == {"ok": True, "decision": "keep_visible", "changed": False}
    assert not (tmp_path / ".gitignore").exists()
    assert request.app[keys.CONFIG].git_swe_mux_prompt_decisions == {
        project.id: "keep_visible"
    }


@pytest.mark.asyncio
async def test_setup_endpoint_applies_ignore_and_never_ask_is_reversible(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init")
    mux = tmp_path / ".swe-mux"
    mux.mkdir()
    (mux / "config.toml").write_text("version = 1\n", encoding="utf-8")
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    request = Request(project, {"project_id": project.id, "decision": "ignore_all"})

    ignored = payload(await git_routes.decide_swe_mux_setup(request))  # type: ignore[arg-type]
    assert ignored["changed"] is True
    assert _root_ignore(tmp_path) == "/.swe-mux/"
    assert request.app[keys.CONFIG].git_swe_mux_prompt_decisions[project.id] == "ignore_all"

    request.app[keys.CONFIG].git_swe_mux_prompt_decisions = {}
    request._body = {"project_id": project.id, "decision": "never_ask"}
    disabled = payload(await git_routes.decide_swe_mux_setup(request))  # type: ignore[arg-type]
    assert disabled == {"ok": True, "decision": "never_ask", "changed": False}
    assert request.app[keys.CONFIG].git_swe_mux_prompt_enabled is False
    assert request.app[keys.CONFIG].git_swe_mux_prompt_decisions == {}


def _root_ignore(root: Path) -> str:
    return next(
        line for line in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line == "/.swe-mux/"
    )


@pytest.mark.asyncio
async def test_init_endpoint_refuses_a_folder_git_already_tracks(tmp_path: Path) -> None:
    git(tmp_path, "init")
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    with pytest.raises(git_review.GitReviewError) as caught:
        await git_routes.init_repository(Request(project, {"project_id": project.id}))  # type: ignore[arg-type]
    # Re-checked in the handler rather than trusted from the client, because `git init`
    # on a tracked folder reinitializes a repository the user still has.
    assert caught.value.code == "already_initialized"
    assert caught.value.status == 409


@pytest.mark.asyncio
async def test_init_endpoint_refuses_a_folder_that_is_gone(tmp_path: Path) -> None:
    project = ProjectRecord("project", "Project", str(tmp_path / "missing"), 0)
    with pytest.raises(git_review.GitReviewError) as caught:
        await git_routes.init_repository(Request(project, {"project_id": project.id}))  # type: ignore[arg-type]
    assert caught.value.code == "root_unavailable"
