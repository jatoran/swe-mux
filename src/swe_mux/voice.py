"""Read-aloud (TTS) service: turn agent replies into playable audio clips.

This is deliberately not an automation observer. Observers are restricted to
annotate/notify through the fixed OpenRouter origin; audio synthesis uses a
separate engine boundary (the offline OS voice, or local Kokoro-82M through
onnxruntime once its pinned model is downloaded) and per-session interactive
state. No synthesis path reaches a network service. The only OpenRouter
traffic here is the optional spoken-summary call, which records its call and
spend in the shared automation ledger under the ``builtin:voice-summary`` rule
id so budgets stay visible in one place.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
import wave
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .background_tasks import background
from .config import Config
from .event_bus import EventBus
from .harness import has_observable_transcript
from .kokoro_tts import KokoroEngine, KokoroError, KokoroPaths
from .kokoro_tts import duration_seconds as wav_duration_seconds
from .openrouter import OpenRouterClient, OpenRouterError
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
)
from .subprocess_flags import background_creation_flags
from .transcript_view import final_exchange
from .voice_models import KokoroModelStore


def _last_exchange(
    path: Path | None, backend: str, native_id: str | None
) -> tuple[str, str]:
    """`final_exchange` positionally, for `asyncio.to_thread`, which takes no keywords."""
    return final_exchange(path, backend, native_id=native_id)

if TYPE_CHECKING:
    from .automation_store import AutomationStore
    from .session import SessionManager

log = logging.getLogger(__name__)

T = TypeVar("T")

VOICE_RULE_ID = "builtin:voice-summary"
VOICE_MODES = {"off", "on_demand", "auto"}
VOICE_EVENT_LOOP = "voice-events"
DEBOUNCE_SECONDS = 1.0
ENGINE_TIMEOUT_SECONDS = 45.0
STT_TIMEOUT_SECONDS = 60.0
STT_MAX_BYTES = 2 * 1024 * 1024
STT_MAX_SECONDS = 35.0
STT_LATENCY_SAMPLES = 200
VOICE_APPROVAL_TTL_SECONDS = 20.0
# Capture always resamples to this rate, and decoding runs from the raw PCM rather
# than a file, so the utterance is never resampled twice and never touches the disk.
STT_SAMPLE_RATE = 16_000
# Two decoders, because the two jobs have opposite priorities: a spoken command is a
# reflex that feels broken past half a second, and dictated prose is read afterwards.
# They hold separate locks so a speculative routing decode cannot queue the real
# utterance behind it.
COMMAND_PROFILE = "command"
DICTATION_PROFILE = "dictation"
DECODE_PROFILES = (COMMAND_PROFILE, DICTATION_PROFILE)
# Beam search buys accuracy that only shows in longer text, and costs about 10% on a
# one-second utterance against 30% on a twelve-second one. Short audio goes greedy.
STT_GREEDY_MAX_MS = 3_000.0
# How many configured wake words may bias the routing decoder. See `_hotwords`: a
# long list of short, similar tokens makes the small model loop instead of decoding.
STT_ROUTING_HOTWORD_LIMIT = 8
STT_HOTWORDS = (
    "swe-mux, Mux, Muxie, Claude, Codex, Git, GitHub, Python, "
    "TypeScript, terminal, API, send, submit, cancel, undo, mute, "
    "read reply, summary, verbatim, interrupt, stop listening"
)
# Every stage a spoken command passes through, end of speech to executed action.
# The daemon owns `queue_ms` and `decode_ms` and hands them back on the transcribe
# response; the browser owns the rest and posts the merged sample once the action
# has run, because only it knows when speech actually stopped.
LATENCY_FIELDS = (
    "audio_ms",  # captured speech, for reading decode cost against utterance length
    "endpoint_ms",  # last speech frame -> the VAD declared the utterance over
    "encode_ms",  # endpoint -> downsampled and WAV encoded
    "wait_ms",  # encoded -> POST issued, i.e. queued behind an earlier utterance
    "upload_ms",  # POST issued -> daemon entered its handler (round trip minus server)
    "queue_ms",  # daemon handler entry -> decode start
    "decode_ms",  # decode start -> recognized text
    "action_ms",  # text -> the command ran or the draft updated
    "total_ms",  # end of speech -> action, the number the exit criterion is about
)
# The four stages the roadmap's exit criterion is stated in, derived from the
# fields so a change in where a cost lands cannot silently rename a stage.
LATENCY_STAGES: dict[str, tuple[str, ...]] = {
    "to_post_ms": ("endpoint_ms", "encode_ms", "wait_ms"),
    "to_decode_ms": ("upload_ms", "queue_ms"),
    "decode_ms": ("decode_ms",),
    "action_ms": ("action_ms",),
}
LATENCY_MAX_MS = 600_000.0
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"speech": {"type": "string"}},
    "required": ["speech"],
    "additionalProperties": False,
}
SUMMARY_PROMPT = (
    "You turn a coding agent's latest reply into a short spoken update for a user "
    "listening hands-free. Return JSON with one field, speech. Write plain "
    "conversational English: no markdown, no code, no file paths unless essential, "
    "no bullet lists. Three to eight sentences. Lead with the outcome, then key "
    "decisions or findings, then anything the user must act on or answer. Speak as "
    "the assistant in first person."
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS voice_clips (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_run_id TEXT,
    created_at REAL NOT NULL,
    trigger TEXT NOT NULL,
    content_mode TEXT NOT NULL,
    engine TEXT NOT NULL,
    voice TEXT NOT NULL,
    text TEXT NOT NULL,
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_hint_s REAL,
    status TEXT NOT NULL,
    error TEXT,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_voice_clips_session ON voice_clips(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_voice_clips_run ON voice_clips(agent_run_id, created_at);
"""

SAPI_SCRIPT = r"""param([string]$TextPath,[string]$OutPath,[string]$Voice,[int]$Rate)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { $synth.SelectVoice($Voice) }
$synth.Rate = $Rate
$synth.SetOutputToWaveFile($OutPath)
$synth.Speak([IO.File]::ReadAllText($TextPath))
$synth.Dispose()
"""

SAPI_STT_SCRIPT = r"""param([string]$AudioPath,[string]$TextPath,[string]$Culture)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$cultureInfo = [Globalization.CultureInfo]::GetCultureInfo($Culture)
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine($cultureInfo)
$recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$recognizer.SetInputToWaveFile($AudioPath)
$result = $recognizer.Recognize()
$text = if ($null -eq $result) { '' } else { $result.Text }
$recognizer.Dispose()
[IO.File]::WriteAllText($TextPath, $text, (New-Object Text.UTF8Encoding($false)))
"""


class VoiceError(RuntimeError):
    """Typed, user-visible voice failure. Never affects the PTY lifecycle."""


@dataclass(frozen=True)
class VoiceApprovalChallenge:
    confirmation_id: str
    session_id: str
    agent_run_id: str
    operation: str
    fingerprint: str
    expires_at: float


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_APPROVAL_CHOICE = re.compile(r"^(?:[❯>›*]\s*)?\d+[.)]?\s+", re.IGNORECASE)


def approval_prompt(tail: str) -> tuple[str, str]:
    """Extract a bounded operation label and fingerprint from the current PTY screen."""
    normalized = _ANSI_ESCAPE.sub("", tail).replace("\r", "")
    lines = [re.sub(r"\s+", " ", line).strip() for line in normalized.splitlines()]
    lines = [line for line in lines if line]
    markers = ("proceed", "allow codex", "do you want", "would you like", "confirm")
    choice_index = next(
        (index for index, line in enumerate(lines) if _APPROVAL_CHOICE.match(line)),
        len(lines),
    )
    marker_indexes = [
        index
        for index, line in enumerate(lines)
        if index <= choice_index and any(marker in line.casefold() for marker in markers)
    ]
    prompt_index = max(marker_indexes, default=choice_index)
    before = lines[max(0, prompt_index - 6) : prompt_index]
    useful = [
        line for line in before
        if not _APPROVAL_CHOICE.match(line)
        and line.casefold() not in {"bash command", "tool use", "approval required"}
    ]
    operation = " ".join(useful[-3:]).strip() or "the currently highlighted operation"
    operation = operation[-400:]
    prompt_frame = [operation, *lines[prompt_index : min(len(lines), choice_index + 8)]]
    fingerprint = hashlib.sha256("\n".join(prompt_frame).encode("utf-8")).hexdigest()
    return operation, fingerprint


@dataclass(frozen=True)
class Transcription:
    """One recognized utterance plus the daemon-side stage timings behind it.

    Transcription returns timings rather than a bare string because the browser is
    the only party that can measure the whole path (it alone knows when speech
    stopped) and the daemon is the only party that can separate queueing from
    decoding. The client merges the two halves into one latency sample.
    """

    text: str
    audio_ms: float
    queue_ms: float
    decode_ms: float
    engine: str
    model: str
    beam_size: int

    def timings(self) -> dict[str, Any]:
        return {
            "audio_ms": round(self.audio_ms, 1),
            "queue_ms": round(self.queue_ms, 1),
            "decode_ms": round(self.decode_ms, 1),
            "engine": self.engine,
            "model": self.model,
            "beam_size": self.beam_size,
        }


def _finite(value: Any, *, limit: float = LATENCY_MAX_MS) -> float:
    """Clamp one browser-supplied duration into a plottable millisecond range."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return round(max(0.0, min(number, limit)), 1)


def normalize_latency_sample(raw: Any) -> dict[str, Any]:
    """Coerce a posted latency sample into the stored shape.

    The numbers arrive from the browser, so every field is clamped rather than
    trusted: a diagnostic that can be poisoned into showing impossible stages is
    worse than no diagnostic, because it is still believed.
    """
    if not isinstance(raw, dict):
        raise VoiceError("latency sample must be an object")
    sample: dict[str, Any] = {field: _finite(raw.get(field)) for field in LATENCY_FIELDS}
    utterance_id = str(raw.get("utterance_id") or "")[:100]
    sample["utterance_id"] = re.sub(r"[^A-Za-z0-9_.:-]", "", utterance_id)
    sample["at"] = time.time()
    sample["speculative"] = bool(raw.get("speculative"))
    sample["command"] = str(raw.get("command") or "")[:40]
    sample["model"] = str(raw.get("model") or "")[:120]
    sample["engine"] = str(raw.get("engine") or "")[:40]
    return sample


def latency_stages(sample: dict[str, Any]) -> dict[str, float]:
    """The four reported stages, summed from the recorded fields."""
    return {
        stage: round(sum(float(sample.get(field) or 0.0) for field in fields), 1)
        for stage, fields in LATENCY_STAGES.items()
    }


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile. Exact on the tiny sample counts voice produces."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 1)


def latency_report(samples: Sequence[dict[str, Any]], *, recent: int = 20) -> dict[str, Any]:
    """Per-stage p50/p95/max plus the newest raw samples.

    Percentiles rather than a mean: one cold model load is a 7-second outlier that
    would drag a mean far away from anything the user ever experiences.
    """
    stages = [latency_stages(sample) for sample in samples]
    summary = {
        stage: {
            "p50": percentile([item[stage] for item in stages], 0.5),
            "p95": percentile([item[stage] for item in stages], 0.95),
            "max": round(max((item[stage] for item in stages), default=0.0), 1),
        }
        for stage in LATENCY_STAGES
    }
    totals = [float(sample.get("total_ms") or 0.0) for sample in samples]
    commands = [
        float(sample.get("total_ms") or 0.0) for sample in samples if sample.get("command")
    ]
    return {
        "count": len(samples),
        "stages": summary,
        "total_ms": {
            "p50": percentile(totals, 0.5),
            "p95": percentile(totals, 0.95),
            "max": round(max(totals, default=0.0), 1),
        },
        # The exit criterion is stated for a short command, not for dictation, so
        # the command-only total is the number to read it against.
        "command_total_ms": {
            "count": len(commands),
            "p50": percentile(commands, 0.5),
            "p95": percentile(commands, 0.95),
        },
        "recent": [
            {**sample, "stages": latency_stages(sample)} for sample in list(samples)[-recent:]
        ],
    }


def speechify(text: str, max_chars: int) -> str:
    """Reduce a markdown agent reply to something listenable for verbatim mode."""
    text = re.sub(r"```.*?```", " Code block omitted. ", text, flags=re.S)
    text = re.sub(r"```.*$", " Code block omitted. ", text, flags=re.S)
    text = re.sub(r"`([^`\n]{1,80})`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " image ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"^\s*\|.*\|\s*$", "", text, flags=re.M)
    text = re.sub(r"^[-=|:\s]{4,}$", "", text, flags=re.M)
    text = re.sub(r"https?://\S+", " link ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0]
        text = f"{cut} … reply truncated."
    return text


def estimate_duration_seconds(text: str, rate: str) -> float:
    words = max(1, len(text.split()))
    per_second = 2.6
    match = re.fullmatch(r"([+-]\d{1,3})%", rate)
    if match:
        per_second *= 1 + int(match.group(1)) / 100
    return round(words / max(per_second, 0.5), 1)


def _bounded_speech_chunks(text: str, max_chars: int) -> list[str]:
    """Split normalized speech at sentence boundaries, then at words."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        words = sentence.split()
        pieces: list[str] = []
        piece = ""
        for word in words:
            candidate = f"{piece} {word}".strip()
            if piece and len(candidate) > max_chars:
                pieces.append(piece)
                piece = word
            else:
                piece = candidate
        if piece:
            pieces.append(piece)
        for item in pieces:
            candidate = f"{current} {item}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = item
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def streaming_segments(text: str, max_chars: int = 420) -> list[str]:
    """Split speakable text into short independently playable clips.

    Auto read-aloud emits each clip as soon as its synthesis finishes. A reply
    that already fits in one ordinary clip stays whole. Longer replies lead with
    one complete sentence whenever possible, because a low-latency cut in the
    middle of a thought sounds like a second, unrelated response when playback
    advances to the continuation. Only sentences longer than the clip bound fall
    back to word chunks.
    """
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    bound = max(80, max_chars)
    if len(cleaned) <= bound:
        return [cleaned]
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    first = _bounded_speech_chunks(first_sentence, bound)[0]
    remainder = cleaned[len(first):].strip()
    return [first, *_bounded_speech_chunks(remainder, bound)]


class VoiceStore:
    """SQLite persistence for generated clips, one dedicated worker thread.

    Mirrors HistoryIndex's confinement: every sqlite3 call runs on the single
    executor thread so nothing blocks the aiohttp event loop.
    """

    _db: sqlite3.Connection

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._operation_lock = database_operation_lock(path)
        self._closed = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-voice-db")
        self._executor.submit(self._connect).result()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self._path, self._open)
            self._db.executescript(SCHEMA)
            self._db.commit()

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    async def add_clip(self, row: dict[str, Any]) -> None:
        def op() -> None:
            self._db.execute(
                # Named columns, not positional: adding a column would otherwise
                # break every insert made by a rolled-back previous bundle.
                "INSERT INTO voice_clips"
                "(id,session_id,agent_run_id,created_at,trigger,content_mode,engine,voice,"
                "text,file_path,format,size_bytes,duration_hint_s,status,error,model,"
                "input_tokens,output_tokens,cost_usd) VALUES("
                ":id,:session_id,:agent_run_id,:created_at,:trigger,:content_mode,"
                ":engine,:voice,:text,:file_path,:format,:size_bytes,:duration_hint_s,"
                ":status,:error,:model,:input_tokens,:output_tokens,:cost_usd)",
                row,
            )
            self._db.commit()

        await self._run(op)

    async def clip(self, clip_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute("SELECT * FROM voice_clips WHERE id=?", (clip_id,)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    async def clips(
        self,
        *,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM voice_clips"
        clauses: list[str] = []
        args: list[Any] = []
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if agent_run_id:
            clauses.append("agent_run_id=?")
            args.append(agent_run_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))

        def op() -> list[dict[str, Any]]:
            return [dict(row) for row in self._db.execute(sql, args).fetchall()]

        return await self._run(op)

    async def delete_clip(self, clip_id: str) -> str | None:
        def op() -> str | None:
            row = self._db.execute(
                "SELECT file_path FROM voice_clips WHERE id=?", (clip_id,)
            ).fetchone()
            if row is None:
                return None
            self._db.execute("DELETE FROM voice_clips WHERE id=?", (clip_id,))
            self._db.commit()
            return str(row["file_path"])

        return await self._run(op)

    async def cache_stats(self) -> dict[str, int]:
        def op() -> dict[str, int]:
            row = self._db.execute(
                "SELECT COUNT(*) count, COALESCE(SUM(size_bytes),0) bytes FROM voice_clips "
                "WHERE status='ready'"
            ).fetchone()
            return {"count": int(row["count"]), "bytes": int(row["bytes"])}

        return await self._run(op)

    async def clip_ids(self) -> set[str]:
        def op() -> set[str]:
            return {
                str(row["id"]) for row in self._db.execute("SELECT id FROM voice_clips").fetchall()
            }

        return await self._run(op)

    async def prune(self, max_bytes: int) -> list[str]:
        """Drop stale failures and the oldest ready clips beyond the byte cap.
        Returns file paths whose backing audio should be removed."""

        def op() -> list[str]:
            day_ago = time.time() - 24 * 3600
            self._db.execute(
                "DELETE FROM voice_clips WHERE status='failed' AND created_at<?", (day_ago,)
            )
            removed: list[str] = []
            total = int(
                self._db.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM voice_clips WHERE status='ready'"
                ).fetchone()[0]
            )
            if total > max_bytes:
                for row in self._db.execute(
                    "SELECT id, file_path, size_bytes FROM voice_clips "
                    "WHERE status='ready' ORDER BY created_at ASC"
                ).fetchall():
                    if total <= max_bytes:
                        break
                    self._db.execute("DELETE FROM voice_clips WHERE id=?", (row["id"],))
                    removed.append(str(row["file_path"]))
                    total -= int(row["size_bytes"])
            self._db.commit()
            return removed

        return await self._run(op)

    def close(self) -> None:
        # Idempotent like every sibling store: a second close would otherwise
        # raise "cannot schedule new futures after shutdown" from the executor,
        # and in the server's shutdown sequence that skips the remaining closes
        # and the clean-exit marker, making the next start report a false
        # unclean-death forensic.
        if self._closed:
            return
        self._closed = True
        self._executor.submit(self._db.close).result()
        self._executor.shutdown(wait=True)


def clip_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    public = dict(row)
    public.pop("file_path", None)
    return public


class VoiceService:
    """Consumes turn_ended events for marked sessions and produces audio clips."""

    def __init__(
        self,
        config: Config,
        events: EventBus,
        sessions: SessionManager,
        store: VoiceStore,
        automation_store: AutomationStore,
        provider: OpenRouterClient,
        kokoro_models: KokoroModelStore | None = None,
    ) -> None:
        self.config = config
        self.events = events
        self.sessions = sessions
        self.store = store
        self.automation_store = automation_store
        self.provider = provider
        self.kokoro_models = kokoro_models or KokoroModelStore(config.data_dir)
        self._kokoro_engine: KokoroEngine | None = None
        self.diagnostic: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._debounce: dict[str, asyncio.Task[None]] = {}
        self._segment_tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._engine_semaphore = asyncio.Semaphore(2)
        self._sapi_script_path: Path | None = None
        self._sapi_stt_script_path: Path | None = None
        # One lock per decode profile, not one for transcription: a speculative
        # routing pass overlaps the real utterance by design, and a shared lock would
        # turn that overlap into exactly the delay speculation exists to remove.
        self._stt_locks = {profile: asyncio.Lock() for profile in DECODE_PROFILES}
        self._whisper_models: dict[str, Any] = {}
        self._whisper_devices: dict[str, str] = {}
        self._submitted_ids: deque[str] = deque(maxlen=512)
        self._submitted_id_set: set[str] = set()
        self._stt_latency: deque[dict[str, Any]] = deque(maxlen=STT_LATENCY_SAMPLES)
        self._approval_challenges: dict[str, VoiceApprovalChallenge] = {}

    @property
    def clip_directory(self) -> Path:
        return self.config.data_dir / "voice"

    def effective_mode(self, record: Any) -> str:
        mode = getattr(record, "voice_mode", None)
        if mode in VOICE_MODES:
            return str(mode)
        return self.config.tts_default_mode if self.config.tts_enabled else "off"

    def effective_content(self, record: Any) -> str:
        content = getattr(record, "voice_content", None)
        if content in {"summary", "verbatim"}:
            return str(content)
        return self.config.tts_content

    def claim_submission(self, utterance_id: str) -> bool:
        """Idempotency gate for reconnect-safe voice prompt commits."""
        if utterance_id in self._submitted_id_set:
            return False
        if len(self._submitted_ids) == self._submitted_ids.maxlen:
            expired = self._submitted_ids.popleft()
            self._submitted_id_set.discard(expired)
        self._submitted_ids.append(utterance_id)
        self._submitted_id_set.add(utterance_id)
        return True

    def prepare_approval(
        self, session_id: str, agent_run_id: str, operation: str, fingerprint: str
    ) -> VoiceApprovalChallenge:
        challenge = VoiceApprovalChallenge(
            confirmation_id=str(uuid.uuid4()),
            session_id=session_id,
            agent_run_id=agent_run_id,
            operation=operation,
            fingerprint=fingerprint,
            expires_at=time.time() + VOICE_APPROVAL_TTL_SECONDS,
        )
        self._approval_challenges[session_id] = challenge
        return challenge

    def consume_approval(
        self,
        session_id: str,
        confirmation_id: str,
        agent_run_id: str,
        fingerprint: str,
    ) -> VoiceApprovalChallenge:
        challenge = self._approval_challenges.pop(session_id, None)
        if challenge is None or challenge.confirmation_id != confirmation_id:
            raise VoiceError("voice approval confirmation is missing or was already used")
        if challenge.expires_at < time.time():
            raise VoiceError("voice approval confirmation expired")
        if challenge.agent_run_id != agent_run_id or challenge.fingerprint != fingerprint:
            raise VoiceError("the approval prompt changed; review it again")
        return challenge

    def cancel_approval(self, session_id: str) -> None:
        self._approval_challenges.pop(session_id, None)

    def record_stt_latency(self, raw: Any) -> dict[str, Any]:
        """Store one end-to-end latency sample and log it durably.

        The ring is for the Settings readout; `daemon.log` is the record that
        outlives a daemon restart, which is what makes a "voice felt slow an hour
        ago" report answerable at all.
        """
        sample = normalize_latency_sample(raw)
        self._stt_latency.append(sample)
        log.info(
            "voice stt latency %s",
            json.dumps({**sample, "stages": latency_stages(sample)}, sort_keys=True),
        )
        return sample

    def record_barge_in_diagnostic(self, raw: Any) -> dict[str, Any]:
        """Validate and durably log one browser-side playback speech probe."""
        if not isinstance(raw, dict):
            raise VoiceError("barge-in diagnostic must be an object")
        outcome = str(raw.get("outcome") or "")
        detector = str(raw.get("detector") or "")
        origin_value = raw.get("origin")
        origin = None if origin_value is None else str(origin_value)
        if outcome not in {"confirmed", "rejected"}:
            raise VoiceError("barge-in outcome must be confirmed or rejected")
        if detector not in {"silero", "energy"}:
            raise VoiceError("barge-in detector must be silero or energy")
        if origin not in {None, "agent", "system"}:
            raise VoiceError("barge-in origin must be agent, system, or null")

        def bounded(value: Any) -> float:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return 0.0
            if not math.isfinite(number):
                return 0.0
            return round(max(0.0, min(number, 1.0)), 4)

        sample: dict[str, Any] = {
            "outcome": outcome,
            "detector": detector,
            "origin": origin,
            "peak_probability": bounded(raw.get("peakProbability")),
            "peak_rms": bounded(raw.get("peakRms")),
        }
        log.info("voice barge-in %s", json.dumps(sample, sort_keys=True))
        return sample

    def stt_latency_report(self) -> dict[str, Any]:
        return latency_report(list(self._stt_latency))

    def clear_stt_latency(self) -> None:
        self._stt_latency.clear()

    def start(self) -> None:
        if self._task:
            return
        self._queue = self.events.subscribe(name="voice")
        self._task = background.start(VOICE_EVENT_LOOP, self._drain)

    async def stop(self) -> None:
        await background.stop(VOICE_EVENT_LOOP)
        for task in self._debounce.values():
            task.cancel()
        pending = list(self._debounce.values())
        self._debounce.clear()
        for task in self._segment_tasks:
            task.cancel()
        pending.extend(self._segment_tasks)
        self._segment_tasks.clear()
        if self._queue:
            self.events.unsubscribe(self._queue)
            self._queue = None
        self._task = None
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _drain(self) -> None:
        assert self._queue is not None
        while True:
            event = await self._queue.get()
            try:
                with background.iteration(VOICE_EVENT_LOOP):
                    self._consider(event)
            finally:
                self._queue.task_done()

    def _consider(self, event: Any) -> None:
        if event.type in {"session_exited", "session_crashed"} and event.session_id:
            # One lock per session ever seen is unbounded on a daemon designed to
            # run for weeks; drop it with the session it belongs to.
            lock = self._locks.get(event.session_id)
            if lock is not None and not lock.locked():
                self._locks.pop(event.session_id, None)
            return
        if event.type != "turn_ended" or not self.config.tts_enabled:
            return
        session_id = event.session_id
        if not session_id:
            return
        session = self.sessions.sessions.get(session_id)
        if not session or not has_observable_transcript(session.record.backend):
            return
        if self.effective_mode(session.record) != "auto":
            return
        previous = self._debounce.pop(session_id, None)
        if previous:
            previous.cancel()
        self._debounce[session_id] = asyncio.create_task(
            self._debounced(session_id), name=f"voice-debounce-{session_id}"
        )

    async def _debounced(self, session_id: str) -> None:
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        self._debounce.pop(session_id, None)
        try:
            await self.generate(session_id, trigger="auto")
        except VoiceError:
            pass  # the failure is already recorded and emitted

    async def generate(
        self,
        session_id: str,
        *,
        trigger: str,
        content_mode: str | None = None,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        session = self.sessions.sessions.get(session_id)
        if not session:
            raise VoiceError("session is not live")
        record = session.record
        if not has_observable_transcript(record.backend):
            raise VoiceError("read aloud requires an observable agent session")
        if not session.transcript_path or not session.transcript_path.exists():
            raise VoiceError("the agent transcript is not available yet")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() and trigger == "auto":
            return {}  # a clip for this session is already being generated
        async with lock:
            stream_id = self._stream_id(stream_id)
            if content_mode is not None and content_mode not in {"summary", "verbatim"}:
                raise VoiceError("content mode must be summary or verbatim")
            selected_content = content_mode or self.effective_content(record)
            row = self._new_clip_row(
                session_id, trigger, record.agent_run_id, selected_content
            )
            try:
                spoken = await self._spoken_text(session, row, selected_content)
            except (VoiceError, OpenRouterError, TimeoutError, OSError) as exc:
                message = str(exc)[:500] or exc.__class__.__name__
                row["error"] = message
                self.diagnostic = message
                log.warning(
                    "voice reply preparation failed session=%s run=%s "
                    "trigger=%s content=%s error=%s",
                    session_id,
                    record.agent_run_id,
                    trigger,
                    selected_content,
                    message,
                )
                await self.store.add_clip(row)
                await self.events.emit(
                    "voice_clip_failed",
                    session_id=session_id,
                    source="daemon",
                    clip_id=row["id"],
                    trigger=trigger,
                    error=message,
                )
                raise VoiceError(message) from exc
            segments = streaming_segments(spoken)
            if not segments:
                raise VoiceError("nothing speakable remained after preprocessing")
            await self._synthesize_stream_segment(
                row, segments[0], session_id=session_id, agent_run_id=record.agent_run_id,
                trigger=trigger, stream_id=stream_id, index=0, count=len(segments),
            )
            first = clip_snapshot(row)
            first["stream_id"] = stream_id
            first["segment_count"] = len(segments)
            if len(segments) > 1:
                self._start_segment_tail(
                    session_id=session_id, agent_run_id=record.agent_run_id,
                    trigger=trigger, content_mode=selected_content, model=row["model"],
                    stream_id=stream_id, segments=segments[1:], total=len(segments),
                )
            else:
                await self._prune()
            log.info(
                "voice reply first segment ready session=%s run=%s clip=%s "
                "trigger=%s content=%s segments=%d",
                session_id, record.agent_run_id, first["id"], trigger,
                selected_content, len(segments),
            )
            return first

    async def speak(self, text: str, *, stream_id: str | None = None) -> dict[str, Any]:
        """Synthesize trusted application text without involving a language model."""
        if not self.config.tts_enabled:
            raise VoiceError("read aloud is off")
        spoken = re.sub(r"\s+", " ", text).strip()
        if not spoken or len(spoken) > 2_000:
            raise VoiceError("system speech must contain 1-2000 characters")
        if any(ord(character) < 32 for character in spoken):
            raise VoiceError("system speech contains control characters")
        stream_id = self._stream_id(stream_id)
        segments = streaming_segments(spoken)
        row = self._new_clip_row("system", "system", None, "verbatim")
        await self._synthesize_stream_segment(
            row, segments[0], session_id="system", agent_run_id=None, trigger="system",
            stream_id=stream_id, index=0, count=len(segments),
        )
        first = clip_snapshot(row)
        first["stream_id"] = stream_id
        first["segment_count"] = len(segments)
        if len(segments) > 1:
            self._start_segment_tail(
                session_id="system", agent_run_id=None, trigger="system",
                content_mode="verbatim", model=None, stream_id=stream_id,
                segments=segments[1:], total=len(segments),
            )
        else:
            await self._prune()
        log.info(
            "voice system first segment ready clip=%s characters=%d segments=%d",
            row["id"], len(spoken), len(segments),
        )
        return first

    @staticmethod
    def _stream_id(requested: str | None) -> str:
        if requested is None:
            return str(uuid.uuid4())
        try:
            parsed = uuid.UUID(requested)
        except (ValueError, AttributeError) as exc:
            raise VoiceError("stream_id must be a UUID") from exc
        return str(parsed)

    async def _synthesize_stream_segment(
        self,
        row: dict[str, Any],
        spoken: str,
        *,
        session_id: str,
        agent_run_id: str | None,
        trigger: str,
        stream_id: str,
        index: int,
        count: int,
    ) -> None:
        try:
            await self._synthesize_clip(row, spoken)
        except (VoiceError, TimeoutError, OSError) as exc:
            message = str(exc)[:500] or exc.__class__.__name__
            row["error"] = message
            self.diagnostic = message
            await self.store.add_clip(row)
            await self.events.emit(
                "voice_clip_failed", session_id=session_id, source="daemon",
                clip_id=row["id"], trigger=trigger, stream_id=stream_id,
                segment_index=index, segment_count=count, error=message,
            )
            log.warning(
                "voice stream synthesis failed session=%s run=%s trigger=%s "
                "segment=%d/%d error=%s",
                session_id, agent_run_id, trigger, index + 1, count, message,
            )
            raise VoiceError(message) from exc
        await self.store.add_clip(row)
        await self.events.emit(
            "voice_clip_ready", session_id=session_id, source="daemon",
            clip_id=row["id"], agent_run_id=agent_run_id, trigger=trigger,
            stream_id=stream_id, segment_index=index, segment_count=count,
        )

    def _start_segment_tail(
        self,
        *,
        session_id: str,
        agent_run_id: str | None,
        trigger: str,
        content_mode: str,
        model: str | None,
        stream_id: str,
        segments: list[str],
        total: int,
    ) -> None:
        task = asyncio.create_task(
            self._generate_segment_tail(
                session_id=session_id, agent_run_id=agent_run_id, trigger=trigger,
                content_mode=content_mode, model=model, stream_id=stream_id,
                segments=segments, total=total,
            ),
            name=f"voice-segments-{stream_id}",
        )
        self._segment_tasks.add(task)
        task.add_done_callback(self._segment_tail_done)

    def _segment_tail_done(self, task: asyncio.Task[None]) -> None:
        self._segment_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            log.error("voice segment task failed task=%s", task.get_name(), exc_info=error)

    async def _generate_segment_tail(
        self,
        *,
        session_id: str,
        agent_run_id: str | None,
        trigger: str,
        content_mode: str,
        model: str | None,
        stream_id: str,
        segments: list[str],
        total: int,
    ) -> None:
        for offset, segment in enumerate(segments, start=1):
            row = self._new_clip_row(session_id, trigger, agent_run_id, content_mode)
            row["model"] = model
            try:
                await self._synthesize_stream_segment(
                    row, segment, session_id=session_id, agent_run_id=agent_run_id,
                    trigger=trigger, stream_id=stream_id, index=offset, count=total,
                )
            except VoiceError:
                return
        await self._prune()
        log.info(
            "voice stream complete session=%s run=%s trigger=%s stream=%s segments=%d",
            session_id, agent_run_id, trigger, stream_id, total,
        )

    def _new_clip_row(
        self, session_id: str, trigger: str, agent_run_id: str | None, content_mode: str
    ) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "agent_run_id": agent_run_id,
            "created_at": time.time(),
            "trigger": trigger,
            "content_mode": content_mode,
            "engine": self.config.tts_engine,
            "voice": self._voice_label(),
            "text": "",
            "file_path": "",
            # Both engines write WAV now; MP3 rows from removed edge-tts clips
            # remain readable because format is stored per row.
            "format": "wav",
            "size_bytes": 0,
            "duration_hint_s": None,
            "status": "failed",
            "error": None,
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
        }

    async def _synthesize_clip(self, row: dict[str, Any], spoken: str) -> None:
        row["text"] = spoken
        destination = self.clip_directory / f"{row['id']}.{row['format']}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._engine_semaphore:
                await asyncio.wait_for(
                    self._synthesize(spoken, destination), timeout=ENGINE_TIMEOUT_SECONDS
                )
        except BaseException:
            # The failed row is stored with file_path="", so a partial file left
            # here has no row pointing at it and the byte-cap prune — which only
            # walks row-listed paths — can never find it again.
            with suppress(OSError):
                destination.unlink(missing_ok=True)
            raise
        row["file_path"] = str(destination)
        row["size_bytes"] = destination.stat().st_size
        row["duration_hint_s"] = (
            wav_duration_seconds(destination) or estimate_duration_seconds(spoken, "+0%")
        )
        row["status"] = "ready"
        self.diagnostic = None

    async def _spoken_text(
        self, session: Any, row: dict[str, Any], content_mode: str
    ) -> str:
        # The same segment "Copy reply" puts on the clipboard and the reader tab
        # shows as the last agent message. Speaking a different span than the one
        # on screen is how a listener ends up hearing "I'll investigate the
        # sidebar sort" as the answer to a question that was already answered.
        prompt, reply = await asyncio.to_thread(
            _last_exchange,
            session.transcript_path,
            session.record.backend,
            session.record.native_session_id,
        )
        if not reply:
            raise VoiceError("no assistant reply text was found in the last turn")
        if content_mode == "verbatim":
            return speechify(reply, self.config.tts_verbatim_max_chars)
        # The summariser still sees what the reply was answering. Without the
        # prompt it has to guess the subject, and a spoken update that opens by
        # restating the wrong question is worse than a long one.
        rendered = f"user: {prompt}\nassistant: {reply}" if prompt else f"assistant: {reply}"
        encoded = rendered.encode()
        model = self.config.tts_summary_model or self.config.openrouter_cheap_model
        if not model:
            raise VoiceError(
                "spoken summaries need an OpenRouter model: set the voice summary model "
                "or the automation cheap model in Settings, or switch content to verbatim"
            )
        spend = await self.automation_store.spend(rule_id=VOICE_RULE_ID)
        if float(spend["cost_usd"]) >= self.config.tts_daily_budget_usd:
            raise VoiceError("the daily read-aloud summary budget is exhausted")
        call_id = await self.automation_store.observer_started(
            firing_id=f"voice:{row['id']}",
            rule_id=VOICE_RULE_ID,
            model=model,
            input_hash=hashlib.sha256(encoded).hexdigest(),
            input_bytes=len(encoded),
        )
        try:
            completion = await self.provider.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": rendered},
                ],
                schema_name="voice_speech",
                schema=SUMMARY_SCHEMA,
                max_tokens=self.config.tts_summary_max_tokens,
            )
        except asyncio.CancelledError:
            await self.automation_store.observer_finished(
                call_id, status="cancelled", error="cancelled"
            )
            raise
        except OpenRouterError as exc:
            await self.automation_store.observer_finished(
                call_id, status="failed", error=str(exc)[:1000]
            )
            raise
        await self.automation_store.observer_finished(
            call_id,
            status="completed",
            resolved_model=completion.resolved_model,
            generation_id=completion.generation_id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
        )
        await self.automation_store.add_spend(
            rule_id=VOICE_RULE_ID,
            model=completion.resolved_model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=completion.cost_usd or 0,
            call_id=call_id,
        )
        row["model"] = completion.resolved_model
        row["input_tokens"] = completion.input_tokens
        row["output_tokens"] = completion.output_tokens
        row["cost_usd"] = completion.cost_usd
        speech = str(completion.value.get("speech") or "").strip()
        if not speech:
            raise VoiceError("the summary model returned empty speech text")
        return speech

    def _voice_label(self) -> str:
        if self.config.tts_engine == "kokoro":
            return self.config.tts_kokoro_voice
        return self.config.tts_sapi_voice or "system default"

    async def _synthesize(self, text: str, destination: Path) -> None:
        if self.config.tts_engine == "kokoro":
            await self._synthesize_kokoro(text, destination)
        else:
            await self._synthesize_sapi(text, destination)

    def _ensure_kokoro(self) -> KokoroEngine:
        """The loaded Kokoro session, constructed once per daemon.

        Construction refuses when the pinned model is not `ready`: a partial
        download must never be loadable, and the settings surface owns the
        download with visible progress rather than this path fetching silently.
        """
        if not self.kokoro_models.ready():
            raise VoiceError(
                "the Kokoro voice model is not downloaded; download it in "
                "Settings → Voice, or switch the engine to the OS voice"
            )
        if self._kokoro_engine is None:
            install = self.kokoro_models.install
            try:
                self._kokoro_engine = KokoroEngine(
                    KokoroPaths(
                        model=install.model,
                        tokenizer=install.tokenizer,
                        voices_dir=install.voices_dir,
                    )
                )
            except KokoroError as exc:
                raise VoiceError(str(exc)) from exc
        return self._kokoro_engine

    async def _synthesize_kokoro(self, text: str, destination: Path) -> None:
        engine = self._ensure_kokoro()
        if not text.strip():
            raise VoiceError("nothing speakable remained after preprocessing")
        voice = self.config.tts_kokoro_voice
        speed = max(0.5, min(2.0, self.config.tts_kokoro_speed))
        try:
            await asyncio.to_thread(
                engine.synthesize_wav, text, destination, voice_id=voice, speed=speed
            )
        except KokoroError as exc:
            raise VoiceError(f"Kokoro synthesis failed: {str(exc)[:300]}") from exc

    async def _synthesize_sapi(self, text: str, destination: Path) -> None:
        script = self._ensure_sapi_script()
        text_path = destination.with_suffix(".txt")
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8-sig")
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-TextPath",
            str(text_path),
            "-OutPath",
            str(destination),
            "-Rate",
            str(self.config.tts_sapi_rate),
        ]
        if self.config.tts_sapi_voice:
            command += ["-Voice", self.config.tts_sapi_voice]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                timeout=ENGINE_TIMEOUT_SECONDS,
                creationflags=background_creation_flags(),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise VoiceError(f"Windows SAPI synthesis failed: {exc}") from exc
        finally:
            text_path.unlink(missing_ok=True)
        if result.returncode != 0 or not destination.exists() or not destination.stat().st_size:
            detail = (result.stderr or result.stdout or "unknown SAPI error").strip()
            raise VoiceError(f"Windows SAPI synthesis failed: {detail[:300]}")

    def _ensure_sapi_script(self) -> Path:
        if self._sapi_script_path and self._sapi_script_path.exists():
            return self._sapi_script_path
        path = self.clip_directory / "sapi_tts.ps1"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SAPI_SCRIPT, encoding="utf-8")
        self._sapi_script_path = path
        return path

    def _sweep_stale_utterances(self, directory: Path) -> None:
        """Delete temporary utterances an abandoned transcription left behind.

        A timed-out worker keeps its WAV open past the request, so the unlink in
        `transcribe_wav` can lose the race and there is no other sweeper for this
        directory. Anything older than the timeout window cannot belong to a live
        request.
        """
        cutoff = time.time() - STT_TIMEOUT_SECONDS * 4
        try:
            entries = list(directory.iterdir())
        except OSError:
            return
        for item in entries:
            try:
                if item.is_file() and item.stat().st_mtime < cutoff:
                    item.unlink(missing_ok=True)
            except OSError:
                continue

    async def transcribe_wav(
        self,
        audio: bytes,
        *,
        received_at: float | None = None,
        correlation_id: str = "",
        profile: str = DICTATION_PROFILE,
    ) -> Transcription:
        """Transcribe one bounded speech utterance without retaining its audio.

        `received_at` is the caller's `time.perf_counter()` at request entry, so the
        reported queue cost covers the body read and the lock wait rather than only
        the part of the path this method can see.

        `profile` selects which decoder answers. The two exist because the latency a
        spoken command can tolerate and the accuracy dictated prose needs are
        different problems: a routing pass wants the small English model and greedy
        search, while dictation wants the larger model. They hold separate locks, so
        a speculative routing decode can never queue the real utterance behind it.
        """
        entry = time.perf_counter() if received_at is None else received_at
        if not self.config.stt_enabled:
            raise VoiceError("microphone transcription is disabled in Settings")
        audio_ms, frames = self._validate_wav(audio)
        profile = profile if profile in DECODE_PROFILES else DICTATION_PROFILE
        utterance_id = correlation_id or str(uuid.uuid4())
        marks: dict[str, Any] = {}
        async with self._stt_locks[profile]:
            if self.config.stt_engine == "whisper":
                try:
                    text = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._transcribe_whisper, frames, audio_ms, profile, marks
                        ),
                        timeout=STT_TIMEOUT_SECONDS,
                    )
                except TimeoutError as exc:
                    # `asyncio.to_thread` cannot be cancelled, so the abandoned worker
                    # keeps decoding; nothing here waits on it, and because the audio
                    # never left memory there is no file for it to hold open either.
                    raise VoiceError("transcription timed out; try a shorter utterance") from exc
            else:
                text = await self._transcribe_sapi(audio, marks)
            normalized = re.sub(r"\s+", " ", text).strip()
            if not normalized:
                raise VoiceError("no speech was recognized")
            now = time.perf_counter()
            decode_start = marks.get("decode_start", now)
            decode_end = marks.get("decode_end", now)
            result = Transcription(
                text=normalized[:20_000],
                audio_ms=audio_ms,
                queue_ms=max(0.0, (decode_start - entry) * 1000),
                decode_ms=max(0.0, (decode_end - decode_start) * 1000),
                engine=self.config.stt_engine,
                # Both read back from the decoder rather than recomputed here, so the
                # reported model and beam can never drift from the ones used.
                model=str(marks.get("model", "system")),
                beam_size=int(marks.get("beam_size", 0)),
            )
            log.info(
                "voice stt decode id=%s profile=%s audio=%.0fms queue=%.0fms decode=%.0fms "
                "engine=%s model=%s beam=%d chars=%d",
                utterance_id,
                profile,
                result.audio_ms,
                result.queue_ms,
                result.decode_ms,
                result.engine,
                result.model,
                result.beam_size,
                len(result.text),
            )
            return result

    def decode_model(self, profile: str) -> str:
        """Which Whisper model a profile decodes with.

        The routing model is optional: an empty setting, or one that fails to load,
        means commands decode on the dictation model. That is slower but correct,
        which is the right way for an optimisation to fail.
        """
        if profile == COMMAND_PROFILE and self.config.stt_routing_model.strip():
            return self.config.stt_routing_model.strip()
        return self.config.stt_whisper_model

    def beam_size(self, profile: str, audio_ms: float) -> int:
        """Greedy for short audio, beam search only where it can pay for itself.

        Beam search costs roughly 10% on a one-second command and 30% on a long
        dictation, and buys accuracy that only shows up in the longer text. Routing
        never uses it: the grammar it feeds is a closed set of short phrases.
        """
        if profile == COMMAND_PROFILE:
            return 1
        return 1 if audio_ms <= STT_GREEDY_MAX_MS else 5

    @staticmethod
    def _validate_wav(audio: bytes) -> tuple[float, bytes]:
        """Reject anything outside the capture contract.

        Returns the audio length and its raw PCM frames. Decoding here is what lets
        transcription run from memory: the utterance never reaches the disk, so "no
        audio is retained" holds by construction rather than by a cleanup sweep that
        could lose a race.
        """
        if not audio or len(audio) > STT_MAX_BYTES:
            raise VoiceError("voice utterance must be between 1 byte and 2 MiB")
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                channels = source.getnchannels()
                width = source.getsampwidth()
                rate = source.getframerate()
                count = source.getnframes()
                frames = source.readframes(count)
        except (EOFError, wave.Error) as exc:
            raise VoiceError("voice utterance must be a valid WAV file") from exc
        if channels != 1 or width != 2 or rate != STT_SAMPLE_RATE:
            raise VoiceError(f"voice WAV must be mono 16-bit PCM at {STT_SAMPLE_RATE} Hz")
        if count <= 0 or count / rate > STT_MAX_SECONDS:
            raise VoiceError("voice utterance must be no longer than 35 seconds")
        return count / rate * 1000, frames

    async def _transcribe_sapi(self, audio: bytes, marks: dict[str, Any]) -> str:
        """Windows `System.Speech`, the legacy engine.

        The only path that still writes the utterance to disk, because the recognizer
        takes a file and nothing else. It is also the only reason the stale-utterance
        sweep survives: the Whisper path decodes from memory and leaves nothing behind.
        """
        if os.name != "nt" or not shutil.which("powershell.exe"):
            raise VoiceError("Windows Speech Recognition is available only on Windows")
        script = self._ensure_sapi_stt_script()
        directory = self.clip_directory / "stt"
        directory.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._sweep_stale_utterances, directory)
        utterance_id = str(uuid.uuid4())
        audio_path = directory / f"{utterance_id}.wav"
        text_path = directory / f"{utterance_id}.txt"
        await asyncio.to_thread(audio_path.write_bytes, audio)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-AudioPath",
            str(audio_path),
            "-TextPath",
            str(text_path),
            "-Culture",
            self.config.stt_language,
        ]
        marks["decode_start"] = time.perf_counter()
        marks["model"] = "system"
        marks["beam_size"] = 0
        try:
            try:
                result = await asyncio.to_thread(
                    subprocess.run,
                    command,
                    capture_output=True,
                    text=True,
                    timeout=STT_TIMEOUT_SECONDS,
                    creationflags=background_creation_flags(),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                raise VoiceError(f"Windows speech recognition failed: {exc}") from exc
            if result.returncode != 0 or not text_path.exists():
                detail = (result.stderr or result.stdout or "recognizer unavailable").strip()
                raise VoiceError(f"Windows speech recognition failed: {detail[:300]}")
            return text_path.read_text(encoding="utf-8")
        finally:
            marks["decode_end"] = time.perf_counter()
            for temporary in (audio_path, text_path):
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _hotwords(self, profile: str) -> str:
        """Recognition bias.

        The routing pass adds the configured **wake words** and nothing else. A
        made-up trigger word has no business appearing in a general model's training
        data, so biasing toward it is worth real accuracy; the command phrases are
        ordinary English the model already knows.

        Adding those phrases too was measured and reverted. Feeding the default set
        of 57 short, near-identical phrases ("send it", "send that", "send message")
        drove `small.en` into a repetition loop — 1530 ms on a 1.6 s utterance and
        3035 ms on a long one, against 94 ms with the wake words alone, and the text
        came back as "mux, send, send message, send message, send message". Hence
        the cap as well: `voice_wake_words` allows 64 entries, and 64 short tokens
        is exactly that failure shape.
        """
        if profile != COMMAND_PROFILE:
            return STT_HOTWORDS
        spoken = (str(item).strip() for item in self.config.voice_wake_words)
        bounded = [*dict.fromkeys(word for word in spoken if word)][:STT_ROUTING_HOTWORD_LIMIT]
        return ", ".join([*bounded, STT_HOTWORDS])

    def _transcribe_whisper(
        self, frames: bytes, audio_ms: float, profile: str, marks: dict[str, Any]
    ) -> str:
        try:
            import numpy
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise VoiceError(
                "faster-whisper is not installed; reinstall/sync swe-mux dependencies "
                "or select Windows Speech Recognition in Settings"
            ) from exc
        name = self._ensure_whisper_model(WhisperModel, profile)
        # int16 PCM straight from the validated WAV header. `astype` copies, so the
        # read-only buffer view never reaches the decoder.
        samples = numpy.frombuffer(frames, dtype=numpy.int16).astype(numpy.float32) / 32768.0
        try:
            return self._run_whisper_transcription(name, samples, audio_ms, profile, marks)
        except Exception as first_error:
            if self._whisper_devices.get(name) != "cuda":
                raise VoiceError(
                    f"local Whisper transcription failed: {str(first_error)[:300]}"
                ) from first_error
            # CTranslate2 can see a GPU even when a required CUDA/cuDNN runtime DLL is
            # absent. Conversation mode should remain useful in that case.
            self._whisper_models[name] = WhisperModel(name, device="cpu", compute_type="int8")
            self._whisper_devices[name] = "cpu"
            try:
                return self._run_whisper_transcription(name, samples, audio_ms, profile, marks)
            except Exception as fallback_error:
                raise VoiceError(
                    f"local Whisper transcription failed: {str(fallback_error)[:300]}"
                ) from fallback_error

    def _ensure_whisper_model(self, model_class: Any, profile: str) -> str:
        """Load the model this profile wants, falling back to the dictation model.

        A missing or unloadable routing model must not take the command path down
        with it: it is a latency optimisation, and the dictation model answers the
        same question correctly, only slower.
        """
        name = self.decode_model(profile)
        if name in self._whisper_models:
            return name
        try:
            self._load_whisper_model(model_class, name)
            return name
        except Exception as exc:
            fallback = self.config.stt_whisper_model
            if name == fallback:
                raise VoiceError(f"local Whisper model could not load: {str(exc)[:300]}") from exc
            log.warning("voice stt routing model %s could not load; using %s", name, fallback)
            if fallback in self._whisper_models:
                return fallback
            try:
                self._load_whisper_model(model_class, fallback)
            except Exception as inner:
                raise VoiceError(
                    f"local Whisper model could not load: {str(inner)[:300]}"
                ) from inner
            return fallback

    def _load_whisper_model(self, model_class: Any, name: str | None = None) -> None:
        name = name or self.config.stt_whisper_model
        device = "cpu"
        compute_type = "int8"
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                device = "cuda"
                compute_type = "float16"
        except Exception:  # noqa: BLE001 - automatic acceleration is best-effort
            pass
        self._whisper_models.pop(name, None)
        self._whisper_devices.pop(name, None)
        try:
            model = model_class(name, device=device, compute_type=compute_type)
        except Exception:
            if device != "cuda":
                raise
            device = "cpu"
            model = model_class(name, device=device, compute_type="int8")
        self._whisper_models[name] = model
        self._whisper_devices[name] = device

    def _run_whisper_transcription(
        self, name: str, samples: Any, audio_ms: float, profile: str, marks: dict[str, Any]
    ) -> str:
        beam_size = self.beam_size(profile, audio_ms)
        marks["beam_size"] = beam_size
        marks["model"] = name
        # Set here rather than at thread entry so a first-use model download or a
        # CUDA→CPU reload is reported as queueing, not as decode time: they are one
        # startup cost, and folding them into decode would hide the steady-state
        # number the latency work is judged on.
        marks["decode_start"] = time.perf_counter()
        segments, _info = self._whisper_models[name].transcribe(
            samples,
            language=self.config.stt_language.split("-", 1)[0],
            beam_size=beam_size,
            temperature=0,
            condition_on_previous_text=False,
            vad_filter=False,
            hotwords=self._hotwords(profile),
        )
        # faster-whisper's generator is lazy: the decode only happens while this join
        # consumes it, so the end mark has to be taken after it, not after the
        # `transcribe` call returns.
        text = " ".join(str(segment.text).strip() for segment in segments).strip()
        marks["decode_end"] = time.perf_counter()
        return text

    def _ensure_sapi_stt_script(self) -> Path:
        if self._sapi_stt_script_path and self._sapi_stt_script_path.exists():
            return self._sapi_stt_script_path
        path = self.clip_directory / "sapi_stt.ps1"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SAPI_STT_SCRIPT, encoding="utf-8")
        self._sapi_stt_script_path = path
        return path

    async def _prune(self) -> None:
        removed = await self.store.prune(self.config.tts_cache_mb * 1024 * 1024)
        for file_path in removed:
            try:
                await asyncio.to_thread(Path(file_path).unlink, True)
            except OSError:
                # A clip being streamed to the browser is held open without
                # FILE_SHARE_DELETE, so this raises on Windows. Its row is already
                # gone, so `_sweep_orphan_clips` is what eventually reclaims it —
                # and letting the error escape here surfaced as an unhandled task
                # exception (auto trigger) or a 500 after a successful clip.
                log.warning("could not delete pruned voice clip %s", file_path, exc_info=True)
        await asyncio.to_thread(self._sweep_orphan_clips, await self.store.clip_ids())

    def _sweep_orphan_clips(self, known: set[str]) -> None:
        """Delete audio files with no `voice_clips` row pointing at them.

        Prune only walks row-listed paths, so any file whose row is gone (a
        delete that lost a lock race, a synthesis that failed after writing) is
        invisible to it and would stay for the life of the install.
        """
        try:
            entries = list(self.clip_directory.iterdir())
        except OSError:
            return
        for item in entries:
            if not item.is_file() or item.suffix.lower() not in {".mp3", ".wav"}:
                continue
            if item.stem in known:
                continue
            with suppress(OSError):
                item.unlink(missing_ok=True)

    async def status(self) -> dict[str, Any]:
        engine_available = True
        engine_diagnostic: str | None = None
        kokoro_model = self.kokoro_models.status()
        if self.config.tts_engine == "kokoro" and kokoro_model["status"] != "ready":
            engine_available = False
            engine_diagnostic = (
                "the Kokoro voice model is not downloaded; download it in "
                "Settings → Voice, or switch the engine to the OS voice"
            )
        elif self.config.tts_engine == "sapi" and (
            os.name != "nt" or not shutil.which("powershell.exe")
        ):
            engine_available = False
            engine_diagnostic = "the OS voice engine requires Windows PowerShell"
        stats = await self.store.cache_stats()
        spend = await self.automation_store.spend(rule_id=VOICE_RULE_ID)
        stt_available = True
        stt_diagnostic: str | None = None
        if self.config.stt_engine == "sapi":
            if os.name != "nt" or not shutil.which("powershell.exe"):
                stt_available = False
                stt_diagnostic = "Windows Speech Recognition requires Windows PowerShell"
        else:
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                stt_available = False
                stt_diagnostic = "faster-whisper is missing; reinstall/sync swe-mux"
            else:
                runtime = (
                    ", ".join(
                        f"{name} on {device}" for name, device in sorted(
                            self._whisper_devices.items()
                        )
                    )
                    or "not loaded yet"
                )
                stt_diagnostic = (
                    f"dictation {self.config.stt_whisper_model}, routing "
                    f"{self.decode_model(COMMAND_PROFILE)}; loaded: {runtime}"
                )
        return {
            "enabled": self.config.tts_enabled,
            "engine": self.config.tts_engine,
            "engine_available": engine_available,
            "diagnostic": engine_diagnostic or self.diagnostic,
            "content": self.config.tts_content,
            "default_mode": self.config.tts_default_mode,
            "voice": self._voice_label(),
            "summary_model": self.config.tts_summary_model or self.config.openrouter_cheap_model,
            "spend_today": spend,
            "daily_budget_usd": self.config.tts_daily_budget_usd,
            "cache_bytes": stats["bytes"],
            "cache_limit_bytes": self.config.tts_cache_mb * 1024 * 1024,
            "clip_count": stats["count"],
            "kokoro_model": kokoro_model,
            "kokoro_voice": self.config.tts_kokoro_voice,
            "stt_enabled": self.config.stt_enabled,
            "stt_engine": self.config.stt_engine,
            "stt_available": stt_available,
            "stt_diagnostic": stt_diagnostic,
            "stt_language": self.config.stt_language,
            "stt_whisper_model": self.config.stt_whisper_model,
            "stt_routing_model": self.decode_model(COMMAND_PROFILE),
            "wake_words": list(self.config.voice_wake_words),
            "commands": [
                {
                    "action": str(command.get("action")),
                    "phrases": list(command.get("phrases") or []),
                }
                for command in self.config.voice_commands
                if isinstance(command, dict)
            ],
        }
