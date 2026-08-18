from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from pathlib import Path

from .harness import is_agent_harness
from .history import HistoryIndex
from .layouts import normalize_layout
from .models import ProjectGroupRecord, ProjectRecord
from .path_identity import same_path
from .project_files import DEFAULT_NOTE_STORAGE_ID, note_header

log = logging.getLogger(__name__)


class ProjectRegistrationResult:
    def __init__(self, project: ProjectRecord, *, restored: bool) -> None:
        self.project = project
        self.restored = restored


def canonical_project_root(value: str | Path, *, create: bool = False) -> Path:
    """Resolve a Project root, optionally creating the folder itself.

    ``create`` makes exactly one directory: the parent must already exist. A typo in a
    deep path is then a clear error instead of a silently materialized tree, and the
    folder picker's flow (choose an existing parent, name the new folder) is the only
    shape the create path has to support. An already-existing folder is accepted as-is,
    so retrying a create that half-succeeded is not an error.
    """
    root = Path(value).expanduser().resolve()
    if create and not root.exists():
        parent = root.parent
        if parent == root:
            raise ValueError(f"cannot create a project folder at a filesystem root: {root}")
        if not parent.is_dir():
            raise ValueError(f"parent folder does not exist: {parent}")
        root.mkdir()
    if not root.exists():
        raise ValueError(f"project folder does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"project root must be a folder: {root}")
    return root


def project_note_template(name: str) -> str:
    """Seed a Project's initial note with a heading and room to start typing.

    An empty file gives the editor no anchor; a heading plus trailing blank lines
    puts the caret where a person actually writes. Existing notes are never
    rewritten, so this only ever applies at first initialization.
    """
    label = " ".join(name.split()) or "Project"
    return f"# {label} notes\n\n\n"


def initialize_project_files(root: Path, name: str | None = None) -> None:
    mux_dir = root / ".swe-mux"
    notes_dir = mux_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    config = mux_dir / "config.toml"
    if not config.exists():
        config.write_text("version = 1\n", encoding="utf-8")
    note = notes_dir / "project.md"
    if not note.exists():
        label = name or root.name
        note.write_text(
            note_header(DEFAULT_NOTE_STORAGE_ID, f"{label} notes")
            + project_note_template(label),
            encoding="utf-8",
            newline="\n",
        )


class ProjectManager:
    def __init__(self, history: HistoryIndex) -> None:
        self.history = history
        self.projects: dict[str, ProjectRecord] = {}
        self.groups: dict[str, ProjectGroupRecord] = {}
        self._order_lock = asyncio.Lock()
        self._project_write_lock = asyncio.Lock()

    def ordered_projects(self) -> list[ProjectRecord]:
        return sorted(self.projects.values(), key=lambda item: (item.position, item.name, item.id))

    def ordered_groups(self) -> list[ProjectGroupRecord]:
        return sorted(self.groups.values(), key=lambda item: (item.position, item.name, item.id))

    async def start(self) -> None:
        self.projects = {item.id: item for item in await self.history.list_projects()}
        self.groups = {item.id: item for item in await self.history.list_project_groups()}
        changed: list[ProjectRecord] = []
        for project in self.projects.values():
            normalized = normalize_layout(project.layout)
            if project.layout != normalized:
                project.layout = normalized
                changed.append(project)
        for project in changed:
            await self.history.upsert_project(project)
        ordered = self.ordered_projects()
        if [item.position for item in ordered] != list(range(len(ordered))):
            await self._apply_order([item.id for item in ordered])
        groups = self.ordered_groups()
        if [item.position for item in groups] != list(range(len(groups))):
            await self._apply_group_order([item.id for item in groups])

    async def create(
        self,
        name: str,
        root: str,
        *,
        group_id: str | None = None,
        create_missing: bool = False,
    ) -> ProjectRecord:
        return (
            await self.register(
                name,
                root,
                group_id=group_id,
                create_missing=create_missing,
            )
        ).project

    async def register(
        self,
        name: str,
        root: str,
        *,
        group_id: str | None = None,
        create_missing: bool = False,
    ) -> ProjectRegistrationResult:
        label = name.strip()
        if not label:
            raise ValueError("project name is required")
        if not str(root).strip():
            # An empty root used to resolve to the daemon's own working directory.
            raise ValueError("project folder is required")
        # Registration conflicts and an unknown group are checked before the folder is
        # created, so a rejected request never leaves a stray directory behind.
        resolved = Path(root).expanduser().resolve()
        # `same_path` rather than a normalized-string compare. `normcase` is
        # already platform-correct about case, but it is still a *string* test, so
        # two genuinely different spellings of one directory - a symlink, a
        # junction, a UNC path against a mapped drive, a bind mount - register as
        # two Projects over the same tree. Both then own the same `.swe-mux/`, and
        # nothing downstream can tell them apart.
        if any(same_path(item.root, resolved) for item in self.projects.values()):
            raise ValueError("that folder is already registered as a project")
        if group_id is not None and group_id not in self.groups:
            raise ValueError("unknown project group")
        canonical = canonical_project_root(resolved, create=create_missing)
        initialize_project_files(canonical, label)
        removed = await self.history.removed_project_for_root(str(canonical))
        if removed is not None:
            removed.name = label
            removed.position = max(
                (item.position for item in self.projects.values()), default=-1
            ) + 1
            removed.group_id = group_id
            removed.sidebar_visible = True
            self.projects[removed.id] = removed
            await self.history.upsert_project(removed)
            log.info(
                "Project registration restored project_id=%s root=%s",
                removed.id,
                removed.root,
            )
            return ProjectRegistrationResult(removed, restored=True)
        project = ProjectRecord(
            id=str(uuid.uuid4()),
            name=label,
            root=str(canonical),
            position=max((item.position for item in self.projects.values()), default=-1) + 1,
            group_id=group_id,
            layout=normalize_layout(None),
            created_at=time.time(),
        )
        self.projects[project.id] = project
        await self.history.upsert_project(project)
        log.info("Project registered project_id=%s root=%s", project.id, project.root)
        return ProjectRegistrationResult(project, restored=False)

    async def _apply_order(self, ordered_ids: list[str]) -> None:
        await self.history.reorder_projects(ordered_ids)
        for position, project_id in enumerate(ordered_ids):
            self.projects[project_id].position = position

    async def reorder(
        self, ordered_ids: list[str], *, expected_order: list[str]
    ) -> list[ProjectRecord]:
        async with self._order_lock:
            current = [item.id for item in self.ordered_projects()]
            if expected_order != current:
                raise ValueError("project order changed; refresh and try again")
            if len(ordered_ids) != len(current) or set(ordered_ids) != set(current):
                raise ValueError("project order must contain every registered project once")
            await self._apply_order(ordered_ids)
            return self.ordered_projects()

    async def touch_used(
        self, project_id: str, *, used_at: float | None = None
    ) -> ProjectRecord:
        """Advance the shared explicit-use timestamp for one Project."""

        stamp = time.time() if used_at is None else float(used_at)
        if not math.isfinite(stamp) or stamp < 0:
            raise ValueError("project use timestamp must be a non-negative finite number")
        async with self._project_write_lock:
            project = self.projects[project_id]
            next_stamp = max(project.last_used_at, stamp)
            if next_stamp == project.last_used_at:
                return project
            await self.history.set_project_last_used(project_id, next_stamp)
            project.last_used_at = next_stamp
            return project

    async def update(self, project_id: str, **changes: object) -> ProjectRecord:
        async with self._project_write_lock:
            return await self._update(project_id, **changes)

    async def _update(self, project_id: str, **changes: object) -> ProjectRecord:
        project = self.projects[project_id]
        if "position" in changes:
            raise ValueError("use the Project order endpoint to change position")
        expected = changes.pop("layout_revision", None)
        if (
            "layout" in changes
            and expected is not None
            and isinstance(expected, (str, int))
            and int(expected) != project.layout_revision
        ):
            raise ValueError(
                f"stale layout revision: expected {project.layout_revision}, received {expected}"
            )
        if "root" in changes:
            root = canonical_project_root(str(changes["root"]))
            if any(
                item.id != project_id and same_path(item.root, root)
                for item in self.projects.values()
            ):
                raise ValueError("that folder is already registered as a project")
            renamed = changes.get("name")
            initialize_project_files(root, str(renamed).strip() if renamed else project.name)
            changes["root"] = str(root)
        if "group_id" in changes:
            group_id = changes["group_id"]
            if group_id is not None and group_id not in self.groups:
                raise ValueError("unknown project group")
        if changes.get("resource_open_mode") not in {None, "dock", "popout"}:
            raise ValueError("resource_open_mode must be dock, popout, or null")
        default_backend = changes.get("default_backend")
        if (
            default_backend is not None
            and default_backend != "shell"
            and not is_agent_harness(default_backend)
        ):
            raise ValueError("default_backend must be shell, a registered agent, or null")
        if "git_compare_ref" in changes:
            compare_ref = changes["git_compare_ref"]
            if compare_ref is not None and (
                not isinstance(compare_ref, str)
                or not compare_ref.strip()
                or compare_ref != compare_ref.strip()
                or len(compare_ref) > 200
                or any(ord(character) < 32 or ord(character) == 127 for character in compare_ref)
            ):
                raise ValueError(
                    "git_compare_ref must be null or a non-empty Git ref of at most 200 characters"
                )
        if "name" in changes and not str(changes["name"]).strip():
            raise ValueError("project name is required")
        if "layout" in changes:
            changes["layout"] = normalize_layout(changes["layout"])
        if "sidebar_visible" in changes:
            changes["sidebar_visible"] = bool(changes["sidebar_visible"])
        selections = changes.get("default_agent_profiles")
        if isinstance(selections, dict):
            # An empty value clears the selection for that harness rather than
            # storing a blank id that later reads as "a profile called ''".
            changes["default_agent_profiles"] = {
                str(key): str(value) for key, value in selections.items() if str(value).strip()
            }
        for key in (
            "name",
            "root",
            "group_id",
            "layout",
            "default_backend",
            "default_profile_id",
            "default_agent_profiles",
            "git_compare_ref",
            "resource_open_mode",
            "sidebar_visible",
        ):
            if key in changes:
                setattr(project, key, changes[key])
        if "layout" in changes:
            project.layout_revision += 1
        await self.history.upsert_project(project)
        return project

    async def remove(self, project_id: str) -> None:
        project = self.projects[project_id]
        history_count = len(await self.history.project_session_ids(project_id))
        await self.history.remove_project(project_id, removed_at=time.time())
        del self.projects[project_id]
        if self.projects:
            await self._apply_order([item.id for item in self.ordered_projects()])
        log.info(
            "Project registration removed project_id=%s root=%s history_rows=%d",
            project.id,
            project.root,
            history_count,
        )

    async def create_group(self, name: str) -> ProjectGroupRecord:
        label = name.strip()
        if not label:
            raise ValueError("group name is required")
        # Past the highest position rather than at ``len(groups)``: after a delete the
        # count no longer matches the occupied slots, and two groups sharing a position
        # would make a reorder drag land somewhere the user did not aim for.
        position = max((item.position for item in self.groups.values()), default=-1) + 1
        group = ProjectGroupRecord(str(uuid.uuid4()), label, position)
        self.groups[group.id] = group
        await self.history.upsert_project_group(group)
        return group

    async def _apply_group_order(self, ordered_ids: list[str]) -> None:
        for position, group_id in enumerate(ordered_ids):
            group = self.groups[group_id]
            if group.position == position:
                continue
            group.position = position
            await self.history.upsert_project_group(group)

    async def reorder_groups(
        self, ordered_ids: list[str], *, expected_order: list[str]
    ) -> list[ProjectGroupRecord]:
        """Rewrite every group position, guarded by the order the caller last saw.

        Same optimistic contract as Project reordering: two devices dragging at once
        would otherwise each write a full permutation and the loser would silently
        win. The rejection tells the client to refresh instead.
        """
        async with self._order_lock:
            current = [item.id for item in self.ordered_groups()]
            if expected_order != current:
                raise ValueError("group order changed; refresh and try again")
            if len(ordered_ids) != len(current) or set(ordered_ids) != set(current):
                raise ValueError("group order must contain every group once")
            await self._apply_group_order(ordered_ids)
            return self.ordered_groups()

    def _group(self, group_id: str) -> ProjectGroupRecord:
        """Resolve a Group id, as a request error rather than a KeyError.

        Both callers below are reachable from a sidebar menu that may have been drawn
        before another device deleted the Group, so an unknown id is an ordinary stale
        request. As a bare `KeyError` it surfaced as an opaque 500.
        """

        group = self.groups.get(group_id)
        if group is None:
            raise ValueError("unknown group")
        return group

    async def update_group(self, group_id: str, **changes: object) -> ProjectGroupRecord:
        group = self._group(group_id)
        if "name" in changes:
            label = str(changes["name"]).strip()
            if not label:
                raise ValueError("group name is required")
            group.name = label
        if "position" in changes:
            group.position = int(str(changes["position"]))
        await self.history.upsert_project_group(group)
        return group

    async def delete_group(self, group_id: str) -> None:
        """Dissolve a Group, returning its Projects to the ungrouped root.

        A Group owns a name, an order, and membership, so this is the whole of it: the
        Projects keep their folders, layouts, sessions, history, and positions, and only
        stop pointing at a Group that no longer exists. `delete_project_group` clears the
        column in the same transaction that removes the row, so the in-memory ungrouping
        below cannot outlive a restart.
        """

        del self.groups[self._group(group_id).id]
        for project in self.projects.values():
            if project.group_id == group_id:
                project.group_id = None
        await self.history.delete_project_group(group_id)
        await self._apply_group_order([item.id for item in self.ordered_groups()])
