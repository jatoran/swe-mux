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
# `tree_sitter_language_pack` ships the compiled grammar shared libraries as
# package data / binaries; without collecting them the frozen app parses no code
# and the Phase 7.9 code-structure graph is silently empty. This is the named
# acceptance check in the roadmap: the grammar binaries must load in the bundle.
#
# `tzdata` is pure data with no importable code path of its own: nothing in the
# source graph references it, so a bundle built without collecting it explicitly
# has no IANA database, and every schedule that names a timezone fails in the
# frozen app while working in a source run (`design/features/scheduled-runs.md`).
#
# `en_core_web_sm`, `spacy`, and `misaki` back the Kokoro TTS G2P (Phase 10.5):
# the spaCy model is a data package `spacy.load()` resolves at runtime, spaCy's
# language modules are imported by registry name rather than by source
# reference, and misaki ships its lexicon as package data. Missing any of the
# three surfaces only in the frozen app ("Can't find model 'en_core_web_sm'"),
# never in a source run.
#
# These four - the three above plus `num2words` - reach the environment through
# the `voice-local` extra, which is optional to install and mandatory to build
# from. `collect_all` on an absent package returns empty lists without failing,
# so a build from an environment without the extra would produce a bundle with
# no TTS and no `_internal/num2words/`. `build_desktop.verify_build_extras_installed`
# refuses that build up front rather than letting the license verification catch
# it minutes later.
#
# `pystray` and `num2words` are the two LGPL packages in the shipped closure
# (`packaging/license_audit.py` allowlists both). `collect_all` defaults to
# `include_py_files=True`, so each lands as readable source under
# `_internal/<pkg>/` instead of being frozen into the executable's archive -
# which is precisely what satisfies the LGPL condition that the recipient be
# able to substitute their own build of the library. `verify_bundle_licenses`
# asserts that property on the built bundle, so dropping either name here is a
# build failure rather than a silent compliance regression. num2words is not
# optional: `misaki.en` imports it at module scope for the Kokoro G2P.
#
# `mcp` is the official MCP client used by the Agent Environment tool-catalog
# fetch to dial a Claude-configured server (`mcp_tools.claude_probe`). It is
# imported lazily, inside the probe, so the daemon never pays for it at startup
# and the drawer degrades to a typed diagnostic if it is missing - which is
# exactly the shape PyInstaller's source graph is least likely to follow on its
# own, and the transport modules it selects between are chosen at call time.
# Collected explicitly so the frozen app can probe at all; without it the failure
# appears only there, as "the MCP client is unavailable".
for package in (
    "PIL",
    "pystray",
    "webview",
    "winpty",
    "tree_sitter",
    "tree_sitter_language_pack",
    "tzdata",
    "spacy",
    "en_core_web_sm",
    "misaki",
    "num2words",
    "mcp",
):
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
    # The av stub satisfies faster-whisper's module-level `import av` after the
    # real package is excluded below; see rthook_av_stub.py for why the real
    # one may never ship (GPL FFmpeg linkage inside the wheel).
    runtime_hooks=[str(ROOT / "rthook_av_stub.py")],
    # Edge TTS is an external integration even when the build environment has
    # the source-install convenience extra. Its Apache bridge is package data;
    # the LGPL client stays in the user's separate Python environment.
    excludes=["av", "edge_tts"],
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
