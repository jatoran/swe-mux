"""Producing the staging tree an app swap renames into place.

`packaging/redeploy_desktop.py` has always staged before it stops anything: the
new bundle is built (or, since the updater, extracted) into `dist/.staging` while
the old app keeps serving, and only a good staging tree earns the stop and the
rename. This module is that step's second implementation - the one that puts the
tree together out of the files this machine **already has**, and writes only what
it does not.

#### Why files, not bytes

An update's dominant cost on Windows is image scanning over a tree of files the
machine has never seen. `redeploy_desktop.APP_HEALTH_TIMEOUT_SECONDS` is 600
rather than 300 for exactly that reason: measured 2026-08-21, an already-scanned
build took 225s to runtime-ready with 30 live sessions, so a cold one exceeded
the old budget and the rollback fired on a healthy deploy. So the thing worth
minimizing is the number of files whose scan verdict is thrown away, and the
mechanism that preserves one is a **hard link**: a linked file is the same
filesystem object as the one already installed, with the same identity the
scanner cached its verdict against, where a copy is a new object with none.

That is why `_place` reaches for `os.link` first and treats `shutil.copy2` as the
fallback rather than the other way round, and why the count of each is reported.
Both are correct; only one collects the win. A copy still avoids inflating the
member out of the archive, so a filesystem without links degrades to "faster,
but not free" rather than to the old behaviour.

Two properties make linking safe here, and both are structural rather than
lucky. `dist/.staging` and `dist/swe-mux` are siblings, so they are always on one
volume, which is what `os.link` requires. And the swap that follows only ever
*renames* whole bundle directories and *deletes* retired ones - nothing edits a
file inside a bundle in place - so two trees sharing a file can never diverge;
retiring `dist/swe-mux.prev` drops one link and leaves the other's bytes exactly
where they were.

#### What is proven, and where the trust comes from

Nothing is reused on the strength of a version number, a timestamp, or a
filename. A file is reused only when its SHA-256 equals the SHA-256 the incoming
manifest publishes for that path, and every file written out of the archive is
hashed as it is written and refused if it does not match. So the staged tree is
byte-for-byte the released bundle by construction, and the *whole-archive*
SHA-256 the updater and `--from-archive` already verify is untouched and still
the root of all of it - the manifest is a member of the archive that hash covers.
This adds no new trust boundary; it adds one more document under an existing one.

#### Failing back is always available, and is always taken

Every refusal in here is "extract the whole archive instead", never "do not
install". The full path is what happened before this module existed, so falling
back to it cannot be wrong - which is what lets the delta be attempted
optimistically and abandoned on anything surprising: a manifest that disagrees
with the archive it shipped in, an unreadable installed tree, a link that will
not create, a hash that does not match. The reason is recorded and logged in the
closed vocabulary of `bundle_manifest`, so an operator asking "why did my update
rewrite everything" gets one word rather than an inference.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .bundle_archive import (
    ARCHIVE_ROOT,
    CHUNK_BYTES,
    ArchiveError,
    ArchiveReader,
    extract_bundle,
    open_archive,
    read_archive_file_manifest,
    validate_members,
)
from .bundle_manifest import (
    BUNDLE_FILES_NAME,
    DELTA_MANIFEST_DISAGREES,
    DELTA_STAGING_FAILED,
    KIND_FILE,
    DeltaPlan,
    FileEntry,
    FileManifest,
    plan_delta,
)

log = logging.getLogger(__name__)

#: How the staging tree was produced. Recorded rather than inferred from the
#: numbers, because "delta that happened to reuse nothing" and "full extraction"
#: produce the same counts and are not the same event.
MODE_FULL = "full"
MODE_DELTA = "delta"

#: How a reused file got into the staging tree.
PLACED_LINK = "link"
PLACED_COPY = "copy"


@dataclass(slots=True)
class StageResult:
    """What staging did, in the terms the cost is actually paid in."""

    root: Path
    mode: str
    #: `bundle_manifest`'s closed vocabulary; `ok` when a delta was used.
    reason: str
    written_files: int = 0
    written_bytes: int = 0
    reused_files: int = 0
    reused_bytes: int = 0
    linked: int = 0
    copied: int = 0
    links_created: int = 0
    observations: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.mode == MODE_FULL:
            return (
                f"extracted the whole bundle ({self.written_files} files, "
                f"{self.written_bytes / 1e6:.1f} MB): {self.reason}"
            )
        return (
            f"staged a delta: wrote {self.written_files} file(s) / "
            f"{self.written_bytes / 1e6:.1f} MB, reused {self.reused_files} file(s) / "
            f"{self.reused_bytes / 1e6:.1f} MB ({self.linked} linked, {self.copied} "
            f"copied), recreated {self.links_created} symlink(s)"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "written_files": self.written_files,
            "written_bytes": self.written_bytes,
            "reused_files": self.reused_files,
            "reused_bytes": self.reused_bytes,
            "linked": self.linked,
            "copied": self.copied,
            "links_created": self.links_created,
            "observations": list(self.observations),
        }


def stage_bundle(
    archive: Path,
    staging_root: Path,
    *,
    current_root: Path | None = None,
    say: Callable[[str], None] | None = None,
) -> StageResult:
    """Put the archive's bundle under `staging_root`, writing as little as possible.

    `current_root` is the installed bundle to reuse from - `dist/swe-mux` for a
    real redeploy, and `None` for a first install or any caller that would rather
    not. `None` is not an error; it is the ordinary full extraction.

    `say` is the caller's own logger (`redeploy_desktop.log`) so that the one
    decision an operator cares about appears in the redeploy log where they are
    already looking, rather than only in the daemon's.
    """
    speak = say if say is not None else log.info
    staging_root = Path(staging_root)
    manifest, manifest_reason = _read_manifest(archive)
    plan = plan_delta(manifest, current_root)
    if manifest is None:
        # Which flavour of absent: an archive from before this feature and an
        # archive carrying a manifest this build cannot parse are different facts
        # and only one of them is a bug. The *reason* stays in the closed set so
        # a surface can still branch on it.
        plan.observations.append(f"no usable file manifest ({manifest_reason})")
    for observation in plan.observations:
        speak(f"bundle: {observation}")
    if manifest is None or current_root is None or not plan.eligible:
        speak(f"staging the whole bundle - {plan.reason}")
        return _stage_full(archive, staging_root, plan)
    speak(plan.summary())
    try:
        return _stage_delta(archive, staging_root, manifest, plan, Path(current_root))
    except (ArchiveError, OSError, ValueError) as exc:
        speak(
            f"the delta could not be staged ({type(exc).__name__}: {exc}); "
            "extracting the whole bundle instead"
        )
        # An `ArchiveError` raised in here already carries the precise word for
        # what went wrong; flattening every failure to `staging_failed` would
        # throw away the one thing the log is read for. Only a failure with no
        # word of its own - a disk error, a decoder giving up - takes the generic.
        plan.reason = (
            exc.reason if isinstance(exc, ArchiveError) else DELTA_STAGING_FAILED
        )
        return _stage_full(archive, staging_root, plan)


def _read_manifest(archive: Path) -> tuple[FileManifest | None, str]:
    """The archive's `files.json`, or `(None, reason)`. Never raises about it."""
    try:
        return read_archive_file_manifest(archive)
    except ArchiveError:
        # The archive itself is unreadable, which the full path is about to
        # discover and refuse properly, with the message it already has.
        return None, "archive_unreadable"


def _stage_full(archive: Path, staging_root: Path, plan: DeltaPlan) -> StageResult:
    """The behaviour that existed before this module: extract everything."""
    root = extract_bundle(archive, staging_root)
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    return StageResult(
        root=root,
        mode=MODE_FULL,
        reason=plan.reason,
        written_files=len(files),
        written_bytes=sum(path.stat().st_size for path in files),
        observations=list(plan.observations),
    )


def _stage_delta(
    archive: Path,
    staging_root: Path,
    manifest: FileManifest,
    plan: DeltaPlan,
    current_root: Path,
) -> StageResult:
    """Build the staging tree from the archive plus the installed bundle.

    Ordered write-then-reuse rather than the other way round on purpose: the
    writes are the part that can fail on a bad archive, and failing before a
    single link has been created keeps the fallback's `rmtree` cheap and keeps
    the installed bundle demonstrably untouched throughout.
    """
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)
    target = staging_root / ARCHIVE_ROOT
    target.mkdir(parents=True, exist_ok=True)
    result = StageResult(root=target, mode=MODE_DELTA, reason=plan.reason)
    result.observations = list(plan.observations)

    with open_archive(archive) as bundle:
        names = bundle.names()
        validate_members(names)
        _refuse_disagreement(names, manifest)
        # `files.json` is not in the manifest - it cannot carry its own digest -
        # so it is copied across verbatim rather than planned for. Doing it first
        # keeps the staged tree self-describing even if a later write fails.
        _write_member(bundle, f"{ARCHIVE_ROOT}/{BUNDLE_FILES_NAME}", target / BUNDLE_FILES_NAME)
        for entry in plan.fetch:
            written = _write_member(
                bundle, f"{ARCHIVE_ROOT}/{entry.path}", target / entry.path, entry=entry
            )
            result.written_files += 1
            result.written_bytes += written

    linkable = True
    for entry in plan.reuse:
        destination = target / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        placed = _place(current_root / entry.path, destination, linkable=linkable)
        if placed == PLACED_LINK:
            result.linked += 1
        else:
            result.copied += 1
            # One cross-device or unsupported-operation failure describes the
            # whole pair of directories, so stop paying for the exception on
            # every one of two thousand files.
            linkable = False
        result.reused_files += 1
        result.reused_bytes += entry.size

    for entry in plan.links:
        _recreate_link(entry, target)
        result.links_created += 1

    _verify_complete(target, manifest)
    return result


def _refuse_disagreement(names: list[str], manifest: FileManifest) -> None:
    """Refuse a delta whose manifest does not describe the archive it shipped in.

    Both documents are covered by the same whole-archive SHA-256, so a difference
    is a packaging bug rather than tampering - but a packaging bug is exactly the
    case where guessing produces a tree that is neither bundle. The caller turns
    this into a full extraction, which is right for both readings.
    """
    prefix = f"{ARCHIVE_ROOT}/"
    in_archive = {name[len(prefix) :] for name in names if name.startswith(prefix)}
    described = {entry.path for entry in manifest.entries} | {BUNDLE_FILES_NAME}
    missing = sorted(described - in_archive)
    extra = sorted(in_archive - described)
    if missing or extra:
        raise ArchiveError(
            DELTA_MANIFEST_DISAGREES,
            f"The archive's {BUNDLE_FILES_NAME} describes {len(missing)} file(s) the "
            f"archive does not carry and omits {len(extra)} it does.",
        )


def _write_member(
    bundle: ArchiveReader,
    member: str,
    destination: Path,
    *,
    entry: FileEntry | None = None,
) -> int:
    """Copy one archive member to disk, hashing it against `entry` as it goes.

    Written straight to its final name rather than to a temporary: this whole
    tree is the temporary, and a partial file inside it is discarded wholesale by
    the fallback's `rmtree` or by the staging check `redeploy_desktop.py` already
    runs before it stops anything.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with bundle.stream(member) as source, destination.open("wb") as sink:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(chunk)
            total += len(chunk)
            sink.write(chunk)
    if entry is not None:
        if total != entry.size or digest.hexdigest() != entry.sha256:
            raise ArchiveError(
                DELTA_MANIFEST_DISAGREES,
                f"The archive's {member!r} is not the file its manifest describes "
                f"({total} bytes, {digest.hexdigest()[:16]}...).",
            )
        if os.name != "nt":
            os.chmod(destination, entry.mode)
    return total


def _place(source: Path, destination: Path, *, linkable: bool) -> str:
    """Hard-link `source` into place, falling back to a copy. Returns which.

    The link is what preserves the file's identity, and with it whatever the
    machine's scanner already decided about those bytes. The copy is correct and
    costs a write and a scan, which is the whole thing being avoided - so it is
    the fallback and it is counted separately, not silently substituted.
    """
    if linkable:
        try:
            os.link(source, destination)
            return PLACED_LINK
        except (OSError, NotImplementedError, AttributeError):
            pass
    shutil.copy2(source, destination)
    return PLACED_COPY


def _recreate_link(entry: FileEntry, target: Path) -> None:
    """Recreate a bundle-internal symlink. Costs no bytes and no archive read."""
    destination = target / entry.path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or destination.exists():
        destination.unlink()
    os.symlink(entry.target, destination)


def _verify_complete(target: Path, manifest: FileManifest) -> None:
    """Every file the manifest names exists, at the size it names.

    A cheap last look rather than a second hash pass: every byte in the tree
    arrived either from a digest-checked archive member or from a local file
    whose digest was checked before it was linked, so what is left to catch is a
    path that was never placed at all - a plan that lost an entry, or a write
    that silently produced nothing. `redeploy_desktop.py`'s own "did anything
    stage a swe-mux.exe" check runs after this and is deliberately kept.
    """
    for entry in manifest.entries:
        path = target / entry.path
        if entry.kind != KIND_FILE:
            if not path.is_symlink():
                raise ArchiveError(
                    DELTA_MANIFEST_DISAGREES,
                    f"The staged tree is missing the symlink {entry.path!r}.",
                )
            continue
        try:
            if path.stat().st_size != entry.size:
                raise ArchiveError(
                    DELTA_MANIFEST_DISAGREES,
                    f"The staged {entry.path!r} is {path.stat().st_size} bytes, not "
                    f"the {entry.size} its manifest names.",
                )
        except OSError as exc:
            raise ArchiveError(
                DELTA_MANIFEST_DISAGREES,
                f"The staged tree is missing {entry.path!r}.",
            ) from exc


__all__ = [
    "MODE_DELTA",
    "MODE_FULL",
    "PLACED_COPY",
    "PLACED_LINK",
    "StageResult",
    "stage_bundle",
]
