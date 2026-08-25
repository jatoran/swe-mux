"""W4.5.3 - every bounded subprocess run says which operation asked for it.

`run_bounded` has carried an `operation_id` parameter since S8 and no caller
passed one, so a `bounded_command_timed_out` line read `operation_id=None` while
the caller's own line beside it carried a real id (D3 soak). Two sources of an
id, and both are real: the in-flight HTTP request where there is one, and an id
the background loop mints for its own iteration where there is not.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux import git_monitor, provider_accounts, usage
from swe_mux.bounded_subprocess import ProcessOutcome
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.logsetup import bound_request_id, current_request_id
from swe_mux.usage import UsageManager


def _outcome(stdout: bytes = b"{}") -> ProcessOutcome:
    return ProcessOutcome(
        exit_code=0,
        stdout=stdout,
        stderr=b"",
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=1.0,
    )


# --------------------------------------------------------------------------- #
# ccusage: the refresh's own id.                                               #
# --------------------------------------------------------------------------- #


async def test_a_ccusage_run_carries_the_refresh_it_belongs_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []

    async def fake_run_bounded(_argv: Any, **kwargs: Any) -> ProcessOutcome:
        seen.append(kwargs.get("operation_id"))
        return _outcome(b'{"daily": []}')

    monkeypatch.setattr(usage, "run_bounded", fake_run_bounded)
    monkeypatch.setattr(usage, "prepare_usage_command", lambda command: list(command))
    events = EventBus()
    queue = events.subscribe(name="test")
    manager = UsageManager(
        Config(data_dir=tmp_path, ccusage_enabled=True, usage_command=["fixture-ccusage"]),
        events,
    )

    await manager.refresh()

    refreshed = [
        event
        for event in _drain(queue)
        if event.type in {"usage_refreshed", "usage_refresh_failed"}
    ]
    assert len(seen) == 1 and seen[0]
    # The same id the event and the adapter's own log line carry, not a second one.
    assert refreshed and refreshed[0].payload.get("operation_id") == seen[0]


def _drain(queue: asyncio.Queue[Any]) -> list[Any]:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def test_a_ccusage_timeout_names_the_bound_and_what_it_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W4.5.5: "timed out" alone could not tell a hang from a slow corpus read."""

    async def fake_run_bounded(_argv: Any, **_kwargs: Any) -> ProcessOutcome:
        return ProcessOutcome(
            exit_code=None,
            stdout=b"",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=usage.USAGE_TIMEOUT_SECONDS * 1000,
            timed_out=True,
        )

    monkeypatch.setattr(usage, "run_bounded", fake_run_bounded)
    monkeypatch.setattr(usage, "prepare_usage_command", lambda command: list(command))
    manager = UsageManager(
        Config(data_dir=tmp_path, ccusage_enabled=True, usage_command=["fixture-ccusage"]),
        EventBus(),
    )

    await manager.refresh()

    assert manager.state.status == "error"
    error = manager.state.error or ""
    assert f"{usage.USAGE_TIMEOUT_SECONDS:g}s" in error
    assert "transcript corpus" in error


def test_the_ccusage_bound_covers_the_measured_cold_read() -> None:
    """The bound is a measurement, not a round number (see the constant's note)."""
    assert usage.USAGE_TIMEOUT_SECONDS >= 120.0


# --------------------------------------------------------------------------- #
# Git: the request, or the poll's own minted id.                               #
# --------------------------------------------------------------------------- #


async def test_a_git_query_carries_the_request_that_caused_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    async def fake_run_bounded(_argv: Any, **kwargs: Any) -> ProcessOutcome:
        seen.append(kwargs.get("operation_id"))
        return _outcome(b"main")

    monkeypatch.setattr(git_monitor, "run_bounded", fake_run_bounded)

    with bound_request_id("req-abc"):
        await git_monitor.read_git(".", "rev-parse", "HEAD")
    await git_monitor.read_git(".", "rev-parse", "HEAD")

    # Empty outside an operation is the honest answer, not a missing field.
    assert seen == ["req-abc", None]


async def test_the_git_poll_mints_an_id_for_its_own_iteration() -> None:
    """`git-monitor` has no request behind it, so it makes one."""
    monitor = cast(Any, git_monitor.GitMonitor.__new__(git_monitor.GitMonitor))
    monitor.cadence = 0.01
    seen: list[str] = []
    ran = asyncio.Event()

    async def fake_poll() -> None:
        seen.append(current_request_id())
        ran.set()

    monitor._poll = fake_poll
    task = asyncio.create_task(git_monitor.GitMonitor._run(monitor))
    await asyncio.wait_for(ran.wait(), timeout=2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert seen and seen[0]
    assert current_request_id() == ""  # and it does not leak out of the iteration


# --------------------------------------------------------------------------- #
# Provider CLIs.                                                               #
# --------------------------------------------------------------------------- #


async def test_a_provider_cli_run_carries_the_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str | None] = []

    async def fake_run_bounded(_argv: Any, **kwargs: Any) -> ProcessOutcome:
        seen.append(kwargs.get("operation_id"))
        return _outcome(b"ok")

    monkeypatch.setattr(provider_accounts, "run_bounded", fake_run_bounded)
    manager = cast(
        Any,
        provider_accounts.ProviderAccountManager.__new__(provider_accounts.ProviderAccountManager),
    )
    manager._spawn_command = lambda _provider, args: ["claude", *args]

    with bound_request_id("req-login"):
        assert (
            await provider_accounts.ProviderAccountManager._run_command(
                manager, "claude", ["auth", "status"], timeout_seconds=5
            )
            == "ok"
        )

    assert seen == ["req-login"]


async def test_the_quota_poll_mints_an_id_for_its_own_iteration() -> None:
    manager = cast(
        Any,
        provider_accounts.ProviderAccountManager.__new__(provider_accounts.ProviderAccountManager),
    )
    manager.poll_seconds = 0.01
    seen: list[str] = []
    ran = asyncio.Event()

    async def fake_refresh() -> Any:
        seen.append(current_request_id())
        ran.set()
        return SimpleNamespace()

    manager.refresh = fake_refresh
    task = asyncio.create_task(provider_accounts.ProviderAccountManager._loop(manager))
    await asyncio.wait_for(ran.wait(), timeout=5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert seen and seen[0]
