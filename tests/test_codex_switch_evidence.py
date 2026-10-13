"""The switch watcher pays only when a conversation could have moved.

Until 2026-10-12 every idle Codex pane, forever, ran an OS process proof every
10 s (which enumerated the host's ~450,000 handles under the GIL) and a directory
search every 2 s (which re-read the first line of the 20 newest rollouts). The
stall log of that day was dominated by both. These tests pin the replacement
contract:

- a followed file gets one baseline proof, then proofs wait for evidence that
  the pane handed its CLI a line or ran a turn the file does not record;
- the heuristic search runs only inside that evidence window;
- a rollout's identity header is read once per file instance;
- the watcher reads the followed file's last write from the tailer's own stat.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from swe_mux.adapters import codex as codex_adapter
from swe_mux.adapters.codex import (
    CodexAdapter,
    rollout_identity,
    rollout_identity_stats,
)
from swe_mux.observation import JsonlTailer
from swe_mux.session import (
    PROCESS_PROOF_MAX_INTERVAL_SECONDS,
    PROCESS_PROOF_MIN_INTERVAL_SECONDS,
    TRANSCRIPT_SWITCH_QUIET_SECONDS,
    Session,
    SessionManager,
)
from tests.support.settle import until
from tests.test_conversation_rollover import agent_record, rollover_manager

ROOT = "11111111-1111-4111-8111-111111111111"
OTHER = "33333333-3333-4333-8333-333333333333"


def rollout(directory: Path, native: str, cwd: Path, **payload: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-10-12T00-00-00-{native}.jsonl"
    meta = {"type": "session_meta", "payload": {"id": native, "cwd": str(cwd), **payload}}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    return path


def elapse(session: Session, seconds: float) -> None:
    """Move the last proof back in time instead of sleeping through the spacing."""
    state = session.observation_state
    state["process_transcript_probe_at"] = (
        float(state.get("process_transcript_probe_at", 0.0)) - seconds
    )


def proving_manager(
    tmp_path: Path, answer: Path | None
) -> tuple[Any, Session, list[tuple[int, float | None]]]:
    rec = agent_record(backend="codex", cwd=str(tmp_path), native_id=ROOT)
    rec.pid, rec.root_started_at = 123, 10.0
    manager, session = rollover_manager(rec)
    calls: list[tuple[int, float | None]] = []

    def prove(pid: int, started_at: float | None) -> Path | None:
        calls.append((pid, started_at))
        return answer

    adapter = cast(Any, session.adapter)
    adapter.process_transcript_path = prove
    adapter.transcript_native_id = lambda _path: ROOT
    return manager, session, calls


# ------------------------------------------------------------ the proof's gate


def test_the_proof_decision_table(tmp_path: Path) -> None:
    rec = agent_record(backend="codex", cwd=str(tmp_path), native_id=ROOT)
    _manager, session = rollover_manager(rec)
    current = tmp_path / "followed.jsonl"
    due = SessionManager._process_proof_due
    state = session.observation_state
    now = time.monotonic()
    wall = time.time()
    idle_write = wall - 600

    # Never closer together than the minimum, whatever the reason.
    state["process_transcript_probe_at"] = now - 1
    assert due(session, current, idle_write, now) is None
    state["process_transcript_probe_at"] = now - PROCESS_PROOF_MIN_INTERVAL_SECONDS - 1

    # One baseline per followed file, then nothing without evidence - for good.
    assert due(session, current, idle_write, now) == "baseline"
    state["process_proof_baseline"] = str(current)
    assert due(session, current, idle_write, now) is None
    state["process_transcript_probe_at"] = now - 86_400
    assert due(session, current, idle_write, now) is None
    assert due(session, tmp_path / "another.jsonl", idle_write, now) == "baseline"

    # A submitted line the file never recorded, once it has settled.
    session.composer.last_submit_at = wall - 1
    assert due(session, current, idle_write, now) is None
    session.composer.last_submit_at = wall - TRANSCRIPT_SWITCH_QUIET_SECONDS - 1
    assert due(session, current, idle_write, now) == "evidence"
    # ...unless the file was written after it: the line landed in this conversation.
    assert due(session, current, wall - 1, now) is None
    # Evidence already answered by a proof is not evidence again.
    state["process_proof_evidence"] = session.composer.last_submit_at
    assert due(session, current, idle_write, now) is None

    # A turn hook needs to clear the file's last write by the quiet window.
    session.last_turn_hook_ts = idle_write + 1
    assert due(session, current, idle_write, now) is None
    session.last_turn_hook_ts = wall - TRANSCRIPT_SWITCH_QUIET_SECONDS - 0.5
    assert due(session, current, idle_write, now) == "evidence"


def test_an_unbound_or_provisional_pane_keeps_probing_on_its_backoff(tmp_path: Path) -> None:
    rec = agent_record(backend="codex", cwd=str(tmp_path), native_id=ROOT)
    _manager, session = rollover_manager(rec)
    due = SessionManager._process_proof_due
    state = session.observation_state
    now = time.monotonic()
    state["process_proof_spacing"] = 40.0
    state["process_transcript_probe_at"] = now - 20

    assert due(session, None, None, now) is None
    state["process_transcript_probe_at"] = now - 41
    assert due(session, None, None, now) == "unbound"
    session.transcript_provisional = True
    assert due(session, tmp_path / "guess.jsonl", None, now) == "provisional"

    # Fresh evidence cuts a backed-off wait back to the minimum spacing.
    state["process_transcript_probe_at"] = now - PROCESS_PROOF_MIN_INTERVAL_SECONDS - 1
    assert due(session, tmp_path / "guess.jsonl", None, now) is None
    session.composer.last_submit_at = time.time()
    assert due(session, tmp_path / "guess.jsonl", None, now) == "provisional"


async def test_an_idle_followed_rollout_is_proven_once_and_then_left_alone(
    tmp_path: Path,
) -> None:
    current = rollout(tmp_path / "sessions", ROOT, tmp_path)
    manager, session, calls = proving_manager(tmp_path, current)
    idle_write = time.time() - 600

    assert await manager._process_owned_transcript(session, current, idle_write) == current
    assert session.observation_state["process_proof_reason"] == "baseline"
    for _ in range(20):
        elapse(session, PROCESS_PROOF_MAX_INTERVAL_SECONDS)
        assert await manager._process_owned_transcript(session, current, idle_write) is None
    assert len(calls) == 1
    assert manager.observation_costs["process_proofs"] == 1
    assert manager.observation_costs["process_proofs_skipped"] == 20

    # `/new` typed into the pane: one proof, once the line has settled unrecorded.
    session.composer.last_submit_at = time.time() - TRANSCRIPT_SWITCH_QUIET_SECONDS - 1
    elapse(session, PROCESS_PROOF_MAX_INTERVAL_SECONDS)
    assert await manager._process_owned_transcript(session, current, idle_write) == current
    assert session.observation_state["process_proof_reason"] == "evidence"
    elapse(session, PROCESS_PROOF_MAX_INTERVAL_SECONDS)
    assert await manager._process_owned_transcript(session, current, idle_write) is None
    assert len(calls) == 2


async def test_confirming_the_same_file_backs_off_and_finding_a_new_one_does_not(
    tmp_path: Path,
) -> None:
    current = rollout(tmp_path / "sessions", ROOT, tmp_path)
    manager, session, calls = proving_manager(tmp_path, current)
    state = session.observation_state

    await manager._process_owned_transcript(session, current, time.time() - 600)
    assert state["process_proof_spacing"] == PROCESS_PROOF_MIN_INTERVAL_SECONDS * 2

    replacement = rollout(tmp_path / "sessions", OTHER, tmp_path)
    cast(Any, session.adapter).process_transcript_path = lambda *_: replacement
    cast(Any, session.adapter).transcript_native_id = lambda _path: OTHER
    session.composer.last_submit_at = time.time() - TRANSCRIPT_SWITCH_QUIET_SECONDS - 1
    elapse(session, PROCESS_PROOF_MAX_INTERVAL_SECONDS)
    assert await manager._process_owned_transcript(session, current, time.time() - 600) == (
        replacement
    )
    assert state["process_proof_spacing"] == PROCESS_PROOF_MIN_INTERVAL_SECONDS
    assert len(calls) == 1


# ------------------------------------------------------- the rollout identity


def test_a_rollout_header_is_read_once_per_file_instance(tmp_path: Path) -> None:
    path = rollout(tmp_path / "sessions", ROOT, tmp_path)
    adapter = CodexAdapter(data_home_resolver=lambda: tmp_path)
    before = rollout_identity_stats()

    assert adapter.transcript_native_id(path) == ROOT
    for _ in range(10):
        assert adapter.transcript_native_id(path) == ROOT
    middle = rollout_identity_stats()
    assert middle["reads"] - before["reads"] == 1
    assert middle["hits"] - before["hits"] == 10

    # A rollout only ever grows; growth is the same file and needs no re-read.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "event_msg", "payload": {}}) + "\n")
    assert adapter.transcript_native_id(path) == ROOT
    assert rollout_identity_stats()["reads"] == middle["reads"]


def test_a_rewritten_rollout_is_read_again(tmp_path: Path) -> None:
    path = rollout(tmp_path / "sessions", ROOT, tmp_path)
    assert rollout_identity(path) is not None
    before = rollout_identity_stats()["reads"]
    # Same path, shorter content: not the file that was read.
    meta = {"type": "session_meta", "payload": {"id": OTHER, "cwd": "c"}}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    identity = rollout_identity(path)
    assert identity is not None and identity.native_id == OTHER
    assert rollout_identity_stats()["reads"] == before + 1


def test_a_rollout_replaced_under_the_same_name_is_read_again(tmp_path: Path) -> None:
    path = rollout(tmp_path / "sessions", ROOT, tmp_path)
    assert rollout_identity(path) is not None
    # A different file instance that happens to be longer, so only the instance
    # token can tell it apart from growth.
    swap = tmp_path / "swap.jsonl"
    padding = "x" * 512
    meta = {"type": "session_meta", "payload": {"id": OTHER, "cwd": padding}}
    swap.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    token = codex_adapter._instance_token(path.stat())
    os.replace(swap, path)
    if codex_adapter._instance_token(path.stat()) == token:
        pytest.skip("this filesystem reports the same instance token for the replacement")
    identity = rollout_identity(path)
    assert identity is not None and identity.native_id == OTHER


def test_a_walk_hint_answers_without_a_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_adapter, "_ROLLOUT_CACHE_SECONDS", 3600.0)
    root = tmp_path / "sessions"
    path = rollout(root / "2026", ROOT, tmp_path)
    adapter = CodexAdapter(data_home_resolver=lambda: tmp_path)
    assert adapter.transcript_path(ROOT, tmp_path) == path

    stats: list[Path] = []
    real_stat = Path.stat

    def counting_stat(self: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        stats.append(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", counting_stat)
    before = rollout_identity_stats()
    assert adapter.transcript_path(ROOT, tmp_path) == path
    after = rollout_identity_stats()
    assert path not in stats
    assert after["reads"] == before["reads"]
    assert after["hits"] == before["hits"] + 1


# --------------------------------------------------------- the watcher's tick


def watched_codex_pane(tmp_path: Path) -> tuple[Any, Session, Path, list[int]]:
    """A real Codex adapter and session following an idle rollout."""
    home = tmp_path / "codex-home"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    current = rollout(home / "sessions" / "2026" / "10" / "12", ROOT, cwd)
    stamp = time.time() - 100
    os.utime(current, (stamp, stamp))
    adapter = CodexAdapter(data_home_resolver=lambda: home)
    proofs: list[int] = []

    def prove(pid: int, started_at: float | None) -> Path:
        proofs.append(pid)
        return current

    cast(Any, adapter).process_transcript_path = prove
    rec = agent_record(backend="codex", cwd=str(cwd), native_id=ROOT)
    rec.pid, rec.root_started_at = 123, 10.0
    rec.agent_run_started_at = time.time() - 1000
    pty = SimpleNamespace(graceful_exit="", isalive=lambda: True)
    session = Session(rec, cast(Any, pty), cast(Any, adapter), 32, "secret")
    session.transcript_path = current
    manager = cast(Any, SessionManager.__new__(SessionManager))
    manager.sessions = {rec.id: session}
    manager.events = SimpleNamespace(emit=AsyncMock())
    manager._note_transcript_staleness = AsyncMock()
    return manager, session, current, proofs


async def run_ticks(manager: Any, session: Session, current: Path, ticks: int) -> Path | None:
    seen = manager._note_transcript_staleness.await_count

    async def until_ticks() -> None:
        await until(
            lambda: manager._note_transcript_staleness.await_count >= seen + ticks,
            seconds=10,
        )

    observe_task = asyncio.create_task(until_ticks())
    try:
        return cast(
            "Path | None",
            await SessionManager._watch_transcript_switch(
                manager, session, current, asyncio.Event(), observe_task
            ),
        )
    finally:
        await observe_task


async def test_an_idle_codex_pane_spends_nothing_per_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.session.TRANSCRIPT_SWITCH_POLL_SECONDS", 0.01)
    manager, session, current, proofs = watched_codex_pane(tmp_path)

    assert await run_ticks(manager, session, current, 1) is None
    assert proofs == [123]  # the baseline
    reads = rollout_identity_stats()["reads"]

    assert await run_ticks(manager, session, current, 25) is None
    assert proofs == [123]
    assert rollout_identity_stats()["reads"] == reads
    costs = manager.observation_costs
    assert costs["heuristic_searches"] == 0
    assert costs["heuristic_searches_skipped"] >= 26
    assert costs["process_proofs"] == 1

    # A submitted line arms the search, and the warm identity cache answers it:
    # the rollout header is not read again.
    session.composer.last_submit_at = time.time()
    hits = rollout_identity_stats()["hits"]
    assert await run_ticks(manager, session, current, 3) is None
    assert costs["heuristic_searches"] >= 3
    assert rollout_identity_stats()["reads"] == reads
    assert rollout_identity_stats()["hits"] > hits


async def test_the_watcher_reads_the_followed_file_from_the_tailers_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("swe_mux.session.TRANSCRIPT_SWITCH_POLL_SECONDS", 0.01)
    manager, session, current, _proofs = watched_codex_pane(tmp_path)
    reading = current.stat().st_mtime

    async def fresh_tailer() -> None:
        while True:
            session.transcript_stat = (current, reading, time.monotonic())
            await asyncio.sleep(0.005)

    tailer = asyncio.create_task(fresh_tailer())
    try:
        await run_ticks(manager, session, current, 5)
    finally:
        tailer.cancel()
        await asyncio.gather(tailer, return_exceptions=True)
    assert manager.observation_costs["watcher_stat_fallbacks"] == 0

    session.transcript_stat = None
    await run_ticks(manager, session, current, 2)
    assert manager.observation_costs["watcher_stat_fallbacks"] >= 2


def test_the_costs_are_reported_for_diagnostics(tmp_path: Path) -> None:
    manager, _session, _calls = proving_manager(tmp_path, None)
    manager._count_observation_cost("process_proofs_skipped")
    snapshot = manager.observation_cost_snapshot()
    assert snapshot["process_proofs_skipped"] == 1
    assert snapshot["process_proofs"] == 0
    assert {"hits", "reads", "entries"} <= set(snapshot["rollout_identities"])
    if sys.platform == "win32":
        assert {"queries", "abandoned_names", "stuck_workers"} <= set(
            snapshot["open_file_queries"]
        )


def test_last_write_prefers_a_current_tailer_reading_to_a_stat(tmp_path: Path) -> None:
    rec = agent_record(backend="codex", cwd=str(tmp_path), native_id=ROOT)
    _manager, session = rollover_manager(rec)
    missing = tmp_path / "never-created.jsonl"
    last_write = SessionManager._transcript_last_write_ts

    # A current reading answers without touching the disk (a stat would fail here).
    session.transcript_stat = (missing, 123.0, time.monotonic())
    assert last_write(session, missing) == 123.0
    # The tailer saw the file missing.
    present = tmp_path / "present.jsonl"
    present.write_text("{}\n", encoding="utf-8")
    session.transcript_stat = (present, None, time.monotonic())
    assert last_write(session, present) is None
    # A reading for another path, or an old one, is no reading: stat.
    session.transcript_stat = (missing, 123.0, time.monotonic())
    assert last_write(session, present) == present.stat().st_mtime
    session.transcript_stat = (present, 123.0, time.monotonic() - 60)
    assert last_write(session, present) == present.stat().st_mtime


async def test_the_tailer_shares_every_stat_it_takes(tmp_path: Path) -> None:
    path = tmp_path / "transcript.jsonl"
    readings: list[os.stat_result | None] = []
    tailer = JsonlTailer(path, on_stat=readings.append)
    stop = asyncio.Event()

    async def consume() -> None:
        async for _item in tailer.events(stop):
            pass

    task = asyncio.create_task(consume())
    try:
        await until(lambda: bool(readings) and readings[-1] is None, what="missing noted")
        path.write_text(json.dumps({"type": "event_msg"}) + "\n", encoding="utf-8")
        size = path.stat().st_size

        def stat_seen() -> bool:
            last = readings[-1] if readings else None
            return last is not None and last.st_size == size

        await until(stat_seen, what="the file's stat shared")
    finally:
        stop.set()
        await task
