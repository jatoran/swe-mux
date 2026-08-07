from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.automation import TranscriptSlice
from swe_mux.config import load_config, update_config
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.openrouter import OpenRouterResult
from swe_mux.server import session_last_reply, voice_submit
from swe_mux.voice import (
    VOICE_RULE_ID,
    VoiceError,
    VoiceService,
    VoiceStore,
    clip_snapshot,
    estimate_duration_seconds,
    last_reply_text,
    soften_stops,
    speechify,
    streaming_segments,
)


def make_slice(messages: list[dict[str, Any]]) -> TranscriptSlice:
    encoded = json.dumps(messages).encode()
    return TranscriptSlice(
        "last_turn",
        tuple(messages),
        len(encoded),
        max(1, len(encoded) // 4),
        False,
        hashlib.sha256(encoded).hexdigest(),
    )


REPLY_MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": [{"type": "text", "text": "fix the failing test"}]},
    {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "## Done\n\nThe test passes now. See `foo.py`."},
            {"type": "text", "text": "```python\nassert True\n```\nNext I suggest a rerun."},
        ],
    },
]

CONTROL_ACK_MESSAGES: list[dict[str, Any]] = [
    {"role": "user", "content": [{"type": "text", "text": "complete the implementation"}]},
    {
        "role": "assistant",
        "content": [{"type": "text", "text": "Implemented it and all checks pass."}],
    },
    {"role": "user", "content": [{"type": "text", "text": "provider control operation"}]},
    {
        "role": "assistant",
        "content": [{"type": "text", "text": "No response requested."}],
    },
]


class AutomationStoreStub:
    def __init__(self, spent_usd: float = 0.0) -> None:
        self.spent_usd = spent_usd
        self.started: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []
        self.spend_rows: list[dict[str, Any]] = []

    async def spend(self, *, rule_id: str | None = None) -> dict[str, float | int]:
        assert rule_id == VOICE_RULE_ID
        return {"tokens": 0, "cost_usd": self.spent_usd}

    async def observer_started(self, **kwargs: Any) -> str:
        self.started.append(kwargs)
        return "call-1"

    async def observer_finished(self, call_id: str, **kwargs: Any) -> None:
        self.finished.append({"call_id": call_id, **kwargs})

    async def add_spend(self, **kwargs: Any) -> None:
        self.spend_rows.append(kwargs)


class ProviderStub:
    def __init__(self, speech: str = "All tests pass now.") -> None:
        self.speech = speech
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
        self.calls.append(kwargs)
        return OpenRouterResult(
            generation_id="gen-1",
            requested_model=kwargs["model"],
            resolved_model=kwargs["model"],
            value={"speech": self.speech},
            input_tokens=100,
            output_tokens=40,
            cost_usd=0.0005,
            latency_ms=120,
        )


def make_service(
    tmp_path: Path,
    *,
    backend: str = "claude",
    content: str = "verbatim",
    provider: Any = None,
    automation_store: Any = None,
    tts_enabled: bool = True,
    default_mode: str = "on_demand",
) -> tuple[VoiceService, EventBus, list[MuxEvent], SessionRecord]:
    config = load_config(tmp_path / "config.toml")
    update_config(
        config,
        {
            "tts_enabled": tts_enabled,
            "tts_content": content,
            "tts_default_mode": default_mode,
            "openrouter_cheap_model": "test/cheap-model",
        },
    )
    emitted: list[MuxEvent] = []
    events = EventBus()

    original_emit = events.emit

    async def capture(event_type: str, **kwargs: Any) -> MuxEvent:
        event = await original_emit(event_type, **kwargs)
        emitted.append(event)
        return event

    events.emit = capture  # type: ignore[method-assign]
    record = SessionRecord(
        id="s1",
        name="agent",
        project_id="default",
        backend=backend,
        native_session_id="native-1",
        cwd=str(tmp_path),
        exe="claude.exe",
        args=[],
        agent_run_id="run-1",
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}", encoding="utf-8")
    session = SimpleNamespace(record=record, transcript_path=transcript)
    sessions = SimpleNamespace(sessions={"s1": session})
    store = VoiceStore(config.database_path)
    service = VoiceService(
        config,
        events,
        cast(Any, sessions),
        store,
        cast(Any, automation_store or AutomationStoreStub()),
        cast(Any, provider or ProviderStub()),
    )
    return service, events, emitted, record


def patch_slice(service: VoiceService, messages: list[dict[str, Any]]) -> None:
    async def build(*_args: Any, **_kwargs: Any) -> TranscriptSlice:
        return make_slice(messages)

    service.slices.build = build  # type: ignore[method-assign]


def patch_engine(service: VoiceService, *, fail: str | None = None) -> list[str]:
    spoken: list[str] = []

    async def synthesize(text: str, destination: Path) -> None:
        if fail:
            raise VoiceError(fail)
        spoken.append(text)
        destination.write_bytes(b"ID3" + text.encode()[:64])

    service._synthesize = synthesize  # type: ignore[method-assign]
    return spoken


def test_voice_config_fields_validate_and_hot_apply(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    hot, restart = update_config(config, {"tts_enabled": True, "tts_engine": "sapi"})
    assert restart == set()
    assert {"tts_enabled", "tts_engine"} <= hot
    with pytest.raises(ValueError, match="tts_engine"):
        update_config(config, {"tts_engine": "espeak"})
    with pytest.raises(ValueError, match="tts_edge_rate"):
        update_config(config, {"tts_edge_rate": "fast"})
    with pytest.raises(ValueError, match="tts_default_mode"):
        update_config(config, {"tts_default_mode": "always"})
    with pytest.raises(ValueError, match="tts_sapi_rate"):
        update_config(config, {"tts_sapi_rate": 25})


def test_speechify_strips_markdown_and_truncates() -> None:
    text = speechify(
        "# Title\n\nUse `foo()` and see [the docs](https://example.com/x).\n"
        "```python\nprint('hi')\n```\n- first\n- second\n",
        max_chars=4000,
    )
    assert "```" not in text and "#" not in text and "https://" not in text
    assert "Code block omitted" in text
    assert "the docs" in text and "foo()" in text
    long = speechify("word " * 300, max_chars=200)
    assert len(long) <= 220 and long.endswith("… reply truncated.")


def test_soften_stops_shortens_sentence_pauses() -> None:
    assert soften_stops("Done. Next step. Ready?") == "Done, Next step, Ready?"
    assert estimate_duration_seconds("one two three four five six", "+0%") > 0


def test_streaming_segments_preserve_text_and_bound_chunks() -> None:
    text = "First result is ready. " + ("A deliberately long sentence " * 30) + "Done!"
    chunks = streaming_segments(text, max_chars=120)
    assert len(chunks) > 2
    assert all(0 < len(chunk) <= 120 for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


def test_last_reply_text_collects_only_assistant_text() -> None:
    text = last_reply_text(REPLY_MESSAGES)
    assert "The test passes now" in text and "Next I suggest a rerun" in text
    assert "fix the failing test" not in text


def test_last_reply_text_skips_provider_control_acknowledgement() -> None:
    assert last_reply_text(CONTROL_ACK_MESSAGES) == "Implemented it and all checks pass."
    assert last_reply_text(CONTROL_ACK_MESSAGES[-2:]) == ""


async def test_last_reply_route_returns_normalized_agent_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text("{}\n", encoding="utf-8")

    class SliceStub:
        async def build(
            self, _path: Path, _backend: str, kind: str, **_kwargs: Any
        ) -> TranscriptSlice:
            assert kind == "last_n_messages"
            return make_slice(CONTROL_ACK_MESSAGES)

    monkeypatch.setattr("swe_mux.server.TranscriptSliceService", SliceStub)
    session = SimpleNamespace(
        transcript_path=transcript_path,
        record=SimpleNamespace(backend="codex", agent_run_id="run-1"),
    )
    request = SimpleNamespace(
        app={"sessions": SimpleNamespace(resolve=lambda _sid: session)},
        match_info={"sid": "s1"},
    )
    response = await session_last_reply(cast(Any, request))
    payload = json.loads(response.text)
    assert response.status == 200
    assert payload["agent_run_id"] == "run-1"
    assert payload["text"] == "Implemented it and all checks pass."


async def test_generate_verbatim_produces_ready_clip_and_event(tmp_path: Path) -> None:
    service, _events, emitted, _record = make_service(tmp_path, content="verbatim")
    patch_slice(service, REPLY_MESSAGES)
    spoken = patch_engine(service)
    try:
        clip = await service.generate("s1", trigger="manual")
        assert clip["status"] == "ready"
        assert clip["content_mode"] == "verbatim"
        assert "file_path" not in clip  # snapshots never leak daemon paths
        assert "Code block omitted" in spoken[0]
        stored = await service.store.clips(session_id="s1")
        assert len(stored) == 1
        audio = Path(stored[0]["file_path"])
        assert audio.is_file() and audio.parent == service.clip_directory
        assert [event.type for event in emitted] == ["voice_clip_ready"]
        assert emitted[0].payload["trigger"] == "manual"
    finally:
        service.store.close()


async def test_generate_summary_records_call_and_spend(tmp_path: Path) -> None:
    ledger = AutomationStoreStub()
    provider = ProviderStub(speech="I fixed the test and everything passes.")
    service, _events, _emitted, _record = make_service(
        tmp_path, content="summary", provider=provider, automation_store=ledger
    )
    patch_slice(service, REPLY_MESSAGES)
    patch_engine(service)
    try:
        clip = await service.generate("s1", trigger="manual")
        assert clip["status"] == "ready"
        assert clip["text"] == "I fixed the test and everything passes."
        assert clip["model"] == "test/cheap-model"
        assert ledger.started[0]["rule_id"] == VOICE_RULE_ID
        assert ledger.finished[0]["status"] == "completed"
        assert ledger.spend_rows[0]["cost_usd"] == pytest.approx(0.0005)
    finally:
        service.store.close()


async def test_summary_budget_exhaustion_fails_closed(tmp_path: Path) -> None:
    ledger = AutomationStoreStub(spent_usd=100.0)
    service, _events, emitted, _record = make_service(
        tmp_path, content="summary", automation_store=ledger
    )
    patch_slice(service, REPLY_MESSAGES)
    patch_engine(service)
    try:
        with pytest.raises(VoiceError, match="budget"):
            await service.generate("s1", trigger="manual")
        assert ledger.started == []  # no provider call is even started
        stored = await service.store.clips(session_id="s1")
        assert stored[0]["status"] == "failed"
        assert [event.type for event in emitted] == ["voice_clip_failed"]
    finally:
        service.store.close()


async def test_engine_failure_records_failed_clip(tmp_path: Path) -> None:
    service, _events, emitted, _record = make_service(tmp_path, content="verbatim")
    patch_slice(service, REPLY_MESSAGES)
    patch_engine(service, fail="edge-tts failed after retries: boom")
    try:
        with pytest.raises(VoiceError, match="boom"):
            await service.generate("s1", trigger="manual")
        stored = await service.store.clips(session_id="s1")
        assert stored[0]["status"] == "failed" and "boom" in str(stored[0]["error"])
        assert [event.type for event in emitted] == ["voice_clip_failed"]
    finally:
        service.store.close()


async def test_generate_rejects_plain_shells(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path, backend="shell")
    try:
        with pytest.raises(VoiceError, match="observable agent session"):
            await service.generate("s1", trigger="manual")
    finally:
        service.store.close()


async def test_session_content_override_beats_global_setting(tmp_path: Path) -> None:
    ledger = AutomationStoreStub()
    provider = ProviderStub()
    service, _events, _emitted, record = make_service(
        tmp_path, content="summary", provider=provider, automation_store=ledger
    )
    record.voice_content = "verbatim"
    patch_slice(service, REPLY_MESSAGES)
    patch_engine(service)
    try:
        clip = await service.generate("s1", trigger="manual")
        assert clip["content_mode"] == "verbatim"
        assert provider.calls == []  # verbatim override means no LLM call at all
        record.voice_content = None
        clip = await service.generate("s1", trigger="manual")
        assert clip["content_mode"] == "summary"
        assert len(provider.calls) == 1
    finally:
        service.store.close()


def test_effective_mode_inherits_global_default_until_marked(tmp_path: Path) -> None:
    service, _events, _emitted, record = make_service(tmp_path, default_mode="auto")
    try:
        assert service.effective_mode(record) == "auto"
        record.voice_mode = "off"
        assert service.effective_mode(record) == "off"
        service.config.tts_enabled = False
        record.voice_mode = None
        assert service.effective_mode(record) == "off"
    finally:
        service.store.close()


async def test_auto_turn_ended_debounces_and_generates(tmp_path: Path) -> None:
    service, events, emitted, record = make_service(tmp_path, default_mode="auto")
    record.voice_mode = "auto"
    patch_slice(service, REPLY_MESSAGES)
    patch_engine(service)
    import swe_mux.voice as voice_module

    original = voice_module.DEBOUNCE_SECONDS
    voice_module.DEBOUNCE_SECONDS = 0.01
    service.start()
    try:
        await events.emit("turn_ended", session_id="s1", source="transcript")
        for _ in range(100):
            if any(event.type == "voice_clip_ready" for event in emitted):
                break
            await asyncio.sleep(0.02)
        ready = [event for event in emitted if event.type == "voice_clip_ready"]
        assert len(ready) == 1
        assert ready[0].payload["trigger"] == "auto"
    finally:
        voice_module.DEBOUNCE_SECONDS = original
        await service.stop()
        service.store.close()


async def test_auto_generation_emits_short_audio_segments_in_order(tmp_path: Path) -> None:
    speech = (
        "First sentence is ready and contains " + "useful detail " * 18 + ". "
        "Second sentence follows with " + "another detail " * 18 + ". "
        "Third sentence finishes the spoken response."
    )
    service, _events, emitted, _record = make_service(
        tmp_path, content="summary", provider=ProviderStub(speech=speech)
    )
    patch_slice(service, REPLY_MESSAGES)
    spoken = patch_engine(service)
    try:
        await service.generate("s1", trigger="auto")
        ready = [event for event in emitted if event.type == "voice_clip_ready"]
        assert len(ready) == len(spoken) >= 2
        assert [event.payload["segment_index"] for event in ready] == list(range(len(ready)))
        assert len({event.payload["stream_id"] for event in ready}) == 1
        assert " ".join(spoken) == speech
    finally:
        service.store.close()


def wav_bytes(seconds: float = 0.2, rate: int = 16_000) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x00\x00" * int(seconds * rate))
    return target.getvalue()


async def test_daemon_stt_validates_wav_and_deletes_temporary_audio(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    service.config.stt_engine = "sapi"
    seen: list[Path] = []

    async def transcribe(audio_path: Path, text_path: Path) -> str:
        assert audio_path.exists()
        seen.extend([audio_path, text_path])
        return "  Mux   send that  "

    service._transcribe_sapi = transcribe  # type: ignore[method-assign]
    try:
        assert await service.transcribe_wav(wav_bytes()) == "Mux send that"
        assert seen and all(not path.exists() for path in seen)
        with pytest.raises(VoiceError, match="valid WAV"):
            await service.transcribe_wav(b"not audio")
    finally:
        service.store.close()


def test_whisper_transcription_uses_accuracy_and_technical_context(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    calls: list[dict[str, object]] = []

    class Model:
        def transcribe(self, _path: str, **options: object):
            calls.append(options)
            return [SimpleNamespace(text=" Run the TypeScript tests. ")], SimpleNamespace()

    service._whisper_model = Model()
    try:
        result = service._run_whisper_transcription(tmp_path / "speech.wav")
        assert result == "Run the TypeScript tests."
        assert calls[0]["beam_size"] == 5
        assert calls[0]["condition_on_previous_text"] is False
        assert calls[0]["vad_filter"] is False
        assert "TypeScript" in str(calls[0]["hotwords"])
    finally:
        service.store.close()


def test_whisper_model_load_falls_back_when_cuda_runtime_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ctranslate2

    service, _events, _emitted, _record = make_service(tmp_path)
    calls: list[tuple[str, str]] = []

    class Model:
        def __init__(self, _name: str, *, device: str, compute_type: str) -> None:
            calls.append((device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA runtime unavailable")

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: 1)
    try:
        service._load_whisper_model(Model)
        assert calls == [("cuda", "float16"), ("cpu", "int8")]
        assert service._whisper_device == "cpu"
        assert service._whisper_model is not None
    finally:
        service.store.close()


def test_voice_submission_idempotency_is_bounded(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        assert service.claim_submission("utterance-1") is True
        assert service.claim_submission("utterance-1") is False
        for index in range(600):
            assert service.claim_submission(f"utterance-{index + 2}") is True
        assert service.claim_submission("utterance-1") is True
    finally:
        service.store.close()


async def test_voice_submit_writes_prompt_and_enter_once_and_marks_human_input(
    tmp_path: Path,
) -> None:
    service, events, emitted, record = make_service(tmp_path)
    writes: list[str] = []
    session = SimpleNamespace(
        record=record,
        pty=SimpleNamespace(write=writes.append),
        input_revision=0,
        last_input_event_ts=0.0,
        last_input_report_ts=0.0,
    )

    class Request:
        match_info = {"sid": "s1"}
        app = {
            "voice": service,
            "sessions": SimpleNamespace(resolve=lambda _sid: session),
            "events": events,
        }

        async def json(self) -> dict[str, str]:
            return {"utterance_id": "voice-1", "text": "Run the focused tests"}

    try:
        first = await voice_submit(cast(Any, Request()))
        duplicate = await voice_submit(cast(Any, Request()))
        await asyncio.sleep(0)
        assert json.loads(first.text)["duplicate"] is False
        assert json.loads(duplicate.text)["duplicate"] is True
        assert writes == ["Run the focused tests\r"]
        assert session.input_revision == 1
        assert {event.type for event in emitted} == {
            "terminal_input",
            "voice_prompt_submitted",
        }
    finally:
        service.store.close()


async def test_store_prune_removes_oldest_ready_clips_beyond_cap(tmp_path: Path) -> None:
    store = VoiceStore(tmp_path / "mux.db")
    try:
        base = time.time()
        for index in range(3):
            audio = tmp_path / f"clip-{index}.mp3"
            audio.write_bytes(b"x" * 600)
            await store.add_clip(
                {
                    "id": f"clip-{index}",
                    "session_id": "s1",
                    "agent_run_id": "run-1",
                    "created_at": base + index,
                    "trigger": "auto",
                    "content_mode": "summary",
                    "engine": "edge",
                    "voice": "en-AU-NatashaNeural",
                    "text": "hello",
                    "file_path": str(audio),
                    "format": "mp3",
                    "size_bytes": 600,
                    "duration_hint_s": 1.0,
                    "status": "ready",
                    "error": None,
                    "model": None,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": None,
                }
            )
        removed = await store.prune(1300)
        assert removed == [str(tmp_path / "clip-0.mp3")]
        remaining = await store.clips(session_id="s1")
        assert [row["id"] for row in remaining] == ["clip-2", "clip-1"]
        assert clip_snapshot(remaining[0]).get("file_path") is None
    finally:
        store.close()
