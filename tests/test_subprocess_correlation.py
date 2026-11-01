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
    monkeypatch.setattr(usage, "resolve_usage_launch", _plain_launch)
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


def _plain_launch(command: Any, **_kwargs: Any) -> usage.UsageLaunch:
    command = list(command)
    return usage.UsageLaunch(command[:1], command[1:], "command", command[0], False)


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
            duration_ms=usage.USAGE_CEILING_SECONDS * 1000,
            timed_out=True,
            resources={"read_bytes": 3 * 2**30, "cpu_seconds": 812.0},
        )

    monkeypatch.setattr(usage, "run_bounded", fake_run_bounded)
    monkeypatch.setattr(usage, "resolve_usage_launch", _plain_launch)
    manager = UsageManager(
        Config(data_dir=tmp_path, ccusage_enabled=True, usage_command=["fixture-ccusage"]),
        EventBus(),
    )

    await manager.refresh()

    assert manager.state.status == "error"
    error = manager.state.error or ""
    assert f"{usage.USAGE_CEILING_SECONDS:g}s" in error
    assert "read 3072 MB" in error
    assert "transcript corpus" in error


async def test_a_ccusage_stall_is_reported_as_hung_not_slow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_bounded(_argv: Any, **_kwargs: Any) -> ProcessOutcome:
        return ProcessOutcome(
            exit_code=None,
            stdout=b"",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=400_000,
            stalled=True,
        )

    monkeypatch.setattr(usage, "run_bounded", fake_run_bounded)
    monkeypatch.setattr(usage, "resolve_usage_launch", _plain_launch)
    manager = UsageManager(
        Config(data_dir=tmp_path, ccusage_enabled=True, usage_command=["fixture-ccusage"]),
        EventBus(),
    )

    await manager.refresh()

    error = manager.state.error or ""
    assert f"no progress for {usage.USAGE_STALL_SECONDS:g}s" in error
    assert "hung rather than slow" in error


def test_the_ccusage_bounds_leave_room_for_a_contained_scan() -> None:
    """The ceiling outlasts the measured cold read many times over (see the constants'
    notes); a hung run is caught by the stall window long before it."""
    assert usage.USAGE_CEILING_SECONDS >= 3600
    assert 60 <= usage.USAGE_STALL_SECONDS < usage.USAGE_CEILING_SECONDS


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
    # A real monitor over an empty fleet rather than a bare `__new__`: the loop
    # body reads state the constructor builds (its wake event), and a half-built
    # instance answers that with `AttributeError` instead of exercising the loop.
    monitor = cast(
        Any,
        git_monitor.GitMonitor(
            cast(Any, SimpleNamespace(sessions={})), EventBus(), cadence=0.01, watch=False
        ),
    )
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str | None] = []

    async def fake_run_bounded(_argv: Any, **kwargs: Any) -> ProcessOutcome:
        seen.append(kwargs.get("operation_id"))
        return _outcome(b"ok")

    monkeypatch.setattr(provider_accounts, "run_bounded", fake_run_bounded)
    # A real manager over a temporary data dir and home rather than a bare
    # `__new__` with one method stubbed on: `_run_command` resolves the configured
    # executable now (so its failures can say *why* nothing was launchable), and a
    # half-built instance answered that with `AttributeError`. Nothing about this
    # test wanted a partial object; it wanted a run it could observe.
    manager = provider_accounts.ProviderAccountManager(
        tmp_path / "data", EventBus(), home=tmp_path / "home"
    )

    with bound_request_id("req-login"):
        assert (
            await manager._run_command("claude", ["auth", "status"], timeout_seconds=5) == "ok"
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
