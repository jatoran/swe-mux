"""The worktree removal transaction: repair, burial, rollback, quarantine, purge."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import (
    git_review,
    worktree_graveyard,
)
from .git_monitor import _git
from .git_operations import GitMutationResult, run_git_mutation

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


@dataclass(frozen=True, slots=True)
class RemovalRefused:
    """The removal stopped, and what the operator should be told about it.

    `status` is carried here rather than derived at the transport boundary
    because the distinction the codes encode - a timeout is a 504, a refusal is a
    409, Git's own failure is a 400 - is a property of what happened, and a
    second mapping in the route module could disagree with the first.
    """

    code: str
    message: str
    status: int
    #: `None` where the answer is not part of this refusal: a removal refused
    #: before Git ran never repaired anything, and reporting `repaired: false`
    #: there would read as a repair that was tried and failed.
    repaired: bool | None = None
    #: Only `worktree_cleanup_failed` sets these: Git *did* drop the registration,
    #: and the operator needs to know that even though the call failed.
    removed: bool = False
    path: str | None = None


@dataclass(frozen=True, slots=True)
class RemovalCompleted:
    """The registration is gone. What is left is the directory, if anything."""

    registered: str
    repaired: bool
    #: `removed` (nothing left), `quarantined` (a directory Git orphaned, moved
    #: aside), or `purging` (the fast path's graveyard, deleted in the background).
    cleanup_status: str
    cleanup_path: str | None
    #: The graveyard directory the caller should purge off the request path.
    purge_root: Path | None = None


RemovalOutcome = RemovalCompleted | RemovalRefused


async def remove_registered_worktree(
    cwd: str,
    requested: str,
    *,
    force: bool,
    operation_id: str,
) -> RemovalOutcome:
    """Remove one registered worktree, repairing and burying it as needed.

    Multi-stage and each stage can fail differently: a prunable registration is
    repaired first (and the repair is verified against the post-state, not
    against Git's exit code); the tree is renamed into the repository graveyard
    when that cannot change what the removal means; a Git failure after a
    successful rename is rolled back before anything else is decided; and a Git
    failure that nevertheless dropped the registration leaves a directory that is
    quarantined rather than left to look like a live checkout.

    Returns an outcome rather than raising or writing a response: the transaction
    is the same whether it is reached from the Git drawer, a future CLI, or a
    test, and the only part that differs is how the answer is phrased.
    """
    started_at = time.perf_counter()
    log.info(
        "worktree_remove_started operation_id=%s cwd=%s path=%s force=%s",
        operation_id,
        cwd,
        requested,
        force,
    )
    listed = await listed_worktree_entries(cwd)
    entry = listed.get(requested.casefold())
    if not entry:
        log.warning(
            "worktree_remove_refused operation_id=%s cwd=%s path=%s "
            "reason=not_registered duration_ms=%.1f",
            operation_id,
            cwd,
            requested,
            (time.perf_counter() - started_at) * 1000,
        )
        return RemovalRefused(
            code="not_registered_worktree",
            message="path is not a registered worktree for this repository",
            status=409,
        )
    registered = str(entry["worktree"])
    repaired = False
    if "prunable" in entry:
        repair_outcome = await _repair_prunable(
            cwd,
            requested,
            registered,
            entry,
            operation_id=operation_id,
            started_at=started_at,
        )
        if isinstance(repair_outcome, RemovalRefused):
            return repair_outcome
        registered = repair_outcome
        repaired = True
    # The fast path, when it applies: the directory is renamed into the repository's
    # graveyard with one call, and what Git removes afterwards is a registration whose
    # tree is already gone - measured to succeed and to drop only this entry, where
    # `git worktree prune` is global and would take unrelated broken checkouts with it.
    # `bury_worktree` answers `None` for every case where this would change what the
    # removal means, and the code below is then exactly what it always was.
    buried = await bury_worktree(
        registered,
        entry,
        # Git lists the main working tree first, and the pre-repair listing is where
        # that is read from because the main tree is not the thing a repair moves.
        is_main=next(iter(listed), None) == requested.casefold(),
        force=force,
        operation_id=operation_id,
    )
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(registered)
    mutation = await run_git_mutation(
        cwd, *args, operation="worktree_remove", operation_id=operation_id
    )
    if mutation.code and buried is not None:
        # Git kept the registration after the tree was renamed away, which is a state
        # nothing here knows how to reason about. Put the tree back exactly where it
        # was and let the ordinary in-place removal produce Git's own answer.
        restored = await asyncio.to_thread(worktree_graveyard.exhume, buried, registered)
        log.warning(
            "worktree_remove_fast_path_reverted operation_id=%s cwd=%s path=%s "
            "git_code=%s restored=%s",
            operation_id,
            cwd,
            registered,
            mutation.code,
            restored,
        )
        buried = None
        if restored:
            mutation = await run_git_mutation(
                cwd, *args, operation="worktree_remove", operation_id=operation_id
            )
    if mutation.code:
        return await _after_failed_removal(
            cwd,
            requested,
            registered,
            entry,
            mutation=mutation,
            force=force,
            repaired=repaired,
            operation_id=operation_id,
            started_at=started_at,
        )
    cleanup_status = "removed"
    cleanup_path: str | None = None
    purge_root: Path | None = None
    if buried is not None:
        cleanup_status = "purging"
        cleanup_path = str(buried)
        purge_root = buried.parent
    log.info(
        "worktree_remove_completed operation_id=%s cwd=%s path=%s force=%s repaired=%s "
        "cleanup_status=%s buried_path=%s duration_ms=%.1f",
        operation_id,
        cwd,
        registered,
        force,
        repaired,
        cleanup_status,
        cleanup_path or "",
        (time.perf_counter() - started_at) * 1000,
    )
    return RemovalCompleted(
        registered=registered,
        repaired=repaired,
        cleanup_status=cleanup_status,
        cleanup_path=cleanup_path,
        purge_root=purge_root,
    )


async def _repair_prunable(
    cwd: str,
    requested: str,
    registered: str,
    entry: Mapping[str, Any],
    *,
    operation_id: str,
    started_at: float,
) -> str | RemovalRefused:
    """Re-register a prunable worktree, or say why it cannot be re-registered.

    Returns the registered path to remove, which is read back out of the
    post-repair listing rather than assumed: Git's exit code says it tried, and
    only the listing says it worked.
    """
    worktree_path = Path(registered)
    if not worktree_path.is_dir() or (worktree_path / ".git").exists():
        log.warning(
            "worktree_remove_refused operation_id=%s cwd=%s path=%s "
            "reason=prunable_not_repairable prune_reason=%s duration_ms=%.1f",
            operation_id,
            cwd,
            registered,
            entry.get("prunable"),
            (time.perf_counter() - started_at) * 1000,
        )
        return RemovalRefused(
            code="prunable_worktree",
            message="worktree is prunable but cannot be repaired at its registered path",
            status=409,
        )
    log.info(
        "worktree_remove_repair_started operation_id=%s cwd=%s path=%s prune_reason=%s",
        operation_id,
        cwd,
        registered,
        entry.get("prunable"),
    )
    repair = await run_git_mutation(
        cwd,
        "worktree",
        "repair",
        registered,
        operation="worktree_repair",
        operation_id=operation_id,
    )
    try:
        repaired_entries = await listed_worktree_entries(cwd)
    except ValueError as exc:
        log.warning(
            "worktree_remove_repair_failed operation_id=%s cwd=%s path=%s "
            "reason=relist_failed git_code=%s duration_ms=%.1f",
            operation_id,
            cwd,
            registered,
            repair.code,
            (time.perf_counter() - started_at) * 1000,
        )
        return RemovalRefused(
            code="git_timeout" if repair.timed_out else "worktree_repair_failed",
            message=repair.output or str(exc),
            status=504 if repair.timed_out else 409,
        )
    repaired_entry = repaired_entries.get(requested.casefold())
    repair_is_usable = bool(
        repaired_entry
        and "prunable" not in repaired_entry
        and (Path(str(repaired_entry["worktree"])) / ".git").exists()
        and await worktree_root_matches(str(repaired_entry["worktree"]), requested)
    )
    if not repair_is_usable:
        log.warning(
            "worktree_remove_repair_failed operation_id=%s cwd=%s path=%s "
            "reason=unusable_post_state git_code=%s duration_ms=%.1f",
            operation_id,
            cwd,
            registered,
            repair.code,
            (time.perf_counter() - started_at) * 1000,
        )
        return RemovalRefused(
            code="git_timeout" if repair.timed_out else "worktree_repair_failed",
            message=repair.output or "Git did not restore the worktree registration",
            status=504 if repair.timed_out else 409,
        )
    assert repaired_entry is not None
    log.log(
        logging.WARNING if repair.code else logging.INFO,
        "worktree_remove_repair_completed operation_id=%s cwd=%s path=%s git_code=%s",
        operation_id,
        cwd,
        str(repaired_entry["worktree"]),
        repair.code,
    )
    return str(repaired_entry["worktree"])


async def _after_failed_removal(
    cwd: str,
    requested: str,
    registered: str,
    entry: Mapping[str, Any],
    *,
    mutation: GitMutationResult,
    force: bool,
    repaired: bool,
    operation_id: str,
    started_at: float,
) -> RemovalOutcome:
    """Decide what a non-zero `git worktree remove` actually left behind.

    A failure that nevertheless dropped the registration is a success with a
    stranded directory, not a failure: reporting Git's error there would leave
    the operator looking at a checkout the repository no longer knows about.
    """
    try:
        post_remove_entries = await listed_worktree_entries(cwd)
    except ValueError:
        post_remove_entries = {requested.casefold(): dict(entry)}
    if requested.casefold() in post_remove_entries:
        log.warning(
            "worktree_remove_failed operation_id=%s cwd=%s path=%s force=%s repaired=%s "
            "git_code=%s duration_ms=%.1f",
            operation_id,
            cwd,
            registered,
            force,
            repaired,
            mutation.code,
            (time.perf_counter() - started_at) * 1000,
        )
        return RemovalRefused(
            code="git_timeout" if mutation.timed_out else "git_error",
            message=mutation.output or "git worktree remove failed",
            status=504 if mutation.timed_out else 400,
            repaired=repaired,
        )
    cleanup_status = "removed"
    orphaned_path: str | None = None
    if Path(registered).exists():
        try:
            orphaned_path = await asyncio.to_thread(
                quarantine_orphaned_worktree, registered, operation_id
            )
            cleanup_status = "quarantined"
        except OSError as exc:
            log.warning(
                "worktree_remove_cleanup_failed operation_id=%s cwd=%s path=%s "
                "git_code=%s error_type=%s duration_ms=%.1f",
                operation_id,
                cwd,
                registered,
                mutation.code,
                type(exc).__name__,
                (time.perf_counter() - started_at) * 1000,
            )
            return RemovalRefused(
                code="worktree_cleanup_failed",
                message=(
                    "Git removed the worktree registration but its directory "
                    "could not be quarantined"
                ),
                status=409,
                repaired=repaired,
                removed=True,
                path=registered,
            )
    log.warning(
        "worktree_remove_completed operation_id=%s cwd=%s path=%s force=%s "
        "repaired=%s git_code=%s cleanup_status=%s orphaned_path=%s "
        "duration_ms=%.1f",
        operation_id,
        cwd,
        registered,
        force,
        repaired,
        mutation.code,
        cleanup_status,
        orphaned_path or "",
        (time.perf_counter() - started_at) * 1000,
    )
    return RemovalCompleted(
        registered=registered,
        repaired=repaired,
        cleanup_status=cleanup_status,
        cleanup_path=orphaned_path,
    )
