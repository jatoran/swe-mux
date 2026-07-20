from __future__ import annotations

from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.server import (
    error_middleware,
    get_project_note,
    get_session_note,
    initialize_session_note,
    put_project_note,
    put_session_note,
)


async def test_project_note_change_event_carries_saved_revision(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "project"
    root.mkdir()
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    events = EventBus()
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app["events"] = events
    app.router.add_get("/projects/{project_id}/note", get_project_note)
    app.router.add_put("/projects/{project_id}/note", put_project_note)

    async with TestClient(TestServer(app)) as client:
        loaded = await client.get("/projects/project-one/note")
        loaded_payload = await loaded.json()
        event_queue = events.subscribe()
        saved = await client.put(
            "/projects/project-one/note",
            json={"markdown": "Shared project context\n", "revision": loaded_payload["revision"]},
        )
        saved_payload = await saved.json()
        changed = await event_queue.get()

    assert saved.status == 200
    assert changed.type == "project_note_changed"
    assert changed.payload == {"project_id": project.id, "revision": saved_payload["revision"]}


async def test_live_terminal_note_is_initialized_saved_and_revision_safe(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "project"
    root.mkdir()
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    live = SimpleNamespace(record=SimpleNamespace(id="terminal-one", project_id=project.id))
    history = HistoryIndex(tmp_path / "mux.db")
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app["sessions"] = SimpleNamespace(sessions={"terminal-one": live})
    app["history"] = history
    events = EventBus()
    app["events"] = events
    app.router.add_get("/projects/{project_id}/session-notes/{note_id}", get_session_note)
    app.router.add_post("/projects/{project_id}/session-notes/{note_id}", initialize_session_note)
    app.router.add_put("/projects/{project_id}/session-notes/{note_id}", put_session_note)

    async with TestClient(TestServer(app)) as client:
        created = await client.post("/projects/project-one/session-notes/terminal-one")
        created_payload = await created.json()
        event_queue = events.subscribe()
        saved = await client.put(
            "/projects/project-one/session-notes/terminal-one",
            json={"markdown": "Useful terminal context\n", "revision": created_payload["revision"]},
        )
        stale = await client.put(
            "/projects/project-one/session-notes/terminal-one",
            json={"markdown": "Overwrite", "revision": created_payload["revision"]},
        )
        loaded = await client.get("/projects/project-one/session-notes/terminal-one")
        unknown = await client.post("/projects/project-one/session-notes/not-a-session")

        assert created.status == 201
        assert saved.status == 200
        assert stale.status == 409
        assert (await stale.json())["code"] == "revision_conflict"
        assert (await loaded.json())["markdown"] == "Useful terminal context\n"
        assert unknown.status == 400
        changed = await event_queue.get()
        assert changed.type == "session_note_changed"
        assert changed.payload == {
            "project_id": project.id,
            "note_id": "terminal-one",
            "revision": (await saved.json())["revision"],
        }

    assert (
        (root / ".swe-mux" / "notes" / "sessions" / "terminal-one.md")
        .read_text(encoding="utf-8")
        .endswith("Useful terminal context\n")
    )
    history.close()


async def test_history_owned_session_can_initialize_its_note(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "project"
    root.mkdir()
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    history = HistoryIndex(tmp_path / "mux.db")
    record = SessionRecord(
        "terminal-history-id",
        "Archived agent",
        project.id,
        "claude",
        "native-id",
        str(root),
        "claude",
        [],
    )
    await history.session_started(record, str(tmp_path / "transcript.jsonl"))
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app["sessions"] = SimpleNamespace(sessions={})
    app["history"] = history
    app["events"] = EventBus()
    app.router.add_post("/projects/{project_id}/session-notes/{note_id}", initialize_session_note)

    async with TestClient(TestServer(app)) as client:
        response = await client.post("/projects/project-one/session-notes/terminal-history-id")
        assert response.status == 201

    assert (root / ".swe-mux" / "notes" / "sessions" / "terminal-history-id.md").is_file()
    history.close()
