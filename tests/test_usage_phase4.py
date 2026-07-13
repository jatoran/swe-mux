from __future__ import annotations

import json
from pathlib import Path
from types import MethodType

import pytest

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.usage import UsageManager, normalize_usage

FIXTURES = Path(__file__).parent / "fixtures" / "usage"


@pytest.mark.parametrize(
    ("filename", "provider", "model", "total"),
    [
        ("claude-v17.json", "claude", "claude-opus", 2000),
        ("codex-v0.json", "codex", "gpt-5", 700),
    ],
)
def test_pinned_usage_fixtures_normalize_without_live_npx(
    filename: str, provider: str, model: str, total: int
) -> None:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    normalized = normalize_usage(
        payload, provider, {"adapter": "fixture", "package": filename}
    )
    assert normalized["provider"] == provider
    assert normalized["models"][0]["model"] == model
    assert normalized["totals"]["total_tokens"] == total
    assert normalized["totals"]["cost_is_estimate"] is True
    assert normalized["monthly"][0]["month"] == "2026-07"


@pytest.mark.asyncio
async def test_refresh_is_cached_and_failure_keeps_last_known_good(tmp_path: Path) -> None:
    config = Config(
        data_dir=tmp_path,
        ccusage_enabled=True,
        ccusage_claude_command=["fixture-claude"],
        ccusage_codex_command=["fixture-codex"],
    )
    manager = UsageManager(config, EventBus())
    calls = 0

    async def invoke(self: UsageManager, command: list[str]) -> str:
        nonlocal calls
        calls += 1
        if calls > 1:
            return "not-json"
        return (FIXTURES / "claude-v17.json").read_text(encoding="utf-8")

    manager._invoke = MethodType(invoke, manager)  # type: ignore[method-assign]
    first = await manager.refresh("claude")
    assert first["states"]["claude"]["status"] == "ready"
    cached = first["cache"]["providers"]["claude"]
    second = await manager.refresh("claude")
    assert second["states"]["claude"]["status"] == "stale"
    assert second["cache"]["providers"]["claude"] == cached
    assert (tmp_path / "usage-cache.json").exists()


def test_usage_cache_clear_is_explicit(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path, ccusage_enabled=True)
    manager = UsageManager(config, EventBus())
    manager.cache = {"version": 1, "providers": {"claude": {}}}
    manager._write_cache()
    snapshot = manager.clear()
    assert snapshot["cache"] == {}
    assert not (tmp_path / "usage-cache.json").exists()
