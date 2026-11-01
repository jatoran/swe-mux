"""Windowed usage refreshes: planning, merging, provenance, the schedule, and launch.

The collector re-read the whole transcript corpus every three hours until 2026-10-31,
when it was found freezing the operator's PC. These tests pin what replaced that: a
refresh reads from the last refresh's date on and merges, the whole corpus is read
only when there is nothing to merge into, and nothing is ever summed across a cached
aggregate and fresh days.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import time
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import usage
from swe_mux.bounded_subprocess import ProcessOutcome
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.routes.usage import get_usage, refresh_usage
from swe_mux.server import error_middleware
from swe_mux.usage import (
    PLAN_CONFIGURED,
    PLAN_INCREMENTAL,
    PLAN_REBUILD,
    UsageAdapterError,
    UsageManager,
    command_bounds_dates,
    find_ccusage_native,
    host_pressure,
    merge_usage_window,
    normalize_usage_sources,
    plan_refresh,
    resolve_usage_launch,
)

from .support.settle import until

COMMAND = ["ccusage", "daily", "--json", "--by-agent"]
NOON = dt.datetime(2026, 10, 31, 12, 0).timestamp()


def _row(date: str, tokens: int, cost: float = 0.0) -> dict[str, Any]:
    return {
        "date": date,
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": tokens,
        "cost_usd": cost,
        "cost_is_estimate": True,
        "cost_method": "source_estimate",
    }


def _model_row(date: str, model: str, tokens: int) -> dict[str, Any]:
    return {**_row(date, tokens), "model": model}


def _source(source: str, days: list[tuple[str, int]]) -> dict[str, Any]:
    return {
        "source_id": source,
        "source_label": source.title(),
        "collector_id": "ccusage",
        "daily": [_row(date, tokens) for date, tokens in days],
        "monthly": [],
        "sessions": [],
        "model_daily": [_model_row(date, "m1", tokens) for date, tokens in days],
        "models": [],
        "totals": {},
        "provenance": {"adapter": "fixture"},
    }


def _by_agent(days: dict[str, dict[str, int]]) -> str:
    """A ccusage `daily --json --by-agent` payload: {date: {agent: tokens}}."""
    return json.dumps(
        {
            "daily": [
                {
                    "date": date,
                    "agents": [
                        {
                            "agent": agent,
                            "inputTokens": tokens,
                            "totalTokens": tokens,
                            "totalCost": tokens / 1000,
                            "modelBreakdowns": [
                                {
                                    "modelName": "m1",
                                    "inputTokens": tokens,
                                    "totalTokens": tokens,
                                    "totalCost": tokens / 1000,
                                }
                            ],
                        }
                        for agent, tokens in agents.items()
                    ],
                }
                for date, agents in sorted(days.items())
            ]
        }
    )


# --------------------------------------------------------------------------- #
# Planning                                                                    #
# --------------------------------------------------------------------------- #


def test_no_history_means_a_rebuild_whatever_was_asked() -> None:
    for requested in ("auto", "incremental"):
        plan = plan_refresh(requested, {}, COMMAND, now=NOON)
        assert plan.kind == PLAN_REBUILD
        assert plan.since is None
        assert plan.argv(COMMAND) == COMMAND


def test_history_means_a_window_from_the_day_before_the_last_refresh() -> None:
    updated = dt.datetime(2026, 10, 29, 18, 56).timestamp()
    cache = {"sources": {"claude": {}}, "updated_at": updated}
    plan = plan_refresh("auto", cache, COMMAND, now=NOON)

    assert plan.kind == PLAN_INCREMENTAL
    assert plan.since == dt.date(2026, 10, 28)
    assert plan.argv(COMMAND) == [*COMMAND, "--since", "20261028"]


def test_a_last_refresh_in_the_future_is_clamped_to_today() -> None:
    future = dt.datetime(2026, 11, 20, 9, 0).timestamp()
    cache = {"sources": {"claude": {}}, "updated_at": future}
    plan = plan_refresh("auto", cache, COMMAND, now=NOON)
    assert plan.since == dt.date(2026, 10, 30)


def test_a_rebuild_reads_everything_even_with_history() -> None:
    cache = {"sources": {"claude": {}}, "updated_at": NOON}
    assert plan_refresh("rebuild", cache, COMMAND, now=NOON).kind == PLAN_REBUILD


@pytest.mark.parametrize(
    "extra",
    [["--since", "20260101"], ["-s", "20260101"], ["--since=20260101"], ["--last", "7"]],
)
def test_a_command_that_sets_its_own_window_is_run_as_configured(extra: list[str]) -> None:
    command = [*COMMAND, *extra]
    assert command_bounds_dates(command)
    plan = plan_refresh("auto", {"sources": {"claude": {}}, "updated_at": NOON}, command, now=NOON)
    assert plan.kind == PLAN_CONFIGURED
    assert plan.argv(command) == command


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(UsageAdapterError, match="unknown refresh mode"):
        plan_refresh("everything", {}, COMMAND, now=NOON)


# --------------------------------------------------------------------------- #
# Merging                                                                     #
# --------------------------------------------------------------------------- #


def test_the_window_replaces_its_days_and_keeps_everything_before() -> None:
    cached = {
        "claude": _source("claude", [("2026-10-27", 10), ("2026-10-28", 20), ("2026-10-29", 30)]),
        "codex": _source("codex", [("2026-10-20", 5), ("2026-10-29", 7)]),
        "amp": _source("amp", [("2026-10-29", 9)]),
    }
    fresh = {
        "claude": _source("claude", [("2026-10-28", 25), ("2026-10-30", 40)]),
        "gemini": _source("gemini", [("2026-10-30", 3)]),
    }

    merged = merge_usage_window(cached, fresh, "2026-10-28")

    claude = merged["claude"]
    assert [(row["date"], row["total_tokens"]) for row in claude["daily"]] == [
        ("2026-10-27", 10),
        ("2026-10-28", 25),  # the window's answer, not the cached 20
        ("2026-10-30", 40),
    ]  # 10-29 is gone: the window covers it and no longer reports it
    assert claude["totals"]["total_tokens"] == 75
    assert [(row["month"], row["total_tokens"]) for row in claude["monthly"]] == [("2026-10", 75)]
    assert claude["models"][0]["total_tokens"] == 75
    # A source with nothing in the window keeps only its days before it.
    assert [row["date"] for row in merged["codex"]["daily"]] == ["2026-10-20"]
    assert merged["codex"]["totals"]["total_tokens"] == 5
    # A source whose every day fell inside the window and was not reported is dropped.
    assert "amp" not in merged
    # A source first seen in the window is added.
    assert merged["gemini"]["totals"]["total_tokens"] == 3


def test_fresh_rows_before_the_window_are_not_trusted_over_the_cache() -> None:
    cached = {"claude": _source("claude", [("2026-10-01", 10)])}
    fresh = {"claude": _source("claude", [("2026-10-01", 999), ("2026-10-30", 1)])}

    merged = merge_usage_window(cached, fresh, "2026-10-29")

    assert [(row["date"], row["total_tokens"]) for row in merged["claude"]["daily"]] == [
        ("2026-10-01", 10),
        ("2026-10-30", 1),
    ]


def test_an_empty_window_is_an_answer_only_when_one_was_asked_for() -> None:
    assert normalize_usage_sources({"daily": []}, {}, allow_empty=True) == {}
    with pytest.raises(UsageAdapterError):
        normalize_usage_sources({"daily": []}, {})
    with pytest.raises(UsageAdapterError):
        normalize_usage_sources({"unexpected": True}, {}, allow_empty=True)


# --------------------------------------------------------------------------- #
# The manager                                                                 #
# --------------------------------------------------------------------------- #


class _FakeCcusage:
    """Answers `_invoke` with scripted payloads and records the argv it was given."""

    def __init__(self, *payloads: str) -> None:
        self.payloads = list(payloads)
        self.calls: list[list[str]] = []
        self.release: asyncio.Event | None = None

    async def __call__(
        self, manager: UsageManager, command: list[str], *, operation_id: str | None = None
    ) -> str:
        assert operation_id
        self.calls.append(list(command))
        if self.release is not None:
            await self.release.wait()
        return self.payloads.pop(0)


def _manager(tmp_path: Path, fake: _FakeCcusage, *, now: float = NOON) -> UsageManager:
    config = Config(data_dir=tmp_path, ccusage_enabled=True, usage_command=list(COMMAND))
    manager = UsageManager(config, EventBus(), clock=lambda: now)
    manager._invoke = MethodType(fake, manager)  # type: ignore[method-assign]

    async def no_version(self: UsageManager, *_args: Any, **_kwargs: Any) -> str | None:
        return self._version.version

    manager._ccusage_version = MethodType(no_version, manager)  # type: ignore[method-assign]
    return manager


async def test_a_second_refresh_reads_a_window_and_merges_it(tmp_path: Path) -> None:
    fake = _FakeCcusage(
        _by_agent(
            {
                "2026-10-20": {"claude": 100},
                "2026-10-30": {"claude": 10, "codex": 5},
                "2026-10-31": {"claude": 1},
            }
        ),
        _by_agent({"2026-10-30": {"claude": 20}, "2026-10-31": {"claude": 2}}),
    )
    manager = _manager(tmp_path, fake)

    first = await manager.refresh()
    second = await manager.refresh()

    assert fake.calls[0] == COMMAND
    assert fake.calls[1] == [*COMMAND, "--since", "20261030"]
    sources = second["cache"]["sources"]
    assert [(row["date"], row["total_tokens"]) for row in sources["claude"]["daily"]] == [
        ("2026-10-20", 100),
        ("2026-10-30", 20),
        ("2026-10-31", 2),
    ]
    assert "codex" not in sources  # its only day is in the window, which no longer has it
    assert first["collector"]["last_run"]["kind"] == PLAN_REBUILD
    assert second["collector"]["last_run"]["kind"] == PLAN_INCREMENTAL
    assert second["collector"]["last_run"]["since"] == "2026-10-30"
    collection = second["cache"]["collection"]
    # The history was computed by the rebuild; the window only topped it up.
    assert collection["history"]["rebuilt_at"] == NOON
    assert collection["last_refresh"]["kind"] == PLAN_INCREMENTAL
    on_disk = json.loads((tmp_path / "usage-cache.json").read_text(encoding="utf-8"))
    assert on_disk["sources"] == sources


async def test_a_window_with_no_usage_keeps_the_history_before_it(tmp_path: Path) -> None:
    fake = _FakeCcusage(_by_agent({"2026-10-01": {"claude": 7}}), json.dumps({"daily": []}))
    manager = _manager(tmp_path, fake)

    await manager.refresh()
    second = await manager.refresh()

    assert second["collector"]["status"] == "ready"
    assert second["cache"]["sources"]["claude"]["totals"]["total_tokens"] == 7


async def test_a_cache_from_before_provenance_is_topped_up_not_claimed(tmp_path: Path) -> None:
    legacy = {
        "version": 3,
        "updated_at": NOON - 86400,
        "sources": {"claude": _source("claude", [("2026-10-01", 5)])},
    }
    (tmp_path / "usage-cache.json").write_text(json.dumps(legacy), encoding="utf-8")
    fake = _FakeCcusage(_by_agent({"2026-10-30": {"claude": 1}}))
    manager = _manager(tmp_path, fake)
    manager._version = usage._VersionProbe(version="20.0.46")

    snapshot = await manager.refresh()

    assert fake.calls[0][-2:] == ["--since", "20261029"]
    history = snapshot["cache"]["collection"]["history"]
    assert history == {"ccusage_version": None, "timezone": None, "rebuilt_at": None}
    notes = snapshot["collector"]["advisories"]
    assert not any("Older days were computed" in note for note in notes)


def test_advisories_name_an_old_ccusage_and_stale_history(tmp_path: Path) -> None:
    manager = _manager(tmp_path, _FakeCcusage())
    manager.cache = {
        "collection": {
            "history": {"ccusage_version": "20.0.23", "timezone": "Elsewhere", "rebuilt_at": 1.0}
        }
    }
    manager._version = usage._VersionProbe(version="20.0.23")
    notes = manager.advisories()
    assert any("ccusage 20.0.23 reads every Claude Code transcript" in note for note in notes)
    assert any("another timezone (Elsewhere)" in note for note in notes)

    manager._version = usage._VersionProbe(version="20.0.46")
    notes = manager.advisories()
    assert not any("reads every Claude Code transcript" in note for note in notes)
    assert any("computed by ccusage 20.0.23" in note for note in notes)


async def test_start_refresh_answers_at_once_and_reports_progress(tmp_path: Path) -> None:
    fake = _FakeCcusage(_by_agent({"2026-10-30": {"claude": 1}}))
    fake.release = asyncio.Event()
    manager = _manager(tmp_path, fake)

    started = manager.start_refresh("auto")
    assert started["started"] is True
    assert started["refreshing"] is True
    again = manager.start_refresh("rebuild")
    assert again["started"] is False
    with pytest.raises(UsageAdapterError, match="refresh is running"):
        manager.clear()

    fake.release.set()
    assert manager._manual is not None
    await manager._manual
    assert manager.snapshot()["refreshing"] is False
    assert manager.snapshot()["collector"]["status"] == "ready"


def test_a_disabled_collector_refuses_to_start(tmp_path: Path) -> None:
    manager = UsageManager(Config(data_dir=tmp_path), EventBus())
    with pytest.raises(UsageAdapterError, match="disabled"):
        manager.start_refresh()


async def test_the_refresh_route_answers_202_without_waiting(tmp_path: Path) -> None:
    fake = _FakeCcusage(_by_agent({"2026-10-30": {"claude": 1}}))
    fake.release = asyncio.Event()
    manager = _manager(tmp_path, fake)
    app = web.Application(middlewares=[error_middleware])
    app[keys.USAGE] = manager
    app.router.add_get("/api/usage", get_usage)
    app.router.add_post("/api/usage/refresh", refresh_usage)

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/usage/refresh", json={"mode": "rebuild"})
        assert response.status == 202
        body = await response.json()
        assert body["started"] is True and body["refreshing"] is True
        bad = await client.post("/api/usage/refresh", json={"mode": "sideways"})
        assert bad.status == 400
        fake.release.set()
        assert manager._manual is not None
        await manager._manual
        done = await (await client.get("/api/usage")).json()
        assert done["refreshing"] is False
        assert done["collector"]["last_run"]["kind"] == PLAN_REBUILD


# --------------------------------------------------------------------------- #
# The schedule                                                                #
# --------------------------------------------------------------------------- #


def test_the_schedule_is_due_from_the_last_refresh_and_respects_settings(
    tmp_path: Path,
) -> None:
    clock = {"now": NOON}
    config = Config(data_dir=tmp_path, ccusage_enabled=True, ccusage_refresh_minutes=180)
    manager = UsageManager(config, EventBus(), clock=lambda: clock["now"])
    manager.cache = {"sources": {"claude": {}}, "updated_at": NOON}

    assert not manager._scheduled_refresh_due(NOON + 179 * 60)
    assert manager._scheduled_refresh_due(NOON + 180 * 60)
    config.ccusage_refresh_minutes = 0
    assert not manager._scheduled_refresh_due(NOON + 999 * 60)
    config.ccusage_refresh_minutes = 180
    config.ccusage_enabled = False
    assert not manager._scheduled_refresh_due(NOON + 999 * 60)
    config.ccusage_enabled = True
    # Just after startup, a refresh that is long overdue still waits out the grace.
    manager._not_before = NOON + 10 * 3600
    assert not manager._scheduled_refresh_due(NOON + 9 * 3600)
    assert manager._scheduled_refresh_due(NOON + 10 * 3600)


async def test_the_loop_checks_the_host_then_runs_a_due_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "SCHEDULE_TICK_SECONDS", 0.01)
    order: list[str] = []
    fake = _FakeCcusage(_by_agent({"2026-10-30": {"claude": 1}}))
    manager = _manager(tmp_path, fake)
    manager.config.ccusage_refresh_minutes = 180

    async def host_load() -> tuple[float | None, float | None]:
        order.append("host")
        return 5.0, 20.0

    manager._host_load = host_load
    loop_task = asyncio.create_task(manager._background())
    try:
        await until(lambda: bool(fake.calls), what="the scheduled refresh ran")
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    assert order == ["host"]
    assert manager.state.last_run is not None
    assert manager.state.last_run["trigger"] == "scheduled"


async def test_the_loop_rereads_the_cadence_and_the_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings changed mid-cadence apply within a tick, not after the old sleep."""
    monkeypatch.setattr(usage, "SCHEDULE_TICK_SECONDS", 0.01)
    fake = _FakeCcusage(_by_agent({"2026-10-30": {"claude": 1}}))
    manager = _manager(tmp_path, fake)
    manager.config.ccusage_refresh_minutes = 0

    async def host_load() -> tuple[float | None, float | None]:
        return 5.0, 20.0

    manager._host_load = host_load
    loop_task = asyncio.create_task(manager._background())
    try:
        # A real quiet window guarding a negative assertion: nothing may run.
        await asyncio.sleep(0.3)
        assert fake.calls == []
        manager.config.ccusage_refresh_minutes = 180
        await until(lambda: bool(fake.calls), what="the refresh ran once enabled")
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


async def test_a_busy_host_defers_until_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "HOST_RECHECK_SECONDS", 0.0)
    readings = [(97.0, 40.0), (50.0, 95.0), (12.0, 40.0)]
    seen_phases: list[str | None] = []

    async def host_load() -> tuple[float | None, float | None]:
        seen_phases.append(manager.state.phase)
        return readings.pop(0)

    config = Config(data_dir=tmp_path, ccusage_enabled=True)
    manager = UsageManager(config, EventBus(), host_load=host_load)

    await manager._wait_for_quiet_host()

    assert readings == []
    assert seen_phases == [None, "deferred", "deferred"]
    assert manager.state.phase is None


async def test_a_host_that_never_quietens_still_gets_its_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(usage, "HOST_RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(usage, "HOST_MAX_DEFERRAL_SECONDS", 0.05)
    calls = 0

    async def host_load() -> tuple[float | None, float | None]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 99.0, 99.0

    manager = UsageManager(
        Config(data_dir=tmp_path, ccusage_enabled=True), EventBus(), host_load=host_load
    )
    started = time.monotonic()
    await manager._wait_for_quiet_host()
    assert calls >= 2
    assert time.monotonic() - started < 30


def test_host_pressure_names_what_is_saturated() -> None:
    assert host_pressure((10.0, 30.0)) is None
    assert host_pressure((None, None)) is None
    assert host_pressure((90.0, 30.0)) == "CPU 90%"
    assert host_pressure((90.0, 92.0)) == "CPU 90%, memory 92%"


# --------------------------------------------------------------------------- #
# Launching ccusage                                                           #
# --------------------------------------------------------------------------- #

_NPM_CMD_SHIM = (
    "@ECHO off\r\nGOTO start\r\n:start\r\nSETLOCAL\r\n"
    'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  '
    '"%dp0%\\node_modules\\ccusage\\src\\cli.js" %*\r\n'
)
_NPM_SH_SHIM = (
    '#!/bin/sh\nbasedir=$(dirname "$0")\n'
    'exec node  "$basedir/node_modules/ccusage/src/cli.js" "$@"\n'
)


def _package(root: Path) -> Path:
    package = root / "node_modules" / "ccusage"
    (package / "src").mkdir(parents=True)
    (package / "src" / "cli.js").write_text("// shim target\n", encoding="utf-8")
    return package


def test_the_native_binary_is_found_nested_inside_the_package(tmp_path: Path) -> None:
    package = _package(tmp_path)
    native = package / "node_modules" / "@ccusage" / "ccusage-win32-x64" / "bin" / "ccusage.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"MZ")
    shim = tmp_path / "ccusage.cmd"
    shim.write_text(_NPM_CMD_SHIM, encoding="utf-8")

    assert find_ccusage_native(shim, platform_name="win32", machine="AMD64") == native.resolve()


def test_the_native_binary_is_found_hoisted_beside_the_package(tmp_path: Path) -> None:
    _package(tmp_path)
    native = tmp_path / "node_modules" / "@ccusage" / "ccusage-linux-arm64" / "bin" / "ccusage"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"\x7fELF")
    shim = tmp_path / "ccusage"
    shim.write_text(_NPM_SH_SHIM, encoding="utf-8")

    assert find_ccusage_native(shim, platform_name="linux", machine="aarch64") == native.resolve()


def test_a_project_local_bin_shim_resolves_too(tmp_path: Path) -> None:
    # `npm install --prefix <dir>` writes `node_modules/.bin/ccusage.cmd`, which names
    # `..\ccusage\src\cli.js` with no `node_modules` segment of its own.
    package = _package(tmp_path)
    native = package / "node_modules" / "@ccusage" / "ccusage-win32-x64" / "bin" / "ccusage.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"MZ")
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir()
    shim = bin_dir / "ccusage.cmd"
    shim.write_text(
        _NPM_CMD_SHIM.replace("\\node_modules\\ccusage\\", "\\..\\ccusage\\"), encoding="utf-8"
    )

    assert find_ccusage_native(shim, platform_name="win32", machine="AMD64") == native.resolve()


def test_a_layout_without_the_binary_falls_back_to_the_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _package(tmp_path)
    shim = tmp_path / "ccusage.cmd"
    shim.write_text(_NPM_CMD_SHIM, encoding="utf-8")
    assert find_ccusage_native(shim, platform_name="win32", machine="AMD64") is None

    monkeypatch.setattr(usage.shutil, "which", lambda _name: str(shim))
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    launch = resolve_usage_launch(COMMAND, windows=True)
    assert launch.launcher == "shim"
    assert launch.program == [r"C:\Windows\System32\cmd.exe", "/d", "/c", str(shim)]
    assert launch.is_ccusage


def test_a_found_binary_replaces_the_shim_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package(tmp_path)
    native = package / "node_modules" / "@ccusage" / "ccusage-win32-x64" / "bin" / "ccusage.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"MZ")
    shim = tmp_path / "ccusage.cmd"
    shim.write_text(_NPM_CMD_SHIM, encoding="utf-8")
    monkeypatch.setattr(usage.shutil, "which", lambda _name: str(shim))
    monkeypatch.setattr(usage.platform, "machine", lambda: "AMD64")

    launch = resolve_usage_launch(COMMAND, windows=True)

    assert launch.launcher == "native"
    assert launch.argv == [str(native.resolve()), "daily", "--json", "--by-agent"]


def test_a_command_that_is_not_ccusage_is_never_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    npx = tmp_path / "npx"
    npx.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(usage.shutil, "which", lambda _name: str(npx))
    launch = resolve_usage_launch(["npx", "ccusage@latest", "daily"], windows=False)
    assert launch.launcher == "command"
    assert not launch.is_ccusage
    assert launch.argv == [str(npx), "ccusage@latest", "daily"]


async def test_the_version_is_probed_once_per_installed_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "ccusage"
    binary.write_text("not a shim", encoding="utf-8")
    monkeypatch.setattr(usage.shutil, "which", lambda _name: str(binary))
    probes: list[list[str]] = []

    async def fake_run_bounded(argv: Any, **_kwargs: Any) -> ProcessOutcome:
        probes.append(list(argv))
        return ProcessOutcome(0, b"ccusage 20.0.23\n", b"", False, False, 1.0)

    monkeypatch.setattr(usage, "run_bounded", fake_run_bounded)
    manager = UsageManager(Config(data_dir=tmp_path, ccusage_enabled=True), EventBus())

    assert await manager._ccusage_version(COMMAND) == "20.0.23"
    assert await manager._ccusage_version(COMMAND) == "20.0.23"
    assert len(probes) == 1
    assert probes[0][-1] == "--version"

    binary.write_text("an upgraded binary", encoding="utf-8")
    await manager._ccusage_version(COMMAND)
    assert len(probes) == 2
