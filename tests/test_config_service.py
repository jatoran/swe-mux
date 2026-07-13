from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from swe_mux.config import BUILTIN_THEME_PAIRS, contrast_ratio, load_config, update_config


def test_legacy_config_migrates_with_backup_and_keeps_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'host = "127.0.0.1"\nport = 9001\ntoken = "keep-me"\n'
        'shell_exe = "pwsh.exe"\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.schema_version == 2
    assert config.token == "keep-me"
    assert config.shell_profiles[0].executable == "pwsh.exe"
    assert path.with_suffix(".toml.bak").is_file()
    assert tomllib.loads(path.read_text(encoding="utf-8"))["token"] == "keep-me"
    assert "token" not in config.public_dict()


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
