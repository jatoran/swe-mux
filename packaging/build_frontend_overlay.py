"""Package a built frontend as a hash-verified overlay, and optionally install it.

This is the producer half of `swe_mux/frontend_overlay.py`. It turns the tree a
`vite build` leaves in `src/swe_mux/static` into a signed-shape zip the daemon
will prefer over its own bundled copy, and it is what makes a CSS or JS fix reach
a **frozen desktop app** in seconds instead of through a multi-minute PyInstaller
redeploy of a ~370 MB tree.

    uv run python packaging/build_frontend_overlay.py --build --install

`--build` runs `npm run build` in `frontend/` first; without it the tree already
in `src/swe_mux/static` is packaged as it stands. `--install` hands the finished
archive to the running daemon (`POST /api/frontend/overlay/install`), after which
one session-preserving daemon reload puts it on screen.

**The compatibility pin comes from this checkout, and that is the whole point.**
The manifest records `swe_mux.__version__` *and* a digest over the checkout's own
route table, both read from `src/` beside the frontend that was just built, so
the pin is a statement about the pairing that was actually produced. The daemon
requires exact equality on both and never invents a pin for a payload that
arrived without a manifest.

The route-table digest is the half that does the work here. `__version__` moves
per release while a frozen desktop app is rebuilt from a checkout that moves per
commit, so a frontend built from master today and an app built from master last
week both say the same version while disagreeing about which endpoints exist.
The practical consequence for an operator: **package from the same checkout the
running app was redeployed from.** If the backend has moved since, the overlay is
refused with `api_mismatch` and a redeploy is the honest answer - which is
correct, because a backend change is not something an overlay can carry.

**Do not run `--install` from a worktree.** Worktrees isolate the working tree,
not the runtime: the daemon on 8765 and the data dir at `~/.mux` are
process-wide singletons, so `--install` from a worktree pushes that worktree's
frontend to the operator's live application. Packaging (without `--install`) is
safe anywhere; it only reads and writes inside the checkout.

Deliberately **not** part of `build_desktop.py`, for the reason
`package_desktop_release.py` is not either: a bundle build is for producing an
application, and an overlay is for shipping a change to one that already exists.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_mux import __version__  # noqa: E402
from swe_mux.frontend_overlay import (  # noqa: E402
    ARCHIVE_SUFFIX,
    OverlayRefused,
    build_manifest,
    file_digest,
    pack_overlay,
)


def overlay_archive_name(version: str) -> str:
    """`swe-mux-<version>-ui.zip`.

    A different shape from `release_archive_name` on purpose: an overlay carries
    no platform tag, because a static tree has no platform. Naming it as though
    it did would invite a per-platform artifact that does not need to exist.
    """
    return f"swe-mux-{version}-ui{ARCHIVE_SUFFIX}"


def build_frontend(frontend: Path) -> None:
    """Run the production frontend build, failing loudly if it does not."""
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    print(f"building the frontend in {frontend}")
    result = subprocess.run([npm, "run", "build"], cwd=frontend, check=False)
    if result.returncode != 0:
        raise SystemExit(f"npm run build failed with exit code {result.returncode}")


def install(archive: Path, sha256: str, *, base: str, timeout: float) -> dict[str, object]:
    """Hand the archive to a running daemon and return what it answered.

    The digest is sent even though a local path does not require one: the
    daemon's check is cheap and it turns "the file changed between packaging and
    installing" from a silent success into a refusal.
    """
    body = json.dumps({"archive": str(archive), "sha256": sha256}).encode()
    request = urllib.request.Request(
        base.rstrip("/") + "/api/frontend/overlay/install",
        data=body,
        headers={
            "Content-Type": "application/json",
            # Typing this command is exactly the deliberate act the gesture header
            # stands for, which is why it is spelled here and not defaulted
            # anywhere that could acquire it by accident.
            "X-Mux-User-Gesture": "frontend-overlay-install",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise SystemExit(f"the daemon refused the overlay (HTTP {exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"cannot reach the mux daemon at {base}: {exc.reason}. Is it running? "
            "Pass --url to point elsewhere."
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--static",
        type=Path,
        default=ROOT / "src" / "swe_mux" / "static",
        help="the built frontend tree to package (default: src/swe_mux/static)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="where to write the archive (default: dist/)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="run `npm run build` in frontend/ before packaging",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="hand the finished archive to the running daemon",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8765",
        help="daemon base URL for --install (default: http://127.0.0.1:8765)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="seconds to wait for --install; it hashes and extracts the whole tree",
    )
    args = parser.parse_args(argv)

    if args.build:
        build_frontend(ROOT / "frontend")
    if not args.static.is_dir():
        raise SystemExit(
            f"{args.static} does not exist. Build the frontend first "
            "(cd frontend; npm ci; npm run build), or pass --build."
        )
    try:
        manifest = build_manifest(
            args.static,
            requires_backend=__version__,
            source=f"packaging/build_frontend_overlay.py in {ROOT}",
        )
    except OverlayRefused as refusal:
        raise SystemExit(f"{refusal.reason}: {refusal.message}") from refusal

    archive = pack_overlay(args.static, args.out / overlay_archive_name(__version__), manifest)
    digest = file_digest(archive)
    print(archive)
    print(f"pins        swe-mux {manifest.requires_backend}")
    print(f"api         {manifest.requires_api}")
    print(f"ui build    {manifest.ui_build_id or 'unknown'}")
    print(f"tree        {manifest.tree_digest}")
    print(f"files       {len(manifest.files)}")
    print(f"sha256      {digest}")
    print(f"bytes       {archive.stat().st_size}")

    if not args.install:
        print()
        print("install it with:")
        print(f"  mux ui-overlay install {archive}")
        return 0

    answer = install(archive, digest, base=args.url, timeout=args.timeout)
    print()
    print(answer.get("message", "installed"))
    print("apply it with: mux reload-daemon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
