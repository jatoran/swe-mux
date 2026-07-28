from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from swe_mux import git_monitor
from swe_mux.git_monitor import read_git_reading, read_git_state, read_unique_git_states
from swe_mux.server import _parse_worktrees

_FULL_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _fake_git_responses(porcelain: str) -> dict[tuple[str, ...], tuple[int, str]]:
    return {
        ("rev-parse", "--show-toplevel"): (0, "C:/repo"),
        ("branch", "--show-current"): (0, ""),
        ("status", "--porcelain"): (0, porcelain),
        ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"): (1, ""),
        ("rev-parse", "HEAD"): (0, _FULL_SHA),
        ("rev-parse", "--short", "HEAD"): (0, "a1b2c3d"),
    }


@pytest.mark.asyncio
async def test_detached_head_uses_short_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _fake_git_responses(" M file.txt")[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    state = await read_git_state("C:/repo")
    assert state.branch == "a1b2c3d"
    assert state.dirty == 1


@pytest.mark.asyncio
async def test_git_reading_carries_head_and_dirty_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier 0 provenance needs commit identity, not just a dirty file count."""
    porcelain = {"value": " M file.txt\n?? new.txt"}

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        return _fake_git_responses(porcelain["value"])[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    reading = await read_git_reading("C:/repo")
    assert reading.evidence.head == _FULL_SHA
    first = reading.evidence.dirty_hash
    assert first

    # Order-independent: the same change set hashes identically...
    porcelain["value"] = "?? new.txt\n M file.txt"
    assert (await read_git_reading("C:/repo")).evidence.dirty_hash == first
    # ...and a different change set does not.
    porcelain["value"] = " M other.txt"
    assert (await read_git_reading("C:/repo")).evidence.dirty_hash != first
    # A clean tree has no dirty hash at all.
    porcelain["value"] = ""
    assert (await read_git_reading("C:/repo")).evidence.dirty_hash is None


@pytest.mark.asyncio
async def test_unborn_branch_reports_no_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repo with no commits must report head=None rather than an error string."""

    async def fake_git(cwd: str, *args: str, timeout_seconds: float = 4.0) -> tuple[int, str]:
        del cwd, timeout_seconds
        responses = _fake_git_responses("")
        responses[("rev-parse", "HEAD")] = (128, "fatal: ambiguous argument 'HEAD'")
        return responses[args]

    monkeypatch.setattr(git_monitor, "_git", fake_git)
    reading = await read_git_reading("C:/repo")
    assert reading.evidence.head is None


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
        # A pid that cannot exist keeps reap_process_tree's psutil descendant
        # scan a no-op instead of inspecting an unrelated live process.
        pid = 2**22 + 12345
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):  # type: ignore[no-untyped-def]
            await asyncio.sleep(1)
            self.returncode = -9
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            self.reaped = True
            self.returncode = -9
            return -9

    process = SlowProcess()

    async def spawn(*args: object, **kwargs: object) -> SlowProcess:
        del args, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    code, message = await git_monitor._git(".", "status", timeout_seconds=0.001)
    assert code == 124
    assert "timed out" in message
    assert process.killed
    assert process.reaped


def test_worktree_porcelain_parser_preserves_registration_metadata() -> None:
    items = _parse_worktrees(
        "worktree C:/repo\nHEAD abc123\nbranch refs/heads/main\n\n"
        "worktree C:/repo-feature\nHEAD def456\ndetached\n\n"
    )
    assert items == [
        {"worktree": "C:/repo", "HEAD": "abc123", "branch": "refs/heads/main"},
        {"worktree": "C:/repo-feature", "HEAD": "def456", "detached": True},
    ]
