"""Turn a built `dist/swe-mux` into the release artifact the updater installs.

The frozen-app updater recognizes its own platform's bundle by **name** - the
manifest says only what an artifact is called, its URL and its hash - so the name
is a contract between whatever publishes a release and every installed build.
This script is that contract's writer, and it derives the name from
`swe_mux.update_install.release_archive_name` rather than spelling it out, so the
two halves cannot drift into a release that no installed copy can find.

It prints the SHA-256 as well, because that is the other half of what
`release.yml`'s manifest step needs and because a maintainer publishing a build
by hand should not have to remember which digest the updater checks.

    uv run python packaging/package_desktop_release.py
    uv run python packaging/package_desktop_release.py --bundle dist/swe-mux --out dist

The archive's shape is fixed and validated on both ends: exactly one top-level
`swe-mux/` directory, carrying the PyInstaller onedir tree and the `bundle.json`
`build_desktop.describe_bundle` wrote into it. An archive missing that file is
refused by the updater rather than installed, because it is the only thing that
answers whether the release needs a new PTY supervisor - and installing one that
does would reap every live session.

Deliberately **not** part of `build_desktop.py`: a local build is for running on
this machine, a release archive is for handing to other people, and folding the
second into the first would make every developer build pay for a
multi-hundred-megabyte zip nobody asked for.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from swe_mux.bundle_archive import ARCHIVE_ROOT, file_digest  # noqa: E402
from swe_mux.bundle_metadata import BUNDLE_METADATA_NAME, read_bundle_metadata  # noqa: E402
from swe_mux.update_install import release_archive_name, release_platform_tag  # noqa: E402


def build_archive(bundle_root: Path, destination: Path) -> Path:
    """Zip `bundle_root` as `swe-mux/...` into `destination`, returning the path.

    Written to a `.part` and renamed, for the same reason the updater downloads
    to one: a half-written archive under the real name is a file some later step
    will treat as complete.
    """
    metadata, reason = read_bundle_metadata(bundle_root)
    if metadata is None:
        raise SystemExit(
            f"{bundle_root / BUNDLE_METADATA_NAME} is {reason}. A release archive must "
            "carry the bundle metadata the updater reads to decide whether the PTY "
            "supervisor would have to change; rebuild with packaging/build_desktop.py."
        )
    if metadata.platform != release_platform_tag():
        # A cross-platform archive would be named for this host and carry another
        # one's binaries, which is precisely the mistake a name-based contract
        # cannot survive.
        raise SystemExit(
            f"the bundle describes itself as {metadata.platform} but this host is "
            f"{release_platform_tag()}; package a release on the host that built it."
        )
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / release_archive_name(metadata.version, metadata.platform)
    partial = archive.with_name(f"{archive.name}.part")
    partial.unlink(missing_ok=True)
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(bundle_root.rglob("*")):
            if path.is_dir():
                continue
            bundle.write(path, f"{ARCHIVE_ROOT}/{path.relative_to(bundle_root).as_posix()}")
    partial.replace(archive)
    return archive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bundle",
        type=Path,
        default=ROOT / "dist" / "swe-mux",
        help="the built bundle directory (default: dist/swe-mux)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "dist",
        help="where to write the archive (default: dist/)",
    )
    args = parser.parse_args(argv)
    if not args.bundle.is_dir():
        raise SystemExit(f"{args.bundle} does not exist; build it first")
    archive = build_archive(args.bundle, args.out)
    print(archive)
    print(f"sha256  {file_digest(archive)}")
    print(f"bytes   {archive.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
