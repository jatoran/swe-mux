from __future__ import annotations

import shutil
from pathlib import Path


def publish_frontend(staging: Path, destination: Path) -> None:
    """Publish a complete Vite build without first emptying the live static tree.

    Every precompressed variant in the destination is dropped first. They are derived
    artifacts that Vite does not produce, so a publish that only copies would leave
    the previous build's `.gz` beside the new source, and the daemon serves the `.gz`
    to any client sending `Accept-Encoding: gzip` - which is every browser.

    That is not a stale-cache annoyance, it is a blank screen: `index.html.gz` names
    content-hashed asset files, and the previous build's hashes no longer exist, so
    the page loads and every asset 404s. It also hides from the usual check, because
    `curl` without `--compressed` is served the correct uncompressed `index.html`.

    Dropping them here makes staleness structurally impossible. The caller
    regenerates them; if it forgets, the failure is a larger download rather than an
    application that does not start.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for stale in destination.rglob("*.gz"):
        try:
            stale.unlink()
        except OSError:
            # A running daemon can briefly hold a handle. The file is about to be
            # overwritten by the regenerated variant anyway.
            print(f"Leaving locked precompressed asset: {stale}")
    files = [path for path in staging.rglob("*") if path.is_file()]
    # Hashed dependencies must exist before the HTML that references them becomes visible.
    for source in sorted(files, key=lambda path: path.name == "index.html"):
        target = destination / source.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    current = {path.relative_to(staging) for path in files}
    assets = destination / "assets"
    # Content-addressed names change on every rebuild, so anything large enough to
    # matter has to be swept explicitly or a superseded copy stays for the life of the
    # install. The voice detector alone is ~13 MB per onnxruntime-web version.
    for pattern in (
        "index-*",
        "continuity_wasm_bg-*",
        "ort-wasm-simd-threaded-*",
        "silero_vad_v5-*",
    ):
        for stale in assets.glob(pattern):
            if stale.relative_to(destination) in current:
                continue
            try:
                stale.unlink()
            except OSError:
                # A running WebView/daemon can briefly retain an asset handle. It is
                # content-addressed and no longer referenced, so leaving it is safe.
                print(f"Leaving locked stale frontend asset: {stale}")
