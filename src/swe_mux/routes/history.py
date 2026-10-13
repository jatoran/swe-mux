"""Run history, its backfills and scans, transcripts, resume, and the event log."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from ..errors import NotFound
from ..harness import (
    AGENT_BACKENDS,
    has_observable_transcript,
)
from ..history import (
    BROWSER_SEARCH_BUDGET_MS,
    HISTORY_MESSAGES_PAGE,
    read_history_conversation,
)
from ..http_support import json_response
from ..layouts import attach_terminal
from ..session import (
    SessionManager,
)
from ..session_resume import ResumeRefused, resume_run
from ..transcript_repair import TranscriptLocation, resolve_row_transcript
from ..transcript_view import TRANSCRIPT_PARSER_VERSION, ParsedConversation
from . import sessions

log = logging.getLogger(__name__)

#: A History open is the operator waiting on a click; anything slower than this is
#: worth a WARNING line with the sizes that explain it.
SLOW_HISTORY_READ_MS = 1_000


def _slim_history_row(item: dict[str, Any]) -> dict[str, Any]:
    """A History row as the browser reads it: no argv, no whole title annotation.

    `generated_title_annotation` is decorated with the full annotation row (evidence,
    provenance, costs) because the sessions listing shares the decorator; History reads
    only its `stability`, and the full row was 16% of a listing page.
    """
    item.pop("argv_json", None)
    annotation = item.get("generated_title_annotation")
    if isinstance(annotation, dict):
        item["generated_title_annotation"] = {"stability": annotation.get("stability")}
    return item


async def _locate(request: web.Request, row: dict[str, Any]) -> TranscriptLocation:
    """Where this row's conversation can be read, repairing a moved one (`transcript_repair`)."""
    return await resolve_row_transcript(
        row,
        # `.get`: reading a conversation must not start depending on a live session
        # manager. With no adapters there is nothing to search with and this degrades to
        # the readability test it replaced.
        adapters=getattr(request.app.get(keys.SESSIONS), "adapters", {}),
        history=request.app[keys.HISTORY],
        events=request.app[keys.EVENTS],
    )


async def _history_row(request: web.Request) -> dict[str, Any]:
    row = await request.app[keys.HISTORY].history_entry(request.match_info["sid"])
    if not row:
        raise NotFound(request.match_info["sid"], kind="session")
    return row


def _optional_int(request: web.Request, name: str) -> int | None:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


class SearchAbandoned(Exception):
    """The reader who asked for this search is gone; do not run it."""


@asynccontextmanager
async def _search_turn(request: web.Request, *, active: bool) -> AsyncIterator[None]:
    """Serialize browser searches and drop the ones nobody is waiting for.

    Two facts make an unguarded search box a denial of service against the
    daemon's own history thread. A search box fires per keystroke pause, so a
    typed phrase queues several searches; and aiohttp does not cancel a handler
    when its client disconnects, so aborting the request in the browser stops
    nothing here. On 2026-09-04 five searches typed in nineteen seconds ran to
    completion one after another, and `/api/sessions` and live agents' hook posts
    queued for the best part of a minute behind them.

    The lock is what makes the abandonment check possible rather than being the
    fix by itself: superseded searches wait here on the event loop, costing
    nothing, and by the time each reaches the front the browser has closed its
    connection and this refuses to spend the executor on it. What still runs is
    the newest one, which is the only one whose answer anybody will read.
    """
    gate = request.app.get(keys.HISTORY_SEARCH_GATE)
    if not active or gate is None:
        # No query is an index-driven listing and has nothing to serialize; a
        # missing gate is a partially-built app in a test, where the budget and
        # the abandonment check are the parts under test rather than the queue.
        yield
        return
    waited = time.monotonic()
    async with gate:
        transport = request.transport
        if transport is None or transport.is_closing():
            raise SearchAbandoned
        queued_ms = (time.monotonic() - waited) * 1000
        if queued_ms > 250:
            log.debug("history search queued behind another for %.0f ms", queued_ms)
        yield


async def list_history(request: web.Request) -> web.Response:
    external_value = request.query.get("external")
    query = request.query.get("q", "")
    history = request.app[keys.HISTORY]
    started = time.monotonic()
    try:
        async with _search_turn(request, active=bool(query)):
            # Re-stamped past the queue wait: a search that queued behind another
            # is not a slow search, and timing the wait as if it were would make
            # the slow-search warning fire hardest exactly when nothing is wrong.
            started = time.monotonic()
            page = await history.history_page(
                query=query,
                search_scope=request.query.get("scope", "all"),
                backend=request.query.get("backend"),
                project_id=request.query.get("project"),
                state=request.query.get("state"),
                external=(
                    external_value.lower() == "true" if external_value is not None else None
                ),
                date_from=(
                    float(request.query["date_from"]) if request.query.get("date_from") else None
                ),
                date_to=float(request.query["date_to"]) if request.query.get("date_to") else None,
                time_basis=request.query.get("time_basis", "last_message"),
                cursor=request.query.get("cursor"),
                limit=int(
                    request.query.get("limit", min(50, request.app[keys.CONFIG].history_limit))
                ),
                # A search is bounded; a plain listing is not, because it has no
                # scan to bound (`HistoryIndex.history_page`).
                budget_ms=BROWSER_SEARCH_BUDGET_MS if query else None,
            )
    except SearchAbandoned:
        # 499-shaped, but nothing reads it: the socket is already gone. Logged
        # because a run of these is the signature of a search that got slow again.
        log.info("history search abandoned before it ran scope=%s", request.query.get("scope"))
        return json_response({"items": [], "next_cursor": None, "abandoned": True})
    if query:
        # Every search is timed, not only the ones that trip the budget: a search
        # that has become slow is visible here as a trend before it is visible to
        # a reader as a freeze, and the budget's own log line only ever fires
        # after the damage is a refusal.
        elapsed_ms = (time.monotonic() - started) * 1000
        log.log(
            logging.WARNING if elapsed_ms > 2_000 else logging.DEBUG,
            "history search scope=%s chars=%d results=%d elapsed_ms=%.0f",
            request.query.get("scope", "all"),
            len(query),
            len(page["items"]),
            elapsed_ms,
        )
    # Bounded: rows whose time span is still being re-read answer with what is stored
    # and land in SQLite for the next listing (`HistoryIndex.refresh_time_summaries`).
    refreshed = await history.refresh_time_summaries(page["items"])
    await sessions._decorate_generated_titles(request.app, page["items"])
    sessions._decorate_conversation_holders(request.app, page["items"])
    for item in page["items"]:
        _slim_history_row(item)
    elapsed_ms = (time.monotonic() - started) * 1000
    log.log(
        logging.WARNING if elapsed_ms > SLOW_HISTORY_READ_MS else logging.DEBUG,
        "history listing rows=%d refreshed=%d query=%s elapsed_ms=%.0f",
        len(page["items"]),
        refreshed,
        bool(query),
        elapsed_ms,
    )
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
    """History's cached conversation read, positional for `asyncio.to_thread`.

    Goes through `history.read_history_conversation`, the one seam the shared transcript
    reader replaces.
    """
    return read_history_conversation(path, backend, native_id, cached=True)


async def history_transcript(request: web.Request) -> web.Response:
    """The whole conversation in one payload, for callers that want all of it.

    The History browser no longer reads this: it opens on `/detail` and pages
    `/messages` out of the index, because this route parses the entire transcript and
    ships every tool input on every open (seconds and megabytes for a long
    conversation). It stays for the CLI, scripts and the repair contract, and it no
    longer rewrites the run's indexed messages on every call: the index is replaced only
    when its watermark says the parse describes something it does not hold.
    """
    row = await _history_row(request)
    # Ask where the conversation *is* rather than testing where it was recorded: the CLI
    # moves these files (`transcript_repair`), and a row whose path went stale refused to
    # open with a message blaming the CLI's pruning for a file sitting one slug away.
    # The search only runs once the recorded path has already missed, and it writes the
    # repair back so Resume and the reindexer stop reading the same dead row.
    located = await _locate(request, row)
    backend = str(row["backend"])
    native_id = str(row.get("native_id") or "") or None
    if not located.readable:
        return json_response(
            {"error": "native transcript is unavailable", "code": "transcript_unavailable"},
            409,
        )
    path = located.path
    history = request.app[keys.HISTORY]
    # Parse off the event loop and reuse the shared watermark-keyed cache; large
    # conversations otherwise block the loop on every open. The watermark comes back
    # from the same call, so it can never claim to cover content this parse did not
    # read.
    parsed = await asyncio.to_thread(_parse_conversation, path, backend, native_id)
    messages = parsed.messages
    index = await history.transcript_index_state(
        str(row["id"]), path, backend, native_id, readable=True, schedule=False
    )
    if not (
        index["abandoned_messages"] is not None
        and index["watermark"] == (parsed.mtime_ns, parsed.size, TRANSCRIPT_PARSER_VERSION)
    ):
        await history.replace_history_messages(
            str(row["id"]),
            messages,
            mtime_ns=parsed.mtime_ns,
            size=parsed.size,
            abandoned=parsed.abandoned,
        )
        row = await history.history_entry(str(row["id"])) or row
    matches = await history.history_message_matches(
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


async def history_detail(request: web.Request) -> web.Response:
    """What the History reader needs to draw a conversation's header, without its body.

    Answered from the row and the message index: no transcript is parsed on this path.
    An index that is missing, stale, or older than the abandoned-message count is
    rebuilt in the background (`HistoryIndex.transcript_index_state`), and the answer
    says so with `transcript.indexing` so the reader can poll this route until it is
    done. A conversation whose file is gone still opens on whatever was indexed, with
    `transcript.available` false: the indexed text is still the conversation, and Resume
    refuses on its own (`session_resume`).
    """
    started = time.monotonic()
    row = await _history_row(request)
    located = await _locate(request, row)
    history = request.app[keys.HISTORY]
    state = await history.transcript_index_state(
        str(row["id"]),
        located.path,
        str(row["backend"]),
        str(row.get("native_id") or "") or None,
        readable=located.readable,
    )
    state.pop("watermark", None)
    await sessions._decorate_generated_titles(request.app, [row])
    sessions._decorate_conversation_holders(request.app, [row])
    elapsed_ms = (time.monotonic() - started) * 1000
    log.log(
        logging.WARNING if elapsed_ms > SLOW_HISTORY_READ_MS else logging.DEBUG,
        "history detail history_id=%s available=%s indexed=%s current=%s indexing=%s "
        "messages=%s elapsed_ms=%.0f",
        row["id"],
        state["available"],
        state["indexed"],
        state["current"],
        state["indexing"],
        state["message_count"],
        elapsed_ms,
    )
    return json_response({"entry": _slim_history_row(row), "transcript": state})


async def history_messages(request: web.Request) -> web.Response:
    """One page of a conversation's indexed messages (`HistoryIndex.history_messages_page`)."""
    started = time.monotonic()
    row = await _history_row(request)
    page = await request.app[keys.HISTORY].history_messages_page(
        str(row["id"]),
        after=_optional_int(request, "after"),
        before=_optional_int(request, "before"),
        around=_optional_int(request, "around"),
        limit=_optional_int(request, "limit") or HISTORY_MESSAGES_PAGE,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    log.log(
        logging.WARNING if elapsed_ms > SLOW_HISTORY_READ_MS else logging.DEBUG,
        "history messages page history_id=%s count=%d elapsed_ms=%.0f",
        row["id"],
        len(page["messages"]),
        elapsed_ms,
    )
    return json_response(page)


async def history_matches(request: web.Request) -> web.Response:
    """Where a search's words occur inside one conversation, by ordinal."""
    row = await _history_row(request)
    matches = await request.app[keys.HISTORY].history_message_matches(
        str(row["id"]), request.query.get("q", ""), request.query.get("scope", "all")
    )
    return json_response({"matches": matches})


async def history_notes(request: web.Request) -> web.Response:
    """A run's notes and behavioural timeline, loaded beside the conversation, not with it."""
    row = await _history_row(request)
    store = request.app[keys.AUTOMATION_STORE]
    annotations = await store.annotations(agent_run_id=str(row["id"]), limit=200)
    scan_records = await store.scan_records(agent_run_id=str(row["id"]), limit=500)
    return json_response({"annotations": annotations, "scan_records": scan_records})


def _tool_call_messages(
    parsed: ParsedConversation, first: int | None, last: int | None
) -> list[dict[str, Any]]:
    """Each message in ``[first, last]`` that carries tool calls, inputs included."""
    found: list[dict[str, Any]] = []
    for ordinal, message in enumerate(parsed.messages):
        if first is not None and ordinal < first:
            continue
        if last is not None and ordinal > last:
            break
        blocks = [
            {"id": block.get("id"), "name": block.get("name"), "input": block.get("input")}
            for block in message.get("content") or []
            if isinstance(block, dict)
            and block.get("type") == "tool_use"
            and str(block.get("name") or "").strip()
        ]
        if not blocks:
            continue
        has_text = any(
            isinstance(block, dict) and block.get("type") == "text" and block.get("text")
            for block in message.get("content") or []
        )
        found.append(
            {
                "ordinal": ordinal,
                "role": message.get("role"),
                "ts": message.get("ts"),
                "has_text": has_text,
                "tool_calls": blocks,
            }
        )
    return found


async def history_tool_calls(request: web.Request) -> web.Response:
    """Tool calls for a range of ordinals, read only when the reader asks to see them.

    The one History read that still parses, because tool inputs are deliberately not
    indexed. It goes through the cached read, so paging through one conversation with
    the toggle on parses it once. `mtime_ns`/`size` say which revision the ordinals
    describe; a reader holding messages from another revision reloads.
    """
    started = time.monotonic()
    row = await _history_row(request)
    located = await _locate(request, row)
    if not located.readable:
        return json_response(
            {"error": "native transcript is unavailable", "code": "transcript_unavailable"},
            409,
        )
    backend = str(row["backend"])
    native_id = str(row.get("native_id") or "") or None
    parsed = await asyncio.to_thread(_parse_conversation, located.path, backend, native_id)
    messages = _tool_call_messages(
        parsed, _optional_int(request, "from"), _optional_int(request, "to")
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    log.log(
        logging.WARNING if elapsed_ms > SLOW_HISTORY_READ_MS else logging.DEBUG,
        "history tool calls history_id=%s messages=%d bytes=%d elapsed_ms=%.0f",
        row["id"],
        len(messages),
        parsed.size,
        elapsed_ms,
    )
    return json_response({"messages": messages, "mtime_ns": parsed.mtime_ns, "size": parsed.size})


async def resume_history(request: web.Request) -> web.Response:
    """Reopen a conversation from its History row, in a pane beside the current one.

    The decision to resume, every refusal, and the proof that the pane came up all
    live in `session_resume.py`, which the scheduled-resume path calls too. What stays
    here is what a *browser* resume owes and a scheduled one does not: the effective
    display name, where the pane is attached in the layout, and an HTTP answer.
    """
    row = await request.app[keys.HISTORY].history_entry(request.match_info["sid"])
    if not row:
        raise NotFound(request.match_info["sid"], kind="session")
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
        raise NotFound(request.match_info["sid"], kind="session")
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
    # The reader's routes. Two segments deep on purpose: a bare `/api/history/{sid}`
    # would shadow single-segment static paths other modules register under
    # `/api/history/` (`scan-search`) whenever it happened to be added first.
    web.get("/api/history/{sid}/detail", history_detail),
    web.get("/api/history/{sid}/messages", history_messages),
    web.get("/api/history/{sid}/matches", history_matches),
    web.get("/api/history/{sid}/notes", history_notes),
    web.get("/api/history/{sid}/tool-calls", history_tool_calls),
    web.post("/api/history/{sid}/resume", resume_history),
    web.delete("/api/history/{sid}", delete_history_entry),
    web.get("/api/events", list_events),
)
