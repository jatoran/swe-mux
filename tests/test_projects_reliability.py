from __future__ import annotations

import time
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

    # A new Project's workspace starts empty: Files and Notes live in the utility
    # drawer, so there is nothing worth seeding a pane with.
    assert project.layout == {"version": 7, "root": None}
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


async def test_group_order_survives_deletes_and_guards_concurrent_writers(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()

    first = await projects.create_group("First")
    second = await projects.create_group("Second")
    third = await projects.create_group("Third")
    assert [item.id for item in projects.ordered_groups()] == [first.id, second.id, third.id]

    reordered = await projects.reorder_groups(
        [third.id, first.id, second.id], expected_order=[first.id, second.id, third.id]
    )
    assert [item.id for item in reordered] == [third.id, first.id, second.id]
    assert [item.position for item in reordered] == [0, 1, 2]

    # A second device still holding the pre-drag order must be told to refresh rather
    # than silently overwriting the permutation that already landed.
    with pytest.raises(ValueError, match="order changed"):
        await projects.reorder_groups(
            [first.id, second.id, third.id], expected_order=[first.id, second.id, third.id]
        )
    with pytest.raises(ValueError, match="every group once"):
        await projects.reorder_groups(
            [third.id, first.id], expected_order=[third.id, first.id, second.id]
        )

    # Deleting from the middle renumbers, so the next group created cannot collide
    # with an occupied slot and land somewhere the user did not drop it.
    await projects.delete_group(first.id)
    assert [item.position for item in projects.ordered_groups()] == [0, 1]
    fourth = await projects.create_group("Fourth")
    assert [item.id for item in projects.ordered_groups()] == [third.id, second.id, fourth.id]
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened = ProjectManager(reopened_history)
    await reopened.start()
    assert [item.id for item in reopened.ordered_groups()] == [third.id, second.id, fourth.id]
    reopened_history.close()


async def test_projects_are_dated_at_registration_and_backfilled_from_history(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()

    before = time.time()
    project = await projects.create("Main", str(root))
    assert before <= project.created_at <= time.time()

    # An unrelated update must not restamp the Project as newly created.
    renamed = await projects.update(project.id, name="Renamed")
    assert renamed.created_at == project.created_at
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened = ProjectManager(reopened_history)
    await reopened.start()
    assert reopened.projects[project.id].created_at == project.created_at
    reopened_history.close()


async def test_created_at_migration_dates_older_projects_from_their_first_session(
    tmp_path: Path,
) -> None:
    """A database written before the column exists must still order by date."""
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)
    projects = ProjectManager(history)
    await projects.start()
    dated = tmp_path / "dated"
    dated.mkdir()
    undated = tmp_path / "undated"
    undated.mkdir()
    with_history = await projects.create("Dated", str(dated))
    without_history = await projects.create("Undated", str(undated))
    await history.session_started(
        SessionRecord(
            "s1",
            "agent",
            with_history.id,
            "claude",
            "native-1",
            str(dated),
            "claude.exe",
            [],
            created_at=1_700_000_000.0,
        ),
        None,
    )
    # Drop the column to look like a database from before the migration.
    history._db.execute("ALTER TABLE projects DROP COLUMN created_at")
    history._db.commit()
    history.close()

    migrated = HistoryIndex(path)
    records = {item.id: item for item in await migrated.list_projects()}
    assert records[with_history.id].created_at == 1_700_000_000.0
    # Nothing in the database dates this one, and inventing a day for it would put it
    # in the middle of a date ordering; 0 is read as unknown and sorts last instead.
    assert records[without_history.id].created_at == 0.0
    migrated.close()


async def test_project_last_activity_takes_the_latest_stamp_a_session_carries(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    record = SessionRecord(
        "s1",
        "agent",
        project.id,
        "claude",
        "native-1",
        str(root),
        "claude.exe",
        [],
        created_at=1_000.0,
    )
    await history.session_started(record, None)
    assert (await history.project_last_activity())[project.id] == 1_000.0

    # A running session has no exit, so the transcript stamp is what keeps it ranked.
    history._db.execute("UPDATE history SET last_message_at=4000 WHERE id=?", (record.id,))
    history._db.commit()
    assert (await history.project_last_activity())[project.id] == 4_000.0
    # An exit later than every other stamp wins; an earlier one must not pull it back.
    history._db.execute("UPDATE history SET exited_at=9000 WHERE id=?", (record.id,))
    history._db.commit()
    assert (await history.project_last_activity())[project.id] == 9_000.0
    history._db.execute("UPDATE history SET exited_at=2000 WHERE id=?", (record.id,))
    history._db.commit()
    assert (await history.project_last_activity())[project.id] == 4_000.0

    # A Project that never ran a session is absent, not zero-stamped in the map.
    other = tmp_path / "other"
    other.mkdir()
    quiet = await projects.create("Quiet", str(other))
    assert quiet.id not in await history.project_last_activity()
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
    assert layout["version"] == 7
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
                "active_id": "note:project-a",
                "size": 0.4,
                "mode": "popout",
            },
        }
    )

    assert migrated["version"] == 7
    assert migrated["root"]["type"] == "split"
    assert migrated["root"]["ratio"] == 0.6
    # `files:project-a` is dropped on the way through: the Files browser is now the
    # utility drawer's Files tab, not a leaf any pane can render.
    assert migrated["root"]["second"]["children"] == [
        {"type": "leaf", "kind": "note", "id": "note:project-a"}
    ]
    assert migrated["root"]["second"]["active_child_id"] == "note:project-a"


def test_v6_files_leaves_are_pruned_rather_than_rejected() -> None:
    """Files moved to the utility drawer, so a persisted `files:` leaf is dropped on read.

    Pruning, not rejecting: a v6 layout is a user's real workspace and a stale client can
    still PATCH one, and neither should fail — the Files browser still exists, just not as
    a pane tab.
    """
    pruned = normalize_layout(
        {
            "version": 6,
            "root": {
                "type": "split",
                "direction": "horizontal",
                "ratio": 0.22,
                "first": {
                    "type": "stack",
                    "id": "files-pane",
                    "active_child_id": "files:project-a",
                    "children": [{"type": "leaf", "kind": "note", "id": "files:project-a"}],
                },
                "second": {
                    "type": "stack",
                    "id": "note-pane",
                    "active_child_id": "note:project-a",
                    "children": [
                        {"type": "leaf", "kind": "note", "id": "note:project-a"},
                        {"type": "leaf", "kind": "terminal", "id": "terminal-a"},
                    ],
                },
            },
        }
    )
    # The pane that held only Files is gone, and the split collapsed into the survivor.
    assert pruned["version"] == 7
    assert pruned["root"]["type"] == "stack"
    assert [child["id"] for child in pruned["root"]["children"]] == [
        "note:project-a",
        "terminal-a",
    ]

    # A Files tab sharing a pane leaves that pane standing, and the active tab moves off it.
    shared = normalize_layout(
        {
            "version": 6,
            "root": {
                "type": "stack",
                "id": "pane-a",
                "active_child_id": "files:project-a",
                "children": [
                    {"type": "leaf", "kind": "terminal", "id": "terminal-a"},
                    {"type": "leaf", "kind": "note", "id": "files:project-a"},
                ],
            },
        }
    )
    assert layout_terminal_ids(shared) == ["terminal-a"]
    assert shared["root"]["active_child_id"] == "terminal-a"

    # A workspace whose only leaf was Files becomes the empty stage rather than an error.
    emptied = normalize_layout(
        {
            "version": 6,
            "root": {
                "type": "stack",
                "id": "pane-a",
                "active_child_id": "files:project-a",
                "children": [{"type": "leaf", "kind": "note", "id": "files:project-a"}],
            },
        }
    )
    assert emptied == {"version": 7, "root": None}
