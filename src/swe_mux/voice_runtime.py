"""The on-device speech *code* closure, as a first-use asset rather than a bundle.

ROADMAP Phase 21, Workstream D. Three stores in :mod:`swe_mux.voice_models`
already acquire the speech *weights* on an explicit press. This one acquires the
libraries that read them - spaCy, thinc, blis, onnxruntime, CTranslate2,
faster-whisper, tokenizers, misaki, num2words and their closure - because they
were 277 MB of the desktop bundle's 400 MB and both speech features are off by
default, so every new user downloaded and scanned them for a capability most
never enable.

It is the same mechanism :class:`swe_mux.voice_models.SpacyModelStore` already
uses, widened from one wheel to the closure: pinned URLs with pinned SHA-256s,
fetched on an explicit act, unpacked into one directory that goes on ``sys.path``
rather than into anybody's ``site-packages``. The pins are generated from
``uv.lock`` (:mod:`swe_mux.voice_wheels`), so the closure this downloads is the
closure this repository resolved, audited and locked.

Three properties are load-bearing and each has its own reason
-------------------------------------------------------------

**The closure is verified, not trusted.** Every wheel is checked against its
pinned size and SHA-256 while streaming, and a wheel that fails is deleted rather
than retried into service. A tampered index cannot reach ``sys.path`` through
this.

**Activation never touches the environment.** The unpacked tree is put on
``sys.path``, exactly as ``SpacyModelStore`` does and for the same reason: a
daemon has no business writing into the interpreter it was installed into. A
source checkout that already has ``--extra voice-local`` short-circuits before
anything is inspected, so the ordinary development case pays nothing and cannot
be perturbed by this module at all.

**The LGPL relink condition is proven where it now lives.** ``num2words`` is
LGPL-2.1 and ``misaki.en`` imports it at module scope. The desktop bundle used to
satisfy the relink condition by shipping it as readable source under
``_internal/num2words/``, which ``build_desktop.verify_bundle_licenses`` proved
against the built tree. That obligation does not vanish when the closure moves -
it changes shape. swe-mux no longer *distributes* num2words at all: the bytes
travel from PyPI to the user, and what this project ships is a URL and a hash.
What remains true, and is asserted here rather than assumed, is that the copy
which lands is readable ``.py`` source a recipient can replace
(:func:`_verify_relinkable`). Both halves are checked: the bundle must not carry
it (``build_desktop.verify_bundle_contents``) and the acquired tree must carry it
as source (here).

What this module deliberately does not do
-----------------------------------------
It does not resolve dependencies, and it must never learn to. The pin table is a
fixed list produced by ``uv``; there is no solver here, no index query, and no
"latest" anything. A closure that could change without a commit is a closure
nobody audited.
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
import time
import zipfile
from pathlib import Path
from typing import Any

import aiohttp

from .voice_models import ProgressCallback
from .voice_wheels import (
    CLOSURE_DIGEST,
    VoiceWheel,
    total_bytes,
    wheels_for_this_interpreter,
)

log = logging.getLogger(__name__)

#: Shared with `voice_models`, deliberately: these are the same four states the
#: weight stores answer with, and the settings panel renders them identically.
STATES = ("not_downloaded", "downloading", "ready", "error")

DOWNLOAD_CHUNK = 1 << 16
#: Generous, and it needs to be: this is ~82 MiB over a link that may be slow,
#: where `voice_models.DOWNLOAD_TIMEOUT_SECONDS` covers a single file.
DOWNLOAD_TIMEOUT_SECONDS = 3600.0

# The modules a working voice stack must be able to find. Probed with
# `find_spec`, which locates a top-level package without importing it - importing
# `spacy` costs a second and this question is asked on every status read.
#
# Every entry is a *top-level* name for that reason: `find_spec("a.b")` imports
# `a`, and a probe with an import in it is not a probe.
REQUIRED_MODULES = (
    "ctranslate2",
    "faster_whisper",
    "misaki",
    "num2words",
    "numpy",
    "onnxruntime",
    "spacy",
    "thinc",
    "tokenizers",
)

#: LGPL packages the acquired closure carries, which must land as replaceable
#: source. Kept in agreement with `license_audit.ALLOWLIST` and
#: `build_desktop.RELINKABLE_LGPL` by `tests/test_license_audit.py`.
RELINKABLE_LGPL = ("num2words",)


class VoiceRuntimeError(RuntimeError):
    """A refusal from this store, safe to show a user verbatim."""


def closure_importable() -> bool:
    """Whether every required module is already findable in this interpreter.

    True for a source checkout synced with ``--extra voice-local`` and for any
    environment that installed the extra, which is why this is checked first
    everywhere: the store is for installs that do not have the closure, and it
    must be inert for the ones that do.
    """
    for name in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                return False
        except (ImportError, ValueError):
            # A namespace package with a broken parent, or a `__spec__` that is
            # None on a partially-initialised module. Either way it is not usable.
            return False
    return True


class VoiceRuntimeStore:
    """``not_downloaded`` -> ``downloading`` -> ``ready``/``error`` for the closure.

    The state file under ``<data_dir>/voice-runtime`` is authoritative for
    "ready", and it records the :data:`swe_mux.voice_wheels.CLOSURE_DIGEST` that
    produced the tree. A digest mismatch reports ``not_downloaded`` rather than
    ``ready``: an app updated to a release with a different pinned closure must
    acquire that closure, not load the previous one and fail somewhere deeper.
    """

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "voice-runtime"
        #: The unpacked closure. Named for what it is - a directory that behaves
        #: like a `site-packages` - because that is exactly what goes on `sys.path`.
        self.site = self.root / "site"
        self._state_path = self.root / "state.json"
        self._task: asyncio.Task[None] | None = None
        self._progress: dict[str, Any] = {}
        self._selected: tuple[VoiceWheel, ...] | None = None
        self._selection_error: str | None = None

    # ---- the pin table -----------------------------------------------------

    def selection(self) -> tuple[VoiceWheel, ...]:
        """The wheels this interpreter can load, memoized.

        Memoized because the answer cannot change inside one process, and
        `packaging.tags.sys_tags()` walks the whole platform tag space.
        """
        if self._selected is None and self._selection_error is None:
            try:
                self._selected = wheels_for_this_interpreter()
            except LookupError as exc:
                self._selection_error = str(exc)
                log.warning("voice runtime unavailable on this interpreter: %s", exc)
        return self._selected or ()

    def supported(self) -> bool:
        """Whether the pinned closure covers this interpreter and platform."""
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
        if state.get("closure") != CLOSURE_DIGEST:
            return False
        return (self.site / "misaki" / "__init__.py").is_file()

    # ---- activation --------------------------------------------------------

    def activate(self) -> bool:
        """Make the closure importable in this interpreter; returns whether it is.

        Idempotent and cheap enough to call on every start, and called there
        rather than at first synthesis on purpose: putting a directory on
        ``sys.path`` from inside an audio callback would change import resolution
        under whatever else happened to be importing at that moment.
        """
        if closure_importable():
            return True
        if not self.unpacked():
            return False
        entry = str(self.site)
        if entry not in sys.path:
            sys.path.insert(0, entry)
            log.info("voice runtime activated from %s", entry)
        # `importlib.metadata` and the path finders both cache their view of
        # `sys.path`; a new entry added after the first lookup is invisible
        # without this. spaCy resolves its registry through entry points, so this
        # is not optional here the way it might look.
        importlib.invalidate_caches()
        return closure_importable()

    def ready(self) -> bool:
        return self.activate()

    def _source(self) -> str:
        """Which kind of present this is, read rather than remembered.

        ``installed`` means the environment already had the closure - a source
        checkout with the extra, or a bundle that still carried it. ``downloaded``
        means this store's directory is what is answering. Derived from
        ``sys.path`` rather than from a flag set when the download last ran,
        because a flag is a memory of one moment; `SpacyModelStore._source`
        carries the same reasoning and the bug that produced it.
        """
        return "downloaded" if str(self.site) in sys.path else "installed"

    # ---- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        selected = self.selection()
        total = total_bytes(selected)
        state = self._read_state()
        downloading = self._task is not None and not self._task.done()

        if self.activate():
            return {
                "status": "ready",
                "source": self._source(),
                "supported": True,
                "closure": CLOSURE_DIGEST,
                "distributions": len(selected),
                "total_bytes": total,
                "downloaded_bytes": total,
                "current_file": None,
                "error": None,
            }

        if not self.supported():
            # A platform the pin table does not cover. Reported as `error` rather
            # than `not_downloaded` because there is nothing to press: the remedy
            # is the install-time extra, and saying "not downloaded" would invite
            # a press that can only fail.
            return {
                "status": "error",
                "source": None,
                "supported": False,
                "closure": CLOSURE_DIGEST,
                "distributions": 0,
                "total_bytes": 0,
                "downloaded_bytes": 0,
                "current_file": None,
                "error": self._selection_error,
            }

        status = "downloading" if downloading else state.get("status", "not_downloaded")
        if status in {"downloading", "ready"} and not downloading:
            # Either a restart killed the task, or the state file says `ready`
            # while `activate` just said otherwise - a deleted or half-unpacked
            # tree, or a state file left by a different pinned closure. All of
            # them mean what is on disk cannot be loaded.
            if state.get("closure") not in (None, CLOSURE_DIGEST):
                status = "not_downloaded"
                state = {"status": "not_downloaded"}
            else:
                status = "error"
                state.setdefault(
                    "error", "the download was interrupted or the unpacked closure is gone"
                )
        return {
            "status": status,
            "source": None,
            "supported": True,
            "closure": CLOSURE_DIGEST,
            "distributions": len(selected),
            "total_bytes": total,
            "downloaded_bytes": int(self._progress.get("downloaded_bytes") or 0)
            if downloading
            else 0,
            "current_file": self._progress.get("current_file") if downloading else None,
            "error": None if status in {"downloading", "not_downloaded"} else state.get("error"),
        }

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
        self._write_state({"status": "downloading", "closure": CLOSURE_DIGEST})
        log.info(
            "voice runtime download starting closure=%s wheels=%d bytes=%d",
            CLOSURE_DIGEST[:12],
            len(self.selection()),
            total_bytes(self.selection()),
        )
        self._task = asyncio.create_task(self._download(progress), name="voice-runtime-download")
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _download(self, progress: ProgressCallback | None) -> None:
        started = time.monotonic()
        wheels = self.selection()
        cache = self.root / "wheels"
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS, connect=20)
        try:
            cache.mkdir(parents=True, exist_ok=True)
            downloaded = 0
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for wheel in wheels:
                    destination = cache / wheel.filename
                    self._progress["current_file"] = wheel.filename
                    if not _file_verified(destination, wheel.size, wheel.sha256):
                        await self._fetch_one(session, wheel, destination, downloaded)
                    downloaded += wheel.size
                    self._progress["downloaded_bytes"] = downloaded
                    if progress is not None:
                        await progress(self.status())
            self._progress["current_file"] = "unpacking"
            if progress is not None:
                await progress(self.status())
            await asyncio.to_thread(self._unpack, wheels, cache)
            # The cache is 82 MiB of wheels whose contents are now on disk twice.
            # Dropped once the tree is verified rather than kept for a re-unpack:
            # re-acquiring is a press, and a user who never enables voice again
            # should not be paying rent on a second copy.
            shutil.rmtree(cache, ignore_errors=True)
            self._write_state(
                {
                    "status": "ready",
                    "closure": CLOSURE_DIGEST,
                    "verified_at": time.time(),
                    "wheels": {wheel.filename: wheel.sha256 for wheel in wheels},
                }
            )
            self.activate()
            log.info(
                "voice runtime download complete closure=%s bytes=%d seconds=%.1f",
                CLOSURE_DIGEST[:12],
                total_bytes(wheels),
                time.monotonic() - started,
            )
        except asyncio.CancelledError:
            self._write_state(
                {
                    "status": "error",
                    "closure": CLOSURE_DIGEST,
                    "error": "the download was cancelled",
                }
            )
            raise
        except (VoiceRuntimeError, aiohttp.ClientError, OSError, TimeoutError) as exc:
            message = str(exc)[:400] or exc.__class__.__name__
            self._write_state(
                {"status": "error", "closure": CLOSURE_DIGEST, "error": message}
            )
            log.warning("voice runtime download failed: %s", message)
        finally:
            self._progress["current_file"] = None
            if progress is not None:
                await progress(self.status())

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        wheel: VoiceWheel,
        destination: Path,
        already: int,
    ) -> None:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        digest = hashlib.sha256()
        received = 0
        try:
            async with session.get(wheel.url, allow_redirects=True) as response:
                if response.status != 200:
                    raise VoiceRuntimeError(
                        f"the package index returned HTTP {response.status} for "
                        f"{wheel.filename}"
                    )
                with temporary.open("wb") as sink:
                    async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK):
                        sink.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if received > wheel.size:
                            raise VoiceRuntimeError(
                                f"{wheel.filename} exceeded its pinned size"
                            )
                        self._progress["downloaded_bytes"] = already + received
            if received != wheel.size or digest.hexdigest() != wheel.sha256:
                raise VoiceRuntimeError(
                    f"{wheel.filename} failed verification (got {received} bytes, "
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

    def _unpack(self, wheels: tuple[VoiceWheel, ...], cache: Path) -> None:
        """Build the tree beside the live one and swap, so a failure keeps the old.

        Runs on a thread: this is ~350 MB of extraction and it must not sit on the
        event loop while the daemon is serving terminals.
        """
        staging = self.root / "site.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for wheel in wheels:
            _extract_wheel(cache / wheel.filename, staging)
        _verify_relinkable(staging)
        missing = [
            name
            for name in REQUIRED_MODULES
            if not _package_present(staging, name)
        ]
        if missing:
            raise VoiceRuntimeError(
                "the acquired closure does not contain "
                + ", ".join(missing)
                + "; the pin table and the modules the voice features import have "
                "diverged (regenerate with packaging/generate_voice_pins.py)"
            )
        shutil.rmtree(self.site, ignore_errors=True)
        staging.replace(self.site)


def _package_present(site: Path, name: str) -> bool:
    return (site / name / "__init__.py").is_file() or (site / f"{name}.py").is_file()


def _verify_relinkable(site: Path) -> None:
    """Prove the LGPL relink condition on the tree that was just built.

    The desktop bundle used to carry this obligation and `build_desktop.
    verify_bundle_licenses` proved it there. The bundle no longer carries the
    package, so the proof moves to where the package now lands. It is a real check
    rather than a comment because the failure it guards is silent: a wheel that
    shipped only compiled artifacts would satisfy every other assertion here while
    leaving a recipient unable to substitute their own build.
    """
    missing = [
        name for name in RELINKABLE_LGPL if not sorted((site / name).glob("*.py"))
    ]
    if missing:
        raise VoiceRuntimeError(
            "LGPL relink regression: "
            + ", ".join(missing)
            + f" must land as readable source under {site} so a recipient can "
            "replace it, which is what THIRD-PARTY-NOTICES.md promises."
        )


def _extract_wheel(archive_path: Path, staging: Path) -> None:
    """Unpack one wheel into `staging`, promoting its `.data` payload.

    A wheel is a zip whose members are already laid out as they belong in a
    `site-packages`, with one exception: a `<name>-<version>.data/` directory
    holding `purelib`, `platlib`, `scripts`, `headers` and `data` subtrees that an
    installer redistributes. Only `purelib` and `platlib` belong on `sys.path`;
    the others are for an install this store is deliberately not performing, and
    `scripts` in particular would drop console-script launchers pointing at an
    interpreter that may not exist.
    """
    root = staging.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.namelist()
        for member in members:
            # The payload is hash-pinned, so this cannot currently be hostile. It
            # is checked anyway because the alternative is a rule that holds only
            # while the pin table is right, and an absolute or parent-relative
            # member is never legitimate in a wheel.
            if not (staging / member).resolve().is_relative_to(root):
                raise VoiceRuntimeError(
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


def _merge_tree(source: Path, destination: Path) -> None:
    """Move `source`'s contents into `destination`, merging existing directories.

    `Path.replace` on a directory fails when the target exists, and two wheels can
    legitimately contribute to one namespace (`google/protobuf`), so the merge is
    per-file rather than per-directory.
    """
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        target = destination / item.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        item.replace(target)


def _file_verified(path: Path, size: int, sha256: str) -> bool:
    """Whether a cached wheel is byte-for-byte the pinned one.

    Exists so an interrupted acquisition resumes at wheel granularity instead of
    re-downloading 82 MiB, and so a resumed one is verified rather than assumed.
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
