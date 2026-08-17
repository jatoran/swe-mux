"""The platform seams themselves: what each host must answer, and what it must refuse.

These are the tests that make the Phase 10 boundary real rather than decorative.
Every case here is either host-neutral or explicitly marked for the host whose rule
it is, and none of them mocks `sys.platform` to fake a target - the cross-platform
findings are explicit that a test which mocks the platform proves nothing about the
platform.
"""

from __future__ import annotations

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
