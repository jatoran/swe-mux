from __future__ import annotations

import asyncio
import json
import sqlite3
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
    TELEMETRY_SCHEMA_VERSION,
    OperationalTelemetryStore,
    command_hash,
    process_identity,
    scan_native_telemetry,
)
from swe_mux.processes import OwnedProcess, ProcessInspector
from swe_mux.provider_accounts import ProviderAccountManager
from swe_mux.server import create_app
from swe_mux.sqlite_store import read_schema_version
from swe_mux.tier0_store import TIER0_SCHEMA_VERSION, Tier0Store

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
        path.with_suffix(".omp.jsonl"),
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
                "attribution_version": 2,
                "attribution_source": "job_membership",
                "last_attributed_at": 1299.0,
                "last_job_confirmed_at": 1299.0,
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
    assert rows[0]["attribution_version"] == 2
    assert rows[0]["attribution_source"] == "job_membership"
    assert rows[0]["last_attributed_at"] == 1299.0
    assert rows[0]["last_job_confirmed_at"] == 1299.0
    columns = {
        row[1] for row in reopened._db.execute("PRAGMA table_info(process_evidence)").fetchall()
    }
    assert "command" not in columns
    reopened.close()


async def test_duplicate_live_process_owners_are_retired_on_reopen(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    started = 1234.5
    await store.record_process_observations(
        [
            {
                "pid": 44,
                "session_id": session_id,
                "started_at": started,
                "evidence_state": "active",
                "attribution_version": 2,
                "attribution_source": "parent_walk",
            }
            for session_id in ("session-a", "session-b")
        ]
    )
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    rows = [item for item in await reopened.process_candidates() if item["pid"] == 44]
    assert len(rows) == 2
    assert {item["state"] for item in rows} == {"stale"}
    assert {item["reason"] for item in rows} == {"duplicate_fingerprint_ownership"}
    assert {item["exit_evidence"] for item in rows} == {"ownership_rejected"}
    assert all(item["exited_at"] is not None for item in rows)

    await reopened.record_process_observations(
        [
            {
                "pid": 44,
                "session_id": "session-a",
                "started_at": started,
                "evidence_state": "active",
                "attribution_version": 2,
                "attribution_source": "parent_walk",
            }
        ]
    )
    rows = [item for item in await reopened.process_candidates() if item["pid"] == 44]
    assert sum(item["exited_at"] is None for item in rows) == 1
    assert next(item for item in rows if item["exited_at"] is None)["session_id"] == "session-a"
    reopened.close()


async def test_identity_repair_resets_rebuildable_provider_telemetry(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    session = FakeLiveSession("claude")
    session.record.agent_run_id = "false-run"
    store.sessions = SimpleNamespace(sessions={session.record.id: session})
    event_bus = EventBus()
    await store.record_tool_event(
        await event_bus.emit(
            "tool_use",
            session_id=session.record.id,
            source="transcript",
            backend="claude",
            tool="Read",
            call_id="false-call",
        )
    )
    await store.record_compaction(
        await event_bus.emit(
            "context_compacted",
            session_id=session.record.id,
            source="transcript",
            backend="claude",
        )
    )
    await store.record_process_observations(
        [
            {
                "pid": 44,
                "session_id": session.record.id,
                "agent_run_id": "false-run",
                "started_at": 1234.5,
            }
        ]
    )
    store._db.execute(
        "INSERT INTO transcript_telemetry_coverage"
        "(session_id,backend,parser_version,status,recognized_records,unknown_records,"
        "tool_events,skill_events,compaction_events,reconciled_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (session.record.id, "claude", "claude-v1", "ready", 1, 0, 1, 0, 1, time.time()),
    )
    store._db.commit()

    await store.reset_session_provider_observations(session.record.id, session.record.id)

    assert (
        store._db.execute(
            "SELECT COUNT(*) FROM tool_events WHERE session_id=?", (session.record.id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        store._db.execute(
            "SELECT COUNT(*) FROM context_compactions WHERE session_id=?",
            (session.record.id,),
        ).fetchone()[0]
        == 0
    )
    assert (
        store._db.execute(
            "SELECT COUNT(*) FROM transcript_telemetry_coverage WHERE session_id=?",
            (session.record.id,),
        ).fetchone()[0]
        == 0
    )
    process = (await store.process_candidates())[0]
    assert process["agent_run_id"] == session.record.id
    store.close()


async def test_historical_identity_repair_removes_only_false_run_telemetry(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    session = FakeLiveSession("claude")
    store.sessions = SimpleNamespace(sessions={session.record.id: session})
    event_bus = EventBus()
    session.record.agent_run_id = "false-run"
    await store.record_tool_event(
        await event_bus.emit(
            "tool_use",
            session_id=session.record.id,
            source="transcript",
            backend="claude",
            tool="Read",
        )
    )
    session.record.agent_run_id = session.record.id
    await store.record_tool_event(
        await event_bus.emit(
            "tool_use",
            session_id=session.record.id,
            source="transcript",
            backend="codex",
            tool="Shell",
            call_id="root-call",
        )
    )
    await store.record_process_observations(
        [
            {
                "pid": 44,
                "session_id": session.record.id,
                "agent_run_id": "false-run",
                "started_at": 1234.5,
            }
        ]
    )
    store._db.execute(
        "INSERT INTO transcript_telemetry_coverage"
        "(session_id,backend,parser_version,status,recognized_records,unknown_records,"
        "tool_events,skill_events,compaction_events,reconciled_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (session.record.id, "claude", "claude-v1", "ready", 1, 0, 1, 0, 0, time.time()),
    )
    store._db.commit()

    await store.quarantine_agent_run_provider_observations(
        session.record.id, "false-run", session.record.id
    )

    rows = store._db.execute(
        "SELECT agent_run_id,backend FROM tool_events WHERE session_id=?",
        (session.record.id,),
    ).fetchall()
    assert [(row["agent_run_id"], row["backend"]) for row in rows] == [
        (session.record.id, "codex")
    ]
    assert (
        store._db.execute(
            "SELECT COUNT(*) FROM transcript_telemetry_coverage WHERE session_id=?",
            (session.record.id,),
        ).fetchone()[0]
        == 0
    )
    assert (await store.process_candidates())[0]["agent_run_id"] == session.record.id
    store.close()


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
    durable_weekly = next(item for item in durable["quota"]["resets"] if item["id"] == weekly["id"])
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


async def test_quota_reset_user_review_is_durable_and_removes_alert(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    now = 1_800_000_000.0
    for reset_id, provider in (("codex-reset", "codex"), ("claude-reset", "claude")):
        store._db.execute(
            "INSERT INTO quota_reset_events(id,provider,account_id,window,before_sample_id,"
            "after_sample_id,before_value,after_value,observed_at,classification,confidence,"
            "confirmed,suppression_reason,created_at,confirmed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                reset_id,
                provider,
                f"{provider}-account",
                "weekly",
                1,
                2,
                80,
                2,
                now,
                "unexpected",
                "high",
                1,
                None,
                now,
                now,
            ),
        )
    store._db.commit()

    reviewed = await store.review_quota_resets(["codex-reset"], "manual_usage")
    assert [item["review_status"] for item in reviewed] == ["manual_usage"]
    assert reviewed[0]["reviewed_at"] is not None
    with pytest.raises(ValueError, match="only valid for Codex"):
        await store.review_quota_resets(["claude-reset"], "manual_usage")
    # The Codex row is already reviewed, so a mixed group must still refuse wholesale
    # rather than half-apply and leave the user staring at the remainder.
    assert (await store.reset_summary())["count"] == 1
    discarded = await store.review_quota_resets(["claude-reset"], "discarded")
    assert [item["review_status"] for item in discarded] == ["discarded"]
    assert (await store.reset_summary()) == {"count": 0, "items": []}
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    snapshot = await reopened.snapshot()
    statuses = {item["id"]: item["review_status"] for item in snapshot["quota"]["resets"]}
    assert statuses == {"codex-reset": "manual_usage", "claude-reset": "discarded"}
    reopened.close()


async def test_one_provider_rollover_raises_one_coalesced_alert(phase2_path: Path) -> None:
    history = HistoryIndex(phase2_path)
    events = EventBus(history.append_event)
    # Long enough that only the explicit flush emits: the claim under test is the
    # grouping, not the wall-clock length of the coalescing window.
    store = OperationalTelemetryStore(phase2_path, reset_alert_coalesce_seconds=30)
    store.start(events, sessions=SimpleNamespace(sessions={}), history=history)
    observed: asyncio.Queue[Any] = events.subscribe(name="test-reset-alerts")
    first = 1_800_000_000.0

    async def sample(provider: str, account_id: str, at: float, weekly: float) -> None:
        await store.record_quota_sample(
            provider=provider,
            account_id=account_id,
            quota={
                "session": None,
                "weekly": {"used_percent": weekly, "resets_at": first + 7 * 86400},
                "source": "fixture",
                "status": "ready",
            },
            sampled_at=at,
            account_active=True,
            auth_state="saved",
        )

    # The provider rolls the whole plan over at once, so every enabled account of that
    # provider registers the same rollover inside one sequential refresh pass.
    slots = (("codex", "codex-a"), ("codex", "codex-b"), ("codex", "codex-c"), ("claude", "cl-a"))
    for index, (provider, account_id) in enumerate(slots):
        await sample(provider, account_id, first + index, 71)
        await sample(provider, account_id, first + 900 + index, 2)
        await sample(provider, account_id, first + 1800 + index, 2.5)

    await store.flush_reset_alerts()
    alerts = []
    while not observed.empty():
        event = observed.get_nowait()
        if event.type == "unexpected_quota_reset":
            alerts.append(event)
    # One alert per provider, not one per account-window: three Codex accounts observing
    # the same rollover used to be three chimes and three lock-screen buzzes.
    assert sorted(event.payload["provider"] for event in alerts) == ["claude", "codex"]
    codex = next(event for event in alerts if event.payload["provider"] == "codex")
    assert codex.payload["count"] == 3
    assert {item["account_id"] for item in codex.payload["resets"]} == {
        "codex-a",
        "codex-b",
        "codex-c",
    }
    # The scalar fields stay populated so an automation rule matching them still fires.
    assert codex.payload["reset_id"] in {item["id"] for item in codex.payload["resets"]}
    assert codex.payload["window"] == "weekly"
    claude = next(event for event in alerts if event.payload["provider"] == "claude")
    assert claude.payload["count"] == 1

    # A flush drains the buffer; shutdown must not replay what was already delivered.
    await store.stop()
    replayed = []
    while not observed.empty():
        event = observed.get_nowait()
        if event.type == "unexpected_quota_reset":
            replayed.append(event)
    assert replayed == []
    events.unsubscribe(observed)
    store.close()
    history.close()


async def test_reset_alert_groups_the_unreviewed_set_and_seen_resolves_it_server_side(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    now = 1_800_000_000.0
    ids = ["codex-a", "codex-b", "codex-c"]
    for index, reset_id in enumerate(ids):
        store._db.execute(
            "INSERT INTO quota_reset_events(id,provider,account_id,window,before_sample_id,"
            "after_sample_id,before_value,after_value,observed_at,classification,confidence,"
            "confirmed,suppression_reason,created_at,confirmed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                reset_id,
                "codex",
                f"account-{index}",
                "weekly",
                1,
                2,
                80,
                2,
                now,
                "unexpected",
                "high",
                1,
                None,
                now,
                now + index,
            ),
        )
    store._db.commit()

    # The whole group surfaces at once; triaging it one row at a time was N alerts for
    # what the provider did once.
    summary = await store.reset_summary()
    assert summary["count"] == 3
    assert [item["id"] for item in summary["items"]] == ["codex-c", "codex-b", "codex-a"]

    # `seen` is an acknowledgement, stored server-side so dismissing at the desk also
    # silences the phone — it used to be a per-browser localStorage marker.
    await store.review_quota_resets(ids, "seen")
    assert (await store.reset_summary()) == {"count": 0, "items": []}
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    snapshot = await reopened.snapshot()
    assert {item["review_status"] for item in snapshot["quota"]["resets"]} == {"seen"}
    assert (await reopened.reset_summary())["count"] == 0
    reopened.close()


def test_legacy_quota_reset_schema_adds_review_columns(phase2_path: Path) -> None:
    connection = sqlite3.connect(phase2_path)
    connection.execute(
        "CREATE TABLE quota_reset_events ("
        "id TEXT PRIMARY KEY,provider TEXT NOT NULL,account_id TEXT NOT NULL,"
        "window TEXT NOT NULL,before_sample_id INTEGER NOT NULL,"
        "after_sample_id INTEGER NOT NULL,confirmation_sample_id INTEGER,"
        "before_value REAL NOT NULL,after_value REAL NOT NULL,expected_reset_at REAL,"
        "observed_at REAL NOT NULL,classification TEXT NOT NULL,confidence TEXT NOT NULL,"
        "confirmed INTEGER NOT NULL DEFAULT 0,suppression_reason TEXT,"
        "created_at REAL NOT NULL,confirmed_at REAL)"
    )
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    store = OperationalTelemetryStore(phase2_path)
    columns = {row["name"] for row in store._db.execute("PRAGMA table_info(quota_reset_events)")}
    assert {"review_status", "reviewed_at"} <= columns
    # Per-store row, not the per-file PRAGMA: several stores share mux.db and
    # each one stamping user_version made the last connect overwrite the rest.
    assert read_schema_version(store._db, "telemetry") == TELEMETRY_SCHEMA_VERSION
    store.close()


def test_legacy_process_evidence_schema_adds_attribution_provenance(
    phase2_path: Path,
) -> None:
    connection = sqlite3.connect(phase2_path)
    connection.execute(
        "CREATE TABLE process_evidence ("
        "identity_id TEXT PRIMARY KEY,pid INTEGER NOT NULL,creation_time REAL NOT NULL,"
        "session_id TEXT NOT NULL,agent_run_id TEXT,project_id TEXT,executable TEXT,"
        "command_hash TEXT NOT NULL,parent_pid INTEGER,parent_lineage_json TEXT NOT NULL "
        "DEFAULT '[]',job_assignment TEXT NOT NULL,state TEXT NOT NULL,reason TEXT NOT NULL,"
        "confidence TEXT NOT NULL,first_seen REAL NOT NULL,last_seen REAL NOT NULL,"
        "last_verified_at REAL,exited_at REAL,exit_evidence TEXT,inaccessible_count INTEGER "
        "NOT NULL DEFAULT 0,startup_revalidated INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "INSERT INTO process_evidence(identity_id,pid,creation_time,session_id,command_hash,"
        "job_assignment,state,reason,confidence,first_seen,last_seen) "
        "VALUES('old',77,10,'session-a','hash','unknown','escaped','old','medium',10,20)"
    )
    connection.commit()
    connection.close()

    store = OperationalTelemetryStore(phase2_path)
    rows = store._db.execute(
        "SELECT attribution_version,attribution_source,last_attributed_at,"
        "last_job_confirmed_at FROM process_evidence WHERE identity_id='old'"
    ).fetchone()
    assert dict(rows) == {
        "attribution_version": 1,
        "attribution_source": "legacy",
        "last_attributed_at": None,
        "last_job_confirmed_at": None,
    }
    store.close()


def test_schema_versions_are_per_store_not_per_file(phase2_path: Path) -> None:
    telemetry = OperationalTelemetryStore(phase2_path)
    tier0 = Tier0Store(phase2_path)
    assert read_schema_version(telemetry._db, "telemetry") == TELEMETRY_SCHEMA_VERSION
    assert read_schema_version(telemetry._db, "tier0") == TIER0_SCHEMA_VERSION
    assert read_schema_version(telemetry._db, "telemetry") != read_schema_version(
        telemetry._db, "tier0"
    )
    tier0.close()
    telemetry.close()


def test_corrupt_database_is_quarantined_instead_of_crashing_startup(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)
    store = OperationalTelemetryStore(path)
    # The daemon comes up on a rebuilt schema; the unusable file is kept beside it
    # rather than deleted, because it is evidence.
    assert store._db.execute("SELECT COUNT(*) FROM quota_samples").fetchone()[0] == 0
    assert list(tmp_path.glob("mux.db.corrupt-*"))
    store.close()


def test_legacy_quota_samples_schema_adds_fable_columns(phase2_path: Path) -> None:
    connection = sqlite3.connect(phase2_path)
    connection.execute(
        "CREATE TABLE quota_samples ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT NOT NULL,account_id TEXT NOT NULL,"
        "sampled_at REAL NOT NULL,status TEXT NOT NULL,session_used REAL,weekly_used REAL,"
        "session_reset_at REAL,weekly_reset_at REAL,source TEXT,freshness TEXT NOT NULL,"
        "raw_precision INTEGER NOT NULL DEFAULT 0,error TEXT,account_active INTEGER NOT NULL,"
        "auth_state TEXT NOT NULL,UNIQUE(provider,account_id,sampled_at))"
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    store = OperationalTelemetryStore(phase2_path)
    columns = {row["name"] for row in store._db.execute("PRAGMA table_info(quota_samples)")}
    assert {"fable_used", "fable_reset_at"} <= columns
    store.close()


async def test_quota_sample_persists_fable_weekly_window(phase2_path: Path) -> None:
    store = OperationalTelemetryStore(phase2_path)
    at = 1_800_000_000.0
    await store.record_quota_sample(
        provider="claude",
        account_id="account-fable",
        quota={
            "session": {"used_percent": 29, "resets_at": at + 7200},
            "weekly": {"used_percent": 90, "resets_at": at + 7 * 86400},
            "fable": {"used_percent": 80, "resets_at": at + 7 * 86400, "window_minutes": 10080},
            "source": "oauth",
            "status": "ready",
        },
        sampled_at=at,
        account_active=True,
        auth_state="saved",
    )
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    latest = await reopened.latest_quota_by_account()
    fable = latest["account-fable"]["fable"]
    assert fable is not None
    assert fable["used_percent"] == 80
    assert fable["window_minutes"] == 10080
    assert fable["resets_at"] == at + 7 * 86400
    reopened.close()


async def test_quota_sample_without_fable_reports_none(phase2_path: Path) -> None:
    store = OperationalTelemetryStore(phase2_path)
    at = 1_800_000_000.0
    await store.record_quota_sample(
        provider="codex",
        account_id="account-nofable",
        quota={
            "session": {"used_percent": 12, "resets_at": at + 3600},
            "weekly": {"used_percent": 34, "resets_at": at + 7 * 86400},
            "source": "backend",
            "status": "ready",
        },
        sampled_at=at,
        account_active=True,
        auth_state="saved",
    )
    store.close()

    reopened = OperationalTelemetryStore(phase2_path)
    latest = await reopened.latest_quota_by_account()
    assert latest["account-nofable"]["fable"] is None
    reopened.close()


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
        "omp": "omp-phase2-v2",
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


async def test_native_compaction_identity_deduplicates_hook_and_transcript(
    phase2_path: Path,
) -> None:
    history = HistoryIndex(phase2_path)
    live = FakeLiveSession("omp")
    await history.session_started(live.record, None)
    sessions = SimpleNamespace(sessions={live.record.id: live})
    events = EventBus(history.append_event)
    store = OperationalTelemetryStore(phase2_path)
    store.start(events, sessions=sessions, history=history)

    for source in ("hook", "transcript"):
        await events.emit(
            "context_compacted",
            session_id=live.record.id,
            source=source,
            scope="root",
            backend="omp",
            capability="explicit_native",
            confidence="high",
            compaction_id="compact-1",
            parser_version="2",
        )
    assert store._event_queue is not None
    await store._event_queue.join()

    snapshot = await store.snapshot()
    assert snapshot["compactions"][0]["count"] == 1
    assert live.record.compaction_count == 1

    await store.stop()
    store.close()
    history.close()


async def test_complete_omp_scan_repairs_legacy_cross_source_compaction_duplicate(
    phase2_path: Path,
) -> None:
    history = HistoryIndex(phase2_path)
    live = FakeLiveSession("omp")
    live.record.compaction_count = 2
    await history.session_started(live.record, None)
    store = OperationalTelemetryStore(phase2_path)
    store.sessions = SimpleNamespace(sessions={live.record.id: live})
    store.history = history
    observed_at = 1_800_000_000.0
    for event_id, source, offset in (
        ("legacy-hook", "hook", 0.0),
        ("legacy-transcript", "transcript", 0.04),
    ):
        store._db.execute(
            "INSERT INTO context_compactions"
            "(id,event_seq,session_id,agent_run_id,project_id,backend,model,observed_at,"
            "source,capability,confidence,parser_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                None,
                live.record.id,
                live.record.agent_run_id,
                live.record.project_id,
                "omp",
                live.record.model,
                observed_at + offset,
                source,
                "explicit_native",
                "high",
                "omp:3",
            ),
        )
    store._db.commit()

    await store._persist_transcript_scan(
        {
            "id": live.record.id,
            "backend": "omp",
            "project_id": live.record.project_id,
            "model": live.record.model,
            "transcript_path": "omp.jsonl",
        },
        1,
        100,
        {
            "tools": [],
            "compactions": [
                {
                    "source_identity": (
                        f"native:{live.record.id}:compaction:compact-native"
                    ),
                    "compaction_id": "compact-native",
                    "observed_at": observed_at,
                }
            ],
            "recognized": 1,
            "unknown": 0,
            "diagnostic": None,
        },
    )

    rows = store._db.execute(
        "SELECT id FROM context_compactions WHERE session_id=?", (live.record.id,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] not in {"legacy-hook", "legacy-transcript"}
    assert live.record.compaction_count == 1
    assert live.updates == 1
    history_row = await history.history_entry(live.record.id)
    assert history_row and history_row["compaction_count"] == 1
    store.close()
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


async def test_quota_series_keeps_verified_account_owners_separate(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path)
    start = 1_720_000_000.0
    for offset, owner, session, weekly in (
        (0, "provider-owner-a", 10.0, 20.0),
        (300, "provider-owner-a", 15.0, 21.0),
        (600, "provider-owner-b", 40.0, 50.0),
    ):
        await store.record_quota_sample(
            provider="codex",
            account_id="account-slot",
            provider_account_uuid=owner,
            quota={
                "session": {"used_percent": session},
                "weekly": {"used_percent": weekly},
                "status": "ready",
            },
            sampled_at=start + offset,
            account_active=True,
            auth_state="saved",
        )

    daily = await store.quota_series(
        provider="codex",
        account_id="account-slot",
        since=start - 1,
        until=start + 1000,
        resolution="daily",
    )
    assert daily["interpretation"] == "quota_utilization_not_token_usage"
    assert [item["provider_account_uuid"] for item in daily["series"]] == [
        "provider-owner-a",
        "provider-owner-b",
    ]
    first_point = daily["series"][0]["points"][0]
    assert first_point["samples"] == 2
    assert first_point["session_first"] == 10.0
    assert first_point["session_last"] == 15.0
    assert all(item["identity"] == "verified" for item in daily["series"])

    raw = await store.quota_series(
        provider="codex",
        account_id="account-slot",
        since=start + 550,
        resolution="raw",
    )
    assert len(raw["series"]) == 1
    assert raw["series"][0]["provider_account_uuid"] == "provider-owner-b"
    assert raw["series"][0]["points"][0]["session"]["used_percent"] == 40.0
    store.close()


async def test_retention_rollups_preserve_verified_account_owner(
    phase2_path: Path,
) -> None:
    store = OperationalTelemetryStore(phase2_path, retention_days=1)
    old = time.time() - 3 * 86400
    for offset, owner, used in (
        (0, "provider-owner-a", 20.0),
        (300, "provider-owner-b", 30.0),
    ):
        await store.record_quota_sample(
            provider="codex",
            account_id="account-slot",
            provider_account_uuid=owner,
            quota={"session": None, "weekly": {"used_percent": used}, "status": "ready"},
            sampled_at=old + offset,
            account_active=True,
            auth_state="saved",
        )

    await store.prune()
    result = await store.quota_series(account_id="account-slot", resolution="daily")
    assert {item["provider_account_uuid"] for item in result["series"]} == {
        "provider-owner-a",
        "provider-owner-b",
    }
    store.close()


def test_legacy_quota_rollups_migrate_without_claiming_verified_identity(
    phase2_path: Path,
) -> None:
    db = sqlite3.connect(phase2_path)
    db.execute(
        "CREATE TABLE quota_sample_rollups (provider TEXT NOT NULL,account_id TEXT NOT NULL,"
        "day TEXT NOT NULL,samples INTEGER NOT NULL,errors INTEGER NOT NULL,"
        "session_min REAL,session_max REAL,session_first REAL,session_last REAL,"
        "weekly_min REAL,weekly_max REAL,weekly_first REAL,weekly_last REAL,"
        "PRIMARY KEY(provider,account_id,day))"
    )
    db.execute(
        "INSERT INTO quota_sample_rollups VALUES "
        "('codex','account-a','2026-01-01',2,0,10,20,10,20,30,40,30,40)"
    )
    db.commit()
    db.close()

    store = OperationalTelemetryStore(phase2_path)
    row = store._db.execute("SELECT * FROM quota_sample_rollups").fetchone()
    assert row["provider_account_uuid"] == ""
    assert row["samples"] == 2
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
    assert ("GET", "/api/telemetry/quota-series") in routes
    assert ("POST", "/api/telemetry/quota-resets/review") in routes


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
