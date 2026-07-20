from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_mux.build_support import publish_frontend  # noqa: E402
from swe_mux.desktop import create_tray_image  # noqa: E402


def main() -> None:
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
    desktop_exe = ROOT / "dist" / "swe-mux" / "swe-mux.exe"
    action_exe = ROOT / "dist" / "swe-mux" / "swe-mux-action.exe"
    print(f"Built {desktop_exe}")
    print(f"Built {action_exe}")


if __name__ == "__main__":
    main()
