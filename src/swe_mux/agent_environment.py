"""Bounded, passive inventory of one running agent CLI environment.

The inventory describes current configuration and the launch options retained by
swe-mux.  It never starts an MCP server, loads plugin code, runs a hook, or claims
that a file visible now was loaded by an already-running CLI.

Drift is reported by *content*, against a baseline captured when the CLI
generation loaded (`capture_config_baseline`), not by comparing an mtime with the
load time.  An mtime comparison reported drift that was not there and did so
almost always: `~/.claude.json` is Claude's own state file and is rewritten
continuously with bookkeeping the inventory never reads, swe-mux rewrites its
`--mcp-config` and hook-settings files with identical bytes on every daemon
start, and Codex persists trust decisions into `~/.codex/config.toml`.  Every
live session therefore reported "changed since load" within seconds of starting,
which is the same as reporting nothing at all.  The baseline digests what each
source actually *contributes* to this inventory, so a rewrite that changes no
reported value is silent and a real edit is not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import time
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, assert_never
from urllib.parse import urlsplit, urlunsplit

from .adapters.claude import claude_data_home
from .adapters.codex import codex_data_home
from .agent_skills import discover_skills, parse_frontmatter
from .harness import Backend, descriptor, is_agent_harness, require_backend
from .path_identity import same_path
from .subprocess_flags import background_creation_flags

Scope = Literal["built_in", "managed", "user", "project", "local", "session", "unknown"]

SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 1024 * 1024
MAX_ITEMS_PER_SECTION = 256
CACHE_SECONDS = 10.0
VERSION_CACHE_SECONDS = 3600.0

_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_VERSION_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}
_CACHE_LOCK = threading.Lock()
_SAFE_KEY = re.compile(r"[A-Za-z0-9_.-]{1,160}\Z")

# Hook display. Both CLIs key hooks by lifecycle event, so the event is the group
# heading and the row identifies the handler by the script or module it runs.
_HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "PreCompact",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "StopFailure",
    "TeammateIdle",
    "SessionEnd",
)
_HOOK_EVENT_RANK = {event: index for index, event in enumerate(_HOOK_EVENTS)}
# Every swe-mux lifecycle hook runs this module, in a source checkout and inside
# the frozen desktop bundle alike (`desktop.py` re-dispatches `-m` itself).
_MUX_HOOK_MARKER = "swe_mux.hook_client"
_SCRIPT_SUFFIXES = frozenset(
    {
        ".bash", ".bat", ".cjs", ".cmd", ".com", ".exe", ".fish", ".jar", ".js", ".lua",
        ".mjs", ".pl", ".php", ".ps1", ".psm1", ".py", ".pyw", ".rb", ".sh", ".ts", ".zsh",
    }
)
_CREDENTIAL_WORD = re.compile(r"(?i)\b(secret|token|password|passwd|bearer|api[_-]?key)\b")
_MODULE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
# A command starting with one of these is a shell snippet, not a program: reporting
# `if` as the hook's program is worse than saying nothing.
_SHELL_KEYWORDS = frozenset(
    {".", "[", "[[", "case", "do", "done", "eval", "exec", "fi", "for", "if",
     "source", "test", "then", "until", "while"}
)


@dataclass(slots=True)
class ConfigSource:
    #: `changed_after_start` starts False and is decided only by
    #: `_apply_baseline`, against the load-time content digest. A scan run
    #: without a baseline leaves every source unmarked rather than guessing.
    source_id: str
    label: str
    scope: Scope
    format: str
    data: dict[str, Any]
    status: str
    mtime: float | None
    changed_after_start: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.source_id,
            "label": self.label,
            "scope": self.scope,
            "format": self.format,
            "status": self.status,
            "mtime": self.mtime,
            "changed_after_start": self.changed_after_start,
        }


def _opaque(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{prefix}:{digest}"


def _bounded_text(path: Path) -> tuple[str | None, str, float | None]:
    try:
        if path.is_symlink():
            return None, "symlink_refused", None
        if not path.exists():
            return None, "missing", None
        if not path.is_file():
            return None, "not_a_file", None
        stat = path.stat()
        if stat.st_size > MAX_CONFIG_BYTES:
            return None, "too_large", stat.st_mtime
        return path.read_text(encoding="utf-8"), "ready", stat.st_mtime
    except (OSError, UnicodeError):
        return None, "unreadable", None


def _load_source(
    path: Path,
    label: str,
    scope: Scope,
    format_name: Literal["json", "toml"],
) -> ConfigSource:
    text, status, mtime = _bounded_text(path)
    data: dict[str, Any] = {}
    if text is not None:
        try:
            parsed = json.loads(text) if format_name == "json" else tomllib.loads(text)
            if isinstance(parsed, dict):
                data = parsed
            else:
                status = "invalid_root"
        except (ValueError, tomllib.TOMLDecodeError):
            status = "invalid"
    return ConfigSource(
        _opaque("source", str(path.resolve(strict=False))),
        label,
        scope,
        format_name,
        data,
        status,
        mtime,
    )


def _virtual_source(
    label: str, scope: Scope, data: dict[str, Any], *, format_name: str = "cli"
) -> ConfigSource:
    return ConfigSource(
        _opaque("source", f"{scope}:{label}"),
        label,
        scope,
        format_name,
        data,
        "ready" if data else "empty",
        None,
    )


def _source_once(sources: list[ConfigSource], source: ConfigSource) -> None:
    if all(item.source_id != source.source_id for item in sources):
        sources.append(source)


def _value_after(args: list[str], *names: str) -> str | None:
    for index, value in enumerate(args):
        if value in names and index + 1 < len(args) and not args[index + 1].startswith("-"):
            return args[index + 1]
        for name in names:
            if value.startswith(f"{name}="):
                return value.split("=", 1)[1]
    return None


def _values_after(args: list[str], name: str, *, variadic: bool = False) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value == name:
            index += 1
            while index < len(args) and not args[index].startswith("-"):
                values.append(args[index])
                index += 1
                if not variadic:
                    break
            continue
        if value.startswith(f"{name}="):
            values.append(value.split("=", 1)[1])
        index += 1
    return values


def _set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    if not _SAFE_KEY.fullmatch(dotted):
        return
    cursor = target
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def _parse_toml_value(raw: str) -> Any:
    try:
        return tomllib.loads(f"value = {raw}")["value"]
    except (KeyError, tomllib.TOMLDecodeError):
        return raw[:300]


def _codex_cli_config(args: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    index = 0
    while index < len(args):
        value = args[index]
        if value in {"-c", "--config"} and index + 1 < len(args):
            assignment = args[index + 1]
            key, separator, raw = assignment.partition("=")
            if separator:
                _set_dotted(data, key.strip(), _parse_toml_value(raw.strip()))
            index += 2
            continue
        if value in {"--enable", "--disable"} and index + 1 < len(args):
            _set_dotted(data, f"features.{args[index + 1]}", value == "--enable")
            index += 2
            continue
        index += 1
    return data


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "enabled" if value else "disabled"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, list):
        return f"{len(value)} entries"
    if isinstance(value, dict):
        return f"{len(value)} entries"
    return "configured"


def _nested(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _item(
    kind: str,
    name: str,
    *,
    scope: Scope,
    origin: str,
    state: str = "configured",
    description: str = "",
    source: ConfigSource | None = None,
    meta: list[tuple[str, str]] | None = None,
    unique: str = "",
    group: str = "",
    owner: str = "",
) -> dict[str, Any]:
    """One normalized inventory row.

    `group` is an optional in-section heading the UI renders above a run of
    consecutive items (hooks use the lifecycle event), and `owner` names who
    installed the entry when that is knowable: `swe_mux` for the rows swe-mux
    provisions itself, so they are distinguishable from the user's own.
    """
    identity = f"{kind}:{name}:{origin}:{source.source_id if source else ''}:{unique}"
    return {
        "id": _opaque("item", identity),
        "kind": kind,
        "name": name[:160],
        "description": description[:500],
        "scope": scope,
        "origin": origin[:160],
        "state": state,
        "group": group[:80],
        "owner": owner[:40],
        "source_id": source.source_id if source else None,
        "source_label": source.label if source else None,
        "changed_after_start": source.changed_after_start if source else False,
        "meta": [{"label": key[:80], "value": value[:240]} for key, value in (meta or [])],
    }


def _item_material(item: dict[str, Any]) -> str:
    """One row reduced to the values this inventory actually reports.

    The drift baseline is built from these, so it tracks whatever the tab shows
    and cannot drift away from it the way an enumerated key allowlist would.
    `id` is omitted because it is a digest of these same fields, and
    `changed_after_start` because it is the answer being computed.
    """
    return json.dumps(
        [
            item["kind"],
            item["name"],
            item["scope"],
            item["origin"],
            item["state"],
            item["description"],
            item["group"],
            item["owner"],
            [[entry["label"], entry["value"]] for entry in item["meta"]],
        ],
        separators=(",", ":"),
        ensure_ascii=False,
        sort_keys=True,
    )


def _data_material(source: ConfigSource) -> str:
    """A whole parsed file reduced to one comparable string.

    For files that are read but never listed as their own configuration source
    (plugin manifests), where there is no derived row to digest instead.
    """
    try:
        body = json.dumps(source.data, separators=(",", ":"), sort_keys=True, default=str)
    except (TypeError, ValueError):
        body = repr(sorted(source.data))
    return f"{source.status}:{body}"


def _section(
    section_id: str,
    label: str,
    completeness: str,
    items: list[dict[str, Any]],
    note: str,
) -> dict[str, Any]:
    truncated = len(items) > MAX_ITEMS_PER_SECTION
    return {
        "id": section_id,
        "label": label,
        "completeness": completeness,
        "items": items[:MAX_ITEMS_PER_SECTION],
        "total": len(items),
        "truncated": truncated,
        "note": note,
    }


def safe_endpoint(raw: Any) -> str:
    """An MCP endpoint reduced to scheme, host, port, and path.

    Userinfo, query string, and fragment are dropped rather than trimmed, because
    every one of them is a place a bearer token is routinely parked. Public
    because the tool-catalog fetch (`mcp_tools.py`) must sanitize the same values
    by the same rule; a second implementation would eventually differ, and the
    direction it would differ in is "leaks a credential".
    """
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path[:160], "", ""))
    except ValueError:
        return ""


def _command_name(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        return ""
    try:
        first = shlex.split(raw, posix=os.name != "nt")[0]
    except ValueError:
        first = raw.strip().split(maxsplit=1)[0]
    return Path(first.strip('"')).name[:120]


def _runtime(backend: str, executable: str, args: list[str], model: str | None) -> dict[str, Any]:
    options: list[dict[str, str]] = []
    pairs = (
        [
            ("model", ("--model",), model),
            ("effort", ("--effort",), None),
            ("permission mode", ("--permission-mode",), None),
            ("main agent", ("--agent",), None),
            ("setting sources", ("--setting-sources",), None),
        ]
        if backend == "claude"
        else [
            ("model", ("--model", "-m"), model),
            ("profile", ("--profile", "-p"), None),
            ("sandbox", ("--sandbox", "-s"), None),
            ("approval policy", ("--ask-for-approval", "-a"), None),
            ("local provider", ("--oss-provider",), None),
        ]
    )
    for label, names, observed in pairs:
        value = observed or _value_after(args, *names)
        if value:
            options.append({"label": label, "value": str(value)[:160]})
    modes: list[str] = []
    flags = {
        "--safe-mode": "safe mode",
        "--bare": "bare mode",
        "--strict-mcp-config": "strict MCP config",
        "--disable-slash-commands": "skills disabled",
        "--dangerously-skip-permissions": "permissions bypassed",
        "--full-auto": "full auto",
        "--oss": "local model",
        "--dangerously-bypass-approvals-and-sandbox": "sandbox and approvals bypassed",
    }
    for flag, label in flags.items():
        if flag in args:
            modes.append(label)
    return {
        "executable": Path(executable).name or backend,
        "model": model,
        "options": options,
        "modes": modes,
    }


def probe_cli_version(backend: str, executable: str, *, refresh: bool = False) -> str | None:
    """The cached `--version` string for a harness binary.

    Public because the CLI's identity is part of an MCP tool catalog's cache key
    as well as of the inventory header: a CLI upgrade can change which servers a
    session has and what they publish, and a fingerprint that ignored it would
    keep serving the old catalog after the upgrade.
    """
    name = Path(executable).name.casefold()
    if backend not in name:
        return None
    key = (backend, executable)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _VERSION_CACHE.get(key)
    if cached and not refresh and now - cached[0] < VERSION_CACHE_SECONDS:
        return cached[1]
    version: str | None = None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
            check=False,
            creationflags=background_creation_flags(),
        )
        first = completed.stdout.strip().splitlines()[0][:120] if completed.stdout.strip() else ""
        if completed.returncode == 0 and first:
            version = first
    except (OSError, subprocess.SubprocessError):
        version = None
    with _CACHE_LOCK:
        _VERSION_CACHE[key] = (now, version)
    return version


def _config_sources(backend: Backend, cwd: Path, args: list[str]) -> list[ConfigSource]:
    sources: list[ConfigSource] = []
    if backend == "claude":
        home = claude_data_home()
        claude_sources: tuple[tuple[Path, str, Scope], ...] = (
            (home / "settings.json", "~/.claude/settings.json", "user"),
            (cwd / ".claude" / "settings.json", ".claude/settings.json", "project"),
            (cwd / ".claude" / "settings.local.json", ".claude/settings.local.json", "local"),
            (cwd / ".mcp.json", ".mcp.json", "project"),
            (Path.home() / ".claude.json", "~/.claude.json", "user"),
        )
        for path, label, scope in claude_sources:
            _source_once(sources, _load_source(path, label, scope, "json"))
        for index, raw in enumerate(_values_after(args, "--settings"), start=1):
            path = Path(raw)
            if not path.is_absolute():
                path = cwd / path
            _source_once(
                sources,
                _load_source(path, f"Session settings {index}", "session", "json"),
            )
        for index, raw in enumerate(_values_after(args, "--mcp-config", variadic=True), start=1):
            path = Path(raw)
            if not path.is_absolute():
                path = cwd / path
            _source_once(
                sources,
                _load_source(path, f"Session MCP config {index}", "session", "json"),
            )
    elif backend == "codex":
        home = codex_data_home()
        user_config = _load_source(
            home / "config.toml", "~/.codex/config.toml", "user", "toml"
        )
        _source_once(sources, user_config)
        profile = _value_after(args, "--profile", "-p")
        if profile and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile):
            profiles = user_config.data.get("profiles")
            profile_data = profiles.get(profile) if isinstance(profiles, dict) else None
            if isinstance(profile_data, dict):
                source = _virtual_source(
                    f"Codex profile: {profile}", "user", profile_data, format_name="toml-profile"
                )
                source.mtime = user_config.mtime
                _source_once(sources, source)
        _source_once(
            sources,
            _load_source(
                cwd / ".codex" / "config.toml", ".codex/config.toml", "project", "toml"
            ),
        )
        codex_hook_sources: tuple[tuple[Path, str, Scope], ...] = (
            (home / "hooks.json", "~/.codex/hooks.json", "user"),
            (cwd / ".codex" / "hooks.json", ".codex/hooks.json", "project"),
        )
        for path, label, scope in codex_hook_sources:
            _source_once(sources, _load_source(path, label, scope, "json"))
        cli = _codex_cli_config(args)
        if cli:
            sources.append(_virtual_source("CLI overrides", "session", cli))
    elif backend == "omp":
        home = descriptor("omp").data_home()
        omp_sources: tuple[tuple[Path, str, Scope], ...] = (
            (home / "settings.json", "~/.omp/agent/settings.json", "user"),
            (cwd / ".omp" / "settings.json", ".omp/settings.json", "project"),
            (home / "mcp.json", "~/.omp/agent/mcp.json", "user"),
            (home / ".mcp.json", "~/.omp/agent/.mcp.json", "user"),
            (cwd / ".omp" / "mcp.json", ".omp/mcp.json", "project"),
            (cwd / ".omp" / ".mcp.json", ".omp/.mcp.json", "project"),
            (cwd / "mcp.json", "mcp.json", "project"),
            (cwd / ".mcp.json", ".mcp.json", "project"),
        )
        for path, label, scope in omp_sources:
            _source_once(sources, _load_source(path, label, scope, "json"))
        for index, raw in enumerate(_values_after(args, "--extension"), start=1):
            root = Path(raw)
            if not root.is_absolute():
                root = cwd / root
            if root.is_dir():
                _source_once(
                    sources,
                    _load_source(
                        root / ".mcp.json",
                        f"Session extension MCP config {index}",
                        "session",
                        "json",
                    ),
                )
    elif backend == "pi":
        pi_home = descriptor("pi").data_home()
        pi_sources: tuple[tuple[Path, str, Scope], ...] = (
            (pi_home / "settings.json", "~/.pi/agent/settings.json", "user"),
            (cwd / ".pi" / "settings.json", ".pi/settings.json", "project"),
        )
        for path, label, scope in pi_sources:
            _source_once(sources, _load_source(path, label, scope, "json"))
    elif backend == "opencode":
        opencode_sources: tuple[tuple[Path, str, Scope], ...] = (
            (
                Path.home() / ".config" / "opencode" / "opencode.json",
                "~/.config/opencode/opencode.json",
                "user",
            ),
            (
                Path.home() / ".config" / "opencode" / "opencode.jsonc",
                "~/.config/opencode/opencode.jsonc",
                "user",
            ),
            (cwd / "opencode.json", "opencode.json", "project"),
            (cwd / "opencode.jsonc", "opencode.jsonc", "project"),
        )
        for path, label, scope in opencode_sources:
            _source_once(sources, _load_source(path, label, scope, "json"))
        # The mux-owned layer added through OPENCODE_CONFIG. Listing it keeps the
        # inventory honest about the plugin mux injected.
        session_config = os.environ.get("OPENCODE_CONFIG")
        if session_config:
            _source_once(
                sources,
                _load_source(
                    Path(session_config),
                    "Session config (OPENCODE_CONFIG)",
                    "session",
                    "json",
                ),
            )
    elif backend == "shell":
        raise ValueError("shell sessions do not have agent configuration")
    else:
        assert_never(backend)
    return sources


def _plugin_inventory(
    backend: Backend, cwd: Path, sources: list[ConfigSource]
) -> tuple[list[dict[str, Any]], list[tuple[Path, str, Scope]], list[tuple[str, str]]]:
    """Plugin rows, the roots they install into, and extra fingerprint material.

    A plugin manifest is read but is not itself a listed configuration source:
    scanning it as one would feed its `hooks`/`mcpServers` keys to the hook and
    MCP walks, which read every source, and invent rows the CLI never loads. Its
    content therefore reaches the drift baseline as extra material attributed to
    the source that *declares* the plugin, so a manifest edit still registers.
    """
    items: list[dict[str, Any]] = []
    roots: list[tuple[Path, str, Scope]] = []
    material: list[tuple[str, str]] = []
    if backend == "claude":
        home = claude_data_home()
        registry = _load_source(
            home / "plugins" / "installed_plugins.json",
            "Claude plugin registry",
            "user",
            "json",
        )
        _source_once(sources, registry)
        enabled: dict[str, tuple[bool, Scope, ConfigSource]] = {}
        for source in sources:
            configured = source.data.get("enabledPlugins")
            if isinstance(configured, dict):
                for plugin_id, value in configured.items():
                    enabled[str(plugin_id)] = (bool(value), source.scope, source)
        installed = registry.data.get("plugins")
        if not isinstance(installed, dict):
            installed = {}
        for plugin_id, records in sorted(installed.items()):
            state, definition_scope, source = enabled.get(
                str(plugin_id), (False, "unknown", registry)
            )
            rows = records if isinstance(records, list) else []
            for row_index, row in enumerate(rows or [{}]):
                if not isinstance(row, dict):
                    continue
                install_path = row.get("installPath")
                root = (
                    Path(install_path) if isinstance(install_path, str) and install_path else None
                )
                manifest_data: dict[str, Any] = {}
                if root:
                    manifest = _load_source(
                        root / ".claude-plugin" / "plugin.json",
                        f"Plugin manifest: {str(plugin_id).split('@')[0]}",
                        definition_scope,
                        "json",
                    )
                    manifest_data = manifest.data
                    material.append((source.source_id, _data_material(manifest)))
                    if state:
                        roots.append((root, str(plugin_id), definition_scope))
                meta = []
                version = row.get("version") or manifest_data.get("version")
                if version:
                    meta.append(("Version", str(version)))
                components = [
                    key
                    for key in ("skills", "agents", "hooks", "mcpServers", "lspServers", "monitors")
                    if manifest_data.get(key)
                ]
                if components:
                    meta.append(("Components", ", ".join(components)))
                item = _item(
                    "plugin",
                    str(plugin_id),
                    scope=definition_scope,
                    origin="plugin registry",
                    state="enabled" if state else "disabled",
                    description=str(manifest_data.get("description") or ""),
                    source=source,
                    meta=meta,
                    unique=str(row_index),
                )
                items.append(item)
    elif backend == "codex":
        plugin_settings: dict[str, tuple[dict[str, Any], ConfigSource]] = {}
        for source in sources:
            table = source.data.get("plugins")
            if isinstance(table, dict):
                for plugin_id, value in table.items():
                    plugin_settings[str(plugin_id)] = (
                        value if isinstance(value, dict) else {"enabled": bool(value)},
                        source,
                    )
        cache = codex_data_home() / "plugins" / "cache"
        for plugin_id, (value, source) in sorted(plugin_settings.items()):
            is_enabled = value.get("enabled", True) is not False
            codex_root: Path | None = None
            if "@" in plugin_id:
                plugin, marketplace = plugin_id.split("@", 1)
                try:
                    candidates = [
                        path for path in (cache / marketplace / plugin).glob("*") if path.is_dir()
                    ]
                    dated = [(path.stat().st_mtime, path) for path in candidates]
                except OSError:
                    dated = []
                if dated:
                    codex_root = max(dated)[1]
            codex_manifest_data: dict[str, Any] = {}
            if codex_root:
                manifest = _load_source(
                    codex_root / ".codex-plugin" / "plugin.json",
                    f"Plugin manifest: {plugin_id.split('@')[0]}",
                    source.scope,
                    "json",
                )
                codex_manifest_data = manifest.data
                material.append((source.source_id, _data_material(manifest)))
                if is_enabled:
                    roots.append((codex_root, plugin_id, source.scope))
            meta = []
            if codex_manifest_data.get("version"):
                meta.append(("Version", str(codex_manifest_data["version"])))
            components = [
                key
                for key in ("skills", "hooks", "mcpServers", "apps")
                if codex_manifest_data.get(key)
            ]
            if components:
                meta.append(("Components", ", ".join(components)))
            item = _item(
                "plugin",
                plugin_id,
                scope=source.scope,
                origin="Codex plugin config",
                state="enabled" if is_enabled else "disabled",
                description=str(codex_manifest_data.get("description") or ""),
                source=source,
                meta=meta,
            )
            items.append(item)
    elif backend == "omp":
        pass
    elif backend == "pi" or backend == "opencode":
        pass
    elif backend == "shell":
        raise ValueError("shell sessions do not have agent plugins")
    else:
        assert_never(backend)
    return items, roots, material


def _command_tokens(raw: str) -> list[str]:
    try:
        tokens = shlex.split(raw, posix=os.name != "nt")
    except ValueError:
        tokens = raw.split()
    # Non-POSIX splitting keeps the quote characters on each token.
    return [stripped for token in tokens if (stripped := token.strip("\"'"))]


def _is_script_token(token: str) -> bool:
    """True when a token is structurally a script path rather than an argument.

    Deliberately narrow: a flag, a `key=value` assignment, a URL, and anything
    naming a credential are all refused, so the only thing a hook command can
    contribute to the inventory is a filesystem path or a bare script name.
    """
    if not token or token.startswith("-") or "=" in token or "://" in token or "@" in token:
        return False
    if _CREDENTIAL_WORD.search(token):
        return False
    if Path(token).suffix.lower() in _SCRIPT_SUFFIXES:
        return True
    return "/" in token or "\\" in token


@dataclass(frozen=True, slots=True)
class _HookTarget:
    """What one hook handler runs, reduced to the parts that are safe to show."""

    program: str = ""
    target: str = ""
    inline_shell: bool = False
    provisioned_by_mux: bool = False


def _hook_handler_target(handler: dict[str, Any]) -> _HookTarget:
    """Reduce one hook handler's command to its program and single script target.

    Only the program and the one script or module it runs are read out; every
    remaining argument stays withheld, because a hook command line is exactly where
    a user's own tokens and passwords sit.  A handler whose target cannot be
    identified structurally reports its program alone rather than guessing, and an
    inline shell body is reported as such rather than quoted.
    """
    raw = ""
    for key in ("command", "command_windows"):
        value = handler.get(key)
        if isinstance(value, str) and value.strip():
            raw = value
            break
    if not raw:
        return _HookTarget()
    provisioned = _MUX_HOOK_MARKER in raw
    tokens = _command_tokens(raw)
    if not tokens:
        return _HookTarget(provisioned_by_mux=provisioned)
    inline = tokens[0] in _SHELL_KEYWORDS
    program = "" if inline else Path(tokens[0]).name[:120]
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in {"-m", "--module"} and index + 1 < len(tokens):
            candidate = tokens[index + 1]
            if _MODULE_NAME.fullmatch(candidate):
                return _HookTarget(program, candidate[:240], inline, provisioned)
            index += 2
            continue
        if _is_script_token(token):
            return _HookTarget(program, token[:240], inline, provisioned)
        index += 1
    return _HookTarget(program, "", inline, provisioned)


def _hooks_from_data(
    data: dict[str, Any], source: ConfigSource, *, origin: str
) -> list[dict[str, Any]]:
    table = data.get("hooks") if isinstance(data.get("hooks"), dict) else data
    if not isinstance(table, dict):
        return []
    items: list[dict[str, Any]] = []
    for event, groups in table.items():
        if not isinstance(groups, list):
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            for handler_index, handler in enumerate(handlers):
                if not isinstance(handler, dict):
                    continue
                handler_type = str(handler.get("type") or "command")
                run = _hook_handler_target(handler)
                # The event is the group heading, so the row identifies the handler
                # by what it runs: the question "which of these is swe-mux?" has no
                # answer when every row is named after its event.
                name = Path(run.target).name if run.target else run.program
                meta: list[tuple[str, str]] = []
                if run.target:
                    meta.append(("Runs", run.target))
                elif run.program:
                    meta.append(("Runs", f"{run.program} (arguments withheld)"))
                if run.inline_shell:
                    meta.append(("Program", "inline shell"))
                elif run.program and run.program != name:
                    meta.append(("Program", run.program))
                if isinstance(matcher, str) and matcher:
                    meta.append(("Matcher", matcher[:160]))
                timeout = handler.get("timeout")
                if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
                    meta.append(("Timeout", f"{timeout:g}s"))
                if handler_type != "command":
                    meta.append(("Handler", handler_type))
                fallback = "inline shell command" if run.inline_shell else f"{handler_type} handler"
                items.append(
                    _item(
                        "hook",
                        name or fallback,
                        scope=source.scope,
                        origin=origin,
                        state="configured",
                        # No per-row prose for swe-mux's own: it would repeat on every
                        # one of its dozen handlers. The `swe_mux` owner chip carries
                        # the fact and the section note explains it once.
                        description="",
                        source=source,
                        meta=meta,
                        unique=f"{event}:{group_index}:{handler_index}",
                        group=str(event),
                        owner="swe_mux" if run.provisioned_by_mux else "",
                    )
                )
    return items


def _hook_inventory(
    sources: list[ConfigSource], plugin_roots: list[tuple[Path, str, Scope]]
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source in sources:
        items.extend(_hooks_from_data(source.data, source, origin=source.label))
    for root, plugin_id, scope in plugin_roots:
        source = _load_source(
            root / "hooks" / "hooks.json",
            f"Plugin hooks: {plugin_id.split('@')[0]}",
            scope,
            "json",
        )
        if source.status == "ready":
            _source_once(sources, source)
            items.extend(_hooks_from_data(source.data, source, origin=f"plugin: {plugin_id}"))
    # Lifecycle order, not source order: the section is read as "what fires when",
    # and every handler for one event has to sit under one heading for the UI to
    # group by a contiguous run. Stable, so discovery order survives inside a group.
    unknown = len(_HOOK_EVENTS)
    items.sort(key=lambda item: (_HOOK_EVENT_RANK.get(item["group"], unknown), item["group"]))
    return items


def _omp_extension_hook_items(args: Sequence[str]) -> list[dict[str, Any]]:
    """Represent OMP's in-process extensions in the Hooks section.

    OMP has no Claude-style hooks table for the config scan to find: its
    lifecycle reporting is an extension package mux injects per session via
    ``--extension``. Without this row an OMP session reports "Hooks: 0" while
    every status transition the UI shows is hook-sourced, which reads as "mux
    is not wired in" - the exact question this section exists to answer. Any
    user-supplied ``--extension`` arguments are listed too, without the
    swe-mux owner chip.
    """
    items: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, token in enumerate(args):
        if token == "--extension" and index + 1 < len(args):
            paths.append(str(args[index + 1]))
        elif token.startswith("--extension="):
            paths.append(token.split("=", 1)[1])
    for path_text in paths:
        package = Path(path_text)
        entry = package / "index.ts" if package.is_dir() else package
        # Provenance by content marker, like shim_paths.is_mux_shim: the mux
        # hook is the one extension that posts through MUX_HOOK_URL. Path
        # shapes vary across data dirs and tests; the marker does not.
        try:
            with entry.open("r", encoding="utf-8", errors="replace") as handle:
                provisioned = "MUX_HOOK_URL" in handle.read(MAX_CONFIG_BYTES)
        except OSError:
            provisioned = False
        meta: list[tuple[str, str]] = [("Runs", str(entry))]
        if provisioned:
            meta.append(
                (
                    "Reports",
                    "turn, tool, approval, compaction, and session lifecycle, "
                    "with an ordered per-session sequence",
                )
            )
        items.append(
            _item(
                "hook",
                entry.name if provisioned else package.name,
                scope="session",
                origin="launch argument",
                state="configured" if entry.is_file() else "missing",
                description="",
                meta=meta,
                unique=path_text,
                group="Extension (in-process)",
                owner="swe_mux" if provisioned else "",
            )
        )
    return items


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """One configured MCP entry, with the raw configuration kept beside the row.

    The row is what the drawer renders and is sanitized for it. `config` is the
    unsanitized table the CLI itself would use - command, argv, environment,
    URL, headers - and exists for exactly one caller: the explicit tool-catalog
    fetch (`mcp_tools.py`), which cannot dial or fingerprint a server without it.
    It is never serialized into an API response, and the fetch reduces it to a
    one-way digest before anything is retained.
    """

    name: str
    scope: Scope
    origin: str
    source_label: str
    enabled: bool
    config: dict[str, Any]


def _mcp_entries_from_data(
    backend: Backend,
    data: dict[str, Any],
    source: ConfigSource,
    *,
    origin: str,
    cwd: Path,
) -> list[tuple[dict[str, Any], McpServerConfig]]:
    tables: list[tuple[dict[str, Any], Scope]] = []
    if backend == "claude":
        key = "mcpServers"
    elif backend == "codex":
        key = "mcp_servers"
    elif backend == "omp":
        key = "mcpServers"
    elif backend == "opencode":
        key = "mcp"
    elif backend == "pi":
        # pi 0.74.2 ships no MCP client: its bundled docs have no MCP page and
        # its dist references no MCP config. There is no table to read.
        return []
    elif backend == "shell":
        raise ValueError("shell sessions do not have agent MCP configuration")
    else:
        assert_never(backend)
    direct = data.get(key)
    if isinstance(direct, dict):
        tables.append((direct, source.scope))
    if backend == "claude" and isinstance(data.get("projects"), dict):
        for project_path, project_data in data["projects"].items():
            matches = same_path(project_path, cwd)
            project_servers = (
                project_data.get("mcpServers") if isinstance(project_data, dict) else None
            )
            if matches and isinstance(project_servers, dict):
                tables.append((project_servers, "local"))
    entries: list[tuple[dict[str, Any], McpServerConfig]] = []
    for table, scope in tables:
        for server_name, raw in table.items():
            config = raw if isinstance(raw, dict) else {}
            enabled = (
                config.get("enabled", True) is not False
                and config.get("disabled", False) is not True
            )
            endpoint = safe_endpoint(config.get("url"))
            command = _command_name(config.get("command"))
            transport = (
                "http"
                if endpoint
                else "stdio"
                if command
                else str(config.get("type") or "configured")
            )
            meta = [("Transport", transport)]
            if endpoint:
                meta.append(("Endpoint", endpoint))
            if command:
                meta.append(("Executable", command))
            enabled_tools = config.get("enabled_tools") or config.get("allowedTools")
            disabled_tools = config.get("disabled_tools")
            if isinstance(enabled_tools, list):
                meta.append(("Enabled tools", str(len(enabled_tools))))
            if isinstance(disabled_tools, list):
                meta.append(("Disabled tools", str(len(disabled_tools))))
            entries.append(
                (
                    _item(
                        "mcp_server",
                        str(server_name),
                        scope=scope,
                        origin=origin,
                        state="configured" if enabled else "disabled",
                        description=(
                            "Configured passively; connection and tool health are not probed."
                        ),
                        source=source,
                        meta=meta,
                    ),
                    McpServerConfig(
                        name=str(server_name),
                        scope=scope,
                        origin=origin,
                        source_label=source.label,
                        enabled=enabled,
                        config=config,
                    ),
                )
            )
    return entries


def _mcp_inventory(
    backend: Backend,
    cwd: Path,
    sources: list[ConfigSource],
    plugin_roots: list[tuple[Path, str, Scope]],
) -> tuple[list[dict[str, Any]], dict[str, McpServerConfig]]:
    """Rows in layer order, plus the *winning* configuration per server name.

    Both come out of one walk because the shadowing rule that decides which row
    reads `shadowed` is the same rule that decides which configuration a fetch
    would actually dial. Two walks would eventually disagree, and the way they
    would disagree is by probing a server the CLI does not use.
    """
    items: list[dict[str, Any]] = []
    winners: dict[str, int] = {}
    configs: dict[str, McpServerConfig] = {}

    def absorb(found: list[tuple[dict[str, Any], McpServerConfig]]) -> None:
        for item, entry in found:
            key = item["name"].casefold()
            if key in winners and items[winners[key]]["state"] != "disabled":
                items[winners[key]]["state"] = "shadowed"
            winners[key] = len(items)
            configs[key] = entry
            items.append(item)

    for source in sources:
        absorb(_mcp_entries_from_data(backend, source.data, source, origin=source.label, cwd=cwd))
    for root, plugin_id, scope in plugin_roots:
        source = _load_source(
            root / ".mcp.json",
            f"Plugin MCP: {plugin_id.split('@')[0]}",
            scope,
            "json",
        )
        if source.status == "ready":
            _source_once(sources, source)
            absorb(
                _mcp_entries_from_data(
                    backend, source.data, source, origin=f"plugin: {plugin_id}", cwd=cwd
                )
            )
    return items, configs


def resolve_mcp_servers(
    *,
    backend: str,
    cwd: Path | str,
    args: list[str],
) -> dict[str, McpServerConfig]:
    """The configuration a fetch would dial, keyed by casefolded server name.

    Deliberately a separate entry point rather than a field on the inventory
    payload: the raw configuration carries the credentials the inventory exists
    to keep out of a response, so it never travels with one. This runs the same
    bounded source scan, which is cheap and already cached at the request layer.
    """
    resolved = require_backend(backend)
    if not is_agent_harness(resolved):
        raise ValueError("agent environment is available only for registered agent sessions")
    root = Path(cwd).resolve()
    sources = _config_sources(resolved, root, args)
    _, plugin_roots, _ = _plugin_inventory(resolved, root, sources)
    _, configs = _mcp_inventory(resolved, root, sources, plugin_roots)
    return configs


def _markdown_agents(
    root: Path,
    scope: Scope,
    origin: str,
    sources: list[ConfigSource],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        paths = sorted(root.glob("*.md"), key=lambda path: path.name.casefold())[:128]
    except OSError:
        return items
    for path in paths:
        text, status, mtime = _bounded_text(path)
        if text is None or status != "ready":
            continue
        meta = parse_frontmatter(text[:16_384])
        name = meta.get("name") or path.stem
        details: list[tuple[str, str]] = []
        for key, label in (("model", "Model"), ("permissionMode", "Permission mode")):
            if meta.get(key):
                details.append((label, meta[key]))
        if meta.get("tools"):
            details.append(("Tools", meta["tools"]))
        source = ConfigSource(
            _opaque("source", str(path.resolve(strict=False))),
            f"Agent definition: {path.name}",
            scope,
            "markdown",
            {},
            "ready",
            mtime,
        )
        _source_once(sources, source)
        items.append(
            _item(
                "agent",
                name,
                scope=scope,
                origin=origin,
                state="configured",
                description=meta.get("description") or "Custom agent definition",
                source=source,
                meta=details,
            )
        )
    return items


def _agent_inventory(
    backend: Backend,
    cwd: Path,
    sources: list[ConfigSource],
    plugin_roots: list[tuple[Path, str, Scope]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if backend == "claude":
        items.extend(
            _markdown_agents(
                claude_data_home() / "agents", "user", "user agents", sources
            )
        )
        items.extend(
            _markdown_agents(
                cwd / ".claude" / "agents", "project", "project agents", sources
            )
        )
        for root, plugin_id, scope in plugin_roots:
            items.extend(
                _markdown_agents(root / "agents", scope, f"plugin: {plugin_id}", sources)
            )
        for name, description in (
            ("general-purpose", "Built-in general-purpose subagent"),
            ("Explore", "Built-in codebase exploration subagent"),
            ("Plan", "Built-in planning subagent"),
        ):
            items.append(
                _item(
                    "agent",
                    name,
                    scope="built_in",
                    origin="Claude Code",
                    state="documented",
                    description=description,
                )
            )
    elif backend == "codex":
        known = {
            "enabled",
            "max_threads",
            "max_concurrent_threads_per_session",
            "default_subagent_model",
            "default_subagent_reasoning_effort",
            "interrupt_message",
        }
        for source in sources:
            table = source.data.get("agents")
            if not isinstance(table, dict):
                continue
            for name, value in table.items():
                if name in known or not isinstance(value, dict):
                    continue
                meta = []
                if value.get("config_file"):
                    meta.append(("Config", "separate role config"))
                items.append(
                    _item(
                        "agent",
                        str(name),
                        scope=source.scope,
                        origin=source.label,
                        state="configured",
                        description=str(value.get("description") or "Custom Codex role"),
                        source=source,
                        meta=meta,
                    )
                )
    elif backend == "omp":
        pass
    elif backend == "pi" or backend == "opencode":
        pass
    elif backend == "shell":
        raise ValueError("shell sessions do not have agent definitions")
    else:
        assert_never(backend)
    return items


def _tool_inventory(
    backend: Backend, sources: list[ConfigSource], args: list[str]
) -> list[dict[str, Any]]:
    if backend == "shell":
        raise ValueError("shell sessions do not have agent tools")
    catalog = descriptor(backend).tool_catalog
    items = [
        _item(
            "built_in_tool",
            name,
            scope="built_in",
            origin=f"{backend} documented catalog",
            state="documented",
            description=description,
        )
        for name, description in catalog
    ]
    denied: set[str] = set()
    allowed: set[str] | None = None
    if backend == "claude":
        for source in sources:
            permissions = source.data.get("permissions")
            if isinstance(permissions, dict):
                rules = permissions.get("deny")
                if isinstance(rules, list):
                    denied.update(str(rule).split("(", 1)[0] for rule in rules)
        disallowed = _value_after(args, "--disallowed-tools", "--disallowedTools")
        if disallowed:
            denied.update(part.strip().split("(", 1)[0] for part in re.split(r"[, ]+", disallowed))
        selected = _value_after(args, "--tools", "--allowed-tools", "--allowedTools")
        if selected and selected != "default":
            allowed = {
                part.strip().split("(", 1)[0]
                for part in re.split(r"[, ]+", selected)
                if part.strip()
            }
    elif backend == "codex":
        pass
    elif backend == "omp":
        pass
    elif backend == "pi":
        # `--tools` is an allowlist and `--no-tools`/`--no-builtin-tools`
        # disable by default, mirroring Claude's shape closely enough to reuse
        # the same restriction pass below.
        selected = _value_after(args, "--tools", "-t")
        if selected:
            allowed = {
                part.strip().split("(", 1)[0]
                for part in re.split(r"[, ]+", selected)
                if part.strip()
            }
        elif "--no-tools" in args or "-nt" in args:
            allowed = set()
    elif backend == "opencode":
        pass
    elif backend == "shell":
        raise AssertionError("shell backend rejected before tool dispatch")
    else:
        assert_never(backend)
    for item in items:
        if item["name"] in denied or (allowed is not None and item["name"] not in allowed):
            item["state"] = "restricted"
    return items


def _policy_inventory(backend: Backend, sources: list[ConfigSource]) -> list[dict[str, Any]]:
    keys: tuple[str, ...]
    if backend == "claude":
        keys = (
            "model",
            "effortLevel",
            "permissions.defaultMode",
            "permissions.allow",
            "permissions.ask",
            "permissions.deny",
            "disableAllHooks",
            "allowManagedHooksOnly",
            "allowManagedMcpServersOnly",
            "agent",
        )
    elif backend == "codex":
        keys = (
            "model",
            "model_reasoning_effort",
            "approval_policy",
            "approvals_reviewer",
            "sandbox_mode",
            "default_permissions",
            "personality",
            "web_search",
            "agents.enabled",
        )
    elif backend == "omp":
        keys = ()
    elif backend == "pi" or backend == "opencode":
        keys = ()
    elif backend == "shell":
        raise ValueError("shell sessions do not have agent policy")
    else:
        assert_never(backend)
    winners: dict[str, tuple[Any, ConfigSource]] = {}
    for source in sources:
        for key in keys:
            value = _nested(source.data, key)
            if value is not None:
                winners[key] = (value, source)
    items: list[dict[str, Any]] = []
    for key, (value, source) in winners.items():
        state = "disabled" if value is False else "configured"
        items.append(
            _item(
                "policy",
                key,
                scope=source.scope,
                origin=source.label,
                state=state,
                source=source,
                meta=[("Value", _display_value(value))],
            )
        )
    return items


def _feature_inventory(backend: Backend, sources: list[ConfigSource]) -> list[dict[str, Any]]:
    if backend == "claude":
        return []
    if backend == "omp":
        return []
    if backend == "pi" or backend == "opencode":
        return []
    if backend == "shell":
        raise ValueError("shell sessions do not have agent features")
    if backend == "codex":
        pass
    else:
        assert_never(backend)
    winners: dict[str, tuple[Any, ConfigSource]] = {}
    for source in sources:
        table = source.data.get("features")
        if not isinstance(table, dict):
            continue
        for name, value in table.items():
            if isinstance(value, (bool, int, float, str)):
                winners[str(name)] = (value, source)
    return [
        _item(
            "feature",
            name,
            scope=source.scope,
            origin=source.label,
            state="enabled" if value is True else "disabled" if value is False else "configured",
            source=source,
            meta=[("Value", _display_value(value))],
        )
        for name, (value, source) in sorted(winners.items())
    ]


@dataclass(slots=True)
class _ConfigScan:
    """Everything the inventory derives from configuration files.

    Deliberately excludes the two expensive parts of a full inventory - the
    ``--version`` probe and skill discovery - so capturing a drift baseline at
    CLI launch costs only the bounded file reads it already performs.
    """

    sources: list[ConfigSource] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    plugins: list[dict[str, Any]] = field(default_factory=list)
    plugin_roots: list[tuple[Path, str, Scope]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    mcp_configs: dict[str, McpServerConfig] = field(default_factory=dict)
    agents: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)
    features: list[dict[str, Any]] = field(default_factory=list)
    #: (source_id, material) for content read but not listed as its own source.
    extra_material: list[tuple[str, str]] = field(default_factory=list)

    def items(self) -> list[dict[str, Any]]:
        return [
            *self.tools,
            *self.plugins,
            *self.hooks,
            *self.mcp_servers,
            *self.agents,
            *self.policies,
            *self.features,
        ]


def _scan_config(backend: Backend, cwd: Path, args: list[str]) -> _ConfigScan:
    """Read every configuration layer once, in the order later layers depend on.

    Each inventory appends the further files it discovers to ``sources`` (plugin
    hooks, plugin MCP, markdown agents), so policies and features run last and
    see the complete set.
    """
    sources = _config_sources(backend, cwd, args)
    plugins, plugin_roots, extra_material = _plugin_inventory(backend, cwd, sources)
    tools = _tool_inventory(backend, sources, args)
    hooks = _hook_inventory(sources, plugin_roots)
    # OMP's lifecycle wiring is an in-process extension in argv, not a hooks
    # table; it leads the section because for OMP it is the section.
    if backend == "omp":
        hooks = _omp_extension_hook_items(args) + hooks
    mcp_servers, mcp_configs = _mcp_inventory(backend, cwd, sources, plugin_roots)
    agents = _agent_inventory(backend, cwd, sources, plugin_roots)
    return _ConfigScan(
        sources=sources,
        tools=tools,
        plugins=plugins,
        plugin_roots=plugin_roots,
        hooks=hooks,
        mcp_servers=mcp_servers,
        mcp_configs=mcp_configs,
        agents=agents,
        policies=_policy_inventory(backend, sources),
        features=_feature_inventory(backend, sources),
        extra_material=extra_material,
    )


def _fingerprints(scan: _ConfigScan) -> dict[str, str]:
    """One digest per configuration source over everything it contributes.

    Sorted rather than ordered, so a reordering that changes no reported value -
    a re-serialized JSON object, a differently ordered table - is not drift.
    """
    material: dict[str, list[str]] = {
        source.source_id: [f"status:{source.status}"] for source in scan.sources
    }
    for item in scan.items():
        rows = material.get(item["source_id"] or "")
        if rows is not None:
            rows.append(_item_material(item))
    for source_id, extra in scan.extra_material:
        rows = material.get(source_id)
        if rows is not None:
            rows.append(extra)
    return {
        source_id: _opaque("fp", "\n".join(sorted(rows)))
        for source_id, rows in material.items()
    }


def _apply_baseline(scan: _ConfigScan, baseline: Mapping[str, str] | None) -> bool:
    """Mark what differs from the snapshot taken when this CLI generation loaded.

    A source the baseline does not name is left unmarked rather than reported as
    new. Source ids are path digests, so a session whose working directory moved
    after launch resolves *different* project-scoped paths than the baseline
    covers, and calling those "changed since load" would recreate the false
    alarm this replaced. User-scoped layers keep the same ids and stay tracked.
    """
    if not baseline:
        return False
    current = _fingerprints(scan)
    changed = {
        source_id
        for source_id, digest in current.items()
        if source_id in baseline and baseline[source_id] != digest
    }
    for source in scan.sources:
        source.changed_after_start = source.source_id in changed
    for item in scan.items():
        item["changed_after_start"] = item["source_id"] in changed
    return True


def capture_config_baseline(*, backend: str, cwd: Path | str, args: list[str]) -> dict[str, str]:
    """Digest the configuration a CLI generation is loading, per source.

    Called once when an agent CLI starts, and compared on every later scan. It
    performs the same bounded file reads as the inventory and starts no process,
    so it is cheap enough to run on the spawn path - but it must run *off* the
    asyncio event loop like the inventory itself.
    """
    resolved = require_backend(backend)
    if not is_agent_harness(resolved):
        raise ValueError("agent environment is available only for registered agent sessions")
    return _fingerprints(_scan_config(resolved, Path(cwd).resolve(), list(args)))


def _skill_section(
    backend: Backend, cwd: Path, loaded_at: float, refresh: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = discover_skills(backend, cwd, refresh=refresh)
    skills: list[dict[str, Any]] = []
    scope_map: dict[str, Scope] = {
        "project": "project",
        "user": "user",
        "plugin": "unknown",
        "system": "built_in",
    }
    raw_skills: list[dict[str, Any]] = []
    for raw in inventory["skills"]:
        added = float(raw.get("mtime") or 0) > loaded_at
        enriched = {**raw, "added_after_start": added}
        raw_skills.append(enriched)
        state = (
            "shadowed" if raw.get("shadowed_by") else "restart_required" if added else "available"
        )
        meta = [("Invocation", str(raw.get("invocation") or ""))]
        if raw.get("implicit") is False:
            meta.append(("Invocation", "explicit only"))
        skills.append(
            _item(
                str(raw.get("kind") or "skill"),
                str(raw.get("display_name") or raw.get("name") or "skill"),
                scope=scope_map.get(str(raw.get("scope")), "unknown"),
                origin=str(raw.get("origin") or "CLI discovery"),
                state=state,
                description=str(raw.get("description") or ""),
                meta=meta,
            )
        )
    enriched_inventory = {
        **inventory,
        "skills": raw_skills,
        "agent_loaded_at": loaded_at,
    }
    note = (
        "The list is read from the CLI's current roots. Restart required means the file is "
        "newer than this CLI process generation."
    )
    if inventory.get("builtin_skills_hidden"):
        note += " Claude built-in skills are compiled into the CLI and have no headless inventory."
    return _section(
        "skills", "Skills and commands", "current_filesystem", skills, note
    ), enriched_inventory


def discover_agent_environment(
    *,
    backend: str,
    cwd: Path | str,
    executable: str,
    args: list[str],
    model: str | None,
    loaded_at: float,
    run_started_at: float | None,
    baseline: Mapping[str, str] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return one JSON-ready, passive environment inventory.

    This function performs bounded filesystem reads and one cached ``--version``
    probe.  Call it off the asyncio event loop.

    ``baseline`` is the per-source digest captured when this CLI generation
    loaded (`capture_config_baseline`).  Without one, ``config_baseline`` reads
    ``unavailable`` and nothing is reported as changed: a session that started
    before its baseline existed - adopted across an upgrade, or recovered cold -
    has no honest answer, and inventing drift is what made this field useless
    before.  ``loaded_at`` still dates the skills section, whose files are
    user-owned and do not churn.
    """
    if not is_agent_harness(backend):
        raise ValueError("agent environment is available only for registered agent sessions")
    backend = require_backend(backend)
    root = Path(cwd).resolve()
    cache_key = (
        backend,
        str(root),
        executable,
        tuple(args),
        model,
        loaded_at,
        run_started_at,
        _opaque("baseline", json.dumps(sorted((baseline or {}).items()))),
        str(descriptor(backend).data_home()),
    )
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached and not refresh and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    scan = _scan_config(backend, root, args)
    tracked = _apply_baseline(scan, baseline)
    skill_section, skill_inventory = _skill_section(backend, root, loaded_at, refresh)
    sources = scan.sources

    runtime = _runtime(backend, executable, args, model)
    runtime.update(
        {
            "version": probe_cli_version(backend, executable, refresh=refresh),
            "loaded_at": loaded_at,
            "run_started_at": run_started_at,
        }
    )
    diagnostics: list[dict[str, Any]] = [
        {
            "kind": "source",
            "source_id": source.source_id,
            "message": f"{source.label}: {source.status.replace('_', ' ')}",
        }
        for source in sources
        if source.status not in {"ready", "missing", "empty"}
    ]
    if skill_inventory.get("errors"):
        diagnostics.append(
            {
                "kind": "skills",
                "source_id": None,
                "message": f"{len(skill_inventory['errors'])} skill entries could not be read",
            }
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "backend": backend,
        "cwd": str(root),
        "generated_at": time.time(),
        # Whether "changed since load" is an answer at all. `unavailable` means
        # no load-time snapshot exists for this CLI generation, so every source
        # reads unchanged because nothing is known, not because nothing moved.
        "config_baseline": "captured" if tracked else "unavailable",
        "runtime": runtime,
        "sources": [source.public() for source in sources],
        "sections": [
            _section(
                "tools",
                "Built-in tools",
                descriptor(backend).tool_catalog_source,
                scan.tools,
                "Documented core catalog. Restrictions are applied when they can be resolved "
                "passively; host and runtime policy can narrow it further.",
            ),
            skill_section,
            _section(
                "mcp",
                "MCP servers",
                "configured_only",
                scan.mcp_servers,
                "Passive inventory only. Opening this tab never starts servers or performs "
                "authentication and therefore does not claim connection health or a complete "
                "tool list. A row the CLI would actually use can fetch its published tools on "
                "request; that action is the only thing here that reaches a server, and its "
                "result is labelled with the evidence it came from.",
            ),
            _section(
                "plugins",
                "Plugins",
                "installed_config",
                scan.plugins,
                "Installed and configured plugin state; plugin code is never loaded by this scan.",
            ),
            _section(
                "hooks",
                "Hooks",
                "configured_only",
                scan.hooks,
                "Definitions only, grouped by lifecycle event and never executed. Each row "
                "names the program and the one script or module its command runs; the "
                "remaining arguments, inline shell bodies, prompts, URLs, environment, and "
                "credentials are deliberately omitted. Rows tagged swe-mux are installed by "
                "swe-mux itself and report the event back to it, which is what session "
                "status detection reads.",
            ),
            _section(
                "agents",
                "Custom agents",
                "documented_and_configured",
                scan.agents,
                "Custom definitions plus documented Claude built-ins. Runtime-only agents that "
                "have no passive inventory are not inferred.",
            ),
            _section(
                "policies",
                "Policies and configuration",
                "resolved_known_keys",
                scan.policies,
                "Security-sensitive values are summarized. Later configuration layers win for "
                "the displayed known keys.",
            ),
            _section(
                "features",
                "Feature flags",
                "configured_only",
                scan.features,
                "Explicit Codex feature overrides. Defaults owned by the installed CLI are not "
                "guessed.",
            ),
        ],
        "diagnostics": diagnostics,
    }
    with _CACHE_LOCK:
        _CACHE[cache_key] = (now, payload)
    return payload


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _VERSION_CACHE.clear()
