from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from swe_mux import git_monitor
from swe_mux.git_monitor import read_git_state, read_unique_git_states
from swe_mux.server import _parse_worktrees


@pytest.mark.asyncio
async def test_detached_head_uses_short_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_git(
        cwd: str, *args: str, timeout_seconds: float = 4.0
    ) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = {
            ("rev-parse", "--show-toplevel"): (0, "C:/repo"),
            ("branch", "--show-current"): (0, ""),
            ("status", "--porcelain"): (0, " M file.txt"),
            ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): (1, ""),
            ("rev-parse", "--short", "HEAD"): (0, "a1b2c3d"),
        }
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert state.branch == "a1b2c3d"
    assert state.dirty == 1


@pytest.mark.asyncio
async def test_unique_git_poll_deduplicates_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_read(cwd: str):  # type: ignore[no-untyped-def]
        calls.append(cwd)
        return git_monitor.GitState(branch=Path(cwd).name)

    monkeypatch.setattr(git_monitor, "read_git_state", fake_read)
    result = await read_unique_git_states(["one", "one", "two"])
    assert set(result) == {"one", "two"}
    assert sorted(calls) == ["one", "two"]


@pytest.mark.asyncio
async def test_git_timeout_kills_and_reaps(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowProcess:
        returncode = None
        killed = False
        calls = 0

        async def communicate(self):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(1)
            self.returncode = -9
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    process = SlowProcess()

    async def spawn(*args: object, **kwargs: object) -> SlowProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    code, message = await git_monitor._git(".", "status", timeout_seconds=0.001)
    assert code == 124
    assert "timed out" in message
    assert process.killed
    assert process.calls == 2


def test_worktree_porcelain_parser_preserves_registration_metadata() -> None:
    items = _parse_worktrees(
        "worktree C:/repo\nHEAD abc123\nbranch refs/heads/main\n\n"
        "worktree C:/repo-feature\nHEAD def456\ndetached\n\n"
    )
    assert items == [
        {"worktree": "C:/repo", "HEAD": "abc123", "branch": "refs/heads/main"},
        {"worktree": "C:/repo-feature", "HEAD": "def456", "detached": True},
    ]
