"""The daemon's side: three outcomes for a shell prompt, and the shim's own reports.

`test_console_contention.py` pins the rules. This pins what the session manager does
with them - which is the half that regressed silently, because the old check could
only demote or say nothing.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from swe_mux.adapters.claude import ClaudeAdapter
from swe_mux.adapters.shell import ShellAdapter
from swe_mux.console_contention import ConsoleCensus
from swe_mux.errors import NotFound
from swe_mux.models import SessionRecord
from swe_mux.runtime_cwd import Osc133Parser
from swe_mux.session import SessionManager

_ADAPTERS: dict[str, Any] = {"claude": ClaudeAdapter(), "shell": ShellAdapter()}


class _Events:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []
        self.background: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, name: str, **fields: Any) -> None:
        self.emitted.append((name, fields))

    def emit_background(self, name: str, **fields: Any) -> None:
        self.background.append((name, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.emitted]


def promoted_session(*, transcript: Path | None = None, pid: int = -1) -> Any:
    record = SessionRecord(
        "mux-id", "agent", "default", "claude", "native-id", ".", "claude.exe", []
    )
    record.spawn_backend = "shell"
    record.pid = pid
    return cast(
        Any,
        SimpleNamespace(
            record=record,
            adapter=_ADAPTERS["claude"],
            stop_event=asyncio.Event(),
            stopping=False,
            transcript_path=transcript,
            agent_lifecycle_id="lifecycle-id",
            agent_promoted_at=time.time() - 60,
            agent_exit_check_task=None,
            ignored_detection_runs=set(),
            cli_state=None,
            osc133=Osc133Parser(),
            tasks=set(),
            published=0,
        ),
    )


def manager_with(events: _Events) -> Any:
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {}
    manager.events = events
    return manager


def _publish(session: Any) -> None:
    session.published += 1


# --- the third outcome ------------------------------------------------------


async def test_a_live_agent_is_reported_as_contention_and_never_demoted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The 2026-08-27 incident, end to end through the daemon's own code path.

    A busy transcript used to be the only thing this loop could see, so a live
    agent produced ten quiet retries and then silence. Demoting instead would be
    worse: the run is not over, and dropping the backend takes the transcript
    binding, the token accounting and queue eligibility with it.
    """
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")  # fresh: "still writing"
    events = _Events()
    manager = manager_with(events)
    session = promoted_session(transcript=transcript, pid=os.getpid())
    session.publish_update = lambda: _publish(session)
    session.record.agent_process_pid = os.getpid()

    demoted: list[Any] = []
    manager.demote = lambda *args: demoted.append(args)

    await SessionManager._confirm_agent_exit(manager, session)

    assert demoted == []
    assert "agent_console_contended" in events.names()
    assert session.record.console_contention is not None
    assert session.record.console_contention["reason"] == "shell_regained_console"
    assert session.record.backend == "claude"


async def test_an_orphaned_agent_is_named_as_orphaned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinguished from plain contention because it names a different repair.

    An agent outside the pane's process tree will not be reaped when the pane ends,
    so an operator who closes the tab leaves a CLI running on their machine.
    """
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    events = _Events()
    manager = manager_with(events)
    session = promoted_session(pid=os.getpid())
    session.publish_update = lambda: _publish(session)
    session.record.agent_process_pid = 4242

    monkeypatch.setattr(
        "swe_mux.session.probe_console_participants",
        lambda root, agent: ConsoleCensus(root, agent, True, False),
    )
    manager.demote = lambda *args: pytest.fail("an orphaned agent must not be demoted")

    await SessionManager._confirm_agent_exit(manager, session)

    assert session.record.console_contention["reason"] == "agent_orphaned"
    payload = dict(events.emitted[0][1])
    assert payload["reason"] == "agent_orphaned"
    assert payload["agent_in_pty_tree"] is False


async def test_contention_is_announced_once_per_stretch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shell at a prompt prints one every time the user presses Enter."""
    events = _Events()
    manager = manager_with(events)
    session = promoted_session()
    session.publish_update = lambda: _publish(session)
    census = ConsoleCensus(1, 2, True, True)

    note = SessionManager._note_console_contention
    await note(manager, session, census, "shell_regained_console")
    await note(manager, session, census, "shell_regained_console")
    assert events.names().count("agent_console_contended") == 1

    # A *different* cause is new information and is announced.
    await note(manager, session, census, "agent_orphaned")
    assert events.names().count("agent_console_contended") == 2


async def test_a_dead_agent_still_demotes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The historical behaviour, now reached by a stronger signal than the transcript.

    An exiting CLI writes its final records on the way out, so the transcript is at
    its least quiet exactly when the process has just gone.
    """
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    events = _Events()
    manager = manager_with(events)
    session = promoted_session(transcript=transcript, pid=os.getpid())
    session.publish_update = lambda: _publish(session)
    session.record.agent_process_pid = 4242

    monkeypatch.setattr(
        "swe_mux.session.probe_console_participants",
        lambda root, agent: ConsoleCensus(root, agent, False, False),
    )
    demoted: list[Any] = []

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append((sid, backend, native_id))

    manager.demote = demote
    await SessionManager._confirm_agent_exit(manager, session)
    assert demoted == [("mux-id", "claude", "lifecycle-id")]
    assert session.record.console_contention is None


async def test_an_unmeasurable_census_falls_back_to_the_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An errored walk is no evidence, and must not move the session either way."""
    monkeypatch.setattr("swe_mux.session.AGENT_EXIT_CHECK_INTERVAL_SECONDS", 0.01)
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    stale = time.time() - 30
    os.utime(transcript, (stale, stale))
    events = _Events()
    manager = manager_with(events)
    session = promoted_session(transcript=transcript)
    session.publish_update = lambda: _publish(session)

    monkeypatch.setattr(
        "swe_mux.session.probe_console_participants",
        lambda root, agent: ConsoleCensus(root, agent, True, True, error="walk_error:denied"),
    )
    demoted: list[Any] = []

    async def demote(sid: str, backend: str, native_id: str) -> None:
        demoted.append((sid, backend, native_id))

    manager.demote = demote
    await SessionManager._confirm_agent_exit(manager, session)
    # `agent_alive=True` was discarded because the census errored, so the quiet
    # transcript decides — exactly the pre-existing behaviour.
    assert demoted == [("mux-id", "claude", "lifecycle-id")]
    assert "agent_console_contended" not in events.names()


# --- where the agent pid comes from -----------------------------------------


def test_the_shim_report_outranks_cli_state() -> None:
    session = promoted_session()
    session.cli_state = {"pid": 111}
    assert SessionManager._agent_process_pid(session) == 111
    session.record.agent_process_pid = 222
    assert SessionManager._agent_process_pid(session) == 222


def test_a_harness_that_publishes_nothing_yields_no_pid() -> None:
    session = promoted_session()
    session.cli_state = None
    assert SessionManager._agent_process_pid(session) is None


# --- ingesting the shim's reports -------------------------------------------


async def test_a_child_started_report_records_the_pid_and_emits_an_event() -> None:
    events = _Events()
    manager = manager_with(events)
    session = promoted_session()
    manager.resolve = lambda sid: session

    await SessionManager.record_shim_report(
        manager,
        "mux-id",
        {"kind": "child_started", "backend": "claude", "child_pid": 5150, "shim_pid": 4},
    )
    assert session.record.agent_process_pid == 5150
    assert events.names() == ["agent_shim_report"]


async def test_a_shim_report_cannot_smuggle_unknown_fields_into_an_event() -> None:
    """The second boundary on what a report may carry.

    The shim already withholds argument values (`argv_shape`); this is the daemon
    refusing to durably record anything it was not told to expect, so a future
    field cannot silently widen the surface.
    """
    events = _Events()
    manager = manager_with(events)
    session = promoted_session()
    manager.resolve = lambda sid: session

    await SessionManager.record_shim_report(
        manager,
        "mux-id",
        {
            "kind": "started",
            "backend": "claude",
            "prompt": "the user's private prompt text",
            "env": {"ANTHROPIC_API_KEY": "sk-secret"},
        },
    )
    _, payload = events.emitted[0]
    assert "prompt" not in payload
    assert "env" not in payload
    assert payload["kind"] == "started"


async def test_a_report_for_an_unknown_session_is_dropped() -> None:
    events = _Events()
    manager = manager_with(events)

    def _missing(sid: str) -> Any:
        raise NotFound(sid, kind="session")

    manager.resolve = _missing
    await SessionManager.record_shim_report(manager, "gone", {"kind": "started"})
    assert events.emitted == []


async def test_a_shim_whose_child_outlived_it_reports_contention_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second, independent detector.

    A hard-killed wrapper emits nothing and only the shell-prompt path catches it;
    a wrapper that got to run its exit path proves the same defect directly, and
    waiting for a prompt would just delay saying so.
    """
    events = _Events()
    manager = manager_with(events)
    session = promoted_session(pid=os.getpid())
    session.publish_update = lambda: _publish(session)
    manager.resolve = lambda sid: session
    monkeypatch.setattr(
        "swe_mux.session.probe_console_participants",
        lambda root, agent: ConsoleCensus(root, agent, True, False),
    )

    await SessionManager.record_shim_report(
        manager,
        "mux-id",
        {"kind": "exited", "backend": "claude", "child_pid": 900, "child_outlived_shim": True},
    )
    assert events.names() == ["agent_shim_report", "agent_console_contended"]
    assert session.record.console_contention["reason"] == "shim_exited_first"


# --- OSC 133 under a promoted pane ------------------------------------------


def test_a_prompt_marker_under_a_promoted_pane_triggers_the_check() -> None:
    """Previously the parser was skipped entirely for an agent pane.

    OSC 7 already triggered the check, so this only matters for a profile carrying
    breakpoint markers and no cwd reporting - but that profile existed and was
    silently uncovered.
    """
    events = _Events()
    manager = manager_with(events)
    session = promoted_session()
    triggered = SessionManager._note_shell_breakpoints(manager, session, b"\x1b]133;A\x07")
    assert triggered is True
    # An agent pane's breakpoints are not the human's, so nothing is announced.
    assert events.background == []


def test_a_shell_pane_still_reports_its_own_breakpoints() -> None:
    events = _Events()
    manager = manager_with(events)
    session = promoted_session()
    session.record.backend = "shell"
    triggered = SessionManager._note_shell_breakpoints(manager, session, b"\x1b]133;D;0\x07")
    assert triggered is False
    assert [name for name, _ in events.background] == ["shell_command_finished"]


def test_output_with_no_markers_triggers_nothing() -> None:
    events = _Events()
    manager = manager_with(events)
    session = promoted_session()
    assert SessionManager._note_shell_breakpoints(manager, session, b"ordinary output") is False


# --- run seams --------------------------------------------------------------


def test_a_run_seam_forgets_the_previous_generation_s_console_identity() -> None:
    """A pid is not an identity on Windows; carrying one across a seam mis-reports."""
    session = promoted_session()
    session.record.agent_process_pid = 4242
    session.record.console_contention = {"reason": "agent_orphaned", "since": 1.0}
    session.record.agent_launch_pending = ["claude"]
    SessionManager._reset_console_identity(session)
    assert session.record.agent_process_pid is None
    assert session.record.console_contention is None
    assert session.record.agent_launch_pending == []
