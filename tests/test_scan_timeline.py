from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.automation import TranscriptSlice
from swe_mux.automation_store import AutomationStore
from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.models import MuxEvent
from swe_mux.openrouter import OpenRouterError, OpenRouterResult
from swe_mux.scan_timeline import (
    CATCHUP_CHAIN_LIMIT,
    DEFAULT_SCAN_MODEL,
    MAX_INPUT_BYTES,
    TOOL_INPUT_CHARS,
    ScanContext,
    ScanTimelineService,
    mechanical_novelty,
)


def backfill_messages(count: int) -> list[dict[str, Any]]:
    return [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "ts": 101.0 + index,
            "content": [{"type": "text", "text": f"message {index}"}],
        }
        for index in range(count)
    ]


class FakeTier0:
    async def facts_for_run(
        self,
        agent_run_id: str,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "fact-1",
                "kind": "file_write",
                "target": "src/example.py",
                "created_at": time.time(),
            }
        ]


class FakeProjectContexts:
    async def prompt_prefix(self, session_id: str) -> str:
        return "Project context: test project"


class FakeProvider:
    def __init__(self, *, abandoned: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.abandoned = abandoned

    async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
        self.calls.append(kwargs)
        return OpenRouterResult(
            generation_id="generation-1",
            requested_model=str(kwargs["model"]),
            resolved_model=str(kwargs["model"]),
            value={
                "behavior": ["executing", "evaluating"],
                "work_phase": "test",
                "intent": "Verify the timeline scanner",
                "claim": "",
                "user_ask": "Build the scan timeline",
                "blocked_on": "none",
                "summary": "Implemented and tested the timeline scanner.",
                "approach_status": "abandoned" if self.abandoned else "active",
                "dead_end": "The first parser approach dropped timestamps."
                if self.abandoned
                else "",
                "confidence": 0.94,
            },
            input_tokens=120,
            output_tokens=42,
            cost_usd=0.00003,
            latency_ms=12,
        )


class FailingProvider:
    async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
        raise OpenRouterError(
            "provider rejected output",
            status=502,
            generation_id="failed-generation",
            resolved_model=str(kwargs["model"]),
            input_tokens=80,
            output_tokens=12,
        )


def fake_session(run_id: str = "run-1") -> SimpleNamespace:
    record = SimpleNamespace(
        id="session-1",
        project_id="project-1",
        agent_run_id=run_id,
        agent_run_started_at=100.0,
        backend="claude",
        native_session_id="native-1",
        parser_status="ready",
        observation_stale_since=None,
        state="working",
        awaiting_reason=None,
    )
    return SimpleNamespace(record=record, transcript_path=Path("unused.jsonl"))


def config(tmp_path: Path, **overrides: Any) -> Config:
    return Config(
        data_dir=tmp_path,
        scan_timeline_enabled=True,
        scan_timeline_model=DEFAULT_SCAN_MODEL,
        automation_daily_token_budget=10_000,
        automation_daily_budget_usd=10,
        # Deliberately tiny. Scan timeline must not consult these; a regression
        # that reconnects it to the per-rule caps fails here rather than in
        # production three hours into a session.
        automation_rule_daily_token_budget=1,
        automation_rule_daily_budget_usd=0.0,
        automation_rule_hourly_call_cap=1,
        **overrides,
    )


async def build_service(
    tmp_path: Path,
    *,
    abandoned: bool = False,
    dead_end: bool = False,
    provider: Any | None = None,
    **config_overrides: Any,
) -> tuple[ScanTimelineService, AutomationStore, Any, SimpleNamespace]:
    store = AutomationStore(tmp_path / "mux.db")
    session = fake_session()
    sessions = SimpleNamespace(sessions={"session-1": session})
    provider = provider or FakeProvider(abandoned=abandoned)

    async def resolve(session_id: str) -> ScanContext | None:
        current = sessions.sessions.get(session_id)
        if not current:
            return None
        return ScanContext(
            project_id="project-1",
            project_root=str(tmp_path),
            agent_run_id=str(current.record.agent_run_id),
            daily_budget_usd=5.0,
            dead_end_memory_enabled=dead_end,
        )

    service = ScanTimelineService(
        store=store,
        tier0=FakeTier0(),
        sessions=sessions,
        events=EventBus(),
        config=config(tmp_path, **config_overrides),
        provider=provider,
        project_contexts=FakeProjectContexts(),
        resolve_context=resolve,
    )

    async def slice_build(*args: Any, **kwargs: Any) -> TranscriptSlice:
        return TranscriptSlice(
            "since_event",
            (
                {
                    "role": "user",
                    "ts": 101.0,
                    "content": [{"type": "text", "text": "Build the scan timeline"}],
                },
                {
                    "role": "assistant",
                    "ts": 102.0,
                    "content": [{"type": "text", "text": "I am implementing it."}],
                },
            ),
            120,
            30,
            False,
            "input-hash-1",
        )

    service.slices.build = slice_build  # type: ignore[method-assign]
    service.slices.build_forward = slice_build  # type: ignore[method-assign]
    return service, store, provider, sessions


@pytest.mark.asyncio
async def test_scan_is_three_gate_run_scoped_and_uses_v4_flash(tmp_path: Path) -> None:
    service, store, provider, sessions = await build_service(tmp_path)
    try:
        disabled = await service.snapshot("session-1")
        assert disabled["global_enabled"] is True
        assert disabled["project_enabled"] is True
        assert disabled["run_enabled"] is False
        assert disabled["model"] == "deepseek/deepseek-v4-flash"

        await service.set_enabled("session-1", True)
        record = await service.scan_now("session-1", "test")
        assert record is not None
        assert record["agent_run_id"] == "run-1"
        assert record["target"] == ["src/example.py"]
        assert provider.calls[0]["model"] == "deepseek/deepseek-v4-flash"

        sessions.sessions["session-1"].record.agent_run_id = "run-2"
        await service._rollover(  # noqa: SLF001 - boundary contract is the test
            MuxEvent(
                103.0,
                "session-1",
                "daemon",
                "agent_conversation_rolled",
                {
                    "previous_agent_run_id": "run-1",
                    "agent_run_id": "run-2",
                    "reason": "clear",
                },
            )
        )
        state = await service.snapshot("session-1")
        assert state["run_enabled"] is False
        assert state["boundaries"][0]["previous_run_id"] == "run-1"
        assert state["boundaries"][0]["next_run_id"] == "run-2"
        assert await store.scan_run("run-2") is None
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_dead_end_memory_requires_its_own_project_opt_in(tmp_path: Path) -> None:
    service, store, _provider, _sessions = await build_service(
        tmp_path, abandoned=True, dead_end=False
    )
    try:
        await service.set_enabled("session-1", True)
        assert await service.scan_now("session-1", "test") is not None
        assert await store.annotations(agent_run_id="run-1", tag="dead-end") == []
    finally:
        await service.stop()
        store.close()

    enabled_root = tmp_path / "enabled"
    enabled_root.mkdir()
    service, store, _provider, _sessions = await build_service(
        enabled_root, abandoned=True, dead_end=True
    )
    try:
        await service.set_enabled("session-1", True)
        assert await service.scan_now("session-1", "test") is not None
        annotations = await store.annotations(agent_run_id="run-1", tag="dead-end")
        assert [item["content"] for item in annotations] == [
            "The first parser approach dropped timestamps."
        ]
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_rehydration_rate_counts_source_expansion(tmp_path: Path) -> None:
    service, store, _provider, _sessions = await build_service(tmp_path)
    try:
        await service.set_enabled("session-1", True)
        record = await service.scan_now("session-1", "test")
        assert record is not None
        compressed = await service.record_detail("session-1", record["id"], rehydrate=False)
        assert compressed["source"] is None
        assert compressed["metrics"]["rehydration_rate"] == 0
        expanded = await service.record_detail("session-1", record["id"], rehydrate=True)
        assert len(expanded["source"]) == 2
        assert expanded["metrics"] == {
            "record_reads": 2,
            "rehydrations": 1,
            "rehydration_rate": 0.5,
        }
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_provider_failure_never_creates_a_guessed_record_and_counts_budget(
    tmp_path: Path,
) -> None:
    service, store, _provider, _sessions = await build_service(tmp_path)
    service.provider = FailingProvider()
    try:
        await service.set_enabled("session-1", True)
        with pytest.raises(OpenRouterError, match="provider rejected output"):
            await service.scan_now("session-1", "test")
        assert await store.scan_records(session_id="session-1") == []
        spend = await store.scan_project_spend("project-1")
        assert spend["tokens"] == 92
        assert spend["cost_usd"] > 0
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_ended_run_can_be_disabled_after_live_session_is_removed(tmp_path: Path) -> None:
    service, store, _provider, sessions = await build_service(tmp_path)
    try:
        await service.set_enabled("session-1", True)
        sessions.sessions.clear()
        await service._disable_run(  # noqa: SLF001 - captured exit identity is the contract
            "session-1", "run-1", "project-1"
        )
        row = await store.scan_run("run-1")
        assert row is not None
        assert row["enabled"] == 0
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_old_run_rehydration_uses_its_historical_transcript(tmp_path: Path) -> None:
    service, store, _provider, sessions = await build_service(tmp_path)
    try:
        await service.set_enabled("session-1", True)
        record = await service.scan_now("session-1", "test")
        assert record is not None
        sessions.sessions["session-1"].record.agent_run_id = "run-2"
        seen: dict[str, Any] = {}

        class FakeHistory:
            async def history_entry(self, run_id: str) -> dict[str, Any]:
                assert run_id == "run-1"
                return {
                    "transcript_path": str(tmp_path / "old-run.jsonl"),
                    "backend": "claude",
                    "native_id": "native-old",
                }

        async def historical_slice(
            path: Path, backend: str, *args: Any, **kwargs: Any
        ) -> TranscriptSlice:
            seen.update(path=path, backend=backend, native_id=kwargs.get("native_id"))
            return TranscriptSlice(
                "since_event",
                ({"role": "assistant", "ts": "1970-01-01T00:01:42Z", "content": []},),
                20,
                5,
                False,
                "old-run-hash",
            )

        service.history = FakeHistory()
        service.slices.build = historical_slice  # type: ignore[method-assign]
        detail = await service.record_detail("session-1", record["id"], rehydrate=True)
        assert len(detail["source"]) == 1
        assert seen == {
            "path": tmp_path / "old-run.jsonl",
            "backend": "claude",
            "native_id": "native-old",
        }
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_full_session_scan_chunks_oldest_first_and_reports_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store, provider, _sessions = await build_service(tmp_path)
    messages = backfill_messages(60)
    monkeypatch.setattr(
        "swe_mux.scan_timeline.parse_transcript_cached",
        lambda *args, **kwargs: messages,
    )
    try:
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        started = await service.start_backfill("session-1")
        assert started["state"] == "running"
        await service._backfill_tasks["run-1"]
        completed = (await service.snapshot("session-1"))["backfill"]
        assert completed["state"] == "completed"
        assert completed["processed_chunks"] == 2
        assert completed["created_records"] == 2
        assert completed["failed_chunks"] == 0
        assert len(provider.calls) == 2
        records = await store.scan_records(agent_run_id="run-1")
        assert [record["trigger"] for record in records] == ["full_session", "full_session"]
        assert records[0]["t0"] == 101.0
        assert records[-1]["t1"] == 160.0
    finally:
        await service.stop()
        store.close()


def test_full_session_chunks_bound_tool_input_to_a_digest_and_bound_large_text() -> None:
    messages = [
        {
            "role": "assistant",
            "ts": 1.0,
            "content": [
                {"type": "tool_use", "name": "shell", "input": "x" * 200_000},
                {"type": "text", "text": "y" * 200_000},
            ],
        }
    ]
    chunks = ScanTimelineService._backfill_chunks(messages)
    assert len(chunks) == 1
    assert chunks[0].bytes <= MAX_INPUT_BYTES
    assert chunks[0].truncated is True
    block = chunks[0].messages[0]["content"][0]
    assert block["type"] == "tool_use"
    assert block["name"] == "shell"
    # Kept, because a call's arguments are the only evidence of what it touched,
    # and bounded, because one native input can be hundreds of kilobytes.
    assert block["input"] is not None
    assert len(block["input"]) <= TOOL_INPUT_CHARS + 1


class ScriptedProvider:
    """Returns a fixed sequence of response bodies, one per call."""

    def __init__(self, values: list[Any]) -> None:
        self.values = list(values)
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
        self.calls.append(kwargs)
        value = self.values[min(len(self.calls) - 1, len(self.values) - 1)]
        return OpenRouterResult(
            generation_id=f"generation-{len(self.calls)}",
            requested_model=str(kwargs["model"]),
            resolved_model=str(kwargs["model"]),
            value=value,
            input_tokens=100,
            output_tokens=20,
            cost_usd=0.00002,
            latency_ms=5,
        )


def semantic_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "behavior": ["reasoning"],
        "work_phase": "test",
        "intent": "check the validator",
        "claim": "",
        "user_ask": "",
        "blocked_on": "none",
        "summary": "A normal record.",
        "approach_status": "active",
        "dead_end": "",
        "confidence": 0.5,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_a_repeated_behaviour_label_is_repaired_rather_than_rejected(
    tmp_path: Path,
) -> None:
    """The field the field failures were actually about.

    ``maxItems``/``uniqueItems`` are the schema keywords structured-output
    backends most often ignore, and the old validator checked length before
    deduplication - so a model repeating a label lost a whole timeline record.
    """
    provider = ScriptedProvider(
        [semantic_body(behavior=["reasoning", "reasoning", "executing", "reasoning", "planning",
                                 "planning", "grounding", "grounding"])]
    )
    service, store, _provider, _sessions = await build_service(tmp_path, provider=provider)
    try:
        await service.set_enabled("session-1", True)
        record = await service.scan_now("session-1", "test")
        assert record is not None
        assert record["behavior"] == ["reasoning", "executing", "planning", "grounding"]
        assert "behavior repeated a label" in record["repairs"]
        assert len(provider.calls) == 1, "a repairable response must not be retried"
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_off_enum_and_overlong_fields_are_coerced_with_an_audit_trail(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(
        [
            semantic_body(
                behavior="reasoning",
                work_phase="refactoring",
                blocked_on="waiting",
                approach_status="in_progress",
                summary="s" * 900,
                confidence=4.0,
            )
        ]
    )
    service, store, _provider, _sessions = await build_service(tmp_path, provider=provider)
    try:
        await service.set_enabled("session-1", True)
        record = await service.scan_now("session-1", "test")
        assert record is not None
        assert record["behavior"] == ["reasoning"]
        assert record["work_phase"] == "unknown"
        assert record["blocked_on"] == "none"
        assert record["approach_status"] == "unknown"
        assert len(record["summary"]) == 600
        assert record["confidence"] == 1.0
        assert len(record["repairs"]) >= 4
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_an_unusable_response_is_retried_once_and_then_recorded_in_full(
    tmp_path: Path,
) -> None:
    """A billed call that produced no record must still be visible and charged."""
    provider = ScriptedProvider([{"behavior": [], "summary": "", "intent": ""}])
    service, store, _provider, _sessions = await build_service(tmp_path, provider=provider)
    try:
        await service.set_enabled("session-1", True)
        with pytest.raises(ValueError, match="no usable semantic content"):
            await service.scan_now("session-1", "test")
        assert len(provider.calls) == 2, "exactly one retry"
        assert service.retries == 1
        calls = await store.observer_calls()
        assert [call["status"] for call in calls] == ["failed", "failed"]
        for call in calls:
            assert call["input_tokens"] == 100
            assert call["output_tokens"] == 20
            assert call["resolved_model"] == DEFAULT_SCAN_MODEL
            assert call["generation_id"]
            assert '"summary":""' in str(call["response_excerpt"])
        spend = await store.scan_project_spend("project-1")
        assert spend["tokens"] == 240, "both billed attempts reach the ledger"
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_the_retry_recovers_a_transient_bad_sample(tmp_path: Path) -> None:
    provider = ScriptedProvider([{"behavior": [], "summary": ""}, semantic_body()])
    service, store, _provider, _sessions = await build_service(tmp_path, provider=provider)
    try:
        await service.set_enabled("session-1", True)
        record = await service.scan_now("session-1", "test")
        assert record is not None
        assert record["summary"] == "A normal record."
        assert len(provider.calls) == 2
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_scan_ignores_the_per_rule_caps_and_reports_the_binding_gate(
    tmp_path: Path,
) -> None:
    """The failure that produced the reported gap, as a contract.

    The per-rule caps in `config()` are set to 1 token, zero dollars and one
    call an hour. A scanner that consults them cannot produce a record at all.
    """
    service, store, _provider, _sessions = await build_service(tmp_path)
    try:
        await service.set_enabled("session-1", True)
        assert await service.scan_now("session-1", "test") is not None
        state = await service.snapshot("session-1")
        gates = {gate["id"]: gate for gate in state["gates"]}
        assert gates["scan_daily_tokens"]["limit"] == 3_000_000
        assert gates["scan_daily_tokens"]["used"] > 0
        assert gates["scan_hourly_calls"]["limit"] == 600
        assert gates["project_daily_usd"]["limit"] == 5.0
        assert state["skip_reason"] is None
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_an_exhausted_scan_budget_names_itself_in_the_snapshot(tmp_path: Path) -> None:
    service, store, _provider, _sessions = await build_service(
        tmp_path, scan_timeline_daily_token_budget=512
    )
    try:
        await service.set_enabled("session-1", True)
        assert await service.scan_now("session-1", "test") is None
        state = await service.snapshot("session-1")
        assert state["skip_reason"] == "the daily Scan timeline token budget is exhausted"
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_a_bounded_window_leaves_no_gap_and_schedules_its_own_catch_up(
    tmp_path: Path,
) -> None:
    """The silent-data-loss contract.

    The old reader took the NEWEST window and trimmed from the front, then
    advanced the cursor to the end of it - so everything trimmed sat before the
    cursor and was never scanned again. The forward reader consumes oldest
    first and reports the remainder, so consecutive scans cover every message.
    """
    service, store, provider, _sessions = await build_service(tmp_path)
    seen: list[float] = []
    transcript = backfill_messages(9)

    async def forward(*args: Any, **kwargs: Any) -> TranscriptSlice:
        since = float(kwargs["since_ts"])
        pending = [item for item in transcript if float(item["ts"]) > since]
        window = pending[:3]
        seen.extend(float(item["ts"]) for item in window)
        return TranscriptSlice(
            "since_event_forward",
            tuple(window),
            120,
            30,
            bool(pending[3:]),
            f"hash-{since}",
            remaining=len(pending[3:]),
        )

    service.slices.build_forward = forward  # type: ignore[method-assign]
    try:
        # Straight to the store: `set_enabled` also schedules a background scan,
        # which would race this test's explicit, counted calls.
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        for _ in range(3):
            record = await service.scan_now("session-1", "test")
            assert record is not None
        assert seen == [item["ts"] for item in transcript], "every message scanned exactly once"
        assert await service.scan_now("session-1", "test") is None
        assert service._skip_reasons["session-1"] == (
            "no unscanned transcript messages are available"
        )
        first = (await store.scan_records(agent_run_id="run-1"))[0]
        assert first["coverage"]["remaining"] == 6
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_every_entry_point_chains_its_own_catch_up(tmp_path: Path) -> None:
    """The chain belongs to `scan_now`, not to the debounce wrapper.

    It lived in the wrapper first, so a manual "Scan now" over a long unscanned
    stretch wrote exactly one window and then waited up to three minutes for
    the heartbeat, which reads as a button that half worked.
    """
    service, store, _provider, _sessions = await build_service(tmp_path)
    left = {"remaining": 5}

    async def forward(*args: Any, **kwargs: Any) -> TranscriptSlice:
        left["remaining"] = max(0, left["remaining"] - 1)
        return TranscriptSlice(
            "since_event_forward",
            ({"role": "user", "ts": 200.0 + left["remaining"], "content": []},),
            10,
            5,
            left["remaining"] > 0,
            f"hash-{left['remaining']}",
            remaining=left["remaining"],
        )

    service.slices.build_forward = forward  # type: ignore[method-assign]
    try:
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        assert await service.scan_now("session-1", "manual") is not None
        follow_up = service._debounce.get("session-1")
        assert follow_up is not None, "a remainder must schedule its own successor"
        assert service._catchup_depth["session-1"] == 1
        follow_up.cancel()

        # The chain is bounded, and a window that leaves nothing clears it.
        service._catchup_depth["session-1"] = CATCHUP_CHAIN_LIMIT - 1
        service._debounce.pop("session-1", None)
        assert await service.scan_now("session-1", "manual") is not None
        assert service._debounce.get("session-1") is None
        assert "session-1" not in service._catchup_depth
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_full_session_scan_survives_one_bad_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One unusable window must not abandon the rest of the conversation."""

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
            self.calls += 1
            # Attempts 1 and 2 are the first chunk and its retry.
            body = {"behavior": []} if self.calls <= 2 else semantic_body()
            return OpenRouterResult(
                generation_id=f"g{self.calls}",
                requested_model=str(kwargs["model"]),
                resolved_model=str(kwargs["model"]),
                value=body,
                input_tokens=50,
                output_tokens=10,
                cost_usd=0.00001,
                latency_ms=1,
            )

    provider = FlakyProvider()
    service, store, _provider, _sessions = await build_service(tmp_path, provider=provider)
    monkeypatch.setattr(
        "swe_mux.scan_timeline.parse_transcript_cached",
        lambda *args, **kwargs: backfill_messages(120),
    )
    try:
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        await service.start_backfill("session-1")
        await service._backfill_tasks["run-1"]
        state = (await service.snapshot("session-1"))["backfill"]
        assert state["total_chunks"] == 3
        assert state["processed_chunks"] == 3, "the job did not stop at the bad chunk"
        assert state["failed_chunks"] == 1
        assert state["created_records"] == 2
        assert state["state"] == "completed_with_gaps"
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_full_session_scan_stops_on_a_terminal_budget_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, store, _provider, _sessions = await build_service(
        tmp_path, scan_timeline_run_token_budget=2_000
    )
    monkeypatch.setattr(
        "swe_mux.scan_timeline.parse_transcript_cached",
        lambda *args, **kwargs: backfill_messages(120),
    )
    try:
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        await service.start_backfill("session-1")
        await service._backfill_tasks["run-1"]
        state = (await service.snapshot("session-1"))["backfill"]
        assert state["state"] == "partial"
        assert state["reason"] == "the run Scan timeline token budget is exhausted"
        assert state["total_chunks"] == 3
        assert state["processed_chunks"] < state["total_chunks"]
        assert state["created_records"] == 0
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_full_session_scan_state_outlives_the_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job held only in process memory reported `idle` after a restart."""
    service, store, _provider, _sessions = await build_service(tmp_path)
    monkeypatch.setattr(
        "swe_mux.scan_timeline.parse_transcript_cached",
        lambda *args, **kwargs: backfill_messages(60),
    )
    try:
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        await service.start_backfill("session-1")
        await service._backfill_tasks["run-1"]
        stored = await store.scan_backfill("run-1")
        assert stored is not None
        assert stored["state"] == "completed"
        assert stored["created_records"] == 2

        # A fresh service over the same database is what a restart looks like.
        service._backfills.clear()
        assert (await service.snapshot("session-1"))["backfill"]["state"] == "completed"

        await store.save_scan_backfill({**stored, "state": "running", "completed_at": None})
        assert await store.interrupt_running_scan_backfills() == 1
        recovered = await store.scan_backfill("run-1")
        assert recovered is not None
        assert recovered["state"] == "partial"
        assert recovered["reason"]
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_a_full_session_scan_can_be_stopped_and_keeps_its_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    class SlowProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete_json(self, **kwargs: Any) -> OpenRouterResult:
            self.calls += 1
            if self.calls > 1:
                await asyncio.sleep(30)
            return OpenRouterResult(
                generation_id=f"g{self.calls}",
                requested_model=str(kwargs["model"]),
                resolved_model=str(kwargs["model"]),
                value=semantic_body(),
                input_tokens=50,
                output_tokens=10,
                cost_usd=0.00001,
                latency_ms=1,
            )

    service, store, _provider, _sessions = await build_service(
        tmp_path, provider=SlowProvider()
    )
    monkeypatch.setattr(
        "swe_mux.scan_timeline.parse_transcript_cached",
        lambda *args, **kwargs: backfill_messages(120),
    )
    try:
        await store.set_scan_run_enabled(
            agent_run_id="run-1",
            session_id="session-1",
            project_id="project-1",
            enabled=True,
        )
        await service.start_backfill("session-1")
        for _ in range(200):
            await asyncio.sleep(0.01)
            if (await store.scan_backfill("run-1") or {}).get("created_records"):
                break
        state = await service.cancel_backfill("session-1")
        assert state["state"] == "partial"
        assert state["reason"] == "stopped from the Timeline tab"
        assert state["created_records"] == 1
        assert len(await store.scan_records(agent_run_id="run-1")) == 1
    finally:
        await service.stop()
        store.close()


def test_novelty_is_mechanical_and_run_local() -> None:
    first = {"summary": "fixed parser timestamps", "work_phase": "debug"}
    same = {"summary": "fixed parser timestamps", "work_phase": "debug"}
    different = {"summary": "reviewed mobile layout", "work_phase": "review"}
    assert mechanical_novelty(first, []) == 1.0
    assert mechanical_novelty(same, [first]) == 0.0
    assert mechanical_novelty(different, [first]) == 1.0
