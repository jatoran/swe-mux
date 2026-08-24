"""Session-preserving daemon self-restart: launch resolution + restart endpoint."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.routes.system import daemon_restart
from swe_mux.supervisor_client import (
    SUPERVISOR_EXE_ENV,
    dedicated_supervisor_exe,
    supervisor_command,
)

CONFIG = Path("C:/tmp/config.toml")


def make_bundle(tmp_path: Path) -> Path:
    """dist-layout fixture: <root>/swe-mux/swe-mux.exe + sibling supervisor bundle."""
    app_dir = tmp_path / "swe-mux"
    app_dir.mkdir()
    (app_dir / "swe-mux.exe").write_bytes(b"app")
    bundle_dir = tmp_path / "swe-mux-supervisor"
    bundle_dir.mkdir()
    exe = bundle_dir / "swe-mux-supervisor.exe"
    exe.write_bytes(b"supervisor")
    return exe


def test_env_override_wins_in_every_mode(tmp_path: Path) -> None:
    exe = tmp_path / "custom-supervisor.exe"
    exe.write_bytes(b"x")
    environ = {SUPERVISOR_EXE_ENV: str(exe)}
    assert dedicated_supervisor_exe(frozen=False, environ=environ) == exe
    assert dedicated_supervisor_exe(frozen=True, environ=environ) == exe
    command = supervisor_command(CONFIG, frozen=False, environ=environ)
    assert command == [str(exe), "--config", str(CONFIG)]
    # A dangling override never resolves; frozen mode then falls back.
    missing = {SUPERVISOR_EXE_ENV: str(tmp_path / "gone.exe")}
    assert dedicated_supervisor_exe(frozen=False, environ=missing) is None


def test_frozen_mode_prefers_the_sibling_supervisor_bundle(tmp_path: Path) -> None:
    exe = make_bundle(tmp_path)
    app_exe = str(tmp_path / "swe-mux" / "swe-mux.exe")
    command = supervisor_command(CONFIG, executable=app_exe, frozen=True, environ={})
    assert command == [str(exe), "--config", str(CONFIG)]


def test_frozen_mode_without_bundle_falls_back_to_supervisor_child(tmp_path: Path) -> None:
    app_dir = tmp_path / "swe-mux"
    app_dir.mkdir()
    app_exe = app_dir / "swe-mux.exe"
    app_exe.write_bytes(b"app")
    command = supervisor_command(CONFIG, executable=str(app_exe), frozen=True, environ={})
    assert command == [str(app_exe), "--supervisor-child", "--config", str(CONFIG)]


def test_source_mode_launches_the_supervisor_from_source(tmp_path: Path) -> None:
    # Even with a dist bundle on disk, source daemons must run source supervisor
    # code — otherwise edits under iteration would silently run stale frozen bytes.
    make_bundle(tmp_path)
    command = supervisor_command(CONFIG, frozen=False, environ={})
    assert command[1:] == ["-m", "swe_mux.supervisor", "--config", str(CONFIG)]


def restart_app(
    tmp_path: Path,
    *,
    relaunchable: bool = True,
    supervisor_connected: bool | None = None,
) -> tuple[web.Application, asyncio.Event, list[list[str]]]:
    app = web.Application()
    app[keys.CONFIG] = Config(data_dir=tmp_path)
    app[keys.SHUTDOWN_STATE] = {"intent": None}
    stop_event = asyncio.Event()
    spawned: list[list[str]] = []
    if relaunchable:
        app[keys.DAEMON_STOP_EVENT] = stop_event
        app[keys.DAEMON_RELAUNCH_COMMAND] = ["python", "-m", "swe_mux", "--relaunch-wait"]
    if supervisor_connected is not None:
        app[keys.SUPERVISOR] = SimpleNamespace(connected=supervisor_connected)
    app.router.add_post("/api/daemon/restart", daemon_restart)
    return app, stop_event, spawned


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_restart_requires_a_relaunchable_daemon(tmp_path: Path) -> None:
    app, stop_event, _ = restart_app(tmp_path, relaunchable=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/daemon/restart")
        assert response.status == 409
        assert (await response.json())["error"] == "restart_unavailable"
        assert not stop_event.is_set()
    finally:
        await client.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_restart_refuses_to_kill_sessions_without_the_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, stop_event, spawned = restart_app(tmp_path)
    monkeypatch.setattr(
        "swe_mux.routes.system._spawn_daemon_successor",
        lambda command, log_path: spawned.append(list(command)),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        refused = await client.post("/api/daemon/restart")
        assert refused.status == 409
        assert (await refused.json())["error"] == "supervisor_not_attached"
        assert not stop_event.is_set() and spawned == []

        forced = await client.post("/api/daemon/restart", json={"force": True})
        assert forced.status == 202
        assert await forced.json() == {"status": "restarting", "sessions_preserved": False}
        # Without a supervisor the shutdown must still reap cleanly.
        assert app[keys.SHUTDOWN_STATE]["intent"] == "quit"
        assert stop_event.is_set()
        assert spawned == [["python", "-m", "swe_mux", "--relaunch-wait"]]
    finally:
        await client.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_restart_detaches_and_spawns_a_successor_with_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, stop_event, spawned = restart_app(tmp_path, supervisor_connected=True)
    monkeypatch.setattr(
        "swe_mux.routes.system._spawn_daemon_successor",
        lambda command, log_path: spawned.append(list(command)),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/daemon/restart")
        assert response.status == 202
        assert await response.json() == {"status": "restarting", "sessions_preserved": True}
        assert app[keys.SHUTDOWN_STATE]["intent"] == "detach"
        assert stop_event.is_set()
        assert len(spawned) == 1
    finally:
        await client.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_restart_with_disconnected_supervisor_counts_as_not_attached(
    tmp_path: Path,
) -> None:
    app, stop_event, _ = restart_app(tmp_path, supervisor_connected=False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/daemon/restart")
        assert response.status == 409
        assert not stop_event.is_set()
    finally:
        await client.close()
