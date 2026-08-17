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
    project_note_count,
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
        # A second note, because a Project's last note is deliberately undeletable.
        await client.post("/projects/project-one/notes", json={"title": "Retained"})
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
    assert note_id in [item["note_id"] for item in listing["items"]]
    assert [item["title"] for item in listing["items"] if item["note_id"] == note_id] == [
        "Release plan"
    ]
    assert saved.status == 200
    assert renamed_payload["title"] == "Release checklist"
    assert stale.status == 409
    assert deleted.status == 200
    assert not note_path(root, note_id).exists()


async def test_deleting_a_note_retires_it_to_the_project_note_trash(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    app = _app(root)

    async with TestClient(TestServer(app)) as client:
        created = await (
            await client.post("/projects/project-one/notes", json={"title": "Scratch work"})
        ).json()
        await client.post("/projects/project-one/notes", json={"title": "Retained"})
        saved = await (
            await client.put(
                f"/projects/project-one/notes/{created['id']}",
                json={"markdown": "recoverable body\n", "revision": created["revision"]},
            )
        ).json()
        response = await client.delete(
            f"/projects/project-one/notes/{created['id']}",
            json={"revision": saved["revision"]},
        )
        payload = await response.json()
        listing = await (await client.get("/notes?project_id=project-one")).json()

    trashed = Path(payload["trashed_path"])
    assert response.status == 200
    assert trashed.parent == root / ".swe-mux" / "notes" / "trash"
    assert "recoverable body" in trashed.read_text(encoding="utf-8")
    assert not note_path(root, created["id"]).exists()
    # The trash is not a second collection: a retired note leaves the listing entirely.
    assert [item["note_id"] for item in listing["items"]] != [created["id"]]
    assert created["id"] not in [item["note_id"] for item in listing["items"]]


async def test_trashing_two_notes_with_one_filename_keeps_both(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    trash = root / ".swe-mux" / "notes" / "trash"
    app = _app(root)

    async with TestClient(TestServer(app)) as client:
        keeper = await (
            await client.post("/projects/project-one/notes", json={"title": "Keeper"})
        ).json()
        first = await (
            await client.post("/projects/project-one/notes", json={"title": "Doomed"})
        ).json()
        await client.delete(
            f"/projects/project-one/notes/{first['id']}", json={"revision": first["revision"]}
        )
        # Reuse the retired note's exact filename, which is what a restored-then-deleted
        # note or a re-imported id looks like on disk.
        revived = note_path(root, first["id"])
        revived.write_text(note_header(first["id"], "Doomed again"), encoding="utf-8")
        reread = await (
            await client.get(f"/projects/project-one/notes/{first['id']}")
        ).json()
        await client.delete(
            f"/projects/project-one/notes/{first['id']}", json={"revision": reread["revision"]}
        )

    assert keeper["id"]
    assert len(list(trash.glob("*.md"))) == 2


async def test_a_project_keeps_its_last_note(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    app = _app(root)

    async with TestClient(TestServer(app)) as client:
        only = await (
            await client.post("/projects/project-one/notes", json={"title": "The only note"})
        ).json()
        refused = await client.delete(
            f"/projects/project-one/notes/{only['id']}", json={"revision": only["revision"]}
        )
        refusal = await refused.json()
        # A second note makes the first ordinary again: the rule is about the collection,
        # not about which note was seeded first.
        second = await (
            await client.post("/projects/project-one/notes", json={"title": "Second"})
        ).json()
        allowed = await client.delete(
            f"/projects/project-one/notes/{only['id']}", json={"revision": only["revision"]}
        )
        last = await client.delete(
            f"/projects/project-one/notes/{second['id']}", json={"revision": second["revision"]}
        )

    assert refused.status == 409
    assert refusal["code"] == "note_protected"
    assert allowed.status == 200
    assert last.status == 409
    assert note_path(root, second["id"]).is_file()


def test_project_note_count_sees_the_seeded_note_and_ignores_the_trash(tmp_path: Path) -> None:
    root = tmp_path / "project"
    notes = root / ".swe-mux" / "notes"
    (notes / "items").mkdir(parents=True)
    (notes / "trash").mkdir()

    assert project_note_count(root) == 0
    (notes / "project.md").write_text(note_header("project", "Seeded"), encoding="utf-8")
    assert project_note_count(root) == 1
    (notes / "items" / "extra.md").write_text(note_header("extra", "Extra"), encoding="utf-8")
    assert project_note_count(root) == 2
    (notes / "trash" / "retired.md").write_text(note_header("retired", "Gone"), encoding="utf-8")
    assert project_note_count(root) == 2


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
        "scope": "project",
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
    assert (root / ".swe-mux" / "notes" / ".gitignore").read_text(encoding="utf-8") == "*\n"
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


def test_listing_notes_does_not_recreate_a_missing_project_root(tmp_path: Path) -> None:
    root = tmp_path / "removed-project"

    summaries = project_note_summaries(
        root,
        default_note_id="project-id",
        default_title="Removed notes",
    )

    assert summaries == []
    assert not root.exists()
