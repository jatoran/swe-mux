"""Phase 7.5: mux MCP cross-session memory reads.

What these pin is the contract the roadmap states, not the wording: every result
traces to a specific fact/record and names the agent run it came from; a result
from the caller's own superseded run is labelled rather than blended into the
present; a low-confidence or weak match is withheld in preference to being
returned; and the per-Project enablement gate answers `disabled`/`unsupported`
explicitly rather than a fake empty. The surface stays read-only.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.mcp import TOOLS, McpService
from swe_mux.mcp_contract import READ_TOOL_NAMES
from swe_mux.prompt_queue import QueueError
from tests.test_mcp import HistoryStub, live_session, manager_for

MEMORY_TOOLS = (
    "provenance",
    "verified_status",
    "prior_resolutions",
    "dead_ends",
    "doc_debt",
)
ALL_ENABLED = frozenset(
    {
        "provenance_graph",
        "declared_vs_verified",
        "dead_end_memory",
        "prior_resolutions",
        "doc_debt",
    }
)


def _normalize(value: str) -> str:
    value = re.sub(r"[A-Fa-f0-9]{8,}", "#", value.casefold())
    value = re.sub(r"\d+", "#", value)
    return " ".join(value.split())


class Tier0Stub:
    def __init__(
        self,
        *,
        project_facts: dict[str, list[dict[str, Any]]] | None = None,
        run_facts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._project = project_facts or {}
        self._run = run_facts or {}

    async def facts_for_project(
        self, project_id: str, *, since: float | None = None, limit: int = 2000
    ) -> list[dict[str, Any]]:
        return list(self._project.get(project_id, []))

    async def facts_for_run(
        self,
        agent_run_id: str,
        *,
        since: float | None = None,
        until: float | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        return list(self._run.get(agent_run_id, []))


class MemoryStoreStub:
    """Stands in for AutomationStore: experiences + scan records + titles."""

    def __init__(
        self,
        *,
        experiences: list[dict[str, Any]] | None = None,
        scan: dict[str, list[dict[str, Any]]] | None = None,
        titles: dict[str, str] | None = None,
    ) -> None:
        self._experiences = experiences or []
        self._scan = scan or {}
        self.titles = titles or {}

    async def annotations(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {"agent_run_id": run_id, "content": title}
            for run_id, title in self.titles.items()
        ]

    async def checkpoint(self, _key: str) -> dict[str, Any] | None:
        return None

    async def experiences(
        self,
        *,
        query: str = "",
        project_scope_id: str | None = None,
        error: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self._experiences:
            if error is not None and _normalize(error) != _normalize(
                str(row.get("error_summary") or "")
            ):
                continue
            if project_scope_id and row.get("project_scope_id") != project_scope_id:
                continue
            rows.append(row)
        return rows[:limit]

    async def scan_records(
        self,
        *,
        session_id: str | None = None,
        agent_run_id: str | None = None,
        project_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return list(self._scan.get(project_id or "", []))[:limit]


def _projects(root: str = "D:/work") -> Any:
    return SimpleNamespace(
        projects={"p1": SimpleNamespace(id="p1", name="Work", root=root)}
    )


def _gate(enabled: frozenset[str] = ALL_ENABLED) -> Any:
    async def gate(_root: str) -> frozenset[str]:
        return enabled

    return gate


def _fact(
    fid: str,
    *,
    kind: str,
    target: str,
    session_id: str,
    run: str,
    content_hash: str | None = None,
    created_at: float = 1.0,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import json

    return {
        "id": fid,
        "session_id": session_id,
        "agent_run_id": run,
        "project_id": "p1",
        "kind": kind,
        "target": target,
        "content_hash": content_hash,
        "created_at": created_at,
        "detail_json": json.dumps(detail or {}),
    }


def _service(
    caller: Any,
    *others: Any,
    tier0: Any = None,
    store: Any = None,
    gate: Any = None,
    history: Any = None,
    projects: Any = None,
) -> McpService:
    return McpService(
        manager_for(caller, *others),
        history or HistoryStub(),
        automation_store=store or MemoryStoreStub(),
        projects=projects or _projects(),
        tier0=tier0,
        automation_gate=gate or _gate(),
    )


def _caller(**kw: Any) -> Any:
    return live_session("s1", token="tok", project_id="p1", scope_id="scope-1", **kw)


# ---------------------------------------------------------------- enablement


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", MEMORY_TOOLS)
async def test_unsupported_when_substrate_absent(tool: str) -> None:
    # tier0 unset (a minimally wired daemon): an explicit typed refusal, never a
    # fake empty an agent would read as "nothing here".
    service = _service(_caller(), tier0=None)
    args = {"file": "x", "claim": "fixed x", "error": "boom", "subsystem": "x"}
    with pytest.raises(QueueError) as excinfo:
        await getattr(service, tool)(_caller(), args)
    assert excinfo.value.code == "unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", MEMORY_TOOLS)
async def test_disabled_when_automation_not_opted_in(tool: str) -> None:
    caller = _caller()
    service = _service(caller, tier0=Tier0Stub(), gate=_gate(frozenset()))
    args = {"file": "x", "claim": "fixed x", "error": "boom", "subsystem": "x"}
    with pytest.raises(QueueError) as excinfo:
        await getattr(service, tool)(caller, args)
    assert excinfo.value.code == "disabled"
    assert excinfo.value.payload.get("automation")


# ---------------------------------------------------------------- provenance


@pytest.mark.asyncio
async def test_provenance_returns_attributed_cross_session_edge() -> None:
    caller = _caller()
    facts = [
        _fact(
            "w1",
            kind="file_write",
            target="src/auth.py",
            session_id="s2",
            run="sibling-run",
            content_hash="abcdef123456",
            created_at=1.0,
        ),
        _fact(
            "r1",
            kind="file_read",
            target="src/auth.py",
            session_id="s1",
            run="s1",
            created_at=2.0,
        ),
    ]
    service = _service(caller, tier0=Tier0Stub(project_facts={"p1": facts}))
    result = await service.provenance(caller, {"file": "src/auth.py"})
    assert len(result["cross_session_edges"]) == 1
    edge = result["cross_session_edges"][0]
    assert edge["content_hash"] == "abcdef123456"
    # The writer was a sibling; the reader is the caller's own current run.
    assert edge["writer"]["run_relation"] == "sibling_run"
    assert edge["writer"]["agent_run_id"] == "sibling-run"
    assert edge["reader"]["run_relation"] == "your_current_run"
    # And the raw touches trace to the same facts.
    assert {t["action"] for t in result["touches"]} == {"write", "read"}


@pytest.mark.asyncio
async def test_provenance_reads_result_kind_facts() -> None:
    # Real capture puts the path and hash on the `_result` variants: the bare
    # file_write/file_read facts carry the intent and a null target. The tool must
    # read the `_result` kinds or it finds nothing on live data (caught 2026-08-16).
    caller = _caller()
    facts = [
        _fact("wi", kind="file_write", target=None, session_id="s2", run="rb",
              created_at=1.0),
        _fact("wr", kind="file_write_result", target="calc.py", session_id="s2",
              run="rb", content_hash="47946e285e", created_at=1.1),
        _fact("ri", kind="file_read", target=None, session_id="s1", run="s1",
              created_at=2.0),
        _fact("rr", kind="file_read_result", target="calc.py", session_id="s1",
              run="s1", content_hash="47946e285e", created_at=2.1),
    ]
    service = _service(caller, tier0=Tier0Stub(project_facts={"p1": facts}))
    result = await service.provenance(caller, {"file": "calc.py"})
    assert {t["action"] for t in result["touches"]} == {"write", "read"}
    write = next(t for t in result["touches"] if t["action"] == "write")
    assert write["content_hash"] == "47946e285e"
    assert write["run"]["run_relation"] == "sibling_run"
    # And the cross-session edge is still built from the same result facts.
    assert len(result["cross_session_edges"]) == 1


@pytest.mark.asyncio
async def test_provenance_labels_the_callers_own_earlier_run() -> None:
    # A write from the caller's own superseded run must be labelled, not blended:
    # after a /clear the agent has no memory of that run's work.
    caller = _caller()
    history = HistoryStub(
        rows=[{"note_id": "s1", "id": "old-run", "agent_run_id": "old-run"}]
    )
    facts = [
        _fact(
            "w1",
            kind="file_write",
            target="src/auth.py",
            session_id="s1",
            run="old-run",
            content_hash="deadbeef0000",
            created_at=1.0,
        ),
        _fact(
            "r1",
            kind="file_read",
            target="src/auth.py",
            session_id="s2",
            run="sibling-run",
            created_at=2.0,
        ),
    ]
    service = _service(
        caller, tier0=Tier0Stub(project_facts={"p1": facts}), history=history
    )
    result = await service.provenance(caller, {"file": "src/auth.py"})
    write_touch = next(t for t in result["touches"] if t["action"] == "write")
    assert write_touch["run"]["run_relation"] == "your_earlier_run"
    assert write_touch["run"]["superseded"] is True


@pytest.mark.asyncio
async def test_provenance_withholds_ambiguous_edges() -> None:
    caller = _caller()
    facts = [
        _fact("w1", kind="file_write", target="f.py", session_id="s2", run="rb",
              content_hash="h1", created_at=1.0),
        _fact("w2", kind="file_write", target="f.py", session_id="s3", run="rc",
              content_hash="h2", created_at=2.0),
        _fact("r1", kind="file_read", target="f.py", session_id="s1", run="s1",
              created_at=3.0),
    ]
    # w1 (foreign) then w2 by the reader's own session, then the read: the latest
    # write is not the latest foreign write, so which write the reader saw is no
    # longer a fact.
    facts = [
        _fact("w1", kind="file_write", target="f.py", session_id="s2", run="rb",
              content_hash="h1", created_at=1.0),
        _fact("w2", kind="file_write", target="f.py", session_id="s1", run="s1",
              content_hash="h2", created_at=2.0),
        _fact("r1", kind="file_read", target="f.py", session_id="s1", run="s1",
              created_at=3.0),
    ]
    service = _service(caller, tier0=Tier0Stub(project_facts={"p1": facts}))
    result = await service.provenance(caller, {"file": "f.py"})
    # The reader saw one of two writes; which is no longer a fact, so the edge is
    # withheld and only counted.
    assert result["cross_session_edges"] == []
    assert result["ambiguous_suppressed"] == 1


@pytest.mark.asyncio
async def test_provenance_reports_tests_that_ran_on_the_file() -> None:
    caller = _caller()
    facts = [
        _fact("w1", kind="file_write", target="src/auth.py", session_id="s2",
              run="rb", content_hash="h1", created_at=1.0),
        _fact("t1", kind="test_result", target="tests/test_auth.py", session_id="s2",
              run="rb", created_at=2.0, detail={"test_outcome": "passed"}),
    ]
    service = _service(caller, tier0=Tier0Stub(project_facts={"p1": facts}))
    result = await service.provenance(caller, {"file": "src/auth.py"})
    assert len(result["tests"]) == 1
    assert result["tests"][0]["outcome"] == "passed"


# ------------------------------------------------------------ verified_status


@pytest.mark.asyncio
async def test_verified_status_flags_declared_but_untested() -> None:
    caller = _caller()
    # No test facts in the run: the claim is declared, nothing verified.
    service = _service(caller, tier0=Tier0Stub(run_facts={"s1": []}))
    result = await service.verified_status(
        caller, {"claim": "I fixed the auth bug"}
    )
    assert result["declared"] is True
    assert result["tests_ran"] is False
    assert result["verified"] is False
    assert "tests not run" in result["status"]
    assert result["checked"]["run"]["run_relation"] == "your_current_run"


@pytest.mark.asyncio
async def test_verified_status_reports_verified_when_tests_passed() -> None:
    caller = _caller()
    facts = [
        _fact("t1", kind="test_result", target="tests/test_auth.py", session_id="s1",
              run="s1", created_at=2.0,
              detail={"test_outcome": {"failed": 0, "errors": 0}}),
    ]
    service = _service(caller, tier0=Tier0Stub(run_facts={"s1": facts}))
    result = await service.verified_status(caller, {"claim": "it is fixed"})
    assert result["verified"] is True
    assert result["tests_passed"] is True


@pytest.mark.asyncio
async def test_verified_status_detects_no_claim() -> None:
    caller = _caller()
    service = _service(caller, tier0=Tier0Stub(run_facts={"s1": []}))
    result = await service.verified_status(
        caller, {"claim": "looking into the parser now"}
    )
    assert result["declared"] is False
    assert "no done/fixed/works claim" in result["status"]


# ---------------------------------------------------------- prior_resolutions


@pytest.mark.asyncio
async def test_prior_resolutions_matches_exact_signature_only() -> None:
    caller = _caller()
    rows = [
        {
            "id": "e1",
            "project_scope_id": "scope-1",
            "backend": "claude",
            "error_summary": "ConnectionError: timed out after 30s",
            "resolution_summary": "raised the pool timeout to 60s",
            "source_run_id": "sibling-run",
            "confidence": 0.9,
            "created_at": 5.0,
        }
    ]
    service = _service(
        caller, tier0=Tier0Stub(), store=MemoryStoreStub(experiences=rows)
    )
    # Same signature up to digits (normalized): a hit.
    hit = await service.prior_resolutions(
        caller, {"error": "ConnectionError: timed out after 45s"}
    )
    assert len(hit["resolutions"]) == 1
    assert hit["resolutions"][0]["source_run"]["run_relation"] == "sibling_run"
    # A genuinely different error: empty over a weak match.
    miss = await service.prior_resolutions(
        caller, {"error": "KeyError: missing config key"}
    )
    assert miss["resolutions"] == []


@pytest.mark.asyncio
async def test_prior_resolutions_withholds_low_confidence() -> None:
    caller = _caller()
    rows = [
        {
            "id": "e1",
            "project_scope_id": "scope-1",
            "backend": "claude",
            "error_summary": "boom",
            "resolution_summary": "guessed",
            "source_run_id": "r",
            "confidence": 0.2,
            "created_at": 5.0,
        }
    ]
    service = _service(
        caller, tier0=Tier0Stub(), store=MemoryStoreStub(experiences=rows)
    )
    result = await service.prior_resolutions(caller, {"error": "boom"})
    assert result["resolutions"] == []
    assert result["low_confidence_suppressed"] == 1


# ------------------------------------------------------------------ dead_ends


@pytest.mark.asyncio
async def test_dead_ends_returns_abandoned_records_attributed() -> None:
    caller = _caller()
    scan = {
        "p1": [
            {
                "agent_run_id": "sibling-run",
                "approach_status": "abandoned",
                "dead_end": "tried a mutex here; the supervisor already owns the lock",
                "intent": "fix the race",
                "summary": "abandoned the mutex approach",
                "target": ["src/supervisor.py"],
                "confidence": 0.8,
                "t1": 9.0,
            },
            {
                "agent_run_id": "sibling-run",
                "approach_status": "active",
                "dead_end": "",
                "target": ["src/other.py"],
                "confidence": 0.9,
                "t1": 10.0,
            },
        ]
    }
    service = _service(
        caller, tier0=Tier0Stub(), store=MemoryStoreStub(scan=scan)
    )
    result = await service.dead_ends(caller, {})
    assert len(result["dead_ends"]) == 1
    item = result["dead_ends"][0]
    assert item["approach_status"] == "abandoned"
    assert item["run"]["run_relation"] == "sibling_run"


@pytest.mark.asyncio
async def test_dead_ends_subsystem_filter_and_low_confidence() -> None:
    caller = _caller()
    scan = {
        "p1": [
            {
                "agent_run_id": "r",
                "approach_status": "failed",
                "dead_end": "the delivery gate never went safe",
                "intent": "",
                "summary": "",
                "target": ["src/delivery_readiness.py"],
                "confidence": 0.7,
                "t1": 9.0,
            },
            {
                "agent_run_id": "r",
                "approach_status": "abandoned",
                "dead_end": "low-signal note",
                "intent": "",
                "summary": "",
                "target": ["src/scan_timeline.py"],
                "confidence": 0.1,
                "t1": 8.0,
            },
        ]
    }
    service = _service(caller, tier0=Tier0Stub(), store=MemoryStoreStub(scan=scan))
    # Subsystem hint matches the first record's target only.
    result = await service.dead_ends(caller, {"subsystem": "delivery"})
    assert len(result["dead_ends"]) == 1
    assert "delivery" in result["dead_ends"][0]["targets"][0]
    # The scan_timeline record was below threshold; querying it returns empty but
    # counts the suppression.
    other = await service.dead_ends(caller, {"subsystem": "scan_timeline"})
    assert other["dead_ends"] == []
    assert other["low_confidence_suppressed"] == 1


# ------------------------------------------------------------------ doc_debt


def _write_doc(root: Any, rel: str, key_files: list[str]) -> None:
    doc = root / ".docs" / rel
    doc.parent.mkdir(parents=True, exist_ok=True)
    body = "# Doc\n\n## Key files\n\n" + "".join(
        f"- `{path}` — owned here\n" for path in key_files
    )
    doc.write_text(body, encoding="utf-8")


@pytest.mark.asyncio
async def test_doc_debt_maps_each_doc_to_its_changed_files(tmp_path: Any) -> None:
    caller = _caller()
    _write_doc(tmp_path, "design/foo.md", ["src/swe_mux/foo.py"])
    facts = {
        "p1": [
            _fact(
                "f1",
                kind="file_write_result",
                target="src/swe_mux/foo.py",
                session_id="s1",
                run="r1",
            )
        ]
    }
    service = _service(
        caller,
        tier0=Tier0Stub(project_facts=facts),
        projects=_projects(root=str(tmp_path)),
    )
    result = await service.doc_debt(caller, {})
    assert result["docs"] == [
        {
            "doc": "design/foo.md",
            "changed_files": ["src/swe_mux/foo.py"],
            "project_id": "p1",
        }
    ]
    # The blind spot is stated so an empty result is never read as "docs current".
    assert "not proof" in result["note"]


@pytest.mark.asyncio
async def test_doc_debt_empty_when_change_is_undocumented(tmp_path: Any) -> None:
    # A file no doc lists in its Key files owns no doc: the blind spot. Empty, and
    # counted empty, rather than a fabricated pair.
    caller = _caller()
    _write_doc(tmp_path, "design/foo.md", ["src/swe_mux/foo.py"])
    facts = {
        "p1": [
            _fact(
                "f1",
                kind="file_write_result",
                target="src/swe_mux/undocumented.py",
                session_id="s1",
                run="r1",
            )
        ]
    }
    service = _service(
        caller,
        tier0=Tier0Stub(project_facts=facts),
        projects=_projects(root=str(tmp_path)),
    )
    result = await service.doc_debt(caller, {})
    assert result["docs"] == []
    outcomes = service.status()["memory_outcomes"]
    assert outcomes["doc_debt"]["empty"] == 1
    assert outcomes["doc_debt"]["returned"] == 0


@pytest.mark.asyncio
async def test_doc_debt_excludes_a_doc_edited_in_the_same_window(tmp_path: Any) -> None:
    # Debt paid as it was incurred: a doc written in the same window is not owed,
    # matching the detector that writes the annotation.
    caller = _caller()
    _write_doc(tmp_path, "design/foo.md", ["src/swe_mux/foo.py"])
    facts = {
        "p1": [
            _fact(
                "f1",
                kind="file_write_result",
                target="src/swe_mux/foo.py",
                session_id="s1",
                run="r1",
            ),
            _fact(
                "f2",
                kind="file_write_result",
                target=".docs/design/foo.md",
                session_id="s1",
                run="r1",
                created_at=2.0,
            ),
        ]
    }
    service = _service(
        caller,
        tier0=Tier0Stub(project_facts=facts),
        projects=_projects(root=str(tmp_path)),
    )
    result = await service.doc_debt(caller, {})
    assert result["docs"] == []


# ---------------------------------------------------------------- read-only


@pytest.mark.asyncio
async def test_retrieval_outcomes_are_measured() -> None:
    # A tool that only ever returns empty is the defect the measurement surfaces.
    caller = _caller()
    service = _service(caller, tier0=Tier0Stub(), store=MemoryStoreStub())
    await service.prior_resolutions(caller, {"error": "nothing matches this"})
    await service.dead_ends(caller, {})
    outcomes = service.status()["memory_outcomes"]
    assert outcomes["prior_resolutions"]["calls"] == 1
    assert outcomes["prior_resolutions"]["empty"] == 1
    assert outcomes["prior_resolutions"]["returned"] == 0
    assert outcomes["dead_ends"]["empty"] == 1


def test_memory_tools_are_declared_read_only() -> None:
    for tool in TOOLS:
        if tool["name"] in MEMORY_TOOLS:
            assert tool["annotations"]["readOnlyHint"] is True
            assert tool["annotations"]["destructiveHint"] is False
    assert set(MEMORY_TOOLS).issubset(set(READ_TOOL_NAMES))
