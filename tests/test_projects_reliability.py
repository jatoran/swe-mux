from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.history import HistoryIndex
from swe_mux.layouts import MAX_LAYOUT_LEAVES, layout_terminal_ids, normalize_layout
from swe_mux.models import SessionRecord
from swe_mux.projects import ProjectManager


async def test_project_creation_initializes_resources_and_persists_layout(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()

    project = await projects.create("Main", str(root))
    assert Path(project.root) == root.resolve()
    assert (root / ".swe-mux" / "config.toml").read_text(encoding="utf-8") == "version = 1\n"
    note = root / ".swe-mux" / "notes" / "project.md"
    assert note.read_text(encoding="utf-8") == "# Main notes\n\n\n"

    updated = await projects.update(
        project.id,
        layout={"version": 1, "panes": ["one", "one"]},
        layout_revision=0,
    )
    assert layout_terminal_ids(updated.layout) == ["one"]
    assert updated.layout_revision == 1
    assert updated.sidebar_visible is True
    hidden = await projects.update(project.id, sidebar_visible=False)
    assert hidden.sidebar_visible is False
    with pytest.raises(ValueError, match="stale layout revision"):
        await projects.update(
            project.id,
            layout={"version": 1, "panes": ["two"]},
            layout_revision=0,
        )
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened = ProjectManager(reopened_history)
    await reopened.start()
    assert layout_terminal_ids(reopened.projects[project.id].layout) == ["one"]
    assert reopened.projects[project.id].sidebar_visible is False
    reopened_history.close()


async def test_project_note_seeding_never_overwrites_existing_text(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    notes = root / ".swe-mux" / "notes"
    notes.mkdir(parents=True)
    note = notes / "project.md"
    note.write_text("existing text\n", encoding="utf-8")

    project = await projects.create("  Main   Repo  ", str(root))
    assert note.read_text(encoding="utf-8") == "existing text\n"

    # A never-arranged Project stays structurally empty; the browser seeds its first
    # layout on open, so revision 0 with an empty root stays the "untouched" signal.
    assert project.layout == {"version": 6, "root": None}
    assert project.layout_revision == 0

    other = tmp_path / "second"
    other.mkdir()
    await projects.create("  Main   Repo  ", str(other))
    seeded = other / ".swe-mux" / "notes" / "project.md"
    assert seeded.read_text(encoding="utf-8") == "# Main Repo notes\n\n\n"
    history.close()


async def test_projects_require_distinct_existing_folders(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()

    await projects.create("Main", str(root))
    with pytest.raises(ValueError, match="already registered"):
        await projects.create("Duplicate", str(root))
    with pytest.raises(ValueError, match="does not exist"):
        await projects.create("Missing", str(tmp_path / "missing"))
    history.close()


async def test_project_groups_only_organize_projects(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()

    group = await projects.create_group("Products")
    project = await projects.create("Main", str(root), group_id=group.id)
    assert project.group_id == group.id
    assert (await projects.update_group(group.id, name="Clients")).name == "Clients"

    await projects.delete_group(group.id)
    assert projects.projects[project.id].group_id is None
    assert group.id not in projects.groups
    history.close()


async def test_project_with_history_cannot_be_deleted(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    session = SessionRecord(
        "ended", "agent", project.id, "claude", "native", project.root, "claude", []
    )
    await history.session_started(session, str(tmp_path / "transcript.jsonl"))

    with pytest.raises(ValueError, match="remove this project's sessions"):
        await projects.delete(project.id)
    history.close()


def test_recursive_layout_validates_splits_ratios_and_unique_resources() -> None:
    layout = normalize_layout(
        {
            "version": 2,
            "root": {
                "type": "split",
                "direction": "horizontal",
                "ratio": 0.6,
                "first": {"type": "leaf", "kind": "terminal", "id": "left"},
                "second": {
                    "type": "split",
                    "direction": "vertical",
                    "ratio": 0.4,
                    "first": {"type": "leaf", "kind": "note", "id": "notes"},
                    "second": {"type": "leaf", "kind": "preview", "id": "web"},
                },
            },
        }
    )
    assert layout["version"] == 6
    assert layout_terminal_ids(layout) == ["left"]
    assert layout["root"]["second"]["first"]["children"] == [
        {"type": "leaf", "kind": "note", "id": "notes"}
    ]
    history_layout = normalize_layout(
        {
            "version": 6,
            "root": {
                "type": "stack",
                "id": "history-pane",
                "active_child_id": "history:archive",
                "children": [{"type": "leaf", "kind": "history", "id": "history:archive"}],
            },
        }
    )
    assert history_layout["root"]["children"][0]["kind"] == "history"
    with pytest.raises(ValueError, match="same resource"):
        normalize_layout(
            {
                "version": 2,
                "root": {
                    "type": "split",
                    "direction": "horizontal",
                    "first": {"type": "leaf", "kind": "terminal", "id": "same"},
                    "second": {"type": "leaf", "kind": "terminal", "id": "same"},
                },
            }
        )
    with pytest.raises(ValueError, match="same resource"):
        normalize_layout(
            {
                "version": 6,
                "root": {
                    "type": "stack",
                    "id": "pane",
                    "active_child_id": "same",
                    "children": [
                        {"type": "leaf", "kind": "terminal", "id": "same"},
                        {"type": "leaf", "kind": "preview", "id": "same"},
                    ],
                },
            }
        )
    with pytest.raises(ValueError, match="between 0.1 and 0.9"):
        normalize_layout(
            {
                "version": 2,
                "root": {
                    "type": "split",
                    "direction": "vertical",
                    "ratio": 0.99,
                    "first": {"type": "leaf", "kind": "terminal", "id": "a"},
                    "second": {"type": "leaf", "kind": "terminal", "id": "b"},
                },
            }
        )


def test_legacy_layout_migration_has_a_documented_safety_limit() -> None:
    ids = [f"session-{index}" for index in range(MAX_LAYOUT_LEAVES)]
    migrated = normalize_layout({"version": 1, "panes": ids})
    assert layout_terminal_ids(migrated) == ids
    with pytest.raises(ValueError, match="maximum leaf count"):
        normalize_layout({"version": 1, "panes": [*ids, "one-too-many"]})


def test_visible_legacy_resource_workspace_migrates_into_an_adjacent_pane() -> None:
    migrated = normalize_layout(
        {
            "version": 5,
            "root": {
                "type": "leaf",
                "kind": "terminal",
                "id": "terminal-a",
            },
            "note_workspace": {
                "visible": True,
                "open_ids": ["note:project-a", "files:project-a"],
                "active_id": "files:project-a",
                "size": 0.4,
                "mode": "popout",
            },
        }
    )

    assert migrated["version"] == 6
    assert migrated["root"]["type"] == "split"
    assert migrated["root"]["ratio"] == 0.6
    assert migrated["root"]["second"]["active_child_id"] == "files:project-a"
