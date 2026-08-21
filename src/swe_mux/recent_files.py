"""Recently touched files for one Project, read from Git rather than the filesystem.

The Files explorer's Recent view answers "what have I been working on here" without a
filesystem sweep. A sweep is the obvious implementation and the wrong one: `node_modules`
and `.venv` hold hundreds of thousands of files whose mtimes move on every install, so an
mtime walk is both expensive and dominated by paths nobody edited. Git already knows the
answer and is bounded by construction.

Two sources, in this order:

1. **The working tree** (`git status --porcelain -z`) - files changed but not committed.
   They carry no timestamp and need none: an uncommitted edit is by definition more recent
   than any commit, so the whole set leads the list in the order Git prints it.
2. **Recent commits** (`git log --name-only`, bounded to `COMMIT_SCAN` commits) - each path
   dated by the newest commit that touched it, newest first.

A path appearing in both is reported once, from the working tree, because that is where its
newest state is. The Project's ignore patterns are applied on top of Git's own, so the
Recent view never lists a file the tree beside it hides. Paths that no longer exist on disk
are dropped: a deleted file is a fact about history, not something the explorer can open.

Everything here is bounded - one status call, one log call capped at `COMMIT_SCAN` commits,
and at most `CANDIDATE_LIMIT` existence checks - so the cost does not grow with repository
size or age.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: How many commits back the log scan reads. Deep enough that a quiet day still fills the
#: list, shallow enough that the call stays a few milliseconds on a large repository.
COMMIT_SCAN = 60
#: How many candidate paths are considered before the existence filter. Bounds the stat
#: calls when a scan turns up far more paths than the view can show.
CANDIDATE_LIMIT = 400
#: Default size of the returned list.
DEFAULT_LIMIT = 20

#: Marks a commit header inside the `git log` stream. A control character no path can hold.
_COMMIT_MARK = "\x02"


@dataclass(slots=True, frozen=True)
class RecentCandidate:
    """One repository-relative path Git reports as recently touched."""

    path: str
    origin: str
    """`working` (uncommitted) or `committed`."""
    status: str | None
    """The two-character porcelain code, for working-tree entries only."""
    committed_at: float | None
    """Committer timestamp of the newest commit that touched it, for committed entries."""


def parse_status_paths(payload: str) -> list[RecentCandidate]:
    """Parse `git status --porcelain=v1 -z` into working-tree candidates, in Git's order.

    `-z` is what makes this total: it disables path quoting entirely, so a path holding a
    quote, a backslash, or a newline arrives verbatim instead of as a C-escaped string this
    would have to decode. A rename or copy entry is followed by its *source* path in the
    next record; the source is consumed and dropped, because the destination is the file
    that exists now.
    """
    records = payload.split("\0")
    candidates: list[RecentCandidate] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        # Trailing empty record from the final separator, or a stray blank.
        if len(record) < 4:
            continue
        code = record[:2]
        path = record[3:]
        if not path:
            continue
        # A rename/copy spends the following record on its source path.
        if code[0] in ("R", "C") or code[1] in ("R", "C"):
            index += 1
        candidates.append(
            RecentCandidate(path=path, origin="working", status=code, committed_at=None)
        )
    return candidates


def parse_log_paths(payload: str) -> list[RecentCandidate]:
    """Parse the bounded `git log` stream into committed candidates, newest first.

    The stream is `--name-only -z` with a `%x02%ct` header, which yields NUL-separated
    records where a commit's header is fused to its first path (`\\x02<unix>\\n<path>`) and
    its remaining paths follow as their own records. A path touched by several commits in
    the window keeps the newest one, which is the first time it is seen.
    """
    seen: dict[str, RecentCandidate] = {}
    committed_at: float | None = None
    for record in payload.split("\0"):
        if not record:
            continue
        if record.startswith(_COMMIT_MARK):
            header, _, first = record[1:].partition("\n")
            try:
                committed_at = float(header.strip())
            except ValueError:
                committed_at = None
            record = first
            if not record:
                continue
        if record in seen:
            continue
        seen[record] = RecentCandidate(
            path=record, origin="committed", status=None, committed_at=committed_at
        )
    return list(seen.values())


def _strip_prefix(path: str, prefix: str) -> str | None:
    """Re-root one repository-relative path onto the Project root.

    Git answers in repository coordinates while the explorer speaks Project ones, and a
    Project can be a subdirectory of its repository. A path outside the Project returns
    `None` rather than a `../` escape.
    """
    if not prefix:
        return path
    if not path.startswith(prefix):
        return None
    return path[len(prefix) :] or None


def merge_candidates(
    working: Sequence[RecentCandidate],
    committed: Sequence[RecentCandidate],
    *,
    prefix: str = "",
    visible: Callable[[str], bool] = lambda _path: True,
    exists: Callable[[str], bool] = lambda _path: True,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Fold both sources into the rendered list: working tree first, then newest commits.

    `visible` applies the Project's ignore rules and `exists` drops paths that are gone.
    Both are injected so the ordering and de-duplication are testable without a repository
    or a filesystem.
    """
    items: list[dict[str, Any]] = []
    taken: set[str] = set()
    scanned = 0
    for candidate in [*working, *committed]:
        if len(items) >= limit:
            break
        scanned += 1
        if scanned > CANDIDATE_LIMIT:
            break
        relative = _strip_prefix(candidate.path, prefix)
        if relative is None or relative in taken:
            continue
        taken.add(relative)
        if not visible(relative) or not exists(relative):
            continue
        items.append(
            {
                "name": relative.rsplit("/", 1)[-1],
                "path": relative,
                "kind": "file",
                "origin": candidate.origin,
                "status": candidate.status,
                "committed_at": candidate.committed_at,
            }
        )
    return items


def unavailable(reason: str) -> dict[str, Any]:
    """The shape returned when Git cannot answer, so the view can say why."""
    return {"items": [], "available": False, "reason": reason}


def _normalize_prefix(value: str) -> str:
    """`git rev-parse --show-prefix` output as a comparable path prefix."""
    prefix = value.strip().replace("\\", "/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return prefix


async def read_recent_files(
    root: str | Path,
    *,
    ignore_patterns: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Read this Project's recently touched files from Git.

    Returns `{"items": [...], "available": bool}` and, when unavailable, a `reason` the
    view renders instead of an empty list - "not a Git repository" and "nothing has changed
    recently" are different answers and must not render the same.
    """
    # Imported here rather than at module scope: `git_monitor` pulls in the session manager
    # and the event bus, and this module is otherwise pure parsing that tests import freely.
    from .git_monitor import read_git
    from .project_files import ignored_project_path

    base = Path(root)
    code, prefix_output = await read_git(str(base), "rev-parse", "--show-prefix")
    if code != 0:
        # Three different failures, and they are not interchangeable to a reader deciding
        # whether to act: a timeout will pass, a missing repository never will, and a Git
        # that would not run at all is a machine problem rather than a Project one.
        if code == 124:
            return unavailable("Git did not answer in time; try again.")
        if "not a git repository" in prefix_output.lower():
            return unavailable("This Project is not inside a Git repository.")
        return unavailable("Git could not be read here.")
    prefix = _normalize_prefix(prefix_output)

    status_code, status_output = await read_git(
        str(base), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."
    )
    working = parse_status_paths(status_output) if status_code == 0 else []

    log_code, log_output = await read_git(
        str(base),
        "log",
        f"-n{COMMIT_SCAN}",
        "--no-merges",
        "--name-only",
        "-z",
        f"--pretty=format:{_COMMIT_MARK}%ct",
        "--",
        ".",
    )
    committed = parse_log_paths(log_output) if log_code == 0 else []

    # A repository with no commits yet answers `rev-parse` but fails `log`, which is not a
    # failure - the working tree alone is the whole honest answer there. Only both calls
    # failing means Git could not be read.
    if status_code != 0 and log_code != 0:
        return unavailable(
            "Git did not answer in time; try again."
            if 124 in (status_code, log_code)
            else "Git could not be read here."
        )

    patterns = list(ignore_patterns)
    items = merge_candidates(
        working,
        committed,
        prefix=prefix,
        visible=lambda path: not ignored_project_path(path, patterns),
        exists=lambda path: os.path.isfile(base / path),
        limit=limit,
    )
    return {"items": items, "available": True}
