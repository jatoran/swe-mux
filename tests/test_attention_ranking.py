"""Phase 6.5: attention ranking, the interrupt budget, and model narration.

What these pin is the policy, not the wording. Four properties carry the phase and
each has a test that fails loudly if it is weakened:

- cheap-blocking work never spends interrupt budget, at any confidence;
- the daily budget is a hard bound, and a budgeted-out finding stays readable;
- a finding from a conversation the session has rolled past never ranks;
- narration is presentation over evidence, so its failure costs the aside and
  never the finding.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux.attention_narration import AttentionNarrator, build_slice
from swe_mux.attention_ranking import (
    CHEAP_BLOCKING,
    DIGEST,
    EXPENSIVE_BLOCKING,
    INBOX,
    INTERRUPT_NOW,
    NEXT_BREAKPOINT,
    NON_BLOCKING,
    AttentionRankingService,
    AttentionTelemetry,
    Finding,
    KindPolicy,
    incident_key,
    mine_rules,
    policy_for,
    route,
    score_for,
)
from swe_mux.automation_store import AutomationStore
from swe_mux.budget import Budget
from swe_mux.deterministic_consumers import ConsumerContext

WORSENING = KindPolicy("stuck", EXPENSIVE_BLOCKING, True, 0.9, "Redirect the run.")
CHEAP = KindPolicy("blocked_on_human", CHEAP_BLOCKING, False, 0.5, "Answer the prompt.")
QUIET = KindPolicy("docs", NON_BLOCKING, False, 0.2, None)


def routed(policy: KindPolicy, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "confidence": 0.95,
        "superseded_run": False,
        "budget_available": True,
        "demoted_by_rule": None,
    }
    arguments.update(overrides)
    return route(policy, **arguments)


# ------------------------------------------------------------------ routing


def test_a_worsening_confident_finding_earns_an_interrupt() -> None:
    decision = routed(WORSENING)
    assert decision.channel == INTERRUPT_NOW
    assert decision.spends_budget is True
    assert decision.suppressed_reason is None


def test_cheap_blocking_work_never_spends_interrupt_budget() -> None:
    # Answering a permission prompt costs seconds. Merging it with "the plan is
    # wrong" is the clinical-alarm failure mode, so it batches to the breakpoint
    # however confident and however many are waiting.
    decision = routed(CHEAP, confidence=1.0)
    assert decision.channel == NEXT_BREAKPOINT
    assert decision.spends_budget is False


def test_an_exhausted_budget_demotes_rather_than_drops() -> None:
    decision = routed(WORSENING, budget_available=False)
    assert decision.channel == INBOX
    assert decision.suppressed_reason == "budget_exhausted"
    assert decision.spends_budget is False


def test_low_confidence_never_interrupts() -> None:
    decision = routed(WORSENING, confidence=0.5)
    assert decision.channel == INBOX
    assert decision.suppressed_reason == "low_confidence"


def test_a_finding_from_a_replaced_conversation_leaves_ranking() -> None:
    # The agent cannot act on a conversation it has rolled past, and the user
    # already resolved it by clearing. It stays inspectable in the digest.
    decision = routed(WORSENING, superseded_run=True)
    assert decision.channel == DIGEST
    assert decision.suppressed_reason == "superseded_run"


def test_non_blocking_findings_are_a_record_not_an_interruption() -> None:
    assert routed(QUIET).channel == DIGEST


def test_an_accepted_rule_demotes_and_says_so() -> None:
    decision = routed(WORSENING, demoted_by_rule="stuck")
    assert decision.channel == INBOX
    assert decision.suppressed_reason == "rule:stuck"


def test_an_unknown_kind_cannot_reach_the_interrupt_channel() -> None:
    # A detector added later must not be able to interrupt by default.
    policy = policy_for("something-new")
    assert policy.cost_to_resolve == NON_BLOCKING
    assert routed(policy).channel == DIGEST


# ---------------------------------------------------------------- incidents


def test_findings_inside_one_window_share_an_incident_and_a_slot() -> None:
    first = incident_key(incident_class="stuck", anchor="run-1", now=1000.0, window_seconds=3600)
    second = incident_key(incident_class="stuck", anchor="run-1", now=1500.0, window_seconds=3600)
    assert first == second


def test_a_recurrence_in_a_later_window_is_a_new_incident() -> None:
    first = incident_key(incident_class="stuck", anchor="run-1", now=1000.0, window_seconds=3600)
    later = incident_key(incident_class="stuck", anchor="run-1", now=9000.0, window_seconds=3600)
    assert first != later


def test_corroboration_raises_the_score_without_changing_severity() -> None:
    single = score_for(WORSENING, confidence=1.0, contributions=1)
    corroborated = score_for(WORSENING, confidence=1.0, contributions=3)
    assert corroborated > single
    assert corroborated <= WORSENING.base_score


# ------------------------------------------------------------ mined rules


def test_a_consistently_dismissed_class_induces_a_proposed_rule() -> None:
    stats = [
        {
            "incident_class": "unverified",
            "channel": INTERRUPT_NOW,
            "action": "dismissed",
            "count": 9,
        },
        {"incident_class": "unverified", "channel": INTERRUPT_NOW, "action": "acted", "count": 1},
    ]
    rules = mine_rules(stats)
    assert [rule.incident_class for rule in rules] == ["unverified"]
    # Proposed, never applied: a suppression the user never agreed to is
    # indistinguishable from a detector that silently broke.
    assert rules[0].state == "proposed"


def test_too_few_observations_induce_nothing() -> None:
    stats = [
        {"incident_class": "stuck", "channel": INTERRUPT_NOW, "action": "dismissed", "count": 2},
    ]
    assert mine_rules(stats) == []


# ------------------------------------------------------------- fan-out math


def test_fanout_reports_itself_unavailable_rather_than_guessing() -> None:
    telemetry = AttentionTelemetry()
    telemetry.observe_interaction("s1", 0.0)
    telemetry.observe_interaction("s1", 5.0)
    estimate = telemetry.fanout(attended_now=3)
    assert estimate["status"] == "insufficient_samples"
    assert estimate["sustainable_agents"] is None


def test_fanout_divides_measured_neglect_by_measured_interaction() -> None:
    telemetry = AttentionTelemetry()
    now = 0.0
    for _ in range(6):
        telemetry.observe_interaction("s1", now)
        telemetry.observe_interaction("s1", now + 10.0)
        now += 610.0
    estimate = telemetry.fanout(attended_now=2)
    assert estimate["status"] == "ok"
    assert estimate["interaction_seconds"] == 10.0
    assert estimate["sustainable_agents"] is not None
    assert estimate["sustainable_agents"] > 1


def test_resumption_lag_measures_the_return_to_interrupted_work() -> None:
    telemetry = AttentionTelemetry()
    telemetry.observe_interaction("s1", 100.0)
    telemetry.note_interruption("s2", 110.0)
    telemetry.observe_interaction("s2", 120.0)
    telemetry.observe_interaction("s1", 200.0)
    resumption = telemetry.resumption()
    assert resumption["samples"] == 1
    assert resumption["mean_seconds"] == 100.0


# ------------------------------------------------------------ service wiring


class Events:
    def __init__(self) -> None:
        self.emitted: list[dict[str, Any]] = []

    async def emit(self, kind: str, **payload: Any) -> None:
        self.emitted.append({"type": kind, **payload})


def session(session_id: str = "s1", run_id: str = "run-1") -> Any:
    return SimpleNamespace(
        record=SimpleNamespace(
            id=session_id,
            agent_run_id=run_id,
            project_id="p1",
            name="alpha",
            state="working",
        )
    )


def fleet(count: int) -> Any:
    """A live fleet whose runs are current, so nothing is superseded by accident."""
    return SimpleNamespace(
        sessions={
            f"s{index}": session(f"s{index}", f"run-{index}") for index in range(count)
        }
    )


def config(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "attention_daily_interrupt_budget": 4,
        "attention_hourly_interrupt_cap": 4,
        "attention_incident_window_seconds": 3600.0,
        "attention_narration_enabled": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def service(
    store: AutomationStore,
    *,
    enabled: frozenset[str] = frozenset({"attention_ranking"}),
    sessions: Any | None = None,
    narrator: Any | None = None,
    **config_overrides: Any,
) -> tuple[AttentionRankingService, Events]:
    events = Events()

    async def context(_session_id: str) -> ConsumerContext:
        return ConsumerContext(
            project_id="p1", project_root="/tmp/p1", agent_run_id="run-1", enabled=enabled
        )

    ranking = AttentionRankingService(
        store,
        sessions or SimpleNamespace(sessions={"s1": session()}),
        events,
        config(**config_overrides),
        resolve_context=context,
        narrator=narrator,
    )
    return ranking, events


def finding(
    kind: str = "loop-detected", *, run_id: str = "run-1", confidence: float = 0.95
) -> Finding:
    return Finding(
        kind=kind,
        session_id="s1",
        agent_run_id=run_id,
        project_id="p1",
        summary=f"{kind} on alpha",
        confidence=confidence,
        evidence=[{"source": "annotation", "tag": kind}],
        source="annotation",
    )


@pytest.mark.asyncio
async def test_a_project_that_did_not_opt_in_ranks_nothing(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, _ = service(store, enabled=frozenset())
        assert await ranking.ingest(finding()) is None
        assert await store.attention_items() == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_several_detectors_on_one_event_spend_one_slot(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, events = service(store)
        first = await ranking.ingest(finding("loop-detected"))
        second = await ranking.ingest(finding("stalled"))
        assert first is not None and second is not None
        assert first["incident_key"] == second["incident_key"]
        assert second["contributions"] == 2
        assert sorted(second["kinds"]) == ["loop-detected", "stalled"]
        budget = await ranking.budget()
        assert budget["used"] == 1
        # One incident, one ranking event: the merge is not a second interruption.
        ranked = [item for item in events.emitted if item["type"] == "attention_item_ranked"]
        assert len(ranked) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_the_daily_budget_is_a_hard_bound_and_the_overflow_stays_readable(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, _ = service(store, sessions=fleet(4), attention_daily_interrupt_budget=2)
        channels = []
        for index in range(4):
            # Four live sessions, so each finding is its own incident and its own
            # slot request rather than a merge into one.
            item = await ranking.ingest(
                Finding(
                    kind="loop-detected",
                    session_id=f"s{index}",
                    agent_run_id=f"run-{index}",
                    project_id="p1",
                    summary="stuck",
                    confidence=0.95,
                    evidence=[],
                    source="test",
                )
            )
            assert item is not None
            channels.append(item["channel"])
        assert channels[:2] == [INTERRUPT_NOW, INTERRUPT_NOW]
        assert channels[2:] == [INBOX, INBOX]
        held = [item for item in await store.attention_items() if item["suppressed_reason"]]
        assert len(held) == 2
        assert {item["suppressed_reason"] for item in held} == {"budget_exhausted"}
        inbox = await ranking.inbox()
        assert inbox["suppressed"]["budget_exhausted"] == 2
        assert inbox["delivery"] == {"push": False, "surface": "in_app"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_an_item_is_demoted_when_its_conversation_is_replaced(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        sessions = SimpleNamespace(sessions={"s1": session()})
        ranking, _ = service(store, sessions=sessions)
        item = await ranking.ingest(finding())
        assert item is not None and item["channel"] == INTERRUPT_NOW
        # The user runs /clear: the CLI mints a new run under the same session.
        sessions.sessions["s1"].record.agent_run_id = "run-2"
        inbox = await ranking.inbox()
        demoted = inbox["channels"][DIGEST]
        assert [entry["suppressed_reason"] for entry in demoted] == ["superseded_run"]
        # Demoted, never deleted: it is still attributed to the run it came from.
        assert demoted[0]["agent_run_id"] == "run-1"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_breakpoint_drains_waiting_work_without_touching_a_session(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, events = service(store)
        item = await ranking.ingest(finding("unattended_attention"))
        assert item is not None and item["channel"] == NEXT_BREAKPOINT
        drained = await ranking.breakpoint_reached("shell-1")
        assert [entry["channel"] for entry in drained] == [INBOX]
        assert any(entry["type"] == "attention_breakpoint" for entry in events.emitted)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_behaviour_mines_a_rule_and_accepting_it_demotes_the_next_one(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, _ = service(
            store,
            sessions=fleet(8),
            attention_daily_interrupt_budget=50,
            attention_hourly_interrupt_cap=50,
        )
        for index in range(6):
            item = await ranking.ingest(
                Finding(
                    kind="loop-detected",
                    session_id=f"s{index}",
                    agent_run_id=f"run-{index}",
                    project_id="p1",
                    summary="stuck",
                    confidence=0.95,
                    evidence=[],
                    source="test",
                )
            )
            assert item is not None
            await ranking.feedback(item["id"], "dismissed")
        proposed = [rule for rule in await ranking.rules() if rule.state == "proposed"]
        assert [rule.incident_class for rule in proposed] == ["stuck"]
        await ranking.decide_rule("stuck", INTERRUPT_NOW, True)
        later = await ranking.ingest(
            Finding(
                kind="loop-detected",
                session_id="s7",
                agent_run_id="run-7",
                project_id="p1",
                summary="stuck",
                confidence=0.95,
                evidence=[],
                source="test",
            )
        )
        assert later is not None
        assert later["channel"] == INBOX
        assert later["suppressed_reason"] == "rule:stuck"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_the_digest_renders_a_rollover_as_a_boundary(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, _ = service(store)
        start = time.time() - 60
        await store.add_scan_boundary(
            session_id="s1",
            previous_run_id="run-0",
            next_run_id="run-1",
            reason="clear",
            created_at=time.time(),
        )
        digest = await ranking.digest(start)
        assert len(digest["boundaries"]) == 1
        # Smoothing this over would misrepresent what the agent currently knows.
        assert "cleared this session" in digest["boundaries"][0]["note"]
        assert digest["boundaries"][0]["previous_run_id"] == "run-0"
    finally:
        store.close()


# --------------------------------------------------------------- HTTP surface


@pytest.mark.asyncio
async def test_the_routes_expose_ranking_feedback_and_rule_decisions(tmp_path: Path) -> None:
    import json as json_module

    from swe_mux.server import attention_feedback, attention_inbox, attention_rule_decision

    store = AutomationStore(tmp_path / "mux.db")
    try:
        ranking, _ = service(store)
        item = await ranking.ingest(finding())
        assert item is not None
        app = {keys.ATTENTION_RANKING: ranking}

        response = await attention_inbox(  # type: ignore[arg-type]
            SimpleNamespace(query={}, app=app)
        )
        payload = json_module.loads(response.body)
        assert payload["channels"][INTERRUPT_NOW][0]["id"] == item["id"]
        assert payload["budget"]["used"] == 1
        assert payload["delivery"]["push"] is False

        async def body() -> dict[str, Any]:
            return {"action": "dismissed"}

        resolved = await attention_feedback(  # type: ignore[arg-type]
            SimpleNamespace(match_info={"item_id": item["id"]}, app=app, json=body)
        )
        assert json_module.loads(resolved.body)["state"] == "dismissed"

        async def rule_body() -> dict[str, Any]:
            return {"incident_class": "stuck", "channel": INTERRUPT_NOW, "accept": True}

        decided = await attention_rule_decision(  # type: ignore[arg-type]
            SimpleNamespace(app=app, json=rule_body)
        )
        rules = json_module.loads(decided.body)["rules"]
        assert any(rule["state"] == "accepted" for rule in rules)
    finally:
        store.close()


# ----------------------------------------------------- breakpoint detection


def test_osc133_parser_survives_fragmentation_and_keeps_the_exit_status() -> None:
    from swe_mux.runtime_cwd import Osc133Parser

    parser = Osc133Parser()
    assert parser.feed(b"output\x1b") == []
    assert parser.feed(b"]133;D;0\x07") == [("D", "0")]
    assert parser.last_exit_status == "0"
    assert parser.feed(b"\x1b]133;A\x07prompt> ") == [("A", None)]


def test_only_a_shell_reports_a_human_breakpoint() -> None:
    from typing import cast

    from swe_mux.runtime_cwd import Osc133Parser
    from swe_mux.session import SessionManager

    emitted: list[dict[str, Any]] = []
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.events = SimpleNamespace(
        emit_background=lambda kind, **payload: emitted.append({"type": kind, **payload})
    )

    def pane(backend: str) -> Any:
        return SimpleNamespace(
            record=SimpleNamespace(id=f"{backend}-1", backend=backend), osc133=Osc133Parser()
        )

    # An agent's "finished" is the agent's breakpoint, not the human's.
    manager._note_shell_breakpoints(pane("claude"), b"\x1b]133;D;0\x07")
    assert emitted == []
    manager._note_shell_breakpoints(pane("shell"), b"\x1b]133;D;0\x07")
    assert [item["type"] for item in emitted] == ["shell_command_finished"]
    assert emitted[0]["exit_status"] == "0"


def test_a_shell_profile_reports_the_human_breakpoint(tmp_path: Path) -> None:
    from swe_mux.config import Config, LaunchProfile
    from swe_mux.profiles import resolve_profile

    executable = tmp_path / "pwsh.exe"
    executable.write_bytes(b"fixture")
    profile = LaunchProfile("plain", "Plain", str(executable), ["-NoLogo"])
    config = Config(shell_profiles=[profile], default_shell_profile=profile.id)
    resolved = resolve_profile(config, profile.id, tmp_path)
    assert "breakpoint-osc133" in resolved.capabilities
    script = resolved.argv[-1]
    assert "$([char]27)]133;D;" in script
    # `$?` has to be the prompt's first statement or it reports the wrong command.
    assert script.index("$__mux_ok=if($?)") < script.index("]133;D;")
    # The user's own prompt is preserved and still called.
    assert "$global:__swe_mux_prompt" in script

    off = Config(
        shell_profiles=[profile],
        default_shell_profile=profile.id,
        attention_breakpoint_markers=False,
    )
    plain = resolve_profile(off, profile.id, tmp_path)
    assert "breakpoint-osc133" not in plain.capabilities
    assert "]133;" not in plain.argv[-1]


# ------------------------------------------------------------------ narration


def test_a_narration_slice_never_leaves_the_run_it_describes() -> None:
    payload = build_slice(
        {
            "id": "i1",
            "incident_key": "k1",
            "title": "Session is not making progress",
            "incident_class": "stuck",
            "kinds": ["loop-detected"],
            "summary": "same action three times",
            "agent_run_id": "run-1",
            "evidence": [{"fact_id": "f1"}],
        }
    )
    assert "run-1" in payload
    assert "f1" in payload
    # Nothing but the item's own, already run-scoped, evidence reaches the model.
    assert "run-2" not in payload


@pytest.mark.asyncio
async def test_narration_is_off_by_default_and_says_so(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        narrator = AttentionNarrator(store, config(), provider=None)
        text, status = await narrator.narrate({"id": "i1", "incident_key": "k1"})
        assert text is None
        assert status == "disabled"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_narration_failure_costs_the_aside_and_not_the_finding(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")

    class Provider:
        async def complete_json(self, **_kwargs: Any) -> Any:
            raise RuntimeError("provider down")

    try:
        narrator = AttentionNarrator(
            store,
            config(
                attention_narration_enabled=True,
                attention_narration_model="test/model",
                attention_narration_daily_budget=Budget(usd=1.0, mode="usd"),
                attention_narration_max_output_tokens=200,
            ),
            Provider(),
        )
        ranking, _ = service(
            store, enabled=frozenset({"attention_ranking", "model_narration"}), narrator=narrator
        )
        item = await ranking.ingest(finding())
        assert item is not None
        text, status = await narrator.narrate(item)
        assert text is None
        assert status == "failed"
        stored = await store.attention_item(item["id"])
        assert stored is not None
        # The deterministic summary is untouched; only the aside is missing.
        assert stored["summary"] == "loop-detected on alpha"
    finally:
        store.close()
