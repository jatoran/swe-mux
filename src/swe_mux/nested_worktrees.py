"""Which directories inside a Project root are separate Git checkouts.

A worktree placed under the Project it branches from is a *sibling checkout*, not Project
content: it holds a second copy of every tracked file, and the explorer that lists it is
answering a question nobody asked. Convention puts them under `.claude/worktrees/`,
`.codex/worktrees/`, `.agents/worktrees/`, or a bare `.worktrees/`, and those four are
carried as ignore patterns (`config.WORKTREE_IGNORE_PATTERNS`) so the common case costs
nothing and keeps working after the checkout is abandoned and Git stops listing it.

This module covers the other half: a worktree Git *does* know about, wherever a person put
it. `git worktree add ./scratch` is legal, and no static pattern will ever name it.

Three properties this deliberately has:

- **It fails open.** Not a repository, Git missing, Git slow, Git angry - every one of them
  answers "no nested worktrees" rather than raising. Losing the dynamic half degrades the
  explorer to the static patterns, which is the behaviour that shipped before; raising would
  take a working file browser offline over a Git that was not needed to browse files.
- **It is cached with a short TTL.** The walk that consumes this runs per keystroke behind a
  debounced search box, and a subprocess per keystroke is not free. Worktrees are created by
  hand at human intervals, so `CACHE_SECONDS` of staleness costs at worst a freshly created
  worktree staying visible for half a minute.
- **It answers in Project coordinates.** Callers filter Project-relative posix paths; a
  caller handed absolute paths would have to re-derive the relationship this already knows.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from .subprocess_flags import background_creation_flags

log = logging.getLogger(__name__)

#: How long one repository's answer is reused. See the module docstring for why staleness
#: is the right trade here.
CACHE_SECONDS = 30.0
#: Bound on the Git call. A repository whose `worktree list` takes longer than this is one
#: where the explorer must not wait, so the answer degrades to the static patterns.
GIT_TIMEOUT_SECONDS = 4.0

_cache: dict[str, tuple[float, frozenset[str]]] = {}
_cache_lock = threading.Lock()


def _relative_inside(root: Path, candidate: str) -> str | None:
    """One worktree path as a Project-relative posix path, or `None` if not inside.

    The Project root itself is `None`: it is always in `git worktree list` and pruning it
    would hide the whole tree. Comparison is case-normalized because this runs on Windows,
    where `D:\\Projects` and `d:\\projects` are the same directory.
    """
    try:
        resolved = Path(candidate).resolve()
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    posix = relative.as_posix()
    if posix in ("", "."):
        return None
    return posix


def parse_worktree_roots(output: str, root: Path) -> frozenset[str]:
    """Project-relative posix paths of the worktrees `git worktree list --porcelain` named.

    Split out from the subprocess call so the parsing is testable without a repository.
    The porcelain format is stanzas of `key value` lines; only the `worktree` key carries a
    path, and it is always the first line of its stanza.
    """
    found: set[str] = set()
    for line in output.splitlines():
        if not line.startswith("worktree "):
            continue
        relative = _relative_inside(root, line[len("worktree ") :].strip())
        if relative is not None:
            found.add(relative)
    return frozenset(found)


def _read_worktree_roots(root: Path) -> frozenset[str]:
    """Ask Git, bounded, read-only, and never raising.

    `--no-optional-locks` for the same reason every other read in this codebase carries it
    (`git_monitor._git`): a browse must not write to the repository it is browsing.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                str(root),
                "worktree",
                "list",
                "--porcelain",
            ],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            creationflags=background_creation_flags(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # Includes the timeout. Debug rather than warning: a Project outside a repository
        # is an ordinary thing to browse, not an incident.
        log.debug("nested worktree listing failed for %s: %s", root, exc)
        return frozenset()
    if completed.returncode != 0:
        return frozenset()
    return parse_worktree_roots(completed.stdout.decode("utf-8", "replace"), root)


def nested_worktree_paths(root: str | Path, *, now: float | None = None) -> frozenset[str]:
    """Project-relative posix paths of every Git worktree registered *inside* `root`.

    Empty when Git cannot answer; see the module docstring for why that is the right
    failure. `now` is injectable so cache expiry is testable without sleeping.
    """
    try:
        resolved = Path(root).resolve()
    except OSError:
        return frozenset()
    key = os.path.normcase(str(resolved))
    moment = time.monotonic() if now is None else now
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] > moment:
            return cached[1]
    # Deliberately outside the lock: this spawns a subprocess, and holding a global lock
    # across it would serialize every Project's file browsing behind the slowest repository.
    # Two threads racing here duplicate one cheap Git call and agree on the answer.
    roots = _read_worktree_roots(resolved)
    with _cache_lock:
        _cache[key] = (moment + CACHE_SECONDS, roots)
        # The cache is keyed by Project root, so it is bounded by how many Projects are
        # registered - but a long-lived daemon browsing many roots should not grow it
        # without limit. Drop the whole thing rather than track an LRU: it is a latency
        # cache, refilling costs one Git call, and 256 roots is far past any real fleet.
        if len(_cache) > 256:
            _cache.clear()
            _cache[key] = (moment + CACHE_SECONDS, roots)
    return roots


def reset_cache() -> None:
    """Forget every cached answer. For tests, and for a Project's roots changing under us."""
    with _cache_lock:
        _cache.clear()
