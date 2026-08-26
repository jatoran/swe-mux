from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_mux.build_support import publish_frontend  # noqa: E402
from swe_mux.desktop import create_tray_image  # noqa: E402

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
    verify_bundle_licenses(output_root / "swe-mux")
    print(f"Built {output_root / 'swe-mux' / 'swe-mux.exe'}")


# Extras the bundle is built from. Optional at *install* time - a source run
# without `voice-local` degrades to the browser speech stack with a typed
# diagnostic - but mandatory at *build* time, and not only because the frozen app
# has no browser fallback for TTS. `num2words` reaches the closure through
# `voice-local` and is LGPL: the relink condition is satisfied by the spec's
# `collect_all` writing it as readable source under `_internal/num2words/`, and
# `collect_all` on a package that is not installed collects nothing and does not
# fail. So a build run in an environment missing the extra produces a bundle that
# `verify_bundle_licenses` then rejects - after several minutes of PyInstaller.
# Checking membership up front turns that into an immediate, legible refusal, and
# `redeploy_desktop`'s preflight runs the same check before it stops anything.
REQUIRED_BUILD_EXTRAS = ("desktop", "voice-local")


def missing_extra_distributions(
    extras: Sequence[str] = REQUIRED_BUILD_EXTRAS,
) -> list[str]:
    """Distributions declared by `extras` that are not installed for this build.

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

    declared = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["optional-dependencies"]
    missing: list[str] = []
    for extra in extras:
        for entry in declared.get(extra, []):
            requirement = Requirement(entry)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            try:
                version(requirement.name)
            except PackageNotFoundError:
                missing.append(f"{requirement.name} (--extra {extra})")
    return missing


def verify_build_extras_installed() -> None:
    """Refuse to build from an environment that cannot produce a compliant bundle."""
    missing = missing_extra_distributions()
    if missing:
        extras = " ".join(f"--extra {extra}" for extra in REQUIRED_BUILD_EXTRAS)
        raise SystemExit(
            "The desktop bundle is built from every distributed extra, and these "
            "are not installed:\n  "
            + "\n  ".join(missing)
            + f"\nRun `uv sync {extras}` and build again. This is a license "
            "requirement as well as a functional one: num2words is LGPL and must "
            "ship as replaceable source under _internal/num2words/, which the "
            "spec's collect_all cannot do for a package that is absent."
        )


# LGPL packages that must ship as replaceable source rather than frozen into the
# executable archive, so a recipient can substitute their own build. Kept in
# sync with `license_audit.ALLOWLIST` by `tests/test_license_audit.py`.
RELINKABLE_LGPL = ("pystray", "num2words")

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
