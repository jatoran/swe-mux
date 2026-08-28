from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import ProjectRecord
from swe_mux.project_watcher import (
    MAX_WATCHED_DIRECTORIES,
    ProjectFileWatcher,
    watched_entry_path,
)
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


def test_a_watched_directorys_own_node_is_not_one_of_its_entries(tmp_path: Path) -> None:
    """The one projection every host must agree on, asserted without an OS watch.

    macOS FSEvents reports a directory whose own node changed alongside the entry
    that changed it, while inotify and ReadDirectoryChangesW report only the
    entry - and `watchfiles` normalizes neither. This is the normalization, so it
    is checked directly rather than only through a live watch that would exercise
    exactly one backend per CI leg.
    """

    root = tmp_path.resolve()
    source = root / "src"
    (source / "sub").mkdir(parents=True)
    (root / "node_modules").mkdir()

    assert watched_entry_path(root, source, str(source / "main.py"), ()) == "src/main.py"
    # The FSEvents companion event, which is what the file event already said.
    assert watched_entry_path(root, source, str(source), ()) is None
    # A watch on the Project root reports its own node as `.` rather than `''`.
    assert watched_entry_path(root, root, str(root), ()) is None
    # An entry that happens to be a directory is real content and stays: it is how
    # the tree learns a new folder exists, and inotify reports exactly this path.
    assert watched_entry_path(root, source, str(source / "sub"), ()) == "src/sub"
    assert watched_entry_path(root, root, str(root / "node_modules"), ("node_modules",)) is None
    assert watched_entry_path(root, source, str(tmp_path.parent / "elsewhere.py"), ()) is None


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
        # One shape on every host. macOS reported `['src', 'src/main.py']` here on
        # 2026-08-27 because FSEvents also fires for the directory whose mtime the
        # write bumped; `watched_entry_path` drops that companion, so this stays an
        # equality rather than becoming a per-platform expectation.
        assert event.payload["paths"] == ["src/main.py"]
    finally:
        events.unsubscribe(queue)
        await watcher.stop()
