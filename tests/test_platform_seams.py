"""The platform seams themselves: what each host must answer, and what it must refuse.

These are the tests that make the Phase 10 boundary real rather than decorative.
Every case here is either host-neutral or explicitly marked for the host whose rule
it is, and none of them mocks `sys.platform` to fake a target - the cross-platform
findings are explicit that a test which mocks the platform proves nothing about the
platform.
"""

from __future__ import annotations

import contextlib
import os
import sys

import pytest

from swe_mux.config import Config, LaunchProfile, default_shell_executable
from swe_mux.host_platform import IS_WINDOWS, platform_key, platform_label
from swe_mux.process_reaper import ProcessReaper, create_reaper, process_in_job
from swe_mux.profiles import profile_host_error
from swe_mux.pty_backend import PtyProcess, open_pty, pty_backend_name
from swe_mux.pty_host import merge_environment

POSIX_ONLY = pytest.mark.skipif(IS_WINDOWS, reason="POSIX process-group semantics")
WINDOWS_ONLY = pytest.mark.skipif(not IS_WINDOWS, reason="Windows Job object semantics")


def test_platform_key_is_one_of_the_known_targets() -> None:
    assert platform_key() in {"windows", "macos", "linux", "posix"}
    assert platform_label()


def test_the_reaper_for_this_host_satisfies_the_shared_contract() -> None:
    """Whatever this host uses for ownership must present the same four operations."""
    reaper = create_reaper()
    try:
        assert isinstance(reaper, ProcessReaper)
        assert reaper.process_ids() == [] or isinstance(reaper.process_ids(), list)
        child = reaper.create_child()
        child.close()
        child.close()  # idempotent
    finally:
        reaper.close()


def test_the_pty_backend_for_this_host_satisfies_the_shared_contract() -> None:
    process = open_pty(80, 24)
    try:
        assert isinstance(process, PtyProcess)
        assert process.pid == -1
    finally:
        process.close()
    assert pty_backend_name() in {"conpty", "posix"}


@WINDOWS_ONLY
def test_windows_reports_job_membership_and_posix_reports_unknowable() -> None:
    assert process_in_job() in {True, False}


@POSIX_ONLY
def test_posix_process_in_job_is_unknowable_rather_than_false() -> None:
    """None means "no such concept here", which is not the same as "not in a job".

    Returning False would read to a caller as a positive finding - "checked, and this
    process is safe" - about a container that does not exist on this host.
    """
    assert process_in_job() is None


@POSIX_ONLY
def test_a_reaper_refuses_to_own_its_own_process_group() -> None:
    """The one failure that would turn session cleanup into a whole-app kill.

    If a child were started without `setsid`, its group is the daemon's group, and
    taking ownership of that would make `close()` signal the daemon, the supervisor,
    and every sibling session. It has to be refused where it is detectable.
    """
    from swe_mux.posix_process_group import ProcessGroupError, ProcessGroupReaper

    reaper = ProcessGroupReaper()
    with pytest.raises(ProcessGroupError, match="own group"):
        reaper.assign(os.getpid())
    assert reaper.process_ids() == []
    reaper.close()


@POSIX_ONLY
def test_a_posix_pty_runs_a_child_in_its_own_session_and_is_owned() -> None:
    """The two halves of POSIX lifetime, proven against each other on a real child.

    `pty.fork` must leave the child leading its own process group, because that is
    exactly the precondition `ProcessGroupReaper.assign` enforces. Testing them
    separately would let either drift into being individually correct and jointly
    useless.
    """
    import time

    from swe_mux.posix_process_group import ProcessGroupReaper

    process = open_pty(80, 24)
    reaper = ProcessGroupReaper()
    try:
        process.spawn(sys.executable, ("-c", "import time; time.sleep(30)"), None, None)
        assert process.pid > 0
        assert os.getpgid(process.pid) != os.getpgid(0)
        reaper.assign(process.pid)
        assert process.pid in reaper.process_ids()
        reaper.close()
        deadline = time.monotonic() + 5
        while process.isalive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not process.isalive()
    finally:
        process.force_kill()
        process.close()
        reaper.close()


@POSIX_ONLY
def test_a_posix_pty_round_trips_output_and_reports_its_exit_code() -> None:
    import time

    process = open_pty(80, 24)
    try:
        process.spawn(
            sys.executable,
            ("-c", "import sys; sys.stdout.write('mux-hello\\n'); sys.stdout.flush(); sys.exit(3)"),
            None,
            None,
        )
        seen = bytearray()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            chunk = process.read()
            if chunk:
                seen += chunk
            if b"mux-hello" in seen and not process.isalive():
                break
            time.sleep(0.02)
        assert b"mux-hello" in seen
        assert process.exit_status() == 3
    finally:
        process.close()


@POSIX_ONLY
def test_posix_environment_keys_are_case_sensitive() -> None:
    """`Path` and `PATH` are two variables on POSIX; folding them would drop one."""
    merged = merge_environment({"Path": "a", "PATH": "b"}, {"PATH": "c"})
    assert merged == {"PATH": "c", "Path": "a"}


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows case-insensitive environment rule")
def test_windows_environment_keys_collapse_case_insensitively() -> None:
    merged = merge_environment({"Path": "a"}, {"PATH": "c"})
    assert merged == {"PATH": "c"}


def test_a_profile_for_another_host_is_refused_here() -> None:
    """`platforms` was stored but never used to select; now it decides both ways."""
    foreign = "linux" if IS_WINDOWS else "windows"
    profile = LaunchProfile("x", "X", "some-shell", [], platforms=[foreign])
    assert profile_host_error(profile) is not None
    assert profile_host_error(LaunchProfile("y", "Y", "s", [], platforms=[platform_key()])) is None
    # An unrestricted profile stays permitted: a hand-written profile that never
    # considered portability must not become unspawnable.
    assert profile_host_error(LaunchProfile("z", "Z", "s", [], platforms=[])) is None


@POSIX_ONLY
def test_a_posix_profile_may_declare_the_generic_posix_target() -> None:
    assert profile_host_error(LaunchProfile("p", "P", "s", [], platforms=["posix"])) is None


def test_the_default_shell_is_one_this_host_can_actually_start() -> None:
    executable = default_shell_executable()
    assert executable
    if IS_WINDOWS:
        assert executable.casefold().endswith(".exe")
    else:
        assert not executable.casefold().endswith(".exe")


def test_a_fresh_config_declares_this_host_rather_than_windows() -> None:
    assert LaunchProfile("a", "A", "sh", []).platforms == [platform_key()]
    assert Config().shell_exe == default_shell_executable()


@POSIX_ONLY
def test_secret_persistence_fails_closed_rather_than_writing_cleartext(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """No keyring must mean a refused write, never a silently unencrypted one."""
    from pathlib import Path

    from swe_mux.secret_backends import SecretStoreError, UnavailableBackend
    from swe_mux.secret_store import PlatformSecretStore

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    store = PlatformSecretStore(
        Path(str(tmp_path)) / "secrets.json", backend=UnavailableBackend("no keyring in the test")
    )
    with pytest.raises(SecretStoreError):
        store.set("openrouter_api_key", "sk-or-secret")
    status = store.status("openrouter_api_key")
    assert status["configured"] is False
    assert status["encrypted"] is False


def test_the_opt_in_file_backend_never_claims_to_be_encrypted(tmp_path: object) -> None:
    """It is base64 in a 0600 file. Reporting it as encrypted would be the real bug."""
    from pathlib import Path

    from swe_mux.secret_backends import RestrictedFileBackend
    from swe_mux.secret_store import PlatformSecretStore

    path = Path(str(tmp_path)) / "secrets.json"
    store = PlatformSecretStore(path, backend=RestrictedFileBackend(path))
    store.set("openrouter_api_key", "sk-or-plaintext")
    assert store.get("openrouter_api_key") == "sk-or-plaintext"
    status = store.status("openrouter_api_key")
    assert status["configured"] is True
    assert status["persistent"] is True
    assert status["encrypted"] is False
    assert status["backend"] == "file"


@POSIX_ONLY
def test_the_file_backend_is_not_reachable_without_an_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pathlib import Path

    from swe_mux.secret_backends import RestrictedFileBackend, resolve_backend

    monkeypatch.delenv("MUX_SECRET_STORE", raising=False)
    monkeypatch.setattr("swe_mux.secret_backends.shutil.which", lambda _name: None)
    assert not isinstance(resolve_backend(Path(str(tmp_path)) / "s.json"), RestrictedFileBackend)
    monkeypatch.setenv("MUX_SECRET_STORE", "file")
    assert isinstance(resolve_backend(Path(str(tmp_path)) / "s.json"), RestrictedFileBackend)


def test_home_cwd_strategy_actually_starts_in_the_home_directory(tmp_path: object) -> None:
    """It was accepted by config and offered in Settings while doing nothing."""
    from pathlib import Path

    from swe_mux.profiles import resolve_profile

    executable = default_shell_executable()
    config = Config()
    config.shell_profiles = [
        LaunchProfile(
            "home-profile",
            "Home",
            executable,
            [],
            platforms=[platform_key()],
            cwd_strategy="home",
        ),
        LaunchProfile(
            "native-profile",
            "Native",
            executable,
            [],
            platforms=[platform_key()],
            cwd_strategy="native",
        ),
    ]
    project = Path(str(tmp_path))
    assert resolve_profile(config, "home-profile", project).start_cwd == str(Path.home())
    assert resolve_profile(config, "native-profile", project).start_cwd == str(project)

@POSIX_ONLY
def test_the_guardian_reaps_the_group_when_the_daemon_pipe_closes() -> None:
    """The case a process group cannot cover: the daemon dying without asking.

    Closing the pipe *is* the daemon dying, as far as the guardian can tell - a
    SIGKILLed process cannot decline to close its descriptors, which is exactly
    why EOF is the trigger rather than a heartbeat. Proven on a real child in a
    real process group, because the whole property is about what the kernel does.
    """
    import signal
    import subprocess
    import time

    from swe_mux.posix_guardian import start_guardian

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    guardian = None
    try:
        pgid = os.getpgid(child.pid)
        assert pgid != os.getpgid(0)
        guardian = start_guardian(pgid)
        assert guardian is not None, "the guardian process could not be started"
        # Dropping the pipe without a release is what an unclean daemon death
        # looks like from the guardian's side.
        guardian.close()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.05)
        assert child.poll() is not None, "the guardian did not reap the group"
    finally:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        with contextlib.suppress(Exception):
            child.wait(timeout=5)
        if guardian is not None:
            guardian.close()


@POSIX_ONLY
def test_a_released_guardian_leaves_the_group_running() -> None:
    """A deliberate restart must not reap sessions - that is the whole point of it.

    The same pipe carries both outcomes, so the difference between "the daemon
    crashed" and "the daemon is restarting on purpose" is one written word. If
    release did not work, every session-preserving reload on POSIX would silently
    become a session-killing one.
    """
    import signal
    import subprocess
    import time

    from swe_mux.posix_guardian import start_guardian

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    guardian = None
    try:
        pgid = os.getpgid(child.pid)
        guardian = start_guardian(pgid)
        assert guardian is not None
        guardian.release()
        time.sleep(3)
        assert child.poll() is None, "a released guardian killed the group anyway"
    finally:
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        with contextlib.suppress(Exception):
            child.wait(timeout=5)


@POSIX_ONLY
def test_a_nested_reaper_shares_one_guardian_with_its_parent() -> None:
    """One guardian per daemon, not one per session.

    Measured on Linux before this held: every session started two guardian
    processes, because `PtyHost` assigns the root pid to the daemon-wide reaper
    and `SessionManager` then assigns the same pid to a nested per-session one.
    Both watched the same group, so the second bought nothing.
    """
    from swe_mux.posix_process_group import ProcessGroupReaper

    root = ProcessGroupReaper(guard_against_daemon_death=False)
    child = root.create_child()
    assert child._parent is root
    # A nested reaper never owns a guardian, so closing one session cannot
    # un-guard its siblings.
    child.close()
    assert root._guardian is None
    root.close()

def test_the_agent_shim_is_written_in_this_host_executable_script_format(
    tmp_path: object,
) -> None:
    """A shim must be executable *and* recognizable as ours on this host.

    Both halves matter and they pull in opposite directions. Windows needs the
    `.cmd` extension or PATHEXT will not run it; POSIX needs no extension at all,
    because `claude` is what the user types and what harness detection looks for.
    `is_mux_shim` therefore has to gate on a different suffix rule per host - and
    if it did not, every POSIX shim would read as a real CLI.
    """
    from pathlib import Path

    from swe_mux.config import Config
    from swe_mux.launchers import create_agent_shims
    from swe_mux.shim_paths import SHIM_NAMES, is_mux_shim

    data_dir = Path(str(tmp_path)) / "data"
    env = create_agent_shims(Config(data_dir=data_dir))
    bin_dir = data_dir / "bin"
    written = sorted(item.name for item in bin_dir.iterdir())
    assert written, "no shims were written"
    for name in SHIM_NAMES:
        shim = bin_dir / name
        assert shim.is_file(), f"{name} was not written for this host"
        assert is_mux_shim(shim), f"{name} is not recognized as a mux shim on this host"
        if not IS_WINDOWS:
            assert shim.suffix == "", "a POSIX shim must be extensionless to be found by name"
            assert os.access(shim, os.X_OK), "a POSIX shim must be executable"
            assert shim.read_text(encoding="utf-8").startswith("#!"), "missing shebang"
    assert env["MUX_SHIM_DIR"] == str(bin_dir)


def test_harness_detection_never_resolves_to_our_own_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """The self-invocation trap, checked on whichever host is running.

    `harness.detect_installation` goes through `which_real`. If the shim were not
    recognized, every harness would report as installed and every launch would
    recurse into the shim. This puts a shim directory first on PATH - exactly what
    a daemon relaunched from inside a session inherits - and asserts it is seen
    through.
    """
    from pathlib import Path

    from swe_mux.config import Config
    from swe_mux.launchers import create_agent_shims
    from swe_mux.shim_paths import SHIM_NAMES, path_without_shim_dirs, which_real

    data_dir = Path(str(tmp_path)) / "data"
    create_agent_shims(Config(data_dir=data_dir))
    bin_dir = data_dir / "bin"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.delenv("MUX_SHIM_DIR", raising=False)

    assert str(bin_dir) not in path_without_shim_dirs()
    for name in SHIM_NAMES:
        stem = Path(name).stem
        resolved = which_real(stem)
        assert resolved is None or Path(resolved) != bin_dir / name, (
            f"which_real resolved {stem} to our own shim"
        )

@POSIX_ONLY
def test_the_posix_shim_launches_the_real_cli_with_argv_intact(tmp_path: object) -> None:
    """The whole POSIX launch chain, minus the vendor binary.

    shim -> `swe_mux.agent_launcher` -> `MUX_<NAME>_EXE`, executed for real. What
    this proves that a unit test of any single link cannot: the shim is actually
    executable by the kernel, the shebang resolves, the launcher reads the
    per-harness variables the shim publishes, and argv survives the round trip.

    The argv case is deliberately the nastiest real one. Codex threads a
    JSON-valued `-c notify=[...]` through, which is exactly the shape a shell
    re-splits if the shim forwards with `$@` unquoted instead of `"$@"` - and the
    damage is invisible until an agent starts with silently mangled configuration.
    """
    import json
    import subprocess
    from pathlib import Path

    from swe_mux.config import Config
    from swe_mux.launchers import create_agent_shims

    root = Path(str(tmp_path))
    data_dir = root / "data"
    fake_bin = root / "fake"
    fake_bin.mkdir(parents=True)
    # A stand-in for the vendor CLI: prints its argv as JSON so the assertion is
    # about exact arguments rather than about a substring of a log line.
    fake_cli = fake_bin / "codex"
    fake_cli.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json, sys",
                "print(json.dumps(sys.argv[1:]))",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    fake_cli.chmod(0o755)

    config = Config(data_dir=data_dir)
    config.harness_exe = {**config.harness_exe, "codex": str(fake_cli)}
    env_extra = create_agent_shims(config)
    shim = data_dir / "bin" / "codex"
    assert shim.is_file() and os.access(shim, os.X_OK)

    notify = json.dumps(["a b", 'quote"inside', "{\"k\": 1}"])
    argv = ["--flag", "-c", f"notify={notify}", "a path/with space"]
    result = subprocess.run(
        [str(shim), *argv],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **env_extra},
    )
    assert result.returncode == 0, result.stderr
    # The launcher prepends the harness's own configured arguments, so the
    # assertion is that ours arrive intact at the end, unsplit and unquoted.
    received = json.loads(result.stdout.strip().splitlines()[-1])
    assert received[-len(argv):] == argv, f"argv was corrupted: {received}"

def test_the_data_directory_follows_this_host_convention() -> None:
    """XDG on Linux, Application Support on macOS, `~/.mux` on Windows."""
    from pathlib import Path

    from swe_mux.config import default_data_dir

    directory = default_data_dir()
    if IS_WINDOWS:
        assert directory == Path.home() / ".mux"
    elif (Path.home() / ".mux").exists():
        # An existing directory always wins; see the next test for why.
        assert directory == Path.home() / ".mux"
    elif sys.platform == "darwin":
        assert directory == Path.home() / "Library" / "Application Support" / "swe-mux"
    else:
        assert directory.name == "swe-mux"
        assert "share" in str(directory) or "XDG" in str(directory)


def test_an_existing_data_directory_always_wins_over_the_convention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """A convention applies to a fresh install, never to one that already has data.

    Without this rule, moving to XDG would silently start a POSIX user from an
    empty directory *beside* their real one - projects gone, history gone, nothing
    reporting an error, and the old data still on disk looking fine.
    """
    from pathlib import Path

    from swe_mux.config import default_data_dir

    home = Path(str(tmp_path)) / "home"
    (home / ".mux").mkdir(parents=True)
    monkeypatch.delenv("MUX_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    assert default_data_dir() == home / ".mux"


def test_an_explicit_data_dir_override_beats_every_convention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pathlib import Path

    from swe_mux.config import default_data_dir

    chosen = Path(str(tmp_path)) / "elsewhere"
    monkeypatch.setenv("MUX_DATA_DIR", str(chosen))
    assert default_data_dir() == chosen


@POSIX_ONLY
def test_the_posix_firewall_advises_rather_than_repairs() -> None:
    """Opening a port needs root and is the user's decision, so nothing here mutates.

    `needs_repair` means "swe-mux can fix this for you", so on POSIX it must stay
    False even when the probe fails - otherwise a UI would offer a repair button
    that cannot exist.
    """
    import asyncio

    from swe_mux.posix_firewall import (
        allow_command,
        inspect_posix_firewall,
        posix_firewall_supported,
    )

    assert posix_firewall_supported() is True
    # A port nothing is listening on: the probe must fail rather than hang.
    status = asyncio.run(inspect_posix_firewall(59_231))
    assert status["supported"] is True
    assert status["reachable"] is False
    assert status["needs_repair"] is False
    assert status["repair_supported"] is False
    assert status["remedy"]
    command = allow_command(8765)
    if command is not None:
        # Advice only, and it must never be something the daemon could run itself.
        assert command.startswith("sudo ")
        assert "8765" in command

def test_the_default_agent_command_is_shaped_for_this_host() -> None:
    """`claude.exe` is not a cosmetic default off Windows - it selects the wrong binary.

    Under WSL the Windows install is on PATH through interop, so
    `shutil.which("claude.exe")` *succeeds* and resolves to `/mnt/c/.../claude.exe`.
    A Linux daemon then launches a Windows agent: it runs, it paints a TUI, it
    reports `\wsl.localhost\...` as its working directory, it writes its transcript
    into the Windows home where no Linux path points, and it joins no Linux process
    group so cleanup cannot reach it. Measured exactly that way before this existed.
    """
    from swe_mux.config import default_harness_executables
    from swe_mux.harness import HARNESSES, host_executable

    for name, harness in HARNESSES.items():
        chosen = host_executable(harness)
        if IS_WINDOWS:
            assert chosen == harness.executable
        else:
            assert not chosen.casefold().endswith(".exe"), f"{name} defaults to a Windows binary"
    assert all(
        IS_WINDOWS or not command.casefold().endswith(".exe")
        for command in default_harness_executables().values()
    )


def test_an_interop_windows_binary_is_never_a_usable_agent() -> None:
    """The rule that keeps a Linux daemon from launching a Windows agent.

    Only meaningful under WSL, and deliberately inert elsewhere: on a native Linux
    host `/mnt` is an ordinary mount point and nothing about it is suspicious, so
    rejecting paths there would break legitimate installs.
    """
    from swe_mux.host_platform import is_windows_interop_path, running_under_wsl

    if not running_under_wsl():
        # Inert off WSL, including on Windows itself.
        assert is_windows_interop_path("/mnt/c/Users/x/claude.exe") is False
        return
    assert is_windows_interop_path("/mnt/c/Users/x/AppData/Roaming/npm/claude") is True
    assert is_windows_interop_path("/mnt/c/Users/x/.local/bin/claude.exe") is True
    assert is_windows_interop_path("/home/user/.local/bin/claude") is False
    # A multi-character directory under /mnt is an ordinary mount, not a drive.
    assert is_windows_interop_path("/mnt/storage/bin/claude") is False


def test_which_real_skips_a_windows_binary_reached_through_interop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence in depth behind the corrected default: even a bare name must not
    resolve to an interop binary, because a WSL PATH carries the Windows npm
    directory and `command -v codex` lands there routinely."""
    from swe_mux import shim_paths
    from swe_mux.host_platform import running_under_wsl

    if not running_under_wsl():
        pytest.skip("the interop rule only applies inside a WSL distribution")

    monkeypatch.setattr(
        shim_paths.shutil, "which", lambda _cmd, path=None: "/mnt/c/npm/codex.exe"
    )
    assert shim_paths.which_real("codex") is None
