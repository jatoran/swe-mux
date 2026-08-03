"""Phase 5.4: an in-CLI `/clear` or `/new` is a new agent run, not a file swap.

The conversation under a live PTY can be replaced without the session, the run,
the hook secret, or the MCP token changing. Before this phase the daemon either
kept tailing the retired transcript (sibling present) or rekeyed `native_session_id`
in place under the same `agent_run_id` (no sibling) — and every run-scoped consumer
kept accumulating across a boundary it could not see.

These tests pin the boundary itself: the rollover transaction, both triggers, the
fail-closed path when the successor cannot be corroborated, and the downstream
consumers whose correctness depends on the run id meaning "one conversation".
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from swe_mux.delivery_readiness import DeliveryReadinessTracker
from swe_mux.models import MuxEvent, SessionRecord
from swe_mux.observation import (
    _record_parser_observation,
    _transcript_authoritative,
    conversation_rollover_decision,
    foreign_conversation_hook_id,
)
from swe_mux.server import _branch_source_id, hook_event_payload
from swe_mux.session import Session, SessionManager
from tests.support.detection_replay import ReplaySession, VirtualClock

CLEARED = "9d1f0c2a-4b6e-4f2a-9c31-0e7a5b6d8f11"
ORIGINAL = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


def agent_record(
    *, backend: str = "claude", cwd: str = ".", native_id: str = ORIGINAL
) -> SessionRecord:
    record = SessionRecord(
        "mux-id", "agent", "default", backend, native_id, cwd, f"{backend}.exe", []
    )
    record.spawn_backend = backend
    record.spawn_native_session_id = record.id
    record.agent_run_id = record.id
    record.agent_run_started_at = record.created_at
    record.run_cwd = cwd
    return record


def rollover_manager(record: SessionRecord) -> tuple[Any, Session]:
    pty = SimpleNamespace(graceful_exit="", isalive=lambda: True)
    adapter = SimpleNamespace(
        name=record.backend,
        graceful_exit_keys=lambda: "exit\r",
        transcript_path=lambda native_id, cwd: Path(cwd) / f"{native_id}.jsonl",
    )
    session = Session(record, cast(Any, pty), cast(Any, adapter), 32, "secret")
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {record.id: session}
    manager.adapters = {record.backend: adapter}
    manager.events = SimpleNamespace(emit=AsyncMock())
    manager.history = SimpleNamespace(
        update_agent_summary=AsyncMock(),
        agent_run_ended=AsyncMock(),
        session_promoted=AsyncMock(),
    )
    manager._await_registration = AsyncMock()
    manager.discard_hook_spool = lambda sid: None
    return manager, session


# ------------------------------------------------------------- the hook trigger


def hook_session(native_id: str = ORIGINAL, *, backend: str = "claude") -> Any:
    return cast(
        Any,
        SimpleNamespace(
            record=agent_record(backend=backend, native_id=native_id),
            agent_lifecycle_id=None,
        ),
    )


def test_a_session_start_reporting_a_different_conversation_is_a_rollover() -> None:
    session = hook_session()
    payload = {"session_id": CLEARED, "source": "clear"}
    decision = conversation_rollover_decision(session, "SessionStart", payload)
    assert decision.roll_to == CLEARED
    assert decision.refused is None


def test_compact_reports_the_same_id_and_never_rolls() -> None:
    # /compact keeps the conversation; the CLI reports the id it already has, so
    # the id comparison alone rules it out before any source is consulted.
    session = hook_session()
    for source in ("compact", "startup", "resume", "clear"):
        payload = {"session_id": ORIGINAL, "source": source}
        decision = conversation_rollover_decision(session, "SessionStart", payload)
        assert decision == (None, None, None)


def test_a_fresh_process_announcing_itself_never_rolls_a_bound_session() -> None:
    # The regression this pins, observed live 2026-07-31: a nested `claude`
    # launched by this session's own tool call inherits the hook wiring and
    # fires a root SessionStart with `source: "startup"` and its own fresh id.
    # A bound session's own CLI can only replace its conversation in place
    # (`clear`/`resume`); a `startup` with a new id is another process, and
    # adopting it hands the session's identity — and its "awaiting approval"
    # display — to a child nobody can see or answer.
    session = hook_session()
    payload = {"session_id": CLEARED, "source": "startup"}
    decision = conversation_rollover_decision(session, "SessionStart", payload)
    assert decision.roll_to is None
    assert decision.refused == CLEARED
    assert decision.refusal_reason == "foreign_process_startup"


def test_a_replacement_reported_from_another_cwd_never_rolls() -> None:
    # `/clear` and in-CLI `/resume` cannot move the CLI's working directory, so a
    # candidate whose hook cwd is elsewhere is a foreign process regardless of
    # what source it claims.
    session = hook_session()
    session.record.run_cwd = "D:/PROJECTS/repo"
    for source in ("clear", "resume"):
        payload = {"session_id": CLEARED, "source": source, "cwd": "C:/Temp/scratch"}
        decision = conversation_rollover_decision(session, "SessionStart", payload)
        assert decision.roll_to is None
        assert decision.refused == CLEARED
        assert decision.refusal_reason == "cwd_mismatch"


def test_a_replacement_in_the_session_cwd_still_rolls() -> None:
    session = hook_session()
    session.record.run_cwd = "D:/PROJECTS/repo"
    for source in ("clear", "resume"):
        payload = {
            "session_id": CLEARED,
            "source": source,
            # Same directory through a different spelling: the comparison must
            # normalize, not string-match.
            "cwd": "D:\\PROJECTS\\other\\..\\repo",
        }
        decision = conversation_rollover_decision(session, "SessionStart", payload)
        assert decision.roll_to == CLEARED


def test_a_replacement_without_a_cwd_or_source_still_rolls() -> None:
    # An older shim or CLI that omits either field must not lose `/clear`
    # tracking: only positive evidence of a foreign process refuses.
    session = hook_session()
    decision = conversation_rollover_decision(
        session, "SessionStart", {"session_id": CLEARED}
    )
    assert decision.roll_to == CLEARED


def test_an_unbound_session_binds_instead_of_rolling() -> None:
    # Nothing to retire yet: `claude --continue` promotes with an empty native id
    # and the one-way bind path owns that case.
    session = hook_session(native_id="mux-id")
    payload = {"session_id": CLEARED, "source": "startup"}
    assert conversation_rollover_decision(session, "SessionStart", payload).roll_to is None


def test_a_subagent_session_start_never_rolls_the_root_run() -> None:
    session = hook_session()
    payload = {"session_id": CLEARED, "source": "clear", "isSidechain": True}
    assert conversation_rollover_decision(session, "SessionStart", payload) == (
        None,
        None,
        None,
    )


def test_a_shell_and_a_non_session_start_hook_never_roll() -> None:
    shell = hook_session(backend="shell")
    assert conversation_rollover_decision(
        shell, "SessionStart", {"session_id": CLEARED, "source": "clear"}
    ) == (None, None, None)
    agent = hook_session()
    assert conversation_rollover_decision(
        agent, "UserPromptSubmit", {"session_id": CLEARED}
    ) == (None, None, None)


def test_a_redelivered_hook_for_the_current_conversation_is_a_no_op() -> None:
    # Hooks are a retried side channel; a duplicate must not roll a second time.
    session = hook_session()
    session.agent_lifecycle_id = CLEARED
    session.record.native_session_id = CLEARED
    payload = {"session_id": CLEARED, "source": "clear"}
    assert conversation_rollover_decision(session, "SessionStart", payload) == (
        None,
        None,
        None,
    )


# ----------------------------------------------- foreign-conversation hook filter


def test_a_bound_sessions_state_only_listens_to_its_own_conversation() -> None:
    session = hook_session()
    # The bound conversation and the spawn conversation are never foreign; any
    # other well-formed conversation id is.
    assert foreign_conversation_hook_id(session, {"session_id": ORIGINAL}) is None
    assert foreign_conversation_hook_id(session, {"session_id": CLEARED}) == CLEARED
    session.record.native_session_id = CLEARED
    spawn = {"session_id": session.record.id}
    # Not foreign even while bound elsewhere: the heal path owns that evidence.
    assert foreign_conversation_hook_id(session, spawn) is None


def test_the_foreign_filter_stands_down_while_unbound_and_for_other_backends() -> None:
    unbound = hook_session(native_id="mux-id")
    assert foreign_conversation_hook_id(unbound, {"session_id": CLEARED}) is None
    codex = hook_session(backend="codex")
    assert foreign_conversation_hook_id(codex, {"session_id": CLEARED}) is None
    # A payload that names no conversation cannot be judged.
    assert foreign_conversation_hook_id(hook_session(), {}) is None


# ----------------------------------------------------- healing a stolen identity

MUX_PANE = "7f0a1b2c-3d4e-4f5a-8b9c-0d1e2f3a4b5c"


def stolen_manager(tmp_path: Path) -> tuple[Any, Session]:
    """A pane whose identity a nested child rolled onto its own conversation."""
    record = agent_record(cwd=str(tmp_path))
    record.id = MUX_PANE
    record.spawn_native_session_id = MUX_PANE
    record.native_session_id = CLEARED
    record.agent_run_id = "stolen-run"
    record.agent_run_seq = 5
    manager, session = rollover_manager(record)
    session.agent_lifecycle_id = CLEARED
    manager._stop_observer = AsyncMock()
    manager._start_observer = lambda _session, _path: None
    manager._reset_provider_observation = lambda _record: None
    manager.history.quarantine_misattributed_agent_run = AsyncMock()
    manager.history.reset_run_transcript_copy = AsyncMock()
    manager.history.reopen_agent_run = AsyncMock()
    return manager, session


async def test_the_spawn_conversation_speaking_heals_a_stolen_binding(
    tmp_path: Path,
) -> None:
    # The pane was spawned with `--session-id MUX_PANE`, so a hook naming exactly
    # that conversation can only be this PTY's own CLI. It speaking while the
    # record is bound to a child's conversation proves the binding is corruption.
    manager, session = stolen_manager(tmp_path)

    healed = await manager.maybe_heal_from_own_conversation_hook(
        session, {"session_id": MUX_PANE}
    )

    record = session.record
    assert healed is True
    assert record.native_session_id == MUX_PANE
    assert session.agent_lifecycle_id == MUX_PANE
    assert record.agent_run_id == MUX_PANE
    assert record.agent_run_seq == 0
    manager.history.quarantine_misattributed_agent_run.assert_awaited_once_with(
        "stolen-run", "live_identity_reconciled"
    )
    reconciled = manager.events.emit.await_args
    assert reconciled.args[0] == "session_identity_reconciled"
    assert reconciled.kwargs["native_session_id"] == MUX_PANE
    assert reconciled.kwargs["trigger"] == "own_conversation_hook"


async def test_a_retired_spawn_conversation_never_heals_back(tmp_path: Path) -> None:
    # After a legitimate `/clear` the spawn conversation is recorded as retired;
    # a stale hook it spooled before dying must not un-clear the session.
    manager, session = stolen_manager(tmp_path)
    session.ignored_detection_runs.add(("claude", MUX_PANE))

    healed = await manager.maybe_heal_from_own_conversation_hook(
        session, {"session_id": MUX_PANE}
    )

    assert healed is False
    assert session.record.native_session_id == CLEARED
    manager.history.reopen_agent_run.assert_not_awaited()


async def test_a_healthy_binding_is_left_alone(tmp_path: Path) -> None:
    manager, session = stolen_manager(tmp_path)
    session.record.native_session_id = MUX_PANE

    healed = await manager.maybe_heal_from_own_conversation_hook(
        session, {"session_id": MUX_PANE}
    )

    assert healed is False
    manager.history.reopen_agent_run.assert_not_awaited()


async def test_only_the_spawn_conversation_can_trigger_the_heal(tmp_path: Path) -> None:
    manager, session = stolen_manager(tmp_path)

    for payload in ({"session_id": ORIGINAL}, {"session_id": "not-a-uuid"}, {}):
        assert (
            await manager.maybe_heal_from_own_conversation_hook(session, payload)
            is False
        )
    assert session.record.native_session_id == CLEARED


def test_the_start_source_survives_the_event_envelope_collision() -> None:
    payload = hook_event_payload({"session_id": "x", "source": "clear", "cwd": "D:/p"})
    assert payload["start_source"] == "clear"
    assert "source" not in payload


# ----------------------------------------------------------- the rollover itself


async def test_a_rollover_retires_the_run_and_opens_a_successor(tmp_path: Path) -> None:
    record = agent_record(cwd=str(tmp_path))
    record.tokens_in = 5_000
    record.tokens_out = 900
    record.context_window = 200_000
    record.context_pct = 74.0
    record.context_peak_pct = 91.0
    record.compaction_count = 2
    record.model = "claude-opus-5"
    record.parser_status = "ready"
    record.parser_events_seen = 400
    manager, session = rollover_manager(record)
    original_run_id = record.agent_run_id
    transcript = tmp_path / f"{CLEARED}.jsonl"

    rolled = await manager._apply_conversation_rollover(
        session,
        native_id=CLEARED,
        transcript=transcript,
        reason="conversation_rolled",
        source="clear",
    )

    assert rolled is True
    assert record.agent_run_id != original_run_id
    assert record.agent_run_seq == 1
    assert record.native_session_id == CLEARED
    assert session.agent_lifecycle_id == CLEARED
    assert session.transcript_path == transcript
    # Nothing that measured the retired conversation may carry over.
    assert (record.tokens_in, record.tokens_out) == (0, 0)
    assert (record.context_window, record.context_pct, record.context_peak_pct) == (0, 0.0, 0.0)
    assert record.compaction_count == 0
    assert record.model is None
    assert record.parser_status == "waiting"
    assert record.parser_events_seen == 0
    # The retired conversation is closed against its own final numbers, and the
    # successor gets its own history row rather than overwriting the old one.
    manager.history.update_agent_summary.assert_awaited_once()
    assert manager.history.agent_run_ended.await_args.args[1] == "conversation_rolled"
    assert manager.history.session_promoted.await_args.args[1] == str(transcript)
    event = manager.events.emit.await_args
    assert event.args[0] == "agent_conversation_rolled"
    assert event.kwargs["previous_agent_run_id"] == original_run_id
    assert event.kwargs["agent_run_id"] == record.agent_run_id
    assert event.kwargs["previous_native_session_id"] == ORIGINAL
    assert event.kwargs["native_session_id"] == CLEARED


async def test_the_retired_conversation_is_never_re_adopted(tmp_path: Path) -> None:
    record = agent_record(cwd=str(tmp_path))
    manager, session = rollover_manager(record)

    await manager._apply_conversation_rollover(
        session,
        native_id=CLEARED,
        transcript=tmp_path / f"{CLEARED}.jsonl",
        reason="conversation_rolled",
        source="clear",
    )

    assert ("claude", ORIGINAL) in session.ignored_detection_runs


async def test_rolling_to_the_current_conversation_is_a_no_op(tmp_path: Path) -> None:
    record = agent_record(cwd=str(tmp_path))
    manager, session = rollover_manager(record)

    for native_id in (ORIGINAL, "", record.native_session_id):
        assert (
            await manager._apply_conversation_rollover(
                session,
                native_id=native_id,
                transcript=None,
                reason="conversation_rolled",
                source="clear",
            )
            is False
        )
    assert record.agent_run_seq == 0
    manager.history.agent_run_ended.assert_not_awaited()


async def test_an_ended_session_never_rolls(tmp_path: Path) -> None:
    record = agent_record(cwd=str(tmp_path))
    record.state = "exited"
    manager, session = rollover_manager(record)

    rolled = await manager._apply_conversation_rollover(
        session,
        native_id=CLEARED,
        transcript=None,
        reason="conversation_rolled",
        source="clear",
    )

    assert rolled is False
    assert record.agent_run_seq == 0


async def test_the_public_entry_point_restarts_observation_on_the_new_file(
    tmp_path: Path,
) -> None:
    record = agent_record(cwd=str(tmp_path))
    manager, session = rollover_manager(record)
    started: list[Path | None] = []
    manager._stop_observer = AsyncMock()
    manager._start_observer = lambda _session, path: started.append(path)

    rolled = await manager.roll_agent_conversation(
        record.id, native_id=CLEARED, reason="conversation_rolled", source="clear"
    )

    assert rolled is True
    # Stopped before the identity was rewritten: a live observer re-derives the
    # native id from the file it is tailing and would put the retired id back.
    manager._stop_observer.assert_awaited_once()
    assert started == [tmp_path / f"{CLEARED}.jsonl"]


# ------------------------------------------------------- the sibling-gate tightening


def sibling(
    cwd: Path, *, transcript: Path | None, last_activity_ts: float, backend: str = "claude"
) -> Any:
    record = agent_record(backend=backend, cwd=str(cwd), native_id="sibling-native")
    record.id = "sibling"
    record.last_activity_ts = last_activity_ts
    return SimpleNamespace(
        record=record, transcript_path=transcript, pending_agent_backends=set()
    )


def switch_fixture(tmp_path: Path) -> tuple[Any, Any, Path, Path]:
    current = tmp_path / "old.jsonl"
    current.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 30
    os.utime(current, (stale, stale))
    fresh = tmp_path / "new.jsonl"
    fresh.write_text("{}\n", encoding="utf-8")
    manager = cast(Any, SessionManager.__new__(SessionManager))
    record = agent_record(cwd=str(tmp_path))
    record.last_activity_ts = time.time()
    session = cast(
        Any,
        SimpleNamespace(
            record=record,
            transcript_path=current,
            ignored_detection_runs=set(),
            pending_agent_backends=set(),
            adapter=SimpleNamespace(
                name="claude",
                recent_transcripts=lambda cwd, created_at: [
                    (fresh.stat().st_mtime, fresh, "native-new")
                ],
            ),
        ),
    )
    manager.sessions = {"mux-id": session}
    return manager, session, current, fresh


def test_a_sibling_still_writing_its_own_transcript_no_longer_blocks(
    tmp_path: Path,
) -> None:
    # The old blanket rule made a `/clear` unfollowable in any project with two
    # agents open. A sibling whose own file kept being written after the candidate
    # appeared is demonstrably still on its own conversation.
    manager, session, current, fresh = switch_fixture(tmp_path)
    sibling_path = tmp_path / "sibling.jsonl"
    sibling_path.write_text("{}\n", encoding="utf-8")
    still_writing = time.time() + 2
    os.utime(sibling_path, (still_writing, still_writing))
    manager.sessions["sibling"] = sibling(
        tmp_path, transcript=sibling_path, last_activity_ts=time.time()
    )
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_a_sibling_whose_pty_was_silent_no_longer_blocks(tmp_path: Path) -> None:
    manager, session, current, fresh = switch_fixture(tmp_path)
    manager.sessions["sibling"] = sibling(
        tmp_path, transcript=None, last_activity_ts=time.time() - 600
    )
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh


def test_a_quiet_sibling_that_was_also_talking_still_blocks(tmp_path: Path) -> None:
    # Its transcript went quiet and its PTY did not: indistinguishable from a
    # sibling that just cleared. Uncertainty keeps the conservative answer.
    manager, session, current, _fresh = switch_fixture(tmp_path)
    quiet = tmp_path / "sibling.jsonl"
    quiet.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 300
    os.utime(quiet, (stale, stale))
    manager.sessions["sibling"] = sibling(
        tmp_path, transcript=quiet, last_activity_ts=time.time()
    )
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_an_unpromoted_shell_launching_an_agent_still_blocks_everything(
    tmp_path: Path,
) -> None:
    manager, session, current, _fresh = switch_fixture(tmp_path)
    shell = sibling(tmp_path, transcript=None, last_activity_ts=0.0, backend="shell")
    shell.record.backend = "shell"
    shell.pending_agent_backends = {"claude"}
    manager.sessions["shell"] = shell
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


# ------------------------------------------------------------- failing closed


def stale_manager(tmp_path: Path, *, last_turn_hook_ts: float) -> tuple[Any, Any, Path]:
    transcript = tmp_path / "dead.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    dead = time.time() - 600
    os.utime(transcript, (dead, dead))
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.events = SimpleNamespace(emit=AsyncMock())
    record = agent_record(cwd=str(tmp_path))
    record.parser_status = "ready"
    session = cast(
        Any,
        SimpleNamespace(
            record=record,
            # Always recent: an idle session gets `Notification:idle_prompt` a minute
            # after every turn, so this being fresh must not by itself mean anything.
            last_hook_ts=time.time(),
            last_turn_hook_ts=last_turn_hook_ts,
            publish_update=lambda: None,
        ),
    )
    return manager, session, transcript


async def test_a_turn_hook_after_a_dead_transcript_marks_observation_stale(
    tmp_path: Path,
) -> None:
    # Codex's hookless fallback cannot see `/new` behind an unresolvable sibling and
    # has no positive signal at all. A turn that must have written records, followed
    # by a file that never changed, is the proof we are watching the wrong one.
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=time.time())

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is not None
    assert manager.events.emit.await_args.args[0] == "observation_stale"


async def test_an_idle_prompt_notification_never_marks_a_session_stale(
    tmp_path: Path,
) -> None:
    # Regression, live 2026-07-29: keying staleness off *any* hook flagged 8 healthy
    # idle agents across 4 sessions and zero real ones. `Notification:idle_prompt`
    # fires ~60 s after a turn ends to say the agent is waiting — it is guaranteed to
    # arrive with no transcript activity, which is exactly the shape being tested for.
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=0.0)
    session.last_hook_ts = time.time()

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    manager.events.emit.assert_not_awaited()


def test_only_transcript_backed_hooks_date_the_staleness_evidence() -> None:
    from swe_mux.server import _TRANSCRIPT_BACKED_HOOK_EVENTS

    for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        assert event in _TRANSCRIPT_BACKED_HOOK_EVENTS
    # Lifecycle and waiting hooks carry no transcript records; subagent records go to
    # sidechain files rather than the root transcript being followed.
    for event in ("Notification", "SessionStart", "SessionEnd", "SubagentStop"):
        assert event not in _TRANSCRIPT_BACKED_HOOK_EVENTS


async def test_a_merely_idle_session_is_not_stale(tmp_path: Path) -> None:
    # Silence on both sides is an idle agent, not a replaced conversation.
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=0.0)
    session.last_hook_ts = 0.0

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    manager.events.emit.assert_not_awaited()


async def test_staleness_is_announced_once(tmp_path: Path) -> None:
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=time.time())

    await SessionManager._note_transcript_staleness(manager, session, transcript)
    first = session.record.observation_stale_since
    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since == first
    assert manager.events.emit.await_count == 1


def test_a_stale_transcript_loses_its_authority_over_hooks() -> None:
    # This is the half that froze sessions: the parser stayed `ready`, so the hook
    # fallback was dropped as redundant to a transcript that could no longer report
    # anything at all.
    session = cast(Any, SimpleNamespace(record=agent_record()))
    session.record.parser_status = "ready"
    assert _transcript_authoritative(session) is True
    session.record.observation_stale_since = time.time()
    assert _transcript_authoritative(session) is False


async def test_a_record_on_the_followed_transcript_clears_staleness() -> None:
    session = cast(
        Any,
        SimpleNamespace(
            record=agent_record(),
            subscribers=(),
            meta_sink=None,
        ),
    )
    session.record.parser_status = "ready"
    session.record.observation_stale_since = time.time()
    events = SimpleNamespace(emit=AsyncMock())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("swe_mux.observation._publish_update", lambda _session: None)
        await _record_parser_observation(session, cast(Any, events), True, "sig")
    assert session.record.observation_stale_since is None


def test_delivery_hard_blocks_on_a_stale_transcript() -> None:
    clock = VirtualClock()
    session = ReplaySession("claude", clock)
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    session.record.parser_status = "ready"
    # The screen Claude Code actually draws its prompt on (`delivery-readiness.md`).
    session.terminal_mode = "alternate"
    session.terminal_mode_updated_at = clock.monotonic()
    tracker.observe(
        MuxEvent(time.time(), "replay-session", "transcript", "turn_started", {}), session
    )
    tracker.observe(
        MuxEvent(
            time.time(),
            "replay-session",
            "transcript",
            "turn_ended",
            {"outcome": "completed"},
        ),
        session,
    )
    clock.advance(5.0)
    session.terminal_mode_updated_at = clock.monotonic()
    assert tracker.evaluate(session)["delivery_state"] == "safe"

    session.record.observation_stale_since = time.time()
    evaluation = tracker.evaluate(session)
    assert evaluation["delivery_state"] == "blocked"
    assert "transcript_stale" in evaluation["reasons"]


# ------------------------------------------------------------ downstream consumers


def test_branch_forks_the_conversation_the_user_is_actually_in() -> None:
    # `/clear` used to leave `agent_lifecycle_id` on the retired conversation, so
    # Branch reopened the *predecessor* as the "original" pane.
    record = agent_record()
    session = cast(Any, SimpleNamespace(record=record, agent_lifecycle_id=None))
    assert _branch_source_id(session) == ORIGINAL

    record.native_session_id = CLEARED
    record.agent_run_seq = 1
    session.agent_lifecycle_id = CLEARED
    assert _branch_source_id(session) == CLEARED


async def test_an_observer_refuses_to_read_a_stale_transcript() -> None:
    # The transcript parses fine; it is simply not this session's conversation any
    # more. An observer that read it would title and summarize work the user left.
    from swe_mux.automation import AutomationEngine

    engine = cast(Any, AutomationEngine.__new__(AutomationEngine))
    record = agent_record()
    record.observation_stale_since = time.time()
    session = SimpleNamespace(record=record, transcript_path=Path("live.jsonl"))
    engine.config = SimpleNamespace(automation_enabled=True)
    engine.sessions = SimpleNamespace(sessions={record.id: session})
    event = SimpleNamespace(
        agent_run_id=record.agent_run_id,
        session_id=record.id,
        capability="semantic",
        source="transcript",
    )

    with pytest.raises(ValueError, match="stale"):
        await engine._llm("firing", cast(Any, None), cast(Any, event), {})


async def test_a_rollover_strands_queue_items_bound_to_the_retired_run() -> None:
    from swe_mux.prompt_queue import PromptQueueService

    service = cast(Any, PromptQueueService.__new__(PromptQueueService))
    stranded: list[tuple[str, str]] = []
    seen = asyncio.Event()

    async def strand(session_id: str, reason: str) -> None:
        stranded.append((session_id, reason))
        seen.set()

    service._strand = strand
    queue: asyncio.Queue[MuxEvent] = asyncio.Queue()
    service._queue = queue
    await queue.put(
        MuxEvent(time.time(), "mux-id", "daemon", "agent_conversation_rolled", {})
    )

    consume = asyncio.create_task(service._consume())
    try:
        await asyncio.wait_for(seen.wait(), timeout=2)
    finally:
        consume.cancel()
        await asyncio.gather(consume, return_exceptions=True)

    assert stranded == [("mux-id", "target agent conversation was replaced")]


# ------------------------------------------------------------ surviving a restart


def rolled_root_record(tmp_path: Path, transcript: Path) -> SessionRecord:
    record = SessionRecord(
        "root",
        "agent",
        "default",
        "claude",
        CLEARED,
        str(tmp_path),
        "claude.exe",
        [],
    )
    record.spawn_backend = "claude"
    record.spawn_native_session_id = ORIGINAL
    record.spawn_cwd = str(tmp_path)
    record.agent_run_id = "rolled-run-id"
    record.agent_run_seq = 1
    record.agent_run_started_at = time.time()
    record.run_cwd = str(tmp_path)
    del transcript
    return record


def adoption_manager(tmp_path: Path) -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {}
    manager.adapters = {
        "claude": SimpleNamespace(
            name="claude",
            transcript_native_id=lambda path: path.stem,
            recent_transcripts=lambda cwd, created_at: [
                (item.stat().st_mtime, item, item.stem)
                for item in sorted(Path(cwd).glob("*.jsonl"))
            ],
        )
    }
    return manager


def test_a_rolled_root_run_survives_adoption_without_being_quarantined(
    tmp_path: Path,
) -> None:
    # A root agent's run id is normally its session id and anything else is
    # corruption. `agent_run_seq` is what distinguishes the daemon's own successor
    # run from that corruption — without it the first daemon restart after a
    # `/clear` repaired the live run away and quarantined its history row.
    live = tmp_path / f"{CLEARED}.jsonl"
    live.write_text("{}\n", encoding="utf-8")
    record = rolled_root_record(tmp_path, live)
    manager = adoption_manager(tmp_path)
    meta = {"transcript_path": str(live)}

    transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        record, meta, {record.id: record}, {record.id: meta}
    )

    assert transcript == live
    assert bad_run_id is None
    assert previous is None
    assert record.agent_run_id == "rolled-run-id"
    assert record.agent_run_seq == 1
    assert record.native_session_id == CLEARED


def test_an_unexplained_root_run_id_is_still_repaired(tmp_path: Path) -> None:
    live = tmp_path / f"{ORIGINAL}.jsonl"
    live.write_text("{}\n", encoding="utf-8")
    record = rolled_root_record(tmp_path, live)
    record.agent_run_seq = 0
    record.native_session_id = ORIGINAL
    manager = adoption_manager(tmp_path)
    meta = {"transcript_path": str(live)}

    _transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        record, meta, {record.id: record}, {record.id: meta}
    )

    assert bad_run_id == "rolled-run-id"
    assert previous is not None
    assert record.agent_run_id == record.id
