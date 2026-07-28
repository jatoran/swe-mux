from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from .history import HistoryIndex
from .layouts import normalize_layout
from .models import ProjectGroupRecord, ProjectRecord


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
    """Seed a new Project note with its own heading and room to start typing.

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
        note.write_text(project_note_template(name or root.name), encoding="utf-8")


class ProjectManager:
    def __init__(self, history: HistoryIndex) -> None:
        self.history = history
        self.projects: dict[str, ProjectRecord] = {}
        self.groups: dict[str, ProjectGroupRecord] = {}
        self._order_lock = asyncio.Lock()

    def ordered_projects(self) -> list[ProjectRecord]:
        return sorted(self.projects.values(), key=lambda item: (item.position, item.name, item.id))

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

    async def create(
        self,
        name: str,
        root: str,
        *,
        group_id: str | None = None,
        create_missing: bool = False,
    ) -> ProjectRecord:
        label = name.strip()
        if not label:
            raise ValueError("project name is required")
        if not str(root).strip():
            # An empty root used to resolve to the daemon's own working directory.
            raise ValueError("project folder is required")
        # Registration conflicts and an unknown group are checked before the folder is
        # created, so a rejected request never leaves a stray directory behind.
        resolved = Path(root).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if any(os.path.normcase(item.root) == key for item in self.projects.values()):
            raise ValueError("that folder is already registered as a project")
        if group_id is not None and group_id not in self.groups:
            raise ValueError("unknown project group")
        canonical = canonical_project_root(resolved, create=create_missing)
        initialize_project_files(canonical, label)
        project = ProjectRecord(
            id=str(uuid.uuid4()),
            name=label,
            root=str(canonical),
            position=max((item.position for item in self.projects.values()), default=-1) + 1,
            group_id=group_id,
            layout=normalize_layout(None),
        )
        self.projects[project.id] = project
        await self.history.upsert_project(project)
        return project

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

    async def update(self, project_id: str, **changes: object) -> ProjectRecord:
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
            key = os.path.normcase(str(root))
            if any(
                item.id != project_id and os.path.normcase(item.root) == key
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
        if changes.get("default_backend") not in {None, "shell", "claude", "codex"}:
            raise ValueError("default_backend must be shell, claude, codex, or null")
        if "name" in changes and not str(changes["name"]).strip():
            raise ValueError("project name is required")
        if "layout" in changes:
            changes["layout"] = normalize_layout(changes["layout"])
        if "sidebar_visible" in changes:
            changes["sidebar_visible"] = bool(changes["sidebar_visible"])
        for key in (
            "name",
            "root",
            "group_id",
            "layout",
            "default_backend",
            "default_profile_id",
            "resource_open_mode",
            "sidebar_visible",
        ):
            if key in changes:
                setattr(project, key, changes[key])
        if "layout" in changes:
            project.layout_revision += 1
        await self.history.upsert_project(project)
        return project

    async def delete(self, project_id: str) -> None:
        if any(True for _ in await self.history.project_session_ids(project_id)):
            raise ValueError("remove this project's sessions before deleting it")
        del self.projects[project_id]
        await self.history.delete_project(project_id)
        if self.projects:
            await self._apply_order([item.id for item in self.ordered_projects()])

    async def create_group(self, name: str) -> ProjectGroupRecord:
        label = name.strip()
        if not label:
            raise ValueError("group name is required")
        group = ProjectGroupRecord(str(uuid.uuid4()), label, len(self.groups))
        self.groups[group.id] = group
        await self.history.upsert_project_group(group)
        return group

    async def update_group(self, group_id: str, **changes: object) -> ProjectGroupRecord:
        group = self.groups[group_id]
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
        del self.groups[group_id]
        for project in self.projects.values():
            if project.group_id == group_id:
                project.group_id = None
        await self.history.delete_project_group(group_id)
