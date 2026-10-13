"""In-place restart of a live agent: the refusal policy and the route's sequence.

No real CLI anywhere: the graceful end and the resume authority are replaced with
fakes that move the session through the same states the real ones do, so what is
under test is the order of operations and which refusals a human may override.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web

from swe_mux import app_keys as keys
from swe_mux.layouts import layout_terminal_ids
from swe_mux.models import StandingActivity
from swe_mux.routes import sessions as session_routes
from swe_mux.routes.sessions import get_restart_eligibility, restart_session
from swe_mux.session import SessionManager
from swe_mux.session_restart import RestartFacts, assess_restart
from swe_mux.session_resume import ResumeRefused, resolve_latest_run

# ----------------------------------------------------------------- fixtures


def _record(**overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = {
        "id": "live-agent",
        "project_id": "default",
        "name": "worker",
        "backend": "claude",
        "state": "idle",
        "inactive": False,
        "cold": False,
        "runtime_boundary": "local",
        "native_session_id": "conv-1",
        "agent_run_id": "run-1",
        "observation_stale_since": None,
        "standing_activity": [],
        "running_work_since": None,
        "awaiting_reason": None,
        "auto_named": True,
        "pid": 0,
        "requested_end_reason": None,
    }
    values.update(overrides)
    record = SimpleNamespace(**values)
    record.snapshot = lambda: {"id": record.id, "backend": record.backend, "state": record.state}
    return record


def _session(record: SimpleNamespace | None = None, **attrs: Any) -> SimpleNamespace:
    session = SimpleNamespace(
        record=record or _record(),
        transcript_provisional=False,
        composer=None,
        pending_submit=None,
        last_input_event_ts=0.0,
        restart_in_progress=False,
        transcript_path=None,
        end_settled=asyncio.Event(),
    )
    for key, value in attrs.items():
        setattr(session, key, value)
    return session


def _codes(session: Any, facts: RestartFacts | None = None) -> list[str]:
    assessment = assess_restart(session, facts or RestartFacts())
    return [item.code for item in assessment.blockers]


# ------------------------------------------------------------------ policy


def test_an_idle_agent_with_a_known_conversation_is_eligible() -> None:
    assessment = assess_restart(_session(), RestartFacts())
    assert assessment.eligible
    assert assessment.payload() == {
        "eligible": True,
        "code": None,
        "reason": None,
        "force_allowed": False,
        "blockers": [],
        "would_lose": [],
    }


def test_a_shell_and_an_ended_pane_answer_with_one_reason_each() -> None:
    assert _codes(_session(_record(backend="shell"))) == ["not_agent"]
    assert _codes(_session(_record(state="exited"))) == ["session_ended"]
    assert _codes(_session(_record(state="crashed"))) == ["session_ended"]


@pytest.mark.parametrize(
    ("record_overrides", "session_attrs", "facts", "code"),
    [
        ({"inactive": True}, {}, RestartFacts(), "session_inactive"),
        ({"cold": True}, {}, RestartFacts(), "session_inactive"),
        ({"runtime_boundary": "remote"}, {}, RestartFacts(), "remote_boundary"),
        ({"native_session_id": ""}, {}, RestartFacts(), "conversation_unknown"),
        ({"agent_run_id": None}, {}, RestartFacts(), "conversation_unknown"),
        ({}, {"transcript_provisional": True}, RestartFacts(), "conversation_unproven"),
        ({"observation_stale_since": 1.0}, {}, RestartFacts(), "transcript_stale"),
        ({}, {}, RestartFacts(transcript_readable=False), "transcript_unavailable"),
        ({}, {}, RestartFacts(history_row_known=False), "history_unavailable"),
        ({}, {}, RestartFacts(hosts_daemon=True), "hosts_daemon"),
        ({}, {}, RestartFacts(project_known=False), "project_missing"),
        ({}, {"restart_in_progress": True}, RestartFacts(), "restart_in_progress"),
    ],
)
def test_identity_refusals_are_never_forceable(
    record_overrides: dict[str, Any],
    session_attrs: dict[str, Any],
    facts: RestartFacts,
    code: str,
) -> None:
    assessment = assess_restart(_session(_record(**record_overrides), **session_attrs), facts)
    assert code in [item.code for item in assessment.blockers]
    assert assessment.force_allowed is False
    assert not assessment.permits(force=True)


def test_a_codex_placeholder_id_is_not_a_conversation() -> None:
    # Codex mints its own id, so until discovery the record carries its own mux id.
    placeholder = _session(_record(backend="codex", id="cx", native_session_id="cx"))
    assert "conversation_unknown" in _codes(placeholder)
    # Claude is spawned with mux's id, so the same equality is a real conversation.
    dictated = _session(_record(backend="claude", id="cl", native_session_id="cl"))
    assert _codes(dictated) == []


@pytest.mark.parametrize(
    ("record_overrides", "session_attrs", "facts", "code", "loses"),
    [
        ({"state": "working"}, {}, RestartFacts(), "agent_working", "current turn"),
        ({"state": "starting"}, {}, RestartFacts(), "agent_working", "current turn"),
        (
            {"state": "awaiting", "awaiting_reason": "approval"},
            {},
            RestartFacts(),
            "awaiting_input",
            "pending approval prompt",
        ),
        (
            {
                "standing_activity": [
                    StandingActivity(kind="subagents", source="hook", evidence="e", since=1.0)
                ]
            },
            {},
            RestartFacts(),
            "background_work",
            "subagents",
        ),
        ({"running_work_since": 5.0}, {}, RestartFacts(), "background_work", "background"),
        (
            {},
            {"composer": SimpleNamespace(pending=True)},
            RestartFacts(),
            "unsent_input",
            "composer",
        ),
        ({}, {"pending_submit": object()}, RestartFacts(), "unsent_input", "composer"),
        ({}, {}, RestartFacts(input_age_seconds=0.5), "recent_input", "typed"),
        ({}, {}, RestartFacts(queued_messages=2), "queue_pending", "2 queued messages"),
        ({}, {}, RestartFacts(open_land_request=True), "land_in_progress", "auto-merge"),
    ],
)
def test_activity_refusals_name_what_a_forced_restart_loses(
    record_overrides: dict[str, Any],
    session_attrs: dict[str, Any],
    facts: RestartFacts,
    code: str,
    loses: str,
) -> None:
    assessment = assess_restart(_session(_record(**record_overrides), **session_attrs), facts)
    assert [item.code for item in assessment.blockers] == [code]
    assert assessment.force_allowed is True
    assert not assessment.permits(force=False)
    assert assessment.permits(force=True)
    (lost,) = assessment.payload()["would_lose"]
    assert loses in lost


def test_one_identity_refusal_makes_the_whole_set_unforceable() -> None:
    assessment = assess_restart(
        _session(_record(state="working", observation_stale_since=3.0)), RestartFacts()
    )
    codes = [item.code for item in assessment.blockers]
    assert codes == ["transcript_stale", "agent_working"]
    assert assessment.force_allowed is False
    # The probe still lists what a force would have lost, alongside why it cannot.
    assert assessment.payload()["would_lose"] == ["the agent's current turn will be interrupted"]


def test_old_input_is_not_somebody_typing() -> None:
    assert _codes(_session(), RestartFacts(input_age_seconds=60.0)) == []


# ------------------------------------------------------------- route stubs


class EventsStub:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    def emit_background(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    async def emit(self, event_type: str, **payload: Any) -> None:
        self.emitted.append((event_type, payload))

    def types(self) -> list[str]:
        return [kind for kind, _ in self.emitted]

    def payload(self, event_type: str) -> dict[str, Any]:
        matches = [payload for kind, payload in self.emitted if kind == event_type]
        assert len(matches) == 1, f"expected exactly one {event_type}, got {len(matches)}"
        return matches[0]


class ProjectsStub:
    def __init__(self, *terminal_ids: str) -> None:
        self.projects = {
            "default": SimpleNamespace(
                id="default",
                name="Main",
                root=".",
                layout={
                    "version": 7,
                    "root": {
                        "type": "stack",
                        "id": "pane-1",
                        "children": [
                            {"type": "leaf", "kind": "terminal", "id": identity}
                            for identity in terminal_ids
                        ],
                        "active_child_id": terminal_ids[0],
                    },
                },
                layout_revision=4,
            )
        }
        self.revisions: list[int] = []

    async def update(self, identity: str, **changes: Any) -> Any:
        project = self.projects[identity]
        self.revisions.append(changes["layout_revision"])
        project.layout = changes["layout"]
        project.layout_revision += 1
        return project


class SessionsStub:
    def __init__(self, *sessions: Any) -> None:
        self.sessions = {session.record.id: session for session in sessions}
        self.stopped: list[str] = []

    def resolve(self, identity: str) -> Any:
        return self.sessions[identity]

    async def stop(self, identity: str, *, reason: str = "killed") -> None:
        self.stopped.append(identity)


class HistoryStub:
    def __init__(self, transcript: Path) -> None:
        self.row = {
            "id": "run-1",
            "backend": "claude",
            "native_id": "conv-1",
            "transcript_path": str(transcript),
            "cwd": str(transcript.parent),
            "agent_visible": 1,
        }
        self.reads = 0

    async def history_entry(self, identity: str) -> dict[str, Any] | None:
        self.reads += 1
        return dict(self.row) if identity == "run-1" else None


class LineageStub:
    def __init__(self) -> None:
        self.edges: list[tuple[str, str, str]] = []

    async def add_lineage(self, parent: str, child: str, relation: str, _meta: Any) -> None:
        self.edges.append((parent, child, relation))


class QueueStoreStub:
    def __init__(self, pending: int = 0) -> None:
        self.pending = pending

    async def messages_for_target(self, _identity: str) -> dict[str, Any]:
        return {"messages": [], "pending": self.pending}


def _app(tmp_path: Path, old: Any, *, queued: int = 0) -> dict[Any, Any]:
    transcript = tmp_path / "conv-1.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    return {
        keys.SESSIONS: SessionsStub(old),
        keys.PROJECTS: ProjectsStub(old.record.id, "unrelated-live"),
        keys.HISTORY: HistoryStub(transcript),
        keys.AUTOMATION_STORE: LineageStub(),
        keys.EVENTS: EventsStub(),
        keys.CONFIG: SimpleNamespace(data_dir=tmp_path),
        keys.PROMPT_QUEUE_STORE: QueueStoreStub(queued),
    }


def _request(
    app: dict[Any, Any], sid: str, body: dict[str, Any] | None = None, **headers: str
) -> Any:
    async def read_json() -> Any:
        if body is None:
            raise json.JSONDecodeError("empty", "", 0)
        return body

    return SimpleNamespace(
        app=app,
        match_info={"sid": sid},
        headers=headers,
        json=read_json,
        path=f"/api/sessions/{sid}/restart",
    )


def _new_session(run_id: str = "run-1") -> SimpleNamespace:
    record = SimpleNamespace(id="restarted-agent", agent_run_id=run_id, backend="claude")
    record.snapshot = lambda: {"id": "restarted-agent", "backend": "claude"}
    return SimpleNamespace(record=record)


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    app: dict[Any, Any],
    old: Any,
    *,
    new: Any = None,
    refusal: ResumeRefused | None = None,
    settle: bool = True,
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake_end(_app: Any, session: Any, reason: str) -> dict[str, Any]:
        seen["end_reason"] = reason
        session.record.requested_end_reason = reason
        session.record.state = "exited"
        if settle:
            session.end_settled.set()
        return {"final_state": "exited", "graceful": True, "reason": reason}

    async def fake_resume(row: dict[str, Any], **kwargs: Any) -> Any:
        seen["resume_row"] = row
        seen["resume_kwargs"] = kwargs
        # The old pane must be fully torn down before the same run is reopened.
        assert old.end_settled.is_set()
        if refusal is not None:
            raise refusal
        replacement = new or _new_session()
        app[keys.SESSIONS].sessions[replacement.record.id] = replacement
        return SimpleNamespace(session=replacement, attempts=1)

    # Recorded rather than raised: cleanup after the layout commit is best-effort, so
    # an assertion raised in here would be logged and swallowed instead of failing.
    seen["media_discarded"] = []

    async def record_media_discard(_app: Any, identity: str) -> None:
        seen["media_discarded"].append(identity)

    monkeypatch.setattr(session_routes.terminal, "_end_session_gracefully", fake_end)
    monkeypatch.setattr(session_routes, "resume_run", fake_resume)
    monkeypatch.setattr(session_routes, "_discard_session_media", record_media_discard)
    return seen


# ------------------------------------------------------------------- route


async def test_restart_resumes_the_same_conversation_into_the_same_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    seen = _install_fakes(monkeypatch, app, old)

    response = await restart_session(_request(app, old.record.id))

    assert response.status == 201
    body = json.loads(cast(Any, response).body)
    assert body["replaced"] == old.record.id
    assert body["session"]["id"] == "restarted-agent"
    assert body["overridden"] == []
    assert seen["end_reason"] == "restarted"
    # An auto-named pane passes no name, so it keeps following its generated title.
    assert seen["resume_kwargs"]["name"] == ""
    assert seen["resume_kwargs"]["target_project_id"] == "default"
    sessions = app[keys.SESSIONS].sessions
    assert old.record.id not in sessions and "restarted-agent" in sessions
    project = app[keys.PROJECTS].projects["default"]
    assert layout_terminal_ids(project.layout) == ["restarted-agent", "unrelated-live"]
    assert project.layout["root"]["active_child_id"] == "restarted-agent"
    # The resumed pane inherited the run, so there is no new run to link.
    assert app[keys.AUTOMATION_STORE].edges == []
    # The conversation continues, so the media it was handed must survive.
    assert seen["media_discarded"] == []
    event = app[keys.EVENTS].payload("session_restarted")
    assert event["replaced"] == old.record.id
    assert event["forced"] is False
    assert event["duration_ms"] >= 0
    assert old.restart_in_progress is False


async def test_a_pinned_name_stays_pinned_and_a_new_run_is_linked_as_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session(_record(auto_named=False, name="Pinned"))
    app = _app(tmp_path, old)
    seen = _install_fakes(monkeypatch, app, old, new=_new_session("run-2"))

    response = await restart_session(_request(app, old.record.id))

    assert response.status == 201
    assert seen["resume_kwargs"]["name"] == "Pinned"
    assert app[keys.AUTOMATION_STORE].edges == [("run-1", "run-2", "restart")]


async def test_a_busy_agent_is_refused_until_a_human_forces_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session(_record(state="working"))
    app = _app(tmp_path, old, queued=1)
    _install_fakes(monkeypatch, app, old)

    refused = await restart_session(_request(app, old.record.id))
    assert refused.status == 409
    body = json.loads(cast(Any, refused).body)
    assert body["code"] == "agent_working"
    assert body["session_id"] == old.record.id
    assert body["force_allowed"] is True
    assert [item["code"] for item in body["blockers"]] == ["agent_working", "queue_pending"]
    assert len(body["would_lose"]) == 2
    assert old.record.state == "working"  # nothing was touched

    forced = await restart_session(_request(app, old.record.id, {"force": True}))
    assert forced.status == 201
    assert json.loads(cast(Any, forced).body)["overridden"] == ["agent_working", "queue_pending"]
    assert app[keys.EVENTS].payload("session_restarted")["forced"] is True


async def test_force_cannot_override_an_identity_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session(_record(observation_stale_since=10.0))
    app = _app(tmp_path, old)
    seen = _install_fakes(monkeypatch, app, old)

    response = await restart_session(_request(app, old.record.id, {"force": True}))

    assert response.status == 409
    body = json.loads(cast(Any, response).body)
    assert body["code"] == "transcript_stale"
    assert body["force_allowed"] is False
    assert "end_reason" not in seen


async def test_a_failed_resume_leaves_the_ended_pane_and_its_resume_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    _install_fakes(
        monkeypatch,
        app,
        old,
        refusal=ResumeRefused("resume_failed", "the resumed session exited", status=503),
    )

    response = await restart_session(_request(app, old.record.id))

    assert response.status == 503
    body = json.loads(cast(Any, response).body)
    assert body["code"] == "resume_failed"
    assert body["stopped"] is True
    # The ended session stays registered and in the layout: its own Resume is the way back.
    assert old.record.id in app[keys.SESSIONS].sessions
    assert old.record.state == "exited"
    project = app[keys.PROJECTS].projects["default"]
    assert layout_terminal_ids(project.layout) == [old.record.id, "unrelated-live"]
    assert app[keys.EVENTS].payload("session_restart_failed")["code"] == "resume_failed"
    assert "session_restarted" not in app[keys.EVENTS].types()


async def test_a_failed_layout_write_stops_the_replacement_and_keeps_the_ended_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    _install_fakes(monkeypatch, app, old)

    async def failing_update(_identity: str, **_changes: Any) -> Any:
        raise OSError("disk full")

    monkeypatch.setattr(app[keys.PROJECTS], "update", failing_update)

    response = await restart_session(_request(app, old.record.id))

    assert response.status == 500
    body = json.loads(cast(Any, response).body)
    assert body["code"] == "restart_replace_failed"
    assert body["stopped"] is True
    sessions = app[keys.SESSIONS]
    assert sessions.stopped == ["restarted-agent"]
    assert "restarted-agent" not in sessions.sessions
    assert old.record.id in sessions.sessions
    project = app[keys.PROJECTS].projects["default"]
    assert layout_terminal_ids(project.layout) == [old.record.id, "unrelated-live"]
    assert app[keys.EVENTS].payload("session_restart_failed")["code"] == "restart_replace_failed"


async def test_a_project_removed_mid_restart_is_a_refusal_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    seen = _install_fakes(monkeypatch, app, old)
    original_resume = session_routes.resume_run

    async def resume_then_remove_project(row: dict[str, Any], **kwargs: Any) -> Any:
        outcome = await original_resume(row, **kwargs)
        app[keys.PROJECTS].projects.clear()
        return outcome

    monkeypatch.setattr(session_routes, "resume_run", resume_then_remove_project)

    response = await restart_session(_request(app, old.record.id))

    assert "resume_row" in seen
    assert response.status == 409
    assert json.loads(cast(Any, response).body)["code"] == "restart_replace_failed"
    assert app[keys.SESSIONS].stopped == ["restarted-agent"]
    assert old.record.id in app[keys.SESSIONS].sessions


async def test_bookkeeping_after_the_layout_commit_cannot_fail_the_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    _install_fakes(monkeypatch, app, old, new=_new_session("run-2"))

    async def failing_lineage(*_args: Any) -> None:
        raise OSError("database is locked")

    monkeypatch.setattr(app[keys.AUTOMATION_STORE], "add_lineage", failing_lineage)

    response = await restart_session(_request(app, old.record.id))

    # The replacement is live and in the pane; reporting failure would strand it.
    assert response.status == 201
    assert app[keys.SESSIONS].stopped == []
    assert old.record.id not in app[keys.SESSIONS].sessions
    project = app[keys.PROJECTS].projects["default"]
    assert layout_terminal_ids(project.layout) == ["restarted-agent", "unrelated-live"]


async def test_a_teardown_that_never_settles_is_reported_rather_than_raced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    seen = _install_fakes(monkeypatch, app, old, settle=False)
    monkeypatch.setattr(session_routes, "END_SETTLE_TIMEOUT_SECONDS", 0.05)

    response = await restart_session(_request(app, old.record.id))

    assert response.status == 503
    assert json.loads(cast(Any, response).body)["code"] == "restart_settle_timeout"
    assert "resume_row" not in seen


async def test_a_second_restart_is_refused_while_the_first_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    app = _app(tmp_path, old)
    _install_fakes(monkeypatch, app, old)
    old.restart_in_progress = True

    response = await restart_session(_request(app, old.record.id, {"force": True}))

    assert response.status == 409
    assert json.loads(cast(Any, response).body)["code"] == "restart_in_progress"


async def test_an_agent_sessions_own_caller_identity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _session()
    old.mcp_token = "secret-token"
    app = _app(tmp_path, old)
    seen = _install_fakes(monkeypatch, app, old)
    request = _request(
        app,
        old.record.id,
        **{"X-Mux-Caller-Session": old.record.id, "X-Mux-Caller-Token": "secret-token"},
    )

    with pytest.raises(web.HTTPForbidden):
        await restart_session(request)
    assert "end_reason" not in seen


async def test_the_probe_reports_the_same_assessment_the_restart_enforces(
    tmp_path: Path,
) -> None:
    old = _session(_record(state="awaiting", awaiting_reason="approval"))
    app = _app(tmp_path, old)

    response = await get_restart_eligibility(_request(app, old.record.id))

    body = json.loads(cast(Any, response).body)
    assert response.status == 200
    assert body["session_id"] == old.record.id
    assert body["eligible"] is False
    assert body["code"] == "awaiting_input"
    assert body["force_allowed"] is True


async def test_the_probe_of_a_shell_reads_nothing(tmp_path: Path) -> None:
    shell = _session(_record(backend="shell"))

    class Untouchable:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"a shell's probe must not read {name}")

    app = {
        keys.SESSIONS: SessionsStub(shell),
        keys.HISTORY: Untouchable(),
        keys.PROJECTS: Untouchable(),
    }
    response = await get_restart_eligibility(_request(app, shell.record.id))
    assert json.loads(cast(Any, response).body)["code"] == "not_agent"


# ------------------------------------------------------ supporting changes


async def test_end_settled_is_set_even_when_teardown_raises() -> None:
    session = SimpleNamespace(record=SimpleNamespace(state="idle"), end_settled=asyncio.Event())

    class Manager:
        async def _retire_ended_session(self, _session: Any, _reason: str) -> None:
            raise RuntimeError("teardown failed")

    with pytest.raises(RuntimeError):
        await SessionManager._mark_ended(cast(Any, Manager()), cast(Any, session), "process_exit")
    assert session.end_settled.is_set()


async def test_an_already_ended_session_is_not_settled_by_a_second_call() -> None:
    session = SimpleNamespace(record=SimpleNamespace(state="exited"), end_settled=asyncio.Event())

    class Manager:
        async def _retire_ended_session(self, _session: Any, _reason: str) -> None:
            raise AssertionError("an ended session is retired once")

    await SessionManager._mark_ended(cast(Any, Manager()), cast(Any, session), "process_exit")
    # Whoever ended it first owns the event; a racing second call must not claim it.
    assert not session.end_settled.is_set()


async def test_the_latest_run_follows_a_restart_like_a_resume() -> None:
    rows = {"run-1": {"id": "run-1"}, "run-2": {"id": "run-2"}}

    class History:
        async def history_entry(self, identity: str) -> dict[str, Any] | None:
            return rows.get(identity)

    class Store:
        async def lineage(self, run_id: str) -> list[dict[str, Any]]:
            edges = [
                {"parent_run_id": "run-1", "child_run_id": "run-2", "relation": "restart"},
                {"parent_run_id": "run-1", "child_run_id": "run-9", "relation": "branch"},
            ]
            return [
                edge
                for edge in edges
                if run_id in (edge["parent_run_id"], edge["child_run_id"])
            ]

    latest = await resolve_latest_run("run-1", history=History(), automation_store=Store())
    assert latest == {"id": "run-2"}
