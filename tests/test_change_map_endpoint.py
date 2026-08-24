"""Phase 7.9: the per-session change-map endpoint.

Red seeds are edited source files; yellow is their blast radius; the view is
bounded server-side. Three scopes answer "what changed" at three different
lifetimes — this run's facts, this branch's delta, every session's edits — and a
worktree session defaults to the branch, because a worktree exists to hold one.

The worktree tests build **real** git worktrees rather than faking a root. The
whole class of bug they exist to catch is a checkout being mistaken for another,
and a stubbed `git worktree list` proves nothing about that.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from swe_mux import app_keys as keys
from swe_mux.code_graph import CodeGraphStore, index_project, parsing_available
from swe_mux.routes import scan_timeline as scan_timeline_routes
from swe_mux.routes.scan_timeline import session_change_map
from swe_mux.tier0_store import Tier0Store

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


@pytest.fixture(autouse=True)
def _fresh_worktree_membership() -> Any:
    """Worktree membership is TTL-cached per (project root, checkout).

    Temp paths differ per test so a stale hit is not possible, but the cache is
    process-global and a test that asserts on a *miss* would otherwise depend on
    ordering.
    """
    scan_timeline_routes._worktree_membership.clear()
    yield
    scan_timeline_routes._worktree_membership.clear()


def _project(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    (tmp_path / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n)\n"
    )


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path) -> Path:
    """A real repository holding the sample project, committed on `main`."""
    _project(root)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test User")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "initial")
    return root


def _worktree(repository: Path, path: Path, branch: str) -> Path:
    """A real linked worktree of `repository`, on its own branch."""
    _git(repository, "worktree", "add", "-b", branch, str(path))
    return path


def _git_state(root: Path, *, worktree: str | None, compare_ref: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        head="deadbeef",
        root=str(root),
        worktree=worktree,
        branch=f"worktree-{worktree}" if worktree else "main",
        compare_ref=compare_ref,
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
    worktree: str | None = None,
    other_worktrees: dict[str, str] | None = None,
    compare_ref: str | None = None,
    history: Any = None,
) -> Any:
    """A change-map request for session ``s1``.

    ``session_root`` is the checkout the requesting session is *working in* — the
    live git root, which for a worktree session is not the Project root. ``others``
    names sibling sessions and their own checkouts, which is what the project view
    has to re-anchor against.
    """
    record = SimpleNamespace(
        id="s1",
        agent_run_id="r1",
        project_id="p1",
        project_root=str(project_root or tmp_path),
        git=_git_state(session_root or tmp_path, worktree=worktree, compare_ref=compare_ref),
    )
    session = SimpleNamespace(record=record)
    sessions: dict[str, Any] = {"s1": session}
    for sid, root in (others or {}).items():
        sessions[sid] = SimpleNamespace(
            record=SimpleNamespace(
                id=sid,
                agent_run_id=f"run-{sid}",
                project_id="p1",
                project_root=str(project_root or tmp_path),
                name=sid,
                git=_git_state(
                    root,
                    worktree=(other_worktrees or {}).get(sid),
                    compare_ref=compare_ref,
                ),
            )
        )

    async def gate(_root: str) -> frozenset[str]:
        return enabled

    app = {
        keys.SESSIONS: SimpleNamespace(resolve=lambda _sid: session, sessions=sessions),
        keys.PROJECTS: SimpleNamespace(
            projects={"p1": SimpleNamespace(root=str(project_root or tmp_path))}
        ),
        keys.CODE_GRAPH: graph,
        keys.TIER0: tier0,
        keys.HISTORY: history,
        keys.AUTOMATION_GATE: gate,
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


async def test_a_worktree_sessions_writes_land_on_the_project_graph(tmp_path: Path) -> None:
    """The bug that made a whole worktree session read as unmappable.

    Its writes are absolute paths under `.claude/worktrees/<name>/…`. Normalized
    against the *Project* root — which is where the Project was registered, not
    where the agent is working — every one of them keeps that prefix, trips the
    hidden-directory rule, and is refused. Normalized against the checkout the
    session is actually in, they are ordinary repository-relative paths that join
    the canonical graph, and the payload names the worktree so the pane opens the
    worktree's copy rather than the primary checkout's.
    """
    repo = _repository(tmp_path / "repo")
    worktree = _worktree(repo, repo / ".claude" / "worktrees" / "wt", "worktree-wt")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        await _write_fact(tier0, str(worktree / "pkg" / "helper.py"))
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={"scope": "session"},
            session_root=worktree,
            project_root=repo,
            worktree="wt",
        )
        body = await _body(await session_change_map(request))
        assert body["excluded"] == {"outside_root": 0, "unindexable": 0}
        roles = {n["path"]: n["role"] for n in body["nodes"]}
        assert roles["pkg/helper.py"] == "seed"
        # It joined the real graph, so the blast radius is real too.
        assert roles.get("app.py") == "blast"
        # Git's own spelling of the worktree root, which is what the Project file
        # endpoint validates against — not this test's `Path` rendering of it.
        assert Path(body["worktree"]).resolve() == worktree.resolve()
        assert body["checkout"]["worktree"] == "wt"
        seed = next(n for n in body["nodes"] if n["path"] == "pkg/helper.py")
        assert seed["display_path"] == "pkg/helper.py"
    finally:
        graph.close()
        tier0.close()


async def test_a_nested_repository_is_not_re_anchored(tmp_path: Path) -> None:
    """Two roots differing does not make them the same repository.

    A vendored or sub-project checkout inside a Project reports its own git root
    with no worktree name. Re-anchoring its paths onto this Project's identities
    would join two unrelated trees, so it keeps the Project root and its writes
    land where they honestly are: outside it.
    """
    repo = _repository(tmp_path / "repo")
    nested = _repository(repo / "vendor" / "nested")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        await _write_fact(tier0, str(nested / "app.py"))
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={"scope": "session"},
            session_root=nested,
            project_root=repo,
            worktree=None,
        )
        body = await _body(await session_change_map(request))
        # `vendor/nested/app.py` is inside the Project directory but is another
        # repository's file: it is refused as unindexable rather than silently
        # merged into this Project's `app.py`.
        assert body["nodes"] == []
        assert body["worktree"] is None
        assert sum(body["excluded"].values()) == 1
    finally:
        graph.close()
        tier0.close()


async def test_a_worktree_session_defaults_to_its_branch(tmp_path: Path) -> None:
    """A worktree exists to hold a branch, so that is what its map describes.

    The branch delta is also the only source immune to both fact expiries: this
    seeds from a commit made with no write facts recorded at all, which is exactly
    the state a session is in hours after its work landed.
    """
    repo = _repository(tmp_path / "repo")
    worktree = _worktree(repo, repo / ".claude" / "worktrees" / "wt", "worktree-wt")
    (worktree / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 2\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "committed on the branch")
    # Uncommitted, and untracked: a diff cannot see the second one at all.
    (worktree / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n) + 1\n"
    )
    (worktree / "pkg" / "brandnew.py").write_text("def fresh():\n    return 3\n")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={},
            session_root=worktree,
            project_root=repo,
            worktree="wt",
            compare_ref="main",
        )
        body = await _body(await session_change_map(request))
        assert body["scope"] == "branch"
        assert body["scopes"] == ["session", "branch", "project"]
        assert body["checkout"]["ref"] == "main"
        assert body["checkout"]["base"]
        seeds = {n["path"] for n in body["nodes"] if n["role"] == "seed"}
        assert seeds == {"pkg/helper.py", "app.py", "pkg/brandnew.py"}
        # Checkout-scoped, so no session is claimed to have written it.
        assert all(not n.get("sessions") for n in body["nodes"] if n["role"] == "seed")
    finally:
        graph.close()
        tier0.close()


async def test_a_branch_only_file_is_drawn_and_marked_unindexed(tmp_path: Path) -> None:
    """A file that exists only on the branch has no node in the canonical graph.

    Drawing it is right — it is the file you are most likely thinking about — but
    reading its empty neighbourhood as "nothing depends on this" would be wrong,
    so it says which one it is.
    """
    repo = _repository(tmp_path / "repo")
    worktree = _worktree(repo, repo / ".claude" / "worktrees" / "wt", "worktree-wt")
    (worktree / "pkg" / "brandnew.py").write_text("def fresh():\n    return 3\n")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={},
            session_root=worktree,
            project_root=repo,
            worktree="wt",
            compare_ref="main",
        )
        body = await _body(await session_change_map(request))
        by_path = {n["path"]: n for n in body["nodes"]}
        assert by_path["pkg/brandnew.py"]["indexed"] is False
        assert by_path["pkg/brandnew.py"]["display_path"] == "pkg/brandnew.py"
    finally:
        graph.close()
        tier0.close()


async def test_a_branch_request_without_a_base_falls_back_rather_than_blanking(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path / "repo")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        await _write_fact(tier0, "pkg/helper.py")
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={"scope": "branch"},
            session_root=repo,
            project_root=repo,
            compare_ref=None,
        )
        body = await _body(await session_change_map(request))
        # An empty branch map would read as "this branch changed nothing", which is
        # a claim, not an absence.
        assert body["scope"] == "session"
        assert body["scopes"] == ["session", "project"]
        assert {n["path"] for n in body["nodes"] if n["role"] == "seed"} == {"pkg/helper.py"}
    finally:
        graph.close()
        tier0.close()


async def test_landed_work_survives_the_fact_window(tmp_path: Path) -> None:
    """Merging a branch does not erase what a session did; the fact window does.

    Tier 0 write facts expire on a six-hour window *and* on a conversation
    rollover, so a session reads as having edited nothing hours after its work
    landed. The git provenance ledger does not expire.
    """
    repo = _repository(tmp_path / "repo")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)

    class _History:
        async def git_provenance(self, **kwargs: Any) -> list[dict[str, Any]]:
            assert kwargs["session_id"] == "s1"
            return [
                {"session_id": "s1", "contributed_paths": ["pkg/helper.py", "readme.md"]},
                {"session_id": "s1", "contributed_paths": []},
            ]

    try:
        await index_project(graph, "p1", str(repo))
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            enabled=frozenset({"code_graph"}),
            query={"scope": "session"},
            session_root=repo,
            project_root=repo,
            history=_History(),
        )
        body = await _body(await session_change_map(request))
        # No write facts at all, and still a map — from commits alone.
        seeds = {n["path"] for n in body["nodes"] if n["role"] == "seed"}
        assert seeds == {"pkg/helper.py"}
        assert next(n for n in body["nodes"] if n["path"] == "pkg/helper.py")["sessions"] == ["s1"]
    finally:
        graph.close()
        tier0.close()


async def test_project_scope_reanchors_a_sibling_worktrees_absolute_writes(
    tmp_path: Path,
) -> None:
    """The project view spans sessions, and sessions do not share a checkout.

    A sibling worktree's writes are absolute paths under *its* root, which the
    requesting session's root cannot strip — so without re-anchoring against each
    contributing session's own checkout, every one of them reads as outside-root
    and the whole session disappears from the unified map.
    """
    repo = _repository(tmp_path / "repo")
    sibling = _worktree(repo, repo / ".claude" / "worktrees" / "sibling", "worktree-sibling")
    database = tmp_path / "mux.db"
    tier0 = Tier0Store(database)
    graph = CodeGraphStore(database)
    try:
        await index_project(graph, "p1", str(repo))
        await _write_fact(tier0, "app.py")
        await _write_fact(tier0, str(sibling / "pkg" / "helper.py"), session_id="s2", run="r2")
        request = _request(
            tmp_path,
            graph=graph,
            tier0=tier0,
            # The legacy alias still resolves to the project scope.
            query={"unify": "true"},
            enabled=frozenset({"code_graph"}),
            session_root=repo,
            project_root=repo,
            others={"s2": sibling},
            other_worktrees={"s2": "sibling"},
        )
        body = await _body(await session_change_map(request))
        assert body["scope"] == "project"
        seeds = {n["path"]: n for n in body["nodes"] if n["role"] == "seed"}
        assert set(seeds) == {"app.py", "pkg/helper.py"}
        assert seeds["pkg/helper.py"]["sessions"] == ["s2"]
        assert body["excluded"] == {"outside_root": 0, "unindexable": 0}
        assert {item["id"] for item in body["sessions"]} == {"s1", "s2"}
    finally:
        graph.close()
        tier0.close()
