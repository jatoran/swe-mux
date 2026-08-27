from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.layouts import MAX_LAYOUT_LEAVES, layout_terminal_ids, normalize_layout
from swe_mux.models import SessionRecord
from swe_mux.project_files import read_note
from swe_mux.projects import ProjectManager
from swe_mux.routes.projects import delete_project, record_project_use
from swe_mux.server import error_middleware


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
    local_ignore = (root / ".swe-mux" / ".gitignore").read_text(encoding="utf-8")
    assert "/notes/" in local_ignore
    assert "/preview-shots/" in local_ignore
    assert "config.toml" not in local_ignore
    loaded = await read_note(root, "project")
    assert loaded["title"] == "Main notes"
    assert loaded["markdown"] == "# Main notes\n\n\n"

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


async def test_git_comparison_override_round_trips_resets_and_survives_other_updates(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))

    configured = await projects.update(project.id, git_compare_ref="origin/main")
    assert configured.git_compare_ref == "origin/main"
    renamed = await projects.update(project.id, name="Renamed")
    assert renamed.git_compare_ref == "origin/main"
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened = ProjectManager(reopened_history)
    await reopened.start()
    assert reopened.projects[project.id].git_compare_ref == "origin/main"
    reset = await reopened.update(project.id, git_compare_ref=None)
    assert reset.git_compare_ref is None
    for invalid in ("", " main", "main\n", "x" * 201, 42):
        with pytest.raises(ValueError, match="git_compare_ref"):
            await reopened.update(project.id, git_compare_ref=invalid)
    reopened_history.close()


async def test_git_comparison_override_column_migrates_existing_project_database(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    history._db.execute("ALTER TABLE projects DROP COLUMN git_compare_ref")
    history._db.commit()
    history.close()

    migrated = HistoryIndex(path)
    columns = {
        row["name"] for row in migrated._db.execute("PRAGMA table_info(projects)").fetchall()
    }
    assert "git_compare_ref" in columns
    records = {item.id: item for item in await migrated.list_projects()}
    assert records[project.id].git_compare_ref is None
    migrated.close()


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
    loaded = await read_note(other, "project")
    assert loaded["title"] == "Main Repo notes"
    assert loaded["markdown"] == "# Main Repo notes\n\n\n"
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
    # Durable, not just in memory: the sidebar's delete would otherwise re-group every
    # Project on the next daemon start.
    reloaded = ProjectManager(HistoryIndex(tmp_path / "mux.db"))
    await reloaded.start()
    assert reloaded.groups == {}
    assert reloaded.projects[project.id].group_id is None
    reloaded.history.close()

    # A menu drawn before another device deleted the Group is an ordinary stale request,
    # which the request layer turns into a 400 only because these raise ValueError; a bare
    # KeyError surfaced as an opaque 500.
    with pytest.raises(ValueError, match="unknown group"):
        await projects.delete_group(group.id)
    with pytest.raises(ValueError, match="unknown group"):
        await projects.update_group(group.id, name="Gone")
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


async def test_project_use_is_monotone_shared_and_survives_unrelated_updates(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    assert project.last_used_at == 0.0

    touched = await projects.touch_used(project.id, used_at=2_000.0)
    assert touched.last_used_at == 2_000.0
    assert (await projects.touch_used(project.id, used_at=1_000.0)).last_used_at == 2_000.0
    assert (await projects.update(project.id, name="Renamed")).last_used_at == 2_000.0
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened = ProjectManager(reopened_history)
    await reopened.start()
    assert reopened.projects[project.id].last_used_at == 2_000.0
    reopened_history.close()


async def test_last_used_migration_seeds_from_latest_non_imported_session_start(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "used"
    root.mkdir()
    used = await projects.create("Used", str(root))
    quiet_root = tmp_path / "quiet"
    quiet_root.mkdir()
    quiet = await projects.create("Quiet", str(quiet_root))
    await history.session_started(
        SessionRecord(
            "s1",
            "agent",
            used.id,
            "claude",
            "native-1",
            str(root),
            "claude.exe",
            [],
            created_at=1_700_000_000.0,
        ),
        None,
    )
    history._db.execute("ALTER TABLE projects DROP COLUMN last_used_at")
    history._db.commit()
    history.close()

    migrated = HistoryIndex(path)
    records = {item.id: item for item in await migrated.list_projects()}
    assert records[used.id].last_used_at == 1_700_000_000.0
    assert records[quiet.id].last_used_at == 0.0
    migrated.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_project_use_api_persists_and_broadcasts_shared_recency(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    events = EventBus(sink=history.append_event)
    subscriber = events.subscribe(name="recency-test")
    app = web.Application(middlewares=[error_middleware])
    app[keys.PROJECTS] = projects
    app[keys.EVENTS] = events
    app.router.add_post("/projects/{project_id}/used", record_project_use)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            f"/projects/{project.id}/used", json={"reason": "prompt_submitted"}
        )
        payload = await response.json()
        invalid = await client.post(f"/projects/{project.id}/used", json={"reason": "focus"})

    event = subscriber.get_nowait()
    assert response.status == 200
    assert payload["project_id"] == project.id
    assert payload["last_used_at"] == project.last_used_at
    assert project.last_used_at > 0
    assert event.type == "project_used"
    assert event.payload == {
        "project_id": project.id,
        "last_used_at": project.last_used_at,
        "reason": "prompt_submitted",
    }
    assert invalid.status == 400
    events.unsubscribe(subscriber)
    history.close()


@pytest.mark.filterwarnings("ignore:It is recommended to use web.AppKey instances for keys")
async def test_project_removal_api_reports_live_session_conflict(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    projects = ProjectManager(history)
    await projects.start()
    root = tmp_path / "repo"
    root.mkdir()
    project = await projects.create("Main", str(root))
    record = SessionRecord(
        "live-session",
        "Working",
        project.id,
        "claude",
        "native",
        project.root,
        "claude",
        [],
        state="working",
    )
    events = EventBus(sink=history.append_event)
    app = web.Application(middlewares=[error_middleware])
    app[keys.PROJECTS] = projects
    app[keys.HISTORY] = history
    app[keys.SESSIONS] = SimpleNamespace(
        sessions={record.id: SimpleNamespace(record=record)}
    )
    app[keys.EVENTS] = events
    app.router.add_delete("/projects/{project_id}", delete_project)

    async with TestClient(TestServer(app)) as client:
        response = await client.delete(f"/projects/{project.id}")
        payload = await response.json()

    assert response.status == 409
    assert payload == {
        "error": "1 live session must be closed before removal",
        "code": "project_has_live_sessions",
        "live_sessions": [
            {"id": record.id, "name": record.name, "state": record.state}
        ],
    }
    assert project.id in projects.projects
    history.close()


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


async def test_project_removal_preserves_history_and_restore_reuses_identity(
    tmp_path: Path,
) -> None:
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
    await projects.update(project.id, default_backend="claude", sidebar_visible=False)

    await projects.remove(project.id)
    assert project.id not in projects.projects
    assert await history.project_session_ids(project.id) == [session.id]
    assert await history.list_projects() == []

    registration = await projects.register("Main restored", str(root))
    restored = registration.project
    assert registration.restored is True
    assert restored.id == project.id
    assert restored.name == "Main restored"
    assert restored.default_backend == "claude"
    assert restored.sidebar_visible is True
    assert await history.project_session_ids(restored.id) == [session.id]
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
