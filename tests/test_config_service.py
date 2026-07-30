from __future__ import annotations

import sys
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
    windows_pty_compatibility,
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


def test_public_config_describes_the_host_pty_for_xterm(tmp_path: Path) -> None:
    """Browsers cannot detect ConPTY, and xterm needs it to size and reflow correctly."""
    descriptor = load_config(tmp_path / "config.toml").public_dict()["pty_windows"]

    assert descriptor == windows_pty_compatibility()
    if sys.platform == "win32":
        assert isinstance(descriptor, dict)
        assert descriptor["backend"] == "conpty"
        assert isinstance(descriptor["build_number"], int)
        assert descriptor["build_number"] > 0
    else:
        assert descriptor is None


def test_conversation_stt_defaults_and_untouched_sapi_pair_migrate_to_whisper(
    tmp_path: Path,
) -> None:
    fresh = load_config(tmp_path / "fresh" / "config.toml")
    assert fresh.stt_engine == "whisper"
    assert fresh.stt_whisper_model == "turbo"

    legacy_path = tmp_path / "legacy" / "config.toml"
    legacy_path.parent.mkdir()
    legacy_path.write_text(
        'schema_version = 13\nstt_engine = "sapi"\nstt_whisper_model = "base.en"\n',
        encoding="utf-8",
    )
    migrated = load_config(legacy_path)
    assert migrated.stt_engine == "whisper"
    assert migrated.stt_whisper_model == "turbo"
    assert legacy_path.with_suffix(".toml.bak").is_file()

    custom_path = tmp_path / "custom" / "config.toml"
    custom_path.parent.mkdir()
    custom_path.write_text(
        'schema_version = 13\nstt_engine = "sapi"\nstt_whisper_model = "small.en"\n',
        encoding="utf-8",
    )
    custom = load_config(custom_path)
    assert custom.stt_engine == "sapi"
    assert custom.stt_whisper_model == "small.en"


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


def test_note_editor_settings_are_hot_reloadable_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    assert config.note_spellcheck is False
    assert config.note_syntax == "markdown"
    assert config.note_tab_behavior == "indent"
    assert config.note_shortcut_policy == "browser-safe"
    assert config.note_command_rail == "auto"
    # Continuity defaults its guides off so an upgrade changes no embedder; we turn them on.
    assert config.note_indent_guides is True
    # Zero/blank means "keep the editor's own default" rather than pinning one here.
    assert config.note_font_family == ""
    assert config.note_font_size_px == 0
    assert config.note_line_height == 0.0
    assert config.note_rail_button_size_px == 0

    hot, restart = update_config(
        config,
        {
            "note_spellcheck": True,
            "note_syntax": "plain",
            "note_tab_behavior": "focus",
            "note_shortcut_policy": "editor-first",
            "note_command_rail": "on",
            "note_indent_guides": False,
            "note_font_family": "Iosevka",
            "note_font_size_px": 18,
            "note_line_height": 1.8,
            "note_rail_button_size_px": 56,
        },
    )

    assert restart == set()
    assert hot == {
        "note_spellcheck",
        "note_syntax",
        "note_tab_behavior",
        "note_shortcut_policy",
        "note_command_rail",
        "note_indent_guides",
        "note_font_family",
        "note_font_size_px",
        "note_line_height",
        "note_rail_button_size_px",
    }
    reloaded = load_config(path)
    assert reloaded.note_indent_guides is False
    assert reloaded.note_syntax == "plain"
    assert reloaded.note_font_family == "Iosevka"
    assert reloaded.note_line_height == 1.8

    with pytest.raises(ValueError, match="markdown or plain"):
        update_config(config, {"note_syntax": "rich"})
    with pytest.raises(ValueError, match="browser-safe, editor-first, or none"):
        update_config(config, {"note_shortcut_policy": "editor-only"})
    with pytest.raises(ValueError, match="between 8 and 48"):
        update_config(config, {"note_font_size_px": 400})
    with pytest.raises(ValueError, match="between 32 and 96"):
        update_config(config, {"note_rail_button_size_px": 8})


def test_note_shortcut_overrides_survive_a_round_trip_and_reject_junk(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    # Reclaims the two chords the editor binds but flags non-browser-safe.
    assert config.note_shortcut_overrides == {
        "mod+r": "editor.toggle_bullet_at_line_start",
        "mod+e": "markdown.toggle_task",
    }

    # A chord is not a bare TOML key, so the serializer has to quote dict keys.
    hot, restart = update_config(
        config,
        {"note_shortcut_overrides": {"mod+shift+r": "editor.reverse_lines", "mod+k": ""}},
    )
    assert hot == {"note_shortcut_overrides"}
    assert restart == set()
    assert load_config(path).note_shortcut_overrides == {
        "mod+shift+r": "editor.reverse_lines",
        # "" is the released-chord marker: TOML has no null.
        "mod+k": "",
    }

    with pytest.raises(ValueError, match="mod \\+r"):
        update_config(config, {"note_shortcut_overrides": {"mod +r": "editor.undo"}})
    with pytest.raises(ValueError, match="mod\\+q"):
        update_config(config, {"note_shortcut_overrides": {"mod+q": "not a command"}})
    with pytest.raises(ValueError, match="at most 128"):
        update_config(
            config,
            {"note_shortcut_overrides": {f"alt+{index}": "editor.undo" for index in range(129)}},
        )


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
    assert config.terminal_auto_copy_selection is True

    hot, restart = update_config(
        config,
        {
            "mobile_vertical_drag": "application",
            "mobile_scroll_direction": "wheel",
            "mobile_scroll_sensitivity": 1.5,
            "mobile_long_press": "disabled",
            "terminal_auto_copy_selection": False,
        },
    )

    assert hot == {
        "mobile_vertical_drag",
        "mobile_scroll_direction",
        "mobile_scroll_sensitivity",
        "mobile_long_press",
        "terminal_auto_copy_selection",
    }
    assert restart == set()
    with pytest.raises(ValueError, match="smart, terminal, application, or disabled"):
        update_config(config, {"mobile_vertical_drag": "swipe"})
    with pytest.raises(ValueError, match="between 0.25 and 4"):
        update_config(config, {"mobile_scroll_sensitivity": 10})


def test_mobile_gestures_default_and_are_hot_reloadable_and_validated(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    assert config.mobile_gestures == {
        "swipe_left": "mobileTab.next",
        "swipe_right": "mobileTab.previous",
        # Directional: leftward pulls in the right-edge clipboard panel, rightward
        # the left-edge sidebar (both were sidebar.toggle before the panel existed).
        "two_finger_swipe_left": "drawer.toggle",
        "two_finger_swipe_right": "sidebar.toggle",
        "two_finger_swipe_up": "session.note",
        "two_finger_swipe_down": "terminal.keyboardToggle",
        "two_finger_tap": "palette.open",
    }

    # Vertical two-finger slots are real, mappable slots (only single-finger
    # vertical stays reserved for the terminal).
    hot, _ = update_config(
        config, {"mobile_gestures": {"two_finger_swipe_up": "processes.open"}}
    )
    assert hot == {"mobile_gestures"}
    assert config.mobile_gestures == {"two_finger_swipe_up": "processes.open"}

    hot, restart = update_config(
        config,
        {"mobile_gestures": {"swipe_left": "palette.open", "two_finger_tap": ""}},
    )
    assert hot == {"mobile_gestures"}
    assert restart == set()
    assert config.mobile_gestures == {"swipe_left": "palette.open", "two_finger_tap": ""}

    with pytest.raises(ValueError, match="unknown command for gestures"):
        update_config(config, {"mobile_gestures": {"swipe_left": "does.not.exist"}})
    with pytest.raises(ValueError, match="unknown gesture slots"):
        update_config(config, {"mobile_gestures": {"triple_tap": "palette.open"}})


def test_swipe_away_close_defaults_on_and_is_hot_reloadable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.mobile_gesture_swipe_away_close is True

    hot, restart = update_config(config, {"mobile_gesture_swipe_away_close": False})
    assert hot == {"mobile_gesture_swipe_away_close"}
    assert restart == set()
    assert config.mobile_gesture_swipe_away_close is False

    reloaded = load_config(path)
    assert reloaded.mobile_gesture_swipe_away_close is False


def test_legacy_sidebar_gestures_migrate_to_toggle(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "schema_version = 15",
                "[mobile_gestures]",
                'two_finger_swipe_right = "sidebar.open"',
                'two_finger_swipe_left = "sidebar.close"',
                'two_finger_tap = "palette.open"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.mobile_gestures["two_finger_swipe_right"] == "sidebar.toggle"
    # Chained through the schema-17 migration: the leftward swipe's old default
    # (sidebar.toggle, redundant with the rightward one) now opens the clipboard panel.
    assert config.mobile_gestures["two_finger_swipe_left"] == "drawer.toggle"
    # A custom, non-default binding is preserved rather than force-migrated.
    path2 = tmp_path / "custom.toml"
    path2.write_text(
        "\n".join(
            [
                "schema_version = 15",
                "[mobile_gestures]",
                'two_finger_swipe_right = "palette.open"',
            ]
        ),
        encoding="utf-8",
    )
    custom = load_config(path2)
    assert custom.mobile_gestures["two_finger_swipe_right"] == "palette.open"


def test_terminal_renderer_is_hot_reloadable_and_validated(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")

    assert config.terminal_renderer == "auto"
    hot, restart = update_config(config, {"terminal_renderer": "dom"})

    assert hot == {"terminal_renderer"}
    assert restart == set()
    assert load_config(tmp_path / "config.toml").terminal_renderer == "dom"
    with pytest.raises(ValueError, match="auto, dom, or webgl"):
        update_config(config, {"terminal_renderer": "canvas"})


def test_builtin_themes_and_custom_text_meet_readability_contract(tmp_path: Path) -> None:
    assert all(contrast_ratio(*pair) >= 4.5 for pair in BUILTIN_THEME_PAIRS.values())
    config = load_config(tmp_path / "config.toml")
    before = (tmp_path / "config.toml").read_bytes()
    unreadable = {**config.custom_theme, "foreground": "#0a0a0a"}
    with pytest.raises(ValueError, match="4.5:1"):
        update_config(config, {"theme": "custom", "custom_theme": unreadable})
    assert (tmp_path / "config.toml").read_bytes() == before
