"""Start Menu, Desktop, and run-at-login shortcuts for a wheel install.

A wheel cannot create a shortcut. `pip` and `uv` write launchers into a scripts
directory and stop there, by design - there is no hook, and nothing that runs
after the install to make one. So a correct `pip install swe-mux` on a clean
Windows machine ends with an application that has no Start Menu entry, no desktop
icon, and no tray, and the only remaining route into it is a command the user may
not even have on ``PATH``. That is a gap the packaging cannot close and the
product can, which is what this module is.

Three rules it keeps.

**The `.lnk` is written by Windows, not by us.** `WScript.Shell`'s
``CreateShortcut`` is the documented way to author a shell link and it ships on
every supported Windows, so this needs no new dependency - the alternative,
`pywin32`, would add a compiled package to the runtime closure (and a license
audit entry) to reach the same COM object PowerShell already exposes.

**The plan is pure and the execution is not.** `plan_shortcuts` and
`render_install_script` compute paths and script text from arguments alone, so
the Windows behaviour is asserted from any host; only `_run_powershell` needs
Windows, and it is the one thing the tests do not exercise there. The repository
has been bitten repeatedly by platform-conditional code whose other branch was
never asserted, and a shortcut writer is precisely the shape that invites it.

**It reports what it wrote, and it is idempotent.** Every operation returns the
slot, the absolute path, and which of ``created``/``updated``/``unchanged``/
``removed``/``absent``/``failed`` happened - a shortcut installer that says
"done" teaches nobody where to look, and one that appends a second copy on every
run is worse than none. Re-running with the same arguments reports ``unchanged``
and touches nothing.

Windows-only, and it says so rather than failing obscurely: on POSIX the report
comes back unsupported with the reason, and the CLI prints it.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, Any

from .host_platform import IS_WINDOWS, platform_label
from .lifecycle import ledger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Config
    from .install_location import InstallLocation

#: The three places a shortcut can go, in the order a report lists them.
SLOT_START_MENU = "start-menu"
SLOT_DESKTOP = "desktop"
SLOT_STARTUP = "startup"
ALL_SLOTS: tuple[str, ...] = (SLOT_START_MENU, SLOT_DESKTOP, SLOT_STARTUP)

#: The single filename used in every slot. One name means a re-run finds what a
#: previous run wrote instead of writing a second link beside it, which is the
#: whole of the idempotence guarantee.
SHORTCUT_FILENAME = "swe-mux.lnk"

SHORTCUT_DESCRIPTION = "swe-mux - browser-based terminal multiplexer for coding agents"

#: `desktop.desktop_parser` accepts `--hidden`, which starts the tray with the
#: window closed. That is what a login shortcut wants and nothing else does.
STARTUP_ARGUMENTS = "--hidden"

#: PowerShell has to finish authoring three shell links; a COM call that hangs is
#: a wedged command, not a slow one.
POWERSHELL_TIMEOUT_SECONDS = 60.0

#: Known-folder GUIDs, which is the only correct way to find these directories.
#: `%USERPROFILE%\Desktop` is wrong on any machine whose Desktop is redirected -
#: OneDrive Backup does exactly that by default on current Windows - and a
#: shortcut written to the un-redirected path is invisible to the user who asked
#: for it. The env-var forms below are a fallback for when the shell call fails,
#: not a preference.
_KNOWN_FOLDERS: dict[str, str] = {
    SLOT_DESKTOP: "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",  # FOLDERID_Desktop
    SLOT_START_MENU: "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",  # FOLDERID_Programs
    SLOT_STARTUP: "{B97D20BB-F46A-4C97-BA10-5E3608430854}",  # FOLDERID_Startup
}


class ShortcutError(Exception):
    """A refusal this command cannot work around, carrying the reason."""


@dataclass(frozen=True, slots=True)
class ShortcutFolders:
    """Where each slot's link goes. Injected in tests; resolved by Windows live."""

    start_menu: Path
    desktop: Path
    startup: Path
    #: Which of the three were resolved through the shell rather than guessed
    #: from environment variables, because a guessed Desktop can be the wrong
    #: directory and the report should not present the two as equally certain.
    resolved_by_shell: tuple[str, ...] = ()

    def for_slot(self, slot: str) -> Path:
        return {
            SLOT_START_MENU: self.start_menu,
            SLOT_DESKTOP: self.desktop,
            SLOT_STARTUP: self.startup,
        }[slot]


@dataclass(frozen=True, slots=True)
class ShortcutSpec:
    """One shell link, fully determined before anything is written."""

    slot: str
    path: Path
    target: Path
    arguments: str
    working_directory: Path
    #: ``<path>,<index>`` or "" to inherit the target executable's own icon.
    icon: str
    description: str


@dataclass(frozen=True, slots=True)
class ShortcutOutcome:
    slot: str
    path: Path
    action: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "failed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "path": str(self.path),
            "action": self.action,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ShortcutReport:
    """Exactly what happened, in the shape both the table and `--json` render."""

    action: str
    supported: bool
    reason: str = ""
    target: Path | None = None
    icon: str = ""
    icon_detail: str = ""
    outcomes: tuple[ShortcutOutcome, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every shortcut that was asked for actually landed.

        A `reason` on a supported host is a refusal that happened *before* any
        link was planned - no `swe-mux` launcher to point at, say - and it has no
        outcome rows to be false about, so it is checked explicitly. Without
        that, `all()` over an empty tuple would report a refusal as a success.
        """
        if not self.supported or self.reason:
            return False
        return all(outcome.ok for outcome in self.outcomes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "supported": self.supported,
            "reason": self.reason,
            "target": str(self.target) if self.target is not None else None,
            "icon": self.icon,
            "icon_detail": self.icon_detail,
            "ok": self.ok,
            "shortcuts": [outcome.as_dict() for outcome in self.outcomes],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Where the links go
# --------------------------------------------------------------------------- #


def _known_folder(guid: str) -> Path | None:
    """Resolve a Windows known folder, or None when the shell will not say.

    ``SHGetKnownFolderPath`` is asked directly through ctypes rather than through
    a helper package: it is three calls, and adding a dependency to read three
    constants would be a worse trade than the code.
    """
    import ctypes

    class _Guid(ctypes.Structure):
        _fields_ = (
            ("Data1", ctypes.c_uint32),
            ("Data2", ctypes.c_uint16),
            ("Data3", ctypes.c_uint16),
            ("Data4", ctypes.c_ubyte * 8),
        )

    try:
        identifier = _Guid()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(identifier))
        buffer = ctypes.c_wchar_p()
        ctypes.oledll.shell32.SHGetKnownFolderPath(
            ctypes.byref(identifier), 0, None, ctypes.byref(buffer)
        )
        try:
            value = buffer.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buffer)
    except (OSError, AttributeError):
        return None
    return Path(value) if value else None


def _environment_folders(environ: dict[str, str]) -> ShortcutFolders:
    """The fallback layout, derived from the variables Windows always sets."""
    appdata = Path(environ.get("APPDATA", "")) if environ.get("APPDATA") else None
    profile = Path(environ.get("USERPROFILE", "")) if environ.get("USERPROFILE") else Path.home()
    start_menu = (
        appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if appdata is not None
        else profile / "Start Menu" / "Programs"
    )
    startup = start_menu / "Startup"
    return ShortcutFolders(start_menu=start_menu, desktop=profile / "Desktop", startup=startup)


def resolve_folders(environ: dict[str, str] | None = None) -> ShortcutFolders:
    """The three destination directories on this machine.

    Every folder the shell answers for is taken from the shell; anything it
    declines falls back to the environment layout, and the report records which
    is which so a Desktop link that landed in an un-redirected directory is a
    stated fact rather than a mystery.
    """
    fallback = _environment_folders(dict(os.environ) if environ is None else environ)
    if not IS_WINDOWS:
        return fallback
    resolved: dict[str, Path] = {}
    for slot, guid in _KNOWN_FOLDERS.items():
        folder = _known_folder(guid)
        if folder is not None:
            resolved[slot] = folder
    return ShortcutFolders(
        start_menu=resolved.get(SLOT_START_MENU, fallback.start_menu),
        desktop=resolved.get(SLOT_DESKTOP, fallback.desktop),
        startup=resolved.get(SLOT_STARTUP, fallback.startup),
        resolved_by_shell=tuple(slot for slot in ALL_SLOTS if slot in resolved),
    )


# --------------------------------------------------------------------------- #
# The icon
# --------------------------------------------------------------------------- #


def icon_path(data_dir: Path) -> Path:
    return data_dir / "icons" / "swe-mux.ico"


def ensure_icon(data_dir: Path, *, frozen_executable: Path | None = None) -> tuple[str, str]:
    """The ``IconLocation`` to use, and a sentence saying where it came from.

    **The packaged icon is not packaged.** ``packaging/swe-mux.ico`` is generated
    at build time by `packaging.build_desktop` and lives under ``packaging/``,
    which the wheel does not contain (`[tool.hatch.build.targets.wheel]` carries
    ``src/swe_mux``, plus ``static`` and ``assets``), so an installed copy has no
    ``.ico`` at all. What it does have is the mark itself:
    `desktop.create_tray_image` draws it, and Pillow is an unconditional runtime
    dependency, so the same image the tray and the frozen executable use is
    rendered here into the data directory once and reused.

    A frozen bundle skips all of that - PyInstaller embedded the icon in the
    executable, so pointing at index 0 of the target is both correct and free.

    Never raises: an icon is the least important thing about a shortcut, and a
    link that inherits its target's icon is a working link.
    """
    if frozen_executable is not None:
        return f"{frozen_executable},0", f"embedded in {frozen_executable.name}"
    destination = icon_path(data_dir)
    if destination.is_file():
        return f"{destination},0", f"reused {destination}"
    try:
        from .desktop import create_tray_image

        destination.parent.mkdir(parents=True, exist_ok=True)
        create_tray_image(256).save(
            destination,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)],
        )
    except Exception as exc:  # noqa: BLE001 - a shortcut without an icon still works
        return "", f"no icon written ({type(exc).__name__}: {exc}); using the target's own"
    return f"{destination},0", f"wrote {destination}"


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def plan_shortcuts(
    *,
    slots: Sequence[str],
    folders: ShortcutFolders,
    target: Path,
    working_directory: Path,
    icon: str,
    description: str = SHORTCUT_DESCRIPTION,
) -> tuple[ShortcutSpec, ...]:
    """Every link that will be written, computed from arguments alone.

    Only the startup slot carries arguments: `--hidden` is what makes a login
    launch open the tray without throwing a window at someone who has just
    signed in, and it is the same flag `desktop.startup_command` passes for the
    tray's own "Start with Windows" toggle.
    """
    unknown = [slot for slot in slots if slot not in ALL_SLOTS]
    if unknown:
        raise ShortcutError(f"unknown shortcut slot(s): {', '.join(sorted(unknown))}")
    return tuple(
        ShortcutSpec(
            slot=slot,
            path=folders.for_slot(slot) / SHORTCUT_FILENAME,
            target=target,
            arguments=STARTUP_ARGUMENTS if slot == SLOT_STARTUP else "",
            working_directory=working_directory,
            icon=icon,
            description=description,
        )
        # Ordered by ALL_SLOTS rather than by the caller, so a report reads the
        # same way whatever order the flags were typed in.
        for slot in ALL_SLOTS
        if slot in slots
    )


# --------------------------------------------------------------------------- #
# The PowerShell it runs
# --------------------------------------------------------------------------- #


def _quote_ps(value: str) -> str:
    """Single-quote a value for PowerShell, escaping embedded single quotes."""
    return "'" + value.replace("'", "''") + "'"


#: Emitting one compact JSON object per line, rather than one array at the end,
#: is deliberate: `ConvertTo-Json` unwraps a single-element array into a bare
#: object, so an array would change shape with the number of shortcuts and the
#: parser would have to guess which it got.
_SCRIPT_HEADER = """$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$emit = {
  param($slot, $path, $action, $detail)
  [pscustomobject]@{ slot = $slot; path = $path; action = $action; detail = $detail } |
    ConvertTo-Json -Compress
}
# The five properties this command owns, joined so an existing link and the one
# about to be written can be compared with a single string equality.
$fields = {
  param($link)
  @(
    $link.TargetPath, $link.Arguments, $link.WorkingDirectory,
    $link.IconLocation, $link.Description
  ) -join '|'
}
"""


def _install_block(spec: ShortcutSpec) -> str:
    """Write one link, and say whether that changed anything.

    The existing link is read *before* the new values are assigned and compared
    against the values as the COM object reports them back, so a re-run with the
    same arguments reports `unchanged` and never rewrites the file - which is
    what keeps a pinned taskbar item and a Start Menu tile from being reset by a
    command someone ran twice.
    """
    path = _quote_ps(str(spec.path))
    return f"""
try {{
  $path = {path}
  $before = ''
  if (Test-Path -LiteralPath $path) {{
    $old = $shell.CreateShortcut($path)
    $before = & $fields $old
  }}
  $link = $shell.CreateShortcut($path)
  $link.TargetPath = {_quote_ps(str(spec.target))}
  $link.Arguments = {_quote_ps(spec.arguments)}
  $link.WorkingDirectory = {_quote_ps(str(spec.working_directory))}
  $link.IconLocation = {_quote_ps(spec.icon)}
  $link.Description = {_quote_ps(spec.description)}
  $after = & $fields $link
  if ($before -eq $after) {{
    & $emit {_quote_ps(spec.slot)} $path 'unchanged' ''
  }} else {{
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    $link.Save()
    if ($before -eq '') {{
      & $emit {_quote_ps(spec.slot)} $path 'created' ''
    }} else {{
      & $emit {_quote_ps(spec.slot)} $path 'updated' ''
    }}
  }}
}} catch {{
  & $emit {_quote_ps(spec.slot)} {path} 'failed' $_.Exception.Message
}}"""


def _remove_block(spec: ShortcutSpec) -> str:
    path = _quote_ps(str(spec.path))
    return f"""
try {{
  $path = {path}
  if (Test-Path -LiteralPath $path) {{
    Remove-Item -LiteralPath $path -Force
    & $emit {_quote_ps(spec.slot)} $path 'removed' ''
  }} else {{
    & $emit {_quote_ps(spec.slot)} $path 'absent' ''
  }}
}} catch {{
  & $emit {_quote_ps(spec.slot)} {path} 'failed' $_.Exception.Message
}}"""


def render_script(specs: Sequence[ShortcutSpec], *, remove: bool) -> str:
    block = _remove_block if remove else _install_block
    return _SCRIPT_HEADER + "".join(block(spec) for spec in specs) + "\n"


def parse_script_output(text: str, specs: Sequence[ShortcutSpec]) -> tuple[ShortcutOutcome, ...]:
    """Turn the script's JSON lines into outcomes, one per planned shortcut.

    Driven by the plan rather than by the output, so a shortcut the script never
    reported on comes back as an explicit `failed` row instead of silently
    vanishing from a report whose whole purpose is to say what was written.
    """
    reported: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            record = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("slot"), str):
            reported[record["slot"]] = record
    outcomes = []
    for spec in specs:
        record = reported.get(spec.slot)
        if record is None:
            outcomes.append(
                ShortcutOutcome(
                    slot=spec.slot,
                    path=spec.path,
                    action="failed",
                    detail="PowerShell reported nothing for this shortcut.",
                )
            )
            continue
        outcomes.append(
            ShortcutOutcome(
                slot=spec.slot,
                path=Path(str(record.get("path") or spec.path)),
                action=str(record.get("action") or "failed"),
                detail=str(record.get("detail") or ""),
            )
        )
    return tuple(outcomes)


def _windows_powershell_path() -> str:
    # Windows PowerShell 5.1 ships on every supported Windows and exposes the
    # WScript.Shell COM object; `pwsh` may be absent, so it is not asked for.
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(
        PureWindowsPath(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )


def _run_powershell(script: str, timeout: float = POWERSHELL_TIMEOUT_SECONDS) -> str:
    """Run the authored script and return its stdout.

    ``-EncodedCommand`` carries the script as UTF-16 base64, so nothing in a path
    - a quote, a non-ASCII home directory, a newline - can be re-interpreted by
    the command line on the way in. The child is waited for here, in the call
    that started it, so nothing outlives this function.
    """
    from .subprocess_flags import background_creation_flags

    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [
            _windows_powershell_path(),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=background_creation_flags(),
    )
    stdout = completed.stdout.decode("utf-8", "replace")
    if completed.returncode != 0 and not stdout.strip():
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise ShortcutError(message or f"PowerShell exited {completed.returncode}")
    return stdout


ScriptRunner = Callable[[str], str]


# --------------------------------------------------------------------------- #
# The command
# --------------------------------------------------------------------------- #


def _unsupported(action: str) -> ShortcutReport:
    return ShortcutReport(
        action=action,
        supported=False,
        reason=(
            f"Shell shortcuts are a Windows feature and this host is {platform_label()}. "
            "Nothing was written. Start swe-mux with `swe-mux` (desktop shell) or "
            "`muxd` (daemon), or run `python -m swe_mux --where` to see where both are."
        ),
    )


def _resolve_target(location: InstallLocation, *, remove: bool) -> tuple[Path, tuple[str, ...]]:
    """The executable a shortcut points at, plus anything worth saying about it.

    The `swe-mux` launcher, never `mux` and never the interpreter: it is the tray
    and native window, which is what someone clicking a Start Menu entry wants.
    `install_location` already knows where it is for every install shape - a
    frozen bundle, a uv tool venv, a plain wheel - so this does not re-derive it.
    """
    target = location.executable("swe-mux")
    if target is None:
        raise ShortcutError(
            "This install has no `swe-mux` launcher to point a shortcut at "
            f"(looked in {location.bin_dir}). Reinstall swe-mux, or run "
            "`python -m swe_mux --where` to see what it did ship."
        )
    notes: list[str] = []
    if not remove and _desktop_extra_missing():
        notes.append(
            "The `desktop` extra is not installed, so the shortcut will open nothing "
            "until it is. Install it with `uv tool install swe-mux --with pystray "
            "--with pywebview`, or `pip install swe-mux[desktop]`."
        )
    return target, tuple(notes)


def _desktop_extra_missing() -> bool:
    """Whether the tray/WebView dependencies are absent from this environment.

    Probed with `find_spec` rather than imported: the question is only whether a
    click on the new shortcut will reach a window, and importing `webview` to
    find out would cost seconds and start initialising a GUI toolkit.
    """
    import importlib.util

    for module in ("pystray", "webview"):
        try:
            if importlib.util.find_spec(module) is None:
                return True
        except (ImportError, ValueError):
            return True
    return False


def apply_shortcuts(
    *,
    config: Config,
    slots: Sequence[str] = (SLOT_START_MENU, SLOT_DESKTOP),
    remove: bool = False,
    folders: ShortcutFolders | None = None,
    location: InstallLocation | None = None,
    runner: ScriptRunner | None = None,
) -> ShortcutReport:
    """Create or remove the shortcuts, and report exactly what happened where.

    Removal always addresses all three slots regardless of ``slots``: the command
    that undoes this one has to be able to clean up a login entry the user added
    on a previous run and has since forgotten about, and narrowing it by flag
    would leave exactly that orphan behind.
    """
    from .install_location import INSTALL_FROZEN, detect_install_location

    action = "remove" if remove else "install"
    if not IS_WINDOWS:
        return _unsupported(action)
    resolved = detect_install_location() if location is None else location
    try:
        target, notes = _resolve_target(resolved, remove=remove)
    except ShortcutError as exc:
        return ShortcutReport(action=action, supported=True, reason=str(exc))
    if remove:
        icon, icon_detail = "", ""
    else:
        icon, icon_detail = ensure_icon(
            config.data_dir,
            frozen_executable=target if resolved.kind == INSTALL_FROZEN else None,
        )
    specs = plan_shortcuts(
        slots=ALL_SLOTS if remove else slots,
        folders=resolve_folders() if folders is None else folders,
        target=target,
        # The data directory, matching what the tray anchors its own daemon in:
        # a long-lived process whose cwd is inside the installation locks that
        # tree against an in-place update (`desktop.ensure_daemon`).
        working_directory=config.data_dir,
        icon=icon,
    )
    run = runner if runner is not None else _run_powershell
    try:
        output = run(render_script(specs, remove=remove))
    except (OSError, subprocess.SubprocessError, ShortcutError) as exc:
        outcomes = tuple(
            ShortcutOutcome(
                slot=spec.slot,
                path=spec.path,
                action="failed",
                detail=f"{type(exc).__name__}: {exc}",
            )
            for spec in specs
        )
    else:
        outcomes = parse_script_output(output, specs)
    report = ShortcutReport(
        action=action,
        supported=True,
        target=target,
        icon=icon,
        icon_detail=icon_detail,
        outcomes=outcomes,
        notes=notes,
    )
    _record(config.data_dir, report)
    return report


def _record(data_dir: Path, report: ShortcutReport) -> None:
    """Append every outcome to the lifecycle ledger.

    Shortcuts are install-lifecycle state that outlives the process that wrote
    them, and this command runs where no daemon logging exists, so the rotated
    ledger the tray and daemon already share is the durable record of who put a
    link where - and of a failure the user has since scrolled past.
    """
    for outcome in report.outcomes:
        ledger(
            data_dir,
            f"shortcut {report.action} {outcome.slot}: {outcome.action} {outcome.path}"
            + (f" ({outcome.detail})" if outcome.detail else ""),
        )


def render_report(report: ShortcutReport) -> str:
    """The human rendering: what was written, where, and what it points at."""
    if not report.supported:
        return report.reason
    if not report.outcomes:
        return report.reason or "Nothing to do."
    lines: list[str] = []
    if report.action == "install":
        lines.append(f"target  {report.target}")
        if report.icon_detail:
            lines.append(f"icon    {report.icon_detail}")
        lines.append("")
    width = max(len(outcome.slot) for outcome in report.outcomes)
    for outcome in report.outcomes:
        lines.append(f"{outcome.action.ljust(9)} {outcome.slot.ljust(width)}  {outcome.path}")
        if outcome.detail:
            lines.append(f"{' ' * 10}{' ' * width}  {outcome.detail}")
    for note in report.notes:
        lines += ["", note]
    return "\n".join(lines)


__all__ = [
    "ALL_SLOTS",
    "SHORTCUT_FILENAME",
    "SLOT_DESKTOP",
    "SLOT_START_MENU",
    "SLOT_STARTUP",
    "ShortcutError",
    "ShortcutFolders",
    "ShortcutOutcome",
    "ShortcutReport",
    "ShortcutSpec",
    "apply_shortcuts",
    "ensure_icon",
    "icon_path",
    "parse_script_output",
    "plan_shortcuts",
    "render_report",
    "render_script",
    "resolve_folders",
]
