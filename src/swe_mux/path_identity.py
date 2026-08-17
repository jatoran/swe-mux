"""Is this the same file, and is this inside that - answered per filesystem.

The shipped answer everywhere was ``str(path.resolve()).casefold()``. On Windows
that is right, because NTFS is case-insensitive and two spellings of one directory
really are one directory. On Linux it is *wrong in the dangerous direction*:
``~/Repo`` and ``~/repo`` are two different directories, and folding them makes a
session, a Project, a transcript, or a worktree containment check silently accept
one for the other. A comparison that over-matches cannot be detected downstream -
everything simply behaves as though the wrong directory were the right one.

Three rules, in order of strength:

1. **Ask the filesystem.** When both paths exist, ``os.path.samefile`` compares
   device and inode (st_dev/st_ino, and the file-id equivalent on Windows). That
   answer is exact and free of every guess below it: symlinks, junctions,
   hardlinks, a UNC path against a mapped drive, a bind mount, and a
   case-sensitive directory on an otherwise case-insensitive volume all resolve
   correctly because none of them are string questions.

2. **Normalize when a path does not exist yet.** Plenty of callers compare a
   recorded path against a live one, and the recorded one may be gone. Then the
   only available answer is textual, and it must at least apply the right case
   rule and the right Unicode form for this host.

3. **Never fold case on a case-sensitive filesystem.** The static per-platform
   default is refined by an actual probe of the directory in question where one
   is available, because both of the interesting hosts can be configured against
   type - a per-directory case-sensitive flag on NTFS, a case-sensitive APFS
   volume on macOS.

Unicode matters on macOS specifically: HFS+ and APFS decompose filenames, so a
path read back from the filesystem can be NFD while the same name typed by a user
or stored in a config file is NFC. They are the same file and compare unequal as
strings, so both sides are normalized to NFC before any textual comparison.
"""

from __future__ import annotations

import functools
import os
import unicodedata
from pathlib import Path, PurePath

from .host_platform import IS_LINUX, IS_MACOS, IS_WINDOWS

__all__ = [
    "canonical_path",
    "identity_key",
    "is_within",
    "paths_are_case_insensitive",
    "same_path",
]


def paths_are_case_insensitive(near: str | os.PathLike[str] | None = None) -> bool:
    """Whether path comparison on this host (or this directory) should fold case.

    ``near`` lets a caller ask about a specific location rather than the platform
    in general, which is what makes a case-sensitive directory on NTFS or a
    case-sensitive APFS volume answerable at all. Without it the platform default
    is used: insensitive on Windows and macOS, sensitive on Linux.
    """
    if near is not None:
        probed = _probe_case_sensitivity(str(near))
        if probed is not None:
            return probed
    if IS_LINUX:
        return False
    return IS_WINDOWS or IS_MACOS


@functools.lru_cache(maxsize=512)
def _probe_case_sensitivity(directory: str) -> bool | None:
    """True when this directory is case-*insensitive*, None when it cannot be told.

    Probing is read-only: it asks whether the same directory, spelled in a
    different case, resolves to the same filesystem object. Creating a temporary
    file to test would be more definitive and is deliberately not done - this runs
    on paths mux does not own, including read-only ones.
    """
    try:
        path = Path(directory)
        if not path.exists():
            path = path.parent
        if not path.exists():
            return None
        name = path.name
        if not name or name.casefold() == name.upper().casefold() and not name.isalpha():
            # Nothing to flip the case of, so the probe cannot answer.
            return None
        flipped = path.with_name(name.swapcase())
        if flipped == path:
            return None
        return flipped.exists() and os.path.samefile(path, flipped)
    except (OSError, ValueError):
        return None


def canonical_path(path: str | os.PathLike[str]) -> Path:
    """The most resolved form available, without failing on a path that is gone.

    ``strict=False`` keeps this usable for recorded paths whose target has been
    deleted, which is the ordinary case when comparing history against the live
    filesystem.
    """
    try:
        return Path(path).expanduser().resolve(strict=False)
    except (OSError, ValueError, RuntimeError):
        return Path(path)


def identity_key(path: str | os.PathLike[str]) -> str:
    """A string two equal paths share on this host, for dict keys and sets.

    Use `same_path` when comparing exactly two paths - it can ask the filesystem
    and is therefore strictly more accurate. This exists for the cases that need a
    hashable key (grouping, dedupe, an index), where a pairwise syscall is not an
    option.
    """
    resolved = canonical_path(path)
    text = unicodedata.normalize("NFC", str(resolved))
    if paths_are_case_insensitive(resolved):
        # normcase also flips separators on Windows, which is wanted: a recorded
        # forward-slash path and a live backslash one are the same path there.
        return os.path.normcase(text)
    return text


def same_path(left: str | os.PathLike[str] | None, right: str | os.PathLike[str] | None) -> bool:
    """Whether two paths name the same filesystem object."""
    if not left or not right:
        return False
    try:
        if os.path.exists(left) and os.path.exists(right):
            # Exact: device + inode. Sees through symlinks, junctions, mapped
            # drives, bind mounts, and per-directory case sensitivity at once.
            return os.path.samefile(left, right)
    except (OSError, ValueError):
        pass
    try:
        return identity_key(left) == identity_key(right)
    except (OSError, ValueError):
        return False


def is_within(
    child: str | os.PathLike[str] | None, parent: str | os.PathLike[str] | None
) -> bool:
    """Whether ``child`` is ``parent`` or sits underneath it.

    Compared component-wise rather than by string prefix. A prefix test reports
    ``/home/user/project-old`` as inside ``/home/user/project``, which for a
    worktree containment check or a Project boundary is a containment failure that
    silently admits a sibling directory.
    """
    if not child or not parent:
        return False
    child_path = canonical_path(child)
    parent_path = canonical_path(parent)
    if same_path(child_path, parent_path):
        return True
    fold = paths_are_case_insensitive(parent_path)
    child_parts = _comparable_parts(child_path, fold)
    parent_parts = _comparable_parts(parent_path, fold)
    if len(parent_parts) > len(child_parts):
        return False
    return child_parts[: len(parent_parts)] == parent_parts


def _comparable_parts(path: PurePath, fold: bool) -> tuple[str, ...]:
    parts = tuple(unicodedata.normalize("NFC", part) for part in path.parts)
    return tuple(os.path.normcase(part) for part in parts) if fold else parts
