from __future__ import annotations

import gzip
import os
import shutil
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

#: What earns a precompressed sidecar. Both halves are a policy rather than a
#: measurement, and both are deliberately conservative: below a kilobyte gzip's
#: own header and trailer eat most of the saving, and a suffix not listed here is
#: either already compressed (`.png`, `.mp3`, `.onnx`) or not served from this
#: tree at all.
PRECOMPRESS_MIN_BYTES = 1024
PRECOMPRESS_SUFFIXES = frozenset(
    {".css", ".html", ".js", ".json", ".mjs", ".svg", ".wasm", ".webmanifest"}
)
#: Level 9 rather than the default 6. This runs once per install, and the
#: measured difference on the largest member (the 10.7 MiB ONNX runtime) is
#: 0.69 s against 0.43 s - a quarter of a second, once, for bytes every client
#: on a phone link downloads.
PRECOMPRESS_LEVEL = 9

_READ_CHUNK = 1 << 20
_GZIP_TRAILER_BYTES = 8


@dataclass(frozen=True)
class PrecompressResult:
    """What one pass over the static tree did, in the units a log line wants."""

    written: int
    kept: int
    orphans_removed: int
    failed: int
    source_bytes: int
    encoded_bytes: int
    seconds: float

    @property
    def changed(self) -> bool:
        return bool(self.written or self.orphans_removed or self.failed)


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

    Dropping them here makes staleness structurally impossible. `precompress_static`
    rebuilds them on the next daemon start; if that never runs, the failure is a
    larger download rather than an application that does not start.
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


def wants_sidecar(path: Path, size: int) -> bool:
    """Whether `path` is large enough and of a kind worth precompressing."""
    return size >= PRECOMPRESS_MIN_BYTES and path.suffix.lower() in PRECOMPRESS_SUFFIXES


def _source_signature(path: Path) -> tuple[int, int] | None:
    """`(crc32, length)` of `path`, or None when it cannot be read.

    The same two numbers a gzip member records about its own input, which is what
    makes the comparison in `sidecar_is_current` exact rather than a heuristic.
    """
    crc = 0
    length = 0
    try:
        with path.open("rb") as source:
            # unsupervised-loop-ok: bounded synchronous file read
            while True:
                chunk = source.read(_READ_CHUNK)
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
                length += len(chunk)
    except OSError:
        return None
    return crc, length & 0xFFFFFFFF


def _sidecar_signature(path: Path) -> tuple[int, int] | None:
    """`(crc32, isize)` from a gzip member's trailer, or None if unreadable."""
    try:
        size = path.stat().st_size
        if size < _GZIP_TRAILER_BYTES:
            return None
        with path.open("rb") as sidecar:
            sidecar.seek(size - _GZIP_TRAILER_BYTES)
            trailer = sidecar.read(_GZIP_TRAILER_BYTES)
    except OSError:
        return None
    if len(trailer) != _GZIP_TRAILER_BYTES:
        return None
    return (
        int.from_bytes(trailer[:4], "little"),
        int.from_bytes(trailer[4:], "little"),
    )


def sidecar_is_current(source: Path, sidecar: Path) -> bool:
    """Whether `sidecar` is the gzip of exactly the bytes now in `source`.

    A stale `.gz` outliving its source is a blank screen rather than a slow page
    (`publish_frontend` above says why), so "regenerate" has to be decided by
    content and never by a timestamp. Two obvious alternatives were rejected:
    an mtime comparison loses on a host whose timer granularity is 15.6 ms - the
    same constant behind two CI failures this repository has already paid for -
    and a sidecar manifest is a second file that can drift from the first.

    The gzip container answers it directly. Every member ends with the CRC-32 and
    the length (mod 2^32) of the data it was made from, so eight bytes off the end
    of the sidecar and one CRC pass over the source settle it exactly, with no
    state kept anywhere. Decompressing the sidecar instead would read the same
    bytes and answer the same question more slowly.
    """
    recorded = _sidecar_signature(sidecar)
    if recorded is None:
        return False
    return recorded == _source_signature(source)


def _write_sidecar(source: Path, sidecar: Path) -> int:
    """Compress `source` to `sidecar` atomically; returns the encoded size.

    Atomic because the daemon that calls this is already serving: a reader must
    see either the previous sidecar or the complete new one, never a truncated
    file being written, which decompresses to a partial document.

    The temporary carries the process id because two daemons can share one static
    tree - a second isolated daemon against a source checkout, or a worker of a
    parallel test run - and a shared temporary name is the one way two correct
    writers produce an incorrect file. With distinct names each writes a complete
    member and the rename settles which one lands; the output is deterministic, so
    they are byte-identical anyway.
    """
    temporary = sidecar.with_name(f"{sidecar.name}.{os.getpid()}.partial")
    try:
        # `filename=""` and `mtime=0` keep the output a pure function of the
        # input, so two installs of the same build produce byte-identical
        # sidecars. `GzipFile` does not close a `fileobj` it was handed, so the
        # raw handle owns its own `with` - the alternative leaks it into the
        # garbage collector, which under `filterwarnings = ["error"]` reddens
        # whichever test happens to be running when the collector gets to it.
        with (
            source.open("rb") as reader,
            temporary.open("wb") as raw,
            gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=PRECOMPRESS_LEVEL,
                fileobj=raw,
                mtime=0,
            ) as writer,
        ):
            shutil.copyfileobj(reader, writer, _READ_CHUNK)
        temporary.replace(sidecar)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return sidecar.stat().st_size


def precompress_static(root: Path) -> PrecompressResult:
    """Bring every precompressed sidecar under `root` up to date with its source.

    aiohttp's `add_static` does no on-the-fly compression; `FileResponse` serves a
    `.gz` sitting beside the file to any client that sent `Accept-Encoding: gzip`,
    which is every browser. So the sidecars are not an optimisation the daemon can
    take or leave - without them a phone over Tailscale fetches the 10.7 MiB ONNX
    runtime uncompressed.

    They are simply not worth *shipping*: they were 4.43 MiB of a 12.70 MiB wheel,
    re-compressing content the wheel's own zip container had already compressed
    (`.docs/development/DEPENDENCY_AUDIT_2026-08-28.md` § 1). Regenerating all 40
    costs a measured 0.93 s, once, on the first start after an install or an
    upgrade; every start after that is a stat-and-CRC pass that writes nothing.

    This is the second producer, not the only one: `frontend/scripts/
    compress-static.mjs` still writes them at build time, because a `npm run
    build` that left the previous build's `index.html.gz` beside fresh
    content-hashed assets is a blank screen on a daemon that is already running,
    and nothing restarts it in that loop. So in a checkout and in the desktop
    bundle this call finds everything current and does nothing; the install that
    needs it is the one from a wheel, which carries no sidecars at all. The two
    definitions of *which* files earn one cannot drift, because
    `test_desktop.py::test_the_python_and_node_precompressors_agree_on_the_rule`
    reads both and compares them.

    Idempotent by construction: a sidecar is kept when its gzip trailer records
    exactly the current source's CRC-32 and length, rewritten otherwise, and
    removed when its source is gone. Nothing here raises - a static tree on a
    read-only filesystem is a slower UI, not a daemon that will not start - so the
    result counts failures rather than propagating the first one.
    """
    started = time.monotonic()
    written = kept = orphans = failed = 0
    source_bytes = encoded_bytes = 0
    if not root.is_dir():
        return PrecompressResult(0, 0, 0, 0, 0, 0, 0.0)

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".gz":
            # An asset that a later build removed leaves its sidecar behind, and
            # an in-place upgrade never sweeps one. Nothing serves it - a `.gz` is
            # only ever reached through a request for its plain sibling - so this
            # is disk hygiene, and a failure to unlink is not worth reporting as
            # an error.
            if not path.with_name(path.name[: -len(".gz")]).is_file():
                try:
                    path.unlink()
                    orphans += 1
                except OSError:
                    pass
            continue
        try:
            size = path.stat().st_size
        except OSError:
            failed += 1
            continue
        if not wants_sidecar(path, size):
            continue
        sidecar = path.with_name(path.name + ".gz")
        if sidecar_is_current(path, sidecar):
            kept += 1
            continue
        try:
            encoded_bytes += _write_sidecar(path, sidecar)
        except OSError:
            failed += 1
            continue
        source_bytes += size
        written += 1

    return PrecompressResult(
        written=written,
        kept=kept,
        orphans_removed=orphans,
        failed=failed,
        source_bytes=source_bytes,
        encoded_bytes=encoded_bytes,
        seconds=time.monotonic() - started,
    )
