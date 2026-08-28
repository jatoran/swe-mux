"""`muxd`'s two install-facing surfaces: the first-run PATH hint and `--where`.

Both exist for one person - the one who installed swe-mux, found no command, and
had nothing to run that would tell them why. Two properties follow and are what
these tests hold.

`--where` **must work when nothing else does**, so it is answered before the
config is touched and before logging is set up. A `--where` that first refused
over `invalid config:` would be useless in exactly the case it exists for.

The hint **must be silent when nothing is wrong**. A block printed on every start
is a block nobody reads, so a healthy install pays nothing for it and the warning
keeps its meaning.

Nothing here starts a daemon, binds a port, or spawns anything.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from swe_mux import __main__ as daemon_main
from swe_mux import install_location
from swe_mux.config import Config
from swe_mux.host_platform import IS_WINDOWS

# A uv tool install as uv lays one out on *this* host. Shaped for the running
# platform, never always for Windows: `install_location` joins and splits paths
# with `pathlib` and `os.path`, which render for the host, so a Windows string on
# a Linux runner is one relative filename and the hint below then describes
# nothing. Its module docstring has the full account.
if IS_WINDOWS:
    _HOME = Path(r"C:\Users\ada")
    _ROOT = _HOME / "AppData" / "Roaming" / "uv" / "tools" / "swe-mux"
    _SCRIPTS = _ROOT / "Scripts"
    _BASE_PREFIX = r"C:\Python312"
    _NOISE = r"C:\Windows"
    _EXE = ".exe"
else:
    _HOME = Path("/home/ada")
    _ROOT = _HOME / ".local" / "share" / "uv" / "tools" / "swe-mux"
    _SCRIPTS = _ROOT / "bin"
    _BASE_PREFIX = "/usr"
    _NOISE = "/usr/bin"
    _EXE = ""


def _use_install(monkeypatch: pytest.MonkeyPatch, *, reachable: bool) -> None:
    """Pin the detector to a described install.

    Built eagerly and captured, never called from inside the replacement: a
    lambda that re-entered `detect_install_location` would find the stub that
    replaced it.
    """
    location = _install(reachable=reachable)
    monkeypatch.setattr(install_location, "detect_install_location", lambda: location)


def _key(entry: str) -> str:
    text = os.path.normpath(entry)
    return text.casefold() if IS_WINDOWS else text


def _install(*, reachable: bool) -> object:
    present = {str(_ROOT / "uv-receipt.toml")} | {
        str(_SCRIPTS / f"{name}{_EXE}") for name in ("mux", "muxd", "swe-mux")
    }
    normalized = {_key(entry) for entry in present}
    return install_location.detect_install_location(
        frozen=False,
        executable=str(_SCRIPTS / f"python{_EXE}"),
        package_dir=_ROOT,
        prefix=str(_ROOT),
        base_prefix=_BASE_PREFIX,
        scripts_dir=_SCRIPTS,
        path=str(_SCRIPTS) if reachable else _NOISE,
        home=_HOME,
        environ={},
        exists=lambda candidate: _key(str(candidate)) in normalized,
    )


# --------------------------------------------------------------------------- #
# --where
# --------------------------------------------------------------------------- #


def test_where_is_answered_before_the_config_is_even_looked_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of the flag. A broken config is one of the states someone
    runs it in, so a load must not come first."""

    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("--where must not load the config")

    monkeypatch.setattr("swe_mux.config.load_config", _forbidden)
    monkeypatch.setattr(
        "swe_mux.logsetup.setup_daemon_logging", _forbidden, raising=True
    )
    _use_install(monkeypatch, reachable=False)
    monkeypatch.setattr(install_location, "installed_version", lambda: "1.2.3")
    daemon_main.main(["--where"])
    out = capsys.readouterr().out
    assert "swe-mux 1.2.3" in out
    assert "uv tool install" in out
    assert str(_SCRIPTS) in out
    assert "-m swe_mux" in out


def test_where_is_declared_on_the_daemon_parser_so_it_is_discoverable() -> None:
    """`muxd --help` has to name it, or the escape hatch is a secret."""
    text = daemon_main.parser().format_help()
    assert "--where" in text
    assert "on PATH" in text


def test_the_config_resolver_is_still_one_function_with_its_old_contract() -> None:
    """`load_daemon_config` was split, not changed: `main` needs the parsed
    arguments before the config exists, and every other caller must not care."""
    config, args = daemon_main.load_daemon_config(["--port", "18765", "--local-only"])
    assert isinstance(config, Config)
    assert config.port == 18765
    assert config.tailnet_enabled is False
    assert args.local_only is True


def test_a_host_that_is_not_loopback_is_still_refused_after_the_split(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        daemon_main.load_daemon_config(["--host", "0.0.0.0"])
    assert "must be loopback" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# The first-run PATH hint
# --------------------------------------------------------------------------- #


def test_a_healthy_install_is_told_nothing_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_install(monkeypatch, reachable=True)
    daemon_main._print_path_hint(Config(data_dir=tmp_path, host="127.0.0.1"))
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "lifecycle.log").exists()


def test_an_unreachable_install_is_told_where_it_is_and_what_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_install(monkeypatch, reachable=False)
    daemon_main._print_path_hint(Config(data_dir=tmp_path, host="127.0.0.1"))
    out = capsys.readouterr().out
    assert str(_SCRIPTS) in out
    assert "uv tool update-shell" in out
    assert "--where" in out


def test_the_hint_also_reaches_the_log_and_the_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The terminal it printed to is gone by the time anyone investigates.

    The structured fields matter as much as the sentence: `daemon.log`'s
    formatter carries `extra=` keywords, so the install kind and the directory
    are queryable rather than only readable.
    """
    _use_install(monkeypatch, reachable=False)
    config = Config(data_dir=tmp_path, host="127.0.0.1")
    with caplog.at_level(logging.WARNING, logger="swe_mux.__main__"):
        daemon_main._print_path_hint(config)
    capsys.readouterr()
    record = next(row for row in caplog.records if "not reachable from PATH" in row.getMessage())
    assert record.install_kind == "uv-tool"  # type: ignore[attr-defined]
    assert record.unreachable == "mux,muxd,swe-mux"  # type: ignore[attr-defined]
    assert "not on PATH" in (tmp_path / "lifecycle.log").read_text(encoding="utf-8")


def test_a_probe_that_raises_never_stops_a_daemon_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A diagnostic is never the reason a daemon does not start."""

    def _boom() -> object:
        raise OSError("the home directory is gone")

    monkeypatch.setattr(install_location, "detect_install_location", _boom)
    daemon_main._print_path_hint(Config(data_dir=tmp_path, host="127.0.0.1"))
    assert capsys.readouterr().out == ""
