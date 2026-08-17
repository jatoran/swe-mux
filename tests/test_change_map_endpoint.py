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
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    (tmp_path / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n)\n"
    )


def _request(
    tmp_path: Path,
    *,
    graph: Any,
    tier0: Any,
    enabled: frozenset[str],
    query: dict,
    session_root: Path | None = None,
    project_root: Path | None = None,
    others: dict[str, Path] | None = None,
) -> Any:
    """A change-map request for session ``s1``.

    ``session_root`` is the checkout the requesting session runs in (a worktree
    when it differs from ``project_root``); ``others`` names sibling sessions and
    their own checkouts, which is what the unified view has to re-anchor against.
    """
    record = SimpleNamespace(
        id="s1",
        agent_run_id="r1",
        project_id="p1",
        project_root=str(session_root or tmp_path),
        git=SimpleNamespace(head="deadbeef"),
    )
    session = SimpleNamespace(record=record)
    sessions: dict[str, Any] = {"s1": session}
    for sid, root in (others or {}).items():
        sessions[sid] = SimpleNamespace(
            record=SimpleNamespace(
                id=sid,
                agent_run_id=f"run-{sid}",
                project_id="p1",
                project_root=str(root),
                name=sid,
                git=SimpleNamespace(head="deadbeef"),
            )
        )

    async def gate(_root: str) -> frozenset[str]:
        return enabled

    app = {
        "sessions": SimpleNamespace(resolve=lambda _sid: session, sessions=sessions),
        "projects": SimpleNamespace(
            projects={"p1": SimpleNamespace(root=str(project_root or tmp_path))}
        ),
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
        assert body["excluded"] == {"outside_root": 0, "unindexable": 0}
        assert body["worktree"] is None
    finally:
        graph.close()
        tier0.close()


async def _write_fact(tier0: Any, target: str, *, session_id: str = "s1", run: str = "r1") -> None:
    await tier0.record_fact(
        session_id=session_id,
        agent_run_id=run,
        project_id="p1",
        kind="file_write_result",
        target=target,
        content_hash=f"h-{target}",
        created_at=time.time() - 5,
    )


async def test_change_map_drops_edits_outside_the_checkout(tmp_path: Path) -> None:
    """A scratchpad script is a source file the graph can never index.

    It sits outside the Project tree, so it can never acquire an edge, never show a
    blast radius, and can never be opened from the pane. Drawing it as a red seed is
    what put temp-directory scripts on the map as permanently isolated dots.
    """
    repo = tmp_path / "repo"
    _project(repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "probe.py").write_text("print(1)\n")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        # Twice, to prove the count is of distinct files rather than of writes.
        await _write_fact(tier0, str(scratch / "probe.py"))
        await _write_fact(tier0, str(scratch / "probe.py"))
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={},
            session_root=repo,
            project_root=repo,
        )
        body = await _body(await session_change_map(request))
        assert body["nodes"] == []
        assert body["excluded"] == {"outside_root": 1, "unindexable": 0}
        # Not "no_edits": the session wrote source, and the map is saying it cannot
        # draw it, which is a different thing to tell the reader.
        assert body["empty_reason"] == "excluded"
    finally:
        graph.close()
        tier0.close()


async def test_change_map_drops_edits_the_indexer_refuses(tmp_path: Path) -> None:
    """The endpoint applies the graph's own admission rule, not a weaker one.

    A write under a generated or hidden directory is removed from the graph by
    ``maintain_files``; a map that still seeds it promises links that can never come.
    """
    _project(tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "bundle.py").write_text("x = 1\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "hook.py").write_text("y = 2\n")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(tmp_path))
        await _write_fact(tier0, "dist/bundle.py")
        await _write_fact(tier0, ".claude/hook.py")
        await _write_fact(tier0, "pkg/helper.py")
        request = _request(
            tmp_path, graph=graph, tier0=tier0, enabled=frozenset({"code_graph"}), query={}
        )
        body = await _body(await session_change_map(request))
        assert {n["path"] for n in body["nodes"] if n["role"] == "seed"} == {"pkg/helper.py"}
        assert body["excluded"] == {"outside_root": 0, "unindexable": 2}
    finally:
        graph.close()
        tier0.close()


async def test_change_map_carries_true_cased_paths(tmp_path: Path) -> None:
    """`path` is a casefolded identity and is not openable; `display_path` is.

    A case-sensitive host cannot open `frontend/src/changemappane.tsx` at all, and a
    case-insensitive one opens it under a second, colliding pane identity.
    """
    _project(tmp_path)
    (tmp_path / "pkg" / "MixedCase.py").write_text("def thing():\n    return 2\n")
    (tmp_path / "Consumer.py").write_text(
        "from pkg.MixedCase import thing\ndef go():\n    return thing()\n"
    )
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(tmp_path))
        await _write_fact(tier0, "pkg/MixedCase.py")
        request = _request(
            tmp_path, graph=graph, tier0=tier0, enabled=frozenset({"code_graph"}), query={}
        )
        body = await _body(await session_change_map(request))
        by_path = {n["path"]: n for n in body["nodes"]}
        assert by_path["pkg/mixedcase.py"]["display_path"] == "pkg/MixedCase.py"
        # Not seeds only: opening a blast-radius file is the "what might I have
        # broken" move the map exists to support.
        assert by_path["consumer.py"]["role"] == "blast"
        assert by_path["consumer.py"]["display_path"] == "Consumer.py"
    finally:
        graph.close()
        tier0.close()


async def test_change_map_offers_no_path_for_a_vanished_file(tmp_path: Path) -> None:
    _project(tmp_path)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(tmp_path))
        await _write_fact(tier0, "pkg/helper.py")
        (tmp_path / "pkg" / "helper.py").unlink()
        request = _request(
            tmp_path, graph=graph, tier0=tier0, enabled=frozenset({"code_graph"}), query={}
        )
        body = await _body(await session_change_map(request))
        seed = next(n for n in body["nodes"] if n["path"] == "pkg/helper.py")
        # Still on the map — it is a file this session wrote — but with no button to
        # a dead link.
        assert "display_path" not in seed
    finally:
        graph.close()
        tier0.close()


async def test_change_map_names_the_worktree_its_files_live_in(tmp_path: Path) -> None:
    """A worktree session's files are not in the primary checkout.

    Without this the pane would open the Project root's copy of a file the session
    never touched.
    """
    repo = tmp_path / "repo"
    _project(repo)
    worktree = tmp_path / "wt"
    _project(worktree)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(worktree))
        await _write_fact(tier0, "pkg/helper.py")
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={},
            session_root=worktree,
            project_root=repo,
        )
        body = await _body(await session_change_map(request))
        assert body["worktree"] == str(worktree)
        seed = next(n for n in body["nodes"] if n["path"] == "pkg/helper.py")
        assert seed["display_path"] == "pkg/helper.py"
    finally:
        graph.close()
        tier0.close()


async def test_unify_reanchors_a_sibling_worktrees_absolute_writes(tmp_path: Path) -> None:
    """Unify spans sessions, and sessions do not share a checkout.

    A sibling worktree's writes are recorded as absolute paths under *its* root,
    which the requesting session's root cannot strip — so without re-anchoring
    against the other session's own root every one of them reads as outside-root and
    the whole session disappears from the unified map.
    """
    repo = tmp_path / "repo"
    _project(repo)
    sibling = tmp_path / "sibling"
    _project(sibling)
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        await _write_fact(tier0, "app.py")
        await _write_fact(
            tier0, str(sibling / "pkg" / "helper.py"), session_id="s2", run="r2"
        )
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={"unify": "true"},
            session_root=repo,
            project_root=repo,
            others={"s2": sibling},
        )
        body = await _body(await session_change_map(request))
        seeds = {n["path"]: n for n in body["nodes"] if n["role"] == "seed"}
        assert set(seeds) == {"app.py", "pkg/helper.py"}
        assert seeds["pkg/helper.py"]["sessions"] == ["s2"]
        assert body["excluded"] == {"outside_root": 0, "unindexable": 0}
        assert {item["id"] for item in body["sessions"]} == {"s1", "s2"}
    finally:
        graph.close()
        tier0.close()
