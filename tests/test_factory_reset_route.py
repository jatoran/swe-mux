"""The surface that asks for a factory reset, and the start that performs one.

Split from `test_factory_reset.py` because the questions are different: that
file asks what a reset does to a directory, and this one asks who is allowed to
ask for one, what they are told first, and whether the sequence that follows
actually leaves nothing holding the files the successor has to move.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.config import Config
from swe_mux.factory_reset import CONFIRMATION_PHRASE, read_request, write_request
from swe_mux.routes.maintenance import ROUTES, factory_reset, factory_reset_preview
from swe_mux.server import _run_pending_factory_reset

pytestmark = pytest.mark.filterwarnings(
    "ignore:It is recommended to use web.AppKey instances for keys"
)


def _session(sid: str, name: str, project: str, state: str = "working") -> SimpleNamespace:
    return SimpleNamespace(
        record=SimpleNamespace(
            id=sid, name=name, project_id=project, backend="claude", state=state
        )
    )


def reset_app(
    tmp_path: Path,
    *,
    relaunchable: bool = True,
    sessions: dict[str, Any] | None = None,
) -> tuple[web.Application, asyncio.Event]:
    data_dir = tmp_path / "mux"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "mux.db").write_text("x", encoding="utf-8")
    (data_dir / "bin").mkdir(exist_ok=True)
    app = web.Application()
    app[keys.CONFIG] = Config(data_dir=data_dir)
    app[keys.SHUTDOWN_STATE] = {"intent": None}
    app[keys.SESSIONS] = SimpleNamespace(sessions=sessions or {})
    app[keys.PROJECTS] = SimpleNamespace(
        projects={"p1": SimpleNamespace(name="swe-mux"), "p2": SimpleNamespace(name="site")}
    )
    stop_event = asyncio.Event()
    if relaunchable:
        app[keys.DAEMON_STOP_EVENT] = stop_event
        app[keys.DAEMON_RELAUNCH_COMMAND] = ["python", "-m", "swe_mux", "--relaunch-wait"]
    app.router.add_get("/api/maintenance/factory-reset", factory_reset_preview)
    app.router.add_post("/api/maintenance/factory-reset", factory_reset)
    return app, stop_event


def _quiet_reset(monkeypatch: pytest.MonkeyPatch, spawned: list[list[str]]) -> None:
    """Stub the two side effects a test process must not really perform."""
    monkeypatch.setattr(
        "swe_mux.routes.maintenance.system._spawn_daemon_successor",
        lambda command, log_path: spawned.append(list(command)),
    )

    async def no_supervisor(config: Config) -> bool:
        return True

    monkeypatch.setattr("swe_mux.routes.maintenance.kill_server", no_supervisor)


async def test_the_preview_names_the_sessions_rather_than_counting_them(tmp_path: Path) -> None:
    """"3 sessions end" and "your release agent ends" are different decisions."""
    app, _ = reset_app(
        tmp_path,
        sessions={
            "a": _session("a", "release", "p1"),
            "b": _session("b", "docs", "p2"),
            # An exited session is not something a reset interrupts, so listing
            # it would overstate the cost of pressing the button.
            "c": _session("c", "old", "p1", state="exited"),
        },
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        payload = await (await client.get("/api/maintenance/factory-reset")).json()
    finally:
        await client.close()
    # Grouped by Project, because that is how the reader recognizes them.
    assert [row["name"] for row in payload["sessions"]] == ["docs", "release"]
    assert [row["project"] for row in payload["sessions"]] == ["site", "swe-mux"]
    assert payload["confirmation_phrase"] == CONFIRMATION_PHRASE
    assert "mux.db" in payload["entries"]
    assert "bin" in payload["kept"] and "bin" not in payload["entries"]
    assert payload["relaunchable"] is True
    # Locality is answered by the preview as well as enforced on the press, so a
    # phone is told before it types the phrase rather than after.
    assert payload["local"] is True
    assert payload["last_result"] is None
    assert [row["item"] for row in payload["external_left"]] == [
        "windows-firewall",
        "tailscale-serve",
    ]


async def test_a_reset_is_refused_without_the_daemon_s_own_phrase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, stop_event = reset_app(tmp_path)
    spawned: list[list[str]] = []
    _quiet_reset(monkeypatch, spawned)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        for body in ({}, {"confirm": ""}, {"confirm": "reset"}, {"confirm": "factoryreset"}):
            response = await client.post("/api/maintenance/factory-reset", json=body)
            assert response.status == 400
            assert (await response.json())["error"] == "not_confirmed"
        assert not stop_event.is_set()
        assert spawned == []
        assert read_request(app[keys.CONFIG].data_dir) is None
    finally:
        await client.close()


async def test_a_daemon_that_cannot_come_back_refuses_rather_than_tries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resetting an install and then exiting would leave nothing to rebuild it."""
    app, stop_event = reset_app(tmp_path, relaunchable=False)
    spawned: list[list[str]] = []
    _quiet_reset(monkeypatch, spawned)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/maintenance/factory-reset", json={"confirm": CONFIRMATION_PHRASE}
        )
        assert response.status == 409
        assert (await response.json())["error"] == "restart_unavailable"
        assert read_request(app[keys.CONFIG].data_dir) is None
        assert not stop_event.is_set()
    finally:
        await client.close()


async def test_a_redeploy_in_flight_defers_the_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, stop_event = reset_app(tmp_path)
    spawned: list[list[str]] = []
    _quiet_reset(monkeypatch, spawned)
    monkeypatch.setattr("swe_mux.routes.maintenance.system._redeploy_lock_pid", lambda config: 4242)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/maintenance/factory-reset", json={"confirm": CONFIRMATION_PHRASE}
        )
        assert response.status == 409
        assert (await response.json())["error"] == "redeploy_in_flight"
        assert not stop_event.is_set() and spawned == []
    finally:
        await client.close()


async def test_a_confirmed_reset_reaps_records_and_hands_off_in_that_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request outlives this daemon; the supervisor must not.

    The successor is what performs the reset, and it cannot rename files a live
    supervisor still holds - so the reap has to happen here, before the spawn,
    and the request has to be on disk before either.
    """
    app, stop_event = reset_app(tmp_path, sessions={"a": _session("a", "release", "p1")})
    order: list[str] = []
    spawned: list[list[str]] = []

    async def reap(config: Config) -> bool:
        order.append("reaped")
        assert read_request(config.data_dir) is not None, "consent is recorded before the reap"
        return True

    monkeypatch.setattr("swe_mux.routes.maintenance.kill_server", reap)
    monkeypatch.setattr(
        "swe_mux.routes.maintenance.system._spawn_daemon_successor",
        lambda command, log_path: (order.append("spawned"), spawned.append(list(command)))[0],
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/maintenance/factory-reset",
            json={"confirm": " Factory Reset ", "external": True},
        )
        assert response.status == 202
        payload = await response.json()
        assert payload["status"] == "resetting"
        assert payload["sessions_reaped"] == 1
        assert payload["clear_client_storage"] is True
        assert payload["external"] is True
    finally:
        await client.close()
    assert order == ["reaped", "spawned"]
    assert spawned == [["python", "-m", "swe_mux", "--relaunch-wait"]]
    assert app[keys.SHUTDOWN_STATE]["intent"] == "quit"
    assert stop_event.is_set()
    request = read_request(app[keys.CONFIG].data_dir)
    assert request is not None and request.external is True


async def test_the_reset_is_loopback_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A phone on the tailnet is a remote control, not a console.

    Every other destructive control in swe-mux is scoped to a session or a
    Project and is reachable from the phone. This one is scoped to the machine.
    """
    app, stop_event = reset_app(tmp_path)
    spawned: list[list[str]] = []
    _quiet_reset(monkeypatch, spawned)
    monkeypatch.setattr("swe_mux.routes.maintenance.is_loopback_peer", lambda value: False)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/maintenance/factory-reset", json={"confirm": CONFIRMATION_PHRASE}
        )
        assert response.status == 403
        assert (await response.json())["error"] == "not_local"
        assert not stop_event.is_set() and spawned == []
    finally:
        await client.close()


def test_the_routes_are_registered_in_the_daemon_s_table() -> None:
    from swe_mux.routes import all_routes

    paths = {(route.method, route.path) for route in all_routes()}
    for route in ROUTES:
        assert (route.method, route.path) in paths


async def test_a_start_with_no_request_pays_one_failed_read(tmp_path: Path) -> None:
    """Every ordinary start runs this phase, so it has to cost nothing."""
    data_dir = tmp_path / "mux"
    data_dir.mkdir()
    (data_dir / "mux.db").write_text("x", encoding="utf-8")
    config = Config(data_dir=data_dir)
    await _run_pending_factory_reset(config, -1)
    assert (data_dir / "mux.db").is_file()


async def test_the_startup_phase_performs_a_pending_reset_exactly_once(tmp_path: Path) -> None:
    """Once is the contract.

    A reset that died part-way has already moved an unknowable amount of the
    install aside; re-running it on the next start would sweep the fresh install
    it had just begun writing.
    """
    data_dir = tmp_path / "mux"
    data_dir.mkdir()
    (data_dir / "mux.db").write_text("x", encoding="utf-8")
    (data_dir / "config.toml").write_text('theme = "custom"\n', encoding="utf-8")
    config = Config(data_dir=data_dir, config_path=data_dir / "config.toml")
    write_request(data_dir, external=False)

    await _run_pending_factory_reset(config, -1)
    assert not (data_dir / "mux.db").exists()
    assert read_request(data_dir) is None
    assert config.theme != "custom"

    # The second start finds no request and leaves the fresh install alone.
    (data_dir / "mux.db").write_text("fresh", encoding="utf-8")
    await _run_pending_factory_reset(config, -1)
    assert (data_dir / "mux.db").read_text(encoding="utf-8") == "fresh"


async def test_a_reset_that_raises_does_not_stop_the_daemon_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worst outcome for someone who just erased their install is no app."""
    data_dir = tmp_path / "mux"
    data_dir.mkdir()
    config = Config(data_dir=data_dir)
    write_request(data_dir)

    def explode(config: Config, request: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("swe_mux.server.perform_reset", explode)
    await _run_pending_factory_reset(config, -1)
    assert read_request(data_dir) is None
