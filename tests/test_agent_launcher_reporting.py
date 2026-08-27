"""What the mux agent shim tells the daemon about its own launch, and what it withholds.

Before this existed the shim emitted nothing at all, which is why the 2026-08-27
console-contention incident had to be diagnosed from a live process walk: nothing
recorded that the wrapper had started, what it resolved, which child it spawned, or
how it died (`console_contention.py`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from swe_mux import agent_launcher
from swe_mux.spawn_contract import AGENT_FORCE_COLOR_ENV


class _FakeChild:
    def __init__(self, pid: int = 9911, code: int = 0) -> None:
        self.pid = pid
        self._code = code

    def wait(self) -> int:
        return self._code


@pytest.fixture(autouse=True)
def _fresh_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean per-test trace; the module keeps one instance per process."""
    monkeypatch.setattr(agent_launcher, "TRACE", agent_launcher._ShimTrace())


def _capture_reports(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []

    def _notify(url_name: str, payload: dict[str, Any], *, timeout: float = 3.0) -> None:
        if url_name == "MUX_SHIM_URL":
            reports.append(payload)

    monkeypatch.setattr(agent_launcher, "_notify_lifecycle", _notify)
    return reports


# --- what leaves the machine ------------------------------------------------


def test_argv_shape_keeps_flags_and_drops_every_value() -> None:
    """An agent command line can carry a prompt, so no value is ever reported."""
    shape = agent_launcher.argv_shape(
        [
            "--session-id",
            "b1e6a0d2-0000-4000-8000-000000000000",
            "--settings",
            r"C:\Users\someone\.mux\sessions\x.json",
            "-p",
            "summarise the incident report and email it to legal",
        ]
    )
    assert shape == ["--session-id", "--settings", "-p"]
    assert not any("legal" in item for item in shape)
    assert not any("someone" in item for item in shape)


def test_a_report_carries_the_backend_and_the_shim_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _capture_reports(monkeypatch)
    agent_launcher.TRACE.backend = "claude"
    agent_launcher.TRACE.report("started", executable="claude.exe")
    assert reports[0]["kind"] == "started"
    assert reports[0]["backend"] == "claude"
    assert reports[0]["shim_pid"] == os.getpid()
    assert isinstance(reports[0]["elapsed_ms"], float)


# --- the exit report, which is the one that matters -------------------------


def test_exit_is_reported_exactly_once_whichever_path_gets_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`atexit`, the console handler, and the normal return all call this.

    Idempotence is not tidiness: the interesting exits are the ones that never run
    a `finally`, so all three paths are armed and any of them may be first.
    """
    reports = _capture_reports(monkeypatch)
    agent_launcher.TRACE.report_exit(0, path="normal")
    agent_launcher.TRACE.report_exit(0, path="atexit")
    agent_launcher.TRACE.report_exit(None, path="console_close")
    assert [item["kind"] for item in reports] == ["exited"]
    assert reports[0]["exit_path"] == "normal"


def test_a_child_that_outlives_the_shim_is_reported_as_such(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect, proven from the wrapper's own side.

    The daemon treats this as contention immediately, without waiting for a shell
    prompt to arrive and be classified.
    """
    reports = _capture_reports(monkeypatch)
    agent_launcher.TRACE.child_pid = os.getpid()  # trivially still alive
    agent_launcher.TRACE.report_exit(0)
    assert reports[0]["child_outlived_shim"] is True
    assert reports[0]["child_pid"] == os.getpid()


def test_an_unknown_child_reports_none_rather_than_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = _capture_reports(monkeypatch)
    agent_launcher.TRACE.report_exit(1)
    assert reports[0]["child_outlived_shim"] is None
    assert reports[0]["child_pid"] is None


def test_pid_liveness_answers_for_a_dead_process() -> None:
    import subprocess

    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603
    dead.wait(timeout=10)
    # A recycled pid could revive this, so only the live direction is asserted
    # unconditionally; the dead direction is asserted when the pid stayed free.
    assert agent_launcher._pid_alive(os.getpid()) is True
    assert agent_launcher._pid_alive(0) is None
    assert agent_launcher._pid_alive(-1) is None


# --- the environment the CLI is actually launched with ----------------------


def test_the_child_gets_forced_colour_a_shell_session_never_had(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shell's env deliberately omits this, and promotion cannot repair it.

    `spawn_contract.session_terminal_env` gives `FORCE_COLOR`/`CLICOLOR_FORCE` to
    agent sessions only, because a shell must keep pipe semantics. That env is
    fixed when the shell spawns, so an agent typed into one would otherwise render
    monochrome while the same agent from the Run menu does not. The shim is the
    only place that knows an agent is being launched into a shell's environment.
    """
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)
    env = agent_launcher.child_environment({"PATH": "/usr/bin"})
    for key, value in AGENT_FORCE_COLOR_ENV.items():
        assert env[key] == value
    assert env["PATH"] == "/usr/bin"


def test_launching_reports_the_child_pid_before_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pid has to be published while the child runs, not after it exits.

    It is what turns "a shell prompt appeared under a promoted pane" from a
    transcript guess into a liveness measurement.
    """
    reports = _capture_reports(monkeypatch)
    spawned: list[Any] = []

    def _popen(command: list[str], **kwargs: Any) -> _FakeChild:
        spawned.append((command, kwargs))
        return _FakeChild(pid=7777)

    monkeypatch.setattr(agent_launcher.subprocess, "Popen", _popen)
    monkeypatch.setattr(agent_launcher.shutil, "which", lambda command, path=None: command)

    assert agent_launcher._launch("someagent", ["--version"]) == 0
    assert [item["kind"] for item in reports] == ["child_started"]
    assert reports[0]["child_pid"] == 7777
    # And the env it was spawned with is the one `child_environment` builds.
    _, kwargs = spawned[0]
    assert kwargs["env"]["FORCE_COLOR"] == AGENT_FORCE_COLOR_ENV["FORCE_COLOR"]


# --- stdio, whose two "empty" answers mean different things -----------------


def test_unfrozen_stdio_is_named_rather_than_left_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    kwargs, mode = agent_launcher._child_stdio()
    assert kwargs == {}
    assert mode == "inherit_unfrozen"


def test_a_frozen_build_with_no_streams_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The windowed PyInstaller build's actual configuration.

    `sys.stdout is None` there, so the frozen path takes the inherit fallback on
    exactly the build where "did the console survive" is the first question of any
    launch incident. Both cases produce `{}`; only the mode tells them apart.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    kwargs, mode = agent_launcher._child_stdio()
    assert kwargs == {}
    assert mode == "inherit_no_streams"


# --- the whole main() path --------------------------------------------------


def test_main_reports_started_child_and_exit_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reports = _capture_reports(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["launcher", "claude"])
    monkeypatch.setattr(
        agent_launcher, "_claude", lambda args: ("claude.exe", ["--session-id", "abc"], "abc")
    )
    monkeypatch.setattr(agent_launcher, "_promote", lambda backend, native_id: None)
    monkeypatch.setattr(agent_launcher, "_demote", lambda backend, native_id: None)
    monkeypatch.setattr(agent_launcher.shutil, "which", lambda command, path=None: command)
    monkeypatch.setattr(
        agent_launcher.subprocess, "Popen", lambda command, **kwargs: _FakeChild(code=3)
    )

    with pytest.raises(SystemExit, match="3"):
        agent_launcher.main()

    assert [item["kind"] for item in reports] == ["started", "child_started", "exited"]
    started = reports[0]
    # The shape of the argv, never its values.
    assert started["argv_flags"] == ["--session-id"]
    assert started["argv_count"] == 2
    assert started["native_id_assigned"] is True
    assert "abc" not in json.dumps(started)
    assert reports[-1]["exit_code"] == 3


@pytest.mark.skipif(os.name != "nt", reason="console control handlers are Windows-only")
def test_holding_console_signals_installs_a_handler_that_swallows_ctrl_c() -> None:
    """Armed before the first byte reaches the CLI, and kept alive for the process.

    A `ctypes` callback garbage-collected while Windows still holds the pointer is
    a crash rather than a no-op, which is why the module keeps a reference.
    """
    import ctypes

    agent_launcher._hold_console_signals()
    handler = agent_launcher._CONSOLE_HANDLER
    assert handler is not None
    try:
        # CTRL_C and CTRL_BREAK are handled (swallowed); everything else is passed
        # on to the default handler, because those are real terminations with a
        # deadline and swallowing one only makes the process die less gracefully.
        assert handler(agent_launcher._CTRL_C_EVENT)
        assert handler(agent_launcher._CTRL_BREAK_EVENT)
        assert not handler(agent_launcher._CTRL_CLOSE_EVENT)
    finally:
        # Uninstall. Left armed, this test would swallow Ctrl+C for the rest of the
        # pytest process, including a human or a CI runner trying to cancel it.
        ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, False)
        agent_launcher._CONSOLE_HANDLER = None
