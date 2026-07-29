"""Phase 4.5: mux MCP v0 — read/discovery surface, identity, scope, bounds.

What these pin is the surface's contract, not its wording: caller identity is
derived from the injected token and never claimed; every tool is scoped to the
caller's Project and answers "not found" identically for scope misses and true
misses; output is bounded and credential-shaped content is withheld; and the
surface is read-only end to end.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.mcp import (
    TOOLS,
    TRANSCRIPT_MAX_MESSAGES,
    McpAuthError,
    McpService,
    session_summary,
)


def record(
    sid: str,
    *,
    project_id: str = "p1",
    scope_id: str = "scope-1",
    backend: str = "claude",
    state: str = "working",
) -> Any:
    return SimpleNamespace(
        id=sid,
        name=f"{backend}-{sid[:6]}",
        backend=backend,
        state=state,
        state_detail=None,
        awaiting_reason=None,
        idle_reason=None,
        model="claude-sonnet-5",
        cwd="D:/work",
        project_id=project_id,
        project_scope_id=scope_id,
        project_label="Work",
        agent_run_id=sid,
        agent_run_seq=0,
        native_session_id=sid,
        created_at=1.0,
        last_activity_ts=2.0,
        tokens_in=10,
        tokens_out=20,
        context_pct=12.5,
        spawn_env={"SECRET_THING": "sk-live-abcdef"},
    )


def live_session(sid: str, *, token: str = "", transcript: Path | None = None, **kw: Any) -> Any:
    return SimpleNamespace(
        record=record(sid, **kw),
        mcp_token=token,
        transcript_path=transcript,
    )


class HistoryStub:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.page_calls: list[dict[str, Any]] = []

    async def history_page(self, **kwargs: Any) -> dict[str, Any]:
        self.page_calls.append(kwargs)
        return {"items": list(self.rows), "next_cursor": None}

    async def history_entry(self, session_id: str) -> dict[str, Any] | None:
        return next((row for row in self.rows if row.get("id") == session_id), None)


def manager_for(*sessions: Any) -> Any:
    table = {session.record.id: session for session in sessions}

    def resolve(identity: str) -> Any:
        if identity in table:
            return table[identity]
        named = [s for s in table.values() if s.record.name == identity]
        if len(named) == 1:
            return named[0]
        raise KeyError(identity)

    return SimpleNamespace(sessions=table, resolve=resolve)


def service_for(*sessions: Any, history: HistoryStub | None = None) -> McpService:
    return McpService(manager_for(*sessions), history or HistoryStub())


# ------------------------------------------------------------------ identity


def test_caller_is_derived_from_the_token_never_claimed() -> None:
    caller = live_session("s1", token="tok-one")
    service = service_for(caller, live_session("s2", token="tok-two"))
    assert service.resolve_caller("Bearer tok-one") is caller
    for bad in ("Bearer wrong", "tok-one", "", None, "Bearer "):
        with pytest.raises(McpAuthError):
            service.resolve_caller(bad)


def test_an_empty_session_token_never_authenticates() -> None:
    # Pre-feature sessions (adopted from an older daemon) hold no token; an
    # empty bearer must not match their empty string.
    service = service_for(live_session("s1", token=""))
    with pytest.raises(McpAuthError):
        service.resolve_caller("Bearer ")


# ------------------------------------------------------------------- scoping


@pytest.mark.asyncio
async def test_list_sessions_is_scoped_to_the_caller_project() -> None:
    caller = live_session("s1", token="tok")
    sibling = live_session("s2", project_id="p1")
    foreign = live_session("s3", project_id="p2")
    service = service_for(caller, sibling, foreign)
    result = await service.list_sessions(caller, {})
    ids = {item["session_id"] for item in result["sessions"]}
    assert ids == {"s1", "s2"}
    you = [item for item in result["sessions"] if item.get("you")]
    assert [item["session_id"] for item in you] == ["s1"]


@pytest.mark.asyncio
async def test_scope_and_true_misses_answer_identically() -> None:
    # Confirming a foreign session exists is itself a leak; both answers must
    # be byte-identical "not found".
    caller = live_session("s1", token="tok")
    foreign = live_session("s3", project_id="p2")
    service = service_for(caller, foreign)
    with pytest.raises(KeyError):
        await service.get_session(caller, {"session_id": "s3"})
    with pytest.raises(KeyError):
        await service.get_session(caller, {"session_id": "never-existed"})


@pytest.mark.asyncio
async def test_search_history_passes_the_caller_project_filter() -> None:
    history = HistoryStub()
    caller = live_session("s1", token="tok")
    service = service_for(caller, history=history)
    await service.search_history(caller, {"query": "deploy"})
    assert history.page_calls[0]["project_id"] == "p1"
    assert history.page_calls[0]["limit"] <= 50


# ------------------------------------------------- output shape and redaction


def test_session_summary_is_an_allowlist_that_cannot_leak_spawn_env() -> None:
    summary = session_summary(record("s1"))
    flattened = json.dumps(summary)
    assert "spawn_env" not in flattened
    assert "sk-live" not in flattened
    assert summary["state"] == "working"


@pytest.mark.asyncio
async def test_read_transcript_is_bounded_and_redacts_credential_shapes(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "t.jsonl"
    lines = [
        json.dumps({"type": "user", "message": {"content": f"message {index}"}})
        for index in range(300)
    ]
    lines.append(
        json.dumps(
            {"type": "user", "message": {"content": "api_key = sk-live-abcdef1234567890"}}
        )
    )
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    result = await service.read_transcript(
        caller, {"session_id": "s1", "max_messages": 10_000}
    )
    assert result["message_count"] <= TRANSCRIPT_MAX_MESSAGES
    last = result["messages"][-1]["text"]
    assert "sk-live" not in last
    assert "redacted" in last
    ordinary = result["messages"][0]["text"]
    assert ordinary.startswith("message ")


@pytest.mark.asyncio
async def test_read_transcript_reports_absence_instead_of_fabricating(
    tmp_path: Path,
) -> None:
    caller = live_session("s1", token="tok", transcript=tmp_path / "missing.jsonl")
    service = service_for(caller)
    result = await service.read_transcript(caller, {"session_id": "s1"})
    assert result["messages"] == []
    assert "no transcript" in result["note"]


# ------------------------------------------------------------------ protocol


@pytest.mark.asyncio
async def test_initialize_negotiates_and_lists_only_read_tools() -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller)
    init = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
    )
    assert init is not None
    assert init["result"]["protocolVersion"] == "2025-06-18"
    unknown = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        },
    )
    assert unknown is not None
    assert unknown["result"]["protocolVersion"] == "2025-06-18"

    listing = await service.handle_rpc(
        caller, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    )
    assert listing is not None
    names = {tool["name"] for tool in listing["result"]["tools"]}
    # A closed allowlist: four read tools plus the two bounded Phase 5 writes,
    # neither of which delivers or spawns anything by itself. A new tool must
    # be added here deliberately.
    assert names == {
        "list_sessions",
        "get_session",
        "read_transcript",
        "search_history",
        "notify",
        "request_spawn",
    }
    assert names == {tool["name"] for tool in TOOLS}


@pytest.mark.asyncio
async def test_notifications_get_no_response_and_unknown_methods_error() -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller)
    silent = await service.handle_rpc(
        caller, {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert silent is None
    missing = await service.handle_rpc(
        caller, {"jsonrpc": "2.0", "id": 9, "method": "resources/list"}
    )
    assert missing is not None
    assert missing["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_tool_call_wraps_results_and_not_found_is_a_soft_error() -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller)
    good = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "list_sessions", "arguments": {}},
        },
    )
    assert good is not None
    payload = json.loads(good["result"]["content"][0]["text"])
    assert payload["sessions"][0]["session_id"] == "s1"
    assert good["result"]["isError"] is False

    miss = await service.handle_rpc(
        caller,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "get_session", "arguments": {"session_id": "nope"}},
        },
    )
    assert miss is not None
    assert miss["result"]["isError"] is True
    assert "no such session" in miss["result"]["content"][0]["text"]


# ------------------------------------------------------ spawn/registration


def test_session_meta_mirrors_the_mcp_token_for_adoption() -> None:
    from swe_mux.session import Session, SessionManager

    session = cast(Any, SimpleNamespace(
        record=SimpleNamespace(snapshot=lambda: {"id": "s1"}),
        hook_secret="hs",
        mcp_token="mcp-tok",
        transcript_path=None,
        agent_lifecycle_id=None,
    ))
    meta = SessionManager._session_meta(session)
    assert meta["mcp_token"] == "mcp-tok"
    # And a Session constructed without one holds the never-authenticates value.
    assert Session.__init__.__kwdefaults__ == {"mcp_token": None}


def test_claude_adapter_registers_the_mux_mcp_server(tmp_path: Path) -> None:
    from swe_mux.adapters.base import SpawnOptions
    from swe_mux.adapters.claude import ClaudeAdapter

    adapter = ClaudeAdapter(
        "claude.exe", tmp_path, [], mcp_url="http://127.0.0.1:8765/mcp"
    )
    spec = adapter.spawn_spec("sid-1", SpawnOptions(tmp_path, None, [], "sid-1"))
    argv = list(spec.argv)
    assert "--mcp-config" in argv
    config_path = Path(argv[argv.index("--mcp-config") + 1])
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["mux"]
    assert server["type"] == "http"
    assert server["url"] == "http://127.0.0.1:8765/mcp"
    # The token is per-session env expansion, never a literal in a shared file.
    assert server["headers"]["Authorization"] == "Bearer ${MUX_MCP_TOKEN}"


def test_codex_adapter_registers_streamable_http_with_env_bearer() -> None:
    from swe_mux.adapters.base import SpawnOptions
    from swe_mux.adapters.codex import CodexAdapter

    adapter = CodexAdapter("codex.exe", mcp_url="http://127.0.0.1:8765/mcp")
    spec = adapter.spawn_spec("sid-1", SpawnOptions(Path("."), None, [], "sid-1"))
    argv = list(spec.argv)
    assert 'mcp_servers.mux.url="http://127.0.0.1:8765/mcp"' in argv
    assert 'mcp_servers.mux.bearer_token_env_var="MUX_MCP_TOKEN"' in argv
    # No secret material on argv.
    assert not any("tok" in arg.casefold() and "token_env" not in arg for arg in argv)


def test_shims_register_mcp_only_when_the_session_holds_a_token(monkeypatch: Any) -> None:
    from swe_mux.agent_launcher import _claude, _codex

    monkeypatch.setenv("MUX_CLAUDE_MCP_CONFIG", "C:/data/claude-mcp.json")
    monkeypatch.setenv("MUX_MCP_URL", "http://127.0.0.1:8765/mcp")
    monkeypatch.delenv("MUX_MCP_TOKEN", raising=False)
    monkeypatch.setenv("MUX_CLAUDE_ARGS", "[]")
    monkeypatch.setenv("MUX_CODEX_ARGS", "[]")

    _, claude_args, _ = _claude([])
    assert "--mcp-config" not in claude_args
    _, codex_args, _ = _codex([])
    assert not any("mcp_servers" in arg for arg in codex_args)

    monkeypatch.setenv("MUX_MCP_TOKEN", "tok")
    _, claude_args, _ = _claude([])
    assert claude_args[claude_args.index("--mcp-config") + 1] == "C:/data/claude-mcp.json"
    _, codex_args, _ = _codex([])
    assert any("mcp_servers.mux.url" in arg for arg in codex_args)
