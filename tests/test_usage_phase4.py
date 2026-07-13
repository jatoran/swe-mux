from __future__ import annotations

import json
from pathlib import Path
from types import MethodType

import pytest

from swe_mux.config import CCUSAGE_PACKAGE, Config, default_ccusage_command
from swe_mux.event_bus import EventBus
from swe_mux.usage import UsageAdapterError, UsageManager, normalize_usage, prepare_usage_command

FIXTURES = Path(__file__).parent / "fixtures" / "usage"


@pytest.mark.parametrize(
    ("filename", "provider", "model", "total"),
    [
        ("claude-v17.json", "claude", "claude-opus", 2000),
        ("codex-v0.json", "codex", "gpt-5", 700),
    ],
)
def test_pinned_usage_fixtures_normalize_without_live_cli(
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


def test_usage_snapshot_exposes_the_pinned_unified_install_command(tmp_path: Path) -> None:
    manager = UsageManager(Config(data_dir=tmp_path), EventBus())

    snapshot = manager.snapshot()

    assert snapshot["package"] == CCUSAGE_PACKAGE
    assert snapshot["install_command"] == f"npm install -g {CCUSAGE_PACKAGE}"


def test_unified_defaults_select_each_provider_from_one_ccusage_executable() -> None:
    config = Config()
    assert config.ccusage_claude_command == default_ccusage_command("claude")
    assert config.ccusage_codex_command == default_ccusage_command("codex")
    assert config.ccusage_claude_command[0] == config.ccusage_codex_command[0] == "ccusage"


def test_usage_command_resolves_windows_batch_shim_through_comspec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "swe_mux.usage.shutil.which", lambda _: r"C:\Users\me\AppData\Roaming\npm\ccusage.cmd"
    )
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")

    prepared = prepare_usage_command(default_ccusage_command("codex"), windows=True)

    assert prepared[:4] == [r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c"]
    assert "ccusage.cmd" in prepared[4]
    assert "codex daily --json" in prepared[4]


def test_missing_unified_usage_command_has_exact_install_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("swe_mux.usage.shutil.which", lambda _: None)
    with pytest.raises(UsageAdapterError, match=f"npm install -g {CCUSAGE_PACKAGE}"):
        prepare_usage_command(default_ccusage_command("claude"))
