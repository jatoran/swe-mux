from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.event_bus import EventBus
from swe_mux.git_projects import ProjectIdentity
from swe_mux.project_files import (
    create_note,
    migrate_legacy_notes,
    note_header,
    note_path,
    project_note_summaries,
    read_note,
    write_note,
)
from swe_mux.server import (
    create_project_note,
    delete_project_note,
    error_middleware,
    get_note,
    list_notes,
    patch_note,
    put_note,
)


def _identity(root: Path) -> ProjectIdentity:
    return ProjectIdentity("project-one", "Project One", str(root), "test")


def _project(root: Path):  # type: ignore[no-untyped-def]
    return SimpleNamespace(id="project-one", root=str(root), name="Project One")


def _app(root: Path) -> web.Application:
    project = _project(root)
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(
        projects={project.id: project}, ordered_projects=lambda: [project]
    )
    app["sessions"] = SimpleNamespace(sessions={})
    app["events"] = EventBus()
    app.router.add_get("/notes", list_notes)
    app.router.add_post("/projects/{project_id}/notes", create_project_note)
    app.router.add_get("/projects/{project_id}/notes/{note_id}", get_note)
    app.router.add_put("/projects/{project_id}/notes/{note_id}", put_note)
    app.router.add_patch("/projects/{project_id}/notes/{note_id}", patch_note)
    app.router.add_delete("/projects/{project_id}/notes/{note_id}", delete_project_note)
    return app


async def test_project_notes_create_list_rename_save_and_delete(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    app = _app(root)

    async with TestClient(TestServer(app)) as client:
        created = await client.post(
            "/projects/project-one/notes", json={"title": "Release plan"}
        )
        created_payload = await created.json()
        note_id = created_payload["id"]
        listing = await (await client.get("/notes?project_id=project-one")).json()
        saved = await client.put(
            f"/projects/project-one/notes/{note_id}",
            json={"markdown": "Ship carefully.\n", "revision": created_payload["revision"]},
        )
        saved_payload = await saved.json()
        renamed = await client.patch(
            f"/projects/project-one/notes/{note_id}",
            json={"title": "Release checklist", "revision": saved_payload["revision"]},
        )
        renamed_payload = await renamed.json()
        stale = await client.delete(
            f"/projects/project-one/notes/{note_id}",
            json={"revision": created_payload["revision"]},
        )
        deleted = await client.delete(
            f"/projects/project-one/notes/{note_id}",
            json={"revision": renamed_payload["revision"]},
        )

    assert created.status == 201
    assert [item["note_id"] for item in listing["items"]] == [note_id]
    assert listing["items"][0]["title"] == "Release plan"
    assert saved.status == 200
    assert renamed_payload["title"] == "Release checklist"
    assert stale.status == 409
    assert deleted.status == 200
    assert not note_path(root, note_id).exists()


async def test_note_change_events_use_one_generic_event_type(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    app = _app(root)
    events: EventBus = app["events"]

    async with TestClient(TestServer(app)) as client:
        queue = events.subscribe()
        created = await client.post(
            "/projects/project-one/notes", json={"title": "Working context"}
        )
        payload = await created.json()
        event = await queue.get()

    assert event.type == "note_changed"
    assert event.payload == {
        "project_id": "project-one",
        "note_id": payload["id"],
        "revision": payload["revision"],
    }


async def test_generic_note_round_trip_normalizes_headers_and_line_endings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    project = _identity(root)
    created = await create_note(root, "Deployment", project=project)
    header = note_header(created["id"], "Deployment")

    saved = await write_note(
        root,
        created["id"],
        header + "one\r\ntwo\r\n",
        created["revision"],
        project=project,
    )

    assert saved["markdown"] == "one\ntwo\n"
    assert note_path(root, created["id"]).read_bytes() == (
        note_header(created["id"], "Deployment", created_at=saved["created_at"])
        + "one\ntwo\n"
    ).encode("utf-8")


async def test_note_read_strips_crlf_and_stacked_headers(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    note_id = "durable-note"
    header = note_header(note_id, "Durable note")
    path = note_path(root, note_id)
    path.parent.mkdir(parents=True)
    path.write_bytes((header + (header + "real content\n").replace("\n", "\r\n")).encode())

    loaded = await read_note(root, note_id, project=_identity(root))

    assert loaded["markdown"] == "real content\n"


def test_legacy_session_notes_migrate_to_flat_collection(tmp_path: Path) -> None:
    root = tmp_path / "project"
    legacy = root / ".swe-mux" / "notes" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "terminal-one.md").write_text(
        '---\nswe_mux_note = 1\nkind = "sessions"\nid = "terminal-one"\n---\n'
        "deployment context\n",
        encoding="utf-8",
    )
    (legacy / "empty.md").write_text(
        '---\nswe_mux_note = 1\nkind = "sessions"\nid = "empty"\n---\n',
        encoding="utf-8",
    )

    result = migrate_legacy_notes(root, legacy_titles={"terminal-one": "Deploy agent"})
    summaries = project_note_summaries(
        root, default_note_id="project-one", default_title="Project One notes"
    )

    assert result == {"migrated": 1, "archived_empty": 1}
    assert [(item["note_id"], item["excerpt"]) for item in summaries] == [
        ("terminal-one", "deployment context")
    ]
    assert summaries[0]["title"].startswith("Deploy agent - ")
    assert summaries[0]["origin_session_id"] == "terminal-one"
    assert not legacy.exists() or not list(legacy.glob("*.md"))
    assert (root / ".swe-mux" / "notes" / "legacy" / "session-notes").is_dir()
    assert (root / ".swe-mux" / "notes" / "legacy" / "empty-session-notes").is_dir()


def test_listing_includes_explicit_empty_notes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    path = note_path(root, "empty-note")
    path.parent.mkdir(parents=True)
    path.write_text(note_header("empty-note", "Empty note"), encoding="utf-8")

    summaries = project_note_summaries(
        root, default_note_id="project-one", default_title="Project One notes"
    )

    assert [(item["title"], item["excerpt"]) for item in summaries] == [("Empty note", "")]
