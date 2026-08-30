# The console client, as its own bundle (ROADMAP Phase 23).
#
# Why a third spec rather than a second executable in `swe_mux.spec`, and why a
# console one at all - both were established by refutation rather than by
# preference, so the reasoning is here rather than in a commit message.
#
# The app bundle builds exactly one executable and it is `console=False`. A
# GUI-subsystem process on Windows has no stdout and no stderr at all, which is
# why `desktop.redirect_gui_streams` exists; and `desktop.main` dispatches only
# `--daemon-child`, `--supervisor-child` and two allowlisted `-m` modules. So
# there was no command-line program in the shipped tree to put on PATH, and
# adding `{app}` to PATH would have published a window-opener under a name
# someone types expecting a session table. The client needs a launcher of its
# own, and a launcher of its own has to be `console=True`, because printing
# tables is the entire job.
#
# It could not live beside `swe-mux.exe` in the app bundle either. That
# directory is renamed by the redeploy's staged swap and deleted by the
# installer's `[InstallDelete]` before an upgrade writes the new tree, and a
# `swemux` sitting in a task terminal - which is exactly where a CLI on PATH is
# used - would hold a file open across both. `swe_mux.spec`'s `# No second
# executable` comment records that hazard as the reason the app bundle carries
# only one; putting the CLI in its own directory is what keeps that true while
# still shipping the command.
#
# `swe_mux_supervisor.spec` is the proven pattern for exactly this: a distinct
# lifecycle unit, its own spec, its own EXE/COLLECT, its own `dist/` directory.
# This differs from it in two ways only - `console=True`, and there is no source
# hash gating a rebuild, because nothing here is ever running when the tree is
# replaced and a stale copy is a wrong answer rather than a reaped session.
#
# **Two executables, one payload.** `pyproject.toml` declares `swemux` and `mux`
# over the same `swe_mux.cli:main`, and an installer user must end up with the
# same commands a `pip install` user has. Both EXEs are built from one Analysis
# and collected into one directory, so the second costs one bootloader rather
# than a second copy of the tree; `cli.invoked_as` reads `argv[0]` and prints
# whichever name was typed.
#
# **The directory that goes on PATH holds nothing but those two executables.**
# PyInstaller 6 puts every collected binary under `_internal/`, so the PATH entry
# exposes `swemux.exe`, `mux.exe` and one subdirectory name. That is load-bearing
# rather than incidental: a `PATH` entry is also a DLL search path for every
# process on the machine, and a directory full of `python312.dll`, `libcrypto`
# and `libssl` would shadow those names for unrelated programs. Do not flatten
# `_internal` into this directory, and do not add a `.dll` beside the launchers.
from pathlib import Path

ROOT = Path(SPECPATH)
PROJECT = ROOT.parent

# Nothing is collected wholesale. The client's import closure is the standard
# library plus a handful of this package's own modules: `harness` at module
# scope, and `config`, `doctor_local` and `shortcuts` inside the three commands
# that need them - all of which PyInstaller's bytecode analysis follows on its
# own. `verify_cli_bundle_contents` in packaging/build_desktop.py asserts the
# result, so a dependency that starts arriving through one of those lazy imports
# fails the build here instead of appearing as an unexplained thirty megabytes.
#
# The excludes below are the packages the build environment has installed and
# that a *reachable* import could otherwise drag in. They are named rather than
# discovered because this bundle's value is its size: it is copied into the
# installer beside a 120 MB app bundle, and a client that costs another hundred
# would not be worth shipping. `tkinter` is excluded for the reason
# `swe_mux.spec` gives - PIL's `ImageTk` and the stdlib's `turtle` are both doors
# to 4.5 MB of Tcl/Tk - and the rest are the desktop, voice and parsing closures
# the client never touches.
#
# The three `swe_mux.*` names are the whole reason this bundle is small, and the
# first build without them is the measurement that says so: 143 MiB, carrying
# `ctranslate2`, `hf_xet`, `mypy`, `regex`, `setuptools`, `watchfiles`, `yaml`
# and `cryptography`, with two 16 MB executables. One import does that.
# `cli install-shortcut` imports `swe_mux.shortcuts`, which reaches
# `swe_mux.desktop` for `create_tray_image`, which imports `swe_mux.__main__`,
# which imports `swe_mux.server` - and `server` is the daemon, so the client's
# analysis swallowed the entire application.
#
# Cutting the chain is sound rather than a size trick, because the one call is
# already written to do without it: `shortcuts.ensure_icon` takes the
# `frozen_executable` branch and returns before importing anything when the
# target is a frozen bundle - which is every target this bundle can produce -
# and its import sits inside an `except Exception` that answers "no icon
# written; using the target's own". So the excluded module is unreachable in
# this bundle and harmless if it ever becomes reachable again.
#
# `__main__` and `server` are named beside `desktop` even though excluding
# `desktop` alone cuts today's only path to them. They are the boundary this
# bundle *is* - a client, not a daemon - and naming the boundary is what makes a
# second door to it fail here rather than ship. `build_desktop.
# verify_cli_bundle_contents` asserts the resulting membership and
# `smoke_cli_bundle` runs the built executable, so a reachable import that this
# list turned into a runtime `ModuleNotFoundError` fails the build.
EXCLUDES = [
    "PIL",
    "_tkinter",
    "aiohttp",
    "av",
    "edge_tts",
    "faster_whisper",
    "mcp",
    "numpy",
    "onnxruntime",
    "playwright",
    "pystray",
    "spacy",
    "swe_mux.__main__",
    "swe_mux.desktop",
    "swe_mux.server",
    "tkinter",
    "tree_sitter",
    "tree_sitter_language_pack",
    "webview",
    "winpty",
]

analysis = Analysis(
    [str(ROOT / "cli_entry.py")],
    pathex=[str(PROJECT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)


def launcher(name: str):
    """One console executable over the shared analysis, named `name`.

    UPX is off for the reason `swe_mux.spec` records at length: it is installed
    on no machine that builds this, so the flag only ever means something on the
    day somebody installs the tool - and on that day it hands every shipped
    binary a packer signature, which is one of the best-known antivirus
    heuristics.
    """
    return EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "swe-mux.ico"),
    )


bundle = COLLECT(
    launcher("swemux"),
    launcher("mux"),
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="swe-mux-cli",
)
