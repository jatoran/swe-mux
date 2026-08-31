"""Versioned, inert parsing for ``swe-mux-plugin.toml``.

Parsing never imports or executes plugin content.  The resulting immutable model is
the single contract used by inspection, registration, invocation, and the author
validator, so a command cannot acquire a different meaning after approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from packaging.version import InvalidVersion, Version

MANIFEST_NAME = "swe-mux-plugin.toml"
MANIFEST_VERSION = 1
HOST_CAPABILITIES = frozenset(
    {
        "plugin.actions.v1",
        "plugin.panes.v1",
        "plugin.events.v1",
        "plugin.startup.v1",
        "plugin.links.v1",
    }
)
API_PERMISSIONS = frozenset(
    {
        "projects.read",
        "sessions.read",
        "sessions.control",
        "terminal.write",
        "notifications.write",
        "plugins.self",
    }
)
CONTEXTS = frozenset({"global", "project", "session", "pane", "selection", "worktree"})
PLACEMENTS = frozenset({"tab", "split", "popup"})
PLATFORMS = frozenset({"windows", "linux", "macos"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:-]{0,63}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
MAX_COMMAND_PARTS = 128
MAX_COMMAND_BYTES = 32 * 1024
MAX_ENV = 64
MAX_CONTRIBUTIONS = 128
MAX_PLUGIN_FILES = 4096
MAX_PLUGIN_BYTES = 512 * 1024 * 1024


class PluginManifestError(ValueError):
    """The manifest is malformed, unsafe, or incompatible."""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command: tuple[str, ...]
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    platforms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginAction:
    id: str
    title: str
    command: CommandSpec
    description: str = ""
    contexts: tuple[str, ...] = ("global",)


@dataclass(frozen=True, slots=True)
class PluginPane:
    id: str
    title: str
    command: CommandSpec
    description: str = ""
    placement: Literal["tab", "split", "popup"] = "tab"
    contexts: tuple[str, ...] = ("project",)


@dataclass(frozen=True, slots=True)
class PluginEventHook:
    id: str
    on: str
    command: CommandSpec
    match: dict[str, str] = field(default_factory=dict)
    rate_limit_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class PluginStartup:
    id: str
    command: CommandSpec


@dataclass(frozen=True, slots=True)
class PluginLinkHandler:
    id: str
    title: str
    pattern: str
    action: str
    platforms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginManifest:
    manifest_version: int
    id: str
    name: str
    version: str
    min_swe_mux_version: str
    description: str
    author: str
    license: str
    homepage: str
    platforms: tuple[str, ...]
    architectures: tuple[str, ...]
    requires: tuple[str, ...]
    permissions: tuple[str, ...]
    runtime_requirements: tuple[str, ...]
    actions: tuple[PluginAction, ...]
    panes: tuple[PluginPane, ...]
    events: tuple[PluginEventHook, ...]
    startup: tuple[PluginStartup, ...]
    link_handlers: tuple[PluginLinkHandler, ...]
    path: Path
    digest: str

    def snapshot(self) -> dict[str, Any]:
        result = asdict(self)
        result["path"] = str(self.path)
        return result

    @property
    def security_digest(self) -> str:
        payload = {
            "id": self.id,
            "version": self.version,
            "requires": self.requires,
            "permissions": self.permissions,
            "actions": [asdict(item) for item in self.actions],
            "panes": [asdict(item) for item in self.panes],
            "events": [asdict(item) for item in self.events],
            "startup": [asdict(item) for item in self.startup],
            "link_handlers": [asdict(item) for item in self.link_handlers],
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def _string(raw: dict[str, Any], key: str, *, required: bool = False, limit: int = 4096) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str) or (required and not value.strip()):
        raise PluginManifestError(f"{key} must be a non-empty string")
    if len(value.encode()) > limit:
        raise PluginManifestError(f"{key} is too long")
    return value.strip()


def _strings(
    raw: dict[str, Any], key: str, *, allowed: frozenset[str] | None = None
) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PluginManifestError(f"{key} must be an array of strings")
    result = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
    if allowed is not None and (unknown := sorted(set(result) - allowed)):
        raise PluginManifestError(f"{key} contains unsupported values: {', '.join(unknown)}")
    return result


def _identifier(value: str, field_name: str, *, local: bool = False) -> str:
    matcher = _LOCAL_ID if local else _ID
    if not matcher.fullmatch(value):
        raise PluginManifestError(f"{field_name} is not a valid identifier")
    return value


def _command(raw: Any, *, owner: str) -> CommandSpec:
    if not isinstance(raw, dict):
        raise PluginManifestError(f"{owner} must be a table")
    command = raw.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
        raise PluginManifestError(f"{owner}.command must be a non-empty argv array")
    if (
        len(command) > MAX_COMMAND_PARTS
        or sum(len(x.encode()) for x in command) > MAX_COMMAND_BYTES
    ):
        raise PluginManifestError(f"{owner}.command exceeds its bound")
    if any("\x00" in part for part in command):
        raise PluginManifestError(f"{owner}.command contains a NUL byte")
    cwd = raw.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
        raise PluginManifestError(f"{owner}.cwd must stay beneath the plugin root")
    env = raw.get("env", {})
    if not isinstance(env, dict) or len(env) > MAX_ENV:
        raise PluginManifestError(f"{owner}.env must be a bounded table")
    clean_env: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV.fullmatch(key) or not isinstance(value, str):
            raise PluginManifestError(f"{owner}.env contains an invalid entry")
        if len(value.encode()) > 4096 or "\x00" in value:
            raise PluginManifestError(f"{owner}.env value is invalid")
        if key.startswith(("MUX_", "SWEMUX_")):
            raise PluginManifestError(f"{owner}.env cannot override host identity")
        clean_env[key] = value
    timeout = raw.get("timeout_seconds", 60.0)
    if not isinstance(timeout, int | float) or not 0.1 <= float(timeout) <= 86400:
        raise PluginManifestError(f"{owner}.timeout_seconds is out of range")
    platforms = _strings(raw, "platforms", allowed=PLATFORMS)
    return CommandSpec(tuple(command), cwd, clean_env, float(timeout), platforms)


def _entries(raw: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = raw.get(key, [])
    if not isinstance(value, list) or len(value) > MAX_CONTRIBUTIONS:
        raise PluginManifestError(f"{key} must be a bounded array of tables")
    if any(not isinstance(item, dict) for item in value):
        raise PluginManifestError(f"{key} must contain tables")
    return value


def _unique(items: list[Any], kind: str) -> None:
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise PluginManifestError(f"duplicate {kind} id")


def parse_plugin_manifest(path: str | Path) -> PluginManifest:
    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path /= MANIFEST_NAME
    if manifest_path.name != MANIFEST_NAME:
        raise PluginManifestError(f"manifest must be named {MANIFEST_NAME}")
    try:
        content = manifest_path.read_bytes()
    except OSError as exc:
        raise PluginManifestError(f"cannot read manifest: {exc}") from exc
    if len(content) > 256 * 1024:
        raise PluginManifestError("manifest exceeds 256 KiB")
    try:
        raw = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginManifestError(f"invalid TOML: {exc}") from exc
    version = raw.get("manifest_version")
    if version != MANIFEST_VERSION:
        raise PluginManifestError(f"unsupported manifest_version {version!r}")
    plugin_id = _identifier(_string(raw, "id", required=True, limit=128), "id")
    plugin_version = _string(raw, "version", required=True, limit=64)
    minimum = _string(raw, "min_swe_mux_version", required=True, limit=64)
    try:
        Version(plugin_version)
        Version(minimum)
    except InvalidVersion as exc:
        raise PluginManifestError(f"invalid semantic version: {exc}") from exc
    platforms = _strings(raw, "platforms", allowed=PLATFORMS)
    if not platforms:
        raise PluginManifestError("platforms must declare at least one host")
    requires = _strings(raw, "requires")
    permissions = _strings(raw, "permissions", allowed=API_PERMISSIONS)

    actions = [
        PluginAction(
            _identifier(_string(item, "id", required=True, limit=64), "action id", local=True),
            _string(item, "title", required=True, limit=256),
            _command(item, owner=f"actions[{index}]"),
            _string(item, "description", limit=4096),
            _strings(item, "contexts", allowed=CONTEXTS) or ("global",),
        )
        for index, item in enumerate(_entries(raw, "actions"))
    ]
    panes = [
        PluginPane(
            _identifier(_string(item, "id", required=True, limit=64), "pane id", local=True),
            _string(item, "title", required=True, limit=256),
            _command(item, owner=f"panes[{index}]"),
            _string(item, "description", limit=4096),
            item.get("placement", "tab"),
            _strings(item, "contexts", allowed=CONTEXTS) or ("project",),
        )
        for index, item in enumerate(_entries(raw, "panes"))
    ]
    for pane in panes:
        if pane.placement not in PLACEMENTS:
            raise PluginManifestError(f"pane {pane.id} has unsupported placement")
    events = [
        PluginEventHook(
            _identifier(_string(item, "id", required=True, limit=64), "event id", local=True),
            _identifier(_string(item, "on", required=True, limit=128), "event name"),
            _command(item, owner=f"events[{index}]"),
            {
                str(key): str(value)
                for key, value in (
                    item.get("match", {}) if isinstance(item.get("match", {}), dict) else {}
                ).items()
                if isinstance(key, str) and isinstance(value, str)
            },
            float(item.get("rate_limit_seconds", 0.0)),
        )
        for index, item in enumerate(_entries(raw, "events"))
    ]
    startup = [
        PluginStartup(
            _identifier(_string(item, "id", required=True, limit=64), "startup id", local=True),
            _command(item, owner=f"startup[{index}]"),
        )
        for index, item in enumerate(_entries(raw, "startup"))
    ]
    links = [
        PluginLinkHandler(
            _identifier(_string(item, "id", required=True, limit=64), "link id", local=True),
            _string(item, "title", required=True, limit=256),
            _string(item, "pattern", required=True, limit=2048),
            _identifier(
                _string(item, "action", required=True, limit=64), "link action", local=True
            ),
            _strings(item, "platforms", allowed=PLATFORMS),
        )
        for item in _entries(raw, "link_handlers")
    ]
    for link in links:
        try:
            re.compile(link.pattern)
        except re.error as exc:
            raise PluginManifestError(f"link handler {link.id} has invalid regex: {exc}") from exc
        if link.action not in {action.id for action in actions}:
            raise PluginManifestError(f"link handler {link.id} names an unknown action")
    for collection, kind in (
        (actions, "action"),
        (panes, "pane"),
        (events, "event"),
        (startup, "startup"),
        (links, "link handler"),
    ):
        _unique(collection, kind)

    implied = set()
    if actions:
        implied.add("plugin.actions.v1")
    if panes:
        implied.add("plugin.panes.v1")
    if events:
        implied.add("plugin.events.v1")
    if startup:
        implied.add("plugin.startup.v1")
    if links:
        implied.add("plugin.links.v1")
    missing = implied - set(requires)
    if missing:
        raise PluginManifestError(f"requires is missing: {', '.join(sorted(missing))}")
    unknown = set(requires) - HOST_CAPABILITIES
    if unknown:
        raise PluginManifestError(f"unsupported host capabilities: {', '.join(sorted(unknown))}")

    return PluginManifest(
        MANIFEST_VERSION,
        plugin_id,
        _string(raw, "name", required=True, limit=256),
        plugin_version,
        minimum,
        _string(raw, "description", limit=4096),
        _string(raw, "author", limit=512),
        _string(raw, "license", limit=128),
        _string(raw, "homepage", limit=2048),
        platforms,
        _strings(raw, "architectures"),
        requires,
        permissions,
        _strings(raw, "runtime_requirements"),
        tuple(actions),
        tuple(panes),
        tuple(events),
        tuple(startup),
        tuple(links),
        manifest_path,
        hashlib.sha256(content).hexdigest(),
    )


def current_platform() -> str:
    if os.name == "nt":
        return "windows"
    import sys

    return "macos" if sys.platform == "darwin" else "linux"


def plugin_content_digest(root: str | Path) -> str:
    """Digest every executable package byte without following an escaping link."""
    base = Path(root).resolve()
    digest = hashlib.sha256()
    total = 0
    count = 0
    ignored = {".git", ".trash", "__pycache__"}
    for path in sorted(base.rglob("*"), key=lambda item: item.as_posix().lower()):
        relative = path.relative_to(base)
        if any(part in ignored for part in relative.parts):
            continue
        if path.is_symlink():
            target = path.resolve()
            if not _path_within(base, target):
                raise PluginManifestError(f"plugin link escapes its root: {relative}")
        if not path.is_file():
            continue
        count += 1
        if count > MAX_PLUGIN_FILES:
            raise PluginManifestError(f"plugin exceeds {MAX_PLUGIN_FILES} files")
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PluginManifestError(f"cannot inspect plugin file {relative}: {exc}") from exc
        total += size
        if total > MAX_PLUGIN_BYTES:
            raise PluginManifestError("plugin exceeds 512 MiB expanded")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_mode & 0o777).encode())
        digest.update(b"\0")
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise PluginManifestError(f"cannot hash plugin file {relative}: {exc}") from exc
        digest.update(b"\0")
    return digest.hexdigest()


def _path_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
