from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
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
from swe_mux.automation_store import AUTOMATION_SCHEMA_VERSION, AutomationStore
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.git_projects import ProjectIdentity
from swe_mux.history import HistoryIndex
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.openrouter import OpenRouterError, OpenRouterResult
from swe_mux.secret_store import PlatformSecretStore
from swe_mux.sqlite_store import read_schema_version

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


@pytest.mark.asyncio
async def test_duplicate_firing_releases_shared_database_writer_lock(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)
    store = AutomationStore(path)
    async def create_firing() -> str | None:
        return await store.create_firing(
            event_seq=7,
            event_type="turn_ended",
            agent_run_id="run-1",
            session_id="session-1",
            rule_id="dedupe-rule",
            rule_revision="revision-1",
            chain_id="chain-1",
            chain_depth=0,
            shadow=False,
            trace=[],
        )

    try:
        assert await create_firing() is not None
        assert await create_firing() is None
        sequence = await asyncio.wait_for(
            history.append_event(
                MuxEvent(time.time(), "session-1", "daemon", "terminal_attached", {})
            ),
            timeout=1,
        )
        assert sequence > 0
    finally:
        store.close()
        history.close()


@pytest.mark.asyncio
async def test_feature_stores_coordinate_complete_database_operations(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    history = HistoryIndex(path)
    store = AutomationStore(path)
    transaction_started = threading.Event()
    release_transaction = threading.Event()

    def shorten_history_busy_timeout() -> None:
        history._db.execute("PRAGMA busy_timeout=50")

    def hold_automation_transaction() -> None:
        store._db.execute("BEGIN IMMEDIATE")
        transaction_started.set()
        if not release_transaction.wait(timeout=2):
            raise TimeoutError("test did not release automation transaction")
        store._db.rollback()

    holder: asyncio.Task[None] | None = None
    writer: asyncio.Task[dict[str, Any]] | None = None
    try:
        await history._run(shorten_history_busy_timeout)
        holder = asyncio.create_task(store._run(hold_automation_transaction))
        assert await asyncio.to_thread(transaction_started.wait, 1)
        writer = asyncio.create_task(
            history.register_project_scope(
                ProjectIdentity("scope-1", "Project", str(tmp_path), "cwd")
            )
        )

        # Longer than history's SQLite busy timeout: this only remains pending
        # when the process-wide coordinator queues it before entering SQLite.
        await asyncio.sleep(0.1)
        assert not writer.done()
        release_transaction.set()
        await asyncio.wait_for(holder, timeout=1)
        scope = await asyncio.wait_for(writer, timeout=1)
        assert scope["id"] == "scope-1"
    finally:
        release_transaction.set()
        if holder is not None:
            await asyncio.gather(holder, return_exceptions=True)
        if writer is not None:
            await asyncio.gather(writer, return_exceptions=True)
        store.close()
        history.close()


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
        "project_id": item.project_id,
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
        parse_rules(RULES.replace('schema = "summary_v1"', 'schema = "summary_v1"\ncommand = "no"'))


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


class InstantTitleProvider(FakeProvider):
    """Returns a title immediately; CountingTitleProvider blocks on a release gate."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_json(self, **_: Any) -> OpenRouterResult:
        self.calls += 1
        return OpenRouterResult(
            "generation-title",
            "vendor/cheap",
            "vendor/cheap",
            {"title": "Login Test Fix", "confidence": 0.95},
            40,
            8,
            0.001,
            10,
        )


def _titler_engine(
    tmp_path: Path, session: Any, store: AutomationStore, provider: Any = None
) -> AutomationEngine:
    return AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={session.record.id: session}),  # type: ignore[arg-type]
        store,
        Config(
            data_dir=tmp_path,
            automation_enabled=True,
            observer_titler_enabled=True,
            openrouter_cheap_model="vendor/cheap",
        ),
        provider or InstantTitleProvider(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_titler_names_a_pane_from_the_prompt_with_no_transcript(
    tmp_path: Path,
) -> None:
    """A pane must get a name when the user asks, not a turn later.

    The titler reads the submitted request, so it needs neither a transcript on disk
    nor semantic observation — the two things that were failing live. Measured before
    this existed: every title waited for `turn_ended`, and 5 of 6 observed failures
    were `observer requires semantic observation`.
    """
    item = record(tmp_path)
    session = SimpleNamespace(
        record=item, transcript_path=None, first_user_prompt="fix the flaky login test"
    )
    store = AutomationStore(tmp_path / "mux.db")
    engine = _titler_engine(tmp_path, session, store)

    reports = await engine.evaluate(
        normalized_event(item, 10, event_type="turn_started", capability="inferred")
    )

    assert reports and reports[0]["matched"] is True
    titles = await store.annotations(agent_run_id="run-1", tag="title")
    assert len(titles) == 1
    assert titles[0]["rule_id"] == "builtin.session-titler-initial"
    store.close()


@pytest.mark.asyncio
async def test_completed_turns_never_retitle_a_named_run(tmp_path: Path) -> None:
    """The opening request is the title; a completed turn is not allowed to move it.

    Titling from the last turn is what made names drift into the work of the last few
    minutes — observed live: "Fix flaky login test" became "OK" one turn later. So the
    turn_ended stage stands down whenever the run has a request to read, and nothing
    replaces a title once it exists.
    """
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"hello"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8",
    )
    item = record(tmp_path)
    session = SimpleNamespace(
        record=item, transcript_path=transcript, first_user_prompt="fix the flaky login test"
    )
    store = AutomationStore(tmp_path / "mux.db")
    engine = _titler_engine(tmp_path, session, store)

    await engine.evaluate(normalized_event(item, 10, event_type="turn_started"))
    after_turn = await engine.evaluate(normalized_event(item, 11, source="native_hook"))
    after_prompt = await engine.evaluate(normalized_event(item, 12, event_type="turn_started"))

    assert after_turn[0]["guarded"] is True
    assert after_prompt[0]["guarded"] is True
    rows = await store.annotations(agent_run_id="run-1", tag="title")
    assert len(rows) == 1
    assert rows[0]["rule_id"] == "builtin.session-titler-initial"
    store.close()


@pytest.mark.asyncio
async def test_turn_ended_titles_a_run_whose_request_was_never_captured(
    tmp_path: Path,
) -> None:
    """Codex has no prompt hook, so the completed turn is all its tab can be named from.

    This is the only case the weaker last-turn reading is for; with a request on hand
    it stands down (see `test_completed_turns_never_retitle_a_named_run`).
    """
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"content":"hello"}}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8",
    )
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=transcript, first_user_prompt=None)
    store = AutomationStore(tmp_path / "mux.db")
    engine = _titler_engine(tmp_path, session, store)

    reports = await engine.evaluate(normalized_event(item, 10, source="native_hook"))

    assert reports and reports[0]["matched"] is True
    rows = await store.annotations(agent_run_id="run-1", tag="title")
    assert [row["rule_id"] for row in rows] == ["builtin.session-titler"]
    store.close()


@pytest.mark.asyncio
async def test_initial_titler_is_skipped_when_no_prompt_was_observed(
    tmp_path: Path,
) -> None:
    """Codex has no prompt hook, so it has nothing to title from until a turn ends."""
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=None, first_user_prompt=None)
    store = AutomationStore(tmp_path / "mux.db")
    engine = _titler_engine(tmp_path, session, store)

    reports = await engine.evaluate(normalized_event(item, 10, event_type="turn_started"))

    assert not await store.annotations(agent_run_id="run-1", tag="title")
    assert reports and reports[0].get("error")
    store.close()


class RateLimitedThenTitleProvider(FakeProvider):
    """Fails the first N calls the way a provider rate limit does, then succeeds."""

    def __init__(self, failures: int) -> None:
        self.remaining_failures = failures
        self.prompts: list[str] = []

    async def complete_json(self, **values: Any) -> OpenRouterResult:
        self.prompts.append(str(values["messages"][-1]["content"]))
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise OpenRouterError("OpenRouter request failed with HTTP 429")
        return OpenRouterResult(
            "generation-title",
            "vendor/cheap",
            "vendor/cheap",
            {"title": "Login Test Fix", "confidence": 0.95},
            40,
            8,
            0.001,
            10,
        )


@pytest.mark.asyncio
async def test_rate_limited_title_retries_in_the_background_off_the_first_request(
    tmp_path: Path,
) -> None:
    """A 429 must not cost the run its name, and the retry must not rename the work.

    Both halves were live defects. Titles were lost to bursts of 429 and the pane then
    sat nameless until the user happened to type again — 20+ minutes, observed — and
    the attempt that finally landed read whatever the newest prompt was, so the tab
    ended up named after a detour rather than the job.
    """
    item = record(tmp_path)
    session = SimpleNamespace(
        record=item, transcript_path=None, first_user_prompt="fix the flaky login test"
    )
    store = AutomationStore(tmp_path / "mux.db")
    provider = RateLimitedThenTitleProvider(failures=1)
    engine = _titler_engine(tmp_path, session, store, provider=provider)
    engine._title_retry_delays = (0.0,)

    reports = await engine.evaluate(normalized_event(item, 10, event_type="turn_started"))
    assert reports[0]["retry_scheduled"] is True
    # The user moves on while the retry is pending; the pinned request must win anyway.
    session.first_user_prompt = "never mind, check the deploy logs"
    await asyncio.gather(*list(engine._background))

    rows = await store.annotations(agent_run_id="run-1", tag="title")
    assert [row["content"] for row in rows] == ["Login Test Fix"]
    assert provider.prompts[0] == provider.prompts[1]
    assert "flaky login test" in provider.prompts[1]
    store.close()


@pytest.mark.asyncio
async def test_title_retries_stop_at_the_attempt_budget(tmp_path: Path) -> None:
    """Retrying is bounded: a provider that is down must not become an infinite loop."""
    item = record(tmp_path)
    session = SimpleNamespace(
        record=item, transcript_path=None, first_user_prompt="fix the flaky login test"
    )
    store = AutomationStore(tmp_path / "mux.db")
    provider = RateLimitedThenTitleProvider(failures=99)
    engine = _titler_engine(tmp_path, session, store, provider=provider)
    engine._title_retry_delays = (0.0, 0.0)

    await engine.evaluate(normalized_event(item, 10, event_type="turn_started"))
    while engine._background:
        await asyncio.gather(*list(engine._background))

    assert not await store.annotations(agent_run_id="run-1", tag="title")
    assert len(provider.prompts) == 3
    store.close()


@pytest.mark.asyncio
async def test_a_decision_failure_is_not_retried(tmp_path: Path) -> None:
    """Only provider faults retry; a run with no request would fail identically."""
    item = record(tmp_path)
    session = SimpleNamespace(record=item, transcript_path=None, first_user_prompt=None)
    store = AutomationStore(tmp_path / "mux.db")
    engine = _titler_engine(tmp_path, session, store)

    reports = await engine.evaluate(normalized_event(item, 10, event_type="turn_started"))

    assert reports[0].get("error")
    assert "retry_scheduled" not in reports[0]
    assert not engine._background
    store.close()


@pytest.mark.asyncio
async def test_pinned_request_survives_a_daemon_restart(tmp_path: Path) -> None:
    """The live pin dies with the daemon; every reload and redeploy restarts it.

    Re-reading the session after a restart would title the run from whatever prompt
    came next, so the first one is pinned in the store on first use instead.
    """
    item = record(tmp_path)
    session = SimpleNamespace(
        record=item, transcript_path=None, first_user_prompt="fix the flaky login test"
    )
    store = AutomationStore(tmp_path / "mux.db")
    engine = _titler_engine(tmp_path, session, store)
    event = normalized_event(item, 10, event_type="turn_started")

    assert await engine._run_prompt(event) == "fix the flaky login test"
    # Restart: the daemon rebuilds the session with no prompt memory at all.
    session.first_user_prompt = None
    assert await engine._run_prompt(event) == "fix the flaky login test"
    store.close()


@pytest.mark.asyncio
async def test_missing_reported_cost_is_reconciled_by_generation_id(tmp_path: Path) -> None:
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
    assert engine.status()["capabilities"]["adapters"]["claude"]["hook_silence_degraded"] == 1
    await engine._probe_sources(MuxEvent(32, item.id, "hook", "turn_ended", {}, 4), item)
    assert engine.status()["capabilities"]["adapters"]["claude"]["hook_silence_degraded"] == 0
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
    for sequence, event_type in enumerate(("stalled", "approval_needed", "context_pressure"), 10):
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


def test_status_lists_enabled_and_disabled_builtin_observers(tmp_path: Path) -> None:
    engine = AutomationEngine(
        tmp_path / "rules.toml",
        EventBus(),
        SimpleNamespace(sessions={}),  # type: ignore[arg-type]
        AutomationStore(tmp_path / "mux.db"),
        Config(
            data_dir=tmp_path,
            observer_titler_enabled=True,
            observer_summarizer_enabled=False,
            phase7_observers_enabled=False,
        ),
        FakeProvider(),  # type: ignore[arg-type]
    )

    builtins = {item["id"]: item for item in engine.status()["built_in_rules"]}

    assert set(builtins) == {
        "builtin.session-titler-initial",
        "builtin.session-titler",
        "builtin.turn-summarizer",
        "builtin.stalled-triage",
        "builtin.approval_needed-triage",
        "builtin.context-handoff",
    }
    assert builtins["builtin.session-titler"]["enabled"] is True
    assert builtins["builtin.turn-summarizer"]["enabled"] is False
    assert builtins["builtin.stalled-triage"]["setting_key"] == "phase7_observers_enabled"
    assert builtins["builtin.context-handoff"]["model"] == "Standard model"
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


@pytest.mark.asyncio
async def test_prune_covers_every_growing_table(tmp_path: Path) -> None:
    """Six tables previously had no DELETE anywhere; on a daemon designed for
    weeks of uptime that is unbounded growth, not slow retention."""
    store = AutomationStore(tmp_path / "mux.db")
    old = time.time() - 400 * 86400
    try:
        firing_id = await store.create_firing(
            event_seq=1,
            event_type="turn_ended",
            agent_run_id="run-1",
            session_id="s1",
            rule_id="r1",
            rule_revision="rev",
            chain_id="c1",
            chain_depth=0,
            shadow=False,
            trace=[],
        )
        assert firing_id is not None
        await store.action_result(firing_id, 0, "annotate", "ok", {})
        await store.observer_started(
            firing_id=firing_id,
            rule_id="r1",
            model="cheap",
            input_hash="h",
            input_bytes=1,
        )
        await store.notify(
            agent_run_id="run-1", session_id="s1", rule_id="r1", kind="k", title="t", message="m"
        )
        await store.add_spend(
            rule_id="r1",
            model="cheap",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.0,
            call_id="call-1",
        )
        await store.create_batch("summary", ["run-1"])
        await store.set_checkpoint("rule:r1:run-1", {"ts": 1})
        await store.create_annotation(
            agent_run_id="run-1",
            session_id="s1",
            tag="turn-summary",
            content="c",
            source_event_seq=1,
            rule_id="r1",
            rule_revision="rev",
            provenance="observer",
        )
        await store.add_lineage("run-1", "run-2", "branch")
        await store.add_experience(
            project_scope_id="scope",
            backend="claude",
            error="boom",
            resolution="fixed",
            source_run_id="run-1",
            confidence=0.5,
        )

        tables = (
            "automation_firings",
            "automation_action_results",
            "automation_observer_calls",
            "automation_notifications",
            "automation_budget_ledger",
            "observer_batches",
            "automation_checkpoints",
            "automation_annotations",
            "session_lineage",
            "experience_entries",
        )

        def counts() -> dict[str, int]:
            return {
                table: store._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables
            }

        assert all(value == 1 for value in (await store._run(counts)).values())

        # Nothing recent is removed.
        await store.prune(90)
        assert all(value == 1 for value in (await store._run(counts)).values())

        # Age every row past both windows, then prune with an explicit durable
        # window so the second retention class is exercised too.
        def age() -> None:
            for table in tables:
                column = "updated_at" if table == "automation_checkpoints" else "created_at"
                store._db.execute(f"UPDATE {table} SET {column}=?", (old,))
            store._db.commit()

        await store._run(age)
        await store.prune(90)
        remaining = await store._run(counts)
        assert all(value == 0 for value in remaining.values()), remaining
    finally:
        store.close()


@pytest.mark.asyncio
async def test_prune_keeps_derived_knowledge_longer_than_the_operational_trail(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        await store.notify(
            agent_run_id="run-1", session_id="s1", rule_id=None, kind="k", title="t", message="m"
        )
        await store.create_annotation(
            agent_run_id="run-1",
            session_id="s1",
            tag="turn-summary",
            content="c",
            source_event_seq=1,
            rule_id=None,
            rule_revision=None,
            provenance="observer",
        )
        older_than_operational = time.time() - 120 * 86400

        def age() -> None:
            for table in ("automation_notifications", "automation_annotations"):
                store._db.execute(f"UPDATE {table} SET created_at=?", (older_than_operational,))
            store._db.commit()

        await store._run(age)
        await store.prune(90)

        def counts() -> tuple[int, int]:
            return (
                store._db.execute("SELECT COUNT(*) FROM automation_notifications").fetchone()[0],
                store._db.execute("SELECT COUNT(*) FROM automation_annotations").fetchone()[0],
            )

        notifications, annotations = await store._run(counts)
        assert notifications == 0
        assert annotations == 1  # run notes outlive the firing trail
    finally:
        store.close()


@pytest.mark.asyncio
async def test_annotations_anchor_to_a_project_and_dedupe_on_a_key(tmp_path: Path) -> None:
    # Deterministic consumers need both anchors: a loop finding belongs to one
    # agent run, while doc debt is a property of the project and has no run to
    # attach to. Evidence is a list because a finding whose case rests on a set
    # of facts is not traceable through a single event pointer.
    store = AutomationStore(tmp_path / "mux.db")
    try:
        with pytest.raises(ValueError, match="anchored"):
            await store.create_annotation(tag="doc-debt", content="c", provenance="detector")

        first = await store.create_annotation(
            project_id="project-1",
            tag="doc-debt",
            content="4 docs dirty",
            evidence=[{"fact_id": "f1"}, {"fact_id": "f2"}],
            dedupe_key="doc-debt:project-1:abc",
            provenance="detector",
        )
        assert first["agent_run_id"] is None
        assert json.loads(first["evidence_json"]) == [{"fact_id": "f1"}, {"fact_id": "f2"}]

        again = await store.create_annotation(
            project_id="project-1",
            tag="doc-debt",
            content="4 docs dirty",
            dedupe_key="doc-debt:project-1:abc",
            provenance="detector",
        )
        assert again["duplicate"] is True
        assert again["id"] == first["id"]

        by_project = await store.annotations(project_id="project-1")
        assert [item["id"] for item in by_project] == [first["id"]]
        assert await store.annotations(agent_run_id="project-1") == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_legacy_annotation_schema_is_migrated_in_place(tmp_path: Path) -> None:
    # This store had no migration mechanism at all: CREATE TABLE IF NOT EXISTS
    # no-ops on an existing table, so a new column existed only in fresh
    # databases and every upgrade-in-place failed on the first insert naming it.
    path = tmp_path / "mux.db"
    legacy = sqlite3.connect(path)
    legacy.execute(
        "CREATE TABLE automation_annotations ("
        "id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL, session_id TEXT,"
        "tag TEXT NOT NULL, content TEXT NOT NULL, source_event_seq INTEGER,"
        "rule_id TEXT, rule_revision TEXT, provenance TEXT NOT NULL,"
        "requested_model TEXT, resolved_model TEXT, generation_id TEXT,"
        "input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,"
        "cost_usd REAL, confidence REAL, created_at REAL NOT NULL)"
    )
    legacy.execute(
        "INSERT INTO automation_annotations"
        "(id,agent_run_id,tag,content,provenance,created_at) VALUES(?,?,?,?,?,?)",
        ("old-1", "run-1", "title", "Legacy title", "observer", time.time()),
    )
    legacy.commit()
    legacy.close()

    store = AutomationStore(path)
    try:
        kept = await store.annotations(agent_run_id="run-1")
        assert [item["id"] for item in kept] == ["old-1"]
        # The rebuild is what relaxes agent_run_id to nullable; ALTER cannot.
        project_scoped = await store.create_annotation(
            project_id="project-1", tag="doc-debt", content="c", provenance="detector"
        )
        assert project_scoped["project_id"] == "project-1"
        assert read_schema_version(store._db, "automation") == AUTOMATION_SCHEMA_VERSION
    finally:
        store.close()
