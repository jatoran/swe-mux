from __future__ import annotations

from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.project_files import note_exists, note_has_content, session_note_summaries
from swe_mux.server import (
    error_middleware,
    get_project_note,
    get_session_note,
    initialize_session_note,
    list_session_notes,
    put_project_note,
    put_session_note,
)


def _write_note(root, note_id: str, body: str) -> None:  # type: ignore[no-untyped-def]
    directory = root / ".swe-mux" / "notes" / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    header = f'---\nswe_mux_note = 1\nkind = "sessions"\nid = "{note_id}"\n---\n'
    (directory / f"{note_id}.md").write_text(header + body, encoding="utf-8")


def test_session_note_summaries_lists_only_notes_holding_text(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "project"
    root.mkdir()
    _write_note(root, "written-one", "deployment  steps\nsecond line\n")
    _write_note(root, "blank-one", "   \n\n")
    _write_note(root, "header-only", "")

    summaries = session_note_summaries(root)

    assert [item["note_id"] for item in summaries] == ["written-one"]
    # The excerpt collapses whitespace so a listing row stays one readable line.
    assert summaries[0]["excerpt"] == "deployment steps second line"
    assert summaries[0]["bytes"] > 0


def test_session_note_summaries_tolerates_a_project_without_notes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert session_note_summaries(tmp_path) == []


async def test_session_notes_listing_filters_by_project_and_labels_owners(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    _write_note(first, "terminal-live", "live terminal context\n")
    _write_note(first, "terminal-archived", "archived agent context\n")
    _write_note(second, "terminal-other", "other project context\n")
    projects = [
        SimpleNamespace(id="alpha", root=str(first), name="Alpha"),
        SimpleNamespace(id="beta", root=str(second), name="Beta"),
    ]
    history = HistoryIndex(tmp_path / "mux.db")
    archived = SessionRecord(
        "terminal-archived", "Archived agent", "alpha", "claude", "native", str(first), "claude", []
    )
    await history.session_started(archived, None)
    live = SimpleNamespace(
        record=SimpleNamespace(
            id="terminal-live", name="Live shell", backend="shell", state="running"
        )
    )
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(ordered_projects=lambda: projects)
    app["sessions"] = SimpleNamespace(sessions={"terminal-live": live})
    app["history"] = history
    app.router.add_get("/session-notes", list_session_notes)

    async with TestClient(TestServer(app)) as client:
        every = await (await client.get("/session-notes")).json()
        scoped = await (await client.get("/session-notes?project_id=beta")).json()
        unknown = await client.get("/session-notes?project_id=missing")

    rows = {item["note_id"]: item for item in every["items"]}
    assert set(rows) == {"terminal-live", "terminal-archived", "terminal-other"}
    # A live terminal supplies its own name; an ended one falls back to history.
    assert rows["terminal-live"]["owner_label"] == "Live shell"
    assert rows["terminal-live"]["owner_live"] is True
    assert rows["terminal-archived"]["owner_label"] == "Archived agent"
    assert rows["terminal-archived"]["owner_backend"] == "claude"
    assert rows["terminal-archived"]["owner_live"] is False
    # A note whose owner left no record anywhere still lists, under its identity.
    assert rows["terminal-other"]["owner_label"] == "terminal-other"
    assert rows["terminal-other"]["owner_known"] is False
    assert rows["terminal-other"]["project_name"] == "Beta"

    assert [item["note_id"] for item in scoped["items"]] == ["terminal-other"]
    assert unknown.status == 400
    history.close()


async def test_empty_note_exists_for_access_but_reports_no_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A note created by a stray click must stay usable yet invisible in the sidebar."""
    root = tmp_path / "project"
    root.mkdir()
    project = SimpleNamespace(id="project-one", root=str(root), name="Project One")
    live = SimpleNamespace(record=SimpleNamespace(id="terminal-one", project_id=project.id))
    history = HistoryIndex(tmp_path / "mux.db")
    app = web.Application(middlewares=[error_middleware])
    app["projects"] = SimpleNamespace(projects={project.id: project})
    app["sessions"] = SimpleNamespace(sessions={"terminal-one": live})
    app["history"] = history
    app["events"] = EventBus()
    app.router.add_post("/projects/{project_id}/session-notes/{note_id}", initialize_session_note)
    app.router.add_put("/projects/{project_id}/session-notes/{note_id}", put_session_note)

    async with TestClient(TestServer(app)) as client:
        created = await client.post("/projects/project-one/session-notes/terminal-one")
        revision = (await created.json())["revision"]

        # Created but untouched: readable and writable, but not a sidebar row.
        assert note_exists(project.root, "sessions", "terminal-one")
        assert not note_has_content(project.root, "sessions", "terminal-one")

        blank = await client.put(
            "/projects/project-one/session-notes/terminal-one",
            json={"markdown": "   \n\n", "revision": revision},
        )
        assert not note_has_content(project.root, "sessions", "terminal-one")

        written = await client.put(
            "/projects/project-one/session-notes/terminal-one",
            json={"markdown": "real content\n", "revision": (await blank.json())["revision"]},
        )
        assert written.status == 200
        # The cached answer must follow the file rather than the first read.
        assert note_has_content(project.root, "sessions", "terminal-one")

    history.close()


def test_missing_note_reports_no_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert not note_has_content(tmp_path, "sessions", "never-created")


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
