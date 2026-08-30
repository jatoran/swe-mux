"""On-demand acquisition of the local speech models, and the state machine over it.

Three stores live here and all three answer the same four-state question -
``not_downloaded`` to ``downloading`` to ``ready``, plus ``error`` - because a
first-use download that a fresh install cannot see coming is the failure this
module exists to remove. No store ever downloads without an explicit act.

- :class:`KokoroModelStore` (TTS) is **pinned**: an immutable repository revision
  and a per-file SHA-256, checked while streaming, whose error state can never be
  loaded. A partial or tampered file is deleted and reported, not retried into
  service. It hand-rolls the transfer, which is why it can report bytes.
- :class:`WhisperModelStore` (STT) wraps ``faster_whisper``'s own resolver over
  the Hugging Face cache, so the cache is authoritative for "ready" and there is
  no second state file to drift from it. It reports **no** byte progress, because
  ``faster_whisper.download_model`` disables the hub's progress hook and there is
  nothing to observe: a percentage derived from an expected total would be an
  estimate presented as a reading, and a wrong number is acted on where an absent
  one is not.
- :class:`SpacyModelStore` (the Kokoro G2P's spaCy model) is pinned the same way
  as Kokoro's, and is the newest of the three because it used to be a *declared
  dependency* rather than an asset. That declaration was unresolvable for every
  downstream install (`.docs/development/DEPENDENCY_AUDIT_2026-08-28.md` § 4), so
  the model moved here, where the two speech models it serves already lived.

The fourth first-use asset, the browser-side Silero VAD, is **not** here and does
not download: its ~11 MB WASM runtime and ~2.3 MB ONNX model are emitted into the
frontend bundle by Vite and served same-origin by this daemon, so a fresh install
already has them (`design/features/voice.md`).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import io
import json
import logging
import shutil
import sys
import time
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from .tls_trust import trusting_connector

log = logging.getLogger(__name__)

KOKORO_REPO = "onnx-community/Kokoro-82M-v1.0-ONNX"
KOKORO_REVISION = "1939ad2a8e416c0acfeecc08a694d14ef25f2231"
HUGGINGFACE_ORIGIN = "https://huggingface.co"
DOWNLOAD_CHUNK = 1 << 16
DOWNLOAD_TIMEOUT_SECONDS = 900.0

# Voice ids the English-only misaki G2P can drive. Non-English voices in the
# repository are deliberately absent: offering them would pair a voice with a
# phonemizer that cannot feed it.
ENGLISH_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
)

# path -> (bytes, sha256), read from the Hugging Face tree API for the pinned
# revision on 2026-08-18. The hash is what makes the pin real: a re-tagged
# revision or a truncated proxy response fails verification instead of loading.
KOKORO_FILES: dict[str, tuple[int, str]] = {
    "onnx/model_quantized.onnx": (
        92361116, "fbae9257e1e05ffc727e951ef9b9c98418e6d79f1c9b6b13bd59f5c9028a1478"
    ),
    "tokenizer.json": (
        3497, "77a02c8e164413299b4b4c403b14f8e0e1c1b727db4d46a09d6327b861060a34"
    ),
    "config.json": (
        44, "df34b4f930b23447cd4dc410fabfb42eb3f24e803e6c3f97d618fb359380a36f"
    ),
}

# Every English voice vector is 522,240 bytes; only the hash varies.
_VOICE_SHAS: dict[str, str] = {
    "af_alloy": "c4a6b876047fd7fb472edf4ebd63cfac7c3b958a7cae7c106e8f038ca6308c45",
    "af_aoede": "4a004c33430762e2461eedb2013fad808ef4ab3121f5300f554476caf58d8361",
    "af_bella": "f69d836209b78eb8c66e75e3cda491e26ea838a3674257e9d4e5703cbaf55c8b",
    "af_heart": "d583ccff3cdca2f7fae535cb998ac07e9fcb90f09737b9a41fa2734ec44a8f0b",
    "af_jessica": "a240a5e3c15b43563d6e923bdca8ef5613a23471d9b77653694012435df23bd8",
    "af_kore": "9be5221b6a941c04b561959b8ff0b06e809444dcc4ab7e75a7b23606f691819e",
    "af_nicole": "cd2191ab31b914ed7b318416b0e4440fdf392ddad9106a060819aa600a64f59a",
    "af_nova": "18778272caa0d0eebaea251c35fd635f038434f9eee5e691d02a174bd328414f",
    "af_river": "00a2bcf82b1d86e8f19902ede58c65ccf6c0e43b44b7d74fad54e5d8933c9c30",
    "af_sarah": "4409fbc125afabacc615d94db5398d847006a737b0247d6892b7a9a0007a2f0a",
    "af_sky": "4435255c9744f3f31659e0d714ab7689bf65d9e77ec1cce060f083912614f0b9",
    "am_adam": "162b035ed91cfc48b6046982184c645f72edcdd1b82843347f605d7bf7b15716",
    "am_echo": "3968b92c3c4cd1c4416dbded36c13eaa388a90d5788d02a13e4d781f5f8cf3c3",
    "am_eric": "e8b5be17edd1e3636901ce7598baafe2dc8dd8ff707a0c23bf9e461add7e2832",
    "am_fenrir": "c27989f741f7ee34d273a39d8a595cc0837d35f5ced9a29b7cc162614616df43",
    "am_liam": "52403be32fd047c6a44517cb0bcd6b134f2a18baa73e70ef41651e0eab921ade",
    "am_michael": "1d1f21dd8da39c30705cd4c75d039d265e9bc4a2a93ed09bc9e1b1225eb95ba1",
    "am_onyx": "da5d135b424164916d75a68ffb4c2abce3d7d5ccc82dd1ee6cf447ce286145e6",
    "am_puck": "fcf73c989033e9233e0b98713eca600c8c74dcc1614b37009d5450ff4a2274a0",
    "am_santa": "61150cf726ab6c5ed7a99f90a304f91f5a72c00c592e89ec94e5df11c319227a",
    "bf_alice": "08afa6ba24da61ea5e8efa139e5aadc938d83f0a6da5a900adaf763ac1da5573",
    "bf_emma": "669fe0647f9dd04fcab92f1439a40eeb4c8b4ab1f82e4996fe3d918ce4a63b73",
    "bf_isabella": "3754352c4aaa46d17f27654ab7518d65b62ad6163a0f55a5f4330c2da2c4e94f",
    "bf_lily": "5e0ee32ebe64a467124976b14e69590746f1c4ce41a12b587a50c862edfea335",
    "bm_daniel": "6b3194bbceffb746733cbc22c8f593dd44e401a71d53895a2dca891bc595a1e8",
    "bm_fable": "f889083196807b4adb15e9204252165f503b8d33d3982e681c52443c49d798f1",
    "bm_george": "c4b235a4c1f2cd3b939fed08b899ce9385638b763f7b73a59616c4fc9bd6c9bc",
    "bm_lewis": "b8f671cef828c30e66fdf0b0756a76bba58f6bb3398cbbf27058642acbcedb97",
}
KOKORO_FILES.update(
    {f"voices/{name}.bin": (522240, sha) for name, sha in _VOICE_SHAS.items()}
)

# The spaCy English model misaki's G2P resolves through `spacy.load`, pinned to
# the same release `[tool.uv.sources]` and `uv.lock` name so a source checkout, a
# desktop bundle, and a downloading install all end up with byte-identical bytes.
# The hash is `uv.lock`'s own, which is what makes the pin real.
G2P_DISTRIBUTION = "en_core_web_sm"
G2P_VERSION = "3.8.0"
G2P_WHEEL_URL = (
    "https://github.com/explosion/spacy-models/releases/download/"
    f"{G2P_DISTRIBUTION}-{G2P_VERSION}/{G2P_DISTRIBUTION}-{G2P_VERSION}-py3-none-any.whl"
)
G2P_WHEEL_SHA256 = "1932429db727d4bff3deed6b34cfc05df17794f4a52eeb26cf8928f7c1a0fb85"
#: Measured from the release asset's `Content-Length` on 2026-08-28.
G2P_WHEEL_BYTES = 12806118

STATES = ("not_downloaded", "downloading", "ready", "error")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def report_progress(
    store: Any, progress: ProgressCallback | None, *args: Any
) -> None:
    """Publish a store's current state, and never fail the download by doing it.

    Every store here calls this from its `finally` and from its progress points.
    One function rather than a method on each, because the property it enforces is
    the same for all of them and a fourth copy is a fourth chance to omit the
    `try`.

    A failure to *report* is not a failure to *acquire*. That distinction was not
    made until 2026-08-29, when an unguarded `await progress(...)` in a `finally`
    let a `TypeError` in the callback escape the download task - so the store's
    own crash handler wrote a correct diagnosis and the `finally` immediately
    threw it away by raising the same exception on the way out. The operator was
    then told his download had been interrupted, and went looking at a disk with
    436 GB free.

    `CancelledError` is re-raised: a cancelled task must stay cancelled, and it is
    not an observer defect.
    """
    if progress is None:
        return
    try:
        await progress(store.status(*args))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - reporting must not fail acquiring
        log.exception("%s progress callback failed", type(store).__name__)


class VoiceModelError(RuntimeError):
    """A typed acquisition failure; the caller surfaces it, nothing loads."""


@dataclass(frozen=True)
class KokoroInstall:
    root: Path

    @property
    def model(self) -> Path:
        return self.root / "onnx" / "model_quantized.onnx"

    @property
    def tokenizer(self) -> Path:
        return self.root / "tokenizer.json"

    @property
    def voices_dir(self) -> Path:
        return self.root / "voices"


class KokoroModelStore:
    """State machine over the Kokoro files under ``<data_dir>/voice-models/kokoro``.

    The state file is authoritative for "ready": files on disk without a state
    file that says ``ready`` (written only after every hash verified) are
    treated as a partial download and never loaded.
    """

    def __init__(self, data_dir: Path) -> None:
        self.install = KokoroInstall(data_dir / "voice-models" / "kokoro")
        self._state_path = self.install.root / "state.json"
        self._task: asyncio.Task[None] | None = None
        self._progress: dict[str, Any] = {}

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
        self.install.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state, indent=2, sort_keys=True)
        temporary = self._state_path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self._state_path)

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        downloading = self._task is not None and not self._task.done()
        status = "downloading" if downloading else state.get("status", "not_downloaded")
        if status == "downloading" and not downloading:
            # A daemon restart killed the task; what is on disk is partial.
            status = "error"
            state.setdefault("error", "the download was interrupted by a restart")
        total = sum(size for size, _sha in KOKORO_FILES.values())
        return {
            "status": status,
            "revision": KOKORO_REVISION,
            "repo": KOKORO_REPO,
            "total_bytes": total,
            "downloaded_bytes": int(self._progress.get("downloaded_bytes") or 0)
            if downloading
            else (total if status == "ready" else 0),
            "current_file": self._progress.get("current_file") if downloading else None,
            "error": None if status in {"ready", "downloading"} else state.get("error"),
            "voices": list(ENGLISH_VOICES),
        }

    def ready(self) -> bool:
        state = self._read_state()
        if state.get("status") != "ready" or state.get("revision") != KOKORO_REVISION:
            return False
        # Cheap size check on the two load-bearing files; the full hash was
        # verified at download time and the state file is the record of that.
        for relative in ("onnx/model_quantized.onnx", "tokenizer.json"):
            path = self.install.root / relative
            expected, _sha = KOKORO_FILES[relative]
            try:
                if path.stat().st_size != expected:
                    return False
            except OSError:
                return False
        return True

    # ---- download ----------------------------------------------------------

    def start_download(self, progress: ProgressCallback | None = None) -> bool:
        """Begin the pinned download; returns False when one is already running."""
        if self._task is not None and not self._task.done():
            return False
        self._progress = {"downloaded_bytes": 0, "current_file": None}
        self._write_state({"status": "downloading", "revision": KOKORO_REVISION})
        self._task = asyncio.create_task(self._download(progress), name="kokoro-download")
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _download(self, progress: ProgressCallback | None) -> None:
        started = time.monotonic()
        downloaded = 0
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS, connect=20)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, connector=trusting_connector()
            ) as session:
                for relative, (size, sha256) in KOKORO_FILES.items():
                    destination = self.install.root / relative
                    if self._file_verified(destination, size, sha256):
                        downloaded += size
                        continue
                    self._progress["current_file"] = relative
                    await self._fetch_one(session, relative, destination, size, sha256)
                    downloaded += size
                    self._progress["downloaded_bytes"] = downloaded
                    await report_progress(self, progress)
            self._write_state(
                {
                    "status": "ready",
                    "revision": KOKORO_REVISION,
                    "verified_at": time.time(),
                    "files": {path: sha for path, (_size, sha) in KOKORO_FILES.items()},
                }
            )
            log.info(
                "kokoro model download complete bytes=%d seconds=%.1f",
                downloaded,
                time.monotonic() - started,
            )
        except asyncio.CancelledError:
            self._write_state(
                {
                    "status": "error",
                    "revision": KOKORO_REVISION,
                    "error": "the download was cancelled",
                }
            )
            raise
        except (VoiceModelError, aiohttp.ClientError, OSError, TimeoutError) as exc:
            message = str(exc)[:400] or exc.__class__.__name__
            self._write_state(
                {"status": "error", "revision": KOKORO_REVISION, "error": message}
            )
            log.warning("kokoro model download failed: %s", message)
        except Exception as exc:  # noqa: BLE001 - a defect must not read as a transfer failure
            # The clause above names what goes wrong fetching bytes over a network
            # onto a disk. Anything else here is a defect in this process, and
            # reporting it in the same words sends the reader to the wrong
            # subsystem - which is exactly what happened to the sibling store on
            # 2026-08-29, when a `TypeError` in a progress callback was reported
            # as an interrupted download and two people went looking at disk and
            # network. `log.exception` is what makes the traceback findable.
            self._write_state(
                {
                    "status": "error",
                    "revision": KOKORO_REVISION,
                    "error": (
                        "the download failed unexpectedly "
                        f"({exc.__class__.__name__}: {str(exc)[:200]}) - this is a "
                        "defect rather than a network or disk problem"
                    ),
                }
            )
            log.exception("kokoro model download crashed")
        finally:
            await report_progress(self, progress)

    @staticmethod
    def _file_verified(path: Path, size: int, sha256: str) -> bool:
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

    async def _fetch_one(
        self,
        session: aiohttp.ClientSession,
        relative: str,
        destination: Path,
        size: int,
        sha256: str,
    ) -> None:
        url = f"{HUGGINGFACE_ORIGIN}/{KOKORO_REPO}/resolve/{KOKORO_REVISION}/{relative}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".partial")
        digest = hashlib.sha256()
        received = 0
        base = int(self._progress.get("downloaded_bytes") or 0)
        try:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    raise VoiceModelError(
                        f"Hugging Face returned HTTP {response.status} for {relative}"
                    )
                with temporary.open("wb") as sink:
                    async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK):
                        sink.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if received > size:
                            raise VoiceModelError(f"{relative} exceeded its pinned size")
                        self._progress["downloaded_bytes"] = base + received
            if received != size or digest.hexdigest() != sha256:
                raise VoiceModelError(
                    f"{relative} failed verification (got {received} bytes); "
                    "the pinned revision may have been tampered with or the "
                    "download was corrupted"
                )
            temporary.replace(destination)
        except BaseException:
            # A partial file must never be mistakable for the real one.
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise


# Approximate download sizes for the CTranslate2 conversions `faster_whisper`
# pulls, in megabytes, so an operator knows what pressing Download costs before
# they press it. Approximate and labelled as such: the exact byte count is not
# known until the hub answers, and a fabricated exact figure would be worse than
# a rounded honest one. An unlisted name - a bare Hugging Face repository id or a
# local directory, both of which `download_model` accepts - reports no size at
# all rather than a guessed one.
WHISPER_APPROXIMATE_MB: dict[str, int] = {
    "tiny": 75, "tiny.en": 75,
    "base": 145, "base.en": 145,
    "small": 484, "small.en": 484,
    "medium": 1530, "medium.en": 1530,
    "large": 3090, "large-v1": 3090, "large-v2": 3090, "large-v3": 3090,
    "turbo": 1620, "large-v3-turbo": 1620,
    "distil-small.en": 332, "distil-medium.en": 789,
    "distil-large-v2": 1510, "distil-large-v3": 1510,
}


def whisper_size_hint(name: str) -> str | None:
    """Human phrasing of the approximate download, or None when it is unknown."""
    megabytes = WHISPER_APPROXIMATE_MB.get(name.strip())
    if megabytes is None:
        return None
    if megabytes >= 1024:
        return f"about {megabytes / 1024:.1f} GB"
    return f"about {megabytes} MB"


class WhisperModelStore:
    """``not_downloaded`` → ``downloading`` → ``ready``/``error`` for Whisper weights.

    The gap this closes: ``WhisperModel(name)`` fetches the weights from Hugging
    Face on first use, *inside* the transcription path, in a worker thread, with
    no surface anywhere. On a fresh install the first press of Talk was therefore
    a silent multi-gigabyte download that presented as one very slow
    transcription. Here the absence is a reported state and the fetch is a
    separate, explicit act.

    ``ready`` is answered by ``faster_whisper``'s own resolver under
    ``local_files_only`` rather than by a state file of our own, for two reasons:
    the hub writes atomically (blobs plus ``.incomplete`` temporaries, so a
    partial download never resolves), and that resolver already understands every
    form the setting accepts - a size alias, a bare repository id, or a local
    directory. Re-deriving the mapping here would be a second copy that drifts.

    Probes are memoized because the answer only changes when this process
    downloads something or an operator installs weights by hand; ``forget``
    exists for the latter and the download path clears its own entry.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}
        self._started: dict[str, float] = {}
        self._cached: dict[str, str | None] = {}
        self._backend: bool | None = None

    # ---- probing -----------------------------------------------------------

    def backend_installed(self) -> bool:
        """Whether ``faster_whisper`` imports at all - the ``voice-local`` extra."""
        if self._backend is None:
            self._backend = self._import_backend()
        return self._backend

    @staticmethod
    def _import_backend() -> bool:
        from . import av_stub

        # Before the import, never after: `faster_whisper.audio` executes a
        # module-level `import av`, and PyAV is deliberately absent from the
        # resolved closure (GPL FFmpeg linkage). See `swe_mux.av_stub`.
        av_stub.install()
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def forget_backend(self) -> None:
        """Re-ask whether ``faster_whisper`` imports.

        The memo above is right for the whole life of a process in every case but
        one: `voice_runtime.VoiceRuntimeStore` can put the closure on `sys.path`
        *after* this store has already answered "no". Nothing else re-asks, so
        without this an install that just acquired the libraries keeps reporting a
        missing backend until it is restarted.
        """
        self._backend = None

    def local_path(self, name: str) -> str | None:
        """Where these weights already are on this machine, or None if nowhere.

        Never reaches the network: ``local_files_only`` is exactly what makes this
        a probe rather than the download it exists to make explicit.
        """
        name = name.strip()
        if not name:
            return None
        if name not in self._cached:
            self._cached[name] = self._resolve_local(name)
        return self._cached[name]

    @staticmethod
    def _resolve_local(name: str) -> str | None:
        from . import av_stub

        av_stub.install()
        try:
            from faster_whisper.utils import download_model
        except ImportError:
            return None
        try:
            return str(download_model(name, local_files_only=True))
        except Exception:  # noqa: BLE001 - any resolver refusal means "not here"
            return None

    def cached(self, name: str) -> bool:
        return self.local_path(name) is not None

    def forget(self, name: str | None = None) -> None:
        """Drop a memoized probe so weights installed out of band are noticed."""
        if name is None:
            self._cached.clear()
        else:
            self._cached.pop(name.strip(), None)

    # ---- state -------------------------------------------------------------

    def status(self, name: str) -> dict[str, Any]:
        """The four-state report for one model name.

        ``downloading`` carries elapsed seconds and the approximate total, and
        deliberately no percentage or downloaded-byte count:
        ``faster_whisper.download_model`` disables the hub's progress hook, so
        there is nothing to observe, and a proportion derived from an expected
        total would be an estimate presented as a reading.
        """
        name = name.strip()
        task = self._tasks.get(name)
        downloading = task is not None and not task.done()
        backend = self.backend_installed()
        status: str
        error: str | None = None
        if not backend:
            status = "not_downloaded"
        elif downloading:
            status = "downloading"
        elif self.cached(name):
            status = "ready"
        elif name in self._errors:
            status, error = "error", self._errors[name]
        else:
            status = "not_downloaded"
        started = self._started.get(name)
        return {
            "model": name,
            "status": status,
            "backend_installed": backend,
            "path": self.local_path(name) if status == "ready" else None,
            "size_hint": whisper_size_hint(name),
            "approximate_mb": WHISPER_APPROXIMATE_MB.get(name),
            "elapsed_seconds": (
                round(time.monotonic() - started, 1) if downloading and started else None
            ),
            "error": error,
        }

    def statuses(self, *names: str) -> list[dict[str, Any]]:
        """One report per distinct configured model, in the order asked."""
        ordered: dict[str, None] = {}
        for name in names:
            cleaned = name.strip()
            if cleaned:
                ordered.setdefault(cleaned, None)
        return [self.status(name) for name in ordered]

    # ---- download ----------------------------------------------------------

    def start_download(self, name: str, progress: ProgressCallback | None = None) -> bool:
        """Begin the fetch for one model; False when one is already running.

        Only ever reached from an explicit operator act - the Settings control or
        its route. Nothing on the transcription path calls this.
        """
        name = name.strip()
        if not name:
            raise VoiceModelError("a model name is required")
        task = self._tasks.get(name)
        if task is not None and not task.done():
            return False
        self._errors.pop(name, None)
        self.forget(name)
        self._started[name] = time.monotonic()
        self._tasks[name] = asyncio.create_task(
            self._download(name, progress), name=f"whisper-download:{name}"
        )
        log.info("whisper model download requested model=%s", name, extra={"model": name})
        return True

    async def wait(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _download(self, name: str, progress: ProgressCallback | None) -> None:
        started = time.monotonic()
        try:
            if not self.backend_installed():
                raise VoiceModelError(
                    "faster-whisper is not installed; local dictation needs the "
                    "on-device speech libraries - download them in Settings → "
                    "Voice, or install the voice-local extra "
                    "(`uv sync --extra voice-local`)"
                )
            await asyncio.to_thread(self._fetch, name)
            self.forget(name)
            if not self.cached(name):
                raise VoiceModelError(
                    f"{name} downloaded but did not resolve locally afterwards"
                )
            elapsed = round(time.monotonic() - started, 1)
            log.info(
                "whisper model download complete model=%s seconds=%.1f",
                name,
                elapsed,
                extra={"model": name, "seconds": elapsed},
            )
        except asyncio.CancelledError:
            self._errors[name] = "the download was cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - every failure becomes a reported state
            message = str(exc)[:400] or exc.__class__.__name__
            self._errors[name] = message
            log.warning(
                "whisper model download failed model=%s: %s",
                name,
                message,
                extra={"model": name, "diagnostic": message},
            )
        finally:
            self._started.pop(name, None)
            await report_progress(self, progress, name)

    @staticmethod
    def _fetch(name: str) -> None:
        from . import av_stub

        av_stub.install()
        from faster_whisper.utils import download_model

        download_model(name)


def g2p_model_installed() -> bool:
    """Whether `spacy.load("en_core_web_sm")` would resolve in this interpreter.

    Deliberately the same question spaCy asks itself. `spacy.util.load_model`
    tries `is_package(name)` first, which is `importlib.metadata.distribution`,
    and only then a filesystem path - so distribution metadata, not importability,
    is what decides, and a bare `find_spec` would answer a different question.
    """
    try:
        importlib.metadata.distribution(G2P_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return False
    except (OSError, ValueError):  # pragma: no cover - unreadable metadata on disk
        return False
    return True


class SpacyModelStore:
    """The Kokoro G2P's spaCy model, as a first-use asset rather than a dependency.

    Why it is here at all
    ---------------------
    `en-core-web-sm` is published as a GitHub release asset and exists on no
    index. It used to be declared in the `voice-local` extra and resolved through
    `[tool.uv.sources]`, which works perfectly for this checkout and not at all
    for anybody else: an override is a property of *this* project's resolution and
    is not carried in the wheel's `Requires-Dist`, so the published metadata
    carried a bare unresolvable name and `pip install "swe-mux[voice-local]"`
    failed outright for every downstream user of 0.1.0
    (`.docs/development/DEPENDENCY_AUDIT_2026-08-28.md` § 4). A PEP 508 direct URL
    is not an escape either, because PyPI rejects distributions whose
    `Requires-Dist` contains one.

    So the declaration moved to the unpublished `g2p-model` dependency group -
    which keeps the development checkout, both CI legs, and the desktop build
    resolving it exactly as before - and an installed copy that does not have it
    acquires it here, the way it already acquires the Kokoro weights.

    Why the activation is a `sys.path` entry
    ----------------------------------------
    misaki calls `spacy.load("en_core_web_sm")` by bare name, and spaCy resolves a
    bare name through `importlib.metadata.distribution`. So the downloaded copy
    has to look like an installed distribution rather than like a directory: the
    wheel is unpacked whole, `.dist-info` included, into one directory that is put
    on `sys.path`. `importlib.metadata` searches `sys.path` at call time, so the
    model becomes resolvable in this process without writing anything into
    `site-packages` - which a daemon has no business doing to the environment it
    was installed into.

    The refusal this must never lose
    --------------------------------
    misaki's `G2P.__init__` reads

        if not spacy.util.is_package(name): spacy.cli.download(name)

    which shells out to `pip install` **from inside the synthesis path**. In a
    frozen app there is no pip to shell to, and in a source checkout it would
    write into the venv unasked. `kokoro_tts._ensure_g2p` therefore refuses with a
    typed error before constructing `en.G2P` at all, and that check is the reason
    this store can be an explicit, visible download instead of a silent one.
    """

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "voice-models" / "spacy"
        #: The unpacked wheel. Named for what it is - a directory that behaves
        #: like a `site-packages` - because that is exactly what goes on `sys.path`.
        self.site = self.root / "site"
        self._state_path = self.root / "state.json"
        self._task: asyncio.Task[None] | None = None

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
        """Whether a verified unpacked copy is sitting under `self.site`."""
        state = self._read_state()
        if state.get("status") != "ready" or state.get("version") != G2P_VERSION:
            return False
        return (self.site / G2P_DISTRIBUTION / "__init__.py").is_file()

    def activate(self) -> bool:
        """Make the model resolvable in this interpreter; returns whether it is.

        Idempotent and cheap enough to call on every start. An environment that
        already has the distribution - a source checkout, the frozen bundle -
        short-circuits and `sys.path` is never touched, so the ordinary case pays
        nothing and cannot be perturbed by this at all.
        """
        if g2p_model_installed():
            return True
        if not self.unpacked():
            return False
        entry = str(self.site)
        if entry not in sys.path:
            sys.path.insert(0, entry)
        # `importlib.metadata` caches its view of a `sys.path` entry, so a new one
        # added after the first lookup is invisible without this.
        importlib.invalidate_caches()
        return g2p_model_installed()

    def ready(self) -> bool:
        return self.activate()

    def _source(self) -> str:
        """Which kind of present this is, read rather than remembered.

        Derived from whether this store's own directory is on `sys.path`, not
        from a flag set when `activate` last took the download branch. A flag is
        a memory of one moment and gets the answer wrong the moment the orderings
        differ - the first version of this reported `installed` for a model it had
        just downloaded itself, and a test caught it. The path entry is the fact.
        """
        return "downloaded" if str(self.site) in sys.path else "installed"

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        downloading = self._task is not None and not self._task.done()
        if self.activate():
            # `installed` and `downloaded` are different facts about the same
            # working state, and the difference is what a remedy is written
            # against: one is a property of the environment, the other of the
            # data directory this daemon owns.
            return {
                "status": "ready",
                "source": self._source(),
                "distribution": G2P_DISTRIBUTION,
                "version": G2P_VERSION,
                "total_bytes": G2P_WHEEL_BYTES,
                "downloaded_bytes": G2P_WHEEL_BYTES,
                "error": None,
            }
        status = "downloading" if downloading else state.get("status", "not_downloaded")
        if status in {"downloading", "ready"} and not downloading:
            # Either a restart killed the task, or the state file says `ready`
            # while `activate` just said otherwise - a deleted or half-unpacked
            # directory. Both mean what is on disk cannot be loaded.
            status = "error"
            state.setdefault("error", "the download was interrupted or the unpacked model is gone")
        return {
            "status": status,
            "source": None,
            "distribution": G2P_DISTRIBUTION,
            "version": G2P_VERSION,
            "total_bytes": G2P_WHEEL_BYTES,
            "downloaded_bytes": 0,
            "error": None if status == "downloading" else state.get("error"),
        }

    # ---- download ----------------------------------------------------------

    def start_download(self, progress: ProgressCallback | None = None) -> bool:
        """Begin the pinned download; returns False when one is already running."""
        if self._task is not None and not self._task.done():
            return False
        if self.activate():
            return False
        self._write_state({"status": "downloading", "version": G2P_VERSION})
        self._task = asyncio.create_task(self._download(progress), name="g2p-model-download")
        return True

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _download(self, progress: ProgressCallback | None) -> None:
        started = time.monotonic()
        timeout = aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT_SECONDS, connect=20)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout, connector=trusting_connector()
            ) as session:
                payload = await self._fetch_wheel(session)
            await asyncio.to_thread(self._unpack, payload)
            self._write_state(
                {
                    "status": "ready",
                    "version": G2P_VERSION,
                    "sha256": G2P_WHEEL_SHA256,
                    "verified_at": time.time(),
                }
            )
            self.activate()
            log.info(
                "g2p model download complete bytes=%d seconds=%.1f",
                G2P_WHEEL_BYTES,
                time.monotonic() - started,
            )
        except asyncio.CancelledError:
            self._write_state(
                {
                    "status": "error",
                    "version": G2P_VERSION,
                    "error": "the download was cancelled",
                }
            )
            raise
        except (VoiceModelError, aiohttp.ClientError, OSError, TimeoutError) as exc:
            message = str(exc)[:400] or exc.__class__.__name__
            self._write_state({"status": "error", "version": G2P_VERSION, "error": message})
            log.warning("g2p model download failed: %s", message)
        except Exception as exc:  # noqa: BLE001 - a defect must not read as a transfer failure
            # The clause above names what goes wrong fetching bytes over a network
            # onto a disk. Anything else here is a defect in this process, and
            # reporting it in the same words sends the reader to the wrong
            # subsystem - which is exactly what happened to the sibling store on
            # 2026-08-29, when a `TypeError` in a progress callback was reported
            # as an interrupted download and two people went looking at disk and
            # network. `log.exception` is what makes the traceback findable.
            self._write_state(
                {
                    "status": "error",
                    "version": G2P_VERSION,
                    "error": (
                        "the download failed unexpectedly "
                        f"({exc.__class__.__name__}: {str(exc)[:200]}) - this is a "
                        "defect rather than a network or disk problem"
                    ),
                }
            )
            log.exception("g2p model download crashed")
        finally:
            await report_progress(self, progress)

    async def _fetch_wheel(self, session: aiohttp.ClientSession) -> bytes:
        """The pinned wheel, in memory, verified before anything touches disk.

        In memory because it is 12 MB and because a partial file that never
        reaches the filesystem cannot be mistaken for a complete one - the same
        property `KokoroModelStore` buys with a `.partial` name for payloads far
        too large to hold.
        """
        async with session.get(G2P_WHEEL_URL, allow_redirects=True) as response:
            if response.status != 200:
                raise VoiceModelError(
                    f"the model host returned HTTP {response.status} for "
                    f"{G2P_DISTRIBUTION} {G2P_VERSION}"
                )
            payload = await response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != G2P_WHEEL_SHA256:
            raise VoiceModelError(
                f"{G2P_DISTRIBUTION} {G2P_VERSION} failed verification "
                f"(got {len(payload)} bytes, sha256 {digest[:16]}...); the pinned "
                "release may have been tampered with or the download was corrupted"
            )
        return payload

    def _unpack(self, payload: bytes) -> None:
        """Replace `self.site` with the wheel's contents, atomically enough.

        Unpacked beside the live directory and then swapped, so a failure part way
        through leaves the previous state rather than a half-model that `activate`
        would happily put on `sys.path`.
        """
        staging = self.root / "site.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in archive.namelist():
                # The payload is hash-pinned, so this cannot currently be hostile.
                # It is checked anyway because the alternative is a rule that holds
                # only while the constant above is right, and an absolute or
                # parent-relative member is never legitimate in a wheel.
                target = (staging / member).resolve()
                if not target.is_relative_to(staging.resolve()):
                    raise VoiceModelError(
                        f"{G2P_DISTRIBUTION} {G2P_VERSION} contains an out-of-tree "
                        f"path ({member!r}); refusing to unpack it"
                    )
            archive.extractall(staging)
        if not (staging / G2P_DISTRIBUTION / "__init__.py").is_file():
            raise VoiceModelError(
                f"the {G2P_DISTRIBUTION} wheel did not contain the package it names"
            )
        shutil.rmtree(self.site, ignore_errors=True)
        staging.replace(self.site)
