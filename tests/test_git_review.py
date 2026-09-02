from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

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
async def test_overview_dates_every_worktree_from_its_branch_tip(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each tree reports its tip's committer date, and reports it from the object database.

    The directory's own mtime is deliberately not the source: Windows freezes
    ``st_mtime`` on a file held open, so a checkout an agent is working in reports a
    stale timestamp and would sort as the most dormant tree in the list.
    """
    (repository / ".gitignore").write_text(".claude/\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "ignore managed worktrees")
    worktree = repository / ".claude" / "worktrees" / "feature"
    worktree.parent.mkdir(parents=True)
    git(repository, "worktree", "add", "-b", "feature", str(worktree))
    (worktree / "work.txt").write_text("branch work\n", encoding="utf-8")
    git(worktree, "add", "work.txt")
    # `--date` moves only the *author* date; the sort key is the committer date, which
    # is the one that actually tracks when the branch last moved.
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2030-01-01T00:00:00+00:00")
    git(worktree, "commit", "-m", "branch work")
    monkeypatch.delenv("GIT_COMMITTER_DATE")

    overview = await git_review.worktree_overview("project", str(repository), None)
    rows = {Path(str(row["worktree"])).resolve(): row for row in overview["worktrees"]}
    main_row = rows[repository.resolve()]
    branch_row = rows[worktree.resolve()]
    assert isinstance(main_row["head_committed_at"], int)
    assert isinstance(branch_row["head_committed_at"], int)
    # The branch committed after the trunk, so it must date later - which is the whole
    # point of the field, and is what puts it on top of an activity-ordered list.
    assert branch_row["head_committed_at"] > main_row["head_committed_at"]
    expected = int(git(worktree, "show", "-s", "--format=%ct", "HEAD"))
    assert branch_row["head_committed_at"] == expected


@pytest.mark.asyncio
async def test_head_commit_dates_ignores_junk_and_keys_by_requested_spelling(
    repository: Path,
) -> None:
    oid = git(repository, "rev-parse", "HEAD")
    dates = await git_review.head_commit_dates(
        str(repository), [oid.upper(), oid, "not-an-oid", ""]
    )
    # Deduplicated by the spelling asked for, and a malformed entry never reaches Git.
    assert set(dates) == {oid.upper(), oid}
    assert dates[oid.upper()] == dates[oid]
    assert await git_review.head_commit_dates(str(repository), []) == {}
    # An oid Git does not have is simply absent rather than an error the caller has to
    # handle: the row renders as unmeasured and sorts last.
    missing = "0" * 40
    assert await git_review.head_commit_dates(str(repository), [missing]) == {}


@pytest.mark.asyncio
async def test_prunable_worktree_still_reports_its_tip_date(repository: Path) -> None:
    """A checkout Git cannot use still says when its branch last moved.

    Its commit lives in the shared object database, so nothing about reading the date
    needs the broken directory - and a damaged tree that reported no date at all would
    sink to the bottom of an activity-ordered list for the wrong reason.
    """
    (repository / ".gitignore").write_text(".codex/\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "ignore managed worktrees")
    worktree = repository / ".codex" / "worktrees" / "broken"
    worktree.parent.mkdir(parents=True)
    git(repository, "worktree", "add", "-b", "broken", str(worktree))
    (worktree / ".git").rename(worktree / ".git.moved")

    overview = await git_review.worktree_overview("project", str(repository), None)
    row = next(
        item
        for item in overview["worktrees"]
        if Path(str(item["worktree"])).resolve() == worktree.resolve()
    )
    assert row["prunable"]
    assert row["unstaged"] is None
    assert isinstance(row["head_committed_at"], int)


@pytest.mark.asyncio
async def test_prunable_nested_worktree_never_inherits_parent_status(repository: Path) -> None:
    (repository / ".gitignore").write_text(".codex/\n", encoding="utf-8")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "ignore managed worktrees")
    worktree = repository / ".codex" / "worktrees" / "broken"
    worktree.parent.mkdir(parents=True)
    git(repository, "worktree", "add", "-b", "broken", str(worktree))
    (worktree / ".git").rename(worktree / ".git.moved")
    (repository / "tracked.txt").write_text("parent is dirty\n", encoding="utf-8")

    overview = await git_review.worktree_overview("project", str(repository), None)
    row = next(
        item
        for item in overview["worktrees"]
        if Path(str(item["worktree"])).resolve() == worktree.resolve()
    )
    assert row["prunable"] == "gitdir file points to non-existent location"
    assert row["unstaged"] is None
    assert row["staged"] is None
    assert row["conflicted"] is None
    assert row["comparison_counts"] is None
    assert row["branch_delta"] is None


@pytest.mark.asyncio
async def test_local_summary_bounds_untracked_content_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = b"".join(
        f"? generated/file-{index}.txt\0".encode() for index in range(500)
    )

    async def run_git(
        cwd: str | Path, *args: str, timeout_seconds: float = 4.0
    ) -> git_review.GitResult:
        del cwd, timeout_seconds
        return git_review.GitResult(0, records if args[0] == "status" else b"", b"")

    inspected: list[str] = []

    def measure(worktree: str, item: git_review.GitFileChange, remaining: int) -> int:
        del worktree, remaining
        inspected.append(item["path"])
        item["additions"] = 1
        item["deletions"] = 0
        return 1

    monkeypatch.setattr(git_review, "_run_git_bytes", run_git)
    monkeypatch.setattr(git_review, "_measure_untracked", measure)
    unstaged, staged, conflicted = await git_review._local_summaries("C:/repo")
    assert unstaged is not None
    assert unstaged["total"] == 500
    assert unstaged["truncated"] is True
    assert len(unstaged["files"]) == git_review.GIT_CHANGE_FILE_LIMIT
    assert len(inspected) == git_review.GIT_CHANGE_FILE_LIMIT
    assert staged == git_review.change_summary([])
    assert conflicted == git_review.change_summary([])


@pytest.mark.asyncio
async def test_overview_requests_share_work_after_caller_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def overview(
        project_id: str, project_root: str, compare_override: str | None, only: str | None = None
    ) -> dict[str, object]:
        nonlocal calls
        assert (project_id, project_root, compare_override, only) == (
            "project",
            "C:/repo",
            None,
            None,
        )
        calls += 1
        started.set()
        await release.wait()
        return {"worktrees": []}

    monkeypatch.setattr(git_review, "worktree_overview", overview)
    first = asyncio.create_task(
        git_review.shared_worktree_overview("project", "C:/repo", None)
    )
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(
        git_review.shared_worktree_overview("project", "C:/repo", None)
    )
    await asyncio.sleep(0)
    assert calls == 1
    release.set()
    assert await second == {"worktrees": []}
    await asyncio.sleep(0)
    assert not git_review._inflight_worktree_overviews


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
async def test_commit_changes_carries_the_whole_message(repository: Path) -> None:
    (repository / "tracked.txt").write_text("first\nsecond\n", encoding="utf-8")
    git(repository, "add", "tracked.txt")
    git(repository, "commit", "-m", "subject line", "-m", "First paragraph.\n\nSecond paragraph.")
    oid = git(repository, "rev-parse", "HEAD")

    changes = await git_review.commit_changes("project", str(repository), oid, None)
    # Subject, blank line, and the body verbatim - the expanded row shows what was written,
    # not the one elided line the summary row has room for.
    assert changes["message"] == "subject line\n\nFirst paragraph.\n\nSecond paragraph."


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
    """The reap is the bounded runner's contract; this module owes the mapping and
    the silence about patch bodies."""
    from swe_mux.bounded_subprocess import ProcessOutcome

    seen: list[dict[str, Any]] = []

    async def fake_run_bounded(argv: Any, **kwargs: Any) -> ProcessOutcome:
        seen.append({"argv": tuple(argv), **kwargs})
        return ProcessOutcome(
            exit_code=None,
            stdout=b"secret patch body",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=10.0,
            timed_out=True,
        )

    monkeypatch.setattr(git_review, "run_bounded", fake_run_bounded)
    with caplog.at_level(logging.INFO, logger=git_review.__name__):
        result = await git_review._run_patch("C:/repo", "diff", timeout_seconds=0.01)
    assert result.timed_out is True
    assert result.stdout == b""
    assert seen[0]["argv"][:4] == ("git", "--no-optional-locks", "-C", "C:/repo")
    assert seen[0]["lane"] == "interactive"
    assert "secret patch body" not in caplog.text


@pytest.mark.asyncio
async def test_oversized_output_is_refused_rather_than_parsed_clipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clipped `worktree list` parses into something confidently wrong; a clipped
    patch renders as a patch. Both come back empty and `too_large`."""
    from swe_mux.bounded_subprocess import ProcessOutcome

    def outcome(stdout: bytes, *, truncated: bool) -> ProcessOutcome:
        return ProcessOutcome(
            exit_code=0,
            stdout=stdout,
            stderr=b"",
            stdout_truncated=truncated,
            stderr_truncated=False,
            duration_ms=1.0,
            timed_out=False,
        )

    answers = iter(
        [
            outcome(b"worktree C:/repo\n", truncated=True),
            outcome(b"diff --git a b\n" * 20_000, truncated=False),
            outcome(b"x" * (git_review.GIT_DIFF_MAX_BYTES + 1), truncated=True),
            outcome(b"diff --git a b\n", truncated=False),
        ]
    )

    async def fake_run_bounded(argv: Any, **kwargs: Any) -> ProcessOutcome:
        return next(answers)

    monkeypatch.setattr(git_review, "run_bounded", fake_run_bounded)
    query = await git_review._run_git_bytes("C:/repo", "worktree", "list")
    assert query.too_large is True and query.stdout == b""
    too_many_lines = await git_review._run_patch("C:/repo", "diff")
    assert too_many_lines.too_large is True and too_many_lines.stdout == b""
    too_many_bytes = await git_review._run_patch("C:/repo", "diff")
    assert too_many_bytes.too_large is True
    fine = await git_review._run_patch("C:/repo", "diff")
    assert fine.too_large is False and fine.stdout == b"diff --git a b\n"


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


@pytest.mark.asyncio
async def test_branch_changed_paths_covers_committed_uncommitted_and_untracked(
    repository: Path, tmp_path: Path
) -> None:
    """The three states a branch's work can be in, from one base.

    Diffing the working tree against the **merge base** is what collapses them:
    `git status` forgets a change the moment it is committed, and `diff ref...HEAD`
    forgets everything not yet committed. Untracked files are read separately
    because no diff can see them.
    """
    worktree = tmp_path / "feature"
    git(repository, "worktree", "add", "-b", "feature", str(worktree))
    (worktree / "committed.txt").write_text("landed\n", encoding="utf-8")
    git(worktree, "add", "committed.txt")
    git(worktree, "commit", "-m", "on the branch")
    (worktree / "tracked.txt").write_text("second\n", encoding="utf-8")
    (worktree / "untracked.txt").write_text("brand new\n", encoding="utf-8")

    result = await git_review.branch_changed_paths(str(worktree), "main")
    assert result is not None
    assert result["ref"] == "main"
    assert result["base"]
    assert set(result["paths"]) == {"committed.txt", "tracked.txt", "untracked.txt"}
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_branch_changed_paths_reports_no_base_rather_than_no_changes(
    tmp_path: Path,
) -> None:
    """An empty list would claim a branch identical to its base.

    That is the one answer a reader would act on, so a checkout with nothing to
    compare against says so by returning nothing at all.
    """
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert await git_review.branch_changed_paths(str(outside), None) is None


@pytest.mark.asyncio
async def test_branch_changed_paths_stops_at_its_limit(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_review, "GIT_BRANCH_PATH_LIMIT", 1)
    git(repository, "checkout", "-b", "wide")
    for index in range(3):
        (repository / f"file{index}.txt").write_text("x\n", encoding="utf-8")
    result = await git_review.branch_changed_paths(str(repository), "main")
    assert result is not None
    # Silently returning one path would read as a one-file branch.
    assert len(result["paths"]) == 1
    assert result["truncated"] is True


# -- the overview's memo, its two readings, and the log search ------------------


@pytest.fixture(autouse=True)
def _drop_overview_memo() -> None:
    """No test inherits another's memoized branch readings."""
    git_review.reset_overview_cache()


def _spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Every `git` argv the overview runs, so process count is a measurable claim."""
    calls: list[tuple[str, ...]] = []
    original = git_review._run_git_bytes

    async def counted(cwd: object, *args: str, **kwargs: object) -> object:
        calls.append(tuple(args))
        return await original(cwd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_review, "_run_git_bytes", counted)
    return calls


@pytest.mark.asyncio
async def test_a_second_overview_reuses_the_branch_readings_it_already_took(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Map's expensive half is memoized on two object IDs, so it is exact.

    Neither reading can be affected by anything in a working tree - both are
    commit-to-commit - so a checkout whose HEAD and base are unchanged has, by
    definition, the same answer. What this measures is that the five subprocesses behind
    it stop being spawned to re-derive it.
    """
    git(repository, "checkout", "-b", "feature")
    (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "feature work")

    first = await git_review.worktree_overview("p", str(repository), "main")
    calls = _spawned(monkeypatch)
    second = await git_review.worktree_overview("p", str(repository), "main")

    assert (
        second["worktrees"][0]["comparison_counts"]
        == first["worktrees"][0]["comparison_counts"]
    )
    assert second["worktrees"][0]["branch_delta"] == first["worktrees"][0]["branch_delta"]
    assert not any(args[0] == "rev-list" and "--left-right" in args for args in calls)
    assert not any(args[0] == "diff" and "main...HEAD" in args for args in calls)
    # Still read live every time: `status --porcelain=v2` carries no worktree blob hash,
    # so a fingerprint taken from it would go quietly wrong about the line counts.
    assert any(args[0] == "status" for args in calls)


@pytest.mark.asyncio
async def test_a_commit_invalidates_the_memoized_branch_reading(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repository, "checkout", "-b", "feature")
    (repository / "feature.txt").write_text("feature\n", encoding="utf-8")
    git(repository, "add", "feature.txt")
    git(repository, "commit", "-m", "feature work")
    await git_review.worktree_overview("p", str(repository), "main")

    (repository / "second.txt").write_text("second\n", encoding="utf-8")
    git(repository, "add", "second.txt")
    git(repository, "commit", "-m", "more work")
    calls = _spawned(monkeypatch)
    after = await git_review.worktree_overview("p", str(repository), "main")

    assert after["worktrees"][0]["comparison_counts"] == {"ahead": 2, "behind": 0}
    assert any(args[0] == "rev-list" and "--left-right" in args for args in calls)


@pytest.mark.asyncio
async def test_a_clean_checkout_spawns_no_diff_for_line_counts(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two `git diff --numstat` per worktree ran unconditionally on a mostly-clean Map."""
    calls = _spawned(monkeypatch)
    overview = await git_review.worktree_overview("p", str(repository), None)
    assert overview["worktrees"][0]["unstaged"] == {
        "total": 0,
        "additions": 0,
        "deletions": 0,
        "binary_files": 0,
        "files": [],
        "truncated": False,
    }
    # The *local* numstats: no revision range, so the argv ends at `-z`. The branch
    # delta's own diffs take a range and are a different question.
    assert not any(
        args[0] == "diff" and "--numstat" in args and args[-1] == "-z" for args in calls
    )


@pytest.mark.asyncio
async def test_a_dirty_checkout_still_gets_its_line_counts(repository: Path) -> None:
    (repository / "tracked.txt").write_text("first\nsecond\n", encoding="utf-8")
    overview = await git_review.worktree_overview("p", str(repository), None)
    unstaged = overview["worktrees"][0]["unstaged"]
    assert unstaged["total"] == 1
    assert unstaged["additions"] == 1


@pytest.mark.asyncio
async def test_summary_detail_withholds_files_and_says_so(repository: Path) -> None:
    (repository / "tracked.txt").write_text("first\nsecond\n", encoding="utf-8")
    full = await git_review.worktree_overview("p", str(repository), None)
    summary = git_review.summarize_overview(full)
    assert full["detail"] == "full"
    assert summary["detail"] == "summary"
    withheld = summary["worktrees"][0]["unstaged"]
    # The counts survive; only the list goes. Without the marker a Map row saying
    # "1 local" over an empty list is indistinguishable from an empty change set.
    assert withheld["total"] == 1
    assert withheld["files"] == []
    assert withheld["files_omitted"] is True
    assert len(full["worktrees"][0]["unstaged"]["files"]) == 1


@pytest.mark.asyncio
async def test_one_worktree_can_be_read_on_its_own(repository: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "-b", "linked", str(linked))
    one = await git_review.worktree_overview("p", str(repository), None, str(linked))
    assert [row["branch"] for row in one["worktrees"]] == ["refs/heads/linked"]
    # `main` is "the first tree Git lists", read off the full listing: a single-row read
    # that renumbered from its own filtered list would call every checkout the main one.
    assert one["worktrees"][0]["main"] is False


@pytest.mark.asyncio
async def test_an_unlisted_path_is_refused_rather_than_measured(
    repository: Path, tmp_path: Path
) -> None:
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    with pytest.raises(git_review.GitReviewError) as caught:
        await git_review.worktree_overview("p", str(repository), None, str(stranger))
    assert caught.value.code == "worktree_not_found"


@pytest.mark.asyncio
async def test_the_log_can_be_searched_by_message_and_by_author(repository: Path) -> None:
    (repository / "b.txt").write_text("b\n", encoding="utf-8")
    git(repository, "add", "b.txt")
    git(repository, "commit", "-m", "teach the rail to overflow")
    (repository / "c.txt").write_text("c\n", encoding="utf-8")
    git(repository, "add", "c.txt")
    git(
        repository,
        "-c",
        "user.name=Other Person",
        "-c",
        "user.email=other@example.invalid",
        "commit",
        "-m",
        "unrelated change",
    )

    # Case-insensitive, and asked of Git rather than of the page already fetched.
    by_message = await git_review.git_graph("p", str(repository), 50, grep="RAIL")
    assert [line["subject"] for line in by_message["lines"]] == [
        "teach the rail to overflow"
    ]
    assert by_message["filtered"] is True
    # Lanes are not drawn over a filtered set - Git only draws them for a contiguous
    # walk - so every row carries a bare node instead of a picture of a DAG that is not
    # there.
    assert {line["graph"] for line in by_message["lines"]} == {"* "}

    by_author = await git_review.git_graph("p", str(repository), 50, author="Other Person")
    assert [line["subject"] for line in by_author["lines"]] == ["unrelated change"]


@pytest.mark.asyncio
async def test_a_log_search_is_literal_unless_regex_is_asked_for(repository: Path) -> None:
    """`.` and `*` are ordinary characters in a commit subject, and a reader means them."""
    (repository / "b.txt").write_text("b\n", encoding="utf-8")
    git(repository, "add", "b.txt")
    git(repository, "commit", "-m", "fix a.b crash")

    literal = await git_review.git_graph("p", str(repository), 50, grep="a.b")
    assert [line["subject"] for line in literal["lines"]] == ["fix a.b crash"]
    assert await git_review.git_graph("p", str(repository), 50, grep="axb") == {
        "lines": [],
        "limit": 50,
        "has_more": False,
        "filtered": True,
    }
    as_regex = await git_review.git_graph("p", str(repository), 50, grep="a.b", regex=True)
    assert [line["subject"] for line in as_regex["lines"]] == ["fix a.b crash"]


@pytest.mark.asyncio
async def test_an_unfiltered_log_keeps_gits_own_lanes(repository: Path) -> None:
    result = await git_review.git_graph("p", str(repository), 50)
    assert result["filtered"] is False
    assert all(line["graph"].startswith("*") for line in result["lines"])
