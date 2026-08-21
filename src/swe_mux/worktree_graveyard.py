"""Fast worktree removal: one rename now, the deletion later.

Removing an agent worktree is honest filesystem work - a checkout carrying
`node_modules` and `.venv` is tens of thousands of small files, and NTFS unlink
plus per-file antivirus scanning turns that into ten to twenty seconds. None of
that time is spent deciding anything, so none of it needs to be spent in front of
the person who pressed the button: the directory is renamed out of the way with
one `MoveFile` call, Git is told to forget the registration, and the bytes are
deleted by a background task afterwards.

**Where the graveyard lives is a correctness question, not a tidiness one.**
It is the repository's own common Git directory (`<common-dir>/swe-mux-graveyard`),
for three reasons that no other location satisfies at once:

  * it is outside every working tree, so a buried checkout can never appear as
    untracked files in `git status` - which would raise dirty counts, and the land
    queue refuses to land a worktree that is not clean;
  * `.git` is the first entry of the default project-ignore list and is skipped by
    the Project watcher and the file surfaces, so a purge deleting thirty thousand
    files does not turn into thirty thousand change events;
  * it is never in `git worktree list`, so Map cannot draw a row for it.

A sibling directory beside the worktree would be same-volume by construction, which
is tempting because it makes the rename always possible - but `.claude/worktrees/`
is only gitignored in repositories that happen to say so, and this repository's own
`.gitignore` is not a fact about anyone else's. The graveyard being on a different
volume from the worktree is handled instead: the rename raises, and the caller falls
back to the ordinary in-place deletion.

**The rename is atomic and its failure is total.** Windows refuses to move a
directory while any handle inside it is open (`WinError 5`/`WinError 32`, measured),
and the source is left exactly as it was - so a defeated rename is a clean signal to
take the slow path rather than a half-moved tree to reason about.

Purging is idempotent and forgiving: whatever it cannot delete stays, and the next
purge (the next removal, or the sweep at daemon start) tries again. Nothing here
ever removes the graveyard root itself, so a purge racing a burial cannot delete a
directory another removal is renaming into.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Inside the repository's common Git directory. Not dot-prefixed: `.git` already
#: hides it from everything that walks a working tree, and a name that reads as a
#: swe-mux artefact is what a human finding it needs.
GRAVEYARD_DIR_NAME = "swe-mux-graveyard"


def graveyard_root(common_dir: str | Path) -> Path:
    """Where buried checkouts wait for deletion, for one repository."""
    return Path(common_dir) / GRAVEYARD_DIR_NAME


def bury(source: str | Path, root: Path, operation_id: str) -> Path:
    """Move ``source`` into the graveyard with one rename.

    Raises ``OSError`` when the rename is defeated - a held handle, a cross-volume
    graveyard, a vanished source. The source is untouched in every one of those
    cases, so the caller's fallback runs against the original tree.
    """
    origin = Path(source)
    root.mkdir(parents=True, exist_ok=True)
    # The operation id, not a counter: it correlates the buried directory with the
    # removal's log line, and it cannot collide with a concurrent burial.
    target = root / f"{origin.name}-{operation_id}"
    os.replace(origin, target)
    return target


def exhume(buried: Path, destination: str | Path) -> bool:
    """Put a buried checkout back where it came from.

    Used when Git refused to drop the registration after the rename: the tree is
    restored exactly, and the ordinary in-place removal answers instead. A failure
    here is reported rather than raised - the caller is already handling one.
    """
    try:
        os.replace(buried, Path(destination))
    except OSError as exc:
        log.warning(
            "worktree_graveyard_restore_failed buried=%s destination=%s error_type=%s",
            buried,
            destination,
            type(exc).__name__,
        )
        return False
    return True


def _clear_readonly(function: Callable[..., Any], path: str, excinfo: BaseException) -> None:
    """Retry one deletion after clearing the read-only bit.

    Git writes its loose objects and packs read-only, and a worktree carrying a
    vendored dependency tree usually carries a few more. On Windows a read-only file
    cannot be unlinked at all, so without this a purge stops on the first one and
    leaves the rest of the tree on disk forever.
    """
    del function, excinfo
    try:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)
    except OSError:
        # Genuinely undeletable (a live handle). Left for the next purge.
        pass


def purge(root: Path) -> tuple[int, int]:
    """Delete every buried checkout under ``root``.

    Returns ``(removed, failed)``. Failures are counted rather than raised: a
    locked file means those bytes wait for the next purge, which is the whole
    reason the graveyard is a directory rather than a temporary name.
    """
    try:
        entries = sorted(root.iterdir())
    except FileNotFoundError:
        return (0, 0)
    except OSError as exc:
        log.warning(
            "worktree_graveyard_unreadable root=%s error_type=%s", root, type(exc).__name__
        )
        return (0, 0)
    removed = failed = 0
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, onexc=_clear_readonly)
            else:
                entry.unlink()
        except OSError as exc:
            failed += 1
            log.warning(
                "worktree_graveyard_purge_failed path=%s error_type=%s error=%s",
                entry,
                type(exc).__name__,
                exc,
            )
            continue
        # `rmtree` with an error handler that swallows can return having left
        # files behind, so success is what the filesystem says afterwards.
        if entry.exists():
            failed += 1
            log.warning("worktree_graveyard_purge_incomplete path=%s", entry)
        else:
            removed += 1
    if removed or failed:
        log.info(
            "worktree_graveyard_purged root=%s removed=%d failed=%d", root, removed, failed
        )
    return (removed, failed)
