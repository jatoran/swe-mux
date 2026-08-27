"""What the Map stops re-deriving, and what it stops making a reader wait for.

Its own file rather than more of `test_git_review.py`, because these all measure one
thing the module did not do before: hold on to an answer. Two shapes of claim -
"this reading costs N processes" and "this reading is served without waiting" - and both
are only meaningful as counts, so every test here counts something.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from swe_mux import git_review
from tests.support.settle import until


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
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


@pytest.fixture(autouse=True)
def _drop_overview_memo() -> None:
    """No test inherits another's memoized readings, or another's cached one."""
    git_review.reset_overview_cache()


def _spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Every `git` argv a reading runs, so process count is a measurable claim."""
    calls: list[tuple[str, ...]] = []
    original = git_review._run_git_bytes

    async def counted(cwd: object, *args: str, **kwargs: object) -> object:
        calls.append(tuple(args))
        return await original(cwd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_review, "_run_git_bytes", counted)
    return calls


async def _settle_overview() -> None:
    """Wait out the revalidation running behind a served reading.

    Polled rather than slept on: the gate runs across every core, and a fixed sleep
    before a positive assertion is a bet this test loses under load (CLAUDE.md
    § Verification, `tests/support/settle.py`).
    """
    await until(
        lambda: not git_review._inflight_worktree_overviews,
        what="the overview revalidation finished",
    )


# -- the preamble every reading pays -------------------------------------------


@pytest.mark.asyncio
async def test_repository_identity_costs_one_process(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`rev-parse` takes both flags at once; asking twice bought only a second spawn."""
    calls = _spawned(monkeypatch)
    root, common = await git_review.repository_identity(str(repository))

    assert Path(root) == repository.resolve()
    assert Path(common).name == ".git"
    assert calls == [("rev-parse", "--show-toplevel", "--git-common-dir")]


@pytest.mark.asyncio
async def test_a_listed_branch_resolves_out_of_the_ref_listing(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison ref was four to eight sequential probes for one branch name.

    `for-each-ref` already reports every branch and the commit each one names, so a
    branch that appears there needs no `check-ref-format`, no `rev-parse --verify`, and
    no separate read for its object ID.
    """
    calls = _spawned(monkeypatch)
    comparison, index = await git_review._infer_comparison_indexed(str(repository), "main")

    assert comparison["ref"] == "main"
    assert comparison["available"] is True
    assert "main" in comparison["candidates"]
    assert [args[0] for args in calls] == ["for-each-ref"]

    # And the base's object ID comes out of the same listing rather than a `rev-parse`.
    calls.clear()
    assert await git_review._comparison_oid(str(repository), "main", index) == git(
        repository, "rev-parse", "main"
    )
    assert calls == []


@pytest.mark.asyncio
async def test_a_ref_outside_the_listing_still_gets_its_probes(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The listing is an accelerator, not a gate: a tag is still a valid comparison."""
    git(repository, "tag", "release-1")
    calls = _spawned(monkeypatch)
    comparison = await git_review.infer_comparison(str(repository), "release-1")

    assert comparison["ref"] == "release-1"
    assert comparison["available"] is True
    assert "check-ref-format" in [args[0] for args in calls]
    assert any(args[0] == "rev-parse" and "--verify" in args for args in calls)


@pytest.mark.asyncio
async def test_an_override_that_stopped_resolving_is_still_unavailable(
    repository: Path,
) -> None:
    """A base the reader did not choose is worse than no comparison, listing or not."""
    comparison = await git_review.infer_comparison(str(repository), "never-existed")

    assert comparison["available"] is False
    assert comparison["ref"] is None
    assert comparison["display"] == "never-existed"


@pytest.mark.asyncio
async def test_an_override_beyond_the_candidate_cap_still_resolves(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap is a display budget for a dropdown, not a limit on what may be compared."""
    monkeypatch.setattr(git_review, "GIT_COMPARE_CANDIDATE_LIMIT", 1)
    git(repository, "branch", "zzz-late")
    index = await git_review.read_ref_index(str(repository))

    assert len(index["names"]) == 1
    assert "zzz-late" in index["oids"]
    comparison = await git_review.infer_comparison(str(repository), "zzz-late")
    assert comparison["available"] is True


# -- what a commit's date costs the second time --------------------------------


@pytest.mark.asyncio
async def test_a_commit_date_is_read_once_and_then_remembered(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit's committer date is part of the object its oid names.

    So the key is the answer's own identity and the memo cannot go stale: a commit whose
    date changed would be a different commit. The Map asks for every checkout's tip date
    on every request, which made this the largest single item in its preamble.
    """
    head = git(repository, "rev-parse", "HEAD")
    first = await git_review.head_commit_dates(str(repository), [head])
    calls = _spawned(monkeypatch)
    again = await git_review.head_commit_dates(str(repository), [head])

    assert again == first
    assert again[head] > 0
    assert calls == []


@pytest.mark.asyncio
async def test_a_commit_that_is_absent_is_not_remembered_as_absent(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It can arrive by `fetch`; caching the miss would leave that row undated."""
    missing = "0" * 40
    assert await git_review.head_commit_dates(str(repository), [missing]) == {}
    calls = _spawned(monkeypatch)

    assert await git_review.head_commit_dates(str(repository), [missing]) == {}
    assert [args[0] for args in calls] == ["show"]


# -- the per-checkout identity guard -------------------------------------------


@pytest.mark.asyncio
async def test_the_identity_guard_is_reread_only_when_the_registration_changes(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `rev-parse --show-toplevel` per checkout, keyed on the worktree listing.

    The guard exists so a broken nested worktree cannot inherit status from an enclosing
    repository, and it is a property of the *registration* rather than of anything in
    the tree - so the digest of `git worktree list --porcelain`, which changes on every
    registration change, is what invalidates it.
    """
    await git_review.worktree_overview("p", str(repository), None)
    calls = _spawned(monkeypatch)
    await git_review.worktree_overview("p", str(repository), None)

    assert not any(args == ("rev-parse", "--show-toplevel") for args in calls)
    # Still read live every time, because it is the reading the memo exists to protect.
    assert any(args[0] == "status" for args in calls)

    git(repository, "worktree", "add", "-b", "linked", str(tmp_path / "linked"))
    calls.clear()
    after = await git_review.worktree_overview("p", str(repository), None)

    assert len(after["worktrees"]) == 2
    # A new registration rewrites the listing, so every checkout re-proves its identity.
    assert sum(args == ("rev-parse", "--show-toplevel") for args in calls) == 2


@pytest.mark.asyncio
async def test_a_mismatched_identity_stays_refused_across_a_memoized_read(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The memo caches the observation, never the verdict.

    A checkout that fails the guard has to keep failing it: the comparison runs on every
    request, on a remembered reading exactly as on a fresh one.
    """
    original = git_review._run_git_bytes
    elsewhere = str(repository.parent.resolve()).encode("utf-8")

    async def lying(cwd: object, *args: str, **kwargs: object) -> object:
        if args == ("rev-parse", "--show-toplevel"):
            return git_review.GitResult(0, elsewhere, b"")
        return await original(cwd, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_review, "_run_git_bytes", lying)
    first = await git_review.worktree_overview("p", str(repository), None)
    second = await git_review.worktree_overview("p", str(repository), None)

    assert first["worktrees"][0]["unstaged"] is None
    assert second["worktrees"][0]["unstaged"] is None


# -- the reading the daemon keeps, so a reader does not wait for Git ------------


@pytest.mark.asyncio
async def test_a_second_read_is_served_from_the_last_reading(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Map stops blocking on a measurement it already has an answer for."""
    first = await git_review.shared_worktree_overview("p", str(repository), None)
    calls = _spawned(monkeypatch)
    second = await git_review.shared_worktree_overview("p", str(repository), None)

    # Served before any of the revalidation's own processes have had a chance to run.
    assert second == first
    assert calls == []
    await _settle_overview()
    assert calls, "the revalidation behind the served reading never ran"


@pytest.mark.asyncio
async def test_the_first_read_of_a_reading_still_measures_it(repository: Path) -> None:
    """There is nothing to serve, so this one waits - and says something true."""
    served = await git_review.shared_worktree_overview("p", str(repository), None)
    assert [Path(row["worktree"]) for row in served["worktrees"]] == [repository.resolve()]


@pytest.mark.asyncio
async def test_the_refresh_button_waits_for_a_newly_measured_answer(
    repository: Path,
) -> None:
    """`fresh` is the one caller that asked to wait, and must not be served stale."""
    await git_review.shared_worktree_overview("p", str(repository), None)
    await _settle_overview()
    (repository / "tracked.txt").write_text("first\nsecond\n", encoding="utf-8")

    served = await git_review.shared_worktree_overview("p", str(repository), None, fresh=True)
    assert served["worktrees"][0]["unstaged"]["total"] == 1


@pytest.mark.asyncio
async def test_a_revalidation_that_disagrees_says_so_and_a_quiet_one_does_not(
    repository: Path,
) -> None:
    """The notice is what closes the loop on serving a reading that was superseded.

    It fires only when a reader was handed the *other* answer: a first computation had
    no stale serve behind it, and an unchanged one has nothing to correct - which is
    what keeps a quiet repository from emitting an event per read, and what stops the
    refetch it provokes from sustaining itself.
    """
    notices = 0

    def refreshed() -> None:
        nonlocal notices
        notices += 1

    await git_review.shared_worktree_overview(
        "p", str(repository), None, on_refreshed=refreshed
    )
    await _settle_overview()
    assert notices == 0, "a first computation had no served answer to correct"

    await git_review.shared_worktree_overview(
        "p", str(repository), None, on_refreshed=refreshed
    )
    await _settle_overview()
    assert notices == 0, "an unchanged revalidation must correct nothing"

    (repository / "tracked.txt").write_text("first\nsecond\n", encoding="utf-8")
    await git_review.shared_worktree_overview(
        "p", str(repository), None, on_refreshed=refreshed
    )
    await _settle_overview()
    assert notices == 1


@pytest.mark.asyncio
async def test_a_whole_project_read_and_one_row_are_not_the_same_reading(
    repository: Path, tmp_path: Path
) -> None:
    """`only` is part of the key: joining them would hand a row the whole inventory."""
    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "-b", "linked", str(linked))
    whole = await git_review.shared_worktree_overview("p", str(repository), None)
    one = await git_review.shared_worktree_overview("p", str(repository), None, str(linked))

    assert len(whole["worktrees"]) == 2
    assert len(one["worktrees"]) == 1


@pytest.mark.asyncio
async def test_a_worktree_mutation_drops_the_reading_it_would_contradict(
    repository: Path,
) -> None:
    """A removed checkout must not come back as a row with Land and Remove on it.

    Ordinary drift is what revalidation is for; a registration change is not drift,
    because it changes *which rows exist*.
    """
    await git_review.shared_worktree_overview("p", str(repository), None)
    await _settle_overview()
    git_review.invalidate_overview_cache("p")
    (repository / "tracked.txt").write_text("first\nsecond\n", encoding="utf-8")

    # Nothing left to serve, so this one measures rather than answering from the cache.
    served = await git_review.shared_worktree_overview("p", str(repository), None)
    assert served["worktrees"][0]["unstaged"]["total"] == 1


@pytest.mark.asyncio
async def test_invalidating_one_project_leaves_another_alone(repository: Path) -> None:
    await git_review.shared_worktree_overview("keep", str(repository), None)
    await git_review.shared_worktree_overview("drop", str(repository), None)
    await _settle_overview()
    git_review.invalidate_overview_cache("drop")

    assert [key[0] for key in git_review._overview_cache] == ["keep"]


@pytest.mark.asyncio
async def test_a_failed_revalidation_leaves_the_previous_reading_in_place(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient Git error must not turn an answer the reader has into a blocking read."""
    good = await git_review.shared_worktree_overview("p", str(repository), None)
    await _settle_overview()

    async def broken(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise git_review.GitReviewError("git_error", "Git fell over")

    monkeypatch.setattr(git_review, "worktree_overview", broken)
    served = await git_review.shared_worktree_overview("p", str(repository), None)
    await _settle_overview()

    assert served == good
    assert await git_review.shared_worktree_overview("p", str(repository), None) == good
