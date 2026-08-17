"""Phase 7.9: the human-passive code-graph detectors on turn boundaries.

The graph is maintained off the same turn_ended stream the other detectors ride,
then three findings are emitted as annotations (never a PTY write): a blast-radius
notice for a wide-reach edit, the mux-unique "callers edited but not examined"
signal, and the project-scoped structural findings (dead code / god node / cycle).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from swe_mux.automation_store import AutomationStore
from swe_mux.code_graph import CodeGraphStore, parsing_available
from swe_mux.tier0_store import Tier0Store

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


class _Events:
    async def emit(self, kind: str, **payload: Any) -> None:
        pass


def _make_project(root: Path, callers: int) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    for i in range(callers):
        (root / f"caller{i}.py").write_text(
            f"from pkg.helper import make_thing\ndef c{i}(n):\n    return make_thing(n)\n"
        )


async def _run_detectors(
    tmp_path: Path, enabled: frozenset[str], *, callers: int = 6
) -> list[dict[str, Any]]:
    from swe_mux.deterministic_consumers import ConsumerContext, DeterministicConsumerService

    _make_project(tmp_path, callers)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    store = AutomationStore(database)
    graph = CodeGraphStore(database)

    async def context(_session_id: str) -> ConsumerContext:
        return ConsumerContext(
            project_id="p1",
            project_root=str(tmp_path),
            agent_run_id="r1",
            enabled=enabled,
        )

    try:
        await tier0.record_fact(
            session_id="s1",
            agent_run_id="r1",
            project_id="p1",
            kind="file_write_result",
            target="pkg/helper.py",
            content_hash="h1",
            created_at=time.time() - 5,
        )
        service = DeterministicConsumerService(
            tier0,
            store,
            type("Sessions", (), {"sessions": {}})(),
            _Events(),
            resolve_context=context,
            code_graph=graph,
        )
        return await service.evaluate("s1")
    finally:
        graph.close()
        store.close()
        tier0.close()


async def test_blast_radius_and_unexamined_callers_fire(tmp_path: Path) -> None:
    written = await _run_detectors(tmp_path, frozenset({"code_graph"}), callers=6)
    tags = {item["tag"] for item in written}
    assert "blast-radius" in tags
    # The six caller files were never read this run, so they are all unexamined.
    assert "unexamined-callers" in tags


async def test_no_findings_when_reach_below_threshold(tmp_path: Path) -> None:
    # Two callers is below BLAST_MIN_REACH, so no blast-radius notice, though the
    # unexamined-callers signal still fires for callers not opened.
    written = await _run_detectors(tmp_path, frozenset({"code_graph"}), callers=2)
    tags = {item["tag"] for item in written}
    assert "blast-radius" not in tags


async def test_disabled_when_code_graph_not_opted_in(tmp_path: Path) -> None:
    written = await _run_detectors(tmp_path, frozenset({"provenance_graph"}), callers=6)
    assert [item for item in written if item["tag"] in {"blast-radius", "unexamined-callers"}] == []


def test_doc_debt_dependency_reach_refinement() -> None:
    # A doc owns caller.py but not helper.py. Editing helper.py (which caller.py
    # depends on) dirties the doc only when the dependency reach is supplied.
    from swe_mux.deterministic_consumers import detect_doc_debt

    ownership = {"caller.py": ("design/caller.md",)}
    facts = [
        {"kind": "file_write_result", "target": "helper.py", "id": "f1", "created_at": 1.0}
    ]
    # Without reach: helper.py owns no doc, so no debt.
    assert detect_doc_debt(facts, ownership) is None
    # With reach: caller.py depends on helper.py, so its doc owes an update.
    finding = detect_doc_debt(facts, ownership, dependents={"helper.py": ("caller.py",)})
    assert finding is not None
    assert "design/caller.md" in finding.dirty


async def test_reevaluation_is_idempotent(tmp_path: Path) -> None:
    from swe_mux.deterministic_consumers import ConsumerContext, DeterministicConsumerService

    _make_project(tmp_path, 6)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    store = AutomationStore(database)
    graph = CodeGraphStore(database)

    async def context(_session_id: str) -> ConsumerContext:
        return ConsumerContext(
            project_id="p1",
            project_root=str(tmp_path),
            agent_run_id="r1",
            enabled=frozenset({"code_graph"}),
        )

    try:
        await tier0.record_fact(
            session_id="s1",
            agent_run_id="r1",
            project_id="p1",
            kind="file_write_result",
            target="pkg/helper.py",
            content_hash="h1",
            created_at=time.time() - 5,
        )
        service = DeterministicConsumerService(
            tier0,
            store,
            type("Sessions", (), {"sessions": {}})(),
            _Events(),
            resolve_context=context,
            code_graph=graph,
        )
        first = await service.evaluate("s1")
        assert first
        # Same window, next turn boundary: every finding dedupes to a no-op.
        assert await service.evaluate("s1") == []
    finally:
        graph.close()
        store.close()
        tier0.close()
