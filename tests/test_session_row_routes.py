"""The single-session read serves exactly the row the fleet list serves.

The browser re-reads one row, rather than the whole fleet, when an event says only
that row moved but does not carry the new values itself. That is only sound if the
row it gets back is the same thing a full list would have given it: the same
generation and revision (so `mergeSessionSnapshot` can order it), the same
display-only readiness and composer presence, and the same generated title.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux.delivery_readiness import DISPLAY_SCREEN_MAX_AGE_SECONDS
from swe_mux.errors import NotFound
from swe_mux.models import SessionRecord
from swe_mux.routes.sessions import get_session, list_sessions
from swe_mux.server import error_middleware


def _session(sid: str, *, revision: int, composing: bool) -> Any:
    record = SessionRecord(sid, f"name-{sid}", "default", "claude", "native", ".", "claude", [])
    record.agent_run_id = f"run-{sid}"
    composer = SimpleNamespace(pending=composing, since=12.5 if composing else None)
    return SimpleNamespace(record=record, revision=revision, composer=composer)


class _Manager:
    def __init__(self, *sessions: Any) -> None:
        self.sessions = {session.record.id: session for session in sessions}

    def resolve(self, identity: str) -> Any:
        if identity not in self.sessions:
            raise NotFound(identity, kind="session")
        return self.sessions[identity]


class _Readiness:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def evaluate(self, session: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"delivery_state": "ready", "reasons": ["idle_prompt"]}


async def _titles(**kwargs: Any) -> list[dict[str, Any]]:
    return [
        {"agent_run_id": run_id, "content": f"title for {run_id}", "evidence_json": None}
        for run_id in kwargs["agent_run_ids"]
    ]


def _app(*sessions: Any) -> tuple[web.Application, _Readiness]:
    readiness = _Readiness()
    app = web.Application(middlewares=[error_middleware])
    app[keys.SESSIONS] = cast(Any, _Manager(*sessions))
    app[keys.FLEET] = cast(Any, SimpleNamespace(readiness=readiness))
    app[keys.DAEMON_GENERATION] = "generation-1"
    app[keys.AUTOMATION_STORE] = cast(Any, SimpleNamespace(annotations=_titles))
    app.router.add_get("/api/sessions", list_sessions)
    app.router.add_get("/api/sessions/{sid}", get_session)
    return app, readiness


def _without_observed_at(row: dict[str, Any]) -> dict[str, Any]:
    """Readiness is stamped with the read's wall clock, which two reads cannot share."""
    readiness = dict(row["delivery_readiness"])
    readiness.pop("observed_at")
    return {**row, "delivery_readiness": readiness}


async def test_one_session_reads_back_exactly_as_its_row_in_the_list() -> None:
    quiet = _session("quiet", revision=7, composing=False)
    typing = _session("typing", revision=3, composing=True)
    app, readiness = _app(quiet, typing)
    async with TestClient(TestServer(app)) as client:
        listed = {row["id"]: row for row in await (await client.get("/api/sessions")).json()}
        for sid in ("quiet", "typing"):
            response = await client.get(f"/api/sessions/{sid}")
            assert response.status == 200
            single = await response.json()
            assert _without_observed_at(single) == _without_observed_at(listed[sid])
    row = listed["typing"]
    assert row["_snapshot_generation"] == "generation-1"
    assert row["_snapshot_revision"] == 3
    assert row["_snapshot_enriched"] is True
    assert row["unsent_input"] == {"since": 12.5}
    assert "unsent_input" not in listed["quiet"]
    assert row["generated_title"] == "title for run-typing"
    assert row["delivery_readiness"]["state"] == "ready"
    # Both paths reuse the bounded classifier verdict rather than rescanning a pane.
    assert readiness.calls and all(
        call["snapshot_pty_cache_seconds"] == DISPLAY_SCREEN_MAX_AGE_SECONDS > 0
        for call in readiness.calls
    )
    assert all(call["record_metrics"] is False for call in readiness.calls)


async def test_an_unknown_session_is_a_404_the_browser_can_act_on() -> None:
    """A row refresh for a session that has gone falls back to a full list re-read."""
    app, _ = _app(_session("present", revision=1, composing=False))
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/sessions/gone")
        assert response.status == 404
