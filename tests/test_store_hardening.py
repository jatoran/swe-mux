"""Store-layer regressions from the 2026-08-23 audit (ROADMAP_V2 S5).

Every test here pins a defect that was invisible from its own surface: a search
that answered from the wrong end of its page, a retention pass that held the
process-wide database lock for its whole sweep, a LIKE filter that silently
widened, an append that rewrote the rows it had just read, and an audit trail
that could record a state change that never happened.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.automation_store import (
    SCAN_SEARCH_SCAN_LIMIT,
    AutomationStore,
)
from swe_mux.history import HistoryIndex
from swe_mux.land_store import LandEvent, LandStore
from swe_mux.models import ProjectRecord, SessionRecord
from swe_mux.prompt_queue import PromptQueueStore
from swe_mux.scan_consumers import search_scan_records
from swe_mux.voice import VoiceStore

# -- S5.1 scan search ---------------------------------------------------------


def _seed_scan_records(store: AutomationStore, count: int, *, needle_at: int) -> str:
    """Insert `count` records for one Project, oldest first. Returns the run id."""
    project_id = "proj-1"
    rows = []
    for index in range(count):
        record = {
            "summary": (
                "recalibrated the flux capacitor"
                if index == needle_at
                else f"routine work item {index}"
            ),
            "work_phase": "implementing",
        }
        rows.append(
            (
                str(uuid.uuid4()),
                "sess-1",
                "run-1",
                project_id,
                float(index),
                float(index) + 0.5,
                "turn_ended",
                json.dumps(record),
                "hash",
                "model",
                None,
                None,
                0,
                0,
                0.0,
                float(index),
            )
        )

    def op() -> None:
        store._db.executemany(
            "INSERT INTO scan_timeline_records"
            "(id,session_id,agent_run_id,project_id,t0,t1,trigger,record_json,input_hash,"
            "requested_model,resolved_model,generation_id,input_tokens,output_tokens,"
            "cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        store._db.commit()

    store._executor.submit(op).result()
    return project_id


@pytest.mark.asyncio
async def test_scan_search_page_finds_the_newest_record_past_the_cap(tmp_path: Path) -> None:
    """The F6 regression: the newest record must be reachable, and the cut reported.

    Seeded with more records than one search reads, and the only match is the
    *last* one written. The old read took `scan_records`' oldest-first default,
    so this record was never in the page at all - and because the ranking
    re-sorts newest-first afterwards, the empty answer looked exactly like a
    Project that had never done the work.
    """
    store = AutomationStore(tmp_path / "mux.db")
    try:
        total = SCAN_SEARCH_SCAN_LIMIT + 500
        project_id = _seed_scan_records(store, total, needle_at=total - 1)

        page = await store.scan_search_page(project_id=project_id)

        assert len(page.records) == SCAN_SEARCH_SCAN_LIMIT
        assert page.truncated is True
        hits = search_scan_records(page.records, "flux capacitor")
        assert [hit["snippet"] for hit in hits] == ["recalibrated the flux capacitor"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_scan_search_page_reports_no_truncation_when_it_read_everything(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        project_id = _seed_scan_records(store, 10, needle_at=0)
        page = await store.scan_search_page(project_id=project_id)
        assert len(page.records) == 10
        assert page.truncated is False
    finally:
        store.close()


@pytest.mark.asyncio
async def test_scan_search_page_scopes_by_run(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        _seed_scan_records(store, 5, needle_at=0)

        def other() -> None:
            store._db.execute(
                "INSERT INTO scan_timeline_records"
                "(id,session_id,agent_run_id,project_id,t0,t1,trigger,record_json,input_hash,"
                "requested_model,resolved_model,generation_id,input_tokens,output_tokens,"
                "cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    "sess-2",
                    "run-2",
                    "proj-1",
                    99.0,
                    99.5,
                    "turn_ended",
                    json.dumps({"summary": "elsewhere"}),
                    "hash",
                    "model",
                    None,
                    None,
                    0,
                    0,
                    0.0,
                    99.0,
                ),
            )
            store._db.commit()

        store._executor.submit(other).result()

        scoped = await store.scan_search_page(agent_run_id="run-2")
        assert [row["agent_run_id"] for row in scoped.records] == ["run-2"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_scan_record_ordering_uses_an_index_rather_than_a_sort(tmp_path: Path) -> None:
    """`ORDER BY t0` had no index; the scoped ones all led with `created_at`."""
    store = AutomationStore(tmp_path / "mux.db")
    try:

        def plan() -> str:
            rows = store._db.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM scan_timeline_records "
                "WHERE project_id=? ORDER BY t0 DESC,created_at DESC LIMIT ?",
                ("proj-1", 10),
            ).fetchall()
            return " | ".join(str(row[-1]) for row in rows)

        detail = store._executor.submit(plan).result()
        assert "idx_scan_records_project_t0" in detail
        assert "TEMP B-TREE" not in detail
    finally:
        store.close()


@pytest.mark.asyncio
async def test_mcp_scan_search_reaches_the_newest_record_and_reports_the_cut(
    tmp_path: Path,
) -> None:
    """The caller-level half of F6: the tool, not just the store, must find it.

    `mcp.scan_search` took `scan_records`' oldest-first default, so on a Project
    past the scan cap the newest work was never in the page it ranked. The
    ranking then re-sorted newest-first, which is precisely what made the wrong
    answer look right.
    """
    from tests.test_mcp_scan_timeline import caller_session, record, seed, service, target_session

    store = AutomationStore(tmp_path / "mux.db")
    try:
        await seed(store, count=1, body=record(summary="filler"), start=0.0)
        _bulk_fill_scan_records(store, SCAN_SEARCH_SCAN_LIMIT, start=1.0)
        await seed(
            store,
            count=1,
            body=record(summary="recalibrated the flux capacitor"),
            start=1_000_000.0,
        )

        caller = caller_session()
        svc = service(caller, target_session(), store=store)
        result = await svc.scan_search(caller, {"query": "flux capacitor"})

        assert [hit["snippet"] for hit in result["results"]] == [
            "recalibrated the flux capacitor"
        ]
        assert result["records_truncated"] is True
        assert "records_truncated_note" in result
        # The scope flag `_memory_scope` returned was being dropped on the floor.
        assert "projects" in result
    finally:
        store.close()


@pytest.mark.asyncio
async def test_scan_timeline_search_endpoint_reports_truncation(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tests.test_phase77_behavioral import _app, _live_session

    store = AutomationStore(tmp_path / "mux.db")
    try:
        _bulk_fill_scan_records(
            store,
            SCAN_SEARCH_SCAN_LIMIT + 5,
            start=1.0,
            project_id="proj",
            run_id="run-1",
            needle_at=SCAN_SEARCH_SCAN_LIMIT + 4,
        )
        session = _live_session("s1", "run-1")
        sessions = SimpleNamespace(sessions={"s1": session}, resolve=lambda _sid: session)
        app = _app(store, sessions, {"semantic_history_search"})
        async with TestClient(TestServer(app)) as client:
            body = await (
                await client.get("/search", params={"run_id": "run-1", "q": "flux capacitor"})
            ).json()
        assert body["enabled"] is True
        assert body["truncated"] is True
        assert body["scanned"] == SCAN_SEARCH_SCAN_LIMIT
        assert [hit["snippet"] for hit in body["results"]] == [
            "recalibrated the flux capacitor"
        ]
    finally:
        store.close()


def _bulk_fill_scan_records(
    store: AutomationStore,
    count: int,
    *,
    start: float,
    project_id: str = "p1",
    run_id: str = "s2",
    needle_at: int | None = None,
) -> None:
    rows = [
        (
            str(uuid.uuid4()),
            "s2",
            run_id,
            project_id,
            start + index,
            start + index + 0.5,
            "tool_result",
            json.dumps(
                {
                    "summary": (
                        "recalibrated the flux capacitor"
                        if index == needle_at
                        else f"filler {index}"
                    ),
                    "work_phase": "implementation",
                }
            ),
            "hash",
            "model",
            None,
            None,
            0,
            0,
            0.0,
            start + index,
        )
        for index in range(count)
    ]

    def op() -> None:
        store._db.executemany(
            "INSERT INTO scan_timeline_records"
            "(id,session_id,agent_run_id,project_id,t0,t1,trigger,record_json,input_hash,"
            "requested_model,resolved_model,generation_id,input_tokens,output_tokens,"
            "cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        store._db.commit()

    store._executor.submit(op).result()


# -- S5.2 retention batching --------------------------------------------------


def _fill_notifications(store: AutomationStore, count: int, created_at: float) -> None:
    rows = [
        (str(uuid.uuid4()), "run-1", "sess-1", None, "k", "t", "m", "info", "{}", created_at)
        for _ in range(count)
    ]

    def op() -> None:
        store._db.executemany(
            "INSERT INTO automation_notifications"
            "(id,agent_run_id,session_id,rule_id,kind,title,message,severity,evidence_json,"
            "created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        store._db.commit()

    store._executor.submit(op).result()


@pytest.mark.asyncio
async def test_retention_commits_between_batches(tmp_path: Path) -> None:
    """The F12 regression: the sweep must not be one transaction over every table.

    An independent connection to the same file is the only honest witness. It can
    only observe a partially-drained table if the deleting worker committed and
    released the writer slot part-way through, which is exactly what "bounded
    rowid batches with commits between batches" has to mean.
    """
    path = tmp_path / "mux.db"
    store = AutomationStore(path)
    observer = sqlite3.connect(path)
    try:
        _fill_notifications(store, 1200, time.time() - 400 * 86400)
        assert observer.execute("SELECT COUNT(*) FROM automation_notifications").fetchone()[0] == (
            1200
        )

        # Sample the table from an independent connection after every store
        # operation the prune submits. Nothing about the prune is stubbed - only
        # observed - so the assertion is about the real code path.
        samples: list[int] = []
        original_run = store._run

        async def sampling_run(fn: Any) -> Any:
            result = await original_run(fn)
            samples.append(
                int(
                    observer.execute(
                        "SELECT COUNT(*) FROM automation_notifications"
                    ).fetchone()[0]
                )
            )
            return result

        store._run = sampling_run  # type: ignore[method-assign]
        removed = await store.prune(90)
        store._run = original_run  # type: ignore[method-assign]

        assert removed == {"automation_notifications": 1200}
        assert observer.execute("SELECT COUNT(*) FROM automation_notifications").fetchone()[0] == 0
        # The proof: an outside reader saw the table part-drained. A single
        # transaction over the whole sweep can only ever show 1200 then 0.
        intermediate = sorted({count for count in samples if 0 < count < 1200})
        assert intermediate == [200, 700], samples
    finally:
        observer.close()
        store.close()


@pytest.mark.asyncio
async def test_retention_reports_rows_removed_per_table(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        _fill_notifications(store, 1100, time.time() - 400 * 86400)
        _fill_notifications(store, 3, time.time())
        removed = await store.prune(90)
        assert removed == {"automation_notifications": 1100}

        def remaining() -> int:
            return int(
                store._db.execute("SELECT COUNT(*) FROM automation_notifications").fetchone()[0]
            )

        assert store._executor.submit(remaining).result() == 3
    finally:
        store.close()


@pytest.mark.asyncio
async def test_retention_logs_what_it_removed(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        _fill_notifications(store, 7, time.time() - 400 * 86400)
        with caplog.at_level("INFO", logger="swe_mux.automation_store"):
            await store.prune(90)
        messages = [record.getMessage() for record in caplog.records]
        assert any(
            "automation_retention_pruned" in message
            and "table=automation_notifications" in message
            and "rows_removed=7" in message
            and "elapsed_ms=" in message
            for message in messages
        ), messages
        assert any("automation_retention_swept" in message for message in messages), messages
    finally:
        store.close()


# -- S5.3 voice eviction ------------------------------------------------------


def _add_clip(
    store: VoiceStore, *, clip_id: str, stream_id: str | None, created_at: float, size: int
) -> None:
    def op() -> None:
        store._db.execute(
            "INSERT INTO voice_clips(id,session_id,created_at,trigger,content_mode,engine,"
            "voice,text,file_path,format,size_bytes,status,stream_id,segment_index,"
            "segment_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                clip_id,
                "sess-1",
                created_at,
                "manual",
                "summary",
                "kokoro",
                "af",
                "text",
                f"{clip_id}.wav",
                "wav",
                size,
                "ready",
                stream_id,
                0,
                1,
            ),
        )
        store._db.commit()

    store._executor.submit(op).result()


@pytest.mark.asyncio
async def test_voice_eviction_takes_whole_streams_and_leaves_the_newest(tmp_path: Path) -> None:
    store = VoiceStore(tmp_path / "mux.db")
    try:
        _add_clip(store, clip_id="a0", stream_id="stream-a", created_at=1.0, size=100)
        _add_clip(store, clip_id="a1", stream_id="stream-a", created_at=1.5, size=100)
        _add_clip(store, clip_id="solo", stream_id=None, created_at=2.0, size=100)
        _add_clip(store, clip_id="b0", stream_id="stream-b", created_at=3.0, size=100)

        removed = await store.prune(150)

        assert sorted(removed) == ["a0.wav", "a1.wav", "solo.wav"]
        assert await store.clip_ids() == {"b0"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_voice_group_lookup_is_indexed_and_matches_only_its_own_stream(
    tmp_path: Path,
) -> None:
    """`COALESCE(stream_id, id)=?` was opaque to both indexes; the OR form is not.

    The second half is the semantic one. A bare `stream_id=? OR id=?` would also
    match a row that belongs to a *different* stream and happens to carry the key
    as its own id — which `COALESCE(stream_id, id)=?` never did. The
    `stream_id IS NULL` guard is what keeps the rewrite equivalent instead of
    merely fast, and this pins it: `member` is a segment of `stream-key`, while
    `stream-key` is also the id of a row belonging to `stream-other`.
    """
    store = VoiceStore(tmp_path / "mux.db")
    try:
        _add_clip(store, clip_id="member", stream_id="stream-key", created_at=1.0, size=10)
        _add_clip(store, clip_id="stream-key", stream_id="stream-other", created_at=2.0, size=10)

        def plan() -> str:
            rows = store._db.execute(
                "EXPLAIN QUERY PLAN SELECT file_path FROM voice_clips "
                "WHERE (stream_id=? OR (stream_id IS NULL AND id=?))",
                ("stream-key", "stream-key"),
            ).fetchall()
            return " | ".join(str(row[-1]) for row in rows)

        detail = store._executor.submit(plan).result()
        assert "idx_voice_clips_stream" in detail
        assert "SCAN voice_clips" not in detail

        removed = await store.delete_clip("member")
        assert removed == ["member.wav"]
        assert await store.clip_ids() == {"stream-key"}
    finally:
        store.close()


# -- S5.4 prompt-queue tail append -------------------------------------------


class _CountingConnection:
    """Wraps the store connection to count the UPDATEs one append performs."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.position_updates = 0

    def execute(self, sql: str, *args: Any) -> sqlite3.Cursor:
        if sql.startswith("UPDATE queue_messages SET position="):
            self.position_updates += 1
        return self._db.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


async def _append(store: PromptQueueStore, body: str, **extra: Any) -> dict[str, Any]:
    return await store.create_message(
        target_session_id="sess-1",
        target_agent_run_id=None,
        target_backend="claude",
        target_label=None,
        project_id=None,
        body=body,
        armed=False,
        sender_kind="human",
        sender_id=None,
        **extra,
    )


@pytest.mark.asyncio
async def test_tail_append_does_not_renumber_the_queue(tmp_path: Path) -> None:
    """The F18 regression: an append rewrote every visible row's position.

    Positions 0..n-1 are already correct when a message goes on the end, so those
    UPDATEs wrote the values the rows already held - O(n) writes per append on a
    surface whose normal use is appending.
    """
    store = PromptQueueStore(tmp_path / "mux.db")
    try:
        for index in range(5):
            await _append(store, f"m{index}")

        counter = _CountingConnection(store._db)
        store._db = counter  # type: ignore[assignment]
        await _append(store, "tail")
        assert counter.position_updates == 0

        store._db = counter._db
        rows = (await store.messages_for_target("sess-1"))["messages"]
        assert [row["body"] for row in rows] == ["m0", "m1", "m2", "m3", "m4", "tail"]
        assert [row["position"] for row in rows] == [0, 1, 2, 3, 4, 5]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_anchor_insert_still_renumbers(tmp_path: Path) -> None:
    store = PromptQueueStore(tmp_path / "mux.db")
    try:
        first = await _append(store, "m0")
        await _append(store, "m1")
        await _append(store, "m2")

        counter = _CountingConnection(store._db)
        store._db = counter  # type: ignore[assignment]
        await _append(store, "inserted", insert_after=first["id"])
        assert counter.position_updates == 4

        store._db = counter._db
        rows = (await store.messages_for_target("sess-1"))["messages"]
        assert [row["body"] for row in rows] == ["m0", "inserted", "m1", "m2"]
        assert [row["position"] for row in rows] == [0, 1, 2, 3]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_tail_append_after_a_delete_keeps_positions_gap_free(tmp_path: Path) -> None:
    """Deletes renumber, so the append's `MAX(position)+1` cannot leave a hole."""
    store = PromptQueueStore(tmp_path / "mux.db")
    try:
        await _append(store, "m0")
        middle = await _append(store, "m1")
        await _append(store, "m2")
        await store.delete_message(middle["id"])
        await _append(store, "tail")

        rows = (await store.messages_for_target("sess-1"))["messages"]
        assert [row["body"] for row in rows] == ["m0", "m2", "tail"]
        assert [row["position"] for row in rows] == [0, 1, 2]
    finally:
        store.close()


# -- S5.5 LIKE escaping -------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_target_fragment_treats_metacharacters_literally(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        for target in ("src/land_store.py", "src/land-store.py"):
            await store.save_scan_record(
                session_id="sess-1",
                agent_run_id="run-1",
                project_id="proj-1",
                t0=1.0,
                t1=2.0,
                trigger="turn_ended",
                record={"summary": "s", "target": [target]},
                input_hash="h",
                requested_model="m",
                resolved_model=None,
                generation_id=None,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
            )

        matched = await store.scan_records(target_fragment="land_store")
        assert [row["target"] for row in matched] == [["src/land_store.py"]]
        assert await store.scan_records(target_fragment="land%store") == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_experience_search_treats_metacharacters_literally(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    try:
        for index, error in enumerate(("no_such_module", "no-such-module")):
            await store.add_experience(
                project_scope_id="proj-1",
                backend="claude",
                error=error,
                resolution="fixed",
                source_run_id=f"run-{index}",
                confidence=1.0,
            )
        found = await store.experiences(query="no_such")
        assert [row["error_summary"] for row in found] == ["no_such_module"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_history_metadata_search_treats_metacharacters_literally(tmp_path: Path) -> None:
    """Both history metadata filters skipped the module's own `_escape_like`."""
    index = HistoryIndex(tmp_path / "mux.db")
    try:
        await index.upsert_project(ProjectRecord("default", "Main", str(tmp_path), 0))
        for name in ("swe_mux daemon", "swe-mux daemon"):
            await index.session_started(
                SessionRecord(
                    str(uuid.uuid4()),
                    name,
                    "default",
                    "claude",
                    str(uuid.uuid4()),
                    str(tmp_path),
                    "claude.exe",
                    [],
                    state="running",
                ),
                None,
            )
        rows = await index.history(query="swe_mux")
        assert [row["name"] for row in rows] == ["swe_mux daemon"]

        page = await index.history_page(query="swe_mux", search_scope="metadata")
        assert [row["name"] for row in page["items"]] == ["swe_mux daemon"]
    finally:
        index.close()


# -- S5.6 land audit atomicity ------------------------------------------------


def _claim(store: LandStore, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": "proj-1",
        "project_root": str(Path("/repo")),
        "worktree_root": str(Path("/repo/wt")),
        "branch": "worktree-x",
        "requested_oid": "a" * 40,
        "trunk_ref": "master",
        **extra,
    }


@pytest.mark.asyncio
async def test_enqueue_and_its_opening_event_commit_together(tmp_path: Path) -> None:
    store = LandStore(tmp_path / "mux.db")
    try:
        row = await store.enqueue(
            **_claim(store),
            event=LandEvent(step="request", outcome="queued", detail={"origin": "operator"}),
        )
        events = await store.events(row["id"])
        assert [(event["step"], event["outcome"]) for event in events] == [("request", "queued")]
        assert events[0]["project_id"] == "proj-1"
        assert events[0]["detail"] == {"origin": "operator"}
    finally:
        store.close()


@pytest.mark.asyncio
async def test_transition_and_its_event_commit_together(tmp_path: Path) -> None:
    store = LandStore(tmp_path / "mux.db")
    try:
        row = await store.enqueue(**_claim(store))
        await store.transition(
            row["id"],
            expect=("queued",),
            state="cancelled",
            reason="cancelled by the operator",
            event=LandEvent(step="request", outcome="cancelled"),
        )
        events = await store.events(row["id"])
        assert [(event["step"], event["outcome"]) for event in events] == [
            ("request", "cancelled")
        ]
        # The event is filed under the row's own Project, never a caller's guess.
        assert events[0]["project_id"] == "proj-1"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_refused_transition_writes_no_event(tmp_path: Path) -> None:
    """The half this makes impossible: a trail entry for a move that lost its race.

    Three land-queue paths wrote the event first, so a request that then failed
    its conditional UPDATE - an operator cancel landing between the two, or a
    daemon dying - left `verify/skipped` standing over a row still in
    `reconciling`. Rolling the UPDATE back now discards the event with it.
    """
    from swe_mux.land_store import LandConflict

    store = LandStore(tmp_path / "mux.db")
    try:
        row = await store.enqueue(**_claim(store))
        with pytest.raises(LandConflict):
            await store.transition(
                row["id"],
                expect=("verifying",),
                state="landing",
                event=LandEvent(step="verify", outcome="skipped"),
            )
        assert await store.events(row["id"]) == []
        current = await store.get(row["id"])
        assert current is not None
        assert current["state"] == "queued"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_restore_requeues_and_records_orphans_in_one_commit(tmp_path: Path) -> None:
    store = LandStore(tmp_path / "mux.db")
    try:
        row = await store.enqueue(**_claim(store))
        await store.transition(row["id"], expect=("queued",), state="verifying")

        recovered = await store.restore()

        assert [item["id"] for item in recovered] == [row["id"]]
        current = await store.get(row["id"])
        assert current is not None
        assert current["state"] == "queued"
        events = await store.events(row["id"])
        assert [(event["step"], event["outcome"]) for event in events] == [
            ("verifying", "orphaned")
        ]
    finally:
        store.close()
