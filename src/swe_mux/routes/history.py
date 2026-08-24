"""Run history, its backfills and scans, transcripts, resume, and the event log."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..harness import (
    AGENT_BACKENDS,
    has_observable_transcript,
)
from ..http_support import json_response
from ..layouts import attach_terminal
from ..session import (
    SessionManager,
)
from ..session_resume import ResumeRefused, resume_run
from ..transcript_view import (
    ParsedConversation,
    conversation_is_readable,
    parse_transcript_with_watermark,
)
from . import sessions

log = logging.getLogger(__name__)


async def list_history(request: web.Request) -> web.Response:
    external_value = request.query.get("external")
    page = await request.app[keys.HISTORY].history_page(
        query=request.query.get("q", ""),
        search_scope=request.query.get("scope", "all"),
        backend=request.query.get("backend"),
        project_id=request.query.get("project"),
        state=request.query.get("state"),
        external=(external_value.lower() == "true") if external_value is not None else None,
        date_from=float(request.query["date_from"]) if request.query.get("date_from") else None,
        date_to=float(request.query["date_to"]) if request.query.get("date_to") else None,
        time_basis=request.query.get("time_basis", "started"),
        cursor=request.query.get("cursor"),
        limit=int(request.query.get("limit", min(50, request.app[keys.CONFIG].history_limit))),
    )
    await request.app[keys.HISTORY].refresh_time_summaries(page["items"])
    await sessions._decorate_generated_titles(request.app, page["items"])
    sessions._decorate_conversation_holders(request.app, page["items"])
    return json_response(page)


async def list_history_projects(request: web.Request) -> web.Response:
    return json_response({"items": await request.app[keys.HISTORY].history_projects()})


async def start_history_backfill(request: web.Request) -> web.Response:
    body = await request.json()
    project_id = str(body.get("project_id") or "")
    if not project_id:
        raise ValueError("project_id is required")
    return json_response({"job": request.app[keys.HISTORY_BACKFILLS].start(project_id)}, 202)


async def list_history_backfills(request: web.Request) -> web.Response:
    return json_response(
        {"items": request.app[keys.HISTORY_BACKFILLS].list(request.query.get("project_id"))}
    )


async def get_history_backfill(request: web.Request) -> web.Response:
    return json_response(
        {"job": request.app[keys.HISTORY_BACKFILLS].get(request.match_info["job_id"])}
    )


async def cancel_history_backfill(request: web.Request) -> web.Response:
    return json_response(
        {"job": request.app[keys.HISTORY_BACKFILLS].cancel(request.match_info["job_id"])}
    )


async def get_history_scan(request: web.Request) -> web.Response:
    return json_response({"job": request.app[keys.HISTORY_SCAN].status()})


async def start_history_scan(request: web.Request) -> web.Response:
    # Scoped to the enabled harnesses inside the manager. Returns the running job so
    # the caller can begin polling immediately; a second start while one runs is a
    # no-op that returns the in-flight job rather than a second scan.
    return json_response({"job": request.app[keys.HISTORY_SCAN].start()}, 202)


async def cancel_history_scan(request: web.Request) -> web.Response:
    return json_response({"job": request.app[keys.HISTORY_SCAN].cancel()})


def _parse_conversation(
    path: Path | None, backend: str, native_id: str | None
) -> ParsedConversation:
    """`parse_transcript_with_watermark` with the conversation reference spelled out.

    A one-line wrapper so the two `asyncio.to_thread` call sites pass the same three
    arguments positionally; `to_thread` cannot forward keywords.
    """
    return parse_transcript_with_watermark(path, backend, native_id=native_id)


async def history_transcript(request: web.Request) -> web.Response:
    row = await request.app[keys.HISTORY].history_entry(request.match_info["sid"])
    if not row:
        raise KeyError(request.match_info["sid"])
    transcript = row.get("transcript_path")
    path = Path(str(transcript)) if transcript else None
    backend = str(row["backend"])
    native_id = str(row.get("native_id") or "") or None
    if not conversation_is_readable(path, backend, native_id):
        return json_response(
            {"error": "native transcript is unavailable", "code": "transcript_unavailable"},
            409,
        )
    # Parse off the event loop and reuse the shared watermark-keyed cache; large
    # conversations otherwise block the loop on every open. The watermark comes back
    # from the same call, so it can never claim to cover content this parse did not
    # read.
    parsed = await asyncio.to_thread(_parse_conversation, path, backend, native_id)
    messages = parsed.messages
    await request.app[keys.HISTORY].replace_history_messages(
        str(row["id"]), messages, mtime_ns=parsed.mtime_ns, size=parsed.size
    )
    row = await request.app[keys.HISTORY].history_entry(str(row["id"])) or row
    matches = await request.app[keys.HISTORY].history_message_matches(
        str(row["id"]), request.query.get("q", ""), request.query.get("scope", "all")
    )
    annotations = await request.app[keys.AUTOMATION_STORE].annotations(
        agent_run_id=str(row["id"]), limit=200
    )
    # Phase 7.7: the scan timeline is the single behavioral-summary producer, so
    # the Run-notes view reads its per-record spine for this run alongside the
    # annotations. Historical `turn-summary` notes stay in `annotations`.
    scan_records = await request.app[keys.AUTOMATION_STORE].scan_records(
        agent_run_id=str(row["id"]), limit=500
    )
    await sessions._decorate_generated_titles(request.app, [row])
    sessions._decorate_conversation_holders(request.app, [row])
    return json_response(
        {
            "entry": row,
            "messages": messages,
            # How many messages this conversation branched away from and the
            # reader is therefore not being shown. Reported so a retried run does
            # not read as a transcript with pieces missing.
            "abandoned_messages": parsed.abandoned,
            "annotations": annotations,
            "matches": matches,
            "scan_records": scan_records,
        }
    )


async def resume_history(request: web.Request) -> web.Response:
    """Reopen a conversation from its History row, in a pane beside the current one.

    The decision to resume, every refusal, and the proof that the pane came up all
    live in `session_resume.py`, which the scheduled-resume path calls too. What stays
    here is what a *browser* resume owes and a scheduled one does not: the effective
    display name, where the pane is attached in the layout, and an HTTP answer.
    """
    row = await request.app[keys.HISTORY].history_entry(request.match_info["sid"])
    if not row:
        raise KeyError(request.match_info["sid"])
    if not row.get("agent_visible") or not has_observable_transcript(row.get("backend")):
        return json_response(
            {"error": "only observable agent history can be resumed", "code": "not_agent"},
            422,
        )
    # History stores the stable raw session name; generated titles live in run
    # annotations. Resolve the effective visible name before Codex mints its new
    # run, otherwise the annotation remains keyed to the retired run and the
    # resumed pane falls back to `codex-<id>`.
    annotation_reader = getattr(request.app.get(keys.AUTOMATION_STORE), "annotations", None)
    if callable(annotation_reader):
        await sessions._decorate_generated_titles(request.app, [row])
    body = await request.json() if request.can_read_body else {}
    target_project = str(body.get("project_id") or row.get("project_id") or "")
    try:
        outcome = await resume_run(
            row,
            sessions=request.app[keys.SESSIONS],
            projects=request.app[keys.PROJECTS],
            target_project_id=target_project,
            name=str(body.get("name") or ""),
        )
    except ResumeRefused as refusal:
        return json_response(refusal.payload(), refusal.status)
    session = outcome.session
    owning_project = request.app[keys.PROJECTS].projects[target_project]
    next_layout = attach_terminal(
        owning_project.layout,
        session.record.id,
        target_id=body.get("target_session_id"),
        direction=body.get("direction"),
    )
    try:
        await request.app[keys.PROJECTS].update(
            target_project,
            layout=next_layout,
            layout_revision=owning_project.layout_revision,
        )
    except Exception:
        await request.app[keys.SESSIONS].stop(session.record.id)
        request.app[keys.SESSIONS].sessions.pop(session.record.id, None)
        raise
    child_run_id = session.record.agent_run_id or session.record.id
    # An inherited run is the same run, not a descendant of one: recording an
    # edge from a conversation to itself would make every consumer that walks
    # lineage see a cycle where nothing was forked.
    if child_run_id != str(row["id"]):
        await request.app[keys.AUTOMATION_STORE].add_lineage(
            str(row["id"]),
            child_run_id,
            "resume",
            {"backend": row["backend"], "project_id": target_project},
        )
    return json_response(session.record.snapshot(), 201)


def _live_agent_run_ids(manager: SessionManager) -> frozenset[str]:
    """Run rows a live pane is still writing to."""
    return frozenset(
        session.record.agent_run_id or session.record.id
        for session in manager.sessions.values()
        if session.record.backend in AGENT_BACKENDS
        and session.record.state not in {"exited", "crashed"}
    )


def _live_history_run_ids(manager: SessionManager) -> frozenset[str]:
    """Every history row a live pane is still writing to, agent or shell.

    Broader than `_live_agent_run_ids` on purpose: the startup sweep that closes
    runs abandoned by a crash must not close a *shell's* row either, and a cold
    session is excluded because its process is exactly what is gone.
    """
    return frozenset(
        session.record.agent_run_id or session.record.id
        for session in manager.sessions.values()
        if session.record.state not in {"exited", "crashed"}
    )


async def list_history_duplicates(request: web.Request) -> web.Response:
    """Conversations whose history is split across more than one entry."""
    return json_response({"items": await request.app[keys.HISTORY].duplicate_conversation_rows()})


async def repair_history_duplicates(request: web.Request) -> web.Response:
    """Fold duplicate rows back into each conversation's own entry.

    Explicit and dry by default. Merging rewrites history entries, so it is never
    something a daemon start or a migration does on its own — the duplicates it
    repairs came from bugs, but an automatic merge would be indistinguishable from
    losing entries, and there is no undo.
    """
    body = await request.json() if request.can_read_body else {}
    dry_run = bool(body.get("dry_run", True))
    result = await request.app[keys.HISTORY].merge_duplicate_conversation_rows(
        live_run_ids=_live_agent_run_ids(request.app[keys.SESSIONS]), dry_run=dry_run
    )
    if not dry_run and result["merged"]:
        await request.app[keys.EVENTS].emit(
            "history_duplicates_merged",
            source="user",
            conversations=len(result["merged"]),
            removed=sum(len(item["removed"]) for item in result["merged"]),
        )
    return json_response(result)


async def delete_history_entry(request: web.Request) -> web.Response:
    row = await request.app[keys.HISTORY].history_entry(request.match_info["sid"])
    if not row or not row.get("agent_visible"):
        raise KeyError(request.match_info["sid"])
    await request.app[keys.HISTORY].delete_history_entry(request.match_info["sid"])
    await request.app[keys.EVENTS].emit(
        "history_entry_deleted", session_id=request.match_info["sid"], source="user"
    )
    return json_response({"ok": True, "native_transcript_deleted": False})


async def list_events(request: web.Request) -> web.Response:
    return json_response(
        await request.app[keys.HISTORY].events(
            float(request.query.get("since", 0)),
            request.query.get("session"),
            min(int(request.query.get("limit", 500)), 2000),
            int(request.query.get("after_seq", 0)),
        )
    )


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/history", list_history),
    web.get("/api/history/projects", list_history_projects),
    web.get("/api/history/backfills", list_history_backfills),
    web.post("/api/history/backfills", start_history_backfill),
    web.get("/api/history/backfills/{job_id}", get_history_backfill),
    web.delete("/api/history/backfills/{job_id}", cancel_history_backfill),
    web.get("/api/history/scan", get_history_scan),
    web.post("/api/history/scan", start_history_scan),
    web.delete("/api/history/scan", cancel_history_scan),
    # Registered before the `{sid}` routes so the static segment wins.
    web.get("/api/history/duplicates", list_history_duplicates),
    web.post("/api/history/duplicates/repair", repair_history_duplicates),
    web.get("/api/history/{sid}/transcript", history_transcript),
    web.post("/api/history/{sid}/resume", resume_history),
    web.delete("/api/history/{sid}", delete_history_entry),
    web.get("/api/events", list_events),
)
