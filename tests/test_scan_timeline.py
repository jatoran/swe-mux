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
    DEFAULT_SCAN_MODEL,
    ScanContext,
    ScanTimelineService,
    mechanical_novelty,
)


class FakeTier0:
    async def facts_for_run(
        self, agent_run_id: str, *, since: float | None = None, limit: int = 2000
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "fact-1",
                "kind": "file_write",
                "target": "src/example.py",
                "created_at": time.time(),
            }
        ]


class FakeCards:
    async def prompt_prefix(self, session_id: str) -> str:
        return "Project card: test project"


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


def config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path,
        scan_timeline_enabled=True,
        scan_timeline_model=DEFAULT_SCAN_MODEL,
        automation_daily_token_budget=10_000,
        automation_daily_budget_usd=10,
        automation_rule_daily_token_budget=10_000,
        automation_rule_daily_budget_usd=10,
    )


async def build_service(
    tmp_path: Path, *, abandoned: bool = False, dead_end: bool = False
) -> tuple[ScanTimelineService, AutomationStore, FakeProvider, SimpleNamespace]:
    store = AutomationStore(tmp_path / "mux.db")
    session = fake_session()
    sessions = SimpleNamespace(sessions={"session-1": session})
    provider = FakeProvider(abandoned=abandoned)

    async def resolve(session_id: str) -> ScanContext | None:
        current = sessions.sessions.get(session_id)
        if not current:
            return None
        return ScanContext(
            project_id="project-1",
            project_root=str(tmp_path),
            agent_run_id=str(current.record.agent_run_id),
            daily_budget_usd=0.10,
            dead_end_memory_enabled=dead_end,
        )

    service = ScanTimelineService(
        store=store,
        tier0=FakeTier0(),
        sessions=sessions,
        events=EventBus(),
        config=config(tmp_path),
        provider=provider,
        project_cards=FakeCards(),
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


def test_novelty_is_mechanical_and_run_local() -> None:
    first = {"summary": "fixed parser timestamps", "work_phase": "debug"}
    same = {"summary": "fixed parser timestamps", "work_phase": "debug"}
    different = {"summary": "reviewed mobile layout", "work_phase": "review"}
    assert mechanical_novelty(first, []) == 1.0
    assert mechanical_novelty(same, [first]) == 0.0
    assert mechanical_novelty(different, [first]) == 1.0
