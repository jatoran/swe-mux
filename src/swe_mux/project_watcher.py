from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from watchfiles import Change, awatch

from .config import Config
from .event_bus import EventBus
from .project_files import (
    effective_project_ignores,
    ignored_project_path,
    project_path,
)
from .projects import ProjectManager

WATCH_LEASE_SECONDS = 45.0
MAX_WATCHED_DIRECTORIES = 64


@dataclass(slots=True)
class WatchLease:
    project_id: str
    watch_id: str
    paths: tuple[str, ...]
    expires_at: float


class ProjectFileWatcher:
    """Non-recursive, leased watches for directories visible in open resource tabs."""

    def __init__(
        self, projects: ProjectManager, events: EventBus, config: Config
    ) -> None:
        self.projects = projects
        self.events = events
        self.config = config
        self.leases: dict[tuple[str, str], WatchLease] = {}
        self._watchers: dict[
            tuple[str, str, str, tuple[str, ...]], asyncio.Task[None]
        ] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="project-file-watches")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for task in self._watchers.values():
            task.cancel()
        await asyncio.gather(
            *(task for task in [self._task, *self._watchers.values()] if task),
            return_exceptions=True,
        )
        self._task = None
        self._watchers.clear()
        self.leases.clear()

    def register(
        self, project_id: str, paths: list[str], watch_id: str | None = None
    ) -> WatchLease:
        project = self.projects.projects.get(project_id)
        if project is None:
            raise ValueError("unknown project")
        if len(paths) > MAX_WATCHED_DIRECTORIES:
            raise ValueError(f"at most {MAX_WATCHED_DIRECTORIES} directories may be watched")
        patterns = effective_project_ignores(
            project.root, self.config.project_ignore_patterns
        )
        normalized: list[str] = []
        for value in paths:
            target = project_path(project.root, value)
            if not target.is_dir():
                raise ValueError(f"watch path is not a directory: {value}")
            relative = (
                target.relative_to(Path(project.root).resolve()).as_posix()
                if target != Path(project.root).resolve()
                else ""
            )
            if relative and ignored_project_path(relative, patterns):
                continue
            normalized.append(relative)
        identity = watch_id or str(uuid.uuid4())
        lease = WatchLease(
            project_id,
            identity,
            tuple(dict.fromkeys(normalized)),
            time.monotonic() + WATCH_LEASE_SECONDS,
        )
        self.leases[(project_id, identity)] = lease
        return lease

    def remove(self, project_id: str, watch_id: str) -> None:
        self.leases.pop((project_id, watch_id), None)

    async def _run(self) -> None:
        while True:
            now = time.monotonic()
            self.leases = {
                key: lease for key, lease in self.leases.items() if lease.expires_at > now
            }
            desired: set[tuple[str, str, str, tuple[str, ...]]] = set()
            for lease in self.leases.values():
                project = self.projects.projects.get(lease.project_id)
                if project is None:
                    continue
                patterns = tuple(
                    effective_project_ignores(
                        project.root, self.config.project_ignore_patterns
                    )
                )
                desired.update(
                    (lease.project_id, project.root, path, patterns) for path in lease.paths
                )
            for key, task in tuple(self._watchers.items()):
                if key not in desired or task.done():
                    if not task.done():
                        task.cancel()
                    self._watchers.pop(key, None)
            for key in desired - self._watchers.keys():
                self._watchers[key] = asyncio.create_task(
                    self._watch_directory(*key), name=f"project-watch:{key[0]}:{key[2]}"
                )
            await asyncio.sleep(1)

    async def _watch_directory(
        self,
        project_id: str,
        project_root: str,
        relative_path: str,
        patterns: tuple[str, ...],
    ) -> None:
        root = Path(project_root).resolve()
        directory = project_path(root, relative_path)

        def visible(_change: Change, changed_path: str) -> bool:
            try:
                relative = Path(changed_path).resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                return False
            return not ignored_project_path(relative, patterns)

        try:
            async for changes in awatch(
                directory,
                watch_filter=visible,
                debounce=300,
                step=100,
                rust_timeout=1000,
                recursive=False,
                ignore_permission_denied=True,
            ):
                paths: list[str] = []
                for _change, changed_path in changes:
                    try:
                        relative = Path(changed_path).resolve().relative_to(root).as_posix()
                    except (OSError, ValueError):
                        continue
                    if not ignored_project_path(relative, patterns):
                        paths.append(relative)
                if paths:
                    unique = sorted(set(paths))
                    await self.events.emit(
                        "project_files_changed",
                        source="project_watcher",
                        project_id=project_id,
                        paths=unique[:200],
                        overflow=len(unique) > 200,
                    )
        except (FileNotFoundError, NotADirectoryError, OSError):
            return
