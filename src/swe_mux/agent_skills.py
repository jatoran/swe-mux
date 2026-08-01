"""Discover the skills an agent CLI can see, by reading the directories it reads.

Both vendors settled on the same on-disk contract — a directory holding a
`SKILL.md` whose YAML frontmatter carries `name` and `description` — so one
scanner serves both, and the vendor difference reduces to *which roots* are
scanned and *how a skill is invoked* (`/name` on Claude, `$name` on Codex).

What this cannot see, stated here because the gap is invisible from the output:

- **Claude's built-in skills are compiled into the CLI binary**, not installed on
  disk, and there is no headless enumeration of them (`/skills` is a TUI-only
  command, and `claude plugin details` only covers installed plugins). A Claude
  inventory is therefore user + project + plugin skills, and says so through
  `builtin_skills_hidden` rather than quietly under-reporting.
- **Codex can be asked directly** — its app-server protocol has `skills/list`
  (with enable state) and a `skills/changed` notification. We deliberately do not
  speak it: it is flagged experimental, costs a JSON-RPC handshake per query, and
  the directory scan answers the drawer's question without one. The single thing
  the scan cannot recover is a skill disabled through the Codex TUI, whose state
  is persisted neither in `config.toml` nor in the `CODEX_HOME` state database.
- An inventory describes **the disk now**, not what a running CLI loaded at
  startup. `added_after_start` marks entries newer than the session's agent run,
  so a skill the live process cannot possibly know about is legible as such.

Roots are verified against Claude Code 2.1.220 and Codex 0.145 (skills 0.146):
Codex reads repo skills from both `.codex/skills` and `.agents/skills` under its
cwd, and does *not* read `.claude/skills`. Claude surfaces `commands/*.md` in the
same list as skills, which is why command roots are scanned here too.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.claude import claude_data_home
from .adapters.codex import codex_data_home

# Enough to cover any real frontmatter block without reading a whole skill body.
FRONTMATTER_BYTES = 16 * 1024
# A ceiling on one inventory. Nothing near this exists in practice; it is here so
# a symlinked or generated tree cannot turn a drawer click into an unbounded read.
MAX_SKILLS = 500
# Claude namespaces nested command files (`commands/git/sync.md` -> `/git:sync`).
MAX_COMMAND_DEPTH = 3
# Collapses the burst of requests a drawer open produces without holding a result
# long enough to be wrong: skills change on the order of days, and a scan is a few
# dozen stats. Bypassed by an explicit refresh.
CACHE_TTL_SECONDS = 10.0

_KEY = re.compile(r"[A-Za-z0-9_.-]+\Z")
_SCOPE_RANK = {"project": 0, "user": 1, "plugin": 2, "system": 3}


@dataclass(frozen=True, slots=True)
class SkillRoot:
    """One directory the CLI reads, reported whether or not it exists.

    Returned even when empty: a root that silently stopped being scanned (a CLI
    update moving one, a Project opened from the wrong cwd) is otherwise
    indistinguishable from a root with nothing in it.
    """

    path: str
    scope: str
    kind: str
    origin: str
    exists: bool
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "scope": self.scope,
            "kind": self.kind,
            "origin": self.origin,
            "exists": self.exists,
            "count": self.count,
        }


@dataclass(slots=True)
class DiscoveredSkill:
    name: str
    description: str
    path: str
    scope: str
    origin: str
    kind: str
    invocation: str
    mtime: float
    # Codex only: `policy.allow_implicit_invocation: false` keeps a skill out of
    # the model's list while leaving it invocable by name. Claude has no
    # equivalent, so its skills are always implicit.
    implicit: bool = True
    display_name: str | None = None
    short_description: str | None = None
    # Set on the loser of a name collision, naming the root that wins.
    shadowed_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "scope": self.scope,
            "origin": self.origin,
            "kind": self.kind,
            "invocation": self.invocation,
            "mtime": self.mtime,
            "implicit": self.implicit,
            "display_name": self.display_name,
            "short_description": self.short_description,
            "shadowed_by": self.shadowed_by,
        }


@dataclass(slots=True)
class SkillInventory:
    backend: str
    cwd: str
    generated_at: float
    roots: list[SkillRoot] = field(default_factory=list)
    skills: list[DiscoveredSkill] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    # Installed but switched off in settings, so not scanned. Named rather than
    # dropped: "my plugin's skill is missing" has exactly one honest answer.
    skipped_plugins: list[str] = field(default_factory=list)
    builtin_skills_hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "cwd": self.cwd,
            "generated_at": self.generated_at,
            "roots": [root.to_dict() for root in self.roots],
            "skills": [skill.to_dict() for skill in self.skills],
            "errors": list(self.errors),
            "truncated": self.truncated,
            "skipped_plugins": list(self.skipped_plugins),
            "builtin_skills_hidden": self.builtin_skills_hidden,
        }


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def parse_yaml_head(text: str) -> dict[str, str]:
    """Parse the flat, at-most-one-level-nested YAML a skill's metadata uses.

    Deliberately not a YAML parser. Skill frontmatter in the wild is `key: value`
    pairs plus one level of nesting (`metadata:`, `interface:`, `policy:`), and
    taking a YAML dependency to read four keys would be the wrong trade. Nested
    keys flatten to `parent.child`; an indented line that is not itself a key
    folds into the previous value, the way a plain scalar continuation does.

    A description containing a colon parses correctly (the split is on the first
    one, and a key may not contain spaces), which is the case that would
    otherwise corrupt half the descriptions in a real skills directory.
    """
    out: dict[str, str] = {}
    parent: str | None = None
    last: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped in {"---", "..."}:
            break
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        key, sep, value = stripped.partition(":")
        if sep and _KEY.fullmatch(key.strip()):
            name = key.strip()
            full = name if indent == 0 or not parent else f"{parent}.{name}"
            if indent == 0:
                parent = name if not value.strip() else None
            out[full] = _unquote(value)
            last = full
        elif last and indent > 0:
            out[last] = f"{out[last]} {_unquote(stripped)}".strip()
    return out


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse a `---`-delimited frontmatter block; `{}` when there is none."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    return parse_yaml_head("\n".join(lines[1:]))


def _read_head(path: Path) -> str:
    with path.open("rb") as handle:
        return handle.read(FRONTMATTER_BYTES).decode("utf-8", "replace")


def _body_summary(text: str) -> str:
    """Description for a file whose frontmatter has none.

    Claude falls back to the body — a skill with no frontmatter at all still
    appears in its list, described by its opening heading — so this prefers the
    first heading and settles for the first prose line, in that order.
    """
    lines = text.lstrip("﻿").splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() in {"---", "..."}:
                lines = lines[index + 1 :]
                break
        else:
            lines = []
    prose = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip()[:300]
        if stripped and not prose and not stripped.startswith(("<!--", "```")):
            prose = stripped[:300]
    return prose


def _codex_agent_meta(directory: Path) -> dict[str, str]:
    """Codex's per-skill display/policy sidecar (`agents/openai.yaml`)."""
    path = directory / "agents" / "openai.yaml"
    try:
        if not path.is_file():
            return {}
        return parse_yaml_head(_read_head(path))
    except OSError:
        return {}


def _scan_skill_root(
    directory: Path,
    scope: str,
    origin: str,
    vendor: str,
    errors: list[dict[str, str]],
) -> list[DiscoveredSkill]:
    """List one skills directory.

    The two CLIs disagree about what names a skill, and the disagreement is not
    cosmetic — it decides what the drawer types into the terminal. Claude keys a
    skill by its **directory** name (a `~/.claude/skills/CreateSkill` declaring
    `name: skill-creator` is invoked as `/CreateSkill`), while Codex keys it by
    the **frontmatter** name (verified by probing both CLIs with a skill whose
    two names differ). Getting this backwards produces a button that types a
    command the agent does not have.
    """
    prefix = "/" if vendor == "claude" else "$"
    found: list[DiscoveredSkill] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        errors.append({"path": str(directory), "message": f"could not be read: {exc}"})
        return found
    for entry in entries:
        # A leading dot is how Codex hides its own bundled tree inside the user
        # root (`skills/.system`), which is scanned separately as its own scope.
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        manifest = entry / "SKILL.md"
        if not manifest.is_file():
            errors.append({"path": str(entry), "message": "directory has no SKILL.md"})
            continue
        try:
            head = _read_head(manifest)
            mtime = manifest.stat().st_mtime
        except OSError as exc:
            errors.append({"path": str(manifest), "message": f"could not be read: {exc}"})
            continue
        meta = parse_frontmatter(head)
        name = entry.name if vendor == "claude" else (meta.get("name") or entry.name)
        description = meta.get("description") or _body_summary(head)
        if not description:
            errors.append({"path": str(manifest), "message": "no description and no body text"})
        sidecar = _codex_agent_meta(entry) if vendor == "codex" else {}
        found.append(
            DiscoveredSkill(
                name=name,
                description=description,
                path=str(manifest),
                scope=scope,
                origin=origin,
                kind="skill",
                invocation=f"{prefix}{name}",
                mtime=mtime,
                implicit=sidecar.get("policy.allow_implicit_invocation", "true").strip().lower()
                != "false",
                display_name=sidecar.get("interface.display_name") or None,
                short_description=(
                    sidecar.get("interface.short_description")
                    or meta.get("metadata.short-description")
                    or None
                ),
            )
        )
    return found


def _scan_command_root(
    directory: Path,
    scope: str,
    origin: str,
    errors: list[dict[str, str]],
) -> list[DiscoveredSkill]:
    """Claude's `commands/*.md`, which it surfaces in the same list as skills.

    Nested files are namespaced with `:` the way Claude invokes them, so
    `commands/git/sync.md` is `/git:sync`.
    """
    found: list[DiscoveredSkill] = []
    try:
        files = sorted(directory.rglob("*.md"), key=lambda item: str(item).lower())
    except OSError as exc:
        errors.append({"path": str(directory), "message": f"could not be read: {exc}"})
        return found
    for path in files:
        try:
            parts = path.relative_to(directory).parts
        except ValueError:
            continue
        if len(parts) > MAX_COMMAND_DEPTH or any(part.startswith(".") for part in parts):
            continue
        try:
            head = _read_head(path)
            mtime = path.stat().st_mtime
        except OSError as exc:
            errors.append({"path": str(path), "message": f"could not be read: {exc}"})
            continue
        meta = parse_frontmatter(head)
        name = ":".join([*parts[:-1], path.stem])
        found.append(
            DiscoveredSkill(
                name=name,
                description=meta.get("description") or _body_summary(head),
                path=str(path),
                scope=scope,
                origin=origin,
                kind="command",
                invocation=f"/{name}",
                mtime=mtime,
            )
        )
    return found


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _enabled_plugins(claude_home: Path, cwd: Path) -> dict[str, bool]:
    """Merged `enabledPlugins`, user settings first so a Project can override."""
    merged: dict[str, bool] = {}
    for path in (
        claude_home / "settings.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
    ):
        entries = _read_json(path).get("enabledPlugins")
        if isinstance(entries, dict):
            merged.update({str(key): bool(value) for key, value in entries.items()})
    return merged


def _claude_plugin_roots(claude_home: Path, cwd: Path) -> tuple[list[tuple[Path, str]], list[str]]:
    """(scannable skill dirs, ids skipped because they are switched off)."""
    installed = _read_json(claude_home / "plugins" / "installed_plugins.json").get("plugins")
    if not isinstance(installed, dict):
        return [], []
    enabled = _enabled_plugins(claude_home, cwd)
    roots: list[tuple[Path, str]] = []
    skipped: list[str] = []
    for plugin_id, records in sorted(installed.items()):
        if not enabled.get(str(plugin_id)):
            skipped.append(str(plugin_id))
            continue
        for record in records if isinstance(records, list) else []:
            install_path = record.get("installPath") if isinstance(record, dict) else None
            if isinstance(install_path, str) and install_path:
                roots.append((Path(install_path) / "skills", str(plugin_id).split("@")[0]))
    return roots, skipped


def _codex_enabled_plugins(codex_home: Path) -> dict[str, bool]:
    """`[plugins."<name>@<marketplace>"] enabled` out of `config.toml`."""
    try:
        parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    section = parsed.get("plugins")
    if not isinstance(section, dict):
        return {}
    return {
        str(key): bool(value.get("enabled")) if isinstance(value, dict) else bool(value)
        for key, value in section.items()
    }


def _codex_plugin_roots(codex_home: Path) -> tuple[list[tuple[Path, str]], list[str]]:
    """(scannable skill dirs, ids present in the cache but not installed).

    The plugin cache is a download area, not an install list: Codex ships several
    marketplace plugins into it that no session ever loads (this machine cached
    seven, carrying 25 skills between them). Gating on `config.toml` is what keeps
    the drawer from advertising `$spreadsheet` to an agent that has never heard of
    it. Only the newest cached version of an enabled plugin is scanned.
    """
    cache = codex_home / "plugins" / "cache"
    enabled = _codex_enabled_plugins(codex_home)
    newest: dict[str, Path] = {}
    skipped: set[str] = set()
    try:
        # marketplace / plugin / version / skills
        candidates = sorted(cache.glob("*/*/*/skills"))
    except OSError:
        return [], []
    for path in candidates:
        if not path.is_dir():
            continue
        marketplace, plugin = path.parts[-4], path.parts[-3]
        plugin_id = f"{plugin}@{marketplace}"
        if not enabled.get(plugin_id):
            skipped.add(plugin_id)
            continue
        current = newest.get(plugin_id)
        if current is None or path.stat().st_mtime > current.stat().st_mtime:
            newest[plugin_id] = path
    return (
        [(path, plugin_id.split("@")[0]) for plugin_id, path in sorted(newest.items())],
        sorted(skipped),
    )


def _claude_inventory(cwd: Path, claude_home: Path, now: float) -> SkillInventory:
    inventory = SkillInventory(
        backend="claude", cwd=str(cwd), generated_at=now, builtin_skills_hidden=True
    )
    plugin_roots, skipped = _claude_plugin_roots(claude_home, cwd)
    inventory.skipped_plugins = skipped
    plan: list[tuple[Path, str, str, str]] = [
        (cwd / ".claude" / "skills", "project", "skill", "project skills"),
        (cwd / ".claude" / "commands", "project", "command", "project commands"),
        (claude_home / "skills", "user", "skill", "user skills"),
        (claude_home / "commands", "user", "command", "user commands"),
    ]
    plan.extend((path, "plugin", "skill", f"plugin: {label}") for path, label in plugin_roots)
    for directory, scope, kind, origin in plan:
        exists = directory.is_dir()
        found: list[DiscoveredSkill] = []
        if exists and kind == "command":
            found = _scan_command_root(directory, scope, origin, inventory.errors)
        elif exists:
            found = _scan_skill_root(directory, scope, origin, "claude", inventory.errors)
        inventory.roots.append(SkillRoot(str(directory), scope, kind, origin, exists, len(found)))
        inventory.skills.extend(found)
    return inventory


def _codex_inventory(cwd: Path, codex_home: Path, now: float) -> SkillInventory:
    inventory = SkillInventory(backend="codex", cwd=str(cwd), generated_at=now)
    plan: list[tuple[Path, str, str]] = [
        (cwd / ".codex" / "skills", "project", "project .codex/skills"),
        (cwd / ".agents" / "skills", "project", "project .agents/skills"),
        (codex_home / "skills", "user", "user skills"),
        (codex_home / "skills" / ".system", "system", "bundled skills"),
    ]
    plugin_roots, skipped = _codex_plugin_roots(codex_home)
    inventory.skipped_plugins = skipped
    plan.extend((path, "plugin", f"plugin: {label}") for path, label in plugin_roots)
    for directory, scope, origin in plan:
        exists = directory.is_dir()
        found: list[DiscoveredSkill] = []
        if exists:
            found = _scan_skill_root(directory, scope, origin, "codex", inventory.errors)
        inventory.roots.append(
            SkillRoot(str(directory), scope, "skill", origin, exists, len(found))
        )
        inventory.skills.extend(found)
    return inventory


def _resolve(inventory: SkillInventory) -> SkillInventory:
    """Order by scope, then mark name collisions and apply the ceiling."""
    inventory.skills.sort(key=lambda skill: (_SCOPE_RANK.get(skill.scope, 9), skill.name.lower()))
    winners: dict[str, DiscoveredSkill] = {}
    for skill in inventory.skills:
        key = f"{skill.kind}:{skill.name}"
        winner = winners.get(key)
        if winner is None:
            winners[key] = skill
        else:
            skill.shadowed_by = winner.origin
    if len(inventory.skills) > MAX_SKILLS:
        inventory.skills = inventory.skills[:MAX_SKILLS]
        inventory.truncated = True
    return inventory


_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}


def discover_skills(
    backend: str,
    cwd: Path | str,
    *,
    claude_home: Path | None = None,
    codex_home: Path | None = None,
    now: float | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Scan the roots `backend` reads from `cwd` and return a JSON-ready payload.

    Blocking (a few dozen stats and small reads); call it off the event loop.
    Results are cached per (backend, cwd, home) for `CACHE_TTL_SECONDS`, which
    collapses a drawer open into one scan without letting a stale answer outlive
    the click that follows it.
    """
    if backend not in {"claude", "codex"}:
        raise ValueError("skills are discoverable only for claude and codex sessions")
    moment = time.time() if now is None else now
    root = Path(cwd)
    home = (
        (claude_home or claude_data_home())
        if backend == "claude"
        else (codex_home or codex_data_home())
    )
    key = (backend, str(root), str(home))
    cached = _CACHE.get(key)
    if cached and not refresh and moment - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    inventory = (
        _claude_inventory(root, home, moment)
        if backend == "claude"
        else _codex_inventory(root, home, moment)
    )
    payload = _resolve(inventory).to_dict()
    _CACHE[key] = (moment, payload)
    return payload


def clear_cache() -> None:
    _CACHE.clear()
