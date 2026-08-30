"""Where swe-mux is installed, and whether its commands can be reached.

**Every fixture here is shaped for the host that is running**, and that is the
correction this file exists in its current form to record. It used to hand the
detector Windows path strings from every leg on the theory that
``detect_install_location(windows=True, ...)`` describes a Windows install
exactly from anywhere. It does not, and cannot: `Path` renders separators for the
platform that is *running*, `os.pathsep` is ``;`` on Windows and ``:`` everywhere
else, and `os.path.normpath` only collapses backslashes on Windows. So on a Linux
runner ``Path(r"C:\\Users\\ada\\.local\\bin")`` is one *relative* filename that
happens to contain backslashes, ``"C:\\Windows;C:\\Users\\ada\\.local\\bin"``
splits into three nonsense entries, and every derived answer is wrong - the
assertion then measures the fixture rather than the code. Thirteen tests failed
that way on both POSIX legs while the Windows gate stayed green.

The rule that replaces it: **each host describes the install its own users get**,
so the Ubuntu and macOS legs now assert the POSIX layout they were never
covering, and the Windows leg still asserts every Windows answer. Where a
behaviour genuinely differs by platform - `_same_path`'s case rule, the
``setx`` / ``export`` line, whether ``install-shortcut`` is offered - the
assertion names the host's own answer rather than pretending one host can speak
for another.

``windows=`` is still passed explicitly in a few places below, and only ever for
an assertion that is **separator-independent** (how a path with a space is
quoted, which PATH-fix line is emitted). Anything that joins, splits, or compares
a path must be given host-shaped input.

The two facts under test that a fresh install turns on:

- a launcher that is **absent** and a launcher that is **present but
  unreachable** are different faults with different fixes, and are never
  collapsed into one answer;
- `on_path` is false when nothing shipped, because "no commands exist" must not
  read as "all commands are reachable" - the vacuous truth is the wrong answer to
  the only question anyone asks this.
"""

from __future__ import annotations

import os
from pathlib import Path

from swe_mux import install_location
from swe_mux.host_platform import IS_WINDOWS
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

#: The launcher suffix the installer writes on this host, and the separator this
#: host's `PATH` uses. Both are read from the running platform for the same
#: reason every path below is: the detector reads them from `os.path`/`os.pathsep`
#: and cannot be told otherwise.
_EXE = ".exe" if IS_WINDOWS else ""
_PATHSEP = os.pathsep

# A uv tool install exactly as uv lays one out on *this* host: the environment
# under the tool directory (with its receipt), the entry points inside that
# environment's scripts directory, and the shims uv exposes in `~/.local/bin`.
if IS_WINDOWS:
    _HOME = Path(r"C:\Users\ada")
    _UV_ROOT = _HOME / "AppData" / "Roaming" / "uv" / "tools" / "swe-mux"
    _UV_SCRIPTS = _UV_ROOT / "Scripts"
    _SITE_PACKAGES = _UV_ROOT / "Lib" / "site-packages" / "swe_mux"
    _INTERPRETER = _UV_SCRIPTS / "python.exe"
    _BASE_PREFIX = r"C:\Python312"
    #: A `PATH` entry that holds none of our launchers.
    _NOISE = r"C:\Windows"
    #: A second, complete swe-mux install - the one that shadows ours.
    _OTHER_SCRIPTS = Path(r"C:\Python312\Scripts")
    #: Somewhere `UV_TOOL_BIN_DIR` could point that is not the default.
    _ELSEWHERE = Path(r"D:\bin")
    _BUNDLE = _HOME / "swe-mux" / "dist" / "swe-mux"
    _SPACED_PREFIX = Path(r"C:\Program Files\Python312")
else:
    _HOME = Path("/home/ada")
    _UV_ROOT = _HOME / ".local" / "share" / "uv" / "tools" / "swe-mux"
    _UV_SCRIPTS = _UV_ROOT / "bin"
    _SITE_PACKAGES = _UV_ROOT / "lib" / "python3.12" / "site-packages" / "swe_mux"
    _INTERPRETER = _UV_SCRIPTS / "python"
    _BASE_PREFIX = "/usr"
    _NOISE = "/usr/bin"
    _OTHER_SCRIPTS = Path("/usr/local/bin")
    _ELSEWHERE = Path("/opt/bin")
    _BUNDLE = _HOME / "swe-mux" / "dist" / "swe-mux"
    _SPACED_PREFIX = Path("/opt/swe mux")

#: Both tools default here, on every platform they support.
_UV_SHIMS = _HOME / ".local" / "bin"

_LAUNCHERS = tuple(f"{name}{_EXE}" for name in ("mux", "muxd", "swe-mux"))


def _key(entry: str, *, case_insensitive: bool) -> str:
    """The comparison key a filesystem on this host would use for `entry`."""
    text = os.path.normpath(entry)
    return text.casefold() if case_insensitive else text


def _layout(present: set[str], *, case_insensitive: bool | None = None) -> object:
    """An existence probe over an explicit set of paths on this host.

    Normalized before comparing, so a fixture may state a path however it reads
    best and the probe still answers the way the platform would. `Path` joining
    renders this host's separator, which is exactly why the paths handed in have
    to be this host's too.

    `case_insensitive` defaults to what the host actually does. The one test that
    overrides it needs the probe to answer *yes* to a shouted spelling on POSIX,
    so that the assertion is about `_same_path`'s case rule rather than about a
    file the probe could not find.
    """
    folded = IS_WINDOWS if case_insensitive is None else case_insensitive
    normalized = {_key(entry, case_insensitive=folded) for entry in present}

    def exists(candidate: Path) -> bool:
        return _key(str(candidate), case_insensitive=folded) in normalized

    return exists


_POSIX_ROOT = "/opt/swe-mux"
_POSIX_BIN = f"{_POSIX_ROOT}/bin"


def _posix_install(*, path: str, case_insensitive_probe: bool = False) -> object:
    """A POSIX prefix install, described the same way from any host.

    The one deliberate cross-platform description left in this file, and it works
    only because every path in it is kept as text and the probe compares text: a
    POSIX layout carries no `;`, so `os.pathsep` cannot mangle it, and the probe
    normalizes the backslashes `Path` would introduce on a Windows host. It earns
    that care by covering the POSIX rendering rules (`export PATH=`, no
    `install-shortcut`) on the Windows leg, which is the leg that ships them.
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


def _uv_tool_install(
    *,
    path: str,
    shims_populated: bool = True,
    case_insensitive_probe: bool | None = None,
) -> object:
    present = {str(_UV_ROOT / "uv-receipt.toml")}
    for name in _LAUNCHERS:
        present.add(str(_UV_SCRIPTS / name))
        if shims_populated:
            present.add(str(_UV_SHIMS / name))
    return detect_install_location(
        frozen=False,
        executable=str(_INTERPRETER),
        package_dir=_SITE_PACKAGES,
        prefix=str(_UV_ROOT),
        base_prefix=_BASE_PREFIX,
        scripts_dir=_UV_SCRIPTS,
        path=path,
        home=_HOME,
        environ={},
        exists=_layout(present, case_insensitive=case_insensitive_probe),  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# Install method
# --------------------------------------------------------------------------- #


def test_a_uv_tool_environment_is_named_by_its_receipt() -> None:
    location = _uv_tool_install(path=_NOISE)
    assert location.kind == INSTALL_UV_TOOL
    assert location.label == "uv tool install"
    # The command uv itself ships, not a hand-written PATH edit: telling a uv
    # user to `setx` would leave uv's own shim directory unregistered.
    assert location.path_fix_lines() == ["uv tool update-shell"]


def test_a_pipx_environment_is_named_by_its_metadata() -> None:
    root = _HOME / "pipx" / "venvs" / "swe-mux"
    scripts = root / ("Scripts" if IS_WINDOWS else "bin")
    location = detect_install_location(
        frozen=False,
        executable=str(scripts / f"python{_EXE}"),
        package_dir=root / "swe_mux",
        prefix=str(root),
        base_prefix=_BASE_PREFIX,
        scripts_dir=scripts,
        path="",
        home=_HOME,
        environ={},
        exists=_layout({str(root / "pipx_metadata.json")}),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_PIPX
    assert location.path_fix_lines() == ["pipx ensurepath"]


def test_a_plain_venv_is_a_venv_and_gets_a_literal_path_line() -> None:
    root = _HOME / "work" / ".venv"
    scripts = root / ("Scripts" if IS_WINDOWS else "bin")
    location = detect_install_location(
        frozen=False,
        executable=str(scripts / f"python{_EXE}"),
        package_dir=root / "swe_mux",
        prefix=str(root),
        base_prefix=_BASE_PREFIX,
        scripts_dir=scripts,
        path="",
        home=_HOME,
        environ={},
        exists=_layout(set()),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_VENV
    # No tool owns this install, so the advice has to be the literal command for
    # the shell the user is in - "add it to your PATH" is the answer that sends
    # someone to a search engine. Which literal it is, is the host's own answer.
    expected = (
        f'setx PATH "%PATH%;{scripts}"'
        if IS_WINDOWS
        else f'export PATH="$PATH:{scripts}"'
    )
    assert location.path_fix_lines()[0] == expected


def test_both_path_fix_renderings_are_asserted_from_every_host() -> None:
    """`setx` and `export` are both shipped, so both are checked everywhere.

    Safe to describe a foreign platform here only because the assertion is about
    the *shape of the line*, and `bin_dir` is interpolated verbatim: nothing
    joins, splits, or compares a path, so the running platform's separator rules
    never enter into it. That is the whole licence for a `windows=` override in
    this file.
    """
    for windows, prefix in ((True, "setx PATH "), (False, 'export PATH="$PATH:')):
        described = detect_install_location(
            frozen=False,
            executable="python",
            package_dir=Path("swe_mux"),
            prefix="env",
            base_prefix="base",
            scripts_dir=Path("scripts"),
            path="",
            home=Path("home"),
            environ={},
            windows=windows,
            exists=_layout(set()),  # type: ignore[arg-type]
        )
        assert described.path_fix_lines()[0].startswith(prefix)
        assert len(described.path_fix_lines()) == 2, "the line, then what it means"


def test_an_interpreter_that_is_its_own_base_is_a_system_install() -> None:
    root = Path(r"C:\Python312") if IS_WINDOWS else Path("/usr")
    scripts = root / ("Scripts" if IS_WINDOWS else "bin")
    location = detect_install_location(
        frozen=False,
        executable=str(scripts / f"python{_EXE}"),
        package_dir=root / "swe_mux",
        prefix=str(root),
        base_prefix=str(root),
        scripts_dir=scripts,
        path="",
        home=_HOME,
        environ={},
        exists=_layout(set()),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_SYSTEM


def test_a_frozen_bundle_reports_its_own_directory_as_the_launcher_home() -> None:
    """A PyInstaller bundle has no scripts directory; the exe beside it is it."""
    launcher = _BUNDLE / f"swe-mux{_EXE}"
    location = detect_install_location(
        frozen=True,
        executable=str(launcher),
        package_dir=_BUNDLE / "_internal" / "swe_mux",
        path="",
        home=_HOME,
        environ={},
        exists=_layout({str(launcher)}),  # type: ignore[arg-type]
    )
    assert location.kind == INSTALL_FROZEN
    assert location.bin_dir == _BUNDLE
    assert location.executable("swe-mux") == launcher


#: The console client's bundle, laid out beside the app's exactly as `dist/` and
#: the Windows installer's `{app}` both have it.
_CLI_BUNDLE = _BUNDLE.parent / "swe-mux-cli"


def test_the_console_client_bundle_describes_the_install_not_its_own_directory() -> None:
    """A `swemux` shipped by the installer must find the app bundle beside it.

    The installer lays three sibling bundles under one `{app}` because
    `supervisor_client.dedicated_supervisor_exe` resolves the supervisor that way.
    Without searching them, `swemux install-shortcut` would report that this
    install has no `swe-mux` to point a shortcut at - false about the install and
    true only about the directory it happened to be looking in.
    """
    client = _CLI_BUNDLE / f"swemux{_EXE}"
    alias = _CLI_BUNDLE / f"mux{_EXE}"
    app = _BUNDLE / f"swe-mux{_EXE}"
    location = detect_install_location(
        frozen=True,
        executable=str(client),
        package_dir=_CLI_BUNDLE / "_internal" / "swe_mux",
        path="",
        home=_HOME,
        environ={},
        exists=_layout({str(client), str(alias), str(app)}),  # type: ignore[arg-type]
    )
    assert location.client_bundle is True
    assert location.executable("swemux") == client
    assert location.executable("mux") == alias
    assert location.executable("swe-mux") == app
    # The daemon launchers are console scripts a wheel install ships and no frozen
    # bundle does; reporting them as present would be an invented capability.
    assert location.executable("swemuxd") is None


def test_the_app_bundle_is_not_the_console_client() -> None:
    """`client_bundle` is decided by which executable is running, not by layout.

    Both bundles share a package, a version and a parent directory. What
    distinguishes them is that one is `swemux`/`mux` and the other is `swe-mux`,
    and `doctor` branches on the answer to avoid reporting a correct install as
    three critical faults.
    """
    client = _CLI_BUNDLE / f"swemux{_EXE}"
    app = _BUNDLE / f"swe-mux{_EXE}"
    location = detect_install_location(
        frozen=True,
        executable=str(app),
        package_dir=_BUNDLE / "_internal" / "swe_mux",
        path="",
        home=_HOME,
        environ={},
        exists=_layout({str(client), str(app)}),  # type: ignore[arg-type]
    )
    assert location.client_bundle is False
    # It still sees the client beside it, which is what lets the app's own doctor
    # report whether the commands a user types are installed at all.
    assert location.executable("swemux") == client


def test_a_client_bundle_standing_alone_invents_no_sibling() -> None:
    """Copied somewhere on its own, it describes what is there and nothing more.

    The sibling directories are candidates, not claims: each launcher is still
    proven by the existence probe, so an absent app bundle reports as absent
    rather than as a path that would be right if it existed.
    """
    client = _CLI_BUNDLE / f"swemux{_EXE}"
    location = detect_install_location(
        frozen=True,
        executable=str(client),
        package_dir=_CLI_BUNDLE / "_internal" / "swe_mux",
        path="",
        home=_HOME,
        environ={},
        exists=_layout({str(client)}),  # type: ignore[arg-type]
    )
    assert location.client_bundle is True
    assert location.executable("swe-mux") is None


def test_an_installed_wheel_is_never_a_client_bundle() -> None:
    """`client_bundle` is a fact about a frozen artifact, so nothing else may claim it.

    A `uv tool` install ships `swemux` too, and its `swemux` is not a bundle that
    deliberately omits the daemon - `swe_mux.server` is right there beside it. If
    this were decided by the launcher name alone, `doctor` would report every
    wheel install's daemon checks as unavailable.
    """
    location = _uv_tool_install(path=str(_UV_SHIMS))
    assert location.client_bundle is False


def test_a_uv_tool_shim_directory_can_be_relocated_by_its_variable() -> None:
    present = {
        str(_UV_ROOT / "uv-receipt.toml"),
        str(_ELSEWHERE / f"mux{_EXE}"),
    }
    location = detect_install_location(
        frozen=False,
        executable=str(_INTERPRETER),
        package_dir=_UV_ROOT,
        prefix=str(_UV_ROOT),
        base_prefix=_BASE_PREFIX,
        scripts_dir=_UV_SCRIPTS,
        path="",
        home=_HOME,
        environ={"UV_TOOL_BIN_DIR": str(_ELSEWHERE)},
        exists=_layout(present),  # type: ignore[arg-type]
    )
    assert location.shim_dir == _ELSEWHERE


def test_a_shim_directory_with_none_of_our_commands_is_not_reported() -> None:
    """Reported when a launcher is *there*, never because a tool might use it.

    A `~/.local/bin` that exists for some other tool says nothing about where
    swe-mux went, and naming it would send a reader to an empty directory.
    """
    location = _uv_tool_install(path="", shims_populated=False)
    assert location.shim_dir is None
    assert location.bin_dir == _UV_SCRIPTS


# --------------------------------------------------------------------------- #
# PATH
# --------------------------------------------------------------------------- #


def test_commands_in_a_directory_that_is_not_on_path_are_unreachable() -> None:
    location = _uv_tool_install(path=_PATHSEP.join([_NOISE, str(_OTHER_SCRIPTS)]))
    assert location.on_path is False
    assert [command.name for command in location.unreachable] == ["mux", "muxd", "swe-mux"]
    assert all(command.status == "not on PATH" for command in location.unreachable)


def test_commands_in_a_directory_that_is_on_path_are_reachable() -> None:
    location = _uv_tool_install(path=_PATHSEP.join([_NOISE, str(_UV_SHIMS)]))
    assert location.on_path is True
    assert location.unreachable == ()


def test_path_matching_ignores_case_on_windows_and_not_elsewhere() -> None:
    """Shouted, a directory is itself on Windows and a different one on POSIX.

    Asserted against the host's own rule rather than against a described foreign
    one: `_same_path` normalizes through `os.path`, so what it does with a
    Windows path on a Linux runner is not a fact about Windows.
    """
    shouted = str(_UV_SHIMS).upper()
    location = _uv_tool_install(path=shouted, case_insensitive_probe=True)
    mux = location.command("mux")
    assert mux is not None
    # The probe answers yes to both spellings, so the case rule in `_same_path`
    # is what the assertion is about rather than a missing file.
    assert mux.resolved is not None, "the probe must find the shouted spelling"
    assert location.on_path is IS_WINDOWS

    # And the POSIX rule from a Windows host too, so the Windows leg still
    # covers the branch it ships to everybody else.
    posix = _posix_install(path="/OPT/SWE-MUX/BIN", case_insensitive_probe=True)
    posix_mux = posix.command("mux")
    assert posix_mux is not None
    assert posix_mux.resolved is not None, "the probe must find the shouted spelling"
    assert posix.on_path is False


def test_a_different_install_earlier_on_path_is_named_as_a_shadow() -> None:
    """Reachable, wrong copy. The state that has someone debugging a version
    they are not running, and it must not render as plain "not on PATH"."""
    present = {str(_UV_ROOT / "uv-receipt.toml")}
    for name in _LAUNCHERS:
        present.add(str(_UV_SCRIPTS / name))
        present.add(str(_OTHER_SCRIPTS / name))
    location = detect_install_location(
        frozen=False,
        executable=str(_INTERPRETER),
        package_dir=_UV_ROOT,
        prefix=str(_UV_ROOT),
        base_prefix=_BASE_PREFIX,
        scripts_dir=_UV_SCRIPTS,
        path=str(_OTHER_SCRIPTS),
        home=_HOME,
        environ={},
        exists=_layout(present),  # type: ignore[arg-type]
    )
    assert location.on_path is False
    mux = location.command("mux")
    assert mux is not None
    assert mux.resolved == _OTHER_SCRIPTS / f"mux{_EXE}"
    assert mux.status == f"shadowed by {_OTHER_SCRIPTS / f'mux{_EXE}'}"


def test_an_install_that_shipped_no_commands_is_not_reported_as_reachable() -> None:
    """The vacuous truth guard: `all([])` is True and would be the wrong answer."""
    root = _HOME / "work" / ".venv"
    scripts = root / ("Scripts" if IS_WINDOWS else "bin")
    location = detect_install_location(
        frozen=False,
        executable=str(scripts / f"python{_EXE}"),
        package_dir=root / "swe_mux",
        prefix=str(root),
        base_prefix=_BASE_PREFIX,
        scripts_dir=scripts,
        path=str(scripts),
        home=_HOME,
        environ={},
        exists=_layout(set()),  # type: ignore[arg-type]
    )
    assert location.installed == ()
    assert location.on_path is False
    assert location.executable("swe-mux") is None


def test_a_missing_launcher_is_never_reported_as_merely_unreachable() -> None:
    present = {str(_UV_ROOT / "uv-receipt.toml"), str(_UV_SCRIPTS / f"mux{_EXE}")}
    location = detect_install_location(
        frozen=False,
        executable=str(_INTERPRETER),
        package_dir=_UV_ROOT,
        prefix=str(_UV_ROOT),
        base_prefix=_BASE_PREFIX,
        scripts_dir=_UV_SCRIPTS,
        path="",
        home=_HOME,
        environ={},
        exists=_layout(present),  # type: ignore[arg-type]
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
    launcher = _BUNDLE / f"swe-mux{_EXE}"
    frozen = detect_install_location(
        frozen=True,
        executable=str(launcher),
        package_dir=_BUNDLE,
        path="",
        home=_HOME,
        environ={},
        exists=_layout({str(launcher)}),  # type: ignore[arg-type]
    )
    assert path_hint_lines(frozen) == []


def test_the_daemon_hint_carries_the_directory_the_fix_and_the_fallback() -> None:
    """Three concrete things, and nothing that needs a document to act on."""
    lines = path_hint_lines(_uv_tool_install(path=_NOISE))
    text = "\n".join(lines)
    assert str(_UV_SHIMS) in text
    assert "uv tool update-shell" in text
    assert "-m swe_mux" in text
    assert "--where" in text
    # Short enough to be read at the moment a daemon is starting.
    assert len(lines) <= 6


def test_where_answers_the_four_questions_it_exists_for() -> None:
    text = render_where(_uv_tool_install(path=_NOISE), version="9.9.9")
    assert "swe-mux 9.9.9" in text
    assert "uv tool install" in text  # how it got here
    assert str(_UV_SHIMS) in text  # where the commands are
    assert "on PATH        no" in text  # whether they are reachable
    assert "-m swe_mux" in text  # how to run the daemon anyway
    # How to get a Start Menu entry - on the only platform that has one. The
    # POSIX side of this branch is `test_where_does_not_offer_shortcuts_off_windows`.
    assert ("install-shortcut" in text) is IS_WINDOWS


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
    """Both quoting rules, from every host.

    `_quote` looks at whitespace and at the platform flag and never at a
    separator, so describing the other platform here is sound in the way the
    file's docstring allows - and both shells ship, so both are asserted.
    """
    scripts = _SPACED_PREFIX / ("Scripts" if IS_WINDOWS else "bin")
    interpreter = scripts / f"python{_EXE}"
    assert " " in str(interpreter), "the fixture has to carry a space to quote"
    for windows, quote in ((True, '"'), (False, "'")):
        described = detect_install_location(
            frozen=False,
            executable=str(interpreter),
            package_dir=_SPACED_PREFIX / "swe_mux",
            prefix=str(_SPACED_PREFIX),
            base_prefix=str(_SPACED_PREFIX),
            scripts_dir=scripts,
            path="",
            home=_HOME,
            environ={},
            windows=windows,
            exists=_layout(set()),  # type: ignore[arg-type]
        )
        assert described.module_fallback == f"{quote}{interpreter}{quote} -m swe_mux"
        assert described.pip_command == f"{quote}{interpreter}{quote} -m pip"


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
    # And it reads this host, which is what makes every fixture above have to
    # be shaped for this host too.
    assert location.windows is IS_WINDOWS


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
