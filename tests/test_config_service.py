from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from swe_mux.config import (
    BUILTIN_THEME_PAIRS,
    DEFAULT_PROJECT_IGNORE_PATTERNS,
    SCHEMA_VERSION,
    contrast_ratio,
    default_ccusage_command,
    load_config,
    update_config,
)


def test_legacy_config_migrates_with_backup_and_removes_obsolete_secret(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'host = "127.0.0.1"\nport = 9001\ntoken = "keep-me"\nshell_exe = "pwsh.exe"\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.schema_version == SCHEMA_VERSION
    assert config.shell_profiles[0].executable == "pwsh.exe"
    assert path.with_suffix(".toml.bak").is_file()
    assert "token" not in tomllib.loads(path.read_text(encoding="utf-8"))
    assert "token" not in config.public_dict()
    assert "notes_default_open" not in config.public_dict()
    assert config.public_dict()["access_mode"] == "local+tailnet"


def test_first_run_prefers_powershell_7_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "swe_mux.config.shutil.which",
        lambda command: rf"C:\Program Files\PowerShell\7\{command}"
        if command == "pwsh.exe"
        else None,
    )

    config = load_config(tmp_path / "config.toml")

    assert config.shell_exe == "pwsh.exe"
    assert config.default_shell_profile == "default"
    assert config.shell_profiles[0].label == "PowerShell 7"
    assert config.shell_profiles[0].executable == "pwsh.exe"
    assert config.shell_profiles[0].args == ["-NoLogo"]
    assert config.shell_profiles[0].marker == "ps7"


def test_first_run_falls_back_to_windows_powershell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.config.shutil.which", lambda command: None)

    config = load_config(tmp_path / "config.toml")

    assert config.shell_exe == "powershell.exe"
    assert config.shell_profiles[0].label == "Windows PowerShell"
    assert config.shell_profiles[0].executable == "powershell.exe"
    assert config.shell_profiles[0].marker == "ps"


def test_untouched_legacy_default_upgrades_to_powershell_7_but_custom_profile_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "swe_mux.config.shutil.which",
        lambda command: rf"C:\Program Files\PowerShell\7\{command}"
        if command == "pwsh.exe"
        else None,
    )
    untouched = tmp_path / "untouched.toml"
    untouched.write_text(
        'schema_version = 12\nshell_exe = "powershell.exe"\n'
        'default_shell_profile = "default"\n'
        "[[shell_profiles]]\n"
        'id = "default"\nlabel = "Default shell"\nexecutable = "powershell.exe"\n'
        'args = ["-NoLogo"]\nmarker = "ps"\n',
        encoding="utf-8",
    )
    customized = tmp_path / "customized.toml"
    customized.write_text(
        untouched.read_text(encoding="utf-8").replace("Default shell", "My Windows shell"),
        encoding="utf-8",
    )

    upgraded = load_config(untouched)
    preserved = load_config(customized)

    assert upgraded.shell_exe == "pwsh.exe"
    assert upgraded.shell_profiles[0].label == "PowerShell 7"
    assert untouched.with_suffix(".toml.bak").is_file()
    assert preserved.shell_exe == "powershell.exe"
    assert preserved.shell_profiles[0].label == "My Windows shell"


def test_auto_managed_windows_default_retries_detection_after_schema_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        f'schema_version = {SCHEMA_VERSION}\nshell_exe = "powershell.exe"\n'
        'default_shell_profile = "default"\n'
        "[[shell_profiles]]\n"
        'id = "default"\nlabel = "Windows PowerShell"\nexecutable = "powershell.exe"\n'
        'args = ["-NoLogo"]\nmarker = "ps"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "swe_mux.config.shutil.which",
        lambda command: rf"C:\Program Files\PowerShell\7\{command}"
        if command == "pwsh.exe"
        else None,
    )

    config = load_config(path)

    assert config.shell_exe == "pwsh.exe"
    assert config.shell_profiles[0].label == "PowerShell 7"
    persisted = tomllib.loads(path.read_text(encoding="utf-8"))
    assert persisted["shell_exe"] == "pwsh.exe"
    assert persisted["shell_profiles"][0]["label"] == "PowerShell 7"


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
        "schema_version = 4\n"
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


def test_note_opening_default_is_hot_reloadable_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.notes_default_open == "dock"

    hot, restart = update_config(config, {"notes_default_open": "popout"})

    assert hot == {"notes_default_open"}
    assert restart == set()
    assert load_config(path).notes_default_open == "popout"
    with pytest.raises(ValueError, match="dock or popout"):
        update_config(config, {"notes_default_open": "window"})


def test_project_ignore_defaults_are_hot_reloadable_and_bounded(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    assert {".venv", ".mypy_cache", ".pytest_cache", "node_modules", "uv.lock"} <= set(
        DEFAULT_PROJECT_IGNORE_PATTERNS
    )
    hot, restart = update_config(config, {"project_ignore_patterns": ["vendor", "*.tmp"]})
    assert hot == {"project_ignore_patterns"}
    assert restart == set()
    assert load_config(tmp_path / "config.toml").project_ignore_patterns == ["vendor", "*.tmp"]
    with pytest.raises(ValueError, match="at most 256"):
        update_config(config, {"project_ignore_patterns": ["x"] * 257})


def test_mobile_input_defaults_are_hot_reloadable_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    assert config.mobile_vertical_drag == "smart"
    assert config.mobile_scroll_direction == "natural"
    assert config.mobile_scroll_sensitivity == 1.0
    assert config.mobile_long_press == "context_menu"

    hot, restart = update_config(
        config,
        {
            "mobile_vertical_drag": "application",
            "mobile_scroll_direction": "wheel",
            "mobile_scroll_sensitivity": 1.5,
            "mobile_long_press": "disabled",
        },
    )

    assert hot == {
        "mobile_vertical_drag",
        "mobile_scroll_direction",
        "mobile_scroll_sensitivity",
        "mobile_long_press",
    }
    assert restart == set()
    with pytest.raises(ValueError, match="smart, terminal, application, or disabled"):
        update_config(config, {"mobile_vertical_drag": "swipe"})
    with pytest.raises(ValueError, match="between 0.25 and 4"):
        update_config(config, {"mobile_scroll_sensitivity": 10})


def test_builtin_themes_and_custom_text_meet_readability_contract(tmp_path: Path) -> None:
    assert all(contrast_ratio(*pair) >= 4.5 for pair in BUILTIN_THEME_PAIRS.values())
    config = load_config(tmp_path / "config.toml")
    before = (tmp_path / "config.toml").read_bytes()
    unreadable = {**config.custom_theme, "foreground": "#0a0a0a"}
    with pytest.raises(ValueError, match="4.5:1"):
        update_config(config, {"theme": "custom", "custom_theme": unreadable})
    assert (tmp_path / "config.toml").read_bytes() == before
