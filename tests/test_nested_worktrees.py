from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swe_mux import nested_worktrees
from swe_mux.nested_worktrees import (
    CACHE_SECONDS,
    nested_worktree_paths,
    parse_worktree_roots,
    reset_cache,
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture(autouse=True)
def clean_cache() -> None:
    reset_cache()


def test_parse_keeps_worktrees_inside_the_root_and_drops_the_root_itself(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    (root / ".claude" / "worktrees" / "feature").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    output = "\n".join(
        [
            f"worktree {root}",
            "HEAD " + "0" * 40,
            "branch refs/heads/master",
            "",
            f"worktree {root / '.claude' / 'worktrees' / 'feature'}",
            "branch refs/heads/worktree-feature",
            "",
            # A worktree of the same repository living outside the Project is somebody
            # else's tree; hiding it here would mean nothing, because it was never listed.
            f"worktree {tmp_path / 'elsewhere'}",
            "detached",
            "",
        ]
    )
    assert parse_worktree_roots(output, root.resolve()) == frozenset(
        {".claude/worktrees/feature"}
    )


def test_a_project_outside_a_repository_answers_empty_rather_than_raising(
    tmp_path: Path,
) -> None:
    # The failure that matters: an explorer must keep working where Git does not.
    assert nested_worktree_paths(tmp_path) == frozenset()


def test_a_real_nested_worktree_is_found_in_project_coordinates(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "master")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "file.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "initial")
    # Deliberately *not* one of the conventional `.claude/worktrees` locations: the static
    # ignore patterns cannot know about this one, which is the whole reason Git is asked.
    git(root, "worktree", "add", "-b", "scratch", str(root / "scratch"))

    assert nested_worktree_paths(root) == frozenset({"scratch"})


def test_the_answer_is_cached_for_its_ttl_and_recomputed_after_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def counted(_root: Path) -> frozenset[str]:
        nonlocal calls
        calls += 1
        return frozenset({"scratch"})

    monkeypatch.setattr(nested_worktrees, "_read_worktree_roots", counted)

    assert nested_worktree_paths(tmp_path, now=100.0) == frozenset({"scratch"})
    assert nested_worktree_paths(tmp_path, now=100.0 + CACHE_SECONDS / 2) == frozenset(
        {"scratch"}
    )
    assert calls == 1
    # The search box is debounced per keystroke; without the cache each one is a subprocess.
    assert nested_worktree_paths(tmp_path, now=100.0 + CACHE_SECONDS + 1) == frozenset(
        {"scratch"}
    )
    assert calls == 2


def test_the_cache_is_bounded_rather_than_growing_with_every_root_browsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nested_worktrees, "_read_worktree_roots", lambda _root: frozenset())
    for index in range(300):
        root = tmp_path / f"root{index}"
        root.mkdir()
        nested_worktree_paths(root, now=1.0)
    assert len(nested_worktrees._cache) <= 256
