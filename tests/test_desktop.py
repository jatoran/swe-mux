from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
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
from swe_mux.desktop_window_state import (
    DesktopWindowState,
    WindowStateRecorder,
    fit_window_state,
    load_window_state,
    save_window_state,
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


def test_desktop_window_state_round_trips_and_rejects_malformed_data(tmp_path: Path) -> None:
    path = tmp_path / "desktop-window-state.json"
    expected = DesktopWindowState(x=-1200, y=80, width=1180, height=760, maximized=True)

    save_window_state(path, expected)

    assert load_window_state(path) == expected
    path.write_text('{"version": 1, "x": true}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_window_state(path)


def test_desktop_window_state_fits_the_current_monitor_topology() -> None:
    screens = [
        SimpleNamespace(x=-1920, y=0, width=1920, height=1040),
        SimpleNamespace(x=0, y=0, width=1920, height=1040),
    ]

    on_secondary = fit_window_state(
        DesktopWindowState(x=-1700, y=100, width=1200, height=800), screens
    )
    assert on_secondary == DesktopWindowState(x=-1700, y=100, width=1200, height=800)

    missing_monitor = fit_window_state(
        DesktopWindowState(x=3000, y=1200, width=2400, height=1400), screens
    )
    assert missing_monitor == DesktopWindowState(x=0, y=0, width=1920, height=1040)


def test_desktop_window_recorder_preserves_normal_bounds_across_window_states(
    tmp_path: Path,
) -> None:
    path = tmp_path / "desktop-window-state.json"
    initial = DesktopWindowState(x=100, y=120, width=1100, height=700)
    recorder = WindowStateRecorder(path, initial, debounce_seconds=60)

    recorder.moved(140, 160)
    recorder.resized(1240, 810)
    assert recorder.flush() == DesktopWindowState(140, 160, 1240, 810)

    recorder.maximized()
    recorder.moved(0, 0)
    recorder.resized(1920, 1040)
    assert recorder.flush() == DesktopWindowState(140, 160, 1240, 810, maximized=True)

    recorder.minimized()
    recorder.moved(-32000, -32000)
    assert recorder.flush() == DesktopWindowState(140, 160, 1240, 810, maximized=True)

    recorder.restored()
    recorder.moved(180, 200)
    recorder.resized(1280, 840)
    assert recorder.flush() == DesktopWindowState(180, 200, 1280, 840)
    recorder.close()
    assert load_window_state(path) == DesktopWindowState(180, 200, 1280, 840)


def test_desktop_window_recorder_seeds_first_launch_from_shown_geometry(tmp_path: Path) -> None:
    path = tmp_path / "desktop-window-state.json"
    recorder = WindowStateRecorder(path, None, debounce_seconds=60)

    recorder.moved(240, 160)
    recorder.resized(1440, 920)

    assert recorder.flush() == DesktopWindowState(240, 160, 1440, 920)
    assert load_window_state(path) == DesktopWindowState(240, 160, 1440, 920)


def test_frontend_publish_never_empties_live_static_tree(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    destination = tmp_path / "static"
    (staging / "assets").mkdir(parents=True)
    (destination / "assets").mkdir(parents=True)
    (staging / "assets" / "index-current.css").write_text("current", encoding="utf-8")
    (staging / "index.html").write_text("index-current.css", encoding="utf-8")
    (staging / "assets" / "ort-wasm-simd-threaded-new.wasm").write_bytes(b"runtime")
    (destination / "assets" / "index-stale.css").write_text("stale", encoding="utf-8")
    # The voice detector is ~13 MB per onnxruntime-web version, so a superseded copy
    # left behind is the largest thing this sweep exists to prevent.
    (destination / "assets" / "ort-wasm-simd-threaded-old.wasm").write_bytes(b"old runtime")
    (destination / "assets" / "silero_vad_v5-old.onnx").write_bytes(b"old model")
    (destination / "notification-sounds").mkdir()
    (destination / "notification-sounds" / "ding.mp3").write_bytes(b"sound")

    publish_frontend(staging, destination)

    assert (destination / "assets" / "index-current.css").read_text() == "current"
    assert (destination / "index.html").read_text() == "index-current.css"
    assert not (destination / "assets" / "index-stale.css").exists()
    assert (destination / "assets" / "ort-wasm-simd-threaded-new.wasm").read_bytes() == b"runtime"
    assert not (destination / "assets" / "ort-wasm-simd-threaded-old.wasm").exists()
    assert not (destination / "assets" / "silero_vad_v5-old.onnx").exists()
    assert (destination / "notification-sounds" / "ding.mp3").read_bytes() == b"sound"


def test_frontend_publish_drops_every_precompressed_variant(tmp_path: Path) -> None:
    """A stale `.gz` outliving its source is a blank screen, not a slow page.

    The daemon serves a precompressed variant to any client sending
    `Accept-Encoding: gzip`, which is every browser. `index.html.gz` names
    content-hashed assets, so last build's copy points at files this build deleted:
    the page loads and every asset 404s. It hides from the usual asset-hash check
    too, because `curl` without `--compressed` is served the correct plain
    `index.html`.

    Vite emits no `.gz`, so a publish that only copies cannot overwrite them. They
    are dropped instead, and the caller regenerates them.
    """
    staging = tmp_path / "staging"
    destination = tmp_path / "static"
    (staging / "assets").mkdir(parents=True)
    (destination / "assets").mkdir(parents=True)
    (staging / "index.html").write_text("index-new.js", encoding="utf-8")
    (staging / "assets" / "index-new.js").write_text("new", encoding="utf-8")
    # Everything the previous build left behind, compressed.
    (destination / "index.html").write_text("index-old.js", encoding="utf-8")
    (destination / "index.html.gz").write_bytes(b"gzipped index-old.js")
    (destination / "sw.js.gz").write_bytes(b"gzipped old worker")
    (destination / "assets" / "index-old.js").write_text("old", encoding="utf-8")
    (destination / "assets" / "index-old.js.gz").write_bytes(b"gzipped old")

    publish_frontend(staging, destination)

    assert (destination / "index.html").read_text() == "index-new.js"
    assert not (destination / "index.html.gz").exists()
    assert not (destination / "sw.js.gz").exists()
    assert not (destination / "assets" / "index-old.js.gz").exists()
    assert not (destination / "assets" / "index-old.js").exists()


def test_the_desktop_frontend_build_repeats_every_postbuild_step() -> None:
    """`vite build` runs directly here, so npm's `postbuild` hook never fires.

    Each step that hook performs has to be repeated in `build_frontend`, and
    forgetting one is silent: the bundle ships, the daemon reports healthy, and the
    defect only appears in a browser. Omitting `compress-static` did exactly that.

    Asserted against the package manifest rather than a hardcoded list, so a script
    added to `postbuild` later is required to appear here too.

    Only `postbuild` is checked because it is the only uncovered hook: `build_frontend`
    runs `npm run check`, whose own `precheck` performs every `prebuild` step.
    """
    import json
    import re

    root = Path(__file__).resolve().parent.parent
    manifest = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    postbuild = manifest["scripts"]["postbuild"]
    source = (root / "packaging" / "build_desktop.py").read_text(encoding="utf-8")
    build_frontend = source.split("def build_frontend()")[1].split("\ndef ")[0]

    scripts = set(re.findall(r"scripts/([\w.-]+\.mjs)", postbuild))
    assert scripts, "postbuild runs no scripts; this guard would assert nothing"
    for script in sorted(scripts):
        assert f"scripts/{script}" in build_frontend, (
            f"frontend/package.json runs scripts/{script} in postbuild, which the "
            f"desktop build bypasses. Run it explicitly in build_frontend."
        )


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


def test_tray_quit_shuts_down_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class HiddenWindow:
        def create_confirmation_dialog(self, *_: object) -> bool:
            raise AssertionError("tray quit must not prompt via the hidden WebView")

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

    runtime.quit()

    assert events == [
        "daemon-stopped",
        "icon-stopped",
        "window-destroyed",
    ]
    assert runtime.exiting
    assert runtime.stop.is_set()


class FakeDaemonChild:
    """A spawned daemon that reports "still running" until it is told otherwise."""

    def __init__(self, exit_after: int | None = None) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self._polls = 0
        self._exit_after = exit_after

    def poll(self) -> int | None:
        self._polls += 1
        if self._exit_after is not None and self._polls > self._exit_after:
            self.returncode = 3
        return self.returncode


def tray_runtime(tmp_path: Path) -> DesktopRuntime:
    runtime = object.__new__(DesktopRuntime)
    runtime.config = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")
    runtime.url = "http://127.0.0.1:8765"
    runtime.token = "desktop-secret"
    runtime.child = None
    runtime.window = None
    runtime.icon = None
    runtime.hidden = False
    runtime.exiting = False
    runtime.stop = threading.Event()
    return runtime


def spawn_child(monkeypatch: pytest.MonkeyPatch, child: FakeDaemonChild) -> None:
    monkeypatch.setattr("swe_mux.desktop.popen_outside_job", lambda *_a, **_k: child)
    monkeypatch.setattr(DesktopRuntime, "_watch_daemon_exit", lambda *_a: None)


def test_a_slow_daemon_is_not_reported_as_a_failed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A daemon that is still starting must never be judged as one that failed.

    The whole class of bug: a cold start binds its port later than the tray was
    willing to wait, and the tray killed itself over a daemon that went on to
    serve normally.
    """
    runtime = tray_runtime(tmp_path)
    child = FakeDaemonChild()
    spawn_child(monkeypatch, child)
    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_a, **_k: None)

    assert runtime.ensure_daemon(wait_seconds=0.3) is False
    assert runtime.child is child
    ledger_text = (tmp_path / "lifecycle.log").read_text(encoding="utf-8")
    assert "still starting" in ledger_text


def test_the_default_health_budget_is_read_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DAEMON_HEALTH_TIMEOUT_SECONDS` is the authority, not a bound default.

    Held as a default argument value it is fixed at import, which makes the
    constant decorative: changing it changes nothing at the only call that uses
    it. Caught by this suite taking 300s to assert a 0.3s wait.
    """
    runtime = tray_runtime(tmp_path)
    spawn_child(monkeypatch, FakeDaemonChild())
    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr("swe_mux.desktop.DAEMON_HEALTH_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    assert runtime.ensure_daemon() is False
    assert time.monotonic() - started < 30


def test_a_daemon_that_exits_fails_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one real failure, and it must not wait out the whole budget."""
    runtime = tray_runtime(tmp_path)
    child = FakeDaemonChild(exit_after=1)
    spawn_child(monkeypatch, child)
    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_a, **_k: None)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="exited during startup"):
        runtime.ensure_daemon(wait_seconds=600.0)
    assert time.monotonic() - started < 30


def test_a_healthy_daemon_is_adopted_without_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tray_runtime(tmp_path)
    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(
        "swe_mux.desktop.popen_outside_job",
        lambda *_a, **_k: pytest.fail("a serving daemon must not be replaced"),
    )

    assert runtime.ensure_daemon() is True
    assert runtime.child is None


def test_the_window_loads_when_a_slow_daemon_finally_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded: list[str] = []
    runtime = tray_runtime(tmp_path)
    runtime.child = cast(Any, FakeDaemonChild())
    runtime.window = SimpleNamespace(load_url=loaded.append)
    answers = iter([None, None, {"ok": True}])
    monkeypatch.setattr(
        "swe_mux.desktop.health_snapshot", lambda *_a, **_k: next(answers, {"ok": True})
    )
    monkeypatch.setattr("swe_mux.desktop.DAEMON_HEALTH_POLL_SECONDS", 0.0)

    runtime.load_when_healthy()

    assert loaded == [runtime.url]


def test_a_daemon_that_dies_while_waiting_warns_instead_of_hanging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[tuple[str, str]] = []
    runtime = tray_runtime(tmp_path)
    runtime.child = cast(Any, FakeDaemonChild(exit_after=0))
    runtime.window = SimpleNamespace(
        load_url=lambda _url: pytest.fail("a dead daemon must not load the window")
    )
    monkeypatch.setattr("swe_mux.desktop.health_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr("swe_mux.desktop.DAEMON_HEALTH_POLL_SECONDS", 0.0)
    monkeypatch.setattr(
        "swe_mux.desktop.show_desktop_warning",
        lambda title, message: warnings.append((title, message)),
    )

    runtime.load_when_healthy()

    assert len(warnings) == 1
    assert "exited during startup" in warnings[0][1]


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
    app[keys.SHUTDOWN_STATE] = {"intent": None}
    if token is not None:
        app[keys.DESKTOP_CONTROL_TOKEN] = token
        app[keys.DESKTOP_SHUTDOWN_EVENT] = event
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
        assert await accepted.json() == {"status": "shutting_down", "mode": "quit"}
        assert event.is_set()
        assert app[keys.SHUTDOWN_STATE]["intent"] == "quit"
        restart = await client.post(
            "/api/desktop/shutdown",
            headers={"Authorization": "Bearer secret-token"},
            json={"mode": "restart"},
        )
        assert restart.status == 202
        assert await restart.json() == {"status": "shutting_down", "mode": "restart"}
        assert app[keys.SHUTDOWN_STATE]["intent"] == "detach"
        rejected = await client.post(
            "/api/desktop/shutdown",
            headers={"Authorization": "Bearer secret-token"},
            json={"mode": "explode"},
        )
        assert rejected.status == 400
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
