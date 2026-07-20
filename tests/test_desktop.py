from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.build_support import publish_frontend
from swe_mux.config import Config
from swe_mux.desktop import (
    DesktopRuntime,
    daemon_command,
    dispatch_internal_module,
    instance_key,
    load_or_create_control_token,
    local_url,
    startup_command,
)
from swe_mux.server import desktop_shutdown, is_loopback_peer

pytestmark = pytest.mark.filterwarnings(
    "ignore:It is recommended to use web.AppKey instances for keys"
)


def test_desktop_urls_commands_and_instance_identity_are_stable(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    assert local_url(Config(host="127.0.0.1", port=8765)) == "http://127.0.0.1:8765"
    assert local_url(Config(host="::1", port=9000)) == "http://[::1]:9000"
    assert instance_key(config_path) == instance_key(config_path)
    assert instance_key(config_path) != instance_key(tmp_path / "other.toml")
    assert daemon_command(config_path, executable="python.exe", frozen=False) == [
        "python.exe",
        "-m",
        "swe_mux",
        "--config",
        str(config_path),
    ]
    assert daemon_command(config_path, executable="swe-mux.exe", frozen=True) == [
        "swe-mux.exe",
        "--daemon-child",
        "--config",
        str(config_path),
    ]
    assert startup_command(config_path, executable="swe-mux.exe", frozen=True).startswith(
        "swe-mux.exe --hidden --config"
    )


def test_desktop_control_token_is_private_and_persistent(tmp_path: Path) -> None:
    first = load_or_create_control_token(tmp_path)
    second = load_or_create_control_token(tmp_path)
    assert len(first) >= 32
    assert second == first
    assert (tmp_path / "desktop-control.token").read_text(encoding="ascii").strip() == first


def test_frontend_publish_never_empties_live_static_tree(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "static"
    (staging / "assets").mkdir(parents=True)
    (destination / "assets").mkdir(parents=True)
    (staging / "assets" / "index-current.css").write_text("current", encoding="utf-8")
    (staging / "index.html").write_text("index-current.css", encoding="utf-8")
    (destination / "assets" / "index-stale.css").write_text("stale", encoding="utf-8")
    (destination / "notification-sounds").mkdir()
    (destination / "notification-sounds" / "ding.mp3").write_bytes(b"sound")

    publish_frontend(staging, destination)

    assert (destination / "assets" / "index-current.css").read_text() == "current"
    assert (destination / "index.html").read_text() == "index-current.css"
    assert not (destination / "assets" / "index-stale.css").exists()
    assert (destination / "notification-sounds" / "ding.mp3").read_bytes() == b"sound"


def test_frozen_desktop_dispatches_allowlisted_internal_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    original = list(sys.argv)
    monkeypatch.setattr(
        "swe_mux.hook_client.main",
        lambda: calls.append(list(sys.argv)),
    )

    assert dispatch_internal_module(["-m", "swe_mux.hook_client", "SessionStart"])
    assert calls == [["swe_mux.hook_client", "SessionStart"]]
    assert sys.argv == original
    assert not dispatch_internal_module(["-m", "os", "system"])


def test_tray_quit_confirmation_does_not_depend_on_visible_webview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class HiddenWindow:
        def create_confirmation_dialog(self, *_: object) -> bool:
            raise AssertionError("tray confirmation must not use the hidden WebView")

        def destroy(self) -> None:
            events.append("window-destroyed")

    runtime = object.__new__(DesktopRuntime)
    runtime.url = "http://127.0.0.1:8765"
    runtime.token = "desktop-secret"
    runtime.child = None
    runtime.window = HiddenWindow()
    runtime.icon = SimpleNamespace(stop=lambda: events.append("icon-stopped"))
    runtime.exiting = False
    runtime.stop = threading.Event()

    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "swe_mux.desktop.request_daemon_shutdown",
        lambda *_args, **_kwargs: events.append("daemon-stopped") or True,
    )
    monkeypatch.setattr(
        "swe_mux.desktop.confirm_desktop_quit",
        lambda message: events.append(message) or True,
    )

    runtime.quit()

    assert events == [
        "Quit the desktop app and stop the daemon?",
        "daemon-stopped",
        "icon-stopped",
        "window-destroyed",
    ]
    assert runtime.exiting
    assert runtime.stop.is_set()


def test_tray_quit_cancel_keeps_hidden_runtime_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = object.__new__(DesktopRuntime)
    runtime.url = "http://127.0.0.1:8765"
    runtime.token = "desktop-secret"
    runtime.child = None
    runtime.window = SimpleNamespace()
    runtime.icon = None
    runtime.exiting = False
    runtime.stop = threading.Event()

    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("swe_mux.desktop.confirm_desktop_quit", lambda _message: False)
    monkeypatch.setattr(
        "swe_mux.desktop.request_daemon_shutdown",
        lambda *_args, **_kwargs: pytest.fail("cancel must not stop the daemon"),
    )

    runtime.quit()

    assert not runtime.exiting
    assert not runtime.stop.is_set()


def test_desktop_control_accepts_only_ip_loopback_peers() -> None:
    assert is_loopback_peer("127.0.0.1")
    assert is_loopback_peer("::1")
    assert is_loopback_peer("::1%4")
    assert not is_loopback_peer("localhost")
    assert not is_loopback_peer("100.101.102.103")
    assert not is_loopback_peer("192.168.1.2")


def control_app(token: str | None = None) -> tuple[web.Application, asyncio.Event]:
    app = web.Application()
    event = asyncio.Event()
    if token is not None:
        app["desktop_control_token"] = token
        app["desktop_shutdown_event"] = event
    app.router.add_post("/api/desktop/shutdown", desktop_shutdown)
    return app, event


@pytest.mark.asyncio
async def test_desktop_shutdown_requires_the_exact_secret() -> None:
    app, event = control_app("secret-token")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        missing = await client.post("/api/desktop/shutdown")
        assert missing.status == 403
        wrong = await client.post(
            "/api/desktop/shutdown", headers={"Authorization": "Bearer wrong"}
        )
        assert wrong.status == 403
        accepted = await client.post(
            "/api/desktop/shutdown", headers={"Authorization": "Bearer secret-token"}
        )
        assert accepted.status == 202
        assert await accepted.json() == {"status": "shutting_down"}
        assert event.is_set()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unmanaged_daemon_has_no_desktop_shutdown_authority() -> None:
    app, event = control_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/desktop/shutdown", headers={"Authorization": "Bearer anything"}
        )
        assert response.status == 404
        assert not event.is_set()
    finally:
        await client.close()
