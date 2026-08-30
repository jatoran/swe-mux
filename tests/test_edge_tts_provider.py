from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from swe_mux import app_keys as keys
from swe_mux.config import load_config, update_config
from swe_mux.edge_tts_provider import (
    EDGE_RISK_ACK_VERSION,
    EDGE_TTS_VERSION,
    EdgeTtsError,
    EdgeTtsProvider,
    EdgeVoiceCatalog,
    _safe_error_message,
    managed_interpreter,
    normalize_edge_voices,
)
from swe_mux.routes.voice import edge_provider_install
from swe_mux.tts_profiles import resolve_tts_profile

VOICE_PAYLOAD = [
    {
        "ShortName": "en-US-JennyNeural",
        "Locale": "en-US",
        "Gender": "Female",
        "FriendlyName": "Microsoft Jenny Online (Natural) - English (United States)",
        "Status": "GA",
        "SuggestedCodec": "audio-24khz-48kbitrate-mono-mp3",
        "VoiceTag": {
            "ContentCategories": ["General"],
            "VoicePersonalities": ["Friendly"],
        },
    },
    # The newest duplicate wins deterministically.
    {
        "ShortName": "en-US-JennyNeural",
        "Locale": "en-US",
        "Gender": "Female",
        "FriendlyName": "Jenny",
        "Status": "GA",
        "VoiceTag": {},
    },
    {"ShortName": "not-a-voice", "Locale": "", "FriendlyName": "bad"},
]


def test_service_errors_never_expose_embedded_tokens_or_signed_urls() -> None:
    safe = _safe_error_message(
        "403 wss://speech.platform.bing.com/consumer/speech/synthesize/readaloud/edge/v1?"
        "TrustedClientToken=secret&Sec-MS-GEC=signed"
    )
    assert "secret" not in safe and "signed" not in safe
    assert "speech.platform.bing.com" in safe


def test_edge_config_and_kokoro_config_persist_independently(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    update_config(
        config,
        {
            "tts_engine": "edge",
            "tts_edge_voice": "en-GB-SoniaNeural",
            "tts_edge_rate_percent": 18,
            "tts_edge_volume_percent": -4,
            "tts_edge_pitch_hz": 7,
            "tts_edge_risk_ack_version": EDGE_RISK_ACK_VERSION,
            "tts_kokoro_voice": "bf_emma",
            "tts_kokoro_speed": 1.25,
            "tts_kokoro_lexicon": {"vaultspaces": "vault spaces"},
        },
    )
    update_config(config, {"tts_engine": "kokoro"})
    reloaded = load_config(path)
    assert reloaded.tts_engine == "kokoro"
    assert reloaded.tts_kokoro_voice == "bf_emma"
    assert reloaded.tts_kokoro_speed == 1.25
    assert reloaded.tts_kokoro_lexicon == {"vaultspaces": "vault spaces"}
    assert reloaded.tts_edge_voice == "en-GB-SoniaNeural"
    assert reloaded.tts_edge_rate_percent == 18
    assert reloaded.tts_edge_volume_percent == -4
    assert reloaded.tts_edge_pitch_hz == 7
    assert reloaded.tts_edge_risk_ack_version == EDGE_RISK_ACK_VERSION


def test_schema_33_renames_the_kokoro_lexicon_without_reenabling_edge(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 25\ntts_engine = "edge"\n'
        'tts_lexicon = { "Vaultspaces" = "vault spaces" }\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.tts_engine == "sapi"
    assert config.tts_kokoro_lexicon == {"Vaultspaces": "vault spaces"}
    serialized = path.read_text(encoding="utf-8")
    assert "tts_kokoro_lexicon" in serialized
    assert "tts_lexicon =" not in serialized


def test_synthesis_keys_cover_every_provider_specific_audio_option(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    sapi = resolve_tts_profile(config)
    update_config(config, {"tts_sapi_rate": 1})
    assert resolve_tts_profile(config).synthesis_key != sapi.synthesis_key

    update_config(config, {"tts_engine": "kokoro"})
    kokoro = resolve_tts_profile(config)
    update_config(config, {"tts_kokoro_lexicon": {"mux": "mucks"}})
    assert resolve_tts_profile(config).synthesis_key != kokoro.synthesis_key

    update_config(config, {"tts_engine": "edge"})
    edge = resolve_tts_profile(config)
    update_config(config, {"tts_edge_pitch_hz": 3})
    changed = resolve_tts_profile(config)
    assert changed.synthesis_key != edge.synthesis_key
    assert changed.format == "mp3"


def test_voice_catalog_normalizes_bounds_and_keeps_last_good_on_error(tmp_path: Path) -> None:
    voices = normalize_edge_voices(VOICE_PAYLOAD)
    assert voices == [
        {
            "id": "en-US-JennyNeural",
            "locale": "en-US",
            "gender": "Female",
            "name": "Jenny",
            "status": "GA",
            "codec": "",
            "categories": [],
            "personalities": [],
        }
    ]
    path = tmp_path / "voices.json"
    catalog = EdgeVoiceCatalog(path)
    catalog.replace(voices, package_version="7.2.8")
    catalog.record_error("offline")
    snapshot = catalog.snapshot(selected="en-US-JennyNeural")
    assert snapshot["voices"] == voices
    assert snapshot["selected_present"] is True
    assert snapshot["error"] == "offline"
    assert json.loads(path.read_text(encoding="utf-8"))["voices"] == voices


def test_provider_startup_sweeps_only_its_abandoned_text_inputs(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    temporary = tmp_path / "voice" / "providers" / "edge" / "tmp"
    temporary.mkdir(parents=True)
    abandoned = temporary / "clip.txt"
    abandoned.write_text("private", encoding="utf-8")
    unrelated = tmp_path / "voice" / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    EdgeTtsProvider(config)
    assert not abandoned.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"


async def _no_preflight() -> None:
    """Stand in for the reachability preflight so the suite makes no network call."""


async def test_managed_install_stages_verifies_and_activates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(tmp_path / "config.toml")
    provider = EdgeTtsProvider(config)
    commands: list[list[str]] = []

    monkeypatch.setattr("swe_mux.edge_tts_provider.shutil.which", lambda command: "uv.exe")

    async def run(argv: list[str], *, label: str, operation_id: str) -> None:
        del label, operation_id
        commands.append(argv)
        if argv[1] == "venv":
            python = provider.managed_python(Path(argv[-1]))
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")

    async def invoke(
        executable: str,
        operation: str,
        *_arguments: str,
        **options: Any,
    ) -> dict[str, Any]:
        assert Path(executable).name.startswith("python")
        assert operation == "status"
        assert options["record_version"] is False
        return {"ok": True, "version": "7.2.8"}

    monkeypatch.setattr(provider, "_run_install_command", run)
    # The gate must never dial PyPI. Stubbed rather than left to succeed on a
    # connected host, which is what made these tests silently make a real
    # request the first time the preflight was added.
    monkeypatch.setattr(provider, "_preflight_index_reachable", _no_preflight)
    monkeypatch.setattr(provider, "_invoke_unlocked", invoke)
    assert provider.start_managed_install() is True
    assert provider.start_managed_install() is False
    await provider.wait_install()

    managed = provider.managed_status()
    assert managed["status"] == "ready"
    assert managed["version"] == "7.2.8"
    assert provider.python() == str(provider.managed_python())
    assert provider.package_version == "7.2.8"
    assert [command[1] for command in commands] == ["venv", "pip"]
    assert json.loads(provider.install_state_path.read_text(encoding="utf-8"))["status"] == "ready"


def install_a_working_managed_environment(integration: Path) -> Path:
    """A previously successful managed install, laid out the way this host's uv would.

    The interpreter path comes from the provider's own owner of that layout rather than
    from a literal, because a hard-coded `current/Scripts/python.exe` describes nothing
    that exists on POSIX - so the environment reads as absent and every assertion about
    keeping it passes vacuously on the host that has it and fails on the one that does not.
    """

    python = managed_interpreter(integration / "current")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"working")
    (integration / "install.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "ready",
                "version": EDGE_TTS_VERSION,
                "installed_at": 1.0,
                "updated_at": 1.0,
            }
        ),
        encoding="utf-8",
    )
    return python


async def test_failed_repair_keeps_the_working_managed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(tmp_path / "config.toml")
    integration = tmp_path / "integrations" / "edge-tts"
    install_a_working_managed_environment(integration)
    provider = EdgeTtsProvider(config)
    monkeypatch.setattr("swe_mux.edge_tts_provider.shutil.which", lambda command: "uv.exe")

    async def run(argv: list[str], *, label: str, operation_id: str) -> None:
        del label, operation_id
        if argv[1] == "venv":
            staged_python = provider.managed_python(Path(argv[-1]))
            staged_python.parent.mkdir(parents=True)
            staged_python.write_bytes(b"staged")
            return
        raise EdgeTtsError("install_failed", "registry unavailable")

    monkeypatch.setattr(provider, "_run_install_command", run)
    # The gate must never dial PyPI. Stubbed rather than left to succeed on a
    # connected host, which is what made these tests silently make a real
    # request the first time the preflight was added.
    monkeypatch.setattr(provider, "_preflight_index_reachable", _no_preflight)
    assert provider.start_managed_install() is True
    await provider.wait_install()
    managed = provider.managed_status()
    assert managed["status"] == "ready"
    assert "registry unavailable" in str(managed["last_install_error"])
    assert provider.managed_python().read_bytes() == b"working"
    assert provider.python() == str(provider.managed_python())


async def test_a_failure_after_activation_restores_the_previous_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The swap itself must be survivable, not only the staging that precedes it.

    The repair test above never reaches `_activate_managed`, so it says nothing about the
    directory renames - which are the part whose semantics differ between hosts.
    """

    config = load_config(tmp_path / "config.toml")
    integration = tmp_path / "integrations" / "edge-tts"
    install_a_working_managed_environment(integration)
    provider = EdgeTtsProvider(config)
    monkeypatch.setattr("swe_mux.edge_tts_provider.shutil.which", lambda command: "uv.exe")

    async def run(argv: list[str], *, label: str, operation_id: str) -> None:
        del label, operation_id
        if argv[1] == "venv":
            staged_python = provider.managed_python(Path(argv[-1]))
            staged_python.parent.mkdir(parents=True)
            staged_python.write_bytes(b"staged")

    async def invoke(
        executable: str, operation: str, *_arguments: str, **_options: Any
    ) -> dict[str, Any]:
        del executable, operation
        return {"ok": True, "version": EDGE_TTS_VERSION}

    write_state = provider._write_install_state
    refused = False

    def refuse_the_first_success_write(state: dict[str, Any]) -> None:
        nonlocal refused
        if state.get("status") == "ready" and not refused:
            refused = True
            raise OSError("no space left on device")
        write_state(state)

    monkeypatch.setattr(provider, "_run_install_command", run)
    # The gate must never dial PyPI. Stubbed rather than left to succeed on a
    # connected host, which is what made these tests silently make a real
    # request the first time the preflight was added.
    monkeypatch.setattr(provider, "_preflight_index_reachable", _no_preflight)
    monkeypatch.setattr(provider, "_invoke_unlocked", invoke)
    monkeypatch.setattr(provider, "_write_install_state", refuse_the_first_success_write)
    assert provider.start_managed_install() is True
    await provider.wait_install()

    managed = provider.managed_status()
    assert managed["status"] == "ready"
    assert "no space left on device" in str(managed["last_install_error"])
    assert provider.managed_python().read_bytes() == b"working"
    assert not (integration / "previous").exists()
    assert not list(integration.glob(".staging-*"))


async def test_managed_install_endpoint_requires_an_explicit_gesture() -> None:
    app = web.Application()
    app[keys.VOICE] = SimpleNamespace(edge_tts=SimpleNamespace())
    request = make_mocked_request("POST", "/api/voice/providers/edge/install", app=app)
    response = await edge_provider_install(request)
    assert response.status == 403


async def test_managed_install_endpoint_starts_one_user_requested_install() -> None:
    edge = SimpleNamespace(
        start_managed_install=lambda: True,
        status=lambda: {"id": "edge", "managed": {"status": "installing"}},
    )
    app = web.Application()
    app[keys.VOICE] = SimpleNamespace(edge_tts=edge)
    request = make_mocked_request(
        "POST",
        "/api/voice/providers/edge/install",
        headers={"X-Mux-User-Gesture": "edge-tts-install"},
        app=app,
    )
    response = await edge_provider_install(request)
    assert response.status == 202
    payload = json.loads(response.body)
    assert payload["started"] is True
    assert payload["managed"]["status"] == "installing"


async def test_external_synthesis_keeps_text_out_of_argv_and_cleans_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(tmp_path / "config.toml")
    update_config(
        config,
        {"tts_engine": "edge", "tts_edge_risk_ack_version": EDGE_RISK_ACK_VERSION},
    )
    provider = EdgeTtsProvider(config)
    captured: list[tuple[str, ...]] = []

    async def invoke(operation: str, *arguments: str, **_kwargs: Any) -> dict[str, Any]:
        captured.append(arguments)
        assert operation == "synthesize"
        assert "private session text" not in " ".join(arguments)
        input_path = Path(arguments[arguments.index("--input") + 1])
        output_path = Path(arguments[arguments.index("--output") + 1])
        assert input_path.read_text(encoding="utf-8") == "private session text"
        output_path.write_bytes(b"ID3" + b"x" * 5_997)
        return {"ok": True, "bytes": 6_000, "bitrate_bps": 48_000, "format": "mp3"}

    monkeypatch.setattr(provider, "_invoke", invoke)
    destination = tmp_path / "clip.mp3"
    duration = await provider.synthesize(
        resolve_tts_profile(config),
        "private session text",
        destination,
        automatic=False,
    )
    assert duration == pytest.approx(1.0)
    assert destination.read_bytes().startswith(b"ID3")
    input_path = Path(captured[0][captured[0].index("--input") + 1])
    assert not input_path.exists()


async def test_edge_synthesis_requires_the_versioned_disclosure(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    update_config(config, {"tts_engine": "edge"})
    provider = EdgeTtsProvider(config)
    with pytest.raises(EdgeTtsError, match="acknowledge") as caught:
        await provider.synthesize(
            resolve_tts_profile(config), "hello", tmp_path / "clip.mp3", automatic=False
        )
    assert caught.value.code == "risk_not_acknowledged"
    assert not (tmp_path / "clip.mp3").exists()


async def test_automatic_failures_back_off_without_calling_or_falling_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_config(tmp_path / "config.toml")
    update_config(
        config,
        {"tts_engine": "edge", "tts_edge_risk_ack_version": EDGE_RISK_ACK_VERSION},
    )
    provider = EdgeTtsProvider(config)
    calls = 0

    async def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise EdgeTtsError("offline", "network unavailable")

    monkeypatch.setattr(provider, "_invoke", fail)
    profile = resolve_tts_profile(config)
    with pytest.raises(EdgeTtsError, match="network unavailable"):
        await provider.synthesize(profile, "hello", tmp_path / "one.mp3", automatic=False)
    with pytest.raises(EdgeTtsError, match="backing off") as caught:
        await provider.synthesize(profile, "hello", tmp_path / "two.mp3", automatic=True)
    assert caught.value.code == "backoff"
    assert calls == 1
    assert not (tmp_path / "one.mp3").exists()
    assert not (tmp_path / "two.mp3").exists()


async def test_connectivity_is_checked_before_the_multi_minute_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install that cannot reach PyPI must fail in seconds, not after 80 of them.

    The observed failure spent 44.8s creating an environment and 36s installing a
    package before anything went wrong, then reported at the step furthest from
    the cause. Nothing may run before the preflight.
    """
    config = load_config(tmp_path / "config.toml")
    provider = EdgeTtsProvider(config)
    monkeypatch.setattr("swe_mux.edge_tts_provider.shutil.which", lambda command: "uv.exe")
    order: list[str] = []

    async def run(argv: list[str], *, label: str, operation_id: str) -> None:
        del label, operation_id
        order.append(argv[1])

    async def refuse() -> None:
        order.append("preflight")
        raise EdgeTtsError("install_index_unreachable", "could not reach PyPI")

    monkeypatch.setattr(provider, "_run_install_command", run)
    monkeypatch.setattr(provider, "_preflight_index_reachable", refuse)
    assert provider.start_managed_install() is True
    await provider.wait_install()

    assert order == ["preflight"]
    managed = provider.managed_status()
    assert managed["status"] == "error"
    assert "could not reach" in str(managed["error"])
    assert not (tmp_path / "integrations" / "edge-tts" / "current").exists()


async def test_a_verify_timeout_says_what_that_step_actually_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Edge TTS status timed out" sent a reader hunting a network fault.

    That step imports the package and reads its version; it makes no request at
    all. The message has to say so, or the next person spends the same hour on
    TLS and proxies.
    """
    config = load_config(tmp_path / "config.toml")
    provider = EdgeTtsProvider(config)
    monkeypatch.setattr("swe_mux.edge_tts_provider.shutil.which", lambda command: "uv.exe")

    async def run(argv: list[str], *, label: str, operation_id: str) -> None:
        del label, operation_id
        if argv[1] == "venv":
            python = provider.managed_python(Path(argv[-1]))
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")

    async def time_out(*_args: Any, **_options: Any) -> dict[str, Any]:
        raise EdgeTtsError("timeout", "Edge TTS status timed out")

    monkeypatch.setattr(provider, "_run_install_command", run)
    monkeypatch.setattr(provider, "_preflight_index_reachable", _no_preflight)
    monkeypatch.setattr(provider, "_invoke_unlocked", time_out)
    assert provider.start_managed_install() is True
    await provider.wait_install()

    error = str(provider.managed_status()["error"])
    assert "makes no network request" in error
    assert "120s" in error


def test_the_verify_budget_is_larger_than_a_cold_import(tmp_path: Path) -> None:
    """20s was the budget and a cold venv on a slow machine blew straight through it.

    The steady-state call budget is deliberately separate: a slow answer there is a
    real fault, whereas here it is a first-ever import with a scanner reading every
    new file.
    """
    from swe_mux.edge_tts_provider import (
        EDGE_BRIDGE_TIMEOUT_SECONDS,
        EDGE_VERIFY_TIMEOUT_SECONDS,
    )

    del tmp_path
    assert EDGE_VERIFY_TIMEOUT_SECONDS >= 120.0
    assert EDGE_VERIFY_TIMEOUT_SECONDS > EDGE_BRIDGE_TIMEOUT_SECONDS
