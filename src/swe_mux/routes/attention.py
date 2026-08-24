"""Lineage, the attention inbox, and the rules that rank it."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from .. import (
    app_keys as keys,
)
from .. import (
    session_titles,
)
from ..http_support import json_response
from ..session import (
    SessionManager,
)

log = logging.getLogger(__name__)


async def list_lineage(request: web.Request) -> web.Response:
    """Lineage edges, with both ends named the way every other surface names them.

    Decorated here rather than in the browser because only the daemon can answer it:
    an edge names two *runs*, and a run's display name is the live session's when one
    is still open, the History row's when it is not, and neither when the row has been
    deleted. A client holding one page of History results has none of those for the
    other end of an edge, which is how the lineage section came to print raw ids.
    """
    edges = await request.app[keys.AUTOMATION_STORE].lineage(request.query.get("run_id"))
    await _decorate_lineage_endpoints(request.app, edges)
    return json_response({"items": edges})


async def _decorate_lineage_endpoints(
    app: web.Application, edges: list[dict[str, Any]]
) -> None:
    """Attach `{name, live, known}` for each edge's parent and child run.

    ``known: false`` is a deliberate third state rather than an empty name. An edge
    whose other end has been deleted from History still records that the fork
    happened, and dropping it would silently reshape the lineage; saying the
    conversation is gone is the true answer.
    """
    if not edges:
        return
    manager: SessionManager = app[keys.SESSIONS]
    endpoints = {
        str(edge.get(field) or "")
        for edge in edges
        for field in ("parent_run_id", "child_run_id")
    }
    endpoints.discard("")
    rows = await app[keys.HISTORY].history_naming_rows(sorted(endpoints))
    live_by_run = {
        session_titles.record_run_id(session.record): session
        for session in manager.sessions.values()
    }
    titles = await session_titles.generated_titles(
        app[keys.AUTOMATION_STORE],
        set(live_by_run) | {session_titles.row_run_id(row) for row in rows.values()},
    )

    def endpoint(run_id: str) -> dict[str, Any]:
        live = live_by_run.get(run_id)
        if live is not None:
            name = session_titles.record_display_name(live.record, titles)
            return {"name": name, "live": True, "known": True, "session_id": live.record.id}
        row = rows.get(run_id)
        if row is not None:
            name = session_titles.row_display_name(row, titles)
            return {"name": name, "live": False, "known": True}
        return {"name": "", "live": False, "known": False}

    for edge in edges:
        edge["parent"] = endpoint(str(edge.get("parent_run_id") or ""))
        edge["child"] = endpoint(str(edge.get("child_run_id") or ""))


async def create_lineage(request: web.Request) -> web.Response:
    body = await request.json()
    parent = str(body.get("parent_run_id") or "")
    child = str(body.get("child_run_id") or "")
    relation = str(body.get("relation") or "")
    if not parent or not child or relation not in {
        "resume",
        "handoff",
        "continuation",
        "review",
        "branch",
    }:
        raise ValueError("parent_run_id, child_run_id, and a valid relation are required")
    return json_response(
        await request.app[keys.AUTOMATION_STORE].add_lineage(
            parent, child, relation, body.get("metadata")
        ),
        201,
    )


async def absence_report(request: web.Request) -> web.Response:
    """The away report: the raw record, plus ranked items and rollover boundaries.

    One endpoint rather than two. The original keys (sessions, annotations,
    notifications) are unchanged for existing readers; the digest adds what
    ranking knows — which findings mattered, what was held back and why, and where
    a conversation was replaced mid-absence.
    """
    since = float(request.query["since"]) if request.query.get("since") else None
    report = await request.app[keys.FLEET].absence_report(since)
    digest = await request.app[keys.ATTENTION_RANKING].digest(report["since"])
    return json_response({**report, **digest, "since": report["since"]})


async def attention_inbox(request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", 200))
    return json_response(await request.app[keys.ATTENTION_RANKING].inbox(limit=limit))


async def attention_feedback(request: web.Request) -> web.Response:
    """Record what the user did with one ranked item; the only learning input."""
    body = await request.json()
    action = str(body.get("action") or "")
    updated = await request.app[keys.ATTENTION_RANKING].feedback(
        request.match_info["item_id"], action
    )
    if updated is None:
        raise KeyError(request.match_info["item_id"])
    return json_response(updated)


async def attention_rule_decision(request: web.Request) -> web.Response:
    """Accept or reject a behaviour-mined demotion rule. Never applied silently."""
    body = await request.json()
    incident_class = str(body.get("incident_class") or "")
    channel = str(body.get("channel") or "")
    if not incident_class or not channel:
        raise ValueError("incident_class and channel are required")
    ranking = request.app[keys.ATTENTION_RANKING]
    await ranking.decide_rule(incident_class, channel, bool(body.get("accept", False)))
    return json_response({"rules": [rule.snapshot() for rule in await ranking.rules()]})


async def injection_safety(request: web.Request) -> web.Response:
    return json_response(request.app[keys.FLEET].injection_safety())


ROUTES: tuple[web.RouteDef, ...] = (
    web.get("/api/lineage", list_lineage),
    web.post("/api/lineage", create_lineage),
    web.get("/api/attention/absence", absence_report),
    web.get("/api/attention/inbox", attention_inbox),
    web.post("/api/attention/items/{item_id}/feedback", attention_feedback),
    web.post("/api/attention/rules", attention_rule_decision),
    web.get("/api/automation/injection-safety", injection_safety),
)
