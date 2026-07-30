"""Conversation-identity integrity: one live session per conversation.

These tests pin the four defences against cross-attribution — the root cause of
sessions rendering a sibling's status/tokens as their own and of history entries
resuming a conversation the user never named:

1. Claude never moves by transcript-switch heuristics (its CLI reports rollovers
   itself over the hook ingress).
2. Unconfirmed (heuristic) rollovers never rewrite the Claude lifecycle anchor.
3. Adoption refuses a rolled conversation that a sibling's root identity claims.
4. The live identity sweep detects collisions and heals Claude sessions back to
   their own anchor.

Plus the history keying that made a rename land on the pane's first conversation
instead of the one being looked at, and the resume guard against double-claiming
a live conversation.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from swe_mux.models import SessionRecord
from swe_mux.session import Session, SessionManager, fleet_status_health

OWN = "aaaaaaaa-1111-4a7b-8c9d-0e1f2a3b4c5d"
STOLEN = "bbbbbbbb-2222-4a7b-8c9d-0e1f2a3b4c5d"
ROLLED = "cccccccc-3333-4a7b-8c9d-0e1f2a3b4c5d"


def agent_record(
    sid: str,
    *,
    backend: str = "claude",
    cwd: str = ".",
    native_id: str | None = None,
) -> SessionRecord:
    record = SessionRecord(
        sid, f"{backend}-{sid[:6]}", "default", backend, native_id or sid, cwd,
        f"{backend}.exe", ["--session-id", sid],
    )
    record.spawn_backend = backend
    record.spawn_native_session_id = sid
    record.agent_run_id = sid
    record.agent_run_started_at = record.created_at
    record.run_cwd = cwd
    return record


def fake_manager() -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {}
    manager._known_identity_collisions = set()
    manager.events = SimpleNamespace(emit=AsyncMock())
    manager.history = SimpleNamespace(
        session_promoted=AsyncMock(),
        reopen_agent_run=AsyncMock(),
        quarantine_misattributed_agent_run=AsyncMock(),
        reset_run_transcript_copy=AsyncMock(),
    )
    return manager


def real_session(record: SessionRecord, cwd: Path) -> Session:
    pty = SimpleNamespace(graceful_exit="", isalive=lambda: True)
    adapter = SimpleNamespace(
        name=record.backend,
        reports_conversation_rollover=record.backend == "claude",
        assigns_conversation_id=record.backend == "claude",
        graceful_exit_keys=lambda: "exit\r",
        transcript_path=lambda native_id, _cwd: cwd / f"{native_id}.jsonl",
    )
    return Session(record, cast(Any, pty), cast(Any, adapter), 32, "secret")


# ------------------------------------------------ heuristic switching stays off


async def test_claude_watcher_never_consults_the_switch_heuristic(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # A fresh unclaimed sibling transcript is exactly what the heuristic would
    # grab. For Claude the watcher must never even look: rollovers arrive from
    # the CLI itself, and a guess here is how sessions latched onto siblings.
    monkeypatch.setattr("swe_mux.session.TRANSCRIPT_SWITCH_POLL_SECONDS", 0.01)
    manager = fake_manager()
    record = agent_record(OWN, cwd=str(tmp_path))
    session = real_session(record, tmp_path)
    current = tmp_path / f"{OWN}.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    manager._transcript_switch_candidate = lambda *_: (tmp_path / "fresh.jsonl")
    manager._note_transcript_staleness = AsyncMock()
    stop_event = asyncio.Event()
    observe_task = asyncio.create_task(asyncio.sleep(0.05))

    switch = await SessionManager._watch_transcript_switch(
        manager, session, current, stop_event, observe_task
    )

    assert switch is None
    assert manager._note_transcript_staleness.await_count >= 1


async def test_codex_watcher_still_uses_the_switch_heuristic(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("swe_mux.session.TRANSCRIPT_SWITCH_POLL_SECONDS", 0.01)
    manager = fake_manager()
    record = agent_record("codex-root", backend="codex", cwd=str(tmp_path))
    session = real_session(record, tmp_path)
    fresh = tmp_path / "fresh.jsonl"
    manager._transcript_switch_candidate = lambda *_: fresh
    manager._note_transcript_staleness = AsyncMock()
    stop_event = asyncio.Event()
    observe_task = asyncio.create_task(asyncio.sleep(5))
    try:
        switch = await SessionManager._watch_transcript_switch(
            manager, session, tmp_path / "old.jsonl", stop_event, observe_task
        )
        assert switch == fresh
    finally:
        observe_task.cancel()


async def test_a_rollover_onto_a_live_siblings_conversation_is_refused(
    tmp_path: Path,
) -> None:
    """An in-CLI `/resume` must not move a pane onto a conversation somebody is on.

    Verified live before this guard: pane B resumed pane A's live conversation from
    the `/resume` picker, the SessionStart hook reported it as an ordinary rollover,
    and B rekeyed onto A's conversation. `identity_collision_detected` fired in 1.1s
    — but the rollover had also moved B's `agent_lifecycle_id` onto the disputed
    conversation, so `_claude_owns_conversation` then reported *both* panes as
    rightful owners, the sweep healed neither, and both reported A's conversation and
    its tokens indefinitely.

    Refusing keeps B's identity intact and leaves the sweep able to act. B's CLI is
    genuinely elsewhere now, so its observation fails closed.
    """
    owner = real_session(agent_record(OWN, cwd=str(tmp_path)), tmp_path)
    owner.agent_lifecycle_id = OWN
    intruder = real_session(agent_record(STOLEN, cwd=str(tmp_path)), tmp_path)
    manager = fake_manager()
    manager.sessions = {OWN: owner, STOLEN: intruder}
    manager.adapters = {"claude": intruder.adapter}
    manager._await_registration = AsyncMock()
    manager.discard_hook_spool = lambda sid: None

    rolled = await SessionManager._apply_conversation_rollover(
        manager,
        intruder,
        native_id=OWN,
        transcript=tmp_path / f"{OWN}.jsonl",
        reason="conversation_rolled",
        source="resume",
    )

    assert rolled is False
    assert intruder.record.native_session_id == STOLEN
    assert intruder.agent_lifecycle_id != OWN
    assert intruder.record.observation_stale_since is not None
    assert owner.record.native_session_id == OWN
    refusals = [
        call for call in manager.events.emit.await_args_list
        if call.args and call.args[0] == "conversation_rollover_refused"
    ]
    assert refusals, "the refusal must be observable"

    # The sweep can now act, because the intruder's claim is unsupported.
    intruder.record.native_session_id = OWN  # as legacy corruption would leave it
    assert SessionManager._claude_owns_conversation(manager, intruder, OWN) is False


def test_a_codex_placeholder_id_counts_as_unbound() -> None:
    """The mux session id Codex carries until discovery is not a binding.

    `conversation_unbound` must not be a shape test: mux session ids are UUIDs, so
    asking whether the id merely looks like one reported every fresh Codex session as
    already bound and refused the only evidence that could bind it.
    """
    from swe_mux.observation import conversation_unbound

    codex = real_session(agent_record("codex-root", backend="codex"), Path("."))
    codex.record.native_session_id = codex.record.id
    assert conversation_unbound(codex) is True

    codex.record.native_session_id = "019fb0bf-ccb8-7b03-93d6-834839791245"
    assert conversation_unbound(codex) is False

    claude = real_session(agent_record(OWN), Path("."))
    assert conversation_unbound(claude) is False


async def test_codex_binds_its_conversation_from_its_own_turn_notify() -> None:
    """Codex's `agent-turn-complete` carries `thread-id` over the session's ingress.

    That is the only unforgeable evidence of which conversation this PTY runs: an
    outsider has no session secret with which to reach the ingress at all. Nothing on
    the filesystem separates its rollout from an interactive outsider's in the same
    cwd, so without this a Codex session can never be bound safely.
    """
    from swe_mux.observation import _bind_native_id_from_hook

    thread = "019fb0bf-ccb8-7b03-93d6-834839791245"
    session = real_session(agent_record("codex-root", backend="codex"), Path("."))
    session.record.native_session_id = session.record.id
    events = SimpleNamespace(emit=AsyncMock())

    await _bind_native_id_from_hook(session, {"thread-id": thread}, cast(Any, events))

    assert session.record.native_session_id == thread
    assert session.agent_lifecycle_id == thread

    # One-way only: a later hook naming something else must not rekey a bound session.
    await _bind_native_id_from_hook(
        session, {"thread-id": STOLEN}, cast(Any, events)
    )
    assert session.record.native_session_id == thread


def test_codex_turn_notify_dates_the_staleness_evidence() -> None:
    """Codex's only turn hook must count as transcript-backed evidence.

    `_note_transcript_staleness` is the fail-closed path for a Codex `/new` behind a
    sibling that cannot be ruled out — the one rollover with no session-start hook to
    fall back on. The ingress tests the **raw** event type, and Codex's notify arrives
    as `agent-turn-complete`, so leaving it out of this set left `last_turn_hook_ts`
    permanently unset and made the path unreachable for the only backend that needs
    it. Verified live: the rolled pane reported the abandoned conversation as live,
    with its retired token counts, for 200s.
    """
    from swe_mux.server import _HOOK_EVENT_TYPES, _TRANSCRIPT_BACKED_HOOK_EVENTS

    assert "agent-turn-complete" in _HOOK_EVENT_TYPES
    assert "agent-turn-complete" in _TRANSCRIPT_BACKED_HOOK_EVENTS


async def test_codex_observation_fails_closed_when_a_turn_ran_off_the_file(
    tmp_path: Path,
) -> None:
    """A turn hook after the followed file died proves we are on a retired thread."""
    import os

    record = agent_record("codex-root", backend="codex", cwd=str(tmp_path))
    session = real_session(record, tmp_path)
    manager = fake_manager()
    current = tmp_path / "old.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    dead = time.time() - 600
    os.utime(current, (dead, dead))
    # The CLI completed a turn long after the file we follow stopped changing.
    session.last_turn_hook_ts = time.time()

    await SessionManager._note_transcript_staleness(manager, session, current)

    assert record.observation_stale_since is not None
    assert "may have been replaced" in (record.parser_diagnostic or "")


async def test_a_quiet_codex_session_is_not_marked_stale(tmp_path: Path) -> None:
    """Silence is not evidence: an idle agent's transcript is quiet too.

    Guards the false-positive direction of the fix above — an idle session must not
    be declared stale just because nothing has been written for a while.
    """
    import os

    record = agent_record("codex-root", backend="codex", cwd=str(tmp_path))
    session = real_session(record, tmp_path)
    manager = fake_manager()
    current = tmp_path / "old.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    quiet = time.time() - 600
    os.utime(current, (quiet, quiet))
    # No turn since the file went quiet: the last turn ended when it was written.
    session.last_turn_hook_ts = quiet

    await SessionManager._note_transcript_staleness(manager, session, current)

    assert record.observation_stale_since is None


async def test_an_unconfirmed_rollover_keeps_the_claude_lifecycle_anchor(
    tmp_path: Path,
) -> None:
    record = agent_record(OWN, cwd=str(tmp_path))
    session = real_session(record, tmp_path)
    session.agent_lifecycle_id = OWN
    manager = fake_manager()
    manager.sessions = {record.id: session}
    manager.adapters = {"claude": session.adapter}
    manager.history.update_agent_summary = AsyncMock()
    manager.history.agent_run_ended = AsyncMock()
    manager._await_registration = AsyncMock()
    manager.discard_hook_spool = lambda sid: None

    rolled = await manager._apply_conversation_rollover(
        session,
        native_id=STOLEN,
        transcript=tmp_path / f"{STOLEN}.jsonl",
        reason="conversation_rolled",
        source="transcript_switch",
        confirmed=False,
    )

    assert rolled is True
    assert record.native_session_id == STOLEN
    # The anchor did not follow the guess, so reconciliation can heal back.
    assert session.agent_lifecycle_id == OWN


# ----------------------------------------------------------- adoption hardening


def test_adoption_refuses_a_rolled_conversation_claimed_by_a_sibling(
    tmp_path: Path,
) -> None:
    # The pane rolled (seq > 0), but the conversation it claims is the one minted
    # for a sibling pane's --session-id. Rolled-trust must not apply: fall back to
    # this pane's own spawn anchor and quarantine the corrupt run row.
    manager = fake_manager()
    corrupt = agent_record("aaaa-corrupt", cwd=str(tmp_path))
    corrupt.native_session_id = "bbbb-owner"
    corrupt.agent_run_id = "corrupt-run"
    corrupt.agent_run_seq = 3
    owner = agent_record("bbbb-owner", cwd=str(tmp_path))
    owner.native_session_id = ROLLED
    owner.agent_run_id = "owner-run"
    owner.agent_run_seq = 1
    records = {corrupt.id: corrupt, owner.id: owner}
    metas: dict[str, dict[str, Any]] = {corrupt.id: {}, owner.id: {}}
    manager.adapters = {
        "claude": SimpleNamespace(
            name="claude",
            recent_transcripts=lambda *_: [],
            transcript_native_id=lambda path: Path(path).stem,
        )
    }

    transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        corrupt, metas[corrupt.id], records, metas
    )

    assert previous is not None
    assert bad_run_id == "corrupt-run"
    assert corrupt.native_session_id == corrupt.id
    assert corrupt.agent_run_id == corrupt.id
    assert corrupt.agent_run_seq == 0


def test_adoption_still_trusts_a_rolled_conversation_nobody_claims(
    tmp_path: Path,
) -> None:
    # The legitimate /clear case: the rolled id is a fresh CLI-minted uuid no
    # sibling claims, and the first restart after it must not repair it away.
    manager = fake_manager()
    rolled = agent_record("aaaa-rolled", cwd=str(tmp_path))
    rolled.native_session_id = ROLLED
    rolled.agent_run_id = "rolled-run"
    rolled.agent_run_seq = 1
    sibling = agent_record("bbbb-owner", cwd=str(tmp_path))
    records = {rolled.id: rolled, sibling.id: sibling}
    metas: dict[str, dict[str, Any]] = {rolled.id: {}, sibling.id: {}}
    transcript_path = tmp_path / f"{ROLLED}.jsonl"
    transcript_path.write_text("{}\n", encoding="utf-8")
    manager.adapters = {
        "claude": SimpleNamespace(
            name="claude",
            recent_transcripts=lambda *_: [(time.time(), transcript_path, ROLLED)],
            transcript_native_id=lambda path: Path(path).stem,
        )
    }
    meta = {"transcript_path": str(transcript_path)}

    transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        rolled, meta, records, metas
    )

    assert previous is None
    assert bad_run_id is None
    assert rolled.native_session_id == ROLLED
    assert rolled.agent_run_id == "rolled-run"
    assert rolled.agent_run_seq == 1


# ------------------------------------------------------------- the live sweep


def sweep_manager(tmp_path: Path) -> tuple[Any, Session, Session]:
    manager = fake_manager()
    owner_record = agent_record(OWN, cwd=str(tmp_path))
    owner = real_session(owner_record, tmp_path)
    thief_record = agent_record(STOLEN, cwd=str(tmp_path))
    thief_record.native_session_id = OWN
    thief_record.agent_run_id = "thief-run"
    thief_record.agent_run_seq = 2
    thief = real_session(thief_record, tmp_path)
    manager.sessions = {OWN: owner, STOLEN: thief}
    manager._stop_observer = AsyncMock()
    started: list[tuple[Session, Path | None]] = []
    manager._start_observer = lambda session, path: started.append((session, path))
    manager._started_observers = started
    return manager, owner, thief


async def test_the_sweep_heals_a_claude_session_off_a_siblings_conversation(
    tmp_path: Path,
) -> None:
    manager, owner, thief = sweep_manager(tmp_path)

    await manager._reconcile_identity_collisions()

    assert manager.events.emit.await_args_list[0].args[0] == "identity_collision_detected"
    # The rightful owner is untouched; the thief is back on its own conversation.
    assert owner.record.native_session_id == OWN
    assert thief.record.native_session_id == STOLEN
    assert thief.record.agent_run_id == STOLEN
    assert thief.record.agent_run_seq == 0
    assert thief.agent_lifecycle_id == STOLEN
    manager.history.quarantine_misattributed_agent_run.assert_awaited_once_with(
        "thief-run", "live_identity_reconciled"
    )
    manager.history.reopen_agent_run.assert_awaited_once_with(STOLEN)
    assert manager._started_observers == [(thief, tmp_path / f"{STOLEN}.jsonl")]
    # The next pass sees no collision and does not re-report.
    manager.events.emit.reset_mock()
    await manager._reconcile_identity_collisions()
    manager.events.emit.assert_not_awaited()


async def test_the_sweep_prefers_the_hook_confirmed_anchor(tmp_path: Path) -> None:
    # A pane that legitimately rolled (hook-confirmed anchor) and later got
    # corrupted heals to the anchor conversation, repairing its current run row
    # in place rather than resurrecting the retired spawn conversation.
    manager, owner, thief = sweep_manager(tmp_path)
    thief.agent_lifecycle_id = ROLLED

    await manager._reconcile_identity_collisions()

    assert thief.record.native_session_id == ROLLED
    assert thief.record.agent_run_id == "thief-run"
    manager.history.quarantine_misattributed_agent_run.assert_not_awaited()
    manager.history.reset_run_transcript_copy.assert_awaited_once_with("thief-run")
    manager.history.reopen_agent_run.assert_awaited_once_with("thief-run")


async def test_the_sweep_never_heals_a_provable_owner(tmp_path: Path) -> None:
    # Both panes prove their claim (one minted it, one was spawned to resume it
    # and has not rolled): ambiguity is reported, never "fixed".
    manager, owner, thief = sweep_manager(tmp_path)
    thief.record.spawn_native_session_id = OWN
    thief.record.agent_run_seq = 0

    await manager._reconcile_identity_collisions()

    assert thief.record.native_session_id == OWN
    manager._stop_observer.assert_not_awaited()
    assert manager.events.emit.await_args_list[0].args[0] == "identity_collision_detected"


# ------------------------------------------------------------------ diagnostics


def test_fleet_status_health_reports_identity_collisions(tmp_path: Path) -> None:
    def fake(sid: str, native: str, path: str | None) -> Any:
        record = agent_record(sid, cwd=str(tmp_path))
        record.native_session_id = native
        return SimpleNamespace(
            record=record,
            transcript_path=Path(path) if path else None,
            status_health=lambda: {
                "counters": {},
                "watchdog_recoveries": 0,
                "watchdog_recovery_actions": {},
                "observer_restarts": 0,
                "reopen_blocked": 0,
                "contract_violations": 0,
                "terminals": {"proven": 0, "inferred": 0},
                "terminal_latencies": [],
                "seconds_in_state": 0.0,
                "seconds_since_evidence": 0.0,
            },
        )

    shared = str(tmp_path / f"{OWN}.jsonl")
    health = fleet_status_health(
        [fake("aaaa", OWN, shared), fake("bbbb", OWN, shared), fake("cccc", ROLLED, None)]
    )

    kinds = {(item["kind"], item["value"]) for item in health["identity_collisions"]}
    assert ("native_session_id", OWN) in kinds
    assert ("transcript_path", shared.casefold()) in kinds
    assert all(item["sessions"] == ["aaaa", "bbbb"] for item in health["identity_collisions"])
    assert "identity_collision" in health["alarm_reasons"]
    assert health["alarm"] is True


def test_fleet_status_health_is_quiet_without_collisions(tmp_path: Path) -> None:
    def fake(sid: str, native: str) -> Any:
        record = agent_record(sid, cwd=str(tmp_path))
        record.native_session_id = native
        return SimpleNamespace(
            record=record,
            transcript_path=None,
            status_health=lambda: {
                "counters": {},
                "watchdog_recoveries": 0,
                "watchdog_recovery_actions": {},
                "observer_restarts": 0,
                "reopen_blocked": 0,
                "contract_violations": 0,
                "terminals": {"proven": 0, "inferred": 0},
                "terminal_latencies": [],
                "seconds_in_state": 0.0,
                "seconds_since_evidence": 0.0,
            },
        )

    health = fleet_status_health([fake("aaaa", OWN), fake("bbbb", ROLLED)])

    assert health["identity_collisions"] == []
    assert "identity_collision" not in health["alarm_reasons"]
