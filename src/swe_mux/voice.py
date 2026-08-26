"""Read-aloud (TTS) service: turn agent replies into playable audio clips.

This is deliberately not an automation observer. Observers are restricted to
annotate/notify through the fixed OpenRouter origin; audio synthesis uses a
separate provider boundary (the offline OS voice, local Kokoro-82M, or the
explicit external Edge TTS integration) and per-session interactive state.
The only network synthesis path is Edge, and it is refused until the operator
acknowledges the service and privacy disclosure. The optional spoken-summary
call records its call and spend in the shared automation ledger under the
``builtin:voice-summary`` rule id so budgets stay visible in one place.
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
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from . import budget
from .background_tasks import background
from .config import Config
from .edge_tts_provider import EdgeTtsError, EdgeTtsProvider
from .event_bus import EventBus
from .harness import has_observable_transcript
from .kokoro_tts import KokoroEngine, KokoroError, KokoroPaths, SpelledWordLog
from .kokoro_tts import duration_seconds as wav_duration_seconds
from .openrouter import OpenRouterClient, OpenRouterError
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)
from .subprocess_flags import background_creation_flags
from .transcript_view import SpokenExchange, final_exchange_record, message_exchange
from .tts_profiles import TtsProfile, resolve_tts_profile
from .voice_audio import join_wav_files
from .voice_models import ENGLISH_VOICES, KokoroModelStore


def _last_exchange(
    path: Path | None, backend: str, native_id: str | None
) -> SpokenExchange:
    """`final_exchange_record` positionally, for `to_thread`, which takes no keywords."""
    return final_exchange_record(path, backend, native_id=native_id)


def _named_exchange(
    path: Path | None, backend: str, native_id: str | None, message_id: str
) -> SpokenExchange:
    """`message_exchange` positionally, for the same reason."""
    return message_exchange(path, backend, message_id, native_id=native_id)

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
# Trusted application speech uses a natural opening rather than imposing a
# target as a hard cut. The target is where a long first sentence prefers a
# clause boundary; the maximum is the last word boundary it may cross before
# time-to-first-sound becomes the same wait as synthesizing a whole reply.
APPLICATION_FIRST_TARGET_CHARS = 120
APPLICATION_FIRST_MAX_CHARS = 200
APPLICATION_FOLLOWUP_MAX_CHARS = 420
# An append waits this briefly before it is sealed. The assistant emits complete
# sentences, and its completion event commonly follows the final sentence in the
# same browser tick. This window lets those requests land together, so a tiny
# final sentence is folded into the preceding audio instead of becoming its own
# file, without adding perceptible time-to-first-sound.
SPEECH_APPEND_COALESCE_SECONDS = 0.12
SPEECH_APPEND_MAX_COALESCE_SECONDS = 0.24
SPEECH_STREAM_MAX_CHARS = 20_000
# No clip this module emits is allowed to be shorter than this, because a clip
# below roughly twelve characters finishes playing before the next one can be
# made (`streaming_segments` carries the measured curve).
#
# Twenty, and the number came down twice under real sentences. The pathology is
# a THREE-to-FIVE character lead - "Yes.", "Ok.", "Done." - which covers 0.35 and
# stalls before the reply's second word. Ordinary short sentences are not the
# problem and must keep leading on their own: "First result is ready." is 22
# characters and covers ~1.4, "Three sessions are working." is 27 and covers
# ~1.5. A floor at 40, then 25, glued both to the sentence after them for no
# gain. Twenty sits above the ~12-character break-even with about 30% margin and
# leaves a coherent opening sentence alone, which is what it is for.
MIN_SEGMENT_CHARS = 20
# An open speech stream with no producer left is dropped after this long. A
# stream is closed explicitly by whoever opened it; this only reclaims the ones
# whose tab went away mid-turn.
SPEECH_STREAM_IDLE_SECONDS = 600.0
SPEECH_STREAM_LIMIT = 32
# How long a stream's segment clips stay servable after the joined clip has
# replaced them in every listing. A browser that queued the segment ids before the
# join is still going to ask for them, and answering 404 there would cut a reply
# off mid-sentence, so the audio outlives its listing by long enough for any
# queued playback to finish. Nothing refers to a segment id after that: a fresh
# listing returns the joined clip.
SUPERSEDED_CLIP_TTL_SECONDS = 600.0
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

VOICE_SCHEMA_VERSION = 4

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
    synthesis_key TEXT NOT NULL DEFAULT '',
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
    cost_usd REAL,
    source_ts REAL,
    message_anchor TEXT,
    stream_id TEXT,
    segment_index INTEGER NOT NULL DEFAULT 0,
    segment_count INTEGER,
    superseded_at REAL
);
"""

# Applied *after* the column migration, never with the table. An index over a
# column that a pre-existing database has not gained yet is a hard error at
# connect, and `CREATE TABLE IF NOT EXISTS` is exactly the case where that
# happens: the table is left alone, so the index runs against the old columns.
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_voice_clips_session ON voice_clips(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_voice_clips_run ON voice_clips(agent_run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_voice_clips_anchor
    ON voice_clips(agent_run_id, message_anchor, content_mode);
CREATE INDEX IF NOT EXISTS idx_voice_clips_synthesis_anchor
    ON voice_clips(agent_run_id, message_anchor, content_mode, synthesis_key);
CREATE INDEX IF NOT EXISTS idx_voice_clips_stream
    ON voice_clips(stream_id, segment_index);
"""

# What makes several rows one clip. A stream's segments share `stream_id`; a clip
# that was never part of a stream is its own group under its own id, so one
# expression covers both and no listing has to special-case the unstreamed case.
GROUP_KEY = "COALESCE(stream_id, id)"

# The same membership test as `GROUP_KEY=?`, written so SQLite can answer it from
# an index. A predicate over `COALESCE(stream_id, id)` is opaque to both indexes
# on this table, so every per-stream lookup was a full scan of `voice_clips` -
# and eviction does one per candidate stream, which is where the cost showed.
# Split into its two cases, SQLite takes the MULTI-INDEX OR path: a seek on
# `idx_voice_clips_stream` unioned with one on the `id` primary key. Measured
# 2026-08-24 over 60,000 rows: 2.337ms per lookup against 0.002ms.
#
# The `stream_id IS NULL` guard is what keeps it *exactly* equivalent rather than
# merely almost: a bare `id=?` arm would also match a row that belongs to some
# other stream and happens to carry this key as its own id. That collision needs
# two uuid4s to coincide, but the guard costs nothing and it is the difference
# between a rewrite that preserves the predicate and one that widens it.
GROUP_MATCH = "(stream_id=? OR (stream_id IS NULL AND id=?))"


def group_match_args(keys: Sequence[str]) -> tuple[str, list[str]]:
    """`GROUP_MATCH` widened to a set of stream keys, with its bound arguments."""
    placeholders = ",".join("?" for _ in keys)
    sql = f"(stream_id IN ({placeholders}) OR (stream_id IS NULL AND id IN ({placeholders})))"
    return sql, [*keys, *keys]


#: Streams one eviction DELETE takes before committing and releasing the
#: process-wide database lock. Each key is bound twice by `group_match_args`, so
#: this stays far inside SQLite's 999-variable ceiling; the smaller reason for
#: the bound is that an over-cap cache can hold hundreds of streams and the
#: history and PTY writers share that lock (`sqlite.md`).
_EVICTION_BATCH_STREAMS = 100

# A clip's life on the daemon. `synthesizing` is written before the engine runs, so a
# backlog the operator is waiting on is visible while it is being made rather than
# appearing only once it can play. `held`, `played` and `dismissed` are deliberately
# NOT here: they are per-device facts (a clip played on the phone is unplayed on the
# desktop), so the browser overlays them on this row rather than writing them to it.
CLIP_STATUSES = frozenset({"synthesizing", "ready", "failed"})

# Short and workload-flavored: long enough to carry the voice's character,
# short enough that browsing a dozen voices stays fluid on CPU synthesis.
KOKORO_PREVIEW_TEXT = (
    "Hey - two of nine sessions need you, and the pixel lab build is green."
)

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


@dataclass
class SpeechStream:
    """One open application-speech stream: ordered clips appended over time.

    The assistant sends a turn in sentence-sized text fragments, so the stream's
    segment count is unknown while the turn is still running. Ordering is the
    invariant this type exists to hold: exactly one worker task batches raw text
    and drains one sealed-segment FIFO, so clip indices are monotonic no matter
    how the appends arrive. Two segments synthesizing concurrently would emit out
    of order whenever the shorter one finished first, and the browser plays clips
    in arrival order - the reply's second part would speak before its first.

    `total` is `None` until the producer closes the stream; the closing segment
    is the only one that carries a real `segment_count`, which is how the client
    learns the stream ended without a separate poll.
    """

    stream_id: str
    session_id: str
    trigger: str
    content_mode: str
    profile: TtsProfile
    agent_run_id: str | None = None
    model: str | None = None
    # The message every clip in this stream speaks, when there is one. Application
    # speech has none - it is the daemon's own words, not a rendering of a reply -
    # and its clips fall back to synthesis time for ordering.
    source_ts: float | None = None
    message_anchor: str | None = None
    created_at: float = 0.0
    # The stream's opening clip: the row that carries its total, and the row the
    # joined clip is built from once the stream is complete.
    head_clip_id: str | None = None
    # Raw application fragments are accumulated while the previous clip is
    # synthesizing. They are segmented here, once, at the stream boundary. The
    # assistant's sentence events remain display units and are not audio clips.
    pending_text: str = ""
    sealed: deque[str] = dataclass_field(default_factory=deque)
    accepted_chars: int = 0
    accepting: bool = True
    changed: asyncio.Event = dataclass_field(default_factory=asyncio.Event)
    next_index: int = 0
    total: int | None = None
    task: asyncio.Task[None] | None = None
    failed: bool = False
    # True while the opening clip is still synthesizing inline. The worker must
    # not start in that window: it would emit segment 1 before segment 0.
    opening: bool = True


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


def streaming_segments(
    text: str, max_chars: int = 420, first_max_chars: int | None = None
) -> list[str]:
    """Split speakable text into short independently playable clips.

    Auto read-aloud emits each clip as soon as its synthesis finishes. A reply
    that already fits in one ordinary clip stays whole. Longer replies lead with
    one complete sentence whenever possible, because a low-latency cut in the
    middle of a thought sounds like a second, unrelated response when playback
    advances to the continuation. Only sentences longer than the clip bound fall
    back to word chunks.

    `first_max_chars` tightens *only* the opening clip, which is the one the
    operator waits on in silence. Agent read-aloud keeps the wide bound
    (coherence of somebody else's prose matters more than the first second);
    Legacy callers may still pass a tighter first bound. Trusted application
    streams use `application_speech_segments`, which treats its opening target
    as a preferred natural boundary rather than a forced word cut.

    Measured on the primary host, Kokoro, two passes, natural prose - the numbers
    every bound here is chosen against, and the reason the previous ones were
    wrong (this docstring used to claim 140 characters spoke "in about a second";
    it is 4.1 s):

        chars    3     19     40     60     90    140    280    420
        synth  578    984   1484   2016   3109   4110   3969   8438   ms
        audio  200   1267   2667   4000   6000   9333  18667  28000   ms

    Synthesis is *not* linear: about 480 ms of fixed overhead plus 26 ms per
    character, while speech plays at roughly 15 characters per second. The ratio
    of those two is what decides whether playback stalls - a clip must play for
    longer than its successor takes to make. Break-even is near twelve
    characters, so a clip below that guarantees a gap no matter how fast the
    machine is, and every clip this function emits is kept above
    `MIN_SEGMENT_CHARS`.
    """
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    bound = max(80, max_chars)
    lead = min(bound, max(40, first_max_chars)) if first_max_chars else bound
    if len(cleaned) <= lead:
        return [cleaned]
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    if len(first_sentence) >= MIN_SEGMENT_CHARS:
        # Long enough to stand alone, so lead with the whole sentence: a cut in
        # the middle of a thought sounds like a second, unrelated reply.
        first = _bounded_speech_chunks(first_sentence, lead)[0]
    else:
        # "Yes." cannot stand alone - 4 characters, `covers=0.27` measured in the
        # field, a stall guaranteed before the second word of the reply. Run past
        # the sentence boundary and fill the opening clip by words instead.
        #
        # `_bounded_speech_chunks` cannot do this: it accumulates whole *pieces*,
        # so a 57-character follow-on never joins a 4-character lead under a
        # 60-character bound and the runt survives. Nor can merging the two
        # segments afterwards - the second is bounded at `max_chars`, so folding
        # it in produced a 190-character opening clip and pushed time-to-first-
        # sound from 2 s to 5.4 s, trading the stall for the wait it exists to
        # avoid.
        first = _lead_words(cleaned, lead)
    remainder = cleaned[len(first):].strip()
    return _merge_runt_tail([first, *_bounded_speech_chunks(remainder, bound)])


def application_speech_segments(text: str) -> list[str]:
    """Progressively batch trusted application speech at natural boundaries.

    The first clip is small enough to buy time-to-first-sound, but a target is
    not a command to cut a grammatical sentence. Later clips combine complete
    sentences up to the ordinary 420-character synthesis bound, amortizing both
    engine startup and browser media handoff costs.
    """
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= APPLICATION_FIRST_TARGET_CHARS:
        return [cleaned]
    first = _application_opening(cleaned)
    remainder = cleaned[len(first):].strip()
    return _merge_runt_tail(
        [first, *_bounded_speech_chunks(remainder, APPLICATION_FOLLOWUP_MAX_CHARS)]
    )


def _application_opening(text: str) -> str:
    """Choose a sentence or clause boundary near the opening target."""
    minimum = max(MIN_SEGMENT_CHARS, 80)
    sentence_ends = [
        match.end()
        for match in re.finditer(r"[.!?](?=\s|$)", text[: APPLICATION_FIRST_MAX_CHARS + 1])
    ]
    for end in sentence_ends:
        if end >= minimum:
            return text[:end].strip()
    clause_ends = [
        match.end()
        for match in re.finditer(r"[,;:](?=\s|$)", text[: APPLICATION_FIRST_MAX_CHARS + 1])
        if match.end() >= minimum
    ]
    preferred = [end for end in clause_ends if end <= APPLICATION_FIRST_TARGET_CHARS]
    if preferred:
        return text[: preferred[-1]].strip()
    if clause_ends:
        return text[: clause_ends[0]].strip()
    return _lead_words(text, APPLICATION_FIRST_MAX_CHARS)


def _lead_words(text: str, limit: int) -> str:
    """As many whole words as fit the opening bound, ignoring sentence ends."""
    lead = ""
    for word in text.split():
        candidate = f"{lead} {word}".strip()
        if lead and len(candidate) > limit:
            break
        lead = candidate
    return lead


def _merge_runt_tail(segments: list[str]) -> list[str]:
    """Fold a runt final clip into the one before it.

    Greedy chunking always leaves its remainder last, and that is the worst place
    for one: a ten-character clip costs about 740 ms to synthesize for 660 ms of
    audio, so it stalls, and it stalls on the last thing the operator hears -
    a stutter on the way out rather than a pause in the middle.

    Merging can push the final clip past `max_chars`. That is deliberate and
    strictly better: the bound exists to keep synthesis latency covered by the
    clip playing ahead of it, and by the last clip there is nothing left to
    cover. The opening clip is floored at the source instead, because merging
    there would blow the one bound that *is* time-to-first-sound.

    A lone short segment is the whole reply and is left alone - there is nothing
    to merge it into, and silence is not an improvement.
    """
    if len(segments) < 2 or len(segments[-1]) >= MIN_SEGMENT_CHARS:
        return segments
    merged = f"{segments[-2]} {segments[-1]}".strip()
    return [*segments[:-2], merged]


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
            self._migrate()
            self._db.executescript(SCHEMA_INDEXES)
            write_schema_version(self._db, "voice", VOICE_SCHEMA_VERSION)
            self._db.commit()

    def _columns(self, table: str) -> set[str]:
        return {
            str(row["name"]) for row in self._db.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def _migrate(self) -> None:
        """Bring a database created by an older schema up to the current columns.

        `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already
        exists, so a column added to the schema definition reaches a fresh install
        and no other. Every added column is nullable (or has a default) and
        backfills accordingly, which is the truth: a clip made before the anchor
        was captured has no anchor, and inventing one from `created_at` would claim
        a source-message time nothing recorded - exactly the ordering error the
        column exists to fix.

        Schema 3 is the exception, and deliberately: gaining the stream columns
        does not make a pre-3 row groupable. Its segments were written as
        independent clips with no stream identity and no index, so they cannot be
        reassembled into the reply they came from, and mixing them into a grouped
        list shows one reply as three rows in reverse spoken order - the exact
        defect the grouping exists to remove. They are a regenerable cache under a
        byte cap, so the migration discards them (rows and audio both) rather than
        carrying a permanently-wrong shape forever.
        """
        columns = self._columns("voice_clips")
        if columns and "source_ts" not in columns:
            self._db.execute("ALTER TABLE voice_clips ADD COLUMN source_ts REAL")
        if columns and "message_anchor" not in columns:
            self._db.execute("ALTER TABLE voice_clips ADD COLUMN message_anchor TEXT")
        if columns and "synthesis_key" not in columns:
            # Old clips remain playable but do not satisfy provider-aware reuse.
            # An empty key is evidence that their complete synthesis profile was
            # never recorded, not permission to guess it from engine and voice.
            self._db.execute(
                "ALTER TABLE voice_clips ADD COLUMN synthesis_key TEXT NOT NULL DEFAULT ''"
            )
        ungrouped = bool(columns) and "stream_id" not in columns
        if ungrouped:
            self._db.execute("ALTER TABLE voice_clips ADD COLUMN stream_id TEXT")
            self._db.execute(
                "ALTER TABLE voice_clips ADD COLUMN segment_index INTEGER NOT NULL DEFAULT 0"
            )
            self._db.execute("ALTER TABLE voice_clips ADD COLUMN segment_count INTEGER")
            self._db.execute("ALTER TABLE voice_clips ADD COLUMN superseded_at REAL")
            self._discard_ungrouped_clips()
        # No synthesis survives the process that started it: the engine runs in
        # this daemon, so a row still claiming `synthesizing` at connect belongs to
        # a run that died. Resolved here rather than left to age out, because a
        # clip list showing a spinner that will never finish is worse than one
        # showing a failure that happened.
        self._db.execute(
            "UPDATE voice_clips SET status='failed', error=COALESCE(error,?) "
            "WHERE status='synthesizing'",
            ("synthesis was interrupted by a daemon restart",),
        )
        # Same reasoning, for length rather than status: a stream is held open by a
        # producer in this process, so one still claiming an unknown total at
        # connect will never be appended to again. Its clip is as long as the
        # segments it has, and left NULL it would read as a reply still being
        # spoken - a spinner with nothing behind it - for the life of the install.
        self._db.execute(
            "UPDATE voice_clips SET segment_count=(SELECT COUNT(*) FROM voice_clips AS parts "
            "WHERE parts.stream_id=voice_clips.stream_id) "
            "WHERE stream_id IS NOT NULL AND segment_index=0 AND segment_count IS NULL"
        )

    def _discard_ungrouped_clips(self) -> None:
        """Drop every clip written before streams were recorded, audio included.

        Runs on the connect thread, once, as part of reaching schema 3. The files
        are removed here rather than left to the orphan sweep so the cache is
        actually reclaimed even if the sweep never runs (a daemon that starts and
        stops without synthesizing anything never calls it).
        """
        rows = self._db.execute("SELECT id, file_path, size_bytes FROM voice_clips").fetchall()
        if not rows:
            return
        removed_bytes = 0
        for row in rows:
            removed_bytes += int(row["size_bytes"] or 0)
            path = str(row["file_path"] or "")
            if path:
                with suppress(OSError):
                    Path(path).unlink(missing_ok=True)
        self._db.execute("DELETE FROM voice_clips")
        log.info(
            "voice clips discarded for schema 3 (ungroupable, pre-stream) count=%d bytes=%d",
            len(rows),
            removed_bytes,
        )

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    async def add_clip(self, row: dict[str, Any]) -> None:
        """Record a clip before it is synthesized, in its `synthesizing` state.

        Inserted up front rather than on completion: the global clip list is the
        operational view of a backlog, and a clip that only appears once it can
        play makes a slow summary look like nothing happening at all.
        """

        row.setdefault("synthesis_key", "")

        def op() -> None:
            self._db.execute(
                # Named columns, not positional: adding a column would otherwise
                # break every insert made by a rolled-back previous bundle.
                "INSERT INTO voice_clips"
                "(id,session_id,agent_run_id,created_at,trigger,content_mode,engine,voice,synthesis_key,"
                "text,file_path,format,size_bytes,duration_hint_s,status,error,model,"
                "input_tokens,output_tokens,cost_usd,source_ts,message_anchor,"
                "stream_id,segment_index,segment_count,superseded_at) VALUES("
                ":id,:session_id,:agent_run_id,:created_at,:trigger,:content_mode,"
                ":engine,:voice,:synthesis_key,:text,:file_path,:format,:size_bytes,:duration_hint_s,"
                ":status,:error,:model,:input_tokens,:output_tokens,:cost_usd,"
                ":source_ts,:message_anchor,:stream_id,:segment_index,:segment_count,"
                ":superseded_at)",
                row,
            )
            self._db.commit()

        await self._run(op)

    async def update_clip(self, row: dict[str, Any]) -> None:
        """Write a synthesized (or failed) clip over the row `add_clip` reserved.

        Only the fields synthesis produces are written, so a concurrent restart
        sweep cannot be undone by a task that outlived it: a row already retired
        to `failed` is simply overwritten by this row's own verdict, and one that
        no longer exists updates nothing rather than raising out of a background
        segment task.
        """

        def op() -> None:
            self._db.execute(
                "UPDATE voice_clips SET text=:text,file_path=:file_path,format=:format,"
                "size_bytes=:size_bytes,duration_hint_s=:duration_hint_s,status=:status,"
                "error=:error,model=:model,input_tokens=:input_tokens,"
                "output_tokens=:output_tokens,cost_usd=:cost_usd,source_ts=:source_ts,"
                "message_anchor=:message_anchor,segment_count=:segment_count "
                "WHERE id=:id",
                row,
            )
            self._db.commit()

        await self._run(op)

    async def clip(self, clip_id: str) -> dict[str, Any] | None:
        def op() -> dict[str, Any] | None:
            row = self._db.execute("SELECT * FROM voice_clips WHERE id=?", (clip_id,)).fetchone()
            return dict(row) if row else None

        return await self._run(op)

    @staticmethod
    def _filters(
        session_id: str | None,
        agent_run_id: str | None,
        message_anchor: str | None,
        content_mode: str | None,
    ) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if session_id:
            clauses.append("session_id=?")
            args.append(session_id)
        if agent_run_id:
            clauses.append("agent_run_id=?")
            args.append(agent_run_id)
        if message_anchor:
            clauses.append("message_anchor=?")
            args.append(message_anchor)
        if content_mode:
            clauses.append("content_mode=?")
            args.append(content_mode)
        return clauses, args

    async def clips(
        self,
        *,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        message_anchor: str | None = None,
        content_mode: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Individual clip rows, newest first, by the arrival of what they speak.

        This is the row-level view - one row per synthesized segment. Callers that
        present clips to a person want `clip_groups` instead, because a reply cut
        into segments for latency is one clip to everybody outside this module.

        Ordering is by source-message time, not synthesis time, and that is the
        whole point of `source_ts`: a held backlog is synthesized in whatever
        order engine slots and summary calls happen to free up, so a list ordered
        by synthesis puts an hour-old reply above the one that landed while the
        operator was reading. `created_at` is the fallback for a clip with no
        source message (application speech).

        Superseded segments are excluded everywhere: their audio is still served
        so queued playback survives the join, but they are no longer part of any
        answer to "what clips exist".
        """
        clauses, args = self._filters(session_id, agent_run_id, message_anchor, content_mode)
        clauses.append("superseded_at IS NULL")
        sql = "SELECT * FROM voice_clips WHERE " + " AND ".join(clauses)
        sql += " ORDER BY COALESCE(source_ts,created_at) DESC, created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 200)))

        def op() -> list[dict[str, Any]]:
            return [dict(row) for row in self._db.execute(sql, args).fetchall()]

        return await self._run(op)

    async def clip_groups(
        self,
        *,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        message_anchor: str | None = None,
        content_mode: str | None = None,
        limit: int = 20,
    ) -> list[list[dict[str, Any]]]:
        """Streams newest first, each as its segments in the order they are spoken.

        Two queries rather than one, because grouping a row window is wrong at its
        edge: a `LIMIT 60` over rows can cut a stream in half and present its tail
        as a clip whose opening sentence is missing. The first query picks the
        newest `limit` *streams* under the caller's filters; the second fetches
        every segment of exactly those streams. Streams are ordered by the arrival
        of the message they speak (identical for every segment of one stream) and
        segments within a stream by index, which is the order they are spoken -
        the opposite of the row listing's newest-first.
        """
        clauses, args = self._filters(session_id, agent_run_id, message_anchor, content_mode)
        clauses.append("superseded_at IS NULL")
        where = " WHERE " + " AND ".join(clauses)
        keys_sql = (
            f"SELECT {GROUP_KEY} AS group_key, "
            "MIN(COALESCE(source_ts,created_at)) AS arrived, MIN(created_at) AS started "
            f"FROM voice_clips{where} GROUP BY group_key "
            "ORDER BY arrived DESC, started DESC LIMIT ?"
        )
        key_args = [*args, max(1, min(limit, 200))]

        def op() -> list[list[dict[str, Any]]]:
            keys = [
                str(row["group_key"])
                for row in self._db.execute(keys_sql, key_args).fetchall()
            ]
            if not keys:
                return []
            match, match_args = group_match_args(keys)
            rows = self._db.execute(
                f"SELECT * FROM voice_clips WHERE superseded_at IS NULL AND {match} "
                "ORDER BY segment_index ASC, created_at ASC",
                match_args,
            ).fetchall()
            buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
            for row in rows:
                item = dict(row)
                buckets[str(item["stream_id"] or item["id"])].append(item)
            return [buckets[key] for key in keys if buckets[key]]

        return await self._run(op)

    async def stream_parts(self, stream_id: str) -> list[dict[str, Any]]:
        """Every live segment of one stream, in spoken order."""

        def op() -> list[dict[str, Any]]:
            return [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM voice_clips WHERE stream_id=? AND superseded_at IS NULL "
                    "ORDER BY segment_index ASC, created_at ASC",
                    (stream_id,),
                ).fetchall()
            ]

        return await self._run(op)

    async def set_segment_count(self, clip_id: str, count: int) -> None:
        """Record on a stream's opening clip how many segments it turned out to have.

        A stream's length is unknown while it runs - the assistant speaks a turn
        sentence by sentence - so the opening row carries NULL until the producer
        closes it. NULL is what makes a grouped clip read as still being made, so
        writing the real count is what ends that state; a stream that dies without
        it would show a spinner forever.
        """

        def op() -> None:
            self._db.execute(
                "UPDATE voice_clips SET segment_count=? WHERE id=?", (count, clip_id)
            )
            self._db.commit()

        await self._run(op)

    async def supersede_clips(self, clip_ids: Sequence[str], *, at: float) -> None:
        """Retire segments that a joined clip now stands for, keeping their audio."""
        ids = list(clip_ids)
        if not ids:
            return

        def op() -> None:
            placeholders = ",".join("?" for _ in ids)
            self._db.execute(
                f"UPDATE voice_clips SET superseded_at=? WHERE id IN ({placeholders})",
                [at, *ids],
            )
            self._db.commit()

        await self._run(op)

    async def sweep_superseded(self, ttl_seconds: float) -> list[str]:
        """Delete segments superseded longer ago than the TTL. Returns their files."""

        def op() -> list[str]:
            cutoff = time.time() - ttl_seconds
            rows = self._db.execute(
                "SELECT id, file_path FROM voice_clips "
                "WHERE superseded_at IS NOT NULL AND superseded_at<?",
                (cutoff,),
            ).fetchall()
            if not rows:
                return []
            self._db.execute(
                "DELETE FROM voice_clips WHERE superseded_at IS NOT NULL AND superseded_at<?",
                (cutoff,),
            )
            self._db.commit()
            return [str(row["file_path"]) for row in rows if row["file_path"]]

        return await self._run(op)

    async def anchored_group(
        self,
        *,
        agent_run_id: str,
        message_anchor: str,
        content_mode: str,
        synthesis_key: str = "",
    ) -> list[dict[str, Any]] | None:
        """The newest complete stream already speaking this message in this mode.

        The dedup lookup behind per-message playback: automatic read-aloud and the
        reader's own play button produce the same audio for the same reply, so the
        second request is answered from the store rather than by spending a summary
        call and an engine slot on a duplicate.

        A *stream*, not a clip, and complete rather than merely ready: answering
        with the newest ready row returned the last segment of a chunked reply, so
        replaying a reply spoke only its final sentences (fixed with schema 3). A
        stream still missing segments is not offered for reuse either - it would
        hand back a partial reply that never gains its tail, because the reuse path
        does not synthesize.
        """

        def op() -> list[dict[str, Any]] | None:
            rows = [
                dict(row)
                for row in self._db.execute(
                    "SELECT * FROM voice_clips WHERE agent_run_id=? AND message_anchor=? "
                    "AND content_mode=? AND synthesis_key=? AND superseded_at IS NULL "
                    "ORDER BY created_at DESC",
                    (agent_run_id, message_anchor, content_mode, synthesis_key),
                ).fetchall()
            ]
            if not rows:
                return None
            buckets: dict[str, list[dict[str, Any]]] = {}
            for row in rows:  # newest stream first, since rows are newest first
                buckets.setdefault(str(row["stream_id"] or row["id"]), []).append(row)
            for parts in buckets.values():
                parts.sort(key=lambda item: (int(item["segment_index"] or 0), item["created_at"]))
                if group_state(parts) == "ready":
                    return parts
            return None

        return await self._run(op)

    async def delete_clip(self, clip_id: str) -> list[str]:
        """Delete a clip and everything that belongs to its stream.

        Whole streams, because half a reply is not a thing anybody asked to keep:
        deleting one segment of three leaves a clip that plays its middle third and
        a group whose opening sentence is gone.
        """

        def op() -> list[str]:
            row = self._db.execute(
                "SELECT stream_id FROM voice_clips WHERE id=?", (clip_id,)
            ).fetchone()
            if row is None:
                return []
            key = str(row["stream_id"] or clip_id)
            rows = self._db.execute(
                f"SELECT file_path FROM voice_clips WHERE {GROUP_MATCH}", (key, key)
            ).fetchall()
            self._db.execute(f"DELETE FROM voice_clips WHERE {GROUP_MATCH}", (key, key))
            self._db.commit()
            return [str(item["file_path"]) for item in rows if item["file_path"]]

        return await self._run(op)

    async def cache_stats(self) -> dict[str, int]:
        """Clips as the operator counts them, bytes as the disk counts them.

        The count is of streams, matching what the clip list shows - a reply cut
        into four segments for latency is one clip there and must be one clip
        here. The byte total is every ready row including superseded segments,
        because those are still on the disk until the sweep takes them and a cache
        readout that omits them would understate the cap it is measured against.
        """

        def op() -> dict[str, int]:
            row = self._db.execute(
                f"SELECT COUNT(DISTINCT CASE WHEN superseded_at IS NULL THEN {GROUP_KEY} END) "
                "count, COALESCE(SUM(size_bytes),0) bytes FROM voice_clips "
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
        """Drop stale failures and the oldest streams beyond the byte cap.

        Eviction is by stream, oldest first, and takes every segment of the stream
        it chooses. Evicting individual rows to the byte the cap allows used to
        leave a reply holding its last two segments and not its first, which reads
        in the list as a clip that opens mid-sentence and cannot be repaired.
        Returns file paths whose backing audio should be removed.

        Two phases rather than one transaction: sweep and choose, then delete the
        chosen streams in committed batches. The whole eviction used to run inside
        a single operation holding the process-wide `mux.db` lock, and an over-cap
        cache can name hundreds of victim streams (`sqlite.md`). Choosing before
        deleting is also what makes each victim an indexed lookup rather than a
        table scan - see `GROUP_MATCH`.
        """

        def choose() -> list[str]:
            """Sweep stale failures and name the streams the cap has to take."""
            day_ago = time.time() - 24 * 3600
            self._db.execute(
                "DELETE FROM voice_clips WHERE status='failed' AND created_at<?", (day_ago,)
            )
            self._db.commit()
            total = int(
                self._db.execute(
                    "SELECT COALESCE(SUM(size_bytes),0) FROM voice_clips WHERE status='ready'"
                ).fetchone()[0]
            )
            if total <= max_bytes:
                return []
            # One grouped pass, oldest stream first, deciding the whole victim
            # list before anything is deleted. It used to re-read and delete each
            # victim inside this same scan, which meant a full table scan per
            # victim on top of the group scan itself.
            groups = self._db.execute(
                f"SELECT {GROUP_KEY} AS group_key, MIN(created_at) AS started, "
                "COALESCE(SUM(size_bytes),0) AS bytes FROM voice_clips "
                "WHERE status='ready' GROUP BY group_key ORDER BY started ASC"
            ).fetchall()
            victims: list[str] = []
            for group in groups:
                if total <= max_bytes:
                    break
                victims.append(str(group["group_key"]))
                total -= int(group["bytes"])
            return victims

        victims = await self._run(choose)
        removed: list[str] = []
        for start in range(0, len(victims), _EVICTION_BATCH_STREAMS):
            chunk = victims[start : start + _EVICTION_BATCH_STREAMS]
            match, args = group_match_args(chunk)

            def evict(match: str = match, args: list[str] = args) -> list[str]:
                rows = self._db.execute(
                    f"SELECT file_path FROM voice_clips WHERE {match}", args
                ).fetchall()
                self._db.execute(f"DELETE FROM voice_clips WHERE {match}", args)
                self._db.commit()
                return [str(row["file_path"]) for row in rows if row["file_path"]]

            removed.extend(await self._run(evict))
        if victims:
            log.info(
                "voice_cache_evicted streams=%d files=%d cap_bytes=%d batches=%d",
                len(victims),
                len(removed),
                max_bytes,
                (len(victims) + _EVICTION_BATCH_STREAMS - 1) // _EVICTION_BATCH_STREAMS,
            )
        return removed

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


def ordered_parts(parts: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """One stream's segments in the order they are spoken."""
    return sorted(
        parts, key=lambda row: (int(row.get("segment_index") or 0), float(row["created_at"]))
    )


def group_state(parts: Sequence[dict[str, Any]]) -> str:
    """The one status a stream reports, from its segments and its expected length.

    Three inputs, in priority order, because they answer different questions and
    the wrong one on top misreports the clip:

    - A failed segment makes the whole stream `failed`. The stream stops at a gap
      rather than speaking the reply out of order, so what exists is a prefix, and
      calling that `ready` would claim the reply is complete when its ending was
      never made.
    - A segment still synthesizing keeps it `synthesizing`.
    - A stream whose length is unknown (`segment_count` NULL on the opening
      segment, the state an open application-speech stream lives in) or which has
      not yet reached that length is `synthesizing` too, even when every segment it
      currently holds is ready. That is what makes a live clip read as one clip
      being appended to, rather than flickering ready between sentences.
    """
    ordered = ordered_parts(parts)
    if not ordered:
        return "failed"
    if any(row["status"] == "failed" for row in ordered):
        return "failed"
    if any(row["status"] == "synthesizing" for row in ordered):
        return "synthesizing"
    expected = ordered[0].get("segment_count")
    if expected is None or len(ordered) < int(expected):
        return "synthesizing"
    return "ready"


def group_snapshot(parts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One stream as the single clip every surface outside synthesis treats it as.

    Built on the opening segment, because that is the row carrying the stream's
    identity (its arrival time, its message anchor, the summary call's spend) and
    the id every other surface already addresses the reply by. What the other
    segments contribute is added to it: their text in spoken order, their duration,
    their bytes.

    `parts` is kept in the payload rather than hidden. Playback is still segment by
    segment until the stream is joined, so the browser needs the ids and their
    durations to play a reply straight through and to place a scrub position inside
    it; and a stream still being appended to has no joined file to offer at all.
    """
    ordered = ordered_parts(parts)
    head = clip_snapshot(ordered[0])
    durations = [row["duration_hint_s"] for row in ordered if row["duration_hint_s"] is not None]
    costs = [row["cost_usd"] for row in ordered if row["cost_usd"] is not None]
    errors = [row["error"] for row in ordered if row["error"]]
    expected = head.get("segment_count")
    head.update(
        {
            "text": " ".join(str(row["text"]) for row in ordered if row["text"]),
            "duration_hint_s": round(sum(float(value) for value in durations), 2)
            if durations
            else None,
            "size_bytes": sum(int(row["size_bytes"] or 0) for row in ordered),
            "input_tokens": sum(int(row["input_tokens"] or 0) for row in ordered),
            "output_tokens": sum(int(row["output_tokens"] or 0) for row in ordered),
            "cost_usd": round(sum(float(value) for value in costs), 6) if costs else None,
            "status": group_state(ordered),
            "error": errors[0] if errors else None,
            # Not the number of segments that exist: the number the producer said
            # there would be, which is NULL while the stream is open. The browser
            # reads the difference as "more is coming".
            "segment_count": expected,
            "stream_open": expected is None,
            "parts": [
                {
                    "id": str(row["id"]),
                    "segment_index": int(row["segment_index"] or 0),
                    "status": str(row["status"]),
                    "duration_hint_s": row["duration_hint_s"],
                    "size_bytes": int(row["size_bytes"] or 0),
                    "error": row["error"],
                }
                for row in ordered
            ],
        }
    )
    return head


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
        self.edge_tts = EdgeTtsProvider(config)
        self._kokoro_engine: KokoroEngine | None = None
        # Voice-audition samples, per voice for the daemon's lifetime: the whole
        # English set caches at a few megabytes, and a picker that re-synthesizes
        # on every tap would make browsing voices feel broken.
        self._kokoro_previews: dict[str, bytes] = {}
        # Words the Kokoro repair ladder had to spell out letter by letter, kept
        # across restarts so Settings → Voice can offer a one-tap respelling.
        self.spelled_words = SpelledWordLog(config.data_dir / "voice" / "spelled_words.json")
        self.diagnostic: str | None = None
        self._provider_diagnostics: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._debounce: dict[str, asyncio.Task[None]] = {}
        self._segment_tasks: set[asyncio.Task[None]] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._engine_semaphore = asyncio.Semaphore(2)
        self._tts_synthesizers = {
            "sapi": self._synthesize_sapi,
            "kokoro": self._synthesize_kokoro,
            "edge": self._synthesize_edge,
        }
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
        # Open application-speech streams, keyed by the client-minted stream id.
        self._streams: dict[str, SpeechStream] = {}

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

    def record_playback_diagnostic(self, raw: Any) -> dict[str, Any]:
        """Validate and durably log one browser audio-file handoff.

        Synthesis coverage alone cannot distinguish a clip that was unavailable
        when playback ended from a ready clip that the media element started
        late. The browser is the only observer that can make that distinction.
        """
        if not isinstance(raw, dict):
            raise VoiceError("playback diagnostic must be an object")
        if str(raw.get("event") or "") != "handoff":
            raise VoiceError("playback diagnostic event must be handoff")

        def identifier(name: str) -> str:
            value = "".join(
                character
                for character in str(raw.get(name) or "")[:100]
                if character.isalnum() or character in {"-", "_", ".", ":"}
            )
            if not value:
                raise VoiceError(f"playback diagnostic {name} is required")
            return value

        sample: dict[str, Any] = {
            "event": "handoff",
            "stream_id": identifier("streamId"),
            "previous_clip_id": identifier("previousClipId"),
            "next_clip_id": identifier("nextClipId"),
            "handoff_ms": _finite(raw.get("handoffMs"), limit=60_000.0),
            "queued_at_end": bool(raw.get("queuedAtEnd")),
            "preloaded": bool(raw.get("preloaded")),
        }
        log.info("voice playback handoff %s", json.dumps(sample, sort_keys=True))
        return sample

    def record_deferral_diagnostic(self, raw: Any) -> dict[str, Any]:
        """Durably log one unfinished-utterance deferral and how it resolved.

        The client's completeness heuristic holds an utterance that ends on a
        dangling conjunction, preposition, or article for exactly one patience
        extension instead of dispatching it as an assistant turn. That rule set
        is a word list, and a word list is only tunable against evidence, so
        every deferral lands here with the token that triggered it and the
        outcome that judges it: `merged` means the operator really was
        mid-sentence, while `submitted` means they were finished and the trigger
        cost them one extension - the false-positive rate is the ratio of the
        two. `held` (folded into a brainstorm hold) and `discarded` (Talk
        stopped, standby, or cancel) are neither, and are counted separately so
        they cannot be mistaken for either verdict.

        `completion` is the score that justified the hold and `extension_ms` is
        the window that score bought, because the wait is no longer one length
        for every trigger. Without both, a record says which word fired but not
        whether its prior was worth what it cost - and the priors are the only
        thing there is to tune. `source` separates the two very different holds:
        `heuristic` is the pre-model word rule, whose `submitted` outcome is a
        false positive, while `assistant` is the model reporting that the turn
        had nothing answerable in it, which never submits on its own and whose
        outcomes are therefore only ever `merged` or `discarded`.
        """
        if not isinstance(raw, dict):
            raise VoiceError("deferral diagnostic must be an object")
        outcome = str(raw.get("outcome") or "")
        if outcome not in {"merged", "submitted", "discarded", "held"}:
            raise VoiceError("deferral outcome must be merged, submitted, discarded, or held")
        kind = str(raw.get("kind") or "")
        if kind not in {"conjunction", "preposition", "article"}:
            raise VoiceError("deferral kind must be conjunction, preposition, or article")
        # The trigger is a transcript fragment, so it is narrowed to what a word
        # can be rather than trusted: this string is written to the daemon log,
        # and a log line is a place a control character does not belong.
        trigger = "".join(
            character
            for character in str(raw.get("trigger") or "").strip().lower()[:48]
            if character.isalnum() or character in {" ", "'", "-"}
        ).strip()
        if not trigger:
            raise VoiceError("deferral trigger must name the dangling token")

        def bounded_int(value: Any, ceiling: int) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return 0
            return max(0, min(number, ceiling))

        def bounded_score(value: Any) -> float:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return -1.0
            if number != number:  # NaN, which json.dumps would write unquoted
                return -1.0
            return round(max(0.0, min(number, 1.0)), 4)

        source = str(raw.get("source") or "heuristic")
        if source not in {"heuristic", "assistant"}:
            raise VoiceError("deferral source must be heuristic or assistant")
        sample: dict[str, Any] = {
            "outcome": outcome,
            "kind": kind,
            "trigger": trigger,
            "source": source,
            # -1 marks "the client did not send one" rather than silently reading
            # as maximal confidence, which 0.0 would.
            "completion": bounded_score(raw.get("completion")),
            "extension_ms": bounded_int(raw.get("extensionMs"), 60_000),
            "words": bounded_int(raw.get("words"), 10_000),
            "held_ms": bounded_int(raw.get("heldMs"), 3_600_000),
        }
        log.info("voice utterance deferral %s", json.dumps(sample, sort_keys=True))
        return sample

    def record_capture_diagnostic(self, raw: Any) -> dict[str, Any]:
        """Durably log one browser-side capture stall or recovery.

        The failure this exists for (2026-08-20): the phone's last
        `/api/voice/transcribe` was at 21:53:30, zero followed, and the UI said
        "listening" throughout — the daemon had no evidence the microphone had
        died until the access log was read after the fact. The client's frame
        watchdog now reports the stall here, so the outage is in daemon.log at
        the moment it happens, with the AudioContext and track state that
        distinguish a suspension from a released device.
        """
        if not isinstance(raw, dict):
            raise VoiceError("capture diagnostic must be an object")
        event = str(raw.get("event") or "")
        if event not in {"stalled", "recovered"}:
            raise VoiceError("capture event must be stalled or recovered")
        detector = str(raw.get("detector") or "")
        if detector not in {"silero", "energy"}:
            raise VoiceError("capture detector must be silero or energy")

        def bounded_int(value: Any, ceiling: int) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return 0
            return max(0, min(number, ceiling))

        sample: dict[str, Any] = {
            "event": event,
            "detector": detector,
            "silent_ms": bounded_int(raw.get("silentMs"), 86_400_000),
            "context_state": str(raw.get("contextState") or "unknown")[:32],
            "track_state": str(raw.get("trackState") or "unknown")[:32],
            "track_muted": bool(raw.get("trackMuted")),
            "recovery_attempts": bounded_int(raw.get("recoveryAttempts"), 100_000),
        }
        if event == "stalled":
            log.warning("voice capture stall %s", json.dumps(sample, sort_keys=True))
        else:
            log.info("voice capture recovered %s", json.dumps(sample, sort_keys=True))
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
        await self.edge_tts.stop()
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
        message_id: str | None = None,
        reuse: bool = True,
    ) -> dict[str, Any]:
        # The master switch, checked here rather than only on the automatic path:
        # `tts_enabled` off means no session generates audio, and a manual "speak
        # this reply" is a session generating audio. Without this the switch was a
        # master only for the paths that happened to consult it, which is exactly
        # the confusion the three-layer policy exists to end.
        if not self.config.tts_enabled:
            raise VoiceError("read aloud is off")
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
            profile = resolve_tts_profile(self.config)
            if content_mode is not None and content_mode not in {"summary", "verbatim"}:
                raise VoiceError("content mode must be summary or verbatim")
            selected_content = content_mode or self.effective_content(record)
            # Dedup before spending anything. Automatic read-aloud and the reader's
            # own play button ask for the same audio for the same reply, and with the
            # clip anchored to its message the second request is a lookup rather than
            # another summary call and another engine slot.
            if reuse and message_id and record.agent_run_id:
                existing = await self.store.anchored_group(
                    agent_run_id=record.agent_run_id,
                    message_anchor=message_id,
                    content_mode=selected_content,
                    synthesis_key=profile.synthesis_key,
                )
                if existing is not None:
                    reused = group_snapshot(existing)
                    reused["reused"] = True
                    return reused
            row = self._new_clip_row(
                session_id,
                trigger,
                record.agent_run_id,
                selected_content,
                profile=profile,
                stream_id=stream_id,
            )
            await self.store.add_clip(row)
            try:
                spoken = await self._spoken_text(
                    session, row, selected_content, message_id=message_id
                )
            except (VoiceError, OpenRouterError, TimeoutError, OSError) as exc:
                message = str(exc)[:500] or exc.__class__.__name__
                # Stated, not defaulted. The row was inserted as `synthesizing`, so
                # every path out of synthesis has to write its own verdict or the
                # clip list keeps a spinner for work that has already stopped.
                row["status"] = "failed"
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
                await self.store.update_clip(row)
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
            # Known up front here, unlike application speech: the whole reply is in
            # hand before the first segment is synthesized, so the opening row can
            # state the total and the browser knows how much is still coming.
            row["segment_count"] = len(segments)
            await self._synthesize_stream_segment(
                row, segments[0], profile=profile, session_id=session_id,
                agent_run_id=record.agent_run_id,
                trigger=trigger, stream_id=stream_id, index=0, count=len(segments),
            )
            # The group as it stands: one ready segment of however many. `status`
            # is `synthesizing` while the tail is outstanding, which is the truth
            # about the clip - the caller plays the parts that are ready and the
            # rest arrive on this stream.
            first = group_snapshot([row])
            if len(segments) > 1:
                self._start_segment_tail(
                    session_id=session_id, agent_run_id=record.agent_run_id,
                    trigger=trigger, content_mode=selected_content, model=row["model"],
                    stream_id=stream_id, segments=segments[1:], total=len(segments),
                    source_ts=row["source_ts"], message_anchor=row["message_anchor"],
                    head_clip_id=row["id"], profile=profile,
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

    async def speak(
        self,
        text: str,
        *,
        stream_id: str | None = None,
        continue_stream: bool = False,
        final: bool = True,
    ) -> dict[str, Any]:
        """Synthesize trusted application text without involving a language model.

        Three shapes, because the assistant produces its reply over several
        seconds and the operator should not wait for the last sentence to hear
        the first:

        - Empty text with `final=False` opens an asynchronous stream and returns
          immediately. This lets later sentence events reach the daemon while
          its opening clip is still synthesizing.
        - Non-empty `continue_stream=False` opens a stream. The opening clip is
          synthesized inline and returned, so a lost `voice_clip_ready` event
          still has the HTTP response as its playback fallback.
        - `continue_stream=True` appends to an already-open stream. Synthesis
          runs on that stream's single worker and the response is an
          acknowledgement, not a clip: the browser is already playing this
          stream and picks the continuation up from its events.

        `final=False` leaves the stream open. Its segments carry
        `segment_count=0` (unknown) until the closing one, which carries the
        real total.
        """
        if not self.config.tts_enabled:
            raise VoiceError("read aloud is off")
        spoken = re.sub(r"\s+", " ", text).strip()
        if not spoken and not final and not continue_stream:
            opened_id = self._stream_id(stream_id)
            profile = resolve_tts_profile(self.config)
            stream = await self._open_stream(opened_id, profile)
            stream.opening = False
            log.info(
                "voice stream opened stream=%s provider=%s voice=%s format=%s",
                opened_id, profile.provider, profile.voice, profile.format,
            )
            return {
                "stream_id": opened_id,
                "queued": 0,
                "stream_open": True,
            }
        if not spoken or len(spoken) > 2_000:
            raise VoiceError("system speech must contain 1-2000 characters")
        if any(ord(character) < 32 for character in spoken):
            raise VoiceError("system speech contains control characters")
        if continue_stream:
            if stream_id is None:
                raise VoiceError("continuing a speech stream needs its stream_id")
            return self._append_stream(self._stream_id(stream_id), spoken, final=final)
        segments = application_speech_segments(spoken)
        if not segments:
            raise VoiceError("nothing speakable remained after preprocessing")
        stream_id = self._stream_id(stream_id)
        profile = resolve_tts_profile(self.config)
        stream = await self._open_stream(stream_id, profile)
        row = self._new_clip_row(
            "system", "system", None, "verbatim",
            profile=profile,
            stream_id=stream_id, segment_index=0,
            # NULL while the stream is open: an assistant turn does not know how
            # many sentences it will speak until the model stops, and claiming a
            # total here would settle the clip before its ending exists.
            segment_count=len(segments) if final else None,
        )
        stream.head_clip_id = row["id"]
        await self.store.add_clip(row)
        # Reserved before the await so an append landing mid-synthesis queues
        # behind this text rather than in front of it. `opening` holds the
        # worker back until index 0 has actually been emitted.
        stream.next_index = 1
        stream.sealed.extend(segments[1:])
        stream.accepted_chars = len(spoken)
        if final:
            stream.accepting = False
            stream.total = len(segments)
        try:
            await self._synthesize_stream_segment(
                row, segments[0], profile=profile, session_id="system", agent_run_id=None,
                trigger="system", stream_id=stream_id, index=0,
                count=len(segments) if final else 0,
            )
        except VoiceError:
            stream.opening = False
            self._forget_stream(stream)
            raise
        stream.opening = False
        first = group_snapshot([row])
        log.info(
            "voice system first segment ready clip=%s stream=%s characters=%d "
            "segments=%d open=%s",
            row["id"], stream_id, len(spoken), len(segments), not final,
        )
        if stream.sealed:
            self._ensure_stream_worker(stream)
        elif stream.total is not None:
            await self._finish_stream(stream)
        return first

    async def close_speech_stream(self, stream_id: str) -> dict[str, Any]:
        """Mark an open application-speech stream complete with no more text.

        The producer (one assistant turn) does not know it has finished until
        the model stops, and a turn that ends on a tool result adds no closing
        sentence. Without this the browser would hold the stream open forever
        and keep accepting late clips for a turn the operator already left.
        """
        stream = self._streams.get(self._stream_id(stream_id))
        if stream is None:
            return {"stream_id": stream_id, "closed": True, "known": False}
        if stream.accepting:
            stream.accepting = False
            self._seal_pending_stream_text(stream)
            stream.total = stream.next_index + len(stream.sealed)
            stream.changed.set()
        # An opening clip still synthesizing owns the finalization; closing here
        # would announce the end of a stream whose first sound has not played.
        if not stream.opening and (stream.task is None or stream.task.done()):
            await self._finish_stream(stream)
        return {
            "stream_id": stream.stream_id,
            "closed": True,
            "known": True,
            "segment_count": stream.total,
        }

    # ------------------------------------------------------- speech streams

    async def _open_stream(self, stream_id: str, profile: TtsProfile) -> SpeechStream:
        await self._expire_streams()
        if stream_id in self._streams:
            raise VoiceError("that speech stream is already open")
        stream = SpeechStream(
            stream_id=stream_id,
            session_id="system",
            trigger="system",
            content_mode="verbatim",
            profile=profile,
            created_at=time.time(),
        )
        self._streams[stream_id] = stream
        return stream

    def _append_stream(self, stream_id: str, text: str, *, final: bool) -> dict[str, Any]:
        stream = self._streams.get(stream_id)
        if stream is None:
            raise VoiceError("that speech stream is closed")
        if not stream.accepting:
            raise VoiceError("that speech stream is already complete")
        added = len(text)
        if stream.accepted_chars + added > SPEECH_STREAM_MAX_CHARS:
            raise VoiceError(
                f"system speech stream must contain at most {SPEECH_STREAM_MAX_CHARS} characters"
            )
        stream.pending_text = f"{stream.pending_text} {text}".strip()
        stream.accepted_chars += added
        stream.changed.set()
        if final:
            stream.accepting = False
            self._seal_pending_stream_text(stream)
            stream.total = stream.next_index + len(stream.sealed)
        self._ensure_stream_worker(stream)
        log.info(
            "voice stream append stream=%s chars=%d buffered_chars=%d sealed=%d open=%s",
            stream_id, added, len(stream.pending_text), len(stream.sealed), not final,
        )
        return {
            "stream_id": stream_id,
            "queued": 1,
            "segment_index": stream.next_index + len(stream.sealed),
            "stream_open": not final,
        }

    def _ensure_stream_worker(self, stream: SpeechStream) -> None:
        """Start the drain task unless one is already running for this stream."""
        if stream.opening:
            return  # `speak` starts the worker once its opening clip is out
        if stream.task is not None and not stream.task.done():
            return
        if not stream.pending_text and not stream.sealed:
            return
        stream.task = asyncio.create_task(
            self._drain_stream(stream), name=f"voice-stream-{stream.stream_id}"
        )
        self._segment_tasks.add(stream.task)
        stream.task.add_done_callback(self._segment_tail_done)

    async def _drain_stream(self, stream: SpeechStream) -> None:
        """Synthesize this stream's queued segments strictly in order.

        The empty-queue check and clearing `task` are one atomic step (no await
        between them), which is what lets `_ensure_stream_worker` decide to
        start a worker by reading `task` alone: an append that lands while this
        runs is either seen by the loop or starts a fresh worker, never neither.
        """
        # unsupervised-loop-ok: one worker for one speech stream, ending as soon
        # as its queue is empty; the stream itself is bounded by one turn.
        while True:
            if not stream.sealed and stream.pending_text:
                if stream.accepting:
                    coalesce_started = asyncio.get_running_loop().time()
                    while stream.accepting:
                        stream.changed.clear()
                        elapsed = asyncio.get_running_loop().time() - coalesce_started
                        remaining = min(
                            SPEECH_APPEND_COALESCE_SECONDS,
                            SPEECH_APPEND_MAX_COALESCE_SECONDS - elapsed,
                        )
                        if remaining <= 0:
                            break
                        try:
                            await asyncio.wait_for(stream.changed.wait(), timeout=remaining)
                        except TimeoutError:
                            break
                self._seal_pending_stream_text(stream)
                if not stream.accepting and stream.total is None:
                    stream.total = stream.next_index + len(stream.sealed)
            if not stream.sealed:
                stream.task = None
                if not stream.accepting:
                    if stream.total is None:
                        stream.total = stream.next_index
                    await self._finish_stream(stream)
                return
            segment = stream.sealed.popleft()
            index = stream.next_index
            stream.next_index += 1
            total = stream.total
            last = total is not None and index >= total - 1
            row = self._new_clip_row(
                stream.session_id, stream.trigger, stream.agent_run_id, stream.content_mode,
                profile=stream.profile,
                stream_id=stream.stream_id, segment_index=index,
            )
            row["model"] = stream.model
            row["source_ts"] = stream.source_ts
            row["message_anchor"] = stream.message_anchor
            await self.store.add_clip(row)
            try:
                await self._synthesize_stream_segment(
                    row, segment, profile=stream.profile, session_id=stream.session_id,
                    agent_run_id=stream.agent_run_id, trigger=stream.trigger,
                    stream_id=stream.stream_id, index=index,
                    count=total if last and total is not None else 0,
                )
            except VoiceError:
                # The stream cannot continue past a gap without speaking the
                # reply out of order, so it ends here — announced, not silent.
                stream.failed = True
                stream.pending_text = ""
                stream.sealed.clear()
                stream.accepting = False
                stream.task = None
                await self._finish_stream(stream)
                return

    def _seal_pending_stream_text(self, stream: SpeechStream) -> None:
        """Turn accumulated fragments into provider-neutral audio segments once."""
        if not stream.pending_text:
            return
        if stream.next_index == 0 and not stream.sealed:
            segments = application_speech_segments(stream.pending_text)
        else:
            segments = _merge_runt_tail(
                _bounded_speech_chunks(
                    stream.pending_text, APPLICATION_FOLLOWUP_MAX_CHARS
                )
            )
        stream.pending_text = ""
        stream.sealed.extend(segments)

    async def _finish_stream(self, stream: SpeechStream) -> None:
        if self._streams.get(stream.stream_id) is not stream:
            return  # already finished
        self._forget_stream(stream)
        if stream.head_clip_id:
            # The count the opening row has been holding NULL for. Written even
            # when the stream failed, and written as what was emitted rather than
            # what was hoped for, so the clip settles instead of reading as still
            # being made.
            await self.store.set_segment_count(stream.head_clip_id, stream.next_index)
            if not stream.failed:
                await self._join_stream(stream.stream_id, stream.head_clip_id)
        await self.events.emit(
            "voice_stream_closed",
            session_id=stream.session_id,
            source="daemon",
            stream_id=stream.stream_id,
            segment_count=stream.next_index,
            failed=stream.failed,
        )
        log.info(
            "voice stream complete stream=%s trigger=%s segments=%d failed=%s",
            stream.stream_id, stream.trigger, stream.next_index, stream.failed,
        )
        await self._prune()

    async def _join_stream(self, stream_id: str, head_clip_id: str) -> None:
        """Collapse a completed stream's segments into the single clip they are.

        Runs once, when nothing more can be appended. Everything about it is
        arranged so that a browser mid-reply is unaffected:

        - The joined audio is written under a **new clip id**, so no id a client
          already holds changes what it plays. A queued segment keeps playing that
          segment; a listing taken afterwards gets one clip.
        - The segments are marked superseded rather than deleted. They vanish from
          every listing immediately and their audio stays servable until the sweep,
          which is what keeps a reply that was mid-playback from being cut off.
        - Nothing is superseded unless the joined clip is already stored, so there
          is no instant in which the reply has no live row.

        Declining to join is normal and silent: a single-segment stream has nothing
        to join, an incomplete or failed one must keep its segments (the failure is
        part of what the clip says), and segments the joiner cannot read or whose
        audio profiles disagree stay exactly as they are and still play in order.
        """
        parts = ordered_parts(await self.store.stream_parts(stream_id))
        if len(parts) < 2:
            return
        if any(str(row["status"]) != "ready" for row in parts):
            return
        if any(str(row["format"]).lower() != "wav" for row in parts):
            return
        sources = [Path(str(row["file_path"])) for row in parts]
        if not all(path.is_file() for path in sources):
            log.info("voice join skipped stream=%s: a segment file is gone", stream_id)
            return
        head = next((row for row in parts if str(row["id"]) == head_clip_id), parts[0])
        joined = self._new_clip_row(
            str(head["session_id"]), str(head["trigger"]),
            head["agent_run_id"], str(head["content_mode"]),
            stream_id=stream_id, segment_index=0, segment_count=1,
        )
        # The joined clip *is* the reply, so it inherits everything that identifies
        # it - including `created_at`, which is what keeps the list from re-sorting
        # a reply to the top the moment it finishes being spoken.
        joined["created_at"] = head["created_at"]
        joined["engine"] = head["engine"]
        joined["voice"] = head["voice"]
        joined["synthesis_key"] = head["synthesis_key"]
        joined["format"] = "wav"
        joined["model"] = head["model"]
        joined["source_ts"] = head["source_ts"]
        joined["message_anchor"] = head["message_anchor"]
        joined["input_tokens"] = sum(int(row["input_tokens"] or 0) for row in parts)
        joined["output_tokens"] = sum(int(row["output_tokens"] or 0) for row in parts)
        costs = [row["cost_usd"] for row in parts if row["cost_usd"] is not None]
        joined["cost_usd"] = round(sum(float(value) for value in costs), 6) if costs else None
        joined["text"] = " ".join(str(row["text"]) for row in parts if row["text"])
        destination = self.clip_directory / f"{joined['id']}.wav"
        if not await asyncio.to_thread(join_wav_files, sources, destination):
            return
        joined["file_path"] = str(destination)
        joined["status"] = "ready"
        try:
            joined["size_bytes"] = destination.stat().st_size
        except OSError:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
            return
        summed = sum(float(row["duration_hint_s"] or 0.0) for row in parts)
        joined["duration_hint_s"] = wav_duration_seconds(destination) or round(summed, 2)
        await self.store.add_clip(joined)
        await self.store.supersede_clips([str(row["id"]) for row in parts], at=time.time())
        await self.events.emit(
            "voice_clip_joined",
            session_id=str(head["session_id"]),
            source="daemon",
            clip_id=joined["id"],
            stream_id=stream_id,
            segment_count=len(parts),
        )
        log.info(
            "voice stream joined stream=%s clip=%s segments=%d bytes=%d duration=%.1f",
            stream_id, joined["id"], len(parts), joined["size_bytes"],
            float(joined["duration_hint_s"] or 0.0),
        )

    def _forget_stream(self, stream: SpeechStream) -> None:
        if self._streams.get(stream.stream_id) is stream:
            self._streams.pop(stream.stream_id, None)

    async def _expire_streams(self) -> None:
        """Drop streams whose producer went away without closing them.

        An expired stream still has a clip, and that clip has to stop reading as
        one still being spoken: its opening row is holding NULL for a total that
        nobody is going to state now, so the count it actually reached is written
        as it is dropped.
        """
        cutoff = time.time() - SPEECH_STREAM_IDLE_SECONDS
        stale = [
            stream_id
            for stream_id, stream in self._streams.items()
            if stream.created_at < cutoff
            and not stream.opening
            and (stream.task is None or stream.task.done())
        ]
        abandoned: list[SpeechStream] = []
        for stream_id in stale:
            stream = self._streams.pop(stream_id, None)
            if stream is not None:
                abandoned.append(stream)
        if len(self._streams) > SPEECH_STREAM_LIMIT:
            # Only idle streams are evictable: dropping one mid-synthesis would
            # strand its worker emitting clips nothing will ever accept.
            idle = sorted(
                (
                    stream
                    for stream in self._streams.values()
                    if not stream.opening and (stream.task is None or stream.task.done())
                ),
                key=lambda stream: stream.created_at,
            )
            for stream in idle[: len(self._streams) - SPEECH_STREAM_LIMIT]:
                self._streams.pop(stream.stream_id, None)
                stale.append(stream.stream_id)
                abandoned.append(stream)
        for stream in abandoned:
            if stream.head_clip_id and stream.total is None:
                await self.store.set_segment_count(stream.head_clip_id, stream.next_index)
        if stale:
            log.info("voice streams expired count=%d", len(stale))

    async def kokoro_preview(self, voice_id: str) -> bytes:
        """One audition clip for a voice the operator has not committed to.

        The picker's whole point is hearing a voice *before* it is the
        configured one, so this synthesizes with the requested voice regardless
        of `tts_engine`/`tts_kokoro_voice` and touches no config, no clip row,
        and no cache accounting — the bytes go straight back to the tap.
        """
        if voice_id not in ENGLISH_VOICES:
            raise VoiceError(f"unknown Kokoro voice {voice_id[:40]}")
        if not self.kokoro_models.ready():
            raise VoiceError(
                "the Kokoro voice model is not downloaded; download it in "
                "Settings → Voice first"
            )
        cached = self._kokoro_previews.get(voice_id)
        if cached is not None:
            return cached
        engine = self._ensure_kokoro()
        destination = self.clip_directory / f"preview-{voice_id}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._engine_semaphore:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        engine.synthesize_wav,
                        KOKORO_PREVIEW_TEXT,
                        destination,
                        voice_id=voice_id,
                        speed=max(0.5, min(2.0, self.config.tts_kokoro_speed)),
                    ),
                    timeout=ENGINE_TIMEOUT_SECONDS,
                )
            data = destination.read_bytes()
        except KokoroError as exc:
            raise VoiceError(f"Kokoro preview failed: {str(exc)[:300]}") from exc
        finally:
            with suppress(OSError):
                destination.unlink(missing_ok=True)
        self._kokoro_previews[voice_id] = data
        log.info("kokoro preview synthesized voice=%s bytes=%d", voice_id, len(data))
        return data

    async def check_lexicon(self, entries: Any) -> dict[str, Any]:
        """Advisory per-entry verdicts for the Settings lexicon editor.

        Each value is resolved exactly the way the repair ladder would resolve
        it in speech, so "would this respelling actually be spoken as written,
        or rejected to the spelling floor" is answered by the real machinery —
        without touching spell-out telemetry. Absence of the Kokoro model is a
        reported condition, not an error: the check is advisory.
        """
        if not isinstance(entries, dict) or len(entries) > 500:
            raise VoiceError("entries must be a map of at most 500 respellings")
        if not self.kokoro_models.ready():
            return {
                "available": False,
                "diagnostic": "the Kokoro voice model is not downloaded",
                "results": {},
            }
        try:
            engine = self._ensure_kokoro()
        except VoiceError as exc:
            return {"available": False, "diagnostic": str(exc), "results": {}}
        results: dict[str, Any] = {}
        for word, value in entries.items():
            if not isinstance(word, str) or not isinstance(value, str):
                raise VoiceError("entries must map words to respelling strings")
            if not value.strip() or len(value) > 200:
                results[word] = {
                    "ok": False,
                    "phonemes": None,
                    "spoken_as": None,
                    "unspeakable": [],
                }
                continue
            results[word] = await asyncio.to_thread(
                engine.check_respelling, word.strip().casefold(), value.strip()
            )
        return {"available": True, "diagnostic": None, "results": results}

    async def build_lexicon_entry(self, word: Any, value: Any) -> dict[str, Any]:
        """Build an exact-pronunciation value from a phonetic spelling.

        The editor's escape hatch for users who can neither type IPA nor find
        dictionary words for a sound: the phonics rules derive the phoneme
        link, and the caller auditions the result rather than trusting it.
        """
        if not isinstance(word, str) or not isinstance(value, str):
            raise VoiceError("word and value must be strings")
        if len(word) > 60 or len(value) > 200:
            raise VoiceError("word must be at most 60 characters and value at most 200")
        if not self.kokoro_models.ready():
            raise VoiceError(
                "the Kokoro voice model is not downloaded; download it in "
                "Settings → Voice first"
            )
        engine = self._ensure_kokoro()
        return await asyncio.to_thread(
            engine.build_respelling, word.strip().casefold(), value
        )

    async def lexicon_preview(self, text: str) -> bytes:
        """Audition one respelling with the configured Kokoro voice.

        Synthesizes the bounded value text through the full pipeline (so what
        plays is exactly what speech would do with it) with spell-out telemetry
        suppressed — a bad candidate under audition is not fleet speech debt.
        Nothing is cached and no clip row is written; a unique temporary name
        keeps two concurrent auditions off each other's file.
        """
        text = text.strip()
        if not text or len(text) > 200:
            raise VoiceError("preview text must be 1–200 characters")
        if not self.kokoro_models.ready():
            raise VoiceError(
                "the Kokoro voice model is not downloaded; download it in "
                "Settings → Voice first"
            )
        engine = self._ensure_kokoro()
        destination = self.clip_directory / f"lexicon-preview-{uuid.uuid4().hex}.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with self._engine_semaphore:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        engine.synthesize_wav,
                        text,
                        destination,
                        voice_id=self.config.tts_kokoro_voice,
                        speed=max(0.5, min(2.0, self.config.tts_kokoro_speed)),
                        report_unknown=False,
                    ),
                    timeout=ENGINE_TIMEOUT_SECONDS,
                )
            return destination.read_bytes()
        except KokoroError as exc:
            raise VoiceError(f"Kokoro preview failed: {str(exc)[:300]}") from exc
        finally:
            with suppress(OSError):
                destination.unlink(missing_ok=True)

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
        profile: TtsProfile,
        session_id: str,
        agent_run_id: str | None,
        trigger: str,
        stream_id: str,
        index: int,
        count: int,
    ) -> None:
        try:
            await self._synthesize_clip(row, spoken, profile)
        except (VoiceError, TimeoutError, OSError) as exc:
            message = str(exc)[:500] or exc.__class__.__name__
            row["status"] = "failed"
            row["error"] = message
            self.diagnostic = message
            self._provider_diagnostics[profile.provider] = message
            await self.store.update_clip(row)
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
        await self.store.update_clip(row)
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
        source_ts: float | None = None,
        message_anchor: str | None = None,
        head_clip_id: str,
        profile: TtsProfile,
    ) -> None:
        task = asyncio.create_task(
            self._generate_segment_tail(
                session_id=session_id, agent_run_id=agent_run_id, trigger=trigger,
                content_mode=content_mode, model=model, stream_id=stream_id,
                segments=segments, total=total, source_ts=source_ts,
                message_anchor=message_anchor, head_clip_id=head_clip_id, profile=profile,
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
        source_ts: float | None = None,
        message_anchor: str | None = None,
        head_clip_id: str,
        profile: TtsProfile,
    ) -> None:
        for offset, segment in enumerate(segments, start=1):
            row = self._new_clip_row(
                session_id, trigger, agent_run_id, content_mode,
                profile=profile,
                stream_id=stream_id, segment_index=offset,
            )
            row["model"] = model
            # Every segment of one reply carries that reply's anchor: they are one
            # spoken message cut into clips, so a list ordered by source time keeps
            # them together and in order instead of scattering them by synthesis.
            row["source_ts"] = source_ts
            row["message_anchor"] = message_anchor
            await self.store.add_clip(row)
            try:
                await self._synthesize_stream_segment(
                    row, segment, profile=profile, session_id=session_id,
                    agent_run_id=agent_run_id,
                    trigger=trigger, stream_id=stream_id, index=offset, count=total,
                )
            except VoiceError:
                # The reply now ends where synthesis stopped. Restating the total as
                # the number of segments that exist - the failed one included, since
                # it is a row and carries the error - is what lets the clip settle on
                # `failed` instead of waiting forever for segments no one is making.
                await self.store.set_segment_count(head_clip_id, offset + 1)
                log.warning(
                    "voice stream truncated session=%s run=%s stream=%s emitted=%d of %d",
                    session_id, agent_run_id, stream_id, offset, total,
                )
                return
        await self._join_stream(stream_id, head_clip_id)
        await self._prune()
        log.info(
            "voice stream complete session=%s run=%s trigger=%s stream=%s segments=%d",
            session_id, agent_run_id, trigger, stream_id, total,
        )

    def _new_clip_row(
        self,
        session_id: str,
        trigger: str,
        agent_run_id: str | None,
        content_mode: str,
        *,
        profile: TtsProfile | None = None,
        stream_id: str | None = None,
        segment_index: int = 0,
        segment_count: int | None = None,
    ) -> dict[str, Any]:
        selected = profile or resolve_tts_profile(self.config)
        return {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "agent_run_id": agent_run_id,
            "created_at": time.time(),
            "trigger": trigger,
            "content_mode": content_mode,
            "engine": selected.provider,
            "voice": selected.voice,
            "synthesis_key": selected.synthesis_key,
            "text": "",
            "file_path": "",
            "format": selected.format,
            "size_bytes": 0,
            "duration_hint_s": None,
            # Inserted in this state and updated by the synthesis that follows, so a
            # clip is visible while it is being made. A row that never reaches
            # `ready` or `failed` is retired by the connect-time sweep in
            # `VoiceStore._migrate`, because synthesis cannot outlive its daemon.
            "status": "synthesizing",
            "error": None,
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": None,
            # The message this clip speaks, captured at generation time. Ordering a
            # held backlog by synthesis time is exactly wrong, and a clip that cannot
            # name its message cannot be reused by the reader's play button either.
            "source_ts": None,
            "message_anchor": None,
            # What makes this row part of a reply rather than a reply. Segments are
            # a latency device; `stream_id` plus `segment_index` is what lets every
            # surface put them back together in the order they are spoken.
            # `segment_count` is carried by the opening segment alone, and is NULL
            # until the producer knows the total - which for an assistant turn is
            # only when the model stops.
            "stream_id": stream_id,
            "segment_index": segment_index,
            "segment_count": segment_count,
            # Set when a joined clip takes over for this segment: excluded from
            # every listing from that moment, audio still served until the sweep.
            "superseded_at": None,
        }

    async def _synthesize_clip(
        self, row: dict[str, Any], spoken: str, profile: TtsProfile
    ) -> None:
        row["text"] = spoken
        destination = self.clip_directory / f"{row['id']}.{row['format']}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        measured_duration: float | None = None
        try:
            async with self._engine_semaphore:
                measured_duration = await asyncio.wait_for(
                    self._synthesize(
                        profile, spoken, destination, automatic=row["trigger"] == "auto"
                    ),
                    timeout=ENGINE_TIMEOUT_SECONDS,
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
            measured_duration
            or wav_duration_seconds(destination)
            or estimate_duration_seconds(spoken, profile.duration_rate)
        )
        row["status"] = "ready"
        # The one measurement that makes chunk pacing tunable from evidence.
        # `covers` is the whole game: audio divided by the time it took to make.
        # Below 1.0 this clip finishes before the next one can exist, so playback
        # stalls - which is exactly what a one-word opening does (0.35), and what
        # nothing in this daemon could previously show. Its absence is why
        # The former application opening bound sat at a value whose own comment
        # was off by four times.
        synth_ms = (time.perf_counter() - started) * 1000
        audio_ms = float(row["duration_hint_s"] or 0.0) * 1000
        log.info(
            "voice clip synthesized clip=%s provider=%s voice=%s format=%s chars=%d "
            "synth_ms=%.0f audio_ms=%.0f covers=%.2f",
            row["id"], profile.provider, profile.voice, profile.format,
            len(spoken), synth_ms, audio_ms,
            (audio_ms / synth_ms) if synth_ms > 0 else 0.0,
        )
        self.diagnostic = None
        self._provider_diagnostics.pop(profile.provider, None)

    async def _spoken_text(
        self,
        session: Any,
        row: dict[str, Any],
        content_mode: str,
        *,
        message_id: str | None = None,
    ) -> str:
        # The same segment "Copy reply" puts on the clipboard and the reader tab
        # shows as the last agent message. Speaking a different span than the one
        # on screen is how a listener ends up hearing "I'll investigate the
        # sidebar sort" as the answer to a question that was already answered.
        # A named message is the same reduction asked for one reply rather than
        # the newest, so the reader's play button and the automatic path speak
        # byte-identical text and their clips are interchangeable.
        if message_id:
            exchange = await asyncio.to_thread(
                _named_exchange,
                session.transcript_path,
                session.record.backend,
                session.record.native_session_id,
                message_id,
            )
            if not exchange.reply:
                raise VoiceError("that message is not an agent reply in this conversation")
        else:
            exchange = await asyncio.to_thread(
                _last_exchange,
                session.transcript_path,
                session.record.backend,
                session.record.native_session_id,
            )
            if not exchange.reply:
                raise VoiceError("no assistant reply text was found in the last turn")
        prompt, reply = exchange.prompt, exchange.reply
        row["source_ts"] = exchange.ts_epoch
        row["message_anchor"] = exchange.message_id or None
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
        verdict = budget.spent_out(
            self.config.tts_daily_budget, spend, label="the daily read-aloud summary"
        )
        if verdict.exhausted:
            raise VoiceError(verdict.reason)
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
            cost_usd=completion.cost_usd,
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
        return resolve_tts_profile(self.config).voice

    async def _synthesize(
        self,
        profile: TtsProfile,
        text: str,
        destination: Path,
        *,
        automatic: bool,
    ) -> float | None:
        synthesizer = self._tts_synthesizers.get(profile.provider)
        if synthesizer is None:
            raise VoiceError(f"unknown TTS provider {profile.provider}")
        return await synthesizer(profile, text, destination, automatic=automatic)

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
                    ),
                    lexicon=dict(self.config.tts_kokoro_lexicon),
                    on_spell_out=self._record_spelled_word,
                )
            except KokoroError as exc:
                raise VoiceError(str(exc)) from exc
        return self._kokoro_engine

    def _record_spelled_word(self, word: str) -> None:
        """Called from the synthesis worker thread; the log carries its own lock."""
        self.spelled_words.record(word)
        log.info(
            "kokoro spelled out unknown word word=%s (fixable with a respelling in "
            "Settings → Voice)",
            word,
        )

    def apply_lexicon(self) -> None:
        """Hot-apply a `tts_kokoro_lexicon` change without a daemon restart.

        Three caches would otherwise silently serve pre-change resolutions: the
        engine's per-word cache, the per-voice audition previews, and the
        spelled-word telemetry entries the new lexicon now covers.
        """
        if self._kokoro_engine is not None:
            self._kokoro_engine.set_lexicon(dict(self.config.tts_kokoro_lexicon))
        self.invalidate_kokoro_previews()
        self.spelled_words.discard(self.config.tts_kokoro_lexicon)

    def invalidate_kokoro_previews(self) -> None:
        self._kokoro_previews.clear()

    async def _synthesize_kokoro(
        self,
        profile: TtsProfile,
        text: str,
        destination: Path,
        *,
        automatic: bool,
    ) -> None:
        del automatic
        engine = self._ensure_kokoro()
        if not text.strip():
            raise VoiceError("nothing speakable remained after preprocessing")
        voice = profile.voice
        speed = max(0.5, min(2.0, float(profile.option("speed", 1.0))))
        try:
            await asyncio.to_thread(
                engine.synthesize_wav, text, destination, voice_id=voice, speed=speed
            )
        except KokoroError as exc:
            raise VoiceError(f"Kokoro synthesis failed: {str(exc)[:300]}") from exc

    async def _synthesize_edge(
        self,
        profile: TtsProfile,
        text: str,
        destination: Path,
        *,
        automatic: bool,
    ) -> float:
        if not text.strip():
            raise VoiceError("nothing speakable remained after preprocessing")
        try:
            return await self.edge_tts.synthesize(
                profile, text, destination, automatic=automatic
            )
        except EdgeTtsError as exc:
            raise VoiceError(f"Edge TTS failed ({exc.code}): {str(exc)[:300]}") from exc

    async def _synthesize_sapi(
        self,
        profile: TtsProfile,
        text: str,
        destination: Path,
        *,
        automatic: bool,
    ) -> None:
        del automatic
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
            str(int(profile.option("rate", 0))),
        ]
        if profile.voice != "system default":
            command += ["-Voice", profile.voice]
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
                "faster-whisper is not installed; local dictation needs the "
                "voice-local extra (`uv sync --extra voice-local`), or select "
                "Windows Speech Recognition in Settings"
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
        # Superseded segments first: they are the cheapest bytes to reclaim and
        # counting them against the cap would evict a live reply to keep audio
        # nothing lists any more.
        removed = await self.store.sweep_superseded(SUPERSEDED_CLIP_TTL_SECONDS)
        removed += await self.store.prune(self.config.tts_cache_mb * 1024 * 1024)
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
        kokoro_model = self.kokoro_models.status()
        sapi_available = os.name == "nt" and bool(shutil.which("powershell.exe"))
        providers = {
            "sapi": {
                "id": "sapi",
                "available": sapi_available,
                "diagnostic": None
                if sapi_available
                else "the OS voice engine requires Windows PowerShell",
                "capabilities": {
                    "offline": True,
                    "voice_catalog": False,
                    "preview": False,
                    "pronunciation": False,
                    "model_download": False,
                },
            },
            "kokoro": {
                "id": "kokoro",
                "available": kokoro_model["status"] == "ready",
                "diagnostic": None
                if kokoro_model["status"] == "ready"
                else (
                    "the Kokoro voice model is not downloaded; download it in "
                    "Settings → Voice, or switch the engine to the OS voice"
                ),
                "capabilities": {
                    "offline": True,
                    "voice_catalog": True,
                    "preview": True,
                    "pronunciation": True,
                    "model_download": True,
                },
            },
            "edge": {
                **self.edge_tts.status(),
                "capabilities": {
                    "offline": False,
                    "voice_catalog": True,
                    "preview": True,
                    "pronunciation": False,
                    "model_download": False,
                },
            },
        }
        active = providers[self.config.tts_engine]
        engine_available = bool(active["available"])
        engine_diagnostic = self._provider_diagnostics.get(
            self.config.tts_engine
        ) or active.get("diagnostic")
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
                stt_diagnostic = (
                    "faster-whisper is missing; install the voice-local extra "
                    "(`uv sync --extra voice-local`)"
                )
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
            "providers": providers,
            "content": self.config.tts_content,
            "default_mode": self.config.tts_default_mode,
            "voice": self._voice_label(),
            "summary_model": self.config.tts_summary_model or self.config.openrouter_cheap_model,
            "spend_today": spend,
            "daily_budget": self.config.tts_daily_budget.as_dict(),
            "budget_status": budget.spent_out(
                self.config.tts_daily_budget, spend, label="the daily read-aloud summary"
            ).as_dict(),
            "cache_bytes": stats["bytes"],
            "cache_limit_bytes": self.config.tts_cache_mb * 1024 * 1024,
            "clip_count": stats["count"],
            "kokoro_model": kokoro_model,
            "kokoro_voice": self.config.tts_kokoro_voice,
            "kokoro_spelled_words": self.spelled_words.entries(),
            "stt_enabled": self.config.stt_enabled,
            "stt_engine": self.config.stt_engine,
            "stt_available": stt_available,
            "stt_diagnostic": stt_diagnostic,
            "stt_language": self.config.stt_language,
            "stt_whisper_model": self.config.stt_whisper_model,
            "stt_routing_model": self.decode_model(COMMAND_PROFILE),
            "chat_patience_ms": self.config.voice_chat_patience_ms,
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
