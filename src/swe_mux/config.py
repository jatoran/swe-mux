from __future__ import annotations

import json
import os
import secrets
import shutil
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
THEMES = {"light", "dark", "system", "solarized-dark", "tokyo-night", "custom"}
CUSTOM_THEME_KEYS = {"background", "panel", "line", "foreground", "muted", "accent", "error"}
RESTART_FIELDS = {"host", "port", "data_dir", "reconcile_external_history"}
BUILTIN_THEME_PAIRS = {
    "dark": ("#090a0c", "#d9dde2"),
    "light": ("#f5f2e9", "#252821"),
    "solarized-dark": ("#002b36", "#93a1a1"),
    "tokyo-night": ("#1a1b26", "#c0caf5"),
}


def contrast_ratio(first: str, second: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
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
    enabled: bool = True


@dataclass(slots=True)
class Config:
    schema_version: int = SCHEMA_VERSION
    revision: int = 1
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""
    loopback_auth: bool = False
    default_backend: str = "shell"
    shell_exe: str = "powershell.exe"
    claude_exe: str = "claude.exe"
    codex_exe: str = "codex.exe"
    claude_args: list[str] = field(default_factory=list)
    codex_args: list[str] = field(default_factory=list)
    scrollback_bytes: int = 5 * 1024 * 1024
    git_poll_seconds: float = 5.0
    reconcile_external_history: bool = True
    startup_cwd: str = ""
    history_limit: int = 200
    theme: str = "dark"
    custom_theme: dict[str, str] = field(
        default_factory=lambda: {
            "background": "#090a0c", "panel": "#0d0f12", "line": "#2a2e34",
            "foreground": "#d9dde2", "muted": "#848b94", "accent": "#8bd450",
            "error": "#f07178",
        }
    )
    middle_click_paste: bool = True
    broadcast_default: bool = False
    ccusage_enabled: bool = False
    ccusage_refresh_minutes: int = 0
    ccusage_claude_command: list[str] = field(
        default_factory=lambda: [
            "npx", "--no-install", "ccusage@17.1.5", "daily", "--json"
        ]
    )
    ccusage_codex_command: list[str] = field(
        default_factory=lambda: [
            "npx", "--no-install", "@ccusage/codex@0.2.7", "daily", "--json"
        ]
    )
    default_shell_profile: str = "default"
    shell_profiles: list[ShellProfile] = field(default_factory=list)
    pinned_directories: list[str] = field(default_factory=list)
    data_dir: Path = Path.home() / ".mux"
    config_path: Path | None = field(default=None, repr=False)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mux.db"

    @property
    def requires_auth(self) -> bool:
        return self.loopback_auth or self.host not in {"127.0.0.1", "localhost", "::1"}

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["data_dir"] = str(self.data_dir)
        result.pop("token", None)
        result.pop("config_path", None)
        result["requires_auth"] = self.requires_auth
        # The daemon retains exact bytes. Browsers retain an approximate line
        # window using a documented 160-byte average, bounded for xterm.
        result["xterm_scrollback_lines"] = max(
            1_000, min(100_000, self.scrollback_bytes // 160)
        )
        return result


def _validate(config: Config) -> None:
    errors: dict[str, str] = {}
    if not 1 <= config.port <= 65535:
        errors["port"] = "must be between 1 and 65535"
    if config.default_backend not in {"shell", "claude", "codex"}:
        errors["default_backend"] = "must be shell, claude, or codex"
    for field_name in (
        "claude_args",
        "codex_args",
        "ccusage_claude_command",
        "ccusage_codex_command",
    ):
        if not all(isinstance(item, str) for item in getattr(config, field_name)):
            errors[field_name] = "must be an array of strings"
    for field_name in ("ccusage_claude_command", "ccusage_codex_command"):
        if config.ccusage_enabled and not getattr(config, field_name):
            errors[field_name] = "must not be empty while usage analytics is enabled"
    if not 0 <= config.ccusage_refresh_minutes <= 24 * 60:
        errors["ccusage_refresh_minutes"] = "must be between 0 and 1440 minutes"
    if not 1024 <= config.scrollback_bytes <= 1024 * 1024 * 1024:
        errors["scrollback_bytes"] = "must be between 1 KiB and 1 GiB"
    if not 0.25 <= config.git_poll_seconds <= 3600:
        errors["git_poll_seconds"] = "must be between 0.25 and 3600 seconds"
    if not 1 <= config.history_limit <= 10000:
        errors["history_limit"] = "must be between 1 and 10000"
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
    elif contrast_ratio(
        config.custom_theme["background"], config.custom_theme["foreground"]
    ) < 4.5:
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
            isinstance(key, str) and isinstance(value, str)
            for key, value in profile.env.items()
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
        return "{ " + ", ".join(
            f"{key} = {_toml_value(item)}" for key, item in value.items()
        ) + " }"
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


def load_config(path: Path | None = None) -> Config:
    path = path or Path.home() / ".mux" / "config.toml"
    cfg = Config(data_dir=path.parent, config_path=path)
    migrated = False
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        migrated = int(raw.get("schema_version", 0)) < SCHEMA_VERSION
        for key in Config.__dataclass_fields__:
            if key in {"config_path", "shell_profiles"}:
                continue
            if key in raw:
                setattr(cfg, key, Path(raw[key]) if key == "data_dir" else raw[key])
        cfg.shell_profiles = [ShellProfile(**item) for item in raw.get("shell_profiles", [])]
    if not cfg.shell_profiles:
        args = ["-NoLogo"] if Path(cfg.shell_exe).name.casefold() in {
            "powershell", "powershell.exe", "pwsh", "pwsh.exe"
        } else []
        cfg.shell_profiles = [
            ShellProfile("default", "Default shell", cfg.shell_exe, args, marker="ps")
        ]
    if not cfg.token:
        cfg.token = secrets.token_urlsafe(32)
        migrated = True
    cfg.schema_version = SCHEMA_VERSION
    _validate(cfg)
    if migrated or not path.exists():
        save_config(cfg, backup=path.exists())
    return cfg


def update_config(config: Config, changes: dict[str, Any]) -> tuple[set[str], set[str]]:
    allowed = set(Config.__dataclass_fields__) - {
        "schema_version", "revision", "token", "data_dir", "config_path"
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
