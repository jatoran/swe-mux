from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from swe_mux.config import (
    BUILTIN_THEME_PAIRS,
    contrast_ratio,
    default_ccusage_command,
    load_config,
    update_config,
)


def test_legacy_config_migrates_with_backup_and_removes_obsolete_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'host = "127.0.0.1"\nport = 9001\ntoken = "keep-me"\n'
        'shell_exe = "pwsh.exe"\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.schema_version == 4
    assert config.shell_profiles[0].executable == "pwsh.exe"
    assert path.with_suffix(".toml.bak").is_file()
    assert "token" not in tomllib.loads(path.read_text(encoding="utf-8"))
    assert "token" not in config.public_dict()
    assert config.public_dict()["access_mode"] == "local+tailnet"


def test_non_loopback_bind_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('schema_version = 4\nhost = "0.0.0.0"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="detected Tailscale address"):
        load_config(path)


def test_tailnet_listener_setting_is_restart_scoped(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    hot, restart = update_config(config, {"tailnet_enabled": False})
    assert hot == set()
    assert restart == {"tailnet_enabled"}
    assert config.public_dict()["access_mode"] == "loopback"


def test_legacy_separate_ccusage_commands_migrate_to_one_unified_cli(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 4\n'
        'ccusage_claude_command = ["npx", "--no-install", "ccusage@17.1.5", '
        '"daily", "--json"]\n'
        'ccusage_codex_command = ["npx", "--no-install", "@ccusage/codex@0.2.7", '
        '"daily", "--json"]\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.ccusage_claude_command == default_ccusage_command("claude")
    assert config.ccusage_codex_command == default_ccusage_command("codex")
    assert path.with_suffix(".toml.bak").is_file()
    persisted = tomllib.loads(path.read_text(encoding="utf-8"))
    assert persisted["ccusage_claude_command"] == ["ccusage", "claude", "daily", "--json"]
    assert persisted["ccusage_codex_command"] == ["ccusage", "codex", "daily", "--json"]


def test_invalid_update_changes_neither_memory_nor_disk(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    before = path.read_bytes()
    revision = config.revision

    with pytest.raises(ValueError):
        update_config(config, {"port": 70000})

    assert config.port == 8765
    assert config.revision == revision
    assert path.read_bytes() == before


def test_safe_update_is_atomic_and_revisioned(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    hot, restart = update_config(config, {"theme": "tokyo-night", "port": 9010})

    assert hot == {"theme"}
    assert restart == {"port"}
    assert config.revision == 2
    assert not path.with_suffix(".toml.tmp").exists()
    loaded = load_config(path)
    assert loaded.theme == "tokyo-night"
    assert loaded.port == 9010


def test_builtin_themes_and_custom_text_meet_readability_contract(tmp_path: Path) -> None:
    assert all(contrast_ratio(*pair) >= 4.5 for pair in BUILTIN_THEME_PAIRS.values())
    config = load_config(tmp_path / "config.toml")
    before = (tmp_path / "config.toml").read_bytes()
    unreadable = {**config.custom_theme, "foreground": "#0a0a0a"}
    with pytest.raises(ValueError, match="4.5:1"):
        update_config(config, {"theme": "custom", "custom_theme": unreadable})
    assert (tmp_path / "config.toml").read_bytes() == before
