"""Phase 7.7: adaptive titling, phase-transition signals, and scan-timeline consumers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.automation_store import AutomationStore
from swe_mux.behavioral_consumers import (
    ADAPTIVE_TITLE_CHECKPOINT_PREFIX,
    PIVOT_BASELINE_CHECKPOINT_PREFIX,
    BehavioralConsumerService,
    evaluate_pivot,
)
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.openrouter import OpenRouterError
from swe_mux.scan_consumers import (
    catch_me_up,
    handoff_progress,
    live_blocker,
    phase_segments,
    search_scan_records,
)


def rec(
    *,
    phase: str = "implementation",
    novelty: float = 0.5,
    t0: float = 0.0,
    t1: float = 1.0,
    user_ask: str = "",
    targets: list[str] | None = None,
    summary: str = "did work",
    intent: str = "do work",
    claim: str = "",
    blocked_on: str = "none",
    confidence: float = 0.8,
    identity: str = "r",
) -> dict[str, Any]:
    return {
        "id": identity,
        "agent_run_id": "run-1",
        "session_id": "s1",
        "project_id": "proj",
        "work_phase": phase,
        "novelty": novelty,
        "t0": t0,
        "t1": t1,
        "user_ask": user_ask,
        "target": targets or [],
        "summary": summary,
        "intent": intent,
        "claim": claim,
        "blocked_on": blocked_on,
        "confidence": confidence,
    }


# ---- pure pivot detector -------------------------------------------------


def test_stable_subject_never_pivots() -> None:
    baseline = {"phase": "implementation", "targets": [], "user_ask": "build auth"}
    prior = [rec(phase="implementation", novelty=0.1) for _ in range(3)]
    decision = evaluate_pivot(
        rec(phase="implementation", novelty=0.1, user_ask="build auth"), prior, baseline
    )
    assert decision.is_pivot is False


def test_genuine_pivot_fires_on_phase_and_novelty() -> None:
    baseline = {"phase": "investigation", "targets": ["a.py"], "user_ask": "read code"}
    prior = [rec(phase="investigation", novelty=0.2) for _ in range(2)]
    decision = evaluate_pivot(
        rec(phase="implementation", novelty=0.9, user_ask="add feature", targets=["b.py"]),
        prior,
        baseline,
    )
    assert decision.is_pivot is True
    assert "work_phase investigation->implementation" in decision.reasons


def test_novelty_spike_without_structural_change_is_not_a_pivot() -> None:
    baseline = {"phase": "implementation", "targets": ["a.py"], "user_ask": "build auth"}
    prior = [rec(phase="implementation", novelty=0.3) for _ in range(2)]
    decision = evaluate_pivot(
        rec(phase="implementation", novelty=0.95, user_ask="build auth", targets=["a.py"]),
        prior,
        baseline,
    )
    assert decision.is_pivot is False


def test_first_records_establish_baseline_without_pivoting() -> None:
    decision = evaluate_pivot(rec(phase="implementation", novelty=0.9), [], None)
    assert decision.is_pivot is False
    assert decision.baseline["phase"] == "implementation"


def test_flat_novelty_stall_is_detected() -> None:
    baseline = {"phase": "debug", "targets": [], "user_ask": ""}
    prior = [
        rec(phase="debug", novelty=0.05, t0=0.0, t1=600.0),
        rec(phase="debug", novelty=0.05, t0=600.0, t1=1200.0),
    ]
    latest = rec(phase="debug", novelty=0.05, t0=1200.0, t1=2000.0)
    decision = evaluate_pivot(latest, prior, baseline)
    assert decision.is_stall is True


def test_short_quiet_patch_is_not_a_stall() -> None:
    baseline = {"phase": "debug", "targets": [], "user_ask": ""}
    prior = [rec(phase="debug", novelty=0.05, t0=0.0, t1=60.0)]
    latest = rec(phase="debug", novelty=0.05, t0=60.0, t1=120.0)
    decision = evaluate_pivot(latest, prior, baseline)
    assert decision.is_stall is False


# ---- pure derivations ----------------------------------------------------


def test_phase_segments_collapse_consecutive_phases() -> None:
    records = [
        rec(phase="investigation", summary="read"),
        rec(phase="investigation", summary="read more"),
        rec(phase="implementation", summary="wrote code"),
    ]
    segments = phase_segments(records)
    assert [segment["work_phase"] for segment in segments] == ["investigation", "implementation"]
    assert "read" in segments[0]["summaries"]


def test_handoff_progress_is_phase_structured() -> None:
    records = [
        rec(phase="investigation", summary="mapped the module"),
        rec(phase="debug", summary="chased a null", blocked_on="tool_error"),
    ]
    lines = handoff_progress(records)
    assert lines[0].startswith("**investigation**")
    assert "blocked on tool_error" in lines[1]


def test_catch_me_up_attributes_the_run_and_reports_blocker() -> None:
    records = [
        rec(phase="implementation", summary="built it", claim="feature done"),
        rec(phase="debug", summary="stuck", blocked_on="missing_context"),
    ]
    digest = catch_me_up(records, "run-1")
    assert digest["agent_run_id"] == "run-1"
    assert digest["phases"] == ["implementation", "debug"]
    assert "feature done" in digest["claims"]
    assert digest["current_blocker"]["blocked_on"] == "missing_context"


def test_live_blocker_none_when_latest_is_unblocked() -> None:
    records = [
        rec(blocked_on="tool_error", t0=0.0, t1=1.0),
        rec(blocked_on="none", t0=1.0, t1=2.0),
    ]
    assert live_blocker(records, "run-1") is None


def test_live_blocker_dates_the_streak_start() -> None:
    records = [
        rec(blocked_on="none", t0=0.0, t1=1.0),
        rec(blocked_on="user_input", t0=1.0, t1=2.0),
        rec(blocked_on="user_input", t0=2.0, t1=3.0),
    ]
    blocker = live_blocker(records, "run-1")
    assert blocker is not None
    assert blocker["blocked_on"] == "user_input"
    assert blocker["since"] == 1.0


def test_semantic_search_matches_distilled_fields_and_targets() -> None:
    records = [
        rec(summary="fixed the CRLF line-ending bug", identity="a"),
        rec(summary="unrelated work", intent="something else", identity="b"),
        rec(summary="touched files", targets=["src/crlf.py"], identity="c"),
    ]
    results = search_scan_records(records, "crlf")
    ids = {item["record_id"] for item in results}
    assert ids == {"a", "c"}
    assert all(item["agent_run_id"] == "run-1" for item in results)


def test_semantic_search_requires_all_terms() -> None:
    records = [
        rec(summary="fixed the parser", identity="a"),
        rec(summary="fixed the CRLF parser bug", identity="b"),
    ]
    results = search_scan_records(records, "crlf parser")
    assert {item["record_id"] for item in results} == {"b"}


# ---- the service (adaptive title + phase signals) ------------------------


class _FakeProvider:
    def __init__(self, title: str) -> None:
        self.title = title
        self.calls = 0

    async def complete_json(self, **_kwargs: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(
            value={"title": self.title},
            resolved_model="vendor/cheap",
            generation_id="g",
            input_tokens=5,
            output_tokens=3,
            cost_usd=0.0,
            latency_ms=1,
            provider_name="p",
            finish_reason="stop",
        )


def _context(**overrides: Any) -> SimpleNamespace:
    base = {
        "project_id": "proj",
        "project_root": "/root",
        "agent_run_id": "run-1",
        "continuous_title_enabled": True,
        "phase_transitions_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _session(auto_named: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        record=SimpleNamespace(
            id="s1", agent_run_id="run-1", project_id="proj", auto_named=auto_named
        )
    )


async def _feed_run(
    service: BehavioralConsumerService, context: SimpleNamespace, session: SimpleNamespace
) -> None:
    a = rec(phase="investigation", novelty=0.2, t0=0.0, t1=1.0, identity="a")
    b = rec(phase="investigation", novelty=0.2, t0=1.0, t1=2.0, identity="b")
    c = rec(
        phase="implementation",
        novelty=0.9,
        t0=2.0,
        t1=3.0,
        user_ask="add the feature",
        identity="c",
    )
    await service.on_scan_record(session=session, context=context, record=a, prior_records=[])
    await service.on_scan_record(session=session, context=context, record=b, prior_records=[a])
    await service.on_scan_record(session=session, context=context, record=c, prior_records=[a, b])


@pytest.mark.asyncio
async def test_adaptive_titler_retitles_on_a_genuine_pivot(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FakeProvider("Feature build")
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    await _feed_run(service, _context(), _session())

    title = await store.recent_annotation("run-1", "title", 0)
    assert title is not None
    assert title["content"] == "Feature build"
    state = await store.checkpoint(f"{ADAPTIVE_TITLE_CHECKPOINT_PREFIX}run-1")
    assert int(state["retitle_count"]) == 1
    # A phase-transition annotation was written on the same pivot.
    pivots = await store.annotations(agent_run_id="run-1", tag="phase-pivot")
    assert len(pivots) == 1
    store.close()


@pytest.mark.asyncio
async def test_adaptive_titler_can_retitle_again_after_hysteresis(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FakeProvider("Feature build")
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    context = _context()
    session = _session()
    key = f"{ADAPTIVE_TITLE_CHECKPOINT_PREFIX}run-1"
    a = rec(phase="investigation", novelty=0.2, t0=0.0, t1=1.0, identity="a")
    b = rec(phase="investigation", novelty=0.2, t0=1.0, t1=2.0, identity="b")
    c = rec(
        phase="implementation", novelty=0.9, t0=2.0, t1=3.0, user_ask="add feature", identity="c"
    )
    await service.on_scan_record(session=session, context=context, record=a, prior_records=[])
    await service.on_scan_record(session=session, context=context, record=b, prior_records=[a])
    await service.on_scan_record(session=session, context=context, record=c, prior_records=[a, b])
    assert int((await store.checkpoint(key))["retitle_count"]) == 1

    # Clear the time cooldown so only the record-count hysteresis remains, then
    # feed enough records (a non-pivot, then a genuine pivot) for it to release.
    state = await store.checkpoint(key)
    state["last_retitle_ts"] = 0.0
    await store.set_checkpoint(key, state)
    provider.title = "Feature build + tests"
    e = rec(
        phase="implementation", novelty=0.1, t0=3.0, t1=4.0, user_ask="add feature", identity="e"
    )
    d = rec(phase="test", novelty=0.9, t0=4.0, t1=5.0, user_ask="add tests", identity="d")
    await service.on_scan_record(
        session=session, context=context, record=e, prior_records=[a, b, c]
    )
    await service.on_scan_record(
        session=session, context=context, record=d, prior_records=[a, b, c, e]
    )

    title = await store.recent_annotation("run-1", "title", 0)
    assert title is not None and title["content"] == "Feature build + tests"
    assert int((await store.checkpoint(key))["retitle_count"]) == 2
    store.close()


@pytest.mark.asyncio
async def test_stable_subject_run_measures_zero_retitles(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FakeProvider("Should not be used")
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    context = _context()
    session = _session()
    # A run whose subject never changes: same phase, low novelty throughout.
    prior: list[dict[str, Any]] = []
    for index in range(6):
        record = rec(
            phase="implementation",
            novelty=0.1,
            t0=float(index),
            t1=float(index + 1),
            user_ask="build auth",
            identity=f"r{index}",
        )
        await service.on_scan_record(
            session=session, context=context, record=record, prior_records=list(prior)
        )
        prior.append(record)

    assert provider.calls == 0
    assert await store.recent_annotation("run-1", "title", 0) is None
    state = await store.checkpoint(f"{ADAPTIVE_TITLE_CHECKPOINT_PREFIX}run-1")
    assert int((state or {}).get("retitle_count") or 0) == 0
    store.close()


class _FailingProvider:
    """A provider whose call always fails, the way a rejected request does."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    async def complete_json(self, **_kwargs: Any) -> Any:
        self.calls += 1
        raise self.error


@pytest.mark.asyncio
async def test_a_rejected_synthesis_records_what_the_failure_was(tmp_path: Path) -> None:
    """A failed call must be diagnosable from its ledger row alone.

    `builtin:adaptive-title` failed 100% of its live calls on a schema the
    provider rejected, and the row said only "request failed with HTTP 400": no
    status, no retryability, nothing to act on. A rejected call bills nothing, so
    the ledger row is the *only* trace it leaves - the spend table cannot show a
    call that was never charged.
    """
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FailingProvider(
        OpenRouterError(
            "OpenRouter request failed with HTTP 400: invalid_json_schema",
            status=400,
            retryable=False,
            resolved_model="vendor/cheap",
            provider_name="Azure",
        )
    )
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    await _feed_run(service, _context(), _session())

    assert provider.calls == 1
    # The pivot fired, so the title simply stays put rather than being rewritten.
    assert await store.recent_annotation("run-1", "title", 0) is None
    calls = await store.observer_calls()
    assert len(calls) == 1
    row = calls[0]
    assert row["status"] == "failed"
    assert row["http_status"] == 400
    assert not row["retryable"]
    assert row["provider_name"] == "Azure"
    assert "invalid_json_schema" in row["error"]
    store.close()


@pytest.mark.asyncio
async def test_an_unexpected_synthesis_fault_leaves_no_running_row(tmp_path: Path) -> None:
    """Anything the provider layer did not wrap must still close its row.

    A call left `running` is worse than one marked failed: it reads as in flight
    forever, and the automation looks idle rather than broken.
    """
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FailingProvider(ValueError("unserializable prompt"))
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    await _feed_run(service, _context(), _session())

    assert provider.calls == 1
    calls = await store.observer_calls()
    assert [row["status"] for row in calls] == ["failed"]
    assert "ValueError" in calls[0]["error"]
    assert await store.recent_annotation("run-1", "title", 0) is None
    store.close()


@pytest.mark.asyncio
async def test_adaptive_titler_never_overwrites_a_human_named_session(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FakeProvider("Auto name")
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    await _feed_run(service, _context(), _session(auto_named=False))

    assert provider.calls == 0
    assert await store.recent_annotation("run-1", "title", 0) is None
    store.close()


@pytest.mark.asyncio
async def test_phase_signals_only_when_the_consumer_is_enabled(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    provider = _FakeProvider("x")
    config = Config(data_dir=tmp_path, openrouter_cheap_model="vendor/cheap")
    service = BehavioralConsumerService(
        store=store,
        sessions=SimpleNamespace(sessions={}),
        config=config,
        provider=provider,
        events=EventBus(),
    )
    # Titling on, phase transitions off.
    await _feed_run(
        service,
        _context(phase_transitions_enabled=False),
        _session(),
    )
    assert await store.annotations(agent_run_id="run-1", tag="phase-pivot") == []
    # But baseline was still tracked (it drives titling too).
    assert await store.checkpoint(f"{PIVOT_BASELINE_CHECKPOINT_PREFIX}run-1") is not None
    store.close()


# ---- endpoints -----------------------------------------------------------


def _seed_record(store: AutomationStore, **kwargs: Any) -> Any:
    payload = rec(**kwargs)
    return store.save_scan_record(
        session_id=payload["session_id"],
        agent_run_id=payload["agent_run_id"],
        project_id=payload["project_id"],
        t0=payload["t0"],
        t1=payload["t1"],
        trigger="turn_ended",
        record=payload,
        input_hash=f"h-{payload['id']}",
        requested_model="m",
        resolved_model="m",
        generation_id=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
    )


def _app(store: AutomationStore, sessions: Any, enabled: set[str]) -> Any:
    from aiohttp import web

    from swe_mux import server

    async def gate(_root: str) -> frozenset[str]:
        return frozenset(enabled)

    app = web.Application()
    app["automation_store"] = store
    app["sessions"] = sessions
    app["automation_gate"] = gate
    app["projects"] = SimpleNamespace(projects={"proj": SimpleNamespace(root="/root")})
    app.router.add_get("/catch/{sid}", server.session_catch_me_up)
    app.router.add_get("/blockers", server.fleet_live_blockers)
    app.router.add_get("/search", server.scan_timeline_search)
    return app


def _live_session(sid: str, run_id: str, **rec_over: Any) -> SimpleNamespace:
    record = SimpleNamespace(
        id=sid,
        agent_run_id=run_id,
        project_id="proj",
        project_root="/root",
        spawn_project_root="",
        state="running",
        name=sid,
        cwd="/root",
    )
    return SimpleNamespace(record=record)


@pytest.mark.asyncio
async def test_catch_me_up_endpoint_gates_and_attributes(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    store = AutomationStore(tmp_path / "mux.db")
    await _seed_record(store, phase="implementation", summary="built it", identity="a")
    await _seed_record(store, phase="debug", blocked_on="tool_error", identity="b", t0=2, t1=3)
    session = _live_session("s1", "run-1")
    sessions = SimpleNamespace(sessions={"s1": session}, resolve=lambda _sid: session)

    async with TestClient(TestServer(_app(store, sessions, {"catch_me_up"}))) as client:
        enabled = await (await client.get("/catch/s1")).json()
    assert enabled["enabled"] is True
    assert enabled["digest"]["agent_run_id"] == "run-1"

    async with TestClient(TestServer(_app(store, sessions, set()))) as client:
        disabled = await (await client.get("/catch/s1")).json()
    assert disabled["enabled"] is False
    assert disabled["digest"] is None
    store.close()


@pytest.mark.asyncio
async def test_live_blockers_endpoint_reports_only_blocked_sessions(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    store = AutomationStore(tmp_path / "mux.db")
    # run-1 ends blocked; run-2 ends clear.
    await _seed_record(store, identity="a", blocked_on="user_input", t0=0, t1=1)
    blocked = _live_session("s1", "run-1")
    clear_session = _live_session("s2", "run-2")
    payload = rec(identity="c", blocked_on="none", t0=0, t1=1)
    payload["agent_run_id"] = "run-2"
    payload["session_id"] = "s2"
    await store.save_scan_record(
        session_id="s2",
        agent_run_id="run-2",
        project_id="proj",
        t0=0.0,
        t1=1.0,
        trigger="turn_ended",
        record=payload,
        input_hash="h-c",
        requested_model="m",
        resolved_model="m",
        generation_id=None,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
    )
    sessions = SimpleNamespace(
        sessions={"s1": blocked, "s2": clear_session},
        resolve=lambda sid: {"s1": blocked, "s2": clear_session}[sid],
    )

    async with TestClient(TestServer(_app(store, sessions, {"live_blockers"}))) as client:
        payload = await (await client.get("/blockers")).json()
    assert [item["session_id"] for item in payload["blockers"]] == ["s1"]
    assert payload["blockers"][0]["blocked_on"] == "user_input"
    store.close()


@pytest.mark.asyncio
async def test_scan_search_endpoint_gates_and_scopes(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    store = AutomationStore(tmp_path / "mux.db")
    await _seed_record(store, summary="fixed the CRLF line-ending bug", identity="a")
    await _seed_record(store, summary="unrelated parser work", identity="b", t0=2, t1=3)
    sessions = SimpleNamespace(sessions={}, resolve=lambda _sid: None)

    async with TestClient(
        TestServer(_app(store, sessions, {"semantic_history_search"}))
    ) as client:
        found = await (await client.get("/search?q=crlf&project_id=proj")).json()
    assert found["enabled"] is True
    assert [item["snippet"] for item in found["results"]] == ["fixed the CRLF line-ending bug"]

    async with TestClient(TestServer(_app(store, sessions, set()))) as client:
        off = await (await client.get("/search?q=crlf&project_id=proj")).json()
    assert off["enabled"] is False
    store.close()


def test_config_drops_retired_summarizer_flag(tmp_path: Path) -> None:
    from swe_mux.config import load_config

    path = tmp_path / "config.toml"
    path.write_text(
        "schema_version = 1\nobserver_summarizer_enabled = true\n"
        "observer_titler_enabled = true\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.observer_titler_enabled is True
    assert not hasattr(cfg, "observer_summarizer_enabled")
