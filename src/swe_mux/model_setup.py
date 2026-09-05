"""Bind setup's successful role probes to the exact endpoint and model choices."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .config import Config
from .llm_endpoint import LlmEndpoint


def role_models(config: Config, endpoint: LlmEndpoint) -> dict[str, str]:
    return {
        "cheap": endpoint.resolve_model(config.openrouter_cheap_model),
        "standard": endpoint.resolve_model(config.openrouter_standard_model),
        "timeline": endpoint.resolve_model(config.scan_timeline_model),
        "assistant": endpoint.resolve_model(config.assistant_model),
        "narration": endpoint.resolve_model(
            config.attention_narration_model or config.openrouter_cheap_model
        ),
        "spoken_summary": endpoint.resolve_model(
            config.tts_summary_model or config.openrouter_cheap_model
        ),
        "project_context": endpoint.resolve_model(
            config.project_card_model or config.openrouter_cheap_model
        ),
    }


def fingerprint(config: Config, endpoint: LlmEndpoint, api_key: str | None) -> str:
    material = {"endpoint": endpoint.fingerprint(api_key), "models": role_models(config, endpoint)}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def verified(config: Config, endpoint: LlmEndpoint, api_key: str | None) -> bool:
    try:
        record = json.loads((config.data_dir / "model-setup-verification.json").read_text())
    except (OSError, ValueError):
        return False
    return isinstance(record, dict) and record.get("fingerprint") == fingerprint(
        config, endpoint, api_key
    )


def record_verification(config: Config, digest: str, checks: list[dict[str, Any]]) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    path = config.data_dir / "model-setup-verification.json"
    staged = path.with_suffix(".tmp")
    staged.write_text(
        json.dumps({"fingerprint": digest, "checked_at": time.time(), "checks": checks}) + "\n",
        encoding="utf-8",
    )
    staged.replace(path)
