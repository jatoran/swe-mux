from __future__ import annotations

import asyncio
from pathlib import Path

from swe_mux.event_bus import EventBus
from swe_mux.git_projects import resolve_project
from swe_mux.history import HistoryIndex
from swe_mux.models import SessionRecord
from swe_mux.reconcile import summarize_transcript


def agent(identity: str, backend: str, cwd: Path, *, project: str = "project") -> SessionRecord:
    return SessionRecord(
        identity,
        identity,
        project,
        backend,
        f"native-{identity}",
        str(cwd),
        f"{backend}.exe",
        [],
        state="idle",
        project_label="Example",
        project_root=str(cwd),
    )


async def test_shell_history_is_hidden_then_promotion_converts_one_row(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    session = agent("one", "shell", tmp_path)
    session.created_at = 123.0
    await history.session_started(session, None)
    assert await history.history() == []

    session.backend = "claude"
    session.native_session_id = "claude-native"
    transcript = tmp_path / "claude.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    await history.session_promoted(session, str(transcript))

    rows = await history.history()
    assert len(rows) == 1
    assert rows[0]["id"] == "one"
    assert rows[0]["native_id"] == "claude-native"
    assert rows[0]["spawned_at"] == 123.0
    assert rows[0]["note_id"] == "one"
    history.close()


async def test_nested_agent_history_keeps_the_owning_terminal_note_id(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    session = agent("terminal-id", "shell", tmp_path)
    await history.session_started(session, None)
    session.backend = "codex"
    session.native_session_id = "codex-native"
    session.agent_run_id = "agent-run-id"
    await history.session_promoted(session, str(tmp_path / "codex.jsonl"))

    row = await history.history_entry("agent-run-id")
    assert row is not None
    assert row["note_id"] == "terminal-id"
    assert await history.session_note_owned("project", "terminal-id")
    history.close()


async def test_identity_repair_quarantines_false_run_and_reopens_root(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    root = agent("root", "codex", tmp_path)
    await history.session_started(root, str(tmp_path / "root.jsonl"))
    await history.session_ended(root, "incorrect_demotion")
    false_run = agent("terminal", "claude", tmp_path)
    false_run.agent_run_id = "false-run"
    await history.session_promoted(false_run, str(tmp_path / "sibling.jsonl"))

    await history.quarantine_misattributed_agent_run(
        "false-run", "root_identity_reconciled"
    )
    await history.session_promoted(root, str(tmp_path / "root.jsonl"))
    await history.reopen_agent_run(root.id)

    assert [row["id"] for row in await history.history()] == ["root"]
    quarantined = await history.history_entry("false-run")
    assert quarantined is not None
    assert quarantined["agent_visible"] == 0
    assert quarantined["exit_reason"] == "root_identity_reconciled"
    assert quarantined["transcript_path"] is None
    reopened = await history.history_entry("root")
    assert reopened is not None
    assert reopened["exited_at"] is None
    assert reopened["exit_reason"] is None
    assert reopened["final_state"] is None
    history.close()

    # Reopening the database re-runs _migrate_schema. Its agent_visible backfill
    # used to be unconditional, so every quarantined misattribution reappeared
    # under the sibling's identity after one daemon restart — and restarts are
    # routine here (session-preserving reload on every backend change).
    reopened_index = HistoryIndex(tmp_path / "mux.db")
    try:
        still_hidden = await reopened_index.history_entry("false-run")
        assert still_hidden is not None
        assert still_hidden["agent_visible"] == 0
        assert [row["id"] for row in await reopened_index.history()] == ["root"]
    finally:
        reopened_index.close()


async def test_historical_provider_collision_is_repaired_after_session_exit(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    root = agent("root", "codex", tmp_path)
    root.exe = "node.exe"
    root.args = [r"C:\npm\node_modules\@openai\codex\bin\codex.js"]
    root.native_session_id = "codex-native"
    await history.session_started(root, str(tmp_path / "codex.jsonl"))

    sibling = agent("sibling", "claude", tmp_path)
    sibling.native_session_id = "claude-native"
    await history.session_started(sibling, str(tmp_path / "claude.jsonl"))

    contaminated = agent("root", "claude", tmp_path)
    contaminated.exe = root.exe
    contaminated.args = root.args
    contaminated.native_session_id = sibling.native_session_id
    contaminated.agent_run_id = "false-run"
    await history.session_promoted(contaminated, str(tmp_path / "claude.jsonl"))

    assert await history.reconcile_historical_provider_collisions() == [
        ("root", "false-run", "root")
    ]
    assert {row["id"] for row in await history.history()} == {"root", "sibling"}
    false_row = await history.history_entry("false-run")
    assert false_row is not None
    assert false_row["agent_visible"] == 0
    assert false_row["exit_reason"] == "historical_provider_collision_reconciled"
    assert false_row["transcript_path"] is None
    assert await history.reconcile_historical_provider_collisions() == []
    history.close()


async def test_history_filters_pages_context_and_safe_deletion(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    transcript = tmp_path / "native.jsonl"
    transcript.write_text("native remains\n", encoding="utf-8")
    for index, backend in enumerate(("claude", "codex", "claude")):
        session = agent(f"agent-{index}", backend, tmp_path, project="shared")
        session.created_at = float(index + 1)
        session.context_window = 200_000
        session.context_pct = 0.25 + index / 10
        session.context_peak_pct = session.context_pct + 0.1
        session.tokens_in = 100 + index
        session.tokens_out = 20 + index
        session.model = f"model-{index}"
        session.measurement_source = f"{backend}-transcript"
        await history.session_started(session, str(transcript))
        await history.session_ended(session, "complete")

    first = await history.history_page(project_id="shared", limit=2)
    assert [row["id"] for row in first["items"]] == ["agent-2", "agent-1"]
    assert first["next_cursor"]
    second = await history.history_page(cursor=first["next_cursor"], limit=2)
    assert [row["id"] for row in second["items"]] == ["agent-0"]
    assert first["items"][0]["final_context_pct"] == 0.45
    assert first["items"][0]["peak_context_pct"] == 0.55
    assert first["items"][0]["measurement_source"] == "claude-transcript"
    assert [row["sessions"] for row in await history.history_projects()] == [3]

    assert await history.delete_history_entry("agent-1") is True
    assert transcript.read_text(encoding="utf-8") == "native remains\n"
    history.close()


async def test_ungrouped_history_is_filterable_without_matching_all_projects(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    grouped = agent("grouped", "claude", tmp_path, project="shared")
    ungrouped = agent("ungrouped", "codex", tmp_path, project="shared")
    ungrouped.project_id = None
    ungrouped.project_label = None
    ungrouped.project_root = None
    await history.session_started(grouped, None)
    await history.session_started(ungrouped, None)

    rows = await history.history_page(project_id="__ungrouped__")
    assert [row["id"] for row in rows["items"]] == ["ungrouped"]
    history.close()


async def test_event_cursor_is_monotonic_and_gap_free(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    events = EventBus(history.append_event)
    emitted = [await events.emit(f"event-{index}") for index in range(4)]
    assert [event.seq for event in emitted] == [1, 2, 3, 4]
    assert [row["seq"] for row in await history.events(after_seq=2)] == [3, 4]
    history.close()


async def test_workload_telemetry_correlates_rates_context_and_completion_evidence(
    tmp_path: Path,
) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    current = agent("observed", "claude", tmp_path)
    current.model = "claude-test"
    current.context_window = 200_000
    current.context_pct = 0.4
    current.context_peak_pct = 0.6
    await history.session_started(current, None)
    events = EventBus(history.append_event)
    await events.emit("turn_ended", session_id=current.id, source="transcript")
    await events.emit("stalled", session_id=current.id, source="automation")
    await events.emit("approval_needed", session_id=current.id, source="hook")
    await events.emit(
        "tool_result",
        session_id=current.id,
        source="transcript",
        tool="pytest",
        success=True,
    )
    await history.session_ended(current, "complete")

    telemetry = await history.workload_telemetry()
    dimension = telemetry["dimensions"][0]

    assert dimension["backend"] == "claude"
    assert dimension["model"] == "claude-test"
    assert dimension["turns_per_run"] == 1
    assert dimension["stalls_per_run"] == 1
    assert dimension["approvals_per_run"] == 1
    assert dimension["completion_evidence_runs"] == 1
    assert dimension["completion_evidence_count"] == 1
    assert dimension["average_final_context_pct"] == 0.4
    assert dimension["average_peak_context_pct"] == 0.6
    history.close()


async def test_worktrees_have_distinct_scopes_but_share_repo_group(
    tmp_path: Path,
    monkeypatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    async def fake_git(cwd: Path, *args: str) -> str | None:
        if args[-1] == "--show-toplevel":
            return str(cwd)
        if args[-1] == "--git-common-dir":
            return str(tmp_path / "common.git")
        if args[-1] == "origin":
            return "git@github.com:example/shared.git"
        return None

    monkeypatch.setattr("swe_mux.git_projects._git", fake_git)
    left = await resolve_project(first)
    right = await resolve_project(second)
    assert left.id != right.id
    assert left.repo_group_id == right.repo_group_id
    assert left.root != right.root


async def test_project_resolution_runs_git_probes_concurrently_and_caches_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = tmp_path / "parallel-resolution"
    project.mkdir()
    active = 0
    peak = 0
    calls = 0

    async def fake_git(cwd: Path, *args: str) -> str | None:
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return str(cwd) if args[-1] == "--show-toplevel" else None

    monkeypatch.setattr("swe_mux.git_projects._git", fake_git)
    first = await resolve_project(project)
    second = await resolve_project(project)

    assert first == second
    assert peak == 3
    assert calls == 3


def test_context_backfill_distinguishes_current_window_from_totals(tmp_path: Path) -> None:
    claude = tmp_path / "claude.jsonl"
    claude.write_text(
        '{"type":"assistant","message":{"model":"claude-haiku-4-5",'
        '"usage":{"input_tokens":100,"output_tokens":5}}}\n'
        '{"type":"assistant","message":{"model":"claude-haiku-4-5",'
        '"usage":{"input_tokens":50,"output_tokens":7}}}\n',
        encoding="utf-8",
    )
    summary = summarize_transcript(claude, "claude")
    assert summary["tokens_in"] == 150
    assert summary["tokens_out"] == 12
    assert summary["final_context_pct"] == 50 / 200_000
    assert summary["peak_context_pct"] == 100 / 200_000
    assert summary["measurement_source"] == "claude-transcript-backfill"
