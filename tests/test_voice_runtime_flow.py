"""The first-use voice flow: one press, typed refusals, and no 500s.

Written after a real failure on 2026-08-29. An operator on the frozen desktop app
switched read-aloud to Kokoro, downloaded the two assets the panel offered, and
met `500 internal server error` - while the daemon log held the exact sentence
naming the third asset and the button that would acquire it.

Four defects, four groups of tests:

1. A typed refusal must never become an internal error, from any route.
2. Readiness must be reported before synthesis, not discovered by it.
3. One press must acquire everything a capability needs.
4. A remedy must be one *this* install can run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import server as server_module
from swe_mux.event_bus import EventBus
from swe_mux.kokoro_tts import KokoroError
from swe_mux.routes import voice as voice_routes
from swe_mux.voice import VOICE_RUNTIME_MISSING, VoiceError

from .test_voice import make_service


def _absent_runtime() -> dict[str, Any]:
    """A `VoiceRuntimeStore.status()` payload for "supported, never acquired".

    Spelled out here rather than by pointing a store at an empty `tmp_path`,
    because the development checkout *has* the closure installed: a real store
    would answer `ready` from `closure_importable()` and every assertion below
    would pass for the wrong reason.
    """
    return {
        "status": "not_downloaded",
        "source": None,
        "supported": True,
        "closure": "0" * 64,
        "distributions": 45,
        "total_bytes": 85_905_087,
        "downloaded_bytes": 0,
        "current_file": None,
        "error": None,
    }


# --------------------------------------------------------------- 1. typed refusals


def test_voice_error_carries_a_machine_code_and_a_remedy() -> None:
    """A message alone cannot tell "press this" from "nothing to press".

    `the speech libraries are not downloaded` is actionable and `nothing speakable
    remained after preprocessing` is not, and a client that had to tell them apart
    by string matching would get it wrong the first time either was reworded.
    """
    plain = VoiceError("nothing speakable remained")
    assert plain.as_payload() == {"error": "nothing speakable remained"}

    typed = VoiceError("no libraries", code=VOICE_RUNTIME_MISSING, remedy="uv sync --extra x")
    assert typed.as_payload() == {
        "error": "no libraries",
        "code": VOICE_RUNTIME_MISSING,
        "remedy": "uv sync --extra x",
    }


async def test_a_voice_refusal_from_an_unguarded_route_is_409_not_500() -> None:
    """The regression this file exists for, at the layer that makes it impossible.

    Most voice routes caught `VoiceError` and answered 409. Two did not, and the
    two that did not are the ones a user found. Translating the class centrally
    means a route added tomorrow cannot reintroduce the defect by forgetting.
    """

    async def handler(_request: web.Request) -> web.Response:
        raise VoiceError(
            "the on-device speech libraries are not downloaded (82 MB)",
            code=VOICE_RUNTIME_MISSING,
        )

    app = web.Application(middlewares=[server_module.error_middleware])
    app.router.add_get("/boom", handler)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/boom")
        assert response.status == 409
        body = await response.json()
    assert body["code"] == VOICE_RUNTIME_MISSING
    assert "not downloaded" in body["error"]
    assert body["error"] != "internal server error"


async def test_the_lexicon_surfaces_refuse_before_handing_out_an_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact two call sites that produced the 500.

    `check_lexicon` and `build_lexicon_entry` both took an engine and called into
    it on a worker thread with no `KokoroError` clause. `KokoroEngine` constructs
    happily without the acquired closure - both of its heavy imports are lazy - so
    the failure surfaced from `_ensure_g2p`, several frames down, on a surface
    that draws a tick or a cross.

    Fixed at the boundary rather than at the two call sites: `_ensure_kokoro` asks
    for the closure before it constructs anything.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        monkeypatch.setattr(service.kokoro_models, "ready", lambda: True)
        monkeypatch.setattr(service.voice_runtime, "ready", lambda: False)
        monkeypatch.setattr(service.voice_runtime, "status", lambda: _absent_runtime())

        with pytest.raises(VoiceError) as refusal:
            await service.build_lexicon_entry("swemux", "swee mux")
        assert refusal.value.code == VOICE_RUNTIME_MISSING
        assert "not downloaded" in str(refusal.value)

        report = await service.check_lexicon({"swemux": "swee mux"})
        assert report["available"] is False
        assert "not downloaded" in (report["diagnostic"] or "")
    finally:
        service.store.close()


async def test_a_kokoro_failure_inside_the_lexicon_check_is_a_verdict_not_a_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The closure is present and one entry still cannot be resolved.

    Advisory surface: one bad respelling is a cross on that row, never a failed
    request that loses the verdicts for every other row.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        monkeypatch.setattr(service.kokoro_models, "ready", lambda: True)
        monkeypatch.setattr(service.voice_runtime, "ready", lambda: True)

        class Engine:
            @staticmethod
            def check_respelling(_word: str, _value: str) -> dict[str, Any]:
                raise KokoroError("the G2P rejected that value")

        monkeypatch.setattr(service, "_ensure_kokoro", lambda: Engine())
        report = await service.check_lexicon({"swemux": "swee mux"})
        assert report["available"] is True
        assert report["results"]["swemux"]["ok"] is False
        assert "rejected" in report["results"]["swemux"]["diagnostic"]
    finally:
        service.store.close()


# --------------------------------------------------- 2. readiness before synthesis


async def test_the_kokoro_provider_reports_unavailable_before_anything_is_spoken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/api/voice` has to answer "not yet" while the weights are already here.

    The operator's install had `tts_enabled` true from before the closure moved
    out of the bundle, so the only thing standing between him and a failure was a
    status read. It must name the libraries rather than the weights he had.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        monkeypatch.setattr(service.kokoro_models, "ready", lambda: True)
        monkeypatch.setattr(
            service.kokoro_models, "status", lambda: {"status": "ready", "error": None}
        )
        monkeypatch.setattr(
            service.g2p_model, "status", lambda: {"status": "ready", "error": None}
        )
        # Both, and the pair is the point: `ready()` is what a guard asks and
        # `status()` is what the report renders. A test that stubbed only the
        # first would pass against a service that reports the closure ready and
        # refuses to use it.
        monkeypatch.setattr(service.voice_runtime, "ready", lambda: False)
        monkeypatch.setattr(service.voice_runtime, "status", lambda: _absent_runtime())
        service.config.tts_engine = "kokoro"

        state = await service.status()
        assert state["engine_available"] is False
        assert state["voice_runtime"]["status"] != "ready"
        diagnostic = state["providers"]["kokoro"]["diagnostic"] or ""
        assert "speech libraries" in diagnostic
        assert "Kokoro voice model is not downloaded" not in diagnostic
    finally:
        service.store.close()


async def test_dictation_readiness_reads_the_store_rather_than_a_stale_memo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`backend_installed()` caches an import attempt made before the closure existed.

    An install that has just acquired the libraries would keep reporting a missing
    dictation backend until a restart if that memo were the only question asked.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        service.config.stt_engine = "whisper"
        monkeypatch.setattr(service.voice_runtime, "ready", lambda: False)
        monkeypatch.setattr(service.voice_runtime, "status", lambda: _absent_runtime())
        monkeypatch.setattr(service.whisper_models, "backend_installed", lambda: True)
        available, diagnostic, _models = service._stt_readiness()
        assert available is False
        assert "speech libraries" in (diagnostic or "")
    finally:
        service.store.close()


# ------------------------------------------------------------------- 3. one press


def _download_app(service: Any) -> web.Application:
    app = web.Application()
    app[keys.VOICE] = service
    app[keys.EVENTS] = EventBus()
    app.router.add_post(
        "/api/voice/models/kokoro/download", voice_routes.kokoro_model_download
    )
    app.router.add_post(
        "/api/voice/models/whisper/download", voice_routes.whisper_model_download
    )
    return app


async def test_one_press_starts_every_store_kokoro_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three stores, one press. The two-press version failed a real operator.

    Started in parallel because none of the three needs another: all are verified
    fetches, and only *loading* needs all three present.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        started: list[str] = []
        monkeypatch.setattr(
            service.kokoro_models, "start_download", lambda *_a: started.append("kokoro") is None
        )
        monkeypatch.setattr(
            service.g2p_model, "start_download", lambda *_a: started.append("g2p") is None
        )
        monkeypatch.setattr(
            service.voice_runtime, "start_download", lambda *_a: started.append("runtime") is None
        )
        async with TestClient(TestServer(_download_app(service))) as client:
            response = await client.post("/api/voice/models/kokoro/download")
            assert response.status == 202
        assert sorted(started) == ["g2p", "kokoro", "runtime"]
    finally:
        service.store.close()


async def test_the_dictation_press_acquires_the_libraries_first_and_chains_the_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dictation cannot start in parallel, so it chains rather than refusing.

    `WhisperModelStore._download` calls `backend_installed()`, so weights started
    beside the closure would fail immediately and read as a broken weights
    download. The closure goes first and the weights start from its completion -
    the alternative, telling the operator to press again in a minute, is the
    defect this whole change removes.
    """
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        chained: list[Any] = []
        weights_started: list[str] = []
        monkeypatch.setattr(service.voice_runtime, "ready", lambda: False)
        monkeypatch.setattr(
            service.voice_runtime,
            "start_download",
            lambda progress=None: chained.append(progress) is None,
        )
        monkeypatch.setattr(
            service.whisper_models,
            "start_download",
            lambda name, _progress=None: weights_started.append(name) is None,
        )
        async with TestClient(TestServer(_download_app(service))) as client:
            response = await client.post(
                "/api/voice/models/whisper/download", json={"model": "turbo"}
            )
            assert response.status == 202
            body = await response.json()
        assert body["waiting_on"] == "voice_runtime"
        assert weights_started == [], "the weights must not start beside the closure"

        # The chain fires when the closure lands, and not before.
        assert len(chained) == 1
        await chained[0]({"status": "downloading"})
        assert weights_started == []
        await chained[0]({"status": "ready"})
        assert weights_started == ["turbo"]
    finally:
        service.store.close()


async def test_the_runtime_progress_clears_the_dictation_backend_memo(
    tmp_path: Path,
) -> None:
    """Otherwise a just-acquired install reports a missing backend until restart."""
    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        forgotten: list[bool] = []
        service.whisper_models.forget_backend = lambda: forgotten.append(True) is None  # type: ignore[method-assign]
        progress = voice_routes._runtime_progress(EventBus(), service)
        await progress({"status": "downloading"})
        assert forgotten == []
        await progress({"status": "ready"})
        assert forgotten == [True]
    finally:
        service.store.close()


# ------------------------------------------------------------------- 4. the remedy


def test_the_frozen_app_is_never_told_to_run_uv_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remedy that cannot be run is worse than none: it ends the search.

    The frozen desktop app's extras are fixed when the bundle is built, so
    `uv sync --extra voice-local` is meaningless to the one audience most likely
    to be reading a voice diagnostic.
    """
    from types import SimpleNamespace

    from swe_mux import install_location

    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        # `InstallLocation` is a fifteen-field description of a real installation
        # and `_extra_remedy` reads two of them. A stand-in carrying exactly what
        # is read keeps this test about the branch rather than about keeping a
        # fixture in step with a dataclass it does not exercise.
        monkeypatch.setattr(
            install_location,
            "detect_install_location",
            lambda *_a, **_k: SimpleNamespace(
                kind=install_location.INSTALL_FROZEN, source_checkout=False
            ),
        )
        service._extra_command = None
        assert service._extra_remedy() == ""

        monkeypatch.setattr(service.voice_runtime, "supported", lambda: False)
        monkeypatch.setattr(
            service.voice_runtime,
            "status",
            lambda: {
                "status": "error",
                "supported": False,
                "total_bytes": 0,
                "error": "no wheel for this interpreter",
            },
        )
        message = service._runtime_diagnostic("read-aloud")
        assert "uv sync" not in message
        assert "OS voice engine" in message
    finally:
        service.store.close()


def test_a_source_checkout_is_told_the_command_it_can_actually_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from swe_mux import install_location

    service, _events, _emitted, _record = make_service(tmp_path)
    try:
        monkeypatch.setattr(
            install_location,
            "detect_install_location",
            lambda *_a, **_k: SimpleNamespace(
                kind=install_location.INSTALL_UV_TOOL, source_checkout=False
            ),
        )
        service._extra_command = None
        assert service._extra_remedy() == 'uv tool install --force "swe-mux[voice-local]"'
    finally:
        service.store.close()
