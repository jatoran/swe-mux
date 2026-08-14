"""Which Projects one agent-facing call may reach.

The mux MCP surface defaults to the caller's own Project. Every tool also takes a
`project` argument that widens that default: `"fleet"` for every Project, or a
Project name or id for one other Project. Nothing widens implicitly - an agent
that wants the fleet has to say so, and the answer it gets back says which scope
produced it.

The resolution lives in its own module rather than in `mcp.py` because the bound
has to hold in the daemon operation as well as in the tool
(`CONTROL_PLANE_ROADMAP.md` §7.1). `agent_messaging` re-resolves the same argument
for writes instead of trusting the value the transport passed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Reserved `project` values. A Project genuinely named "fleet" or "self" is
#: still reachable by its id, which is what the refusal text points at.
FLEET = "fleet"
OWN = "self"

#: How many Project names an unknown-project refusal lists before it stops.
_MAX_NAMED = 20


class UnknownProject(ValueError):
    """The `project` argument named no Project this daemon knows."""


class AmbiguousProject(ValueError):
    """The `project` argument named more than one Project."""


@dataclass(frozen=True, slots=True)
class ProjectScope:
    """The Projects one call may read or write within.

    Exactly one of three shapes:

    - ``fleet`` - every Project, because the caller asked for it.
    - ``project_id`` - one registered Project (the caller's own by default).
    - ``scope_id`` only - an unregistered caller, matched on its git Project
      identity. This is the pre-existing fallback for sessions that belong to no
      registered Project, and it never widens.
    """

    fleet: bool
    project_id: str
    scope_id: str
    #: Human phrase for refusals and notes ("your Project", "every Project", a name).
    label: str
    #: The `project` argument that produced this scope, echoed back in results.
    requested: str

    @property
    def widened(self) -> bool:
        """True when the caller asked for anything beyond its own Project."""
        return self.requested != OWN

    def admits(self, project_id: str, scope_id: str = "") -> bool:
        if self.fleet:
            return True
        if self.project_id:
            return project_id == self.project_id
        return bool(self.scope_id) and scope_id == self.scope_id


def record_scope(record: Any) -> tuple[str, str]:
    """(project_id, project_scope_id) of a live session record."""
    return (
        str(getattr(record, "project_id", "") or ""),
        str(getattr(record, "project_scope_id", "") or ""),
    )


def row_scope(row: dict[str, Any]) -> tuple[str, str]:
    """(project_id, project_scope_id) of a history row."""
    return (
        str(row.get("project_id") or ""),
        str(row.get("project_scope_id") or ""),
    )


def own_scope(record: Any) -> ProjectScope:
    """The caller's own Project - the scope every tool uses by default."""
    project_id, scope_id = record_scope(record)
    return ProjectScope(
        fleet=False,
        project_id=project_id,
        scope_id=scope_id,
        label="your Project",
        requested=OWN,
    )


def fleet_scope() -> ProjectScope:
    return ProjectScope(
        fleet=True, project_id="", scope_id="", label="every Project", requested=FLEET
    )


def resolve_project_scope(raw: Any, caller_record: Any, projects: Any) -> ProjectScope:
    """Turn a tool's `project` argument into the scope it asked for.

    `projects` is the `ProjectService` (or anything exposing a `projects` mapping
    of id -> record with `id` and `name`). It may be absent on a minimally wired
    daemon, in which case only the caller's own Project and `"fleet"` resolve.
    """
    text = str(raw or "").strip()
    if not text or text.casefold() == OWN:
        return own_scope(caller_record)
    if text.casefold() == FLEET:
        return fleet_scope()
    project = _find_project(text, projects)
    return ProjectScope(
        fleet=False,
        project_id=str(project.id),
        scope_id="",
        label=str(project.name),
        requested=str(project.name),
    )


def _find_project(text: str, projects: Any) -> Any:
    known: dict[str, Any] = dict(getattr(projects, "projects", {}) or {})
    if not known:
        raise UnknownProject(
            f'unknown project "{text}": this daemon has no registered Projects. '
            f'Omit project for your own, or pass "{FLEET}" for every Project.'
        )
    exact = known.get(text)
    if exact is not None:
        return exact
    folded = text.casefold()
    matches = [
        project
        for project in known.values()
        if str(project.name).casefold() == folded
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        ids = ", ".join(sorted(str(project.id) for project in matches))
        raise AmbiguousProject(
            f'{len(matches)} Projects are named "{text}"; pass one of these '
            f"project ids instead: {ids}"
        )
    names = sorted(str(project.name) for project in known.values())
    listed = ", ".join(names[:_MAX_NAMED])
    if len(names) > _MAX_NAMED:
        listed += f", and {len(names) - _MAX_NAMED} more"
    raise UnknownProject(
        f'unknown project "{text}". Omit project for your own, pass "{FLEET}" '
        f"for every Project, or name one of: {listed}"
    )


def split_qualified_target(text: str) -> tuple[str, str]:
    """Split a `Project/session` target into its two halves.

    Returns ``("", text)`` when the text carries no Project qualifier. The split
    is on the last separator, so a session name may contain one; callers try the
    unqualified form first and only fall back to this.
    """
    for separator in ("/", "\\"):
        if separator in text:
            project, _, name = text.rpartition(separator)
            project = project.strip()
            name = name.strip()
            if project and name:
                return project, name
    return "", text.strip()
