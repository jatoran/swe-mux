from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.app_notes import read_space_note, write_space_note
from swe_mux.history import HistoryIndex
from swe_mux.layouts import (
    layout_terminal_ids,
    normalize_layout,
    remove_layout_leaf,
    stack_leaf,
)
from swe_mux.models import SessionRecord, SpaceRecord
from swe_mux.note_migration import migrate_space_notes, repair_misbound_project_notes
from swe_mux.projects import ProjectIdentity, project_scope_id
from swe_mux.server import note_shelf_items
from swe_mux.spaces import SpaceManager


async def test_scope_registry_separates_worktree_scope_from_repo_group(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    group = "repo-group"
    left = ProjectIdentity(
        project_scope_id(first), "first", str(first), "git-worktree", group, "shared"
    )
    right = ProjectIdentity(
        project_scope_id(second), "second", str(second), "git-worktree", group, "shared"
    )
    await history.register_project_scope(left)
    await history.register_project_scope(right)
    scopes = await history.project_scopes()
    assert {item["id"] for item in scopes} == {left.id, right.id}
    assert {item["repo_group_id"] for item in scopes} == {group}
    history.close()


async def test_space_notes_are_app_owned_and_project_scope_has_no_anchor_blocker(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()
    root = tmp_path / "project"
    root.mkdir()
    scope = ProjectIdentity(project_scope_id(root), "project", str(root), "cwd")
    await history.register_project_scope(scope)
    note = write_space_note(tmp_path / "data", "default", "Main", "hello", "missing")
    assert note["storage"] == "app-data"
    assert ".swe-mux" not in note["path"]
    assert read_space_note(tmp_path / "data", "default", "Main")["markdown"] == "hello"
    result = await history.forget_project_scope(scope.id)
    assert result["forgotten"]
    history.close()


async def test_retired_anchor_update_is_atomic(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()

    with pytest.raises(ValueError, match="anchors have been retired"):
        await spaces.update("default", name="must-not-stick", anchor_mode="fixed")

    assert spaces.spaces["default"].name == "Main"
    assert spaces.spaces["default"].anchor_mode == "auto"
    history.close()


async def test_default_cwd_does_not_create_or_anchor_a_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    history = HistoryIndex(tmp_path / "mux.db")
    await history.ensure_default_space(SpaceRecord("default", "Main", 0))
    stored = (await history.list_spaces())[0]
    stored.default_cwd = str(root)
    await history.upsert_space(stored)

    spaces = SpaceManager(history)
    await spaces.start()

    assert spaces.spaces["default"].default_cwd == str(root)
    assert not await history.project_scope(project_scope_id(root))
    assert "anchor_mode" not in spaces.spaces["default"].snapshot()
    history.close()


async def test_legacy_project_space_note_is_copied_and_unbound(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    scope_root = tmp_path / "project"
    scope_root.mkdir()
    scope = ProjectIdentity(project_scope_id(scope_root), "project", str(scope_root), "cwd")
    await history.register_project_scope(scope)
    await history.ensure_default_space(SpaceRecord("default", "Main", 0))
    spaces = SpaceManager(history)
    await spaces.start()
    legacy = scope_root / ".swe-mux" / "notes" / "spaces" / "default.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy body", encoding="utf-8")
    await history.bind_artifact(
        artifact_id="legacy-space-note",
        kind="note",
        owner_type="space",
        owner_id="default",
        owner_label="Main",
        project_scope_id=scope.id,
        relative_path=".swe-mux/notes/spaces/default.md",
    )
    result = await migrate_space_notes(tmp_path / "data", history, spaces)
    assert result == {"copied": 1, "released": 1}
    assert read_space_note(tmp_path / "data", "default", "Main")["markdown"] == "legacy body"
    assert legacy.exists()
    assert await history.artifacts() == []
    assert spaces.spaces["default"].anchor_mode == "none"
    history.close()


def test_v3_stack_validation_preserves_order_and_active_identity() -> None:
    layout = normalize_layout(
        {
            "version": 3,
            "root": {
                "type": "stack",
                "id": "tabs-a",
                "active_child_id": "two",
                "children": [
                    {"type": "leaf", "kind": "terminal", "id": "one"},
                    {"type": "leaf", "kind": "terminal", "id": "two"},
                ],
            },
        }
    )
    assert layout_terminal_ids(layout) == ["one", "two"]
    assert layout["root"]["active_child_id"] == "two"
    # Notes live in the space note workspace, never in a terminal tab region.
    with pytest.raises(ValueError, match="terminal and preview leaves only"):
        normalize_layout(
            {
                "version": 3,
                "root": {
                    "type": "stack",
                    "id": "bad",
                    "active_child_id": "note",
                    "children": [{"type": "leaf", "kind": "note", "id": "note"}],
                },
            }
        )


def test_stacks_accept_preview_tabs_beside_their_session() -> None:
    layout = normalize_layout(
        {
            "version": 5,
            "root": {
                "type": "stack",
                "id": "tabs-a",
                "active_child_id": "preview-1",
                "children": [
                    {"type": "leaf", "kind": "terminal", "id": "one"},
                    {"type": "leaf", "kind": "preview", "id": "preview-1"},
                ],
            },
        }
    )
    assert layout_terminal_ids(layout) == ["one"]
    assert [child["kind"] for child in layout["root"]["children"]] == ["terminal", "preview"]
    assert layout["root"]["active_child_id"] == "preview-1"


def _single_terminal_layout(session_id: str) -> dict[str, Any]:
    return normalize_layout(
        {"version": 5, "root": {"type": "leaf", "kind": "terminal", "id": session_id}}
    )


def test_stack_leaf_groups_a_preview_beside_a_bare_session() -> None:
    layout = _single_terminal_layout("a")
    grouped = stack_leaf(layout, "preview", "preview-1", target_id="a")
    assert grouped is not None
    root = grouped["root"]
    assert root["type"] == "stack"
    assert [(child["kind"], child["id"]) for child in root["children"]] == [
        ("terminal", "a"),
        ("preview", "preview-1"),
    ]
    assert root["active_child_id"] == "preview-1"


def test_stack_leaf_appends_into_the_sessions_existing_tab_region() -> None:
    layout = normalize_layout(
        {
            "version": 5,
            "root": {
                "type": "stack",
                "id": "tabs-a",
                "active_child_id": "a",
                "children": [
                    {"type": "leaf", "kind": "terminal", "id": "a"},
                    {"type": "leaf", "kind": "terminal", "id": "b"},
                ],
            },
        }
    )
    grouped = stack_leaf(layout, "preview", "preview-1", target_id="b")
    assert grouped is not None
    root = grouped["root"]
    assert root["id"] == "tabs-a"  # joins the existing region, does not create a new one
    assert [child["id"] for child in root["children"]] == ["a", "b", "preview-1"]
    assert root["active_child_id"] == "preview-1"


def test_stack_leaf_reports_when_the_session_has_no_terminal_to_group_with() -> None:
    layout = _single_terminal_layout("a")
    # The caller falls back to an ordinary split attach when this returns None.
    assert stack_leaf(layout, "preview", "preview-1", target_id="absent") is None
    empty = normalize_layout({"version": 5, "root": None})
    seeded = stack_leaf(empty, "preview", "preview-1", target_id="absent")
    assert seeded is not None and seeded["root"] == {
        "type": "leaf", "kind": "preview", "id": "preview-1",
    }


def test_stack_leaf_rejects_notes_and_ignores_duplicates() -> None:
    layout = _single_terminal_layout("a")
    with pytest.raises(ValueError, match="cannot hold note leaves"):
        stack_leaf(layout, "note", "spaces:one", target_id="a")
    grouped = stack_leaf(layout, "preview", "preview-1", target_id="a")
    assert grouped is not None
    again = stack_leaf(grouped, "preview", "preview-1", target_id="a")
    assert again is not None
    assert [child["id"] for child in again["root"]["children"]] == ["a", "preview-1"]


async def test_misbound_project_notes_are_released_and_removed_from_layout(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()
    expected_root = tmp_path / "expected"
    wrong_root = tmp_path / "wrong"
    expected_root.mkdir()
    wrong_root.mkdir()
    expected = ProjectIdentity(
        project_scope_id(expected_root), "expected", str(expected_root), "cwd"
    )
    wrong = ProjectIdentity(project_scope_id(wrong_root), "wrong", str(wrong_root), "cwd")
    await history.register_project_scope(expected)
    await history.register_project_scope(wrong)
    wrong_note = wrong_root / ".swe-mux" / "notes" / "project.md"
    wrong_note.parent.mkdir(parents=True)
    wrong_note.write_text("keep me", encoding="utf-8")
    await history.bind_artifact(
        artifact_id="misbound",
        kind="note",
        owner_type="project",
        owner_id=expected.id,
        owner_label=expected.label,
        project_scope_id=wrong.id,
        relative_path=".swe-mux/notes/project.md",
    )
    await spaces.update(
        "default",
        layout={
            "version": 3,
            "root": {
                "type": "split",
                "direction": "horizontal",
                "ratio": 0.6,
                "first": {"type": "leaf", "kind": "terminal", "id": "shell"},
                "second": {
                    "type": "leaf",
                    "kind": "note",
                    "id": f"projects:{expected.id}",
                },
            },
        },
    )

    result = await repair_misbound_project_notes(history, spaces)

    assert result == {"released": 1, "layouts": 1}
    assert await history.artifacts() == []
    assert layout_terminal_ids(spaces.spaces["default"].layout) == ["shell"]
    assert wrong_note.read_text(encoding="utf-8") == "keep me"
    history.close()


def test_remove_layout_leaf_collapses_its_split() -> None:
    layout = {
        "version": 3,
        "root": {
            "type": "split",
            "direction": "horizontal",
            "ratio": 0.5,
            "first": {"type": "leaf", "kind": "terminal", "id": "shell"},
            "second": {"type": "leaf", "kind": "note", "id": "projects:stale"},
        },
    }
    cleaned = remove_layout_leaf(layout, "note", "projects:stale")
    assert cleaned["root"] == {"type": "leaf", "kind": "terminal", "id": "shell"}


async def test_note_shelf_unifies_space_project_run_and_recovered_notes(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()
    data_dir = tmp_path / "data"
    write_space_note(data_dir, "default", "Main", "# Daily log\nSpace detail", "missing")

    root = tmp_path / "project"
    root.mkdir()
    scope = ProjectIdentity(project_scope_id(root), "project", str(root), "cwd")
    await history.register_project_scope(scope)
    project_note = root / ".swe-mux" / "notes" / "project.md"
    project_note.parent.mkdir(parents=True)
    project_note.write_text("# Project plan\nShip it", encoding="utf-8")
    await history.bind_artifact(
        artifact_id="project-note",
        kind="note",
        owner_type="project",
        owner_id=scope.id,
        owner_label="project",
        project_scope_id=scope.id,
        relative_path=".swe-mux/notes/project.md",
    )

    run = SessionRecord(
        "run-1", "claude-a", "default", "claude", "native-1", str(root), "claude", []
    )
    run.project_scope_id = scope.id
    run.project_label = scope.label
    run.project_root = scope.root
    run.state = "exited"
    await history.session_started(run, None)
    run_note = root / ".swe-mux" / "notes" / "sessions" / "run-1.md"
    run_note.parent.mkdir(parents=True)
    run_note.write_text("# Fix parser\nRemember the edge case", encoding="utf-8")
    await history.bind_artifact(
        artifact_id="run-note",
        kind="note",
        owner_type="session",
        owner_id="run-1",
        owner_label="claude-a",
        project_scope_id=scope.id,
        relative_path=".swe-mux/notes/sessions/run-1.md",
    )
    recovered = root / ".swe-mux" / "notes" / "sessions" / "unknown.md"
    recovered.write_text("orphaned but visible", encoding="utf-8")

    request = SimpleNamespace(
        app={
            "history": history,
            "spaces": spaces,
            "sessions": SimpleNamespace(sessions={}),
            "config": SimpleNamespace(data_dir=data_dir),
        }
    )
    items = await note_shelf_items(request)
    assert {item["category"] for item in items} == {
        "space",
        "project",
        "agent-run",
        "recovered",
    }
    assert next(item for item in items if item["category"] == "space")["content_title"] == (
        "Daily log"
    )
    ended = next(item for item in items if item["category"] == "agent-run")
    assert ended["owner_label"] == "claude-a"
    assert ended["openable"] is True
    orphan = next(item for item in items if item["category"] == "recovered")
    assert orphan["openable"] is False
    assert "orphaned but visible" in orphan["excerpt"]
    history.close()


def test_frontend_exposes_global_notes_shelf_and_keeps_projects_project_only() -> None:
    app = Path("frontend/src/App.tsx").read_text(encoding="utf-8")
    shelf = Path("frontend/src/NotesShelf.tsx").read_text(encoding="utf-8")
    projects = Path("frontend/src/ProjectRegistry.tsx").read_text(encoding="utf-8")
    assert 'class="notes-shelf-trigger"' in app
    assert "Browse all notes" in app
    assert "/api/note-shelf" in shelf
    assert "Agent runs" in shelf and "Recovered" in shelf
    assert "APP-OWNED SPACE NOTES" not in projects
    assert "/api/space-notes" not in projects
    assert "project settings" in projects
    assert "onOpenSettings(selected)" in projects
