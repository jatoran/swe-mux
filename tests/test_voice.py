from __future__ import annotations

import asyncio
import io
import json
import logging
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.config import load_config, update_config
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.openrouter import OpenRouterResult
from swe_mux.server import (
    session_last_reply,
    voice_approval,
    voice_generate,
    voice_prepare_submit,
    voice_submit,
)
from swe_mux.voice import (
    VOICE_RULE_ID,
    VoiceError,
    VoiceService,
    VoiceStore,
    approval_prompt,
    clip_snapshot,
    estimate_duration_seconds,
    latency_report,
    latency_stages,
    normalize_latency_sample,
    percentile,
    speechify,
    streaming_segments,
)


# Real transcript records rather than stubbed slices. What voice speaks is now
# the same segment the reader tab shows and the copy button copies, so the
# segmentation *is* the behaviour under test: a fixture that skipped the parse
# would assert nothing about the thing that used to be wrong.
def claude_user(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-08-09T10:00:00Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "origin": {"kind": "human"},
    }


def claude_assistant(*texts: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-08-09T10:00:01Z",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text} for text in texts],
        },
    }


CLAUDE_TOOL_USE: dict[str, Any] = {
    "type": "assistant",
    "timestamp": "2026-08-09T10:00:02Z",
    "message": {
        "role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash", "input": {}}],
    },
}

REPLY_EVENTS: list[dict[str, Any]] = [
    claude_user("fix the failing test"),
    # Narration. It belongs to the tool call that follows it, not to the answer.
    claude_assistant("I'll run the suite and see what breaks."),
    CLAUDE_TOOL_USE,
    claude_assistant("## Done\n\nThe test passes now. See `foo.py`."),
    # A streaming split with no tool between: one message, so it merges.
    claude_assistant("```python\nassert True\n```\nNext I suggest a rerun."),
]

CONTROL_ACK_EVENTS: list[dict[str, Any]] = [
    claude_user("complete the implementation"),
    claude_assistant("Implemented it and all checks pass."),
    claude_user("provider control operation"),
    claude_assistant("No response requested."),
]

REPLY_TEXT = (
    "## Done\n\nThe test passes now. See `foo.py`."
    "\n\n```python\nassert True\n```\nNext I suggest a rerun."
)


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


def write_transcript(service: VoiceService, events: list[dict[str, Any]]) -> None:
    """Put real records where the service reads its session's transcript."""
    path = service.sessions.sessions["s1"].transcript_path
    path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )


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
    hot, restart = update_config(config, {"tts_enabled": True, "tts_engine": "kokoro"})
    assert restart == set()
    assert {"tts_enabled", "tts_engine"} <= hot
    with pytest.raises(ValueError, match="tts_engine"):
        update_config(config, {"tts_engine": "espeak"})
    with pytest.raises(ValueError, match="tts_kokoro_speed"):
        update_config(config, {"tts_kokoro_speed": 9.0})
    with pytest.raises(ValueError, match="tts_kokoro_voice"):
        update_config(config, {"tts_kokoro_voice": "Robot Voice!"})
    with pytest.raises(ValueError, match="tts_default_mode"):
        update_config(config, {"tts_default_mode": "always"})
    with pytest.raises(ValueError, match="tts_sapi_rate"):
        update_config(config, {"tts_sapi_rate": 25})
    # Chat patience: hot, bounded, and defaulted for thinking out loud.
    assert config.voice_chat_patience_ms == 1200
    hot, restart = update_config(config, {"voice_chat_patience_ms": 2000})
    assert "voice_chat_patience_ms" in hot and restart == set()
    with pytest.raises(ValueError, match="voice_chat_patience_ms"):
        update_config(config, {"voice_chat_patience_ms": 9000})


def test_tts_lexicon_validates_and_hot_applies(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    hot, restart = update_config(config, {"tts_lexicon": {"vaultspaces": "vault spaces"}})
    assert "tts_lexicon" in hot and restart == set()
    # Round-trips through the TOML file like every other field.
    assert load_config(config.config_path or tmp_path / "config.toml").tts_lexicon == {
        "vaultspaces": "vault spaces"
    }
    with pytest.raises(ValueError, match="tts_lexicon"):
        update_config(config, {"tts_lexicon": {"two words": "nope"}})
    with pytest.raises(ValueError, match="tts_lexicon"):
        update_config(config, {"tts_lexicon": {"vaultspaces": "  "}})
    with pytest.raises(ValueError, match="tts_lexicon"):
        update_config(config, {"tts_lexicon": {"x" * 61: "too long a word"}})
    with pytest.raises(ValueError, match="tts_lexicon"):
        update_config(config, {"tts_lexicon": "vaultspaces"})


async def test_lexicon_check_and_preview_guard_their_inputs(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    # Advisory: no downloaded model is a reported condition, not an error.
    verdicts = await service.check_lexicon({"swe": "swee"})
    assert verdicts["available"] is False and verdicts["results"] == {}
    with pytest.raises(VoiceError, match="entries"):
        await service.check_lexicon("swe")
    with pytest.raises(VoiceError, match="entries"):
        await service.check_lexicon({str(index): "x" for index in range(501)})
    with pytest.raises(VoiceError, match="1–200"):
        await service.lexicon_preview("   ")
    with pytest.raises(VoiceError, match="1–200"):
        await service.lexicon_preview("x" * 201)
    with pytest.raises(VoiceError, match="not downloaded"):
        await service.lexicon_preview("vault spaces")
    with pytest.raises(VoiceError, match="strings"):
        await service.build_lexicon_entry("swe", 7)
    with pytest.raises(VoiceError, match="at most 60"):
        await service.build_lexicon_entry("x" * 61, "swee")
    with pytest.raises(VoiceError, match="not downloaded"):
        await service.build_lexicon_entry("swe", "swee")


def test_apply_lexicon_invalidates_every_kokoro_cache(tmp_path: Path) -> None:
    """A lexicon change must reach a loaded engine, the audition previews, and
    the spelled-word telemetry — or it silently waits for a daemon restart."""
    service, _events, _emitted, _record = make_service(tmp_path)
    applied: list[dict[str, str]] = []
    service._kokoro_engine = cast(Any, SimpleNamespace(set_lexicon=applied.append))
    service._kokoro_previews["af_heart"] = b"stale"
    service.spelled_words.record("vaultspaces")
    service.spelled_words.record("govspend")
    update_config(service.config, {"tts_lexicon": {"Vaultspaces": "vault spaces"}})
    service.apply_lexicon()
    assert applied == [{"Vaultspaces": "vault spaces"}]
    assert service._kokoro_previews == {}
    # The covered word leaves the telemetry list; the still-broken one stays.
    assert [item["word"] for item in service.spelled_words.entries()] == ["govspend"]


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


def test_estimate_duration_is_positive_for_ordinary_text() -> None:
    assert estimate_duration_seconds("one two three four five six", "+0%") > 0


def test_streaming_segments_preserve_text_and_bound_chunks() -> None:
    text = "First result is ready. " + ("A deliberately long sentence " * 30) + "Done!"
    chunks = streaming_segments(text, max_chars=120)
    assert len(chunks) > 2
    assert all(0 < len(chunk) <= 120 for chunk in chunks)
    assert chunks[0] == "First result is ready."
    assert " ".join(chunks) == " ".join(text.split())


def test_streaming_segments_keep_a_comms_sized_reply_coherent() -> None:
    text = (
        "It's an evidence-based hypertrophy training system: a researched exercise "
        "database plus physiological models for fatigue and volume, feeding an optimizer "
        "that searches over a whole seven-day cycle of three full-body sessions rather "
        "than picking exercises one at a time. It outputs a web viewer and reports, and "
        "it also has a training logger you can run locally."
    )
    assert len(text) <= 420
    assert streaming_segments(text) == [text]


def test_a_tighter_opening_clip_only_moves_the_first_cut() -> None:
    # Time-to-first-sound is the opening clip's length, because synthesis is
    # roughly linear in characters. Application speech buys that back by opening
    # short; the tail keeps the wide bound so the rest stays coherent.
    text = (
        "Three sessions are working. The swe-mux session has been running for "
        "eleven minutes, and the other two finished their turns a moment ago, so "
        "nothing is waiting on you right now."
    )
    assert len(text) <= 420
    assert streaming_segments(text) == [text], "the wide default keeps this whole"
    opened = streaming_segments(text, first_max_chars=140)
    assert opened[0] == "Three sessions are working."
    assert len(opened) == 2
    assert " ".join(opened) == " ".join(text.split())


def test_a_long_opening_sentence_still_starts_speaking_early() -> None:
    # Only a sentence longer than the opening bound falls back to a word cut;
    # nothing may be dropped when it does.
    text = "A single deliberately unpunctuated clause that keeps going " * 6 + "end."
    opened = streaming_segments(text, first_max_chars=140)
    assert len(opened[0]) <= 140
    assert " ".join(opened) == " ".join(text.split())


async def last_reply_response(tmp_path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )
    session = SimpleNamespace(
        transcript_path=transcript_path,
        # `native_session_id` is what names the conversation for a harness that
        # keeps one in a store; a file-backed harness carries it and ignores it.
        record=SimpleNamespace(
            backend="claude", agent_run_id="run-1", native_session_id="native-1"
        ),
    )
    request = SimpleNamespace(
        app={"sessions": SimpleNamespace(resolve=lambda _sid: session)},
        match_info={"sid": "s1"},
    )
    response = await session_last_reply(cast(Any, request))
    return {"status": response.status, **json.loads(response.text)}


async def test_last_reply_route_stops_at_the_tool_boundary(tmp_path: Path) -> None:
    """The reported defect: narration written before a tool call is not the reply."""
    payload = await last_reply_response(tmp_path, REPLY_EVENTS)
    assert payload["status"] == 200
    assert payload["agent_run_id"] == "run-1"
    assert payload["text"] == REPLY_TEXT
    assert "I'll run the suite" not in payload["text"]


async def test_last_reply_route_skips_provider_control_acknowledgement(tmp_path: Path) -> None:
    payload = await last_reply_response(tmp_path, CONTROL_ACK_EVENTS)
    assert payload["text"] == "Implemented it and all checks pass."


async def test_last_reply_route_reports_a_conversation_with_no_reply(tmp_path: Path) -> None:
    payload = await last_reply_response(tmp_path, [claude_user("only a question so far")])
    assert payload["status"] == 409
    assert "no assistant reply" in payload["error"]


async def test_generate_verbatim_produces_ready_clip_and_event(tmp_path: Path) -> None:
    service, _events, emitted, _record = make_service(tmp_path, content="verbatim")
    write_transcript(service, REPLY_EVENTS)
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
    write_transcript(service, REPLY_EVENTS)
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
    write_transcript(service, REPLY_EVENTS)
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
    write_transcript(service, REPLY_EVENTS)
    patch_engine(service, fail="Kokoro synthesis failed: boom")
    try:
        with pytest.raises(VoiceError, match="boom"):
            await service.generate("s1", trigger="manual")
        stored = await service.store.clips(session_id="s1")
        assert stored[0]["status"] == "failed" and "boom" in str(stored[0]["error"])
        assert [event.type for event in emitted] == ["voice_clip_failed"]
    finally:
        service.store.close()


async def test_master_switch_off_blocks_manual_generation(tmp_path: Path) -> None:
    """The master gates generation everywhere, not only on the automatic path.

    `tts_enabled` off means no session generates audio. Before Phase 15 the manual
    "speak this reply" path never consulted it, so the install-wide switch was a
    master only for the paths that happened to check.
    """
    service, _events, emitted, record = make_service(tmp_path, tts_enabled=False)
    record.voice_mode = "auto"  # an explicit mode must not out-rank the master
    write_transcript(service, REPLY_EVENTS)
    patch_engine(service)
    try:
        with pytest.raises(VoiceError, match="read aloud is off"):
            await service.generate("s1", trigger="manual")
        assert emitted == []
        assert await service.store.clips(session_id="s1") == []
        service.config.tts_enabled = True
        clip = await service.generate("s1", trigger="manual")
        assert clip["status"] == "ready"
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
    write_transcript(service, REPLY_EVENTS)
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


async def test_one_shot_content_override_does_not_change_session_mode(tmp_path: Path) -> None:
    ledger = AutomationStoreStub()
    provider = ProviderStub()
    service, _events, _emitted, record = make_service(
        tmp_path, content="summary", provider=provider, automation_store=ledger
    )
    record.voice_content = "summary"
    write_transcript(service, REPLY_EVENTS)
    patch_engine(service)
    try:
        clip = await service.generate(
            "s1", trigger="manual", content_mode="verbatim"
        )
        assert clip["content_mode"] == "verbatim"
        assert record.voice_content == "summary"
        assert provider.calls == []
        with pytest.raises(VoiceError, match="content mode"):
            await service.generate("s1", trigger="manual", content_mode="brief")
    finally:
        service.store.close()


async def test_voice_generate_route_forwards_validated_one_shot_mode() -> None:
    calls: list[dict[str, Any]] = []

    class VoiceStub:
        async def generate(self, session_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"session_id": session_id, **kwargs})
            return {"id": "clip-1"}

    request = SimpleNamespace(
        app={
            "voice": VoiceStub(),
            "sessions": SimpleNamespace(
                resolve=lambda _sid: SimpleNamespace(record=SimpleNamespace(id="s1"))
            ),
        },
        match_info={"sid": "s1"},
        can_read_body=True,
        json=lambda: asyncio.sleep(0, result={"content_mode": "verbatim"}),
    )
    response = await voice_generate(cast(Any, request))
    assert response.status == 200
    assert calls == [
        {"session_id": "s1", "trigger": "manual", "content_mode": "verbatim"}
    ]

    request.json = lambda: asyncio.sleep(
        0,
        result={
            "content_mode": "summary",
            "stream_id": "11111111-1111-4111-8111-111111111111",
        },
    )
    await voice_generate(cast(Any, request))
    assert calls[-1]["stream_id"] == "11111111-1111-4111-8111-111111111111"


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
    write_transcript(service, REPLY_EVENTS)
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
    write_transcript(service, REPLY_EVENTS)
    spoken = patch_engine(service)
    try:
        first = await service.generate("s1", trigger="auto")
        assert first["segment_count"] >= 2
        assert len(first["text"]) <= 420
        assert first["text"].endswith(".")
        assert len(spoken) == 1
        await asyncio.gather(*tuple(service._segment_tasks))
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


async def test_daemon_stt_validates_wav_and_reports_stage_timings(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    # STT is off by default now, so the capture path is opt-in; this test exercises it.
    service.config.stt_enabled = True
    service.config.stt_engine = "sapi"
    seen: list[bytes] = []

    async def transcribe(audio: bytes, marks: dict[str, Any]) -> str:
        seen.append(audio)
        marks["decode_start"] = time.perf_counter()
        marks["decode_end"] = time.perf_counter()
        return "  Mux   send that  "

    service._transcribe_sapi = transcribe  # type: ignore[method-assign]
    try:
        result = await service.transcribe_wav(wav_bytes())
        assert result.text == "Mux send that"
        assert result.audio_ms == pytest.approx(200, abs=1)
        assert result.queue_ms >= 0 and result.decode_ms >= 0
        assert seen == [wav_bytes()]
        with pytest.raises(VoiceError, match="valid WAV"):
            await service.transcribe_wav(b"not audio")
        # The capture contract is one rate now that decoding runs from raw PCM;
        # accepting another would resample silently and mis-time every utterance.
        with pytest.raises(VoiceError, match="16000 Hz"):
            await service.transcribe_wav(wav_bytes(rate=8_000))
    finally:
        service.store.close()


def test_whisper_decoding_never_touches_the_disk(tmp_path: Path) -> None:
    """Audio is discarded by construction, not by a sweep that can lose a race."""
    service, _events, _emitted, _record = make_service(tmp_path)
    seen: list[Any] = []

    class Model:
        def transcribe(self, samples: Any, **_options: object):
            seen.append(samples)
            return [SimpleNamespace(text=" run the tests ")], SimpleNamespace()

    service._whisper_models["turbo"] = Model()
    marks: dict[str, Any] = {}
    try:
        _length, frames = service._validate_wav(wav_bytes(seconds=0.1))
        assert service._transcribe_whisper(frames, 100, "dictation", marks) == "run the tests"
        assert not (service.clip_directory / "stt").exists()
        assert len(seen[0]) == 1_600
    finally:
        service.store.close()


def test_whisper_transcription_uses_accuracy_and_technical_context(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    calls: list[dict[str, object]] = []

    class Model:
        def transcribe(self, _path: str, **options: object):
            calls.append(options)
            return [SimpleNamespace(text=" Run the TypeScript tests. ")], SimpleNamespace()

    service._whisper_models["turbo"] = Model()
    marks: dict[str, Any] = {}
    try:
        result = service._run_whisper_transcription("turbo", [], 12_000, "dictation", marks)
        assert result == "Run the TypeScript tests."
        # The decode window is bracketed inside the decoder, so a first-use model
        # download or a CUDA→CPU reload is reported as queueing, not as decode time.
        assert marks["decode_end"] >= marks["decode_start"]
        assert marks["beam_size"] == calls[0]["beam_size"] and marks["model"] == "turbo"
        assert calls[0]["beam_size"] == 5
        assert calls[0]["condition_on_previous_text"] is False
        assert calls[0]["vad_filter"] is False
        assert "TypeScript" in str(calls[0]["hotwords"])
    finally:
        service.store.close()


def test_decoder_choice_splits_the_reflex_path_from_dictation(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        # A spoken command is a reflex: small model, greedy, no beam search.
        assert service.decode_model("command") == "small.en"
        assert service.beam_size("command", 12_000) == 1
        # Dictation gets the accurate model, and beam search only where the extra
        # 30% of decode time buys accuracy that shows up in the text.
        assert service.decode_model("dictation") == "turbo"
        assert service.beam_size("dictation", 1_600) == 1
        assert service.beam_size("dictation", 12_000) == 5
        # A blank routing model is not a failure; commands decode on the dictation
        # model, slower but correct.
        service.config.stt_routing_model = "  "
        assert service.decode_model("command") == "turbo"
    finally:
        service.store.close()


def test_routing_hotwords_carry_the_wake_words_and_stay_bounded(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    service.config.voice_wake_words = ["swee", "swe", "swee"]
    service.config.voice_commands = [{"action": "send", "phrases": ["ship it"]}]
    try:
        routing = service._hotwords("command")
        # A made-up trigger word is where a general model is weakest, so it is worth
        # biasing toward. Command phrases are not: adding the default 57 of them was
        # measured driving small.en into a repetition loop at 16x the decode time.
        assert routing.startswith("swee, swe, ")
        assert "ship it" not in routing
        assert "TypeScript" in routing
        assert "TypeScript" in service._hotwords("dictation")

        # `voice_wake_words` allows 64 entries, and a long list of short similar
        # tokens is exactly the shape that made the decoder loop.
        service.config.voice_wake_words = [f"word{index}" for index in range(40)]
        bounded = service._hotwords("command")
        assert bounded.startswith("word0, ") and "word7" in bounded and "word8" not in bounded
    finally:
        service.store.close()


def test_routing_model_failure_falls_back_to_the_dictation_model(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    loaded: list[str] = []

    class Model:
        def __init__(self, name: str, *, device: str, compute_type: str) -> None:
            loaded.append(name)
            if name == "small.en":
                raise RuntimeError("model not found")

    try:
        # The routing model is a latency optimisation. Losing it must cost speed,
        # not the command path.
        assert service._ensure_whisper_model(Model, "command") == "turbo"
        # A machine with a visible GPU retries the routing model on CPU first, so
        # assert the order rather than the exact attempt count.
        assert loaded[0] == "small.en" and loaded[-1] == "turbo"
        with pytest.raises(VoiceError, match="could not load"):
            service.config.stt_whisper_model = "small.en"
            service._ensure_whisper_model(Model, "dictation")
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
        service._load_whisper_model(Model, "turbo")
        assert calls == [("cuda", "float16"), ("cpu", "int8")]
        assert service._whisper_devices["turbo"] == "cpu"
        assert service._whisper_models["turbo"] is not None
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


async def test_system_speech_is_synthesized_without_a_model(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    spoken = patch_engine(service)
    try:
        clip = await service.speak("Three sessions: one active.")
        assert clip["session_id"] == "system"
        assert clip["trigger"] == "system"
        assert clip["content_mode"] == "verbatim"
        assert spoken == ["Three sessions: one active."]
    finally:
        service.store.close()


def stream_events(emitted: list[MuxEvent], stream_id: str) -> list[MuxEvent]:
    return [
        event
        for event in emitted
        if event.type in {"voice_clip_ready", "voice_stream_closed"}
        and event.payload.get("stream_id") == stream_id
    ]


async def test_an_open_speech_stream_appends_in_order_and_closes(tmp_path: Path) -> None:
    # The assistant speaks a turn sentence by sentence, so the stream's length is
    # unknown while it runs: segments carry count 0 until the closing one, which
    # carries the real total and lets the browser release its claim.
    service, _events, emitted, _record = make_service(tmp_path)
    spoken = patch_engine(service)
    stream = "11111111-1111-4111-8111-111111111111"
    try:
        first = await service.speak("Opening sentence.", stream_id=stream, final=False)
        assert first["stream_open"] is True
        assert first["segment_count"] == 0
        await service.speak(
            "Second sentence.", stream_id=stream, continue_stream=True, final=False
        )
        await service.speak(
            "Third sentence.", stream_id=stream, continue_stream=True, final=True
        )
        await asyncio.sleep(0.05)
        assert spoken == ["Opening sentence.", "Second sentence.", "Third sentence."]
        events = stream_events(emitted, stream)
        clips = [event for event in events if event.type == "voice_clip_ready"]
        assert [event.payload["segment_index"] for event in clips] == [0, 1, 2]
        # Only the closing clip names a count; anything earlier would tell the
        # browser the stream had ended and drop every later sentence.
        assert [event.payload["segment_count"] for event in clips] == [0, 0, 3]
        assert events[-1].type == "voice_stream_closed"
        assert events[-1].payload["segment_count"] == 3
    finally:
        service.store.close()


async def test_an_open_stream_closes_with_nothing_left_to_say(tmp_path: Path) -> None:
    # A turn that ends on a tool result has no closing sentence, so the last clip
    # it emitted carries no real count. Without an explicit close the browser
    # would hold the stream claimed and let a later turn's clips join it.
    service, _events, emitted, _record = make_service(tmp_path)
    patch_engine(service)
    stream = "22222222-2222-4222-8222-222222222222"
    try:
        await service.speak("Working on it.", stream_id=stream, final=False)
        await asyncio.sleep(0.05)
        closed = await service.close_speech_stream(stream)
        assert closed == {
            "stream_id": stream, "closed": True, "known": True, "segment_count": 1
        }
        assert stream_events(emitted, stream)[-1].type == "voice_stream_closed"
        # Closing twice is not an error; the second call simply finds nothing.
        assert (await service.close_speech_stream(stream))["known"] is False
    finally:
        service.store.close()


async def test_appending_to_a_closed_stream_is_refused(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    patch_engine(service)
    stream = "33333333-3333-4333-8333-333333333333"
    try:
        await service.speak("All done.", stream_id=stream, final=True)
        await asyncio.sleep(0.05)
        with pytest.raises(VoiceError, match="closed"):
            await service.speak(
                "Late text.", stream_id=stream, continue_stream=True, final=True
            )
    finally:
        service.store.close()


async def test_a_failed_segment_ends_its_stream_rather_than_reordering_it(
    tmp_path: Path,
) -> None:
    # A gap cannot be skipped: speaking sentence three after sentence one failed
    # would read the reply out of order. The stream ends, and says so.
    service, _events, emitted, _record = make_service(tmp_path)
    patch_engine(service)
    stream = "44444444-4444-4444-8444-444444444444"
    try:
        await service.speak("First.", stream_id=stream, final=False)
        patch_engine(service, fail="engine unavailable")
        await service.speak(
            "Second.", stream_id=stream, continue_stream=True, final=False
        )
        await asyncio.sleep(0.05)
        closed = [
            event for event in stream_events(emitted, stream)
            if event.type == "voice_stream_closed"
        ]
        assert closed and closed[-1].payload["failed"] is True
    finally:
        service.store.close()


def test_approval_challenge_is_bound_to_the_exact_prompt(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        operation, fingerprint = approval_prompt(
            "Bash command\n  npm test\n\nDo you want to proceed?\n❯ 1. Yes\n  2. No"
        )
        assert operation == "npm test"
        codex_operation, _ = approval_prompt(
            "Run command\nuv run pytest\nAllow Codex to run this command?\n1. Yes\n2. No"
        )
        assert codex_operation == "Run command uv run pytest"
        challenge = service.prepare_approval("s1", "run-1", operation, fingerprint)
        with pytest.raises(VoiceError, match="prompt changed"):
            service.consume_approval(
                "s1", challenge.confirmation_id, "run-1", "different"
            )
        with pytest.raises(VoiceError, match="missing or was already used"):
            service.consume_approval(
                "s1", challenge.confirmation_id, "run-1", fingerprint
            )
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


async def test_voice_submit_pastes_a_multiline_draft_instead_of_submitting_early(
    tmp_path: Path,
) -> None:
    """An edited dictation draft can hold newlines; a raw newline submits early.

    Recognition never emits one, so this only happens once the draft is editable —
    and getting it wrong sends the agent half a prompt and types the rest at
    whatever it shows next.
    """
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
            return {"utterance_id": "voice-2", "text": "first line\nsecond line"}

    try:
        response = await voice_submit(cast(Any, Request()))
        await asyncio.sleep(0)
        assert json.loads(response.text)["duplicate"] is False
        assert writes == ["\x1b[200~first line\rsecond line\x1b[201~", "\r"]
        assert "\n" not in "".join(writes)
        assert session.input_revision == 2
        assert "voice_prompt_submitted" in {event.type for event in emitted}
    finally:
        service.store.close()


async def test_voice_submit_refuses_a_protected_approval_prompt(tmp_path: Path) -> None:
    service, events, _emitted, record = make_service(tmp_path)
    record.state = "awaiting"
    record.awaiting_reason = "approval"
    writes: list[str] = []
    session = SimpleNamespace(record=record, pty=SimpleNamespace(write=writes.append))

    class Request:
        match_info = {"sid": "s1"}
        app = {
            "voice": service,
            "sessions": SimpleNamespace(resolve=lambda _sid: session),
            "events": events,
        }

        async def json(self) -> dict[str, str]:
            return {"utterance_id": "protected-1", "text": "type this"}

    try:
        response = await voice_submit(cast(Any, Request()))
        payload = json.loads(response.text)
        assert response.status == 409
        assert payload["code"] == "delivery_protected"
        assert payload["reasons"] == ["approval_required"]
        assert writes == []
    finally:
        service.store.close()


async def test_voice_prepare_submit_guards_without_writing(tmp_path: Path) -> None:
    service, events, _emitted, record = make_service(tmp_path)
    writes: list[str] = []
    session = SimpleNamespace(
        record=record,
        pty=SimpleNamespace(write=writes.append),
    )

    class Request:
        match_info = {"sid": "s1"}
        app = {
            "voice": service,
            "sessions": SimpleNamespace(resolve=lambda _sid: session),
            "events": events,
        }

        async def json(self) -> dict[str, object]:
            return {"text": "append this", "submit": True}

    try:
        ready = await voice_prepare_submit(cast(Any, Request()))
        assert json.loads(ready.text) == {
            "ok": True,
            "session_id": "s1",
            "agent_run_id": "run-1",
        }
        assert writes == []

        record.state = "awaiting"
        record.awaiting_reason = "approval"
        protected = await voice_prepare_submit(cast(Any, Request()))
        assert protected.status == 409
        assert json.loads(protected.text)["reasons"] == ["approval_required"]
        assert writes == []
    finally:
        service.store.close()


async def test_voice_approval_requires_prepare_and_rechecks_the_screen(tmp_path: Path) -> None:
    service, events, emitted, record = make_service(tmp_path)
    record.state = "awaiting"
    record.awaiting_reason = "approval"
    tail = bytearray(
        "Bash command\n  npm test\n\nDo you want to proceed?\n❯ 1. Yes\n  2. No".encode()
    )
    writes: list[str] = []
    session = SimpleNamespace(
        record=record,
        pty=SimpleNamespace(write=writes.append),
        scrollback=SimpleNamespace(tail_bytes=lambda _limit: bytes(tail)),
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
        body: dict[str, str] = {"action": "prepare"}

        async def json(self) -> dict[str, str]:
            return self.body

    request = Request()
    try:
        prepared = json.loads((await voice_approval(cast(Any, request))).text)
        assert prepared["operation"] == "npm test"
        assert writes == []
        request.body = {
            "action": "confirm",
            "confirmation_id": prepared["confirmation_id"],
        }
        confirmed = await voice_approval(cast(Any, request))
        await asyncio.sleep(0)
        assert confirmed.status == 200
        assert writes == ["\r"]
        assert "voice_approval_confirmed" in {event.type for event in emitted}
        duplicate = await voice_approval(cast(Any, request))
        assert duplicate.status == 409
        assert writes == ["\r"]
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


def latency_sample(**overrides: Any) -> dict[str, Any]:
    sample = {
        "utterance_id": "u1",
        "audio_ms": 1600,
        "endpoint_ms": 900,
        "encode_ms": 12,
        "wait_ms": 3,
        "upload_ms": 10,
        "queue_ms": 40,
        "decode_ms": 240,
        "action_ms": 13,
        "total_ms": 1218,
        "command": "send",
    }
    sample.update(overrides)
    return sample


def test_latency_sample_clamps_untrusted_browser_numbers() -> None:
    stored = normalize_latency_sample(
        latency_sample(decode_ms=-5, endpoint_ms="nonsense", total_ms=1e12)
    )
    # A readout that can be poisoned into showing impossible stages is worse than
    # none, because it is still believed.
    assert stored["decode_ms"] == 0
    assert stored["endpoint_ms"] == 0
    assert stored["total_ms"] == 600_000
    assert stored["queue_ms"] == 40
    assert stored["at"] > 0


def test_latency_sample_rejects_a_non_object_and_scrubs_the_correlation_id() -> None:
    with pytest.raises(VoiceError):
        normalize_latency_sample(["not", "an", "object"])
    stored = normalize_latency_sample(latency_sample(utterance_id="a b/c<script>"))
    assert stored["utterance_id"] == "abcscript"


def test_latency_stages_sum_the_recorded_fields() -> None:
    stages = latency_stages(normalize_latency_sample(latency_sample()))
    assert stages == {
        "to_post_ms": 915.0,
        "to_decode_ms": 50.0,
        "decode_ms": 240.0,
        "action_ms": 13.0,
    }
    assert sum(stages.values()) == 1218.0


def test_percentile_is_nearest_rank_and_survives_an_empty_run() -> None:
    assert percentile([], 0.5) == 0.0
    assert percentile([10.0], 0.95) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0


def test_latency_report_separates_commands_from_dictation() -> None:
    samples = [
        normalize_latency_sample(latency_sample(total_ms=1200, command="send")),
        normalize_latency_sample(latency_sample(total_ms=1400, command="send")),
        # Dictation decodes several times longer audio, so blending the two totals
        # would answer neither the exit criterion nor the dictation question.
        normalize_latency_sample(latency_sample(total_ms=2600, command="", decode_ms=700)),
    ]
    report = latency_report(samples)
    assert report["count"] == 3
    assert report["total_ms"]["p50"] == 1400
    assert report["command_total_ms"] == {"count": 2, "p50": 1200, "p95": 1400}
    assert report["stages"]["decode_ms"]["max"] == 700
    assert [item["stages"]["decode_ms"] for item in report["recent"]] == [240, 240, 700]


def test_latency_report_is_empty_before_anything_is_spoken() -> None:
    report = latency_report([])
    assert report["count"] == 0
    assert report["stages"]["decode_ms"] == {"p50": 0.0, "p95": 0.0, "max": 0.0}
    assert report["recent"] == []


def test_service_records_and_clears_latency_samples(tmp_path: Path) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        service.record_stt_latency(latency_sample())
        service.record_stt_latency(latency_sample(total_ms=1400))
        assert service.stt_latency_report()["count"] == 2
        service.clear_stt_latency()
        assert service.stt_latency_report()["count"] == 0
    finally:
        service.store.close()


def test_service_validates_and_logs_barge_in_diagnostics(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        with caplog.at_level(logging.INFO, logger="swe_mux.voice"):
            sample = service.record_barge_in_diagnostic(
                {
                    "outcome": "confirmed",
                    "detector": "silero",
                    "origin": "system",
                    "peakProbability": 1.4,
                    "peakRms": 0.023456,
                }
            )
        assert sample == {
            "outcome": "confirmed",
            "detector": "silero",
            "origin": "system",
            "peak_probability": 1.0,
            "peak_rms": 0.0235,
        }
        assert '"outcome": "confirmed"' in caplog.text
        with pytest.raises(VoiceError, match="outcome"):
            service.record_barge_in_diagnostic({"outcome": "maybe"})
    finally:
        service.store.close()


def test_service_validates_and_logs_capture_diagnostics(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A capture stall is durable daemon evidence, not just a phone-side phase.

    The outage this pins (2026-08-20): the phone stopped POSTing audio while the
    UI said "listening", and the only evidence was the access log read after the
    fact. A stall must land in the log at WARNING, a recovery at INFO, and junk
    numbers must be bounded rather than trusted.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        with caplog.at_level(logging.INFO, logger="swe_mux.voice"):
            stalled = service.record_capture_diagnostic(
                {
                    "event": "stalled",
                    "detector": "silero",
                    "silentMs": 6120.7,
                    "contextState": "suspended",
                    "trackState": "live",
                    "trackMuted": False,
                    "recoveryAttempts": 1,
                }
            )
        assert stalled == {
            "event": "stalled",
            "detector": "silero",
            "silent_ms": 6120,
            "context_state": "suspended",
            "track_state": "live",
            "track_muted": False,
            "recovery_attempts": 1,
        }
        stall_records = [
            record for record in caplog.records if "voice capture stall" in record.getMessage()
        ]
        assert stall_records and stall_records[0].levelno == logging.WARNING
        recovered = service.record_capture_diagnostic(
            {
                "event": "recovered",
                "detector": "energy",
                "silentMs": -50,
                "recoveryAttempts": 10**9,
            }
        )
        assert recovered["silent_ms"] == 0
        assert recovered["recovery_attempts"] == 100_000
        assert recovered["context_state"] == "unknown"
        with pytest.raises(VoiceError, match="event"):
            service.record_capture_diagnostic({"event": "listening", "detector": "silero"})
        with pytest.raises(VoiceError, match="detector"):
            service.record_capture_diagnostic({"event": "stalled", "detector": "sonar"})
        with pytest.raises(VoiceError, match="object"):
            service.record_capture_diagnostic("stalled")
    finally:
        service.store.close()
