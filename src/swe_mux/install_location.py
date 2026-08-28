"""Where this copy of swe-mux is installed, and whether its commands are reachable.

A wheel install puts three launchers in an interpreter's scripts directory and
nothing anywhere else. When that directory is not on ``PATH`` the install is
complete, correct, and completely invisible: ``mux`` is not a command, no window
opens, nothing lands on the Start Menu, and the one fact that would rescue the
situation - that ``muxd`` is ``swe_mux.__main__:main``, so ``python -m swe_mux``
is the same daemon - is known only to someone who has read ``pyproject.toml``.
That is the state an operator was left in on a clean Windows machine, with no
command they could run to find out anything at all.

This module is the single answer to "where am I, and can I be reached". Four
surfaces read it - the first-run hint ``muxd`` prints, ``python -m swe_mux
--where``, the ``install`` rows in the local ``mux doctor`` report, and
``mux install-shortcut``'s target resolution - so all four agree by construction
rather than by four separate guesses at the same filesystem.

Two rules it keeps.

**Every input is an argument with a live default.** The answers that matter are
the Windows ones, and the gate that has to prove them runs on three hosts; a
platform branch whose non-development side is never asserted is exactly the class
of bug this package is cleaning up after. So
``detect_install_location(windows=True, path=..., exists=...)`` describes a
Windows install from a Linux runner exactly, and the tests do that.

**"Not found" is never reported as "not on PATH".** A launcher that is missing
from the scripts directory and a launcher that is present but unreachable are
different faults with different fixes, so a command carries both its own location
and what the bare name resolves to, and the two are never collapsed.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .host_platform import IS_WINDOWS

#: Every launcher `pyproject.toml` declares, with the kind of launcher it builds.
#: ``gui`` is the console-less (``pythonw``-style) launcher: it has no stdout or
#: stderr at all, which is why `desktop.main` writes its own log instead.
SHIPPED_COMMANDS: tuple[tuple[str, str], ...] = (
    ("mux", "console"),
    ("muxd", "console"),
    ("swe-mux", "gui"),
)

INSTALL_FROZEN = "frozen"
INSTALL_UV_TOOL = "uv-tool"
INSTALL_PIPX = "pipx"
INSTALL_VENV = "venv"
INSTALL_SYSTEM = "system"

#: How each install method describes itself, and the command that puts its
#: launchers on ``PATH``. `uv` and `pipx` both own a shim directory and both ship
#: a command that registers it, so telling their users to edit ``PATH`` by hand
#: would be worse advice than the tool's own.
_KINDS: dict[str, tuple[str, str]] = {
    INSTALL_FROZEN: ("frozen desktop app", ""),
    INSTALL_UV_TOOL: ("uv tool install", "uv tool update-shell"),
    INSTALL_PIPX: ("pipx install", "pipx ensurepath"),
    INSTALL_VENV: ("pip install into a virtual environment", ""),
    INSTALL_SYSTEM: ("pip install into the system interpreter", ""),
}

#: Marker files each installer leaves at the root of the environment it made.
#: Both are documented artifacts of the tool rather than heuristics over a path
#: string, which matters because a user can put a uv tool directory anywhere.
_ENVIRONMENT_MARKERS: tuple[tuple[str, str], ...] = (
    ("uv-receipt.toml", INSTALL_UV_TOOL),
    ("pipx_metadata.json", INSTALL_PIPX),
)

#: Environment variables that relocate each tool's shim directory, and the
#: default it uses otherwise. Consulted only to *look for a launcher that is
#: actually there*: a shim directory is reported when it holds one of our
#: commands and never merely because the tool might have used it.
_SHIM_DIRS: dict[str, tuple[str, str]] = {
    INSTALL_UV_TOOL: ("UV_TOOL_BIN_DIR", ".local/bin"),
    INSTALL_PIPX: ("PIPX_BIN_DIR", ".local/bin"),
}


@dataclass(frozen=True, slots=True)
class CommandLocation:
    """One shipped launcher: where it is, and what the bare name resolves to."""

    name: str
    #: ``console`` or ``gui`` - see `SHIPPED_COMMANDS`.
    launcher: str
    #: The launcher this install actually shipped, or None when it is absent.
    path: Path | None
    #: What a shell running the bare name would execute, or None when nothing
    #: on ``PATH`` answers to it.
    resolved: Path | None
    #: True only when the name resolves *and* resolves to this install's own
    #: launcher. A different swe-mux earlier on ``PATH`` is its own finding.
    on_path: bool

    @property
    def status(self) -> str:
        if self.path is None:
            return "not installed"
        if self.on_path:
            return "on PATH"
        if self.resolved is not None:
            return f"shadowed by {self.resolved}"
        return "not on PATH"


@dataclass(frozen=True, slots=True)
class InstallLocation:
    """A complete description of the copy of swe-mux that is running."""

    kind: str
    label: str
    package_dir: Path
    #: ``sys.prefix`` for an installed copy, the bundle directory when frozen.
    environment_root: Path
    #: Where the launchers were written by the installer.
    scripts_dir: Path
    #: Where `uv` or `pipx` re-exposed them, when one of ours is actually there.
    shim_dir: Path | None
    commands: tuple[CommandLocation, ...]
    path_entries: tuple[str, ...]
    #: The exact command that fixes ``PATH``, or "" when there is nothing to fix
    #: or no single command that does it (see `path_fix_lines`).
    path_fix_command: str
    #: ``<interpreter> -m swe_mux``, shell-ready. The fallback that needs nothing
    #: but a working interpreter, which is the point of it.
    module_fallback: str
    windows: bool

    @property
    def bin_dir(self) -> Path:
        """The directory a user would add to ``PATH`` to get these commands."""
        return self.shim_dir or self.scripts_dir

    @property
    def installed(self) -> tuple[CommandLocation, ...]:
        return tuple(command for command in self.commands if command.path is not None)

    @property
    def unreachable(self) -> tuple[CommandLocation, ...]:
        """Launchers this install shipped that the bare name does not reach."""
        return tuple(command for command in self.installed if not command.on_path)

    @property
    def on_path(self) -> bool:
        """True when every launcher this install shipped resolves to itself.

        False when nothing shipped at all, because "no commands exist" must not
        read as "all commands are reachable" - the vacuous truth is the wrong
        answer to the only question anyone asks this.
        """
        return bool(self.installed) and not self.unreachable

    def command(self, name: str) -> CommandLocation | None:
        return next((entry for entry in self.commands if entry.name == name), None)

    def executable(self, name: str) -> Path | None:
        """The launcher named ``name``, preferring this install's own copy."""
        entry = self.command(name)
        return entry.path if entry is not None else None

    def path_fix_lines(self) -> list[str]:
        """The concrete way to put `bin_dir` on ``PATH`` on this host.

        A tool that owns a shim directory gets its own command; everything else
        gets the literal line for this shell, because "add it to your PATH" is
        the advice that sends someone to a search engine.
        """
        if self.path_fix_command:
            return [self.path_fix_command]
        if self.windows:
            return [
                f'setx PATH "%PATH%;{self.bin_dir}"',
                "(setx writes the user PATH permanently; open a new terminal after it)",
            ]
        return [
            f'export PATH="$PATH:{self.bin_dir}"',
            "(add that line to your shell profile to make it permanent)",
        ]


def _script_filename(name: str, *, windows: bool) -> str:
    return f"{name}.exe" if windows else name


def _same_path(left: Path, right: Path, *, windows: bool) -> bool:
    """Whether two paths name the same launcher.

    Compared as normalized text, not with ``samefile``: the question is asked
    about paths that may not exist (an injected layout in a test) and about a
    ``PATH`` entry that may be a stale directory, and a raise there would take
    down a diagnostic whose entire job is to survive a broken install.
    """
    a = os.path.normpath(str(left))
    b = os.path.normpath(str(right))
    return a.casefold() == b.casefold() if windows else a == b


def _path_entries(path: str | None) -> tuple[str, ...]:
    raw = os.environ.get("PATH", "") if path is None else path
    return tuple(entry for entry in raw.split(os.pathsep) if entry.strip())


def _which(
    filename: str,
    entries: tuple[str, ...],
    *,
    exists: Callable[[Path], bool],
) -> Path | None:
    """First ``PATH`` entry holding ``filename``, searching the way a shell does.

    Written out rather than delegated to ``shutil.which`` so the Windows answer
    is computable from a Linux runner: ``shutil.which`` consults the real
    process environment and the real ``PATHEXT``, neither of which a test can
    describe. The filename is already exact (``mux.exe``), so ``PATHEXT``
    expansion is not part of the question being asked.
    """
    for entry in entries:
        candidate = Path(entry) / filename
        try:
            if exists(candidate):
                return candidate
        except OSError:
            continue
    return None


def _detect_kind(
    *,
    frozen: bool,
    environment_root: Path,
    prefix: Path,
    base_prefix: Path,
    exists: Callable[[Path], bool],
) -> str:
    if frozen:
        return INSTALL_FROZEN
    for marker, kind in _ENVIRONMENT_MARKERS:
        try:
            if exists(environment_root / marker):
                return kind
        except OSError:
            continue
    return INSTALL_VENV if prefix != base_prefix else INSTALL_SYSTEM


def _shim_dir(
    kind: str,
    filenames: list[str],
    *,
    home: Path,
    environ: dict[str, str],
    exists: Callable[[Path], bool],
) -> Path | None:
    """The tool-owned bin directory, reported only when a launcher is in it."""
    configured = _SHIM_DIRS.get(kind)
    if configured is None:
        return None
    variable, default = configured
    override = environ.get(variable, "").strip()
    candidate = Path(override) if override else home.joinpath(*default.split("/"))
    for filename in filenames:
        try:
            if exists(candidate / filename):
                return candidate
        except OSError:
            continue
    return None


def _quote(value: str, *, windows: bool) -> str:
    """Quote a path for the shell the user is actually in, when it needs it."""
    if not any(character.isspace() for character in value):
        return value
    return f'"{value}"' if windows else f"'{value}'"


def detect_install_location(
    *,
    frozen: bool | None = None,
    executable: str | None = None,
    package_dir: Path | None = None,
    prefix: str | None = None,
    base_prefix: str | None = None,
    scripts_dir: Path | None = None,
    path: str | None = None,
    home: Path | None = None,
    environ: dict[str, str] | None = None,
    windows: bool | None = None,
    exists: Callable[[Path], bool] | None = None,
) -> InstallLocation:
    """Describe the installed copy of swe-mux this process is running from.

    Every parameter defaults to the live value; they exist so a Windows install
    can be described - and asserted - from any host.
    """
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    is_windows = IS_WINDOWS if windows is None else windows
    probe = exists if exists is not None else Path.is_file
    environment = dict(os.environ) if environ is None else environ
    interpreter = Path(executable or sys.executable)
    package = package_dir if package_dir is not None else Path(__file__).resolve().parent
    entries = _path_entries(path if path is not None else environment.get("PATH", ""))
    user_home = home if home is not None else Path.home()

    if is_frozen:
        # A frozen bundle has no scripts directory: the launchers *are* the
        # bundle's own executables, sitting beside it.
        root = interpreter.parent
        scripts = scripts_dir if scripts_dir is not None else root
    else:
        root = Path(prefix if prefix is not None else sys.prefix)
        scripts = (
            scripts_dir
            if scripts_dir is not None
            else Path(sysconfig.get_path("scripts"))
        )
    kind = _detect_kind(
        frozen=is_frozen,
        environment_root=root,
        prefix=Path(prefix if prefix is not None else sys.prefix),
        base_prefix=Path(base_prefix if base_prefix is not None else sys.base_prefix),
        exists=probe,
    )
    label, fix_command = _KINDS[kind]
    filenames = [_script_filename(name, windows=is_windows) for name, _ in SHIPPED_COMMANDS]
    shim = _shim_dir(kind, filenames, home=user_home, environ=environment, exists=probe)

    commands: list[CommandLocation] = []
    for (name, launcher), filename in zip(SHIPPED_COMMANDS, filenames, strict=True):
        own: Path | None = None
        for directory in (shim, scripts):
            if directory is None:
                continue
            candidate = directory / filename
            try:
                if probe(candidate):
                    own = candidate
                    break
            except OSError:
                continue
        found = _which(filename, entries, exists=probe)
        commands.append(
            CommandLocation(
                name=name,
                launcher=launcher,
                path=own,
                resolved=found,
                on_path=(
                    own is not None
                    and found is not None
                    and _same_path(found, own, windows=is_windows)
                ),
            )
        )
    return InstallLocation(
        kind=kind,
        label=label,
        package_dir=package,
        environment_root=root,
        scripts_dir=scripts,
        shim_dir=shim,
        commands=tuple(commands),
        path_entries=entries,
        path_fix_command=fix_command,
        module_fallback=f"{_quote(str(interpreter), windows=is_windows)} -m swe_mux",
        windows=is_windows,
    )


def installed_version() -> str | None:
    """The installed distribution version, or None when metadata cannot be read.

    Read from installed metadata rather than a constant, for the same reason
    `doctor_local._installed_version` is: this describes the copy on the machine,
    and a constant would describe the copy the source came from. A frozen bundle
    legitimately carries no distribution metadata.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("swe-mux")
    except PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - metadata is best-effort in a frozen build
        return None


def path_hint_lines(location: InstallLocation) -> list[str]:
    """The terse block a starting daemon prints, or [] when nothing is wrong.

    Deliberately short. It fires while the user is watching a daemon start, and
    a wall of text at that moment is skipped exactly like no text at all - so it
    says where the commands are, the one line that fixes it, and the two commands
    that work regardless.
    """
    unreachable = location.unreachable
    if not unreachable or location.kind == INSTALL_FROZEN:
        return []
    names = ", ".join(command.name for command in unreachable)
    lines = [
        f"swe-mux: {names} installed, but not reachable by name from this PATH.",
        f"  scripts   {location.bin_dir}",
    ]
    fixes = location.path_fix_lines()
    lines.append(f"  fix       {fixes[0]}")
    lines.extend(f"            {line}" for line in fixes[1:])
    lines.append(f"  or run    {location.module_fallback}   (this daemon, no PATH needed)")
    lines.append(f"  details   {location.module_fallback} --where")
    return lines


def _command_table(location: InstallLocation) -> list[str]:
    width = max(len(name) for name, _ in SHIPPED_COMMANDS)
    lines = []
    for command in location.commands:
        where = str(command.path) if command.path is not None else "(not installed)"
        lines.append(f"  {command.name.ljust(width)}  {where}")
        lines.append(f"  {' ' * width}  {command.status}")
    return lines


def render_where(location: InstallLocation, *, version: str | None) -> str:
    """The full ``--where`` answer: the command every stuck user is pointed at.

    It has to work when nothing else does, so it reports rather than probes:
    every line is derived from `detect_install_location`, which reads the
    filesystem and the environment and calls nothing. No daemon, no config, no
    import of the package's runtime graph.

    ``version`` has no default on purpose. A default would have to be
    `installed_version()`, which would make "the caller did not say" and "the
    metadata is unreadable" the same argument - and those are different facts
    that must not render the same, which is the rule this whole report is built
    on. Callers pass `installed_version()` explicitly.
    """
    header = f"swe-mux {version}" if version else "swe-mux (version metadata unavailable)"
    lines = [
        header,
        f"  installed by   {location.label}",
        f"  package        {location.package_dir}",
        f"  environment    {location.environment_root}",
        f"  launchers      {location.bin_dir}",
        f"  on PATH        {'yes' if location.on_path else 'no'}",
        "",
        "Commands this install ships:",
        *_command_table(location),
    ]
    if not location.on_path and location.kind != INSTALL_FROZEN:
        lines += ["", "Put them on PATH:", *(f"  {line}" for line in location.path_fix_lines())]
    # Each command gets its purpose on the line above rather than trailing it:
    # these are absolute paths on a machine whose install is already confusing,
    # and a comment pushed past a 120-character path is a comment nobody reads.
    # Written this way they are also copy-pasteable as-is.
    lines += ["", "Run it without PATH:", "  # the daemon, identical to muxd"]
    lines.append(f"  {location.module_fallback}")
    desktop = location.executable("swe-mux")
    if desktop is not None:
        lines += [
            "  # the desktop app (tray + native window)",
            f"  {_quote(str(desktop), windows=location.windows)}",
        ]
    mux = location.executable("mux")
    if mux is not None:
        quoted = _quote(str(mux), windows=location.windows)
        lines += ["  # full diagnostics for this machine", f"  {quoted} doctor"]
        if location.windows:
            lines += [
                "",
                "Create Start Menu and Desktop shortcuts:",
                f"  {quoted} install-shortcut",
            ]
    return "\n".join(lines)


__all__ = [
    "INSTALL_FROZEN",
    "INSTALL_PIPX",
    "INSTALL_SYSTEM",
    "INSTALL_UV_TOOL",
    "INSTALL_VENV",
    "SHIPPED_COMMANDS",
    "CommandLocation",
    "InstallLocation",
    "detect_install_location",
    "installed_version",
    "path_hint_lines",
    "render_where",
]
