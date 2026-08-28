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
import os
import re
import struct
import threading
import time
import wave
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .voice_models import g2p_model_installed

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
# misaki's Markdown-link phoneme override: [word](/phonemes/) speaks the exact
# phonemes. A lexicon value in this form is atomic — splitting it on whitespace
# (multi-word phonemes contain spaces) or re-running word repair over its inside
# would corrupt it, so replacement handling treats a link as one piece.
PHONEME_LINK = re.compile(r"\[[^\[\]]*\]\(/[^()]*/\)")
# The trailing characters _WORD can absorb from adjacent punctuation. A token
# like "vaultspaces." must not defeat the whole-word lexicon lookup.
_WORD_EDGE = "'_.-"


def replacement_pieces(replacement: str) -> list[str]:
    """Split a respelling on whitespace, keeping phoneme links whole."""
    pieces: list[str] = []
    position = 0
    for match in PHONEME_LINK.finditer(replacement):
        pieces.extend(replacement[position : match.start()].split())
        pieces.append(match.group(0))
        position = match.end()
    pieces.extend(replacement[position:].split())
    return pieces


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
            "(swe-mux forbids espeak-ng anywhere in its shipped closure)"
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


class SpelledWordLog:
    """Durable, bounded, deduplicated record of words the ladder had to spell.

    A spelled word is the ladder admitting defeat, and the operator can fix each
    one with a single lexicon entry — but only if they can see which words hit
    the floor. Entries key on the casefolded word and carry occurrence counts
    and timestamps; the JSON file survives daemon restarts, and the cap keeps a
    pathological text from growing it without bound. Thread-safe, because the
    engine reports from the synthesis worker thread.
    """

    CAP = 200

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._entries is None:
            entries: dict[str, dict[str, Any]] = {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for word, item in raw.items():
                        if isinstance(word, str) and isinstance(item, dict):
                            entries[word] = {
                                "count": max(1, int(item.get("count", 1))),
                                "first_seen": float(item.get("first_seen", 0.0)),
                                "last_seen": float(item.get("last_seen", 0.0)),
                            }
            except (OSError, ValueError, TypeError):
                # A missing or corrupt file starts the log over; telemetry must
                # never be able to break synthesis.
                entries = {}
            self._entries = entries
        return self._entries

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(entries), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            log.warning("could not persist the spelled-word log", exc_info=True)

    def record(self, word: str) -> None:
        key = word.strip().casefold()
        if not key or len(key) > 60:
            return
        now = time.time()
        with self._lock:
            entries = self._load()
            entry = entries.get(key)
            if entry is not None:
                entry["count"] += 1
                entry["last_seen"] = now
            else:
                entries[key] = {"count": 1, "first_seen": now, "last_seen": now}
                while len(entries) > self.CAP:
                    oldest = min(entries, key=lambda item: entries[item]["last_seen"])
                    del entries[oldest]
            self._write(entries)

    def discard(self, words: Iterable[str]) -> None:
        """Drop entries the lexicon now covers; a fixed word is no longer debt."""
        keys = {str(word).strip().casefold() for word in words}
        with self._lock:
            entries = self._load()
            removed = [word for word in entries if word in keys]
            for word in removed:
                del entries[word]
            if removed:
                self._write(entries)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            loaded = self._load()
            return [
                {"word": word, **item}
                for word, item in sorted(
                    loaded.items(), key=lambda pair: pair[1]["last_seen"], reverse=True
                )
            ]


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

    def __init__(
        self,
        paths: KokoroPaths,
        *,
        lexicon: dict[str, str] | None = None,
        on_spell_out: Callable[[str], None] | None = None,
    ) -> None:
        assert_espeak_free()
        self.paths = paths
        self._lock = threading.Lock()
        self._g2p: Any = None
        self._session: Any = None
        self._vocab: dict[str, int] = {}
        self._voices: dict[str, Any] = {}
        # Reported once per spoken occurrence of a word whose final resolution
        # involved the spelling floor — the telemetry half of the repair ladder.
        self._on_spell_out = on_spell_out
        self._lexicon: dict[str, str] = dict(PROJECT_LEXICON)
        self._word_cache: dict[str, tuple[str, bool]] = {}
        if lexicon:
            self.set_lexicon(lexicon)

    def set_lexicon(self, lexicon: dict[str, str]) -> None:
        """Merge user respellings over the project lexicon; invalidate resolutions.

        Both maps are *rebound*, never mutated in place: a synthesis run on the
        worker thread may be mid-resolution, and rebinding lets it finish against
        the dicts it started with while every later lookup sees the new ones.
        The lexicon rebinds first — `_resolve_word` snapshots the cache before
        the lexicon, so a snapshot holding the new cache has by construction
        already seen the new lexicon, and a result computed from the old lexicon
        can only land in the discarded old cache.
        """
        merged = dict(PROJECT_LEXICON)
        for word, spoken in lexicon.items():
            key = str(word).strip().casefold()
            replacement = str(spoken).strip()
            if key and replacement:
                merged[key] = replacement
        self._lexicon = merged
        self._word_cache = {}

    # ---- loading ----------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - dependency of the package
            raise KokoroError(
                "onnxruntime is not installed; local speech synthesis needs the "
                "voice-local extra (`uv sync --extra voice-local`)"
            ) from exc
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
                raise KokoroError(
                    "the misaki G2P package is not installed; local speech synthesis "
                    "needs the voice-local extra (`uv sync --extra voice-local`)"
                ) from exc
            # This refusal is load-bearing and is not defensive tidiness. misaki's
            # `G2P.__init__` reads `if not spacy.util.is_package(name):
            # spacy.cli.download(name)`, which shells out to `pip install` from
            # inside the synthesis path - into the venv of a source checkout, and
            # into nothing at all in a frozen app, where there is no pip to reach.
            # The model is a first-use asset now rather than a declared dependency
            # (`voice_models.SpacyModelStore` says why), so its absence is a
            # legitimate state and has to answer with a remedy the reader can act
            # on rather than with an unasked-for install.
            if not g2p_model_installed():
                raise KokoroError(
                    "the spaCy English model the G2P needs is not present; download "
                    "it in Settings → Voice (it comes with the Kokoro model), or "
                    "install it into this environment with `uv sync --group g2p-model`"
                )
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

    def _resolve_word(self, word: str, report: bool = True) -> str:
        # Snapshot order matters against set_lexicon's rebind order; see there.
        cache = self._word_cache
        lexicon = self._lexicon
        # Trailing punctuation _WORD absorbs ("vaultspaces.") must not defeat
        # the whole-word lexicon lookup: resolve the core, keep the tail so a
        # sentence-final repair does not lose its prosody pause. Only when the
        # full token is unresolvable — a token misaki handles natively as-is
        # stays untouched.
        suffix = ""
        stripped = word.rstrip(_WORD_EDGE)
        if stripped and stripped != word and not self._word_resolves(word):
            word, suffix = stripped, word[len(stripped) :]
        result, spelled = self._resolve_word_inner(word, 0, cache, lexicon)
        if spelled and report and self._on_spell_out is not None:
            try:
                self._on_spell_out(word)
            except Exception:  # noqa: BLE001 - telemetry must never break speech
                log.warning("spell-out reporter failed for %r", word, exc_info=True)
        return result + suffix

    def _resolve_word_inner(
        self,
        word: str,
        depth: int,
        cache: dict[str, tuple[str, bool]],
        lexicon: dict[str, str],
    ) -> tuple[str, bool]:
        """The repair ladder: lexicon, compound split, then spell it out.

        Every replacement is re-verified and repaired recursively, because a
        lexicon respelling can itself contain a token the lexicon-only G2P does
        not know — the audit's own "pyproject" produced "py", which is exactly
        such a token. Spelling is the bounded floor the recursion lands on.
        The returned flag says whether that floor was part of the final result,
        so `_resolve_word` can report the top-level word — the token an operator
        would actually add to the lexicon — rather than a synthetic fragment.
        """
        cached = cache.get(word)
        if cached is not None:
            return cached
        result, spelled = word, False
        if not self._word_resolves(word):
            if depth >= 3:
                result, spelled = spell_out(word), True
            else:
                replacement = lexicon.get(word.casefold())
                if replacement is None:
                    pieces = split_compound(word)
                    replacement = " ".join(pieces) if len(pieces) > 1 else None
                if replacement is not None:
                    result, spelled = self._resolve_replacement(
                        replacement, word, depth, cache, lexicon
                    )
                else:
                    result, spelled = spell_out(word), True
                if not self._word_resolves(result):
                    result, spelled = spell_out(word), True
        if len(cache) > 4096:
            cache.clear()
        cache[word] = (result, spelled)
        return result, spelled

    def _resolve_replacement(
        self,
        replacement: str,
        word: str,
        depth: int,
        cache: dict[str, tuple[str, bool]],
        lexicon: dict[str, str],
    ) -> tuple[str, bool]:
        """Resolve a respelling's pieces the way the ladder speaks them.

        Phoneme links are atomic: verified whole against the G2P, never split
        on their internal spaces or repaired from the inside. A piece equal to
        the word being repaired would recurse forever, so it goes straight to
        the spelling floor.
        """
        resolved: list[str] = []
        spelled = False
        for piece in replacement_pieces(replacement):
            if piece == word:
                resolved.append(spell_out(piece))
                spelled = True
            elif PHONEME_LINK.fullmatch(piece):
                if self._word_resolves(piece):
                    resolved.append(piece)
                else:
                    resolved.append(spell_out(word))
                    spelled = True
            else:
                piece_result, piece_spelled = self._resolve_word_inner(
                    piece, depth + 1, cache, lexicon
                )
                resolved.append(piece_result)
                spelled = spelled or piece_spelled
        return " ".join(resolved), spelled

    def check_respelling(self, word: str, value: str) -> dict[str, Any]:
        """Advisory verdict for one lexicon entry: does its value speak as
        written, or would the ladder reject it and fall to the spelling floor?

        Runs exactly the resolution the ladder runs when the entry is used, so
        a value that legitimately repairs through the lexicon or the splitter
        passes, and reports nothing to spell-out telemetry.
        """
        with self._lock:
            cache = self._word_cache
            lexicon = self._lexicon
            resolved, spelled = self._resolve_replacement(value, word, 0, cache, lexicon)
            # Name the pieces that end on the floor, not every OOV piece: a piece
            # that repairs through the lexicon or the splitter is not the
            # entry's problem and would make the hint cry wolf.
            unspeakable: list[str] = []
            for piece in replacement_pieces(value):
                if PHONEME_LINK.fullmatch(piece):
                    if not self._word_resolves(piece):
                        unspeakable.append(piece)
                elif piece == word:
                    unspeakable.append(piece)
                else:
                    _piece_result, piece_spelled = self._resolve_word_inner(
                        piece, 1, cache, lexicon
                    )
                    if piece_spelled:
                        unspeakable.append(piece)
            phonemes, _tokens = self._ensure_g2p()(resolved)
        ok = not spelled and UNKNOWN_TOKEN not in phonemes
        return {
            "ok": ok,
            "phonemes": str(phonemes).replace(UNKNOWN_TOKEN, "").strip() or None,
            "spoken_as": resolved if resolved != value else None,
            "unspeakable": unspeakable,
        }

    def build_respelling(self, word: str, value: str) -> dict[str, Any]:
        """Derive a ladder-accepted value from a phonetic spelling.

        The user types how the word sounds (or nothing, in which case the word
        itself is read as its own phonetic spelling); every piece the G2P
        already knows passes through as text, and each unknown piece becomes an
        exact ``[piece](/phonemes/)`` link via the deterministic phonics rules.
        The result is re-checked with the real machinery before it is offered.
        """
        from .phonics import phonetic_to_phonemes

        source = value.strip() or word.strip()
        if not source:
            return {"ok": False, "value": None, "diagnostic": "nothing to build from"}
        pieces: list[str] = []
        with self._lock:
            for piece in replacement_pieces(source):
                if PHONEME_LINK.fullmatch(piece) or self._word_resolves(piece):
                    pieces.append(piece)
                    continue
                phonemes = phonetic_to_phonemes(piece)
                if phonemes is None:
                    return {
                        "ok": False,
                        "value": None,
                        "diagnostic": (
                            f"could not derive phonemes for “{piece[:40]}” — "
                            "spell it with plain letters, the way it sounds"
                        ),
                    }
                pieces.append(f"[{piece}](/{phonemes}/)")
        built = " ".join(pieces)
        verdict = self.check_respelling(word, built)
        return {
            "ok": bool(verdict["ok"]),
            "value": built,
            "phonemes": verdict["phonemes"],
            "diagnostic": None if verdict["ok"] else "the built value still fails verification",
        }

    def prepare_text(self, text: str, report: bool = True) -> str:
        """Rewrite any word the lexicon-only G2P cannot resolve.

        Cheap in the common case: one full-text pass decides whether any repair
        is needed at all, and only an unknown token triggers per-word work.
        `report=False` keeps an audition out of spell-out telemetry.
        """
        phonemes, _tokens = self._ensure_g2p()(text)
        if UNKNOWN_TOKEN not in phonemes:
            return text
        return _WORD.sub(lambda match: self._resolve_word(match.group(0), report), text)

    def phonemize(self, text: str, report: bool = True) -> str:
        prepared = self.prepare_text(text, report)
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
        self,
        text: str,
        destination: Path,
        *,
        voice_id: str,
        speed: float,
        report_unknown: bool = True,
    ) -> None:
        """Synthesize `text` into a 16-bit mono WAV at `destination`."""
        import numpy

        with self._lock:
            self._ensure_loaded()
            phonemes = self.phonemize(text, report_unknown)
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
