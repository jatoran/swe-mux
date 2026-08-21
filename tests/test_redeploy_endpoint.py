"""Guards and spawn contract of the frozen-app redeploy endpoint."""

from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import server


@pytest.fixture(autouse=True)
def no_real_bundle_scan(monkeypatch: Any) -> None:
    """Endpoint tests must not scan this machine's real process table.

    The bundle-in-use gate enumerates live processes, which is slow and — on a
    dev machine actually running the frozen app — environment-dependent. Tests
    that exercise the gate override this stub with their own holders.
    """
    monkeypatch.setattr(server, "bundle_lock_holders", lambda _bundle: [])


class FakeRequest:
    def __init__(
        self, app: dict[str, Any], body: Any = None, *, remote: str = "127.0.0.1"
    ) -> None:
        self.app = app
        self._body = body
        self.remote = remote
        self.headers: dict[str, str] = {}

    async def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(response.body)


class FakeEvents:
    """Collects what the daemon broadcast, in order."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def types(self) -> list[str]:
        return [event_type for event_type, _ in self.emitted]


def _app(tmp_path: Path, *, supervisor_connected: bool = True) -> dict[str, Any]:
    return {
        "config": SimpleNamespace(data_dir=tmp_path),
        "supervisor": SimpleNamespace(connected=supervisor_connected),
        "events": FakeEvents(),
    }


def test_redeploy_source_root_finds_this_checkout() -> None:
    root = server.redeploy_source_root()
    assert root is not None
    assert (root / "packaging" / "redeploy_desktop.py").is_file()
    assert (root / "pyproject.toml").is_file()


async def test_redeploy_refused_without_source_checkout(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(server, "redeploy_source_root", lambda: None)
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 409
    assert _payload(response)["error"] == "no_source_checkout"


async def test_redeploy_refused_without_uv(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(server.shutil, "which", lambda _name: None)
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 409
    assert _payload(response)["error"] == "uv_not_found"


async def test_redeploy_refused_without_supervisor_unless_forced(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app = _app(tmp_path, supervisor_connected=False)
    response = await server.daemon_redeploy(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 409
    assert _payload(response)["error"] == "supervisor_not_attached"
    # force=true carries the same authority as killing sessions.
    spawned: list[Any] = []

    def fake_popen(*args: Any, **kwargs: Any) -> SimpleNamespace:
        spawned.append((args, kwargs))
        return SimpleNamespace(pid=4242)

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    response = await server.daemon_redeploy(  # type: ignore[arg-type]
        FakeRequest(app, body={"force": True})
    )
    assert response.status == 202
    assert spawned


async def test_redeploy_refused_while_the_bundle_is_held(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A foreign process anchoring dist/swe-mux dooms the swap; refuse up front.

    Measured live 2026-08-02: two redeploys built for minutes, stopped the
    app, then died renaming dist/swe-mux because a session-spawned process
    (which descends from the supervisor and survives the stop) held it. The
    gate names the holder instead, before anything is built or stopped.
    """
    holders = [
        {
            "pid": 4321,
            "name": "node.exe",
            "via": "cwd",
            "path": r"D:\PROJECTS\swe-mux\dist\swe-mux\_internal",
        }
    ]
    monkeypatch.setattr(server, "bundle_lock_holders", lambda _bundle: holders)
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 409
    body = _payload(response)
    assert body["error"] == "bundle_in_use"
    # The message is what the UI shows verbatim: it must name the process.
    assert "pid 4321 node.exe" in body["message"]
    assert body["holders"] == holders

    # force=true attempts anyway (the holder may exit during the build).
    spawned: list[Any] = []
    monkeypatch.setattr(
        server.subprocess,
        "Popen",
        lambda *args, **kwargs: spawned.append(args) or SimpleNamespace(pid=7),
    )
    response = await server.daemon_redeploy(  # type: ignore[arg-type]
        FakeRequest(_app(tmp_path), body={"force": True})
    )
    assert response.status == 202
    assert spawned


async def test_redeploy_single_flight_lock(tmp_path: Path) -> None:
    # A lock naming a live pid (ours) refuses a second redeploy.
    (tmp_path / "redeploy.lock").write_text(str(os.getpid()), encoding="ascii")
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 409
    assert _payload(response)["error"] == "redeploy_in_progress"


async def test_redeploy_spawn_contract(tmp_path: Path, monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_popen(command: list[str], **kwargs: Any) -> SimpleNamespace:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=31337)

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    # A stale lock (dead pid) must not block.
    (tmp_path / "redeploy.lock").write_text("999999999", encoding="ascii")
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 202
    body = _payload(response)
    assert body["status"] == "redeploying"
    assert body["pid"] == 31337
    # Lock now names the spawned process.
    assert (tmp_path / "redeploy.lock").read_text(encoding="ascii") == "31337"
    command = captured["command"]
    assert command[0].lower().endswith(("uv", "uv.exe"))
    assert any(str(part).endswith("redeploy_desktop.py") for part in command)
    assert "--restore-visibility" in command
    assert "--hidden" not in command
    kwargs = captured["kwargs"]
    # cwd is the source root, never inside dist/ (directory-lock hazard), and
    # the child env is scrubbed of parent-Claude session markers.
    cwd = Path(kwargs["cwd"]).resolve()
    assert (cwd / "pyproject.toml").is_file()
    assert "dist" not in cwd.parts


async def test_redeploy_status_reports_lock_and_log(tmp_path: Path) -> None:
    (tmp_path / "redeploy.log").write_text(
        "[redeploy] rebuilding\n[redeploy] ABORT: build failed\n", encoding="utf-8"
    )
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    body = _payload(response)
    assert body["running"] is False
    assert body["log_tail"][-1] == "[redeploy] ABORT: build failed"
    assert body["available"] is True

    (tmp_path / "redeploy.lock").write_text(str(os.getpid()), encoding="ascii")
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert _payload(response)["running"] is True


def _redeploy_module() -> Any:
    import importlib.util
    import sys

    root = Path(server.__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "packaging"))
    try:
        spec = importlib.util.spec_from_file_location(
            "redeploy_desktop_under_test", root / "packaging" / "redeploy_desktop.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_replace_dir_moves_and_reports_failure(tmp_path: Path) -> None:
    module = _redeploy_module()

    source = tmp_path / "bundle"
    source.mkdir()
    (source / "app.exe").write_bytes(b"x")
    target = tmp_path / "swapped"
    assert module.replace_dir(source, target, retry_seconds=1.0) is True
    assert (target / "app.exe").is_file() and not source.exists()
    # A missing source fails within the retry budget instead of hanging.
    assert module.replace_dir(tmp_path / "missing", target, retry_seconds=0.3) is False


def test_redeploy_health_wait_allows_cold_start_but_stops_on_process_exit(
    monkeypatch: Any,
) -> None:
    module = _redeploy_module()
    health_calls: list[object] = []
    monkeypatch.setattr(
        module,
        "health_payload",
        lambda *_args, **_kwargs: health_calls.append(object()) or None,
    )
    process = SimpleNamespace(poll=lambda: 7, returncode=7)

    assert module.APP_HEALTH_TIMEOUT_SECONDS >= 300
    assert module.wait_healthy(SimpleNamespace(), process=process) is None
    assert len(health_calls) == 1


def test_health_wait_reports_each_startup_phase_once(monkeypatch: Any) -> None:
    """The wait must read as progress, not as an outage.

    This is the redeploy half of the startup-legibility change: the daemon binds
    its listeners before its runtime exists and answers 503 with the phase it is
    in, so a multi-minute wait can name what is happening. The once-per-*phase*
    rule is the point - the elapsed seconds in the rendered line move on every
    poll, so comparing rendered text would put two lines a second in the log.
    """
    module = _redeploy_module()
    answers = [
        {"ok": False, "status": "starting", "phase": "stores", "phase_seconds": 1.0,
         "elapsed_seconds": 1.0, "phases": []},
        {"ok": False, "status": "starting", "phase": "stores", "phase_seconds": 9.0,
         "elapsed_seconds": 9.0, "phases": []},
        {"ok": False, "status": "starting", "phase": "session-reattach", "phase_seconds": 0.5,
         "elapsed_seconds": 12.0, "phases": [{"name": "stores", "seconds": 11.5}]},
        {"ok": True, "status": "ready", "live_sessions": 3},
    ]
    monkeypatch.setattr(module, "health_payload", lambda *_a, **_k: answers.pop(0))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    lines: list[str] = []
    monkeypatch.setattr(module, "log", lines.append)

    payload = module.wait_healthy(SimpleNamespace())

    assert payload is not None and payload["status"] == "ready"
    # Two phases seen, two lines - the repeated `stores` poll adds nothing.
    assert len(lines) == 2
    assert "phase stores" in lines[0]
    assert "phase session-reattach" in lines[1]
    # A finished phase is named with its cost, so the log says where the time went.
    assert "done: stores" in lines[1]


def test_health_reads_the_starting_daemons_503_body(monkeypatch: Any) -> None:
    """A starting daemon answers 503; the body is a response, not a failure.

    `health()` must still say "no usable daemon" for it - every caller of that
    means "can I use this port" - while `health_payload()` recovers the phase.
    """
    module = _redeploy_module()
    body = json.dumps({"ok": False, "status": "starting", "phase": "supervisor-connect"})

    def raise_503(*_args: Any, **_kwargs: Any) -> Any:
        raise module.urllib.error.HTTPError(
            "http://127.0.0.1:8765/api/health", 503, "starting", {}, io.BytesIO(body.encode())
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", raise_503)
    config = SimpleNamespace(port=8765)

    assert module.health(config) is None
    payload = module.health_payload(config)
    assert payload is not None and payload["phase"] == "supervisor-connect"
    assert module.startup_progress(payload).startswith("starting - phase supervisor-connect")
    # A ready payload is not startup progress and must render as nothing.
    assert module.startup_progress({"ok": True, "status": "ready"}) == ""


@pytest.mark.parametrize(
    ("window_visible", "expected_hidden"),
    [(True, False), (False, True)],
)
def test_ui_redeploy_restores_desktop_window_visibility(
    monkeypatch: Any, window_visible: bool, expected_hidden: bool
) -> None:
    module = _redeploy_module()
    monkeypatch.setattr(module, "app_window_visible", lambda: window_visible)

    assert (
        module.resolve_relaunch_hidden(hidden=False, restore_visibility=True)
        is expected_hidden
    )
    assert module.resolve_relaunch_hidden(hidden=True, restore_visibility=False) is True


def test_in_session_helpers_are_not_confused_with_the_shell_or_daemon() -> None:
    """`swe-mux.exe -m swe_mux.hook_client` lives inside a live session's tree.

    A redeploy once ran a bare `taskkill /F /IM swe-mux.exe`, which reached those
    helpers and took down the one session that was mid-tool-call. Only the shell and
    the daemon may be stopped by the ordinary path.
    """
    module = _redeploy_module()
    exe = r"D:\PROJECTS\swe-mux\dist\swe-mux\swe-mux.exe"

    def fake(*argv: str) -> Any:
        return SimpleNamespace(cmdline=lambda: list(argv))

    # The shell and the daemon child are the redeploy's actual targets.
    assert module.is_session_helper(fake(exe)) is False
    assert (
        module.is_session_helper(fake(exe, "--daemon-child", "--config", r"C:\x\config.toml"))
        is False
    )
    assert module.is_session_helper(fake(exe, "--hidden")) is False

    # Every agent tool call spawns these; they share the image name only.
    assert module.is_session_helper(fake(exe, "-m", "swe_mux.hook_client", "PreToolUse")) is True
    assert module.is_session_helper(fake(exe, "-m", "swe_mux.hook_client", "PostToolUse")) is True
    # The rule is the module form, so a future helper is covered without a new entry.
    assert module.is_session_helper(fake(exe, "-m", "swe_mux.supervisor")) is True


def test_unreadable_argv_is_spared_rather_than_killed() -> None:
    """An unprovable process must not be killed: a lock straggler is the cheaper risk."""
    module = _redeploy_module()
    import psutil

    def denied() -> list[str]:
        raise psutil.AccessDenied(1)

    assert module.is_session_helper(SimpleNamespace(cmdline=denied)) is True


def test_ordinary_stop_terminates_only_shell_pids(monkeypatch: Any) -> None:
    """The stop path signals enumerated pids and never a whole image name."""
    module = _redeploy_module()
    argv = {
        11: [r"dist\swe-mux\swe-mux.exe"],
        12: [r"dist\swe-mux\swe-mux.exe", "--daemon-child"],
        13: [r"dist\swe-mux\swe-mux.exe", "-m", "swe_mux.hook_client", "PreToolUse"],
    }
    import psutil

    monkeypatch.setattr(
        module, "processes_by_image", lambda _names: [(pid, "swe-mux.exe") for pid in argv]
    )
    # redeploy_desktop imports psutil inside each function, so patch it at the source.
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(cmdline=lambda: argv[pid]))
    killed: list[int] = []
    monkeypatch.setattr(module, "terminate_pids", lambda pids, **_: killed.extend(pids))
    monkeypatch.setattr(module, "health", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    blunt: list[Any] = []
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: blunt.append(a) or None)

    shell, helpers = module.partition_app_processes()
    assert shell == [11, 12]
    assert helpers == [13]

    module.stop_app_processes(SimpleNamespace(port=1, data_dir=Path(".")))

    assert killed == [11, 12]
    # The hook client survives, and taskkill is never reached on the ordinary path.
    assert 13 not in killed
    assert blunt == []


@pytest.mark.parametrize("marker", ["CLAUDE_CODE_SESSION_ID", "CLAUDECODE"])
async def test_redeploy_env_scrub_covers_session_markers(
    tmp_path: Path, monkeypatch: Any, marker: str
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        server.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(kwargs) or SimpleNamespace(pid=1),
    )
    monkeypatch.setenv(marker, "leaked-parent-session")
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 202
    assert marker not in captured["env"]


async def test_accepting_a_redeploy_broadcasts_the_build_stage(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Every client learns about the redeploy while the daemon is still serving.

    This is what lets a phone (or a second window, or the desktop when the phone
    started it) show a progress chip instead of discovering the redeploy minutes
    later as a wall of failed requests.
    """
    monkeypatch.setattr(
        server.subprocess, "Popen", lambda command, **kwargs: SimpleNamespace(pid=4242)
    )
    app = _app(tmp_path)
    response = await server.daemon_redeploy(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 202
    events: FakeEvents = app["events"]
    assert events.types() == ["daemon_redeploy_started"]
    assert events.emitted[0][1]["pid"] == 4242
    assert events.emitted[0][1]["phase"] == "building"


async def test_a_refused_redeploy_broadcasts_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(server, "redeploy_source_root", lambda: None)
    app = _app(tmp_path)
    response = await server.daemon_redeploy(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 409
    assert app["events"].types() == []


async def test_the_daemon_spawns_the_script_with_the_lock_already_claimed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Without --lock-held the script would find the daemon's lock and refuse itself."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        server.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command) or SimpleNamespace(pid=7),
    )
    response = await server.daemon_redeploy(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert response.status == 202
    assert "--lock-held" in captured["command"]


async def test_announce_broadcasts_a_cli_started_redeploy(tmp_path: Path) -> None:
    """A redeploy run straight from a terminal reaches the UI the same way."""
    app = _app(tmp_path)
    (tmp_path / "redeploy.lock").write_text(str(os.getpid()), encoding="ascii")
    response = await server.daemon_redeploy_announce(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 202
    assert app["events"].types() == ["daemon_redeploy_started"]


async def test_announce_refuses_when_no_redeploy_is_running(tmp_path: Path) -> None:
    """It describes a real redeploy; it is not a way to fake a maintenance mode."""
    app = _app(tmp_path)
    response = await server.daemon_redeploy_announce(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 409
    assert _payload(response)["error"] == "no_redeploy_in_flight"
    assert app["events"].types() == []
    # A lock naming a dead process is not a redeploy either.
    (tmp_path / "redeploy.lock").write_text("999999999", encoding="ascii")
    response = await server.daemon_redeploy_announce(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 409


async def test_announce_is_loopback_only(tmp_path: Path) -> None:
    app = _app(tmp_path)
    (tmp_path / "redeploy.lock").write_text(str(os.getpid()), encoding="ascii")
    with pytest.raises(server.web.HTTPForbidden):
        await server.daemon_redeploy_announce(  # type: ignore[arg-type]
            FakeRequest(app, remote="10.0.0.4")
        )
    assert app["events"].types() == []


async def test_stopping_for_a_redeploy_is_broadcast_before_the_daemon_goes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The one authoritative 'the outage starts now'.

    The script stops the daemon through this endpoint, so the daemon is alive and
    still has its sockets at the moment it learns the build finished. Inferring
    the same thing from a dropped socket is indistinguishable from a blip.
    """
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(server.asyncio, "sleep", fake_sleep)
    (tmp_path / "redeploy.lock").write_text(str(os.getpid()), encoding="ascii")
    shutdown = asyncio.Event()
    app = _app(tmp_path)
    app.update(
        desktop_control_token="secret",
        desktop_shutdown_event=shutdown,
        shutdown_state={},
    )
    request = FakeRequest(app, {"mode": "restart"})
    request.headers["Authorization"] = "Bearer secret"
    response = await server.desktop_shutdown(request)  # type: ignore[arg-type]
    assert response.status == 202
    assert app["events"].types() == ["daemon_redeploy_stopping"]
    # The frame the client most needs is the one the shutdown would otherwise
    # close the socket before writing.
    assert slept == [server.REDEPLOY_STOPPING_DRAIN_SECONDS]
    assert shutdown.is_set()


async def test_an_ordinary_desktop_quit_broadcasts_no_redeploy(tmp_path: Path) -> None:
    """No redeploy in flight means this is just a quit, not an outage to wait out."""
    shutdown = asyncio.Event()
    app = _app(tmp_path)
    app.update(
        desktop_control_token="secret",
        desktop_shutdown_event=shutdown,
        shutdown_state={},
    )
    request = FakeRequest(app, {"mode": "quit"})
    request.headers["Authorization"] = "Bearer secret"
    response = await server.desktop_shutdown(request)  # type: ignore[arg-type]
    assert response.status == 202
    assert app["events"].types() == []


async def test_a_shutdown_app_without_config_or_events_still_shuts_down() -> None:
    """The broadcast is a courtesy and must never break the shutdown itself.

    A minimal desktop-control app carries neither key, and looking them up
    directly turned every quit on such an app into a 500 instead of a shutdown.
    """
    shutdown = asyncio.Event()
    request = FakeRequest(
        {
            "desktop_control_token": "secret",
            "desktop_shutdown_event": shutdown,
            "shutdown_state": {},
        }
    )
    request.headers["Authorization"] = "Bearer secret"
    response = await server.desktop_shutdown(request)  # type: ignore[arg-type]
    assert response.status == 202
    assert shutdown.is_set()


async def test_status_reports_the_phase_and_the_last_outcome(tmp_path: Path) -> None:
    """A rollback is the outcome that otherwise looks exactly like success."""
    (tmp_path / "redeploy-result.json").write_text(
        json.dumps({"outcome": "rolled_back", "detail": "Your change did NOT ship."}),
        encoding="utf-8",
    )
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    body = _payload(response)
    assert body["phase"] == "idle"
    assert body["last_result"]["outcome"] == "rolled_back"

    (tmp_path / "redeploy.lock").write_text(str(os.getpid()), encoding="ascii")
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    # Answering at all means the daemon is up, so a live lock is always the build.
    assert _payload(response)["phase"] == "building"


async def test_status_survives_an_unreadable_outcome_file(tmp_path: Path) -> None:
    (tmp_path / "redeploy-result.json").write_text("{not json", encoding="utf-8")
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert _payload(response)["last_result"] is None


def test_a_terminal_launched_redeploy_claims_the_same_lock(tmp_path: Path) -> None:
    """It used to take no lock at all.

    Two concurrent CLI redeploys could race the same staging tree and the swap,
    and `GET /api/daemon/redeploy` reported nothing in flight - so the UI had no
    way to know it should stop trusting the daemon.
    """
    module = _redeploy_module()
    config = SimpleNamespace(data_dir=tmp_path)
    assert module.claim_lock(config, already_held=False) is True
    assert (tmp_path / "redeploy.lock").read_text(encoding="ascii") == str(os.getpid())
    # The daemon-spawned path was handed a lock that already names it.
    (tmp_path / "redeploy.lock").write_text("12345", encoding="ascii")
    assert module.claim_lock(config, already_held=True) is True
    assert (tmp_path / "redeploy.lock").read_text(encoding="ascii") == "12345"


def test_a_second_redeploy_is_refused_while_one_is_live(tmp_path: Path) -> None:
    module = _redeploy_module()
    config = SimpleNamespace(data_dir=tmp_path)
    # A live pid that is not ours: another redeploy owns the run.
    lock = tmp_path / "redeploy.lock"
    lock.write_text(str(_a_live_foreign_pid()), encoding="ascii")
    assert module.claim_lock(config, already_held=False) is False
    # A stale lock (dead pid) is taken over rather than blocking forever.
    lock.write_text("999999999", encoding="ascii")
    assert module.claim_lock(config, already_held=False) is True
    assert lock.read_text(encoding="ascii") == str(os.getpid())


def _a_live_foreign_pid() -> int:
    """A pid that exists and is not this process (the OS's own, pid 4 on Windows)."""
    import psutil

    for pid in psutil.pids():
        if pid != os.getpid() and pid > 0:
            return pid
    raise AssertionError("no other live process")


def test_the_outcome_file_records_a_rollback_distinctly_from_a_plain_failure(
    tmp_path: Path,
) -> None:
    """Both exit 1, and only one of them means "your change did not ship"."""
    module = _redeploy_module()
    config = SimpleNamespace(data_dir=tmp_path)
    (tmp_path / "redeploy.log").write_text("[redeploy] rolling back\n", encoding="utf-8")

    outcome = module.Outcome(config, 1000.0)
    outcome.record(module.OUTCOME_ROLLED_BACK, "Your change did NOT ship.", code=1)
    # Written at the moment of decision, not on the way out: a rollback relaunches
    # the old app immediately afterwards, and the browser asks for this file as
    # soon as any daemon answers health.
    payload = json.loads((tmp_path / "redeploy-result.json").read_text(encoding="utf-8"))
    assert payload["outcome"] == "rolled_back"
    assert payload["exit_code"] == 1
    assert payload["started_at"] == 1000.0
    assert payload["finished_at"] >= payload["started_at"]
    assert payload["log_tail"] == ["[redeploy] rolling back"]
    # No leftover temp file to be mistaken for the record.
    assert not (tmp_path / "redeploy-result.json.tmp").exists()
    # A recorded outcome is not overwritten by the exit-code backstop.
    assert outcome.finish(1) == 1
    payload = json.loads((tmp_path / "redeploy-result.json").read_text(encoding="utf-8"))
    assert payload["outcome"] == "rolled_back"


def test_an_unclassified_exit_still_records_something(tmp_path: Path) -> None:
    """A new early return can never leave the previous run's result standing."""
    module = _redeploy_module()
    config = SimpleNamespace(data_dir=tmp_path)
    for code, expected in ((0, "succeeded"), (1, "failed"), (2, "refused")):
        assert module.Outcome(config, 1.0).finish(code) == code
        payload = json.loads((tmp_path / "redeploy-result.json").read_text(encoding="utf-8"))
        assert payload["outcome"] == expected
        assert payload["detail"]


def test_the_lock_held_flag_is_understood(tmp_path: Path) -> None:
    module = _redeploy_module()
    assert module.parse_args(["--restore-visibility", "--lock-held"]).lock_held is True
    assert module.parse_args([]).lock_held is False


def test_a_refused_claim_leaves_the_running_redeploys_result_alone(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Recording "refused" here would misreport the redeploy that owns the lock."""
    module = _redeploy_module()
    result = tmp_path / "redeploy-result.json"
    result.write_text(json.dumps({"outcome": "succeeded"}), encoding="utf-8")
    (tmp_path / "redeploy.lock").write_text(str(_a_live_foreign_pid()), encoding="ascii")
    monkeypatch.setattr(module, "load_config", lambda _path: SimpleNamespace(data_dir=tmp_path))
    assert module.main([]) == 2
    assert json.loads(result.read_text(encoding="utf-8"))["outcome"] == "succeeded"


def test_only_a_terminal_launched_run_announces_itself(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The daemon already broadcast the start for the runs it spawned."""
    module = _redeploy_module()
    announced: list[int] = []
    monkeypatch.setattr(module, "load_config", lambda _path: SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(module, "announce_start", lambda _config: announced.append(1))
    monkeypatch.setattr(module, "_run", lambda *_args, **_kwargs: 0)

    assert module.main(["--lock-held"]) == 0
    assert announced == []
    # --lock-held also means "do not claim it", so there is nothing to clear here.
    assert not (tmp_path / "redeploy.lock").exists()
    assert module.main([]) == 0
    assert announced == [1]


def test_an_unreachable_daemon_does_not_stop_the_redeploy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The announcement only buys the UI a progress chip; it is never load-bearing."""
    module = _redeploy_module()

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(module.urllib.request, "urlopen", refuse)
    module.announce_start(SimpleNamespace(data_dir=tmp_path, port=1))


async def test_a_stale_log_is_not_served_as_the_running_redeploys_progress(
    tmp_path: Path,
) -> None:
    """Observed live: a CLI redeploy's chip would show an earlier run's build log.

    Only a redeploy this daemon spawned writes redeploy.log; one launched from a
    terminal prints to its own stdout. Serving the leftover file regardless shows
    a previous run's output as this run's progress, which reads as real and is not.
    """
    log = tmp_path / "redeploy.log"
    log.write_text("[redeploy] daemon healthy: live_sessions=2\n", encoding="utf-8")
    lock = tmp_path / "redeploy.lock"
    lock.write_text(str(os.getpid()), encoding="ascii")
    # Lock claimed after the log was written: the log is a previous run's.
    os.utime(log, (1_000_000, 1_000_000))
    os.utime(lock, (2_000_000, 2_000_000))

    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    body = _payload(response)
    assert body["running"] is True
    assert body["log_tail"] == []

    # A log written during this run is served normally.
    os.utime(log, (3_000_000, 3_000_000))
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    assert _payload(response)["log_tail"] == ["[redeploy] daemon healthy: live_sessions=2"]


async def test_the_last_runs_log_is_still_readable_when_nothing_is_running(
    tmp_path: Path,
) -> None:
    """With no redeploy in flight the file is the last run's, which is what to show."""
    (tmp_path / "redeploy.log").write_text("[redeploy] ABORT: build failed\n", encoding="utf-8")
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    body = _payload(response)
    assert body["running"] is False
    assert body["log_tail"] == ["[redeploy] ABORT: build failed"]


def test_an_outcome_never_carries_an_earlier_runs_log(tmp_path: Path) -> None:
    """The bug this caught live: detail said 11 sessions, the tail said 2."""
    module = _redeploy_module()
    config = SimpleNamespace(data_dir=tmp_path)
    log = tmp_path / "redeploy.log"
    log.write_text("[redeploy] daemon healthy: live_sessions=2\n", encoding="utf-8")
    os.utime(log, (1_000_000, 1_000_000))

    # started_at after the log's mtime: a terminal-launched run, whose own output
    # went to stdout and never touched this file.
    module.Outcome(config, 2_000_000.0).record(
        module.OUTCOME_SUCCEEDED, "The rebuilt app is running with 11 live session(s).", code=0
    )
    payload = json.loads((tmp_path / "redeploy-result.json").read_text(encoding="utf-8"))
    assert payload["log_tail"] == []
    assert "11 live session" in payload["detail"]

    # A run that did write the log keeps its tail.
    module.Outcome(config, 500_000.0).record(module.OUTCOME_SUCCEEDED, "fine", code=0)
    payload = json.loads((tmp_path / "redeploy-result.json").read_text(encoding="utf-8"))
    assert payload["log_tail"] == ["[redeploy] daemon healthy: live_sessions=2"]


# --- what a redeploy interrupts, reported and never enforced ----------------


class FakePreviews:
    """Just enough PreviewRegistry surface for the interruption report."""

    def __init__(self, *items: Any) -> None:
        self.items = {item.id: item for item in items}


def _preview(preview_id: str, port: int, *, listed: bool = True) -> Any:
    return SimpleNamespace(
        id=preview_id,
        url=f"http://127.0.0.1:{port}/",
        host="127.0.0.1",
        port=port,
        source="detected",
        project_id="default",
        session_id="session-a",
        listed=listed,
    )


async def test_accepting_a_redeploy_reports_what_goes_dark(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An agent gets the consequences in the same reply that accepts the redeploy.

    Without this it discovers them minutes later as a dead proxy, which reads as
    "the redeploy broke my server" when the server never stopped.
    """
    monkeypatch.setattr(
        server.subprocess, "Popen", lambda command, **kwargs: SimpleNamespace(pid=9)
    )
    app = _app(tmp_path)
    app["previews"] = FakePreviews(_preview("aaa", 5173), _preview("bbb", 8080))
    response = await server.daemon_redeploy(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 202
    interrupted = _payload(response)["interrupted"]
    assert [entry["port"] for entry in interrupted["previews"]] == [5173, 8080]
    # The proxy path is the thing that actually stops answering, and it is stable
    # across the restart, so the same URL works again afterwards.
    assert interrupted["previews"][0]["proxy_path"] == "/preview/aaa/"
    # Nothing is killed, and the reply says so in as many words.
    assert interrupted["kills_processes"] is False
    assert "keep running" in interrupted["note"]


async def test_an_open_port_never_refuses_a_redeploy(tmp_path: Path, monkeypatch: Any) -> None:
    """Reported, not enforced.

    Refusing here would make redeploy nearly un-runnable - there is almost always
    a dev server up - and it is the only mechanism that ships anything, including
    a fix for a gate that refuses wrongly.
    """
    monkeypatch.setattr(
        server.subprocess, "Popen", lambda command, **kwargs: SimpleNamespace(pid=9)
    )
    app = _app(tmp_path)
    app["previews"] = FakePreviews(*(_preview(f"p{index}", 3000 + index) for index in range(12)))
    response = await server.daemon_redeploy(FakeRequest(app))  # type: ignore[arg-type]
    assert response.status == 202


async def test_unlisted_previews_are_not_reported(tmp_path: Path) -> None:
    """`listed` is what belongs in navigation; the rest is routing plumbing."""
    app = _app(tmp_path)
    app["previews"] = FakePreviews(_preview("aaa", 5173), _preview("bbb", 8080, listed=False))
    response = await server.daemon_redeploy_status(FakeRequest(app))  # type: ignore[arg-type]
    ports = [entry["port"] for entry in _payload(response)["interrupted"]["previews"]]
    assert ports == [5173]


async def test_the_status_reports_interruptions_before_any_redeploy_starts(
    tmp_path: Path,
) -> None:
    """The confirm dialog reads this, which is the only moment it can change a decision."""
    app = _app(tmp_path)
    app["previews"] = FakePreviews(_preview("aaa", 5173))
    response = await server.daemon_redeploy_status(FakeRequest(app))  # type: ignore[arg-type]
    body = _payload(response)
    assert body["running"] is False
    assert len(body["interrupted"]["previews"]) == 1


async def test_a_daemon_with_no_preview_registry_still_answers(tmp_path: Path) -> None:
    response = await server.daemon_redeploy_status(FakeRequest(_app(tmp_path)))  # type: ignore[arg-type]
    interrupted = _payload(response)["interrupted"]
    assert interrupted["previews"] == []
    assert interrupted["kills_processes"] is False
