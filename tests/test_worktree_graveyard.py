"""Removal that feels instant, against a real repository.

Every assertion here is about the *boundary* the fast path may not cross: it must
remove exactly one registration, refuse exactly what Git refuses, and leave the
checkout untouched whenever it declines. Mocks would prove none of that, so these
build real worktrees and ask Git afterwards.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux import worktree_graveyard, worktree_mutation
from swe_mux.git_operations import GitMutationResult
from swe_mux.routes import git as git_routes
from swe_mux.worktree_graveyard import GraveyardPurger


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    ).stdout.strip()


class FakeEvents:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **payload: Any) -> None:
        self.emitted.append((name, payload))


#: Every purger a test made, stopped at its teardown so no idle thread outlives it.
_PURGERS: list[GraveyardPurger] = []


@pytest.fixture(autouse=True)
def _stop_purgers() -> Iterator[None]:
    yield
    while _PURGERS:
        _PURGERS.pop().stop(timeout=10)


def _purger(**kwargs: Any) -> GraveyardPurger:
    purger = GraveyardPurger(**kwargs)
    _PURGERS.append(purger)
    return purger


class Request:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body
        self.events = FakeEvents()
        self.app: dict[str, Any] = {keys.EVENTS: self.events, keys.GRAVEYARD_PURGER: _purger()}

    async def json(self) -> dict[str, Any]:
        return self._body


async def _settle(request: Request) -> None:
    """Wait for the background purge this removal asked for, if any."""
    purger: GraveyardPurger = request.app[keys.GRAVEYARD_PURGER]
    assert await asyncio.to_thread(purger.wait_idle, 30), "the purge finished"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.invalid")
    (root / "tracked.txt").write_text("first\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-m", "initial")
    return root


def add_worktree(repo: Path, name: str) -> Path:
    path = repo.parent / name
    git(repo, "worktree", "add", "-b", name, str(path))
    return path


def registered_paths(repo: Path) -> set[str]:
    return {
        str(Path(line.split(" ", 1)[1]).resolve()).casefold()
        for line in git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    }


@pytest.mark.asyncio
async def test_a_clean_removal_renames_the_tree_away_and_purges_it(repo: Path) -> None:
    worktree = add_worktree(repo, "quick")
    (worktree / "bulk").mkdir()
    (worktree / "bulk" / "one.txt").write_text("payload\n", encoding="utf-8")
    git(worktree, "add", "bulk/one.txt")
    git(worktree, "commit", "-m", "bulk")

    request = Request({"cwd": str(repo), "path": str(worktree)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["cleanup"]["status"] == "purging"
    buried = Path(payload["cleanup"]["path"])
    assert buried.parent == worktree_graveyard.graveyard_root(repo / ".git")
    # The registration is gone the moment the request answers, and the checkout is
    # no longer where it was - which is the whole point of the rename.
    assert not worktree.exists()
    assert str(worktree.resolve()).casefold() not in registered_paths(repo)
    assert request.events.emitted[0][0] == "worktree_removed"

    await _settle(request)
    assert not buried.exists()


@pytest.mark.asyncio
async def test_removing_one_worktree_leaves_a_broken_sibling_registered(repo: Path) -> None:
    """`git worktree prune` is global; this removal must not be.

    The measured reason the fast path drops the registration with `worktree remove`
    rather than a prune: a prune would also drop `stale`, whose directory merely went
    missing, and with it that checkout's index and reflog.
    """
    healthy = add_worktree(repo, "healthy")
    stale = add_worktree(repo, "stale")
    for item in sorted(stale.rglob("*"), reverse=True):
        item.unlink() if item.is_file() else item.rmdir()
    stale.rmdir()

    request = Request({"cwd": str(repo), "path": str(healthy)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]
    assert response.status == 200
    await _settle(request)

    remaining = registered_paths(repo)
    assert str(healthy.resolve()).casefold() not in remaining
    assert str(stale.resolve()).casefold() in remaining


@pytest.mark.asyncio
async def test_a_dirty_worktree_is_refused_without_force_and_is_left_alone(repo: Path) -> None:
    worktree = add_worktree(repo, "dirty")
    (worktree / "untracked.txt").write_text("mine\n", encoding="utf-8")

    request = Request({"cwd": str(repo), "path": str(worktree)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 400
    assert payload["code"] == "git_error"
    # Not renamed, not partially moved: the file the user has not committed is where
    # they left it, and Git's own sentence is what came back.
    assert (worktree / "untracked.txt").read_text(encoding="utf-8") == "mine\n"
    assert str(worktree.resolve()).casefold() in registered_paths(repo)
    assert not worktree_graveyard.graveyard_root(repo / ".git").exists()


@pytest.mark.asyncio
async def test_force_buries_a_dirty_worktree(repo: Path) -> None:
    worktree = add_worktree(repo, "discardable")
    (worktree / "untracked.txt").write_text("mine\n", encoding="utf-8")

    request = Request({"cwd": str(repo), "path": str(worktree), "force": True})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["cleanup"]["status"] == "purging"
    assert not worktree.exists()
    await _settle(request)


@pytest.mark.asyncio
async def test_a_locked_worktree_is_never_renamed_away(repo: Path) -> None:
    """Measured: Git refuses to remove a locked worktree even once its directory is
    gone, so burying one first would leave a renamed tree beside a live registration."""
    worktree = add_worktree(repo, "locked")
    git(repo, "worktree", "lock", str(worktree))

    request = Request({"cwd": str(repo), "path": str(worktree)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]

    assert response.status == 400
    assert worktree.is_dir()
    assert str(worktree.resolve()).casefold() in registered_paths(repo)
    assert not worktree_graveyard.graveyard_root(repo / ".git").exists()


@pytest.mark.asyncio
async def test_the_main_tree_is_never_renamed_away(repo: Path) -> None:
    """Git refuses to remove the main working tree, so renaming it first would move the
    user's primary checkout out of the way for a removal that was never going to happen."""
    add_worktree(repo, "sibling")

    request = Request({"cwd": str(repo), "path": str(repo), "force": True})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]

    assert response.status == 400
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "first\n"
    assert str(repo.resolve()).casefold() in registered_paths(repo)
    assert not worktree_graveyard.graveyard_root(repo / ".git").exists()


@pytest.mark.asyncio
async def test_a_defeated_rename_falls_back_to_the_in_place_delete(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The known Windows class - an open handle inside the tree - is a clean signal."""
    worktree = add_worktree(repo, "held")

    def refuse(*args: object, **kwargs: object) -> Path:
        del args, kwargs
        raise PermissionError(5, "The process cannot access the file")

    monkeypatch.setattr(worktree_graveyard, "bury", refuse)
    request = Request({"cwd": str(repo), "path": str(worktree)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["cleanup"]["status"] == "removed"
    assert not worktree.exists()
    assert str(worktree.resolve()).casefold() not in registered_paths(repo)


@pytest.mark.asyncio
async def test_git_refusing_after_the_rename_puts_the_tree_back(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = add_worktree(repo, "restored")
    (worktree / "keep.txt").write_text("valuable\n", encoding="utf-8")
    git(worktree, "add", "keep.txt")
    git(worktree, "commit", "-m", "keep")

    async def refuse(cwd: str, *args: str, **kwargs: object) -> Any:
        del cwd, args, kwargs
        return GitMutationResult(1, "git refused for reasons of its own")

    monkeypatch.setattr(worktree_mutation, "run_git_mutation", refuse)
    request = Request({"cwd": str(repo), "path": str(worktree)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]

    assert response.status == 400
    # Exactly where it was, with its contents, and still registered.
    assert (worktree / "keep.txt").read_text(encoding="utf-8") == "valuable\n"
    assert str(worktree.resolve()).casefold() in registered_paths(repo)
    graveyard = worktree_graveyard.graveyard_root(repo / ".git")
    assert not graveyard.exists() or not any(graveyard.iterdir())


def test_purge_clears_read_only_files_and_reports_what_it_removed(tmp_path: Path) -> None:
    """Git writes loose objects read-only, and Windows cannot unlink one at all."""
    root = tmp_path / "graveyard"
    (root / "buried" / "objects").mkdir(parents=True)
    stubborn = root / "buried" / "objects" / "blob"
    stubborn.write_text("packed\n", encoding="utf-8")
    os.chmod(stubborn, stat.S_IREAD)

    assert worktree_graveyard.purge(root) == (1, 0)
    assert not (root / "buried").exists()
    # The root itself survives, so a purge racing a burial cannot delete the
    # directory another removal is renaming into.
    assert root.is_dir()


def _raw(path: Path) -> str:
    """A path Win32 will not reinterpret, written independently of the module's own.

    Lexical on purpose: `os.path.abspath` is `GetFullPathNameW`, which is itself one
    of the calls that turns `...\\nul.json` into `\\\\.\\nul`.
    """
    return "\\\\?\\" + os.path.normpath(path) if os.name == "nt" else str(path)


def test_purge_deletes_names_win32_would_reinterpret_and_paths_past_max_path(
    tmp_path: Path,
) -> None:
    """stellar-matter, 2026-10-17 and 18: one `.trash/nul.json` held a buried checkout.

    On Windows an ordinary path ending in a reserved device name, with any extension,
    is the device, so the unlink deleted nothing and the checkout was retried at
    every daemon start. Git Bash and WSL create such names freely. A trailing dot is
    the same class (Win32 strips it), and a path past MAX_PATH is reached by the
    same verbatim form whatever the process's long-path setting.
    """
    root = tmp_path / "graveyard"
    buried = root / "buried-abc"
    trash = buried / ".trash"
    deep = buried.joinpath(*(["d" * 40] * 7))
    os.makedirs(_raw(trash))
    os.makedirs(_raw(deep))
    names = ["nul.json", "con.txt", "aux", "trailing."]
    for name in names:
        with open(_raw(trash / name), "w", encoding="utf-8") as handle:
            handle.write("x")
    with open(_raw(deep / "leaf.txt"), "w", encoding="utf-8") as handle:
        handle.write("x")
    # The setup really made files, not writes into a device.
    assert sorted(os.listdir(_raw(trash))) == sorted(names)
    assert len(os.path.abspath(deep / "leaf.txt")) > 260

    assert worktree_graveyard.purge(root) == (1, 0)
    assert os.listdir(root) == []


def test_purge_of_a_missing_graveyard_is_not_an_error(tmp_path: Path) -> None:
    assert worktree_graveyard.purge(tmp_path / "never-created") == (0, 0)


def test_a_path_that_is_already_gone_counts_as_purged_and_warns_about_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The D2 finding: 1,165 warnings for directories that were already deleted.

    A graveyard entry can vanish between the listing and the delete - a concurrent
    purge took it, or Windows is still listing a directory it has marked
    delete-pending while reporting every operation on it as missing. Either way the
    postcondition the purge exists for already holds.
    """
    worktree_graveyard.reset_purge_attempts()
    root = tmp_path / "graveyard"
    root.mkdir()
    vanishing = root / "buried-abc"
    vanishing.mkdir()

    real_iterdir = Path.iterdir

    def listing_of_a_ghost(self: Path) -> Any:
        entries = list(real_iterdir(self))
        if self == root:
            # Listed, then gone before anyone touches it.
            vanishing.rmdir()
        return iter(entries)

    with caplog.at_level(logging.WARNING, logger="swe_mux.worktree_graveyard"):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(Path, "iterdir", listing_of_a_ghost)
            removed, failed = worktree_graveyard.purge(root)

    assert (removed, failed) == (1, 0)
    assert [record.message for record in caplog.records] == []


def test_a_delete_that_raises_file_not_found_is_purged_not_failed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_graveyard.reset_purge_attempts()
    root = tmp_path / "graveyard"
    (root / "buried-abc").mkdir(parents=True)

    def vanished(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(worktree_graveyard.shutil, "rmtree", vanished)

    with caplog.at_level(logging.WARNING, logger="swe_mux.worktree_graveyard"):
        removed, failed = worktree_graveyard.purge(root)

    assert (removed, failed) == (1, 0)
    assert not any("purge_failed" in record.message for record in caplog.records)


def test_a_path_that_never_deletes_is_retried_a_bounded_number_of_times(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded, with a terminal line - not 24 identical warnings for one checkout."""
    worktree_graveyard.reset_purge_attempts()
    root = tmp_path / "graveyard"
    (root / "buried-abc").mkdir(parents=True)

    def locked(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(worktree_graveyard.shutil, "rmtree", locked)

    with caplog.at_level(logging.WARNING, logger="swe_mux.worktree_graveyard"):
        outcomes = [worktree_graveyard.purge(root) for _ in range(10)]

    limit = worktree_graveyard.PURGE_ATTEMPT_LIMIT
    # Every purge up to the limit reports the failure; every one after it skips the
    # path without a word, and the tree is still on disk for a human to look at.
    assert outcomes[: limit - 1] == [(0, 1)] * (limit - 1)
    assert outcomes[limit - 1] == (0, 1)
    assert outcomes[limit:] == [(0, 0)] * (10 - limit)
    failures = [r for r in caplog.records if "purge_failed" in r.message]
    abandonments = [r for r in caplog.records if "purge_abandoned" in r.message]
    assert len(failures) == limit - 1
    assert len(abandonments) == 1
    assert (root / "buried-abc").is_dir()


def test_a_path_that_finally_deletes_forgets_its_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient lock must not spend the budget a later real failure needs."""
    worktree_graveyard.reset_purge_attempts()
    root = tmp_path / "graveyard"
    buried = root / "buried-abc"
    buried.mkdir(parents=True)

    real_rmtree = worktree_graveyard.shutil.rmtree
    attempts = {"count": 0}

    def flaky(path: Any, **kwargs: Any) -> None:
        attempts["count"] += 1
        if attempts["count"] <= worktree_graveyard.PURGE_ATTEMPT_LIMIT - 1:
            raise PermissionError(13, "still locked")
        real_rmtree(path, **kwargs)

    monkeypatch.setattr(worktree_graveyard.shutil, "rmtree", flaky)

    limit = worktree_graveyard.PURGE_ATTEMPT_LIMIT
    outcomes = [worktree_graveyard.purge(root) for _ in range(limit)]

    assert outcomes[-1] == (1, 0)
    assert not buried.exists()
    key = worktree_graveyard._purge_key(buried)
    assert key not in worktree_graveyard._purge_attempts
    assert key not in worktree_graveyard._purge_abandoned


def test_purging_an_empty_graveyard_twice_says_nothing_either_time(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    worktree_graveyard.reset_purge_attempts()
    root = tmp_path / "graveyard"
    (root / "buried-abc").mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="swe_mux.worktree_graveyard"):
        first = worktree_graveyard.purge(root)
        second = worktree_graveyard.purge(root)

    assert (first, second) == ((1, 0), (0, 0))
    assert [record.message for record in caplog.records] == []


def test_the_startup_sweep_clears_what_a_killed_purge_left(repo: Path, tmp_path: Path) -> None:
    graveyard = worktree_graveyard.graveyard_root(repo / ".git")
    (graveyard / "leftover-abc").mkdir(parents=True)
    (graveyard / "leftover-abc" / "file.txt").write_text("stale\n", encoding="utf-8")
    # A Project root that is a linked worktree carries a `.git` *file* and is skipped
    # rather than resolved with a subprocess on the startup path.
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    roots = [str(repo), str(linked), str(tmp_path / "gone")]
    assert worktree_graveyard.project_graveyards(roots) == [graveyard]
    purger = _purger()
    purger.request_sweep(roots)
    assert purger.wait_idle(30)

    assert not (graveyard / "leftover-abc").exists()
    assert purger.status()["passes"] == 1


def test_a_sweep_being_listed_is_not_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The sweep leaves `_sweeps` before its graveyards are listed, and nothing is
    # waiting or active yet, so without its own mark the purger read as idle in that
    # window and `wait_idle` returned before the pass it was about to queue.
    listing = threading.Event()
    release = threading.Event()
    graveyard = tmp_path / "graveyard"

    def slow_listing(project_roots: Any) -> list[Path]:
        listing.set()
        assert release.wait(10)
        return [graveyard]

    monkeypatch.setattr(worktree_graveyard, "project_graveyards", slow_listing)
    purged: list[Path] = []

    def record(root: Path, *, stop: threading.Event | None = None) -> tuple[int, int]:
        purged.append(root)
        return 0, 0

    purger = _purger(purge_fn=record)
    purger.request_sweep(["project"])
    assert listing.wait(10)
    assert not purger.idle()
    assert purger.status()["sweeping"] is True
    assert purger.wait_idle(0.05) is False

    release.set()
    assert purger.wait_idle(10)
    assert purged == [graveyard]
    assert purger.status()["sweeping"] is False


# --- the purger -----------------------------------------------------------------------


class _HeldPurge:
    """A purge function that blocks until released, recording every call."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.entered = threading.Event()
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, root: Path, *, stop: threading.Event | None = None) -> tuple[int, int]:
        self.calls.append((root, threading.current_thread().name))
        self.entered.set()
        assert self.release.wait(30), "the test released the purge"
        return (1, 0)


def test_a_burst_of_removals_costs_two_passes_not_one_per_removal(tmp_path: Path) -> None:
    """The 2026-10-18 shape: thirty-five removals inside a minute.

    Each used to start its own purge of the whole graveyard on the shared executor,
    so thirty-two of them held every worker at once. Now the first starts a pass,
    and every request that arrives while it runs folds into exactly one more - the
    one that sees the checkouts buried after the running pass listed the graveyard.
    """
    held = _HeldPurge()
    purger = _purger(purge_fn=held)
    root = tmp_path / "graveyard"

    assert purger.request(root, operation_id="op-0") is True
    assert held.entered.wait(10)
    queued = [purger.request(root, operation_id=f"op-{index}") for index in range(1, 35)]
    assert queued[0] is True and not any(queued[1:]), "one pass queued, the rest absorbed"
    status = purger.status()
    assert status["active"] == str(root)
    assert status["waiting"] == [str(root)]
    assert (status["requests"], status["coalesced"]) == (35, 33)

    held.release.set()
    assert purger.wait_idle(30)
    assert len(held.calls) == 2
    # Never a pool worker: the pass ran on the purger's own thread.
    assert {thread for _root, thread in held.calls} == {worktree_graveyard.PURGER_THREAD_NAME}
    assert purger.status()["passes"] == 2


def test_graveyards_are_purged_one_at_a_time_in_request_order(tmp_path: Path) -> None:
    held = _HeldPurge()
    held.release.set()
    order: list[Path] = []

    def recording(root: Path, *, stop: threading.Event | None = None) -> tuple[int, int]:
        order.append(root)
        return held(root, stop=stop)

    purger = _purger(purge_fn=recording)
    first, second = tmp_path / "one", tmp_path / "two"
    purger.request(first)
    purger.request(second)
    assert purger.wait_idle(30)
    assert order == [first, second]


def test_a_crashing_pass_does_not_end_the_purger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[Path] = []

    def flaky(root: Path, *, stop: threading.Event | None = None) -> tuple[int, int]:
        calls.append(root)
        if len(calls) == 1:
            raise RuntimeError("disk said no")
        return (1, 0)

    purger = _purger(purge_fn=flaky)
    with caplog.at_level(logging.ERROR, logger="swe_mux.worktree_graveyard"):
        purger.request(tmp_path / "graveyard")
        assert purger.wait_idle(30)
    assert purger.status()["last_pass"]["outcome"] == "error"
    assert any("purge_crashed" in record.message for record in caplog.records)

    purger.request(tmp_path / "graveyard")
    assert purger.wait_idle(30)
    assert len(calls) == 2
    assert purger.status()["last_pass"]["outcome"] == "ok"


def test_stopping_drops_what_waits_and_refuses_new_requests(tmp_path: Path) -> None:
    held = _HeldPurge()
    purger = _purger(purge_fn=held)
    purger.request(tmp_path / "one")
    assert held.entered.wait(10)
    purger.request(tmp_path / "two")

    # Not waiting for the running pass: that is the shutdown contract.
    assert purger.stop(timeout=0) is False
    assert purger.request(tmp_path / "three") is False
    assert purger.status()["waiting"] == []
    held.release.set()
    assert purger.stop(timeout=10), "the thread ends after the pass it was running"
    assert [root for root, _thread in held.calls] == [tmp_path / "one"]


def test_a_stopping_purge_finishes_the_checkout_it_holds_and_leaves_the_rest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "graveyard"
    for name in ("a-1", "b-2", "c-3"):
        (root / name).mkdir(parents=True)
    stop = threading.Event()
    real_rmtree = worktree_graveyard.shutil.rmtree

    def stop_after_first(path: Any, **kwargs: Any) -> None:
        real_rmtree(path, **kwargs)
        stop.set()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worktree_graveyard.shutil, "rmtree", stop_after_first)
        assert worktree_graveyard.purge(root, stop=stop) == (1, 0)
    assert sorted(entry.name for entry in root.iterdir()) == ["b-2", "c-3"]


@pytest.mark.asyncio
async def test_a_removal_only_asks_and_the_purger_reports_what_it_did(repo: Path) -> None:
    worktree = add_worktree(repo, "asked")
    request = Request({"cwd": str(repo), "path": str(worktree)})
    response = await git_routes.remove_worktree(request)  # type: ignore[arg-type]
    payload = json.loads(response.text)
    assert payload["cleanup"]["status"] == "purging"

    await _settle(request)
    status = request.app[keys.GRAVEYARD_PURGER].status()
    assert status["passes"] == 1
    assert status["last_pass"]["removed"] == 1
    assert status["last_pass"]["root"] == str(worktree_graveyard.graveyard_root(repo / ".git"))
