from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 13
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
THEMES = {"light", "dark", "system", "solarized-dark", "tokyo-night", "custom"}
CUSTOM_THEME_KEYS = {"background", "panel", "line", "foreground", "muted", "accent", "error"}
RESTART_FIELDS = {
    "host",
    "port",
    "data_dir",
    "reconcile_external_history",
    "tailnet_enabled",
    "automation_concurrency",
    "automation_queue_size",
    "openrouter_request_timeout_seconds",
}
BUILTIN_THEME_PAIRS = {
    "dark": ("#090a0c", "#d9dde2"),
    "light": ("#f5f2e9", "#252821"),
    "solarized-dark": ("#002b36", "#93a1a1"),
    "tokyo-night": ("#1a1b26", "#c0caf5"),
}
CCUSAGE_PACKAGE = "ccusage@latest"
DEFAULT_PROJECT_IGNORE_PATTERNS = [
    ".git",
    ".swe-mux",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    "dist",
    "build",
    "coverage",
    "*.pyc",
    "*.pyo",
    "*.code-workspace",
    "uv.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
]


def default_ccusage_command(provider: str) -> list[str]:
    return ["ccusage", provider, "daily", "--json"]


_LEGACY_CCUSAGE_COMMANDS = {
    "claude": ["--no-install", "ccusage@17.1.5", "daily", "--json"],
    "codex": ["--no-install", "@ccusage/codex@0.2.7", "daily", "--json"],
}


def contrast_ratio(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@dataclass(slots=True)
class ShellProfile:
    id: str
    label: str
    executable: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=lambda: ["windows"])
    cwd_strategy: str = "native"
    marker: str = "sh"
    capabilities: list[str] = field(default_factory=lambda: ["interactive", "agent-aware"])
    cwd_integration: bool = False
    enabled: bool = True


@dataclass(slots=True)
class Config:
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    host: str = "127.0.0.1"
    port: int = 8765
    tailnet_enabled: bool = True
    default_backend: str = "shell"
    shell_exe: str = "powershell.exe"
    claude_exe: str = "claude.exe"
    codex_exe: str = "codex.exe"
    claude_args: list[str] = field(default_factory=list)
    codex_args: list[str] = field(default_factory=list)
    scrollback_bytes: int = 5 * 1024 * 1024
    git_poll_seconds: float = 5.0
    process_poll_seconds: float = 5.0
    process_orphan_grace_seconds: float = 15.0
    process_evidence_retention_days: int = 30
    operational_telemetry_retention_days: int = 180
    provider_quota_poll_minutes: int = 15
    provider_quota_turn_refresh_enabled: bool = False
    provider_quota_turn_refresh_min_minutes: int = 5
    reconcile_external_history: bool = True
    startup_cwd: str = ""
    history_limit: int = 200
    theme: str = "dark"
    custom_theme: dict[str, str] = field(
        default_factory=lambda: {
            "background": "#090a0c",
            "panel": "#0d0f12",
            "line": "#2a2e34",
            "foreground": "#d9dde2",
            "muted": "#848b94",
            "accent": "#8bd450",
            "error": "#f07178",
        }
    )
    middle_click_paste: bool = True
    broadcast_default: bool = False
    mobile_vertical_drag: str = "smart"
    mobile_scroll_direction: str = "natural"
    mobile_scroll_sensitivity: float = 1.0
    mobile_long_press: str = "context_menu"
    notes_default_open: str = "dock"
    ccusage_enabled: bool = False
    ccusage_refresh_minutes: int = 0
    ccusage_claude_command: list[str] = field(
        default_factory=lambda: default_ccusage_command("claude")
    )
    ccusage_codex_command: list[str] = field(
        default_factory=lambda: default_ccusage_command("codex")
    )
    default_shell_profile: str = "default"
    shell_profiles: list[ShellProfile] = field(default_factory=list)
    pinned_directories: list[str] = field(default_factory=list)
    project_ignore_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROJECT_IGNORE_PATTERNS)
    )
    automation_enabled: bool = False
    automation_retention_days: int = 90
    automation_concurrency: int = 2
    automation_queue_size: int = 256
    automation_max_input_tokens: int = 4096
    automation_max_output_tokens: int = 256
    automation_daily_token_budget: int = 200_000
    automation_daily_budget_usd: float = 2.0
    automation_rule_daily_token_budget: int = 50_000
    automation_rule_daily_budget_usd: float = 0.5
    automation_hourly_call_cap: int = 60
    automation_rule_hourly_call_cap: int = 20
    openrouter_cheap_model: str = ""
    openrouter_standard_model: str = ""
    openrouter_request_timeout_seconds: float = 30.0
    observer_titler_enabled: bool = False
    observer_summarizer_enabled: bool = False
    phase7_observers_enabled: bool = False
    tts_enabled: bool = False
    tts_default_mode: str = "on_demand"
    tts_content: str = "summary"
    tts_engine: str = "edge"
    tts_edge_voice: str = "en-AU-NatashaNeural"
    tts_edge_rate: str = "+10%"
    tts_edge_pitch: str = "+0Hz"
    tts_soften_stops: bool = True
    tts_sapi_voice: str = ""
    tts_sapi_rate: int = 0
    tts_summary_model: str = ""
    tts_summary_max_tokens: int = 500
    tts_verbatim_max_chars: int = 6000
    tts_daily_budget_usd: float = 1.0
    tts_cache_mb: int = 200
    stt_enabled: bool = True
    data_dir: Path = Path.home() / ".mux"
    config_path: Path | None = field(default=None, repr=False)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mux.db"

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["data_dir"] = str(self.data_dir)
        result.pop("config_path", None)
        # Legacy input is still accepted so existing config files start cleanly,
        # but layout v6 has no dock/pop-out presentation preference.
        result.pop("notes_default_open", None)
        result["access_mode"] = "local+tailnet" if self.tailnet_enabled else "loopback"
        result["requires_auth"] = False
        # The daemon retains exact bytes. Browsers retain an approximate line
        # window using a documented 160-byte average, bounded for xterm.
        result["xterm_scrollback_lines"] = max(1_000, min(100_000, self.scrollback_bytes // 160))
        return result


def _validate(config: Config) -> None:
    errors: dict[str, str] = {}
    if config.host not in LOOPBACK_HOSTS:
        errors["host"] = (
            "must be a loopback address (127.0.0.1, localhost, or ::1); "
            "direct tailnet listening uses the detected Tailscale address automatically"
        )
    if not 1 <= config.port <= 65535:
        errors["port"] = "must be between 1 and 65535"
    if config.default_backend not in {"shell", "claude", "codex"}:
        errors["default_backend"] = "must be shell, claude, or codex"
    if config.notes_default_open not in {"dock", "popout"}:
        errors["notes_default_open"] = "must be dock or popout"
    if config.mobile_vertical_drag not in {"smart", "terminal", "application", "disabled"}:
        errors["mobile_vertical_drag"] = "must be smart, terminal, application, or disabled"
    if config.mobile_scroll_direction not in {"natural", "wheel"}:
        errors["mobile_scroll_direction"] = "must be natural or wheel"
    if not 0.25 <= config.mobile_scroll_sensitivity <= 4:
        errors["mobile_scroll_sensitivity"] = "must be between 0.25 and 4"
    if config.mobile_long_press not in {"context_menu", "disabled"}:
        errors["mobile_long_press"] = "must be context_menu or disabled"
    for field_name in (
        "claude_args",
        "codex_args",
        "ccusage_claude_command",
        "ccusage_codex_command",
        "project_ignore_patterns",
    ):
        value = getattr(config, field_name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors[field_name] = "must be an array of strings"
    if isinstance(config.project_ignore_patterns, list) and (
        len(config.project_ignore_patterns) > 256
        or any(
            not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 200
            for pattern in config.project_ignore_patterns
        )
    ):
        errors["project_ignore_patterns"] = (
            "must contain at most 256 non-empty patterns of 200 characters or fewer"
        )
    for field_name in ("ccusage_claude_command", "ccusage_codex_command"):
        if config.ccusage_enabled and not getattr(config, field_name):
            errors[field_name] = "must not be empty while usage analytics is enabled"
    if not 0 <= config.ccusage_refresh_minutes <= 24 * 60:
        errors["ccusage_refresh_minutes"] = "must be between 0 and 1440 minutes"
    if not 1024 <= config.scrollback_bytes <= 1024 * 1024 * 1024:
        errors["scrollback_bytes"] = "must be between 1 KiB and 1 GiB"
    if not 0.25 <= config.git_poll_seconds <= 3600:
        errors["git_poll_seconds"] = "must be between 0.25 and 3600 seconds"
    if not 0.5 <= config.process_poll_seconds <= 60:
        errors["process_poll_seconds"] = "must be between 0.5 and 60 seconds"
    if not 1 <= config.process_orphan_grace_seconds <= 3600:
        errors["process_orphan_grace_seconds"] = "must be between 1 and 3600 seconds"
    if not 1 <= config.process_evidence_retention_days <= 3650:
        errors["process_evidence_retention_days"] = "must be between 1 and 3650"
    if not 1 <= config.operational_telemetry_retention_days <= 3650:
        errors["operational_telemetry_retention_days"] = "must be between 1 and 3650"
    if not 5 <= config.provider_quota_poll_minutes <= 1440:
        errors["provider_quota_poll_minutes"] = "must be between 5 and 1440"
    if not 1 <= config.provider_quota_turn_refresh_min_minutes <= 1440:
        errors["provider_quota_turn_refresh_min_minutes"] = "must be between 1 and 1440"
    if not 1 <= config.history_limit <= 10000:
        errors["history_limit"] = "must be between 1 and 10000"
    if not 1 <= config.automation_retention_days <= 3650:
        errors["automation_retention_days"] = "must be between 1 and 3650"
    if not 1 <= config.automation_concurrency <= 16:
        errors["automation_concurrency"] = "must be between 1 and 16"
    if not 16 <= config.automation_queue_size <= 4096:
        errors["automation_queue_size"] = "must be between 16 and 4096"
    if not 128 <= config.automation_max_input_tokens <= 128_000:
        errors["automation_max_input_tokens"] = "must be between 128 and 128000"
    if not 16 <= config.automation_max_output_tokens <= 8192:
        errors["automation_max_output_tokens"] = "must be between 16 and 8192"
    if not 0 <= config.automation_daily_token_budget <= 100_000_000:
        errors["automation_daily_token_budget"] = "must be between 0 and 100000000"
    if not 0 <= config.automation_daily_budget_usd <= 10_000:
        errors["automation_daily_budget_usd"] = "must be between 0 and 10000"
    if not 0 <= config.automation_rule_daily_token_budget <= 100_000_000:
        errors["automation_rule_daily_token_budget"] = "must be between 0 and 100000000"
    if not 0 <= config.automation_rule_daily_budget_usd <= 10_000:
        errors["automation_rule_daily_budget_usd"] = "must be between 0 and 10000"
    if not 1 <= config.automation_hourly_call_cap <= 10_000:
        errors["automation_hourly_call_cap"] = "must be between 1 and 10000"
    if not 1 <= config.automation_rule_hourly_call_cap <= 10_000:
        errors["automation_rule_hourly_call_cap"] = "must be between 1 and 10000"
    if not 1 <= config.openrouter_request_timeout_seconds <= 120:
        errors["openrouter_request_timeout_seconds"] = "must be between 1 and 120"
    if config.tts_default_mode not in {"off", "on_demand", "auto"}:
        errors["tts_default_mode"] = "must be off, on_demand, or auto"
    if config.tts_content not in {"summary", "verbatim"}:
        errors["tts_content"] = "must be summary or verbatim"
    if config.tts_engine not in {"edge", "sapi"}:
        errors["tts_engine"] = "must be edge or sapi"
    if not config.tts_edge_voice.strip():
        errors["tts_edge_voice"] = "must name an edge-tts voice, e.g. en-AU-NatashaNeural"
    if not re.fullmatch(r"[+-]\d{1,3}%", config.tts_edge_rate):
        errors["tts_edge_rate"] = "must look like +10% or -5%"
    if not re.fullmatch(r"[+-]\d{1,3}Hz", config.tts_edge_pitch):
        errors["tts_edge_pitch"] = "must look like +0Hz or -20Hz"
    if not -10 <= config.tts_sapi_rate <= 10:
        errors["tts_sapi_rate"] = "must be between -10 and 10"
    if not 64 <= config.tts_summary_max_tokens <= 2000:
        errors["tts_summary_max_tokens"] = "must be between 64 and 2000"
    if not 200 <= config.tts_verbatim_max_chars <= 40_000:
        errors["tts_verbatim_max_chars"] = "must be between 200 and 40000"
    if not 0 <= config.tts_daily_budget_usd <= 100:
        errors["tts_daily_budget_usd"] = "must be between 0 and 100"
    if not 10 <= config.tts_cache_mb <= 5000:
        errors["tts_cache_mb"] = "must be between 10 and 5000"
    if config.theme not in THEMES:
        errors["theme"] = f"must be one of {', '.join(sorted(THEMES))}"
    if set(config.custom_theme) != CUSTOM_THEME_KEYS or any(
        not isinstance(value, str)
        or len(value) != 7
        or not value.startswith("#")
        or any(character not in "0123456789abcdefABCDEF" for character in value[1:])
        for value in config.custom_theme.values()
    ):
        errors["custom_theme"] = "must contain every semantic token as a #RRGGBB color"
    elif contrast_ratio(config.custom_theme["background"], config.custom_theme["foreground"]) < 4.5:
        errors["custom_theme"] = "background and foreground require at least 4.5:1 contrast"
    ids = [profile.id for profile in config.shell_profiles]
    if len(ids) != len(set(ids)) or any(not value.strip() for value in ids):
        errors["shell_profiles"] = "profile ids must be non-empty and unique"
    if config.shell_profiles and config.default_shell_profile not in ids:
        errors["default_shell_profile"] = "must reference an existing shell profile"
    for index, profile in enumerate(config.shell_profiles):
        prefix = f"shell_profiles.{index}"
        if not profile.label.strip() or not profile.executable.strip():
            errors[prefix] = "label and executable are required"
        if profile.cwd_strategy not in {"native", "home", "wsl"}:
            errors[f"{prefix}.cwd_strategy"] = "must be native, home, or wsl"
        if not all(isinstance(item, str) for item in profile.args):
            errors[f"{prefix}.args"] = "must be an array of strings"
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in profile.env.items()
        ):
            errors[f"{prefix}.env"] = "must be a string map"
    if errors:
        raise ValueError(errors)


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{key} = {_toml_value(item)}" for key, item in value.items()) + " }"
        )
    raise TypeError(f"cannot encode {type(value).__name__}")


def _serialize(config: Config) -> str:
    values = asdict(config)
    values.pop("config_path", None)
    values["data_dir"] = str(config.data_dir)
    profiles = values.pop("shell_profiles")
    lines = ["# swe-mux configuration (canonical, schema versioned)"]
    for key, value in values.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for profile in profiles:
        lines.append("")
        lines.append("[[shell_profiles]]")
        for key, value in profile.items():
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def save_config(config: Config, *, backup: bool = False) -> None:
    path = config.config_path or config.data_dir / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate(config)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_serialize(config), encoding="utf-8")
    os.replace(temporary, path)
    config.config_path = path


def _migrate_legacy_ccusage_commands(config: Config) -> bool:
    changed = False
    for provider, legacy_tail in _LEGACY_CCUSAGE_COMMANDS.items():
        field_name = f"ccusage_{provider}_command"
        command = getattr(config, field_name)
        executable = Path(command[0]).stem.casefold() if command else ""
        if executable == "npx" and command[1:] == legacy_tail:
            setattr(config, field_name, default_ccusage_command(provider))
            changed = True
    return changed


def _default_shell_profile(executable: str) -> ShellProfile:
    executable_name = Path(executable).name.casefold()
    args = (
        ["-NoLogo"]
        if executable_name in {"powershell", "powershell.exe", "pwsh", "pwsh.exe"}
        else []
    )
    if executable_name in {"pwsh", "pwsh.exe"}:
        return ShellProfile("default", "PowerShell 7", executable, args, marker="ps7")
    if executable_name in {"powershell", "powershell.exe"}:
        return ShellProfile("default", "Windows PowerShell", executable, args, marker="ps")
    return ShellProfile("default", "Default shell", executable, args)


def _is_auto_managed_windows_powershell_default(config: Config) -> bool:
    if config.default_shell_profile != "default" or len(config.shell_profiles) != 1:
        return False
    profile = config.shell_profiles[0]
    return (
        Path(config.shell_exe).name.casefold() in {"powershell", "powershell.exe"}
        and profile.id == "default"
        and profile.label in {"Default shell", "Windows PowerShell"}
        and Path(profile.executable).name.casefold() in {"powershell", "powershell.exe"}
        and profile.args == ["-NoLogo"]
        and profile.env == {}
        and profile.platforms == ["windows"]
        and profile.cwd_strategy == "native"
        and profile.marker == "ps"
        and profile.capabilities == ["interactive", "agent-aware"]
        and not profile.cwd_integration
        and profile.enabled
    )


def load_config(path: Path | None = None) -> Config:
    path = path or Path.home() / ".mux" / "config.toml"
    cfg = Config(data_dir=path.parent, config_path=path)
    migrated = False
    raw: dict[str, Any] = {}
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        source_schema = int(raw.get("schema_version", 0))
        migrated = source_schema < SCHEMA_VERSION
        for key in Config.__dataclass_fields__:
            if key in {"config_path", "shell_profiles"}:
                continue
            if key in raw:
                setattr(cfg, key, Path(raw[key]) if key == "data_dir" else raw[key])
        cfg.shell_profiles = [ShellProfile(**item) for item in raw.get("shell_profiles", [])]
    if not cfg.shell_profiles:
        if "shell_exe" not in raw and shutil.which("pwsh.exe"):
            cfg.shell_exe = "pwsh.exe"
        cfg.shell_profiles = [_default_shell_profile(cfg.shell_exe)]
    elif _is_auto_managed_windows_powershell_default(cfg) and shutil.which("pwsh.exe"):
        cfg.shell_exe = "pwsh.exe"
        cfg.shell_profiles = [_default_shell_profile(cfg.shell_exe)]
        migrated = True
    migrated = _migrate_legacy_ccusage_commands(cfg) or migrated
    cfg.schema_version = SCHEMA_VERSION
    _validate(cfg)
    if migrated or not path.exists():
        save_config(cfg, backup=path.exists())
    return cfg


def update_config(config: Config, changes: dict[str, Any]) -> tuple[set[str], set[str]]:
    allowed = set(Config.__dataclass_fields__) - {
        "schema_version",
        "revision",
        "data_dir",
        "config_path",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError({key: "unknown or read-only setting" for key in sorted(unknown)})
    candidate = Config(**{**asdict(config), "config_path": config.config_path})
    candidate.data_dir = config.data_dir
    candidate.shell_profiles = list(config.shell_profiles)
    changed: set[str] = set()
    for key, value in changes.items():
        if key == "shell_profiles":
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError({"shell_profiles": "must be an array of profile objects"})
            value = [ShellProfile(**item) for item in value]
        if getattr(candidate, key) != value:
            setattr(candidate, key, value)
            changed.add(key)
    candidate.revision = config.revision + 1
    _validate(candidate)
    save_config(candidate)
    for field_name in Config.__dataclass_fields__:
        setattr(config, field_name, getattr(candidate, field_name))
    return changed - RESTART_FIELDS, changed & RESTART_FIELDS
