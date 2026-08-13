from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.config import Config
from swe_mux.history import HistoryIndex
from swe_mux.models import ProjectRecord
from swe_mux.project_files import parse_project_config, serialize_project_config
from swe_mux.projects import ProjectManager
from swe_mux.prompt_library import PromptLibrary, parse_template
from swe_mux.server import create_app


@pytest.mark.asyncio
async def test_project_reorder_is_normalized_persistent_and_optimistic(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    manager = ProjectManager(history)
    await manager.start()
    roots = [tmp_path / name for name in ("one", "two", "three")]
    for root in roots:
        root.mkdir()
    projects = [await manager.create(root.name, str(root)) for root in roots]
    initial = [item.id for item in manager.ordered_projects()]
    reordered = [projects[2].id, projects[0].id, projects[1].id]

    result = await manager.reorder(reordered, expected_order=initial)
    assert [item.id for item in result] == reordered
    assert [item.position for item in result] == [0, 1, 2]
    with pytest.raises(ValueError, match="order changed"):
        await manager.reorder(initial, expected_order=initial)

    reopened = ProjectManager(history)
    await reopened.start()
    assert [item.id for item in reopened.ordered_projects()] == reordered
    history.close()


def test_project_config_accepts_only_typed_portable_phase3_options() -> None:
    values = {
        "default_shell_profile": "pwsh",
        "preferred_backend": "codex",
        "prompt_library_scope": "both",
        "notification_sounds_enabled": False,
        "ignore_patterns": ["dist", "*.tmp"],
    }
    assert parse_project_config(serialize_project_config(values)) == values
    with pytest.raises(ValueError, match="preferred_backend"):
        serialize_project_config({"preferred_backend": "other"})
    with pytest.raises(ValueError, match="unknown project fields"):
        serialize_project_config({"command": "pwsh -c bad"})


def test_prompt_library_round_trip_conflicts_usage_and_revision_safety(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / ".swe-mux").mkdir(parents=True)
    (root / ".swe-mux" / "config.toml").write_bytes(
        serialize_project_config({"prompt_library_scope": "both"})
    )
    project = ProjectRecord("project", "Project", str(root), 0)
    library = PromptLibrary(tmp_path / "data")
    values = {
        "title": "Review change",
        "body": "Review {{area}} for regressions.",
        "tags": ["review"],
        "backends": ["claude", "codex"],
    }
    global_item = library.create("global", values, project)
    project_item = library.create("project", {**values, "id": global_item["id"]}, project)

    snapshot = library.list(project)
    assert len(snapshot["items"]) == 2
    assert all(item["conflict"] for item in snapshot["items"])
    assert global_item["variables"] == ["area"]
    assert (
        parse_template(library.global_dir.joinpath(f"{global_item['id']}.md").read_bytes())[
            "body"
        ].strip()
        == values["body"]
    )

    library.set_favorite(global_item["key"], True)
    library.record_use(global_item["key"])
    favorite = library.list(project)["items"][0]
    assert favorite["key"] == global_item["key"]
    assert favorite["favorite"] is True
    assert favorite["use_count"] == 1

    updated = library.update(
        "project",
        project_item["id"],
        {**values, "title": "Review carefully"},
        project_item["revision"],
        project,
    )
    with pytest.raises(ValueError, match="changed externally"):
        library.update("project", project_item["id"], values, project_item["revision"], project)
    library.delete("project", updated["id"], updated["revision"], project)
    assert len(library.list(project)["items"]) == 1


def test_prompt_body_has_no_trailing_newline_after_round_trip(tmp_path: Path) -> None:
    library = PromptLibrary(tmp_path / "data")
    created = library.create("global", {"title": "Insert me", "body": "just text"})
    assert created["body"] == "just text"
    reloaded = parse_template(library.global_dir.joinpath(f"{created['id']}.md").read_bytes())
    # No trailing newline: inserting the prompt must not submit it.
    assert reloaded["body"] == "just text"
    # A body typed with a trailing newline is normalized away, not preserved.
    trailing = library.create("global", {"title": "Trailing", "body": "line one\n"})
    assert trailing["body"] == "line one"
    reloaded_trailing = parse_template(
        library.global_dir.joinpath(f"{trailing['id']}.md").read_bytes()
    )
    assert reloaded_trailing["body"] == "line one"


def test_prompt_library_rejects_terminal_controls_and_disabled_project_scope(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    (root / ".swe-mux").mkdir(parents=True)
    (root / ".swe-mux" / "config.toml").write_bytes(
        serialize_project_config({"prompt_library_scope": "global"})
    )
    project = ProjectRecord("project", "Project", str(root), 0)
    library = PromptLibrary(tmp_path / "data")
    with pytest.raises(ValueError, match="control"):
        library.create("global", {"title": "bad", "body": "hello\x1b[31m"}, project)
    with pytest.raises(ValueError, match="scope is global"):
        library.create("project", {"title": "bad", "body": "hello"}, project)


def test_phase3_routes_are_registered(tmp_path: Path) -> None:
    app = create_app(Config(data_dir=tmp_path))
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("PUT", "/api/projects/order") in routes
    assert ("POST", "/api/projects/{project_id}/used") in routes
    # Sidebar sections reorder the same way Projects inside them do.
    assert ("PUT", "/api/project-groups/order") in routes
    assert ("GET", "/api/prompts") in routes
    assert ("POST", "/api/prompts") in routes
    assert ("PUT", "/api/prompts/{scope}/{template_id}") in routes
    assert ("PATCH", "/api/prompts/{scope}/{template_id}/favorite") in routes
    assert ("GET", "/api/diagnostics/notifications") in routes
    assert ("GET", "/api/diagnostics/network") in routes
    assert ("DELETE", "/api/diagnostics/network") in routes
    assert any(canonical == "/notification-sounds" for _, canonical in routes)


@pytest.mark.asyncio
async def test_notification_sound_route_serves_the_packaged_audio(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    sound_dir = frontend / "notification-sounds"
    sound_dir.mkdir(parents=True)
    payload = b"ID3\x04\x00\x00preview-audio"
    (sound_dir / "two-tone.mp3").write_bytes(payload)
    (frontend / "index.html").write_text("app shell", encoding="utf-8")
    client = TestClient(
        TestServer(create_app(Config(data_dir=tmp_path / "data"), frontend_dir=frontend))
    )
    await client.start_server()
    try:
        response = await client.get("/notification-sounds/two-tone.mp3")
        assert response.status == 200
        assert await response.read() == payload
        assert response.content_type == "audio/mpeg"
    finally:
        await client.close()
