from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from watchfiles import Change, awatch

from .background_tasks import background
from .config import Config
from .event_bus import EventBus
from .project_files import (
    effective_project_ignores,
    ignored_project_path,
    project_path,
)
from .projects import ProjectManager

PROJECT_WATCH_LOOP = "project-file-watches"
WATCH_LEASE_SECONDS = 45.0
MAX_WATCHED_DIRECTORIES = 64
# Per-call caps bound one client; these bound the daemon. Every browser tab (and
# any client minting fresh watch_ids in a retry loop) adds a lease, and each
# distinct watched directory costs an OS watch handle and a Rust notify thread.
MAX_WATCH_LEASES = 16
MAX_WATCHED_KEYS = 256
# A watcher whose directory was deleted fails immediately; without a cooldown the
# reconcile pass re-creates it every second forever.
WATCH_FAILURE_COOLDOWN_SECONDS = 30.0


def watched_entry_path(
    root: Path,
    directory: Path,
    changed_path: str,
    patterns: tuple[str, ...],
) -> str | None:
    """Project one raw watchfiles change onto a Project-relative *entry* path.

    Returns `None` for anything that is not an entry of `directory`: a path that
    resolves outside the Project, an ignored path, and the watched directory's
    own node.

    That last exclusion is what makes the three hosts agree. `watchfiles`
    documents only "the path of the file that changed" and normalizes nothing
    across backends - it hands Rust `notify`'s events straight through - so the
    granularity is whatever the OS reports. Linux inotify and Windows
    `ReadDirectoryChangesW` report the entry that changed. macOS FSEvents
    reports at directory granularity and additionally fires for the directory
    whose own node changed, and writing an entry bumps its parent's mtime, so a
    write to `src/main.py` under a watch on `src` reports both `src/main.py`
    *and* `src`.

    The directory's own node is never a member of its own contents, and this
    watch is non-recursive and exists to report contents, so that second event
    carries nothing the first does not - it is derived from it. Dropping it
    removes the platform difference rather than teaching each consumer about it.

    Entries that merely *happen* to be directories are kept. A new subfolder
    inside a watched directory is real content, it is how the file tree learns
    the folder exists, and inotify reports exactly that path - so filtering on
    "is a directory" rather than on "is *this* directory" would trade a macOS-only
    redundancy for a lost event on every host.
    """

    try:
        target = Path(changed_path).resolve()
        relative = target.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    if os.path.normcase(str(target)) == os.path.normcase(str(directory)):
        return None
    if ignored_project_path(relative, patterns):
        return None
    return relative


def _desired_watches(
    requests: list[tuple[str, str, tuple[str, ...], str]],
    global_patterns: tuple[str, ...],
) -> set[tuple[str, str, str, tuple[str, ...]]]:
    """Watch keys for the live leases. Filesystem-bound: runs in a worker thread.

    Each request is `(project_id, lease_root, paths, project_root)`. Ignore patterns
    apply only to a lease on the Project's own root, never to a sibling worktree's.
    """
    canonical: dict[str, str] = {}
    ignores: dict[str, tuple[str, ...]] = {}
    desired: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for project_id, lease_root, paths, project_root in requests:
        if project_root not in canonical:
            canonical[project_root] = os.path.normcase(str(Path(project_root).resolve()))
        if os.path.normcase(lease_root) == canonical[project_root]:
            if project_root not in ignores:
                ignores[project_root] = tuple(
                    effective_project_ignores(project_root, list(global_patterns))
                )
            patterns = ignores[project_root]
        else:
            patterns = ()
        desired.update((project_id, lease_root, path, patterns) for path in paths)
    return desired


def _watch_target(project_root: str, relative_path: str) -> tuple[Path, Path]:
    root = Path(project_root).resolve()
    return root, project_path(root, relative_path)


@dataclass(slots=True)
class WatchLease:
    project_id: str
    watch_id: str
    root: str
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
        self._failed_until: dict[tuple[str, str, str, tuple[str, ...]], float] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = background.start(PROJECT_WATCH_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(PROJECT_WATCH_LOOP)
        self._task = None
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
        self,
        project_id: str,
        paths: list[str],
        watch_id: str | None = None,
        *,
        root: str | None = None,
    ) -> WatchLease:
        """Validate and record a lease. Synchronous and filesystem-bound: tests only.

        The route calls `register_async`, which does the same filesystem work in a
        worker thread; this form keeps the contract testable without a loop.
        """
        return self._commit(project_id, watch_id, *self._prepare(project_id, paths, root))

    async def register_async(
        self,
        project_id: str,
        paths: list[str],
        watch_id: str | None = None,
        *,
        root: str | None = None,
    ) -> WatchLease:
        """`register`, with the resolves and directory checks off the event loop.

        A client renews its lease every 30s per open tree, and every path in it is a
        `resolve` plus an `is_dir`; on a disk that is busy those are what stalled the
        loop. Only the lease bookkeeping runs on the loop, which is also what keeps
        `leases` single-writer.
        """
        prepared = await asyncio.to_thread(self._prepare, project_id, paths, root)
        return self._commit(project_id, watch_id, *prepared)

    def _prepare(
        self, project_id: str, paths: list[str], root: str | None
    ) -> tuple[str, tuple[str, ...]]:
        project = self.projects.projects.get(project_id)
        if project is None:
            raise ValueError("unknown project")
        if len(paths) > MAX_WATCHED_DIRECTORIES:
            raise ValueError(f"at most {MAX_WATCHED_DIRECTORIES} directories may be watched")
        resource_root = str(Path(root or project.root).resolve())
        canonical_root = str(Path(project.root).resolve())
        patterns = (
            effective_project_ignores(project.root, self.config.project_ignore_patterns)
            if os.path.normcase(resource_root) == os.path.normcase(canonical_root)
            else []
        )
        resolved_root = Path(resource_root).resolve()
        normalized: list[str] = []
        for value in paths:
            target = project_path(resource_root, value)
            if not target.is_dir():
                # Skipped, not rejected: a renewal that happens to include a
                # folder the user just deleted would otherwise fail as a whole
                # and silently drop that client's entire watch set.
                continue
            relative = (
                target.relative_to(resolved_root).as_posix() if target != resolved_root else ""
            )
            if relative and ignored_project_path(relative, patterns):
                continue
            normalized.append(relative)
        return resource_root, tuple(dict.fromkeys(normalized))

    def _commit(
        self, project_id: str, watch_id: str | None, resource_root: str, paths: tuple[str, ...]
    ) -> WatchLease:
        identity = watch_id or str(uuid.uuid4())
        lease = WatchLease(
            project_id,
            identity,
            resource_root,
            paths,
            time.monotonic() + WATCH_LEASE_SECONDS,
        )
        self.leases[(project_id, identity)] = lease
        # Oldest-expiring first, so a renewing client keeps its lease and an
        # abandoned one is what gets dropped.
        while len(self.leases) > MAX_WATCH_LEASES:
            oldest = min(self.leases.values(), key=lambda item: item.expires_at)
            if (oldest.project_id, oldest.watch_id) == (project_id, identity):
                break
            self.leases.pop((oldest.project_id, oldest.watch_id), None)
        return lease

    def remove(self, project_id: str, watch_id: str) -> None:
        self.leases.pop((project_id, watch_id), None)

    async def _run(self) -> None:
        while True:
            with background.iteration(PROJECT_WATCH_LOOP):
                await self._reconcile_watchers()
            await asyncio.sleep(1)

    async def _reconcile_watchers(self) -> None:
        now = time.monotonic()
        self.leases = {
            key: lease for key, lease in self.leases.items() if lease.expires_at > now
        }
        requests = [
            (lease.project_id, lease.root, lease.paths, project.root)
            for lease in self.leases.values()
            if (project := self.projects.projects.get(lease.project_id)) is not None
        ]
        # Every second, per lease: a `resolve` of the Project root and a read of its
        # `.swe-mux/config.toml`. Run on the loop, that was a 49.5s stall on
        # 2026-09-23 when the disk under a Project root stopped answering.
        desired = await asyncio.to_thread(
            _desired_watches, requests, tuple(self.config.project_ignore_patterns)
        )
        for key, task in tuple(self._watchers.items()):
            if key not in desired or task.done():
                if task.done() and key in desired:
                    # It ended on its own — the directory is gone or unwatchable.
                    # Respawning it every tick is a steady churn of failed Rust
                    # watcher startups that nothing ever reports.
                    self._failed_until[key] = now + WATCH_FAILURE_COOLDOWN_SECONDS
                if not task.done():
                    task.cancel()
                self._watchers.pop(key, None)
        self._failed_until = {
            key: until for key, until in self._failed_until.items() if until > now
        }
        for key in sorted(desired - self._watchers.keys()):
            if len(self._watchers) >= MAX_WATCHED_KEYS:
                break
            if key in self._failed_until:
                continue
            self._watchers[key] = asyncio.create_task(
                self._watch_directory(*key), name=f"project-watch:{key[0]}:{key[2]}"
            )

    async def _watch_directory(
        self,
        project_id: str,
        project_root: str,
        relative_path: str,
        patterns: tuple[str, ...],
    ) -> None:
        try:
            root, directory = await asyncio.to_thread(_watch_target, project_root, relative_path)
        except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
            return

        def entry(changed_path: str) -> str | None:
            return watched_entry_path(root, directory, changed_path, patterns)

        def visible(_change: Change, changed_path: str) -> bool:
            return entry(changed_path) is not None

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
                paths = [
                    relative
                    for relative in (entry(changed_path) for _change, changed_path in changes)
                    if relative is not None
                ]
                if paths:
                    unique = sorted(set(paths))
                    await self.events.emit(
                        "project_files_changed",
                        source="project_watcher",
                        project_id=project_id,
                        worktree=project_root,
                        paths=unique[:200],
                        overflow=len(unique) > 200,
                    )
        except (FileNotFoundError, NotADirectoryError, OSError):
            return
