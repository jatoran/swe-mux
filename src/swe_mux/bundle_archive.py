"""Reading and extracting a released desktop bundle archive.

Shared by the two processes that touch a release archive, and shared on purpose:
the daemon's updater (`update_install.py`) validates and interrogates the archive
before it decides anything, and `packaging/redeploy_desktop.py --from-archive`
extracts it into the staging tree minutes later. Those are separate processes, so
a rule enforced in only one of them is a rule the other does not have.

**Validation happens before extraction and again inside it.** The updater's
refusal path runs while nothing has been touched, which is the only moment a
refusal costs nothing - but the script is separately invocable with any path a
person can type, and it does not inherit the daemon's checks by being downstream
of them. Both call `validate_members`.

The rules are deliberately narrow rather than clever. A swe-mux desktop archive
is exactly one top-level directory named `swe-mux`, containing the PyInstaller
onedir tree and the `bundle.json` that describes it. Anything else - an absolute
path, a drive letter, a `..` segment, a second top-level entry - is not that, and
is refused rather than normalized. A hash proves an archive is the file the
manifest named; it proves nothing about what extracting it would write.

**Two container formats, because `update_install._ARCHIVE_SUFFIX` names two.**
Windows gets `.zip` (Explorer and `Expand-Archive` open one with nothing
installed); macOS and Linux get `.tar.gz`. That suffix map predates any POSIX
desktop wrapper, and until 2026-08-28 this module could open only zips - so the
map was a promise about a format nothing could read, which would have surfaced
as a refusal to install the very first POSIX release. The gap is closed by
teaching the reader rather than by pointing POSIX at `.zip`, because a zip cannot
carry the property a POSIX bundle needs: `ZipFile.extractall` does not restore
the Unix mode bits it stores, so an extracted `swe-mux` binary would arrive
without its executable bit and the "installed" app would not start. `.tar.gz` is
the honest answer there, and it is the one the map already gave.

Extraction of a tarball goes through `tarfile`'s `filter="data"`, which is the
3.12+ supported way to refuse absolute paths, `..` escapes, links pointing out of
the tree, and device/fifo members. `validate_members` still runs first and
independently, because it is the rule *both* processes share and because it says
what a swe-mux bundle is rather than what a tarball may not do.

Both readers bound the *uncompressed* size as well. `update_install` bounds the
download, which bounds a zip and says nothing about a gzip stream: a few hundred
compressed kilobytes can name terabytes of members, and the point of a ceiling is
not to write the bytes in the first place.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Protocol

from .bundle_manifest import (
    BUNDLE_FILES_NAME,
    FILES_MALFORMED,
    FILES_MISSING,
    FILES_OK,
    FileManifest,
    parse_file_manifest,
)
from .bundle_metadata import BUNDLE_METADATA_NAME, BundleMetadata, parse_bundle_metadata

#: The archive's single top-level directory. Named rather than inferred from the
#: first entry, so a malformed archive is rejected instead of extracted into
#: whatever shape it happens to have.
ARCHIVE_ROOT = "swe-mux"

#: Read granularity for hashing. Large enough that hashing is not syscall-bound.
CHUNK_BYTES = 1024 * 1024

#: The container formats a release archive may be in, longest suffix first so a
#: `.tar.gz` is never mistaken for something ending in `.gz`. Kept here rather
#: than in `update_install` because this module is what can actually open one -
#: a name the reader cannot honour is the defect this pairing exists to prevent.
ZIP_SUFFIX = ".zip"
TAR_GZ_SUFFIX = ".tar.gz"
ARCHIVE_SUFFIXES = (TAR_GZ_SUFFIX, ZIP_SUFFIX)

#: The ceiling on what extracting an archive may write. A desktop bundle is a few
#: hundred megabytes; this is the size past which the file is not the artifact we
#: asked for. It bounds the members' declared sizes, so a decompression bomb is
#: refused before anything is written rather than after the disk fills.
MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024

#: Closed reason vocabulary, shared with `update_install`'s refusals so one word
#: means one thing wherever it is printed.
ARCHIVE_INVALID = "archive_invalid"
BUNDLE_METADATA_MISSING = "bundle_metadata_missing"


class ArchiveError(Exception):
    """A refusal about an archive: a machine word and a sentence for a human."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def file_digest(path: Path) -> str:
    """SHA-256 of a file, streamed. The one hash function this feature has."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_members(names: list[str]) -> None:
    """Refuse an archive that would write anywhere but its own `swe-mux/` tree."""
    if not names:
        raise ArchiveError(ARCHIVE_INVALID, "The archive is empty, so it is not a bundle.")
    for name in names:
        pure = name.replace("\\", "/")
        head = pure.split("/")[0]
        if pure.startswith("/") or ":" in head:
            raise ArchiveError(
                ARCHIVE_INVALID,
                f"The archive contains an absolute path ({name!r}), which a release "
                "bundle never does.",
            )
        if any(part == ".." for part in pure.split("/")):
            raise ArchiveError(
                ARCHIVE_INVALID,
                f"The archive contains a parent-directory path ({name!r}), which a "
                "release bundle never does.",
            )
        if head != ARCHIVE_ROOT:
            raise ArchiveError(
                ARCHIVE_INVALID,
                f"The archive's entries are not all under {ARCHIVE_ROOT}/ ({name!r}), "
                "so it is not a swe-mux desktop bundle.",
            )


def archive_suffix(archive: Path) -> str:
    """Which container format `archive` claims to be, by name.

    By name and never by sniffing content: the name is what the manifest
    published and what the digest was taken over, so a file whose bytes disagree
    with its name is a substitution to refuse rather than a format to detect.
    """
    lowered = Path(archive).name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    raise ArchiveError(
        ARCHIVE_INVALID,
        f"{Path(archive).name!r} is not a swe-mux release archive; one is named "
        f"{' or '.join(ARCHIVE_SUFFIXES)}.",
    )


def _check_total_size(sizes: list[int]) -> None:
    """Refuse an archive whose members declare more bytes than the ceiling."""
    total = sum(size for size in sizes if size > 0)
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ArchiveError(
            ARCHIVE_INVALID,
            f"The archive's entries declare {total} bytes, above the "
            f"{MAX_UNCOMPRESSED_BYTES} byte ceiling, so it is not the artifact the "
            "manifest describes. Nothing was extracted.",
        )


class ArchiveReader(Protocol):
    """What both formats have to answer before anything is extracted.

    `stream` exists beside `read` for the delta stager, which copies individual
    members out to disk: a bundle carries members of tens of megabytes, and a
    writer that holds each one whole in memory is paying for nothing. `read` is
    kept for the small documents (`bundle.json`, `files.json`) that a decision is
    made on, where having the bytes in hand is the point.
    """

    def names(self) -> list[str]: ...

    def read(self, member: str) -> bytes: ...

    def stream(self, member: str) -> IO[bytes]: ...


class _ZipReader:
    def __init__(self, handle: zipfile.ZipFile) -> None:
        self._handle = handle

    def names(self) -> list[str]:
        _check_total_size([info.file_size for info in self._handle.infolist()])
        return self._handle.namelist()

    def read(self, member: str) -> bytes:
        return self._handle.read(member)

    def stream(self, member: str) -> IO[bytes]:
        return self._handle.open(member)


class _TarReader:
    def __init__(self, handle: tarfile.TarFile) -> None:
        self._handle = handle
        self._members = handle.getmembers()

    def names(self) -> list[str]:
        _check_total_size([member.size for member in self._members])
        return [member.name for member in self._members]

    def read(self, member: str) -> bytes:
        with self.stream(member) as stream:
            return stream.read()

    def stream(self, member: str) -> IO[bytes]:
        stream = self._handle.extractfile(member)
        if stream is None:
            # A directory, a symlink, or a special member under a name we asked
            # for as a file. Not something to follow.
            raise ArchiveError(
                ARCHIVE_INVALID,
                f"The archive's {member!r} is not a regular file, so it cannot be read.",
            )
        return stream


@contextmanager
def open_archive(archive: Path) -> Iterator[ArchiveReader]:
    """Open a release archive in whichever of the two formats it is named for."""
    if archive_suffix(archive) == TAR_GZ_SUFFIX:
        with tarfile.open(archive, "r:gz") as tar:
            yield _TarReader(tar)
    else:
        with zipfile.ZipFile(archive) as bundle:
            yield _ZipReader(bundle)


#: Everything either container library raises for a file that is not the archive
#: it was named as. Caught as one set so both formats produce one refusal.
_UNREADABLE = (
    zipfile.BadZipFile,
    tarfile.TarError,
    EOFError,
    OSError,
    ValueError,
    UnicodeDecodeError,
)


def read_archive_metadata(archive: Path) -> BundleMetadata:
    """The incoming bundle's `bundle.json`, read without extracting anything.

    Reading it from the archive rather than after extraction is what lets the
    supervisor gate refuse an update while the staging tree is still empty - and
    it is also the reason nothing here has to execute a freshly downloaded
    binary to find out what it needs.
    """
    try:
        with open_archive(archive) as bundle:
            names = bundle.names()
            validate_members(names)
            member = f"{ARCHIVE_ROOT}/{BUNDLE_METADATA_NAME}"
            if member not in names:
                raise ArchiveError(
                    BUNDLE_METADATA_MISSING,
                    f"The archive carries no {member}, so nothing can tell whether "
                    "installing it would require a new PTY supervisor.",
                )
            payload = json.loads(bundle.read(member).decode("utf-8"))
    except ArchiveError:
        raise
    except _UNREADABLE as exc:
        raise ArchiveError(
            ARCHIVE_INVALID,
            f"The archive could not be read ({type(exc).__name__}).",
        ) from exc
    metadata, reason = parse_bundle_metadata(payload)
    if metadata is None:
        raise ArchiveError(
            BUNDLE_METADATA_MISSING,
            f"The archive describes itself in a way this build does not understand "
            f"({reason}).",
        )
    return metadata


def read_archive_file_manifest(archive: Path) -> tuple[FileManifest | None, str]:
    """The incoming bundle's `files.json`, read without extracting anything.

    `(None, reason)` rather than a raise for every outcome except an unreadable
    archive, and the asymmetry with `read_archive_metadata` above is deliberate.
    Missing supervisor metadata is a refusal because the property at stake is the
    operator's live fleet; a missing or unreadable file manifest costs only the
    delta, and the fallback is the full extraction that was the only behaviour
    before this document existed. Anything that cannot be understood degrades to
    "install it the slow way", which is never wrong.

    A release archive written before this feature carries no `files.json` at all,
    which is exactly the `missing` case - so an old archive installs today the
    way it installed yesterday, with no version negotiation anywhere.
    """
    member = f"{ARCHIVE_ROOT}/{BUNDLE_FILES_NAME}"
    try:
        with open_archive(archive) as bundle:
            names = bundle.names()
            validate_members(names)
            if member not in names:
                return None, FILES_MISSING
            raw = bundle.read(member)
    except ArchiveError:
        raise
    except _UNREADABLE as exc:
        raise ArchiveError(
            ARCHIVE_INVALID,
            f"The archive could not be read ({type(exc).__name__}).",
        ) from exc
    # Decoded outside the reader, so a JSON error is a malformed *manifest* and
    # not a malformed archive: `_UNREADABLE` carries `ValueError` for the
    # container libraries, and catching the two together would turn a typo in
    # this one document into a refusal to install anything at all.
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, FILES_MALFORMED
    manifest, reason = parse_file_manifest(payload)
    return (manifest, FILES_OK) if manifest is not None else (None, reason)


def extract_bundle(archive: Path, staging_root: Path) -> Path:
    """Extract a validated archive under `staging_root`, returning the bundle root.

    The destination is emptied first. A staging tree left by a previous run is
    not a starting point: merging a new bundle over an old one produces a tree
    that is neither, and the failure would only appear at runtime.

    A tarball is extracted under `filter="data"`, which is the interpreter's own
    refusal of absolute paths, `..` escapes, links leaving the tree, and special
    files. `validate_members` has already run; the filter is the second half that
    covers what a name alone cannot say, and it is deliberately not disabled to
    preserve some member a bundle has never needed.
    """
    import shutil

    staging_root = Path(staging_root)
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    try:
        if archive_suffix(archive) == TAR_GZ_SUFFIX:
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                _check_total_size([member.size for member in members])
                validate_members([member.name for member in members])
                tar.extractall(staging_root, filter="data")
        else:
            with zipfile.ZipFile(archive) as bundle:
                _check_total_size([info.file_size for info in bundle.infolist()])
                validate_members(bundle.namelist())
                bundle.extractall(staging_root)
    except ArchiveError:
        raise
    except _UNREADABLE as exc:
        raise ArchiveError(
            ARCHIVE_INVALID,
            f"The archive could not be extracted ({type(exc).__name__}).",
        ) from exc
    root = staging_root / ARCHIVE_ROOT
    if not root.is_dir():
        raise ArchiveError(
            ARCHIVE_INVALID,
            f"The archive produced no {ARCHIVE_ROOT}/ directory when extracted.",
        )
    return root
