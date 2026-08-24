"""The configurator agent: an ordinary harness session pointed at swe-mux itself.

A new operator meets swe-mux as a large surface with most of its interesting
parts switched off on purpose, and the only thing that can explain a specific
install to a specific person is something that can *read* that install. So the
configurator is not a help page. It is a real agent session, spawned into the
harness the operator already uses, that starts with a prompt naming this
machine's actual state and holds four gated tools for reading and changing it.

Three properties are what make it worth building rather than writing a manual.

**Everything structural is generated at read time.** The settings catalog is
derived from the ``Config`` dataclass and its *own validator*; the harness table
from the descriptor registry; the automation table from the enablement DAG; the
tool table from the MCP contract. Nothing here restates a fact that lives
somewhere else, because a hand-written mirror of a registry is a second registry,
and the one that drifts is always the copy. The per-field *constraints* are the
sharpest case: rather than transcribing "must be DEBUG, INFO, WARNING, or ERROR"
into a table, :func:`settings_catalog` asks ``_validate`` by handing it a value
that cannot be legal and keeping the sentence it objects with. The validator is
the authority for writes, so quoting it is the only description that cannot be
wrong.

**Prose ships as an asset, not as a repository path.** The guides live under
``assets/configurator/`` because that directory is in both distribution paths
(``pyproject.toml`` artifacts and ``packaging/swe_mux.spec`` datas), while
``.docs/`` is in neither. A prompt that told an agent to read
``.docs/design/features/ui.md`` would work on a maintainer's machine and fail
silently for every user of the frozen app - which is exactly the class of bug
this module must not have, because its whole audience is people running the
frozen app.

**Authority is explicit and asymmetric.** The reads are broad; the one write goes
through ``update_config``, the same call the Settings panel makes, so the
configurator can change nothing the panel could not and cannot bypass a single
validation. Editing swe-mux's *source* is a different thing entirely and is not a
tool at all: it needs a source checkout, it needs a rebuild the agent must not run
unattended, and one class of it reaps every live session. The seed prompt says all
of that in the section the agent reads before it starts, and
:func:`build_manifest` reports whether this install even has source to edit.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import platform
import re
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config as config_module
from .automation_registry import (
    RECOMMENDED_PROJECT_AUTOMATIONS,
    dependency_closure,
)
from .automation_registry import (
    REGISTRY as AUTOMATION_REGISTRY,
)
from .config import RESTART_FIELDS, Config
from .errors import NotFound
from .harness import HarnessInstallation, enabled_backends, public_harness_registry
from .mcp_contract import (
    CONFIGURATOR_READ_TOOL_NAMES,
    CONFIGURATOR_WRITE_TOOL_NAMES,
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
)

#: Shipped guide prose. Under ``assets/`` because that is what the wheel and the
#: PyInstaller bundle both carry; see the module docstring.
GUIDE_DIR = Path(__file__).with_name("assets") / "configurator"

#: Settings the daemon owns outright. ``update_config`` refuses them, so the
#: catalog marks them read-only rather than offering a write that always fails.
READ_ONLY_FIELDS = frozenset({"schema_version", "revision", "data_dir", "config_path"})

#: Fields whose *value* must never reach a transcript. Matched by name so a
#: credential added later is redacted by default rather than by remembering to
#: add it here; ``_SECRET_FIELDS`` covers any whose spelling misses the pattern.
#: A redacted row still reports whether something is *set*, because "is the key
#: configured" is the question a configurator actually needs.
#:
#: Anchored on whole singular words for a reason worth stating: a loose substring
#: match on "token" swallows every `*_max_output_tokens` ceiling in the config,
#: and redacting a budget is not a harmless excess of caution - it hides a number
#: the operator is asking about and tells the agent, falsely, that a limit is a
#: credential. Plurals (`_tokens`, `redact_secrets`) are therefore not matched.
#:
#: As of writing no ``Config`` field is a credential at all - swe-mux keeps its
#: secrets in the secret store, not here - so this redacts nothing today. It is
#: deliberately in place anyway: the failure it prevents is a credential field
#: being added later and reaching a transcript before anyone notices, and that is
#: not a mistake worth making once.
_SECRET_PATTERN = re.compile(
    r"(?:^|_)(?:token|secret|password|passphrase|credential|apikey)$|api_key",
    re.IGNORECASE,
)
_SECRET_FIELDS: frozenset[str] = frozenset()

# Sentinels chosen to be illegal for every validator that checks anything, and
# harmless to the ones that check nothing (those simply report no constraint).
# The NUL prefix keeps the string out of any legal enum, path, or font name.
_PROBE_STR = "\x00swe-mux-configurator-probe"
_PROBE_INT = -987654321
_PROBE_FLOAT = -987654.321


@dataclass(frozen=True, slots=True)
class Guide:
    """One shipped document, addressable by id through the ``guide`` tool."""

    id: str
    title: str
    summary: str

    @property
    def path(self) -> Path:
        return GUIDE_DIR / f"{self.id}.md"


#: The closed guide set, in reading order. Closed rather than a directory glob
#: for the same reason the MCP tool list is: a file that appears in the bundle
#: without a title and a summary is not discoverable, and one listed here without
#: a file is a dead link. ``tests/test_configurator.py`` holds the two in step.
GUIDES: tuple[Guide, ...] = (
    Guide(
        "orientation",
        "What swe-mux is, and why so much of it is switched off",
        "The mental model - Projects, sessions, panes, the drawer - and the "
        "deliberate rule that anything which costs money or acts on its own "
        "starts disabled.",
    ),
    Guide(
        "settings",
        "The three places a setting can live",
        "Install-wide daemon config, per-Project config, and per-device UI "
        "settings; which of the three a given control writes to, and which "
        "changes need a daemon restart.",
    ),
    Guide(
        "harnesses",
        "Harnesses, accounts, and the default harness",
        "Registering and enabling agent CLIs, saving provider logins, launch "
        "profiles, and what the default harness selects.",
    ),
    Guide(
        "rail-and-actions",
        "The command rail, and editing it",
        "Where the rail is stored and the profile trap that comes with it, the "
        "catalog/layout/override split, why an unqualified request means the "
        "global rail, and how to edit one safely.",
    ),
    Guide(
        "automations",
        "Automations, the enablement DAG, and spending",
        "Substrate versus consumers, why a consumer stays inert until its whole "
        "dependency closure is on, and which opt-ins can bill you.",
    ),
    Guide(
        "remote",
        "Reaching this install from another device",
        "Loopback by default, the tailnet path, and what the phone needs before "
        "push notifications and voice work.",
    ),
    Guide(
        "worktrees",
        "Worktrees, verification, and landing",
        "Parallel checkouts for parallel agents, the verification command's "
        "contract, and the land queue that runs it.",
    ),
    Guide(
        "diagnostics",
        "Symptom to evidence",
        "Which report answers which complaint, so a diagnosis reads a fact "
        "instead of guessing.",
    ),
    Guide(
        "modifying-swe-mux",
        "Changing swe-mux itself",
        "When source edits are possible at all, how a change actually reaches "
        "the running app, and the one kind of change that stops every live "
        "session.",
    ),
)

_GUIDES_BY_ID = {guide.id: guide for guide in GUIDES}


# --------------------------------------------------------------- install shape


def source_checkout() -> Path | None:
    """The swe-mux source repository this daemon is running from, if any.

    Only a ``src/`` layout checkout counts: an installed wheel and a frozen
    bundle both put the package somewhere that is not a repository, and offering
    to edit either would produce changes that are overwritten by the next
    install or that vanish into a bundle nobody can rebuild. Returning ``None``
    is the honest answer that lets the prompt say "code changes are not
    available on this install" instead of sending an agent to look for files.
    """
    if getattr(sys, "frozen", False):
        return None
    try:
        package = Path(__file__).resolve()
    except OSError:
        return None
    if package.parent.parent.name != "src":
        return None
    root = package.parent.parent.parent
    if not (root / "pyproject.toml").is_file():
        return None
    return root


def install_mode() -> str:
    """``source``, ``frozen``, or ``installed`` - which build is running.

    The three differ in exactly the way that matters to a configurator: a source
    daemon picks up an edit on restart, a frozen one respawns its own bundled
    copy and ignores the edit entirely, and an installed wheel has no source to
    edit at all. Every warning in the seed prompt keys off this.
    """
    if getattr(sys, "frozen", False):
        return "frozen"
    return "source" if source_checkout() is not None else "installed"


# ------------------------------------------------------------ settings catalog


def _json_safe(value: Any) -> Any:
    """A configuration value in a shape ``json.dumps`` accepts."""
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def is_secret_field(name: str) -> bool:
    return name in _SECRET_FIELDS or bool(_SECRET_PATTERN.search(name))


def _probe_for(current: Any) -> Any:
    """A value guaranteed to be rejected by any validator that checks this field."""
    if isinstance(current, bool):
        # `bool` before `int`: it is a subclass, and a numeric probe would read
        # as a legal truthy value rather than as the type error we want.
        return _PROBE_STR
    if isinstance(current, int):
        return _PROBE_INT
    if isinstance(current, float):
        return _PROBE_FLOAT
    return _PROBE_STR


def _validation_candidate(config: Config) -> Config:
    """A detached copy of ``config`` safe to hand to ``_validate`` repeatedly.

    Built exactly the way ``update_config`` builds its candidate, including the
    two fields ``asdict`` flattens into shapes the validator's tail cannot read
    (``data_dir`` becomes a plain value, ``shell_profiles`` become dicts). A copy
    assembled any other way validates differently from a real save, which would
    make the reported constraints subtly untrue.
    """
    candidate = Config(**{**dataclasses.asdict(config), "config_path": config.config_path})
    candidate.data_dir = config.data_dir
    candidate.shell_profiles = list(config.shell_profiles)
    return candidate


def _constraint_for(candidate: Config, name: str) -> str | None:
    """What ``_validate`` says when ``name`` holds a value it cannot accept.

    The validator is what actually decides a write, so its own sentence is the
    only constraint description that cannot drift from the rule it describes. A
    field with no check answers ``None`` - correctly, because there is nothing to
    say beyond its type. A validator that raises on the probe rather than
    collecting an error (a type-unguarded comparison) also answers ``None``:
    losing one row's hint is the right trade against a catalog that cannot be
    built at all.
    """
    original = getattr(candidate, name)
    try:
        setattr(candidate, name, _probe_for(original))
    except (AttributeError, TypeError, ValueError):
        return None
    try:
        config_module._validate(candidate)
    except ValueError as exc:
        detail = exc.args[0] if exc.args else {}
        message = detail.get(name) if isinstance(detail, dict) else None
        return str(message) if message else None
    except Exception:  # noqa: BLE001 - an unguarded comparison costs this row only
        return None
    finally:
        setattr(candidate, name, original)
    return None


def settings_catalog(config: Config) -> list[dict[str, Any]]:
    """Every install-wide setting, with its current value and its real constraint.

    Derived wholly from the ``Config`` dataclass and its validator: the field
    list, the defaults, the read-only set, the restart set, and the constraint
    sentences all come from the code that enforces them. Adding a setting to
    ``Config`` therefore adds it here, and no separate registration exists to
    forget.

    One ``_validate`` pass per field makes this tens of milliseconds rather than
    microseconds. That is deliberate and it is the right trade: this is read on
    demand by an agent that is about to change something, not on a hot path, and
    the alternative is a hand-maintained table that is wrong.
    """
    defaults = Config(data_dir=config.data_dir)
    candidate = _validation_candidate(config)
    rows: list[dict[str, Any]] = []
    for name, field in Config.__dataclass_fields__.items():
        secret = is_secret_field(name)
        current = getattr(config, name)
        row: dict[str, Any] = {
            "name": name,
            "type": str(field.type),
            "writable": name not in READ_ONLY_FIELDS,
            "restart_required": name in RESTART_FIELDS,
            "secret": secret,
        }
        if secret:
            # Never the value. "Configured or not" is the whole of what a
            # configurator needs, and a transcript is a durable artifact.
            row["current"] = "<set>" if current else "<unset>"
        else:
            row["current"] = _json_safe(current)
            row["default"] = _json_safe(getattr(defaults, name))
        if row["writable"]:
            constraint = _constraint_for(candidate, name)
            if constraint:
                row["constraint"] = constraint
        rows.append(row)
    return rows


# ------------------------------------------------------------- other registries


def harness_catalog(
    config: Config, installations: dict[str, HarnessInstallation] | None = None
) -> list[dict[str, Any]]:
    """Registered agent harnesses, with this machine's detection folded in.

    Built over :func:`public_harness_registry` rather than over the descriptors
    directly. That projection already answers every per-harness capability
    question - including the ones whose answer is a property of one vendor's
    adapter, like whether an MCP server can be registered at all - and rederiving
    those here would mean naming a harness in a conditional, which is exactly the
    drift `tests/test_harness_name_literals.py` exists to refuse.

    ``enabled`` is the launcher's own three-state answer (explicit choice, else
    detection), so the list agrees with what the Run menu will actually offer
    rather than with what is merely registered.
    """
    available = set(enabled_backends(config.harness_enabled, config.harness_exe))
    registry = public_harness_registry(installations)
    entries = registry["harnesses"]
    assert isinstance(entries, list)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        name = str(entry["name"])
        capabilities = entry.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, dict) else {}
        rows.append(
            {
                "name": name,
                "display_name": entry.get("display_name"),
                "level": entry.get("level"),
                "cli": entry.get("cli_name"),
                # `None` when detection was not supplied, which is a different
                # answer from "not installed" and must not render as one.
                "installed": entry.get("installed"),
                "resolved_path": entry.get("resolved_path"),
                "cli_version": entry.get("cli_version"),
                "enabled": name in available,
                "explicitly_set": name in config.harness_enabled,
                "executable_override": config.harness_exe.get(name, ""),
                "provider_accounts": capabilities.get("provider_accounts"),
                "mcp_capable": capabilities.get("mcp"),
                "mcp_enabled": config.harness_mcp_enabled.get(name, True),
            }
        )
    return rows


def automation_catalog() -> list[dict[str, Any]]:
    """The enablement DAG, with each entry's full transitive requirement set.

    ``requires`` is the declared edge and ``closure`` is what must actually be on
    before the entry does anything. Reporting only the former is how an operator
    switches on a consumer, sees nothing happen, and concludes the feature is
    broken - the closure is the answer to that question, so it travels with the
    row rather than being something a reader has to compute.
    """
    return [
        {
            "id": automation.id,
            "kind": automation.kind,
            "label": automation.label,
            "requires": list(automation.requires),
            "closure": sorted(dependency_closure(automation.id)),
            "implemented": automation.implemented,
            "spends": automation.spends,
            "needs_llm": automation.needs_llm,
            "recommended": automation.id in RECOMMENDED_PROJECT_AUTOMATIONS,
        }
        for automation in AUTOMATION_REGISTRY.values()
    ]


def project_settings_catalog() -> dict[str, Any]:
    """What a Project's own committed `.swe-mux/config.toml` may and may not say.

    The second place a setting can live, and the one most likely to be reached
    for wrongly: it is *committed*, so it is shared with everyone who clones the
    repository, which is exactly why some fields are refused outright. Reporting
    the forbidden set alongside the allowed one is the point - "unknown project
    fields: token" read as a typo when it is in fact a boundary.
    """
    from .project_files import (
        FORBIDDEN_PROJECT_FIELDS,
        PROJECT_CONFIG_FIELDS,
        PROJECT_CONFIG_VERSION,
    )

    return {
        "version": PROJECT_CONFIG_VERSION,
        "path": ".swe-mux/config.toml",
        "fields": sorted(PROJECT_CONFIG_FIELDS),
        "forbidden": sorted(FORBIDDEN_PROJECT_FIELDS),
        "note": (
            "Committed and shared with every clone. The forbidden fields are "
            "refused rather than ignored: a repository must not be able to set "
            "this daemon's bind address, token, or the command a harness runs."
        ),
    }


def mcp_catalog() -> dict[str, list[str]]:
    """The MCP surface, split the way authority is actually split."""
    return {
        "read": list(READ_TOOL_NAMES),
        "write": list(WRITE_TOOL_NAMES),
        "configurator_read": list(CONFIGURATOR_READ_TOOL_NAMES),
        "configurator_write": list(CONFIGURATOR_WRITE_TOOL_NAMES),
    }


def guide_index() -> list[dict[str, str]]:
    return [
        {"id": guide.id, "title": guide.title, "summary": guide.summary} for guide in GUIDES
    ]


def read_guide(guide_id: str) -> str:
    """One shipped guide's text, or raise ``KeyError`` naming what exists.

    A missing *file* is reported as the packaging fault it is rather than as an
    empty document: a guide silently reading blank in the frozen app and fine
    from source is precisely the failure this module is built to avoid.
    """
    guide = _GUIDES_BY_ID.get(str(guide_id).strip())
    if guide is None:
        # The catalog belongs in the body here, unlike the id the caller sent:
        # it is a fixed list of what this build ships, and it is the only thing
        # that turns "no such guide" into a next step.
        raise NotFound(
            guide_id,
            kind="guide",
            message="unknown guide; available: " + ", ".join(entry.id for entry in GUIDES),
        )
    try:
        return guide.path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"guide {guide.id!r} is listed but its file is missing from this "
            f"build ({guide.path}); this is a packaging fault, not a missing topic"
        ) from exc


# ------------------------------------------------------------------- manifest


#: Sections `build_manifest` can answer, and the default set.
#:
#: The default omits `settings`, which is 45 KB of the manifest's 56 KB. A first
#: call almost never wants all 197 rows - it wants to know what kind of thing it
#: is looking at - and paying eleven thousand tokens to find that out, on every
#: turn of a tool loop, is the difference between a cheap question and an
#: expensive one (measured 2026-08-24: 61k input tokens for a question whose
#: answer was twelve strings). `settings` is reachable by naming it, and
#: `settings_query` narrows it further.
MANIFEST_SECTIONS = (
    "install",
    "settings",
    "project_settings",
    "harnesses",
    "automations",
    "mcp_tools",
    "guides",
    "projects",
)
DEFAULT_MANIFEST_SECTIONS = (
    "install",
    "harnesses",
    "automations",
    "mcp_tools",
    "guides",
    "projects",
)


def build_manifest(
    config: Config,
    *,
    installations: dict[str, HarnessInstallation] | None = None,
    projects: list[dict[str, Any]] | None = None,
    version: str = "",
    session: dict[str, Any] | None = None,
    sections: Sequence[str] = DEFAULT_MANIFEST_SECTIONS,
    settings_query: str = "",
) -> dict[str, Any]:
    """Everything structural the configurator needs to reason about this install.

    Assembled from live registries on every call rather than cached: it is read
    a handful of times per session, and a cached manifest is a manifest that
    disagrees with the settings the agent just changed.

    ``session`` is where the caller is *standing*, and it is not a convenience.
    A configurator launched into somebody else's Project can read this whole
    install and has no way to tell which of twenty-four Projects it is in, so
    every per-Project fact it meets - a rail override, an automation opt-in -
    reads as "this one" when it is the only one present. That is not a
    hypothetical: it produced a confident, wrong warning about another Project's
    rail button on 2026-08-24.
    """
    wanted = [name for name in MANIFEST_SECTIONS if name in set(sections)]
    unknown = sorted(set(sections) - set(MANIFEST_SECTIONS))
    if unknown:
        raise ValueError(
            f"unknown manifest section(s): {', '.join(unknown)}; "
            f"available: {', '.join(MANIFEST_SECTIONS)}"
        )
    if not wanted:
        raise ValueError(f"name at least one of: {', '.join(MANIFEST_SECTIONS)}")
    checkout = source_checkout()
    listed = projects if projects is not None else []

    def settings_section() -> list[dict[str, Any]]:
        rows = settings_catalog(config)
        needle = settings_query.strip().casefold()
        return [row for row in rows if needle in row["name"].casefold()] if needle else rows

    builders: dict[str, Any] = {
        "install": lambda: {
            "mode": install_mode(),
            "source_checkout": str(checkout) if checkout else None,
            "version": version,
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python": platform.python_version(),
            "data_dir": str(config.data_dir),
            # None rather than the string "None": an agent told the config lives
            # at a path called None will go looking for it.
            "config_path": str(config.config_path) if config.config_path else None,
            "host": config.host,
            "port": config.port,
            "config_revision": config.revision,
            "session": session or {},
        },
        "settings": settings_section,
        "project_settings": project_settings_catalog,
        "harnesses": lambda: harness_catalog(config, installations),
        "automations": automation_catalog,
        "mcp_tools": mcp_catalog,
        "guides": guide_index,
        "projects": lambda: [
            {**entry, "is_this_session_project": bool(
                session and entry.get("id") == session.get("project_id")
            )}
            for entry in listed
        ],
    }
    manifest = {name: builders[name]() for name in wanted}
    manifest["sections"] = {
        "returned": wanted,
        "available": list(MANIFEST_SECTIONS),
        "omitted": [name for name in MANIFEST_SECTIONS if name not in wanted],
        "note": (
            "Ask for `settings` when you need a setting's current value, default, "
            "or accepted values, and pass `settings_query` to narrow it - the full "
            "catalog is 197 rows."
        ),
    }
    return manifest


# ------------------------------------------------------------- device settings


#: Where the command rail actually lives. Both device layouts are inside **one**
#: blob under the **desktop** profile - a deliberate frontend decision
#: (`deviceSettings.ts`, `RAIL_PROFILE`): the catalog of commands is shared while
#: the arrangements are not, so splitting the layouts across the store's two
#: profile buckets would make a save two writes with a window where one device's
#: layout names a command the catalog has not got yet.
#:
#: It is stated here, loudly, because it is the trap a second editor walks into
#: without ever being told it did: "edit the mobile rail" reads as "write the
#: mobile profile", and a `commandRail` document under `profiles.mobile` is
#: valid, stored, and read by nothing.
RAIL_PROFILE = "desktop"
RAIL_DOMAIN = "commandRail"

_RAIL_STORAGE_NOTE = (
    "The command rail lives in ONE document, under the `desktop` profile, and "
    "carries both device layouts inside it (`layouts.desktop` and "
    "`layouts.mobile`). Editing the mobile rail means editing "
    "`profile=desktop domain=commandRail` at a path under `/layouts/mobile`. A "
    "`commandRail` document written under the `mobile` profile is stored and "
    "read by nothing."
)


def _rail_row_view(
    row: Any, labels: dict[str, str], pointer: str
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for raw in row.get("items") or []:
        item_id = raw if isinstance(raw, str) else str((raw or {}).get("id") or "")
        entry: dict[str, Any] = {"id": item_id}
        if item_id in labels:
            entry["label"] = labels[item_id]
        entries.append(entry)
    return {
        "row_id": str(row.get("id") or ""),
        "items": entries,
        # The exact pointer an edit would name, so the agent does not compose one
        # from a shape it inferred. Selector form rather than an index: a row
        # named by its own id cannot be reordered out from under the write.
        "items_path": f"{pointer}/[id={row.get('id')}]/items",
    }


def rail_projection(
    document: Any, *, project_names: dict[str, str], session_project_id: str = ""
) -> dict[str, Any]:
    """A legible read of the rail blob, resolved against the Project registry.

    Purely derived, and it never writes: the blob's schema belongs to the browser
    and this is a *reading* of it, so anything it cannot make sense of is reported
    as unreadable rather than guessed at, and a write still goes through
    path-scoped operations that need no schema at all.

    Two things it supplies that a raw file read cannot, and both are why it
    exists. Catalog **labels**, so a row is a list of names rather than of
    identifiers. And every project override resolved to its **Project name**,
    with the caller's own Project marked - a blob keyed by bare UUIDs invites the
    reader to assume the one override it can see belongs to wherever it is
    standing, which is exactly the mistake that produced a confident, wrong
    warning about another Project's button (2026-08-24).
    """
    if not isinstance(document, dict):
        return {"readable": False, "reason": "the stored rail is not an object"}
    labels = {
        str(item.get("id") or ""): str(item.get("label") or "")
        for item in (document.get("items") or [])
        if isinstance(item, dict) and item.get("label")
    }
    layouts: dict[str, Any] = {}
    raw_layouts = document.get("layouts")
    if isinstance(raw_layouts, dict):
        for device, surfaces in raw_layouts.items():
            if not isinstance(surfaces, dict):
                continue
            device_view: dict[str, Any] = {}
            for surface, rows in surfaces.items():
                if not isinstance(rows, list):
                    continue
                pointer = f"/layouts/{device}/{surface}"
                device_view[str(surface)] = [
                    _rail_row_view(row, labels, pointer) for row in rows if isinstance(row, dict)
                ]
            layouts[str(device)] = device_view

    overrides: list[dict[str, Any]] = []
    raw_projects = document.get("projects")
    if isinstance(raw_projects, dict):
        for project_id, scope in raw_projects.items():
            mode = str((scope or {}).get("mode") or "fork") if isinstance(scope, dict) else "?"
            added = [
                {"id": str(item.get("id") or ""), "label": str(item.get("label") or "")}
                for item in ((scope or {}).get("items") or [])
                if isinstance(scope, dict) and isinstance(item, dict)
            ]
            overrides.append(
                {
                    "project_id": project_id,
                    "project_name": project_names.get(project_id, "<not a registered Project>"),
                    "is_this_session_project": bool(
                        session_project_id and project_id == session_project_id
                    ),
                    "mode": mode,
                    "adds_items": added,
                    "path": f"/projects/{project_id}",
                }
            )

    return {
        "readable": True,
        "version": document.get("version"),
        "storage": {
            "profile": RAIL_PROFILE,
            "domain": RAIL_DOMAIN,
            "note": _RAIL_STORAGE_NOTE,
        },
        "catalog_items": len(document.get("items") or []),
        "layouts": layouts,
        "project_overrides": overrides,
        "this_session_has_an_override": any(
            entry["is_this_session_project"] for entry in overrides
        ),
        "scope_rule": (
            "The rail an operator sees in most Projects is the GLOBAL one, under "
            "`/layouts`. Edit that unless they have said the change is for one "
            "Project. A project override under `/projects/<id>` belongs to the "
            "Project named beside it and to no other - never read one as 'this "
            "Project's' because it is the only one present."
        ),
    }


def device_settings_view(
    store: Any,
    *,
    project_names: dict[str, str] | None = None,
    session_project_id: str = "",
    profile: str = "",
    domain: str = "",
) -> dict[str, Any]:
    """The per-device UI settings, with the rail resolved into something readable.

    The third settings location (`settings.md`), and until now the one the
    configurator could see only by finding and parsing `~/.mux/settings.json`
    itself - 195 KB of transcript to answer a question about twelve strings,
    with the schema reverse-engineered per session and no way to resolve a
    Project id to a name.

    With no arguments it answers the index: which profiles hold which domains,
    which of them the daemon interprets, and how large each is. That is the shape
    a first call should be, because most questions are answered by *which* domain
    to look at, and pulling every document to find out costs the caller the thing
    this tool exists to save.
    """
    from .settings_store import DOMAINS, INTERPRETED_DOMAINS, PROFILES

    names = project_names or {}
    if profile and profile not in PROFILES:
        raise ValueError(f"profile must be one of {', '.join(PROFILES)}")
    if domain and domain not in DOMAINS:
        raise ValueError(f"domain must be one of {', '.join(DOMAINS)}")

    index: dict[str, Any] = {}
    for name in PROFILES:
        stored = store.all()["profiles"].get(name) or {}
        index[name] = {
            key: {
                "present": key in stored,
                "interpreted": key in INTERPRETED_DOMAINS,
                "bytes": len(json.dumps(stored.get(key) or {})),
            }
            for key in DOMAINS
        }

    result: dict[str, Any] = {
        "profiles": list(PROFILES),
        "domains": list(DOMAINS),
        "interpreted_domains": list(INTERPRETED_DOMAINS),
        "index": index,
        "rail_storage": {
            "profile": RAIL_PROFILE,
            "domain": RAIL_DOMAIN,
            "note": _RAIL_STORAGE_NOTE,
        },
        "note": (
            "A domain the daemon does not interpret is stored verbatim: a "
            "malformed write is kept, not refused, and the browser normalizes "
            "whatever it finds. Edit them with path-scoped operations "
            "(`configurator_edit_device_settings`), never by resending a whole "
            "document, and read the result back."
        ),
    }
    if not domain:
        return result

    target_profile = profile or (RAIL_PROFILE if domain == RAIL_DOMAIN else "desktop")
    entry = store.domain(target_profile, domain)
    result["requested"] = entry
    if domain == RAIL_DOMAIN:
        result["rail"] = rail_projection(
            entry["document"], project_names=names, session_project_id=session_project_id
        )
    return result


# --------------------------------------------------------------------- service


class ConfiguratorService:
    """What the configurator MCP tools call, with the HTTP layer injected out.

    The daemon's diagnostics and its validated settings write both need pieces of
    the aiohttp application, and MCP is a transport over the daemon's own
    operations rather than a second implementation of them - the same reasoning
    that made ``action_runner`` a callable rather than an application handle. So
    the two are passed in as awaitables and this module stays importable, and
    testable, with no server running.

    ``installations`` is separate from the harness registry it feeds because
    detection shells out to probe CLI versions: it is handed in as a plain
    callable and run off the loop here, so a capabilities read never blocks the
    event loop on a subprocess.
    """

    def __init__(
        self,
        *,
        config: Config,
        projects: Callable[[], list[dict[str, Any]]],
        installations: Callable[[], dict[str, HarnessInstallation]],
        diagnostics: Callable[[], Awaitable[dict[str, Any]]],
        apply_settings: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        # A callable rather than the store itself: this service is constructed
        # while the daemon's runtime is still being assembled, and resolving the
        # store eagerly binds to whatever exists at that instant. Every other
        # dependency here is late-bound for the same reason.
        settings_store: Callable[[], Any] | None = None,
        edit_device_settings: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        read_project_settings: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
        apply_project_settings: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        version: str = "",
    ) -> None:
        self._config = config
        self._projects = projects
        self._installations = installations
        self._diagnostics = diagnostics
        self._apply_settings = apply_settings
        # The device-settings store is read directly (it is a plain file store
        # with no HTTP layer behind it) while the *write* is injected, because a
        # write has to emit the event that makes every attached browser refetch -
        # an agent-applied rail change that does not repaint reads as a change
        # that did not happen.
        self._settings_store = settings_store
        self._edit_device_settings = edit_device_settings
        self._read_project_settings = read_project_settings
        self._apply_project_settings = apply_project_settings
        self.version = version

    def _project_names(self) -> dict[str, str]:
        return {str(entry.get("id")): str(entry.get("name")) for entry in self._projects()}

    async def capabilities(
        self,
        *,
        session: dict[str, Any] | None = None,
        sections: Sequence[str] = DEFAULT_MANIFEST_SECTIONS,
        settings_query: str = "",
    ) -> dict[str, Any]:
        installations = await asyncio.to_thread(self._installations)
        return await asyncio.to_thread(
            build_manifest,
            self._config,
            installations=installations,
            projects=self._projects(),
            version=self.version,
            session=session,
            sections=sections,
            settings_query=settings_query,
        )

    async def diagnostics(self) -> dict[str, Any]:
        return await self._diagnostics()

    async def apply_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        return await self._apply_settings(changes)

    async def device_settings(
        self,
        *,
        profile: str = "",
        domain: str = "",
        session_project_id: str = "",
    ) -> dict[str, Any]:
        if self._settings_store is None:
            raise RuntimeError("device settings are not available on this daemon")
        return await asyncio.to_thread(
            device_settings_view,
            self._settings_store(),
            project_names=self._project_names(),
            session_project_id=session_project_id,
            profile=profile,
            domain=domain,
        )

    async def edit_device_settings(
        self, *, profile: str, domain: str, operations: Any, expect_digest: str = ""
    ) -> dict[str, Any]:
        if self._edit_device_settings is None:
            raise RuntimeError("device settings are not writable on this daemon")
        return await self._edit_device_settings(
            profile=profile, domain=domain, operations=operations, expect_digest=expect_digest
        )

    async def project_settings(self, project: str = "") -> dict[str, Any]:
        if self._read_project_settings is None:
            raise RuntimeError("project settings are not available on this daemon")
        return await self._read_project_settings(project)

    async def apply_project_settings(
        self, *, project: str, changes: dict[str, Any]
    ) -> dict[str, Any]:
        if self._apply_project_settings is None:
            raise RuntimeError("project settings are not writable on this daemon")
        return await self._apply_project_settings(project=project, changes=changes)


# --------------------------------------------------------------- seed prompt


def _harness_line(row: dict[str, Any]) -> str:
    state = "enabled" if row["enabled"] else "not enabled"
    if row["installed"] is False:
        state += ", CLI not detected"
    elif row["installed"] is True:
        state += ", CLI detected"
    return f"- {row['name']} ({row['display_name']}): {state}"


def compose_seed_prompt(
    config: Config,
    *,
    harness: str,
    cwd: str,
    installations: dict[str, HarnessInstallation] | None = None,
    projects: list[dict[str, Any]] | None = None,
    doctor_summary: str = "",
    version: str = "",
    project_name: str = "",
    project_id: str = "",
) -> str:
    """The message the configurator session runs as its first turn.

    Deliberately short next to the material it introduces. Everything durable is
    in the guides and everything structural is in ``capabilities``; putting either
    inline would spend the session's first turn on text it can fetch on demand,
    and would freeze a copy of both into a transcript that outlives them. What
    the prompt carries instead is the part no tool call can supply: who the agent
    is talking to, what authority it holds, and the handful of *this-machine*
    facts that decide whether its very first suggestion is even applicable.
    """
    mode = install_mode()
    checkout = source_checkout()
    harnesses = harness_catalog(config, installations)
    project_count = len(projects or [])
    known_harnesses = "\n".join(_harness_line(row) for row in harnesses)

    if mode == "source":
        code_paragraph = (
            f"This daemon runs from source at `{checkout}`, so you *can* edit swe-mux "
            "itself. Read the `modifying-swe-mux` guide before you do - a source edit "
            "does not reach the running app on its own, and one category of change "
            "stops every live session. Never rebuild, redeploy, or restart anything "
            "without saying what it will cost and getting a yes."
        )
    elif mode == "frozen":
        code_paragraph = (
            "This is the frozen desktop app. There is no source checkout here, so you "
            "cannot change swe-mux's own code from this session, and an edit to any "
            "file that looks like swe-mux source would not be what this app runs. Say "
            "so plainly if the operator asks for a code change; the `modifying-swe-mux` "
            "guide describes what they would need instead."
        )
    else:
        code_paragraph = (
            "swe-mux is installed as a package here rather than as a source checkout, "
            "so there is no code for you to edit - an installed copy is replaced by "
            "the next install, not amended. Settings and configuration are fully "
            "available."
        )

    return f"""You are the swe-mux configurator for this install.

A human just pressed a button in swe-mux asking for help configuring, understanding,
or diagnosing it. You are running as a `{harness}` session inside swe-mux itself, in
`{cwd}`. Your job is that conversation - not a task in their codebase.

## Settings live in three places, and you can write all three

Getting this wrong is the most common way to change the right value somewhere it
does nothing. Every one of these has its own tool, and the `settings` guide covers
the split.

1. **Install-wide daemon config** (`config.toml`) - ports, themes, terminal
   behaviour, harnesses, budgets, voice. Read with `configurator_capabilities`
   (`sections: ["settings"]`, `settings_query` to narrow), write with
   `configurator_apply_settings`.
2. **Per-device UI settings** (`settings.json`) - the command rail, sounds,
   alerts, drawer tabs, sidebar rows, the file tree. Read with
   `configurator_device_settings`, write with `configurator_edit_device_settings`.
3. **Per-Project config** (`.swe-mux/config.toml`, committed to that repository) -
   automation opt-ins, agent authority grants, worktree commands. Read with
   `configurator_project_settings`, write with `configurator_apply_project_settings`.

## Your other tools

- `configurator_capabilities` - this install's generated inventory: the harness
  registry with live detection, the automation dependency graph, the MCP surface,
  and - when you ask for the `settings` section - every setting with its current
  value, default, and the constraint its own validator enforces. Read it instead
  of guessing what a setting is called. It omits the 197-row settings catalog by
  default; name the section when you need it.
- `configurator_guide` - the shipped guides. Call it with no argument for the
  index, then with an `id` for the text. They explain the design decisions behind
  what you will see, including why so much starts switched off. Read the guide for
  a surface before editing it.
- `configurator_diagnostics` - the health report and prerequisite checks.

## This machine, right now

- Install mode: {mode}
- Version: {version or "unknown"}
- Config file: {config.config_path}
- Data directory: {config.data_dir}
- Projects registered: {project_count}
- Default harness setting: {config.default_harness or "(unset - resolved by detection)"}

**You are standing in the Project `{project_name or "(unknown)"}`**, id
`{project_id or "(unknown)"}`. That matters more than it looks: this install has
{project_count} Projects and most per-Project settings you will meet belong to one of
the others. A per-Project override is *this* Project's only when its id matches the
one above - never because it is the only one you can see.

Harnesses:
{known_harnesses}

{doctor_summary or "Health report: call `configurator_diagnostics` for the current one."}

## How to behave

**Ask before you change anything.** Name the setting, its current value, the value
you propose, and what will visibly differ. Some settings apply immediately and some
need a daemon restart; `configurator_capabilities` marks which, and you must say
which one you are about to cause. Never restart the daemon yourself without being
asked to.

**Do the thing they asked for.** They pressed a button that opens an agent, not a
manual. When they say "remove these four buttons", the answer is to propose the exact
edit and make it once they agree - not to describe where the editor is. Name the
control *as well*, because they will want to change it again, but do not use it as a
substitute for doing the work.

**Global unless they said otherwise.** Most settings have a shared scope and a
narrower one - a global command rail and a per-Project override, an install-wide
switch and a per-Project opt-in. An unqualified request means the shared one. Only
reach for the narrow scope when they named a Project, or when the thing they are
changing exists only there.

**Edit device settings with path-scoped operations, never a whole document.** The
daemon cannot validate a rail or a sound map - the browser owns those schemas - so
an operation that names what to change is the only kind that cannot lose what it did
not mean to touch. Read the domain, pass back the `digest` you read, and read the
result afterwards.

**Explain what is off on purpose.** Nearly every analysis and automation surface in
swe-mux ships disabled, per Project, and an empty panel is usually that rather than a
bug. Check the automation graph before agreeing that something is broken.

**Never expose a secret.** The settings catalog redacts credentials to `<set>` or
`<unset>` and you should keep it that way; this conversation is written to a
transcript that outlives it.

{code_paragraph}

## Start here

Greet them in a sentence, say what you can do, and ask what they want. Do not dump
the inventory at them. If they have no idea what to ask, the useful opening offers are:
a health check, a walk through what is switched off and what it would cost to switch
on, rearranging the command rail, or setting up remote access from a phone."""
