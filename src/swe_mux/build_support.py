from __future__ import annotations

import shutil
from pathlib import Path


def publish_frontend(staging: Path, destination: Path) -> None:
    """Publish a complete Vite build without first emptying the live static tree."""
    destination.mkdir(parents=True, exist_ok=True)
    files = [path for path in staging.rglob("*") if path.is_file()]
    # Hashed dependencies must exist before the HTML that references them becomes visible.
    for source in sorted(files, key=lambda path: path.name == "index.html"):
        target = destination / source.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    current = {path.relative_to(staging) for path in files}
    assets = destination / "assets"
    for pattern in ("index-*", "continuity_wasm_bg-*"):
        for stale in assets.glob(pattern):
            if stale.relative_to(destination) in current:
                continue
            try:
                stale.unlink()
            except OSError:
                # A running WebView/daemon can briefly retain an asset handle. It is
                # content-addressed and no longer referenced, so leaving it is safe.
                print(f"Leaving locked stale frontend asset: {stale}")
