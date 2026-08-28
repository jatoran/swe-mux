"""Where swe-mux is installed, and whether its commands can be reached.

**Every Windows answer here is asserted from every host.** `install_location`
takes its whole world as arguments - the platform flag, `PATH`, the scripts
directory, the environment, and the existence probe - precisely so the Windows
layouts, which are the ones that matter and the ones the development host cannot
be trusted to represent, are described exactly on the Ubuntu and macOS legs too.
A platform branch whose other side is never asserted is the bug class this whole
work package is cleaning up after, so nothing below is skipped by host.

The two facts under test that a fresh install turns on:

- a launcher that is **absent** and a launcher that is **present but
  unreachable** are different faults with different fixes, and are never
  collapsed into one answer;
- `on_path` is false when nothing shipped, because "no commands exist" must not
  read as "all commands are reachable" - the vacuous truth is the wrong answer to
  the only question anyone asks this.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

from swe_mux import install_location
from swe_mux.install_location import (
    INSTALL_FROZEN,
    INSTALL_PIPX,
    INSTALL_SYSTEM,
    INSTALL_UV_TOOL,
    INSTALL_VENV,
    detect_install_location,
    path_hint_lines,
    render_where,
)

# A uv tool install exactly as uv lays one out on Windows: the environment under
# the tool directory (with its receipt), the entry points inside that
# environment's Scripts, and the shims uv exposes in `~/.local/bin`.
_UV_ROOT = PureWindowsPath(r"C:\Users\ada\AppData\Roaming\uv\tools\swe-mux")
_UV_SCRIPTS = _UV_ROOT / "Scripts"
_UV_HOME = PureWindowsPath(r"C:\Users\ada")
_UV_SHIMS = _UV_HOME / ".local" / "bin"


def _windows_layout(present: set[str]) -> object:
    """An existence probe over an explicit set of Windows paths.

    Compared casefolded with backslashes normalized, so a test states the paths
    the way Windows writes them and the probe answers the way Windows would.
    """
    normalized = {str(PureWindowsPath(entry)).casefold() for entry in present}

    def exists(candidate: Path) -> bool:
        return str(PureWindowsPath(str(candidate))).casefold() in normalized

    return exists


_POSIX_ROOT = "/opt/swe-mux"
_POSIX_BIN = f"{_POSIX_ROOT}/bin"


def _posix_install(*, path: str, case_insensitive_probe: bool = False) -> object:
    """A POSIX prefix install, described the same way from any host.

    `Path` renders separators for the *running* platform, so a POSIX layout is
    kept as text and only converted where a `Path` is required. The probe
    compares text for the same reason.
    """

    def exists(candidate: Path) -> bool:
        text = str(candidate).replace("\\", "/")
        if case_insensitive_probe:
            return text.casefold().startswith(_POSIX_BIN.casefold())
        return text.startswith(_POSIX_BIN)

    return detect_install_location(
        frozen=False,
        executable=f"{_POSIX_BIN}/python",
        package_dir=Path(f"{_POSIX_ROOT}/lib/swe_mux"),
        prefix=_POSIX_ROOT,
        base_prefix="/usr",
        scripts_dir=Path(_POSIX_BIN),
        path=path,
        home=Path("/home/ada"),
        environ={},
        windows=False,
        exists=exists,
    )


def _uv_tool_install(*, path: str, shims_populated: bool = True) -> object:
    present = {str(_UV_ROOT / "uv-receipt.toml")}
    for name in ("mux.exe", "muxd.exe", "swe-mux.exe"):
        present.add(str(_UV_SCRIPTS / name))
        if shims_populated:
            present.add(str(_UV_SHIMS / name))
    return detect_install_location(
        frozen=False,
        executable=str(_UV_SCRIPTS / "python.exe"),
        package_dir=Path(str(_UV_ROOT / "Lib" / "site-packages" / "swe_mux")),
        prefix=str(_UV_ROOT),
        base_prefix=r"C:\Python312",
        scripts_dir=Path(str(_UV_SCRIPTS)),
        path=path,
        home=Path(str(_UV_HOME)),
        environ={},
        windows=True,
        exists=_windows_layout(present),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# Install method
# --------------------------------------------------------------------------- #


def test_a_uv_tool_environment_is_named_by_its_receipt() -> None:
    location = _uv_tool_install(path=r"C:\Windows")
    assert location.kind == INSTALL_UV_TOOL
    assert location.label == "uv tool install"
    # The command uv itself ships, not a hand-written PATH edit: telling a uv
    # user to `setx` would leave uv's own shim directory unregistered.
    assert location.path_fix_lines() == ["uv tool update-shell"]


def test_a_pipx_environment_is_named_by_its_metadata() -> None:
    root = PureWindowsPath(r"C:\Users\ada\pipx\venvs\swe-mux")
    location = detect_install_location(
        frozen=False,
        executable=str(root / "Scripts" / "python.exe"),
        package_dir=Path(str(root / "Lib" / "site-packages" / "swe_mux")),
        prefix=str(root),
        base_prefix=r"C:\Python312",
        scripts_dir=Path(str(root / "Scripts")),
        path="",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout({str(root / "pipx_metadata.json")}),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_PIPX
    assert location.path_fix_lines() == ["pipx ensurepath"]


def test_a_plain_venv_is_a_venv_and_gets_a_literal_path_line() -> None:
    root = PureWindowsPath(r"C:\work\.venv")
    location = detect_install_location(
        frozen=False,
        executable=str(root / "Scripts" / "python.exe"),
        package_dir=Path(str(root / "Lib" / "site-packages" / "swe_mux")),
        prefix=str(root),
        base_prefix=r"C:\Python312",
        scripts_dir=Path(str(root / "Scripts")),
        path="",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout(set()),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_VENV
    # No tool owns this install, so the advice has to be the literal command for
    # the shell the user is in - "add it to your PATH" is the answer that sends
    # someone to a search engine.
    assert location.path_fix_lines()[0] == f'setx PATH "%PATH%;{root / "Scripts"}"'


def test_an_interpreter_that_is_its_own_base_is_a_system_install() -> None:
    root = PureWindowsPath(r"C:\Python312")
    location = detect_install_location(
        frozen=False,
        executable=str(root / "python.exe"),
        package_dir=Path(str(root / "Lib" / "site-packages" / "swe_mux")),
        prefix=str(root),
        base_prefix=str(root),
        scripts_dir=Path(str(root / "Scripts")),
        path="",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout(set()),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_SYSTEM


def test_a_frozen_bundle_reports_its_own_directory_as_the_launcher_home() -> None:
    """A PyInstaller bundle has no scripts directory; the exe beside it is it."""
    bundle = PureWindowsPath(r"C:\Users\ada\swe-mux\dist\swe-mux")
    location = detect_install_location(
        frozen=True,
        executable=str(bundle / "swe-mux.exe"),
        package_dir=Path(str(bundle / "_internal" / "swe_mux")),
        path="",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout({str(bundle / "swe-mux.exe")}),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_FROZEN
    assert location.bin_dir == Path(str(bundle))
    assert location.executable("swe-mux") == Path(str(bundle / "swe-mux.exe"))


def test_a_uv_tool_shim_directory_can_be_relocated_by_its_variable() -> None:
    elsewhere = PureWindowsPath(r"D:\bin")
    present = {str(_UV_ROOT / "uv-receipt.toml"), str(elsewhere / "mux.exe")}
    location = detect_install_location(
        frozen=False,
        executable=str(_UV_SCRIPTS / "python.exe"),
        package_dir=Path(str(_UV_ROOT)),
        prefix=str(_UV_ROOT),
        base_prefix=r"C:\Python312",
        scripts_dir=Path(str(_UV_SCRIPTS)),
        path="",
        home=Path(str(_UV_HOME)),
        environ={"UV_TOOL_BIN_DIR": str(elsewhere)},
        windows=True,
        exists=_windows_layout(present),  # type: ignore[arg-type]
    )
    assert location.shim_dir == Path(str(elsewhere))


def test_a_shim_directory_with_none_of_our_commands_is_not_reported() -> None:
    """Reported when a launcher is *there*, never because a tool might use it.

    A `~/.local/bin` that exists for some other tool says nothing about where
    swe-mux went, and naming it would send a reader to an empty directory.
    """
    location = _uv_tool_install(path="", shims_populated=False)
    assert location.shim_dir is None
    assert location.bin_dir == Path(str(_UV_SCRIPTS))


# --------------------------------------------------------------------------- #
# PATH
# --------------------------------------------------------------------------- #


def test_commands_in_a_directory_that_is_not_on_path_are_unreachable() -> None:
    location = _uv_tool_install(path=r"C:\Windows;C:\Windows\System32")
    assert location.on_path is False
    assert [command.name for command in location.unreachable] == ["mux", "muxd", "swe-mux"]
    assert all(command.status == "not on PATH" for command in location.unreachable)


def test_commands_in_a_directory_that_is_on_path_are_reachable() -> None:
    location = _uv_tool_install(path=rf"C:\Windows;{_UV_SHIMS}")
    assert location.on_path is True
    assert location.unreachable == ()


def test_path_matching_ignores_case_on_windows_and_not_elsewhere() -> None:
    """`C:\\USERS\\ADA\\.LOCAL\\BIN` is the same directory; on POSIX it is not."""
    shouted = str(_UV_SHIMS).upper()
    assert _uv_tool_install(path=shouted).on_path is True

    # The probe answers yes to both spellings, so the case rule in `_same_path`
    # is what the assertion is about rather than a missing file.
    posix = _posix_install(path="/OPT/SWE-MUX/BIN", case_insensitive_probe=True)
    mux = posix.command("mux")
    assert mux is not None
    assert mux.resolved is not None, "the probe must find the shouted spelling"
    assert posix.on_path is False


def test_a_different_install_earlier_on_path_is_named_as_a_shadow() -> None:
    """Reachable, wrong copy. The state that has someone debugging a version
    they are not running, and it must not render as plain "not on PATH"."""
    other = PureWindowsPath(r"C:\Python312\Scripts")
    present = {str(_UV_ROOT / "uv-receipt.toml")}
    for name in ("mux.exe", "muxd.exe", "swe-mux.exe"):
        present.add(str(_UV_SCRIPTS / name))
        present.add(str(other / name))
    location = detect_install_location(
        frozen=False,
        executable=str(_UV_SCRIPTS / "python.exe"),
        package_dir=Path(str(_UV_ROOT)),
        prefix=str(_UV_ROOT),
        base_prefix=r"C:\Python312",
        scripts_dir=Path(str(_UV_SCRIPTS)),
        path=str(other),
        home=Path(str(_UV_HOME)),
        environ={},
        windows=True,
        exists=_windows_layout(present),  # type: ignore[arg-type]
    )
    assert location.on_path is False
    mux = location.command("mux")
    assert mux is not None
    assert mux.resolved == Path(str(other / "mux.exe"))
    assert mux.status == f"shadowed by {other / 'mux.exe'}"


def test_an_install_that_shipped_no_commands_is_not_reported_as_reachable() -> None:
    """The vacuous truth guard: `all([])` is True and would be the wrong answer."""
    location = detect_install_location(
        frozen=False,
        executable=r"C:\work\.venv\Scripts\python.exe",
        package_dir=Path(r"C:\work\.venv\Lib\site-packages\swe_mux"),
        prefix=r"C:\work\.venv",
        base_prefix=r"C:\Python312",
        scripts_dir=Path(r"C:\work\.venv\Scripts"),
        path=r"C:\work\.venv\Scripts",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout(set()),  # type: ignore[arg-type]
    )
    assert location.installed == ()
    assert location.on_path is False
    assert location.executable("swe-mux") is None


def test_a_missing_launcher_is_never_reported_as_merely_unreachable() -> None:
    present = {str(_UV_ROOT / "uv-receipt.toml"), str(_UV_SCRIPTS / "mux.exe")}
    location = detect_install_location(
        frozen=False,
        executable=str(_UV_SCRIPTS / "python.exe"),
        package_dir=Path(str(_UV_ROOT)),
        prefix=str(_UV_ROOT),
        base_prefix=r"C:\Python312",
        scripts_dir=Path(str(_UV_SCRIPTS)),
        path="",
        home=Path(str(_UV_HOME)),
        environ={},
        windows=True,
        exists=_windows_layout(present),  # type: ignore[arg-type]
    )
    desktop = location.command("swe-mux")
    assert desktop is not None
    assert desktop.path is None
    assert desktop.status == "not installed"
    # ... and it is not counted among the things a PATH edit would fix.
    assert [command.name for command in location.unreachable] == ["mux"]


# --------------------------------------------------------------------------- #
# What the surfaces print
# --------------------------------------------------------------------------- #


def test_the_daemon_hint_is_silent_when_everything_resolves() -> None:
    """It costs a healthy install nothing, which is what keeps it worth reading."""
    assert path_hint_lines(_uv_tool_install(path=str(_UV_SHIMS))) == []


def test_the_daemon_hint_is_silent_for_a_frozen_app() -> None:
    bundle = PureWindowsPath(r"C:\Users\ada\swe-mux\dist\swe-mux")
    frozen = detect_install_location(
        frozen=True,
        executable=str(bundle / "swe-mux.exe"),
        package_dir=Path(str(bundle)),
        path="",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout({str(bundle / "swe-mux.exe")}),  # type: ignore[arg-type]
    )
    assert path_hint_lines(frozen) == []


def test_the_daemon_hint_carries_the_directory_the_fix_and_the_fallback() -> None:
    """Three concrete things, and nothing that needs a document to act on."""
    lines = path_hint_lines(_uv_tool_install(path=r"C:\Windows"))
    text = "\n".join(lines)
    assert str(_UV_SHIMS) in text
    assert "uv tool update-shell" in text
    assert "-m swe_mux" in text
    assert "--where" in text
    # Short enough to be read at the moment a daemon is starting.
    assert len(lines) <= 6


def test_where_answers_the_four_questions_it_exists_for() -> None:
    text = render_where(_uv_tool_install(path=r"C:\Windows"), version="9.9.9")
    assert "swe-mux 9.9.9" in text
    assert "uv tool install" in text  # how it got here
    assert str(_UV_SHIMS) in text  # where the commands are
    assert "on PATH        no" in text  # whether they are reachable
    assert "-m swe_mux" in text  # how to run the daemon anyway
    assert "install-shortcut" in text  # how to get a Start Menu entry


def test_where_does_not_offer_shortcuts_off_windows() -> None:
    """`mux install-shortcut` is Windows-only, so POSIX is not told to run it."""
    posix = _posix_install(path="/usr/bin")
    text = render_where(posix, version="9.9.9")
    assert "install-shortcut" not in text
    # The POSIX shell line, not `setx`.
    assert posix.path_fix_lines()[0] == f'export PATH="$PATH:{posix.bin_dir}"'
    assert posix.path_fix_lines()[0] in text
    assert "setx" not in text


def test_where_names_the_missing_version_rather_than_printing_none() -> None:
    """Unreadable metadata is said out loud, never rendered as the word `None`.

    `version` has no default for the same reason: "the caller did not say" and
    "the metadata is unreadable" would otherwise be the same argument.
    """
    text = render_where(_uv_tool_install(path=""), version=None)
    assert "None" not in text.splitlines()[0]
    assert "version metadata unavailable" in text.splitlines()[0]


def test_a_path_with_a_space_is_quoted_for_the_shell_it_is_pasted_into() -> None:
    windows = detect_install_location(
        frozen=False,
        executable=r"C:\Program Files\Python312\python.exe",
        package_dir=Path(r"C:\Program Files\Python312\Lib\site-packages\swe_mux"),
        prefix=r"C:\Program Files\Python312",
        base_prefix=r"C:\Program Files\Python312",
        scripts_dir=Path(r"C:\Program Files\Python312\Scripts"),
        path="",
        home=Path(r"C:\Users\ada"),
        environ={},
        windows=True,
        exists=_windows_layout(set()),  # type: ignore[arg-type]
    )
    assert windows.module_fallback.startswith('"C:\\Program Files\\')
    assert windows.module_fallback.endswith('" -m swe_mux')


# --------------------------------------------------------------------------- #
# The live defaults
# --------------------------------------------------------------------------- #


def test_the_live_probe_describes_this_very_interpreter() -> None:
    """The defaults are wired to the real interpreter, not merely defaulted.

    Everything above injects its world, so this is the one assertion that the
    zero-argument call reads the process it is running in - without it, a typo
    in a default would pass every other test in this file.
    """
    location = detect_install_location()
    assert location.package_dir == Path(install_location.__file__).resolve().parent
    assert location.kind in {
        INSTALL_FROZEN,
        INSTALL_PIPX,
        INSTALL_SYSTEM,
        INSTALL_UV_TOOL,
        INSTALL_VENV,
    }
    # The suite runs from a checkout's own virtual environment.
    assert location.kind == INSTALL_VENV
    assert location.executable("muxd") is not None


def test_every_declared_command_is_one_pyproject_actually_ships() -> None:
    """The launcher table cannot drift from the entry points that build them.

    A name added to `[project.scripts]` and forgotten here would be invisible to
    the PATH check, the hint, and `--where` - which is the exact failure mode of
    a hand-maintained list beside a generated one.
    """
    import tomllib

    root = Path(__file__).resolve().parent.parent
    manifest = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    console = set(manifest["project"]["scripts"])
    gui = set(manifest["project"]["gui-scripts"])
    declared = {name: launcher for name, launcher in install_location.SHIPPED_COMMANDS}
    assert declared == {**dict.fromkeys(console, "console"), **dict.fromkeys(gui, "gui")}
