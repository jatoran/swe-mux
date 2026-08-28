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
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from .bundle_metadata import BUNDLE_METADATA_NAME, BundleMetadata, parse_bundle_metadata

#: The archive's single top-level directory. Named rather than inferred from the
#: first entry, so a malformed archive is rejected instead of extracted into
#: whatever shape it happens to have.
ARCHIVE_ROOT = "swe-mux"

#: Read granularity for hashing. Large enough that hashing is not syscall-bound.
CHUNK_BYTES = 1024 * 1024

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


def read_archive_metadata(archive: Path) -> BundleMetadata:
    """The incoming bundle's `bundle.json`, read without extracting anything.

    Reading it from the archive rather than after extraction is what lets the
    supervisor gate refuse an update while the staging tree is still empty - and
    it is also the reason nothing here has to execute a freshly downloaded
    binary to find out what it needs.
    """
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
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
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError) as exc:
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


def extract_bundle(archive: Path, staging_root: Path) -> Path:
    """Extract a validated archive under `staging_root`, returning the bundle root.

    The destination is emptied first. A staging tree left by a previous run is
    not a starting point: merging a new bundle over an old one produces a tree
    that is neither, and the failure would only appear at runtime.
    """
    import shutil

    staging_root = Path(staging_root)
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        validate_members(bundle.namelist())
        bundle.extractall(staging_root)
    root = staging_root / ARCHIVE_ROOT
    if not root.is_dir():
        raise ArchiveError(
            ARCHIVE_INVALID,
            f"The archive produced no {ARCHIVE_ROOT}/ directory when extracted.",
        )
    return root
