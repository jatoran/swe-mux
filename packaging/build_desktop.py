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
    ROOT / "src" / "swe_mux" / "scrollback.py",
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
    publish_frontend(staging, ROOT / "src" / "swe_mux" / "static")


def build_app_bundle() -> None:
    create_tray_image(256).save(
        ROOT / "packaging" / "swe-mux.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)],
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            str(ROOT / "packaging" / "swe_mux.spec"),
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"Built {ROOT / 'dist' / 'swe-mux' / 'swe-mux.exe'}")
    print(f"Built {ROOT / 'dist' / 'swe-mux' / 'swe-mux-action.exe'}")


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
    return result


def main(argv: Sequence[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.supervisor_only:
        build_supervisor_bundle(force=args.force_supervisor)
        return
    build_frontend()
    build_app_bundle()
    if not args.skip_supervisor:
        build_supervisor_bundle(force=args.force_supervisor)


if __name__ == "__main__":
    main()
