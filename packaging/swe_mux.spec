from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH)
PROJECT = ROOT.parent

datas = [
    (str(PROJECT / "src" / "swe_mux" / "static"), "swe_mux/static"),
    (str(PROJECT / "src" / "swe_mux" / "assets"), "swe_mux/assets"),
]
binaries = []
hiddenimports = []
for package in ("PIL", "pystray", "webview", "winpty"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(ROOT / "desktop_entry.py")],
    pathex=[str(PROJECT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="swe-mux",
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

# No second executable: Project Action steps are spawned by the supervisor as
# ordinary shells, so nothing from this bundle runs inside a task terminal and a
# live task cannot lock dist/swe-mux against a redeploy swap.
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="swe-mux",
)
