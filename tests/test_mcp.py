"""Phase 4.5: mux MCP v0 — read/discovery surface, identity, scope, bounds.

What these pin is the surface's contract, not its wording: caller identity is
derived from the injected token and never claimed; every tool defaults to the
caller's Project and answers "not found" identically for scope misses and true
misses; the `project` argument is the only way past that default, and the answer
says which scope produced it; output is bounded and credential-shaped content is
withheld; and the read surface is read-only end to end.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.agent_context import AgentContextService
from swe_mux.git_projects import ProjectIdentity
from swe_mux.mcp import (
    LIST_MAX_BYTES,
    TOOLS,
    TRANSCRIPT_MAX_MESSAGES,
    AmbiguousIdentity,
    McpAuthError,
    McpService,
    session_summary,
)
from swe_mux.mcp_contract import READ_TOOL_NAMES, WRITE_TOOL_NAMES
from swe_mux.project_files import create_note, write_note
from swe_mux.prompt_queue import QueueError


def record(
    sid: str,
    *,
    project_id: str = "p1",
    scope_id: str = "scope-1",
    backend: str = "claude",
    state: str = "working",
    auto_named: bool = True,
) -> Any:
    return SimpleNamespace(
        id=sid,
        name=f"{backend}-{sid[:6]}",
        auto_named=auto_named,
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
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        *,
        window: dict[str, Any] | None = None,
    ) -> None:
        self.rows = rows or []
        self.window = window or {"stale": True, "messages": []}
        self.page_calls: list[dict[str, Any]] = []

    async def history_page(self, **kwargs: Any) -> dict[str, Any]:
        self.page_calls.append(kwargs)
        return {"items": list(self.rows), "next_cursor": None}

    async def search_history_index(self, **kwargs: Any) -> dict[str, Any]:
        self.page_calls.append(kwargs)
        limit = int(kwargs.get("limit") or len(self.rows))
        return {
            "items": list(self.rows[:limit]),
            "has_more": len(self.rows) > limit,
            "candidate_truncated": False,
        }

    async def history_message_window(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return self.window

    async def history_entry(self, session_id: str) -> dict[str, Any] | None:
        return next((row for row in self.rows if row.get("id") == session_id), None)

    async def agent_runs_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [row for row in self.rows if row.get("note_id") == session_id]


class AutomationStoreStub:
    def __init__(
        self,
        titles: dict[str, str] | None = None,
        checkpoints: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.titles = titles or {}
        self.checkpoints = checkpoints or {}

    async def annotations(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"agent_run_id": run_id, "content": title}
            for run_id, title in self.titles.items()
        ]

    async def checkpoint(self, key: str) -> dict[str, Any] | None:
        return self.checkpoints.get(key)


class MessagingStub:
    def __init__(self) -> None:
        self.targets: list[str] = []
        self.calls: list[dict[str, Any]] = []

    async def notify(self, _caller: Any, *, target: str, **kwargs: Any) -> dict[str, Any]:
        self.targets.append(target)
        self.calls.append({"target": target, **kwargs})
        return {"target_session_id": target}


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


def service_for(
    *sessions: Any,
    history: HistoryStub | None = None,
    titles: dict[str, str] | None = None,
    automation_store: Any = None,
    messaging: Any = None,
    agent_context: Any = None,
    projects: Any = None,
) -> McpService:
    return McpService(
        manager_for(*sessions),
        history or HistoryStub(),
        messaging=messaging,
        automation_store=automation_store or AutomationStoreStub(titles),
        agent_context=agent_context,
        projects=projects,
    )


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
async def test_list_sessions_is_combined_bounded_searchable_and_pageable() -> None:
    caller = live_session("s00", token="tok")
    live = [live_session(f"s{index:02d}") for index in range(1, 21)]
    rows = [
        {
            "id": f"ended-{index:02d}",
            "name": f"codex-ended-{index:02d}",
            "backend": "codex",
            "model": "gpt-test",
            "project_id": "p1",
            "project_scope_id": "scope-1",
            "final_state": "exited",
        }
        for index in range(20)
    ]
    service = service_for(caller, *live, history=HistoryStub(rows))

    first = await service.list_sessions(caller, {"include_ended": True, "limit": 100})
    second = await service.list_sessions(
        caller,
        {
            "include_ended": True,
            "limit": 100,
            "cursor": first["next_cursor"],
        },
    )
    first_ids = {
        item["agent_run_id"]
        for item in [*first["sessions"], *first["ended_sessions"]]
    }
    second_ids = {
        item["agent_run_id"]
        for item in [*second["sessions"], *second["ended_sessions"]]
    }

    assert first["count"] == 25
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert second["count"] == 16
    assert not first_ids & second_ids
    assert len(json.dumps(first).encode("utf-8")) <= LIST_MAX_BYTES

    filtered = await service.list_sessions(
        caller, {"include_ended": True, "query": "ENDED-07"}
    )
    assert filtered["sessions"] == []
    assert [item["agent_run_id"] for item in filtered["ended_sessions"]] == [
        "ended-07"
    ]

    with pytest.raises(ValueError, match="does not match"):
        await service.list_sessions(
            caller,
            {
                "include_ended": True,
                "query": "different",
                "cursor": first["next_cursor"],
            },
        )


@pytest.mark.asyncio
async def test_sessions_include_the_same_effective_display_name_as_the_ui() -> None:
    caller = live_session("s1", token="tok")
    generated = live_session("s2")
    manual = live_session("s3", auto_named=False)
    service = service_for(
        caller,
        generated,
        manual,
        titles={"s2": "Fix Sidebar Sorting", "s3": "Ignored generated title"},
    )

    result = await service.list_sessions(caller, {})
    by_id = {item["session_id"]: item for item in result["sessions"]}

    assert by_id["s1"]["display_name"] == "claude-s1"
    assert by_id["s2"]["name"] == "claude-s2"
    assert by_id["s2"]["display_name"] == "Fix Sidebar Sorting"
    assert by_id["s3"]["display_name"] == "claude-s3"


@pytest.mark.asyncio
async def test_exact_display_name_resolves_live_sessions() -> None:
    caller = live_session("s1", token="tok")
    target = live_session("s2")
    service = service_for(caller, target, titles={"s2": "Fix Sidebar Sorting"})

    result = await service.get_session(caller, {"session_id": "Fix Sidebar Sorting"})

    assert result["session_id"] == "s2"
    assert result["name"] == "claude-s2"
    assert result["display_name"] == "Fix Sidebar Sorting"


@pytest.mark.asyncio
async def test_ambiguous_display_name_names_the_candidates_instead_of_guessing() -> None:
    caller = live_session("s1", token="tok")
    first = live_session("s2")
    second = live_session("s3")
    service = service_for(
        caller,
        first,
        second,
        titles={"s2": "Same title", "s3": "Same title"},
    )

    # Never a weak match, and never a bare "not found" either: "not found" is
    # unactionable when the session does exist twice, which is the normal case
    # once a call reaches past one Project.
    with pytest.raises(AmbiguousIdentity) as raised:
        await service.get_session(caller, {"session_id": "Same title"})
    assert raised.value.candidates == ["s2", "s3"]
    assert "s2, s3" in str(raised.value)


@pytest.mark.asyncio
async def test_notify_accepts_the_display_name_but_sends_the_stable_id() -> None:
    caller = live_session("s1", token="tok")
    target = live_session("s2")
    messaging = MessagingStub()
    service = service_for(
        caller,
        target,
        titles={"s2": "Fix Sidebar Sorting"},
        messaging=messaging,
    )

    result = await service.notify(
        caller,
        {"target": "Fix Sidebar Sorting", "body": "Review this"},
    )

    assert messaging.targets == ["s2"]
    assert result["target_session_id"] == "s2"


@pytest.mark.asyncio
async def test_notify_resolves_a_display_name_across_projects_and_carries_the_scope() -> None:
    # Generated UI titles are only resolvable here, so this layer maps the name
    # to an id. It also hands the write the scope it used, because the write
    # re-checks it rather than trusting this resolution.
    caller = live_session("s1", token="tok")
    foreign = live_session("s3", project_id="p2")
    messaging = MessagingStub()
    service = service_for(
        caller,
        foreign,
        titles={"s3": "Pack The Atlas"},
        messaging=messaging,
        projects=projects_stub(),
    )

    named = await service.notify(
        caller, {"target": "Pack The Atlas", "body": "hi", "project": "fleet"}
    )
    qualified = await service.notify(
        caller, {"target": "Pixel Lab/Pack The Atlas", "body": "hi"}
    )

    assert named["target_session_id"] == "s3"
    assert messaging.calls[0]["project"] == "fleet"
    assert qualified["target_session_id"] == "s3"
    assert messaging.calls[1]["project"] == "Pixel Lab"


@pytest.mark.asyncio
async def test_notify_refuses_an_ambiguous_target_as_a_typed_result() -> None:
    caller = live_session("s1", token="tok")
    messaging = MessagingStub()
    service = service_for(
        caller,
        live_session("s2"),
        live_session("s3", project_id="p2"),
        titles={"s2": "Same title", "s3": "Same title"},
        messaging=messaging,
        projects=projects_stub(),
    )

    with pytest.raises(QueueError) as caught:
        await service.notify(
            caller, {"target": "Same title", "body": "which one", "project": "fleet"}
        )

    assert caught.value.code == "ambiguous_target"
    assert caught.value.payload["candidates"] == ["s2", "s3"]
    assert not messaging.calls


@pytest.mark.asyncio
async def test_recently_ended_sessions_include_and_resolve_the_display_name() -> None:
    caller = live_session("s1", token="tok")
    history = HistoryStub(
        [
            {
                "id": "ended-1",
                "name": "codex-ended",
                "backend": "codex",
                "project_id": "p1",
                "project_scope_id": "scope-1",
                "auto_named": 1,
                "final_state": "exited",
            }
        ]
    )
    service = service_for(
        caller,
        history=history,
        titles={"ended-1": "Fix Sidebar Sorting"},
    )

    listing = await service.list_sessions(caller, {"include_ended": True})
    resolved = await service.get_session(caller, {"session_id": "Fix Sidebar Sorting"})

    assert listing["ended_sessions"][0]["display_name"] == "Fix Sidebar Sorting"
    assert resolved["session_id"] == "ended-1"
    assert resolved["display_name"] == "Fix Sidebar Sorting"


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


# ------------------------------------------------------- widening the scope


def projects_stub(**roots: str) -> Any:
    return SimpleNamespace(
        projects={
            pid: SimpleNamespace(id=pid, name=name, root=roots.get(pid, f"D:/{pid}"))
            for pid, name in (("p1", "Work"), ("p2", "Pixel Lab"))
        }
    )


@pytest.mark.asyncio
async def test_the_fleet_is_reachable_but_only_when_the_caller_asks() -> None:
    caller = live_session("s1", token="tok")
    sibling = live_session("s2", project_id="p1")
    foreign = live_session("s3", project_id="p2")
    service = service_for(caller, sibling, foreign, projects=projects_stub())

    default = await service.list_sessions(caller, {})
    fleet = await service.list_sessions(caller, {"project": "fleet"})
    named = await service.list_sessions(caller, {"project": "Pixel Lab"})

    assert {item["session_id"] for item in default["sessions"]} == {"s1", "s2"}
    assert {item["session_id"] for item in fleet["sessions"]} == {"s1", "s2", "s3"}
    assert {item["session_id"] for item in named["sessions"]} == {"s3"}
    # The caller stays first and its own Project outranks the rest, so widening
    # never pushes a sibling off the first page.
    assert [item["session_id"] for item in fleet["sessions"]] == ["s1", "s2", "s3"]
    assert fleet["project_scope"] == "fleet"
    assert named["project_scope"] == "Pixel Lab"


@pytest.mark.asyncio
async def test_a_default_listing_says_what_the_default_hid() -> None:
    # The hint has to live in the result: an agent reads answers far more often
    # than it reads a tool schema, and a default it cannot discover reads as a
    # prohibition.
    caller = live_session("s1", token="tok")
    service = service_for(caller, live_session("s3", project_id="p2"))

    default = await service.list_sessions(caller, {})
    fleet = await service.list_sessions(caller, {"project": "fleet"})

    assert default["project_scope"] == "self"
    assert default["live_sessions_in_other_projects"] == 1
    assert 'project:"fleet"' in default["scope_note"]
    # Nothing left to advertise once the caller has widened.
    assert "scope_note" not in fleet


@pytest.mark.asyncio
async def test_each_session_entry_names_its_project() -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller, live_session("s3", project_id="p2"))
    result = await service.list_sessions(caller, {"project": "fleet"})
    by_id = {item["session_id"]: item for item in result["sessions"]}
    assert by_id["s3"]["project_id"] == "p2"
    assert by_id["s1"]["project_label"] == "Work"


@pytest.mark.asyncio
async def test_an_unknown_project_names_the_projects_that_exist() -> None:
    caller = live_session("s1", token="tok")
    service = service_for(caller, projects=projects_stub())
    with pytest.raises(ValueError, match="Pixel Lab"):
        await service.list_sessions(caller, {"project": "pixellab"})


@pytest.mark.asyncio
async def test_a_session_in_another_project_resolves_only_under_a_wider_scope() -> None:
    caller = live_session("s1", token="tok")
    foreign = live_session("s3", project_id="p2")
    service = service_for(caller, foreign, projects=projects_stub())

    with pytest.raises(KeyError):
        await service.get_session(caller, {"session_id": "s3"})
    widened = await service.get_session(caller, {"session_id": "s3", "project": "fleet"})
    assert widened["session_id"] == "s3"
    assert widened["project_scope"] == "fleet"


@pytest.mark.asyncio
async def test_search_widens_its_project_filter_only_when_asked() -> None:
    history = HistoryStub()
    caller = live_session("s1", token="tok")
    service = service_for(caller, history=history, projects=projects_stub())

    await service.search_history(caller, {"query": "deploy", "project": "fleet"})
    await service.search_history(caller, {"query": "deploy", "project": "Pixel Lab"})

    assert history.page_calls[0]["project_id"] is None
    assert history.page_calls[1]["project_id"] == "p2"


@pytest.mark.asyncio
async def test_a_hit_from_another_project_is_not_readable_by_default() -> None:
    row = {
        "id": "run-9",
        "note_id": "session-9",
        "name": "claude-run",
        "backend": "claude",
        "project_id": "p2",
        "project_scope_id": "scope-2",
        "agent_visible": 1,
        "agent_run_seq": 1,
        "match_ordinal": 4,
        "match_role": "assistant",
        "match_ts": "2026-08-01T00:00:01Z",
        "match_mtime_ns": 1,
        "match_size": 2,
        "match_parser_version": 3,
        "excerpt": "the other project's answer",
        "match_kind": "message",
    }
    history = HistoryStub(
        [row], window={"stale": False, "messages": [{"ordinal": 4, "role": "assistant"}]}
    )
    caller = live_session("s1", token="tok")
    service = service_for(caller, history=history, projects=projects_stub())

    found = await service.search_history(caller, {"query": "answer", "project": "fleet"})
    hit_id = found["hits"][0]["hit_id"]
    assert found["hits"][0]["hit_id"]

    # The hit carries its own Project, so reading it back has to say `project`
    # again. A hit id is not a capability that widens the next call.
    with pytest.raises(KeyError):
        await service.read_transcript(caller, {"hit_id": hit_id})
    read = await service.read_transcript(caller, {"hit_id": hit_id, "project": "fleet"})
    assert read["agent_run_id"] == "run-9"


@pytest.mark.asyncio
async def test_search_history_returns_compact_hits_then_reads_only_hit_context() -> None:
    row = {
        "id": "run-1",
        "note_id": "session-1",
        "name": "claude-run",
        "backend": "claude",
        "project_id": "p1",
        "project_scope_id": "scope-1",
        "agent_visible": 1,
        "agent_run_seq": 3,
        "match_ordinal": 7,
        "match_role": "assistant",
        "match_ts": "2026-08-01T00:00:01Z",
        "match_mtime_ns": 123,
        "match_size": 456,
        "match_parser_version": 2,
        "excerpt": "the deployment needle is here",
        "match_kind": "message",
        "relevance": 0.9,
        "cwd": "D:/private/path",
    }
    history = HistoryStub(
        [row],
        window={
            "stale": False,
            "messages": [
                {"ordinal": 6, "role": "user", "ts": None, "text": "before"},
                {
                    "ordinal": 7,
                    "role": "assistant",
                    "ts": "2026-08-01T00:00:01Z",
                    "text": "the deployment needle is here",
                },
                {"ordinal": 8, "role": "user", "ts": None, "text": "after"},
            ],
        },
    )
    caller = live_session("s1", token="tok")
    service = service_for(
        caller,
        history=history,
        titles={"run-1": "Desktop packaging"},
    )

    search = await service.search_history(
        caller,
        {
            "query": "deployment needle",
            "roles": ["assistant"],
            "title_query": "packaging",
            "session_after": "2026-01-01T00:00:00Z",
            "message_before": "2027-01-01T00:00:00Z",
        },
    )

    assert search["hit_count"] == 1
    hit = search["hits"][0]
    assert hit["title"] == "Desktop packaging"
    assert hit["role"] == "assistant"
    assert hit["hit_id"]
    assert "cwd" not in hit
    assert history.page_calls[0]["search_scope"] == "assistant"
    assert history.page_calls[0]["title_query"] == "packaging"
    assert history.page_calls[0]["generated_title_run_ids"] == ("run-1",)

    context = await service.read_transcript(caller, {"hit_id": hit["hit_id"]})

    assert context["around_hit"] is True
    assert [message["text"] for message in context["messages"]] == [
        "before",
        "the deployment needle is here",
        "after",
    ]
    assert [message["is_match"] for message in context["messages"]] == [
        False,
        True,
        False,
    ]


@pytest.mark.asyncio
async def test_search_history_requires_filters_and_binds_cursor_to_query() -> None:
    caller = live_session("s1", token="tok")
    rows = [
        {
            "id": f"run-{index}",
            "name": f"run-{index}",
            "backend": "claude",
            "project_id": "p1",
            "project_scope_id": "scope-1",
            "match_ordinal": index,
            "match_role": "user",
            "match_ts": None,
            "match_mtime_ns": 1,
            "match_size": 2,
            "match_parser_version": 2,
            "excerpt": "needle",
            "match_kind": "message",
        }
        for index in range(3)
    ]
    history = HistoryStub(rows)
    service = service_for(caller, history=history)

    with pytest.raises(ValueError, match="query or at least one filter"):
        await service.search_history(caller, {})

    await service.search_history(
        caller, {"roles": ["user", "assistant"], "run_ids": ["run-0"]}
    )
    assert history.page_calls[-1]["include_metadata"] is False

    first = await service.search_history(
        caller, {"query": "needle", "limit": 2, "max_output_bytes": 2048}
    )
    assert first["next_cursor"]
    with pytest.raises(ValueError, match="does not match"):
        await service.search_history(
            caller,
            {"query": "different", "limit": 2, "cursor": first["next_cursor"]},
        )


# ------------------------------------------------- output shape and redaction


def test_session_summary_is_an_allowlist_that_cannot_leak_spawn_env() -> None:
    summary = session_summary(record("s1"), display_name="Fix Sidebar Sorting")
    flattened = json.dumps(summary)
    assert "spawn_env" not in flattened
    assert "sk-live" not in flattened
    assert summary["state"] == "working"
    assert summary["display_name"] == "Fix Sidebar Sorting"


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
async def test_read_transcript_defaults_to_a_small_window_and_text_budget(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "large.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "user",
                    "message": {"content": f"message {index} " + ("x" * 4_000)},
                }
            )
            for index in range(30)
        )
        + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    result = await service.read_transcript(
        caller, {"session_id": "s1", "max_output_bytes": 1_024}
    )

    assert result["message_count"] == 12
    assert result["messages"][0]["text"].startswith("message 18 ")
    assert result["returned_text_bytes"] <= 1_024
    assert result["content_truncated"] is True

    explicit = await service.read_transcript(
        caller,
        {"session_id": "s1", "max_messages": 200, "max_output_bytes": 1_024},
    )
    assert explicit["message_count"] == 30
    assert explicit["returned_text_bytes"] <= 1_024


@pytest.mark.asyncio
async def test_read_transcript_accepts_the_display_name(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "hello"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok")
    target = live_session("s2", transcript=transcript)
    service = service_for(caller, target, titles={"s2": "Fix Sidebar Sorting"})

    result = await service.read_transcript(
        caller, {"session_id": "Fix Sidebar Sorting"}
    )

    assert result["session_id"] == "s2"
    assert result["messages"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_self_defaults_resolve_the_calling_session(tmp_path: Path) -> None:
    transcript = tmp_path / "self.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "my transcript"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    details = await service.get_session(caller, {})
    transcript_result = await service.read_transcript(caller, {"from": "head"})

    assert details["session_id"] == "s1"
    assert transcript_result["session_id"] == "s1"
    assert transcript_result["messages"][0]["text"] == "my transcript"


@pytest.mark.asyncio
async def test_explicit_run_id_reads_a_colliding_superseded_run(tmp_path: Path) -> None:
    current_path = tmp_path / "current.jsonl"
    old_path = tmp_path / "old.jsonl"
    current_path.write_text(
        json.dumps({"type": "user", "message": {"content": "current marker"}}) + "\n",
        encoding="utf-8",
    )
    old_path.write_text(
        json.dumps({"type": "user", "message": {"content": "old marker"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=current_path)
    caller.record.agent_run_id = "run-current"
    caller.record.agent_run_seq = 1
    history = HistoryStub(
        [
            {
                "id": "s1",
                "note_id": "s1",
                "agent_run_seq": 0,
                "name": "claude-s1",
                "backend": "claude",
                "native_id": "s1",
                "transcript_path": str(old_path),
                "project_id": "p1",
                "project_scope_id": "scope-1",
                "agent_visible": 1,
            }
        ]
    )
    service = service_for(caller, history=history)

    result = await service.read_transcript(
        caller,
        {"session_id": "self", "agent_run_id": "s1", "from": "head"},
    )

    assert result["session_id"] == "s1"
    assert result["agent_run_id"] == "s1"
    assert result["agent_run_seq"] == 0
    assert result["own_superseded_run"] is True
    assert [item["text"] for item in result["messages"]] == ["old marker"]


@pytest.mark.asyncio
async def test_explicit_run_id_cannot_read_a_siblings_superseded_run() -> None:
    caller = live_session("s1", token="tok")
    sibling = live_session("s2")
    history = HistoryStub(
        [
            {
                "id": "run-sibling-old",
                "note_id": "s2",
                "backend": "claude",
                "project_id": "p1",
                "project_scope_id": "scope-1",
                "agent_visible": 1,
            }
        ]
    )
    service = service_for(caller, sibling, history=history)

    with pytest.raises(KeyError):
        await service.read_transcript(
            caller,
            {"session_id": "s2", "agent_run_id": "run-sibling-old"},
        )


@pytest.mark.asyncio
async def test_read_transcript_pages_from_both_ends_and_binds_cursor_to_run(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps({"type": "user", "message": {"content": f"message {index}"}})
            for index in range(6)
        )
        + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    other = live_session("s2", transcript=transcript)
    service = service_for(caller, other)

    head = await service.read_transcript(
        caller, {"session_id": "s1", "from": "head", "max_messages": 2}
    )
    head_next = await service.read_transcript(
        caller,
        {"session_id": "s1", "cursor": head["next_cursor"], "max_messages": 2},
    )
    tail = await service.read_transcript(
        caller, {"session_id": "s1", "from": "tail", "max_messages": 2}
    )

    assert [item["text"] for item in head["messages"]] == ["message 0", "message 1"]
    assert [item["text"] for item in head_next["messages"]] == ["message 2", "message 3"]
    assert [item["text"] for item in tail["messages"]] == ["message 4", "message 5"]
    assert {item["agent_run_id"] for item in head["messages"]} == {"s1"}
    with pytest.raises(ValueError, match="different agent run"):
        await service.read_transcript(
            caller, {"session_id": "s2", "cursor": head["next_cursor"]}
        )


@pytest.mark.asyncio
async def test_read_transcript_system_records_require_explicit_opt_in(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "isMeta": True,
                        "message": {
                            "content": "<system-reminder>internal context</system-reminder>"
                        },
                    }
                ),
                json.dumps({"type": "user", "message": {"content": "real request"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    service = service_for(caller)

    ordinary = await service.read_transcript(caller, {"session_id": "s1", "from": "head"})
    explicit = await service.read_transcript(
        caller,
        {"session_id": "s1", "from": "head", "include_system": True},
    )

    assert [item["text"] for item in ordinary["messages"]] == ["real request"]
    assert [item["role"] for item in explicit["messages"]] == ["meta", "user"]


@pytest.mark.asyncio
async def test_get_session_includes_run_brief_and_own_superseded_runs(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "opening request"}}) + "\n",
        encoding="utf-8",
    )
    caller = live_session("s1", token="tok", transcript=transcript)
    caller.record.agent_run_id = "run-current"
    caller.record.agent_run_seq = 1
    history = HistoryStub(
        [
            {
                "id": "run-old",
                "note_id": "s1",
                "agent_run_seq": 0,
                "name": "claude-s1",
                "backend": "claude",
                "project_id": "p1",
                "project_scope_id": "scope-1",
                "agent_visible": 1,
            },
            {
                "id": "run-current",
                "note_id": "s1",
                "agent_run_seq": 1,
                "name": "claude-s1",
                "backend": "claude",
                "project_id": "p1",
                "project_scope_id": "scope-1",
                "agent_visible": 1,
            },
        ]
    )
    service = service_for(caller, history=history, titles={"run-current": "Pinned work"})

    result = await service.get_session(caller, {"session_id": "s1"})

    assert result["run_brief"]["pinned_title"] == "Pinned work"
    assert result["run_brief"]["opening_request"] == "opening request"
    assert result["superseded_runs"][0]["agent_run_id"] == "run-old"
    assert result["superseded_runs"][0]["agent_run_seq"] == 0


@pytest.mark.asyncio
async def test_get_ended_session_uses_persisted_opening_request() -> None:
    caller = live_session("s1", token="tok")
    row = {
        "id": "run-old",
        "agent_run_id": "run-old",
        "name": "claude-old",
        "backend": "claude",
        "project_id": "p1",
        "project_scope_id": "scope-1",
        "agent_visible": 1,
    }
    store = AutomationStoreStub(
        checkpoints={"run-prompt:run-old": {"text": "persisted opening request"}}
    )
    service = service_for(
        caller,
        history=HistoryStub([row]),
        automation_store=store,
    )

    result = await service.get_session(caller, {"session_id": "run-old"})

    assert result["run_brief"]["opening_request"] == "persisted opening request"


@pytest.mark.asyncio
async def test_read_transcript_reports_absence_instead_of_fabricating(
    tmp_path: Path,
) -> None:
    caller = live_session("s1", token="tok", transcript=tmp_path / "missing.jsonl")
    service = service_for(caller)
    result = await service.read_transcript(caller, {"session_id": "s1"})
    assert result["messages"] == []
    assert "no transcript" in result["note"]


@pytest.mark.asyncio
async def test_read_transcript_refuses_a_stale_local_source_across_remote_boundary(
    tmp_path: Path,
) -> None:
    caller = live_session("s1", token="tok", transcript=tmp_path / "local.jsonl")
    caller.record.runtime_boundary = "remote"
    service = service_for(caller)

    result = await service.read_transcript(caller, {"session_id": "s1"})

    assert result["messages"] == []
    assert result["capability"] == "agent-bridge-unavailable"
    assert result["reason"] == "remote_terminal_boundary"
    assert result["agent_run_id"] == "s1"
    assert result["agent_run_seq"] == 0


@pytest.mark.asyncio
async def test_memory_tools_share_the_project_agent_context_inventory(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    home = tmp_path / "home"
    memory = home / "memory"
    root.mkdir()
    memory.mkdir(parents=True)
    (root / "AGENTS.md").write_text("project rules\n", encoding="utf-8")
    (memory / "MEMORY.md").write_text("learned fact\n", encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(
        '{"autoMemoryDirectory": "~/memory"}', encoding="utf-8"
    )
    context = AgentContextService(tmp_path / "backups", home=home)
    project = SimpleNamespace(id="p1", name="Work", root=str(root))
    projects = SimpleNamespace(projects={project.id: project})
    caller = live_session("s1", token="tok")
    service = service_for(
        caller,
        agent_context=context,
        projects=projects,
    )

    inventory = await service.memory_sources(caller, {})
    by_label = {item["label"]: item for item in inventory["sources"]}
    assert by_label["AGENTS.md"]["readers"] == ["codex", "omp", "pi", "opencode"]
    assert by_label["AGENTS.md"]["harness"] == "codex"
    assert by_label["AGENTS.md"]["entrypoint_kind"] == "project_root_instructions"
    assert by_label["MEMORY.md"]["harness"] == "claude"
    assert by_label["MEMORY.md"]["entrypoint_kind"] == "entrypoint"
    providers = {item["id"]: item for item in inventory["providers"]}
    assert providers["claude"]["status"] == "available"
    assert providers["pi"]["status"] == "unsupported"

    read = await service.read_memory(
        caller, {"source_id": by_label["MEMORY.md"]["id"]}
    )
    assert read["text"].replace("\r\n", "\n") == "learned fact\n"
    assert read["source"]["revision"] == by_label["MEMORY.md"]["revision"]
    assert read["source"]["harness"] == "claude"
    assert read["source"]["entrypoint_kind"] == "entrypoint"
    assert read["redacted"] is False
    with pytest.raises(ValueError, match="unknown agent context source"):
        await service.read_memory(caller, {"source_id": "memory:claude:.."})


@pytest.mark.asyncio
async def test_read_memory_withholds_credential_shaped_content(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENTS.md").write_text(
        "api_key = sk-live-abcdef1234567890\n", encoding="utf-8"
    )
    context = AgentContextService(tmp_path / "backups", home=tmp_path / "home")
    project = SimpleNamespace(id="p1", name="Work", root=str(root))
    caller = live_session("s1", token="tok")
    service = service_for(
        caller,
        agent_context=context,
        projects=SimpleNamespace(projects={project.id: project}),
    )

    read = await service.read_memory(caller, {"source_id": "instruction:codex"})
    assert read["redacted"] is True
    assert "sk-live" not in read["text"]


@pytest.mark.asyncio
async def test_project_notes_are_bounded_read_only_and_project_scoped(tmp_path: Path) -> None:
    root = tmp_path / "project"
    other_root = tmp_path / "other"
    root.mkdir()
    other_root.mkdir()
    identity = ProjectIdentity("p1", "Work", str(root), "registered")
    created = await create_note(root, "Release checks", project=identity)
    await write_note(
        root,
        created["id"],
        "All manual checks passed.\n",
        created["revision"],
        title="Release checks",
        project=identity,
    )
    project = SimpleNamespace(id="p1", name="Work", root=str(root))
    foreign = SimpleNamespace(id="p2", name="Other", root=str(other_root))
    caller = live_session("s1", token="tok")
    service = service_for(
        caller,
        projects=SimpleNamespace(projects={"p1": project, "p2": foreign}),
    )

    inventory = await service.project_notes(caller, {})
    note = await service.read_project_note(
        caller, {"note_id": inventory["notes"][0]["note_id"]}
    )

    assert inventory["project"] == {"id": "p1", "name": "Work"}
    assert note["markdown"] == "All manual checks passed.\n"
    assert "path" not in json.dumps(note)
    assert not (other_root / ".swe-mux").exists()


@pytest.mark.asyncio
async def test_notes_in_another_project_are_reachable_and_labelled(tmp_path: Path) -> None:
    root = tmp_path / "project"
    other_root = tmp_path / "other"
    root.mkdir()
    other_root.mkdir()
    foreign_identity = ProjectIdentity("p2", "Pixel Lab", str(other_root), "registered")
    created = await create_note(other_root, "Sprite pipeline", project=foreign_identity)
    await write_note(
        other_root,
        created["id"],
        "Atlas packing is done.\n",
        created["revision"],
        title="Sprite pipeline",
        project=foreign_identity,
    )
    caller = live_session("s1", token="tok")
    service = service_for(
        caller,
        projects=projects_stub(p1=str(root), p2=str(other_root)),
    )

    default = await service.project_notes(caller, {})
    fleet = await service.project_notes(caller, {"project": "fleet"})
    titles = {item["title"]: item for item in fleet["notes"]}

    assert "Sprite pipeline" not in {item["title"] for item in default["notes"]}
    assert titles["Sprite pipeline"]["project_name"] == "Pixel Lab"
    # A note id belongs to one Project, so the fleet form is what makes a
    # fleet-wide listing actionable rather than decorative.
    read = await service.read_project_note(
        caller, {"note_id": titles["Sprite pipeline"]["note_id"], "project": "fleet"}
    )
    assert read["markdown"] == "Atlas packing is done.\n"
    assert read["project"]["name"] == "Pixel Lab"
    with pytest.raises(QueueError):
        await service.read_project_note(
            caller, {"note_id": titles["Sprite pipeline"]["note_id"]}
        )


# ------------------------------------------------------------------ protocol


@pytest.mark.asyncio
async def test_initialize_negotiates_and_lists_closed_tool_allowlist() -> None:
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
    # A closed allowlist: Phase 5.6 adds only situational-awareness reads to the
    # two bounded Phase 5 writes,
    # neither of which delivers or spawns anything by itself. Phase 7.5 adds four
    # cross-session memory reads, and Phase 7.10 adds the doc_debt read. A new
    # tool must be added here deliberately.
    assert names == {
        "list_sessions",
        "get_session",
        "read_transcript",
        "search_history",
        "provenance",
        "verified_status",
        "prior_resolutions",
        "dead_ends",
        "doc_debt",
        "blast_radius",
        "find_definition",
        "find_callers",
        "find_references",
        "code_context",
        "test_gap",
        "memory_sources",
        "read_memory",
        "message_status",
        "project_notes",
        "read_project_note",
        "project_actions",
        "spawn_requests",
        "notify",
        "request_spawn",
        "run_action",
        "interrupt",
        "end_session",
    }
    assert names == {tool["name"] for tool in TOOLS}
    by_name = {tool["name"]: tool for tool in listing["result"]["tools"]}
    assert set(READ_TOOL_NAMES) | set(WRITE_TOOL_NAMES) == names
    assert all(by_name[name]["annotations"]["readOnlyHint"] for name in READ_TOOL_NAMES)
    assert all(
        not by_name[name]["annotations"]["readOnlyHint"] for name in WRITE_TOOL_NAMES
    )


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
    stats = service.status()["tools"]["list_sessions"]
    assert stats["calls"] == 1
    assert stats["response_bytes"] == len(
        good["result"]["content"][0]["text"].encode("utf-8")
    )
    assert stats["truncated_results"] == 0

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
        transcript_provisional=False,
        agent_lifecycle_id=None,
    ))
    meta = SessionManager._session_meta(session)
    assert meta["mcp_token"] == "mcp-tok"
    # And a Session constructed without one holds the never-authenticates value.
    assert Session.__init__.__kwdefaults__["mcp_token"] is None


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
    settings_path = Path(argv[argv.index("--settings") + 1])
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == [
        f"mcp__mux__{name}" for name in READ_TOOL_NAMES
    ]
    assert not any(
        f"mcp__mux__{name}" in settings["permissions"]["allow"]
        for name in WRITE_TOOL_NAMES
    )


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


def test_shims_register_mcp_only_when_the_session_holds_a_token(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from swe_mux.agent_launcher import _claude, _codex, _omp

    monkeypatch.setenv("MUX_CLAUDE_MCP_CONFIG", "C:/data/claude-mcp.json")
    monkeypatch.setenv("MUX_MCP_URL", "http://127.0.0.1:8765/mcp")
    monkeypatch.delenv("MUX_MCP_TOKEN", raising=False)
    monkeypatch.setenv("MUX_CLAUDE_ARGS", "[]")
    monkeypatch.setenv("MUX_CODEX_ARGS", "[]")
    monkeypatch.setenv("MUX_OMP_ARGS", "[]")
    monkeypatch.setenv("MUX_OMP_EXTENSION_ROOT", str(tmp_path / "omp-extensions"))
    monkeypatch.setenv("MUX_SESSION_ID", "mux-session")

    _, claude_args, _ = _claude([])
    assert "--mcp-config" not in claude_args
    _, codex_args, _ = _codex([])
    assert not any("mcp_servers" in arg for arg in codex_args)
    _, omp_args, _ = _omp([])
    omp_extension = Path(omp_args[omp_args.index("--extension") + 1])
    assert not (omp_extension / ".mcp.json").exists()

    monkeypatch.setenv("MUX_MCP_TOKEN", "tok")
    _, claude_args, _ = _claude([])
    assert claude_args[claude_args.index("--mcp-config") + 1] == "C:/data/claude-mcp.json"
    _, codex_args, _ = _codex([])
    assert any("mcp_servers.mux.url" in arg for arg in codex_args)
    _, omp_args, _ = _omp([])
    omp_extension = Path(omp_args[omp_args.index("--extension") + 1])
    server = json.loads((omp_extension / ".mcp.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ]["mux"]
    assert server["headers"]["Authorization"] == "Bearer tok"
