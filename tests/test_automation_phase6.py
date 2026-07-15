from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.automation import (
    AutomationEngine,
    NormalizedEvent,
    RuleValidationError,
    normalize_event,
    parse_rules,
    serialize_rules,
)
from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.openrouter import OpenRouterError, OpenRouterResult
from swe_mux.secret_store import PlatformSecretStore

RULES = """
version = 1

[[rule]]
id = "summarize-turn"
name = "Summarize turn"
on = { trigger = "turn_ended", debounce_s = 1 }
when = [{ field = "backend", op = "in", value = ["claude", "codex"] }]

[[rule.do]]
kind = "llm"
model = "cheap"
input = { slice = "last_turn" }
prompt = "Summarize without acting."
schema = "summary_v1"
on_result = { kind = "annotate", tag = "turn-summary", content = "{result.summary}" }
"""


class FakeProvider:
    async def complete_json(self, **_: Any) -> OpenRouterResult:
        return OpenRouterResult(
            "generation-1",
            "vendor/cheap",
            "vendor/cheap",
            {"summary": "Implemented the parser and verified it.", "confidence": 0.9},
            120,
            18,
            0.002,
            15,
        )

    async def generation_cost(self, _generation_id: str) -> float | None:
        return None

    async def close(self) -> None:
        return None


class InvalidProvider(FakeProvider):
    async def complete_json(self, **_: Any) -> OpenRouterResult:
        result = await super().complete_json()
        return OpenRouterResult(
            result.generation_id,
            result.requested_model,
            result.resolved_model,
            {**result.value, "unapproved_action": "write_pty"},
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
            result.latency_ms,
        )


class BlockingProvider(FakeProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def complete_json(self, **_: Any) -> OpenRouterResult:
        self.started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


class CountingTitleProvider(FakeProvider):
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete_json(self, **_: Any) -> OpenRouterResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return OpenRouterResult(
            "generation-title",
            "vendor/cheap",
            "vendor/cheap",
            {"title": "Getting Started", "confidence": 0.95},
            40,
            8,
            0.001,
            10,
        )


class Phase7FixtureProvider(FakeProvider):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete_json(self, **values: Any) -> OpenRouterResult:
        schema = str(values["schema_name"])
        self.calls.append(schema)
        value = (
            {"needs_attention": True, "summary": "Review this condition.", "confidence": 0.88}
            if schema == "attention_v1"
            else {
                "summary": "Continue in a fresh context with the verified state.",
                "confidence": 0.9,
            }
        )
        return OpenRouterResult(
            f"generation-{len(self.calls)}",
            str(values["model"]),
            str(values["model"]),
            value,
            80,
            12,
            0.001,
            10,
        )


class FailingPhase7Provider(FakeProvider):
    async def complete_json(self, **_: Any) -> OpenRouterResult:
        raise OpenRouterError("fixture provider unavailable")


class ReconcilingProvider(FakeProvider):
    async def complete_json(self, **_: Any) -> OpenRouterResult:
        result = await super().complete_json()
        return OpenRouterResult(
            result.generation_id,
            result.requested_model,
            result.resolved_model,
            result.value,
            result.input_tokens,
            result.output_tokens,
            None,
            result.latency_ms,
        )

    async def generation_cost(self, generation_id: str) -> float | None:
        assert generation_id == "generation-1"
        return 0.0125


def record(tmp_path: Path) -> SessionRecord:
    item = SessionRecord(
        "pty-1",
        "builder",
        "default",
        "claude",
        "native-1",
        str(tmp_path),
        "claude",
        [],
    )
    item.agent_run_id = "run-1"
    item.run_project_scope_id = "scope-1"
    item.project_scope_id = "scope-1"
    return item


def normalized_event(
    item: SessionRecord, sequence: int, event_type: str = "turn_ended", **changes: Any
) -> NormalizedEvent:
    values: dict[str, Any] = {
        "version": 1,
        "seq": sequence,
        "ts": 12,
        "type": event_type,
        "session_id": item.id,
        "agent_run_id": item.agent_run_id,
        "backend": item.backend,
        "project_scope_id": "scope-1",
        "session_name": item.name,
        "space_id": item.space_id,
        "state": item.state,
        "attended": False,
        "context_pct": item.context_pct,
        "pinned": False,
        "source": "transcript",
        "confidence": 0.9,
        "capability": "semantic",
        "chain_id": f"chain-{sequence}",
        "chain_depth": 0,
        "payload": {},
    }
    values.update(changes)
    return NormalizedEvent(**values)


def test_rules_are_stable_and_reject_actuation() -> None:
    first = parse_rules(RULES)[0]
    second = parse_rules(RULES.replace('name = "Summarize turn"', 'name="Summarize turn"'))[0]
    assert first.revision == second.revision
    assert first.actions[0]["kind"] == "llm"

    with pytest.raises(RuleValidationError, match="limited"):
        parse_rules(
            'version=1\n[[rule]]\nid="bad"\non="turn_ended"\ndo=[{kind="write_pty", text="yes"}]\n'
        )

    assert parse_rules(serialize_rules([first]))[0].revision == first.revision
    with pytest.raises(RuleValidationError, match="numeric"):
        parse_rules(
            'version=1\n[[rule]]\nid="bad-delay"\non={trigger="turn_ended",'
            'debounce_s="soon"}\ndo=[{kind="notify",message="done"}]\n'
        )


def test_rules_reject_unknown_action_and_condition_fields() -> None:
    with pytest.raises(RuleValidationError, match="action has unknown fields"):
        parse_rules(
            RULES.replace(
                'schema = "summary_v1"', 'schema = "summary_v1"\ncommand = "no"'
            )
        )


def test_invalid_reload_keeps_last_known_good_rules(tmp_path: Path) -> None:
    path = tmp_path / "rules.toml"
    path.write_text(RULES, encoding="utf-8")
    engine = AutomationEngine(
        path,
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        AutomationStore(tmp_path / "mux.db"),
        Config(data_dir=tmp_path),
        FakeProvider(),  # type: ignore[arg-type]
    )
    engine.reload()
    revision = engine.rules[0].revision

    path.write_text("version=1\n[[rule]]\nid='broken'\n", encoding="utf-8")
    engine.reload()

    assert engine.rules[0].revision == revision
    assert engine.diagnostic
    engine.store.close()
    with pytest.raises(RuleValidationError, match="condition has unknown fields"):
        parse_rules(
            RULES.replace(
                'when = [{ field = "backend", op = "in", value = ["claude", "codex"] }]',
                'when = [{ field = "backend", op = "in", value = ["claude"], '
                "native_field = true }]",
            )
        )


def test_normalized_event_allowlists_payload_and_uses_trusted_run_scope(tmp_path: Path) -> None:
    item = record(tmp_path)
    event = MuxEvent(
        10,
        item.id,
        "transcript",
        "tool_use",
        {"tool": "Bash", "native_path": "secret", "detail": "x" * 9000},
        42,
    )
    normalized = normalize_event(event, item)
    assert normalized.seq == 42
    assert normalized.project_scope_id == "scope-1"
    assert normalized.source == "transcript"
    assert "native_path" not in normalized.payload
    assert len(normalized.payload["detail"]) == 4096


@pytest.mark.asyncio
async def test_rule_chain_revisit_is_flagged_without_side_effects(tmp_path: Path) -> None:
    item = record(tmp_path)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        store,
        Config(data_dir=tmp_path, automation_enabled=True),
        FakeProvider(),  # type: ignore[arg-type]
    )
    rules = parse_rules(
        'version=1\n[[rule]]\nid="again"\non="notification_created"\n'
        'do=[{kind="notify",message="again"}]\n'
    )
    event = normalized_event(
        item,
        45,
        "notification_created",
        chain_id="chain-loop",
        chain_depth=1,
        chain_rules=("again",),
    )

    reports = await engine.evaluate(event, rules=rules)

    assert reports[0]["loop_detected"] is True
    assert engine.status()["queue"]["loop_rejections"] == 1
    assert await store.firings() == []
    assert await store.notifications() == []
    store.close()


@pytest.mark.asyncio
async def test_observer_pipeline_persists_result_not_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"TOP-SECRET-PROMPT"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}\n',
        encoding="utf-8",
    )
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=transcript)
    sessions = SimpleNamespace(sessions={item.id: session})
    config = Config(
        data_dir=tmp_path,
        automation_enabled=True,
        openrouter_cheap_model="vendor/cheap",
    )
    store = AutomationStore(tmp_path / "mux.db")
    bus = EventBus()
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        bus,
        sessions,  # type: ignore[arg-type]
        store,
        config,
        FakeProvider(),  # type: ignore[arg-type]
    )
    event = NormalizedEvent(
        1,
        4,
        12,
        "turn_ended",
        item.id,
        item.agent_run_id,
        "claude",
        "scope-1",
        "builder",
        "default",
        "idle",
        False,
        0,
        False,
        "transcript",
        0.9,
        "semantic",
        "chain-1",
        0,
        {},
    )

    reports = await engine.evaluate(event, rules=parse_rules(RULES))

    assert reports[0]["matched"] is True
    annotations = await store.annotations(agent_run_id="run-1")
    assert annotations[0]["content"] == "Implemented the parser and verified it."
    assert annotations[0]["requested_model"] == "vendor/cheap"
    assert (await store.spend())["tokens"] == 138
    store.close()
    persisted = b"".join(path.read_bytes() for path in tmp_path.glob("mux.db*"))
    assert b"TOP-SECRET-PROMPT" not in persisted


@pytest.mark.asyncio
async def test_builtin_titler_reserves_one_paid_call_per_agent_run(tmp_path: Path) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"hello"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8",
    )
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=transcript)
    provider = CountingTitleProvider()
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={item.id: session}),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            observer_titler_enabled=True,
            openrouter_cheap_model="vendor/cheap",
        ),
        provider,  # type: ignore[arg-type]
    )

    first = asyncio.create_task(engine.evaluate(normalized_event(item, 10, source="native_hook")))
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    second_reports = await engine.evaluate(normalized_event(item, 11, source="transcript"))
    provider.release.set()
    first_reports = await first

    assert provider.calls == 1
    assert len(await store.annotations(agent_run_id="run-1", tag="title")) == 1
    assert len(await store.observer_calls()) == 1
    assert first_reports[0]["matched"] is True
    assert second_reports[0]["guarded"] is True
    store.close()


@pytest.mark.asyncio
async def test_missing_reported_cost_is_reconciled_by_generation_id(tmp_path: Path) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"check"}}\n', encoding="utf-8"
    )
    item = record(tmp_path)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(
            sessions={item.id: SimpleNamespace(record=item, transcript_path=transcript)}
        ),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            openrouter_cheap_model="vendor/cheap",
        ),
        ReconcilingProvider(),  # type: ignore[arg-type]
    )

    await engine.evaluate(normalized_event(item, 5), rules=parse_rules(RULES))
    await asyncio.gather(*engine._background)

    assert (await store.spend())["cost_usd"] == pytest.approx(0.0125)
    assert (await store.observer_calls())[0]["cost_usd"] == pytest.approx(0.0125)
    store.close()


@pytest.mark.asyncio
async def test_queue_overflow_is_bounded_and_diagnosed(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        store,
        Config(data_dir=tmp_path, automation_queue_size=1),
        FakeProvider(),  # type: ignore[arg-type]
    )
    engine._event_queue = asyncio.Queue()
    for sequence in range(3):
        await engine._event_queue.put(
            MuxEvent(12 + sequence, None, "daemon", "timer", {}, sequence + 1)
        )
    ingest = asyncio.create_task(engine._ingest())
    for _ in range(10):
        if engine.queue_dropped == 2:
            break
        await asyncio.sleep(0)
    ingest.cancel()
    await asyncio.gather(ingest, return_exceptions=True)

    assert engine.queue.qsize() == 1
    assert engine.status()["queue"]["dropped"] == 2
    store.close()


@pytest.mark.asyncio
async def test_dry_run_is_repeatable_and_writes_no_automation_records(tmp_path: Path) -> None:
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=None)
    config = Config(data_dir=tmp_path, automation_enabled=True)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={item.id: session}),  # type: ignore[arg-type]
        store,
        config,
        FakeProvider(),  # type: ignore[arg-type]
    )
    event = NormalizedEvent(
        1,
        9,
        12,
        "turn_ended",
        item.id,
        item.agent_run_id,
        "claude",
        "scope-1",
        "builder",
        "default",
        "idle",
        False,
        0,
        False,
        "transcript",
        0.9,
        "semantic",
        "chain-1",
        0,
        {},
    )

    first = await engine.evaluate(event, rules=parse_rules(RULES), dry_run=True)
    second = await engine.evaluate(event, rules=parse_rules(RULES), dry_run=True)

    assert first == second
    assert first[0]["actions"][0]["would_execute"]["kind"] == "llm"
    assert await store.firings() == []
    assert await store.annotations() == []
    assert await store.spend() == {"tokens": 0, "cost_usd": 0.0}
    store.close()


@pytest.mark.asyncio
async def test_debounce_coalesces_and_threshold_hysteresis_rearms(tmp_path: Path) -> None:
    item = record(tmp_path)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        store,
        Config(data_dir=tmp_path, automation_enabled=True),
        FakeProvider(),  # type: ignore[arg-type]
    )
    debounce_rules = parse_rules(
        'version=1\n[[rule]]\nid="debounced"\non={trigger="turn_ended",debounce_s=0.03}'
        '\ndo=[{kind="notify",message="latest"}]\n'
    )
    engine._tasks.append(asyncio.current_task())  # Mark the runtime scheduler as active.
    await engine.evaluate(normalized_event(item, 1), rules=debounce_rules)
    await engine.evaluate(normalized_event(item, 2), rules=debounce_rules)
    await asyncio.sleep(0.08)
    engine._tasks.clear()

    notifications = await store.notifications()
    firings = await store.firings()
    assert len(notifications) == 1
    assert [row["event_seq"] for row in firings] == [2]
    debounce = await store.checkpoint("debounce:debounced:run-1")
    assert debounce and debounce["status"] == "fired"

    threshold_rules = parse_rules(
        'version=1\n[[rule]]\nid="context"\n'
        'on={trigger="context_pressure",threshold={field="context_pct",op="gte",'
        'value=0.8,hysteresis=0.1}}\ndo=[{kind="notify",message="high context"}]\n'
    )
    for sequence, value in ((3, 0.8), (4, 0.9), (5, 0.6), (6, 0.81)):
        await engine.evaluate(
            normalized_event(item, sequence, "context_pressure", context_pct=value),
            rules=threshold_rules,
        )
    context_notifications = [
        row for row in await store.notifications() if row["rule_id"] == "context"
    ]
    assert len(context_notifications) == 2
    store.close()


@pytest.mark.asyncio
async def test_invalid_structured_output_is_charged_and_action_failure_is_traced(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"check"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}\n',
        encoding="utf-8",
    )
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=transcript)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={item.id: session}),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            openrouter_cheap_model="vendor/cheap",
        ),
        InvalidProvider(),  # type: ignore[arg-type]
    )

    reports = await engine.evaluate(normalized_event(item, 20), rules=parse_rules(RULES))

    assert "extra fields" in reports[0]["error"]
    assert (await store.spend())["tokens"] == 138
    calls = await store.observer_calls()
    assert calls[0]["status"] == "failed"
    assert calls[0]["input_tokens"] == 120
    actions = await store.action_results()
    assert actions[0]["status"] == "failed"
    assert "extra fields" in actions[0]["error"]
    store.close()


@pytest.mark.asyncio
async def test_rule_token_budget_blocks_call_before_provider_use(tmp_path: Path) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"check"}}\n', encoding="utf-8")
    item = record(tmp_path)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(
            sessions={item.id: SimpleNamespace(record=item, transcript_path=transcript)}
        ),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            openrouter_cheap_model="vendor/cheap",
            automation_rule_daily_token_budget=1,
        ),
        FakeProvider(),  # type: ignore[arg-type]
    )

    reports = await engine.evaluate(normalized_event(item, 25), rules=parse_rules(RULES))

    assert "rule token budget" in reports[0]["error"]
    assert await store.observer_calls() == []
    store.close()


@pytest.mark.asyncio
async def test_observer_cancellation_closes_call_action_and_firing_records(tmp_path: Path) -> None:
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"check"}}\n', encoding="utf-8")
    item = record(tmp_path)
    provider = BlockingProvider()
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(
            sessions={item.id: SimpleNamespace(record=item, transcript_path=transcript)}
        ),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            openrouter_cheap_model="vendor/cheap",
        ),
        provider,  # type: ignore[arg-type]
    )
    task = asyncio.create_task(
        engine.evaluate(normalized_event(item, 30), rules=parse_rules(RULES))
    )
    await asyncio.wait_for(provider.started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await store.observer_calls())[0]["status"] == "cancelled"
    assert (await store.action_results())[0]["status"] == "cancelled"
    assert (await store.firings())[0]["status"] == "cancelled"
    store.close()


def test_public_config_and_secret_status_never_return_key(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    store = PlatformSecretStore(tmp_path / "secrets.json")

    assert store.get("openrouter_api_key") == "sk-or-secret"
    assert store.status("openrouter_api_key") == {
        "configured": True,
        "source": "environment",
        "persistent": False,
    }
    payload = Config(data_dir=tmp_path).public_dict()
    assert "sk-or-secret" not in str(payload)
    assert not (tmp_path / "secrets.json").exists()
    os.environ.pop("OPENROUTER_API_KEY", None)


@pytest.mark.asyncio
async def test_hook_silence_emits_typed_capability_degradation(tmp_path: Path) -> None:
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=None)
    bus = EventBus()
    monitor = bus.subscribe()
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        bus,
        SimpleNamespace(sessions={item.id: session}),  # type: ignore[arg-type]
        store,
        Config(data_dir=tmp_path),
        FakeProvider(),  # type: ignore[arg-type]
    )

    for sequence, timestamp in enumerate((1.0, 16.0, 31.0), 1):
        await engine._probe_sources(
            MuxEvent(timestamp, item.id, "transcript", "turn_ended", {}, sequence), item
        )

    degraded = await asyncio.wait_for(monitor.get(), timeout=1)
    assert degraded.type == "capability_degraded"
    assert degraded.payload["capability"] == "native_hook"
    assert engine.status()["capabilities"]["adapters"]["claude"][
        "hook_silence_degraded"
    ] == 1
    await engine._probe_sources(
        MuxEvent(32, item.id, "hook", "turn_ended", {}, 4), item
    )
    assert engine.status()["capabilities"]["adapters"]["claude"][
        "hook_silence_degraded"
    ] == 0
    store.close()


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.asyncio
async def test_phase7_observers_are_bounded_on_both_backend_fixtures(
    tmp_path: Path, backend: str
) -> None:
    transcript = Path(__file__).parent / "fixtures" / "transcripts" / "v1" / f"{backend}.jsonl"
    item = record(tmp_path)
    item.backend = backend  # type: ignore[assignment]
    item.native_session_id = f"{backend}-fixture"
    session = SimpleNamespace(record=item, transcript_path=transcript)
    provider = Phase7FixtureProvider()
    store = AutomationStore(tmp_path / "mux.db")
    await store.create_annotation(
        agent_run_id=item.agent_run_id or "run-1",
        session_id=item.id,
        tag="turn-summary",
        content="The agent has repeated the same failed parser repair.",
        source_event_seq=1,
        rule_id="builtin.turn-summarizer",
        rule_revision="r1",
        provenance="fixture",
    )
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={item.id: session}),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            phase7_observers_enabled=True,
            openrouter_cheap_model="vendor/cheap",
            openrouter_standard_model="vendor/standard",
        ),
        provider,  # type: ignore[arg-type]
    )

    reports = []
    for sequence, event_type in enumerate(
        ("stalled", "approval_needed", "context_pressure"), 10
    ):
        reports.extend(await engine.evaluate(normalized_event(item, sequence, event_type)))

    assert all("error" not in report for report in reports)
    assert provider.calls == ["attention_v1", "attention_v1", "summary_v1"]
    assert len(await store.notifications()) == 2
    assert any(
        annotation["tag"] == "handoff-suggestion"
        for annotation in await store.annotations(agent_run_id=item.agent_run_id)
    )
    store.close()


@pytest.mark.parametrize("backend", ["claude", "codex"])
@pytest.mark.asyncio
async def test_phase7_provider_failure_does_not_change_agent_state(
    tmp_path: Path, backend: str
) -> None:
    transcript = Path(__file__).parent / "fixtures" / "transcripts" / "v1" / f"{backend}.jsonl"
    item = record(tmp_path)
    item.backend = backend  # type: ignore[assignment]
    item.state = "awaiting"
    session = SimpleNamespace(record=item, transcript_path=transcript)
    store = AutomationStore(tmp_path / "mux.db")
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={item.id: session}),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            phase7_observers_enabled=True,
            openrouter_cheap_model="vendor/cheap",
        ),
        FailingPhase7Provider(),  # type: ignore[arg-type]
    )

    reports = await engine.evaluate(normalized_event(item, 50, "approval_needed"))

    assert "provider unavailable" in reports[0]["error"]
    assert item.state == "awaiting"
    assert await store.notifications() == []
    assert (await store.observer_calls())[0]["status"] == "failed"
    store.close()


def test_builtin_rules_are_cached_and_rebuild_on_flag_change(tmp_path: Path) -> None:
    path = tmp_path / "rules.toml"
    path.write_text(RULES, encoding="utf-8")
    engine = AutomationEngine(
        path,
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        AutomationStore(tmp_path / "mux.db"),
        Config(data_dir=tmp_path, observer_titler_enabled=True),
        FakeProvider(),  # type: ignore[arg-type]
    )
    record = SessionRecord(
        "a", "name", "default", "claude", "native", str(tmp_path), "claude.exe", []
    )
    record.agent_run_id = "run-a"
    record.state = "working"
    event = normalized_event(record, 1, "turn_ended")

    first = engine._builtin_rules(event)
    second = engine._builtin_rules(event)
    assert first is second  # memoised: same list object, not re-parsed
    assert any(rule.id == "builtin.session-titler" for rule in first)

    # A different event type keys a distinct entry.
    other = engine._builtin_rules(normalized_event(record, 2, "stalled"))
    assert other is not first

    # Flipping a tracked flag invalidates via a new key.
    engine.config.observer_titler_enabled = False
    third = engine._builtin_rules(event)
    assert third is not first
    assert all(rule.id != "builtin.session-titler" for rule in third)

    engine.store.close()


def test_builtin_rules_skip_without_agent_run(tmp_path: Path) -> None:
    path = tmp_path / "rules.toml"
    path.write_text(RULES, encoding="utf-8")
    engine = AutomationEngine(
        path,
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        AutomationStore(tmp_path / "mux.db"),
        Config(data_dir=tmp_path, observer_titler_enabled=True),
        FakeProvider(),  # type: ignore[arg-type]
    )
    record = SessionRecord(
        "a", "name", "default", "claude", "native", str(tmp_path), "claude.exe", []
    )
    # No agent_run_id -> short-circuit before any cache use.
    event = normalized_event(record, 1, "turn_ended", agent_run_id=None)
    assert engine._builtin_rules(event) == []
    engine.store.close()
