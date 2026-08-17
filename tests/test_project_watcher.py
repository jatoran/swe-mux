from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import ProjectRecord
from swe_mux.project_watcher import MAX_WATCHED_DIRECTORIES, ProjectFileWatcher
from swe_mux.projects import ProjectManager


def test_project_watches_are_leased_bounded_and_ignore_heavy_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    projects = cast(ProjectManager, SimpleNamespace(projects={project.id: project}))
    watcher = ProjectFileWatcher(projects, EventBus(), Config(data_dir=tmp_path / "data"))

    lease = watcher.register(project.id, ["", "src", "node_modules"], "browser-tab")

    assert lease.watch_id == "browser-tab"
    assert lease.paths == ("", "src")
    assert lease.expires_at > 0
    watcher.remove(project.id, lease.watch_id)
    assert watcher.leases == {}
    with pytest.raises(ValueError, match="at most"):
        watcher.register(project.id, [""] * (MAX_WATCHED_DIRECTORIES + 1))


async def test_equal_relative_paths_in_two_worktrees_have_distinct_watch_identities(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "main"
    sibling = tmp_path / "sibling"
    (canonical / "src").mkdir(parents=True)
    (sibling / "src").mkdir(parents=True)
    project = ProjectRecord("project", "Project", str(canonical), 0)
    projects = cast(ProjectManager, SimpleNamespace(projects={project.id: project}))
    watcher = ProjectFileWatcher(projects, EventBus(), Config(data_dir=tmp_path / "data"))

    main = watcher.register(project.id, ["src"], "main-tab")
    other = watcher.register(project.id, ["src"], "sibling-tab", root=str(sibling))
    watcher._reconcile_watchers()

    assert main.root == str(canonical.resolve())
    assert other.root == str(sibling.resolve())
    roots = {key[1] for key in watcher._watchers}
    assert roots == {main.root, other.root}
    for task in watcher._watchers.values():
        task.cancel()
    await asyncio.gather(*watcher._watchers.values(), return_exceptions=True)


async def test_project_watcher_emits_changes_only_for_an_open_directory(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    project = ProjectRecord("project", "Project", str(tmp_path), 0)
    projects = cast(ProjectManager, SimpleNamespace(projects={project.id: project}))
    events = EventBus()
    queue = events.subscribe()
    watcher = ProjectFileWatcher(projects, events, Config(data_dir=tmp_path / "data"))
    watcher.register(project.id, ["src"], "open-tree")
    watcher.start()
    try:
        for _ in range(30):
            if watcher._watchers:
                break
            await asyncio.sleep(0.1)
        # `_watchers` being populated means the watch was *registered*, not that the
        # OS is delivering for it yet. A fixed settle is enough on Windows and is
        # not on Linux under a loaded parallel suite, where arming inotify can lose
        # the race with the write - so the write is retried instead of the sleep
        # being lengthened, which would slow every run to fix the slowest one.
        event = None
        for attempt in range(20):
            (source / "main.py").write_text(f"print('ready {attempt}')\n", encoding="utf-8")
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1)
                break
            except TimeoutError:
                continue
        assert event is not None, "the watcher never reported a change it was watching for"
        assert event.type == "project_files_changed"
        assert event.payload["project_id"] == project.id
        assert event.payload["paths"] == ["src/main.py"]
    finally:
        events.unsubscribe(queue)
        await watcher.stop()
