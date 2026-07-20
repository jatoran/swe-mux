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
        await asyncio.sleep(0.2)
        (source / "main.py").write_text("print('ready')\n", encoding="utf-8")
        event = await asyncio.wait_for(queue.get(), timeout=5)
        assert event.type == "project_files_changed"
        assert event.payload["project_id"] == project.id
        assert event.payload["paths"] == ["src/main.py"]
    finally:
        events.unsubscribe(queue)
        await watcher.stop()
