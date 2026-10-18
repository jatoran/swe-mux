from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import git_monitor, git_review
from swe_mux.event_bus import EventBus
from swe_mux.git_monitor import read_git_reading, read_git_state, read_unique_git_states
from swe_mux.git_readonly import READ_ONLY_GIT_OPTIONS

from .host_paths import ABS_ROOT

_FULL_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _fake_git_responses(porcelain: str) -> dict[tuple[str, ...], tuple[int, str]]:
    return {
        ("rev-parse", "--show-toplevel"): (0, "C:/repo"),
        ("branch", "--show-current"): (0, ""),
        ("status", "--porcelain"): (0, porcelain),
        ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): (1, ""),
        ("rev-parse", "HEAD"): (0, _FULL_SHA),
        ("rev-parse", "--short", "HEAD"): (0, "a1b2c3d"),
        # Verbatim real output: `--absolute-git-dir` is absolute, `--git-common-dir`
        # answers relative to the directory git ran in whenever it can. Writing both
        # sides absolute here is what let the primary checkout regress unnoticed.
        ("rev-parse", "--absolute-git-dir", "--git-common-dir"): (0, "C:/repo/.git\n.git"),
        ("diff", "--numstat", "HEAD"): (0, "3\t1\tfile.txt"),
    }


def _unavailable_comparison(source: str = "none", reason: str = "stubbed") -> dict[str, Any]:
    return {"ref": None, "display": None, "source": source, "available": False, "reason": reason}


def _available_comparison(ref: str) -> dict[str, Any]:
    return {"ref": ref, "display": ref, "source": "origin_head", "available": True, "reason": None}


@pytest.fixture(autouse=True)
def _clean_diffstat_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The memos are process-wide; a test must not inherit another's tree.

    Comparison inference is stubbed unavailable by default. It runs through
    `git_review`'s own subprocess helper rather than this module's `_git`, so
    without the stub a test that says nothing about a base would reach a real
    `git` in a directory that does not exist — which is exactly the kind of
    silent live call the exhaustive fake-git tables exist to make impossible.
    """
    git_monitor.reset_diffstat_cache()

    async def no_comparison(repository: str, override: str | None) -> dict[str, Any]:
        del repository, override
        return _unavailable_comparison()

    monkeypatch.setattr(git_monitor, "resolve_comparison_ref", no_comparison)


@pytest.mark.asyncio
async def test_detached_head_uses_short_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _fake_git_responses(" M file.txt")[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert state.branch == "a1b2c3d"
    assert state.dirty == 1


@pytest.mark.asyncio
async def test_git_reading_carries_head_and_dirty_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 0 provenance needs commit identity, not just a dirty file count."""
    porcelain = {"value": " M file.txt\n?? new.txt"}

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _fake_git_responses(porcelain["value"])[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    reading = await read_git_reading("C:/repo")
    assert reading.evidence.head == _FULL_SHA
    first = reading.evidence.dirty_hash
    assert first

    # Order-independent: the same change set hashes identically...
    porcelain["value"] = "?? new.txt\n M file.txt"
    assert (await read_git_reading("C:/repo")).evidence.dirty_hash == first
    # ...and a different change set does not.
    porcelain["value"] = " M other.txt"
    assert (await read_git_reading("C:/repo")).evidence.dirty_hash != first
    # A clean tree has no dirty hash at all.
    porcelain["value"] = ""
    assert (await read_git_reading("C:/repo")).evidence.dirty_hash is None


@pytest.mark.asyncio
async def test_diffstat_is_memoized_on_the_dirty_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git diff --numstat` runs when the change set moves, not once per poll.

    This is what makes per-session line counts affordable: an idle fleet sharing
    one checkout re-reads the fingerprint the cheap poll already computed and
    spawns no diff at all.
    """
    porcelain = {"value": " M file.txt"}
    numstat_calls: list[str] = []

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del timeout_seconds
        if args[:2] == ("diff", "--numstat"):
            numstat_calls.append(cwd)
        return _fake_git_responses(porcelain["value"])[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    first = await read_git_state("C:/repo")
    assert (first.added, first.removed) == (3, 1)
    assert len(numstat_calls) == 1

    # Same change set: memoized, no second subprocess.
    await read_git_reading("C:/repo")
    assert len(numstat_calls) == 1

    # The change set moved, so the measurement must be redone.
    porcelain["value"] = " M file.txt\n M other.txt"
    await read_git_reading("C:/repo")
    assert len(numstat_calls) == 2


@pytest.mark.asyncio
async def test_clean_tree_reports_zero_without_running_a_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No change set means no changed lines, and that needs no subprocess."""
    numstat_calls: list[str] = []

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del timeout_seconds
        if args[:2] == ("diff", "--numstat"):
            numstat_calls.append(cwd)
        return _fake_git_responses("")[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert (state.added, state.removed) == (0, 0)
    assert numstat_calls == []


@pytest.mark.asyncio
async def test_unmeasurable_diffstat_is_none_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed diff must not read as a clean tree — those are different facts."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _fake_git_responses(" M file.txt")
        responses[("diff", "--numstat", "HEAD")] = (128, "fatal: bad revision")
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert state.added is None and state.removed is None


def _controllable_clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    now = {"value": 1000.0}
    monkeypatch.setattr(git_monitor, "monotonic", lambda: now["value"])
    return now


def _porcelain(count: int) -> str:
    return "\n".join(f" M file{index}.json" for index in range(count))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [(124, "git timed out after 4s"), (git_monitor.GIT_OUTPUT_CAPPED, "git output exceeded")],
)
async def test_a_diffstat_too_big_to_count_is_not_retried_while_its_change_set_churns(
    monkeypatch: pytest.MonkeyPatch, failure: tuple[int, str]
) -> None:
    """Measured 2026-10-12 and again 2026-10-13, on one checkout.

    First: a change set whose count needs 13s timed out on every read, and nothing
    remembered it. Then, with the failure keyed on the exact fingerprint, 158 of 192
    timed-out diffs still ran again inside the bound, because every file an agent
    added to the 1,871-entry set moved the fingerprint. Running out of budget is a
    property of *how much* changed, so only a set half the size, a clean tree, or
    the bound earns another attempt.
    """
    porcelain = {"value": _porcelain(8)}
    outcome = {"value": failure}
    numstat_calls: list[str] = []
    now = _controllable_clock(monkeypatch)

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _fake_git_responses(porcelain["value"])
        if args == ("diff", "--numstat", "HEAD"):
            numstat_calls.append(porcelain["value"])
            return outcome["value"]
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    first = await read_git_state("C:/repo")
    assert (first.added, first.removed) == (None, None)
    assert len(numstat_calls) == 1

    now["value"] += 60
    await read_git_state("C:/repo")
    assert len(numstat_calls) == 1, "the same change set is not asked again"

    porcelain["value"] = _porcelain(9)
    await read_git_state("C:/repo")
    porcelain["value"] = _porcelain(5)
    churned = await read_git_state("C:/repo")
    assert len(numstat_calls) == 1, "a set that grew, or shrank a little, is no cheaper"
    assert (churned.added, churned.removed) == (None, None)

    porcelain["value"] = _porcelain(4)
    await read_git_state("C:/repo")
    assert len(numstat_calls) == 2, "half the set it failed on is worth asking about"

    porcelain["value"] = _porcelain(3)
    await read_git_state("C:/repo")
    assert len(numstat_calls) == 2, "and that failure is measured from its own size"

    now["value"] += git_monitor.DIFFSTAT_FAILURE_RETRY_SECONDS
    outcome["value"] = (0, "7\t2\tfile0.json\n1\t0\tfile1.json")
    recovered = await read_git_state("C:/repo")
    assert len(numstat_calls) == 3, "a failure is re-tested once its bound passes"
    assert (recovered.added, recovered.removed) == (8, 2)


@pytest.mark.asyncio
async def test_a_clean_tree_forgets_a_diffstat_too_big_to_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean tree proves the old change set gone, so the next one is asked at once."""
    porcelain = {"value": _porcelain(8)}
    numstat_calls: list[str] = []
    _controllable_clock(monkeypatch)

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        if args == ("diff", "--numstat", "HEAD"):
            numstat_calls.append(porcelain["value"])
            return 124, "git timed out after 4s"
        return _fake_git_responses(porcelain["value"])[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    await read_git_state("C:/repo")
    porcelain["value"] = ""
    clean = await read_git_state("C:/repo")
    assert (clean.added, clean.removed) == (0, 0)
    assert len(numstat_calls) == 1, "a clean tree is counted without git"

    porcelain["value"] = _porcelain(8)
    await read_git_state("C:/repo")
    assert len(numstat_calls) == 2, "a new change set after a clean tree is a new question"


@pytest.mark.asyncio
async def test_a_diffstat_git_refused_is_asked_again_when_the_change_set_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal, unlike a timeout, is about this exact tree, not about its size."""
    porcelain = {"value": " M file.txt"}
    numstat_calls: list[str] = []
    now = _controllable_clock(monkeypatch)

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        if args == ("diff", "--numstat", "HEAD"):
            numstat_calls.append(porcelain["value"])
            return 128, "fatal: unable to read tree"
        return _fake_git_responses(porcelain["value"])[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    await read_git_state("C:/repo")
    now["value"] += 60
    await read_git_state("C:/repo")
    assert len(numstat_calls) == 1, "the same change set is not asked again"

    porcelain["value"] = " M file.txt\n M other.txt"
    await read_git_state("C:/repo")
    assert len(numstat_calls) == 2, "a different change set is a different question"


@pytest.mark.asyncio
async def test_the_ignored_listing_has_more_room_than_an_ordinary_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Measured 2026-10-13: 130,892 individually ignored files made a 17.2 MB listing.

    Under the shared 16 MiB cap it failed on every refresh, which left the checkout
    with the most noise as the one checkout with no ignore rules at all.
    """
    limits: list[int] = []

    async def fake_git(
        cwd: str,
        *args: str,
        timeout_seconds: float = 4.0,
        output_limit: int = git_monitor.GIT_OUTPUT_LIMIT_BYTES,
    ) -> tuple[int, str]:
        del cwd, args, timeout_seconds
        limits.append(output_limit)
        return 0, "dist/"

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    assert await git_monitor._read_ignored_listing("C:/repo") == "dist/"
    assert limits == [git_monitor.IGNORED_LISTING_LIMIT_BYTES]
    assert git_monitor.IGNORED_LISTING_LIMIT_BYTES > git_monitor.GIT_OUTPUT_LIMIT_BYTES


@pytest.mark.asyncio
async def test_unborn_branch_has_no_diffstat(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no HEAD there is nothing to diff against, so nothing is claimed."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _fake_git_responses(" M file.txt")
        responses[("rev-parse", "HEAD")] = (128, "fatal: ambiguous argument 'HEAD'")
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert state.added is None and state.removed is None


def test_numstat_parsing_survives_binary_files() -> None:
    """Binary rows report `-` for both counts; a PNG must not void the sum."""
    output = "3\t1\tfile.txt\n-\t-\timage.png\n12\t0\tother.txt"
    assert git_monitor.parse_numstat(output) == (15, 1)


def test_numstat_summary_counts_binary_files_as_changed_files() -> None:
    """A binary file contributes no lines and is still a changed file."""
    output = "3\t1\tfile.txt\n-\t-\timage.png\n12\t0\tother.txt"
    assert git_monitor.parse_numstat_summary(output) == (15, 1, 3)


# --- Branch-scoped comparison -------------------------------------------------

_BASE_REF_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
_MERGE_BASE_SHA = "cccccccccccccccccccccccccccccccccccccccc"


def _comparison_git_responses(porcelain: str) -> dict[tuple[str, ...], tuple[int, str]]:
    responses = _fake_git_responses(porcelain)
    responses[("rev-parse", "--verify", "origin/main^{commit}")] = (0, _BASE_REF_SHA)
    responses[("merge-base", _BASE_REF_SHA, _FULL_SHA)] = (0, _MERGE_BASE_SHA)
    responses[("diff", "--numstat", _MERGE_BASE_SHA)] = (
        0,
        "40\t8\ta.txt\n5\t0\tb.txt\n-\t-\tlogo.png",
    )
    return responses


def _use_comparison(monkeypatch: pytest.MonkeyPatch, ref: str = "origin/main") -> list[str | None]:
    """Stub inference to `ref`, recording every override it was asked about."""
    asked: list[str | None] = []

    async def resolve(repository: str, override: str | None) -> dict[str, Any]:
        del repository
        asked.append(override)
        return _available_comparison(ref)

    monkeypatch.setattr(git_monitor, "resolve_comparison_ref", resolve)
    return asked


@pytest.mark.asyncio
async def test_comparison_is_measured_from_the_merge_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch-scoped diff covers committed work the HEAD diff has already lost.

    Measured from the merge base rather than from the ref itself: diffing against
    a base that has advanced reports its inbound commits as this branch's
    deletions, which reads as work destroyed rather than work not yet merged.
    """

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _comparison_git_responses(" M file.txt")[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    _use_comparison(monkeypatch)
    state = await read_git_state("C:/repo")
    assert state.root == "C:/repo"
    assert state.compare_ref == "origin/main"
    assert (state.compare_added, state.compare_removed, state.compare_files) == (45, 8, 3)
    # The HEAD-scoped pair is a different fact and keeps its own, smaller answer.
    assert (state.added, state.removed) == (3, 1)


@pytest.mark.asyncio
async def test_comparison_is_memoized_on_base_head_and_dirty_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It re-runs when the base moves, when HEAD moves, and when the tree moves.

    All three, because the branch diff changes for a strictly larger set of
    reasons than the working-tree diff: committing changes it while leaving the
    dirty fingerprint untouched, and the base advances underneath it.
    """
    head = {"value": _FULL_SHA}
    base_oid = {"value": _BASE_REF_SHA}
    porcelain = {"value": " M file.txt"}
    diffs: list[tuple[str, ...]] = []

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _comparison_git_responses(porcelain["value"])
        responses[("rev-parse", "HEAD")] = (0, head["value"])
        responses[("rev-parse", "--verify", "origin/main^{commit}")] = (0, base_oid["value"])
        responses[("merge-base", base_oid["value"], head["value"])] = (0, _MERGE_BASE_SHA)
        responses[("diff", "--numstat", "HEAD")] = (0, "3\t1\tfile.txt")
        if args[:2] == ("diff", "--numstat") and args[2] == _MERGE_BASE_SHA:
            diffs.append(args)
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    _use_comparison(monkeypatch)

    await read_git_state("C:/repo")
    assert len(diffs) == 1
    await read_git_state("C:/repo")
    assert len(diffs) == 1, "nothing moved, so no second measurement"

    porcelain["value"] = " M file.txt\n M other.txt"
    await read_git_state("C:/repo")
    assert len(diffs) == 2, "the working tree moved"

    head["value"] = "d" * 40
    await read_git_state("C:/repo")
    assert len(diffs) == 3, "a commit moved HEAD without touching the tree"

    base_oid["value"] = "e" * 40
    await read_git_state("C:/repo")
    assert len(diffs) == 4, "the base advanced underneath the branch"


@pytest.mark.asyncio
async def test_comparison_ref_is_inferred_once_per_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inference is several git calls answering a question that changes rarely."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _comparison_git_responses(" M file.txt")[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    asked = _use_comparison(monkeypatch)

    await read_git_state("C:/repo")
    await read_git_state("C:/repo")
    await read_git_state("C:/repo/subdir")
    assert asked == [None], "one inference covers every poll and every subdirectory"

    # A different Project override is a different question, not a cache hit.
    await read_git_state("C:/repo", "release/2.0")
    assert asked == [None, "release/2.0"]


@pytest.mark.asyncio
async def test_a_deleted_comparison_ref_is_re_inferred_rather_than_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached ref that stopped resolving must not be trusted until its TTL ends."""
    resolves = {"value": True}

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _comparison_git_responses(" M file.txt")
        if not resolves["value"]:
            responses[("rev-parse", "--verify", "origin/main^{commit}")] = (128, "fatal")
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    asked = _use_comparison(monkeypatch)

    await read_git_state("C:/repo")
    assert len(asked) == 1

    # The cached name stops resolving. That poll still uses the cached name (it
    # is what it had), measures nothing, and drops the cache on the way out.
    resolves["value"] = False
    state = await read_git_state("C:/repo")
    assert state.compare_added is None, "an unresolvable base measures nothing"
    assert len(asked) == 1

    # So the next poll re-infers instead of waiting out the TTL on a dead ref.
    await read_git_state("C:/repo")
    assert len(asked) == 2


@pytest.mark.asyncio
async def test_unmeasurable_comparison_is_none_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero here would claim a branch identical to its base. It never guesses that."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _comparison_git_responses(" M file.txt")
        responses[("merge-base", _BASE_REF_SHA, _FULL_SHA)] = (128, "fatal: no merge base")
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    _use_comparison(monkeypatch)
    state = await read_git_state("C:/repo")
    assert state.compare_ref == "origin/main"
    assert state.compare_added is None
    assert state.compare_removed is None
    assert state.compare_files is None


@pytest.mark.asyncio
async def test_a_comparison_too_big_to_count_is_not_retried_until_it_could_have_shrunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branch diverged too far to count in time stays that way on the next read.

    Through a commit as well: committing moves work from the tree into the branch,
    and the branch is exactly what this diff measures. Only a smaller change set or
    the bound re-asks.
    """
    head = {"value": _FULL_SHA}
    porcelain = {"value": _porcelain(4)}
    merge_bases: list[str] = []
    diffs: list[str] = []
    now = _controllable_clock(monkeypatch)

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _comparison_git_responses(porcelain["value"])
        responses[("rev-parse", "HEAD")] = (0, head["value"])
        responses[("merge-base", _BASE_REF_SHA, head["value"])] = (0, _MERGE_BASE_SHA)
        if args[0] == "merge-base":
            merge_bases.append(head["value"])
        if args == ("diff", "--numstat", _MERGE_BASE_SHA):
            diffs.append(head["value"])
            return 124, "git timed out after 4s"
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    _use_comparison(monkeypatch)

    first = await read_git_state("C:/repo")
    assert (first.compare_added, first.compare_removed, first.compare_files) == (
        None,
        None,
        None,
    )
    assert (first.added, first.removed) == (3, 1), "the HEAD diff is a separate answer"
    now["value"] += 60
    await read_git_state("C:/repo")
    assert (len(merge_bases), len(diffs)) == (1, 1), "nothing moved, so nothing is re-run"

    porcelain["value"] = _porcelain(5)
    await read_git_state("C:/repo")
    head["value"] = "d" * 40
    await read_git_state("C:/repo")
    assert (len(merge_bases), len(diffs)) == (1, 1), "neither churn nor a commit shrinks it"

    porcelain["value"] = _porcelain(2)
    await read_git_state("C:/repo")
    assert (len(merge_bases), len(diffs)) == (2, 2), "half the change set is worth asking"

    now["value"] += git_monitor.DIFFSTAT_FAILURE_RETRY_SECONDS
    await read_git_state("C:/repo")
    assert (len(merge_bases), len(diffs)) == (3, 3), "and so is the bound passing"


@pytest.mark.asyncio
async def test_a_comparison_git_refused_is_asked_again_when_anything_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused diff stands for its exact fingerprint, a commit included."""
    head = {"value": _FULL_SHA}
    diffs: list[str] = []
    _controllable_clock(monkeypatch)

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _comparison_git_responses(" M file.txt")
        responses[("rev-parse", "HEAD")] = (0, head["value"])
        responses[("merge-base", _BASE_REF_SHA, head["value"])] = (0, _MERGE_BASE_SHA)
        if args == ("diff", "--numstat", _MERGE_BASE_SHA):
            diffs.append(head["value"])
            return 128, "fatal: bad object"
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    _use_comparison(monkeypatch)
    await read_git_state("C:/repo")
    await read_git_state("C:/repo")
    assert len(diffs) == 1
    head["value"] = "d" * 40
    await read_git_state("C:/repo")
    assert len(diffs) == 2, "a commit is a different question"


@pytest.mark.asyncio
async def test_an_unrelated_base_is_not_asked_for_a_merge_base_on_every_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A merge base depends on two commits and nothing else, so it is keyed on them.

    Churn in the working tree used to re-ask it, because the failure shared the
    comparison's fingerprint, which carries the tree.
    """
    merge_bases = 0
    head = {"value": _FULL_SHA}
    porcelain = {"value": " M file.txt"}
    _controllable_clock(monkeypatch)

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        nonlocal merge_bases
        del cwd, timeout_seconds
        responses = _comparison_git_responses(porcelain["value"])
        responses[("rev-parse", "HEAD")] = (0, head["value"])
        if args[0] == "merge-base":
            merge_bases += 1
            return 1, ""
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    _use_comparison(monkeypatch)
    for index in range(3):
        porcelain["value"] = _porcelain(index + 1)
        state = await read_git_state("C:/repo")
        assert state.compare_added is None
    assert merge_bases == 1

    head["value"] = "d" * 40
    await read_git_state("C:/repo")
    assert merge_bases == 2, "a commit is the one thing that can change the answer"


@pytest.mark.asyncio
async def test_no_resolvable_base_leaves_the_branch_fields_unmeasured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repository with no main, no master, and no remote HEAD simply says nothing."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _fake_git_responses(" M file.txt")[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert state.compare_ref is None
    assert state.compare_added is None
    # The working-tree pair is independent of the base and stays measured.
    assert (state.added, state.removed) == (3, 1)


@pytest.mark.parametrize(
    ("cwd", "common_dir"),
    [
        # The exact replies real git gives, which are relative whenever it can be:
        # `.git` from a repository root, `../.git` from a subdirectory. Resolving
        # either against the daemon's own cwd rather than the directory git ran in
        # made every primary checkout read as a worktree named after the repo.
        (ABS_ROOT, '.git'),
        (f'{ABS_ROOT}/frontend', '../.git'),
        (ABS_ROOT, f'{ABS_ROOT}/.git'),
    ],
)
@pytest.mark.asyncio
async def test_primary_checkout_is_never_a_worktree(
    monkeypatch: pytest.MonkeyPatch, cwd: str, common_dir: str
) -> None:
    async def primary(_cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del _cwd, timeout_seconds
        responses = _fake_git_responses("")
        responses[("rev-parse", "--absolute-git-dir", "--git-common-dir")] = (
            0,
            f"{ABS_ROOT}/.git\n{common_dir}",
        )
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", primary)
    assert (await read_git_state(cwd)).worktree is None


@pytest.mark.asyncio
async def test_linked_worktree_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comparing git-dir to git-common-dir is the check that stays correct.

    Comparing directory *names* would misreport bare repositories and `.git`-file
    submodules; the two paths only diverge for a genuinely linked worktree.
    """

    async def linked(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _fake_git_responses("")
        responses[("rev-parse", "--show-toplevel")] = (0, "C:/repo/.worktrees/wt-audit")
        responses[("rev-parse", "--absolute-git-dir", "--git-common-dir")] = (
            0,
            "C:/repo/.git/worktrees/wt-audit\nC:/repo/.git",
        )
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", linked)
    assert (await read_git_state("C:/repo/.worktrees/wt-audit")).worktree == "wt-audit"


@pytest.mark.asyncio
async def test_unborn_branch_reports_no_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo with no commits must report head=None rather than an error string."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _fake_git_responses("")
        responses[("rev-parse", "HEAD")] = (128, "fatal: ambiguous argument 'HEAD'")
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    reading = await read_git_reading("C:/repo")
    assert reading.evidence.head is None


@pytest.mark.asyncio
async def test_unique_git_poll_deduplicates_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_read(cwd: str):  # type: ignore[no-untyped-def]
        calls.append(cwd)
        return git_monitor.GitState(branch=Path(cwd).name)

    monkeypatch.setattr(git_monitor, "read_git_state", fake_read)
    result = await read_unique_git_states(["one", "one", "two"])
    assert set(result) == {"one", "two"}
    assert sorted(calls) == ["one", "two"]


class _FakeSession:
    """The attributes `GitMonitor._poll` touches on a session."""

    def __init__(
        self,
        cwd: str,
        attached: bool,
        git: git_monitor.GitState,
        state: str = "idle",
    ) -> None:
        self.subscribers = [object()] if attached else []
        self.attended = attached
        # `project_id` rides every `git_changed`: it is what lets the Git tab tell a
        # poll about *its* repository from one about somebody else's.
        self.record = SimpleNamespace(
            id=cwd, git=git, git_cwd=cwd, state=state, project_id="project-1"
        )
        self.published = 0

    def publish_update(self) -> None:
        self.published += 1


def _monitor(sessions: list[_FakeSession]) -> git_monitor.GitMonitor:
    manager = cast(Any, SimpleNamespace(sessions={str(i): s for i, s in enumerate(sessions)}))
    return git_monitor.GitMonitor(manager, EventBus())


@pytest.mark.asyncio
async def test_detached_sessions_are_swept_so_stale_git_state_cannot_outlive_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed pane must not freeze a derived value forever.

    `GitState` is a cache of an observation, living on a record that outlives the
    daemon that wrote it. Without a sweep, a value computed by code that has since
    been fixed survives the fix for as long as the session lives — which is how a
    wrong worktree name kept rendering after its bug was gone.
    """
    fresh = git_monitor.GitState(branch="master", worktree=None, root="C:/repo")

    async def fake_read(cwds):  # type: ignore[no-untyped-def]
        return {cwd: git_monitor.GitReading(fresh, git_monitor.GitEvidence()) for cwd in cwds}

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)
    stale = git_monitor.GitState(branch="master", worktree="swe-mux", root="C:/repo")
    attached = _FakeSession("C:/repo", True, stale)
    attached.record.git_cwd = "C:/repo/attached"
    detached = _FakeSession("C:/repo", False, stale)
    monitor = _monitor([attached, detached])

    # The first tick always reads everything: state adopted from a previous daemon
    # is re-derived by this one rather than trusted.
    await monitor._poll(now=0.0)
    assert attached.record.git.worktree is None
    assert detached.record.git.worktree is None

    # These readings carry no layout, so nothing is watched and the checkouts keep
    # timed polling: attached at the cadence, detached on the slower sweep.
    detached.record.git = stale
    await monitor._poll(now=monitor.cadence)
    assert detached.record.git.worktree == "swe-mux", "a cadence tick stays attached-only"

    await monitor._poll(now=git_monitor.GitMonitor.UNWATCHED_DETACHED_SECONDS)
    assert detached.record.git.worktree is None, "the sweep must come back around"


@pytest.mark.asyncio
async def test_an_ended_sessions_git_reading_is_frozen_at_its_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every field on `GitState` describes the *checkout*, not the session.

    Following it onward would attribute whatever somebody changed afterwards to a
    session that could not have made it - and a retained ended pane keeps its
    subscribers, so without the guard a dead row would poll Git forever.
    """
    reads: list[list[str]] = []

    async def fake_read(cwds):  # type: ignore[no-untyped-def]
        listed = list(cwds)
        reads.append(listed)
        fresh = git_monitor.GitReading(
            git_monitor.GitState(branch="master", worktree=None), git_monitor.GitEvidence()
        )
        return {cwd: fresh for cwd in listed}

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)
    at_death = git_monitor.GitState(branch="feature", worktree="swe-mux")
    ended = _FakeSession("C:/dead", True, at_death, state="exited")
    cold = _FakeSession("C:/cold", True, at_death, state="crashed")
    monitor = _monitor([ended, cold])

    # The first tick is a full sweep, so nothing else can be excluding them.
    await monitor._poll()

    assert not any(reads), "no checkout is read on behalf of a session with no process"
    assert ended.record.git.worktree == "swe-mux"
    assert cold.record.git.worktree == "swe-mux"


@pytest.mark.asyncio
async def test_sweeping_the_fleet_costs_one_read_per_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What makes the sweep affordable: cost scales with cwds, not sessions."""
    seen: list[list[str]] = []

    async def fake_read(cwds):  # type: ignore[no-untyped-def]
        listed = list(cwds)
        seen.append(listed)
        empty = git_monitor.GitReading(git_monitor.GitState(), git_monitor.GitEvidence())
        return dict.fromkeys(listed, empty)

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)
    fleet = [_FakeSession("C:/repo", False, git_monitor.GitState()) for _ in range(30)]
    fleet.append(_FakeSession("C:/other", False, git_monitor.GitState()))
    await _monitor(fleet)._poll()
    assert sorted(seen[0]) == [("C:/other", None), ("C:/repo", None)]


@pytest.mark.asyncio
async def test_head_only_change_emits_the_previous_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = "a" * 40
    new = "b" * 40
    state = git_monitor.GitState(branch="master", root="C:/repo", head=new)

    async def fake_read(cwds):  # type: ignore[no-untyped-def]
        return {
            cwd: git_monitor.GitReading(state, git_monitor.GitEvidence(head=new))
            for cwd in cwds
        }

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)
    events = EventBus()
    queue = events.subscribe(name="test")
    session = _FakeSession(
        "C:/repo",
        True,
        git_monitor.GitState(branch="master", root="C:/repo", head=old),
    )
    manager = cast(Any, SimpleNamespace(sessions={"1": session}))
    monitor = git_monitor.GitMonitor(manager, events)

    await monitor._poll()
    event = queue.get_nowait()

    assert event.type == "git_changed"
    assert event.payload["head"] == new
    assert event.payload["previous_head"] == old


@pytest.mark.asyncio
async def test_one_checkout_two_projects_do_not_share_a_comparison_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll key is the checkout *and* its base, not the checkout alone.

    Two Projects may legitimately point at one directory with different
    comparison refs. Collapsing them onto the cwd would serve whichever polled
    first, and the other Project's rows would quote a base nobody configured.
    """
    seen: list[list[tuple[str, str | None]]] = []

    async def fake_read(targets):  # type: ignore[no-untyped-def]
        listed = list(targets)
        seen.append(listed)
        empty = git_monitor.GitReading(git_monitor.GitState(), git_monitor.GitEvidence())
        return dict.fromkeys(listed, empty)

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)
    one = _FakeSession("C:/repo", False, git_monitor.GitState())
    one.record.project_id = "p1"
    two = _FakeSession("C:/repo", False, git_monitor.GitState())
    two.record.project_id = "p2"
    manager = cast(Any, SimpleNamespace(sessions={"1": one, "2": two}))
    monitor = git_monitor.GitMonitor(
        manager, EventBus(), compare_override=lambda pid: "origin/main" if pid == "p1" else None
    )
    await monitor._poll()
    assert set(seen[0]) == {("C:/repo", None), ("C:/repo", "origin/main")}


@pytest.mark.asyncio
async def test_a_failing_override_lookup_does_not_stop_the_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken Project lookup degrades to automatic inference, not to no Git state."""

    async def fake_read(targets):  # type: ignore[no-untyped-def]
        empty = git_monitor.GitReading(
            git_monitor.GitState(branch="master"), git_monitor.GitEvidence()
        )
        return dict.fromkeys(list(targets), empty)

    monkeypatch.setattr(git_monitor, "read_unique_git_readings", fake_read)

    def explode(project_id: str) -> str | None:
        raise RuntimeError(project_id)

    session = _FakeSession("C:/repo", True, git_monitor.GitState())
    session.record.project_id = "p1"
    manager = cast(Any, SimpleNamespace(sessions={"1": session}))
    await git_monitor.GitMonitor(manager, EventBus(), compare_override=explode)._poll()
    assert session.record.git.branch == "master"


@pytest.mark.asyncio
async def test_git_timeout_kills_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowProcess:
        # A pid that cannot exist keeps reap_process_tree's psutil descendant
        # scan a no-op instead of inspecting an unrelated live process.
        pid = 2**22 + 12345
        returncode = None
        killed = False
        reaped = False

        def __init__(self) -> None:
            # The bounded runner drains both pipes concurrently rather than
            # buffering through `communicate()`, so a fake process needs streams.
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()
            self._killed = asyncio.Event()

        def kill(self) -> None:
            self.killed = True
            self._killed.set()

        async def wait(self) -> int:
            # Ends only when killed, so the timeout is what the runner observes
            # and the reap is what ends the wait - the order under test.
            await self._killed.wait()
            self.reaped = True
            self.returncode = -9
            return -9

    process = SlowProcess()

    async def spawn(*args: object, **kwargs: object) -> SlowProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    code, message = await git_monitor._git(".", "status", timeout_seconds=0.001)
    assert code == 124
    assert "timed out" in message
    assert process.killed
    assert process.reaped


def test_worktree_porcelain_parser_preserves_registration_metadata() -> None:
    items = git_review.parse_worktrees(
        "worktree C:/repo\nHEAD abc123\nbranch refs/heads/main\n\n"
        "worktree C:/repo-feature\nHEAD def456\ndetached\n\n"
    )
    assert items == [
        {"worktree": "C:/repo", "HEAD": "abc123", "branch": "refs/heads/main"},
        {"worktree": "C:/repo-feature", "HEAD": "def456", "detached": True},
    ]


@pytest.mark.asyncio
async def test_repository_reads_never_write_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A monitor must not mutate what it monitors.

    `git status` and `git diff` refresh the index and write it back whenever a tracked
    file's mtime has moved. In a repository where agents are editing files that is
    every poll, so a 5-second read of the branch name was writing to the user's
    repository and taking `.git/index.lock` to do it. Verified 2026-08-05 by touching
    a tracked file and comparing `.git/index` mtime: plain `status` rewrote it,
    `--no-optional-locks status` did not, with byte-identical output.

    The failure this prevents is worse than the waste: a write in flight when the
    daemon is killed strands `index.lock`, which blocks every git operation in that
    repository, for every agent, until someone removes it by hand.
    """
    from swe_mux import git_monitor, git_review

    seen: list[tuple[str, ...]] = []

    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_eof()

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

        async def wait(self) -> int:
            return 0

    async def fake_exec(*args: str, **_kwargs: object) -> FakeProcess:
        seen.append(args)
        return FakeProcess()

    monkeypatch.setattr(git_monitor.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(git_review.asyncio, "create_subprocess_exec", fake_exec)

    await git_monitor._git("D:/repo", "status", "--porcelain")
    await git_review._run_git_bytes("D:/repo", "diff", "--numstat")

    assert seen, "no git invocation was captured"
    for invocation in seen:
        assert invocation[0] == "git"
        # The options are global and must precede the subcommand to apply at all.
        assert invocation[1 : 1 + len(READ_ONLY_GIT_OPTIONS)] == READ_ONLY_GIT_OPTIONS, (
            f"read-only git call may not take the index lock: {invocation}"
        )
        assert invocation.index("-C") == 1 + len(READ_ONLY_GIT_OPTIONS)


def _stat_dirty_repository(root: Path) -> tuple[Path, Path]:
    """A repository with one edited file and one file whose stat moved but content did not.

    The second is what makes `git diff` refresh the index. Its mtime is pushed forward
    and the index's pushed back, both with `os.utime`, so a rewrite of the index is
    visible as a changed mtime without betting on filesystem timestamp granularity.
    """

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    root.mkdir(parents=True)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    git("config", "core.autocrlf", "false")
    (root / "same.txt").write_text("unchanged\n", encoding="utf-8")
    (root / "edited.txt").write_text("before\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "init")
    (root / "edited.txt").write_text("after\n", encoding="utf-8")
    stat = (root / "same.txt").stat()
    os.utime(root / "same.txt", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    index = root / ".git" / "index"
    old = index.stat().st_mtime_ns - 3_600_000_000_000
    os.utime(index, ns=(old, old))
    return root, index


@pytest.mark.asyncio
async def test_a_diff_read_never_takes_the_index_lock(tmp_path: Path) -> None:
    """`--no-optional-locks` alone does not make `git diff` read-only; the options do.

    Porcelain `diff` refreshes a stat-dirty index (`diff.autoRefreshIndex`) and takes
    `.git/index.lock` before the refresh, so the monitor's 4-second diffstat of a large
    checkout, killed mid-refresh, stranded an empty lock that blocked every Git write
    in that repository (2026-10-13, `git_readonly.py`). The control half proves the
    probe can see the write: the bare form still rewrites the index here.
    """
    repo, index = _stat_dirty_repository(tmp_path / "control")
    before = index.stat().st_mtime_ns
    await asyncio.to_thread(
        subprocess.run,
        ["git", "--no-optional-locks", "-C", str(repo), "diff", "--numstat", "HEAD"],
        check=True,
        capture_output=True,
    )
    assert index.stat().st_mtime_ns != before, "probe cannot see an index write"

    repo, index = _stat_dirty_repository(tmp_path / "monitored")
    before = index.stat().st_mtime_ns
    code, output = await git_monitor._git(str(repo), "diff", "--numstat", "HEAD")
    reviewed = await git_review._run_git_bytes(str(repo), "diff", "--numstat", "HEAD")
    assert code == 0
    assert output.splitlines() == ["1\t1\tedited.txt"]
    assert reviewed.stdout.decode().splitlines() == ["1\t1\tedited.txt"]
    assert index.stat().st_mtime_ns == before
    assert not (repo / ".git" / "index.lock").exists()
