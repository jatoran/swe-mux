"""Installing a release: plan, download, verify, and hand to the staged swap.

`update_check.py` is the passive half - it says a newer release exists and stops
there. This is the deliberate half, and everything about it is arranged so that
the operator's live fleet is never the thing that pays for a mistake.

**It is the same swap as a local redeploy, with a download where the PyInstaller
build used to be.** `bundle_apply.py` stages into `<install root>/.staging`
while the old app serves, stops it only after the staging tree is good, swaps,
health-checks, and rolls back to `<name>.prev` when the new build never turns
healthy. Nothing here re-implements any of that; this module's whole job is to
produce an archive worth handing to it, and a process that can run it.

**That process is a copy of the console client, and it comes out of the
archive.** An installed copy has no source checkout and no `uv`, and the app
bundle cannot rename the directory it is running from, so the swap has to run
from somewhere outside every tree it touches. The release archive carries
`swe-mux-cli/` (`bundle_archive.py`); the updater extracts it into
`<data_dir>/updates/applier-<id>/` and runs `swemux update-apply` from there.
The applier is therefore always the release being installed, never a stale
copy, and an archive from before the client was carried falls back to the
installed client beside the app. Only when neither exists is the install
refused (`no_applier`), which is the pre-2026-09-05 case of a portable archive
unpacked without its siblings.

Seven properties, each a constraint rather than a preference:

**Nothing downloads without an explicit act.** `POST /api/update/install` carries
the same explicit-gesture header the manual check does, and it must *name the
version it intends to install* - the one the operator was shown. A manifest that
moved between the banner and the press is refused rather than silently installed,
because "I pressed the button about 0.3.0" is the only consent that was given.

**The SHA-256 is checked against the manifest before anything is staged.** The
manifest carries hashes precisely so this check exists; an unverified download
that reaches the swap is an arbitrary-code-execution path with a network
attacker at one end and a replaced application at the other. The digest is
computed over the bytes as they arrive, the file lands under a `.part` name, and
only a matching digest promotes it - so a partial or tampered download is not a
file the swap can even see. A hash is taken from the **manifest** and never from
the GitHub fallback, which publishes none: a release the fallback found can be
announced and cannot be installed.

**Sessions survive unless the operator says otherwise, and the ask is explicit.**
The PTY supervisor owns every live pseudoterminal and is exactly why an app swap
preserves sessions; updating it reaps the whole fleet. The incoming bundle
declares the supervisor protocol it speaks (`bundle_metadata.py`), the running
supervisor declares its own in `<data_dir>/supervisor.json`, and a difference
means the release can only be installed by replacing the supervisor too. That
is never done behind the operator's back: the install is refused with
`supervisor_update_required` and `consent: "supervisor_update"`, the plan says
so before any download, and only a request carrying
`accept_supervisor_update: true` proceeds - in **replace** mode, which stops with
quit intent (every session ends) and swaps the supervisor bundle as well. It is
deliberately `!=` rather than `>`: the supervisor's `hello` refuses any mismatch,
so a downgrade strands the fleet exactly as a bump does. And it is deliberately
the *protocol*, not a source hash: `build_desktop.supervisor_source_hash()` mixes
in the build machine's own package versions, so hashes never match across a
release and comparing them would refuse every update forever. Consent is
permission, not an instruction: a release whose supervisor protocol matches the
running one installs in swap mode whether or not consent was given.

**The plan answers before the download.** `POST /api/update/plan` reads the
manifest and the release's `.bundle.json` sidecar (a few hundred bytes, hashed
like every other artifact) to say which mode this release needs, what it will
rewrite, and what it will end - so the confirm dialog states the cost of the
press before it is pressed. For a release published without the sidecar the
plan says the supervisor question is unknown, and the install answers it from
the archive's own `bundle.json` after the download, refusing exactly as it
would have.

**A failure leaves the running app untouched.** Every refusal here happens before
the applier is spawned, and therefore before anything stops. Past that point the
swap's own guarantees apply unchanged.

**An interrupted download restarts cleanly.** The `.part` file is truncated and
re-fetched rather than resumed: an HTTP range resume would have to trust a server
to have handed back the same bytes, and the whole point of this module is that
bytes are trusted only after a full-file digest. An archive that *did* verify is
kept and reused, which is the resume that is actually worth having - and the
reason a refusal for want of consent costs nothing to retry with it.

**Most of a release is already on the machine, and the install says so before it
starts.** A release publishes a per-file hash manifest beside its archive
(`bundle_manifest.py`), verified against `version.json` exactly like the archive
is, so this module can hash the installed bundle and report how much of the
incoming one it already has *before* committing to a several-hundred-megabyte
download. That preview is advisory and nothing branches on it: the swap
recomputes the same plan authoritatively from the manifest inside the archive
(`bundle_stage.py`), because that copy is covered by the whole-archive digest the
swap has already checked.

**A source install is not a frozen app.** `uv tool install` and `pipx` users
update with the tool's own upgrade command; there is no bundle to swap and
pretending otherwise would be the first thing most operators hit. The install
kind is read from `sys.frozen` plus the presence of a bundle root, and a source
install is told what to run instead - the exact command for how *this* copy was
installed, not a guess.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import aiohttp

from . import __version__
from .bundle_apply import (
    MODE_REPLACE,
    MODE_SWAP,
    Layout,
)
from .bundle_archive import (
    ARCHIVE_ROOT,
    CLI_ROOT,
    TAR_GZ_SUFFIX,
    ZIP_SUFFIX,
    ArchiveError,
    archive_bundle_names,
    extract_roots,
    file_digest,
    read_archive_metadata,
)
from .bundle_manifest import (
    DELTA_NO_MANIFEST,
    DeltaPlan,
    parse_file_manifest,
    plan_delta,
)
from .bundle_metadata import BundleMetadata, parse_bundle_metadata
from .config import Config
from .host_platform import platform_key
from .install_location import (
    INSTALL_PIPX,
    INSTALL_UV_TOOL,
    detect_install_location,
    installer_registration,
)
from .redeploy_launch import (
    RedeployInFlight,
    claim_redeploy_lock,
    redeploy_lock_pid,
    redeploy_source_root,
    spawn_applier,
)
from .supervisor import discovery_path
from .tls_trust import trusting_connector
from .update_check import (
    MALFORMED,
    MANIFEST_URL,
    UNREACHABLE,
    UNSUPPORTED_SCHEMA,
    Artifact,
    Fetcher,
    Release,
    http_fetch,
    is_newer,
    parse_manifest,
)

log = logging.getLogger(__name__)

# --- the artifact naming contract --------------------------------------------
#
# The manifest names artifacts and says nothing about what any of them is for, so
# the updater has to recognize its own platform's bundle by name. That makes the
# name a contract between `release.yml` and every installed build, exactly like
# the manifest path itself: it may not be changed to suit a build script, and a
# release that publishes nothing matching it is reported as "no artifact for this
# platform" rather than guessed at.
#
# `packaging/package_desktop_release.py` is the writer, and it derives every name
# from the functions here so the two cannot drift.

#: Extensions per platform. Windows gets a zip because that is what Explorer and
#: `Expand-Archive` open with nothing installed; POSIX gets a tarball because a
#: zip does not carry the executable bit back out of `ZipFile.extractall`, so an
#: extracted `swe-mux` binary would arrive unable to run.
#:
#: Both values are taken from `bundle_archive`, which is what can actually open
#: one. Until 2026-08-28 the `.tar.gz` here was a name no reader honoured - a
#: promise that would have turned the first POSIX desktop release into a refusal
#: to install. Naming the reader's own constants is what keeps the two halves
#: from drifting apart again.
_ARCHIVE_SUFFIX = {
    "windows": ZIP_SUFFIX,
    "macos": TAR_GZ_SUFFIX,
    "linux": TAR_GZ_SUFFIX,
}

#: The Windows installer's extension. Only Windows has one: it is an Inno Setup
#: executable, and there is deliberately no invented equivalent for a platform
#: that has no desktop wrapper yet.
_INSTALLER_SUFFIX = {"windows": "-setup.exe"}


def release_platform_tag() -> str:
    """`windows-x64`, `macos-arm64`, ... - this host's slot in a release.

    The machine string is normalized because the same architecture answers
    `AMD64` on Windows and `x86_64` on Linux, and an artifact name that depended
    on which OS you asked would be two contracts wearing one name.
    """
    machine = platform.machine().lower()
    architecture = {
        "amd64": "x64",
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine or "unknown")
    return f"{platform_key()}-{architecture}"


def release_archive_name(version: str, tag: str | None = None) -> str:
    """The desktop bundle artifact's name for a version on this host.

    This is the **portable** archive - the one the in-app updater downloads,
    verifies and hands to the staged swap. The Windows installer beside it in a
    release is a different artifact with a different name and a different job;
    see `release_installer_name`.
    """
    platform_tag = tag or release_platform_tag()
    host = platform_tag.split("-", 1)[0]
    return f"swe-mux-{version}-{platform_tag}{_ARCHIVE_SUFFIX.get(host, ZIP_SUFFIX)}"


def release_file_manifest_name(version: str, tag: str | None = None) -> str:
    """The per-file hash manifest's artifact name for a version on this host.

    A third name on the same contract, and derived from the same two facts as
    the archive's so a release cannot publish one without the other being
    findable. It is the *sidecar* copy: identical bytes to the `files.json`
    inside the archive, published separately so the updater can plan a delta -
    and say how much of the bundle it will rewrite - before committing to a
    several-hundred-megabyte download.

    Its absence from a release is a normal state, not a failure. Every release
    published before this existed has none, and the updater installs those
    exactly as it always did.
    """
    platform_tag = tag or release_platform_tag()
    return f"swe-mux-{version}-{platform_tag}.files.json"


def release_bundle_metadata_name(version: str, tag: str | None = None) -> str:
    """The bundle metadata sidecar's artifact name for a version on this host.

    The app bundle's `bundle.json`, byte for byte, published beside the archive
    so the daemon can answer *before* a download whether this release replaces
    the PTY supervisor - the one question the operator has to answer before
    anything is fetched. The copy inside the archive stays authoritative; the
    install re-reads it out of the verified download and refuses on a
    disagreement. Absent from every release before 2026-09-05, which the plan
    reports as "unknown until downloaded" rather than guessing.
    """
    platform_tag = tag or release_platform_tag()
    return f"swe-mux-{version}-{platform_tag}.bundle.json"


def release_installer_name(version: str, tag: str | None = None) -> str | None:
    """The platform installer's artifact name, or None where there is no installer.

    A second name on the same contract, and separate from `release_archive_name`
    for a reason the updater depends on: it looks its own artifact up by *exact*
    name, so the installer has to be unmistakably not that. `-setup.exe` cannot
    collide with a `.zip` under any version string.

    `None` rather than a guessed name off Windows. An invented
    `swe-mux-1.2.3-linux-x64-setup.exe` would be a name the release will never
    carry, and a caller looking for one would report "missing" about a thing that
    was never promised.
    """
    platform_tag = tag or release_platform_tag()
    host = platform_tag.split("-", 1)[0]
    suffix = _INSTALLER_SUFFIX.get(host)
    if suffix is None:
        return None
    return f"swe-mux-{version}-{platform_tag}{suffix}"


#: Where verified archives rest, under the data dir rather than in the bundle:
#: the bundle is the thing being replaced.
DOWNLOAD_DIRNAME = "updates"

#: Where the applier - a copy of the console client - is unpacked to run from.
#: One directory per attempt, named by the attempt, because a finished applier
#: on Windows may still hold its own image for a moment and a fixed name would
#: refuse the next attempt for it; older ones are pruned best-effort.
APPLIER_DIRNAME_PREFIX = "applier-"

#: How many verified archives are kept. Two, so a rollback has the bundle it came
#: from to hand and nothing accumulates: these are hundreds of megabytes each.
KEEP_ARCHIVES = 2

#: Refuse a body larger than this. A desktop bundle is a few hundred megabytes;
#: this is the ceiling past which the file is not the artifact we asked for, and
#: it bounds what an unfriendly host can make us write to disk.
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024

#: Read granularity. Large enough that hashing is not syscall-bound, small enough
#: that progress moves visibly on a slow link.
CHUNK_BYTES = 1024 * 1024

#: Refuse a file manifest larger than this. It is one JSON object per file in the
#: bundle, which for the 2937-file bundle measured 2026-08-29 is about 400 KB;
#: this is the size past which the file is not that document. Held in memory
#: rather than written to disk, because nothing downstream reads it from there
#: and a small file under the downloads directory would compete with the
#: archives `_prune_downloads` is counting.
MAX_FILE_MANIFEST_BYTES = 32 * 1024 * 1024

#: Refuse a bundle metadata sidecar larger than this: it is five fields.
MAX_BUNDLE_METADATA_BYTES = 64 * 1024

#: A download is minutes rather than seconds, so it gets its own budget: the
#: manifest fetch's 10s ceiling would kill every real install. Bounded all the
#: same, because a stalled socket must not pin a task forever.
DOWNLOAD_TIMEOUT_SECONDS = 30 * 60.0
#: ...and a per-chunk ceiling, which is what actually catches a stalled peer: a
#: total budget alone lets a server dribble one byte an hour for half an hour.
DOWNLOAD_CHUNK_TIMEOUT_SECONDS = 120.0

STATE_FILENAME = "update-install.json"
STATE_SCHEMA = 2

# --- the closed vocabulary ---------------------------------------------------
#
# Phases and reasons are closed sets for the reason the update check's statuses
# are: a surface branches on the word, and a new failure mode has to be named
# rather than rendering as an existing one.

PHASE_IDLE = "idle"
PHASE_DOWNLOADING = "downloading"
PHASE_VERIFYING = "verifying"
PHASE_INSPECTING = "inspecting"
PHASE_PREPARING = "preparing"
PHASE_HANDED_OFF = "handed_off"
PHASE_REFUSED = "refused"
PHASE_FAILED = "failed"

#: Refusals - the install did not happen and nothing was touched.
REASON_OK = "ok"
REASON_SOURCE_INSTALL = "source_install"
REASON_UNSUPPORTED_PLATFORM = "unsupported_platform"
REASON_DISABLED = "update_check_disabled"
REASON_NO_ARTIFACT = "no_artifact"
REASON_VERSION_MISMATCH = "version_mismatch"
REASON_NOT_NEWER = "not_newer"
REASON_HASH_MISMATCH = "hash_mismatch"
REASON_TRUNCATED = "truncated"
REASON_OVERSIZED = "oversized"
REASON_DOWNLOAD_FAILED = "download_failed"
REASON_ARCHIVE_INVALID = "archive_invalid"
REASON_BUNDLE_METADATA_MISSING = "bundle_metadata_missing"
REASON_SUPERVISOR_UPDATE_REQUIRED = "supervisor_update_required"
REASON_SUPERVISOR_IN_BUNDLE = "supervisor_in_bundle"
REASON_SUPERVISOR_UNKNOWN = "supervisor_unknown"
REASON_NO_SUPERVISOR = "no_supervisor"
REASON_NO_APPLIER = "no_applier"
REASON_IN_PROGRESS = "in_progress"
#: Reused verbatim from the check, so one word means one thing across both.
REASON_UNREACHABLE = UNREACHABLE
REASON_MALFORMED = MALFORMED
REASON_UNSUPPORTED_SCHEMA = UNSUPPORTED_SCHEMA

#: The one consent the install can ask for. Carried on a refusal as `consent`,
#: so a surface knows that re-sending the request with
#: `accept_supervisor_update: true` is what proceeds - and that doing so ends
#: every live session.
CONSENT_SUPERVISOR_UPDATE = "supervisor_update"
#: The refusals that consent answers. Named as a set so the route, the CLI and
#: the browser branch on one list rather than three.
CONSENT_REASONS = frozenset(
    {REASON_SUPERVISOR_UPDATE_REQUIRED, REASON_SUPERVISOR_IN_BUNDLE, REASON_NO_SUPERVISOR}
)


class UpdateRefused(Exception):
    """A refusal with a machine word and a sentence for a human.

    Every refusal in this module raises rather than returning, because the
    control flow has one shape - stop, record, report - and a returned sentinel
    invites a caller to continue past one. `consent` names what would let the
    same request proceed, when something would.
    """

    def __init__(self, reason: str, message: str, *, consent: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.consent = consent


# --- install kind -------------------------------------------------------------

INSTALL_FROZEN = "frozen"
INSTALL_SOURCE = "source"

#: How a frozen install is managed - which decides only what happens *after* a
#: successful swap, never whether one may happen.
MANAGED_CHECKOUT = "checkout"
MANAGED_INSTALLER = "installer"
MANAGED_PORTABLE = "portable"


@dataclass(frozen=True, slots=True)
class InstallKind:
    """What this process is running as, and what may therefore be done to it."""

    kind: str
    bundle_root: Path | None
    #: The command that *does* update this install, when it is not a bundle swap.
    upgrade_command: str
    #: `checkout`, `installer`, or `portable` for a frozen install; "" otherwise.
    managed: str = ""
    #: The directory holding the three sibling bundles, for a frozen install.
    install_root: Path | None = None

    @property
    def swappable(self) -> bool:
        return self.kind == INSTALL_FROZEN and self.bundle_root is not None

    @property
    def layout(self) -> Layout | None:
        if self.install_root is not None:
            return Layout(self.install_root)
        return Layout.for_bundle(self.bundle_root) if self.bundle_root is not None else None


def _source_upgrade_command() -> str:
    """The exact command that upgrades *this* source install.

    Derived from how the copy got here rather than guessed: `uv tool upgrade`
    is wrong advice for a pipx user and both are wrong for a plain venv.
    """
    try:
        location = detect_install_location()
    except Exception:  # noqa: BLE001 - a description must never break the updater
        return "uv tool upgrade swe-mux"
    if location.source_checkout:
        return "git pull && uv sync"
    if location.kind == INSTALL_UV_TOOL:
        return "uv tool upgrade swe-mux"
    if location.kind == INSTALL_PIPX:
        return "pipx upgrade swe-mux"
    return f"{location.pip_command} install --upgrade swe-mux"


def detect_install_kind(
    *,
    frozen: bool | None = None,
    executable: str | None = None,
    registration_reader: Callable[[str, str], str | None] | None = None,
) -> InstallKind:
    """Whether this is the frozen desktop app or a source/wheel install.

    Read from `sys.frozen` and the executable's own location, never from whether
    a `dist/` directory exists beside the checkout: a source daemon run with
    `uv run` in a repository that also contains a built bundle is still a source
    install, and swapping the bundle underneath it would update an application
    that is not the one running (`frozen-app-detection-asset-hash`, the same trap
    the CLAUDE.md asset-hash check exists for).

    A frozen install is further described as `checkout` (the bundle sits in a
    source checkout's `dist/`), `installer` (the Windows installer registered
    this install root), or `portable`. The three swap identically; the answer
    decides only whether the installer's registry entry is brought up to date
    afterwards, and which manual path the messages name.
    """
    import sys

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return InstallKind(
            kind=INSTALL_SOURCE,
            bundle_root=None,
            upgrade_command=_source_upgrade_command(),
        )
    exe = Path(executable or sys.executable)
    try:
        root = exe.resolve().parent
    except OSError:
        root = exe.parent
    install_root = root.parent
    managed = MANAGED_PORTABLE
    if redeploy_source_root() is not None:
        managed = MANAGED_CHECKOUT
    else:
        registration = installer_registration(read=registration_reader)
        if registration is not None and registration.manages(install_root):
            managed = MANAGED_INSTALLER
    return InstallKind(
        kind=INSTALL_FROZEN,
        bundle_root=root,
        upgrade_command="",
        managed=managed,
        install_root=install_root,
    )


# --- the downloader ------------------------------------------------------------


@dataclass(slots=True)
class DownloadOutcome:
    """What a stream produced: enough to tell truncation from a bad hash."""

    status: int
    #: `Content-Length` when the server declared one, else None.
    declared_bytes: int | None
    received_bytes: int


class Downloader(Protocol):
    """One bounded streaming GET. Injected so tests need no network.

    `write` is called with each chunk as it arrives, so nothing here ever holds
    an artifact in memory. Raising means the transfer failed part-way, which the
    caller treats as `download_failed` and never as a complete file.
    """

    async def __call__(
        self,
        url: str,
        *,
        write: Callable[[bytes], None],
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
    ) -> DownloadOutcome: ...


async def http_download(
    url: str,
    *,
    write: Callable[[bytes], None],
    max_bytes: int,
    headers: Mapping[str, str] | None = None,
) -> DownloadOutcome:
    """The real downloader: a plain streaming GET carrying nothing identifying.

    The same `DummyCookieJar` posture as the manifest fetch, for the same reason:
    a `Set-Cookie` accepted here would be an install id on the next request. The
    redirect to the release CDN is followed, because that is how GitHub serves a
    release asset at all.
    """
    timeout = aiohttp.ClientTimeout(
        total=DOWNLOAD_TIMEOUT_SECONDS, sock_read=DOWNLOAD_CHUNK_TIMEOUT_SECONDS
    )
    async with aiohttp.ClientSession(
        timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(), connector=trusting_connector()
    ) as session:
        async with session.get(url, headers=dict(headers or {}), allow_redirects=True) as response:
            if response.status != 200:
                return DownloadOutcome(
                    status=response.status, declared_bytes=None, received_bytes=0
                )
            declared = response.content_length
            received = 0
            async for chunk in response.content.iter_chunked(CHUNK_BYTES):
                received += len(chunk)
                if received > max_bytes:
                    # Stop reading rather than finish and reject: the point of a
                    # ceiling is not to write the bytes in the first place.
                    return DownloadOutcome(
                        status=response.status,
                        declared_bytes=declared,
                        received_bytes=received,
                    )
                write(chunk)
            return DownloadOutcome(
                status=response.status, declared_bytes=declared, received_bytes=received
            )


# --- state --------------------------------------------------------------------


@dataclass(slots=True)
class _State:
    """The last install attempt, durable because the daemon dies mid-swap.

    Once the archive is handed off, the applier stops this very daemon - so a
    record that lived only in memory would be gone exactly when someone wanted to
    know what happened. `redeploy-result.json` records the *swap's* outcome; this
    records everything up to it, which is the half a rollback record cannot
    explain.
    """

    phase: str = PHASE_IDLE
    reason: str = ""
    message: str = ""
    version: str = ""
    artifact: str = ""
    archive: str = ""
    bytes_downloaded: int = 0
    bytes_total: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    #: Correlates every log line of one attempt, and the redeploy log it spawned.
    install_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    #: `DeltaPlan.as_dict()` for this release against the installed bundle, or
    #: `{}` before one has been computed. Advisory: the swap recomputes it.
    delta: dict[str, Any] = field(default_factory=dict)
    #: `swap` or `replace` once the supervisor gate has decided; "" before.
    mode: str = ""
    #: Whether the request carried consent to end every session.
    accepted_supervisor_update: bool = False
    #: What a refusal is waiting on (`CONSENT_*`), or "".
    consent: str = ""
    #: The applier executable the attempt ran, once prepared.
    applier: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "reason": self.reason,
            "message": self.message,
            "version": self.version,
            "artifact": self.artifact,
            "archive": self.archive,
            "bytes_downloaded": self.bytes_downloaded,
            "bytes_total": self.bytes_total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "install_id": self.install_id,
            "events": list(self.events),
            "delta": dict(self.delta),
            "mode": self.mode,
            "accepted_supervisor_update": self.accepted_supervisor_update,
            "consent": self.consent,
            "applier": self.applier,
        }


#: How many phase transitions one attempt keeps. An attempt has about eight; the
#: bound exists so a pathological retry loop cannot grow the file without limit.
MAX_EVENTS = 40


@dataclass(slots=True)
class SupervisorVerdict:
    """What the supervisor gate decided, and why, in the terms a surface shows.

    `mode` is the swap mode the install would run in; `reason` is the refusal
    that stands unless consent is given (one of `CONSENT_REASONS`, or ""). The
    two are computed together because they are one decision: the reason is what
    consent turns into `MODE_REPLACE`.
    """

    mode: str
    reason: str
    message: str
    running_protocol: int | None
    incoming_protocol: int | None
    #: Whether the incoming protocol was actually read (False before a download
    #: of a release with no metadata sidecar).
    known: bool

    @property
    def reaps_sessions(self) -> bool:
        return self.mode == MODE_REPLACE

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "message": self.message,
            "running_protocol": self.running_protocol,
            "incoming_protocol": self.incoming_protocol,
            "known": self.known,
            "reaps_sessions": self.reaps_sessions,
            "consent": CONSENT_SUPERVISOR_UPDATE if self.reason else "",
        }


class UpdateInstaller:
    """Owns one install attempt at a time: download, verify, gate, hand off.

    Constructed with everything it touches, so the whole of it is testable with
    no network, no bundle, and no supervisor: `download` supplies bytes, `fetch`
    supplies the manifest, `install_kind` supplies the deployment shape, and
    `handoff` supplies the swap.
    """

    def __init__(
        self,
        config: Config,
        *,
        current_version: str = __version__,
        manifest_url: str = MANIFEST_URL,
        fetch: Fetcher | None = None,
        download: Downloader | None = None,
        install_kind: InstallKind | None = None,
        handoff: Callable[[Path, str, str], int] | None = None,
        clock: Callable[[], float] = time.time,
        platform_tag: str | None = None,
        supervisor_exe: Callable[[], Path | None] | None = None,
    ) -> None:
        self._config = config
        self._current_version = current_version
        self._manifest_url = manifest_url
        self._fetch: Fetcher = fetch if fetch is not None else http_fetch
        self._download: Downloader = download if download is not None else http_download
        self._install_kind = install_kind
        self._handoff = handoff
        self._clock = clock
        self._platform_tag = platform_tag
        self._supervisor_exe = supervisor_exe
        self._state = _State()
        self._loaded = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        #: Set by the daemon once its event bus exists: tells every client a
        #: swap has begun, minutes before the daemon goes away, exactly as
        #: `POST /api/daemon/redeploy` does for a rebuild.
        self.announce: Callable[[int, str], Awaitable[None]] | None = None

    # -- identity -------------------------------------------------------------

    @property
    def install_kind(self) -> InstallKind:
        if self._install_kind is None:
            self._install_kind = detect_install_kind()
        return self._install_kind

    @property
    def platform_tag(self) -> str:
        if self._platform_tag is None:
            self._platform_tag = release_platform_tag()
        return self._platform_tag

    @property
    def _path(self) -> Path:
        return Path(self._config.data_dir) / STATE_FILENAME

    @property
    def downloads_dir(self) -> Path:
        return Path(self._config.data_dir) / DOWNLOAD_DIRNAME

    # -- state ----------------------------------------------------------------

    async def ensure_loaded(self) -> None:
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            self._state = await asyncio.to_thread(self._read_state)
            self._loaded = True

    def _read_state(self) -> _State:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _State()
        except (OSError, ValueError):
            log.warning(
                "update install state unreadable; starting from empty",
                extra={"update_state_path": str(self._path)},
            )
            return _State()
        if not isinstance(payload, dict) or payload.get("schema") not in (1, STATE_SCHEMA):
            return _State()
        state = _State()
        for name in (
            "phase",
            "reason",
            "message",
            "version",
            "artifact",
            "archive",
            "install_id",
            "mode",
            "consent",
            "applier",
        ):
            value = payload.get(name)
            if isinstance(value, str):
                setattr(state, name, value)
        for name in ("bytes_downloaded", "bytes_total"):
            value = payload.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(state, name, value)
        for name in ("started_at", "finished_at"):
            value = payload.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(state, name, float(value))
        state.accepted_supervisor_update = payload.get("accepted_supervisor_update") is True
        events = payload.get("events")
        if isinstance(events, list):
            state.events = [item for item in events[:MAX_EVENTS] if isinstance(item, dict)]
        delta = payload.get("delta")
        if isinstance(delta, dict):
            state.delta = delta
        # A daemon that died mid-download comes back saying so rather than
        # claiming a transfer is still running: nothing is transferring, because
        # the process that was doing it is gone.
        if state.phase in {PHASE_DOWNLOADING, PHASE_VERIFYING, PHASE_INSPECTING, PHASE_PREPARING}:
            state.phase = PHASE_FAILED
            state.reason = REASON_DOWNLOAD_FAILED
            state.message = (
                "The daemon restarted while an update was downloading, so the "
                "transfer was abandoned. Nothing was installed; start it again."
            )
        return state

    def _write_state(self) -> None:
        """Atomic, and never raises: this is housekeeping on a UI path."""
        payload = {"schema": STATE_SCHEMA, **self._state.as_dict()}
        path = self._path
        temp = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            log.warning(
                "could not persist update install state",
                extra={"update_state_path": str(path), "error_type": type(exc).__name__},
            )

    def _record(
        self, phase: str, *, reason: str = "", message: str = "", consent: str = ""
    ) -> None:
        """Move to a phase, log it, and persist it.

        Every transition is logged and every transition is durable. This is the
        one code path in the project that replaces the application while the
        operator is not watching, so "what did it do and how far did it get" has
        to be answerable afterwards from the log alone.
        """
        self._state.phase = phase
        self._state.reason = reason
        self._state.message = message
        self._state.consent = consent
        entry = {"phase": phase, "at": float(self._clock())}
        if reason:
            entry["reason"] = reason
        self._state.events = [*self._state.events, entry][-MAX_EVENTS:]
        if phase in {PHASE_HANDED_OFF, PHASE_REFUSED, PHASE_FAILED}:
            self._state.finished_at = float(self._clock())
        level = log.warning if phase in {PHASE_REFUSED, PHASE_FAILED} else log.info
        level(
            "update install %s",
            phase,
            extra={
                "install_id": self._state.install_id,
                "update_phase": phase,
                "update_reason": reason,
                "update_version": self._state.version,
                "update_artifact": self._state.artifact,
                "update_bytes": self._state.bytes_downloaded,
                "update_bytes_total": self._state.bytes_total,
                "update_mode": self._state.mode,
                "update_consent": consent,
            },
        )
        self._write_state()

    # -- the answer -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Everything a surface needs, computed from state. No I/O, never raises."""
        kind = self.install_kind
        state = self._state
        return {
            "install_kind": kind.kind,
            "managed": kind.managed,
            "swappable": kind.swappable,
            "bundle_root": str(kind.bundle_root) if kind.bundle_root else "",
            "install_root": str(kind.install_root) if kind.install_root else "",
            "upgrade_command": kind.upgrade_command,
            "platform": self.platform_tag,
            "artifact_name": release_archive_name("<version>", self.platform_tag),
            "current_version": self._current_version,
            "running": self.running,
            **state.as_dict(),
        }

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # -- lifecycle ------------------------------------------------------------

    async def stop(self) -> None:
        """Cancel an in-flight download at daemon shutdown, and wait for it.

        Awaited rather than fired and forgotten: a task still holding a socket
        and a file handle when the loop closes is the failure mode that reddens
        somebody else's test, and here it would also leave a `.part` file with no
        writer.
        """
        task = self._task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

    async def wait(self) -> None:
        """Await the current attempt, if any. Used by tests and by `stop`."""
        task = self._task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # -- the plan -------------------------------------------------------------

    async def plan(self, version: str) -> dict[str, Any]:
        """What installing `version` would do, without downloading the archive.

        Reaches the network for the manifest and the two small sidecars only.
        Raises `UpdateRefused` for the refusals that need no archive - a source
        install, a manifest that moved, no artifact for this platform - and
        answers the rest as facts: the mode the release needs, whether that
        needs consent, and how much of the bundle would be rewritten. Nothing
        here is persisted; the plan is the confirm dialog's content and the
        install re-derives every part of it.
        """
        await self.ensure_loaded()
        kind = self.install_kind
        wanted = str(version).strip()
        if kind.kind == INSTALL_SOURCE:
            raise UpdateRefused(REASON_SOURCE_INSTALL, self._source_refusal(kind))
        release, artifact = await self._resolve_artifact(wanted, record=False)
        verdict = await self._supervisor_verdict(
            await self._incoming_protocol(release), accepted=False
        )
        delta = await self._delta_plan(release)
        installer = release_installer_name(release.version, self.platform_tag)
        return {
            "version": release.version,
            "current_version": self._current_version,
            "changelog": release.changelog,
            "published": release.published,
            "install_kind": kind.kind,
            "managed": kind.managed,
            "install_root": str(kind.install_root) if kind.install_root else "",
            "artifact": artifact.as_dict(),
            "installer": next(
                (item.as_dict() for item in release.artifacts if item.name == installer), None
            ),
            "supervisor": verdict.as_dict(),
            "mode": verdict.mode,
            "reaps_sessions": verdict.reaps_sessions,
            "consent": CONSENT_SUPERVISOR_UPDATE if verdict.reason else "",
            "consent_reason": verdict.reason,
            "delta": delta.as_dict(),
            "archive_cached": (self.downloads_dir / artifact.name).is_file(),
        }

    # -- the install ----------------------------------------------------------

    async def start(
        self, version: str, *, accept_supervisor_update: bool = False
    ) -> dict[str, Any]:
        """Begin an install of exactly `version`, in the background.

        Returns immediately with the snapshot the caller should render. The work
        runs as a task because a download is minutes long and the request that
        started it is a button press, not a transfer. `accept_supervisor_update`
        is the operator's consent to end every live session if - and only if -
        this release cannot be installed any other way.
        """
        await self.ensure_loaded()
        if self.running:
            raise UpdateRefused(
                REASON_IN_PROGRESS,
                "An update is already downloading; wait for it to finish or restart the daemon.",
            )
        self._state = _State(
            version=str(version).strip(),
            started_at=float(self._clock()),
            install_id=f"{int(self._clock())}-{os.getpid()}",
            accepted_supervisor_update=bool(accept_supervisor_update),
        )
        # Refusals that need nothing from the network are made here, so the
        # caller gets them as the response to its own request rather than having
        # to poll for them - and recorded anyway, so `swemux update` and the next
        # daemon can still say why the last attempt did nothing. A refusal
        # answered only in an HTTP body is one nobody can find afterwards.
        try:
            self._preflight()
        except UpdateRefused as refusal:
            self._record(
                PHASE_REFUSED,
                reason=refusal.reason,
                message=refusal.message,
                consent=refusal.consent,
            )
            raise
        self._record(PHASE_DOWNLOADING)
        self._task = asyncio.create_task(self._run(str(version).strip()), name="update-install")
        return self.snapshot()

    def _source_refusal(self, kind: InstallKind) -> str:
        return (
            "This is a source install, not the frozen desktop app: there is no "
            f"bundle to swap. Update it with `{kind.upgrade_command}`, then restart "
            "the daemon (`swemux reload-daemon` keeps your sessions)."
        )

    def _preflight(self) -> None:
        """Every refusal that can be made without touching the network."""
        kind = self.install_kind
        if kind.kind == INSTALL_SOURCE:
            raise UpdateRefused(REASON_SOURCE_INSTALL, self._source_refusal(kind))
        if not kind.swappable:
            raise UpdateRefused(
                REASON_SOURCE_INSTALL,
                "This build reports itself frozen but names no bundle directory, so "
                "there is nothing a swap could replace.",
            )
        if not getattr(self._config, "update_check_enabled", True):
            raise UpdateRefused(
                REASON_DISABLED,
                "Update checks are turned off, so nothing will be fetched; enable "
                "them in Settings → Diagnostics.",
            )
        if redeploy_lock_pid(self._config) is not None:
            raise UpdateRefused(
                REASON_IN_PROGRESS,
                "A redeploy is already running, and it owns the bundle swap.",
            )

    async def _run(self, version: str) -> None:
        """The whole attempt. Never raises out of the task."""
        try:
            release, artifact = await self._resolve_artifact(version)
            await self._preview_delta(release)
            archive = await self._acquire(artifact)
            metadata = self._inspect(archive)
            verdict = await self._supervisor_verdict(
                metadata.supervisor_protocol,
                accepted=self._state.accepted_supervisor_update,
                known=True,
            )
            if verdict.reason:
                raise UpdateRefused(
                    verdict.reason, verdict.message, consent=CONSENT_SUPERVISOR_UPDATE
                )
            self._state.mode = verdict.mode
            applier = await self._prepare_applier(archive)
            self._hand_off(archive, release, artifact.sha256, applier, verdict.mode)
        except UpdateRefused as refusal:
            self._record(
                PHASE_REFUSED,
                reason=refusal.reason,
                message=refusal.message,
                consent=refusal.consent,
            )
        except asyncio.CancelledError:
            self._record(
                PHASE_FAILED,
                reason=REASON_DOWNLOAD_FAILED,
                message="The update was cancelled before anything was installed.",
            )
            raise
        except Exception as exc:  # noqa: BLE001 - a UI path must not raise
            log.exception(
                "update install failed unexpectedly",
                extra={"install_id": self._state.install_id},
            )
            self._record(
                PHASE_FAILED,
                reason=REASON_DOWNLOAD_FAILED,
                message=f"The update failed unexpectedly ({type(exc).__name__}). "
                "Nothing was installed.",
            )

    # -- step 1: the manifest, fetched fresh ----------------------------------

    async def _resolve_artifact(
        self, version: str, *, record: bool = True
    ) -> tuple[Release, Artifact]:
        """The manifest as it is *now*, and this platform's artifact in it.

        Re-fetched rather than read from the check's cached snapshot, because the
        release workflow uploads with `--clobber`: a hash the daily check stored
        yesterday is a claim about a file that may have been replaced since, and
        the one moment a hash is worth anything is the moment bytes are measured
        against it.

        `record=False` is the plan's: it reads the same manifest and must leave
        the attempt state alone, because a plan is a question and not an attempt.
        """
        try:
            status, body = await self._fetch(self._manifest_url, headers=None)
        except Exception as exc:  # noqa: BLE001 - offline is normal, not exceptional
            raise UpdateRefused(
                REASON_UNREACHABLE,
                f"{self._manifest_url} could not be reached ({type(exc).__name__}), "
                "so no release could be verified.",
            ) from exc
        if status != 200:
            raise UpdateRefused(
                REASON_UNREACHABLE,
                f"{self._manifest_url} answered HTTP {status}, so no release could be verified.",
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise UpdateRefused(
                REASON_MALFORMED,
                "The update manifest was not JSON, so nothing could be verified.",
            ) from exc
        release, reason = parse_manifest(payload)
        if release is None:
            if reason == UNSUPPORTED_SCHEMA:
                raise UpdateRefused(
                    REASON_UNSUPPORTED_SCHEMA,
                    "The update manifest uses a schema this build does not "
                    "understand, so it cannot tell what to install. Update by hand "
                    "from the release page.",
                )
            raise UpdateRefused(
                REASON_MALFORMED,
                "The update manifest could not be read, so nothing was installed.",
            )
        if release.version != version:
            raise UpdateRefused(
                REASON_VERSION_MISMATCH,
                f"The manifest now offers {release.version}, not the {version} this "
                "install was asked for. Check again and confirm the new version.",
            )
        newer = is_newer(release.version, self._current_version)
        if newer is False:
            raise UpdateRefused(
                REASON_NOT_NEWER,
                f"{release.version} is not newer than the running {self._current_version}.",
            )
        wanted = release_archive_name(release.version, self.platform_tag)
        for artifact in release.artifacts:
            if artifact.name == wanted:
                if record:
                    self._state.artifact = artifact.name
                return release, artifact
        raise UpdateRefused(
            REASON_NO_ARTIFACT,
            f"Release {release.version} publishes no {wanted}, so there is no "
            f"desktop bundle for {self.platform_tag} to install. "
            + (
                "The release was found through the GitHub fallback, which carries no "
                "hashes and therefore nothing verifiable."
                if release.source != "manifest"
                else "Install it by hand from the release page."
            ),
        )

    # -- step 1b: how much of this release is already here ---------------------

    async def _delta_plan(self, release: Release) -> DeltaPlan:
        """The delta this release would stage against the installed bundle.

        Every failure - a release from before the manifest existed, a manifest
        that will not parse, an unreadable installed bundle - answers an empty
        plan with its reason rather than raising. Nothing branches on it.
        """
        wanted = release_file_manifest_name(release.version, self.platform_tag)
        artifact = next((item for item in release.artifacts if item.name == wanted), None)
        if artifact is None:
            return DeltaPlan(reason=DELTA_NO_MANIFEST)
        try:
            payload = await self._fetch_small(artifact, MAX_FILE_MANIFEST_BYTES)
        except UpdateRefused as refusal:
            log.info(
                "update install could not preview the delta",
                extra={
                    "install_id": self._state.install_id,
                    "update_reason": refusal.reason,
                },
            )
            return DeltaPlan(reason=DELTA_NO_MANIFEST)
        manifest, reason = parse_file_manifest(payload)
        if manifest is None:
            log.warning(
                "the release's file manifest could not be read",
                extra={"install_id": self._state.install_id, "update_reason": reason},
            )
            return DeltaPlan(reason=DELTA_NO_MANIFEST)
        # Hashing a few hundred megabytes off a thread, because this daemon is
        # serving a UI while it does it and a twenty-second stall in the event
        # loop would read to every client as the app having died mid-update.
        return await asyncio.to_thread(plan_delta, manifest, self.install_kind.bundle_root)

    async def _preview_delta(self, release: Release) -> None:
        """Record how much of `release` the installed bundle already has.

        Advisory, and deliberately so. Nothing branches on the answer: the swap
        recomputes the identical plan from the `files.json` **inside** the
        archive, which is the copy covered by the whole-archive digest it has
        already verified. This one is here because it is the only moment the
        operator can be told what the install is about to cost, and because a
        number that arrives after the swap is a number nobody needed.
        """
        plan = await self._delta_plan(release)
        self._state.delta = plan.as_dict()
        log.info(
            "update install delta preview: %s",
            plan.summary(),
            extra={"install_id": self._state.install_id, **plan.as_dict()},
        )
        self._write_state()

    async def _fetch_small(self, artifact: Artifact, ceiling: int) -> object:
        """A sidecar's decoded JSON, verified against `version.json`.

        Held entirely in memory and hashed as it arrives, the same posture the
        archive download has and for the same reason: a document that failed its
        digest must never exist under a name something downstream might read.
        """
        chunks = bytearray()
        digest = hashlib.sha256()

        def write(chunk: bytes) -> None:
            digest.update(chunk)
            chunks.extend(chunk)

        try:
            outcome = await self._download(
                artifact.url, write=write, max_bytes=ceiling, headers=None
            )
        except Exception as exc:  # noqa: BLE001 - offline is ordinary here
            raise UpdateRefused(
                REASON_DOWNLOAD_FAILED,
                f"{artifact.name} could not be fetched ({type(exc).__name__}).",
            ) from exc
        if outcome.status != 200:
            raise UpdateRefused(
                REASON_UNREACHABLE, f"{artifact.name} answered HTTP {outcome.status}."
            )
        if outcome.received_bytes > ceiling:
            raise UpdateRefused(
                REASON_OVERSIZED, f"{artifact.name} exceeded the {ceiling} byte ceiling."
            )
        if digest.hexdigest() != artifact.sha256:
            raise UpdateRefused(
                REASON_HASH_MISMATCH,
                f"{artifact.name} does not match the SHA-256 the manifest publishes.",
            )
        try:
            return json.loads(bytes(chunks).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise UpdateRefused(REASON_MALFORMED, f"{artifact.name} was not JSON.") from exc

    # -- step 1c: what the release says about the supervisor, before download ---

    async def _incoming_protocol(self, release: Release) -> int | None:
        """The release's supervisor protocol from its metadata sidecar, or None.

        None means "not published" or "could not be read", both of which the
        plan reports as unknown; the install answers from the archive itself.
        """
        wanted = release_bundle_metadata_name(release.version, self.platform_tag)
        artifact = next((item for item in release.artifacts if item.name == wanted), None)
        if artifact is None:
            return None
        try:
            payload = await self._fetch_small(artifact, MAX_BUNDLE_METADATA_BYTES)
        except UpdateRefused as refusal:
            log.info(
                "update plan could not read the release's bundle metadata",
                extra={"update_reason": refusal.reason, "update_version": release.version},
            )
            return None
        metadata, reason = parse_bundle_metadata(payload)
        if metadata is None:
            log.warning(
                "the release's bundle metadata sidecar could not be read",
                extra={"update_reason": reason, "update_version": release.version},
            )
            return None
        return metadata.supervisor_protocol

    # -- step 2: the bytes, and the hash over them ----------------------------

    async def _acquire(self, artifact: Artifact) -> Path:
        """Return a path holding exactly the bytes the manifest hashed.

        Three states are distinguished on purpose, because they mean different
        things to whoever reads the log: a *truncated* transfer (the server said
        how many bytes it would send and sent fewer) is a network event and worth
        retrying, a *hash mismatch* over a complete body is a file that is not the
        one the manifest describes and is never retried into success, and an
        *oversized* body is neither - it is a refusal to keep writing.
        """
        directory = self.downloads_dir
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / artifact.name
        # An archive that already verified is reused: this is the resume worth
        # having, and it is the whole recovery path for "the daemon restarted
        # during a 400 MB download and the operator pressed it again" - and for
        # "the install asked for consent and the operator gave it".
        if final.is_file():
            self._record(PHASE_VERIFYING)
            if await asyncio.to_thread(file_digest, final) == artifact.sha256:
                log.info(
                    "reusing a previously verified update archive",
                    extra={"install_id": self._state.install_id, "update_archive": str(final)},
                )
                self._state.archive = str(final)
                self._state.bytes_downloaded = final.stat().st_size
                self._state.bytes_total = self._state.bytes_downloaded
                return final
            # A file under the artifact's own name whose digest is wrong is not
            # a partial download - it is a stale or tampered one, and keeping it
            # would make every future attempt fail the same way.
            with suppress(OSError):
                final.unlink()
        self._record(PHASE_DOWNLOADING)
        part = directory / f"{artifact.name}.part"
        with suppress(OSError):
            part.unlink(missing_ok=True)
        digest = hashlib.sha256()
        try:
            with part.open("wb") as handle:

                def write(chunk: bytes) -> None:
                    digest.update(chunk)
                    handle.write(chunk)
                    self._state.bytes_downloaded += len(chunk)

                outcome = await self._download(
                    artifact.url, write=write, max_bytes=MAX_ARTIFACT_BYTES, headers=None
                )
        except asyncio.CancelledError:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - a dropped transfer is ordinary
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise UpdateRefused(
                REASON_DOWNLOAD_FAILED,
                f"The download of {artifact.name} failed part-way "
                f"({type(exc).__name__}). Nothing was installed.",
            ) from exc
        self._state.bytes_total = outcome.declared_bytes or outcome.received_bytes
        if outcome.status != 200:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise UpdateRefused(
                REASON_UNREACHABLE,
                f"Downloading {artifact.name} answered HTTP {outcome.status}.",
            )
        if outcome.received_bytes > MAX_ARTIFACT_BYTES:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise UpdateRefused(
                REASON_OVERSIZED,
                f"{artifact.name} exceeded the {MAX_ARTIFACT_BYTES} byte ceiling, so "
                "the download was abandoned. This is not the artifact the manifest "
                "describes.",
            )
        if outcome.declared_bytes is not None and outcome.received_bytes != outcome.declared_bytes:
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise UpdateRefused(
                REASON_TRUNCATED,
                f"The download of {artifact.name} ended after "
                f"{outcome.received_bytes} of {outcome.declared_bytes} bytes. "
                "Nothing was staged; try again.",
            )
        self._record(PHASE_VERIFYING)
        actual = digest.hexdigest()
        if actual != artifact.sha256:
            # Deliberately deleted rather than kept for inspection. A file that
            # failed its hash is the one file in this flow that must not be
            # sitting on disk under a name the swap recognizes.
            with suppress(OSError):
                part.unlink(missing_ok=True)
            raise UpdateRefused(
                REASON_HASH_MISMATCH,
                f"{artifact.name} does not match the SHA-256 the manifest publishes "
                f"(expected {artifact.sha256[:16]}…, got {actual[:16]}…). Nothing was "
                "staged. This means the download was corrupted or the file is not "
                "the released one.",
            )
        # Only a verified file is given the artifact's real name. Everything
        # downstream keys off that name, so a `.part` can never be staged even
        # if this process dies in the next instruction.
        os.replace(part, final)
        self._state.archive = str(final)
        self._prune_downloads(keep=final)
        return final

    def _prune_downloads(self, *, keep: Path) -> None:
        """Keep the newest archives and remove the rest. Never raises."""
        try:
            archives = sorted(
                (path for path in self.downloads_dir.iterdir() if path.is_file()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for path in archives[KEEP_ARCHIVES:]:
            if path == keep:
                continue
            with suppress(OSError):
                path.unlink()

    # -- step 3: what the archive says it needs -------------------------------

    def _inspect(self, archive: Path) -> BundleMetadata:
        """Read the incoming bundle's own metadata, without extracting it.

        `bundle_archive` owns both the shape rules and the read, because the
        applier re-applies the identical rules in its own process when it
        extracts: a validation that lived here alone would be one the extractor
        does not have. What this method adds is the *consequence* - a refusal
        whose message says what the operator can do instead.
        """
        self._record(PHASE_INSPECTING)
        try:
            return read_archive_metadata(archive)
        except ArchiveError as exc:
            if exc.reason == REASON_BUNDLE_METADATA_MISSING:
                raise UpdateRefused(
                    exc.reason,
                    f"{exc.message} Installing it could reap every live session, so "
                    "this is a refusal rather than a guess; install it by hand if "
                    "you accept that.",
                ) from exc
            raise UpdateRefused(exc.reason, f"{exc.message} Nothing was staged.") from exc

    # -- step 4: the supervisor gate ------------------------------------------

    async def _supervisor_verdict(
        self, incoming: int | None, *, accepted: bool, known: bool | None = None
    ) -> SupervisorVerdict:
        """Which mode installing a release with protocol `incoming` needs.

        The gate that decides whether sessions survive. Swap mode needs a
        running supervisor, outside the app bundle, speaking the incoming
        protocol; anything else needs replace mode, which the operator has to
        consent to because it ends every session. `incoming` of None with
        `known` unset is the plan's "the sidecar was not published" case, and
        answers swap mode with `known: false` - the install will decide from the
        archive.
        """
        is_known = (incoming is not None) if known is None else known
        running, reason = await asyncio.to_thread(
            running_supervisor_protocol, Path(self._config.data_dir)
        )

        def verdict(refusal: str, message: str) -> SupervisorVerdict:
            # The mode names what installing this release *needs*, whether or
            # not consent has been given yet: a plan has to say "this one ends
            # your sessions" before the press, and the reason is what stands in
            # the way until the press carries consent.
            return SupervisorVerdict(
                mode=MODE_REPLACE,
                reason="" if accepted else refusal,
                message=message,
                running_protocol=running,
                incoming_protocol=incoming,
                known=is_known,
            )

        if running is None:
            if reason == REASON_NO_SUPERVISOR:
                return verdict(
                    REASON_NO_SUPERVISOR,
                    "No PTY supervisor is running for this install, so the app cannot "
                    "be replaced around your sessions: every in-process session would "
                    "end. Accepting installs the release anyway and ends them.",
                )
            return verdict(
                REASON_SUPERVISOR_UNKNOWN,
                "The running PTY supervisor's protocol could not be read, so this "
                "build cannot tell whether the update would preserve your sessions. "
                "Accepting installs the release with every session ended.",
            )
        inside = await asyncio.to_thread(self._supervisor_inside_bundle)
        if inside:
            return verdict(
                REASON_SUPERVISOR_IN_BUNDLE,
                "The PTY supervisor is running from inside the app bundle (the fallback "
                "for an install without the dedicated supervisor bundle), so replacing "
                "the app would end every live session. Accepting installs the release, "
                "ends every session, and puts the dedicated supervisor in place so "
                "later updates preserve them.",
            )
        if incoming is not None and incoming != running:
            return verdict(
                REASON_SUPERVISOR_UPDATE_REQUIRED,
                f"This release speaks PTY supervisor protocol {incoming} and the "
                f"supervisor running here speaks {running}. Installing it replaces "
                "the supervisor, which ends every live terminal session. Accepting "
                "installs it and ends them; declining keeps everything running until "
                "you choose a quieter moment.",
            )
        return SupervisorVerdict(
            mode=MODE_SWAP,
            reason="",
            message="Your live sessions are preserved: the PTY supervisor keeps them "
            "while the app restarts around them.",
            running_protocol=running,
            incoming_protocol=incoming,
            known=is_known,
        )

    def _supervisor_inside_bundle(self) -> bool:
        """Whether the running supervisor's image lives inside the app bundle."""
        bundle = self.install_kind.bundle_root
        if bundle is None:
            return False
        exe = (
            self._supervisor_exe()
            if self._supervisor_exe is not None
            else _live_supervisor_exe(Path(self._config.data_dir))
        )
        if exe is None:
            return False
        try:
            return exe.resolve().is_relative_to(bundle.resolve())
        except OSError:
            return False

    # -- step 5: the process that performs the swap ----------------------------

    async def _prepare_applier(self, archive: Path) -> Path:
        """Unpack the console client the swap runs from, outside every bundle.

        From the archive first, so the applier is the release being installed;
        from the installed client beside the app when the archive predates the
        client being carried; refused when neither exists. Off the event loop,
        because it is an extraction of ~100 files.
        """
        self._record(PHASE_PREPARING)
        directory = self.downloads_dir / f"{APPLIER_DIRNAME_PREFIX}{self._state.install_id}"
        try:
            applier = await asyncio.to_thread(
                prepare_applier, archive, directory, self.install_kind.layout
            )
        except ArchiveError as exc:
            raise UpdateRefused(exc.reason, f"{exc.message} Nothing was staged.") from exc
        except OSError as exc:
            raise UpdateRefused(
                REASON_NO_APPLIER,
                f"The update applier could not be unpacked into {directory} "
                f"({type(exc).__name__}: {exc}). Nothing was staged.",
            ) from exc
        if applier is None:
            raise UpdateRefused(
                REASON_NO_APPLIER,
                "Neither this release nor this install carries the console client "
                "(`swe-mux-cli`) that performs the swap from outside the app bundle, "
                "so nothing here can replace the running app. Install the release by "
                "hand: download it from the release page and replace the bundles, or "
                "run the installer.",
            )
        await asyncio.to_thread(prune_appliers, self.downloads_dir, keep=directory)
        self._state.applier = str(applier)
        return applier

    # -- step 6: the handoff --------------------------------------------------

    def _hand_off(
        self, archive: Path, release: Release, sha256: str, applier: Path, mode: str
    ) -> None:
        """Give the verified archive to the applier's staged swap.

        Everything past this line belongs to `bundle_apply.py` and is deliberately
        unchanged from a local redeploy: it stages, stops, swaps, health-checks,
        and rolls back to `<name>.prev` if the new bundle never turns healthy.
        This daemon is stopped by that process a minute or two from now, which
        is why the state written just below is durable - and why the clients are
        told now, while this daemon still has sockets to tell them on.
        """
        if self._handoff is not None:
            pid = self._handoff(archive, release.version, mode)
        else:
            pid = _spawn_from_archive(self._config, archive, sha256, applier, self, mode)
        self._state.bytes_total = self._state.bytes_total or archive.stat().st_size
        ends = (
            "Every live session ends while the PTY supervisor is replaced"
            if mode == MODE_REPLACE
            else "Sessions are preserved"
        )
        self._record(
            PHASE_HANDED_OFF,
            reason=REASON_OK,
            message=(
                f"swe-mux {release.version} was downloaded and verified, and the "
                f"staged swap is running (pid {pid}). {ends}; the app restarts when "
                "it completes."
            ),
        )
        if self.announce is not None:
            task = asyncio.ensure_future(self.announce(pid, mode))
            task.add_done_callback(_swallow_announce_failure)


def _swallow_announce_failure(task: asyncio.Future[Any]) -> None:
    """An announcement only buys the UI a progress chip; it is never load-bearing."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log.debug("update handoff announcement failed", extra={"error_type": type(error).__name__})


def running_supervisor_protocol(data_dir: Path) -> tuple[int | None, str]:
    """`(protocol, reason)` for the supervisor serving this data dir.

    Read from the discovery file the supervisor writes at start, which carries
    the protocol number alongside its pid and port. The file is the supervisor's
    own statement about itself, which is the right source: the alternative is
    inferring a protocol from a bundle's file hash, and that answer would be
    wrong on every machine that did not build it.
    """
    try:
        payload = json.loads(discovery_path(data_dir).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, REASON_NO_SUPERVISOR
    except (OSError, ValueError):
        return None, REASON_SUPERVISOR_UNKNOWN
    protocol = payload.get("protocol") if isinstance(payload, dict) else None
    if isinstance(protocol, bool) or not isinstance(protocol, int):
        # A supervisor from before the field existed, or a truncated write. Both
        # are "cannot tell", which is a refusal rather than a default.
        return None, REASON_SUPERVISOR_UNKNOWN
    return protocol, REASON_OK


def _live_supervisor_exe(data_dir: Path) -> Path | None:
    """The running supervisor's executable, from its discovery pid, or None."""
    import psutil

    try:
        payload = json.loads(discovery_path(data_dir).read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        return Path(psutil.Process(pid).exe())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def applier_executable_name() -> str:
    """The console client's launcher filename on this host."""
    return "swemux.exe" if os.name == "nt" else "swemux"


def prepare_applier(archive: Path, directory: Path, layout: Layout | None) -> Path | None:
    """Put a console client under `directory` and return its launcher, or None.

    Synchronous and deliberately free of daemon state, so the CLI and a test can
    call it. The archive's `swe-mux-cli/` wins; the installed client is the
    fallback for an archive that predates it; `None` means neither exists.
    """
    directory = Path(directory)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    if CLI_ROOT in archive_bundle_names(archive):
        extract_roots(archive, directory, (CLI_ROOT,))
        source = "the release archive"
    elif layout is not None and (layout.cli / applier_executable_name()).is_file():
        shutil.copytree(layout.cli, directory / CLI_ROOT)
        source = "the installed console client"
    else:
        shutil.rmtree(directory, ignore_errors=True)
        return None
    launcher = directory / CLI_ROOT / applier_executable_name()
    if not launcher.is_file():
        shutil.rmtree(directory, ignore_errors=True)
        raise ArchiveError(
            REASON_ARCHIVE_INVALID,
            f"{source} carries a {CLI_ROOT}/ with no {applier_executable_name()} in it.",
        )
    log.info(
        "update applier prepared",
        extra={"update_applier": str(launcher), "update_applier_source": source},
    )
    return launcher


def prune_appliers(downloads_dir: Path, *, keep: Path) -> None:
    """Remove applier copies from earlier attempts. Best effort, never raises.

    A finished applier on Windows may still hold its own image for a moment, and
    `ignore_errors` is what keeps that from failing the attempt that is running
    now; the leftover goes on the next attempt.
    """
    try:
        for path in downloads_dir.iterdir():
            if path.is_dir() and path.name.startswith(APPLIER_DIRNAME_PREFIX) and path != keep:
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        return


def _spawn_from_archive(
    config: Config,
    archive: Path,
    sha256: str,
    applier: Path,
    installer: UpdateInstaller,
    mode: str,
) -> int:
    """Start `swemux update-apply` from the applier copy, returning its pid."""
    layout = installer.install_kind.layout
    if layout is None:
        raise UpdateRefused(
            REASON_SOURCE_INSTALL,
            "This install names no bundle directory, so there is nothing to replace.",
        )
    try:
        lock_path = claim_redeploy_lock(config)
    except RedeployInFlight as exc:
        raise UpdateRefused(REASON_IN_PROGRESS, str(exc)) from exc
    process = spawn_applier(
        config,
        applier=applier,
        archive=archive,
        sha256=sha256,
        install_root=layout.install_root,
        mode=mode,
        lock_path=lock_path,
        log_path=Path(config.data_dir) / "redeploy.log",
    )
    return int(process.pid)


__all__ = [
    "ARCHIVE_ROOT",
    "CONSENT_REASONS",
    "CONSENT_SUPERVISOR_UPDATE",
    "INSTALL_FROZEN",
    "INSTALL_SOURCE",
    "MANAGED_CHECKOUT",
    "MANAGED_INSTALLER",
    "MANAGED_PORTABLE",
    "InstallKind",
    "SupervisorVerdict",
    "UpdateInstaller",
    "UpdateRefused",
    "applier_executable_name",
    "detect_install_kind",
    "prepare_applier",
    "prune_appliers",
    "release_archive_name",
    "release_bundle_metadata_name",
    "release_file_manifest_name",
    "release_installer_name",
    "release_platform_tag",
    "running_supervisor_protocol",
]
