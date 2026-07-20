"""Read-aloud (TTS) service: turn agent replies into playable audio clips.

This is deliberately not an automation observer. Observers are restricted to
annotate/notify through the fixed OpenRouter origin; audio synthesis uses a
separate engine boundary (edge-tts network voices or offline Windows SAPI) and
per-session interactive state. The only OpenRouter traffic here is the optional
spoken-summary call, which records its call and spend in the shared automation
ledger under the ``builtin:voice-summary`` rule id so budgets stay visible in
one place.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from .automation import MAX_SLICE_BYTES, TranscriptSliceService
from .config import Config
from .event_bus import EventBus
from .openrouter import OpenRouterClient, OpenRouterError
from .sqlite_store import database_operation_lock, run_sqlite_operation
from .subprocess_flags import background_creation_flags

if TYPE_CHECKING:
    from .automation_store import AutomationStore
    from .session import SessionManager

T = TypeVar("T")

VOICE_RULE_ID = "builtin:voice-summary"
VOICE_MODES = {"off", "on_demand", "auto"}
AGENT_BACKENDS = {"claude", "codex"}
DEBOUNCE_SECONDS = 1.0
ENGINE_TIMEOUT_SECONDS = 45.0
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


class VoiceError(RuntimeError):
    """Typed, user-visible read-aloud failure. Never affects the PTY lifecycle."""


def soften_stops(text: str) -> str:
    """Shorten edge-tts's long inter-sentence pause by turning sentence-final
    periods into commas. Keeps ? and ! for expressiveness."""
    text = re.sub(r"\.(\s+)", r",\1", text)
    text = re.sub(r"\.\s*$", "", text.strip())
    text = re.sub(r",\s*$", "", text.strip())
    return text


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


def last_reply_text(messages: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """Text from the newest turn containing a meaningful assistant reply.

    Provider control operations can append a synthetic assistant acknowledgement
    after the real turn. Walk backward across turn boundaries so those records do
    not become the user-facing "latest reply".
    """
    parts: list[str] = []
    found_turn = False
    for message in reversed(messages):
        role = message.get("role")
        if role == "user":
            if found_turn:
                break
            continue
        if role != "assistant":
            continue
        message_parts: list[str] = []
        for block in message.get("content") or []:
            if block.get("type") != "text":
                continue
            value = str(block.get("text") or "")
            if value.strip().casefold() in {"no response requested."}:
                continue
            if value.strip():
                message_parts.append(value)
        if message_parts:
            parts[0:0] = message_parts
            found_turn = True
    return "\n\n".join(parts).strip()


def estimate_duration_seconds(text: str, rate: str) -> float:
    words = max(1, len(text.split()))
    per_second = 2.6
    match = re.fullmatch(r"([+-]\d{1,3})%", rate)
    if match:
        per_second *= 1 + int(match.group(1)) / 100
    return round(words / max(per_second, 0.5), 1)


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
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-voice-db")
        self._executor.submit(self._connect).result()

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = sqlite3.connect(self._path, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA busy_timeout=5000")
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
                "INSERT INTO voice_clips VALUES("
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
    ) -> None:
        self.config = config
        self.events = events
        self.sessions = sessions
        self.store = store
        self.automation_store = automation_store
        self.provider = provider
        self.slices = TranscriptSliceService()
        self.diagnostic: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[Any] | None = None
        self._debounce: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._engine_semaphore = asyncio.Semaphore(2)
        self._sapi_script_path: Path | None = None

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

    def start(self) -> None:
        if self._task:
            return
        self._queue = self.events.subscribe()
        self._task = asyncio.create_task(self._drain(), name="voice-events")

    async def stop(self) -> None:
        for task in (*self._debounce.values(), self._task):
            if task:
                task.cancel()
        pending = [task for task in (*self._debounce.values(), self._task) if task]
        self._debounce.clear()
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
                self._consider(event)
            except Exception:  # noqa: BLE001 - the drain loop must survive anything
                continue

    def _consider(self, event: Any) -> None:
        if event.type != "turn_ended" or not self.config.tts_enabled:
            return
        session_id = event.session_id
        if not session_id:
            return
        session = self.sessions.sessions.get(session_id)
        if not session or session.record.backend not in AGENT_BACKENDS:
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

    async def generate(self, session_id: str, *, trigger: str) -> dict[str, Any]:
        session = self.sessions.sessions.get(session_id)
        if not session:
            raise VoiceError("session is not live")
        record = session.record
        if record.backend not in AGENT_BACKENDS:
            raise VoiceError("read aloud requires a Claude or Codex session")
        if not session.transcript_path or not session.transcript_path.exists():
            raise VoiceError("the agent transcript is not available yet")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        if lock.locked() and trigger == "auto":
            return {}  # a clip for this session is already being generated
        async with lock:
            clip_id = str(uuid.uuid4())
            row: dict[str, Any] = {
                "id": clip_id,
                "session_id": session_id,
                "agent_run_id": record.agent_run_id,
                "created_at": time.time(),
                "trigger": trigger,
                "content_mode": self.effective_content(record),
                "engine": self.config.tts_engine,
                "voice": self._voice_label(),
                "text": "",
                "file_path": "",
                "format": "mp3" if self.config.tts_engine == "edge" else "wav",
                "size_bytes": 0,
                "duration_hint_s": None,
                "status": "failed",
                "error": None,
                "model": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": None,
            }
            try:
                spoken = await self._spoken_text(session, row)
                row["text"] = spoken
                destination = self.clip_directory / f"{clip_id}.{row['format']}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                async with self._engine_semaphore:
                    await asyncio.wait_for(
                        self._synthesize(spoken, destination), timeout=ENGINE_TIMEOUT_SECONDS
                    )
                row["file_path"] = str(destination)
                row["size_bytes"] = destination.stat().st_size
                row["duration_hint_s"] = estimate_duration_seconds(
                    spoken, self.config.tts_edge_rate if self.config.tts_engine == "edge" else "+0%"
                )
                row["status"] = "ready"
                self.diagnostic = None
            except (VoiceError, OpenRouterError, TimeoutError, OSError) as exc:
                message = str(exc)[:500] or exc.__class__.__name__
                row["error"] = message
                self.diagnostic = message
                await self.store.add_clip(row)
                await self.events.emit(
                    "voice_clip_failed",
                    session_id=session_id,
                    source="daemon",
                    clip_id=clip_id,
                    trigger=trigger,
                    error=message,
                )
                raise VoiceError(message) from exc
            await self.store.add_clip(row)
            await self._prune()
            await self.events.emit(
                "voice_clip_ready",
                session_id=session_id,
                source="daemon",
                clip_id=clip_id,
                agent_run_id=record.agent_run_id,
                trigger=trigger,
            )
            return clip_snapshot(row)

    async def _spoken_text(self, session: Any, row: dict[str, Any]) -> str:
        transcript = await self.slices.build(
            session.transcript_path,
            session.record.backend,
            "last_turn",
            max_bytes=min(self.config.automation_max_input_tokens * 4, MAX_SLICE_BYTES),
        )
        reply = last_reply_text(transcript.messages)
        if not reply:
            raise VoiceError("no assistant reply text was found in the last turn")
        if self.effective_content(session.record) == "verbatim":
            return speechify(reply, self.config.tts_verbatim_max_chars)
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
            input_hash=transcript.input_hash,
            input_bytes=transcript.bytes,
        )
        try:
            completion = await self.provider.complete_json(
                model=model,
                messages=[
                    {"role": "system", "content": SUMMARY_PROMPT},
                    {"role": "user", "content": transcript.render()},
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
        if self.config.tts_engine == "edge":
            return self.config.tts_edge_voice
        return self.config.tts_sapi_voice or "system default"

    async def _synthesize(self, text: str, destination: Path) -> None:
        if self.config.tts_engine == "edge":
            await self._synthesize_edge(text, destination)
        else:
            await self._synthesize_sapi(text, destination)

    async def _synthesize_edge(self, text: str, destination: Path) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise VoiceError(
                "the edge-tts package is not installed; run `uv sync`, or switch the "
                "voice engine to sapi in Settings"
            ) from exc
        if self.config.tts_soften_stops:
            text = soften_stops(text)
        if not text.strip():
            raise VoiceError("nothing speakable remained after preprocessing")
        last = ""
        # Edge neural voices occasionally 403 on a cold call; retry briefly.
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(
                    text,
                    self.config.tts_edge_voice,
                    rate=self.config.tts_edge_rate,
                    pitch=self.config.tts_edge_pitch,
                )
                await communicate.save(str(destination))
                if destination.exists() and destination.stat().st_size > 0:
                    return
                last = "edge-tts produced no audio"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - edge_tts raises assorted network errors
                last = str(exc)
            await asyncio.sleep(0.8 * (attempt + 1))
        raise VoiceError(f"edge-tts failed after retries: {last[:300]}")

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

    async def _prune(self) -> None:
        removed = await self.store.prune(self.config.tts_cache_mb * 1024 * 1024)
        for file_path in removed:
            await asyncio.to_thread(Path(file_path).unlink, True)

    async def status(self) -> dict[str, Any]:
        engine_available = True
        engine_diagnostic: str | None = None
        if self.config.tts_engine == "edge":
            try:
                import edge_tts  # noqa: F401
            except ImportError:
                engine_available = False
                engine_diagnostic = "edge-tts package is not installed; run `uv sync`"
        stats = await self.store.cache_stats()
        spend = await self.automation_store.spend(rule_id=VOICE_RULE_ID)
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
            "stt_enabled": self.config.stt_enabled,
        }
