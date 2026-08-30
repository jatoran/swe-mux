"""Compile the Windows installer from three already-built desktop bundles.

`build_desktop.py` produces `dist/swe-mux`, `dist/swe-mux-supervisor` and
`dist/swe-mux-cli`;
`package_desktop_release.py` turns the first into the portable archive the in-app
updater consumes. This is the third artifact and the only one a person without
Python can use: a single `.exe` that installs all three bundles, registers in
Add/Remove Programs, and can be upgraded over.

    uv run python packaging/build_installer.py
    uv run python packaging/build_installer.py --dist dist --out dist

Three properties, each a constraint rather than a convenience.

**The name comes from `update_install.release_installer_name`, not from here.**
A release's artifacts are looked up by name, and a build script that invents its
own is how a published release stops matching what the code goes looking for.
Deriving both names from the one module is the same rule
`package_desktop_release.py` already follows for the archive.

**It refuses to package a bundle it cannot describe.** The installer carries the
app's `bundle.json`, so the same metadata gate the release archive applies runs
here: the version in the installer's own VERSIONINFO and in its Add/Remove
Programs entry is read out of the built bundle rather than out of this process's
`swe_mux.__version__`, because those two disagree exactly when someone packages
a stale `dist/`.

**Signing is a hook, not a step.** Nothing here signs anything and nothing fails
when no certificate exists, which is today's state (`RELEASE_MANUAL_TASKS.md`
§ 1). When one does, set `SWE_MUX_SIGNTOOL` to the full command line - Inno's
`$f` stands for the file being signed - and this registers it with ISCC and
switches the script's `SignTool`/`SignedUninstaller` block on:

    set SWE_MUX_SIGNTOOL=signtool.exe sign /fd sha256 /tr http://ts.example /td sha256 $f

That is one environment variable and no change to any file. The *payload*
executables (`swe-mux.exe`, `swe-mux-supervisor.exe`) are a separate signing
question and belong to `build_desktop.py`; an installer signed around unsigned
binaries still raises SmartScreen on first launch.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_mux.bundle_archive import file_digest  # noqa: E402
from swe_mux.bundle_metadata import BUNDLE_METADATA_NAME, read_bundle_metadata  # noqa: E402
from swe_mux.update_install import release_installer_name, release_platform_tag  # noqa: E402

#: The script, and the icon it and the installed shortcuts use.
INSTALLER_SCRIPT = ROOT / "packaging" / "installer" / "swe-mux.iss"
ICON = ROOT / "packaging" / "swe-mux.ico"

#: The three bundle directories the installer packs, relative to `--dist`. Named
#: rather than globbed: a `dist/` that happens to hold `swe-mux.prev` from a
#: rolled-back redeploy would otherwise be packaged into the release.
APP_BUNDLE = "swe-mux"
SUPERVISOR_BUNDLE = "swe-mux-supervisor"
#: The console client (ROADMAP Phase 23), and the only bundle whose directory the
#: installer puts on the user's ``PATH``. It is third rather than folded into the
#: app bundle because a `swemux` running in a terminal would otherwise hold
#: `{app}\swe-mux` open against the upgrade that is deleting it.
CLI_BUNDLE = "swe-mux-cli"

#: The environment variable that turns signing on. Absent means unsigned, which
#: is a supported build rather than a degraded one.
SIGNTOOL_ENV = "SWE_MUX_SIGNTOOL"
#: The name this script registers the sign tool under with ISCC. Arbitrary, but
#: it has to be the same on both `/S<name>=` and `/DSignTool=`.
SIGNTOOL_NAME = "swemux"

#: Where Inno Setup 6 installs by default, checked when `iscc` is not on PATH.
#: The GitHub `windows-latest` image puts it on PATH (its own image test asserts
#: `Get-Command iscc` resolves), so this is for a developer machine.
ISCC_FALLBACKS = (
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
)


def find_iscc() -> Path:
    """Locate Inno Setup's compiler, or say exactly how to get it."""
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return Path(found)
    for candidate in ISCC_FALLBACKS:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "Inno Setup's compiler (ISCC.exe) was not found on PATH or in either "
        "default location. Install Inno Setup 6.3 or newer - "
        "`winget install JRSoftware.InnoSetup` or `choco install innosetup` - or "
        "put ISCC.exe on PATH. GitHub's windows-latest image ships it already."
    )


def file_version(version: str) -> str:
    """A four-part numeric VERSIONINFO version for a PEP 440 display version.

    Windows' VERSIONINFO resource takes four integers and nothing else, so
    `0.2.0a1` has to become `0.2.0.0` there while the display version stays
    `0.2.0a1` everywhere a person reads one. Only the leading dotted numeric run
    is taken, so the prerelease segment is dropped rather than encoded: there is
    no ordering of `a1` against `rc1` that four integers can carry honestly, and
    nothing consumes this field for comparison.
    """
    release = re.match(r"\d+(?:\.\d+)*", version.strip())
    if release is None:
        raise SystemExit(
            f"{version!r} does not start with a number, so it is not a version this "
            "can put in a Windows VERSIONINFO resource."
        )
    parts = release.group(0).split(".")[:4]
    return ".".join([*parts, *(["0"] * (4 - len(parts)))])


def build_installer(dist: Path, out: Path) -> Path:
    """Compile the installer for the bundles under `dist`, returning its path."""
    app = dist / APP_BUNDLE
    for bundle in (app, dist / SUPERVISOR_BUNDLE, dist / CLI_BUNDLE):
        if not bundle.is_dir():
            raise SystemExit(
                f"{bundle} does not exist. The installer carries all three bundles, "
                "so build them first with `python packaging/build_desktop.py`."
            )
    if not ICON.is_file():
        # `packaging/swe-mux.ico` is gitignored build output, not a checked-in
        # asset: `build_desktop.build_app_bundle` renders it from
        # `desktop.create_tray_image` on every build. So a fresh clone - a CI
        # checkout, a worktree - has none until that runs, and ISCC's own failure
        # for a missing `SetupIconFile` is a bare "The system cannot find the file
        # specified" naming a line number. Refused here rather than regenerated,
        # because there is one renderer and adding a second is how two icons start
        # to differ.
        raise SystemExit(
            f"{ICON} does not exist. It is generated by "
            "`packaging/build_desktop.py`, which has to run before this script "
            "anyway - the bundles it packs come from the same build."
        )
    metadata, reason = read_bundle_metadata(app)
    if metadata is None:
        raise SystemExit(
            f"{app / BUNDLE_METADATA_NAME} is {reason}. The installer takes its "
            "version from the bundle it packs rather than from this process, so a "
            "bundle that does not describe itself cannot be packaged; rebuild with "
            "packaging/build_desktop.py."
        )
    if metadata.platform != release_platform_tag():
        raise SystemExit(
            f"the bundle describes itself as {metadata.platform} but this host is "
            f"{release_platform_tag()}; build the installer on the host that built it."
        )
    name = release_installer_name(metadata.version, metadata.platform)
    if name is None:
        raise SystemExit(
            f"there is no installer for {metadata.platform}; the Windows installer "
            "is the only one that exists."
        )
    out.mkdir(parents=True, exist_ok=True)
    command = [
        str(find_iscc()),
        f"/DAppVersion={metadata.version}",
        f"/DFileVersion={file_version(metadata.version)}",
        # Absolute, which the script documents as this define's contract and
        # which a relative path silently breaks. ISCC resolves a relative
        # `Source:` against the **.iss file's own directory**, not against its
        # working directory and not against `SourceRoot` - so `--dist dist`
        # reached the compiler as `packaging/installer/dist/swe-mux/*`, and the
        # only symptom was "No files found matching" naming a path nobody
        # passed. `cwd=ROOT` below does not help, and looking like it should is
        # what made this cost a release (v0.1.1, 2026-08-28).
        f"/DAppSource={dist.resolve()}",
        f"/DSourceRoot={ROOT}",
        f"/DIconFile={ICON}",
        f"/F{name.removesuffix('.exe')}",
        f"/O{out}",
    ]
    signtool = os.environ.get(SIGNTOOL_ENV, "").strip()
    if signtool:
        # Both halves or neither: `/S` registers the command, `/D` is what makes
        # the script emit its `SignTool=` line at all.
        command.insert(1, f"/S{SIGNTOOL_NAME}={signtool}")
        command.append(f"/DSignTool={SIGNTOOL_NAME}")
        print(f"signing with {SIGNTOOL_ENV}")
    else:
        print(f"{SIGNTOOL_ENV} is unset; building an unsigned installer")
    subprocess.run([*command, str(INSTALLER_SCRIPT)], cwd=ROOT, check=True)
    installer = out / name
    if not installer.is_file():
        raise SystemExit(
            f"ISCC reported success but wrote no {installer}; the script's "
            "OutputBaseFilename and this script's expected name have diverged."
        )
    return installer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--dist",
        type=Path,
        default=ROOT / "dist",
        help="the directory holding both built bundles (default: dist/)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="where to write the installer (default: dist/)",
    )
    args = parser.parse_args(argv)
    installer = build_installer(args.dist, args.out)
    print(installer)
    print(f"sha256  {file_digest(installer)}")
    print(f"bytes   {installer.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
