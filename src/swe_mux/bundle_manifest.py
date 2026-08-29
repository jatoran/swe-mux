"""A per-file hash manifest for a desktop bundle, and the delta it makes possible.

`bundle_metadata.py` answers one question about a bundle - would installing it
need a new PTY supervisor. This answers a different one: **which of these files
does the machine already have?**

The reason that question is worth asking is not bandwidth. An update's dominant
cost on Windows is image scanning over a tree of files the machine has never
seen, which is why `redeploy_desktop.APP_HEALTH_TIMEOUT_SECONDS` is 600 rather
than 300 (`../../.docs/development/ROADMAP.md` Phase 21). A file that is not
rewritten keeps its existing scan verdict, so the win is measured in **files
touched**, not in bytes transferred - and those are different goals that pull in
different directions. Everything here optimizes the first one.

Measured 2026-08-29 across two real consecutive builds of this project's own
bundle (2026-08-27 and 2026-08-29, an interval that included a frontend rebuild
*and* the eviction of 101 MB of `playwright/driver`): 420.0 MB in 2937 files, of
which **2874 files and 387.7 MB - 92.3% of the bytes - were byte-identical**.
Sixty-three files and 32.4 MB actually needed writing, and 25.1 MB of that was
`swe-mux.exe` itself.

**The manifest describes the bundle it ships with, and nothing else.** It is not
a delta against a previous release, it names no other version, and it is
therefore valid for a user updating from any version or from none. What decides
what gets written is a hash comparison against whatever this machine actually
has on disk, which is the only source that cannot be stale.

**Every file the delta reuses is proven, not assumed.** A local file is reused
only when its SHA-256 equals the SHA-256 the incoming manifest publishes for that
path, so the staged tree is byte-for-byte the released bundle by construction.
The manifest travels two ways and both are already hash-covered: inside the
release archive as `swe-mux/files.json` (covered by the whole-archive SHA-256 the
updater already verifies) and beside it as a sidecar artifact (covered by its own
entry in `version.json`). Nothing here introduces a new trust boundary; it adds
one hash-verified document under a root that was already trusted.

#### Why the fallback is a measurement rather than a version comparison

The Phase 21 text asked for a full replacement "when the Python version or the
dependency set moves", on the reasoning that a dependency bump invalidates most
of `_internal/` and a delta would be worse than useless. The measurement above
refutes the second half of that: the 2026-08-27 -> 2026-08-29 pair *removed an
entire 101 MB top-level package* and still shared 92.3% of its bytes. Adding,
removing, or upgrading one package invalidates that package's files and nothing
else, so "the dependency set moved" does not predict how much a delta saves.

More importantly it cannot affect correctness. A file whose SHA-256 equals the
target's SHA-256 *is* the target's file, whatever moved to produce it - so no
structural trigger is needed to keep a delta honest, only to keep it worthwhile.
The decision is therefore the thing actually at stake: `DELTA_REUSE_FLOOR`, the
share of bytes that must already be present before a delta beats streaming the
archive straight out. A Python bump or a wholesale dependency change drives the
measured share under that floor by itself, which is the same answer the named
triggers would have given, arrived at from evidence rather than from a proxy.
The structural facts are still computed and reported (`DeltaPlan.observations`),
because they are what a human wants in the log; they are just not the decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The manifest's name inside a bundle root, and inside a release archive with
#: `swe-mux/` in front of it. Deliberately beside `bundle.json` rather than in
#: it: that file is read to decide whether an install may happen at all, and it
#: must stay small enough to read out of an archive in one member.
BUNDLE_FILES_NAME = "files.json"

#: This document's schema, independent of `bundle.json`'s and of the update
#: manifest's. An unrecognized value means "cannot tell", which here degrades to
#: a full replacement rather than to a refusal - the full path is what happened
#: before this file existed, so falling back to it is always safe.
BUNDLE_FILES_SCHEMA = 1

#: Reasons a read produced no manifest. A closed set, so a caller branches on the
#: word rather than on a message.
FILES_OK = "ok"
FILES_MISSING = "missing"
FILES_MALFORMED = "malformed"
FILES_UNSUPPORTED_SCHEMA = "unsupported_schema"

#: Reasons a delta was not attempted. Also closed, and also logged verbatim, so
#: "why did my update rewrite everything" has one vocabulary wherever it is asked.
DELTA_OK = "ok"
DELTA_NO_MANIFEST = "no_file_manifest"
DELTA_NO_CURRENT_BUNDLE = "no_current_bundle"
DELTA_TOO_LITTLE_REUSE = "too_little_reuse"
DELTA_UNSAFE_MANIFEST = "unsafe_manifest"
DELTA_MANIFEST_DISAGREES = "manifest_disagrees_with_archive"
DELTA_STAGING_FAILED = "staging_failed"

#: The share of the target bundle's bytes that must already be on this machine
#: before a delta is worth doing. Below it, the delta's own costs - hashing the
#: installed tree, and writing file-by-file rather than streaming the archive
#: out in one pass - stop being repaid, and the ordinary extraction is both
#: faster and simpler. Chosen rather than measured to a decimal: the observed
#: cases cluster at either end (92.3% for an ordinary release, and near zero for
#: a Python or wholesale dependency move), so anything in the middle of the range
#: separates them equally well and a precise number would imply a precision the
#: data does not have.
DELTA_REUSE_FLOOR = 0.25

#: Read granularity for hashing, matching `bundle_archive.CHUNK_BYTES`.
CHUNK_BYTES = 1024 * 1024

#: Entry kinds. A bundle is overwhelmingly regular files; POSIX bundles also
#: carry symlinks between an so-name and its versioned target, and those are
#: recorded as links rather than hashed through, because following one would
#: record the same bytes twice and recreating one costs no bytes at all.
KIND_FILE = "file"
KIND_LINK = "link"


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file in a bundle: where it goes, and how to know it is the right one.

    `sha256` is empty for a link and required for a file. `mode` is the POSIX
    permission bits, carried because a `.tar.gz` bundle's `swe-mux` binary is
    unusable without its executable bit and a delta writes that file itself
    rather than letting `tarfile` restore it.
    """

    path: str
    kind: str = KIND_FILE
    size: int = 0
    sha256: str = ""
    mode: int = 0o644
    #: For a link only: its target, relative and inside the bundle.
    target: str = ""

    def as_dict(self) -> dict[str, Any]:
        if self.kind == KIND_LINK:
            return {"path": self.path, "kind": KIND_LINK, "target": self.target}
        return {
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class FileManifest:
    """Every file in one built bundle, with the facts a diff needs.

    `python` and `packages` are observations rather than gates - see the module
    docstring. They are recorded because they are the first thing a human asks
    when a delta did not happen, and computing them at build time is free while
    reconstructing them afterwards is not.
    """

    version: str
    platform: str
    #: The interpreter the bundle was frozen with, as `python312` / `python3.12`
    #: read off the bundle's own runtime library. Empty when none was found.
    python: str
    #: Sorted top-level entry names under `_internal/`, `.dist-info` excluded for
    #: the same reason `build_desktop.EXPECTED_BUNDLE_PACKAGES` excludes them.
    packages: tuple[str, ...]
    entries: tuple[FileEntry, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries if entry.kind == KIND_FILE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": BUNDLE_FILES_SCHEMA,
            "version": self.version,
            "platform": self.platform,
            "python": self.python,
            "packages": list(self.packages),
            "files": [entry.as_dict() for entry in self.entries],
        }


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, streamed. Same function as `bundle_archive.file_digest`.

    Duplicated as a private helper rather than imported so that this module has
    no dependency on the archive reader: the packager builds a manifest from a
    directory long before anything is packed, and the diff below runs against an
    installed tree with no archive in sight.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> bool:
    """True when `value` is a relative path that cannot leave its own tree."""
    pure = value.replace("\\", "/")
    if not pure or pure.startswith("/"):
        return False
    head = pure.split("/")[0]
    if ":" in head:
        return False
    return not any(part == ".." for part in pure.split("/"))


#: The interpreter's runtime library, per platform, requiring the **minor
#: version** to be present. Requiring the digits is what makes this useful, and
#: it was found by running the code over a real bundle: PyInstaller collects
#: Windows' stable-ABI forwarder `python3.dll` alongside `python312.dll`, and
#: `python3.dll` sorts first, so a looser pattern reported every bundle ever
#: built as `python3` - a value that cannot distinguish 3.12 from 3.13 and would
#: have made the observation silently useless rather than visibly absent.
_PYTHON_LIBRARY = re.compile(
    r"^(?:(python3\d+)\.(?:dll|dylib)|(libpython3\.\d+)\.(?:so|dylib))", re.IGNORECASE
)


def bundle_python_tag(bundle_root: Path) -> str:
    """The interpreter a built bundle carries, by the name of its runtime library.

    Read off the tree rather than from `sys.version_info`, because the process
    asking is never the bundle: `redeploy_desktop.py` runs under the source
    checkout's interpreter, and the daemon that previews an update is running the
    *old* bundle while asking about the new one.
    """
    root = Path(bundle_root)
    for parent in (root, root / "_internal"):
        try:
            names = sorted(item.name for item in parent.iterdir() if item.is_file())
        except OSError:
            continue
        for name in names:
            found = _PYTHON_LIBRARY.match(name)
            if found is not None:
                return (found.group(1) or found.group(2)).lower()
    return ""


def bundle_packages(bundle_root: Path) -> tuple[str, ...]:
    """Sorted top-level names under `_internal/`, `.dist-info` excluded."""
    try:
        entries = sorted(
            item.name
            for item in (Path(bundle_root) / "_internal").iterdir()
            if not item.name.endswith(".dist-info")
        )
    except OSError:
        return ()
    return tuple(entries)


def build_file_manifest(
    bundle_root: Path, *, version: str, platform: str
) -> FileManifest:
    """Hash every file in a built bundle.

    `BUNDLE_FILES_NAME` is excluded, and has to be: the manifest is a file in the
    bundle it describes, so an entry for itself could never hold its own digest.
    Everything that consumes a manifest therefore treats that one path as
    supplied by the archive rather than by the diff.
    """
    root = Path(bundle_root)
    entries: list[FileEntry] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == BUNDLE_FILES_NAME:
            continue
        if path.is_symlink():
            target = os.readlink(path).replace("\\", "/")
            entries.append(FileEntry(path=relative, kind=KIND_LINK, target=target))
            continue
        stat = path.stat()
        entries.append(
            FileEntry(
                path=relative,
                kind=KIND_FILE,
                size=stat.st_size,
                sha256=file_sha256(path),
                mode=stat.st_mode & 0o777,
            )
        )
    return FileManifest(
        version=str(version),
        platform=str(platform),
        python=bundle_python_tag(root),
        packages=bundle_packages(root),
        entries=tuple(entries),
    )


def parse_file_manifest(payload: object) -> tuple[FileManifest | None, str]:
    """`(manifest, reason)` for a decoded `files.json`.

    The schema is checked before any field is read, exactly as
    `parse_bundle_metadata` and `parse_manifest` do. A single malformed entry
    fails the whole document rather than being dropped: a manifest missing one
    file would make a delta stage a tree that is silently not the release, which
    is the one outcome this file exists to prevent. Dropping an entry is safe in
    an *artifact list* because the consequence is "no artifact for you"; here the
    consequence would be a wrong application.
    """
    if not isinstance(payload, dict):
        return None, FILES_MALFORMED
    if payload.get("schema") != BUNDLE_FILES_SCHEMA:
        return None, FILES_UNSUPPORTED_SCHEMA
    files = payload.get("files")
    if not isinstance(files, list):
        return None, FILES_MALFORMED
    seen: set[str] = set()
    entries: list[FileEntry] = []
    for item in files:
        entry = _parse_entry(item)
        if entry is None or entry.path in seen:
            return None, FILES_MALFORMED
        seen.add(entry.path)
        entries.append(entry)
    version = payload.get("version")
    platform = payload.get("platform")
    python = payload.get("python")
    packages = payload.get("packages")
    return (
        FileManifest(
            version=version.strip() if isinstance(version, str) else "",
            platform=platform.strip() if isinstance(platform, str) else "",
            python=python.strip() if isinstance(python, str) else "",
            packages=tuple(name for name in packages if isinstance(name, str))
            if isinstance(packages, list)
            else (),
            entries=tuple(entries),
        ),
        FILES_OK,
    )


def _parse_entry(item: object) -> FileEntry | None:
    """One `files` element, or None when it is not one this build can act on."""
    if not isinstance(item, dict):
        return None
    path = item.get("path")
    if not isinstance(path, str) or not _safe_relative(path):
        return None
    path = path.replace("\\", "/")
    if item.get("kind") == KIND_LINK:
        target = item.get("target")
        if not isinstance(target, str) or not _safe_relative(target):
            return None
        return FileEntry(path=path, kind=KIND_LINK, target=target.replace("\\", "/"))
    size = item.get("size")
    digest = item.get("sha256")
    mode = item.get("mode")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    if not isinstance(digest, str) or len(digest) != 64:
        return None
    try:
        int(digest, 16)
    except ValueError:
        return None
    if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o7777:
        mode = 0o644
    return FileEntry(
        path=path, kind=KIND_FILE, size=size, sha256=digest.lower(), mode=mode
    )


@dataclass(slots=True)
class DeltaPlan:
    """What a delta would do, and whether it is worth doing.

    Pure: computing one reads the installed tree and writes nothing, so a caller
    may ask for it and then decline. `reuse` and `fetch` together are exactly the
    manifest's file entries, so a plan that has lost or invented one is a bug a
    test can see rather than a tree that is subtly wrong.
    """

    #: Entries whose bytes are already on this machine, at `<current>/<path>`.
    reuse: list[FileEntry] = field(default_factory=list)
    #: Entries that have to come out of the archive.
    fetch: list[FileEntry] = field(default_factory=list)
    #: Symlinks, recreated from the manifest and never fetched.
    links: list[FileEntry] = field(default_factory=list)
    #: `DELTA_OK`, or why the caller should extract the whole archive instead.
    reason: str = DELTA_OK
    #: Structural facts for the log. Never the decision - see the module docstring.
    observations: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.reason == DELTA_OK

    @property
    def reuse_bytes(self) -> int:
        return sum(entry.size for entry in self.reuse)

    @property
    def fetch_bytes(self) -> int:
        return sum(entry.size for entry in self.fetch)

    @property
    def total_bytes(self) -> int:
        return self.reuse_bytes + self.fetch_bytes

    @property
    def reuse_share(self) -> float:
        total = self.total_bytes
        return 1.0 if total == 0 else self.reuse_bytes / total

    def summary(self) -> str:
        """One line for a log, in files first because files are the cost."""
        if not self.eligible:
            return f"full replacement ({self.reason})"
        return (
            f"delta: reuse {len(self.reuse)} file(s) / {self.reuse_bytes / 1e6:.1f} MB "
            f"({self.reuse_share * 100:.1f}%), write {len(self.fetch)} file(s) / "
            f"{self.fetch_bytes / 1e6:.1f} MB, recreate {len(self.links)} link(s)"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reason": self.reason,
            "reuse_files": len(self.reuse),
            "reuse_bytes": self.reuse_bytes,
            "write_files": len(self.fetch),
            "write_bytes": self.fetch_bytes,
            "link_files": len(self.links),
            "total_bytes": self.total_bytes,
            "reuse_share": round(self.reuse_share, 4),
            "observations": list(self.observations),
        }


def plan_delta(
    manifest: FileManifest | None,
    current_root: Path | None,
    *,
    floor: float = DELTA_REUSE_FLOOR,
) -> DeltaPlan:
    """Decide, by hashing, which of `manifest`'s files this machine already has.

    Reads only; nothing is written, moved, or linked. A file that cannot be read
    - the running app's own locked image, a permission failure, a path that is a
    directory where the manifest says a file - is simply not reusable, so the
    worst outcome of an unreadable installed tree is the behaviour that existed
    before this function did.

    The hash is skipped when the size already differs, which is a pure early-out
    and never a substitute for it: two files of equal size are compared byte for
    byte through their digests, because "same size, same path" is exactly the
    shape a stale build has.
    """
    plan = DeltaPlan()
    if manifest is None:
        plan.reason = DELTA_NO_MANIFEST
        return plan
    root = Path(current_root) if current_root is not None else None
    if root is None or not root.is_dir():
        plan.reason = DELTA_NO_CURRENT_BUNDLE
        return plan
    if any(not _safe_relative(entry.path) for entry in manifest.entries):
        # `parse_file_manifest` already refuses these, so reaching here means a
        # manifest built in-process from a tree that is not one. Refuse anyway:
        # this is the function that turns a path into a write.
        plan.reason = DELTA_UNSAFE_MANIFEST
        return plan
    local_python = bundle_python_tag(root)
    if manifest.python and local_python and manifest.python != local_python:
        plan.observations.append(
            f"python moved: {local_python} installed, {manifest.python} incoming"
        )
    local_packages = set(bundle_packages(root))
    incoming = set(manifest.packages)
    if incoming and local_packages and incoming != local_packages:
        added = sorted(incoming - local_packages)
        removed = sorted(local_packages - incoming)
        plan.observations.append(
            f"packages moved: +{len(added)} -{len(removed)}"
            + (f" added {', '.join(added[:6])}" if added else "")
            + (f" removed {', '.join(removed[:6])}" if removed else "")
        )
    for entry in manifest.entries:
        if entry.kind == KIND_LINK:
            plan.links.append(entry)
            continue
        if _matches(root / entry.path, entry):
            plan.reuse.append(entry)
        else:
            plan.fetch.append(entry)
    if plan.reuse_share < floor:
        plan.reason = DELTA_TOO_LITTLE_REUSE
    return plan


def _matches(path: Path, entry: FileEntry) -> bool:
    """Whether the installed `path` is byte-for-byte what `entry` describes."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        stat = path.stat()
        if stat.st_size != entry.size:
            return False
        if os.name != "nt" and (stat.st_mode & 0o777) != entry.mode:
            # A mode change with no content change cannot be applied to a
            # hardlink without changing the file the old bundle still points at,
            # so it is written fresh instead. Vanishingly rare, and cheap.
            return False
        return file_sha256(path) == entry.sha256
    except OSError:
        return False


def manifest_bytes(manifest: FileManifest) -> bytes:
    """The exact bytes written both into the archive and beside it.

    One function, because the sidecar artifact and the archive member must be
    the same document: the sidecar is what the daemon plans against before
    downloading, and the member is what the swap acts on. Two writers would be
    two documents that only probably agree.
    """
    return (json.dumps(manifest.as_dict(), indent=2, sort_keys=False) + "\n").encode(
        "utf-8"
    )


__all__ = [
    "BUNDLE_FILES_NAME",
    "BUNDLE_FILES_SCHEMA",
    "DELTA_MANIFEST_DISAGREES",
    "DELTA_NO_CURRENT_BUNDLE",
    "DELTA_NO_MANIFEST",
    "DELTA_OK",
    "DELTA_REUSE_FLOOR",
    "DELTA_STAGING_FAILED",
    "DELTA_TOO_LITTLE_REUSE",
    "DELTA_UNSAFE_MANIFEST",
    "FILES_MALFORMED",
    "FILES_MISSING",
    "FILES_OK",
    "FILES_UNSUPPORTED_SCHEMA",
    "KIND_FILE",
    "KIND_LINK",
    "DeltaPlan",
    "FileEntry",
    "FileManifest",
    "build_file_manifest",
    "bundle_packages",
    "bundle_python_tag",
    "file_sha256",
    "manifest_bytes",
    "parse_file_manifest",
    "plan_delta",
]
