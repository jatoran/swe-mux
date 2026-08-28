"""Resolving an agent CLI, and saying why when nothing is launchable.

Three failures on WSL Ubuntu on 2026-08-28 cost an operator about an hour of
manual archaeology, and `daemon.log` held none of them. Two were logic that was
wrong only on the host nobody tested:

- the `.exe` suffix-stripping recovery in `provider_accounts._spawn_command` was
  gated on Windows, where an `.exe` is at least plausible, and skipped on POSIX,
  where it is certainly wrong - so the daemon exec'd `codex.exe` verbatim with a
  working `codex` on the same PATH;
- `which_real` answered `None` for "nothing exists", "that is our own shim" and
  "that is a Windows binary reached through WSL interop", so the operator was
  told the file did not exist when the truth was that a Windows codex had been
  found and deliberately refused.

Every case here runs on **every** host. Where a platform branch has to be forced,
it is forced through a seam that is a real parameter or a real host predicate
(`windows=`, `host_platform.running_under_wsl`) rather than by faking
`sys.platform`, which the cross-platform findings are explicit proves nothing.
Where a real file is needed, a real one is written in this host's executable
form, because a hand-built fake of a resolver's input is checked by nothing
(`[tool.mypy]` is pinned to `platform = "win32"` and does not read `tests/` at
all).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from swe_mux import host_platform, shim_paths
from swe_mux.bounded_subprocess import ProcessOutcome
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.host_platform import IS_WINDOWS
from swe_mux.launchers import create_agent_shims
from swe_mux.provider_accounts import ProviderAccountError, ProviderAccountManager
from swe_mux.shim_paths import (
    ExecutableResolution,
    combine_resolutions,
    resolve_executable,
    which_real,
)

INTEROP_CODEX = "/mnt/c/Users/Jatora/AppData/Roaming/npm/codex"


@pytest.fixture(autouse=True)
def _isolate_resolution() -> Iterator[None]:
    """No memoized PATH scan or already-reported refusal may leak between tests."""
    shim_paths.clear_caches()
    yield
    shim_paths.clear_caches()


def _install_cli(directory: Path, name: str) -> Path:
    """A real, launchable no-op CLI at *name*, in this host's executable form.

    Windows needs the `.cmd` extension for `shutil.which` to see it through
    PATHEXT; POSIX needs the executable bit and no extension. Writing a real one
    rather than stubbing `shutil.which` is what makes the assertion mean
    "this host would have launched it".
    """
    directory.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        path = directory / f"{name}.cmd"
        path.write_text("@echo off\r\n", encoding="utf-8")
        return path
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _only_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """PATH is exactly *directory*, and this host is not a WSL distribution.

    The second half matters: `is_windows_interop_path` refuses anything under a
    DrvFs mount, so a developer running the suite inside WSL with `TMPDIR` on
    `/mnt/...` would otherwise have every real file `_install_cli` writes refused
    as a Windows binary. The interop cases force the same predicate the other way,
    explicitly.
    """
    monkeypatch.setenv("PATH", str(directory))
    monkeypatch.delenv("MUX_SHIM_DIR", raising=False)
    monkeypatch.setattr(host_platform, "running_under_wsl", lambda: False)
    shim_paths.clear_caches()


def _pretend_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the one host predicate the interop refusal turns on.

    `running_under_wsl` reads `/proc/sys/kernel/osrelease`, so on Windows and on
    native Linux the interop branch is unreachable and its behaviour was asserted
    by nobody - which is precisely how the misleading message survived. Forcing
    the predicate is not faking a platform: `is_windows_interop_path` is pure
    path classification with this as its only host input.
    """
    monkeypatch.setattr(host_platform, "running_under_wsl", lambda: True)


# --- the three refusals, told apart -----------------------------------------


def test_nothing_of_that_name_says_so_and_names_what_was_searched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _only_path(monkeypatch, tmp_path / "empty")

    resolution = resolve_executable("codex")

    assert resolution.reason == "not_found"
    assert resolution.path is None
    assert resolution.rejected is None
    assert resolution.describe() == '"codex" was not found on PATH'


def test_our_own_shim_is_named_as_a_shim_rather_than_as_an_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real shim, written in this host's format by the real generator.

    "no codex is installed" and "the only codex on PATH is the wrapper that would
    invoke itself" call for different repairs, and the second one is invisible
    from a bare `None`.
    """
    data_dir = tmp_path / "data"
    create_agent_shims(Config(data_dir=data_dir))
    bin_dir = data_dir / "bin"
    _only_path(monkeypatch, bin_dir)

    resolution = resolve_executable("codex")

    assert resolution.reason == "mux_shim"
    assert resolution.path is None
    assert Path(resolution.rejected or "") == bin_dir / f"codex{shim_paths.SHIM_SUFFIX}"
    assert "swe-mux's own agent shim" in resolution.describe()
    assert "would invoke itself" in resolution.describe()
    assert "no other codex CLI was found on PATH" in resolution.describe()
    assert which_real("codex") is None


def test_an_interop_binary_is_named_as_found_and_refused_not_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message that cost the hour, asserted on every host.

    `which codex` answered `/mnt/c/.../npm/codex` and `npm ls -g` was empty: only
    the Windows install was reachable, mux correctly refused it, and then reported
    it as a missing file.
    """
    _pretend_wsl(monkeypatch)
    monkeypatch.setattr(
        shim_paths.shutil, "which", lambda _command, path=None: INTEROP_CODEX
    )

    resolution = resolve_executable("codex")

    assert resolution.reason == "windows_interop"
    assert resolution.path is None
    assert resolution.rejected == INTEROP_CODEX
    described = resolution.describe()
    assert INTEROP_CODEX in described
    assert "reached through WSL interop" in described
    assert "was refused" in described
    # The actionable half. Without this the operator is told what not to do and
    # nothing about what to do instead.
    assert "Install the Linux build of codex inside this distribution" in described
    assert which_real("codex") is None


def test_which_real_is_the_same_resolver_with_the_reason_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One resolver, so the two answers can never disagree."""
    directory = tmp_path / "bin"
    installed = _install_cli(directory, "codex")
    _only_path(monkeypatch, directory)

    resolution = resolve_executable("codex")

    assert resolution.reason == "found"
    assert Path(resolution.path or "") == installed
    assert which_real("codex") == resolution.path


# --- the ranking that decides which attempt gets reported --------------------


def test_a_refusal_outranks_an_absence_so_the_message_is_the_informative_one() -> None:
    absent = ExecutableResolution("codex.exe", None, "not_found")
    refused = ExecutableResolution("codex", None, "windows_interop", INTEROP_CODEX)

    combined = combine_resolutions(absent, refused)

    assert combined.reason == "windows_interop"
    assert combined.rejected == INTEROP_CODEX
    # And the name that was *also* searched is carried, so the sentence describes
    # the whole search rather than half of it.
    assert combined.also_tried == ("codex.exe",)
    assert 'also tried "codex.exe"' in combined.describe()


def test_a_tie_keeps_the_callers_preferred_spelling() -> None:
    first = ExecutableResolution("codex.exe", None, "not_found")
    second = ExecutableResolution("codex", None, "not_found")

    combined = combine_resolutions(first, second)

    assert combined.command == "codex.exe"
    assert combined.describe() == '"codex.exe" was not found on PATH (also tried "codex")'


def test_a_find_outranks_everything() -> None:
    refused = ExecutableResolution("codex.exe", None, "mux_shim", "/home/u/.mux/bin/codex")
    found = ExecutableResolution("codex", "/usr/local/bin/codex", "found")

    assert combine_resolutions(refused, found).path == "/usr/local/bin/codex"
    assert combine_resolutions(found, refused).path == "/usr/local/bin/codex"


# --- what reaches daemon.log ------------------------------------------------


def test_a_refusal_is_logged_once_with_the_path_it_refused(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Durable, and de-duplicated because detection re-resolves on every read."""
    _pretend_wsl(monkeypatch)
    monkeypatch.setattr(
        shim_paths.shutil, "which", lambda _command, path=None: INTEROP_CODEX
    )

    with caplog.at_level(logging.WARNING, logger="swe_mux.shim_paths"):
        resolve_executable("codex")
        resolve_executable("codex")

    records = _records(caplog, "swe_mux.shim_paths", logging.WARNING)
    assert len(records) == 1, "a refusal repeated on every registry read would be the whole log"
    assert INTEROP_CODEX in records[0].getMessage()
    assert records[0].reason == "windows_interop"
    assert records[0].rejected == INTEROP_CODEX


def test_a_plain_absence_is_not_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Detection probes every registered harness on a host that has one or two."""
    _only_path(monkeypatch, tmp_path / "empty")

    with caplog.at_level(logging.DEBUG, logger="swe_mux.shim_paths"):
        resolve_executable("codex")

    assert not _records(caplog, "swe_mux.shim_paths", logging.WARNING)
    assert len(_records(caplog, "swe_mux.shim_paths", logging.DEBUG)) == 1


# --- bug 1: the .exe recovery, on the host that needed it --------------------


def _manager(tmp_path: Path, executable: str) -> ProviderAccountManager:
    """A real manager over a real temporary data dir and home - never a fake.

    `home` is pinned inside `tmp_path` so nothing here reads the developer's own
    provider credentials while deciding what to log.
    """
    return ProviderAccountManager(
        tmp_path / "data",
        EventBus(),
        home=tmp_path / "home",
        executables={"codex": executable},
    )


def _records(
    caplog: pytest.LogCaptureFixture, logger: str, level: int
) -> list[logging.LogRecord]:
    """Records from one logger at one level.

    caplog's handler sits on the root logger and therefore sees every propagated
    record, so filtering on level alone would count a `shim_paths` refusal as a
    `provider_accounts` one.
    """
    return [item for item in caplog.records if item.name == logger and item.levelno == level]


def test_a_stale_exe_suffix_is_repaired_on_whichever_host_runs_this(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bug 1, proven against a real file in this host's executable form.

    A config authored on Windows carries `codex.exe` onto a POSIX host, where it
    is certainly wrong; the recovery used to be skipped on exactly that host.
    """
    directory = tmp_path / "bin"
    installed = _install_cli(directory, "codex")
    _only_path(monkeypatch, directory)
    manager = _manager(tmp_path, "codex.exe")

    resolution = manager._resolve_executable("codex")

    assert resolution.usable, "the .exe recovery did not run on this host"
    assert Path(resolution.path or "") == installed


def test_the_exe_recovery_is_not_gated_on_the_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same claim as above, but asserted where the host cannot hide it.

    The recovery is proven by the *lookups it performs*, so this fails on the
    Windows leg too if the platform guard is ever restored - which the file-based
    test above cannot do, because on Windows PATHEXT would have found the answer
    either way.
    """
    looked_up: list[str] = []

    def which(command: str, path: str | None = None) -> str | None:
        looked_up.append(command)
        return "/usr/local/bin/codex" if command == "codex" else None

    monkeypatch.setattr(shim_paths.shutil, "which", which)
    manager = _manager(tmp_path, "codex.exe")

    resolution = manager._resolve_executable("codex")

    assert "codex" in looked_up, "the suffix-stripped form was never tried"
    assert resolution.path == "/usr/local/bin/codex"
    assert manager._spawn_command("codex", ["login"], windows=False) == [
        "/usr/local/bin/codex",
        "login",
    ]


def test_the_comspec_wrapper_is_still_windows_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unguarding the suffix retry must not unguard the batch wrapper with it.

    Driven through the `windows=` parameter so both branches are exercised on
    every host rather than one of them being skipped wherever the gate runs.
    """
    monkeypatch.setattr(
        shim_paths.shutil,
        "which",
        lambda command, path=None: r"C:\npm\codex.cmd" if command == "codex" else None,
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    manager = _manager(tmp_path, "codex.exe")

    assert manager._spawn_command("codex", ["login"], windows=True) == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/c",
        r"C:\npm\codex.cmd",
        "login",
    ]
    # A POSIX host has no COMSPEC and no batch interpreter; the `.cmd` name is
    # just a filename there.
    assert manager._spawn_command("codex", ["login"], windows=False) == [
        r"C:\npm\codex.cmd",
        "login",
    ]


# --- bug 2 + 3 at the provider CLI: the error text, and the log line ---------


def test_a_refused_provider_cli_is_refused_rather_than_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The operator's actual failure, with the message they should have got.

    Executing the configured value anyway is what the old fallback did, and on
    WSL that would run the very binary the resolver had just refused.
    """
    _pretend_wsl(monkeypatch)
    monkeypatch.setattr(
        shim_paths.shutil, "which", lambda command, path=None: INTEROP_CODEX
    )
    manager = _manager(tmp_path, "codex.exe")

    with caplog.at_level(logging.ERROR, logger="swe_mux.provider_accounts"):
        with pytest.raises(ProviderAccountError) as raised:
            manager._spawn_command("codex", ["login"])

    message = str(raised.value)
    assert message.startswith("Could not start codex: ")
    assert INTEROP_CODEX in message
    assert "Install the Linux build of codex inside this distribution" in message
    assert "No such file or directory" not in message

    records = _records(caplog, "swe_mux.provider_accounts", logging.ERROR)
    assert len(records) == 1
    assert records[0].configured == "codex.exe"
    assert records[0].reason == "windows_interop"
    assert records[0].rejected == INTEROP_CODEX


async def test_a_provider_cli_that_cannot_start_says_what_the_search_covered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`Could not start codex: [Errno 2] ... 'codex.exe'` on its own is a dead end."""
    monkeypatch.setattr(shim_paths.shutil, "which", lambda _command, path=None: None)

    async def refuse(*_args: object, **_kwargs: object) -> ProcessOutcome:
        raise FileNotFoundError(2, "No such file or directory", "codex.exe")

    monkeypatch.setattr("swe_mux.provider_accounts.run_bounded", refuse)
    manager = _manager(tmp_path, "codex.exe")

    with caplog.at_level(logging.ERROR, logger="swe_mux.provider_accounts"):
        with pytest.raises(ProviderAccountError) as raised:
            await manager._run_command("codex", ["login"], timeout_seconds=5)

    message = str(raised.value)
    assert "Could not start codex: " in message
    assert '"codex.exe" was not found on PATH (also tried "codex")' in message

    records = _records(caplog, "swe_mux.provider_accounts", logging.ERROR)
    assert len(records) == 1
    assert records[0].provider == "codex"
    assert records[0].configured == "codex.exe"
    assert records[0].reason == "not_found"


async def test_a_failing_provider_command_reaches_the_log_without_its_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Bug 3, and the one thing that must never be logged.

    A provider CLI's stdout is where a token or a credential blob would be, so the
    log carries the exit code and a bounded *stderr* tail only - even when stdout
    is the only thing the failure printed and the operator-facing error therefore
    quotes it.
    """
    directory = tmp_path / "bin"
    _install_cli(directory, "codex")
    _only_path(monkeypatch, directory)

    async def fail(*_args: object, **_kwargs: object) -> ProcessOutcome:
        return ProcessOutcome(
            exit_code=1,
            stdout=b"sk-live-do-not-log-this",
            stderr=b"not logged in",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=1.0,
        )

    monkeypatch.setattr("swe_mux.provider_accounts.run_bounded", fail)
    manager = _manager(tmp_path, "codex")

    with caplog.at_level(logging.WARNING, logger="swe_mux.provider_accounts"):
        with pytest.raises(ProviderAccountError, match="not logged in"):
            await manager._run_command("codex", ["login"], timeout_seconds=5)

    records = _records(caplog, "swe_mux.provider_accounts", logging.WARNING)
    assert len(records) == 1
    assert records[0].exit_code == 1
    assert records[0].stderr_tail == "not logged in"
    assert "sk-live-do-not-log-this" not in str(records[0].__dict__)


async def test_a_provider_command_that_times_out_is_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A login that hung for five minutes left nothing behind at all."""
    directory = tmp_path / "bin"
    _install_cli(directory, "codex")
    _only_path(monkeypatch, directory)

    async def hang(*_args: object, **_kwargs: object) -> ProcessOutcome:
        return ProcessOutcome(
            exit_code=None,
            stdout=b"",
            stderr=b"",
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=300_000.0,
            timed_out=True,
        )

    monkeypatch.setattr("swe_mux.provider_accounts.run_bounded", hang)
    manager = _manager(tmp_path, "codex")

    with caplog.at_level(logging.WARNING, logger="swe_mux.provider_accounts"):
        with pytest.raises(ProviderAccountError, match="timed out"):
            await manager._run_command("codex", ["login"], timeout_seconds=300)

    records = _records(caplog, "swe_mux.provider_accounts", logging.WARNING)
    assert len(records) == 1
    assert records[0].timeout_seconds == 300
    assert records[0].argv == ["login"]


# --- the same two bugs in the shim's own process ----------------------------


def test_the_launcher_repairs_a_stale_exe_on_whichever_host_runs_this(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from swe_mux import agent_launcher

    directory = tmp_path / "bin"
    installed = _install_cli(directory, "codex")
    _only_path(monkeypatch, directory)

    resolution = agent_launcher._resolve_agent_executable("codex.exe")

    assert Path(resolution.path or "") == installed


def test_the_launcher_refuses_an_interop_binary_with_a_reason(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to have no sentence for this at all - the resolver said "nothing"."""
    from swe_mux import agent_launcher

    _pretend_wsl(monkeypatch)
    monkeypatch.setattr(
        shim_paths.shutil, "which", lambda _command, path=None: INTEROP_CODEX
    )
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("must not run a Windows CLI from a Linux daemon"),
    )

    with pytest.raises(SystemExit) as raised:
        agent_launcher._launch("codex.exe", ["--version"])

    message = str(raised.value)
    assert INTEROP_CODEX in message
    assert "Install the Linux build of codex inside this distribution" in message


def test_the_launcher_annotates_a_spawn_that_could_not_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from swe_mux import agent_launcher

    _only_path(monkeypatch, tmp_path / "empty")

    def refuse(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError(2, "No such file or directory", "codex.exe")

    monkeypatch.setattr(agent_launcher.subprocess, "Popen", refuse)

    with pytest.raises(SystemExit) as raised:
        agent_launcher._launch("codex.exe", ["--version"])

    message = str(raised.value)
    assert "could not start" in message
    assert '"codex.exe" was not found on PATH (also tried "codex")' in message


def test_the_launcher_still_refuses_to_relaunch_its_own_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from swe_mux import agent_launcher

    data_dir = tmp_path / "data"
    create_agent_shims(Config(data_dir=data_dir))
    bin_dir = data_dir / "bin"
    shim = bin_dir / f"codex{shim_paths.SHIM_SUFFIX}"
    _only_path(monkeypatch, bin_dir)
    monkeypatch.setattr(
        agent_launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("must not spawn the mux shim"),
    )

    with pytest.raises(SystemExit, match="refusing to relaunch the mux shim"):
        agent_launcher._launch(str(shim), ["login"])
