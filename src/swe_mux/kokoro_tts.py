"""Kokoro-82M text-to-speech through a direct onnxruntime session.

The engine is deliberately wrapper-free: the published Kokoro wrapper packages
(`kokoro-onnx`, `misaki[en]`, Piper, KittenTTS) all pull an espeak-ng payload,
which is GPL and may never enter this project's shipped closure (Phase 10.5).
The ONNX interface is three inputs (`tokens` int64, `style` float32[1,256],
`speed` float32[1]) and one `audio` output, so a direct session is smaller than
any wrapper anyway.

Phonemization is lexicon-only misaki with ``fallback=None``. Out-of-vocabulary
words come back as a ``❓`` token rather than being silently dropped, and this
module owns the repair ladder: a project lexicon of respellings, a compound
splitter (camelCase, digits, hyphens, underscores), and spelling the word letter
by letter as the unambiguous last resort.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import struct
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
# Kokoro's style table has 510 rows and the model was trained on windows of at
# most 510 phoneme tokens plus the two pad tokens. Inputs are chunked below this.
MAX_PHONEME_TOKENS = 500

# The G2P constraint from Phase 10.5 is a dependency-review rule, not a
# preference: no espeak-ng binary, data directory, or Python wrapper may exist
# anywhere in the closure. Failing loudly here is what keeps a transitive
# dependency from silently re-introducing it.
ESPEAK_MODULES = ("espeakng_loader", "phonemizer", "phonemizer_fork")

# Respellings for the vocabulary the 2026-08-17 measurement found unresolved,
# plus this project's own recurring jargon. Keys are casefolded whole words.
PROJECT_LEXICON: dict[str, str] = {
    "swe": "S W E",
    "mux": "mucks",
    "muxd": "mucks D",
    "py": "pie",
    "pyproject": "pie project",
    "healthcheck": "health check",
    "worktree": "work tree",
    "worktrees": "work trees",
    "conpty": "con P T Y",
    "pty": "P T Y",
    "stt": "S T T",
    "tts": "T T S",
    "cli": "C L I",
    "repo": "repo",
}

# Spoken names for spelled-out letters, the unambiguous last resort. A dropped
# word is worse than a spelled one: the listener cannot know something is
# missing, but they can survive hearing "pie why" for an unknown compound.
LETTER_NAMES: dict[str, str] = {
    "a": "eigh", "b": "bee", "c": "sea", "d": "dee", "e": "ee", "f": "eff",
    "g": "gee", "h": "aitch", "i": "eye", "j": "jay", "k": "kay", "l": "ell",
    "m": "em", "n": "en", "o": "oh", "p": "pee", "q": "cue", "r": "are",
    "s": "ess", "t": "tee", "u": "you", "v": "vee", "w": "double you",
    "x": "ex", "y": "why", "z": "zee",
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}

UNKNOWN_TOKEN = "❓"
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'_.-]*")


class KokoroError(RuntimeError):
    """A typed Kokoro failure. voice.py wraps it into a VoiceError."""


def assert_espeak_free() -> None:
    """Refuse to construct if any espeak wrapper is importable.

    The alternative — quietly running while a GPL payload sits in the
    environment — is exactly the silent regression the Phase 10.5 audit found
    in packages that declared permissive licenses over GPL binaries.
    """
    present = [name for name in ESPEAK_MODULES if importlib.util.find_spec(name) is not None]
    if present:
        raise KokoroError(
            "an espeak-ng wrapper is installed ("
            + ", ".join(present)
            + "); the Kokoro engine refuses to run beside it — remove the package "
            "(Phase 10.5 forbids espeak-ng anywhere in the closure)"
        )


def split_compound(word: str) -> list[str]:
    """Split camelCase, digit boundaries, hyphens, underscores, and dots."""
    parts = re.split(r"[-_./]+", word)
    pieces: list[str] = []
    for part in parts:
        if not part:
            continue
        # camelCase and letter/digit boundaries: swemux stays whole, SweMux and
        # swe2mux split. Acronym runs stay together (ConPTY -> Con, PTY).
        pieces.extend(
            piece
            for piece in re.findall(
                r"[A-Z]{2,}(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]{2,}|\d+|[A-Za-z]", part
            )
            if piece
        )
    return pieces if len(pieces) > 1 else [word]


def spell_out(word: str) -> str:
    """Letter-by-letter last resort, so no token is ever silently dropped."""
    names = [LETTER_NAMES[ch.lower()] for ch in word if ch.lower() in LETTER_NAMES]
    return " ".join(names) or word


@dataclass(frozen=True)
class KokoroPaths:
    model: Path
    tokenizer: Path
    voices_dir: Path

    def voice(self, voice_id: str) -> Path:
        return self.voices_dir / f"{voice_id}.bin"


class KokoroEngine:
    """One loaded Kokoro session plus its phonemizer and vocabulary.

    Thread-safe for the two-slot synthesis concurrency VoiceService allows: the
    session itself is stateless per run and misaki's G2P is guarded by a lock
    because its spaCy pipeline is not documented reentrant.
    """

    def __init__(self, paths: KokoroPaths) -> None:
        assert_espeak_free()
        self.paths = paths
        self._lock = threading.Lock()
        self._g2p: Any = None
        self._session: Any = None
        self._vocab: dict[str, int] = {}
        self._voices: dict[str, Any] = {}
        self._word_cache: dict[str, str] = {}

    # ---- loading ----------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - dependency of the package
            raise KokoroError("onnxruntime is not installed; run `uv sync`") from exc
        if not self.paths.model.exists() or not self.paths.tokenizer.exists():
            raise KokoroError(
                "the Kokoro model is not downloaded; download it in Settings → Voice"
            )
        try:
            payload = json.loads(self.paths.tokenizer.read_text(encoding="utf-8"))
            vocab = payload["model"]["vocab"]
            self._vocab = {str(key): int(value) for key, value in vocab.items()}
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise KokoroError(f"the Kokoro tokenizer could not be read: {exc}") from exc
        providers = ["CPUExecutionProvider"]
        try:
            available = set(onnxruntime.get_available_providers())
            # GPU execution collapses synthesis latency where a provider exists;
            # the pip package usually ships CPU-only and this list degrades to it.
            preferred = [
                name
                for name in ("CUDAExecutionProvider", "DmlExecutionProvider")
                if name in available
            ]
            providers = [*preferred, "CPUExecutionProvider"]
        except Exception:  # noqa: BLE001 - provider probing is best-effort
            pass
        try:
            self._session = onnxruntime.InferenceSession(
                str(self.paths.model), providers=providers
            )
        except Exception as exc:
            raise KokoroError(f"the Kokoro model failed to load: {str(exc)[:300]}") from exc
        log.info(
            "kokoro model loaded model=%s providers=%s vocab=%d",
            self.paths.model.name,
            providers,
            len(self._vocab),
        )

    def _ensure_g2p(self) -> Any:
        if self._g2p is None:
            assert_espeak_free()
            try:
                from misaki import en
            except ImportError as exc:
                raise KokoroError("the misaki G2P package is not installed; run `uv sync`") from exc
            # fallback=None is the espeak-free constraint: unknown words come
            # back as the unknown token and are repaired here instead of being
            # handed to a GPL phonemizer.
            self._g2p = en.G2P(trf=False, british=False, fallback=None)
        return self._g2p

    def _voice_style(self, voice_id: str) -> Any:
        import numpy

        cached = self._voices.get(voice_id)
        if cached is not None:
            return cached
        path = self.paths.voice(voice_id)
        if not path.exists():
            raise KokoroError(f"the Kokoro voice {voice_id} is not downloaded")
        raw = numpy.fromfile(path, dtype=numpy.float32)
        if raw.size % 256 != 0:
            raise KokoroError(f"the Kokoro voice file {voice_id} is malformed")
        style = raw.reshape(-1, 1, 256)
        self._voices[voice_id] = style
        return style

    # ---- text preparation --------------------------------------------------

    def _word_resolves(self, word: str) -> bool:
        phonemes, _tokens = self._ensure_g2p()(word)
        return UNKNOWN_TOKEN not in phonemes

    def _resolve_word(self, word: str, depth: int = 0) -> str:
        """The repair ladder: lexicon, compound split, then spell it out.

        Every replacement is re-verified and repaired recursively, because a
        lexicon respelling can itself contain a token the lexicon-only G2P does
        not know — the audit's own "pyproject" produced "py", which is exactly
        such a token. Spelling is the bounded floor the recursion lands on.
        """
        cached = self._word_cache.get(word)
        if cached is not None:
            return cached
        result = word
        if not self._word_resolves(word):
            if depth >= 3:
                result = spell_out(word)
            else:
                replacement = PROJECT_LEXICON.get(word.casefold())
                if replacement is None:
                    pieces = split_compound(word)
                    replacement = " ".join(pieces) if len(pieces) > 1 else None
                if replacement is not None:
                    result = " ".join(
                        self._resolve_word(piece, depth + 1) if piece != word else spell_out(piece)
                        for piece in replacement.split()
                    )
                else:
                    result = spell_out(word)
                if not self._word_resolves(result):
                    result = spell_out(word)
        if len(self._word_cache) > 4096:
            self._word_cache.clear()
        self._word_cache[word] = result
        return result

    def prepare_text(self, text: str) -> str:
        """Rewrite any word the lexicon-only G2P cannot resolve.

        Cheap in the common case: one full-text pass decides whether any repair
        is needed at all, and only an unknown token triggers per-word work.
        """
        phonemes, _tokens = self._ensure_g2p()(text)
        if UNKNOWN_TOKEN not in phonemes:
            return text
        return _WORD.sub(lambda match: self._resolve_word(match.group(0)), text)

    def phonemize(self, text: str) -> str:
        prepared = self.prepare_text(text)
        phonemes, _tokens = self._ensure_g2p()(prepared)
        # The repair ladder above makes this unreachable for English words; a
        # non-Latin glyph can still produce one, and dropping it is explicit.
        return str(phonemes).replace(UNKNOWN_TOKEN, "")

    def _token_chunks(self, phonemes: str) -> list[list[int]]:
        ids = [self._vocab[ch] for ch in phonemes if ch in self._vocab]
        if len(ids) <= MAX_PHONEME_TOKENS:
            return [ids] if ids else []
        # Prefer a punctuation boundary near the window edge, so a chunk seam
        # lands between clauses rather than inside a word.
        boundary_ids = {self._vocab[ch] for ch in ".,;:!?" if ch in self._vocab}
        chunks: list[list[int]] = []
        start = 0
        while start < len(ids):
            end = min(start + MAX_PHONEME_TOKENS, len(ids))
            if end < len(ids):
                cut = next(
                    (
                        index
                        for index in range(end - 1, max(start + 50, end - 120), -1)
                        if ids[index] in boundary_ids
                    ),
                    end - 1,
                )
                end = cut + 1
            chunks.append(ids[start:end])
            start = end
        return chunks

    # ---- synthesis ---------------------------------------------------------

    def synthesize_wav(
        self, text: str, destination: Path, *, voice_id: str, speed: float
    ) -> None:
        """Synthesize `text` into a 16-bit mono WAV at `destination`."""
        import numpy

        with self._lock:
            self._ensure_loaded()
            phonemes = self.phonemize(text)
            chunks = self._token_chunks(phonemes)
            if not chunks:
                raise KokoroError("nothing speakable remained after phonemization")
            style_table = self._voice_style(voice_id)
            assert self._session is not None
            pieces: list[Any] = []
            for ids in chunks:
                row = min(len(ids), style_table.shape[0] - 1)
                inputs = {
                    "input_ids": numpy.array([[0, *ids, 0]], dtype=numpy.int64),
                    "style": style_table[row].astype(numpy.float32),
                    "speed": numpy.array([speed], dtype=numpy.float32),
                }
                names = {item.name for item in self._session.get_inputs()}
                if "input_ids" not in names and "tokens" in names:
                    inputs["tokens"] = inputs.pop("input_ids")
                outputs = self._session.run(None, inputs)
                pieces.append(numpy.asarray(outputs[0]).reshape(-1))
            audio = numpy.concatenate(pieces) if len(pieces) > 1 else pieces[0]
            clipped = numpy.clip(audio, -1.0, 1.0)
            samples = (clipped * 32767.0).astype(numpy.int16)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(SAMPLE_RATE)
            sink.writeframes(samples.tobytes())
        if destination.stat().st_size <= 44:
            raise KokoroError("Kokoro produced no audio")


def duration_seconds(path: Path) -> float | None:
    """Exact duration from the WAV header, cheaper than an estimate."""
    try:
        with wave.open(str(path), "rb") as source:
            frames = source.getnframes()
            rate = source.getframerate() or SAMPLE_RATE
            return round(frames / rate, 1)
    except (OSError, wave.Error, struct.error):
        return None
