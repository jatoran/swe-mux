"""`swemux start`: a detached daemon, and "started" meaning "answered"."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from swe_mux import daemon_start
from swe_mux.config import Config


def _config(tmp_path: Path) -> Config:
    config = Config(data_dir=tmp_path)
    config.config_path = tmp_path / "config.toml"
    return config


class _Child:
    """A spawned process that is alive until told otherwise."""

    def __init__(self, pid: int = 4242, exit_code: int | None = None) -> None:
        self.pid = pid
        self._code = exit_code

    def poll(self) -> int | None:
        return self._code


def test_an_already_serving_daemon_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Idempotence is what makes this safe in a login script or typed twice.

    The port is the interlock - two daemons cannot both hold it - so checking
    health first is checking the thing that actually decides the outcome.
    """
    monkeypatch.setattr(daemon_start, "health", lambda *a, **k: {"ok": True})

    def _never(*args: object, **kwargs: object) -> object:
        raise AssertionError("a serving daemon must not be respawned")

    monkeypatch.setattr(daemon_start, "popen_outside_job", _never)
    outcome = daemon_start.start_daemon(_config(tmp_path), url="http://127.0.0.1:8765")
    assert outcome.status == "already-running"
    assert outcome.ok is True
    assert outcome.pid is None


def test_a_daemon_that_answers_is_reported_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Success is an answered health check, never a returned pid.

    A daemon that dies during startup has a pid too, so reporting the spawn
    would call the most common real failure a success.
    """
    answers = iter([None, None, {"ok": True}])
    monkeypatch.setattr(daemon_start, "health", lambda *a, **k: next(answers, {"ok": True}))
    monkeypatch.setattr(
        daemon_start, "popen_outside_job", lambda *a, **k: _Child(pid=99)
    )
    monkeypatch.setattr(daemon_start.time, "sleep", lambda _s: None)
    outcome = daemon_start.start_daemon(_config(tmp_path), url="http://127.0.0.1:8765")
    assert outcome.status == "started"
    assert outcome.pid == 99
    assert outcome.ok is True
    assert "daemon-start.log" in outcome.log_path


def test_a_child_that_exits_is_a_failure_reported_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one unambiguous failure, and it must not wait out the timeout.

    Distinguishing it from a slow start is the whole reason this polls the child
    beside the port instead of only sleeping on health.
    """
    monkeypatch.setattr(daemon_start, "health", lambda *a, **k: None)
    monkeypatch.setattr(
        daemon_start, "popen_outside_job", lambda *a, **k: _Child(pid=7, exit_code=3)
    )
    monkeypatch.setattr(daemon_start.time, "sleep", lambda _s: None)
    outcome = daemon_start.start_daemon(
        _config(tmp_path), url="http://127.0.0.1:8765", timeout_seconds=600
    )
    assert outcome.status == "failed"
    assert outcome.ok is False
    assert "code 3" in outcome.detail


def test_a_slow_start_is_reported_as_starting_rather_than_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live daemon that has not finished is not a failure.

    A cold page cache after an update, a large `mux.db`, a supervisor with
    sessions to reattach: all of these produce a daemon that is working and slow.
    Exiting non-zero there would make a script flap on machine state rather than
    on whether swe-mux can run.
    """
    monkeypatch.setattr(daemon_start, "health", lambda *a, **k: None)
    monkeypatch.setattr(
        daemon_start, "popen_outside_job", lambda *a, **k: _Child(pid=11)
    )
    monkeypatch.setattr(daemon_start.time, "sleep", lambda _s: None)
    outcome = daemon_start.start_daemon(
        _config(tmp_path), url="http://127.0.0.1:8765", timeout_seconds=0.01
    )
    assert outcome.status == "starting"
    assert outcome.ok is True
    assert outcome.pid == 11
    assert "still running" in outcome.detail


def test_the_child_is_detached_from_this_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the command is named for, asserted on the spawn arguments.

    POSIX: `start_new_session` runs `setsid`, so no terminal hangup reaches it.
    Windows: `DETACHED_PROCESS` keeps it off this console, so a Ctrl-C in the
    shell that started it does not reach the daemon.
    """
    seen: dict[str, Any] = {}

    def _spawn(command: list[str], **kwargs: Any) -> _Child:
        seen["command"] = command
        seen.update(kwargs)
        return _Child()

    monkeypatch.setattr(daemon_start, "health", lambda *a, **k: {"ok": True} if seen else None)
    monkeypatch.setattr(daemon_start, "popen_outside_job", _spawn)
    monkeypatch.setattr(daemon_start.time, "sleep", lambda _s: None)
    daemon_start.start_daemon(_config(tmp_path), url="http://127.0.0.1:8765")

    assert seen["stdin"] is subprocess.DEVNULL
    # cwd is the data dir, never the installation: a long-lived process anchored
    # in `dist/` locks that tree against an in-place update.
    assert seen["cwd"] == str(tmp_path)
    if os.name == "nt":
        assert seen["start_new_session"] is False
        assert seen["creationflags"] & 0x00000008  # DETACHED_PROCESS
        assert seen["creationflags"] & 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        assert seen["start_new_session"] is True
        assert seen["creationflags"] == 0


def test_creation_flags_are_zero_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserted from any host, so the Windows branch is not the only one exercised."""
    monkeypatch.setattr(daemon_start.os, "name", "posix")
    assert daemon_start.creation_flags() == 0


def test_the_daemon_command_matches_how_this_copy_actually_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen build has no interpreter to hand `-m swe_mux` to.

    Same split as `desktop.daemon_command`, and the failure it avoids is a
    frozen app spawning `swe-mux.exe -m swe_mux`, which is not a thing.
    """
    config_path = tmp_path / "config.toml"
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert daemon_start.daemon_command(config_path, executable="python") == [
        "python",
        "-m",
        "swe_mux",
        "--config",
        str(config_path),
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert daemon_start.daemon_command(config_path, executable="swe-mux.exe") == [
        "swe-mux.exe",
        "--daemon-child",
        "--config",
        str(config_path),
    ]


def test_health_treats_an_unreachable_or_junk_daemon_as_absent() -> None:
    """Anything that is not a JSON object on 200 means "nothing is serving here".

    A port held by some other program is the case this really guards: it answers,
    and treating that as a live daemon would report success and then hand the
    user a browser tab full of somebody else's application.
    """
    assert daemon_start.health("http://127.0.0.1:1", timeout=0.05) is None


def test_a_browser_opens_only_when_a_terminal_is_watching() -> None:
    """The gate is "is a person looking at this process", not "is this first run".

    Every way swe-mux starts a daemon without a person in front of it is non-TTY
    - the tray's child writes to a log file and shows its own window, `swemux
    start`'s detached child writes to `daemon-start.log`, a restart successor is
    nobody's foreground, and a login task has no console. One rule covers all
    four, and none of them has to know this exists.
    """
    assert daemon_start.should_open_browser(
        requested=True, stdout_isatty=True, environ={}
    )
    assert not daemon_start.should_open_browser(
        requested=True, stdout_isatty=False, environ={}
    )
    assert not daemon_start.should_open_browser(
        requested=False, stdout_isatty=True, environ={}
    )


def test_the_environment_override_works_where_a_flag_cannot_reach() -> None:
    """A service wrapper or somebody else's script gets a way to say no.

    Whitespace counts as unset so an empty variable exported by a shell profile
    does not silently disable a feature the user never turned off.
    """
    assert not daemon_start.should_open_browser(
        requested=True, stdout_isatty=True, environ={daemon_start.NO_BROWSER_ENV: "1"}
    )
    assert daemon_start.should_open_browser(
        requested=True, stdout_isatty=True, environ={daemon_start.NO_BROWSER_ENV: "  "}
    )


def test_opening_a_browser_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine with no browser must still get a daemon.

    `webbrowser.open` raises on some headless configurations rather than
    returning False, and this runs on the daemon's own startup path.
    """
    import webbrowser

    def _explode(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("no browser here")

    monkeypatch.setattr(webbrowser, "open", _explode)
    assert daemon_start.open_browser("http://127.0.0.1:8765") is False


def test_the_banner_answers_what_a_new_user_cannot_look_up() -> None:
    """The only moment swe-mux gets, so it carries the non-obvious facts.

    Nothing runs after `uv tool install` prints its executable list, so this is
    where "where is the UI" and "how do I stop it" have to be answered. The stop
    line matters most: Ctrl-C detaches and leaves supervised sessions running,
    which is the opposite of what the key normally means.
    """
    banner = daemon_start.startup_banner(
        "http://127.0.0.1:8765", windows=True, opened_browser=False
    )
    assert "http://127.0.0.1:8765" in banner
    assert "swemuxd --shutdown" in banner
    assert "Ctrl-C only detaches" in banner
    assert "swemux doctor" in banner
    # The desktop shell exists only on Windows, so naming it elsewhere would be
    # advice about a command that install cannot have written.
    assert "with a tray icon" in banner
    assert "with a tray icon" not in daemon_start.startup_banner(
        "http://127.0.0.1:8765", windows=False, opened_browser=False
    )


def test_the_banner_says_when_it_opened_a_browser() -> None:
    """A window appearing unexplained reads as something else having happened."""
    assert "opening it in your browser" in daemon_start.startup_banner(
        "http://127.0.0.1:8765", windows=True, opened_browser=True
    )
    assert "opening it in your browser" not in daemon_start.startup_banner(
        "http://127.0.0.1:8765", windows=True, opened_browser=False
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("already-running", "already serving"),
        ("started", "detached"),
        ("starting", "still coming up"),
        ("failed", "did not start"),
    ],
)
def test_every_outcome_renders_something_a_person_can_act_on(
    status: str, expected: str
) -> None:
    outcome = daemon_start.StartOutcome(
        status=status,
        url="http://127.0.0.1:8765",
        pid=5,
        detail="because of a reason",
        log_path="C:/x/daemon-start.log",
    )
    assert expected in daemon_start.render(outcome)
