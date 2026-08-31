"""Phase 23 W1: operator-only routes refuse an agent session's pane, at the route.

What these pin: the refusal fires only on a *valid* identity naming an
agent-backed session; a shell pane's operator, an unknown session, a wrong
token, and the header-free case all pass, because the guard exists to redirect
honestly-written agents to the agent surface - never to refuse the person the
route serves. The same-host trust decision stands: a caller that strips its
own identity is out of the guard's scope by design (`agent-messaging.md`).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from swe_mux import app_keys as keys
from swe_mux.routes.support import refuse_agent_session_caller


def _session(backend: str, token: str = "tok-1") -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(id="s1", backend=backend), mcp_token=token
    )


def _request(headers: dict[str, str], session: Any | None) -> Any:
    app: dict[Any, Any] = {
        keys.SESSIONS: SimpleNamespace(
            sessions={"s1": session} if session is not None else {}
        )
    }
    # `make_mocked_request` accepts anything as `app`; the guard only indexes
    # it, so a plain dict keyed by the AppKey serves.
    return make_mocked_request(
        "POST", "/api/sessions/s2/input", headers=headers, app=app
    )


_IDENTITY = {"X-Mux-Caller-Session": "s1", "X-Mux-Caller-Token": "tok-1"}


def test_an_agent_pane_is_refused_and_pointed_at_the_agent_surface() -> None:
    request = _request(_IDENTITY, _session("claude"))
    with pytest.raises(web.HTTPForbidden) as caught:
        refuse_agent_session_caller(request, operation="writing terminal input")
    assert "swemux agent" in caught.value.text


def test_a_shell_panes_operator_passes() -> None:
    refuse_agent_session_caller(
        _request(_IDENTITY, _session("shell")), operation="op"
    )


def test_missing_headers_pass() -> None:
    refuse_agent_session_caller(_request({}, _session("claude")), operation="op")


def test_a_wrong_token_passes_rather_than_guessing() -> None:
    # A forged or stale header must not refuse whoever is actually calling.
    headers = {"X-Mux-Caller-Session": "s1", "X-Mux-Caller-Token": "wrong"}
    refuse_agent_session_caller(_request(headers, _session("claude")), operation="op")


def test_an_unknown_session_passes() -> None:
    refuse_agent_session_caller(_request(_IDENTITY, None), operation="op")


def test_an_empty_held_token_never_matches() -> None:
    # Pre-feature sessions hold no token; an empty comparison must not admit
    # an empty header either (the header-presence check catches that first).
    refuse_agent_session_caller(
        _request(_IDENTITY, _session("claude", token="")), operation="op"
    )
