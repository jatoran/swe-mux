from __future__ import annotations

import asyncio
from pathlib import Path

from .event_bus import EventBus
from .models import GitState
from .session import Session, SessionManager


async def _git(cwd: str, *args: str) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            cwd,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await process.communicate()
        return process.returncode or 0, stdout.decode("utf-8", "replace").strip()
    except OSError:
        return 1, ""


async def read_git_state(cwd: str) -> GitState:
    code, root = await _git(cwd, "rev-parse", "--show-toplevel")
    if code or not root:
        return GitState()
    _, branch = await _git(cwd, "branch", "--show-current")
    _, porcelain = await _git(cwd, "status", "--porcelain")
    ahead = behind = 0
    code, counts = await _git(cwd, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if not code and counts:
        parts = counts.replace("\t", " ").split()
        if len(parts) == 2:
            ahead, behind = int(parts[0]), int(parts[1])
    return GitState(branch or Path(root).name, len(porcelain.splitlines()), ahead, behind)


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
                by_cwd.setdefault(session.record.cwd, []).append(session)
            for cwd, sessions in by_cwd.items():
                state = await read_git_state(cwd)
                for session in sessions:
                    if session.record.git != state:
                        session.record.git = state
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
