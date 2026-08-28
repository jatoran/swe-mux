"""Speech in and out: models, transcription, submission, and clips."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..edge_tts_provider import EdgeTtsError
from ..event_bus import EventBus
from ..harness import (
    delivers_prompts_through_pty,
    is_agent_harness,
)
from ..http_support import json_response
from ..prompt_queue import (
    NON_OVERRIDABLE_REASONS,
    SUBMIT_DELAY_SECONDS,
    SUBMIT_SEQUENCE,
)
from ..scrollback import SCREEN_TAIL_BYTES
from ..session import (
    pty_tail_state,
    session_cli_state_status,
)
from ..tts_profiles import resolve_tts_profile
from ..voice import (
    COMMAND_PROFILE,
    DICTATION_PROFILE,
    VoiceError,
    VoiceService,
    VoiceStore,
    approval_prompt,
    group_snapshot,
)
from ..voice_models import VoiceModelError
from . import terminal

log = logging.getLogger(__name__)


async def voice_status(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    return json_response(await voice.status())


async def edge_provider_status(request: web.Request) -> web.Response:
    """Cached local state only. Starting the external integration is explicit."""

    voice: VoiceService = request.app[keys.VOICE]
    return json_response(voice.edge_tts.status())


async def edge_provider_probe(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    return json_response(await voice.edge_tts.probe())


async def edge_provider_install(request: web.Request) -> web.Response:
    """Explicitly create or repair the managed external Edge TTS environment."""

    if request.headers.get("X-Mux-User-Gesture") != "edge-tts-install":
        return json_response(
            {"error": "managed Edge TTS installation requires an explicit user action"}, 403
        )
    voice: VoiceService = request.app[keys.VOICE]
    try:
        started = voice.edge_tts.start_managed_install()
    except OSError as exc:
        return json_response({"error": f"could not start the managed installation: {exc}"}, 500)
    return json_response({"started": started, **voice.edge_tts.status()}, 202)


async def edge_voice_catalog(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    return json_response(
        voice.edge_tts.catalog.snapshot(selected=voice.config.tts_edge_voice)
    )


async def edge_voice_refresh(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    try:
        return json_response(await voice.edge_tts.refresh_voices())
    except EdgeTtsError as exc:
        return json_response({"error": str(exc), "code": exc.code}, 503)


async def edge_voice_preview(request: web.Request) -> web.Response:
    """Explicitly synthesize a fixed, non-sensitive audition sentence."""

    voice: VoiceService = request.app[keys.VOICE]
    voice_id = str(request.query.get("voice") or "")
    if not voice_id or len(voice_id) > 160 or not voice_id.endswith("Neural"):
        return json_response({"error": "invalid Edge voice id"}, 400)
    profile = resolve_tts_profile(voice.config, provider="edge", voice_override=voice_id)
    destination = voice.clip_directory / f"edge-preview-{uuid.uuid4().hex}.mp3"
    try:
        await voice.edge_tts.synthesize(
            profile,
            "This is a preview of the selected Edge voice.",
            destination,
            automatic=False,
        )
        data = destination.read_bytes()
    except EdgeTtsError as exc:
        return json_response({"error": str(exc), "code": exc.code}, 503)
    finally:
        with suppress(OSError):
            destination.unlink(missing_ok=True)
    return web.Response(body=data, content_type="audio/mpeg", headers={"Cache-Control": "no-store"})


async def kokoro_model_status(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    return json_response(voice.kokoro_models.status())


async def whisper_model_status(request: web.Request) -> web.Response:
    """The four-state report for the configured dictation and routing models.

    A read only: it resolves what is already on disk and never reaches the
    network, which is the whole point of separating this from the download.
    """
    voice: VoiceService = request.app[keys.VOICE]
    requested = str(request.query.get("model") or "").strip()
    names = (
        (requested,)
        if requested
        else (voice.config.stt_whisper_model, voice.decode_model(COMMAND_PROFILE))
    )
    models = voice.whisper_models.statuses(*names)
    return json_response({"models": models})


async def whisper_model_download(request: web.Request) -> web.Response:
    """Start the Whisper weights download (idempotent while one is running).

    The explicit act the transcription path refuses to perform on the operator's
    behalf. Progress reaches every client over the event stream, because the
    download outlives the request and may have been started from another device.
    """
    voice: VoiceService = request.app[keys.VOICE]
    events: EventBus = request.app[keys.EVENTS]
    body = await request.json() if request.can_read_body else {}
    if not isinstance(body, dict):
        raise ValueError("whisper download body must be an object")
    name = str(body.get("model") or voice.config.stt_whisper_model).strip()

    async def progress(status: dict[str, Any]) -> None:
        await events.emit("voice_model_progress", source="daemon", **status)

    try:
        started = voice.whisper_models.start_download(name, progress)
    except VoiceModelError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(
        {"started": started, **voice.whisper_models.status(name)}, 202
    )


async def kokoro_voice_preview(request: web.Request) -> web.Response:
    """Audition one Kokoro voice: WAV bytes straight back, no clip machinery.

    The settings picker taps through voices before any of them is configured,
    so this must work whatever `tts_engine` currently is. Samples are cached
    per voice on the service for the daemon's lifetime.

    A GET a media element can point at directly, not a POST the client turns
    into a blob: the document CSP has no `media-src`, so `default-src 'self'`
    governs media and a `blob:` URL is refused ("no supported source") while a
    same-origin URL plays — the same reason clip playback streams from
    `/api/voice/clips/{id}/audio` rather than from fetched bytes.
    """
    voice: VoiceService = request.app[keys.VOICE]
    try:
        data = await voice.kokoro_preview(str(request.query.get("voice") or ""))
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return web.Response(
        body=data,
        content_type="audio/wav",
        headers={"Cache-Control": "private, max-age=3600"},
    )


async def voice_lexicon_check(request: web.Request) -> web.Response:
    """Advisory pronunciation verdicts for lexicon entries being edited.

    The Settings editor sends the draft entries and shows ✓/✗ per row, so a
    respelling that would be rejected by the ladder's re-verification (and end
    up spelled out anyway) is visible before Save instead of failing silently.
    """
    voice: VoiceService = request.app[keys.VOICE]
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("lexicon check body must be an object")
    try:
        return json_response(await voice.check_lexicon(body.get("entries")))
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)


async def voice_lexicon_build(request: web.Request) -> web.Response:
    """Derive an exact-pronunciation lexicon value from a phonetic spelling.

    `{word, value}` → `{ok, value, phonemes, diagnostic}`. An empty value reads
    the word itself as its phonetic spelling. Failure to build is a verdict in
    a 200, not an HTTP error — the editor shows the diagnostic inline.
    """
    voice: VoiceService = request.app[keys.VOICE]
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("lexicon build body must be an object")
    try:
        result = await voice.build_lexicon_entry(
            body.get("word") or "", body.get("value") or ""
        )
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(result)


async def voice_lexicon_preview(request: web.Request) -> web.Response:
    """Audition one respelling value: WAV bytes straight back.

    A GET a media element can point at directly, for the same CSP reason as
    the voice picker preview (no `media-src`, so `blob:` sources are refused).
    Uncached: the value under audition changes as the user types.
    """
    voice: VoiceService = request.app[keys.VOICE]
    try:
        data = await voice.lexicon_preview(str(request.query.get("text") or ""))
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return web.Response(
        body=data,
        content_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


async def kokoro_model_download(request: web.Request) -> web.Response:
    """Start the pinned, hash-verified Kokoro download (idempotent while running).

    Progress reaches every client over the event stream, because the download
    outlives any single request and may have been started from another device.
    """
    voice: VoiceService = request.app[keys.VOICE]
    events: EventBus = request.app[keys.EVENTS]

    async def progress(status: dict[str, Any]) -> None:
        await events.emit("voice_model_progress", source="daemon", model="kokoro", **status)

    started = voice.kokoro_models.start_download(progress)
    return json_response({"started": started, **voice.kokoro_models.status()}, 202)


async def voice_transcribe(request: web.Request) -> web.Response:
    # Taken before anything else so the reported queue cost covers the body read
    # and the STT lock wait, not just the part of the path VoiceService can see.
    received_at = time.perf_counter()
    voice: VoiceService = request.app[keys.VOICE]
    # The session is what dictation is *for*, not what transcription needs. The
    # session-free form exists so the wake-word tester measures the real decoder and
    # the real grammar rather than a parallel implementation of both.
    sid = request.match_info.get("sid")
    if sid:
        session = request.app[keys.SESSIONS].resolve(sid)
        if not is_agent_harness(session.record.backend):
            return json_response({"error": "conversation mode requires an agent session"}, 409)
    if request.content_type not in {"audio/wav", "audio/x-wav", "application/octet-stream"}:
        return json_response({"error": "voice transcription requires WAV audio"}, 415)
    if request.content_length is not None and request.content_length > 2 * 1024 * 1024:
        return json_response({"error": "voice utterance must not exceed 2 MiB"}, 413)
    correlation_id = re.sub(
        r"[^A-Za-z0-9_.:-]", "", str(request.headers.get("X-Mux-Utterance-Id", ""))[:100]
    )
    profile = str(request.headers.get("X-Mux-Decode-Profile", "")).strip().lower()
    try:
        audio = await request.read()
        result = await voice.transcribe_wav(
            audio,
            received_at=received_at,
            correlation_id=correlation_id,
            profile=profile or DICTATION_PROFILE,
        )
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    # `server_ms` lets the client subtract the daemon's own time from the round
    # trip and be left with transport, which it cannot measure any other way.
    timings = result.timings()
    timings["server_ms"] = round((time.perf_counter() - received_at) * 1000, 1)
    return json_response({"text": result.text, "timings": timings})


async def voice_latency(request: web.Request) -> web.Response:
    """The end-of-speech-to-action stage breakdown.

    GET reports it, POST records one browser-measured sample, DELETE starts a fresh
    measurement run. Samples are also written to `daemon.log`, which is what makes a
    latency complaint answerable after a restart has emptied the ring.
    """
    voice: VoiceService = request.app[keys.VOICE]
    if request.method == "DELETE":
        voice.clear_stt_latency()
    elif request.method == "POST":
        try:
            voice.record_stt_latency(await request.json())
        except VoiceError as exc:
            return json_response({"error": str(exc)}, 400)
    return json_response(voice.stt_latency_report())


async def voice_barge_in_diagnostic(request: web.Request) -> web.Response:
    """Record whether the playback sidechain confirmed speech or rejected echo."""
    voice: VoiceService = request.app[keys.VOICE]
    try:
        sample = voice.record_barge_in_diagnostic(await request.json())
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(sample)


async def voice_playback_diagnostic(request: web.Request) -> web.Response:
    """Record one browser audio-file handoff and whether the next clip was ready."""
    voice: VoiceService = request.app[keys.VOICE]
    try:
        sample = voice.record_playback_diagnostic(await request.json())
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(sample)


async def voice_capture_diagnostic(request: web.Request) -> web.Response:
    """Record a browser-side capture stall or recovery from the frame watchdog."""
    voice: VoiceService = request.app[keys.VOICE]
    try:
        sample = voice.record_capture_diagnostic(await request.json())
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(sample)


async def voice_deferral_diagnostic(request: web.Request) -> web.Response:
    """Record one unfinished-utterance deferral and the outcome that judges it."""
    voice: VoiceService = request.app[keys.VOICE]
    try:
        sample = voice.record_deferral_diagnostic(await request.json())
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 400)
    return json_response(sample)


def _validate_voice_terminal_text(session: Any, text: str) -> None:
    if not delivers_prompts_through_pty(session.record.backend):
        raise VoiceError("conversation mode requires an agent session")
    if session.record.state in {"exited", "crashed"}:
        raise VoiceError("the agent session has ended")
    if not text or len(text) > 20_000:
        raise ValueError("voice prompt must contain 1–20000 characters")
    if any(ord(character) < 32 and character not in {"\t", "\n"} for character in text):
        raise ValueError("voice prompt contains terminal control characters")


def _voice_delivery_protected(app: Any, session: Any) -> list[str]:
    fleet = app.get(keys.FLEET)
    readiness_reasons = set(fleet.readiness.evaluate(session)["reasons"]) if fleet else set()
    if session.record.state in {"exited", "crashed"}:
        readiness_reasons.add("session_ended")
    if session.record.state == "awaiting":
        if session.record.awaiting_reason == "approval":
            readiness_reasons.add("approval_required")
        elif session.record.awaiting_reason in {"question", "elicitation"}:
            readiness_reasons.add("awaiting_user_input")
    return sorted(readiness_reasons & NON_OVERRIDABLE_REASONS)


def _voice_delivery_protected_response(protected: list[str]) -> web.Response:
    return json_response(
        {
            "error": "voice delivery is protected until the agent prompt is safe",
            "code": "delivery_protected",
            "reasons": protected,
        },
        409,
    )


async def voice_prepare_submit(request: web.Request) -> web.Response:
    """Validate a Talk append before the browser uses the mounted terminal path."""
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    text = str((await request.json()).get("text") or "").strip()
    try:
        _validate_voice_terminal_text(session, text)
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    protected = _voice_delivery_protected(request.app, session)
    if protected:
        return _voice_delivery_protected_response(protected)
    return json_response(
        {"ok": True, "session_id": session.record.id, "agent_run_id": session.record.agent_run_id}
    )


async def voice_submit(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    body = await request.json()
    text = str(body.get("text") or "").strip()
    try:
        _validate_voice_terminal_text(session, text)
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    utterance_id = str(body.get("utterance_id") or "").strip()
    if not utterance_id or len(utterance_id) > 100:
        raise ValueError("utterance_id is required and must be at most 100 characters")
    protected = _voice_delivery_protected(request.app, session)
    if protected:
        return _voice_delivery_protected_response(protected)
    if not voice.claim_submission(utterance_id):
        return json_response({"ok": True, "duplicate": True})
    if "\n" in text:
        # Recognition never produces a newline; an edited dictation draft can. Sent
        # raw, the first newline submits the prompt early and the remainder is typed
        # at whatever the agent shows next, so multi-line text takes the queue's
        # delivery bytes instead: bracketed paste with newlines as CR, then a
        # separate Enter after the same settle delay. Single-line prompts keep the
        # one-write path they have always used.
        terminal._record_operator_input(
            request.app[keys.EVENTS],
            session,
            terminal._composer_insertion(session.record.backend, text),
            source="voice",
        )
        await asyncio.sleep(SUBMIT_DELAY_SECONDS)
        if session.record.state in {"exited", "crashed"}:
            return json_response({"error": "the agent session ended during delivery"}, 409)
        terminal._record_operator_input(
            request.app[keys.EVENTS], session, SUBMIT_SEQUENCE, source="voice"
        )
    else:
        terminal._record_operator_input(
            request.app[keys.EVENTS], session, f"{text}\r", source="voice"
        )
    await request.app[keys.EVENTS].emit(
        "voice_prompt_submitted",
        session_id=session.record.id,
        source="voice",
        characters=len(text),
    )
    return json_response({"ok": True, "duplicate": False, "characters": len(text)})


def _current_voice_approval(session: Any) -> tuple[str, str] | None:
    if (
        not delivers_prompts_through_pty(session.record.backend)
        or not session.record.agent_run_id
        or session.record.state != "awaiting"
        or session.record.awaiting_reason != "approval"
    ):
        return None
    try:
        tail = session.scrollback.tail_bytes(SCREEN_TAIL_BYTES).decode("utf-8", "replace")
    except (AttributeError, OSError, ValueError):
        return None
    if (
        pty_tail_state(
            tail,
            backend=session.record.backend,
            cli_state_status=session_cli_state_status(session),
        )
        != "approval"
    ):
        return None
    return approval_prompt(tail)


async def voice_approval(request: web.Request) -> web.Response:
    """Prepare or consume one confirmation for one currently visible approval."""
    voice: VoiceService = request.app[keys.VOICE]
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    body = await request.json()
    action = str(body.get("action") or "").strip()
    if action == "cancel":
        voice.cancel_approval(session.record.id)
        return json_response({"ok": True, "cancelled": True})
    current = _current_voice_approval(session)
    if current is None:
        return json_response({"error": "the focused session is not showing an approval"}, 409)
    operation, fingerprint = current
    run_id = str(session.record.agent_run_id or "")
    if action == "prepare":
        challenge = voice.prepare_approval(session.record.id, run_id, operation, fingerprint)
        return json_response(
            {
                "confirmation_id": challenge.confirmation_id,
                "operation": challenge.operation,
                "expires_at": challenge.expires_at,
            }
        )
    if action != "confirm":
        raise ValueError("voice approval action must be prepare, confirm, or cancel")
    confirmation_id = str(body.get("confirmation_id") or "")
    try:
        challenge = voice.consume_approval(session.record.id, confirmation_id, run_id, fingerprint)
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    terminal._record_operator_input(request.app[keys.EVENTS], session, "\r", source="voice")
    await request.app[keys.EVENTS].emit(
        "voice_approval_confirmed",
        session_id=session.record.id,
        source="voice",
        operation=challenge.operation,
    )
    return json_response({"ok": True, "operation": challenge.operation})


async def voice_interrupt(request: web.Request) -> web.Response:
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    if not delivers_prompts_through_pty(session.record.backend):
        return json_response({"error": "conversation mode requires an agent session"}, 409)
    if session.record.state in {"exited", "crashed"}:
        return json_response({"ok": True, "already_ended": True})
    terminal._record_operator_input(request.app[keys.EVENTS], session, "\x03", source="voice")
    await request.app[keys.EVENTS].emit(
        "voice_agent_interrupted", session_id=session.record.id, source="voice"
    )
    return json_response({"ok": True, "already_ended": False})


async def voice_generate(request: web.Request) -> web.Response:
    voice: VoiceService = request.app[keys.VOICE]
    session = request.app[keys.SESSIONS].resolve(request.match_info["sid"])
    body = await request.json() if request.can_read_body else {}
    content_mode = body.get("content_mode")
    if content_mode is not None and content_mode not in {"summary", "verbatim"}:
        raise ValueError("content_mode must be summary or verbatim")
    # `message_id` names one reply in the reader rather than "the newest": the
    # Transcript tab plays any message through this same pipeline, and naming the
    # message is also what lets an existing clip answer the request instead of a
    # second synthesis of identical audio (`design/features/voice.md`).
    message_id = body.get("message_id")
    if message_id is not None and not isinstance(message_id, str):
        raise ValueError("message_id must be a string")
    try:
        options: dict[str, Any] = {"trigger": "manual", "content_mode": content_mode}
        if body.get("stream_id") is not None:
            options["stream_id"] = body["stream_id"]
        if message_id:
            options["message_id"] = message_id
            # `regenerate` is the deliberate override for a clip whose text the
            # operator no longer trusts; it is never the default, because the
            # default request is "let me hear this" and the audio already exists.
            options["reuse"] = not bool(body.get("regenerate"))
        clip = await voice.generate(session.record.id, **options)
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    return json_response(clip)


async def voice_speak(request: web.Request) -> web.Response:
    """Start, extend, or close one trusted application-speech stream.

    `continue_stream` appends to an open stream instead of starting a new one,
    and empty text with `final` closes it — the shape an assistant turn that
    ended on a tool result needs, having no closing sentence to speak.
    """
    voice: VoiceService = request.app[keys.VOICE]
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise VoiceError("speak body must be an object")
        text = str(body.get("text") or "")
        stream_id = body.get("stream_id")
        final = bool(body.get("final", True))
        if not text.strip() and final and stream_id:
            return json_response(await voice.close_speech_stream(str(stream_id)))
        clip = await voice.speak(
            text,
            stream_id=stream_id,
            continue_stream=bool(body.get("continue_stream")),
            final=final,
        )
    except VoiceError as exc:
        return json_response({"error": str(exc)}, 409)
    return json_response(clip)


async def list_voice_clips(request: web.Request) -> web.Response:
    store: VoiceStore = request.app[keys.VOICE_STORE]
    session_id = request.query.get("session") or None
    if session_id:
        session_id = request.app[keys.SESSIONS].resolve(session_id).record.id
    content_mode = request.query.get("kind") or None
    if content_mode is not None and content_mode not in {"summary", "verbatim"}:
        raise ValueError("kind must be summary or verbatim")
    # Streams, not rows. A reply is cut into segments so its first sentence can
    # play while the rest is still being synthesized; that is a synthesis detail,
    # and listing it as three clips is the operator's problem, not their model.
    groups = await store.clip_groups(
        session_id=session_id,
        agent_run_id=request.query.get("run") or None,
        message_anchor=request.query.get("anchor") or None,
        content_mode=content_mode,
        limit=int(request.query.get("limit") or 20),
    )
    return json_response({"items": [group_snapshot(parts) for parts in groups]})


async def voice_clip_audio(request: web.Request) -> web.StreamResponse:
    store: VoiceStore = request.app[keys.VOICE_STORE]
    row = await store.clip(request.match_info["clip_id"])
    if not row or row["status"] != "ready":
        raise web.HTTPNotFound(text="voice clip not found")
    path = Path(str(row["file_path"]))
    if not path.is_file():
        raise web.HTTPNotFound(text="voice clip audio is no longer cached")
    content_type = "audio/mpeg" if row["format"] == "mp3" else "audio/wav"
    return web.FileResponse(path, headers={"Content-Type": content_type})


async def delete_voice_clip(request: web.Request) -> web.Response:
    store: VoiceStore = request.app[keys.VOICE_STORE]
    # Deleting a clip deletes its whole stream: half a reply is not something to
    # keep, and the segments are only separate rows for latency's sake.
    for file_path in await store.delete_clip(request.match_info["clip_id"]):
        with suppress(OSError):
            Path(file_path).unlink(missing_ok=True)
    return json_response({"ok": True})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/voice", voice_status),
    web.get("/api/voice/providers/edge", edge_provider_status),
    web.post("/api/voice/providers/edge/probe", edge_provider_probe),
    web.post("/api/voice/providers/edge/install", edge_provider_install),
    web.get("/api/voice/providers/edge/voices", edge_voice_catalog),
    web.post("/api/voice/providers/edge/voices/refresh", edge_voice_refresh),
    web.get("/api/voice/providers/edge/preview", edge_voice_preview),
    web.get("/api/voice/models/kokoro", kokoro_model_status),
    web.post("/api/voice/models/kokoro/download", kokoro_model_download),
    web.get("/api/voice/models/whisper", whisper_model_status),
    web.post("/api/voice/models/whisper/download", whisper_model_download),
    web.get("/api/voice/models/kokoro/preview", kokoro_voice_preview),
    web.post("/api/voice/lexicon/check", voice_lexicon_check),
    web.post("/api/voice/lexicon/build", voice_lexicon_build),
    web.get("/api/voice/lexicon/preview", voice_lexicon_preview),
    web.post("/api/sessions/{sid}/voice/transcribe", voice_transcribe),
    web.post("/api/voice/transcribe", voice_transcribe),
    web.get("/api/voice/stt-latency", voice_latency),
    web.post("/api/voice/stt-latency", voice_latency),
    web.delete("/api/voice/stt-latency", voice_latency),
    web.post("/api/voice/barge-in-diagnostic", voice_barge_in_diagnostic),
    web.post("/api/voice/playback-diagnostic", voice_playback_diagnostic),
    web.post("/api/voice/capture-diagnostic", voice_capture_diagnostic),
    web.post("/api/voice/deferral-diagnostic", voice_deferral_diagnostic),
    web.post("/api/sessions/{sid}/voice/prepare-submit", voice_prepare_submit),
    web.post("/api/sessions/{sid}/voice/submit", voice_submit),
    web.post("/api/sessions/{sid}/voice/approval", voice_approval),
    web.post("/api/sessions/{sid}/voice/interrupt", voice_interrupt),
    web.post("/api/sessions/{sid}/voice/generate", voice_generate),
    web.post("/api/voice/speak", voice_speak),
    web.get("/api/voice/clips", list_voice_clips),
    web.get("/api/voice/clips/{clip_id}/audio", voice_clip_audio),
    web.delete("/api/voice/clips/{clip_id}", delete_voice_clip),
)
