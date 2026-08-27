from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from swe_mux.config import (
    BUILTIN_THEME_PAIRS,
    DEFAULT_PROJECT_IGNORE_PATTERNS,
    PROJECT_IGNORE_PATTERN_LIMIT,
    SCHEMA_32_IGNORE_ADDITIONS,
    SCHEMA_VERSION,
    WORKTREE_IGNORE_PATTERNS,
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


def test_the_scan_timeline_runs_under_the_global_ceilings(tmp_path: Path) -> None:
    """The scan's dedicated caps are gone; the global ones must accommodate it.

    A continuous sampler needs headroom the episodic caps never gave it, and now
    that the scan spends under `automation_daily_budget` and emits under
    `automation_max_output_tokens`, those defaults have to hold its worst case:
    a per-call output ceiling below ~900 tokens truncates the scan schema's own
    prose budget into an unparseable strict-JSON body.
    """
    config = load_config(tmp_path / "config.toml")

    assert not hasattr(config, "scan_timeline_daily_budget")
    assert not hasattr(config, "scan_timeline_run_budget")
    assert not hasattr(config, "scan_timeline_hourly_call_cap")
    assert not hasattr(config, "scan_timeline_max_output_tokens")
    assert config.automation_daily_budget.tokens is not None
    assert config.automation_max_output_tokens >= 900


def test_the_schema_34_ceiling_lift_absorbs_the_retired_output_caps(tmp_path: Path) -> None:
    """An upgraded config's global output ceiling covers what the retired caps did.

    The scan enforced 900 through its own field; a global ceiling left at the
    old 256 default would truncate every scan response on upgrade. The lift
    takes the loosest of the three and never lowers a deliberate choice.
    """
    lifted = tmp_path / "lifted" / "config.toml"
    lifted.parent.mkdir()
    lifted.write_text(
        "schema_version = 33\nautomation_max_output_tokens = 256\n", encoding="utf-8"
    )
    assert load_config(lifted).automation_max_output_tokens == 900

    deliberate = tmp_path / "deliberate" / "config.toml"
    deliberate.parent.mkdir()
    deliberate.write_text(
        "schema_version = 33\n"
        "automation_max_output_tokens = 4096\n"
        "scan_timeline_max_output_tokens = 900\n",
        encoding="utf-8",
    )
    assert load_config(deliberate).automation_max_output_tokens == 4096

    lowered_scan = tmp_path / "lowered" / "config.toml"
    lowered_scan.parent.mkdir()
    lowered_scan.write_text(
        "schema_version = 33\n"
        "automation_max_output_tokens = 256\n"
        "scan_timeline_max_output_tokens = 300\n",
        encoding="utf-8",
    )
    assert load_config(lowered_scan).automation_max_output_tokens == 300


def test_untouched_legacy_automation_caps_are_lifted_on_upgrade(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy" / "config.toml"
    legacy.parent.mkdir()
    legacy.write_text(
        "schema_version = 22\n"
        "automation_rule_daily_token_budget = 50000\n"
        "automation_hourly_call_cap = 60\n"
        "automation_daily_token_budget = 123456\n",
        encoding="utf-8",
    )
    migrated = load_config(legacy)
    assert migrated.automation_rule_daily_budget.tokens == 4_000_000
    assert migrated.automation_hourly_call_cap == 1_200
    # Not a schema-22 default, so it was a deliberate choice and survives.
    assert migrated.automation_daily_budget.tokens == 123_456


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


def test_voice_command_migration_adds_only_new_schema_20_actions(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 19\n'
        '[[voice_commands]]\naction = "send"\nphrases = ["ship this"]\n'
        '[[voice_commands]]\naction = "cancel"\nphrases = []\n',
        encoding="utf-8",
    )

    config = load_config(path)
    commands = {item["action"]: item["phrases"] for item in config.voice_commands}

    assert commands["send"] == ["ship this"]
    assert commands["cancel"] == []
    # Schema 20 added append/comms; schema 28 added the brainstorm hold pair.
    assert set(commands) == {
        "send", "cancel", "append", "comms_on", "comms_off", "hold", "proceed",
    }


def test_voice_command_migration_adds_only_new_schema_28_actions(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 27\n'
        '[[voice_commands]]\naction = "send"\nphrases = ["ship this"]\n'
        '[[voice_commands]]\naction = "hold"\nphrases = ["shush"]\n',
        encoding="utf-8",
    )
    config = load_config(path)
    commands = {item["action"]: item["phrases"] for item in config.voice_commands}
    # A customized hold survives; only the genuinely missing proceed is added.
    assert commands["send"] == ["ship this"]
    assert commands["hold"] == ["shush"]
    assert commands["proceed"] == ["go ahead", "your turn", "over to you", "proceed"]


def test_voice_command_migration_adds_bare_stop_only_to_stock_mute_phrases(
    tmp_path: Path,
) -> None:
    stock_path = tmp_path / "stock.toml"
    stock_path.write_text(
        'schema_version = 20\n'
        '[[voice_commands]]\naction = "mute"\n'
        'phrases = ["mute", "stop speaking", "stop playback", "stop audio"]\n',
        encoding="utf-8",
    )
    custom_path = tmp_path / "custom.toml"
    custom_path.write_text(
        'schema_version = 20\n'
        '[[voice_commands]]\naction = "mute"\nphrases = ["silence"]\n',
        encoding="utf-8",
    )

    stock = load_config(stock_path)
    custom = load_config(custom_path)

    assert stock.voice_commands[0]["phrases"] == [
        "mute", "stop", "stop speaking", "stop playback", "stop audio",
    ]
    assert custom.voice_commands[0]["phrases"] == ["silence"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell first-run defaults")
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell first-run defaults")
def test_first_run_falls_back_to_windows_powershell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.config.shutil.which", lambda command: None)

    config = load_config(tmp_path / "config.toml")

    assert config.shell_exe == "powershell.exe"
    assert config.shell_profiles[0].label == "Windows PowerShell"
    assert config.shell_profiles[0].executable == "powershell.exe"
    assert config.shell_profiles[0].marker == "ps"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell first-run defaults")
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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell first-run defaults")
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

    assert config.usage_command == default_ccusage_command()
    assert config.usage_commands == {}
    assert path.with_suffix(".toml.bak").is_file()
    persisted = tomllib.loads(path.read_text(encoding="utf-8"))
    assert persisted["usage_command"] == ["ccusage", "daily", "--json", "--by-agent"]
    assert persisted["usage_commands"] == {}


def test_custom_usage_source_override_survives_unified_command_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 24\n"
        'usage_command = ["custom-collector", "--json"]\n'
        '[usage_commands]\n'
        'opencode = ["custom-opencode", "--json"]\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.usage_command == ["custom-collector", "--json"]
    assert config.usage_commands == {"opencode": ["custom-opencode", "--json"]}


def test_legacy_harness_executable_and_argument_keys_migrate_to_registry_maps(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 18\n"
        'claude_exe = "claude-custom"\n'
        'codex_exe = "codex-custom"\n'
        'claude_args = ["--claude-flag"]\n'
        'codex_args = ["--codex-flag"]\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.harness_exe == {
        "claude": "claude-custom",
        "codex": "codex-custom",
        "omp": "omp",
        "pi": "pi",
        "opencode": "opencode",
    }
    assert config.harness_args == {
        "claude": ["--claude-flag"],
        "codex": ["--codex-flag"],
        "omp": [],
        "pi": [],
        "opencode": [],
    }
    persisted = tomllib.loads(path.read_text(encoding="utf-8"))
    assert persisted["harness_exe"] == config.harness_exe
    assert persisted["harness_args"] == config.harness_args
    assert "claude_exe" not in persisted
    assert "codex_args" not in persisted


def test_worktree_ignore_patterns_reach_an_install_that_already_persisted_the_old_list(
    tmp_path: Path,
) -> None:
    # The trap this exists to close: `project_ignore_patterns` is written out in full, so a
    # new entry in the *defaults* reaches brand-new installs only. Ship it without this and
    # every machine that has ever run swe-mux keeps browsing its own worktrees, which is
    # indistinguishable from never having shipped the fix.
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 31\nproject_ignore_patterns = [".git", "node_modules", "mine"]\n',
        encoding="utf-8",
    )

    config = load_config(path)

    assert all(
        pattern in config.project_ignore_patterns for pattern in SCHEMA_32_IGNORE_ADDITIONS
    )
    assert all(pattern in SCHEMA_32_IGNORE_ADDITIONS for pattern in WORKTREE_IGNORE_PATTERNS)
    # An operator's own entries and their order survive; the migration only appends.
    assert config.project_ignore_patterns[:3] == [".git", "node_modules", "mine"]
    persisted = tomllib.loads(path.read_text(encoding="utf-8"))
    assert ".claude/worktrees" in persisted["project_ignore_patterns"]
    assert ".trash" in persisted["project_ignore_patterns"]


def test_the_worktree_pattern_migration_does_not_duplicate_what_is_already_there(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'schema_version = 31\nproject_ignore_patterns = [".git", ".claude/worktrees/"]\n',
        encoding="utf-8",
    )

    patterns = load_config(path).project_ignore_patterns

    # Compared after the same normalization the matcher applies, so a trailing slash is
    # recognized as the pattern it already is rather than added beside it.
    assert patterns.count(".claude/worktrees/") == 1
    assert ".claude/worktrees" not in patterns


def test_the_worktree_pattern_migration_respects_the_ceiling_validation_enforces(
    tmp_path: Path,
) -> None:
    # Appending past the limit would turn a silent upgrade into a daemon that refuses to
    # start, on a config the migration itself made invalid.
    existing = [f"pattern{index}" for index in range(PROJECT_IGNORE_PATTERN_LIMIT - 1)]
    path = tmp_path / "config.toml"
    path.write_text(
        f"schema_version = 31\nproject_ignore_patterns = {existing!r}\n".replace("'", '"'),
        encoding="utf-8",
    )

    patterns = load_config(path).project_ignore_patterns

    assert len(patterns) == PROJECT_IGNORE_PATTERN_LIMIT
    assert patterns[-1] == SCHEMA_32_IGNORE_ADDITIONS[0]


def test_enabled_legacy_attention_observer_switch_survives_its_rename(tmp_path: Path) -> None:
    # `load_config` copies only known dataclass fields, so a bare rename would drop
    # the old key and re-save without it - three observers silently turning off on
    # upgrade. The migration must carry the value, not merely tolerate the key.
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 30\nphase7_observers_enabled = true\n",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.attention_observers_enabled is True
    persisted = tomllib.loads(path.read_text(encoding="utf-8"))
    assert persisted["attention_observers_enabled"] is True
    assert "phase7_observers_enabled" not in persisted


def test_disabled_legacy_attention_observer_switch_stays_disabled(tmp_path: Path) -> None:
    # The off case is not covered by the default: it must migrate as a recorded
    # choice, so a later change to the field's default cannot silently turn it on.
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 30\nphase7_observers_enabled = false\n",
        encoding="utf-8",
    )

    assert load_config(path).attention_observers_enabled is False


def test_current_attention_observer_switch_wins_over_a_stale_legacy_key(
    tmp_path: Path,
) -> None:
    # A config carrying both names was written by the new build; the legacy key is
    # residue from a hand edit or a merged file and must not overwrite the live one.
    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 30\n"
        "phase7_observers_enabled = false\n"
        "attention_observers_enabled = true\n",
        encoding="utf-8",
    )

    assert load_config(path).attention_observers_enabled is True


def test_harness_enabled_holds_only_explicit_choices_and_hot_reloads(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    # Empty by default: a fresh install follows detection for every harness rather
    # than pinning any of them, so a CLI installed next week appears on its own.
    assert config.harness_enabled == {}

    hot, restart = update_config(config, {"harness_enabled": {"codex": False, "claude": True}})

    # A launcher/UI filter, so it applies live rather than forcing a restart.
    assert hot == {"harness_enabled"}
    assert restart == set()
    assert load_config(path).harness_enabled == {"codex": False, "claude": True}


def test_first_run_shows_only_for_a_fresh_install(tmp_path: Path) -> None:
    # A brand-new install (no config file) starts with the first-run panel pending.
    fresh = load_config(tmp_path / "fresh.toml")
    assert fresh.harness_setup_complete is False

    # An existing config from before the flag is not a first run: it upgrades to
    # complete so a long-running install never suddenly sees the setup panel.
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('schema_version = 21\nshell_exe = "powershell.exe"\n', encoding="utf-8")
    migrated = load_config(legacy)
    assert migrated.harness_setup_complete is True


def test_harness_enabled_rejects_unknown_harnesses_and_non_bool_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    with pytest.raises(ValueError, match="unknown harnesses"):
        update_config(config, {"harness_enabled": {"ghost": True}})
    with pytest.raises(ValueError, match="harness names to booleans"):
        update_config(config, {"harness_enabled": {"claude": "yes"}})


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


def test_ui_scale_is_per_device_class_hot_reloadable_and_stepped(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    # Both default to 1: installing this build must not resize anyone's UI.
    assert config.ui_scale_desktop == 1.0
    assert config.ui_scale_mobile == 1.0

    hot, restart = update_config(config, {"ui_scale_mobile": 1.25})

    assert hot == {"ui_scale_mobile"}
    assert restart == set()
    reloaded = load_config(path)
    assert reloaded.ui_scale_mobile == 1.25
    # The two classes are independent — sizing the phone must not touch the desktop.
    assert reloaded.ui_scale_desktop == 1.0

    # A JSON PATCH sends bare `1` for a whole number, which is a legitimate
    # spelling of the 1.0 step and must not be rejected as the wrong type.
    update_config(config, {"ui_scale_desktop": 1.4})
    hot, _ = update_config(config, {"ui_scale_desktop": 1})
    assert hot == {"ui_scale_desktop"}
    assert load_config(path).ui_scale_desktop == 1

    for bad in (2.0, 0.5, 1.05, "1.25"):
        with pytest.raises(ValueError, match="ui_scale_desktop"):
            update_config(config, {"ui_scale_desktop": bad})
    # A rejected value leaves the stored config untouched.
    assert load_config(path).ui_scale_desktop == 1.0


def test_claude_width_envelope_is_hot_reloadable_stepped_and_disableable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)

    # Today's behaviour: installing this build must not reshape anyone's Claude pane.
    assert config.claude_max_columns == 120

    hot, restart = update_config(config, {"claude_max_columns": 200})
    assert hot == {"claude_max_columns"}
    assert restart == set()
    assert load_config(path).claude_max_columns == 200

    # 0 is the opt-out, and it has to survive as a stored value rather than being
    # treated as "unset" - a browser reads a missing key as an older daemon and
    # restores the default cap.
    hot, _ = update_config(config, {"claude_max_columns": 0})
    assert hot == {"claude_max_columns"}
    assert load_config(path).claude_max_columns == 0

    # A cap between 1 and the smallest step would render a Claude pane unusably
    # narrow; `True` is the one that matters, since `bool` is an `int` subclass and
    # would otherwise pass as a 1-column cap.
    for bad in (1, 79, 130, -1, 5000, True, "120", 120.0):
        with pytest.raises(ValueError, match="claude_max_columns"):
            update_config(config, {"claude_max_columns": bad})
    assert load_config(path).claude_max_columns == 0


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


def test_worktree_root_defaults_below_data_dir_and_accepts_an_absolute_override(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.public_dict()["worktree_root"] == str((tmp_path / "worktrees").resolve())

    custom = (tmp_path / "agent-checkouts").resolve()
    hot, restart = update_config(config, {"worktree_root": str(custom)})
    assert hot == {"worktree_root"}
    assert restart == set()
    assert load_config(path).resolved_worktree_root == custom

    update_config(config, {"worktree_root": ""})
    assert config.resolved_worktree_root == (tmp_path / "worktrees").resolve()

    with pytest.raises(ValueError, match="absolute directory"):
        update_config(config, {"worktree_root": "relative/worktrees"})


def test_git_swe_mux_prompt_settings_persist_and_validate(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.git_swe_mux_prompt_enabled is True
    assert config.git_swe_mux_prompt_decisions == {}
    hot, restart = update_config(
        config,
        {
            "git_swe_mux_prompt_enabled": False,
            "git_swe_mux_prompt_decisions": {"project-1": "keep_visible"},
        },
    )
    assert hot == {"git_swe_mux_prompt_enabled", "git_swe_mux_prompt_decisions"}
    assert restart == set()
    loaded = load_config(path)
    assert loaded.git_swe_mux_prompt_enabled is False
    assert loaded.git_swe_mux_prompt_decisions == {"project-1": "keep_visible"}

    with pytest.raises(ValueError):
        update_config(config, {"git_swe_mux_prompt_decisions": {"project-1": "maybe"}})


def test_new_project_parent_is_hot_shape_validated_and_defaults_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.new_project_parent == ""

    parent = (tmp_path / "projects-home").resolve()
    hot, restart = update_config(config, {"new_project_parent": str(parent)})
    assert hot == {"new_project_parent"}
    assert restart == set()
    assert load_config(path).new_project_parent == str(parent)

    # Empty disables assistant project creation; it is always a valid value.
    update_config(config, {"new_project_parent": ""})
    assert config.new_project_parent == ""

    with pytest.raises(ValueError, match="absolute directory"):
        update_config(config, {"new_project_parent": "relative/projects"})
    with pytest.raises(ValueError, match="filesystem root"):
        update_config(config, {"new_project_parent": str(Path(tmp_path.anchor))})
    # Shape only, deliberately: existence is checked at use time so a directory
    # deleted while the daemon is down cannot stop the config from loading.
    update_config(config, {"new_project_parent": str(tmp_path / "not-created-yet")})


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
        "two_finger_swipe_up": "notes.open",
        "two_finger_swipe_down": "terminal.keyboardToggle",
        "two_finger_tap": "palette.open",
        # Region-scoped: recognized only for a touch that began on the command rail,
        # which is why a *single*-finger vertical is a real slot here.
        "rail_swipe_up": "menu.toggle",
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

    hot, _ = update_config(
        config, {"mobile_gestures": {"two_finger_tap": "voice.toggleTalk"}}
    )
    assert hot == {"mobile_gestures"}
    assert config.mobile_gestures == {"two_finger_tap": "voice.toggleTalk"}

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


def test_surface_gestures_default_on_and_are_hot_reloadable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.mobile_surface_gestures is True

    hot, restart = update_config(config, {"mobile_surface_gestures": False})
    assert hot == {"mobile_surface_gestures"}
    assert restart == set()
    assert config.mobile_surface_gestures is False

    reloaded = load_config(path)
    assert reloaded.mobile_surface_gestures is False


def test_overlay_back_defaults_on_and_is_hot_reloadable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.mobile_gesture_overlay_back is True

    hot, restart = update_config(config, {"mobile_gesture_overlay_back": False})
    assert hot == {"mobile_gesture_overlay_back"}
    assert restart == set()
    assert config.mobile_gesture_overlay_back is False

    reloaded = load_config(path)
    assert reloaded.mobile_gesture_overlay_back is False


def test_a_config_predating_overlay_back_keeps_the_default_without_a_migration(
    tmp_path: Path,
) -> None:
    # The field was added without a schema bump because there is nothing to migrate:
    # an absent key falls through to the dataclass default.
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 21\n", encoding="utf-8")
    assert load_config(path).mobile_gesture_overlay_back is True


def test_view_history_back_defaults_on_and_is_hot_reloadable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert config.mobile_back_view_history is True

    hot, restart = update_config(config, {"mobile_back_view_history": False})
    assert hot == {"mobile_back_view_history"}
    assert restart == set()
    assert config.mobile_back_view_history is False

    reloaded = load_config(path)
    assert reloaded.mobile_back_view_history is False


def test_a_config_predating_view_history_back_keeps_the_default_without_a_migration(
    tmp_path: Path,
) -> None:
    # Same reason as the overlay-back field above: an absent key falls through to the
    # dataclass default, so there is nothing for a schema bump to migrate.
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 21\n", encoding="utf-8")
    assert load_config(path).mobile_back_view_history is True


def test_the_back_command_can_be_bound_to_a_gesture_slot(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    hot, _ = update_config(config, {"mobile_gestures": {"swipe_right": "nav.back"}})
    assert hot == {"mobile_gestures"}
    assert config.mobile_gestures == {"swipe_right": "nav.back"}


def test_legacy_note_gestures_migrate_to_project_notes(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '\n'.join(
            [
                'schema_version = 17',
                '[mobile_gestures]',
                'two_finger_swipe_up = "session.note"',
                'two_finger_tap = "project.note"',
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.mobile_gestures["two_finger_swipe_up"] == "notes.open"
    assert config.mobile_gestures["two_finger_tap"] == "notes.open"


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


def test_drawer_tab_display_is_hot_reloadable_and_validated(tmp_path: Path) -> None:
    config = load_config(tmp_path / "config.toml")
    assert config.drawer_tab_display == "icon"
    assert config.utility_rail_display == "icon"

    hot, restart = update_config(
        config,
        {"drawer_tab_display": "title", "utility_rail_display": "title"},
    )
    assert hot == {"drawer_tab_display", "utility_rail_display"}
    assert restart == set()
    assert load_config(tmp_path / "config.toml").drawer_tab_display == "title"
    assert load_config(tmp_path / "config.toml").utility_rail_display == "title"

    with pytest.raises(ValueError, match="drawer_tab_display"):
        update_config(config, {"drawer_tab_display": "both"})
    assert config.drawer_tab_display == "title"
    assert load_config(tmp_path / "config.toml").drawer_tab_display == "title"

    with pytest.raises(ValueError, match="utility_rail_display"):
        update_config(config, {"utility_rail_display": "both"})
    assert config.utility_rail_display == "title"
    assert load_config(tmp_path / "config.toml").utility_rail_display == "title"


def test_builtin_themes_and_custom_text_meet_readability_contract(tmp_path: Path) -> None:
    assert all(contrast_ratio(*pair) >= 4.5 for pair in BUILTIN_THEME_PAIRS.values())
    config = load_config(tmp_path / "config.toml")
    before = (tmp_path / "config.toml").read_bytes()
    unreadable = {**config.custom_theme, "foreground": "#0a0a0a"}
    with pytest.raises(ValueError, match="4.5:1"):
        update_config(config, {"theme": "custom", "custom_theme": unreadable})
    assert (tmp_path / "config.toml").read_bytes() == before
