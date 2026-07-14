from __future__ import annotations

from pathlib import Path

from .app_notes import note_body, read_space_note, write_space_note
from .history import HistoryIndex
from .layouts import remove_layout_leaf
from .project_files import safe_note_filename
from .spaces import SpaceManager


async def migrate_space_notes(
    data_dir: Path, history: HistoryIndex, spaces: SpaceManager
) -> dict[str, int]:
    """Copy legacy project-owned space notes to app data without deleting source files."""
    copied = 0
    released = 0
    scopes = {
        item["id"]: item
        for item in await history.project_scopes(include_hidden=True)
    }
    artifacts = [
        item
        for item in await history.artifacts()
        if item["kind"] == "note" and item["owner_type"] == "space"
    ]
    migrated_ids: set[str] = set()
    for artifact in artifacts:
        identity = str(artifact["owner_id"])
        label = str(
            artifact.get("owner_label")
            or getattr(spaces.spaces.get(identity), "name", identity)
        )
        scope = scopes.get(artifact["project_scope_id"])
        source = Path(scope["root"]) / artifact["relative_path"] if scope else None
        current = read_space_note(data_dir, identity, label)
        if not current["exists"] and source and source.is_file():
            try:
                markdown = note_body(source.read_text(encoding="utf-8"))
                write_space_note(data_dir, identity, label, markdown, "missing")
                copied += 1
            except (OSError, UnicodeDecodeError, ValueError):
                continue
        if current["exists"] or read_space_note(data_dir, identity, label)["exists"]:
            await history.delete_artifact_binding(str(artifact["id"]))
            released += 1
            migrated_ids.add(identity)

    # Early Phase 5.5 builds could create the file before recording an artifact.
    # Recover a unique legacy file for every known space, but never guess on conflicts.
    for identity, space in spaces.spaces.items():
        if identity in migrated_ids or read_space_note(data_dir, identity, space.name)["exists"]:
            continue
        relative = Path(".swe-mux") / "notes" / "spaces" / (
            f"{safe_note_filename(identity)}.md"
        )
        candidates = [
            Path(scope["root"]) / relative
            for scope in scopes.values()
            if (Path(scope["root"]) / relative).is_file()
        ]
        if len(candidates) == 1:
            try:
                markdown = note_body(candidates[0].read_text(encoding="utf-8"))
                write_space_note(data_dir, identity, space.name, markdown, "missing")
                copied += 1
            except (OSError, UnicodeDecodeError, ValueError):
                pass

    await spaces.retire_anchors()
    return {"copied": copied, "released": released}


async def repair_misbound_project_notes(
    history: HistoryIndex, spaces: SpaceManager
) -> dict[str, int]:
    """Release early-build bindings that point a project identity at another scope."""
    stale = [
        item
        for item in await history.artifacts()
        if item["kind"] == "note"
        and item["owner_type"] == "project"
        and item["owner_id"] != item["project_scope_id"]
    ]
    stale_owner_ids = {str(item["owner_id"]) for item in stale}
    for artifact in stale:
        await history.delete_artifact_binding(str(artifact["id"]))

    repaired_layouts = 0
    for space in spaces.spaces.values():
        layout = space.layout
        for owner_id in stale_owner_ids:
            layout = remove_layout_leaf(layout, "note", f"projects:{owner_id}")
        if layout != space.layout:
            await spaces.update(space.id, layout=layout)
            repaired_layouts += 1
    return {"released": len(stale), "layouts": repaired_layouts}
