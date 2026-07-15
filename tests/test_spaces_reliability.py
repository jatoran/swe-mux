from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.history import HistoryIndex
from swe_mux.layouts import MAX_LAYOUT_LEAVES, layout_terminal_ids, normalize_layout
from swe_mux.models import SessionRecord
from swe_mux.spaces import SpaceManager


async def test_layout_is_versioned_revisioned_and_rejects_stale_writes(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()

    updated = await spaces.update(
        "default", layout={"version": 1, "panes": ["one", "one"]}, layout_revision=0
    )
    assert updated.layout == {
        "version": 5,
        "root": {"type": "leaf", "kind": "terminal", "id": "one"},
        "note_workspace": {
            "open_ids": [],
            "active_id": None,
            "size": 0.38,
            "visible": False,
            "mode": "dock",
        },
    }
    assert updated.layout_revision == 1
    with pytest.raises(ValueError, match="stale layout revision"):
        await spaces.update(
            "default", layout={"version": 1, "panes": ["two"]}, layout_revision=0
        )
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened_spaces = SpaceManager(reopened_history)
    await reopened_spaces.start()
    assert layout_terminal_ids(reopened_spaces.spaces["default"].layout) == ["one"]
    assert reopened_spaces.spaces["default"].layout_revision == 1
    reopened_history.close()


async def test_space_notes_open_mode_persists_and_inherits_global(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()

    assert spaces.spaces["default"].notes_open_mode is None
    updated = await spaces.update("default", notes_open_mode="popout")
    assert updated.notes_open_mode == "popout"
    history.close()

    reopened_history = HistoryIndex(tmp_path / "mux.db")
    reopened_spaces = SpaceManager(reopened_history)
    await reopened_spaces.start()
    assert reopened_spaces.spaces["default"].notes_open_mode == "popout"
    await reopened_spaces.update("default", notes_open_mode=None)
    assert reopened_spaces.spaces["default"].notes_open_mode is None
    with pytest.raises(ValueError, match="notes_open_mode"):
        await reopened_spaces.update("default", notes_open_mode="sideways")
    reopened_history.close()


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
    assert layout["version"] == 5
    assert layout_terminal_ids(layout) == ["left"]
    assert layout["note_workspace"]["open_ids"] == ["notes"]
    assert layout["note_workspace"]["active_id"] == "notes"
    assert layout["note_workspace"]["visible"] is True
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


async def test_deleting_space_rehomes_ended_history_records(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    spaces = SpaceManager(history)
    await spaces.start()
    space = await spaces.create("Disposable")
    session = SessionRecord(
        "ended", "agent", space.id, "claude", "native", str(tmp_path), "claude", []
    )
    await history.session_started(session, str(tmp_path / "transcript.jsonl"))

    await spaces.delete(space.id)

    assert (await history.history_entry("ended"))["space_id"] == "default"
    history.close()
