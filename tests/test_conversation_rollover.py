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

from swe_mux.cli_state import ParkedMove
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

from .host_paths import OTHER_ABS_ROOT, abs_path

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
    session.record.run_cwd = abs_path("PROJECTS", "repo")
    for source in ("clear", "resume"):
        payload = {
            "session_id": CLEARED,
            "source": source,
            "cwd": f"{OTHER_ABS_ROOT}/Temp/scratch",
        }
        decision = conversation_rollover_decision(session, "SessionStart", payload)
        assert decision.roll_to is None
        assert decision.refused == CLEARED
        assert decision.refusal_reason == "cwd_mismatch"


def test_a_replacement_in_the_session_cwd_still_rolls() -> None:
    session = hook_session()
    session.record.run_cwd = abs_path("PROJECTS", "repo")
    for source in ("clear", "resume"):
        payload = {
            "session_id": CLEARED,
            "source": source,
            # Same directory through a different spelling: the comparison must
            # normalize, not string-match. Built from the host's own separator,
            # because a Windows-shaped literal is not a path at all on POSIX and
            # the assertion would then prove nothing about normalization.
            "cwd": str(Path(abs_path("PROJECTS", "other")) / ".." / "repo"),
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


def test_the_foreign_filter_stands_down_only_while_unbound() -> None:
    unbound = hook_session(native_id="mux-id")
    assert foreign_conversation_hook_id(unbound, {"session_id": CLEARED}) is None
    codex = hook_session(backend="codex")
    assert foreign_conversation_hook_id(codex, {"session_id": CLEARED}) == CLEARED
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


async def test_a_backgrounded_conversation_is_followed_onto_the_jobs_transcript(
    tmp_path: Path,
) -> None:
    """Claude parks a pane's conversation into a job and no hook reports it.

    The CLI's own `parkedJobId` is the only report of the move, so the pane is
    rolled from it exactly as a SessionStart-reported `/clear` rolls it — onto
    the transcript the job publishes, not one re-derived from the pane's cwd.
    """
    record = agent_record(cwd=str(tmp_path))
    manager, session = rollover_manager(record)
    manager._stop_observer = AsyncMock()
    manager._start_observer = lambda _session, _path: None
    parked_transcript = tmp_path / "elsewhere" / f"{CLEARED}.jsonl"

    await manager._follow_parked_conversation(
        ParkedMove(
            session_id=record.id,
            native_session_id=CLEARED,
            transcript=str(parked_transcript),
            job_id="job1",
            job_state="working",
        )
    )

    assert record.native_session_id == CLEARED
    assert session.transcript_path == parked_transcript
    # A retired run, so the titler names the conversation the work is actually in.
    assert record.agent_run_seq == 1
    manager.history.agent_run_ended.assert_awaited_once()
    assert manager.history.agent_run_ended.await_args.args[1] == "conversation_backgrounded"


async def test_a_parked_move_for_an_unknown_session_is_ignored(tmp_path: Path) -> None:
    """Panes close between the threaded poll and the loop that applies its moves."""
    record = agent_record(cwd=str(tmp_path))
    manager, _ = rollover_manager(record)
    manager._stop_observer = AsyncMock()

    await manager._follow_parked_conversation(
        ParkedMove(
            session_id="gone",
            native_session_id=CLEARED,
            transcript="",
            job_id="job1",
            job_state="done",
        )
    )

    manager._stop_observer.assert_not_awaited()


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


def stale_manager(
    tmp_path: Path,
    *,
    last_turn_hook_ts: float,
    transcript_growth_ts: float = 0.0,
    transcript_record_ts: float = 0.0,
) -> tuple[Any, Any, Path]:
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
            # The tailer's own reading of the followed file. Zero means "no growth
            # observed", which is what an actually-dead transcript looks like.
            transcript_growth_ts=transcript_growth_ts,
            transcript_record_ts=transcript_record_ts,
            observation_stale_reason=None,
            state_transitions=[],
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


async def test_a_missing_transcript_with_a_turn_hook_is_stale(tmp_path: Path) -> None:
    # The hardest version of "the conversation moved": the file is not quiet, it
    # is *gone*, so there is no timestamp to be quiet and this used to return
    # before deciding anything. Measured live 2026-08-06 on a session that
    # entered a Claude native worktree - the CLI relocates the transcript to the
    # new cwd's project directory - the observer sat on a path that no longer
    # existed while `parser_status` stayed frozen at `ready` from its last
    # successful read. Because `_transcript_authoritative` reads that field, the
    # hook tier stayed suppressed as redundant to a transcript that could no
    # longer report anything, and the session latched `idle` for four minutes
    # while its own screen showed the working spinner and root turn hooks kept
    # arriving 8 s apart.
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=time.time())
    transcript.unlink()

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is not None
    assert manager.events.emit.await_args.kwargs["reason"] == "transcript_missing"


async def test_a_missing_transcript_without_a_turn_hook_stays_silent(tmp_path: Path) -> None:
    # The observer is aimed before the CLI creates the file, so "missing" on its
    # own is an ordinary startup race. Only a turn hook makes it evidence: the
    # CLI ran a turn and none of it landed where we are looking.
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=0.0)
    transcript.unlink()

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    manager.events.emit.assert_not_awaited()


def relocation_fixture(tmp_path: Path) -> tuple[Any, Any, Path, Path]:
    """A Claude session whose transcript moved to a worktree's project dir."""
    from swe_mux.adapters.claude import ClaudeAdapter, encode_cwd

    native = "3763350c-df0c-4f85-9e93-7f73ffba2c07"
    projects = tmp_path / "projects"
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "feature"
    worktree.mkdir(parents=True)
    spawn_dir = projects / encode_cwd(tmp_path / "repo")
    spawn_dir.mkdir(parents=True)
    current = spawn_dir / f"{native}.jsonl"
    moved_dir = projects / encode_cwd(worktree)
    moved_dir.mkdir(parents=True)
    moved = moved_dir / f"{native}.jsonl"
    moved.write_text("{}\n", encoding="utf-8")

    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.events = SimpleNamespace(emit=AsyncMock())
    record = agent_record(cwd=str(tmp_path / "repo"))
    record.native_session_id = native
    adapter = ClaudeAdapter(data_home_resolver=lambda: projects.parent)
    session = cast(
        Any,
        SimpleNamespace(
            record=record,
            adapter=adapter,
            transcript_provisional=False,
            cli_state={"cwd": str(worktree)},
        ),
    )
    return manager, session, current, moved


def test_a_relocated_transcript_is_re_found_from_the_live_cwd(tmp_path: Path) -> None:
    # Not the mtime heuristic and needing none of its ownership analysis: that
    # one guesses *which* conversation a session moved to and can latch onto a
    # sibling's, while this re-finds a file named by the conversation id the
    # session already owns. Both halves are proven - the followed path is gone,
    # and the candidate's stem is this session's own native id.
    manager, session, current, moved = relocation_fixture(tmp_path)
    assert SessionManager._relocated_transcript_candidate(manager, session, current) == moved


def hook_relocation_fixture(tmp_path: Path) -> tuple[Any, Any, Path, Path]:
    """`relocation_fixture` plus the state the hook-reported path needs."""
    manager, session, current, moved = relocation_fixture(tmp_path)
    manager.sessions = {}
    session.stopping = False
    session.stop_event = asyncio.Event()
    session.transcript_path = current
    session.pending_transcript_path = None
    session.transcript_relocation_signal = asyncio.Event()
    return manager, session, current, moved


def test_a_hook_naming_a_moved_transcript_is_staged(tmp_path: Path) -> None:
    # The CLI names the file it is writing in every hook payload, and the daemon
    # read it only when the payload *also* reported a new conversation id - so the
    # one case it could not see was the file moving while the conversation stayed
    # the same, which is exactly what entering a native worktree does.
    manager, session, _current, moved = hook_relocation_fixture(tmp_path)

    SessionManager.note_hook_transcript_path(
        manager, session, {"transcript_path": str(moved)}
    )

    assert session.pending_transcript_path == moved
    assert session.transcript_relocation_signal.is_set()


def test_a_hook_naming_the_followed_file_stages_nothing(tmp_path: Path) -> None:
    # The common case by far: every hook of every turn reports the path already
    # being followed, and none of them may wake the watcher.
    manager, session, current, _moved = hook_relocation_fixture(tmp_path)

    SessionManager.note_hook_transcript_path(
        manager, session, {"transcript_path": str(current)}
    )

    assert session.pending_transcript_path is None
    assert not session.transcript_relocation_signal.is_set()


def test_a_hook_naming_another_conversation_is_ignored(tmp_path: Path) -> None:
    # A nested child CLI inherits this session's hook wiring but not its
    # conversation id, and the file name carries that id. Rejecting on the name is
    # what stops a child from aiming its parent's observation at its own transcript.
    manager, session, _current, moved = hook_relocation_fixture(tmp_path)
    foreign = moved.with_name("11111111-2222-4333-8444-555555555555.jsonl")
    foreign.write_text("{}\n", encoding="utf-8")

    SessionManager.note_hook_transcript_path(
        manager, session, {"transcript_path": str(foreign)}
    )

    assert session.pending_transcript_path is None


def test_codex_never_stages_a_relocation(tmp_path: Path) -> None:
    # Rollouts are addressed by thread id in a date tree, so a Codex conversation's
    # file does not move when its pane's directory does. A differing path from that
    # backend is a report about some other conversation.
    manager, session, _current, moved = hook_relocation_fixture(tmp_path)
    session.adapter = SimpleNamespace(
        resolves_transcript_by_cwd=False,
        transcript_native_id=lambda path: path.stem,
        name="codex",
    )
    session.record.backend = "codex"

    SessionManager.note_hook_transcript_path(
        manager, session, {"transcript_path": str(moved)}
    )

    assert session.pending_transcript_path is None


def test_a_staged_relocation_is_consumed_once(tmp_path: Path) -> None:
    manager, session, current, moved = hook_relocation_fixture(tmp_path)
    SessionManager.note_hook_transcript_path(
        manager, session, {"transcript_path": str(moved)}
    )

    assert SessionManager._staged_transcript_relocation(manager, session, current) == moved
    # Consumed, and the signal retracted: a second tick must not re-aim observation
    # at a file it is already following.
    assert SessionManager._staged_transcript_relocation(manager, session, current) is None
    assert not session.transcript_relocation_signal.is_set()


def test_a_staged_relocation_a_live_sibling_owns_is_refused(tmp_path: Path) -> None:
    # The same claim rule every other binding path applies. Two panes on one
    # transcript is the failure this whole area exists to prevent.
    manager, session, current, moved = hook_relocation_fixture(tmp_path)
    sibling_record = agent_record(cwd=str(tmp_path / "repo"))
    sibling_record.id = "other-mux-id"
    manager.sessions = {
        "other-mux-id": SimpleNamespace(record=sibling_record, transcript_path=moved)
    }
    session.pending_transcript_path = moved

    assert SessionManager._staged_transcript_relocation(manager, session, current) is None


def test_a_staged_relocation_that_vanished_is_refused(tmp_path: Path) -> None:
    manager, session, current, moved = hook_relocation_fixture(tmp_path)
    session.pending_transcript_path = moved
    moved.unlink()

    assert SessionManager._staged_transcript_relocation(manager, session, current) is None


def test_a_clear_inside_a_worktree_still_rolls() -> None:
    # `/clear` reports the cwd the CLI is standing in, which for a session that
    # entered a worktree is not the one it spawned in. Comparing against the spawn
    # cwd alone refused the roll as a foreign process: the session stayed keyed to
    # the conversation the user had just wiped, and every later hook was then
    # filtered as foreign - a permanent detachment from a routine command.
    record = agent_record(cwd="D:/repo")
    record.runtime_cwd = "D:/repo/.claude/worktrees/feature"
    record.runtime_cwd_live = True
    session = cast(Any, SimpleNamespace(record=record, agent_lifecycle_id=ORIGINAL))

    decision = conversation_rollover_decision(
        session,
        "SessionStart",
        {"session_id": CLEARED, "source": "clear", "cwd": record.runtime_cwd},
    )

    assert decision.roll_to == CLEARED
    assert decision.refused is None


def test_a_clear_from_an_unrelated_directory_is_still_refused() -> None:
    record = agent_record(cwd="D:/repo")
    record.runtime_cwd = "D:/repo/.claude/worktrees/feature"
    record.runtime_cwd_live = True
    session = cast(Any, SimpleNamespace(record=record, agent_lifecycle_id=ORIGINAL))

    decision = conversation_rollover_decision(
        session,
        "SessionStart",
        {"session_id": CLEARED, "source": "clear", "cwd": "C:/somewhere/else"},
    )

    assert decision.refused == CLEARED
    assert decision.refusal_reason == "cwd_mismatch"


def test_relocation_stands_down_without_proof(tmp_path: Path, monkeypatch: Any) -> None:
    manager, session, current, moved = relocation_fixture(tmp_path)
    monkeypatch.setattr(
        "swe_mux.adapters.claude.claude_data_home", lambda: moved.parent.parent.parent
    )
    # A live followed file is never relocated out from under the observer.
    current.write_text("{}\n", encoding="utf-8")
    assert SessionManager._relocated_transcript_candidate(manager, session, current) is None
    current.unlink()
    # No live cwd reading, or one that never moved: nothing to re-resolve.
    session.cli_state = None
    assert SessionManager._relocated_transcript_candidate(manager, session, current) is None
    session.cli_state = {"cwd": str(tmp_path / "repo")}
    assert SessionManager._relocated_transcript_candidate(manager, session, current) is None
    # A guessed binding must not be re-resolved as if the id were proven.
    session.cli_state = {"cwd": str(moved.parent)}
    session.transcript_provisional = True
    assert SessionManager._relocated_transcript_candidate(manager, session, current) is None


# ------------------------------------ a filesystem that stops dating a live file
#
# Windows does not keep a live file's last-write time current. Measured 2026-08-06:
# every long Codex rollout on the machine reported an mtime frozen at the file's
# *creation* — 290 s to 3.5 h behind content that had grown to 5 MB — and `os.stat`,
# `GetFileAttributesExW`, `FindFirstFileW`, and `GetFileInformationByHandle` all
# returned the same frozen value, so there was no better call to reach for. `st_size`
# stayed accurate throughout.
#
# Everything below is one bug: staleness was measured with that timestamp, so a
# healthy Codex session started failing closed ~90 s into its life and kept flapping.
# Delivery hard-blocks on `transcript_stale`, so an armed queue message would not send
# at the exact moment the agent finished and was ready for it, and the operator had to
# override the one prompt that is meant to stop them (live report 2026-08-06; the
# session's own ledger showed 30+ `observation_stale` events on one idle Codex pane
# whose transcript_mtime never moved).


async def test_a_live_transcript_whose_mtime_is_frozen_is_never_stale(
    tmp_path: Path,
) -> None:
    # The exact shape of the live incident: ancient mtime, turn hook just now, and a
    # tailer that watched the file grow a moment ago.
    manager, session, transcript = stale_manager(
        tmp_path, last_turn_hook_ts=time.time(), transcript_growth_ts=time.time()
    )

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    manager.events.emit.assert_not_awaited()


async def test_a_late_observer_uses_the_completed_record_time_not_the_frozen_mtime(
    tmp_path: Path,
) -> None:
    """The first observer can attach after Codex has already written task_complete."""
    completed = time.time() - 120
    manager, session, transcript = stale_manager(
        tmp_path,
        last_turn_hook_ts=completed + 0.5,
        transcript_record_ts=completed,
    )

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    manager.events.emit.assert_not_awaited()


async def test_corroborating_catchup_retracts_the_existing_false_stale_claim(
    tmp_path: Path,
) -> None:
    completed = time.time() - 120
    manager, session, transcript = stale_manager(
        tmp_path,
        last_turn_hook_ts=completed + 0.5,
        transcript_record_ts=completed,
    )
    session.record.observation_stale_since = time.time()
    session.record.observation_diagnostic = "transcript rollout.jsonl last written 90s ago"
    session.observation_stale_reason = "transcript_stale"

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    assert manager.events.emit.await_args.args[0] == "observation_stale_cleared"


async def test_growth_older_than_the_turn_hook_still_marks_a_session_stale(
    tmp_path: Path,
) -> None:
    # The safety property this guard exists for, restated against the new evidence:
    # the file did grow once, then stopped, and the CLI has run a turn since. That is
    # still a conversation we are no longer following.
    manager, session, transcript = stale_manager(
        tmp_path,
        last_turn_hook_ts=time.time(),
        transcript_growth_ts=time.time() - 600,
    )

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is not None
    assert manager.events.emit.await_args.args[0] == "observation_stale"


async def test_the_stale_event_carries_both_readings(tmp_path: Path) -> None:
    # A post-mortem has to be able to tell a genuinely dead file from a filesystem
    # that stopped dating a live one, which the mtime alone cannot say.
    manager, session, transcript = stale_manager(
        tmp_path, last_turn_hook_ts=time.time(), transcript_growth_ts=time.time() - 600
    )

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    payload = manager.events.emit.await_args.kwargs
    assert payload["transcript_mtime"] == pytest.approx(transcript.stat().st_mtime)
    assert payload["transcript_growth_ts"] == pytest.approx(session.transcript_growth_ts)
    assert payload["transcript_record_ts"] == pytest.approx(session.transcript_record_ts)
    assert payload["transcript_last_write"] == pytest.approx(session.transcript_growth_ts)


async def test_growth_after_the_claim_retracts_it(tmp_path: Path) -> None:
    # Without this the flag is a one-way door for anything the observer cannot parse:
    # `_record_parser_observation` only clears it on a complete *record*, so a file
    # that resumes growing without yielding one stayed undeliverable until the
    # session ended.
    manager, session, transcript = stale_manager(
        tmp_path, last_turn_hook_ts=time.time(), transcript_growth_ts=time.time() - 600
    )
    await SessionManager._note_transcript_staleness(manager, session, transcript)
    marked = session.record.observation_stale_since
    assert marked is not None

    session.transcript_growth_ts = marked + 1
    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is None
    assert manager.events.emit.await_args.args[0] == "observation_stale_cleared"


async def test_a_quiet_abandoned_transcript_keeps_its_claim(tmp_path: Path) -> None:
    # The retraction above is deliberately *not* the negation of the staleness
    # predicate. The other two callers that set this flag — a rollover refused
    # because a live sibling owns the conversation, and a CLI-reported rollover that
    # could not be adopted — already know the CLI is elsewhere. Quiet on the file
    # they abandoned is not permission to forget that.
    manager, session, transcript = stale_manager(tmp_path, last_turn_hook_ts=0.0)
    session.record.observation_stale_since = time.time()

    await SessionManager._note_transcript_staleness(manager, session, transcript)

    assert session.record.observation_stale_since is not None
    manager.events.emit.assert_not_awaited()


def test_the_switch_watcher_will_not_retarget_a_growing_transcript(
    tmp_path: Path,
) -> None:
    # "The file we follow has gone quiet" is the precondition for retargeting at all.
    # Read from the frozen timestamp it was not enforced, so the daemon ran its
    # candidate search against an actively-written file and only the ownership
    # evidence stood between that and adopting a sibling's conversation.
    manager, session, current, fresh = switch_fixture(tmp_path)
    frozen = time.time() - 4000
    os.utime(current, (frozen, frozen))
    assert SessionManager._transcript_switch_candidate(manager, session, current) == fresh

    session.transcript_growth_ts = time.time()
    assert SessionManager._transcript_switch_candidate(manager, session, current) is None


def test_a_sibling_growing_a_frozen_transcript_still_blocks(tmp_path: Path) -> None:
    # The mirror of the rule above, on a sibling: its own growth reading is what
    # clears it of having written the candidate, and a frozen mtime hides exactly
    # that.
    manager, session, current, _fresh = switch_fixture(tmp_path)
    sibling_path = tmp_path / "sibling.jsonl"
    sibling_path.write_text("{}\n", encoding="utf-8")
    frozen = time.time() - 4000
    os.utime(sibling_path, (frozen, frozen))
    other = sibling(tmp_path, transcript=sibling_path, last_activity_ts=time.time())
    manager.sessions["sibling"] = other

    assert SessionManager._transcript_switch_candidate(manager, session, current) is None

    other.transcript_growth_ts = time.time() + 2
    assert SessionManager._transcript_switch_candidate(manager, session, current) is not None


def test_re_tailing_the_same_transcript_keeps_its_growth_evidence(
    tmp_path: Path,
) -> None:
    # The observer loop re-aims on every restart, including the ones that follow a
    # crash on the same file. Clearing the stamp there would drop the session back
    # onto the filesystem timestamp after each fault — silently, and for as long as
    # the agent then stayed quiet.
    session = cast(
        Any,
        SimpleNamespace(
            transcript_path=None, transcript_growth_ts=0.0, transcript_record_ts=0.0
        ),
    )
    first = tmp_path / "one.jsonl"
    second = tmp_path / "two.jsonl"

    SessionManager._aim_observer(session, first)
    session.transcript_growth_ts = time.time()
    session.transcript_record_ts = time.time()
    SessionManager._aim_observer(session, first)
    assert session.transcript_growth_ts > 0.0
    assert session.transcript_record_ts > 0.0

    SessionManager._aim_observer(session, second)
    assert session.transcript_growth_ts == 0.0
    assert session.transcript_record_ts == 0.0
    assert session.transcript_path == second


async def test_a_rollover_discards_the_retired_transcripts_growth(
    tmp_path: Path,
) -> None:
    record = agent_record(cwd=str(tmp_path))
    manager, session = rollover_manager(record)
    session.transcript_growth_ts = time.time()
    session.transcript_record_ts = time.time()

    rolled = await manager._apply_conversation_rollover(
        session,
        native_id=CLEARED,
        transcript=tmp_path / f"{CLEARED}.jsonl",
        reason="conversation_rolled",
        source="clear",
    )

    assert rolled is True
    assert session.transcript_growth_ts == 0.0
    assert session.transcript_record_ts == 0.0


def test_no_liveness_rule_reads_the_filesystem_timestamp_on_its_own() -> None:
    """Both callers must go through `_transcript_last_write_ts`.

    A structural pin, because the defect was invisible in review: `stat().st_mtime`
    is the obvious way to ask "when was this last written", it is correct on every
    other platform the developer is likely to check, and it fails silently.
    """
    import ast
    import inspect
    import textwrap

    for rule in (
        SessionManager._note_transcript_staleness,
        SessionManager._transcript_switch_candidate,
        SessionManager._unresolved_transcript_sibling,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(rule)))
        # Attribute access, so the prose above each of these may keep explaining
        # why the timestamp is not to be trusted.
        reads_mtime = any(
            isinstance(node, ast.Attribute) and node.attr in {"st_mtime", "st_atime"}
            for node in ast.walk(tree)
        )
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert not reads_mtime, rule.__name__
        assert "_transcript_last_write_ts" in calls, rule.__name__


async def test_a_frozen_mtime_leaves_a_finished_agent_deliverable(
    tmp_path: Path,
) -> None:
    """The user-visible half: the armed message sends.

    Staleness and delivery are separate modules, and the bug only bites where they
    meet — a session that reads `idle · turn complete` everywhere in the UI while the
    queue refuses it. This pins the join, not either half.
    """
    manager, session, transcript = stale_manager(
        tmp_path, last_turn_hook_ts=time.time(), transcript_growth_ts=time.time()
    )
    await SessionManager._note_transcript_staleness(manager, session, transcript)

    clock = VirtualClock()
    delivery = ReplaySession("codex", clock)
    delivery.record.parser_status = "ready"
    delivery.record.observation_stale_since = session.record.observation_stale_since
    # mux launches Codex with `tui.alternate_screen="never"` (`delivery-readiness.md`).
    delivery.terminal_mode = "normal"
    delivery.terminal_mode_updated_at = clock.monotonic()
    tracker = DeliveryReadinessTracker(clock=clock.monotonic)
    tracker.observe(
        MuxEvent(time.time(), "replay-session", "transcript", "turn_started", {}), delivery
    )
    tracker.observe(
        MuxEvent(
            time.time(),
            "replay-session",
            "transcript",
            "turn_ended",
            {"outcome": "completed"},
        ),
        delivery,
    )
    clock.advance(5.0)
    delivery.terminal_mode_updated_at = clock.monotonic()

    evaluation = tracker.evaluate(delivery)
    assert "transcript_stale" not in evaluation["reasons"]
    assert evaluation["delivery_state"] == "safe"


def test_a_stale_transcript_loses_its_authority_over_hooks() -> None:
    # Parser confidence is irrelevant to precedence. Growth after the latest
    # transcript-backed hook owns boundaries; a newer hook proves the followed
    # transcript has not yet reported that activity.
    session = cast(
        Any,
        SimpleNamespace(
            record=agent_record(),
            transcript_growth_ts=20.0,
            last_turn_hook_ts=10.0,
        ),
    )
    session.record.parser_status = "ready"
    assert _transcript_authoritative(session) is True
    session.last_turn_hook_ts = 30.0
    assert _transcript_authoritative(session) is False
    session.record.observation_stale_since = time.time()
    assert _transcript_authoritative(session) is False


async def test_a_record_on_the_followed_transcript_clears_staleness() -> None:
    session = cast(
        Any,
        SimpleNamespace(
            record=agent_record(),
            subscribers=(),
            meta_sink=None,
            observation_stale_reason="transcript_stale",
        ),
    )
    session.record.parser_status = "ready"
    session.record.observation_stale_since = time.time()
    events = SimpleNamespace(emit=AsyncMock())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("swe_mux.observation._publish_update", lambda _session: None)
        await _record_parser_observation(session, cast(Any, events), True, "sig")
    assert session.record.observation_stale_since is None


async def test_a_record_cannot_clear_an_explicit_conversation_mismatch() -> None:
    session = cast(
        Any,
        SimpleNamespace(
            record=agent_record(),
            subscribers=(),
            meta_sink=None,
            observation_stale_reason="conversation_owned_elsewhere",
        ),
    )
    session.record.parser_status = "ready"
    marked = time.time()
    session.record.observation_stale_since = marked
    events = SimpleNamespace(emit=AsyncMock())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("swe_mux.observation._publish_update", lambda _session: None)
        await _record_parser_observation(session, cast(Any, events), True, "sig")
    assert session.record.observation_stale_since == marked


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
