"""Immutable TTS provider selections shared by every segment of one stream."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from .config import Config
from .voice_models import KOKORO_REVISION

TtsProviderId = Literal["sapi", "kokoro", "edge"]
TtsFormat = Literal["wav", "mp3"]


@dataclass(frozen=True, slots=True)
class TtsProfile:
    """One resolved provider configuration, fixed for an entire speech stream."""

    provider: TtsProviderId
    voice: str
    format: TtsFormat
    options: tuple[tuple[str, str | int | float], ...]
    synthesis_key: str
    duration_rate: str = "+0%"

    def option(self, name: str, default: Any = None) -> Any:
        return dict(self.options).get(name, default)


def _key(provider: str, voice: str, options: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"provider": provider, "voice": voice, "options": options},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_tts_profile(
    config: Config,
    *,
    provider: TtsProviderId | None = None,
    voice_override: str | None = None,
) -> TtsProfile:
    """Snapshot the selected provider without probing files, packages, or network."""

    selected = provider or config.tts_engine
    if selected == "kokoro":
        voice = voice_override or config.tts_kokoro_voice
        options: dict[str, Any] = {
            "speed": float(config.tts_kokoro_speed),
            "model_revision": KOKORO_REVISION,
            "lexicon": sorted(config.tts_kokoro_lexicon.items()),
        }
        return TtsProfile(
            provider="kokoro",
            voice=voice,
            format="wav",
            options=(("speed", float(config.tts_kokoro_speed)),),
            synthesis_key=_key("kokoro", voice, options),
        )
    if selected == "edge":
        voice = voice_override or config.tts_edge_voice
        options = {
            "rate_percent": int(config.tts_edge_rate_percent),
            "volume_percent": int(config.tts_edge_volume_percent),
            "pitch_hz": int(config.tts_edge_pitch_hz),
        }
        rate = int(config.tts_edge_rate_percent)
        return TtsProfile(
            provider="edge",
            voice=voice,
            format="mp3",
            options=tuple(options.items()),
            synthesis_key=_key("edge", voice, options),
            duration_rate=f"{rate:+d}%",
        )
    voice = config.tts_sapi_voice or "system default"
    options = {"rate": int(config.tts_sapi_rate)}
    return TtsProfile(
        provider="sapi",
        voice=voice,
        format="wav",
        options=(("rate", int(config.tts_sapi_rate)),),
        synthesis_key=_key("sapi", voice, options),
    )
