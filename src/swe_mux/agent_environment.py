"""Bounded, passive inventory of one running agent CLI environment.

The inventory describes current configuration and the launch options retained by
swe-mux.  It never starts an MCP server, loads plugin code, runs a hook, or claims
that a file visible now was loaded by an already-running CLI.  Source mtimes are
compared with the CLI process-generation start so drift is explicit.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from .adapters.claude import claude_data_home
from .adapters.codex import codex_data_home
from .agent_skills import discover_skills, parse_frontmatter
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


@dataclass(slots=True)
class ConfigSource:
    source_id: str
    label: str
    scope: Scope
    format: str
    data: dict[str, Any]
    status: str
    mtime: float | None
    changed_after_start: bool

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
    loaded_at: float,
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
        bool(mtime and mtime > loaded_at),
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
        False,
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
) -> dict[str, Any]:
    identity = f"{kind}:{name}:{origin}:{source.source_id if source else ''}:{unique}"
    return {
        "id": _opaque("item", identity),
        "kind": kind,
        "name": name[:160],
        "description": description[:500],
        "scope": scope,
        "origin": origin[:160],
        "state": state,
        "source_id": source.source_id if source else None,
        "source_label": source.label if source else None,
        "changed_after_start": source.changed_after_start if source else False,
        "meta": [{"label": key[:80], "value": value[:240]} for key, value in (meta or [])],
    }


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


def _safe_endpoint(raw: Any) -> str:
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


def _probe_version(backend: str, executable: str, *, refresh: bool) -> str | None:
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


def _config_sources(
    backend: str, cwd: Path, args: list[str], loaded_at: float
) -> list[ConfigSource]:
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
            _source_once(sources, _load_source(path, label, scope, "json", loaded_at))
        for index, raw in enumerate(_values_after(args, "--settings"), start=1):
            path = Path(raw)
            if not path.is_absolute():
                path = cwd / path
            _source_once(
                sources,
                _load_source(path, f"Session settings {index}", "session", "json", loaded_at),
            )
        for index, raw in enumerate(_values_after(args, "--mcp-config", variadic=True), start=1):
            path = Path(raw)
            if not path.is_absolute():
                path = cwd / path
            _source_once(
                sources,
                _load_source(path, f"Session MCP config {index}", "session", "json", loaded_at),
            )
    else:
        home = codex_data_home()
        user_config = _load_source(
            home / "config.toml", "~/.codex/config.toml", "user", "toml", loaded_at
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
                source.changed_after_start = user_config.changed_after_start
                source.mtime = user_config.mtime
                _source_once(sources, source)
        _source_once(
            sources,
            _load_source(
                cwd / ".codex" / "config.toml", ".codex/config.toml", "project", "toml", loaded_at
            ),
        )
        codex_hook_sources: tuple[tuple[Path, str, Scope], ...] = (
            (home / "hooks.json", "~/.codex/hooks.json", "user"),
            (cwd / ".codex" / "hooks.json", ".codex/hooks.json", "project"),
        )
        for path, label, scope in codex_hook_sources:
            _source_once(sources, _load_source(path, label, scope, "json", loaded_at))
        cli = _codex_cli_config(args)
        if cli:
            sources.append(_virtual_source("CLI overrides", "session", cli))
    return sources


def _plugin_inventory(
    backend: str, cwd: Path, sources: list[ConfigSource], loaded_at: float
) -> tuple[list[dict[str, Any]], list[tuple[Path, str, Scope]]]:
    items: list[dict[str, Any]] = []
    roots: list[tuple[Path, str, Scope]] = []
    if backend == "claude":
        home = claude_data_home()
        registry = _load_source(
            home / "plugins" / "installed_plugins.json",
            "Claude plugin registry",
            "user",
            "json",
            loaded_at,
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
                changed = False
                if root:
                    manifest = _load_source(
                        root / ".claude-plugin" / "plugin.json",
                        f"Plugin manifest: {str(plugin_id).split('@')[0]}",
                        definition_scope,
                        "json",
                        loaded_at,
                    )
                    manifest_data = manifest.data
                    changed = manifest.changed_after_start
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
                item["changed_after_start"] = item["changed_after_start"] or changed
                items.append(item)
    else:
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
            changed = False
            if codex_root:
                manifest = _load_source(
                    codex_root / ".codex-plugin" / "plugin.json",
                    f"Plugin manifest: {plugin_id.split('@')[0]}",
                    source.scope,
                    "json",
                    loaded_at,
                )
                codex_manifest_data = manifest.data
                changed = manifest.changed_after_start
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
            item["changed_after_start"] = item["changed_after_start"] or changed
            items.append(item)
    return items, roots


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
                meta = [("Handler", handler_type)]
                if isinstance(matcher, str) and matcher:
                    meta.append(("Matcher", matcher[:160]))
                items.append(
                    _item(
                        "hook",
                        str(event),
                        scope=source.scope,
                        origin=origin,
                        state="configured",
                        description=f"{handler_type} lifecycle hook",
                        source=source,
                        meta=meta,
                        unique=f"{group_index}:{handler_index}",
                    )
                )
    return items


def _hook_inventory(
    sources: list[ConfigSource], plugin_roots: list[tuple[Path, str, Scope]], loaded_at: float
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
            loaded_at,
        )
        if source.status == "ready":
            _source_once(sources, source)
            items.extend(_hooks_from_data(source.data, source, origin=f"plugin: {plugin_id}"))
    return items


def _mcp_from_data(
    backend: str,
    data: dict[str, Any],
    source: ConfigSource,
    *,
    origin: str,
    cwd: Path,
) -> list[dict[str, Any]]:
    tables: list[tuple[dict[str, Any], Scope]] = []
    key = "mcpServers" if backend == "claude" else "mcp_servers"
    direct = data.get(key)
    if isinstance(direct, dict):
        tables.append((direct, source.scope))
    if backend == "claude" and isinstance(data.get("projects"), dict):
        target = str(cwd.resolve()).casefold()
        for project_path, project_data in data["projects"].items():
            try:
                matches = str(Path(project_path).resolve()).casefold() == target
            except OSError:
                matches = False
            project_servers = (
                project_data.get("mcpServers") if isinstance(project_data, dict) else None
            )
            if matches and isinstance(project_servers, dict):
                tables.append((project_servers, "local"))
    items: list[dict[str, Any]] = []
    for table, scope in tables:
        for server_name, raw in table.items():
            config = raw if isinstance(raw, dict) else {}
            enabled = (
                config.get("enabled", True) is not False
                and config.get("disabled", False) is not True
            )
            endpoint = _safe_endpoint(config.get("url"))
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
            items.append(
                _item(
                    "mcp_server",
                    str(server_name),
                    scope=scope,
                    origin=origin,
                    state="configured" if enabled else "disabled",
                    description="Configured passively; connection and tool health are not probed.",
                    source=source,
                    meta=meta,
                )
            )
    return items


def _mcp_inventory(
    backend: str,
    cwd: Path,
    sources: list[ConfigSource],
    plugin_roots: list[tuple[Path, str, Scope]],
    loaded_at: float,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    winners: dict[str, int] = {}
    for source in sources:
        found = _mcp_from_data(backend, source.data, source, origin=source.label, cwd=cwd)
        for item in found:
            key = item["name"].casefold()
            if key in winners and items[winners[key]]["state"] != "disabled":
                items[winners[key]]["state"] = "shadowed"
            winners[key] = len(items)
            items.append(item)
    for root, plugin_id, scope in plugin_roots:
        source = _load_source(
            root / ".mcp.json",
            f"Plugin MCP: {plugin_id.split('@')[0]}",
            scope,
            "json",
            loaded_at,
        )
        if source.status == "ready":
            _source_once(sources, source)
            for item in _mcp_from_data(
                backend, source.data, source, origin=f"plugin: {plugin_id}", cwd=cwd
            ):
                key = item["name"].casefold()
                if key in winners and items[winners[key]]["state"] != "disabled":
                    items[winners[key]]["state"] = "shadowed"
                winners[key] = len(items)
                items.append(item)
    return items


def _markdown_agents(
    root: Path,
    scope: Scope,
    origin: str,
    loaded_at: float,
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
            bool(mtime and mtime > loaded_at),
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
    backend: str,
    cwd: Path,
    sources: list[ConfigSource],
    plugin_roots: list[tuple[Path, str, Scope]],
    loaded_at: float,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if backend == "claude":
        items.extend(
            _markdown_agents(
                claude_data_home() / "agents", "user", "user agents", loaded_at, sources
            )
        )
        items.extend(
            _markdown_agents(
                cwd / ".claude" / "agents", "project", "project agents", loaded_at, sources
            )
        )
        for root, plugin_id, scope in plugin_roots:
            items.extend(
                _markdown_agents(root / "agents", scope, f"plugin: {plugin_id}", loaded_at, sources)
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
    else:
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
    return items


_CLAUDE_TOOLS = (
    ("Agent", "Spawn a subagent with an isolated context"),
    ("AskUserQuestion", "Request structured user input"),
    ("Bash", "Run shell commands"),
    ("Edit", "Apply exact text edits"),
    ("EnterPlanMode", "Enter planning mode"),
    ("ExitPlanMode", "Leave planning mode"),
    ("Glob", "Find files by pattern"),
    ("Grep", "Search file contents"),
    ("LSP", "Query language servers"),
    ("NotebookEdit", "Edit notebook cells"),
    ("Read", "Read files"),
    ("Skill", "Load an installed skill"),
    ("TaskCreate", "Create tracked work"),
    ("TaskGet", "Read tracked work"),
    ("TaskList", "List tracked work"),
    ("TaskOutput", "Read background task output"),
    ("TaskStop", "Stop background work"),
    ("TaskUpdate", "Update tracked work"),
    ("WebFetch", "Fetch a web page"),
    ("WebSearch", "Search the web"),
    ("Write", "Write a file"),
)

_CODEX_CAPABILITIES = (
    ("shell", "Run commands under the active sandbox and approval policy"),
    ("apply_patch", "Apply structured workspace patches"),
    ("plan", "Track multi-step work"),
    ("skills", "Load installed skills"),
    ("multi-agent", "Coordinate isolated agent threads"),
    ("web", "Search or fetch when enabled"),
    ("MCP", "Use tools exposed by configured MCP servers"),
    ("apps", "Use enabled app and connector tools"),
)


def _tool_inventory(
    backend: str, sources: list[ConfigSource], args: list[str]
) -> list[dict[str, Any]]:
    catalog = _CLAUDE_TOOLS if backend == "claude" else _CODEX_CAPABILITIES
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
    for item in items:
        if item["name"] in denied or (allowed is not None and item["name"] not in allowed):
            item["state"] = "restricted"
    return items


def _policy_inventory(backend: str, sources: list[ConfigSource]) -> list[dict[str, Any]]:
    keys = (
        (
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
        if backend == "claude"
        else (
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
    )
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


def _feature_inventory(backend: str, sources: list[ConfigSource]) -> list[dict[str, Any]]:
    if backend != "codex":
        return []
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


def _skill_section(
    backend: str, cwd: Path, loaded_at: float, refresh: bool
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
    refresh: bool = False,
) -> dict[str, Any]:
    """Return one JSON-ready, passive environment inventory.

    This function performs bounded filesystem reads and one cached ``--version``
    probe.  Call it off the asyncio event loop.
    """
    if backend not in {"claude", "codex"}:
        raise ValueError("agent environment is available only for Claude and Codex sessions")
    root = Path(cwd).resolve()
    cache_key = (
        backend,
        str(root),
        executable,
        tuple(args),
        model,
        loaded_at,
        run_started_at,
        str(claude_data_home() if backend == "claude" else codex_data_home()),
    )
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached and not refresh and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    sources = _config_sources(backend, root, args, loaded_at)
    plugins, plugin_roots = _plugin_inventory(backend, root, sources, loaded_at)
    skill_section, skill_inventory = _skill_section(backend, root, loaded_at, refresh)
    tools = _tool_inventory(backend, sources, args)
    hooks = _hook_inventory(sources, plugin_roots, loaded_at)
    mcp_servers = _mcp_inventory(backend, root, sources, plugin_roots, loaded_at)
    agents = _agent_inventory(backend, root, sources, plugin_roots, loaded_at)
    policies = _policy_inventory(backend, sources)
    features = _feature_inventory(backend, sources)

    runtime = _runtime(backend, executable, args, model)
    runtime.update(
        {
            "version": _probe_version(backend, executable, refresh=refresh),
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
        "runtime": runtime,
        "sources": [source.public() for source in sources],
        "sections": [
            _section(
                "tools",
                "Built-in tools",
                "documented_catalog" if backend == "claude" else "runtime_dependent",
                tools,
                "Documented core catalog. Restrictions are applied when they can be resolved "
                "passively; host and runtime policy can narrow it further.",
            ),
            skill_section,
            _section(
                "mcp",
                "MCP servers",
                "configured_only",
                mcp_servers,
                "Passive inventory only. Opening this tab never starts servers or performs "
                "authentication and therefore does not claim connection health or a complete "
                "tool list.",
            ),
            _section(
                "plugins",
                "Plugins",
                "installed_config",
                plugins,
                "Installed and configured plugin state; plugin code is never loaded by this scan.",
            ),
            _section(
                "hooks",
                "Hooks",
                "configured_only",
                hooks,
                "Definitions only. Commands, prompts, URLs, and credentials are deliberately "
                "omitted.",
            ),
            _section(
                "agents",
                "Custom agents",
                "documented_and_configured",
                agents,
                "Custom definitions plus documented Claude built-ins. Runtime-only agents that "
                "have no passive inventory are not inferred.",
            ),
            _section(
                "policies",
                "Policies and configuration",
                "resolved_known_keys",
                policies,
                "Security-sensitive values are summarized. Later configuration layers win for "
                "the displayed known keys.",
            ),
            _section(
                "features",
                "Feature flags",
                "configured_only",
                features,
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
