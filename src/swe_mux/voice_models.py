"""On-demand, hash-verified acquisition of the Kokoro TTS model.

Models are downloaded, never bundled — the rule the Whisper weights and the
Silero VAD assets already follow. What is different here is that this download
is pinned: an immutable repository revision and a per-file SHA-256, checked
while streaming, with an explicit ``not_downloaded → downloading → ready``
state machine whose error state can never be loaded. A partial or tampered
file is deleted and reported, not retried into service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

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

STATES = ("not_downloaded", "downloading", "ready", "error")

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


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
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for relative, (size, sha256) in KOKORO_FILES.items():
                    destination = self.install.root / relative
                    if self._file_verified(destination, size, sha256):
                        downloaded += size
                        continue
                    self._progress["current_file"] = relative
                    await self._fetch_one(session, relative, destination, size, sha256)
                    downloaded += size
                    self._progress["downloaded_bytes"] = downloaded
                    if progress is not None:
                        await progress(self.status())
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
        finally:
            if progress is not None:
                await progress(self.status())

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
