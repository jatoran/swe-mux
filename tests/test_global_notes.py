from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.event_bus import EventBus
from swe_mux.project_files import GLOBAL_SCRATCHPAD_ID, global_note_path, read_global_note
from swe_mux.routes.notes import get_global_note, put_global_note
from swe_mux.server import error_middleware


def _app(data_dir: Path) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app[keys.CONFIG] = SimpleNamespace(data_dir=data_dir)
    app[keys.EVENTS] = EventBus()
    app.router.add_get("/global-notes/{note_id}", get_global_note)
    app.router.add_put("/global-notes/{note_id}", put_global_note)
    return app


async def test_scratchpad_is_lazy_global_and_revision_checked(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async with TestClient(TestServer(app)) as client:
        initial = await (await client.get("/global-notes/scratchpad")).json()
        saved_response = await client.put(
            "/global-notes/scratchpad",
            json={"markdown": "Cross-project context.\n", "revision": "missing"},
        )
        saved = await saved_response.json()
        stale = await client.put(
            "/global-notes/scratchpad",
            json={"markdown": "Stale edit.\n", "revision": "missing"},
        )
        reloaded = await (await client.get("/global-notes/scratchpad")).json()

    assert initial == {
        "scope": "global",
        "kind": "global-note",
        "id": "scratchpad",
        "title": "Scratchpad",
        "created_at": None,
        "origin_session_id": None,
        "path": str(global_note_path(tmp_path, GLOBAL_SCRATCHPAD_ID)),
        "exists": False,
        "bytes": 0,
        "revision": "missing",
        "markdown": "",
        "status": "missing",
    }
    assert saved_response.status == 200
    assert stale.status == 409
    assert reloaded["markdown"] == "Cross-project context.\n"
    assert reloaded["revision"] == saved["revision"]
    assert global_note_path(tmp_path, GLOBAL_SCRATCHPAD_ID).is_file()


async def test_scratchpad_save_emits_global_note_event(tmp_path: Path) -> None:
    app = _app(tmp_path)
    events: EventBus = app[keys.EVENTS]

    async with TestClient(TestServer(app)) as client:
        queue = events.subscribe()
        response = await client.put(
            "/global-notes/scratchpad",
            json={"markdown": "Remember this.\n", "revision": "missing"},
        )
        payload = await response.json()
        event = await queue.get()

    assert event.type == "note_changed"
    assert event.payload == {
        "scope": "global",
        "note_id": "scratchpad",
        "revision": payload["revision"],
    }


async def test_unknown_global_note_is_refused(tmp_path: Path) -> None:
    app = _app(tmp_path)

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/global-notes/not-created")

    assert response.status == 400
    assert not (tmp_path / "notes").exists()


async def test_read_global_note_does_not_depend_on_project_discovery(tmp_path: Path) -> None:
    note = await read_global_note(tmp_path, GLOBAL_SCRATCHPAD_ID, default_title="Scratchpad")

    assert note["scope"] == "global"
    assert note["revision"] == "missing"
    assert note["path"] == str(tmp_path / "notes" / "items" / "scratchpad.md")
