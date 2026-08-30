"""One pinned-closure acquisition store, parameterized by what it acquires.

Extracted from :mod:`swe_mux.voice_runtime` (ROADMAP Phase 21 Workstream D) when
ROADMAP Phase 24 needed the same mechanism for the desktop shell closure - and
extracted rather than copied, because the download/verify/unpack path is the
part whose properties are load-bearing and a second copy of it is a second
thing to audit. The three properties, unchanged from where they were proven:

- **The closure is verified, not trusted.** Every file is checked against its
  pinned size and SHA-256 while streaming; a failure is deleted, never retried
  into service.
- **Activation never touches the environment.** The unpacked tree goes on
  ``sys.path``; nothing is written into the interpreter's own site-packages,
  and an environment that already has the closure short-circuits untouched.
- **There is no solver.** The pin tables are generated from ``uv.lock``
  (:mod:`swe_mux.voice_wheels`, :mod:`swe_mux.desktop_wheels`); nothing here
  queries an index or resolves anything.

One capability was added for Phase 24, under one non-negotiable condition.
The desktop closure contains a distribution that publishes **no wheel**
(``proxy-tools``: sdist only, pure Python, imported unconditionally by
pywebview), so a spec may pin sdists beside its wheels - and **"unpack" means
extract, never build**. Installing an sdist normally runs its build backend,
which is arbitrary code execution at install time and would undo every
guarantee above; extracting a tarball executes nothing. So
:func:`_extract_sdist` copies the already-importable package source out of the
archive and *refuses* an sdist whose package would need building (compiled
sources, a missing plain package directory), loudly, rather than falling back
to anything clever. ``tests/test_wheel_closure.py`` pins the refusal.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import logging
import shutil
import sys
import tarfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import aiohttp

from .voice_models import ProgressCallback, report_progress

log = logging.getLogger(__name__)

#: Shared with `voice_models`, deliberately: these are the same four states the
#: weight stores answer with, and the settings panels render them identically.
STATES = ("not_downloaded", "downloading", "ready", "error")

DOWNLOAD_CHUNK = 1 << 16
#: Generous, and it needs to be: this covers a whole closure over a link that may
#: be slow, where `voice_models.DOWNLOAD_TIMEOUT_SECONDS` covers a single file.
DOWNLOAD_TIMEOUT_SECONDS = 3600.0

#: File suffixes inside an sdist's package directory that mean "this needs a
#: build step". Their presence is a refusal, never a fallback: the extract-only
#: rule is what keeps a pinned sdist as auditable as a pinned wheel.
_BUILD_REQUIRED_SUFFIXES = (".c", ".cc", ".cpp", ".pyx", ".pxd", ".h")


class ClosureAcquisitionError(RuntimeError):
    """A refusal from a closure store, safe to show a user verbatim."""


class PinnedFile(Protocol):
    """What a pin-table row must carry; wheels and sdists share the shape."""

    distribution: str
    version: str
    filename: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ClosureSpec:
    """Everything that differs between two acquired closures.

    ``select`` returns this interpreter's wheels and raises ``LookupError`` on a
    platform the table does not cover; ``sdists`` is the (usually empty) set of
    pinned no-wheel distributions extracted under the extract-never-build rule;
    ``importable`` answers "does this environment already have the closure",
    which is what keeps the store inert for installs that installed the extra.
    """

    label: str
    slug: str
    digest: str
    select: Callable[[], tuple[Any, ...]]
    required_modules: tuple[str, ...]
    regenerate_hint: str
    importable: Callable[[], bool]
    probe: tuple[str, ...]
    relinkable_lgpl: tuple[str, ...] = ()
    sdists: tuple[Any, ...] = field(default_factory=tuple)


class WheelClosureStore:
    """``not_downloaded`` -> ``downloading`` -> ``ready``/``error`` for one closure.

    The state file under ``<data_dir>/<slug>`` is authoritative for "ready", and
    it records the digest that produced the tree. A digest mismatch reports
    ``not_downloaded`` rather than ``ready``: an app updated to a release with a
    different pinned closure must acquire that closure, not load the previous
    one and fail somewhere deeper.
    """

    def __init__(self, data_dir: Path, spec: ClosureSpec) -> None:
        self.spec = spec
        self.root = data_dir / spec.slug
        #: The unpacked closure - a directory that behaves like a site-packages.
        self.site = self.root / "site"
        self._state_path = self.root / "state.json"
        self._task: asyncio.Task[None] | None = None
        self._progress: dict[str, Any] = {}
        self._selected: tuple[Any, ...] | None = None
        self._selection_error: str | None = None

    # ---- the pin table -----------------------------------------------------

    def selection(self) -> tuple[Any, ...]:
        """The files this interpreter can load, memoized (tags cannot change)."""
        if self._selected is None and self._selection_error is None:
            try:
                self._selected = (*self.spec.select(), *self.spec.sdists)
            except LookupError as exc:
                self._selection_error = str(exc)
                log.warning("%s unavailable on this interpreter: %s", self.spec.label, exc)
        return self._selected or ()

    def supported(self) -> bool:
        self.selection()
        return self._selection_error is None

    # ---- state -------------------------------------------------------------

    def _read_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"status": "not_downloaded"}
        if not isinstance(raw, dict) or raw.get("status") not in STATES:
            return {"status": "not_downloaded"}
        return raw

    def _write_state(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, indent=2, sort_keys=True)
        temporary = self._state_path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self._state_path)

    def unpacked(self) -> bool:
        """Whether a verified tree for *this* pinned closure is on disk."""
        state = self._read_state()
        if state.get("status") != "ready":
            return False
        if state.get("closure") != self.spec.digest:
            return False
        return self.site.joinpath(*self.spec.probe).is_file()

    # ---- activation --------------------------------------------------------

    def activate(self) -> bool:
        """Make the closure importable in this interpreter; returns whether it is.

        Idempotent and cheap enough to call on every start; putting a directory
        on ``sys.path`` mid-operation would change import resolution under
        whatever else happened to be importing at that moment.
        """
        if self.spec.importable():
            return True
        if not self.unpacked():
            return False
        entry = str(self.site)
        if entry not in sys.path:
            sys.path.insert(0, entry)
            log.info("%s activated from %s", self.spec.label, entry)
        # The path finders cache their view of `sys.path`; a new entry added
        # after the first lookup is invisible without this.
        importlib.invalidate_caches()
        return self.spec.importable()

    def ready(self) -> bool:
        """Whether the closure can be imported right now, activating if needed."""
        return self.activate()

    def _source(self) -> str:
        """``installed`` (the environment had it) or ``downloaded`` (this tree).

        Derived from ``sys.path`` rather than a remembered flag, because a flag
        is a memory of one moment (`SpacyModelStore._source` carries the bug
        that taught this).
        """
        return "downloaded" if str(self.site) in sys.path else "installed"

    # ---- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        selected = self.selection()
        total = sum(item.size for item in selected)
        state = self._read_state()
        downloading = self._task is not None and not self._task.done()

        if self.activate():
            return {
                "status": "ready",
                "source": self._source(),
                "supported": True,
                "closure": self.spec.digest,
                "distributions": len(selected),
                "total_bytes": total,
                "downloaded_bytes": total,
                "current_file": None,
                "error": None,
            }

        if not self.supported():
            # A platform the pin table does not cover. `error` rather than
            # `not_downloaded` because there is nothing to press: the remedy is
            # the install-time extra, and "not downloaded" invites a press that
            # can only fail.
            return {
                "status": "error",
                "source": None,
                "supported": False,
                "closure": self.spec.digest,
                "distributions": 0,
                "total_bytes": 0,
                "downloaded_bytes": 0,
                "current_file": None,
                "error": self._selection_error,
            }

        status = "downloading" if downloading else state.get("status", "not_downloaded")
        if status in {"downloading", "ready"} and not downloading:
            # Either a restart killed the task, or the state file says `ready`
            # while `activate` just said otherwise. Both mean what is on disk
            # cannot be loaded.
            if state.get("closure") not in (None, self.spec.digest):
                status = "not_downloaded"
                state = {"status": "not_downloaded"}
            else:
                status = "error"
                state.setdefault(
                    "error", self._interrupted_reason(str(state.get("status") or ""))
                )
        return {
            "status": status,
            "source": None,
            "supported": True,
            "closure": self.spec.digest,
            "distributions": len(selected),
            "total_bytes": total,
            "downloaded_bytes": int(self._progress.get("downloaded_bytes") or 0)
            if downloading
            else 0,
            "current_file": self._progress.get("current_file") if downloading else None,
            "error": None if status in {"downloading", "not_downloaded"} else state.get("error"),
        }

    def _interrupted_reason(self, recorded: str) -> str:
        """Why the state file and the world disagree, said specifically.

        The task object still knows what happened, so it is asked rather than
        guessed at; the history behind each branch is recorded on the voice
        store this was extracted from.
        """
        task = self._task
        if task is not None and task.cancelled():
            return "the download was cancelled"
        error = task.exception() if task is not None and task.done() else None
        if error is not None:
            return (
                "the acquisition failed unexpectedly "
                f"({error.__class__.__name__}: {str(error)[:200]}) - this is a "
                "defect rather than a network or disk problem"
            )
        if recorded == "ready":
            return "the unpacked closure is gone; press Download again"
        return (
            "the daemon restarted while the download was running; press Download "
            "again"
        )

    # ---- download ----------------------------------------------------------

    def start_download(self, progress: ProgressCallback | None = None) -> bool:
        """Begin the pinned acquisition; False when one is running or unneeded."""
        if self._task is not None and not self._task.done():
            return False
        if not self.supported():
            return False
        if self.activate():
            return False
        self._progress = {"downloaded_bytes": 0, "current_file": None}
        self._write_state({"status": "downloading", "closure": self.spec.digest})
        log.info(
            "%s download starting closure=%s files=%d bytes=%d",
            self.spec.label,
            self.spec.digest[:12],
            len(self.selection()),
            sum(item.size for item in self.selection()),
        )
        self._task = asyncio.create_task(
            self._download(progress), name=f"{self.spec.slug}-download"
        )
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _download(self, progress: ProgressCallback | None) -> None:
        started = time.monotonic()
        files = self.selection()
        cache = self.root / "wheels"
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS, connect=20)
        try:
            cache.mkdir(parents=True, exist_ok=True)
            downloaded = 0
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for item in files:
                    destination = cache / item.filename
                    self._progress["current_file"] = item.filename
                    if not _file_verified(destination, item.size, item.sha256):
                        await self._fetch_one(session, item, destination, downloaded)
                    downloaded += item.size
                    self._progress["downloaded_bytes"] = downloaded
                    await report_progress(self, progress)
            self._progress["current_file"] = "unpacking"
            await report_progress(self, progress)
            await asyncio.to_thread(self._unpack, files, cache)
            # The cache is the whole closure a second time; dropped once the
            # tree is verified, because re-acquiring is a press.
            shutil.rmtree(cache, ignore_errors=True)
            self._write_state(
                {
                    "status": "ready",
                    "closure": self.spec.digest,
                    "verified_at": time.time(),
                    "wheels": {item.filename: item.sha256 for item in files},
                }
            )
            self.activate()
            log.info(
                "%s download complete closure=%s bytes=%d seconds=%.1f",
                self.spec.label,
                self.spec.digest[:12],
                sum(item.size for item in files),
                time.monotonic() - started,
            )
        except asyncio.CancelledError:
            self._write_state(
                {
                    "status": "error",
                    "closure": self.spec.digest,
                    "error": "the download was cancelled",
                }
            )
            raise
        except (ClosureAcquisitionError, aiohttp.ClientError, OSError, TimeoutError) as exc:
            message = str(exc)[:400] or exc.__class__.__name__
            self._write_state(
                {"status": "error", "closure": self.spec.digest, "error": message}
            )
            log.warning("%s download failed: %s", self.spec.label, message)
        except Exception as exc:  # noqa: BLE001 - a defect here must not read as a transfer failure
            # The clause above names what goes wrong when fetching a closure over
            # a network onto a disk. Anything else is a defect in this process,
            # and reporting it as an interrupted transfer sent two investigators
            # at the wrong subsystems once (voice_runtime, 2026-08-29).
            message = f"{exc.__class__.__name__}: {str(exc)[:300]}"
            self._write_state(
                {
                    "status": "error",
                    "closure": self.spec.digest,
                    "error": f"the acquisition failed unexpectedly ({message})",
                    "crashed": True,
                }
            )
            log.exception("%s acquisition crashed", self.spec.label)
        finally:
            self._progress["current_file"] = None
            await report_progress(self, progress)

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        item: PinnedFile,
        destination: Path,
        already: int,
    ) -> None:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        digest = hashlib.sha256()
        received = 0
        try:
            async with session.get(item.url, allow_redirects=True) as response:
                if response.status != 200:
                    raise ClosureAcquisitionError(
                        f"the package index returned HTTP {response.status} for "
                        f"{item.filename}"
                    )
                with temporary.open("wb") as sink:
                    async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK):
                        sink.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if received > item.size:
                            raise ClosureAcquisitionError(
                                f"{item.filename} exceeded its pinned size"
                            )
                        self._progress["downloaded_bytes"] = already + received
            if received != item.size or digest.hexdigest() != item.sha256:
                raise ClosureAcquisitionError(
                    f"{item.filename} failed verification (got {received} bytes, "
                    f"sha256 {digest.hexdigest()[:16]}...); the pinned release may "
                    "have been tampered with or the download was corrupted"
                )
            temporary.replace(destination)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # ---- unpacking ---------------------------------------------------------

    def _unpack(self, files: tuple[Any, ...], cache: Path) -> None:
        """Build the tree beside the live one and swap, so a failure keeps the old.

        Runs on a thread: extraction must not sit on the event loop while the
        daemon is serving terminals.
        """
        sdist_names = {item.filename for item in self.spec.sdists}
        staging = self.root / "site.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for item in files:
            if item.filename in sdist_names:
                _extract_sdist(cache / item.filename, staging, item.distribution)
            else:
                _extract_wheel(cache / item.filename, staging)
        verify_relinkable(staging, self.spec.relinkable_lgpl)
        missing = [
            name
            for name in self.spec.required_modules
            if not _package_present(staging, name)
        ]
        if missing:
            raise ClosureAcquisitionError(
                "the acquired closure does not contain "
                + ", ".join(missing)
                + "; the pin table and the modules this feature imports have "
                f"diverged (regenerate with {self.spec.regenerate_hint})"
            )
        shutil.rmtree(self.site, ignore_errors=True)
        staging.replace(self.site)


def _package_present(site: Path, name: str) -> bool:
    return (site / name / "__init__.py").is_file() or (site / f"{name}.py").is_file()


def verify_relinkable(site: Path, names: tuple[str, ...]) -> None:
    """Prove the LGPL relink condition on the tree that was just built.

    A real check rather than a comment because the failure it guards is silent:
    a payload carrying only compiled artifacts would satisfy every other
    assertion while leaving a recipient unable to substitute their own build,
    which is what THIRD-PARTY-NOTICES.md promises they can do.
    """
    missing = [name for name in names if not sorted((site / name).glob("*.py"))]
    if missing:
        raise ClosureAcquisitionError(
            "LGPL relink regression: "
            + ", ".join(missing)
            + f" must land as readable source under {site} so a recipient can "
            "replace it, which is what THIRD-PARTY-NOTICES.md promises."
        )


def _extract_wheel(archive_path: Path, staging: Path) -> None:
    """Unpack one wheel into `staging`, promoting its `.data` payload.

    A wheel is a zip whose members are already laid out as they belong in a
    `site-packages`, with one exception: a `<name>-<version>.data/` directory
    holding `purelib`, `platlib`, `scripts`, `headers` and `data` subtrees. Only
    `purelib` and `platlib` belong on `sys.path`; `scripts` in particular would
    drop console-script launchers pointing at an interpreter that may not exist.
    """
    root = staging.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.namelist()
        for member in members:
            # The payload is hash-pinned, so this cannot currently be hostile.
            # Checked anyway: an absolute or parent-relative member is never
            # legitimate in a wheel.
            if not (staging / member).resolve().is_relative_to(root):
                raise ClosureAcquisitionError(
                    f"{archive_path.name} contains an out-of-tree path "
                    f"({member!r}); refusing to unpack it"
                )
        archive.extractall(staging)
    for entry in list(staging.iterdir()):
        if not entry.is_dir() or not entry.name.endswith(".data"):
            continue
        for relocatable in ("purelib", "platlib"):
            source = entry / relocatable
            if source.is_dir():
                _merge_tree(source, staging)
        shutil.rmtree(entry, ignore_errors=True)


def _extract_sdist(archive_path: Path, staging: Path, distribution: str) -> None:
    """Extract - never build - one pinned sdist's package source into `staging`.

    The condition that makes a pinned sdist as auditable as a pinned wheel:
    nothing from the archive is executed. The build backend, `setup.py`, and
    everything else in the sdist are ignored; what is taken is exactly the
    already-importable package - `<root>/<import_name>/` with an `__init__.py`,
    or a single `<root>/<import_name>.py` module - copied file by file.

    Anything that would need a build step is a refusal, not a fallback:
    compiled sources inside the package, or a layout with no plain package to
    take. A future pin that quietly grows a build requirement must fail loudly
    here rather than be handled cleverly.
    """
    import_name = distribution.replace("-", "_")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        package_prefixes = {
            member.name.split("/", 1)[0] for member in members if "/" in member.name
        }
        if len(package_prefixes) != 1:
            raise ClosureAcquisitionError(
                f"{archive_path.name} does not have the single-root layout an "
                "sdist promises; refusing to extract it"
            )
        root = package_prefixes.pop()
        package_dir = f"{root}/{import_name}/"
        module_file = f"{root}/{import_name}.py"
        wanted = [
            member
            for member in members
            if member.isfile()
            and (member.name.startswith(package_dir) or member.name == module_file)
        ]
        if not any(
            member.name == f"{package_dir}__init__.py" or member.name == module_file
            for member in wanted
        ):
            raise ClosureAcquisitionError(
                f"{archive_path.name} carries no plain `{import_name}` package to "
                "extract; an sdist that needs building is refused - pin a wheel "
                "for this distribution or drop it from the closure"
            )
        built = [
            member.name
            for member in wanted
            if member.name.endswith(_BUILD_REQUIRED_SUFFIXES)
        ]
        if built:
            raise ClosureAcquisitionError(
                f"{archive_path.name} contains sources that need building "
                f"({', '.join(built[:5])}); extract-never-build is the condition "
                "that makes a pinned sdist acceptable, so this one is refused"
            )
        for member in wanted:
            relative = member.name[len(root) + 1 :]
            target = (staging / relative).resolve()
            if not target.is_relative_to(staging.resolve()):
                raise ClosureAcquisitionError(
                    f"{archive_path.name} contains an out-of-tree path "
                    f"({member.name!r}); refusing to unpack it"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ClosureAcquisitionError(
                    f"{archive_path.name}: {member.name!r} could not be read"
                )
            with source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)


def _merge_tree(source: Path, destination: Path) -> None:
    """Move `source`'s contents into `destination`, merging existing directories.

    `Path.replace` on a directory fails when the target exists, and two wheels
    can legitimately contribute to one namespace, so the merge is per-file.
    """
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        item.replace(target)


def _file_verified(path: Path, size: int, sha256: str) -> bool:
    """Whether a cached archive is byte-for-byte the pinned one.

    Exists so an interrupted acquisition resumes at file granularity, verified
    rather than assumed.
    """
    try:
        if path.stat().st_size != size:
            return False
    except OSError:
        return False
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            # unsupervised-loop-ok: bounded synchronous file read
            while True:
                chunk = source.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == sha256
