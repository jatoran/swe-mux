"""Finding an external tool that is installed but not on PATH, and saying which.

`shim_paths.which_real` answers a different question and answers it correctly:
"what would *run* if this daemon spawned this command name". Everything that
launches - agent CLIs, shells, the harness registry - must keep asking exactly
that, because a tool this process cannot invoke by name is not usable to it
however present it is on disk.

This module answers the onboarding question instead: "does the user have this
installed". The two came apart on a clean Windows 11 laptop on 2026-08-30. Git
was installed, Tailscale was installed and connected to the tailnet, Node was
mid-install, and swe-mux reported all four prerequisites absent and told the user
to `winget install` software they already had - because none of the installers had
put its directory on PATH, and PATH presence was the whole test. Tailscale's
Windows installer in particular never adds one, so *every* GUI install of it was
misreported as "not installed" while `tailscale status` returned a healthy tailnet.

Three states, not two, and the distinction is the point: `on_path` (usable by
name), `off_path` (found at a known location or named by the user, so the tool
exists and the remedy is a PATH entry rather than an install), and `missing`.
Collapsing the middle one into `missing` is what produced the wrong advice.

Order of resolution is override, then PATH, then well-known locations. The user's
own answer wins over any search because it is the escape hatch for everything this
file cannot guess, and PATH beats the well-known list because a tool that is
invocable by name is the one that will actually be spawned.

The well-known list is deliberately short and literal. It is a fallback for the
default install location of a handful of tools whose installers are known not to
touch PATH - not a filesystem search, which would be slow, non-deterministic, and
capable of finding a stale copy the user forgot about.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .host_platform import IS_MACOS, IS_WINDOWS
from .shim_paths import clear_caches, which_real

#: How a tool was located, or why it was not.
#:
#: - ``on_path`` - resolvable by name; this is what a spawn will use.
#: - ``override`` - the user named an absolute path in config and it exists.
#: - ``off_path`` - found at a known install location. Present, not invocable by
#:   name, so anything that spawns it by name still cannot. The remedy is a PATH
#:   entry, which is a different sentence from "install it".
#: - ``missing`` - nothing of that name anywhere this looked.
LocationSource = Literal["on_path", "override", "off_path", "missing"]


@dataclass(frozen=True, slots=True)
class ToolLocation:
    """Where one external tool is, and how that was determined."""

    tool: str
    path: str | None
    source: LocationSource

    @property
    def present(self) -> bool:
        """Whether the tool exists on this machine at all."""
        return self.path is not None

    @property
    def invocable_by_name(self) -> bool:
        """Whether a bare ``subprocess`` call on the command name would find it.

        False for an override and for an off-PATH find, both of which have to be
        spawned by absolute path. A caller that builds an argv must use ``path``.
        """
        return self.source == "on_path"


def _expand(*parts: str) -> str:
    return os.path.expandvars(str(Path(*parts)))


def _windows_locations() -> dict[str, tuple[str, ...]]:
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", _expand("%USERPROFILE%", "AppData", "Local"))
    roaming = os.environ.get("APPDATA", _expand("%USERPROFILE%", "AppData", "Roaming"))
    return {
        # The MSI puts the CLI here and does not add it to PATH; this is the
        # single case that motivated the whole module.
        "tailscale": (
            str(Path(program_files, "Tailscale", "tailscale.exe")),
            str(Path(program_files_x86, "Tailscale", "tailscale.exe")),
        ),
        "git": (
            str(Path(program_files, "Git", "cmd", "git.exe")),
            str(Path(program_files_x86, "Git", "cmd", "git.exe")),
            str(Path(local, "Programs", "Git", "cmd", "git.exe")),
        ),
        "node": (
            str(Path(program_files, "nodejs", "node.exe")),
            str(Path(program_files_x86, "nodejs", "node.exe")),
            str(Path(roaming, "nvm", "node.exe")),
        ),
        "npm": (
            str(Path(program_files, "nodejs", "npm.cmd")),
            str(Path(program_files_x86, "nodejs", "npm.cmd")),
            str(Path(roaming, "npm", "npm.cmd")),
        ),
        # uv installs itself here and asks the user to restart their shell, so a
        # daemon started from the old environment cannot see it by name.
        "uv": (
            str(Path(local, "Programs", "uv", "uv.exe")),
            str(Path(roaming, "uv", "uv.exe")),
            str(Path(Path.home(), ".local", "bin", "uv.exe")),
            str(Path(Path.home(), ".cargo", "bin", "uv.exe")),
        ),
    }


def _posix_locations() -> dict[str, tuple[str, ...]]:
    home = Path.home()
    # Homebrew moved prefix between Intel and Apple silicon and a daemon launched
    # from a GUI session frequently has neither on PATH, which is the macOS shape
    # of the same problem.
    brew = ("/opt/homebrew/bin", "/usr/local/bin") if IS_MACOS else ()
    extra = (*brew, "/usr/bin", "/usr/local/bin", "/snap/bin")
    return {
        "tailscale": (
            *[str(Path(root, "tailscale")) for root in extra],
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
        ),
        "git": tuple(str(Path(root, "git")) for root in extra),
        "node": tuple(str(Path(root, "node")) for root in extra),
        "npm": tuple(str(Path(root, "npm")) for root in extra),
        "uv": (
            str(home / ".local" / "bin" / "uv"),
            str(home / ".cargo" / "bin" / "uv"),
            *[str(Path(root, "uv")) for root in extra],
        ),
    }


def well_known_locations(tool: str) -> tuple[str, ...]:
    """Default install paths for *tool*, expanded against this machine.

    Computed per call rather than at import, because the environment variables it
    reads (`ProgramFiles`, `LOCALAPPDATA`) are exactly the ones a refresh may have
    just changed, and a module-level table would freeze a stale answer.
    """
    table = _windows_locations() if IS_WINDOWS else _posix_locations()
    return table.get(tool, ())


def locate_tool(tool: str, *, override: str | None = None) -> ToolLocation:
    """Where *tool* is on this machine, and how that was decided.

    ``override`` is an absolute path the user supplied. It is checked first and
    trusted if it exists as a file: it is the answer for every install layout this
    module does not know about, and second-guessing it would defeat the purpose.
    An override that no longer exists falls through to the search rather than
    reporting missing, so a stale entry degrades to the old behaviour instead of
    hiding a tool that is now installed properly.
    """
    if override:
        candidate = Path(os.path.expandvars(override)).expanduser()
        if candidate.is_file():
            return ToolLocation(tool, str(candidate), "override")
    on_path = which_real(tool)
    if on_path:
        return ToolLocation(tool, on_path, "on_path")
    for candidate_path in well_known_locations(tool):
        if Path(candidate_path).is_file():
            return ToolLocation(tool, candidate_path, "off_path")
    return ToolLocation(tool, None, "missing")


def refresh_search_path() -> bool:
    """Re-read the OS's PATH into this process. Returns whether it changed.

    A daemon inherits its environment once, at spawn. Every Windows installer that
    edits PATH edits the *registry* and broadcasts a change that only interactive
    shells act on, so a tool installed while swe-mux is running stays invisible to
    it until a restart - and nothing said so, which made "re-run detection" a
    button that would have returned the same wrong answer and taught the user that
    the feature does not work.

    Reads the machine and user `Environment` keys and rebuilds `os.environ["PATH"]`
    from them, which is what the broadcast is telling everyone to do. Off Windows
    this is a no-op and returns False: there is no equivalent out-of-band source,
    a POSIX daemon's PATH is simply what it was given, and pretending otherwise
    would be a false promise rather than a limitation.

    Also drops `shim_paths`' PATH-keyed caches, without which the new PATH would be
    read through the old scan.
    """
    if not IS_WINDOWS:
        return False
    try:
        import winreg
    except ImportError:
        return False

    def _read(root: int, subkey: str) -> str:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _kind = winreg.QueryValueEx(key, "Path")
                return str(value) if value else ""
        except OSError:
            return ""

    machine = _read(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )
    user = _read(winreg.HKEY_CURRENT_USER, "Environment")
    entries: list[str] = []
    for raw in (machine, user):
        for entry in raw.split(os.pathsep):
            expanded = os.path.expandvars(entry).strip()
            if expanded and expanded not in entries:
                entries.append(expanded)
    if not entries:
        # A registry this process could not read is not evidence that PATH is
        # empty, and overwriting a working PATH with nothing would break every
        # spawn for the life of the daemon.
        return False
    # The process PATH can legitimately carry entries no registry key mentions -
    # the mux shim directory is prepended at spawn, and a session-scoped tool may
    # have added one. Those are kept, so a refresh only ever *widens* the search.
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip()
        if entry and entry not in entries:
            entries.append(entry)
    rebuilt = os.pathsep.join(entries)
    changed = rebuilt != os.environ.get("PATH", "")
    os.environ["PATH"] = rebuilt
    if changed:
        clear_caches()
    return changed
