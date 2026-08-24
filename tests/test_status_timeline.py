"""Durable status timeline: the transition ledger made investigable.

Persistence is a sink outside the transition contract: `apply_state_transition`
and the replay harness stay untouched (the corpus pins that), while every
ledger entry — transitions and the non-transition kinds — survives daemon
restarts and session ends, keyed by ``(session_id, agent_run_id, seq)`` and
queryable by time range for post-mortems.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from swe_mux import app_keys as keys
from swe_mux.cli_state import CliStateMonitor
from swe_mux.session import (
    STATE_TRANSITION_LOG_LIMIT,
    TRANSCRIPT_STALE_SECONDS,
    apply_state_transition,
    clear_standing_activity,
    set_standing_activity,
)
from swe_mux.status_timeline import (
    LedgerRing,
    StatusTimelineStore,
    note_layer_reading,
)
from tests.support.detection_replay import ReplaySession
from tests.test_cli_state import OWN, write_state


def ringed_session(sid: str = "replay-session", run_id: str = "run-1") -> ReplaySession:
    """A ReplaySession whose ledger is the production LedgerRing.

    The harness itself keeps a plain deque (persistence is not part of the
    shared contract); swapping the ring here exercises the exact stamping the
    live Session performs, through the exact code paths that append entries.
    """
    session = ReplaySession("claude")
    session.record.id = sid
    session.record.agent_run_id = run_id
    session.state_transitions = cast(
        Any,
        LedgerRing(
            STATE_TRANSITION_LOG_LIMIT,
            run_id_provider=lambda: session.record.agent_run_id or session.record.id,
        ),
    )
    return session


def entries_of(session: Any, kind: str) -> list[dict[str, Any]]:
    return [entry for entry in session.state_transitions if entry.get("kind") == kind]


# --- LedgerRing ---------------------------------------------------------------


def test_ledger_ring_stamps_seq_and_run_id_at_append_time() -> None:
    session = ringed_session()
    session.transition("working", None, source="hook", evidence="hook:UserPromptSubmit")
    # A rollover mints a new run id; entries appended after it must carry it.
    session.record.agent_run_id = "run-2"
    session.transition("idle", None, source="hook", evidence="hook:Stop")
    entries = list(session.state_transitions)
    assert [entry["seq"] for entry in entries] == [0, 1]
    assert entries[0]["agent_run_id"] == "run-1"
    assert entries[1]["agent_run_id"] == "run-2"


def test_ledger_ring_sink_is_guarded_and_nudged_per_append() -> None:
    session = ringed_session()
    nudges: list[int] = []
    ring = cast(LedgerRing, session.state_transitions)
    ring.sink = lambda: nudges.append(1)
    session.transition("working", None, source="hook", evidence="e")
    assert nudges  # every append nudges

    def boom() -> None:
        raise RuntimeError("sink down")

    ring.sink = boom
    # A raising sink must never lose the entry or raise into the transition.
    session.transition("idle", None, source="hook", evidence="hook:Stop")
    assert session.record.state == "idle"
    assert len(session.state_transitions) == 2


# --- round-trip of every ledger kind -----------------------------------------


async def test_every_ledger_kind_round_trips_verbatim(tmp_path: Path) -> None:
    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session()
    now = session.clock.wall()
    # Kinds produced through their real code paths where they are cheap to
    # drive; the remainder appended in their production shapes (the store
    # persists payloads verbatim and must not care who appended them).
    session.transition("working", "Read", source="hook", evidence="hook:PreToolUse")
    set_standing_activity(
        session, "loop", source="transcript", evidence="tool:ScheduleWakeup", now=now
    )
    clear_standing_activity(session, "loop", evidence="tool:ScheduleWakeup:stop", now=now)
    session.note_watchdog_recovery("pty_idle_prompt", stalled_seconds=61.0, tail_verdict="open")
    session.note_reopen_blocked("hook")
    note_layer_reading(session, "pty_tail", "working", now=now)
    session.record.state = "exited"
    apply_state_transition(
        session, "idle", None, source="hook", evidence="late", now=now, monotonic_now=1.0
    )  # refused by the terminal latch -> transition_refused
    session.record.state = "idle"
    for shape in (
        {"ts": now, "kind": "cli_state", "action": "status_disagrees", "cli_status": "busy",
         "mux_state": "idle", "status_updated_at": now - 60},
        {"ts": now, "kind": "cli_state", "action": "nested_child_observed",
         "native_session_id": "99999999-8888-4777-8666-555544443333", "pid": 4242,
         "cli_status": "busy"},
        {"ts": now, "kind": "screen_classifier_blind", "blind_seconds": 120.4, "state": "working"},
        {"ts": now, "kind": "foreign_conversation_hook_ignored", "event": "PermissionRequest",
         "native_session_id": "99999999-8888-4777-8666-555544443333"},
        {"ts": now, "kind": "observer_fault", "error": "boom", "path": None, "restart_count": 1},
        {"ts": now, "kind": "hook_spool_discarded", "event": "Stop", "spooled_at": now - 900,
         "floor": now - 100},
        {"ts": now, "kind": "hook_spool_replay", "event": "PermissionRequest"},
    ):
        session.state_transitions.append(dict(shape))

    appended = [dict(entry) for entry in session.state_transitions]
    written = await store.flush_session(session)
    assert written == len(appended)
    timeline, truncated = await store.timeline(session.record.id)
    assert not truncated
    assert len(timeline) == len(appended)
    by_kind = {entry["kind"] for entry in timeline}
    assert by_kind == {
        "transition",
        "standing_activity",
        "watchdog_recovery",
        "reopen_blocked",
        "layer_reading",
        "transition_refused",
        "cli_state",
        "screen_classifier_blind",
        "foreign_conversation_hook_ignored",
        "observer_fault",
        "hook_spool_discarded",
        "hook_spool_replay",
    }
    # Verbatim payloads: every appended field survives the round trip.
    stored_by_seq = {entry["seq"]: entry for entry in timeline}
    for original in appended:
        stored = stored_by_seq[original["seq"]]
        for key, value in original.items():
            assert stored[key] == value, f"{original['kind']}.{key} mutated in storage"
    store.close()


# --- restart survival and seq continuation ------------------------------------


async def test_restart_survival_continues_seqs_without_collision(tmp_path: Path) -> None:
    path = tmp_path / "mux.db"
    first_store = StatusTimelineStore(path)
    session = ringed_session(sid="sess-1", run_id="run-1")
    session.transition("working", None, source="hook", evidence="turn-1")
    session.transition("idle", None, source="hook", evidence="hook:Stop")
    await first_store.flush_session(session)
    first_store.close()

    # A daemon restart: fresh store, fresh Session object, ring starts empty
    # and in-memory seqs restart at zero for the same (session, run).
    second_store = StatusTimelineStore(path)
    adopted = ringed_session(sid="sess-1", run_id="run-1")
    adopted.transition("working", None, source="hook", evidence="turn-2")
    adopted.transition("idle", None, source="hook", evidence="hook:Stop")
    await second_store.flush_session(adopted)

    timeline, _ = await second_store.timeline("sess-1")
    assert len(timeline) == 4
    seqs = [entry["seq"] for entry in timeline]
    assert seqs == sorted(seqs) and len(set(seqs)) == 4, "durable seqs must stay unique"
    assert [entry["evidence"] for entry in timeline if entry["kind"] == "transition"] == [
        "turn-1",
        "hook:Stop",
        "turn-2",
        "hook:Stop",
    ]
    second_store.close()


async def test_repeated_flushes_never_duplicate_rows(tmp_path: Path) -> None:
    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session()
    session.transition("working", None, source="hook", evidence="e1")
    assert await store.flush_session(session) == 1
    assert await store.flush_session(session) == 0  # checkpointed
    session.transition("idle", None, source="hook", evidence="hook:Stop")
    assert await store.flush_session(session) == 1
    timeline, _ = await store.timeline(session.record.id)
    assert len(timeline) == 2
    store.close()


async def test_ring_eviction_between_flushes_is_counted_not_silent(tmp_path: Path) -> None:
    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session()
    ring = LedgerRing(4, run_id_provider=lambda: "run-1")
    session.state_transitions = cast(Any, ring)
    for index in range(10):
        ring.append({"ts": float(index), "kind": "layer_reading", "reading": str(index)})
    await store.flush_session(session)
    assert store.stats()["rows_lost_to_ring_eviction"] == 6
    timeline, _ = await store.timeline(session.record.id)
    assert len(timeline) == 4
    store.close()


# --- time-range and retention -------------------------------------------------


async def test_time_range_query_is_inclusive_and_ordered(tmp_path: Path) -> None:
    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session()
    for offset, evidence in ((0.0, "a"), (100.0, "b"), (200.0, "c")):
        session.state_transitions.append(
            {"ts": 1_800_000_000.0 + offset, "kind": "layer_reading", "reading": evidence}
        )
    await store.flush_session(session)
    sliced, _ = await store.timeline(
        session.record.id, from_ts=1_800_000_000.0 + 100.0, to_ts=1_800_000_000.0 + 100.0
    )
    assert [entry["reading"] for entry in sliced] == ["b"]
    tail, _ = await store.timeline(session.record.id, from_ts=1_800_000_000.0 + 50.0)
    assert [entry["reading"] for entry in tail] == ["b", "c"]
    head, _ = await store.timeline(session.record.id, to_ts=1_800_000_000.0 + 50.0)
    assert [entry["reading"] for entry in head] == ["a"]
    store.close()


async def test_timeline_matches_by_agent_run_id_too(tmp_path: Path) -> None:
    # A history row's key is the run id; the investigation must reach the same
    # rows from either identity.
    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session(sid="sess-9", run_id="run-9")
    session.transition("working", None, source="hook", evidence="e")
    await store.flush_session(session)
    by_run, _ = await store.timeline("run-9")
    assert len(by_run) == 1 and by_run[0]["session_id"] == "sess-9"
    assert await store.runs_for_session("sess-9") == ["run-9"]
    store.close()


async def test_retention_prunes_only_rows_older_than_the_window(tmp_path: Path) -> None:
    store = StatusTimelineStore(tmp_path / "mux.db", retention_days=7)
    session = ringed_session()
    now = time.time()
    session.state_transitions.append(
        {"ts": now - 30 * 86400, "kind": "layer_reading", "reading": "ancient"}
    )
    session.state_transitions.append(
        {"ts": now - 60, "kind": "layer_reading", "reading": "recent"}
    )
    await store.flush_session(session)
    assert await store.prune() == 1
    timeline, _ = await store.timeline(session.record.id)
    assert [entry["reading"] for entry in timeline] == ["recent"]
    store.close()


# --- layer readings: on change only -------------------------------------------


def test_note_layer_reading_appends_on_change_only() -> None:
    session = ringed_session()
    now = session.clock.wall()
    assert note_layer_reading(session, "pty_tail", "unknown", now=now)
    assert not note_layer_reading(session, "pty_tail", "unknown", now=now + 5)
    assert not note_layer_reading(session, "pty_tail", "unknown", now=now + 10)
    assert note_layer_reading(session, "pty_tail", "working", now=now + 15)
    readings = entries_of(session, "layer_reading")
    assert [(entry["reading"], entry["previous"]) for entry in readings] == [
        ("unknown", None),
        ("working", "unknown"),
    ]


def test_cli_state_layer_reading_follows_the_files_status(tmp_path: Path) -> None:
    session = ringed_session()
    session.record.native_session_id = OWN
    session.record.cwd = str(tmp_path)
    session.record.run_cwd = str(tmp_path)
    now = session.clock.wall()
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="busy",
        status_updated_at_ms=(now - 60) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now)
    monitor.observe(monitor.poll(), [session], now + 5)  # unchanged -> no entry
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="idle",
        status_updated_at_ms=(now + 9) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now + 10)
    (tmp_path / "100.json").unlink()
    monitor.observe(monitor.poll(), [session], now + 15)  # file gone -> absent
    readings = [
        entry
        for entry in entries_of(session, "layer_reading")
        if entry["layer"] == "cli_state"
    ]
    assert [entry["reading"] for entry in readings] == ["busy", "idle", "absent"]


def test_cli_state_waiting_is_a_layer_reading_not_a_disagreement(tmp_path: Path) -> None:
    # Measured live 2026-08-01: the CLI publishes `waiting` for the duration of
    # a permission dialog. It is neither of the two counted contradictions, so
    # it must record as a reading and count nothing — a `waiting` file while
    # mux shows `working` is a dialog, not a defect, until the telemetry gate
    # says otherwise.
    session = ringed_session()
    session.record.native_session_id = OWN
    session.record.cwd = str(tmp_path)
    session.record.run_cwd = str(tmp_path)
    session.record.state = "working"
    now = session.clock.wall()
    session.last_state_change_ts = now - 60
    monitor = CliStateMonitor(tmp_path)
    write_state(
        tmp_path, 100, OWN, cwd=str(tmp_path), status="waiting",
        status_updated_at_ms=(now - 60) * 1000,
    )
    monitor.observe(monitor.poll(), [session], now)
    assert "cli_state_disagrees" not in session.status_health_counters
    assert session.record.state == "working"
    (reading,) = [
        entry
        for entry in entries_of(session, "layer_reading")
        if entry["layer"] == "cli_state"
    ]
    assert reading["reading"] == "waiting"


async def test_watchdog_pass_ledgers_pty_and_hook_recency_flips_once(tmp_path: Path) -> None:
    from swe_mux.session import SessionManager

    async def noop_drain(_session: Any) -> None:
        return None

    mgr = SimpleNamespace(
        hook_spool_dir=None,
        events=SimpleNamespace(emit=lambda *_a, **_k: asyncio.sleep(0)),
        _drain_hook_spool=noop_drain,
        _pty_tail_explanation=SessionManager._pty_tail_explanation,
        _note_pty_tail_readings=SessionManager._note_pty_tail_readings,
        _pty_tail_state=SessionManager._pty_tail_state,
    )
    mgr._check_unwitnessed_pty_turn = lambda s, n: SessionManager._check_unwitnessed_pty_turn(
        cast(Any, mgr), s, n
    )
    session = ringed_session()
    now = session.clock.wall()
    session.record.state = "working"
    session.last_state_change_ts = now - 1.0  # inside the stall gates: no recovery fires
    session.last_hook_ts = now - 10.0
    session.last_turn_hook_ts = now - 10.0
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    session.transcript_path = transcript
    cast(Any, session).scrollback = SimpleNamespace(
        tail_bytes=lambda _n: b"\xe2\x9c\xb6 Envisioning\xe2\x80\xa6 (3s)"
    )

    await SessionManager._watchdog_check_session(cast(Any, mgr), cast(Any, session), now)
    await SessionManager._watchdog_check_session(cast(Any, mgr), cast(Any, session), now + 5)
    readings = entries_of(session, "layer_reading")
    assert [(e["layer"], e["reading"]) for e in readings] == [
        ("hook_recency", "fresh"),
        ("pty_tail_screen", "working"),
        ("pty_tail", "working"),
        ("pty_tail_arbitration", "screen"),
    ]
    # The threshold crossing is one more entry, not one per pass.
    later = now + TRANSCRIPT_STALE_SECONDS + 20
    await SessionManager._watchdog_check_session(cast(Any, mgr), cast(Any, session), later)
    await SessionManager._watchdog_check_session(cast(Any, mgr), cast(Any, session), later + 5)
    hook_readings = [
        entry
        for entry in entries_of(session, "layer_reading")
        if entry["layer"] == "hook_recency"
    ]
    assert [entry["reading"] for entry in hook_readings] == ["fresh", "stale"]


# --- endpoints ----------------------------------------------------------------


def _request(app: dict[str, Any], sid: str, query: dict[str, str] | None = None) -> Any:
    return SimpleNamespace(app=app, match_info={"sid": sid}, query=query or {})


async def test_state_log_range_query_serves_the_durable_timeline(tmp_path: Path) -> None:
    from swe_mux.routes.diagnostics import get_session_state_log

    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session(sid="live-1", run_id="live-1")
    session.transcript_path = None
    session.transition("working", None, source="hook", evidence="turn")
    # The live payload reads Session-only diagnostics the harness session does
    # not carry; decorate the stub with inert values.
    stub = cast(Any, session)
    stub.transcript_provisional = False
    stub.transcript_growth_ts = 0.0
    stub.observer_restart_count = 0
    stub.observer_last_fault = None
    stub.agent_lifecycle_id = None
    stub.input_owner_device = None
    stub.input_owner_epoch = 0
    stub.viewports = {}
    stub.geometry = None
    stub.input_rejections = 0
    stub.input_claim_denials = 0
    stub.claim_log = []

    class Sessions:
        sessions = {"live-1": session}

        def resolve(self, identity: str) -> Any:
            if identity != "live-1":
                raise KeyError(identity)
            return session

    app = {
        keys.SESSIONS: Sessions(),
        keys.STATUS_TIMELINE: store,
        keys.HISTORY: SimpleNamespace(history_entry=_none_entry),
        keys.DEVICE_PRESENCE: SimpleNamespace(
            active_profiles=lambda: set(), leading_profile=lambda: None
        ),
    }
    wall = session.clock.wall()
    response = await get_session_state_log(
        _request(app, "live-1", {"from": str(wall - 3600), "to": str(wall + 3600)})
    )
    payload = json.loads(response.text or "")
    assert payload["live"] is True
    assert payload["timeline_truncated"] is False
    kinds = [entry["kind"] for entry in payload["timeline"]]
    assert "transition" in kinds
    assert payload["timeline_sink"]["rows_written"] >= 1
    # The flush-then-query means the slice is complete to the request moment:
    # the live ring holds nothing the durable slice lacks.
    assert len(payload["timeline"]) == len(payload["transitions"])
    store.close()


async def _none_entry(_identity: str) -> dict[str, Any] | None:
    return None


async def test_ended_session_state_log_is_served_post_mortem(tmp_path: Path) -> None:
    from swe_mux.routes.diagnostics import get_session_state_log

    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session(sid="gone-1", run_id="gone-run")
    session.transition("working", None, source="hook", evidence="turn")
    session.transition("exited", None, source="pty", evidence="process_exit:completed", force=True)
    await store.flush_session(session)

    class Sessions:
        sessions: dict[str, Any] = {}

        def resolve(self, identity: str) -> Any:
            raise KeyError(identity)

    history_row = {"id": "gone-run", "backend": "claude", "final_state": "exited"}

    async def history_entry(identity: str) -> dict[str, Any] | None:
        return history_row if identity in {"gone-run"} else None

    app = {
        keys.SESSIONS: Sessions(),
        keys.STATUS_TIMELINE: store,
        keys.HISTORY: SimpleNamespace(history_entry=history_entry),
    }
    response = await get_session_state_log(_request(app, "gone-1"))
    payload = json.loads(response.text or "")
    assert payload["live"] is False
    assert payload["runs"] == ["gone-run"]
    states = [
        (entry["previous"], entry["state"])
        for entry in payload["timeline"]
        if entry["kind"] == "transition"
    ]
    assert states == [("idle", "working"), ("working", "exited")]
    store.close()


async def test_diagnostic_bundle_packages_timeline_health_and_transcript(
    tmp_path: Path,
) -> None:
    from swe_mux.routes.diagnostics import get_session_diagnostic_bundle

    store = StatusTimelineStore(tmp_path / "mux.db")
    session = ringed_session(sid="gone-2", run_id="gone-2-run")
    wall = session.clock.wall()
    session.transition("working", None, source="hook", evidence="turn")
    session.transition("idle", None, source="hook", evidence="hook:Stop")
    await store.flush_session(session)

    transcript = tmp_path / "native.jsonl"
    stamp = datetime.fromtimestamp(wall, tz=UTC).isoformat().replace("+00:00", "Z")
    outside = datetime.fromtimestamp(wall - 7200, tz=UTC).isoformat().replace("+00:00", "Z")
    transcript.write_text(
        json.dumps({"type": "user", "timestamp": outside, "message": {"content": "old"}})
        + "\n"
        + json.dumps({"type": "user", "timestamp": stamp, "message": {"content": "in window"}})
        + "\n",
        encoding="utf-8",
    )
    history_row = {
        "id": "gone-2-run",
        "backend": "claude",
        "final_state": "exited",
        "transcript_path": str(transcript),
    }

    async def history_entry(identity: str) -> dict[str, Any] | None:
        return history_row if identity in {"gone-2-run"} else None

    class Sessions:
        sessions: dict[str, Any] = {}

        def resolve(self, identity: str) -> Any:
            raise KeyError(identity)

    app = {
        keys.SESSIONS: Sessions(),
        keys.STATUS_TIMELINE: store,
        keys.HISTORY: SimpleNamespace(history_entry=history_entry),
    }
    response = await get_session_diagnostic_bundle(
        _request(app, "gone-2", {"from": str(wall - 3600), "to": str(wall + 3600)})
    )
    payload = json.loads(response.text or "")
    assert payload["live"] is False
    assert payload["runs"] == ["gone-2-run"]
    assert [entry["kind"] for entry in payload["timeline"]].count("transition") == 2
    assert payload["fleet_status_health"]["alarm"] is False
    (transcript_slice,) = payload["transcripts"]
    assert transcript_slice["agent_run_id"] == "gone-2-run"
    texts = [
        block["text"]
        for message in transcript_slice["messages"]
        for block in message["content"]
    ]
    assert texts == ["in window"], "only records inside the window belong in the bundle"
    store.close()
