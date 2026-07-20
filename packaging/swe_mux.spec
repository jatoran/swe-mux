from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH)
PROJECT = ROOT.parent

datas = [(str(PROJECT / "src" / "swe_mux" / "static"), "swe_mux/static")]
binaries = []
hiddenimports = []
for package in ("PIL", "pystray", "webview", "winpty"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(ROOT / "desktop_entry.py"), str(ROOT / "action_entry.py")],
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
runtime_scripts = analysis.scripts[:-2]
desktop_scripts = runtime_scripts + analysis.scripts[-2:-1]
action_scripts = runtime_scripts + analysis.scripts[-1:]

executable = EXE(
    pyz,
    desktop_scripts,
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

action_runner = EXE(
    pyz,
    action_scripts,
    [],
    exclude_binaries=True,
    name="swe-mux-action",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "swe-mux.ico"),
)

bundle = COLLECT(
    executable,
    action_runner,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="swe-mux",
)
