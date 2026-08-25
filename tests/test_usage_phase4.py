from __future__ import annotations

import json
from pathlib import Path
from types import MethodType

import pytest

from swe_mux.config import CCUSAGE_PACKAGE, Config, default_ccusage_command
from swe_mux.event_bus import EventBus
from swe_mux.usage import (
    UsageAdapterError,
    UsageManager,
    normalize_usage,
    normalize_usage_sources,
    prepare_usage_command,
)

FIXTURES = Path(__file__).parent / "fixtures" / "usage"


@pytest.mark.parametrize(
    ("filename", "provider", "model", "total"),
    [
        ("claude-v17.json", "claude", "claude-opus", 2000),
        ("codex-v0.json", "codex", "gpt-5", 700),
        ("codex-v20.json", "codex", "gpt-5.2-codex", 700),
    ],
)
def test_versioned_usage_fixtures_normalize_without_live_cli(
    filename: str, provider: str, model: str, total: int
) -> None:
    payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    normalized = normalize_usage(payload, provider, {"adapter": "fixture", "package": filename})
    assert normalized["source_id"] == provider
    assert normalized["collector_id"] == "ccusage"
    assert normalized["models"][0]["model"] == model
    assert normalized["totals"]["total_tokens"] == total
    assert normalized["totals"]["cost_is_estimate"] is True
    assert normalized["monthly"][0]["month"] == "2026-07"


@pytest.mark.asyncio
async def test_refresh_is_cached_and_failure_keeps_last_known_good(tmp_path: Path) -> None:
    config = Config(
        data_dir=tmp_path,
        ccusage_enabled=True,
        usage_command=["fixture-ccusage"],
    )
    manager = UsageManager(config, EventBus())
    calls = 0

    async def invoke(
        self: UsageManager, command: list[str], *, operation_id: str | None = None
    ) -> str:
        nonlocal calls
        calls += 1
        # The refresh names itself, so the runner's own timeout and cap lines join
        # the adapter's failure line instead of reading `operation_id=None`.
        assert operation_id
        if calls > 1:
            return "not-json"
        return (FIXTURES / "ccusage-by-agent-v20.json").read_text(encoding="utf-8")

    manager._invoke = MethodType(invoke, manager)  # type: ignore[method-assign]
    first = await manager.refresh()
    assert first["collector"]["status"] == "ready"
    cached = first["cache"]["sources"]
    assert set(cached) == {"claude", "opencode"}
    second = await manager.refresh()
    assert second["collector"]["status"] == "stale"
    assert second["cache"]["sources"] == cached
    assert (tmp_path / "usage-cache.json").exists()


def test_usage_cache_clear_is_explicit(tmp_path: Path) -> None:
    config = Config(data_dir=tmp_path, ccusage_enabled=True)
    manager = UsageManager(config, EventBus())
    manager.cache = {"version": 1, "providers": {"claude": {}}}
    manager._write_cache()
    snapshot = manager.clear()
    assert snapshot["cache"] == {}
    assert not (tmp_path / "usage-cache.json").exists()


def test_version_two_provider_cache_migrates_to_dynamic_sources(tmp_path: Path) -> None:
    cache_path = tmp_path / "usage-cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 2,
                "updated_at": 123.0,
                "providers": {"codex": {"provider": "codex", "daily": []}},
            }
        ),
        encoding="utf-8",
    )

    manager = UsageManager(Config(data_dir=tmp_path, ccusage_enabled=True), EventBus())

    assert manager.cache["version"] == 3
    assert manager.cache["sources"]["codex"]["source_id"] == "codex"
    assert manager.cache["sources"]["codex"]["source_label"] == "Codex"
    assert "provider" not in manager.cache["sources"]["codex"]
    assert manager.snapshot()["collector"]["refreshed_at"] == 123.0


def test_usage_snapshot_exposes_the_latest_unified_install_command(tmp_path: Path) -> None:
    manager = UsageManager(Config(data_dir=tmp_path), EventBus())

    snapshot = manager.snapshot()

    assert snapshot["package"] == CCUSAGE_PACKAGE
    assert snapshot["install_command"] == f"npm install -g {CCUSAGE_PACKAGE}"


def test_current_codex_model_map_is_aggregated_with_proportional_cost() -> None:
    payload = json.loads((FIXTURES / "codex-v20.json").read_text(encoding="utf-8"))

    normalized = normalize_usage(payload, "codex", {"adapter": "fixture"})

    assert normalized["models"] == [
        {
            "model": "gpt-5.2-codex",
            "cost_is_estimate": True,
            "cost_method": "proportional",
            "input_tokens": 500,
            "output_tokens": 125,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 75,
            "total_tokens": 700,
            "cost_usd": 0.42,
        }
    ]
    assert normalized["model_daily"][0]["date"] == "2026-07-11"
    assert normalized["model_daily"][0]["cost_method"] == "proportional"


def test_unified_default_discovers_sources_in_one_ccusage_process() -> None:
    config = Config()
    assert config.usage_command == ["ccusage", "daily", "--json", "--by-agent"]
    assert config.usage_commands == {}


def test_by_agent_payload_splits_managed_and_external_sources() -> None:
    payload = json.loads((FIXTURES / "ccusage-by-agent-v20.json").read_text(encoding="utf-8"))

    sources = normalize_usage_sources(payload, {"adapter": "fixture"})

    assert set(sources) == {"claude", "opencode"}
    assert sources["claude"]["source_label"] == "Claude Code"
    assert sources["opencode"]["source_label"] == "OpenCode"
    assert sources["opencode"]["totals"]["total_tokens"] == 120
    assert sources["opencode"]["models"][0]["model"] == "gemini-2.5-pro"


def test_usage_command_resolves_windows_batch_shim_through_comspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "swe_mux.usage.shutil.which", lambda _: r"C:\Users\me\AppData\Roaming\npm\ccusage.cmd"
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    prepared = prepare_usage_command(default_ccusage_command(), windows=True)

    assert prepared == [
        r"C:\Windows\System32\cmd.exe",
        "/d",
        "/c",
        r"C:\Users\me\AppData\Roaming\npm\ccusage.cmd",
        "daily",
        "--json",
        "--by-agent",
    ]


def test_missing_unified_usage_command_has_exact_install_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.usage.shutil.which", lambda _: None)
    with pytest.raises(UsageAdapterError, match=f"npm install -g {CCUSAGE_PACKAGE}"):
        prepare_usage_command(default_ccusage_command())
