"""Phase 7.9: the per-session change-map endpoint.

Red seeds are this session's own edited source files; yellow is their blast
radius; the view is bounded server-side and excludes concurrent other-session
edits by construction (the non-unified view reads one run's facts).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux.code_graph import CodeGraphStore, index_project, parsing_available
from swe_mux.server import session_change_map
from swe_mux.tier0_store import Tier0Store

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


def _project(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    (tmp_path / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n)\n"
    )


def _request(
    tmp_path: Path, *, graph: Any, tier0: Any, enabled: frozenset[str], query: dict
) -> Any:
    record = SimpleNamespace(
        id="s1",
        agent_run_id="r1",
        project_id="p1",
        project_root=str(tmp_path),
        git=SimpleNamespace(head="deadbeef"),
    )
    session = SimpleNamespace(record=record)

    async def gate(_root: str) -> frozenset[str]:
        return enabled

    app = {
        "sessions": SimpleNamespace(resolve=lambda _sid: session, sessions={"s1": session}),
        "code_graph": graph,
        "tier0": tier0,
        "automation_gate": gate,
    }
    return SimpleNamespace(app=app, match_info={"sid": "s1"}, query=query)


async def _body(response: Any) -> dict:
    return json.loads(response.text)


async def test_change_map_seeds_and_blast(tmp_path: Path) -> None:
    _project(tmp_path)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(tmp_path))
        await tier0.record_fact(
            session_id="s1",
            agent_run_id="r1",
            project_id="p1",
            kind="file_write_result",
            target="pkg/helper.py",
            content_hash="h1",
            created_at=time.time() - 5,
        )
        request = _request(
            tmp_path, graph=graph, tier0=tier0, enabled=frozenset({"code_graph"}), query={}
        )
        body = await _body(await session_change_map(request))
        assert body["available"] is True
        assert body["baseline_head"] == "deadbeef"
        roles = {n["path"]: n["role"] for n in body["nodes"]}
        assert roles["pkg/helper.py"] == "seed"
        assert roles.get("app.py") == "blast"
        assert body["edges"]
        assert "lower bound" in body["lower_bound_note"].lower()
    finally:
        graph.close()
        tier0.close()


async def test_change_map_disabled(tmp_path: Path) -> None:
    _project(tmp_path)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        request = _request(
            tmp_path, graph=graph, tier0=tier0, enabled=frozenset(), query={}
        )
        body = await _body(await session_change_map(request))
        assert body["available"] is False
        assert body["disabled_reason"] == "automation_disabled"
    finally:
        graph.close()
        tier0.close()


async def test_change_map_unsupported_without_graph(tmp_path: Path) -> None:
    _project(tmp_path)
    tier0 = Tier0Store(tmp_path / "mux.db")
    try:
        request = _request(
            tmp_path, graph=None, tier0=tier0, enabled=frozenset({"code_graph"}), query={}
        )
        body = await _body(await session_change_map(request))
        assert body["available"] is False
        assert body["disabled_reason"] == "unsupported"
    finally:
        tier0.close()


async def test_change_map_empty_when_no_edits(tmp_path: Path) -> None:
    _project(tmp_path)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(tmp_path))
        request = _request(
            tmp_path, graph=graph, tier0=tier0, enabled=frozenset({"code_graph"}), query={}
        )
        body = await _body(await session_change_map(request))
        assert body["available"] is True
        assert body["nodes"] == []
        assert body["empty_reason"] == "no_edits"
    finally:
        graph.close()
        tier0.close()
