from __future__ import annotations

import json
import time
import uuid
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from swe_mux.config import Config
from swe_mux.event_bus import EventBus
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.operational_telemetry import (
    OperationalTelemetryStore,
    command_hash,
    process_identity,
    scan_native_telemetry,
)
from swe_mux.processes import OwnedProcess, ProcessInspector
from swe_mux.provider_accounts import ProviderAccountManager
from swe_mux.server import create_app

TELEMETRY_FIXTURES = Path(__file__).parent / "fixtures" / "telemetry" / "v1"


@pytest.fixture
def phase2_path() -> Path:
    path = Path(__file__).parent / f".phase2-{uuid.uuid4().hex}.db"
    yield path
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        path.with_suffix(".claude.jsonl"),
        path.with_suffix(".codex.jsonl"),
    ):
        with suppress(OSError):
            candidate.unlink()


class FakeLiveSession:
    def __init__(self, backend: str = "codex") -> None:
        self.record = SessionRecord(
            "session-a",
            "run",
            "project-a",
            backend,
            "native-a",
            ".",
            f"{backend}.exe",
            [],
            agent_run_id="session-a",
            model="test-model",
            state="idle",
        )
        self.updates = 0

    def publish_update(self) -> None:
        self.updates += 1


async def test_process_evidence_survives_restart_without_persisting_command(
    phase2_path: Path,
) -> None:
    path = phase2_path
    store = OperationalTelemetryStore(path)
    started = 1234.5
    await store.record_process_observations(
        [
            {
                "pid": 44,
                "parent_pid": 10,
                "session_id": "session-a",
                "agent_run_id": "run-a",
                "project_id": "project-a",
                "executable": "server.exe",
                "command": "server.exe --secret never-store-this",
                "started_at": started,
                "parent_lineage": [{"pid": 10, "creation_time": 1000.0}],
                "job_assignment": "nested_session_job",
                "evidence_state": "suspected_orphan",
                "evidence_reason": "survived_root_session_grace_with_matching_fingerprint",
                "confidence": "high",
                "first_seen": 1200.0,
                "last_seen": 1300.0,
            }
        ]
    )
    store.close()

    reopened = OperationalTelemetryStore(path)
    rows = await reopened.process_candidates()
    assert rows[0]["identity_id"] == process_identity("session-a", 44, started)
    assert rows[0]["command_hash"] == command_hash("server.exe --secret never-store-this")
    assert rows[0]["state"] == "suspected_orphan"
    assert rows[0]["parent_lineage"] == [{"pid": 10, "creation_time": 1000.0}]
    columns = {
        row[1] for row in reopened._db.execute("PRAGMA table_info(process_evidence)").fetchall()
    }
    assert "command" not in columns
    reopened.close()


async def test_unexpected_reset_requires_two_fresh_samples_and_auth_transitions_suppress(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    first = 1_800_000_000.0

    async def sample(
        at: float,
        weekly: float,
        *,
        active: bool = True,
        status: str = "ready",
        auth: str = "saved",
    ) -> dict[str, Any]:
        return await store.record_quota_sample(
            provider="codex",
            account_id="account-a",
            quota={
                "session": {"used_percent": weekly / 2, "resets_at": first + 7200},
                "weekly": {"used_percent": weekly, "resets_at": first + 7 * 86400},
                "source": "fixture",
                "status": status,
            },
            sampled_at=at,
            account_active=active,
            auth_state=auth,
        )

    await sample(first, 71)
    candidate = await sample(first + 900, 2)
    weekly = next(item for item in candidate["reset_events"] if item["window"] == "weekly")
    assert weekly["classification"] == "unexpected"
    assert weekly["confirmed"] == 0

    confirmation = await sample(first + 1800, 2.5)
    confirmed = next(
        item
        for item in confirmation["reset_events"]
        if item["window"] == "weekly" and item["id"] == weekly["id"]
    )
    assert confirmed["confirmed"] == 1
    assert confirmed["confidence"] == "high"

    await sample(first + 2700, 60, active=True)
    switched = await sample(first + 3600, 1, active=False)
    suppressed = next(item for item in switched["reset_events"] if item["window"] == "weekly")
    assert suppressed["suppression_reason"] == "account_or_auth_transition"
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    latest = await reopened.latest_quota_by_account()
    assert latest["account-a"]["weekly"]["used_percent"] == 1
    reset_summary = await reopened.reset_summary()
    assert reset_summary["count"] >= 1
    durable = await reopened.snapshot(account_id="account-a")
    durable_weekly = next(
        item for item in durable["quota"]["resets"] if item["id"] == weekly["id"]
    )
    assert durable_weekly["confirmed"] == 1
    reopened.close()


async def test_scheduled_reset_uses_expected_time_tolerance(phase2_path: Path) -> None:
    store = OperationalTelemetryStore(phase2_path)
    expected = 1_800_010_000.0
    base = expected - 600
    for at, used in ((base, 90.0), (expected + 30, 1.0)):
        result = await store.record_quota_sample(
            provider="claude",
            account_id="account-a",
            quota={
                "session": {"used_percent": used, "resets_at": expected},
                "weekly": None,
                "source": "fixture",
                "status": "ready",
            },
            sampled_at=at,
            account_active=True,
            auth_state="saved",
        )
    reset = next(item for item in result["reset_events"] if item["window"] == "session")
    assert reset["classification"] == "scheduled"
    assert reset["confirmed"] == 1
    store.close()


async def test_scheduled_reset_observed_after_polling_gap_is_not_unexpected(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    expected = 1_800_010_000.0
    before = expected - 5 * 60
    await store.record_quota_sample(
        provider="claude",
        account_id="late-poll",
        quota={
            "session": {"used_percent": 92.0, "resets_at": expected},
            "weekly": None,
            "source": "fixture",
            "status": "ready",
        },
        sampled_at=before,
        account_active=False,
        auth_state="saved",
    )
    result = await store.record_quota_sample(
        provider="claude",
        account_id="late-poll",
        quota={
            "session": {"used_percent": 0.0, "resets_at": None},
            "weekly": None,
            "source": "fixture",
            "status": "ready",
        },
        sampled_at=expected + 38 * 60,
        account_active=False,
        auth_state="saved",
    )

    reset = next(item for item in result["reset_events"] if item["window"] == "session")
    assert reset["classification"] == "scheduled"
    assert reset["confirmed"] == 1
    assert (await store.reset_summary())["count"] == 0
    store.close()


async def test_unexpected_reset_requires_future_timer_large_drop_and_low_floor(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    base = 1_800_000_000.0

    async def pair(account: str, before: float, after: float, reset_at: float | None) -> str:
        for offset, value in ((0, before), (900, after)):
            result = await store.record_quota_sample(
                provider="codex",
                account_id=account,
                quota={
                    "session": {"used_percent": value, "resets_at": reset_at},
                    "weekly": None,
                    "source": "fixture",
                    "status": "ready",
                },
                sampled_at=base + offset,
                account_active=True,
                auth_state="saved",
            )
        return str(result["reset_events"][0]["suppression_reason"])

    assert await pair("missing-timer", 80, 0, None) == "missing_expected_reset"
    assert await pair("near-timer", 80, 0, base + 45 * 60) == "too_close_to_expected_reset"
    assert await pair("small-drop", 25, 10, base + 86400) == "movement_below_reset_threshold"
    assert await pair("high-floor", 80, 40, base + 86400) == "value_above_reset_floor"
    assert (await store.reset_summary())["count"] == 0
    store.close()


async def test_unexpected_reset_confirmation_requires_independent_pre_boundary_sample(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    base = 1_800_000_000.0
    expected = base + 2 * 3600

    async def put(at: float, used: float) -> dict[str, Any]:
        return await store.record_quota_sample(
            provider="codex",
            account_id="confirmation",
            quota={
                "session": {"used_percent": used, "resets_at": expected},
                "weekly": None,
                "source": "fixture",
                "status": "ready",
            },
            sampled_at=at,
            account_active=True,
            auth_state="saved",
        )

    await put(base, 75)
    candidate = await put(base + 60, 2)
    reset = candidate["reset_events"][0]
    assert reset["classification"] == "unexpected" and reset["confirmed"] == 0

    too_fast = await put(base + 120, 2)
    assert not any(item.get("confirmed") for item in too_fast["reset_events"])
    confirmed = await put(base + 6 * 60, 2)
    matching = next(item for item in confirmed["reset_events"] if item["id"] == reset["id"])
    assert matching["confirmed"] == 1
    store.close()


async def test_store_reclassifies_legacy_late_observed_reset(phase2_path: Path) -> None:
    expected = 1_800_010_000.0
    store = OperationalTelemetryStore(phase2_path)
    await store.record_quota_sample(
        provider="claude",
        account_id="legacy",
        quota={
            "session": {"used_percent": 92.0, "resets_at": expected},
            "weekly": None,
            "source": "fixture",
            "status": "ready",
        },
        sampled_at=expected - 5 * 60,
        account_active=False,
        auth_state="saved",
    )
    await store.record_quota_sample(
        provider="claude",
        account_id="legacy",
        quota={
            "session": {"used_percent": 0.0, "resets_at": None},
            "weekly": None,
            "source": "fixture",
            "status": "ready",
        },
        sampled_at=expected + 38 * 60,
        account_active=False,
        auth_state="saved",
    )
    store._db.execute(
        "UPDATE quota_reset_events SET classification='unexpected',confirmed=1,confidence='high'"
    )
    store._db.commit()
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    snapshot = await reopened.snapshot(account_id="legacy")
    reset = snapshot["quota"]["resets"][0]
    assert reset["classification"] == "scheduled"
    assert reset["confirmed"] == 1
    assert (await reopened.reset_summary())["count"] == 0
    reopened.close()


async def test_rounding_lag_external_activity_and_uncertain_movements_remain_explicit(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    base = 1_800_000_000.0

    async def put(at: float, used: float, status: str = "ready") -> dict[str, Any]:
        return await store.record_quota_sample(
            provider="claude",
            account_id="shared",
            quota={
                "session": None,
                "weekly": {"used_percent": used, "resets_at": base + 7 * 86400},
                "status": status,
                "source": "rounded-fixture",
            },
            sampled_at=at,
            account_active=True,
            auth_state="saved",
        )

    await put(base, 10.0)
    await put(base + 3600, 10.4)
    small_drop = await put(base + 7200, 10.1)
    uncertain = next(item for item in small_drop["reset_events"] if item["window"] == "weekly")
    assert uncertain["classification"] == "uncertain"
    assert uncertain["suppression_reason"] == "movement_below_reset_threshold"
    # Out-of-order evidence is stored but never treated as a causal reset sequence.
    out_of_order = await put(base + 100, 1.0)
    assert out_of_order["reset_events"] == []
    snapshot = await store.snapshot()
    rounded = next(
        item for item in snapshot["quota"]["samples"] if item["weekly"]["used_percent"] == 10.4
    )
    assert rounded["raw_precision"] == 1
    attribution = snapshot["quota"]["attributions"][-1]
    assert attribution["correlated_estimate"] == 0
    assert attribution["external_estimate"] == pytest.approx(0.4)
    assert "large_sample_gap" in attribution["caveats"]
    assert "no_mux_activity_in_interval" in attribution["caveats"]
    store.close()


async def test_probabilistic_attribution_preserves_external_remainder_and_ambiguity(
    phase2_path: Path,
) -> None:
    path = phase2_path
    history = HistoryIndex(path)
    events = EventBus(history.append_event)
    for identity in ("session-a", "session-b"):
        record = SessionRecord(
            identity,
            identity,
            "project-a",
            "codex",
            identity,
            ".",
            "codex.exe",
            [],
            tokens_in=100 if identity == "session-a" else 300,
            tokens_out=0,
            agent_run_id=identity,
        )
        await history.session_started(record, None)
        await events.emit("turn_started", session_id=identity, source="fixture", scope="root")

    store = OperationalTelemetryStore(path)
    base = time.time() - 60
    await store.record_quota_sample(
        provider="codex",
        account_id="account-a",
        quota={"session": None, "weekly": {"used_percent": 10}, "status": "ready"},
        sampled_at=base,
        account_active=True,
        auth_state="saved",
    )
    # Events must fall inside the sampled interval.
    await events.emit("tool_use", session_id="session-a", source="fixture", tool="shell")
    await events.emit("tool_use", session_id="session-b", source="fixture", tool="shell")
    await store.record_quota_sample(
        provider="codex",
        account_id="account-a",
        quota={"session": None, "weekly": {"used_percent": 14}, "status": "ready"},
        sampled_at=time.time() + 1,
        account_active=True,
        auth_state="saved",
    )
    store.close()
    store = OperationalTelemetryStore(path)
    snapshot = await store.snapshot()
    item = snapshot["quota"]["attributions"][0]
    assert item["quota_delta"] == 4
    assert item["correlated_low"] == 0
    assert item["correlated_high"] == 4
    assert item["external_low"] == 0
    assert item["external_high"] == 4
    assert item["concurrent_sessions"] == 2
    assert [round(row["quota_percent_estimate"], 2) for row in item["allocations"]] == [1, 3]
    assert "shared_account_identity_unprovable" in item["caveats"]
    store.close()
    history.close()


async def test_explicit_compaction_tool_and_skill_events_are_durable(
    phase2_path: Path,
) -> None:
    path = phase2_path
    history = HistoryIndex(path)
    live = FakeLiveSession("claude")
    await history.session_started(live.record, None)
    sessions = SimpleNamespace(sessions={live.record.id: live})
    events = EventBus(history.append_event)
    store = OperationalTelemetryStore(path)
    store.start(events, sessions=sessions, history=history)
    await events.emit(
        "tool_use",
        session_id="session-a",
        source="transcript",
        scope="root",
        backend="claude",
        tool="Bash",
        call_id="call-a",
        parser_version="2",
    )
    await events.emit(
        "skill_invoked",
        session_id="session-a",
        source="transcript",
        scope="root",
        backend="claude",
        tool="Skill",
        call_id="call-b",
        skill="review-code",
        parser_version="2",
    )
    await events.emit(
        "context_compacted",
        session_id="session-a",
        source="transcript",
        scope="root",
        backend="claude",
        capability="explicit_native",
        confidence="high",
        parser_version="2",
    )
    assert store._event_queue is not None
    await store._event_queue.join()
    snapshot = await store.snapshot()
    assert snapshot["tools"]["metrics"][0]["raw_tool"] in {"Bash", "Skill"}
    assert snapshot["tools"]["metrics"][0]["session_id"] == "session-a"
    assert snapshot["tools"]["parser_versions"] == {
        "claude": "claude-phase2-v1",
        "codex": "codex-phase2-v1",
    }
    assert snapshot["tools"]["skills"][0]["explicit_skill"] == "review-code"
    assert snapshot["compactions"][0]["count"] == 1
    assert live.record.compaction_count == 1
    row = await history.history_entry("session-a")
    assert row and row["compaction_count"] == 1
    await store.stop()
    store.close()

    reopened = OperationalTelemetryStore(path)
    durable = await reopened.snapshot()
    assert durable["tools"]["metrics"]
    assert durable["tools"]["skills"][0]["explicit_skill"] == "review-code"
    assert durable["compactions"][0]["count"] == 1
    reopened.close()
    history.close()


async def test_retention_compacts_old_quota_samples_and_bounds_process_evidence(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path, retention_days=1, process_retention_days=1)
    old = time.time() - 3 * 86400
    for offset, used in ((0, 20.0), (600, 25.0)):
        await store.record_quota_sample(
            provider="codex",
            account_id="account-a",
            quota={"session": None, "weekly": {"used_percent": used}, "status": "ready"},
            sampled_at=old + offset,
            account_active=True,
            auth_state="saved",
        )
    await store.record_process_observations(
        [
            {
                "pid": 1,
                "session_id": "old-session",
                "started_at": old,
                "exited_at": old + 5,
                "last_seen": old,
                "evidence_state": "exited",
            },
            {
                "pid": 2,
                "session_id": "live-session",
                "started_at": old,
                "last_seen": old,
                "evidence_state": "active",
            },
        ]
    )
    deleted = await store.prune(process_retention_days=1)
    assert deleted == {"quota_samples": 2, "processes": 1}
    snapshot = await store.snapshot()
    assert snapshot["quota"]["samples"] == []
    assert snapshot["quota"]["rollups"][0]["samples"] == 2
    candidates = await store.process_candidates()
    assert [item["pid"] for item in candidates] == [2]
    store.close()


async def test_root_turn_quota_refresh_is_selected_account_only_and_globally_rate_limited(
    phase2_path: Path,
) -> None:
    events = EventBus()
    session = FakeLiveSession("codex")
    sessions = SimpleNamespace(sessions={session.record.id: session})
    manager = ProviderAccountManager(
        phase2_path.with_suffix(".data"),
        events,
        home=phase2_path.with_suffix(".home"),
        sessions=sessions,
        turn_refresh_enabled=True,
        turn_refresh_min_seconds=60,
        poll_seconds=3600,
    )
    manager._manifest["accounts"] = [{"id": "account-a", "provider": "codex", "label": "A"}]
    manager._manifest["selected"]["codex"] = "account-a"
    manager.refresh = AsyncMock(return_value={})  # type: ignore[method-assign]
    manager.start()
    await events.emit(
        "turn_ended",
        session_id=session.record.id,
        source="fixture",
        scope="subagent",
    )
    await events.emit(
        "turn_ended",
        session_id=session.record.id,
        source="fixture",
        scope="root",
    )
    assert manager._event_queue is not None
    await manager._event_queue.join()
    manager.refresh.assert_awaited_once_with("account-a")
    await events.emit(
        "turn_ended",
        session_id=session.record.id,
        source="fixture",
        scope="root",
    )
    await manager._event_queue.join()
    assert manager.refresh.await_count == 1
    await manager.stop()


def test_operational_telemetry_route_is_registered(phase2_path: Path) -> None:
    app = create_app(Config(data_dir=phase2_path.with_suffix(".data")))
    routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
    assert ("GET", "/api/telemetry/operational") in routes


def test_native_transcript_scanners_count_only_explicit_skills_and_compactions(
    phase2_path: Path,
) -> None:
    claude = phase2_path.with_suffix(".claude.jsonl")
    claude.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "skill-a",
                                "name": "Skill",
                                "input": {"skill": "review-code"},
                            }
                        ]
                    },
                },
                {"type": "system", "subtype": "compact_boundary"},
                {"type": "user", "message": {"content": "mentions review-code only"}},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    scan = scan_native_telemetry(claude, "claude", "run-a", "project-a", None)
    assert [item["explicit_skill"] for item in scan["tools"]] == ["review-code"]
    assert len(scan["compactions"]) == 1


@pytest.mark.parametrize(
    "manifest_path", sorted(TELEMETRY_FIXTURES.glob("*.json")), ids=lambda path: path.stem
)
def test_versioned_phase2_provider_telemetry_fixtures(
    phase2_path: Path, manifest_path: Path
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    transcript = phase2_path.with_suffix(f".{manifest['backend']}.jsonl")
    transcript.write_text(
        "\n".join(json.dumps(record) for record in manifest["records"]) + "\n",
        encoding="utf-8",
    )
    scan = scan_native_telemetry(
        transcript, manifest["backend"], f"fixture-{manifest['backend']}", None, None
    )
    expected = manifest["expected"]
    assert len(scan["tools"]) == expected["tools"]
    assert sum(bool(item.get("explicit_skill")) for item in scan["tools"]) == expected["skills"]
    assert len(scan["compactions"]) == expected["compactions"]
    assert scan["unknown"] == expected["unknown"]
    if "errors" in expected:
        assert sum(item.get("success") == 0 for item in scan["tools"]) == expected["errors"]

    codex = phase2_path.with_suffix(".codex.jsonl")
    codex.write_text(
        json.dumps({"type": "compacted", "payload": {"type": "context_compacted"}}) + "\n",
        encoding="utf-8",
    )
    scan = scan_native_telemetry(codex, "codex", "run-b", "project-a", None)
    assert len(scan["compactions"]) == 1


def test_pid_reuse_marks_restored_fingerprint_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    from swe_mux import processes

    class Reused:
        def create_time(self) -> float:
            return 20.0

    fake_psutil = SimpleNamespace(
        Process=lambda _pid: Reused(),
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
        AccessDenied=type("AccessDenied", (Exception,), {}),
    )
    monkeypatch.setattr(processes, "psutil", fake_psutil)
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    item = OwnedProcess(
        55,
        10,
        "old-session",
        "server",
        "",
        10.0,
        None,
        0,
        0,
        [],
        [],
        identity_id=process_identity("old-session", 55, 10.0),
    )
    inspector.owned[(55, 10.0)] = item
    inspector._revalidate_unseen(set(), {}, time.time(), True)
    assert item.evidence_state == "stale"
    assert item.exit_evidence == "pid_reused"
    assert item.startup_revalidated is True


def test_daemon_restart_revalidates_matching_escape_as_suspected_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    class Matching:
        pid = 66

        def create_time(self) -> float:
            return 10.0

        def oneshot(self) -> Matching:
            return self

        def __enter__(self) -> Matching:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return "escaped.exe"

        def cmdline(self) -> list[str]:
            return ["escaped.exe", "--serve"]

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1024)

    fake_psutil = SimpleNamespace(
        Process=lambda _pid: Matching(),
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
        AccessDenied=type("AccessDenied", (Exception,), {}),
        CONN_LISTEN="LISTEN",
        CONN_ESTABLISHED="ESTABLISHED",
    )
    monkeypatch.setattr(processes, "psutil", fake_psutil)
    inspector = ProcessInspector(
        cast(Any, SimpleNamespace(sessions={})), EventBus(), orphan_grace_seconds=10
    )
    item = OwnedProcess(
        66,
        10,
        "dead-session",
        "escaped.exe",
        "",
        10.0,
        None,
        0,
        0,
        [],
        [],
        last_seen=100.0,
        first_seen=90.0,
    )
    inspector.owned[(66, 10.0)] = item
    inspector._revalidate_unseen(set(), {}, 120.0, True)
    assert item.evidence_state == "suspected_orphan"
    assert item.confidence in {"high", "medium"}
    assert item.startup_revalidated is True


def test_inaccessible_startup_fingerprint_is_stale_not_reattached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    AccessDenied = type("AccessDenied", (Exception,), {})
    fake_psutil = SimpleNamespace(
        Process=lambda _pid: (_ for _ in ()).throw(AccessDenied()),
        NoSuchProcess=type("NoSuchProcess", (Exception,), {}),
        AccessDenied=AccessDenied,
    )
    monkeypatch.setattr(processes, "psutil", fake_psutil)
    inspector = ProcessInspector(cast(Any, SimpleNamespace(sessions={})), EventBus())
    item = OwnedProcess(77, 1, "old", "x", "", 7.0, None, 0, 0, [], [])
    inspector.owned[(77, 7.0)] = item
    inspector._revalidate_unseen(set(), {}, 100.0, True)
    assert item.evidence_state == "stale"
    assert item.evidence_reason == "startup_fingerprint_unverifiable"
    assert item.confidence == "low"


def test_delayed_exit_moves_escape_through_grace_then_records_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import processes

    NoSuchProcess = type("NoSuchProcess", (Exception,), {})

    class Matching:
        pid = 88

        def create_time(self) -> float:
            return 8.0

        def oneshot(self) -> Matching:
            return self

        def __enter__(self) -> Matching:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ppid(self) -> int:
            return 1

        def name(self) -> str:
            return "child.exe"

        def cmdline(self) -> list[str]:
            return ["child.exe"]

        def memory_info(self) -> Any:
            return SimpleNamespace(rss=1)

    current: list[Any] = [Matching()]
    fake_psutil = SimpleNamespace(
        Process=lambda _pid: current[0] if current else (_ for _ in ()).throw(NoSuchProcess()),
        NoSuchProcess=NoSuchProcess,
        AccessDenied=type("AccessDenied", (Exception,), {}),
        CONN_LISTEN="LISTEN",
        CONN_ESTABLISHED="ESTABLISHED",
    )
    monkeypatch.setattr(processes, "psutil", fake_psutil)
    ended = SimpleNamespace(record=SimpleNamespace(state="exited", last_activity_ts=100.0))
    inspector = ProcessInspector(
        cast(Any, SimpleNamespace(sessions={"ended": ended})),
        EventBus(),
        orphan_grace_seconds=10,
    )
    item = OwnedProcess(88, 1, "ended", "child.exe", "", 8.0, None, 0, 0, [], [])
    inspector.owned[(88, 8.0)] = item
    inspector._revalidate_unseen(set(), {}, 105.0, False)
    assert item.evidence_state == "escaped"
    inspector._revalidate_unseen(set(), {}, 111.0, False)
    assert item.evidence_state == "suspected_orphan"
    current.clear()
    inspector._revalidate_unseen(set(), {}, 120.0, False)
    assert item.evidence_state == "exited"
    assert item.exit_evidence == "no_such_process"
