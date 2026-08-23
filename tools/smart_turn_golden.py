"""Regenerate the golden reference for the Smart Turn v3 preprocessing chain.

    uv run --isolated --with transformers --with onnxruntime --with numpy \
        python tools/smart_turn_golden.py

Only needed when the weights change or a case is added; the fixture it writes
(`frontend/test/smartTurnGolden.json`) is committed and is what the TypeScript
extractor is tested against.

Mirrors pipecat-ai/smart-turn `inference.py` + `audio_utils.py` exactly, so the
TypeScript mel extractor can be unit-tested against real numbers instead of
against my memory of how WhisperFeatureExtractor works.

The waveform's noise term is an integer LCG rather than numpy's `randn` on
purpose: it reproduces bit-for-bit in TypeScript from four lines of arithmetic,
where replaying numpy's Mersenne Twister would have meant committing a 3.6 MB
fixture of raw draws.
"""
import json
import pathlib
import urllib.request

import numpy as np
import onnxruntime as ort
from transformers import WhisperFeatureExtractor

HERE = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "models"
MODEL_NAME = "smart-turn-v3.2-cpu.onnx"
MODEL_URL = f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/{MODEL_NAME}"
MODEL_PATH = HERE / MODEL_NAME

if not MODEL_PATH.exists():
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
print(f"model bytes: {MODEL_PATH.stat().st_size}")


def truncate_audio_to_last_n_seconds(audio_array, n_seconds=8, sample_rate=16000):
    max_samples = n_seconds * sample_rate
    if len(audio_array) > max_samples:
        return audio_array[-max_samples:]
    if len(audio_array) < max_samples:
        padding = max_samples - len(audio_array)
        return np.pad(audio_array, (padding, 0), mode="constant", constant_values=0)
    return audio_array


def lcg_noise(n: int) -> np.ndarray:
    """Numerical Recipes LCG mapped to [-1, 1). Exact in float64, so JS matches."""
    state = 1234567891
    out = np.empty(n, dtype=np.float64)
    for index in range(n):
        state = (state * 1664525 + 1013904223) % 4294967296
        out[index] = (state / 4294967296.0) * 2.0 - 1.0
    return out


def make_waveform(n_samples: int) -> np.ndarray:
    """Deterministic, and shaped enough to exercise the mel bins.

    Pure noise would put nearly equal energy in every bin, so a filterbank bug
    would still look plausible. A sweep plus harmonics does not.
    """
    t = np.arange(n_samples, dtype=np.float64) / 16000.0
    sweep = np.sin(2 * np.pi * (120.0 + 900.0 * t) * t)
    harmonic = 0.4 * np.sin(2 * np.pi * 1750.0 * t)
    noise = 0.05 * lcg_noise(n_samples)
    envelope = np.minimum(1.0, t * 4.0) * np.exp(-t / 2.5)
    return ((sweep + harmonic + noise) * envelope * 0.35).astype(np.float32)


feature_extractor = WhisperFeatureExtractor(chunk_length=8)
session = ort.InferenceSession(str(MODEL_PATH))
for inp in session.get_inputs():
    print("onnx input", inp.name, inp.shape, inp.type)

cases = {}
for label, seconds in (("short_1s", 1.0), ("mid_3s", 3.0), ("full_8s", 8.0), ("long_11s", 11.0)):
    raw = make_waveform(int(seconds * 16000))
    audio = truncate_audio_to_last_n_seconds(raw)
    inputs = feature_extractor(
        audio,
        sampling_rate=16000,
        return_tensors="np",
        padding="max_length",
        max_length=8 * 16000,
        truncation=True,
        do_normalize=True,
    )
    features = inputs.input_features.squeeze(0).astype(np.float32)
    probability = float(session.run(None, {"input_features": features[None]})[0][0].item())

    # A coarse but unforgiving fingerprint: every 37th value of the flattened
    # (80, 800) grid, plus global stats. A transposed axis, an off-by-one frame,
    # or a wrong mel norm all move these.
    flat = features.reshape(-1)
    cases[label] = {
        "seconds": seconds,
        "shape": list(features.shape),
        "mean": float(flat.mean()),
        "std": float(flat.std()),
        "min": float(flat.min()),
        "max": float(flat.max()),
        "head": [float(v) for v in flat[:12]],
        "stride37": [float(v) for v in flat[::37][:64]],
        "frame0": [float(v) for v in features[:, 0][:16]],
        "frameLast": [float(v) for v in features[:, -1][:16]],
        "probability": probability,
    }
    print(f"{label}: shape={features.shape} p={probability:.6f} mean={flat.mean():.6f}")

ROOT = pathlib.Path(__file__).resolve().parent.parent
target = ROOT / "frontend" / "test" / "smartTurnGolden.json"
target.write_text(json.dumps(cases, indent=2), encoding="utf-8")
print("wrote", target)
print("waveform head", [float(v) for v in make_waveform(8)])
