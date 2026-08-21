"""Phase 7.9 code-structure graph — engine, resolution, and store."""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.code_graph import (
    CodeGraphStore,
    co_change_net,
    content_hash,
    import_targets,
    index_project,
    maintain_files,
    parse_source,
    parsing_available,
    resolve_edges,
    resolve_import,
    spec_for_path,
)

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)


# --------------------------------------------------------------------------- #
# Parsing.                                                                     #
# --------------------------------------------------------------------------- #


def test_python_symbols_and_qualnames() -> None:
    src = b"""
class Widget:
    def draw(self):
        return self.paint()
    def paint(self):
        return 1

def top():
    return Widget().draw()
"""
    parsed = parse_source("/proj/ui.py", src, project_root="/proj")
    assert parsed is not None
    names = {(s.name, s.kind) for s in parsed.symbols}
    assert ("Widget", "class") in names
    assert ("Widget.draw", "method") in names
    assert ("Widget.paint", "method") in names
    assert ("top", "function") in names
    # A top-level function never names itself as its own enclosing scope.
    assert "top.top" not in {s.name for s in parsed.symbols}


def test_python_call_reference_dedup() -> None:
    # `self.paint()` is one event: it must be one `calls` edge, not a call plus a
    # duplicate `references` twin.
    src = (
        b"class C:\n"
        b"    def a(self):\n"
        b"        return self.b()\n"
        b"    def b(self):\n"
        b"        return 1\n"
    )
    parsed = parse_source("/proj/c.py", src, project_root="/proj")
    assert parsed is not None
    paint_refs = [r for r in parsed.refs if r.name == "b"]
    assert [r.kind for r in paint_refs] == ["calls"]


def test_tsx_parses_and_extracts() -> None:
    src = b"""
import { helper } from "./util";
export function render(n: number) { return helper(n); }
export class Panel { open() { return this.close(); } close() { return 2; } }
"""
    parsed = parse_source("/proj/view.tsx", src, project_root="/proj")
    assert parsed is not None
    assert parsed.lang == "tsx"
    names = {s.name for s in parsed.symbols}
    assert "render" in names
    assert "Panel" in names
    assert "Panel.open" in names
    assert [i.module for i in parsed.imports] == ["./util"]


def test_unsupported_extension_returns_none() -> None:
    assert parse_source("/proj/readme.md", b"# hi", project_root="/proj") is None
    assert spec_for_path("x.md") is None
    assert spec_for_path("x.d.ts") is not None


def test_tsx_and_ts_grammars_distinct() -> None:
    # A .tsx file must use the tsx grammar (JSX) and a .ts file the ts grammar.
    assert spec_for_path("a.tsx").name == "tsx"
    assert spec_for_path("a.ts").name == "typescript"


# --------------------------------------------------------------------------- #
# Import resolution — filesystem, order-independent.                          #
# --------------------------------------------------------------------------- #


def test_python_import_resolution(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make(x):\n    return x\n")
    spec = spec_for_path("app.py")
    assert resolve_import("pkg.helper", "app.py", str(tmp_path), spec=spec) == "pkg/helper.py"
    # A stdlib/third-party module resolves to no edge.
    assert resolve_import("os.path", "app.py", str(tmp_path), spec=spec) is None


def test_python_relative_import(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "sibling.py").write_text("def f():\n    return 1\n")
    spec = spec_for_path("pkg/app.py")
    assert resolve_import(".sibling", "pkg/app.py", str(tmp_path), spec=spec) == "pkg/sibling.py"


def test_ts_relative_and_bare(tmp_path: Path) -> None:
    (tmp_path / "util.ts").write_text("export const helper = (x) => x;")
    spec = spec_for_path("view.tsx")
    assert resolve_import("./util", "view.tsx", str(tmp_path), spec=spec) == "util.ts"
    # A bare specifier is node_modules — no in-project edge.
    assert resolve_import("react", "view.tsx", str(tmp_path), spec=spec) is None


def test_resolution_is_order_independent(tmp_path: Path) -> None:
    (tmp_path / "helper.py").write_text("def make(x):\n    return x\n")
    (tmp_path / "app.py").write_text("from helper import make\ndef run(n):\n    return make(n)\n")
    # Resolve app.py without helper.py ever being parsed: the import still resolves
    # because resolution reads the filesystem, not the parse order.
    parsed = parse_source(str(tmp_path / "app.py"), (tmp_path / "app.py").read_bytes(),
                          project_root=str(tmp_path))
    assert parsed is not None
    assert import_targets(parsed, str(tmp_path)) == ["helper.py"]


def test_import_aware_call_not_a_false_edge(tmp_path: Path) -> None:
    # Two modules define `make`; a call in app.py must resolve to the one it
    # imported, never the unrelated same-named symbol.
    (tmp_path / "real.py").write_text("def make(x):\n    return x\n")
    (tmp_path / "other.py").write_text("def make(y):\n    return y\n")
    (tmp_path / "app.py").write_text("from real import make\ndef run(n):\n    return make(n)\n")
    parsed = parse_source(str(tmp_path / "app.py"), (tmp_path / "app.py").read_bytes(),
                          project_root=str(tmp_path))
    assert parsed is not None
    known = {"real.py": {"make"}, "other.py": {"make"}}
    edges = resolve_edges(parsed, str(tmp_path), known_symbols=known)
    call_edges = [e for e in edges if e.kind == "calls" and e.dst_name == "make"]
    assert len(call_edges) == 1
    assert call_edges[0].dst_path == "real.py"
    assert call_edges[0].resolved is True


# --------------------------------------------------------------------------- #
# Store round-trip + queries.                                                 #
# --------------------------------------------------------------------------- #


async def _seed(tmp_path: Path) -> tuple[CodeGraphStore, str]:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make_thing(x):\n    return x + 1\n")
    (tmp_path / "app.py").write_text(
        "from pkg.helper import make_thing\ndef run(n):\n    return make_thing(n)\n"
    )
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(tmp_path))
    return store, "p1"


async def test_blast_radius_and_callers(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    blast = await store.reverse_dependents(pid, "pkg/helper.py", hops=2)
    assert any(node.path == "app.py" for node in blast)
    callers = await store.callers_of_symbol(pid, "pkg/helper.py", "make_thing")
    assert callers and callers[0]["src_path"] == "app.py"
    assert callers[0]["src_symbol"] == "run"
    store.close()


async def test_definitions_and_imports(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    defs = await store.definitions(pid, "make_thing")
    assert defs and defs[0]["path"] == "pkg/helper.py"
    assert defs[0]["kind"] == "function"
    assert await store.imports_of(pid, "app.py") == ["pkg/helper.py"]
    store.close()


async def test_incremental_replace_is_idempotent(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    before = await store.callers_of_symbol(pid, "pkg/helper.py", "make_thing")
    # Re-maintain the unchanged file: hash matches, nothing re-parsed, no dup edges.
    updated = await maintain_files(store, pid, str(tmp_path), ["app.py"])
    assert updated == 0
    after = await store.callers_of_symbol(pid, "pkg/helper.py", "make_thing")
    assert before == after
    store.close()


async def test_edit_updates_edges(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    # app.py stops calling make_thing.
    (tmp_path / "app.py").write_text("def run(n):\n    return n\n")
    updated = await maintain_files(store, pid, str(tmp_path), ["app.py"])
    assert updated == 1
    callers = await store.callers_of_symbol(pid, "pkg/helper.py", "make_thing")
    assert callers == []
    store.close()


async def test_duplicate_symbol_names_do_not_abort_index(tmp_path: Path) -> None:
    # A file that defines the same qualname more than once — @overload stubs, a
    # TYPE_CHECKING branch, a property/setter pair — must not raise a UNIQUE
    # violation that aborts the whole index. (Regression: the seed once died on the
    # first such file and left the backend graph empty in production.)
    (tmp_path / "over.py").write_text(
        "from typing import overload\n"
        "@overload\n"
        "def f(x: int) -> int: ...\n"
        "@overload\n"
        "def f(x: str) -> str: ...\n"
        "def f(x):\n    return x\n"
    )
    (tmp_path / "plain.py").write_text("def g():\n    return 1\n")
    store = CodeGraphStore(tmp_path / "graph.db")
    count = await index_project(store, "p1", str(tmp_path))
    assert count == 2  # both files indexed; the overload file did not abort the pass
    syms = await store.symbols_in("p1", "over.py")
    assert [s["name"] for s in syms] == ["f"]  # deduped to one row
    assert await store.definitions("p1", "g")  # the second file survived
    store.close()


async def test_deleted_file_removed_from_graph(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    (tmp_path / "app.py").unlink()
    await maintain_files(store, pid, str(tmp_path), ["app.py"])
    assert await store.imports_of(pid, "app.py") == []
    callers = await store.callers_of_symbol(pid, "pkg/helper.py", "make_thing")
    assert callers == []
    store.close()


async def test_orphans_and_god_nodes(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    orphans = await store.orphans(pid)
    orphan_paths = {o["path"] for o in orphans}
    # app.py is called by nothing; helper.py is called by app -> not an orphan.
    assert "app.py" in orphan_paths
    assert "pkg/helper.py" not in orphan_paths
    store.close()


async def test_orphans_exclude_paths_the_graph_could_never_link(tmp_path: Path) -> None:
    # Every one of the 76 `dead-code` findings in a measured 24-hour window
    # (2026-08-21) was a path the graph could not have drawn an inbound edge to:
    # 51 agent scratchpad scripts outside the Project root, 11 hashed bundle
    # assets, 14 test modules and Playwright harnesses. Their volume also starved
    # the accurate structural rules, because the per-pass budget is spent in path
    # order.
    from swe_mux.code_graph import is_dead_code_candidate

    for path in (
        "c:/users/j/appdata/local/temp/claude/run/scratchpad/probe.py",
        "/tmp/scratch/probe.py",
        "src/swe_mux/static/assets/gitdiffview-bqv1fpqq.js",
        "src/swe_mux/static/sw.js",
        ".claude/worktrees/other/src/swe_mux/session.py",
        "tests/test_mcp_scan_timeline.py",
        "frontend/test/renderer/rail-dropup.spec.ts",
        "frontend/test/renderer/paneharness.tsx",
        "src/pkg/helper.test.ts",
        "tests/conftest.py",
    ):
        assert is_dead_code_candidate(path) is False, path
    # Ordinary project source is still a candidate.
    for path in ("src/swe_mux/session.py", "frontend/src/app.tsx", "pkg/helper.py"):
        assert is_dead_code_candidate(path) is True, path


async def test_orphans_query_applies_the_admission_rule(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    # A test module in the graph is discovered by a runner, never imported: "no
    # inbound edge" is how it is supposed to look, so it is not a candidate.
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
    await maintain_files(store, pid, str(tmp_path), ["tests/test_app.py"])
    orphan_paths = {o["path"] for o in await store.orphans(pid)}
    assert "app.py" in orphan_paths
    assert "tests/test_app.py" not in orphan_paths
    store.close()


async def test_subgraph_bounded(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    sub = await store.subgraph(pid, ["pkg/helper.py"], hops=2)
    roles = {n["path"]: n["role"] for n in sub["nodes"]}
    assert roles["pkg/helper.py"] == "seed"
    assert roles.get("app.py") == "blast"
    assert sub["edges"]
    store.close()


async def test_subgraph_marks_a_seed_the_index_has_never_seen(tmp_path: Path) -> None:
    """A file that exists only on a branch has no node in the canonical graph.

    It is still drawn — it is the file the reader is most likely thinking about —
    but its empty neighbourhood means "not indexed here", not "nothing depends on
    it", and only the flag can tell those apart.
    """
    store, pid = await _seed(tmp_path)
    sub = await store.subgraph(pid, ["pkg/helper.py", "pkg/brandnew.py"], hops=1)
    by_path = {n["path"]: n for n in sub["nodes"]}
    assert "indexed" not in by_path["pkg/helper.py"]
    assert by_path["pkg/brandnew.py"]["indexed"] is False
    assert by_path["pkg/brandnew.py"]["role"] == "seed"
    assert await store.known_files(pid, ["pkg/helper.py", "pkg/brandnew.py"]) == {"pkg/helper.py"}
    assert await store.known_files(pid, []) == set()
    store.close()


def test_is_indexable_path_excludes_worktrees_and_generated() -> None:
    from swe_mux.code_graph import is_indexable_path

    assert is_indexable_path("src/swe_mux/server.py")
    assert is_indexable_path("frontend/src/api.ts")
    # A nested worktree copy, a dependency, and generated output are not part of
    # the canonical tree the graph indexes.
    assert not is_indexable_path(".claude/worktrees/x/frontend/src/api.ts")
    assert not is_indexable_path("frontend/node_modules/pkg/index.js")
    assert not is_indexable_path("dist/bundle.js")
    assert not is_indexable_path("readme.md")  # unsupported extension


def test_is_project_relative_rejects_both_absolute_spellings() -> None:
    """Host-independent on purpose.

    A fact recorded on Windows carries ``c:/…`` and one recorded on Linux carries
    ``/home/…``; a POSIX daemon reading the first (or a Windows one reading the
    second) must still recognise it as outside the checkout. ``os.path.isabs``
    answers only for the host it runs on, which is why this does not use it.
    """
    from swe_mux.code_graph import is_project_relative

    assert is_project_relative("src/swe_mux/server.py")
    assert is_project_relative("app.py")
    assert not is_project_relative("c:/users/dev/appdata/local/temp/scratch/probe.py")
    assert not is_project_relative("/home/dev/scratch/probe.py")
    assert not is_project_relative("c:\\users\\dev\\probe.py")
    # Identities arrive casefolded, but a raw path from any other caller must not
    # read as relative just because its drive letter is capitalised.
    assert not is_project_relative("C:/Users/dev/probe.py")
    assert not is_project_relative("../sibling/app.py")
    assert not is_project_relative("")


def test_resolve_display_paths_recovers_casing(tmp_path: Path) -> None:
    """The identity is casefolded; the filesystem path is not.

    Matching is by directory listing rather than by ``stat`` because on Windows a
    wrong-cased ``stat`` succeeds — a fast path there would keep the wrong casing
    and silently defeat the whole point.
    """
    from swe_mux.code_graph import resolve_display_paths

    (tmp_path / "Frontend" / "src").mkdir(parents=True)
    (tmp_path / "Frontend" / "src" / "ChangeMapPane.tsx").write_text("export {}\n")
    (tmp_path / "app.py").write_text("x = 1\n")

    resolved = resolve_display_paths(
        tmp_path.as_posix(),
        [
            "frontend/src/changemappane.tsx",
            "app.py",
            "frontend/src/gone.tsx",
            "c:/elsewhere/probe.py",
            # A directory, not a file: resolving it would hand the client a path
            # the file endpoint then rejects.
            "frontend/src",
        ],
    )
    assert resolved == {
        "frontend/src/changemappane.tsx": "Frontend/src/ChangeMapPane.tsx",
        "app.py": "app.py",
    }


async def test_maintain_skips_worktree_copies(tmp_path: Path) -> None:
    # A write under a nested worktree must not leak a copy of the repo into the
    # Project graph (the bug that ballooned the live edge table).
    (tmp_path / ".claude" / "worktrees" / "x").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "x" / "app.py").write_text("def f():\n    return 1\n")
    store = CodeGraphStore(tmp_path / "graph.db")
    await maintain_files(store, "p1", str(tmp_path), [".claude/worktrees/x/app.py"])
    assert await store.file_count("p1") == 0
    store.close()


async def test_edges_dedupe_call_sites(tmp_path: Path) -> None:
    # A caller that calls the same target several times is one relationship, not one
    # edge per call site — otherwise the reverse-dependency walk explodes on a real
    # repo (measured 14x edge bloat, a 3-hop hub walk going from 35ms to ~58s).
    (tmp_path / "helper.py").write_text("def make(x):\n    return x\n")
    (tmp_path / "app.py").write_text(
        "from helper import make\n"
        "def run(n):\n"
        "    return make(make(make(n)))\n"  # three call sites, one relationship
    )
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(tmp_path))
    calls = await store.callers_of_symbol("p1", "helper.py", "make")
    assert len([c for c in calls if c["src_path"] == "app.py"]) == 1
    store.close()


async def test_subgraph_caps_and_reports_truncation(tmp_path: Path, monkeypatch) -> None:
    import swe_mux.code_graph as cg

    # helper.py is imported by six callers; cap the map at 3 nodes and confirm the
    # seed plus two nearest dependents ship, with the drop reported honestly.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "helper.py").write_text("def make(x):\n    return x\n")
    for i in range(6):
        (tmp_path / f"c{i}.py").write_text(
            f"from pkg.helper import make\ndef f{i}():\n    return make(1)\n"
        )
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(tmp_path))
    monkeypatch.setattr(cg, "MAX_MAP_NODES", 3)
    sub = await store.subgraph("p1", ["pkg/helper.py"], hops=1)
    assert len(sub["nodes"]) == 3  # 1 seed + 2 blast
    assert sub["truncated"] is True
    assert sub["totals"]["blast"] == 6  # all six counted even though capped
    assert sub["totals"]["shown"] == 3
    store.close()


async def test_import_cycles(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import b\ndef fa():\n    return b.fb()\n")
    (tmp_path / "b.py").write_text("import a\ndef fb():\n    return a.fa()\n")
    store = CodeGraphStore(tmp_path / "g.db")
    await index_project(store, "p1", str(tmp_path))
    cycles = await store.import_cycles("p1")
    assert any({"a.py", "b.py"} == set(cycle) for cycle in cycles)
    store.close()


# --------------------------------------------------------------------------- #
# Co-change net (git recall safety net).                                       #
# --------------------------------------------------------------------------- #


def test_co_change_net_groups_by_commit() -> None:
    rows = [
        {"commit_oid": "c1", "contributed_paths": ["src/a.py", "src/b.py"]},
        {"commit_oid": "c1", "contributed_paths": ["src/a.py"]},  # same commit, other session
        {"commit_oid": "c2", "contributed_paths": ["src/a.py", "src/c.py"]},
        {"commit_oid": "c3", "contributed_paths": ["src/x.py", "src/y.py"]},  # no a.py
    ]
    net = co_change_net(rows, "src/a.py")
    result = dict(net)
    assert result.get("src/b.py") == 1
    assert result.get("src/c.py") == 1
    assert "src/x.py" not in result
    assert "src/a.py" not in result


def test_content_hash_stable() -> None:
    assert content_hash(b"abc") == content_hash(b"abc")
    assert content_hash(b"abc") != content_hash(b"abd")
