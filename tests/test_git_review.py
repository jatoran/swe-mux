from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

from swe_mux import git_review


def git(repo: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "tracked.txt").write_text("first\n", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def test_nul_parsers_keep_renames_binary_unicode_and_submodules() -> None:
    names = git_review.parse_name_status(
        "M\0space name.txt\0R100\0old\tname.txt\0new-λ.txt\0".encode()
    )
    stats = git_review.parse_numstat(
        "2\t1\tspace name.txt\0-\t-\t\0old\tname.txt\0new-λ.txt\0".encode()
    )
    joined = git_review._apply_numstat(names, stats)
    assert joined[0]["additions"] == 2
    assert joined[1]["old_path"] == "old\tname.txt"
    assert joined[1]["binary"] is True
    raw = b":160000 160000 aaaaaaa bbbbbbb M\0vendor/tool\0"
    assert git_review.parse_raw_submodules(raw) == {"vendor/tool"}


def test_porcelain_v2_splits_both_sides_conflicts_and_untracked() -> None:
    data = (
        b"1 MM N... 100644 100644 100644 aaaaaaa bbbbbbb both.txt\0"
        b"u UU N... 100644 100644 100644 100644 aaaaaaa bbbbbbb ccccccc conflict.txt\0"
        b"? new file.txt\0"
        b"2 R. N... 100644 100644 100644 aaaaaaa bbbbbbb R100 renamed.txt\0old.txt\0"
    )
    staged, unstaged, conflicted = git_review.parse_porcelain_v2(data)
    assert [item["path"] for item in staged] == ["both.txt", "renamed.txt"]
    assert [item["path"] for item in unstaged] == ["both.txt", "new file.txt"]
    assert [item["path"] for item in conflicted] == ["conflict.txt"]
    assert staged[1]["old_path"] == "old.txt"


@pytest.mark.asyncio
async def test_comparison_inference_override_and_absence(repository: Path, tmp_path: Path) -> None:
    automatic = await git_review.infer_comparison(str(repository), None)
    assert automatic["ref"] == "main"
    assert automatic["source"] == "local_fallback"
    override = await git_review.infer_comparison(str(repository), "main")
    assert override["available"] is True
    assert override["source"] == "project_override"
    stale = await git_review.infer_comparison(str(repository), "gone")
    assert stale["available"] is False
    assert stale["source"] == "project_override"

    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init")
    unavailable = await git_review.infer_comparison(str(empty), None)
    assert unavailable["source"] == "none"
    assert unavailable["ref"] is None


@pytest.mark.asyncio
async def test_comparison_inference_prefers_remote_heads_and_bounds_candidates(
    repository: Path,
) -> None:
    git(repository, "remote", "add", "origin", str(repository))
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(
        repository,
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        "refs/remotes/origin/main",
    )
    comparison = await git_review.infer_comparison(str(repository), None)
    assert comparison["ref"] == "origin/main"
    assert comparison["source"] == "origin_head"
    assert "origin/HEAD" not in comparison["candidates"]
    assert "origin/main" in comparison["candidates"]


@pytest.mark.asyncio
async def test_single_non_origin_remote_head_is_used_when_unambiguous(
    repository: Path,
) -> None:
    git(repository, "remote", "add", "upstream", str(repository))
    git(repository, "update-ref", "refs/remotes/upstream/main", "HEAD")
    git(
        repository,
        "symbolic-ref",
        "refs/remotes/upstream/HEAD",
        "refs/remotes/upstream/main",
    )
    comparison = await git_review.infer_comparison(str(repository), None)
    assert comparison["ref"] == "upstream/main"
    assert comparison["source"] == "single_remote_head"


@pytest.mark.asyncio
async def test_overview_separates_staged_unstaged_and_untracked(repository: Path) -> None:
    (repository / "tracked.txt").write_text("first\nstaged\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    (repository / "tracked.txt").write_text("first\nstaged\nunstaged\n", encoding="utf-8")
    (repository / "new.txt").write_text("one\ntwo\n", encoding="utf-8")

    overview = await git_review.worktree_overview("project", str(repository), None)
    row = overview["worktrees"][0]
    assert row["staged"]["total"] == 1
    assert row["unstaged"]["total"] == 2
    assert row["unstaged"]["additions"] == 3
    assert row["comparison_counts"] == {"ahead": 0, "behind": 0}
    assert overview["comparison"]["ref"] == "main"


@pytest.mark.asyncio
async def test_branch_counts_can_be_ahead_and_behind(repository: Path) -> None:
    git(repository, "checkout", "-b", "feature")
    (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "feature")
    git(repository, "checkout", "main")
    (repository / "main.txt").write_text("main\n", encoding="utf-8")
    git(repository, "add", "main.txt")
    git(repository, "commit", "-m", "main")
    git(repository, "checkout", "feature")

    overview = await git_review.worktree_overview("project", str(repository), "main")
    assert overview["worktrees"][0]["comparison_counts"] == {"ahead": 1, "behind": 1}
    paths = {item["path"] for item in overview["worktrees"][0]["branch_delta"]["files"]}
    assert paths == {"feature.txt"}


@pytest.mark.asyncio
async def test_root_ordinary_merge_commit_changes_and_parent_validation(
    repository: Path,
) -> None:
    root_oid = git(repository, "rev-parse", "HEAD")
    root = await git_review.commit_changes("project", str(repository), root_oid, None)
    assert root["parent"] is None
    assert root["parent_label"] == "initial commit"
    assert root["summary"]["files"][0]["path"] == "tracked.txt"
    assert root["summary"]["files"][0]["current_exists"] is True

    git(repository, "checkout", "-b", "side")
    (repository / "side.txt").write_text("side\n", encoding="utf-8")
    git(repository, "add", "side.txt")
    git(repository, "commit", "-m", "side")
    git(repository, "checkout", "main")
    (repository / "main.txt").write_text("main\n", encoding="utf-8")
    git(repository, "add", "main.txt")
    git(repository, "commit", "-m", "main")
    git(repository, "merge", "--no-ff", "side", "-m", "merge")
    merge_oid = git(repository, "rev-parse", "HEAD")
    merge = await git_review.commit_changes("project", str(repository), merge_oid, None)
    assert len(merge["parents"]) == 2
    assert merge["parent"] == merge["parents"][0]
    second = await git_review.commit_changes(
        "project", str(repository), merge_oid, merge["parents"][1]
    )
    assert second["parent"] == merge["parents"][1]
    with pytest.raises(git_review.GitReviewError, match="parent is not attached"):
        await git_review.commit_changes("project", str(repository), merge_oid, root_oid)


@pytest.mark.asyncio
async def test_patch_snapshots_are_scoped_bounded_and_stale_checked(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / "tracked.txt").write_text("first\nchanged\n", encoding="utf-8")
    head = git(repository, "rev-parse", "HEAD")
    snapshot = await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override=None,
        scope="unstaged",
        path="tracked.txt",
        worktree=str(repository),
        commit=None,
        requested_parent=None,
        expected_head=head,
    )
    assert snapshot["patch"].startswith("diff --git")
    assert len(snapshot["patch_sha256"]) == 64
    with pytest.raises(git_review.GitReviewError) as stale:
        await git_review.patch_snapshot(
            project_id="project",
            project_root=str(repository),
            compare_override=None,
            scope="unstaged",
            path="tracked.txt",
            worktree=str(repository),
            commit=None,
            requested_parent=None,
            expected_head="0" * 40,
        )
    assert stale.value.status == 409

    monkeypatch.setattr(git_review, "GIT_DIFF_MAX_BYTES", 10)
    result = await git_review._run_patch(
        repository,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--",
        "tracked.txt",
    )
    assert result.too_large is True
    assert result.stdout == b""


@pytest.mark.asyncio
async def test_patch_snapshots_cover_staged_branch_commit_untracked_and_binary(
    repository: Path,
) -> None:
    git(repository, "checkout", "-b", "feature")
    (repository / "branch.txt").write_text("branch\n", encoding="utf-8")
    git(repository, "add", "branch.txt")
    git(repository, "commit", "-m", "branch")
    commit = git(repository, "rev-parse", "HEAD")

    branch = await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override="main",
        scope="branch",
        path="branch.txt",
        worktree=str(repository),
        commit=None,
        requested_parent=None,
    )
    assert branch["comparison_ref"] == "main"
    assert "+branch" in branch["patch"]

    commit_patch = await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override=None,
        scope="commit",
        path="branch.txt",
        worktree=None,
        commit=commit,
        requested_parent=None,
    )
    assert commit_patch["commit"] == commit
    assert commit_patch["parent"] is not None

    (repository / "tracked.txt").write_text("first\nstaged without newline", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    staged = await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override=None,
        scope="staged",
        path="tracked.txt",
        worktree=str(repository),
        commit=None,
        requested_parent=None,
    )
    assert "No newline at end of file" in staged["patch"]

    (repository / "untracked.txt").write_text("new\n", encoding="utf-8")
    untracked = await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override=None,
        scope="unstaged",
        path="untracked.txt",
        worktree=str(repository),
        commit=None,
        requested_parent=None,
    )
    assert untracked["patch"].startswith("--- /dev/null")

    (repository / "binary.bin").write_bytes(b"binary\0bytes")
    binary = await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override=None,
        scope="unstaged",
        path="binary.bin",
        worktree=str(repository),
        commit=None,
        requested_parent=None,
    )
    assert binary["binary"] is True
    assert binary["patch"] is None


@pytest.mark.asyncio
async def test_patch_command_is_hardened_and_scope_parameters_are_strict(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    async def fake_patch(cwd: str | Path, *args: str, **kwargs: object) -> git_review.GitResult:
        del cwd, kwargs
        calls.append(args)
        return git_review.GitResult(0, b"diff --git a/tracked.txt b/tracked.txt\n", b"")

    monkeypatch.setattr(git_review, "_run_patch", fake_patch)
    await git_review.patch_snapshot(
        project_id="project",
        project_root=str(repository),
        compare_override=None,
        scope="staged",
        path="tracked.txt",
        worktree=str(repository),
        commit=None,
        requested_parent=None,
    )
    invoked = calls[0]
    assert "--no-ext-diff" in invoked
    assert "--no-textconv" in invoked
    assert "--no-color" in invoked
    assert invoked[-2:] == ("--", "tracked.txt")

    with pytest.raises(git_review.GitReviewError, match="forbid"):
        await git_review.patch_snapshot(
            project_id="project",
            project_root=str(repository),
            compare_override=None,
            scope="commit",
            path="tracked.txt",
            worktree=str(repository),
            commit=git(repository, "rev-parse", "HEAD"),
            requested_parent=None,
        )


@pytest.mark.asyncio
async def test_patch_runner_times_out_reaps_and_does_not_log_content(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class NeverEndingReader:
        async def read(self, size: int) -> bytes:
            del size
            await asyncio.Event().wait()
            return b"secret patch body"

    class FakeProcess:
        stdout = NeverEndingReader()
        stderr = NeverEndingReader()
        returncode: int | None = None

        async def wait(self) -> int:
            await asyncio.Event().wait()
            return 0

    process = FakeProcess()
    reaped = 0

    async def fake_create(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        return process

    async def fake_reap(target: object) -> None:
        nonlocal reaped
        assert target is process
        reaped += 1
        process.returncode = 124

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(git_review, "reap_process_tree", fake_reap)
    with caplog.at_level(logging.INFO, logger=git_review.__name__):
        result = await git_review._run_patch("C:/repo", "diff", timeout_seconds=0.01)
    assert result.timed_out is True
    assert reaped >= 1
    assert "secret patch body" not in caplog.text


@pytest.mark.asyncio
async def test_path_and_worktree_validation_is_exact(repository: Path) -> None:
    assert git_review.validate_relative_path("src/space name.ts") == "src/space name.ts"
    for invalid in ("", "../secret", "src/../secret", "/absolute", "C:\\absolute"):
        with pytest.raises(git_review.GitReviewError):
            git_review.validate_relative_path(invalid)
    assert await git_review.validate_worktree_root(repository, str(repository))
    with pytest.raises(git_review.GitReviewError) as nested:
        await git_review.validate_worktree_root(repository, str(repository / "src"))
    assert nested.value.status == 404
    with pytest.raises(git_review.GitReviewError):
        git_review.validate_relative_path("control\nname.txt")
    with pytest.raises(git_review.GitReviewError):
        await git_review.validate_commit(str(repository), "HEAD")
