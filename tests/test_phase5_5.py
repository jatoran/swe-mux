from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.app_notes import read_space_note, write_space_note
from swe_mux.history import HistoryIndex
from swe_mux.layouts import layout_terminal_ids, normalize_layout, remove_layout_leaf
from swe_mux.models import SpaceRecord
from swe_mux.note_migration import migrate_space_notes, repair_misbound_project_notes
from swe_mux.projects import ProjectIdentity, project_scope_id
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
    with pytest.raises(ValueError, match="terminal leaves only"):
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
