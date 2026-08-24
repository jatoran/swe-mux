"""The shared clipboard."""

from __future__ import annotations

import logging

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..clipboard_store import ClipboardStore
from ..errors import NotFound
from ..http_support import json_response

log = logging.getLogger(__name__)


def _clipboard_store(request: web.Request) -> ClipboardStore:
    store: ClipboardStore = request.app[keys.CLIPBOARD]
    return store


async def _emit_clipboard_changed(request: web.Request, reason: str, entry_id: str = "") -> None:
    """Announce a ring change so other clients refetch.

    The payload carries no copied text: these events are persisted in the history
    event log, and putting clipboard contents there would defeat the point of the
    memory-only default.
    """

    await request.app[keys.EVENTS].emit(
        "clipboard_changed",
        source="user",
        reason=reason,
        entry_id=entry_id,
        count=len(_clipboard_store(request).entries()),
    )


async def list_clipboard_entries(request: web.Request) -> web.Response:
    store = _clipboard_store(request)
    await store.prune()
    return json_response(
        {
            **store.status(),
            "entries": [entry.snapshot() for entry in store.entries()],
        }
    )


async def capture_clipboard_entry(request: web.Request) -> web.Response:
    store = _clipboard_store(request)
    body = await request.json() if request.can_read_body else {}
    entry, reason = await store.capture(
        body.get("text"),
        source=str(body.get("source") or ""),
        session_id=body.get("session_id"),
        project_id=body.get("project_id"),
        device=str(body.get("device") or ""),
    )
    if entry is not None:
        await _emit_clipboard_changed(request, reason, entry.id)
    return json_response(
        {
            "stored": entry is not None,
            "reason": reason,
            "entry": entry.snapshot() if entry else None,
        },
        201 if reason == "stored" else 200,
    )


async def get_clipboard_entry(request: web.Request) -> web.Response:
    entry = _clipboard_store(request).entry(request.match_info["entry_id"])
    if entry is None:
        raise NotFound(request.match_info["entry_id"], kind="clipboard entry")
    return json_response(entry.snapshot(include_text=True))


async def patch_clipboard_entry(request: web.Request) -> web.Response:
    body = await request.json() if request.can_read_body else {}
    if "pinned" not in body:
        raise ValueError("pinned is required")
    entry = await _clipboard_store(request).set_pinned(
        request.match_info["entry_id"], bool(body["pinned"])
    )
    await _emit_clipboard_changed(request, "pinned" if entry.pinned else "unpinned", entry.id)
    return json_response(entry.snapshot())


async def delete_clipboard_entry(request: web.Request) -> web.Response:
    entry_id = request.match_info["entry_id"]
    if not await _clipboard_store(request).delete(entry_id):
        raise NotFound(entry_id, kind="clipboard entry")
    await _emit_clipboard_changed(request, "deleted", entry_id)
    return json_response({"ok": True})


async def clear_clipboard_entries(request: web.Request) -> web.Response:
    include_pinned = request.query.get("include_pinned", "").lower() in {"1", "true", "yes"}
    removed = await _clipboard_store(request).clear(include_pinned=include_pinned)
    await _emit_clipboard_changed(request, "cleared")
    return json_response({"ok": True, "removed": removed})


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/clipboard", list_clipboard_entries),
    web.post("/api/clipboard", capture_clipboard_entry),
    web.delete("/api/clipboard", clear_clipboard_entries),
    web.get("/api/clipboard/{entry_id}", get_clipboard_entry),
    web.patch("/api/clipboard/{entry_id}", patch_clipboard_entry),
    web.delete("/api/clipboard/{entry_id}", delete_clipboard_entry),
)
