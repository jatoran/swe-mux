from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from swe_mux.config import load_config, update_config
from swe_mux.edge_tts_provider import EDGE_RISK_ACK_VERSION, EdgeTtsProvider
from swe_mux.tts_profiles import resolve_tts_profile

pytestmark = [
    pytest.mark.live_edge_tts,
    pytest.mark.skipif(
        os.environ.get("SWE_MUX_LIVE_EDGE_TTS") != "1",
        reason="set SWE_MUX_LIVE_EDGE_TTS=1 to call the unofficial Microsoft service",
    ),
]


async def test_live_catalog_and_audio_output(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    update_config(
        config,
        {
            "tts_engine": "edge",
            "tts_edge_python": sys.executable,
            "tts_edge_risk_ack_version": EDGE_RISK_ACK_VERSION,
        },
    )
    provider = EdgeTtsProvider(config)
    status = await provider.probe()
    assert status["integration"] == "ready", status
    catalog = await provider.refresh_voices()
    assert catalog["voices"]
    chosen = next(
        (voice for voice in catalog["voices"] if voice["id"] == config.tts_edge_voice),
        catalog["voices"][0],
    )
    update_config(config, {"tts_edge_voice": chosen["id"]})
    destination = tmp_path / "edge-live.mp3"
    duration = await provider.synthesize(
        resolve_tts_profile(config),
        "This is the swe mux Edge TTS live output test.",
        destination,
        automatic=False,
    )
    assert destination.stat().st_size > 1_000
    assert duration > 0.1
