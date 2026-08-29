"""`swe-mux` is a GUI script, and what that costs is paid for here.

`[project.gui-scripts]` builds the `pythonw`-style launcher, so the desktop shell
no longer pops and holds a console window beside the native window that is its
whole point. The price is that Python hands such a process `sys.stdout is None`
and `sys.stderr is None`: a failure before the window exists has nowhere to go,
and - worse than being discarded - `argparse` and any other reporter that writes
to `sys.stderr` unconditionally dies on `AttributeError: 'NoneType' object has no
attribute 'write'` *inside the code that was reporting the original fault*.

Invisible is worse than ugly, so the entry-point move is only correct while the
three sinks below exist. These tests hold the two together: the last one reads
`pyproject.toml` and fails if `swe-mux` is moved back to `[project.scripts]`, and
the rest fail if the compensating machinery stops working.

None of it needs Windows. `show_error` is stubbed rather than allowed to raise a
message box, which would block the suite.
"""

from __future__ import annotations

import contextlib
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from swe_mux import desktop


@pytest.fixture(autouse=True)
def _quiet_message_box(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture what the user would have been shown instead of showing it."""
    shown: list[str] = []
    monkeypatch.setattr(desktop, "show_error", shown.append)
    return shown


@contextlib.contextmanager
def no_console(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make this process look like one started by the GUI launcher.

    A context manager rather than a fixture, and that is not a style choice:
    pytest's capture manager re-installs its own `sys.stdout` when the call
    phase begins, so a fixture that nulled the streams during setup would be
    silently undone before the first assertion and every test here would pass
    against the ordinary console path instead of the one under test.

    `monkeypatch` still owns the assignment, so the real streams come back at
    teardown even if the body raises; the window in which nothing may print is
    only as long as the `with`.
    """
    monkeypatch.setattr(desktop, "_gui_log", None)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        yield
    finally:
        _close_gui_log()


def _close_gui_log() -> None:
    """Close the handle the module opened, so no finalizer reddens a neighbour.

    A file left open until the collector runs is reported against whatever test
    happens to be running then, which is the failure shape that is worse than
    the bug because it names the wrong test.
    """
    handle = desktop._gui_log
    if handle is not None:
        handle.close()
    desktop._gui_log = None


# --------------------------------------------------------------------------- #
# Detecting the console-less launch
# --------------------------------------------------------------------------- #


def test_a_process_with_streams_is_left_alone(tmp_path: Path) -> None:
    """Every source run, every test, and the frozen app's daemon child - all of
    which were given a real handle by whoever spawned them."""
    assert desktop.console_streams_present() is True
    assert desktop.redirect_gui_streams(tmp_path) is None
    assert not (tmp_path / desktop.GUI_LOG_NAME).exists()


def test_a_console_less_launch_gets_a_real_stdout_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with no_console(monkeypatch):
        assert desktop.console_streams_present() is False
        path = desktop.redirect_gui_streams(tmp_path)
        assert path == tmp_path / desktop.GUI_LOG_NAME
        assert sys.stdout is not None and sys.stderr is not None
        print("a line that would otherwise have been discarded")
        print("and one on stderr", file=sys.stderr)
        sys.stdout.flush()
    written = (tmp_path / desktop.GUI_LOG_NAME).read_text(encoding="utf-8")
    assert "swe-mux desktop shell started" in written
    assert "a line that would otherwise have been discarded" in written
    assert "and one on stderr" in written


def test_the_redirect_is_installed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with no_console(monkeypatch):
        first = desktop.redirect_gui_streams(tmp_path)
        stream = sys.stdout
        assert desktop.redirect_gui_streams(tmp_path) is None
        assert sys.stdout is stream, "a second call must not reopen the file"
    assert first is not None


def test_an_unwritable_data_directory_leaves_the_process_as_it_found_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A diagnostic that raised while installing itself would be strictly worse
    than the silence it replaces."""
    blocked = tmp_path / "file-not-a-directory"
    blocked.write_text("", encoding="utf-8")
    with no_console(monkeypatch):
        assert desktop.redirect_gui_streams(blocked / "data") is None
        assert sys.stdout is None and sys.stderr is None


def test_the_log_keeps_one_rotated_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / desktop.GUI_LOG_NAME
    path.write_text("x" * (desktop.GUI_LOG_MAX_BYTES + 1), encoding="utf-8")
    with no_console(monkeypatch):
        desktop.redirect_gui_streams(tmp_path)
    assert path.with_suffix(".log.1").is_file(), "the failing launch before this one"
    assert path.stat().st_size < desktop.GUI_LOG_MAX_BYTES


# --------------------------------------------------------------------------- #
# Reporting a failure that happens before the window exists
# --------------------------------------------------------------------------- #


def test_a_startup_failure_reaches_the_ledger_the_log_and_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quiet_message_box: list[str]
) -> None:
    """Three sinks, because none of them subsumes the others: only the message
    box is seen at the time, only the ledger puts it in sequence with the tray's
    other events, and only the log carries the line the fault came from."""
    monkeypatch.setenv("MUX_DATA_DIR", str(tmp_path))
    with no_console(monkeypatch):
        desktop.redirect_gui_streams()
        try:
            raise RuntimeError("webview2 runtime is not installed")
        except RuntimeError as exc:
            desktop.report_launch_failure(str(exc), exc)
    assert "webview2 runtime is not installed" in _quiet_message_box[0]
    assert desktop.GUI_LOG_NAME in _quiet_message_box[0], "it names where to look"
    ledger = (tmp_path / "lifecycle.log").read_text(encoding="utf-8")
    assert "desktop shell failed to start: webview2 runtime is not installed" in ledger
    log = (tmp_path / desktop.GUI_LOG_NAME).read_text(encoding="utf-8")
    assert "RuntimeError: webview2 runtime is not installed" in log
    assert "Traceback" in log, "str(exc) names the fault; only this names the line"


def test_a_usage_error_is_reported_before_the_silent_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quiet_message_box: list[str]
) -> None:
    """A GUI launcher turns argparse's usage message into a process that starts
    and vanishes. The exit still happens; it just no longer happens in silence."""
    monkeypatch.setenv("MUX_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exit_request:
        desktop._parse_desktop_arguments(["--no-such-flag"])
    assert exit_request.value.code == 2
    assert "--no-such-flag" in _quiet_message_box[0]
    assert "Usage: swe-mux" in _quiet_message_box[0]


def test_asking_for_help_is_not_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    _quiet_message_box: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_request:
        desktop._parse_desktop_arguments(["--help"])
    assert exit_request.value.code == 0
    assert _quiet_message_box == []
    assert "swe-mux desktop shell" in capsys.readouterr().out


def test_a_config_that_will_not_load_becomes_a_reported_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quiet_message_box: list[str]
) -> None:
    monkeypatch.setenv("MUX_DATA_DIR", str(tmp_path))
    broken = tmp_path / "config.toml"
    broken.write_text("this is not = = toml", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_request:
        desktop._run_desktop_shell(["--config", str(broken)])
    assert exit_request.value.code == 1
    assert _quiet_message_box, "the failure must not be silent"
    assert "lifecycle.log" in _quiet_message_box[0]
    assert (tmp_path / "lifecycle.log").is_file()


def test_the_crash_sink_is_armed_before_the_runtime_is_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _quiet_message_box: list[str]
) -> None:
    """`enable_crash_tracebacks` used to run partway through `DesktopRuntime`,
    so a native crash in the single-instance mutex or the control token left no
    trace at all. It now runs the moment the data directory is known."""
    monkeypatch.setenv("MUX_DATA_DIR", str(tmp_path))
    armed: list[Path] = []
    monkeypatch.setattr("swe_mux.desktop.enable_crash_tracebacks", armed.append)

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("no single-instance mutex on this host")

    monkeypatch.setattr(desktop, "DesktopRuntime", explode)
    with pytest.raises(SystemExit):
        desktop._run_desktop_shell([])
    assert armed == [tmp_path], "armed before the thing that failed"


# --------------------------------------------------------------------------- #
# The entry point itself
# --------------------------------------------------------------------------- #


def test_main_installs_the_redirect_before_it_can_dispatch_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `--daemon-child` and `--supervisor-child` branches are also how the
    frozen app starts its children, and a failure in one of those has to reach a
    file too - so the redirect happens before the dispatch, not inside it."""
    order: list[str] = []
    monkeypatch.setattr(desktop, "redirect_gui_streams", lambda *_: order.append("redirect"))
    monkeypatch.setattr(desktop, "_run_desktop_shell", lambda _: order.append("shell"))
    monkeypatch.setattr(
        desktop, "dispatch_internal_module", lambda _: bool(order.append("dispatch"))
    )
    desktop.main([])
    assert order == ["redirect", "dispatch", "shell"]


def test_the_desktop_entry_point_is_a_gui_script_and_the_others_are_not() -> None:
    """The half of the tradeoff `pyproject.toml` cannot assert about itself.

    Moving `swe-mux` back to `[project.scripts]` re-introduces the console
    window; moving `mux` or `muxd` *out* of it would silence two programs whose
    entire output is text. Both are one-line edits, and neither would fail
    anything else in this suite.
    """
    root = Path(__file__).resolve().parent.parent
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["gui-scripts"] == {"swe-mux": "swe_mux.desktop:main"}
    assert manifest["project"]["scripts"] == {
        "swemux": "swe_mux.cli:main",
        "swemuxd": "swe_mux.__main__:main",
        "mux": "swe_mux.cli:main",
        "muxd": "swe_mux.__main__:main",
    }


def test_the_alias_pairs_are_the_same_program_rather_than_two_of_them() -> None:
    """`swemux` may not drift into meaning something `mux` does not.

    The pair exists because `mux` is a contested name, not because there are two
    clients. Pointing one of them at a different target - a "v2" entry point, a
    thin wrapper - would make every document that says `mux` quietly describe a
    different program, and nothing else in this suite compares the two targets.
    """
    root = Path(__file__).resolve().parent.parent
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = manifest["project"]["scripts"]
    assert scripts["swemux"] == scripts["mux"]
    assert scripts["swemuxd"] == scripts["muxd"]
    assert scripts["swemux"] != scripts["swemuxd"]
