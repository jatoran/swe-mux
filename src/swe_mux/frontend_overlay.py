"""A hash-verified frontend overlay: shipping a UI fix without swapping a bundle.

The frozen desktop app serves its own bundled `swe_mux/static`, so a frontend fix
reaches it only through a full PyInstaller redeploy - a ~370 MB tree the machine
has never seen, re-scanned by the OS, minutes long. The tree that actually
changed is 24 MB and is self-contained web assets. This module lets that 24 MB
land in the data directory and be preferred over the bundled copy.

It is a named pattern rather than a workaround: over-the-air updating of the
non-native layer is what Expo/EAS Update and CodePush do for React Native, and
what asar swapping does for Electron. What separates the sound version from the
hacky one is exactly three properties, and all three are here.

**Hash verification, over the whole tree, on every start.** An overlay declares a
SHA-256 per file and a `tree_digest` over that set, and the daemon recomputes all
of it before serving a byte. Measured 2026-08-29 on the primary host against a
real production build - 101 files, 23.05 MiB - the whole verification pass is
70-91 ms, and resolution including it is 87 ms. Against a daemon start measured
in tens of seconds there is therefore no case for a cheaper stat-and-mtime
signature, which is fortunate: this repository has already paid twice for
trusting filesystem timestamps at 15.625 ms granularity. A tree that fails is not
repaired and not partially served; the bundled tree is used and the reason is
reported.

**A compatibility pin against the backend, and a mismatch is refused.** A
frontend that does not match its daemon's API shape is worse than a stale
frontend, because the failure is arbitrary rather than legible. The pin has two
halves, and the second exists because the first is not enough.

`requires_backend` is exact equality against `swe_mux.__version__`, which gives
the rule an operator can hold in their head: *an app update always supersedes an
overlay*. When the frozen app moves to 0.1.3, the 0.1.2 overlay stops being
served and 0.1.3's own bundled frontend takes over, with no cleanup step and no
way to end up on a pairing nobody tested.

`requires_api` is a digest over the daemon's whole route table, and it is what
actually catches the failure the paragraph above describes. `__version__` moves
per *release*, while this project's frozen app is rebuilt from a checkout that
moves per *commit* - so a frontend built from master today and a frozen app built
from master last week both say "0.1.2" while disagreeing about which endpoints
exist. A version pin alone would happily serve a frontend calling a route the
daemon does not have, which is exactly the arbitrary failure. The route table
answers the real question, deterministically, on both sides. It is deliberately
the *whole* table rather than an allowlist of "interesting" routes: an overlay
refused because an unrelated endpoint moved is a legible "rebuild or redeploy",
whereas an allowlist is a second thing to keep in agreement and its failure is
silent.

Both halves are claims by the *producer* - this module never mints one for a
payload that arrived without a manifest, because a pin the consumer invented is
not a pin. Both are checked *before* anything is hashed, which is what makes the
common post-update start cost 0.5 ms rather than 90.

**A one-press revert.** Reverting flips one boolean in one small atomic file. It
never moves, deletes or rewrites a tree, so it cannot half-fail and cannot be the
thing that leaves an install unserveable. `mux ui-overlay revert` does it without
the UI, which matters because the overlay's own failure mode is a UI that will
not load.

Three shape decisions worth the sentence each:

**Trees are content-addressed and installs never overwrite one.** An install
writes `trees/<tree_digest>/` and then points `state.json` at it. On Windows a
directory the daemon is actively serving cannot be renamed or removed reliably
(`WinError 5/32`), and the previous design - one `active/` directory, swapped in
place - would have had the running daemon holding the very thing an install must
move. Pruning old generations is best-effort for the same reason: a locked
directory is left alone rather than made into a failed install.

**A zip, and only a zip.** `bundle_archive` supports `.tar.gz` because a zip
cannot carry the executable bit a POSIX desktop bundle needs. A static tree has
no executable bit to lose, so the second format would buy nothing and be a second
reader to keep correct.

**This module deliberately re-implements the small archive reader it needs rather
than generalizing `bundle_archive`.** That module is on `update_install`'s path,
which Workstream B owns; a shared abstraction is worth building later, from two
existing implementations, rather than inventing one across a live seam. The
duplication is about sixty lines and is noted so it is a decision rather than an
oversight.

**And precompressed sidecars are derived, not payload.** The daemon runs
`precompress_static` over whatever tree it serves, which writes `.gz` files into
the overlay after verification passed. So verification accepts one class of
unlisted file: a `.gz` whose plain sibling *is* listed and whose gzip trailer
records exactly that sibling's current CRC-32 and length (`build_support.
sidecar_is_current`, the same exact check the precompressor decides staleness
with). Every other unlisted file is a refusal, which is what keeps the tree
closed rather than merely mostly-specified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .build_support import sidecar_is_current
from .ui_build import normalize_ui_build_id, parse_ui_build_id

log = logging.getLogger(__name__)

#: Everything this feature owns lives under one directory in the data dir, so an
#: operator who wants it gone can remove exactly one path.
OVERLAY_DIRNAME = "frontend-overlay"
TREES_DIRNAME = "trees"
STAGING_DIRNAME = "staging"
DOWNLOADS_DIRNAME = "downloads"
STATE_FILENAME = "state.json"

#: The manifest a payload carries about itself, at the root of the tree.
MANIFEST_NAME = "overlay.json"

#: The archive's single top-level directory, named rather than inferred so a
#: malformed archive is refused instead of extracted into whatever shape it has.
ARCHIVE_ROOT = "static"
ARCHIVE_SUFFIX = ".zip"

MANIFEST_SCHEMA = 1
STATE_SCHEMA = 1

#: The one file without which a tree cannot serve the application at all.
INDEX_NAME = "index.html"

#: Read granularity for hashing and for streaming a download. Large enough that
#: hashing is not syscall-bound, small enough that progress moves on a slow link.
CHUNK_BYTES = 1024 * 1024

#: What an overlay may weigh, uncompressed. The built tree is ~24 MiB; this is
#: the size past which the payload is not a frontend, and it bounds what an
#: unfriendly archive or host can make the daemon write.
MAX_OVERLAY_BYTES = 512 * 1024 * 1024

#: How many tree generations are kept. Two, so the previous build is still on
#: disk when an overlay turns out to be wrong and nothing accumulates.
KEEP_TREES = 2

#: A frontend overlay is single-digit megabytes, so it gets a much tighter budget
#: than a bundle download. Bounded per chunk as well, because a total budget
#: alone lets a server dribble bytes for the whole of it.
DOWNLOAD_TIMEOUT_SECONDS = 5 * 60.0
DOWNLOAD_CHUNK_TIMEOUT_SECONDS = 60.0

# --- the closed reason vocabulary --------------------------------------------
#
# Closed for the reason `update_install`'s is: surfaces branch on the word, and a
# new failure mode has to be named rather than rendering as an existing one.

REASON_OK = "ok"
#: Resolution outcomes that are not faults - no overlay was asked for.
REASON_NONE = "no_overlay"
REASON_REVERTED = "reverted"
REASON_DISABLED = "disabled"
#: Faults in an installed overlay, found at resolution.
REASON_TREE_MISSING = "tree_missing"
REASON_MANIFEST_MISSING = "manifest_missing"
REASON_MANIFEST_UNREADABLE = "manifest_unreadable"
REASON_UNSUPPORTED_SCHEMA = "unsupported_schema"
REASON_MANIFEST_INCONSISTENT = "manifest_inconsistent"
REASON_VERSION_MISMATCH = "version_mismatch"
REASON_API_MISMATCH = "api_mismatch"
REASON_NO_INDEX = "no_index"
REASON_MISSING_FILE = "missing_file"
REASON_HASH_MISMATCH = "hash_mismatch"
REASON_UNREADABLE_FILE = "unreadable_file"
REASON_UNEXPECTED_FILE = "unexpected_file"
#: Refusals on the way in.
REASON_SOURCE_MISSING = "source_missing"
REASON_ARCHIVE_INVALID = "archive_invalid"
REASON_OVERSIZED = "oversized"
REASON_PAYLOAD_HASH_MISMATCH = "payload_hash_mismatch"
REASON_DIGEST_REQUIRED = "digest_required"
REASON_DOWNLOAD_FAILED = "download_failed"
REASON_TRUNCATED = "truncated"
REASON_UNREACHABLE = "unreachable"
REASON_WRITE_FAILED = "write_failed"
REASON_NOTHING_INSTALLED = "nothing_installed"

#: Which reasons describe an overlay that is present and broken, as opposed to
#: one that was never installed or was deliberately turned off. Surfaces render
#: these two groups differently and a caller should not have to enumerate them.
FAULT_REASONS = frozenset(
    {
        REASON_TREE_MISSING,
        REASON_MANIFEST_MISSING,
        REASON_MANIFEST_UNREADABLE,
        REASON_UNSUPPORTED_SCHEMA,
        REASON_MANIFEST_INCONSISTENT,
        REASON_VERSION_MISMATCH,
        REASON_API_MISMATCH,
        REASON_NO_INDEX,
        REASON_MISSING_FILE,
        REASON_HASH_MISMATCH,
        REASON_UNREADABLE_FILE,
        REASON_UNEXPECTED_FILE,
    }
)


class OverlayRefused(Exception):
    """A refusal with a machine word and a sentence for a human."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# --- the manifest --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverlayManifest:
    """What a payload says about itself. Produced once, verified many times."""

    requires_backend: str
    requires_api: str
    tree_digest: str
    files: Mapping[str, str]
    ui_build_id: str | None = None
    built_at: float | None = None
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA,
            "requires_backend": self.requires_backend,
            "requires_api": self.requires_api,
            "tree_digest": self.tree_digest,
            "ui_build_id": self.ui_build_id,
            "built_at": self.built_at,
            "source": self.source,
            "files": dict(sorted(self.files.items())),
        }

    def summary(self) -> dict[str, Any]:
        """Everything but the file map, which is a hundred lines nobody reads."""
        payload = self.as_dict()
        payload.pop("files")
        payload["file_count"] = len(self.files)
        return payload


def file_digest(path: Path) -> str:
    """SHA-256 of a file, streamed. The one hash function this feature has."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(files: Mapping[str, str]) -> str:
    """One number over the whole file set, stable under any iteration order.

    Not a security boundary - anything that could rewrite the map could rewrite
    this too - and it is not claimed as one. It is an *identity*: the single
    string a human compares between what was built, what was published and what
    is being served, and the value an inconsistent manifest fails against.
    """
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[name].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def route_table_digest(routes: Iterable[tuple[str, str]]) -> str:
    """One number over a daemon's whole HTTP surface: `(method, path)` pairs.

    Computed identically by the producer (from the checkout it packages) and the
    consumer (from its own live table), which is what makes it an answer rather
    than a version string's opinion. Sorted and de-duplicated, so registration
    order - which is load-bearing for aiohttp's resolution and *not* a
    compatibility fact - cannot move it.

    Takes the pairs rather than importing `routes`, because `routes/frontend.py`
    imports this module: a module-level import back would be a cycle, and a lazy
    one inside the function would make this untestable without the whole route
    table. The caller that already has it passes it in.
    """
    digest = hashlib.sha256()
    for method, path in sorted({(str(method).upper(), str(path)) for method, path in routes}):
        digest.update(f"{method} {path}\n".encode())
    return digest.hexdigest()


def daemon_api_digest() -> str:
    """This daemon's own route-table digest.

    The lazy import is the point: `routes` reaches every route module, one of
    which imports this one, so the dependency exists only while this is running.
    """
    from . import routes

    return route_table_digest((route.method, route.path) for route in routes.all_routes())


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def payload_files(root: Path) -> list[Path]:
    """Every regular file a manifest should cover, in a stable order.

    The manifest itself is excluded because it cannot hash itself, and `.gz`
    sidecars are excluded because they are derived from listed files rather than
    payload - see the module docstring for how verification treats them.

    "The manifest" means the one at the root, compared by tree-relative path
    rather than by filename: a build that happened to emit an `overlay.json`
    inside `assets/` is payload, and dropping it by name would leave a file the
    manifest does not describe and verification then refuses.
    """
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and _relative_posix(path, root) != MANIFEST_NAME
        and path.suffix.lower() != ".gz"
    )


def build_manifest(
    root: Path,
    *,
    requires_backend: str,
    requires_api: str | None = None,
    source: str = "",
) -> OverlayManifest:
    """Describe the tree at `root` as an overlay pinned to `requires_backend`.

    The producer's job, run by `packaging/build_frontend_overlay.py`. Both pins
    are passed in rather than read from this process, because the honest pin is
    the checkout the frontend was *built beside*, which is not necessarily
    whatever interpreter is packaging it. `requires_api` defaults to this
    process's own table, which is right for the ordinary case where the packaging
    script runs from the checkout it is packaging.
    """
    root = Path(root)
    if not root.is_dir():
        raise OverlayRefused(
            REASON_SOURCE_MISSING, f"{root} is not a directory, so there is nothing to package."
        )
    files = {_relative_posix(path, root): file_digest(path) for path in payload_files(root)}
    if INDEX_NAME not in files:
        raise OverlayRefused(
            REASON_NO_INDEX,
            f"{root} carries no {INDEX_NAME}, so it cannot serve the application and "
            "is not a frontend build.",
        )
    index = root / INDEX_NAME
    try:
        build_id = parse_ui_build_id(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        build_id = None
    return OverlayManifest(
        requires_backend=str(requires_backend).strip(),
        requires_api=requires_api if requires_api is not None else daemon_api_digest(),
        tree_digest=tree_digest(files),
        files=files,
        ui_build_id=build_id,
        built_at=time.time(),
        source=source,
    )


def write_manifest(root: Path, manifest: OverlayManifest) -> Path:
    """Write `overlay.json` at the root of a tree, and return its path."""
    path = Path(root) / MANIFEST_NAME
    path.write_text(json.dumps(manifest.as_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def parse_manifest(payload: object) -> OverlayManifest:
    """An `overlay.json` body, or a refusal naming which shape rule it broke."""
    if not isinstance(payload, dict):
        raise OverlayRefused(
            REASON_MANIFEST_UNREADABLE, f"{MANIFEST_NAME} is not a JSON object."
        )
    if payload.get("schema") != MANIFEST_SCHEMA:
        raise OverlayRefused(
            REASON_UNSUPPORTED_SCHEMA,
            f"{MANIFEST_NAME} declares schema {payload.get('schema')!r}, and this "
            f"build understands {MANIFEST_SCHEMA}. Refusing rather than guessing at "
            "a format it does not know.",
        )
    requires = payload.get("requires_backend")
    if not isinstance(requires, str) or not requires.strip():
        raise OverlayRefused(
            REASON_MANIFEST_UNREADABLE,
            f"{MANIFEST_NAME} names no backend version, so there is no compatibility "
            "pin and nothing to check a daemon against.",
        )
    api = payload.get("requires_api")
    if not isinstance(api, str) or len(api) != 64:
        # Required rather than optional, and this is why: an optional pin is a
        # pin any producer can decline to make, which is the same as not having
        # one. A manifest that cannot state the API surface it was built against
        # is refused instead.
        raise OverlayRefused(
            REASON_MANIFEST_UNREADABLE,
            f"{MANIFEST_NAME} names no API digest, so nothing can tell whether the "
            "frontend was built against the endpoints this daemon serves.",
        )
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise OverlayRefused(
            REASON_MANIFEST_UNREADABLE, f"{MANIFEST_NAME} lists no files."
        )
    checked: dict[str, str] = {}
    for name, digest in files.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise OverlayRefused(
                REASON_MANIFEST_UNREADABLE,
                f"{MANIFEST_NAME} carries a file entry that is not a name and a digest.",
            )
        safe = _safe_relative_name(name)
        if safe is None:
            raise OverlayRefused(
                REASON_MANIFEST_UNREADABLE,
                f"{MANIFEST_NAME} names {name!r}, which is not a path inside the tree.",
            )
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise OverlayRefused(
                REASON_MANIFEST_UNREADABLE,
                f"{MANIFEST_NAME} carries {digest!r} for {name!r}, which is not a "
                "SHA-256 digest.",
            )
        checked[safe] = digest
    declared = payload.get("tree_digest")
    if not isinstance(declared, str) or declared != tree_digest(checked):
        raise OverlayRefused(
            REASON_MANIFEST_INCONSISTENT,
            f"{MANIFEST_NAME}'s tree_digest does not describe the files it lists, so "
            "the manifest disagrees with itself.",
        )
    built_at = payload.get("built_at")
    source = payload.get("source")
    return OverlayManifest(
        requires_backend=requires.strip(),
        requires_api=api,
        tree_digest=declared,
        files=checked,
        ui_build_id=normalize_ui_build_id(payload.get("ui_build_id")),
        built_at=float(built_at) if isinstance(built_at, (int, float)) else None,
        source=source if isinstance(source, str) else "",
    )


def read_manifest(root: Path) -> OverlayManifest:
    """The tree's own `overlay.json`, parsed, or a refusal."""
    path = Path(root) / MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise OverlayRefused(
            REASON_MANIFEST_MISSING,
            f"The tree carries no {MANIFEST_NAME}, so it declares no compatibility "
            "pin and no hashes. An overlay without those is not one.",
        ) from exc
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise OverlayRefused(
            REASON_MANIFEST_UNREADABLE,
            f"{MANIFEST_NAME} could not be read ({type(exc).__name__}).",
        ) from exc
    return parse_manifest(payload)


def _safe_relative_name(name: str) -> str | None:
    """`name` as a tree-relative posix path, or None when it escapes the tree."""
    pure = name.replace("\\", "/").strip()
    if not pure or pure.startswith("/") or ":" in pure.split("/")[0]:
        return None
    parts = [part for part in pure.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


# --- verification ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Verdict:
    """Whether a tree may be served, and if not, precisely why."""

    ok: bool
    reason: str
    message: str = ""
    manifest: OverlayManifest | None = None


def verify_tree(root: Path, *, backend_version: str, api_digest: str | None = None) -> Verdict:
    """Recompute every hash and check both pins. Never raises, never repairs.

    Ordered so the cheapest disqualification runs first: an overlay pinned to a
    version or an API surface this daemon is not needs no I/O at all beyond its
    manifest, which is the common case after an app update. Measured, that is
    0.5 ms rather than the ~90 ms a full pass costs.

    `api_digest` defaults to this process's own route table. It is a parameter
    because that default costs an import of every route module, and a caller that
    already has the answer - `create_app`, once per start - should not pay for it
    twice.
    """
    root = Path(root)
    if not root.is_dir():
        return Verdict(False, REASON_TREE_MISSING, f"{root} does not exist.")
    try:
        manifest = read_manifest(root)
    except OverlayRefused as refusal:
        return Verdict(False, refusal.reason, refusal.message)
    if manifest.requires_backend != backend_version:
        return Verdict(
            False,
            REASON_VERSION_MISMATCH,
            f"The overlay is built for swe-mux {manifest.requires_backend} and this "
            f"daemon is {backend_version}. A frontend that does not match its "
            "daemon's API shape fails arbitrarily rather than legibly, so it is "
            "refused; the bundled frontend is being served instead.",
            manifest,
        )
    running_api = daemon_api_digest() if api_digest is None else api_digest
    if manifest.requires_api != running_api:
        return Verdict(
            False,
            REASON_API_MISMATCH,
            "The overlay was built against a different set of daemon endpoints than "
            f"this daemon serves (overlay {manifest.requires_api[:16]}…, daemon "
            f"{running_api[:16]}…). Both report swe-mux "
            f"{manifest.requires_backend}, which is why the version alone cannot "
            "catch this: the app is rebuilt from a checkout that moves between "
            "releases. Rebuild the overlay from the same checkout this daemon was "
            "built from, or redeploy the app.",
            manifest,
        )
    if INDEX_NAME not in manifest.files:
        return Verdict(
            False,
            REASON_NO_INDEX,
            f"The overlay lists no {INDEX_NAME}, so it cannot serve the application.",
            manifest,
        )
    listed = set(manifest.files)
    for name, expected in sorted(manifest.files.items()):
        path = root / name
        if not path.is_file():
            return Verdict(
                False,
                REASON_MISSING_FILE,
                f"The overlay is missing {name}, which its manifest lists.",
                manifest,
            )
        try:
            actual = file_digest(path)
        except OSError as exc:
            return Verdict(
                False,
                REASON_UNREADABLE_FILE,
                f"The overlay's {name} could not be read ({type(exc).__name__}).",
                manifest,
            )
        if actual != expected:
            return Verdict(
                False,
                REASON_HASH_MISMATCH,
                f"The overlay's {name} does not match the SHA-256 its manifest "
                f"publishes (expected {expected[:16]}…, got {actual[:16]}…).",
                manifest,
            )
    unexpected = _unexpected_file(root, listed)
    if unexpected is not None:
        return Verdict(
            False,
            REASON_UNEXPECTED_FILE,
            f"The overlay contains {unexpected}, which its manifest does not describe. "
            "A tree that is only mostly specified is not a verified one.",
            manifest,
        )
    return Verdict(True, REASON_OK, "", manifest)


def _unexpected_file(root: Path, listed: set[str]) -> str | None:
    """The first file in the tree the manifest does not account for, or None.

    A `.gz` is accounted for when its plain sibling is listed *and* its gzip
    trailer records exactly that sibling's current bytes - the same exact check
    the precompressor uses to decide staleness, so a sidecar the daemon wrote
    after the last verification passes and a stale or planted one does not.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = _relative_posix(path, root)
        if name == MANIFEST_NAME or name in listed:
            continue
        if path.suffix.lower() == ".gz":
            source = path.with_name(path.name[: -len(".gz")])
            if _relative_posix(source, root) in listed and sidecar_is_current(source, path):
                continue
            return f"a stale or unrecognized precompressed sidecar ({name})"
        return f"an unlisted file ({name})"
    return None


# --- durable state --------------------------------------------------------------


@dataclass(slots=True)
class OverlayState:
    """Which tree is installed and whether it is switched on.

    Deliberately tiny and deliberately the only mutable thing a revert touches:
    reverting must not be able to half-fail, and a single atomic write of a few
    hundred bytes is the smallest operation that can express it.
    """

    active: bool = False
    digest: str = ""
    previous_digest: str = ""
    requires_backend: str = ""
    requires_api: str = ""
    ui_build_id: str = ""
    installed_at: float | None = None
    installed_from: str = ""
    reverted_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "digest": self.digest,
            "previous_digest": self.previous_digest,
            "requires_backend": self.requires_backend,
            "requires_api": self.requires_api,
            "ui_build_id": self.ui_build_id,
            "installed_at": self.installed_at,
            "installed_from": self.installed_from,
            "reverted_at": self.reverted_at,
        }


def _read_state(path: Path) -> OverlayState:
    """The state file, or an empty state. Never raises: this is a start path."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return OverlayState()
    except (OSError, ValueError, UnicodeDecodeError):
        log.warning(
            "frontend overlay state unreadable; treating it as no overlay",
            extra={"overlay_state_path": str(path)},
        )
        return OverlayState()
    if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
        return OverlayState()
    state = OverlayState(active=bool(payload.get("active")))
    for name in (
        "digest",
        "previous_digest",
        "requires_backend",
        "requires_api",
        "ui_build_id",
        "installed_from",
    ):
        value = payload.get(name)
        if isinstance(value, str):
            setattr(state, name, value)
    for name in ("installed_at", "reverted_at"):
        value = payload.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            setattr(state, name, float(value))
    return state


def _write_state(path: Path, state: OverlayState) -> None:
    """Atomically replace the state file. Raises: a revert that silently did not
    happen is the one failure this feature must never report as success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(
            json.dumps({"schema": STATE_SCHEMA, **state.as_dict()}, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    except OSError as exc:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
        raise OverlayRefused(
            REASON_WRITE_FAILED,
            f"The overlay state at {path} could not be written ({type(exc).__name__}), "
            "so nothing was changed.",
        ) from exc


# --- resolution: which tree the daemon serves ------------------------------------


@dataclass(frozen=True, slots=True)
class FrontendChoice:
    """Which static tree this daemon process serves, and why.

    Held in memory for the life of the process rather than persisted, for the
    reason every other per-generation fact here is: a decision that outlived the
    process that made it would be a false claim about what is being served.
    """

    directory: Path
    bundled: Path
    source: str
    reason: str
    message: str = ""
    manifest: OverlayManifest | None = None

    @property
    def overlay_active(self) -> bool:
        return self.source == "overlay"

    @property
    def faulted(self) -> bool:
        """The overlay was installed and switched on, and could not be served."""
        return self.reason in FAULT_REASONS

    def as_dict(self) -> dict[str, Any]:
        return {
            "serving": self.source,
            "directory": str(self.directory),
            "bundled_directory": str(self.bundled),
            "reason": self.reason,
            "message": self.message,
            "faulted": self.faulted,
            "overlay": self.manifest.summary() if self.manifest is not None else None,
        }


def overlay_root(data_dir: Path) -> Path:
    return Path(data_dir) / OVERLAY_DIRNAME


def tree_path(data_dir: Path, digest: str) -> Path:
    return overlay_root(data_dir) / TREES_DIRNAME / digest


def state_path(data_dir: Path) -> Path:
    return overlay_root(data_dir) / STATE_FILENAME


def resolve_frontend_dir(
    *,
    data_dir: Path,
    bundled: Path,
    backend_version: str,
    api_digest: str | None = None,
    enabled: bool = True,
) -> FrontendChoice:
    """The tree to serve: a verified overlay when there is one, else the bundle.

    The single resolution point, called once at app construction, because the
    static routes bind their directory there and four other readers take
    `FRONTEND_DIR` from the same key. Teaching each of those readers about
    overlays would have been five places to keep in agreement instead of one -
    and the one is where the existing `frontend_dir` override already lives, so
    an explicit override still wins over an overlay, which is what tests and
    `--frontend-dir` style callers mean by an override.

    Never raises. Every failure resolves to the bundled tree with a reason, and
    that is the whole safety property: a bad overlay costs a stale frontend and a
    log line, never a daemon that will not start.
    """
    bundled = Path(bundled)
    if not enabled:
        return FrontendChoice(
            bundled,
            bundled,
            "bundled",
            REASON_DISABLED,
            "Frontend overlays are switched off for this install, so the bundled "
            "frontend is served.",
        )
    state = _read_state(state_path(data_dir))
    if not state.digest:
        return FrontendChoice(bundled, bundled, "bundled", REASON_NONE)
    if not state.active:
        return FrontendChoice(
            bundled,
            bundled,
            "bundled",
            REASON_REVERTED,
            "The installed frontend overlay was reverted, so the bundled frontend is "
            "served. Restoring it takes one press.",
        )
    root = tree_path(data_dir, state.digest)
    verdict = verify_tree(root, backend_version=backend_version, api_digest=api_digest)
    if not verdict.ok:
        return FrontendChoice(
            bundled, bundled, "bundled", verdict.reason, verdict.message, verdict.manifest
        )
    return FrontendChoice(root, bundled, "overlay", REASON_OK, "", verdict.manifest)


def log_choice(choice: FrontendChoice) -> None:
    """Say once, at start, which frontend is being served and why.

    At WARNING when an installed overlay could not be served, because that is the
    one case where what the operator installed is not what they are looking at,
    and it is exactly the class of silent no-op this whole workstream exists to
    end.
    """
    if choice.overlay_active:
        manifest = choice.manifest
        log.info(
            "serving the frontend overlay at %s (pinned to swe-mux %s, ui build %s)",
            choice.directory,
            manifest.requires_backend if manifest else "?",
            (manifest.ui_build_id or "unknown")[:12] if manifest else "unknown",
            extra={"frontend_source": "overlay", "frontend_dir": str(choice.directory)},
        )
        return
    if choice.faulted:
        log.warning(
            "the installed frontend overlay was refused (%s): %s",
            choice.reason,
            choice.message,
            extra={"frontend_source": "bundled", "frontend_reason": choice.reason},
        )
        return
    log.debug(
        "serving the bundled frontend at %s (%s)",
        choice.directory,
        choice.reason,
        extra={"frontend_source": "bundled", "frontend_reason": choice.reason},
    )


# --- the archive reader ----------------------------------------------------------


def _validate_members(names: Iterable[str]) -> list[str]:
    """Refuse an archive that would write anywhere but its own `static/` tree."""
    collected = list(names)
    if not collected:
        raise OverlayRefused(
            REASON_ARCHIVE_INVALID, "The archive is empty, so it is not a frontend overlay."
        )
    for name in collected:
        pure = name.replace("\\", "/")
        if not pure.rstrip("/"):
            continue
        head = pure.split("/")[0]
        if pure.startswith("/") or ":" in head:
            raise OverlayRefused(
                REASON_ARCHIVE_INVALID,
                f"The archive contains an absolute path ({name!r}), which an overlay "
                "never does.",
            )
        if any(part == ".." for part in pure.split("/")):
            raise OverlayRefused(
                REASON_ARCHIVE_INVALID,
                f"The archive contains a parent-directory path ({name!r}), which an "
                "overlay never does.",
            )
        if head != ARCHIVE_ROOT:
            raise OverlayRefused(
                REASON_ARCHIVE_INVALID,
                f"The archive's entries are not all under {ARCHIVE_ROOT}/ ({name!r}), "
                "so it is not a swe-mux frontend overlay.",
            )
    return collected


def extract_overlay(archive: Path, destination: Path) -> Path:
    """Extract a validated overlay zip under `destination`, returning its root.

    The members' declared sizes are summed and refused before a byte is written,
    because a download ceiling bounds a compressed file and says nothing about
    what decompressing it would produce.
    """
    archive = Path(archive)
    if not archive.name.lower().endswith(ARCHIVE_SUFFIX):
        raise OverlayRefused(
            REASON_ARCHIVE_INVALID,
            f"{archive.name!r} is not a frontend overlay archive; one is named "
            f"*{ARCHIVE_SUFFIX}.",
        )
    destination = Path(destination)
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as payload:
            infos = payload.infolist()
            total = sum(info.file_size for info in infos if info.file_size > 0)
            if total > MAX_OVERLAY_BYTES:
                raise OverlayRefused(
                    REASON_OVERSIZED,
                    f"The archive's entries declare {total} bytes, above the "
                    f"{MAX_OVERLAY_BYTES} byte ceiling, so it is not a frontend build. "
                    "Nothing was extracted.",
                )
            _validate_members(info.filename for info in infos)
            payload.extractall(destination)
    except OverlayRefused:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise OverlayRefused(
            REASON_ARCHIVE_INVALID,
            f"The archive could not be extracted ({type(exc).__name__}).",
        ) from exc
    root = destination / ARCHIVE_ROOT
    if not root.is_dir():
        raise OverlayRefused(
            REASON_ARCHIVE_INVALID,
            f"The archive produced no {ARCHIVE_ROOT}/ directory when extracted.",
        )
    return root


def pack_overlay(root: Path, archive: Path, manifest: OverlayManifest | None = None) -> Path:
    """Write the tree at `root` into an overlay zip. The producer's other half.

    A `manifest` is written into the archive rather than into `root`, so
    packaging a checkout's `src/swe_mux/static` leaves that tree exactly as the
    frontend build produced it. It supersedes any `overlay.json` already in
    `root`, because two manifests in one archive is a question about precedence
    with no good answer. Passing none packs the tree's own manifest as it stands,
    which is what "package this directory" has to mean for a tree that already
    carries one.

    Precompressed `.gz` sidecars are packed. They are not *listed* in the manifest
    - they are derived from files that are - but shipping them saves the receiving
    daemon a measured 0.93 s of recompression on its first start, and verification
    checks each one against its listed sibling's bytes anyway.

    Written to a `.part` and renamed, for the same reason the updater downloads to
    one: a half-written archive under the real name is a file some later step will
    treat as complete.
    """
    root = Path(root)
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    partial = archive.with_name(f"{archive.name}.part")
    with suppress(OSError):
        partial.unlink(missing_ok=True)
    superseded = MANIFEST_NAME if manifest is not None else None
    members = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and _relative_posix(path, root) != superseded
    )
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as payload:
        for path in members:
            payload.write(path, f"{ARCHIVE_ROOT}/{_relative_posix(path, root)}")
        if manifest is not None:
            payload.writestr(
                f"{ARCHIVE_ROOT}/{MANIFEST_NAME}",
                json.dumps(manifest.as_dict(), indent=2) + "\n",
            )
    os.replace(partial, archive)
    return archive


# --- the installer ----------------------------------------------------------------


@dataclass(slots=True)
class InstallResult:
    """What one install did, in the shape a surface and a CLI both want."""

    digest: str
    manifest: OverlayManifest
    source: str
    #: True when the daemon is already serving this exact tree, which happens
    #: only when the same overlay is installed twice in one process generation.
    already_serving: bool = False
    events: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "installed": True,
            "digest": self.digest,
            "source": self.source,
            "already_serving": self.already_serving,
            "overlay": self.manifest.summary(),
            "events": list(self.events),
        }


class OverlayStore:
    """Owns the data dir's overlay: install it, revert it, restore it, describe it.

    Constructed with everything it touches so the whole of it is testable with no
    network and no daemon. `download` supplies bytes for the remote source and is
    injected for exactly the reason `update_install`'s is.
    """

    def __init__(
        self,
        data_dir: Path,
        *,
        backend_version: str,
        api_digest: str | None = None,
        download: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._backend_version = backend_version
        self._api_digest = api_digest
        self._download = download
        self._clock = clock

    # -- paths ---------------------------------------------------------------

    @property
    def root(self) -> Path:
        return overlay_root(self._data_dir)

    @property
    def trees_dir(self) -> Path:
        return self.root / TREES_DIRNAME

    @property
    def downloads_dir(self) -> Path:
        return self.root / DOWNLOADS_DIRNAME

    @property
    def state(self) -> OverlayState:
        return _read_state(state_path(self._data_dir))

    # -- describing ------------------------------------------------------------

    def status(self, choice: FrontendChoice | None = None) -> dict[str, Any]:
        """Everything a surface needs. Reads the small state file and nothing else.

        Deliberately does *not* re-verify: verification is a start-time act whose
        answer is `choice`, and a status endpoint that re-hashed 24 MiB on every
        poll would turn a passive panel into real work.
        """
        state = self.state
        installed = bool(state.digest)
        payload: dict[str, Any] = {
            "supported": True,
            "installed": installed,
            "active": bool(state.active and installed),
            "backend_version": self._backend_version,
            "state": state.as_dict(),
            "can_restore": bool(installed and not state.active),
            "tree_exists": installed and tree_path(self._data_dir, state.digest).is_dir(),
        }
        payload["serving"] = choice.as_dict() if choice is not None else None
        return payload

    # -- installing -------------------------------------------------------------

    def install_from_directory(self, source: Path) -> InstallResult:
        """Install a tree that already carries its own `overlay.json`.

        The payload is copied into a staging directory and verified *there*, so a
        source that changes underneath the install cannot be the thing that was
        verified. That is not paranoia here: the obvious source is a checkout's
        `src/swe_mux/static`, and the obvious way to produce one is a `vite build`
        that may still be writing.
        """
        source = Path(source)
        if not source.is_dir():
            raise OverlayRefused(
                REASON_SOURCE_MISSING,
                f"{source} is not a directory, so there is nothing to install.",
            )
        staging = self._new_staging()
        try:
            target = staging / ARCHIVE_ROOT
            shutil.copytree(source, target)
            return self._promote(target, source=str(source))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def install_from_archive(self, archive: Path, *, sha256: str | None = None) -> InstallResult:
        """Install an overlay zip already on disk, optionally pinned to a digest.

        The digest is optional here and required for a download, and the asymmetry
        is the point: a path an operator typed is trusted exactly as far as the
        filesystem it names, while bytes that arrived over a network are trusted
        only after a full-file digest.
        """
        archive = Path(archive)
        if not archive.is_file():
            raise OverlayRefused(
                REASON_SOURCE_MISSING, f"{archive} is not a file, so there is nothing to install."
            )
        if sha256:
            actual = file_digest(archive)
            if actual != sha256.strip().lower():
                raise OverlayRefused(
                    REASON_PAYLOAD_HASH_MISMATCH,
                    f"{archive.name} does not match the SHA-256 given for it (expected "
                    f"{sha256[:16]}…, got {actual[:16]}…). Nothing was installed.",
                )
        staging = self._new_staging()
        try:
            root = extract_overlay(archive, staging)
            return self._promote(root, source=str(archive))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    async def install_from_url(self, url: str, *, sha256: str) -> InstallResult:
        """Download an overlay zip, verify it against `sha256`, and install it.

        The digest is **required**, because there is no manifest here to take one
        from: an unverified download that reached the served tree would be an
        arbitrary-code-execution path with a network attacker at one end and the
        application's own UI at the other. The digest is computed over the bytes
        as they arrive, the file lands under a `.part` name, and only a matching
        digest promotes it - so a partial or tampered download is never a file the
        install can see.
        """
        expected = (sha256 or "").strip().lower()
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise OverlayRefused(
                REASON_DIGEST_REQUIRED,
                "Downloading an overlay requires the SHA-256 it must match. Nothing "
                "here can vouch for bytes that arrived over a network, so an "
                "unverifiable download is refused rather than installed.",
            )
        archive = await self._download_archive(url, expected)
        return self.install_from_archive(archive, sha256=expected)

    async def _download_archive(self, url: str, expected: str) -> Path:
        downloader = self._download if self._download is not None else _http_download
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        final = self.downloads_dir / f"{expected}{ARCHIVE_SUFFIX}"
        # A verified archive under its own digest is reused: the name *is* the
        # verification, so there is nothing to re-check and nothing to re-fetch.
        if final.is_file() and file_digest(final) == expected:
            return final
        with suppress(OSError):
            final.unlink(missing_ok=True)
        part = self.downloads_dir / f"{expected}{ARCHIVE_SUFFIX}.part"
        with suppress(OSError):
            part.unlink(missing_ok=True)
        digest = hashlib.sha256()
        received = 0
        try:
            with part.open("wb") as handle:

                def write(chunk: bytes) -> None:
                    nonlocal received
                    digest.update(chunk)
                    handle.write(chunk)
                    received += len(chunk)

                status, declared = await downloader(
                    url, write=write, max_bytes=MAX_OVERLAY_BYTES
                )
        except OverlayRefused:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - a dropped transfer is ordinary
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise OverlayRefused(
                REASON_DOWNLOAD_FAILED,
                f"The overlay download failed part-way ({type(exc).__name__}). "
                "Nothing was installed.",
            ) from exc
        if status != 200:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise OverlayRefused(REASON_UNREACHABLE, f"{url} answered HTTP {status}.")
        if received > MAX_OVERLAY_BYTES:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise OverlayRefused(
                REASON_OVERSIZED,
                f"The download exceeded the {MAX_OVERLAY_BYTES} byte ceiling, so it "
                "was abandoned. This is not a frontend overlay.",
            )
        if declared is not None and received != declared:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise OverlayRefused(
                REASON_TRUNCATED,
                f"The download ended after {received} of {declared} bytes. Nothing "
                "was installed; try again.",
            )
        actual = digest.hexdigest()
        if actual != expected:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise OverlayRefused(
                REASON_PAYLOAD_HASH_MISMATCH,
                f"The downloaded overlay does not match the SHA-256 given for it "
                f"(expected {expected[:16]}…, got {actual[:16]}…). Nothing was "
                "installed. The download was corrupted, or the file is not the one "
                "asked for.",
            )
        os.replace(part, final)
        return final

    # -- the swap ---------------------------------------------------------------

    def _new_staging(self) -> Path:
        staging = self.root / STAGING_DIRNAME / f"{int(self._clock())}-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        return staging

    def _promote(self, staged: Path, *, source: str) -> InstallResult:
        """Verify a staged tree and, only then, make it the installed overlay.

        The tree is moved to `trees/<tree_digest>/` and the state file is pointed
        at it. Nothing overwrites the tree the running daemon is serving, which is
        what makes this safe on Windows: a directory with an open file in it
        cannot be renamed or removed, and the previous design would have had the
        daemon holding exactly the thing an install must move.
        """
        verdict = verify_tree(
            staged, backend_version=self._backend_version, api_digest=self._api_digest
        )
        if not verdict.ok or verdict.manifest is None:
            raise OverlayRefused(verdict.reason, f"{verdict.message} Nothing was installed.")
        manifest = verdict.manifest
        events: list[str] = []
        destination = tree_path(self._data_dir, manifest.tree_digest)
        already = destination.is_dir()
        if already:
            # The same content, already unpacked. Re-verified above in staging, so
            # the existing copy is left exactly as it is rather than replaced -
            # replacing it is the one operation that could disturb a tree the
            # daemon is serving right now.
            events.append("this tree was already installed; its files were left alone")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(staged, destination)
            except OSError:
                # A cross-device staging directory, or a Windows rename refused
                # for a reason a copy is not. Copy and let the staging cleanup
                # remove the original.
                shutil.copytree(staged, destination, dirs_exist_ok=True)
                events.append("the staged tree was copied rather than renamed into place")
        state = self.state
        previous = state.digest if state.digest != manifest.tree_digest else state.previous_digest
        _write_state(
            state_path(self._data_dir),
            OverlayState(
                active=True,
                digest=manifest.tree_digest,
                previous_digest=previous,
                requires_backend=manifest.requires_backend,
                requires_api=manifest.requires_api,
                ui_build_id=manifest.ui_build_id or "",
                installed_at=float(self._clock()),
                installed_from=source,
                reverted_at=None,
            ),
        )
        events.extend(self._prune({manifest.tree_digest, previous}))
        log.info(
            "installed a frontend overlay",
            extra={
                "overlay_digest": manifest.tree_digest,
                "overlay_requires_backend": manifest.requires_backend,
                "overlay_files": len(manifest.files),
                "overlay_source": source,
            },
        )
        return InstallResult(
            digest=manifest.tree_digest,
            manifest=manifest,
            source=source,
            already_serving=already,
            events=events,
        )

    def _prune(self, keep: set[str]) -> list[str]:
        """Drop tree generations nothing points at. Best-effort by design.

        A locked directory - the running daemon serving it, an antivirus reading
        it - is left where it is. Disk hygiene is never worth turning a successful
        install into a failed one.
        """
        events: list[str] = []
        try:
            generations = [path for path in self.trees_dir.iterdir() if path.is_dir()]
        except OSError:
            return events
        for path in sorted(generations):
            if path.name in keep or not path.name:
                continue
            try:
                shutil.rmtree(path)
            except OSError as exc:
                events.append(
                    f"could not remove the superseded tree {path.name[:12]} "
                    f"({type(exc).__name__})"
                )
        with suppress(OSError):
            shutil.rmtree(self.root / STAGING_DIRNAME, ignore_errors=True)
        return events

    # -- the revert --------------------------------------------------------------

    def revert(self) -> dict[str, Any]:
        """Switch the overlay off. One boolean, one atomic write, no tree touched.

        The tree stays on disk so restoring it is the same one press in reverse,
        and so the bytes are still there to inspect when the question is *why* the
        overlay was wrong. Takes effect at the next daemon start, because the
        static routes bind their directory at app construction; the caller is told
        so rather than left to discover it.
        """
        state = self.state
        if not state.digest:
            raise OverlayRefused(
                REASON_NOTHING_INSTALLED,
                "No frontend overlay is installed, so there is nothing to revert; the "
                "bundled frontend is already what is being served.",
            )
        if not state.active:
            return {
                "reverted": True,
                "changed": False,
                "message": "The overlay was already reverted.",
                "state": state.as_dict(),
            }
        state.active = False
        state.reverted_at = float(self._clock())
        _write_state(state_path(self._data_dir), state)
        log.warning(
            "the frontend overlay was reverted; the bundled frontend serves after the "
            "next daemon start",
            extra={"overlay_digest": state.digest},
        )
        return {
            "reverted": True,
            "changed": True,
            "message": (
                "The overlay is switched off. The bundled frontend serves from the "
                "next daemon start - reload the daemon to apply it now."
            ),
            "state": state.as_dict(),
        }

    def restore(self) -> dict[str, Any]:
        """Switch a reverted overlay back on. The exact inverse of `revert`."""
        state = self.state
        if not state.digest:
            raise OverlayRefused(
                REASON_NOTHING_INSTALLED,
                "No frontend overlay is installed, so there is nothing to restore.",
            )
        if state.active:
            return {
                "restored": True,
                "changed": False,
                "message": "The overlay is already switched on.",
                "state": state.as_dict(),
            }
        if not tree_path(self._data_dir, state.digest).is_dir():
            raise OverlayRefused(
                REASON_TREE_MISSING,
                "The overlay's files are no longer on disk, so there is nothing to "
                "restore. Install it again.",
            )
        state.active = True
        state.reverted_at = None
        _write_state(state_path(self._data_dir), state)
        return {
            "restored": True,
            "changed": True,
            "message": (
                "The overlay is switched on. It serves from the next daemon start - "
                "reload the daemon to apply it now."
            ),
            "state": state.as_dict(),
        }


async def _http_download(
    url: str, *, write: Callable[[bytes], None], max_bytes: int
) -> tuple[int, int | None]:
    """A bounded streaming GET carrying nothing identifying.

    The same `DummyCookieJar` posture as every other outbound request this
    project makes, for the same reason: a `Set-Cookie` accepted here would be an
    install id on the next one.
    """
    import aiohttp

    timeout = aiohttp.ClientTimeout(
        total=DOWNLOAD_TIMEOUT_SECONDS, sock_read=DOWNLOAD_CHUNK_TIMEOUT_SECONDS
    )
    async with aiohttp.ClientSession(
        timeout=timeout, cookie_jar=aiohttp.DummyCookieJar()
    ) as session:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                return response.status, None
            declared = response.content_length
            received = 0
            async for chunk in response.content.iter_chunked(CHUNK_BYTES):
                received += len(chunk)
                if received > max_bytes:
                    # Stop reading rather than finish and reject: the point of a
                    # ceiling is not to write the bytes in the first place.
                    return response.status, declared
                write(chunk)
            return response.status, declared


__all__ = [
    "ARCHIVE_ROOT",
    "ARCHIVE_SUFFIX",
    "FAULT_REASONS",
    "FrontendChoice",
    "InstallResult",
    "MANIFEST_NAME",
    "OverlayManifest",
    "OverlayRefused",
    "OverlayState",
    "OverlayStore",
    "Verdict",
    "build_manifest",
    "daemon_api_digest",
    "extract_overlay",
    "file_digest",
    "log_choice",
    "route_table_digest",
    "overlay_root",
    "pack_overlay",
    "parse_manifest",
    "read_manifest",
    "resolve_frontend_dir",
    "state_path",
    "tree_digest",
    "tree_path",
    "verify_tree",
    "write_manifest",
]
