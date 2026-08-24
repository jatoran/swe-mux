from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux import git_review
from swe_mux.models import ProjectRecord
from swe_mux.routes import project_files as project_files_routes


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


class Request:
    def __init__(self, project: ProjectRecord, *, query: dict[str, str], body: Any = None) -> None:
        self.query = query
        self._body = body
        self.match_info = {"project_id": project.id}
        self.app = {keys.PROJECTS: SimpleNamespace(projects={project.id: project})}

    async def json(self) -> Any:
        return self._body


@pytest.fixture
def worktrees(tmp_path: Path) -> tuple[ProjectRecord, Path, Path]:
    root = tmp_path / "main"
    sibling = tmp_path / "sibling"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "same.txt").write_text("main\n", encoding="utf-8")
    git(root, "add", "same.txt")
    git(root, "commit", "-m", "initial")
    git(root, "worktree", "add", "-b", "sibling", str(sibling))
    (sibling / "same.txt").write_text("sibling\n", encoding="utf-8")
    return ProjectRecord("project", "Project", str(root), 0), root, sibling


@pytest.mark.asyncio
async def test_worktree_file_reads_and_writes_use_the_exact_checkout(
    worktrees: tuple[ProjectRecord, Path, Path],
) -> None:
    project, root, sibling = worktrees
    response = await project_files_routes.get_project_file(
        Request(project, query={"path": "same.txt", "worktree": str(sibling)})  # type: ignore[arg-type]
    )
    payload = json.loads(response.body)
    assert payload["text"].splitlines() == ["sibling"]
    assert Path(payload["worktree"]).resolve() == sibling.resolve()
    assert (root / "same.txt").read_text(encoding="utf-8").splitlines() == ["main"]

    written = await project_files_routes.put_project_file(
        Request(
            project,
            query={},
            body={
                "path": "same.txt",
                "worktree": str(sibling),
                "text": "updated sibling\n",
                "revision": payload["revision"],
            },
        )  # type: ignore[arg-type]
    )
    assert json.loads(written.body)["text"] == "updated sibling\n"
    assert (sibling / "same.txt").read_text(encoding="utf-8") == "updated sibling\n"
    assert (root / "same.txt").read_text(encoding="utf-8").splitlines() == ["main"]


@pytest.mark.asyncio
async def test_removed_or_nested_worktree_roots_are_recoverable_errors(
    worktrees: tuple[ProjectRecord, Path, Path],
) -> None:
    project, root, sibling = worktrees
    with pytest.raises(git_review.GitReviewError) as nested:
        await project_files_routes.get_project_file(
            Request(
                project,
                query={"path": "same.txt", "worktree": str(sibling / "nested")},
            )  # type: ignore[arg-type]
        )
    assert nested.value.status == 404

    git(root, "worktree", "remove", "--force", str(sibling))
    with pytest.raises(git_review.GitReviewError) as removed:
        await project_files_routes.get_project_file(
            Request(project, query={"path": "same.txt", "worktree": str(sibling)})  # type: ignore[arg-type]
        )
    assert removed.value.status == 404
