"""Resuming a conversation continues its run instead of forking a second.

`claude --resume` and `codex resume` both append to the same transcript, under
the same conversation id, in the same file: neither CLI forks. Opening a second
history entry for it indexed one file twice, showed one conversation as two
entries, and left the first entry's totals moving after its own pane had exited.
So the resumed pane inherits the conversation's `agent_run_id` — every
control-plane record keys on that, so the timeline, facts, annotations, and title
continue too.

Whether a resume continues the conversation is the *adapter's* answer, because it
is the CLI's own transcript-resolution rule: Claude resolves by working
directory, so a resume into another root writes a different file and is a new
conversation, while Codex resolves by thread id and reopens the original rollout
wherever the pane runs. Codex was assumed to mint a new rollout per resume, which
was true of an older CLI and is why a resume used to open a row per pane.

The inheritance is the mirror of a rollover (same PTY, new conversation, new
run), and it needs the same defence: `spawn_agent_run_id` is immutable spawn
evidence, so adoption can tell an inherited run id from the misattribution it
repairs. These tests pin that, the row-level continuity, and the two places that
must never quarantine a row the pane merely continued.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

from swe_mux.adapters import ClaudeAdapter, CodexAdapter
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.server import resume_history
from swe_mux.session import Session, SessionManager

CONVERSATION = "aaaaaaaa-1111-4a7b-8c9d-0e1f2a3b4c5d"
PANE = "bbbbbbbb-2222-4a7b-8c9d-0e1f2a3b4c5d"
RUN = "conversation-run"


async def _async_value(value: Any) -> Any:
    return value


# --------------------------------------------------------------- the endpoint


def resume_call(
    tmp_path: Path,
    *,
    backend: str = "claude",
    project_root: Path | None = None,
    name: str = "device ownership",
    auto_named: int = 1,
    generated_title: str | None = None,
) -> tuple[Any, list[dict[str, Any]], list[tuple[str, str, str, dict[str, Any]]]]:
    """A `/api/history/{id}/resume` request over stubbed collaborators."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    root = project_root or tmp_path
    captured: list[dict[str, Any]] = []
    lineage: list[tuple[str, str, str, dict[str, Any]]] = []

    async def spawn(**kwargs: Any) -> Any:
        captured.append(kwargs)
        # Mirrors SessionManager.spawn: an inherited run id is the record's.
        return SimpleNamespace(
            record=SimpleNamespace(
                id=PANE,
                agent_run_id=kwargs.get("adopt_run_id") or PANE,
                project_id=kwargs["project_id"],
                snapshot=lambda: {},
            )
        )

    async def add_lineage(
        parent: str, child: str, relation: str, metadata: dict[str, Any]
    ) -> None:
        lineage.append((parent, child, relation, metadata))

    async def annotations(**kwargs: Any) -> list[dict[str, Any]]:
        if generated_title is None:
            return []
        return [{"agent_run_id": RUN, "content": generated_title}]

    async def update_project(*args: Any, **kwargs: Any) -> Any:
        return (args, kwargs)

    history = SimpleNamespace(
        history_entry=lambda identity: _async_value(
            {
                "id": RUN,
                "backend": backend,
                "name": name,
                "cwd": str(tmp_path),
                "native_id": CONVERSATION,
                "agent_visible": 1,
                "auto_named": auto_named,
                "transcript_path": str(transcript),
                "project_id": "default",
            }
        )
    )
    request = SimpleNamespace(
        app={
            "history": history,
            # The real adapters: whether a resume continues the conversation is their
            # answer, and stubbing it would only pin the stub.
            "sessions": SimpleNamespace(
                spawn=spawn,
                adapters={"claude": ClaudeAdapter("claude.exe"), "codex": CodexAdapter()},
                sessions={},
            ),
            "projects": SimpleNamespace(
                projects={
                    "default": SimpleNamespace(
                        name="Main",
                        root=str(root),
                        layout={"version": 2, "root": None},
                        layout_revision=0,
                    )
                },
                update=update_project,
            ),
            "automation_store": SimpleNamespace(
                add_lineage=add_lineage,
                annotations=annotations,
            ),
        },
        match_info={"sid": RUN},
        can_read_body=False,
    )
    return request, captured, lineage


async def test_a_claude_resume_inherits_the_conversations_run(tmp_path: Path) -> None:
    request, captured, lineage = resume_call(tmp_path)

    await resume_history(cast(Any, request))

    assert captured[0]["adopt_run_id"] == RUN
    assert captured[0]["resume_native_id"] == CONVERSATION
    # Same run, so there is no fork to record. An edge to itself would make every
    # consumer walking lineage see a cycle where nothing branched.
    assert lineage == []


async def test_a_claude_resume_keeps_the_conversations_name(tmp_path: Path) -> None:
    request, captured, _ = resume_call(tmp_path)

    await resume_history(cast(Any, request))

    # No " resumed" suffix: it compounded on every resume and retitled an entry
    # the resumed pane now shares rather than replaces.
    assert captured[0]["name"] == "device ownership"
    # A conversation nobody renamed stays auto-titleable.
    assert captured[0]["auto_named"] is True


async def test_a_resume_of_a_renamed_conversation_stays_pinned(tmp_path: Path) -> None:
    request, captured, _ = resume_call(tmp_path, auto_named=0)

    await resume_history(cast(Any, request))

    assert captured[0]["name"] == "device ownership"
    assert captured[0]["auto_named"] is False


async def test_a_renamed_conversation_resumes_under_the_name_the_user_pinned(
    tmp_path: Path,
) -> None:
    # A row carrying both a rename and an old generated title. `auto_named` arrives
    # from SQLite as 0, and `0 is not False`, so the title test used to pass for
    # every row: a conversation the user had named "device ownership" came back as
    # "Session notes redesign".
    request, captured, _ = resume_call(
        tmp_path, auto_named=0, generated_title="Session notes redesign"
    )

    await resume_history(cast(Any, request))

    assert captured[0]["name"] == "device ownership"
    assert captured[0]["auto_named"] is False


async def test_a_codex_resume_inherits_the_conversations_run(tmp_path: Path) -> None:
    # `codex resume` reopens the rollout the thread id already owns and appends to
    # it, so the pane continues that conversation and shares its entry.
    request, captured, lineage = resume_call(tmp_path, backend="codex")

    await resume_history(cast(Any, request))

    assert captured[0]["adopt_run_id"] == RUN
    assert lineage == []


async def test_a_codex_resume_under_another_root_still_inherits_the_run(
    tmp_path: Path,
) -> None:
    # The cwd rule is Claude's, not a general one: Codex addresses a conversation by
    # thread id, so a resume elsewhere is still the same conversation in the same
    # rollout. Asking the adapter is what keeps the two rules apart.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    request, captured, lineage = resume_call(tmp_path, backend="codex", project_root=elsewhere)

    await resume_history(cast(Any, request))

    assert captured[0]["adopt_run_id"] == RUN
    assert lineage == []


async def test_a_codex_resume_inherits_the_effective_generated_title(tmp_path: Path) -> None:
    request, captured, _ = resume_call(
        tmp_path,
        backend="codex",
        name="codex-e09a7f",
        generated_title="Session notes redesign",
    )

    await resume_history(cast(Any, request))

    assert captured[0]["name"] == "Session notes redesign"
    assert captured[0]["auto_named"] is True


async def test_a_claude_resume_into_another_root_opens_its_own_run(tmp_path: Path) -> None:
    # Claude resolves transcripts by working directory, so resuming under a
    # different root writes a different file: a different conversation.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    request, captured, lineage = resume_call(tmp_path, project_root=elsewhere)

    await resume_history(cast(Any, request))

    assert captured[0]["adopt_run_id"] is None
    assert lineage == [(RUN, PANE, "resume", {"backend": "claude", "project_id": "default"})]


# ------------------------------------------------------------- the history row


def pane_record(sid: str, cwd: Path, *, name: str = "device ownership") -> SessionRecord:
    return SessionRecord(
        sid,
        name,
        "default",
        "claude",
        CONVERSATION,
        str(cwd),
        "claude.exe",
        ["--resume", CONVERSATION],
    )


async def test_an_inherited_run_reopens_the_row_instead_of_writing_a_second(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    transcript = tmp_path / "transcript.jsonl"
    original = pane_record("pane-1", tmp_path)
    original.agent_run_id = original.id
    original.tokens_in = 500
    await history.session_started(original, str(transcript))
    await history.session_ended(original, "process_exit")

    resumed = pane_record("pane-2", tmp_path, name="device ownership")
    resumed.agent_run_id = original.id
    resumed.spawn_agent_run_id = original.id
    await history.resume_agent_run(resumed, str(transcript))

    rows = await history.history()
    assert [row["id"] for row in rows] == ["pane-1"]
    # Reopened, not restarted: the conversation's own start, totals and note
    # survive; only the terminal markers the exit wrote are cleared.
    assert rows[0]["exited_at"] is None
    assert rows[0]["exit_reason"] is None
    assert rows[0]["final_state"] is None
    assert rows[0]["spawned_at"] == original.created_at
    assert rows[0]["tokens_in"] == 500
    assert rows[0]["note_id"] == "pane-1"


async def test_a_resumed_panes_exit_closes_the_conversations_row(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    transcript = tmp_path / "transcript.jsonl"
    original = pane_record("pane-1", tmp_path)
    original.agent_run_id = original.id
    await history.session_started(original, str(transcript))
    await history.session_ended(original, "process_exit")

    resumed = pane_record("pane-2", tmp_path)
    resumed.agent_run_id = original.id
    resumed.spawn_agent_run_id = original.id
    resumed.tokens_in = 900
    await history.resume_agent_run(resumed, str(transcript))
    await history.session_ended(resumed, "process_exit")

    rows = await history.history()
    assert [row["id"] for row in rows] == ["pane-1"]
    assert rows[0]["exited_at"] is not None
    assert rows[0]["tokens_in"] == 900


async def test_resuming_never_resurrects_a_quarantined_row(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    transcript = tmp_path / "transcript.jsonl"
    original = pane_record("pane-1", tmp_path)
    original.agent_run_id = original.id
    await history.session_started(original, str(transcript))
    await history.quarantine_misattributed_agent_run("pane-1", "root_identity_reconciled")

    resumed = pane_record("pane-2", tmp_path)
    resumed.agent_run_id = original.id
    resumed.spawn_agent_run_id = original.id
    await history.resume_agent_run(resumed, str(transcript))

    # A quarantine is permanent and is an audit record: overwriting it would
    # destroy the evidence it exists to keep.
    assert await history.history() == []
    entry = await history.history_entry("pane-1")
    assert entry is not None and entry["agent_visible"] == 0


async def test_a_vanished_row_is_reopened_at_the_run_id(tmp_path: Path) -> None:
    # Deleted between the resume request and the spawn. Every later write for the
    # pane keys on the run id, so a row has to exist there or the pane can record
    # nothing at all.
    history = HistoryIndex(tmp_path / "mux.db")
    transcript = tmp_path / "transcript.jsonl"
    original = pane_record("pane-1", tmp_path)
    original.agent_run_id = original.id
    await history.session_started(original, str(transcript))
    await history.delete_history_entry("pane-1")

    resumed = pane_record("pane-2", tmp_path)
    resumed.agent_run_id = original.id
    resumed.spawn_agent_run_id = original.id
    await history.resume_agent_run(resumed, str(transcript))

    rows = await history.history()
    assert [row["id"] for row in rows] == ["pane-1"]
    assert rows[0]["note_id"] == "pane-2"


# ------------------------------------------------------------ identity defence


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


def resumed_record(cwd: Path) -> SessionRecord:
    record = SessionRecord(
        PANE, "device ownership", "default", "claude", CONVERSATION, str(cwd),
        "claude.exe", ["--resume", CONVERSATION],
    )
    record.spawn_backend = "claude"
    record.spawn_native_session_id = CONVERSATION
    record.agent_run_id = RUN
    record.spawn_agent_run_id = RUN
    record.agent_run_started_at = record.created_at
    record.run_cwd = str(cwd)
    return record


def real_session(record: SessionRecord, cwd: Path) -> Session:
    pty = SimpleNamespace(graceful_exit="", isalive=lambda: True)
    adapter = SimpleNamespace(
        name=record.backend,
        reports_conversation_rollover=True,
        assigns_conversation_id=True,
        graceful_exit_keys=lambda: "exit\r",
        transcript_path=lambda native_id, _cwd: cwd / f"{native_id}.jsonl",
    )
    return Session(record, cast(Any, pty), cast(Any, adapter), 32, "secret")


def test_a_legacy_snapshot_of_a_resumed_pane_is_still_an_agent() -> None:
    # Spawn metadata a pre-inheritance daemon never wrote, rebuilt from argv. An
    # inherited run id used to read as "promoted shell", which demoted a live
    # Claude pane to a shell on the first daemon restart.
    record = SessionRecord(
        PANE, "device ownership", "default", "claude", CONVERSATION, ".",
        "node", ["--resume", CONVERSATION],
    )
    record.agent_run_id = RUN
    record.spawn_agent_run_id = RUN

    SessionManager._ensure_spawn_identity(record)

    assert record.spawn_backend == "claude"
    assert record.spawn_native_session_id == CONVERSATION


def test_an_inherited_run_survives_a_daemon_restart(tmp_path: Path) -> None:
    # Without the inheritance evidence this is indistinguishable from corruption:
    # the first restart would "repair" the run to the session id and quarantine
    # the conversation's row as misattributed.
    manager = fake_manager()
    record = resumed_record(tmp_path)
    transcript_path = tmp_path / f"{CONVERSATION}.jsonl"
    transcript_path.write_text("{}\n", encoding="utf-8")
    manager.adapters = {
        "claude": SimpleNamespace(
            name="claude",
            recent_transcripts=lambda *_: [(time.time(), transcript_path, CONVERSATION)],
            transcript_native_id=lambda path: Path(path).stem,
        )
    }
    records = {record.id: record}
    metas: dict[str, dict[str, Any]] = {record.id: {"transcript_path": str(transcript_path)}}

    transcript, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        record, metas[record.id], records, metas
    )

    assert previous is None
    assert bad_run_id is None
    assert transcript == transcript_path
    assert record.agent_run_id == RUN
    assert record.agent_run_seq == 0


def test_a_contested_inherited_run_is_dropped_but_never_quarantined(tmp_path: Path) -> None:
    # A sibling's immutable spawn claim on the conversation outranks an inherited
    # run id, so the pane falls back to its own anchor. The row it leaves behind
    # is the *conversation's*, holding that conversation's messages, so it is
    # dropped rather than quarantined.
    manager = fake_manager()
    record = resumed_record(tmp_path)
    sibling = SessionRecord(
        CONVERSATION, "owner", "default", "claude", CONVERSATION, str(tmp_path),
        "claude.exe", ["--session-id", CONVERSATION],
    )
    sibling.spawn_backend = "claude"
    sibling.spawn_native_session_id = CONVERSATION
    sibling.agent_run_id = sibling.id
    manager.adapters = {
        "claude": SimpleNamespace(
            name="claude",
            recent_transcripts=lambda *_: [],
            transcript_native_id=lambda path: Path(path).stem,
            transcript_path=lambda native_id, _cwd: tmp_path / f"{native_id}.jsonl",
        )
    }
    records = {record.id: record, sibling.id: sibling}
    metas: dict[str, dict[str, Any]] = {record.id: {}, sibling.id: {}}

    _, bad_run_id, previous = manager._reconcile_adopted_root_identity(
        record, metas[record.id], records, metas
    )

    assert previous is not None
    assert bad_run_id is None
    assert record.agent_run_id == record.id


async def test_the_live_heal_never_quarantines_an_inherited_run(tmp_path: Path) -> None:
    # Same rule on the live path: healing this pane off the disputed conversation
    # must not delete the history of the conversation it was resuming.
    manager = fake_manager()
    record = resumed_record(tmp_path)
    session = real_session(record, tmp_path)
    manager.sessions = {record.id: session}
    manager._stop_observer = AsyncMock()
    manager._start_observer = lambda session, path: None

    await manager._heal_minted_identity(session, CONVERSATION)

    assert record.agent_run_id == record.id
    assert record.native_session_id == record.id
    manager.history.quarantine_misattributed_agent_run.assert_not_awaited()
