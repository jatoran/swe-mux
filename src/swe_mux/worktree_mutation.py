"""The worktree removal transaction: repair, burial, rollback, quarantine, purge."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import (
    git_review,
    worktree_graveyard,
)
from .git_monitor import _git

log = logging.getLogger(__name__)


async def listed_worktree_entries(cwd: str) -> dict[str, dict[str, Any]]:
    code, output = await _git(cwd, "worktree", "list", "--porcelain")
    if code:
        raise ValueError(output or "unable to inspect repository worktrees")
    return {
        str(Path(str(item["worktree"])).resolve()).casefold(): item
        for item in git_review.parse_worktrees(output)
        if item.get("worktree")
    }


async def listed_worktree_paths(cwd: str) -> dict[str, str]:
    return {
        key: str(item["worktree"])
        for key, item in (await listed_worktree_entries(cwd)).items()
    }


async def worktree_root_matches(path: str, expected: str) -> bool:
    code, reported = await _git(path, "rev-parse", "--show-toplevel")
    if code or not reported.strip():
        return False
    try:
        return str(Path(reported.strip()).resolve()).casefold() == str(
            Path(expected).resolve()
        ).casefold()
    except OSError:
        return False


def quarantine_orphaned_worktree(path: str, operation_id: str) -> str:
    source = Path(path)
    quarantine_root = source.parent / ".swe-mux-orphans"
    quarantine_root.mkdir(parents=False, exist_ok=True)
    target = quarantine_root / f"{source.name}-{operation_id}"
    source.replace(target)
    return str(target)


#: How long the fast path waits for `git status` to say whether a checkout is clean.
WORKTREE_STATUS_TIMEOUT_SECONDS = 20.0


async def worktree_common_dir(worktree_root: str) -> Path | None:
    """The object store this checkout belongs to, asked of the checkout itself.

    Asked *of the worktree* rather than of the Project root on purpose: the answer
    has to be the store that owns this exact tree, and a directory whose `.git` link
    is broken must produce no answer at all rather than the enclosing repository's.
    `--git-common-dir` still replies relatively whenever it can, and relative to
    Git's own working directory - so it is resolved against the worktree, never
    against the daemon's process directory.
    """
    code, reported = await _git(worktree_root, "rev-parse", "--git-common-dir")
    if code or not reported.strip():
        return None
    try:
        return Path(worktree_root).joinpath(reported.strip()).resolve()
    except OSError:
        return None


async def worktree_is_removable_in_place(worktree_root: str) -> bool:
    """Whether Git would delete this tree without `--force`.

    The question the fast path has to answer before renaming anything: Git refuses
    to remove a worktree containing modified or untracked files, and the rename
    would step around that refusal. Asking `status` is the same question Git asks
    itself, and an unreadable answer counts as "no" - the ordinary in-place removal
    then re-asks it and states Git's own refusal, which is the message worth
    showing.
    """
    # Not the four-second observation deadline: this runs inside a mutation route, and a
    # cold `status` over a checkout carrying a dependency tree is exactly the case the
    # fast path exists for. A timeout here is answered as "no", which costs the rename
    # rather than correctness.
    code, output = await _git(
        worktree_root,
        "status",
        "--porcelain",
        "--ignore-submodules=none",
        timeout_seconds=WORKTREE_STATUS_TIMEOUT_SECONDS,
    )
    return code == 0 and not output.strip()


async def bury_worktree(
    registered: str,
    entry: Mapping[str, Any],
    *,
    is_main: bool,
    force: bool,
    operation_id: str,
) -> Path | None:
    """Rename a checkout out of the way so its removal can feel instant.

    Returns the buried path, or ``None`` when the fast path does not apply - in
    which case the caller removes the tree in place exactly as before. Every
    ``None`` is a case where the rename would either be refused or would change
    what the removal means:

      * **the main tree** - Git refuses to remove it at all, so renaming it first
        would move the user's primary checkout out of the way for a removal that
        was never going to happen. Git lists the main working tree first, which is
        what `is_main` is read from; nothing here may infer it from the shape of
        `.git` instead, because a main tree with a `.git` *file* is legal
        (`git init --separate-git-dir`) and the obvious probe would say the opposite.
      * **locked** - measured: Git refuses to remove a locked worktree even once
        its directory is gone, so renaming first would leave a renamed tree and a
        live registration. Git's own refusal is the right answer and needs the tree
        where it is.
      * **submodules** - Git refuses to remove a worktree with populated
        submodules, and burying it would step around a rule this code does not
        reimplement.
      * **not clean, without force** - Git refuses in about fifty milliseconds, so
        the in-place path costs nothing and says why.
      * **no resolvable common directory, or a rename the filesystem refused** -
        a cross-volume graveyard, or the known Windows class where an open handle
        inside the tree defeats the move (`WinError 5`/`32`). The source is
        untouched in both.
    """
    if is_main or "locked" in entry:
        return None
    tree = Path(registered)
    if (tree / ".gitmodules").exists():
        return None
    if not force and not await worktree_is_removable_in_place(registered):
        return None
    common_dir = await worktree_common_dir(registered)
    if common_dir is None:
        return None
    try:
        return await asyncio.to_thread(
            worktree_graveyard.bury,
            registered,
            worktree_graveyard.graveyard_root(common_dir),
            operation_id,
        )
    except OSError as exc:
        log.info(
            "worktree_remove_fast_path_defeated operation_id=%s path=%s error_type=%s error=%s",
            operation_id,
            registered,
            type(exc).__name__,
            exc,
        )
        return None


def sweep_graveyards(roots: Sequence[str]) -> None:
    """Purge leftovers from removals a previous daemon did not finish.

    Filesystem only, no Git: for each Project root whose `.git` is a directory,
    that directory is the common one and its graveyard is purged. A Project root
    that is itself a linked worktree carries a `.git` *file* and is skipped - its
    common directory belongs to a repository that is either registered here in its
    own right or will be swept by the next removal, and resolving it would mean
    running Git for every Project on the startup path.
    """
    for root in roots:
        common = Path(root) / ".git"
        if not common.is_dir():
            continue
        worktree_graveyard.purge(worktree_graveyard.graveyard_root(common))
