from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_mux import __version__  # noqa: E402
from swe_mux.build_support import publish_frontend  # noqa: E402
from swe_mux.bundle_metadata import bundle_metadata, write_bundle_metadata  # noqa: E402
from swe_mux.desktop import create_tray_image  # noqa: E402
from swe_mux.supervisor import PROTOCOL_VERSION as SUPERVISOR_PROTOCOL_VERSION  # noqa: E402
from swe_mux.update_install import release_platform_tag  # noqa: E402

# The dedicated supervisor bundle's complete source closure. Rebuilding it is
# gated on this hash: a supervisor rebuild requires reaping live sessions
# (its exe would be locked otherwise), so it must never rebuild incidentally.
SUPERVISOR_SOURCES = (
    ROOT / "src" / "swe_mux" / "supervisor.py",
    ROOT / "src" / "swe_mux" / "pty_host.py",
    # The platform seams `pty_host` now delegates to. They are part of the
    # closure for the same reason `pty_host.py` is: a change to how a
    # pseudoterminal is allocated or how a process tree is owned is a change to
    # the supervisor's behaviour, and if the hash does not cover them the gate
    # reports a current bundle while shipping the old PTY implementation.
    ROOT / "src" / "swe_mux" / "host_platform.py",
    ROOT / "src" / "swe_mux" / "pty_backend.py",
    ROOT / "src" / "swe_mux" / "pty_backend_windows.py",
    ROOT / "src" / "swe_mux" / "process_reaper.py",
    # The nested per-session owner, shared with the daemon. It imports nothing
    # but `process_reaper`, which is already here, so sharing it did not widen
    # the closure - the property that made extracting it safe at all.
    ROOT / "src" / "swe_mux" / "nested_job.py",
    ROOT / "src" / "swe_mux" / "scrollback.py",
    ROOT / "src" / "swe_mux" / "timer_resolution.py",
    ROOT / "src" / "swe_mux" / "win_jobobj.py",
    ROOT / "src" / "swe_mux" / "subprocess_flags.py",
    ROOT / "packaging" / "supervisor_entry.py",
    ROOT / "packaging" / "swe_mux_supervisor.spec",
)
SUPERVISOR_DIST = ROOT / "dist" / "swe-mux-supervisor"
SUPERVISOR_EXE = SUPERVISOR_DIST / "swe-mux-supervisor.exe"
SUPERVISOR_HASH_FILE = SUPERVISOR_DIST / ".source-hash"


def supervisor_source_hash() -> str:
    digest = hashlib.sha256()
    for path in SUPERVISOR_SOURCES:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    # Native dependency upgrades must also invalidate the bundle.
    from importlib.metadata import version

    for package in ("pywinpty", "psutil", "pyinstaller"):
        try:
            digest.update(f"{package}=={version(package)}".encode())
        except Exception:
            digest.update(f"{package}==unknown".encode())
    return digest.hexdigest()


def supervisor_bundle_current() -> bool:
    if not SUPERVISOR_EXE.is_file():
        return False
    try:
        return SUPERVISOR_HASH_FILE.read_text(encoding="utf-8").strip() == supervisor_source_hash()
    except OSError:
        return False


def build_frontend() -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("npm is required to build the bundled frontend")
    frontend = ROOT / "frontend"
    vite = frontend / "node_modules" / ".bin" / "vite.cmd"
    if not vite.is_file():
        raise SystemExit("frontend dependencies are missing; run npm install in frontend")
    staging = ROOT / ".runtime" / "desktop-frontend-build"
    shutil.rmtree(staging, ignore_errors=True)
    subprocess.run([npm, "run", "check"], cwd=frontend, check=True)
    subprocess.run(
        [str(vite), "build", "--outDir", str(staging), "--emptyOutDir"],
        cwd=frontend,
        check=True,
    )
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node is required to verify the bundled frontend")
    # This path runs `vite build` directly, so npm's `postbuild` hook does NOT run
    # and every step it would have performed has to be repeated here. Forgetting one
    # is silent: the bundle ships, the daemon starts healthy, and the defect only
    # appears in a browser.
    #
    # Refuse to ship a bundle carrying a dropped-declaration ReferenceError (the
    # defect class that rendered every oh-my-pi pane black).
    subprocess.run(
        [node, "scripts/verify-bundle.mjs", str(staging / "assets")],
        cwd=frontend,
        check=True,
    )
    publish_frontend(staging, ROOT / "src" / "swe_mux" / "static")
    # Regenerate the precompressed variants the publish just dropped. Skipping this
    # left the previous build's `index.html.gz` in place, and because the daemon
    # prefers a `.gz` for any client that accepts gzip, every browser was served an
    # index naming asset hashes that no longer existed: a blank screen on a bundle
    # that reported itself healthy.
    #
    # Since 2026-08-28 there is a second producer - `build_support.
    # precompress_static`, run by the daemon as a startup phase - because the wheel
    # and the sdist no longer *carry* the sidecars (they were 35% of the download).
    # This one is still the right thing to run here: it makes the built tree
    # correct at build time rather than at first start, and it is the step that
    # keeps a *stale* sidecar from ever existing beside a fresh asset. The two
    # cannot drift on which files earn a sidecar, because
    # `test_desktop.py::test_the_python_and_node_precompressors_agree_on_the_rule`
    # reads both definitions and compares them.
    subprocess.run([node, "scripts/compress-static.mjs"], cwd=frontend, check=True)


def build_app_bundle(distpath: Path | None = None) -> None:
    """Build the app bundle; ``distpath`` overrides PyInstaller's output root.

    A staged redeploy builds into a staging distpath while the old app is
    still running (nothing under dist/swe-mux is touched), then swaps the
    finished bundle in after stopping it.
    """
    verify_build_extras_installed()
    create_tray_image(256).save(
        ROOT / "packaging" / "swe-mux.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)],
    )
    output_root = distpath or (ROOT / "dist")
    # `--clean` is unconditional, and since 2026-08-29 that is a measured decision
    # rather than an unexplained default. ROADMAP Phase 21 named it the prime
    # suspect for local rebuild time; it is not, and the numbers are in that
    # section. Three findings, in the order they have to be understood:
    #
    # 1. It costs nothing here because the two things it discards are both
    #    already worthless. PyInstaller's user-level bincache exists to hold
    #    UPX-compressed and stripped binaries, and this spec sets `upx=False` and
    #    `strip=False`, so the cache is a pass-through. And the workpath's
    #    analysis cache never validated anyway - see (2).
    # 2. It never validated because `Analysis(excludes=[...])` is a *list*, and
    #    `PyInstaller.depend.analysis.initialize_modgraph` does
    #    `excludes += ("__main__",)`, which extends that list in place. The saved
    #    guts therefore carry an entry the next run's input does not, PyInstaller
    #    logs "Building because excludes changed", and it re-derives the whole
    #    module graph every time. Passing a tuple fixes it and was measured
    #    fixing it: a no-op rebuild fell from 64s to 12s.
    # 3. That fix is still not worth taking, which is the part worth writing
    #    down. `Analysis`'s guts include an mtime check over the analysed `pure`
    #    and `datas` TOCs, so *any* changed Python source or rebuilt frontend
    #    asset forces a full re-analysis - and every real redeploy has one.
    #    Measured with a source edit before each build, the arms are
    #    indistinguishable (clean 52.5s/58.0s against reuse 55.6s/60.1s). A
    #    12-second rebuild only exists when nothing changed, which is not a
    #    redeploy. Trading an mtime-staleness risk on the most dangerous
    #    operation in the project for a win of zero is the wrong side of the
    #    trade, so the hygienic option stays.
    #
    # Re-measure before reversing this; do not reason about it from the source.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(output_root),
            str(ROOT / "packaging" / "swe_mux.spec"),
        ],
        cwd=ROOT,
        check=True,
    )
    verify_bundle_contents(output_root / "swe-mux")
    verify_voice_closure_absent(output_root / "swe-mux")
    verify_bundle_licenses(output_root / "swe-mux")
    describe_bundle(output_root / "swe-mux")
    print(f"Built {output_root / 'swe-mux' / 'swe-mux.exe'}")


def describe_bundle(bundle_root: Path) -> Path:
    """Write `bundle.json` into a freshly built bundle.

    Every bundle carries it, not only the ones that become releases: the frozen
    updater refuses an archive it cannot interrogate, so a bundle built without
    this is a bundle nobody can update to. Writing it here rather than in the
    release packaging step is what makes that true of a locally-staged redeploy
    as well - which is the tree the next redeploy's `dist/swe-mux` becomes.

    `supervisor_protocol` is read from the running source's `supervisor.py`, so
    it describes the daemon that is actually being packaged. That is the whole
    value of the file (`swe_mux/bundle_metadata.py`).
    """
    return write_bundle_metadata(
        bundle_root,
        bundle_metadata(
            version=__version__,
            supervisor_protocol=SUPERVISOR_PROTOCOL_VERSION,
            platform=release_platform_tag(),
        ),
    )


# Extras the bundle is built from. `voice-local` is still here after the bundle
# stopped shipping it (ROADMAP Phase 21 Workstream D), and the reason inverted
# rather than expired - which is worth stating plainly, because "the bundle no
# longer needs it" is the obvious and wrong conclusion.
#
# It used to be required because `num2words` is LGPL and the spec's `collect_all`
# had to write it as readable source under `_internal/num2words/`; `collect_all`
# on an absent package collects nothing without failing, so a build from an
# environment missing the extra produced a bundle `verify_bundle_licenses` then
# rejected minutes later. That obligation has moved to `voice_runtime`, which
# proves it on the tree it unpacks.
#
# What requires the extra now is the opposite assertion. `verify_bundle_contents`
# proves the voice closure is **absent** from the bundle, and that proof is
# vacuous in an environment that never had the closure to exclude: a build
# without `voice-local` would pass while telling you nothing about whether the
# spec's excludes work. The extra is what makes the absence evidence rather than
# an accident, and the failure it guards - a frozen app that silently reacquires
# 277 MB it was supposed to have shed - is invisible until somebody measures a
# release.
REQUIRED_BUILD_EXTRAS = ("desktop", "voice-local")

# Dependency *groups* the bundle is built from, for the same inverted reason.
# `g2p-model` holds `en-core-web-sm`, which the bundle also no longer carries -
# `voice_models.SpacyModelStore` has acquired it on an explicit press since
# 2026-08-28, and shipping it as well was 15 MB of duplicate. Required here so
# that its exclusion, too, is proven against an environment that has it.
REQUIRED_BUILD_GROUPS = ("g2p-model",)


def missing_extra_distributions(
    extras: Sequence[str] = REQUIRED_BUILD_EXTRAS,
    groups: Sequence[str] = REQUIRED_BUILD_GROUPS,
) -> list[str]:
    """Distributions declared by `extras` or `groups` that are not installed here.

    Membership is read from `pyproject.toml` rather than listed here so adding a
    package to an extra cannot leave the check behind. Presence is decided by
    distribution metadata, not by importing: `en-core-web-sm` imports as
    `en_core_web_sm` and importing spaCy to find out costs seconds.

    Markers are evaluated against *this* interpreter, unlike the license audit's
    walk across every supported platform: this answers "can this machine build a
    compliant bundle", so a Windows-only package is correctly not required when
    the build runs elsewhere.
    """
    import tomllib
    from importlib.metadata import PackageNotFoundError, version

    from packaging.requirements import Requirement

    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared: list[tuple[str, list[Any]]] = [
        (f"--extra {extra}", manifest["project"]["optional-dependencies"].get(extra, []))
        for extra in extras
    ]
    declared += [
        (f"--group {group}", manifest.get("dependency-groups", {}).get(group, []))
        for group in groups
    ]
    missing: list[str] = []
    for flag, entries in declared:
        for entry in entries:
            # PEP 735 allows `{include-group = "..."}` beside plain strings. Both
            # groups named here are flat, and a non-string entry is skipped rather
            # than followed: this check is about what is installed, and an included
            # group is named in its own right or it is not required.
            if not isinstance(entry, str):
                continue
            requirement = Requirement(entry)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            try:
                version(requirement.name)
            except PackageNotFoundError:
                missing.append(f"{requirement.name} ({flag})")
    return missing


def verify_build_extras_installed() -> None:
    """Refuse to build from an environment that cannot produce a compliant bundle."""
    missing = missing_extra_distributions()
    if missing:
        flags = " ".join(
            [f"--extra {extra}" for extra in REQUIRED_BUILD_EXTRAS]
            + [f"--group {group}" for group in REQUIRED_BUILD_GROUPS]
        )
        raise SystemExit(
            "The desktop bundle is built from every distributed extra and group, "
            "and these are not installed:\n  "
            + "\n  ".join(missing)
            + f"\nRun `uv sync {flags}` and build again. The voice extra is "
            "required in order to prove its own absence: the bundle deliberately "
            "no longer ships the speech closure, and verify_bundle_contents "
            "cannot demonstrate that a package was excluded in an environment "
            "that never had it to exclude. voice_closure_top_levels() also reads "
            "these distributions' metadata to build the spec's excludes list, so "
            "a build without them excludes too little and silently reships the "
            "277 MB this bundle was made to shed."
        )


# ---------------------------------------------------------------------------
# The acquired voice closure, and keeping it out of the bundle
# ---------------------------------------------------------------------------


def _holds_importable_code(dist: Any, name: str) -> bool:
    """Whether `name`, as installed beside `dist`, is something Python can import.

    Needed because `top_level.txt` is written by whatever built the wheel and is
    not always true: `tqdm` declares `images`, which is a directory of GIFs its
    README links to. An `excludes` entry naming it would exclude nothing while
    reading like a rule, and the next person to see the list would trust it.
    """
    try:
        located = Path(str(dist.locate_file(name)))
    except Exception:  # noqa: BLE001 - a metadata backend that cannot locate is not a vote
        return True
    if located.is_file():
        return located.suffix in {".py", ".pyd", ".so", ".dylib"}
    if not located.is_dir():
        return False
    return any(
        child.suffix in {".py", ".pyd", ".so", ".dylib"} for child in located.rglob("*")
    )


def voice_closure_top_levels() -> tuple[str, ...]:
    """Top-level import names of every distribution acquired at first use.

    Derived from the installed distributions named by
    `swe_mux.voice_wheels.DISTRIBUTIONS` rather than listed here, for the reason
    every list in this file is derived: a hand-written copy of a generated table
    is the copy that drifts, and the drift is silent - PyInstaller would simply
    collect a package the spec forgot to name and the bundle would grow back.

    This is why `REQUIRED_BUILD_EXTRAS` still demands `voice-local`. Reading the
    metadata needs the distributions installed; an environment without them
    returns a short list, excludes too little, and produces a bundle that passes
    every check while carrying whatever was importable.
    """
    from importlib.metadata import PackageNotFoundError, distribution

    from swe_mux.voice_wheels import DISTRIBUTIONS

    names: set[str] = set()
    missing: list[str] = []
    for dist_name in DISTRIBUTIONS:
        try:
            dist = distribution(dist_name)
        except PackageNotFoundError:
            missing.append(dist_name)
            continue
        top_level = dist.read_text("top_level.txt")
        if top_level:
            names.update(
                name
                for name in (line.strip() for line in top_level.splitlines())
                if name and _holds_importable_code(dist, name)
            )
            continue
        # No `top_level.txt` - modern wheels often omit it. Derive the names from
        # the recorded file list instead, skipping the metadata directory and the
        # `.data` payload, neither of which is importable.
        for recorded in dist.files or []:
            head = recorded.parts[0]
            if head.endswith((".dist-info", ".data")) or head in {"..", "bin", "Scripts"}:
                continue
            # Only heads that actually contain importable code. `tqdm` records an
            # `images/` directory of GIFs used by its README, and an `excludes`
            # entry naming it would read like a rule while excluding nothing -
            # the kind of line somebody later trusts.
            if recorded.suffix not in {".py", ".pyd", ".so", ".dylib"}:
                continue
            names.add(head[:-3] if head.endswith(".py") else head)
    if missing:
        raise SystemExit(
            "The voice closure cannot be excluded from a bundle built without it: "
            + ", ".join(missing)
            + "\nRun `uv sync --extra desktop --extra voice-local --group package`. "
            "See REQUIRED_BUILD_EXTRAS for why the extra is required in order to "
            "prove its own absence."
        )
    # `numpy.libs` and friends are directories PyInstaller creates, never module
    # names; excluding a dotted name would be a no-op that reads like a rule.
    return tuple(sorted(name for name in names if "." not in name and name.isidentifier()))


# LGPL packages that must ship as replaceable source rather than frozen into the
# executable archive, so a recipient can substitute their own build. Kept in
# sync with `license_audit.ALLOWLIST` by `tests/test_license_audit.py`.
#
# `num2words` was here until 2026-08-29 and its obligation did not lapse - it left
# the *distribution*. The bundle no longer contains it; `swe_mux.voice_runtime`
# fetches its wheel from PyPI on an explicit press, so the bytes travel from the
# index to the user and what this project ships is a URL and a hash. The relink
# condition still has to hold for the copy that lands, and
# `voice_runtime._verify_relinkable` asserts it on the unpacked tree, which is the
# same assertion in the place the package now is. `verify_voice_closure_absent`
# is the other half: it fails the build if `num2words` ever ships again without
# this list being updated to match.
RELINKABLE_LGPL = ("pystray",)

# Optional integrations whose client code deliberately remains outside the
# frozen artifact. Edge TTS runs through the shipped Apache bridge under a
# user-managed Python, so finding the LGPL package here means the distribution
# boundary regressed even when relinking would have been technically possible.
EXTERNAL_ONLY_ARTIFACTS = (
    ("edge_tts", "the optional LGPL Edge TTS client must remain external"),
)

# Payloads the audit found hiding inside wheels that declare a permissive
# license. Each is checked by artifact name because the declaration lies: PyAV
# declares BSD-3-Clause and links GPL x264/x265, and the espeak-ng family enters
# through packages that declare Apache-2.0 or MIT.
# PyAV itself is covered by the dedicated `verify_no_gpl_av` below.
FORBIDDEN_ARTIFACTS = (
    ("espeakng_loader", "the espeak-ng loader (GPL data payload)"),
    ("phonemizer", "phonemizer/phonemizer-fork (drags in espeak-ng)"),
    ("espeak-ng-data", "espeak-ng voice data"),
)
# Deliberately matched as shared libraries only. `misaki/espeak.py` is misaki's
# own optional wrapper and ships harmlessly - the G2P is constructed with
# `fallback=None` and the loader it would need is forbidden above, so it can
# never acquire a backend. A bare `*espeak*` glob would fail every build over a
# file that is inert by construction.
FORBIDDEN_BINARY_GLOBS = (
    ("**/*x264*", "GPL x264"),
    ("**/*x265*", "GPL x265"),
    ("**/avcodec*", "FFmpeg avcodec"),
    ("**/*espeak*.dll", "an espeak-ng shared library"),
    ("**/*espeak*.so*", "an espeak-ng shared library"),
    ("**/*espeak*.dylib", "an espeak-ng shared library"),
)


def verify_bundle_licenses(bundle_root: Path) -> None:
    """Prove the bundle's license posture rather than asserting it in a doc.

    Phase 10.5. Three properties, each of which has silently regressed or could:

    1. No GPL payload by artifact name (`verify_no_gpl_av` plus the espeak
       family). Declared metadata does not describe shipped binaries, which is
       the audit's central lesson, so this reads the built tree.
    2. Every allowlisted LGPL package ships as readable source under
       `_internal/<pkg>/`. That is the LGPL relink condition, and it holds only
       because those packages are in the spec's `collect_all` loop; removing one
       would leave the notices file promising something untrue.
    3. `misaki`'s espeak module never acquires a working backend, checked by the
       absence of the loader above rather than by importing anything.
    4. External-only integrations do not enter the frozen artifact.
    """
    verify_no_gpl_av(bundle_root)
    internal = bundle_root / "_internal"

    external = [
        f"{internal / name} ({why})"
        for name, why in EXTERNAL_ONLY_ARTIFACTS
        if (internal / name).exists()
    ]
    if external:
        raise SystemExit(
            "External integration regression: a user-managed payload entered the bundle:\n  "
            + "\n  ".join(external)
        )

    offenders = [
        f"{internal / name} ({why})"
        for name, why in FORBIDDEN_ARTIFACTS
        if (internal / name).exists()
    ]
    for pattern, why in FORBIDDEN_BINARY_GLOBS:
        offenders += [f"{path} ({why})" for path in sorted(internal.glob(pattern))[:3]]
    if offenders:
        raise SystemExit(
            "Copyleft regression: a forbidden payload entered the bundle:\n  "
            + "\n  ".join(offenders)
            + "\nSee packaging/license_audit.py for why each of these may never ship."
        )

    missing = [
        name
        for name in RELINKABLE_LGPL
        if not sorted((internal / name).glob("*.py"))
    ]
    if missing:
        raise SystemExit(
            "LGPL relink regression: "
            + ", ".join(missing)
            + f" must ship as readable source under {internal} so a recipient can "
            "replace it, which is what THIRD-PARTY-NOTICES.md promises. Add the "
            "name back to the collect_all loop in packaging/swe_mux.spec."
        )


def verify_no_gpl_av(bundle_root: Path) -> None:
    """Fail the build if PyAV's GPL FFmpeg payload re-entered the bundle.

    Phase 10.5: the spec excludes `av` and satisfies faster-whisper with a
    runtime stub. Regenerating the spec or upgrading PyInstaller can silently
    undo that, and nothing at runtime would notice — the stub simply loses the
    race to the real module. The bundle check is the regression gate.
    """
    internal = bundle_root / "_internal"
    offenders = [
        path
        for path in (internal / "av.libs", internal / "av")
        if path.exists()
    ]
    offenders += sorted(internal.glob("av/**/*.pyd")) if (internal / "av").exists() else []
    if offenders:
        raise SystemExit(
            "GPL closure regression: PyAV was collected into the bundle "
            f"({', '.join(str(item) for item in offenders[:3])}). The swe_mux.spec "
            "excludes=['av'] plus rthook_av_stub.py must keep it out."
        )


# Every top-level package directory the app bundle is expected to carry under
# `_internal/`, and nothing else. Measured from a build in the exact closure CI
# uses (`uv sync --extra desktop --extra voice-local --group package`) on
# 2026-08-29: 30 packages, 79 MB under `_internal/`, in a 120 MB bundle.
#
# It was 51 packages and 371 MB the same day, in a 400 MB bundle. The difference
# is ROADMAP Phase 21 Workstream D: the on-device speech closure - spacy, thinc,
# blis, cymem, murmurhash, preshed, srsly, ctranslate2, tokenizers, hf_xet,
# onnxruntime, numpy, numpy.libs, misaki, num2words, en_core_web_sm, regex,
# wrapt, yaml, markupsafe and setuptools - is no longer shipped. It is acquired
# on an explicit press by `swe_mux.voice_runtime`, from pins generated out of
# `uv.lock`. Reading this diff is the record of exactly what stopped shipping.
#
# `setuptools` leaving is worth noting because a previous audit recorded it as an
# unexplained passenger: it was not one. It was a real edge of the voice closure
# (spaCy and thinc reach it), and removing the closure removed it.
#
# This exists because a bundle's *membership* was previously unchecked, and
# membership is decided by what happens to be importable in the build venv rather
# than by anything this repository declares. `dist/swe-mux` as built 2026-08-27
# carried 101 MB of `playwright/driver` - collected through the lazy `import
# playwright` in `preview_capture.py` - while `license_audit.py` states plainly
# that `preview-capture` does not ship. Nothing caught it: `verify_bundle_licenses`
# reads the tree for copyleft payloads and Playwright is Apache-2.0, so it passed.
# A hundred megabytes of files a user's machine has never seen is also the exact
# thing that makes an update multi-minute (ROADMAP Phase 21), so this is a size
# gate as much as a hygiene one.
#
# `*.dist-info` directories are deliberately not listed: their names carry version
# numbers, so a manifest containing them would churn on every dependency bump and
# be edited without being read. The package they describe is checked here in its
# own right.
#
# One passenger is still recorded rather than removed. `mypy`, `mypyc`'s `librt`
# and `ast_serialize` are 3.8 MB of mypyc-compiled `.pyd` reached through
# `pydantic/mypy.py` - a static-analysis plugin nothing imports at runtime.
# (`thinc/mypy.py` was the other door and it has left with the voice closure.)
# Excluding it is a spec change that has to be proven against a running frozen
# app, which a worktree cannot do; it is 3% of this bundle rather than 1% of the
# old one, so it is more worth doing than it was. Listed here so the next person
# sees it.
#
# The stdlib is a deliberate passenger too, and a larger one: `swe_mux.spec` adds
# every importable standard-library module as a hidden import, because excluding
# the voice closure makes its import graph invisible to PyInstaller's analysis
# while the graph itself keeps existing. That reasoning is in the spec, next to
# the line that does it. It shows up here as extension modules
# (`_msi.pyd`, `_wmi.pyd`, `winsound.pyd`) rather than as package directories.
EXPECTED_BUNDLE_PACKAGES = frozenset(
    {
        "PIL",
        "aiohttp",
        "ast_serialize",
        "certifi",
        "charset_normalizer",
        "clr_loader",
        "cryptography",
        "frozenlist",
        "jsonschema",
        "jsonschema_specifications",
        "librt",
        "mcp",
        "multidict",
        "mypy",
        "propcache",
        "psutil",
        "pydantic_core",
        "pystray",
        "pythonnet",
        "pywin32_system32",
        "rpds",
        "swe_mux",
        "tree_sitter",
        "tree_sitter_language_pack",
        "tzdata",
        "watchfiles",
        "webview",
        "win32",
        "winpty",
        "yarl",
    }
)


def bundle_top_level_packages(bundle_root: Path) -> dict[str, int]:
    """Top-level package directories under `_internal/`, mapped to size in bytes.

    Directories only, and `*.dist-info` skipped: those are the units a stray
    dependency arrives as, and the ones whose names do not carry a version.
    """
    internal = bundle_root / "_internal"
    return {
        entry.name: sum(item.stat().st_size for item in entry.rglob("*") if item.is_file())
        for entry in sorted(internal.iterdir())
        if entry.is_dir() and not entry.name.endswith(".dist-info")
    }


def verify_stable_abi_forwarder(bundle_root: Path) -> None:
    """Fail the build when `python3.dll` is absent from a Windows bundle.

    Not hygiene. The acquired voice closure (`swe_mux.voice_runtime`) contains
    `abi3` wheels - `tokenizers` and `hf_xet` today - whose extension modules link
    against Windows' stable-ABI forwarder by name. PyInstaller collects that file
    when an `abi3` extension is *in the analysis*, and the acquired closure is by
    definition not in it.

    So the file is here for the benefit of code the bundle does not contain, which
    makes it exactly the kind of dependency that disappears without anyone
    noticing: nothing in the base app imports it, no test exercises it, and its
    absence surfaces as `ImportError: DLL load failed while importing tokenizers`
    at first dictation, in the frozen app, naming neither the file nor the reason.
    Measured on a frozen probe: with it, the whole closure loads; without it,
    every non-abi3 package loads and the two abi3 ones do not.

    `cryptography` also ships an `abi3` `.pyd` and would pull the forwarder in
    today. That is a coincidence of the base closure and not a guarantee, so the
    spec collects it explicitly and this asserts the result.
    """
    if not (bundle_root / "_internal" / "python312.dll").is_file():
        return  # not a Windows bundle; the forwarder is a Windows concept
    if (bundle_root / "_internal" / "python3.dll").is_file():
        return
    raise SystemExit(
        "Stable-ABI forwarder missing: _internal/python3.dll is not in the bundle. "
        "Every abi3 wheel in the acquired voice closure links against it by name, "
        "and nothing in the bundle's own analysis pulls it in. See the PYTHON3_DLL "
        "block in packaging/swe_mux.spec."
    )


def verify_voice_closure_absent(
    bundle_root: Path, closure: Collection[str] | None = None
) -> None:
    """Fail the build when the acquired speech closure rode along anyway.

    `verify_bundle_contents` already rejects any unexpected top-level package, so
    this is the same assertion said a second time - deliberately, because the two
    fail differently. That check says "a package you did not declare is here" and
    points at the build venv; this one says "the package you deliberately stopped
    shipping is back", and names the mechanism (`EXCLUDED_VOICE_CLOSURE`) and the
    consequence (a 400 MB bundle again). The failure being guarded is a silent
    regrowth that only a size measurement would otherwise reveal.

    `closure` is injectable for the same reason `verify_bundle_contents`'s
    `expected` is: the default reads installed distribution metadata, which a
    build environment has and a bare `uv sync` does not - and the assertion this
    function makes is about a *bundle*, not about the machine checking it. CI's
    Linux and macOS legs sync no extras on purpose, and the test that proves this
    refusal reports the right thing should run there most of all.
    """
    internal = bundle_root / "_internal"
    names = voice_closure_top_levels() if closure is None else closure
    present = sorted(name for name in names if (internal / name).exists())
    if present:
        raise SystemExit(
            "Voice closure regression: the bundle carries packages that are "
            "supposed to be acquired at first use:\n  "
            + "\n  ".join(present)
            + "\nThese are ~277 MB of the 400 MB this bundle used to be (ROADMAP "
            "Phase 21 Workstream D). EXCLUDED_VOICE_CLOSURE in packaging/"
            "swe_mux.spec is what keeps them out; a package that returns has "
            "either been added to a `collect_all` loop or reached through a hook."
        )


def verify_bundle_contents(
    bundle_root: Path,
    expected: Collection[str] = EXPECTED_BUNDLE_PACKAGES,
) -> None:
    """Fail the build when the bundle's package set is not the expected one.

    Both directions are errors, and for different reasons. An **extra** package
    means the build venv leaked something the distributed closure does not
    declare, which costs download size, scan time on every user's machine, and
    possibly a license obligation nobody audited. A **missing** package means a
    `collect_all` entry stopped collecting - the failure mode that shows up only
    in the frozen app and only on one feature (no IANA database, no G2P model, no
    tree-sitter grammars), which is why several of them are collected explicitly
    in the first place.
    """
    sizes = bundle_top_level_packages(bundle_root)
    total = sum(sizes.values())
    print(f"Bundle: {len(sizes)} top-level packages, {total / 1_000_000:.0f} MB under _internal/")

    verify_stable_abi_forwarder(bundle_root)

    unexpected = sorted(
        (size, name) for name, size in sizes.items() if name not in expected
    )
    if unexpected:
        listing = "\n  ".join(
            f"{name} ({size / 1_000_000:.1f} MB)" for size, name in reversed(unexpected)
        )
        raise SystemExit(
            "Bundle membership regression: the build collected a top-level package "
            "the shipped closure does not declare:\n  "
            + listing
            + "\nA package reaches the bundle because PyInstaller found it in the "
            "*build venv*, not because this repository ships it - which is how 101 MB "
            "of Playwright once rode along behind a lazy import. Build from the "
            "declared closure (`uv sync --extra desktop --extra voice-local --group "
            "package`); or add it to `excludes` in packaging/swe_mux.spec if it must "
            "not ship; or, if it genuinely ships now, add the name to "
            "EXPECTED_BUNDLE_PACKAGES here with a line saying why. If you are "
            "building on a platform this manifest was never measured on, record that "
            "platform's set rather than widening this one."
        )

    missing = sorted(name for name in expected if name not in sizes)
    if missing:
        raise SystemExit(
            "Bundle membership regression: an expected top-level package is absent:\n  "
            + "\n  ".join(missing)
            + "\nMost of these fail only in the frozen app and only on one feature, "
            "which is why they are collected explicitly. Check that "
            "packaging/swe_mux.spec still names it in the collect_all loop and that "
            "the build environment still has it; drop it from "
            "EXPECTED_BUNDLE_PACKAGES only when it deliberately no longer ships."
        )


def build_supervisor_bundle(*, force: bool = False) -> bool:
    """Build dist/swe-mux-supervisor when its source closure changed.

    Returns True when a (re)build happened. A running supervisor locks its exe;
    in that case PyInstaller fails and we surface the reap-first remedy instead
    of a raw traceback.
    """
    if not force and supervisor_bundle_current():
        print(f"Supervisor bundle up to date: {SUPERVISOR_EXE}")
        return False
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                str(ROOT / "packaging" / "swe_mux_supervisor.spec"),
            ],
            cwd=ROOT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if SUPERVISOR_EXE.is_file():
            raise SystemExit(
                "Supervisor bundle rebuild failed. If a supervisor is running it locks "
                "its exe; stop it first with `muxd --shutdown` (this reaps all "
                "sessions), then rebuild."
            ) from exc
        raise
    SUPERVISOR_HASH_FILE.write_text(supervisor_source_hash() + "\n", encoding="utf-8")
    print(f"Built {SUPERVISOR_EXE}")
    return True


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build the swe-mux desktop distribution")
    result.add_argument(
        "--supervisor-only",
        action="store_true",
        help="build only the dedicated PTY supervisor bundle",
    )
    result.add_argument(
        "--force-supervisor",
        action="store_true",
        help="rebuild the supervisor bundle even when its sources are unchanged",
    )
    result.add_argument(
        "--skip-supervisor",
        action="store_true",
        help="never touch the supervisor bundle in this run",
    )
    result.add_argument(
        "--skip-frontend",
        action="store_true",
        help="bundle the already-built src/swe_mux/static as-is (backend-only redeploy)",
    )
    result.add_argument(
        "--app-distpath",
        type=Path,
        default=None,
        help="PyInstaller distpath for the app bundle (staged redeploys build "
        "outside dist/ and swap in afterwards); the supervisor bundle always "
        "goes to dist/swe-mux-supervisor",
    )
    return result


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.supervisor_only:
        build_supervisor_bundle(force=args.force_supervisor)
        return
    if not args.skip_frontend:
        build_frontend()
    build_app_bundle(distpath=args.app_distpath)
    if not args.skip_supervisor:
        build_supervisor_bundle(force=args.force_supervisor)


if __name__ == "__main__":
    main()
