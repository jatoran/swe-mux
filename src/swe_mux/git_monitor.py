from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypeVar

from .background_tasks import background
from .event_bus import EventBus
from .models import GitState
from .session import Session, SessionManager
from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

GIT_MONITOR_LOOP = "git-monitor"
GIT_TIMEOUT_SECONDS = 4.0
GIT_CONCURRENCY = 4
#: Repository roots retained in the diffstat memo. One entry per checkout the
#: fleet has open, so this is bounded by how many worktrees exist, not by time.
DIFFSTAT_CACHE_LIMIT = 256

T = TypeVar("T")


async def _git(
    cwd: str, *args: str, timeout_seconds: float = GIT_TIMEOUT_SECONDS
) -> tuple[int, str]:
    """Run one bounded, **read-only** Git query and always reap the subprocess.

    Code 124 is reserved for a timeout so API callers can return a typed diagnostic
    instead of hanging a terminal-facing request indefinitely.

    `--no-optional-locks` is what makes this read-only, and it is not a tuning knob.
    `git status` refreshes the index and *writes it back* whenever any tracked file's
    mtime has moved, taking `.git/index.lock` to do so. In a repository where agents
    are editing files that is every single poll, so a monitor that merely wanted to
    read the branch name was writing to the user's repository every 5 seconds and
    contending for the lock with the agents it was watching. Verified 2026-08-05 by
    touching a tracked file and comparing `.git/index` mtime across both forms: plain
    `status` rewrote it, `--no-optional-locks status` did not, with byte-identical
    output.

    The failure mode that makes this more than waste: a write in flight when the
    daemon is killed can strand `index.lock`, which blocks *every* git operation in
    that repository for every agent until someone removes it by hand. One such lock
    was found stranded in this repo, created within seconds of a daemon restart.

    Git documents this flag for exactly this caller: tools that poll a repository for
    display. Latency is unaffected (measured 15.1ms against 14.8ms); the point is that
    a monitor must not mutate what it monitors.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "--no-optional-locks",
            "-C",
            cwd,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=background_creation_flags(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            await reap_process_tree(process)
            return 124, f"git timed out after {timeout_seconds:g}s"
        output = stdout if process.returncode == 0 else stderr or stdout
        return process.returncode or 0, output.decode("utf-8", "replace").strip()
    except OSError:
        return 1, ""


@dataclass(slots=True, frozen=True)
class GitEvidence:
    """Deterministic Tier 0 git facts for one repository root.

    `head` is the exact commit the work happened at; `dirty_hash` fingerprints the
    working-tree change set (paths + status codes, order-independent). Together
    they let a provenance consumer say *which* tree a fact was produced against
    — `GitState` alone only reports a dirty file count, which is not an identity.
    """

    head: str | None = None
    dirty_hash: str | None = None

    def as_payload(self) -> dict[str, str | None]:
        return {"head": self.head, "dirty_hash": self.dirty_hash}


@dataclass(slots=True, frozen=True)
class GitReading:
    state: GitState
    evidence: GitEvidence


def _dirty_hash(porcelain: str) -> str | None:
    """Order-independent fingerprint of the working-tree change set."""
    lines = sorted(line.strip() for line in porcelain.splitlines() if line.strip())
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode("utf-8", "replace")).hexdigest()[:16]


def _worktree_name(cwd: str, root: str, git_dir: str, common_dir: str) -> str | None:
    """Leaf name of `root` when it is a linked worktree, else None.

    A linked worktree's `--git-dir` lives under the primary checkout's
    `.git/worktrees/<name>`, so it differs from `--git-common-dir`; the primary
    checkout reports the same path for both. Comparing the two paths is the only
    check that stays correct for bare repositories and `.git`-file submodules,
    where comparing directory *names* does not.

    Both paths are resolved against `cwd` first, and that is the whole
    correctness of this function. `--absolute-git-dir` promises an absolute
    answer for the git dir **only**: `--git-common-dir` still replies relatively
    whenever it can — `.git` from a repository root, `../.git` from a
    subdirectory — and relative to *the directory git ran in*, not to the
    toplevel. Resolved against this process's cwd instead, those never matched
    the git dir, so every primary checkout compared unequal to itself and was
    reported as a linked worktree named after the repository folder.
    """
    if not git_dir or not common_dir:
        return None
    try:
        base = Path(cwd)
        if (base / git_dir).resolve() == (base / common_dir).resolve():
            return None
    except OSError:
        return None
    return Path(root).name or None


#: root -> (dirty_hash, added, removed). Keyed by the working-tree fingerprint the
#: cheap poll already computes, so `git diff --numstat` runs only when the change
#: set actually moved — not once per session, and not once per five-second poll.
_diffstat_memo: OrderedDict[str, tuple[str | None, int, int]] = OrderedDict()


def reset_diffstat_cache() -> None:
    """Drop every memoized diffstat. For tests and for daemon restart."""
    _diffstat_memo.clear()


def _memoized_diffstat(root: str, dirty_hash: str | None) -> tuple[int, int] | None:
    cached = _diffstat_memo.get(root)
    if cached is None or cached[0] != dirty_hash:
        return None
    _diffstat_memo.move_to_end(root)
    return cached[1], cached[2]


def _memoize_diffstat(root: str, dirty_hash: str | None, added: int, removed: int) -> None:
    _diffstat_memo[root] = (dirty_hash, added, removed)
    _diffstat_memo.move_to_end(root)
    while len(_diffstat_memo) > DIFFSTAT_CACHE_LIMIT:
        _diffstat_memo.popitem(last=False)


def parse_numstat(output: str) -> tuple[int, int]:
    """Sum added/removed lines from `git diff --numstat`.

    Binary files report `-` for both counts and contribute nothing rather than
    aborting the sum — a repository with one PNG in it still has a line count.
    """
    added = removed = 0
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            removed += int(parts[1])
    return added, removed


async def _read_diffstat(
    cwd: str, root: str, dirty_hash: str | None, has_head: bool
) -> tuple[int | None, int | None]:
    """Lines added/removed vs HEAD, memoized on the working-tree fingerprint.

    A clean tree is answered without touching git at all: no change set means no
    changed lines, and that is the common case across an idle fleet.
    """
    if not has_head:
        return None, None
    if dirty_hash is None:
        _memoize_diffstat(root, None, 0, 0)
        return 0, 0
    cached = _memoized_diffstat(root, dirty_hash)
    if cached is not None:
        return cached
    code, output = await _git(cwd, "diff", "--numstat", "HEAD")
    if code:
        log.debug(
            "git diffstat unavailable",
            extra={"root": root, "code": code, "diagnostic": output[:200]},
        )
        return None, None
    added, removed = parse_numstat(output)
    _memoize_diffstat(root, dirty_hash, added, removed)
    return added, removed


async def read_git_reading(cwd: str) -> GitReading:
    code, root = await _git(cwd, "rev-parse", "--show-toplevel")
    if code or not root:
        return GitReading(GitState(), GitEvidence())
    (
        (_, branch),
        (_, porcelain),
        (upstream_code, counts),
        (head_code, head),
        (dir_code, dirs),
    ) = await asyncio.gather(
        _git(cwd, "branch", "--show-current"),
        _git(cwd, "status", "--porcelain"),
        _git(cwd, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
        _git(cwd, "rev-parse", "HEAD"),
        _git(cwd, "rev-parse", "--absolute-git-dir", "--git-common-dir"),
    )
    if not branch:
        _, branch = await _git(cwd, "rev-parse", "--short", "HEAD")
    ahead = behind = 0
    if not upstream_code and counts:
        parts = counts.replace("\t", " ").split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
    # An unborn branch (no commits yet) reports a non-zero code and no oid.
    commit = head.strip() if not head_code and head.strip() else None
    dir_lines = dirs.splitlines() if not dir_code else []
    worktree = _worktree_name(cwd, root, *dir_lines[:2]) if len(dir_lines) >= 2 else None
    dirty_hash = _dirty_hash(porcelain)
    added, removed = await _read_diffstat(cwd, root, dirty_hash, commit is not None)
    return GitReading(
        GitState(
            branch,
            len(porcelain.splitlines()),
            ahead,
            behind,
            worktree=worktree,
            added=added,
            removed=removed,
        ),
        GitEvidence(head=commit, dirty_hash=dirty_hash),
    )


async def read_git_state(cwd: str) -> GitState:
    return (await read_git_reading(cwd)).state


async def _read_unique[T](
    cwds: Iterable[str], read_one: Callable[[str], Awaitable[T]]
) -> dict[str, T]:
    """Poll unique roots concurrently while keeping subprocess pressure bounded."""
    semaphore = asyncio.Semaphore(GIT_CONCURRENCY)

    async def read(cwd: str) -> tuple[str, T]:
        async with semaphore:
            return cwd, await read_one(cwd)

    return dict(await asyncio.gather(*(read(cwd) for cwd in dict.fromkeys(cwds))))


async def read_unique_git_states(cwds: Iterable[str]) -> dict[str, GitState]:
    return await _read_unique(cwds, lambda cwd: read_git_state(cwd))


async def read_unique_git_readings(cwds: Iterable[str]) -> dict[str, GitReading]:
    return await _read_unique(cwds, read_git_reading)


class GitMonitor:
    """Keeps every session's `GitState` converging on what git actually says.

    Attached sessions are polled at `cadence`; every session is swept far more
    slowly. The sweep is not a nicety: `GitState` is a *cache of a derived
    observation* living on a record that outlives the daemon that wrote it, so a
    session whose pane is closed used to freeze whatever the last poll computed,
    for as long as the session lived. A value derived by code that has since been
    fixed then survives the fix — which is exactly how a wrong `worktree` name
    kept rendering after the bug that produced it was gone.

    The sweep is affordable because `read_unique_git_readings` deduplicates by
    cwd: a fleet of thirty sessions in one checkout is one git read, not thirty.
    Cost scales with distinct working directories, which is why polling more
    sessions is close to free while polling more often would not be.
    """

    #: Sweeps including detached sessions, in cadence ticks. 12 * 5 s = one minute.
    DETACHED_SWEEP_EVERY = 12

    def __init__(self, sessions: SessionManager, events: EventBus, cadence: float = 5.0) -> None:
        self.sessions = sessions
        self.events = events
        self.cadence = cadence
        self._task: asyncio.Task[None] | None = None
        self._sweep = 0

    def start(self) -> None:
        self._task = background.start(GIT_MONITOR_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(GIT_MONITOR_LOOP)
        self._task = None

    async def _run(self) -> None:
        while True:
            with background.iteration(GIT_MONITOR_LOOP):
                await self._poll()
            await asyncio.sleep(self.cadence)

    def _due(self) -> list[Session]:
        """Attached sessions every tick; the whole fleet on a sweep tick.

        The first tick after a daemon start is always a sweep, so state adopted
        from a previous daemon is re-derived by *this* daemon's code rather than
        trusted indefinitely.
        """
        self._sweep += 1
        everything = self._sweep % self.DETACHED_SWEEP_EVERY == 1
        return [
            session
            for session in self.sessions.sessions.values()
            if session.subscribers or everything
        ]

    async def _poll(self) -> None:
        by_cwd: dict[str, list[Session]] = {}
        for session in self._due():
            by_cwd.setdefault(session.record.git_cwd, []).append(session)
        readings = await read_unique_git_readings(by_cwd)
        for cwd, sessions in by_cwd.items():
            reading = readings[cwd]
            state = reading.state
            for session in sessions:
                if session.record.git != state:
                    session.record.git = state
                    session.publish_update()
                    await self.events.emit(
                        "git_changed",
                        session_id=session.record.id,
                        source="daemon",
                        git=asdict(state),
                        # Tier 0 provenance reads these: which commit, which
                        # working-tree change set. The UI ignores them.
                        content_hash=reading.evidence.head,
                        target=cwd,
                        **reading.evidence.as_payload(),
                    )
