import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


ROOT = Path(SPECPATH)
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(PROJECT / "src"))

from build_desktop import voice_closure_top_levels  # noqa: E402

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
# `spacy`, `en_core_web_sm`, `misaki` and `num2words` were collected here until
# 2026-08-29 and are now deliberately absent (ROADMAP Phase 21, Workstream D).
# They were the visible tip of 277 MB of the bundle's 400 MB - the whole
# on-device speech closure - for two features that both default to off, so every
# new user downloaded and let Windows scan the machinery for a capability most
# never enable. `swe_mux.voice_runtime` acquires that closure on an explicit
# press, from pins generated out of `uv.lock`, verified by SHA-256, and unpacked
# into one directory on `sys.path`. `EXCLUDED_VOICE_CLOSURE` below is what keeps
# it out, and it is derived rather than listed.
#
# `pystray` is now the only LGPL package in the shipped closure
# (`packaging/license_audit.py` allowlists it). `collect_all` defaults to
# `include_py_files=True`, so it lands as readable source under
# `_internal/pystray/` instead of being frozen into the executable's archive -
# which is precisely what satisfies the LGPL condition that the recipient be
# able to substitute their own build of the library. `verify_bundle_licenses`
# asserts that property on the built bundle, so dropping the name here is a
# build failure rather than a silent compliance regression. `num2words` carries
# the same obligation and it did not disappear with the package: swe-mux no
# longer distributes num2words at all - the bytes go from PyPI to the user - and
# `voice_runtime._verify_relinkable` proves the copy that lands is readable
# source.
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
    "mcp",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# The distributions `swe_mux.voice_runtime` acquires at first use, as top-level
# module names, read from the metadata of the very packages this build
# environment has installed. Derived rather than listed because the pin table is
# generated from `uv.lock` and a hand-copied excludes list is the copy that
# drifts - and the drift is silent, because PyInstaller would simply follow the
# lazy import and put the package back.
EXCLUDED_VOICE_CLOSURE = list(voice_closure_top_levels())

# The Windows stable-ABI forwarder, collected explicitly because nothing pulls it
# in on purpose and its absence is invisible until first voice use.
#
# PyInstaller collects `python3.dll` beside `python312.dll` when an `abi3`
# extension is in the analysis. Every `abi3` wheel in the acquired closure -
# `tokenizers`, `hf_xet` - links against it by name, and the acquired closure is
# by definition not in the analysis. Measured: a frozen probe without this file
# imported numpy, onnxruntime, CTranslate2 and spaCy from a sidecar perfectly and
# failed `tokenizers` with "DLL load failed while importing tokenizers: The
# specified module could not be found", which names neither the missing file nor
# the reason. `cryptography` happens to ship an abi3 `.pyd` and would collect it
# today, but "happens to" is not a guarantee this may rest on, and
# `verify_bundle_contents` asserts the file is there.
PYTHON3_DLL = Path(sys.base_prefix) / "python3.dll"
if sys.platform == "win32" and PYTHON3_DLL.is_file():
    binaries += [(str(PYTHON3_DLL), ".")]

# Everything importable in the standard library, deliberately, and this is the
# one line in this file that costs megabytes on purpose.
#
# Excluding the voice closure makes its import graph invisible to PyInstaller's
# analysis - which is the point - but the graph did not stop existing. spaCy
# imports `http.cookies`; something in `huggingface_hub` will import something
# else tomorrow. Those imports resolve against *this* bundle's standard library
# at first voice use, in the frozen app, months after the build, with an error
# that names a stdlib module and explains nothing. Measured while proving the
# sidecar loads at all: a frozen probe carrying only its own stdlib closure
# failed on `platform`, then `ctypes`, then `json`, then `http.cookies`, one at a
# time, each revealed only by fixing the one before it.
#
# Guessing the list is the wrong shape of answer - it would be a list nobody
# could re-derive, wrong the first time an acquired package added an import. The
# base bundle owns the standard library; owning all of it is what makes the
# sidecar boundary total rather than probabilistic.
STDLIB_HIDDENIMPORTS = sorted(
    name
    for name in sys.stdlib_module_names
    if not name.startswith("_")
    # `antigravity` opens a web browser on import and `this` prints; the rest are
    # test and demo packages that are large and reach GUI toolkits nothing here
    # has. None of them is reachable from a speech library.
    # `tkinter` drags 4.5 MB of Tcl/Tk data into the bundle and no speech library
    # reaches it; the sidecar probe that produced this list passed without it.
    and name not in {
        "antigravity", "this", "idlelib", "turtledemo", "lib2to3", "test",
        "tkinter", "turtle",
    }
)

analysis = Analysis(
    [str(ROOT / "desktop_entry.py")],
    pathex=[str(PROJECT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + STDLIB_HIDDENIMPORTS,
    hookspath=[],
    hooksconfig={},
    # The av stub satisfies faster-whisper's module-level `import av` after the
    # real package is excluded below; see rthook_av_stub.py for why the real
    # one may never ship (GPL FFmpeg linkage inside the wheel).
    runtime_hooks=[str(ROOT / "rthook_av_stub.py")],
    # A *list* here means PyInstaller's analysis cache can never validate, because
    # `initialize_modgraph` extends the list in place and the saved guts then
    # never match the next run's input. That is a known state rather than an
    # oversight: `build_desktop.build_app_bundle` passes `--clean` on every build,
    # which discards the workpath anyway, and the comment there carries the
    # measurement for why reusing it buys nothing.
    #
    # Edge TTS is an external integration even when the build environment has
    # the source-install convenience extra. Its Apache bridge is package data;
    # the LGPL client stays in the user's separate Python environment.
    # `tkinter` is excluded rather than merely left out of the hidden imports
    # above, because leaving it out is not enough: PIL's `collect_all` reaches
    # `PIL.ImageTk` and the stdlib's own `turtle` imports it, and either door
    # brings 4.5 MB of Tcl/Tk data with it. No speech library reaches it - the
    # frozen probe that established the sidecar loads at all ran without it.
    excludes=["av", "edge_tts", "tkinter", "_tkinter", *EXCLUDED_VOICE_CLOSURE],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

# UPX is off, deliberately, and this is the reasoning rather than a preference.
# It is not installed on any machine that builds this today, so `upx=True` was
# silently a no-op and the setting only ever meant something on the day somebody
# installed the tool. On that day it would do two harmful things at once: add a
# compression pass over a ~400 MB closure to every build, and hand every shipped
# binary a packer signature. UPX packing is one of the best-known antivirus
# heuristics, and the dominant cost of a swe-mux update is already Windows
# scanning a tree of files it has never seen (ROADMAP Phase 21) - so the upside
# is a smaller download and the downside is more of the exact thing that makes
# updates slow, plus a higher chance of being quarantined outright. Pinned False
# so the answer does not change by accident when a toolchain gains a package.
#
# `swe_mux_supervisor.spec` still says `upx=True` and is deliberately left alone,
# including the comment that would have explained why. That file is a member of
# `build_desktop.SUPERVISOR_SOURCES`, whose SHA-256 gates the supervisor bundle,
# and the hash is taken over the file's *bytes* - so a pure comment invalidates
# it exactly as a value change would. `supervisor_bundle_current()` would then
# report the running bundle stale forever, `mux doctor` would advise a rebuild,
# and performing that rebuild reaps every live session. Paying that to pin a flag
# that does nothing is the wrong trade; pin it in the same commit as the next
# deliberate supervisor rebuild, when the reap is being paid for anyway.
UPX = False

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="swe-mux",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=UPX,
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
    upx=UPX,
    upx_exclude=[],
    name="swe-mux",
)
