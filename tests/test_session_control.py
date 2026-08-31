"""Phase 7.6: agent session control - interrupt and end.

What these pin is the contract: the install master switch and the per-Project
grant gate every action; `draft` writes an inert request and acts
nothing; `granted` (the default since 2026-08-25 - the grant values here are
injected, so the tests pin behaviour per level rather than the default) acts
inside bounds (per-origin budget, reciprocal-cycle guard,
idempotency) and an interrupt only when delivery-readiness is `safe`; a shell
pane, an out-of-scope session, and the daemon-hosting session are never valid
targets; and self-end returns before teardown. MCP is transport - every bound
lives in the service.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.adapters import build_agent_adapter
from swe_mux.harness import HARNESSES, is_agent_harness
from swe_mux.prompt_queue import QueueError
from swe_mux.session_control import SessionControlService
from tests.test_mcp import live_session, manager_for


class EventsStub:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **kwargs: Any) -> None:
        self.emitted.append((name, kwargs))

    def names(self) -> list[str]:
        return [name for name, _ in self.emitted]


def _projects(root: str = "D:/work") -> Any:
    return SimpleNamespace(
        projects={"p1": SimpleNamespace(id="p1", name="Work", root=root)}
    )


def _config(**overrides: Any) -> Any:
    base = dict(
        session_control_enabled=True,
        session_control_hourly_budget=30,
        session_control_graceful_timeout_s=12.0,
        request_spawn_enabled=True,
        agent_spawn_hourly_budget=10,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _build(
    *sessions: Any,
    grant: str = "granted",
    spawn_grant: str = "granted",
    automation_on: bool = True,
    readiness: str = "safe",
    config: Any = None,
    events: EventsStub | None = None,
    clock: _Clock | None = None,
    interrupt_op: Any = None,
    graceful_op: Any = None,
    daemon_owner_ids: frozenset[str] = frozenset(),
    append_observation: Any = None,
    spawn_op: Any = None,
    settle_op: Any = None,
    draft_spawn: Any = None,
    projects: Any = None,
) -> SessionControlService:
    async def gate(_root: str) -> frozenset[str]:
        return frozenset({"session_control"}) if automation_on else frozenset()

    def grant_field(_root: str) -> str:
        return grant

    def spawn_grant_field(_root: str) -> str:
        return spawn_grant

    def readiness_evaluate(_session: Any) -> dict[str, Any]:
        return {"delivery_state": readiness, "reason": "test"}

    def is_daemon_owner(session: Any) -> bool:
        return str(session.record.id) in daemon_owner_ids

    return SessionControlService(
        sessions=manager_for(*sessions),
        projects=projects or _projects(),
        config=config or _config(),
        events=events or EventsStub(),
        readiness_evaluate=readiness_evaluate,
        automation_gate=gate,
        grant_field=grant_field,
        interrupt_op=interrupt_op or _noop_interrupt,
        graceful_end_op=graceful_op or _noop_graceful,
        is_daemon_owner=is_daemon_owner,
        spawn_grant_field=spawn_grant_field,
        spawn_op=spawn_op,
        settle_op=settle_op,
        draft_spawn=draft_spawn,
        append_observation=append_observation,
        clock=clock or _Clock(),
    )


async def _noop_interrupt(_session: Any) -> None:
    return None


async def _noop_graceful(session: Any, reason: str) -> dict[str, Any]:
    session.record.state = "exited"
    session.record.requested_end_reason = reason
    return {"final_state": "exited", "graceful": True, "reason": reason}


def _caller(sid: str = "s1") -> Any:
    return live_session(sid, token=f"tok-{sid}", project_id="p1", scope_id="scope-1")


def _target(sid: str = "s2", *, backend: str = "claude", state: str = "working") -> Any:
    return live_session(
        sid, token=f"tok-{sid}", project_id="p1", scope_id="scope-1",
        backend=backend, state=state,
    )


# ------------------------------------------------------------- master switch


@pytest.mark.asyncio
async def test_master_switch_off_refuses() -> None:
    caller, target = _caller(), _target()
    service = _build(caller, target, config=_config(session_control_enabled=False))
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(caller, target="s2")
    assert excinfo.value.code == "session_control_disabled"


@pytest.mark.asyncio
async def test_grant_off_when_automation_not_enabled() -> None:
    caller, target = _caller(), _target()
    service = _build(caller, target, automation_on=False)
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(caller, target="s2")
    assert excinfo.value.code == "session_control_not_enabled"


# -------------------------------------------------------------------- draft


@pytest.mark.asyncio
async def test_draft_writes_inert_request_and_acts_nothing() -> None:
    caller, target = _caller(), _target()
    interrupts: list[Any] = []
    drafts: list[dict[str, Any]] = []

    async def record_interrupt(session: Any) -> None:
        interrupts.append(session)

    async def append_obs(
        root: str, body: str, *, kind: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        drafts.append({"root": root, "body": body, "kind": kind, "request": request})
        return {"appended_id": "obs-1"}

    events = EventsStub()
    service = _build(
        caller, target, grant="draft", events=events,
        interrupt_op=record_interrupt, append_observation=append_obs,
    )
    result = await service.interrupt(caller, target="s2", reason="stuck in a loop")
    assert result["status"] == "drafted"
    assert result["request_id"] == "obs-1"
    # Nothing acted.
    assert interrupts == []
    assert drafts[0]["kind"] == "control_request"
    assert drafts[0]["request"]["action"] == "interrupt"
    assert drafts[0]["request"]["status"] == "pending"
    assert "agent_control_drafted" in events.names()


# ------------------------------------------------------------ granted: act


@pytest.mark.asyncio
async def test_granted_interrupt_gates_on_readiness_safe() -> None:
    caller, target = _caller(), _target()
    interrupts: list[Any] = []

    async def record_interrupt(session: Any) -> None:
        interrupts.append(session.record.id)

    service = _build(caller, target, grant="granted", interrupt_op=record_interrupt)
    result = await service.interrupt(caller, target="s2")
    assert result["status"] == "interrupted"
    assert interrupts == ["s2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["blocked", "unknown"])
async def test_granted_interrupt_refused_when_not_safe(state: str) -> None:
    caller, target = _caller(), _target()
    interrupts: list[Any] = []

    async def record_interrupt(session: Any) -> None:
        interrupts.append(session)

    service = _build(
        caller, target, grant="granted", readiness=state,
        interrupt_op=record_interrupt,
    )
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(caller, target="s2")
    assert excinfo.value.code == "readiness_not_safe"
    assert excinfo.value.payload.get("delivery_state") == state
    # Fail closed: nothing was written to the target.
    assert interrupts == []


@pytest.mark.asyncio
async def test_granted_end_other_session_is_graceful() -> None:
    caller, target = _caller(), _target()
    ends: list[tuple[str, str]] = []

    async def graceful(session: Any, reason: str) -> dict[str, Any]:
        ends.append((session.record.id, reason))
        session.record.state = "exited"
        return {"final_state": "exited", "graceful": True, "reason": reason}

    service = _build(caller, target, grant="granted", graceful_op=graceful)
    result = await service.end_session(caller, target="s2", reason="worker done")
    assert result["status"] == "ended"
    assert result["graceful"] is True
    assert ends == [("s2", "agent_ended")]


@pytest.mark.asyncio
async def test_self_end_returns_before_teardown() -> None:
    caller = _caller()
    started = asyncio.Event()
    release = asyncio.Event()
    torn_down: list[str] = []

    async def slow_graceful(session: Any, reason: str) -> dict[str, Any]:
        started.set()
        await release.wait()  # would deadlock if end_session awaited this
        torn_down.append(session.record.id)
        session.record.state = "exited"
        return {"final_state": "exited", "graceful": True, "reason": reason}

    service = _build(caller, grant="granted", graceful_op=slow_graceful)
    result = await service.end_session(caller, target="self")
    # Returned before teardown finished.
    assert result["status"] == "ending"
    assert result["self"] is True
    assert torn_down == []
    # The teardown was scheduled and is now running.
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    await asyncio.sleep(0)


# --------------------------------------------------------- forbidden targets


@pytest.mark.asyncio
async def test_out_of_scope_target_is_not_found() -> None:
    caller = _caller()
    other = live_session(
        "s9", token="t9", project_id="p2", scope_id="scope-2", backend="claude"
    )
    service = _build(caller, other, grant="granted")
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(caller, target="s9")
    assert excinfo.value.code == "unknown_target"
    assert excinfo.value.status == 404


@pytest.mark.asyncio
async def test_shell_pane_is_not_a_valid_target() -> None:
    caller = _caller()
    shell = _target("s2", backend="shell")
    assert not is_agent_harness("shell")
    service = _build(caller, shell, grant="granted")
    with pytest.raises(QueueError) as excinfo:
        await service.end_session(caller, target="s2")
    assert excinfo.value.code == "not_agent_target"


@pytest.mark.asyncio
async def test_daemon_owning_session_is_forbidden() -> None:
    caller = _caller()
    host = _target("s2")
    service = _build(caller, host, grant="granted", daemon_owner_ids=frozenset({"s2"}))
    with pytest.raises(QueueError) as excinfo:
        await service.end_session(caller, target="s2")
    assert excinfo.value.code == "forbidden_target"


@pytest.mark.asyncio
async def test_interrupt_cannot_target_self() -> None:
    caller = _caller()
    service = _build(caller, grant="granted")
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(caller, target="self")
    assert excinfo.value.code == "self_not_allowed"


@pytest.mark.asyncio
async def test_ended_target_refused() -> None:
    caller = _caller()
    dead = _target("s2", state="exited")
    service = _build(caller, dead, grant="granted")
    with pytest.raises(QueueError) as excinfo:
        await service.end_session(caller, target="s2")
    assert excinfo.value.code == "target_ended"


# ------------------------------------------------------------------ bounds


@pytest.mark.asyncio
async def test_per_origin_budget_exhausts() -> None:
    caller = _caller()
    targets = [_target(f"t{i}") for i in range(3)]
    clock = _Clock()
    service = _build(
        caller, *targets, grant="granted",
        config=_config(session_control_hourly_budget=2), clock=clock,
    )
    await service.interrupt(caller, target="t0")
    await service.interrupt(caller, target="t1")
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(caller, target="t2")
    assert excinfo.value.code == "origin_budget_exhausted"


@pytest.mark.asyncio
async def test_reciprocal_cycle_is_refused() -> None:
    a = _caller("a")
    b = _target("b")
    # Both are agents in the same project; make each addressable as caller.
    service = _build(a, b, grant="granted")
    # a ends... no, use interrupt so neither actually ends. a interrupts b.
    await service.interrupt(a, target="b")
    # Now b interrupting a back closes the ring.
    b_caller = b
    with pytest.raises(QueueError) as excinfo:
        await service.interrupt(b_caller, target="a")
    assert excinfo.value.code == "relay_cycle"


@pytest.mark.asyncio
async def test_idempotent_correlation_acts_once() -> None:
    caller, target = _caller(), _target()
    calls: list[str] = []

    async def record_interrupt(session: Any) -> None:
        calls.append(session.record.id)

    service = _build(caller, target, grant="granted", interrupt_op=record_interrupt)
    first = await service.interrupt(caller, target="s2", correlation_id="k1")
    second = await service.interrupt(caller, target="s2", correlation_id="k1")
    assert first == second
    # Acted once despite two calls.
    assert calls == ["s2"]


# ------------------------------------------------------------ audit / silent


@pytest.mark.asyncio
async def test_actions_emit_audit_events() -> None:
    caller, target = _caller(), _target()
    events = EventsStub()
    service = _build(caller, target, grant="granted", events=events)
    await service.interrupt(caller, target="s2", reason="loop")
    assert "agent_session_control" in events.names()
    payload = dict(events.emitted[-1][1])
    assert payload["action"] == "interrupt"
    assert payload["target_session_id"] == "s2"


# --------------------------------------------------------- granted spawn


def _two_projects() -> Any:
    return SimpleNamespace(
        projects={
            "p1": SimpleNamespace(id="p1", name="Work", root="D:/work"),
            "p2": SimpleNamespace(id="p2", name="Continuity", root="D:/cont"),
        }
    )


@pytest.mark.asyncio
async def test_granted_spawn_creates_session_directly() -> None:
    caller = _caller()
    bodies: list[dict[str, Any]] = []

    async def spawn_op(body: dict[str, Any]) -> Any:
        bodies.append(body)
        return live_session("new-1", project_id="p1", backend="claude")

    events = EventsStub()
    service = _build(caller, spawn_grant="granted", spawn_op=spawn_op, events=events)
    result = await service.spawn(caller, prompt="do the thing", backend="claude")
    assert result["status"] == "spawned"
    assert result["session_id"] == "new-1"
    assert result["cross_project"] is False
    assert bodies[0] == {
        "project_id": "p1",
        "backend": "claude",
        "seed_text": "do the thing",
    }
    payload = dict(events.emitted[-1][1])
    assert payload["action"] == "spawn" and payload["outcome"] == "spawned"


@pytest.mark.asyncio
async def test_granted_spawn_into_another_project() -> None:
    caller = _caller()
    bodies: list[dict[str, Any]] = []

    async def spawn_op(body: dict[str, Any]) -> Any:
        bodies.append(body)
        return live_session("cont-1", project_id="p2", backend="claude")

    service = _build(
        caller, spawn_grant="granted", spawn_op=spawn_op, projects=_two_projects()
    )
    result = await service.spawn(caller, prompt="update the editor", project="Continuity")
    assert result["status"] == "spawned"
    assert result["cross_project"] is True
    assert bodies[0]["project_id"] == "p2"


@pytest.mark.asyncio
async def test_a_granted_spawn_records_the_pane_hint_one_shot() -> None:
    """A placement request, not layout: the daemon records what was asked for
    on the session and the first browser viewing the Project claims it."""
    caller = _caller()
    created: list[Any] = []

    async def spawn_op(body: dict[str, Any]) -> Any:
        session = live_session("new-1", project_id="p1", backend="claude")
        session.record.pane_hint = ""
        created.append(session)
        return session

    service = _build(caller, spawn_grant="granted", spawn_op=spawn_op)
    await service.spawn(caller, prompt="go", backend="claude", pane="split_vertical")
    assert created[0].record.pane_hint == "split_vertical"

    # Omitted: nothing is stamped.
    await service.spawn(caller, prompt="go", backend="claude")
    assert created[1].record.pane_hint == ""


@pytest.mark.asyncio
async def test_a_drafted_spawn_carries_the_deferred_watch_and_pane() -> None:
    caller = _caller()
    drafts: list[dict[str, Any]] = []

    async def spawn_op(body: dict[str, Any]) -> Any:  # pragma: no cover - draft path
        raise AssertionError("a draft must not spawn")

    async def draft(_caller: Any, **kwargs: Any) -> dict[str, Any]:
        drafts.append(kwargs)
        return {"status": "drafted", "request_id": "r1"}

    service = _build(caller, spawn_grant="draft", spawn_op=spawn_op, draft_spawn=draft)
    await service.spawn(
        caller,
        prompt="later",
        pane="split_horizontal",
        watch=True,
        watch_timeout_minutes=45,
    )
    assert drafts[0]["pane"] == "split_horizontal"
    assert drafts[0]["watch"] is True
    assert drafts[0]["watch_timeout_minutes"] == 45


@pytest.mark.asyncio
async def test_spawn_drafts_when_grant_is_draft() -> None:
    caller = _caller()
    spawns: list[Any] = []
    drafts: list[dict[str, Any]] = []

    async def spawn_op(body: dict[str, Any]) -> Any:
        spawns.append(body)
        return live_session("x")

    async def draft(_caller: Any, **kwargs: Any) -> dict[str, Any]:
        drafts.append(kwargs)
        return {"status": "drafted", "request_id": "r1"}

    service = _build(
        caller, spawn_grant="draft", spawn_op=spawn_op, draft_spawn=draft
    )
    result = await service.spawn(caller, prompt="later")
    assert result["status"] == "drafted"
    assert spawns == []  # nothing was created


@pytest.mark.asyncio
async def test_spawn_drafts_when_automation_off() -> None:
    caller = _caller()
    drafts: list[dict[str, Any]] = []

    async def draft(_caller: Any, **kwargs: Any) -> dict[str, Any]:
        drafts.append(kwargs)
        return {"status": "drafted"}

    async def spawn_op(body: dict[str, Any]) -> Any:
        raise AssertionError("must not spawn when the automation is off")

    # Grant says granted, but the session_control automation is not opted in, so
    # spawn falls back to the Phase 5 draft.
    service = _build(
        caller, spawn_grant="granted", automation_on=False,
        spawn_op=spawn_op, draft_spawn=draft,
    )
    result = await service.spawn(caller, prompt="x")
    assert result["status"] == "drafted"


@pytest.mark.asyncio
async def test_spawn_budget_exhausts() -> None:
    caller = _caller()

    async def spawn_op(body: dict[str, Any]) -> Any:
        return live_session("s")

    service = _build(
        caller, spawn_grant="granted", spawn_op=spawn_op,
        config=_config(agent_spawn_hourly_budget=1),
    )
    await service.spawn(caller, prompt="one")
    with pytest.raises(QueueError) as excinfo:
        await service.spawn(caller, prompt="two")
    assert excinfo.value.code == "origin_budget_exhausted"


@pytest.mark.asyncio
async def test_spawn_refused_when_install_disabled() -> None:
    caller = _caller()
    service = _build(
        caller, spawn_grant="granted", config=_config(request_spawn_enabled=False)
    )
    with pytest.raises(QueueError) as excinfo:
        await service.spawn(caller, prompt="x")
    assert excinfo.value.code == "request_spawn_disabled"


@pytest.mark.asyncio
async def test_a_spawned_model_reaches_the_spawn_body_and_the_result() -> None:
    """An agent may choose the model, and is told which one it asked for.

    The failure this replaces is silent: `model` arrived on the MCP call, was
    dropped between the tool and the spawn body, and an ordinary session started
    with nothing in the answer to say the request had been ignored.
    """
    caller = _caller()
    bodies: list[dict[str, Any]] = []

    async def spawn_op(body: dict[str, Any]) -> Any:
        bodies.append(body)
        return live_session("new-1", project_id="p1", backend="claude")

    service = _build(caller, spawn_grant="granted", spawn_op=spawn_op)
    result = await service.spawn(
        caller, prompt="do the thing", backend="claude", model="Opus 5"
    )
    # Canonicalized on the way through, so the body carries the CLI's spelling
    # rather than the spoken one.
    assert bodies[0]["model"] == "claude-opus-5"
    assert result["model_requested"] == "claude-opus-5"
    assert "model_status" in result["note"]


@pytest.mark.asyncio
async def test_a_model_the_harness_cannot_take_is_refused_before_the_budget() -> None:
    """A typo must not spend an hour's spawn allowance, and must not spawn.

    Ordering matters here rather than being incidental: the budget exists to cap
    real spawns, and charging a request that never reached a process would let
    one bad name lock a session out of the feature for an hour.
    """
    caller = _caller()

    async def spawn_op(body: dict[str, Any]) -> Any:
        raise AssertionError("an unusable model must not reach a spawn")

    service = _build(
        caller, spawn_grant="granted", spawn_op=spawn_op,
        config=_config(agent_spawn_hourly_budget=1),
    )
    with pytest.raises(QueueError) as excinfo:
        await service.spawn(caller, prompt="x", backend="codex", model="opus")
    assert excinfo.value.code == "invalid_model"
    assert "does not recognize" in str(excinfo.value)
    # The allowance is intact: a real spawn still goes through afterwards.
    assert service._enforce_spawn_budget(caller) is None


@pytest.mark.asyncio
async def test_a_pane_that_dies_at_startup_is_reported_rather_than_returned() -> None:
    """The caller has no eyes on the pane, so "spawned" has to mean "still up".

    Four of the five CLIs refuse an unusable launch flag by exiting a second in,
    *after* the spawn call has already returned a session. Handing that back as a
    success is what leaves an agent watching a dead session id.
    """
    caller = _caller()
    events = EventsStub()
    discarded: list[str] = []

    async def spawn_op(body: dict[str, Any]) -> Any:
        return live_session("doomed", project_id="p1", backend="claude")

    async def settle_op(session: Any) -> str | None:
        discarded.append(str(session.record.id))
        return "exited (exit code 1): unrecognized_model"

    service = _build(
        caller, spawn_grant="granted", spawn_op=spawn_op, settle_op=settle_op,
        events=events,
    )
    with pytest.raises(QueueError) as excinfo:
        await service.spawn(caller, prompt="x", backend="claude", model="claude-nope")
    assert excinfo.value.code == "spawn_failed"
    # The harness's own words, so a caller can act on a refusal mux has never seen.
    assert "unrecognized_model" in str(excinfo.value)
    assert discarded == ["doomed"]
    assert dict(events.emitted[-1][1])["outcome"] == "spawn_failed"


@pytest.mark.asyncio
async def test_a_failed_spawn_still_costs_its_attempt() -> None:
    """The budget counts attempts, because a failing spawn spends the same host.

    A request that fails identically every time is exactly what an hourly ceiling
    is for; refunding it would make a broken launch flag an unbounded retry loop.
    """
    caller = _caller()

    async def spawn_op(body: dict[str, Any]) -> Any:
        return live_session("doomed", project_id="p1", backend="claude")

    async def settle_op(_session: Any) -> str | None:
        return "exited (exit code 1)"

    service = _build(
        caller, spawn_grant="granted", spawn_op=spawn_op, settle_op=settle_op,
        config=_config(agent_spawn_hourly_budget=1),
    )
    with pytest.raises(QueueError) as first:
        await service.spawn(caller, prompt="x")
    assert first.value.code == "spawn_failed"
    with pytest.raises(QueueError) as second:
        await service.spawn(caller, prompt="x")
    assert second.value.code == "origin_budget_exhausted"


@pytest.mark.asyncio
async def test_a_probe_that_cannot_run_does_not_fail_a_working_spawn() -> None:
    """This proves a pane is dead; it can never prove one is alive.

    A settle probe that raises has learned nothing, and turning "no evidence" into
    a refusal would make a diagnostic able to break the thing it watches.
    """
    caller = _caller()

    async def spawn_op(body: dict[str, Any]) -> Any:
        return live_session("new-1", project_id="p1", backend="claude")

    async def settle_op(_session: Any) -> str | None:
        raise RuntimeError("scrollback unreadable")

    service = _build(
        caller, spawn_grant="granted", spawn_op=spawn_op, settle_op=settle_op
    )
    result = await service.spawn(caller, prompt="x")
    assert result["status"] == "spawned"


@pytest.mark.asyncio
async def test_a_drafted_spawn_carries_its_model_to_the_approval() -> None:
    """A model the approval drops is worse than one it never took.

    The human approves a card that says "on opus"; if the field stops there, an
    ordinary session starts and nobody is in a position to notice.
    """
    caller = _caller()
    drafts: list[dict[str, Any]] = []

    async def draft(_caller: Any, **kwargs: Any) -> dict[str, Any]:
        drafts.append(kwargs)
        return {"status": "drafted", "request_id": "r1"}

    service = _build(caller, spawn_grant="draft", draft_spawn=draft)
    await service.spawn(caller, prompt="later", backend="claude", model="opus")
    assert drafts[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_spawn_rejects_empty_prompt_and_fleet() -> None:
    caller = _caller()

    async def spawn_op(body: dict[str, Any]) -> Any:
        return live_session("s")

    service = _build(caller, spawn_grant="granted", spawn_op=spawn_op)
    with pytest.raises(QueueError) as empty:
        await service.spawn(caller, prompt="   ")
    assert empty.value.code == "invalid_body"
    with pytest.raises(QueueError) as fleet:
        await service.spawn(caller, prompt="x", project="fleet")
    assert fleet.value.code == "invalid_project"


# --------------------------------------------------- durable end reasons


def test_end_reason_is_forwarded_and_distinguishable() -> None:
    """agent_ended, killed, and completed stay distinct in the durable record.

    `_mark_ended` prefers a session's `requested_end_reason` and passes it to
    `terminal_exit_outcome`, which forwards it verbatim as the `final_reason` the
    history row and the exit event carry - except a clean one-shot completion,
    which is always `completed`. A post-mortem reads these apart.
    """
    from swe_mux.session import terminal_exit_outcome

    # An agent-initiated end of an interactive session.
    state, reason, _ = terminal_exit_outcome(
        "interactive", stopping=True, exit_code=0, reason="agent_ended"
    )
    assert (state, reason) == ("exited", "agent_ended")
    # An operator hard stop stays `killed`.
    _, killed_reason, _ = terminal_exit_outcome(
        "interactive", stopping=True, exit_code=0, reason="killed"
    )
    assert killed_reason == "killed"
    # A clean one-shot completion is `completed` regardless of the passed reason.
    _, completed_reason, _ = terminal_exit_outcome(
        "one_shot", stopping=False, exit_code=0, reason="agent_ended"
    )
    assert completed_reason == "completed"


# ------------------------------------------------- per-harness coverage guard


def test_every_agent_harness_declares_a_graceful_exit_for_control() -> None:
    """The graceful end sends the harness's own exit sequence (CP §7.6).

    That sequence is per-harness and adapter-owned; `_end_session_gracefully`
    reads it off the PTY, where it was set from `adapter.graceful_exit_keys()`.
    A new agent harness added to the registry with no exit keystrokes would make
    the graceful end degrade straight to a hard stop with nothing to send, so the
    interrupt/end path is covered per harness the way the adapter matrix is.
    """
    agent_names = [name for name in HARNESSES if is_agent_harness(name)]
    assert agent_names, "no agent harnesses registered"
    for name in agent_names:
        harness = HARNESSES[name]
        adapter = build_agent_adapter(
            harness,
            executable=f"C:/fake/{harness.script_base_name}.cmd",
            args=list(harness.default_args),
            data_dir=Path("D:/tmp") / name,
            mcp_url="http://127.0.0.1:8765/mcp",
        )
        assert adapter.graceful_exit_keys(), (
            f"agent harness {name} declares no graceful exit keystrokes; the "
            "graceful end has nothing to send and would hard-stop it"
        )
