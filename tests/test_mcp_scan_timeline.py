"""Phase 7.11: the scan timeline as an agent-readable surface.

What these pin is the contract, not the wording:

- the projection drops the heavy fields and keeps the two that let a reader
  calibrate trust (`repaired_fields`, `messages_seen`);
- an absent `approach_status` stays absent rather than becoming `unknown`;
- the store's filters and the `since_t1` cursor run in SQL, so a bounded page
  means "rows returned", not "rows scanned";
- the per-Project gate answers `disabled` rather than a fake empty, and it is
  the **target session's** Project that is gated;
- an ended session is readable, and a stopped scanner never reads as quiet;
- nothing here can trigger a scan.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.automation_store import AutomationStore
from swe_mux.mcp import TOOLS, McpService
from swe_mux.mcp_contract import READ_TOOL_NAMES, WRITE_TOOL_NAMES
from swe_mux.prompt_queue import QueueError
from swe_mux.scan_consumers import (
    DIGEST_MAX_CLAIMS,
    DIGEST_MAX_SEGMENTS,
    catch_me_up,
    project_record,
    repaired_fields,
)
from tests.test_mcp import HistoryStub, live_session, manager_for

SCAN_TOOLS = ("scan_timeline", "scan_search")
ALL_ENABLED = frozenset({"scan_reads", "semantic_history_search"})


def record(
    *,
    work_phase: str = "implementation",
    summary: str = "did work",
    intent: str = "do work",
    claim: str = "",
    user_ask: str = "",
    blocked_on: str = "none",
    approach_status: str | None = "active",
    dead_end: str = "",
    confidence: float = 0.8,
    targets: list[str] | None = None,
    repairs: list[str] | None = None,
    messages_seen: int = 4,
    truncated: bool = False,
) -> dict[str, Any]:
    """A stored record body, shaped exactly as `save_scan_record` persists one."""
    body: dict[str, Any] = {
        "schema_version": 1,
        "work_phase": work_phase,
        "summary": summary,
        "intent": intent,
        "claim": claim,
        "user_ask": user_ask,
        "blocked_on": blocked_on,
        "dead_end": dead_end,
        "confidence": confidence,
        "behavior": ["reasoning"],
        "novelty": 0.5,
        "target": list(targets or ["src/swe_mux/server.py"]),
        "evidence_refs": [{"kind": "transcript", "ts": 1.0, "input_hash": "h" * 64}],
        "tier0_fact_ids": ["f1", "f2"],
        "coverage": {
            "messages_seen": messages_seen,
            "facts_seen": 2,
            "truncated": truncated,
            "remaining": 0,
        },
        "repairs": list(repairs or []),
        "observer_model": "test/model",
        "prompt_hash": "p" * 64,
        "prompt_version": 4,
    }
    if approach_status is not None:
        body["approach_status"] = approach_status
    return body


async def seed(
    store: AutomationStore,
    *,
    session_id: str = "s2",
    run_id: str = "s2",
    project_id: str = "p1",
    count: int = 4,
    trigger: str = "tool_result",
    body: dict[str, Any] | None = None,
    start: float = 100.0,
) -> list[str]:
    ids: list[str] = []
    await store.set_scan_run_enabled(
        agent_run_id=run_id,
        session_id=session_id,
        project_id=project_id,
        enabled=True,
    )
    for index in range(count):
        saved = await store.save_scan_record(
            session_id=session_id,
            agent_run_id=run_id,
            project_id=project_id,
            t0=start + index,
            t1=start + index + 1,
            trigger=trigger,
            record=body or record(),
            input_hash=f"in-{index}",
            requested_model="test/model",
            resolved_model="test/model",
            generation_id=f"g{index}",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
        )
        ids.append(str(saved["id"]))
    return ids


def projects(root: str = "D:/work") -> Any:
    return SimpleNamespace(
        projects={"p1": SimpleNamespace(id="p1", name="Work", root=root)}
    )


def gate(enabled: frozenset[str] = ALL_ENABLED) -> Any:
    async def _gate(_root: str) -> frozenset[str]:
        return enabled

    return _gate


class ScanServiceStub:
    """Only `liveness` is read from the scan service by the MCP surface."""

    def __init__(self, **fields: Any) -> None:
        self.fields = fields
        self.scan_calls = 0

    async def liveness(self, session_id: str, *, agent_run_id: str = "") -> dict[str, Any]:
        return {
            "session_id": session_id,
            "agent_run_id": agent_run_id or None,
            "session_live": True,
            "scanning": False,
            "run_decided": True,
            "run_enabled": True,
            "global_enabled": True,
            "project_enabled": True,
            "auto_enable": False,
            "last_scan_at": 123.0,
            "skip_reason": None,
            "closest_gate": None,
            **self.fields,
        }

    async def scan_now(self, *_args: Any, **_kwargs: Any) -> None:  # pragma: no cover
        self.scan_calls += 1


def service(
    caller: Any,
    *others: Any,
    store: Any = None,
    enabled: frozenset[str] = ALL_ENABLED,
    history: Any = None,
    scan_service: Any = None,
    tier0: Any = None,
) -> McpService:
    return McpService(
        manager_for(caller, *others),
        history or HistoryStub(),
        automation_store=store,
        projects=projects(),
        # `_memory_scope` (which `scan_search` shares) refuses when the memory
        # substrate is absent, so a present-but-inert stand-in is what a wired
        # daemon looks like here.
        tier0=tier0 if tier0 is not None else SimpleNamespace(),
        automation_gate=gate(enabled),
        scan_timeline_service=scan_service or ScanServiceStub(),
    )


def caller_session(**kw: Any) -> Any:
    return live_session("s1", token="tok", project_id="p1", scope_id="scope-1", **kw)


def target_session(**kw: Any) -> Any:
    # `record()` derives agent_run_id from the session id, so the target run is
    # "s2" and the seeded records use the same id.
    return live_session("s2", token="tok2", project_id="p1", scope_id="scope-1", **kw)


# ------------------------------------------------------------- the projection


def test_projection_drops_the_heavy_fields_and_keeps_the_trust_signals() -> None:
    projected = project_record(
        {"id": "r1", "agent_run_id": "run-2", "t0": 1.0, "t1": 2.0, "trigger": "tool_result"}
        | record(repairs=["behavior repeated a label"], messages_seen=1)
    )
    for dropped in (
        "evidence_refs",
        "tier0_fact_ids",
        "prompt_hash",
        "prompt_version",
        "observer_model",
        "coverage",
        "target",
    ):
        assert dropped not in projected
    # The two fields that say how much a label is worth.
    assert projected["repaired_fields"] == ["behavior"]
    assert projected["messages_seen"] == 1
    # `target` becomes a count plus a bounded sample: it is the single largest
    # field in a stored record.
    assert projected["target_count"] == 1
    assert projected["targets"] == ["src/swe_mux/server.py"]


def test_projection_bounds_the_target_sample() -> None:
    paths = [f"src/mod_{index}.py" for index in range(12)]
    projected = project_record(record(targets=paths), max_targets=3)
    assert projected["target_count"] == 12
    assert len(projected["targets"]) == 3


def test_absent_approach_status_stays_absent() -> None:
    # A record whose window was too narrow to support a run-level verdict must
    # not read as the model having considered the question and answered
    # "unknown". Absence and uncertainty are different claims.
    withheld = project_record(record(approach_status=None))
    assert "approach_status" not in withheld
    asserted = project_record(record(approach_status="unknown"))
    assert asserted["approach_status"] == "unknown"


def test_window_truncation_is_reported() -> None:
    assert project_record(record(truncated=True))["window_truncated"] is True
    assert "window_truncated" not in project_record(record(truncated=False))


@pytest.mark.parametrize(
    ("repairs", "expected"),
    [
        (["behavior repeated a label"], ["behavior"]),
        (["work_phase was not one of its allowed values"], ["work_phase"]),
        (["filled missing fields: claim,summary"], ["claim", "summary"]),
        (["dropped unknown fields: mood"], ["mood"]),
        (["summary was truncated to 600 characters"], ["summary"]),
        (["something entirely unexpected"], ["other"]),
        ([], []),
        (None, []),
    ],
)
def test_repairs_are_classified_by_field(repairs: Any, expected: list[str]) -> None:
    # The raw list cries wolf - most repairs are a cosmetic `behavior` dedup -
    # so a reader needs to know *which* field was coerced before distrusting a
    # label. An unrecognized repair is reported, never dropped.
    assert repaired_fields(repairs) == expected


# ------------------------------------------------------------- digest bounds


def test_digest_is_bounded_and_says_what_it_dropped() -> None:
    # Alternating phases make one segment per record.
    records = [
        {
            "id": f"r{index}",
            "t0": float(index),
            "t1": float(index + 1),
            "work_phase": "debug" if index % 2 else "implementation",
            "summary": "x" * 900,
            "claim": f"claim {index}",
            "blocked_on": "none",
        }
        for index in range(40)
    ]
    digest = catch_me_up(records, "run-2")
    assert len(digest["progress"]) == DIGEST_MAX_SEGMENTS
    assert len(digest["claims"]) == DIGEST_MAX_CLAIMS
    assert digest["phase_segments"] == 40
    assert digest["phase_segments_omitted"] == 40 - DIGEST_MAX_SEGMENTS
    assert digest["claims_omitted"] == 40 - DIGEST_MAX_CLAIMS
    # The most recent segments are what a "is this run healthy" read needs.
    assert "claim 39" in digest["claims"][-1]
    assert all(len(line) < 2000 for line in digest["progress"])


@pytest.mark.parametrize("segments", [1, 5, 6, 7, 8, 9, 15, 40])
def test_digest_never_drops_a_segment_it_does_not_count(segments: int) -> None:
    # The regression the isolated-daemon run found: `items[len(items) - keep:]`
    # slices a *negative* index whenever the list is shorter than the bound but
    # longer than the shortfall, so six segments under a bound of eight came back
    # as two - beside `phase_segments_omitted: 0`. Both the far-larger and
    # far-smaller cases are correct by clamping, which is why a test at only
    # those sizes passed. Every size in the middle is the actual contract.
    records = [
        {
            "id": f"r{index}",
            "t0": float(index),
            "t1": float(index + 1),
            "work_phase": "debug" if index % 2 else "implementation",
            "summary": f"step {index}",
            "claim": f"claim {index}",
            "blocked_on": "none",
        }
        for index in range(segments)
    ]
    digest = catch_me_up(records, "run-2")
    assert digest["phase_segments"] == segments
    assert len(digest["progress"]) == min(segments, DIGEST_MAX_SEGMENTS)
    # The counters are derived from what is actually carried, so they cannot
    # disagree with it however the bounds are later changed.
    assert digest["phase_segments_omitted"] == segments - len(digest["progress"])
    assert digest["phases_omitted"] == segments - len(digest["phases"])
    assert digest["claims_omitted"] == segments - len(digest["claims"])
    # Whatever survived is the most recent end of the run.
    assert f"step {segments - 1}" in digest["progress"][-1]


def test_digest_keeps_the_whole_run_when_it_fits() -> None:
    records = [
        {"id": "r0", "t0": 0.0, "t1": 1.0, "work_phase": "test", "summary": "ran tests"}
    ]
    digest = catch_me_up(records, "run-2")
    assert digest["phase_segments_omitted"] == 0
    assert digest["claims_omitted"] == 0
    assert digest["progress"] == ["**test**: ran tests"]


# --------------------------------------------------------------- store filters


@pytest.mark.asyncio
async def test_since_cursor_is_exclusive_and_filters_run_in_sql(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=5)
    rows = await store.scan_records(session_id="s2")
    assert len(rows) == 5
    # Exclusive, so feeding back the newest t1 seen never repeats the boundary.
    newest = max(float(row["t1"]) for row in rows)
    assert await store.scan_records(session_id="s2", since_t1=newest) == []
    tail = await store.scan_records(session_id="s2", since_t1=103.0)
    assert [float(row["t1"]) for row in tail] == [104.0, 105.0]
    store.close()


@pytest.mark.asyncio
async def test_semantic_filters_bound_returned_rows_not_scanned_rows(
    tmp_path: Any,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=6, body=record(blocked_on="none"), start=100.0)
    await seed(
        store,
        count=3,
        body=record(blocked_on="tool_error", work_phase="debug"),
        start=200.0,
    )
    # A limit smaller than the unfiltered set: if the filter ran in Python after
    # the read, this would come back short of a full page and a caller could not
    # tell that from the end of the run.
    blocked = await store.scan_records(session_id="s2", blocked_only=True, limit=2)
    assert len(blocked) == 2
    assert all(row["blocked_on"] == "tool_error" for row in blocked)
    assert len(await store.scan_records(session_id="s2", blocked_only=True)) == 3
    assert len(await store.scan_records(session_id="s2", work_phase="debug")) == 3
    assert len(await store.scan_records(session_id="s2", approach_status="active")) == 9
    store.close()


@pytest.mark.asyncio
async def test_newest_first_returns_the_newest_page(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=10)
    # The default ordering is oldest-first because the derivations require it;
    # a bounded read has to be able to ask for the other end, or `limit` would
    # silently truncate the *newest* records away.
    oldest = await store.scan_records(session_id="s2", limit=3)
    newest = await store.scan_records(session_id="s2", limit=3, newest_first=True)
    assert [row["t1"] for row in oldest] == [101.0, 102.0, 103.0]
    assert [row["t1"] for row in newest] == [110.0, 109.0, 108.0]
    store.close()


@pytest.mark.asyncio
async def test_trigger_filters_use_the_stored_vocabulary(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=3, trigger="tool_result", start=100.0)
    # `heartbeat` never reaches the event bus, so it is absent from
    # SCAN_TRIGGERS and present in the store - excluding it must still work.
    await seed(store, count=2, trigger="heartbeat", start=200.0)
    assert len(await store.scan_records(session_id="s2")) == 5
    kept = await store.scan_records(session_id="s2", exclude_triggers=["heartbeat"])
    assert len(kept) == 3
    assert all(row["trigger"] == "tool_result" for row in kept)
    store.close()


@pytest.mark.asyncio
async def test_target_fragment_matches_a_path(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=2, body=record(targets=["src/swe_mux/mcp.py"]), start=100.0)
    await seed(store, count=2, body=record(targets=["frontend/src/App.tsx"]), start=200.0)
    hits = await store.scan_records(session_id="s2", target_fragment="mcp.py")
    assert len(hits) == 2
    assert await store.scan_records(session_id="s2", target_fragment="nothing") == []
    store.close()


# ----------------------------------------------------------------- enablement


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", SCAN_TOOLS)
async def test_disabled_is_typed_not_a_fake_empty(tmp_path: Any, tool: str) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store)
    caller = caller_session()
    svc = service(caller, target_session(), store=store, enabled=frozenset())
    with pytest.raises(QueueError) as excinfo:
        await getattr(svc, tool)(caller, {"session_id": "s2", "query": "work"})
    assert excinfo.value.code == "disabled"
    store.close()


@pytest.mark.asyncio
async def test_scan_timeline_gates_on_the_target_projects_opt_in(tmp_path: Any) -> None:
    # Session-scoped: the gate that matters is the Project of the session being
    # read, which is what the roots this stub distinguishes stand for.
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store)

    async def per_root(root: str) -> frozenset[str]:
        return ALL_ENABLED if root == "D:/work" else frozenset()

    caller = caller_session()
    svc = McpService(
        manager_for(caller, target_session()),
        HistoryStub(),
        automation_store=store,
        projects=SimpleNamespace(
            projects={"p1": SimpleNamespace(id="p1", name="Work", root="D:/other")}
        ),
        tier0=SimpleNamespace(),
        automation_gate=per_root,
        scan_timeline_service=ScanServiceStub(),
    )
    with pytest.raises(QueueError) as excinfo:
        await svc.scan_timeline(caller, {"session_id": "s2"})
    assert excinfo.value.code == "disabled"
    assert "Work" in str(excinfo.value)
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", SCAN_TOOLS)
async def test_unsupported_when_the_substrate_is_absent(tool: str) -> None:
    # A minimally wired daemon answers an explicit typed refusal, never a fake
    # empty an agent would read as "nothing here".
    caller = caller_session()
    svc = service(caller, target_session(), store=None)
    with pytest.raises(QueueError) as excinfo:
        await getattr(svc, tool)(caller, {"session_id": "s2", "query": "work"})
    assert excinfo.value.code == "unsupported"


# ----------------------------------------------------------------- the tool


@pytest.mark.asyncio
async def test_digest_is_the_default_and_carries_the_liveness_block(
    tmp_path: Any,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=3)
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    result = await svc.scan_timeline(caller, {"session_id": "s2"})
    assert result["detail"] == "digest"
    assert result["digest"]["agent_run_id"] == "s2"
    assert result["digest"]["record_count"] == 3
    # Every result states whether scanning is on and why it stopped, because a
    # budget-stopped scanner and a quiet session both return an empty tail.
    for field in ("scanning", "last_scan_at", "skip_reason", "run_decided"):
        assert field in result["scan_state"]
    store.close()


@pytest.mark.asyncio
async def test_a_stopped_scanner_is_not_readable_as_a_quiet_session(
    tmp_path: Any,
) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    caller = caller_session()
    stopped = ScanServiceStub(
        scanning=False, skip_reason="the daily scan budget is spent", run_enabled=True
    )
    svc = service(caller, target_session(), store=store, scan_service=stopped)
    result = await svc.scan_timeline(caller, {"session_id": "s2", "detail": "records"})
    assert result["records"] == []
    assert result["scan_state"]["skip_reason"] == "the daily scan budget is spent"
    store.close()


@pytest.mark.asyncio
async def test_records_page_is_bounded_and_cursors_forward(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=10)
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    page = await svc.scan_timeline(
        caller, {"session_id": "s2", "detail": "records", "limit": 4}
    )
    assert len(page["records"]) == 4
    assert page["page_is_full"] is True
    # Newest-first by default, so a bounded page is the recent end of the run.
    assert [item["t1"] for item in page["records"]] == [110.0, 109.0, 108.0, 107.0]
    assert page["next_since_t1"] == 110.0
    tail = await svc.scan_timeline(
        caller,
        {"session_id": "s2", "detail": "records", "since_t1": page["next_since_t1"]},
    )
    assert tail["records"] == []
    assert tail["page_is_full"] is False
    store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("work_phase", "vibes"),
        ("approach_status", "nope"),
        ("detail", "everything"),
    ],
)
async def test_an_out_of_range_argument_is_refused_not_answered_empty(
    tmp_path: Any, field: str, value: str
) -> None:
    # Found on the isolated daemon: a typo'd filter answered with an empty page,
    # which reads exactly like "no records are in that phase". The inputSchema
    # declares these enums, but a server that trusts the client to enforce them
    # has the same silent-empty failure the rest of this surface refuses.
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=3)
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    with pytest.raises(ValueError) as excinfo:
        await svc.scan_timeline(
            caller, {"session_id": "s2", "detail": "records", field: value}
        )
    assert field in str(excinfo.value)
    # The valid form of the same filter still answers.
    ok = await svc.scan_timeline(
        caller, {"session_id": "s2", "detail": "records", "work_phase": "implementation"}
    )
    assert len(ok["records"]) == 3
    store.close()


@pytest.mark.asyncio
async def test_records_projection_omits_the_heavy_fields(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=1, body=record(repairs=["behavior repeated a label"]))
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    page = await svc.scan_timeline(caller, {"session_id": "s2", "detail": "records"})
    row = page["records"][0]
    assert "evidence_refs" not in row and "tier0_fact_ids" not in row
    assert row["repaired_fields"] == ["behavior"]
    assert row["messages_seen"] == 4


@pytest.mark.asyncio
async def test_full_detail_requires_ids_and_is_bounded(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    ids = await seed(store, count=8)
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    with pytest.raises(ValueError):
        await svc.scan_timeline(caller, {"session_id": "s2", "detail": "full"})
    with pytest.raises(ValueError):
        await svc.scan_timeline(
            caller, {"session_id": "s2", "detail": "full", "record_ids": ids}
        )
    result = await svc.scan_timeline(
        caller, {"session_id": "s2", "detail": "full", "record_ids": ids[:2]}
    )
    assert len(result["records"]) == 2
    # Full means the whole stored record, hashes and evidence included.
    assert result["records"][0]["evidence_refs"]
    store.close()


@pytest.mark.asyncio
async def test_full_detail_cannot_reach_another_sessions_record(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, session_id="s2", run_id="s2", count=1)
    other = await seed(store, session_id="s9", run_id="run-9", count=1, start=500.0)
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    result = await svc.scan_timeline(
        caller, {"session_id": "s2", "detail": "full", "record_ids": other}
    )
    # A record id borrowed from elsewhere reads as absent, not as a way past the
    # opt-in that was checked for s2.
    assert result["records"] == []
    store.close()


@pytest.mark.asyncio
async def test_ended_session_is_readable(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, session_id="s-old", run_id="run-old", count=2)
    history = HistoryStub(
        [
            {
                "id": "run-old",
                "note_id": "s-old",
                "name": "finished",
                "project_id": "p1",
                "project_scope_id": "scope-1",
                "agent_visible": 1,
                "agent_run_seq": 1,
            }
        ]
    )
    caller = caller_session()
    svc = service(caller, store=store, history=history)
    result = await svc.scan_timeline(
        caller, {"session_id": "run-old", "detail": "records"}
    )
    assert result["session_id"] == "s-old"
    assert len(result["records"]) == 2
    store.close()


@pytest.mark.asyncio
async def test_no_scan_trigger_is_reachable(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    scan_service = ScanServiceStub()
    caller = caller_session()
    svc = service(caller, target_session(), store=store, scan_service=scan_service)
    await svc.scan_timeline(caller, {"session_id": "s2", "detail": "records"})
    # Reads cost nothing; a scan spends the human's gated budget, so the tool
    # surface has no path to one. No dispatchable tool names a scan or backfill.
    assert scan_service.scan_calls == 0
    names = {tool["name"] for tool in TOOLS}
    assert not {name for name in names if "backfill" in name or name.endswith("_scan")}
    store.close()


# --------------------------------------------------------------- scan_search


@pytest.mark.asyncio
async def test_scan_search_matches_distilled_records(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(
        store,
        count=2,
        body=record(summary="repaired the CRLF line ending drift", intent="fix crlf"),
        start=100.0,
    )
    await seed(
        store,
        count=2,
        body=record(summary="wired the settings panel", intent="ui work"),
        start=200.0,
    )
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    result = await svc.scan_search(caller, {"query": "crlf"})
    assert result["query"] == "crlf"
    assert len(result["results"]) == 2
    assert all("crlf" in hit["snippet"].casefold() for hit in result["results"])
    # All terms must match, so a two-word query narrows rather than widens.
    narrowed = await svc.scan_search(caller, {"query": "crlf settings"})
    assert narrowed["results"] == []
    store.close()


@pytest.mark.asyncio
async def test_scan_search_requires_a_query(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    with pytest.raises(ValueError):
        await svc.scan_search(caller, {"query": "   "})
    store.close()


@pytest.mark.asyncio
async def test_scan_search_hits_name_their_run_and_window(tmp_path: Any) -> None:
    store = AutomationStore(tmp_path / "mux.db")
    await seed(store, count=1, body=record(summary="touched the parser"))
    caller = caller_session()
    svc = service(caller, target_session(), store=store)
    hit = (await svc.scan_search(caller, {"query": "parser"}))["results"][0]
    # The composition the tool descriptions promise: a hit carries the run and
    # the exact window, which is what search_history's run_ids +
    # message_after/message_before need.
    for field in ("agent_run_id", "t0", "t1", "run"):
        assert field in hit
    store.close()


# ------------------------------------------------------------------ contract


def test_the_two_tools_are_declared_read_only() -> None:
    by_name = {tool["name"]: tool for tool in TOOLS}
    for name in SCAN_TOOLS:
        assert name in READ_TOOL_NAMES
        assert name not in WRITE_TOOL_NAMES
        assert by_name[name]["inputSchema"]["additionalProperties"] is False
