# Dedicated PTY-supervisor bundle (SESSION_PRESERVING_RELOAD.md).
#
# The supervisor is a distinct lifecycle unit: it must keep running — with live
# ConPTYs — while dist/swe-mux is rebuilt and replaced. Building it as its own
# artifact in its own directory makes that collision impossible by
# construction: rebuilding the app never touches the files backing a running
# supervisor. This bundle is rebuilt only when the supervisor's small source
# closure changes (see packaging/build_desktop.py), which is also the only case
# where sessions must be reaped first anyway.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH)
PROJECT = ROOT.parent

datas = []
binaries = []
hiddenimports = []
for package in ("winpty", "psutil"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(ROOT / "supervisor_entry.py")],
    pathex=[str(PROJECT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The supervisor imports only pty_host/scrollback/win_jobobj; keep the
    # bundle small and its surface frozen by excluding heavyweight optional
    # packages that PyInstaller might otherwise chase through the environment.
    excludes=["PIL", "pystray", "webview", "aiohttp", "numpy", "av", "faster_whisper"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="swe-mux-supervisor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "swe-mux.ico"),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="swe-mux-supervisor",
)
