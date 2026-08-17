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


async def test_subgraph_bounded(tmp_path: Path) -> None:
    store, pid = await _seed(tmp_path)
    sub = await store.subgraph(pid, ["pkg/helper.py"], hops=2)
    roles = {n["path"]: n["role"] for n in sub["nodes"]}
    assert roles["pkg/helper.py"] == "seed"
    assert roles.get("app.py") == "blast"
    assert sub["edges"]
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
