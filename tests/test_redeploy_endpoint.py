"""Guards and spawn contract of the frozen-app redeploy endpoint."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import server


class FakeRequest:
    def __init__(self, app: dict[str, Any], body: Any = None) -> None:
        self.app = app
        self._body = body

    async def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(response.body)


def _app(tmp_path: Path, *, supervisor_connected: bool = True) -> dict[str, Any]:
    return {
        "config": SimpleNamespace(data_dir=tmp_path),
        "supervisor": SimpleNamespace(connected=supervisor_connected),
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
    assert "--hidden" in command
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
        "health",
        lambda *_args, **_kwargs: health_calls.append(object()) or None,
    )
    process = SimpleNamespace(poll=lambda: 7, returncode=7)

    assert module.APP_HEALTH_TIMEOUT_SECONDS >= 300
    assert module.wait_healthy(SimpleNamespace(), process=process) is None
    assert len(health_calls) == 1


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


def test_stale_trunk_detection_lists_commits_the_checkout_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build reads the working tree, so trunk commits it lacks would ship missing.

    This exists because it already happened once: an agent landed four fixes, the
    fast-forward of master was blocked by an unrelated dirty file, and the redeploy
    built master and shipped none of them.
    """
    module = _redeploy_module()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t.t")
    git("config", "user.name", "T")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "integration")
    monkeypatch.setattr(module, "ROOT", tmp_path)

    # Trunk level with HEAD: nothing missing.
    assert module.unmerged_trunk_commits() == []

    # Advance the trunk without touching HEAD, exactly what a land does.
    git("worktree", "add", "-q", str(tmp_path / "wt"), "integration")
    wt = tmp_path / "wt"
    (wt / "b.txt").write_text("b", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-qm", "landed fix"], cwd=wt, check=True, capture_output=True
    )

    stale = module.unmerged_trunk_commits()
    assert len(stale) == 1
    assert "landed fix" in stale[0]


def test_stale_trunk_detection_is_silent_without_a_trunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Packaging must not start depending on git, or on this branch existing.
    module = _redeploy_module()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True
    )
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module.unmerged_trunk_commits() == []


def test_stale_trunk_detection_is_silent_outside_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _redeploy_module()
    monkeypatch.setattr(module, "ROOT", tmp_path / "not-a-repo")
    assert module.unmerged_trunk_commits() == []
