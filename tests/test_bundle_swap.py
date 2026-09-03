"""The gate that keeps helpers out of the bundle-swap window (`bundle_swap`).

Two halves are asserted separately and for different reasons. The *content* of a
generated shim is asserted for both platforms on every host, because the artifact
is a property of the repository and not of the runner - the same rule that made
the voice-closure assertions injectable. Only the tests that actually *execute* a
shim are platform-gated, since running one is genuinely a question about the host.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from swe_mux.bundle_swap import (
    EXEC_LAUNCHER_STEM,
    HOLD_FILENAME,
    WAIT_SECONDS,
    clear_stale_hold,
    ensure_exec_launcher,
    exec_launcher_path,
    hold_bundle_swap,
    hold_path,
    hook_delivery_executable,
    hook_delivery_from_env,
    write_script,
)

WINDOWS = sys.platform == "win32"


def test_source_install_gets_no_launcher(tmp_path: Path) -> None:
    """A virtualenv interpreter is renamed by nothing, so it is not worth a shell."""
    assert ensure_exec_launcher(tmp_path, frozen=False) is None
    assert not (tmp_path / "bin").exists()


def test_hook_delivery_executable_falls_back_to_the_interpreter(tmp_path: Path) -> None:
    assert hook_delivery_executable(tmp_path, executable="py.exe", frozen=False) == "py.exe"
    assert hook_delivery_executable(None) == sys.executable


def test_hook_delivery_executable_prefers_the_launcher(tmp_path: Path) -> None:
    resolved = hook_delivery_executable(
        tmp_path, executable="C:/app/swe-mux.exe", frozen=True, windows=True
    )
    assert resolved == str(exec_launcher_path(tmp_path, windows=True))


@pytest.mark.parametrize("windows", [True, False])
def test_launcher_is_a_drop_in_for_the_interpreter(tmp_path: Path, windows: bool) -> None:
    """The shim adds a gate and changes nothing else about the argv it forwards.

    This is what lets every existing caller keep building
    `<python> -m swe_mux.hook_client <event>` and swap only the program in front.
    """
    path = ensure_exec_launcher(
        tmp_path, executable="/app/swe-mux", frozen=True, windows=windows
    )
    assert path is not None
    assert path.name == EXEC_LAUNCHER_STEM + (".cmd" if windows else "")
    body = path.read_bytes().decode("utf-8")
    forward = '"/app/swe-mux" %*' if windows else 'exec "/app/swe-mux" "$@"'
    assert forward in body
    assert str(hold_path(tmp_path)) in body
    # The gate has to precede the launch, or it is not a gate.
    assert body.index(HOLD_FILENAME) < body.index(forward)


def test_windows_launcher_is_written_with_real_crlf(tmp_path: Path) -> None:
    """`Path.write_text` would translate to `\\r\\r\\n`, which breaks a `goto` label."""
    path = ensure_exec_launcher(tmp_path, executable="x.exe", frozen=True, windows=True)
    assert path is not None
    raw = path.read_bytes()
    assert b"\r\r\n" not in raw
    assert raw.startswith(b"@echo off\r\n")


def test_launcher_rewrite_is_skipped_when_the_bytes_match(tmp_path: Path) -> None:
    """A shim is executed by processes the daemon does not own; do not truncate one."""
    path = ensure_exec_launcher(tmp_path, executable="x.exe", frozen=True, windows=True)
    assert path is not None
    before = path.stat().st_mtime_ns
    again = ensure_exec_launcher(tmp_path, executable="x.exe", frozen=True, windows=True)
    assert again == path
    assert path.stat().st_mtime_ns == before
    ensure_exec_launcher(tmp_path, executable="y.exe", frozen=True, windows=True)
    assert "y.exe" in path.read_bytes().decode("utf-8")


def test_hold_is_raised_for_the_block_and_always_dropped(tmp_path: Path) -> None:
    path = hold_path(tmp_path)
    with hold_bundle_swap(tmp_path, settle=0):
        assert path.is_file()
    assert not path.exists()
    with pytest.raises(RuntimeError), hold_bundle_swap(tmp_path, settle=0):
        raise RuntimeError("swap blew up")
    assert not path.exists()


def test_hold_settles_before_yielding(tmp_path: Path) -> None:
    """The settle covers bootloaders already in flight; it is not a courtesy delay."""
    started = time.monotonic()
    with hold_bundle_swap(tmp_path, settle=0.2):
        elapsed = time.monotonic() - started
    assert elapsed >= 0.2


def test_clear_stale_hold_only_removes_an_abandoned_one(tmp_path: Path) -> None:
    path = hold_path(tmp_path)
    path.write_text("1 0.0\n", encoding="utf-8")
    assert clear_stale_hold(tmp_path, max_age=300.0) is False
    assert path.is_file()
    old = time.time() - 600
    os.utime(path, (old, old))
    assert clear_stale_hold(tmp_path, max_age=300.0) is True
    assert not path.exists()
    assert clear_stale_hold(tmp_path) is False


def test_hook_delivery_from_env_reads_the_published_launcher() -> None:
    assert hook_delivery_from_env({}) == sys.executable
    assert hook_delivery_from_env({"MUX_HOOK_LAUNCHER": "/x/swemux-exec"}) == "/x/swemux-exec"


def _echo_target(tmp_path: Path) -> str:
    """A stand-in for the frozen exe that reports its argv and a chosen exit code."""
    script = tmp_path / "target.py"
    script.write_text(
        "import sys\nprint(' '.join(sys.argv[1:]))\nsys.exit(7)\n", encoding="utf-8"
    )
    return f'"{sys.executable}" "{script}"'


def _run_launcher(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    argv = [str(path), *args]
    return subprocess.run(argv, capture_output=True, text=True, shell=False, timeout=120)


def _install_launcher(tmp_path: Path) -> Path:
    """The generated shim, with a real runnable program baked in as its target."""
    path = ensure_exec_launcher(tmp_path, executable="PLACEHOLDER", frozen=True)
    assert path is not None
    body = path.read_bytes().decode("utf-8").replace('"PLACEHOLDER"', _echo_target(tmp_path))
    write_script(path, body)
    if not WINDOWS:
        path.chmod(0o755)
    return path


def test_launcher_forwards_arguments_and_the_exit_code(tmp_path: Path) -> None:
    """The common path: no swap in flight, so the shim is a transparent wrapper."""
    result = _run_launcher(_install_launcher(tmp_path), ["-m", "swe_mux.hook_client", "Stop"])
    assert result.stdout.strip() == "-m swe_mux.hook_client Stop"
    assert result.returncode == 7


def test_launcher_waits_out_a_held_swap(tmp_path: Path) -> None:
    """The whole point: a hook launched during the swap starts *after* it, not during.

    The hold is dropped by a thread rather than after a fixed sleep in the test, so
    the assertion is on the shim's own behaviour and not on a race with the clock:
    a loaded machine can only make the shim wait longer.
    """
    launcher = _install_launcher(tmp_path)
    hold = hold_path(tmp_path)
    hold.write_text("0 0.0\n", encoding="utf-8")
    release = threading.Timer(1.5, hold.unlink)
    release.start()
    try:
        started = time.monotonic()
        result = _run_launcher(launcher, ["gated"])
        elapsed = time.monotonic() - started
    finally:
        release.cancel()
    assert result.stdout.strip() == "gated"
    assert result.returncode == 7
    assert elapsed >= 1.0, "the shim launched the target while the swap was in flight"


def test_launcher_gives_up_on_a_wedged_hold(tmp_path: Path) -> None:
    """A hold nobody drops degrades to the old behaviour rather than to a hang."""
    body = _install_launcher(tmp_path).read_bytes().decode("utf-8")
    ceiling = str(WAIT_SECONDS if WINDOWS else int(WAIT_SECONDS / 0.1))
    assert ceiling in body


def test_agent_shims_are_gated_and_still_identify_as_ours(tmp_path: Path) -> None:
    """The gate sits above the marker `is_mux_shim` reads, and must not hide it.

    `shim_paths` reads only the first 4 KiB and matches on `swe_mux.agent_launcher`;
    a prologue that pushed the launch line past that cap would make every shim read
    as a real CLI and every launch recurse into itself.
    """
    from swe_mux.config import Config
    from swe_mux.launchers import create_agent_shims
    from swe_mux.shim_paths import SHIM_NAMES, is_mux_shim

    create_agent_shims(Config(data_dir=tmp_path))
    written = [tmp_path / "bin" / name for name in SHIM_NAMES]
    assert written and all(path.is_file() for path in written)
    for path in written:
        assert is_mux_shim(path)
        assert HOLD_FILENAME in path.read_bytes().decode("utf-8")


def test_source_install_publishes_no_launcher_to_sessions(tmp_path: Path) -> None:
    """No `MUX_HOOK_LAUNCHER` means no shell process per hook where none is needed."""
    from swe_mux.config import Config
    from swe_mux.launchers import create_agent_shims

    assert "MUX_HOOK_LAUNCHER" not in create_agent_shims(Config(data_dir=tmp_path))


def test_claude_hook_command_routes_through_the_launcher_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command written into `claude-hooks.json` is the thing that crashed."""
    import json

    from swe_mux.adapters.claude import ClaudeAdapter

    source = ClaudeAdapter("claude.exe", tmp_path / "source")
    assert source.settings_path is not None
    unhooked = json.loads(source.settings_path.read_text(encoding="utf-8"))
    assert unhooked["hooks"]["Stop"][0]["hooks"][0]["command"].startswith(
        sys.executable.replace("\\", "/")
    )

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = ClaudeAdapter("claude.exe", tmp_path / "frozen")
    assert frozen.settings_path is not None
    hooked = json.loads(frozen.settings_path.read_text(encoding="utf-8"))
    launcher = str(exec_launcher_path(tmp_path / "frozen")).replace("\\", "/")
    for event, entries in hooked["hooks"].items():
        assert entries[0]["hooks"][0]["command"].startswith(launcher), event


def test_codex_lifecycle_hooks_route_through_the_published_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux.adapters.codex import codex_lifecycle_hook_args

    monkeypatch.setenv("MUX_HOOK_LAUNCHER", "/x/swemux-exec")
    assert any("/x/swemux-exec" in arg for arg in codex_lifecycle_hook_args())
    monkeypatch.delenv("MUX_HOOK_LAUNCHER")
    assert not any("/x/swemux-exec" in arg for arg in codex_lifecycle_hook_args())
