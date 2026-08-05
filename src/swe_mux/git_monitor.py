from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

from .background_tasks import background
from .event_bus import EventBus
from .models import GitState
from .session import Session, SessionManager
from .subprocess_flags import background_creation_flags, reap_process_tree

GIT_MONITOR_LOOP = "git-monitor"
GIT_TIMEOUT_SECONDS = 4.0
GIT_CONCURRENCY = 4

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


async def read_git_reading(cwd: str) -> GitReading:
    code, root = await _git(cwd, "rev-parse", "--show-toplevel")
    if code or not root:
        return GitReading(GitState(), GitEvidence())
    (_, branch), (_, porcelain), (upstream_code, counts), (head_code, head) = await asyncio.gather(
        _git(cwd, "branch", "--show-current"),
        _git(cwd, "status", "--porcelain"),
        _git(cwd, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
        _git(cwd, "rev-parse", "HEAD"),
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
    return GitReading(
        GitState(branch, len(porcelain.splitlines()), ahead, behind),
        GitEvidence(head=commit, dirty_hash=_dirty_hash(porcelain)),
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
    def __init__(self, sessions: SessionManager, events: EventBus, cadence: float = 5.0) -> None:
        self.sessions = sessions
        self.events = events
        self.cadence = cadence
        self._task: asyncio.Task[None] | None = None

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

    async def _poll(self) -> None:
        attached = [
            session for session in self.sessions.sessions.values() if session.subscribers
        ]
        by_cwd: dict[str, list[Session]] = {}
        for session in attached:
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
                        git=state.__dict__
                        if hasattr(state, "__dict__")
                        else {
                            "branch": state.branch,
                            "dirty": state.dirty,
                            "ahead": state.ahead,
                            "behind": state.behind,
                        },
                        # Tier 0 provenance reads these: which commit, which
                        # working-tree change set. The UI ignores them.
                        content_hash=reading.evidence.head,
                        target=cwd,
                        **reading.evidence.as_payload(),
                    )
