from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .event_bus import EventBus
from .models import GitState
from .session import Session, SessionManager
from .subprocess_flags import background_creation_flags

GIT_TIMEOUT_SECONDS = 4.0
GIT_CONCURRENCY = 4


async def _git(
    cwd: str, *args: str, timeout_seconds: float = GIT_TIMEOUT_SECONDS
) -> tuple[int, str]:
    """Run one bounded Git query and always reap the subprocess.

    Code 124 is reserved for a timeout so API callers can return a typed diagnostic
    instead of hanging a terminal-facing request indefinitely.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
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
            process.kill()
            await process.communicate()
            return 124, f"git timed out after {timeout_seconds:g}s"
        output = stdout if process.returncode == 0 else stderr or stdout
        return process.returncode or 0, output.decode("utf-8", "replace").strip()
    except OSError:
        return 1, ""


async def read_git_state(cwd: str) -> GitState:
    code, root = await _git(cwd, "rev-parse", "--show-toplevel")
    if code or not root:
        return GitState()
    (_, branch), (_, porcelain), (upstream_code, counts) = await asyncio.gather(
        _git(cwd, "branch", "--show-current"),
        _git(cwd, "status", "--porcelain"),
        _git(cwd, "rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
    )
    if not branch:
        _, branch = await _git(cwd, "rev-parse", "--short", "HEAD")
    ahead = behind = 0
    if not upstream_code and counts:
        parts = counts.replace("\t", " ").split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
    return GitState(branch, len(porcelain.splitlines()), ahead, behind)


async def read_unique_git_states(cwds: Iterable[str]) -> dict[str, GitState]:
    """Poll unique roots concurrently while keeping subprocess pressure bounded."""
    semaphore = asyncio.Semaphore(GIT_CONCURRENCY)

    async def read(cwd: str) -> tuple[str, GitState]:
        async with semaphore:
            return cwd, await read_git_state(cwd)

    return dict(await asyncio.gather(*(read(cwd) for cwd in dict.fromkeys(cwds))))


class GitMonitor:
    def __init__(self, sessions: SessionManager, events: EventBus, cadence: float = 5.0) -> None:
        self.sessions = sessions
        self.events = events
        self.cadence = cadence
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="git-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            attached = [
                session for session in self.sessions.sessions.values() if session.subscribers
            ]
            by_cwd: dict[str, list[Session]] = {}
            for session in attached:
                by_cwd.setdefault(session.record.git_cwd, []).append(session)
            states = await read_unique_git_states(by_cwd)
            for cwd, sessions in by_cwd.items():
                state = states[cwd]
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
                        )
            await asyncio.sleep(self.cadence)
