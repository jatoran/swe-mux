"""DAEMON_LOAD_ISOLATION item 22a - the code graph across a daemon restart.

The graph persists in `mux.db`, but the first turn of every process used to clear
it and re-parse the whole tree: 6,000 files, about a minute of the daemon's CPU, at
each restart of a live host whose tree had not changed. `sync_project` replaced
that with a stat-based catch-up, and incremental upkeep now re-links the files it
did not re-parse, so a synced graph is the graph a seed of the same tree builds.

The oracle throughout is a fresh seed of the same tree into a second database:
every row of every table must match, which is what "converges" means here.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import swe_mux.code_graph as cg
from swe_mux.code_graph import (
    CodeGraphStore,
    index_project,
    maintain_files,
    parsing_available,
    sync_project,
)

pytestmark = pytest.mark.skipif(
    not parsing_available(), reason="tree-sitter grammars not available on this host"
)

AGED_SECONDS = 600
MAKE_THING = "def make_thing(x):\n    return x + 1\n"
DEFINES_FRESH = MAKE_THING + "\n\ndef fresh():\n    return 5\n"
CALLS_FRESH = "from pkg.helper import fresh\n\n\ndef stable():\n    return fresh()\n"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _age(root: Path, seconds: int = AGED_SECONDS) -> None:
    """Move every file's mtime into the past, out of the racy window.

    A file written a moment ago has a stamp the sync deliberately refuses to trust
    (`RACY_STAMP_NS`); a real tree's files mostly do not.
    """
    when = time.time() - seconds
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (when, when))


def _project(root: Path) -> None:
    _write(root, "pkg/__init__.py", "")
    _write(
        root,
        "pkg/helper.py",
        "def make_thing(x):\n    return x + 1\n\n\ndef doomed():\n    return 0\n",
    )
    _write(root, "pkg/util/__init__.py", "def shared():\n    return 1\n")
    _write(root, "pkg/gone.py", "def vanish():\n    return 2\n")
    _write(
        root,
        "app.py",
        "from pkg.helper import make_thing, later, doomed\n"
        "import pkg.util\n"
        "import pkg.newmod\n"
        "\n"
        "\n"
        "def run(n):\n"
        "    return later(make_thing(n)) + doomed() + pkg.util.shared() + pkg.newmod.novel()\n",
    )
    _write(root, "gone_user.py", "import pkg.gone\n\n\ndef use():\n    return pkg.gone.vanish()\n")
    _write(root, "body.py", "def stable():\n    return 1\n")
    _write(root, "touched.py", "def same():\n    return 1\n")
    _write(root, "web/util.ts", "export function helper(n: number) { return n; }\n")
    _write(
        root,
        "web/view.ts",
        'import { helper, extra } from "./util";\n'
        'import { thing } from "./lib";\n'
        "export function render(n: number) { return extra(helper(n)) + thing(); }\n",
    )


def _graph(store: CodeGraphStore, project_id: str) -> dict[str, Any]:
    """Every row the graph holds for a Project, minus ids and clocks."""
    db = sqlite3.connect(store.path)
    try:
        files = set(
            db.execute(
                "SELECT path, lang, content_hash, symbol_count, truncated "
                "FROM code_graph_files WHERE project_id=?",
                (project_id,),
            )
        )
        symbols = set(
            db.execute(
                "SELECT path, name, kind, start_line, end_line "
                "FROM code_graph_symbols WHERE project_id=?",
                (project_id,),
            )
        )
        edges = Counter(
            db.execute(
                "SELECT kind, src_path, src_symbol, dst_path, dst_symbol, dst_name, resolved, "
                "src_line FROM code_graph_edges WHERE project_id=?",
                (project_id,),
            )
        )
    finally:
        db.close()
    return {"files": files, "symbols": symbols, "edges": edges}


async def _seeded_graph(root: Path, database: Path) -> dict[str, Any]:
    """The oracle: what a seed of the tree as it stands now builds."""
    store = CodeGraphStore(database)
    try:
        await index_project(store, "p1", str(root))
        return _graph(store, "p1")
    finally:
        store.close()


def _count(monkeypatch: pytest.MonkeyPatch, name: str) -> list[str]:
    """Record each call to a `code_graph` function by its first argument."""
    calls: list[str] = []
    original = getattr(cg, name)

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(str(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(cg, name, counted)
    return calls


async def test_an_unseeded_project_is_seeded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    result = await sync_project(store, "p1", str(root), reason="first_turn")
    assert result.outcome == "seeded"
    assert result.parsed == result.files == 10
    assert _graph(store, "p1") == await _seeded_graph(root, tmp_path / "oracle.db")
    store.close()


async def test_a_sync_over_an_unchanged_tree_reads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case every restart of a quiet host is: a walk and a stat, nothing more."""
    root = tmp_path / "repo"
    _project(root)
    _age(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    before = _graph(store, "p1")

    reads = _count(monkeypatch, "_load")
    result = await sync_project(store, "p1", str(root), reason="first_turn")

    assert (result.outcome, result.files, result.stale, result.parsed) == ("synced", 10, 0, 0)
    assert (result.removed, result.relinked) == (0, 0)
    assert reads == []
    assert _graph(store, "p1") == before
    store.close()


async def test_a_sync_converges_to_the_graph_a_seed_would_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every way a tree changes while no daemon watches, against a fresh seed.

    Each change below is one an unchanged file's edges depend on, which is exactly
    what the per-start re-seed used to heal and what re-linking now has to.
    """
    root = tmp_path / "repo"
    _project(root)
    _age(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    # Preconditions, so the comparison measures the sync and not the fixture.
    assert await store.callers_of_symbol("p1", "pkg/helper.py", "later") == []
    assert sorted(await store.imports_of("p1", "app.py")) == [
        "pkg/helper.py",
        "pkg/util/__init__.py",
    ]

    # A module gains the name an unchanged caller calls, and loses another it calls.
    _write(root, "pkg/helper.py", MAKE_THING + "\n\ndef later(y):\n    return y\n")
    # A module appears that an unchanged file's import names but could not resolve.
    _write(root, "pkg/newmod.py", "def novel():\n    return 3\n")
    # A module appears that outranks the package an unchanged import resolved to.
    _write(root, "pkg/util.py", "def shared():\n    return 4\n")
    # A module an unchanged file imports disappears.
    (root / "pkg" / "gone.py").unlink()
    # A body changes and no name does.
    _write(root, "body.py", "def stable():\n    return 2\n")
    # A file is touched and its bytes are the same.
    when = time.time() - AGED_SECONDS // 2
    os.utime(root / "touched.py", (when, when))
    # The same three shapes in TypeScript: a gained export, and a package index.
    _write(
        root,
        "web/util.ts",
        "export function helper(n: number) { return n; }\n"
        "export function extra(n: number) { return n * 2; }\n",
    )
    _write(root, "web/lib/index.ts", "export function thing() { return 1; }\n")

    parses = _count(monkeypatch, "parse_source")
    result = await sync_project(store, "p1", str(root), reason="first_turn")

    changed = {
        "pkg/helper.py",
        "pkg/newmod.py",
        "pkg/util.py",
        "body.py",
        "web/util.ts",
        "web/lib/index.ts",
    }
    assert {cg.normalize_target(path, str(root)) for path in parses} == changed
    assert (result.parsed, result.removed, result.stale) == (len(changed), 1, len(changed) + 1)
    assert result.relinked > 0
    assert _graph(store, "p1") == await _seeded_graph(root, tmp_path / "oracle.db")
    # And the answers a person reads, not only the rows.
    callers = await store.callers_of_symbol("p1", "pkg/helper.py", "later")
    assert [row["src_path"] for row in callers] == ["app.py"]
    assert sorted(await store.imports_of("p1", "app.py")) == [
        "pkg/helper.py",
        "pkg/newmod.py",
        "pkg/util.py",
    ]
    assert await store.imports_of("p1", "gone_user.py") == []
    assert [node.path for node in await store.reverse_dependents("p1", "web/lib/index.ts")] == [
        "web/view.ts"
    ]
    store.close()


async def test_a_touched_file_is_read_once_and_then_trusted(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    _age(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    when = time.time() - AGED_SECONDS // 2
    os.utime(root / "touched.py", (when, when))

    first = await sync_project(store, "p1", str(root))
    assert (first.stale, first.parsed) == (1, 0)
    second = await sync_project(store, "p1", str(root))
    assert (second.stale, second.parsed) == (0, 0)
    store.close()


async def test_a_fresh_stamp_is_verified_by_content_until_it_is_old_enough(
    tmp_path: Path,
) -> None:
    """A stamp taken within the racy window is recorded as unknown, never trusted.

    Otherwise a write landing in the same timestamp tick as the read, without
    changing the size, would leave a stamp that matches bytes the graph never saw.
    """
    root = tmp_path / "repo"
    _project(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))  # every file written a moment ago
    stamps = await store.file_stamps("p1")
    assert {stamp.mtime_ns for stamp in stamps.values()} == {None}

    _age(root)
    verified = await sync_project(store, "p1", str(root))
    assert (verified.stale, verified.parsed) == (10, 0)
    assert all(stamp.mtime_ns is not None for stamp in (await store.file_stamps("p1")).values())
    assert (await sync_project(store, "p1", str(root))).stale == 0
    store.close()


async def test_a_version_2_graph_keeps_its_rows_and_is_verified_once(tmp_path: Path) -> None:
    """The upgrade is a column, not a re-seed: hashes are checked, nothing is parsed."""
    root = tmp_path / "repo"
    _project(root)
    _age(root)
    database = tmp_path / "graph.db"
    store = CodeGraphStore(database)
    await index_project(store, "p1", str(root))
    before = _graph(store, "p1")
    store.close()
    db = sqlite3.connect(database)
    db.execute("ALTER TABLE code_graph_files DROP COLUMN source_mtime_ns")
    db.execute("ALTER TABLE code_graph_files DROP COLUMN source_size")
    db.commit()
    db.close()

    store = CodeGraphStore(database)
    assert {stamp.mtime_ns for stamp in (await store.file_stamps("p1")).values()} == {None}
    result = await sync_project(store, "p1", str(root))
    assert (result.outcome, result.stale, result.parsed, result.removed) == ("synced", 10, 0, 0)
    assert _graph(store, "p1") == before
    assert (await sync_project(store, "p1", str(root))).stale == 0
    store.close()


async def test_a_caller_maintained_before_its_callee_still_resolves(tmp_path: Path) -> None:
    """One turn writes the call, then the definition: order must not decide the edge."""
    root = tmp_path / "repo"
    _project(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))

    _write(root, "body.py", CALLS_FRESH)
    _write(root, "pkg/helper.py", DEFINES_FRESH)
    assert await maintain_files(store, "p1", str(root), ["body.py", "pkg/helper.py"]) == 2

    callers = await store.callers_of_symbol("p1", "pkg/helper.py", "fresh")
    assert [row["src_path"] for row in callers] == ["body.py"]
    assert _graph(store, "p1") == await _seeded_graph(root, tmp_path / "oracle.db")
    store.close()


async def test_a_callee_written_a_turn_after_its_caller_relinks_the_caller(
    tmp_path: Path,
) -> None:
    """The live drift the per-start re-seed used to hide until the next restart."""
    root = tmp_path / "repo"
    _project(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))

    _write(root, "body.py", CALLS_FRESH)
    await maintain_files(store, "p1", str(root), ["body.py"])
    assert await store.callers_of_symbol("p1", "pkg/helper.py", "fresh") == []

    _write(root, "pkg/helper.py", DEFINES_FRESH)
    await maintain_files(store, "p1", str(root), ["pkg/helper.py"])

    callers = await store.callers_of_symbol("p1", "pkg/helper.py", "fresh")
    assert [row["src_path"] for row in callers] == ["body.py"]
    assert _graph(store, "p1") == await _seeded_graph(root, tmp_path / "oracle.db")
    store.close()


async def test_deleting_a_module_unlinks_its_importers(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _project(root)
    store = CodeGraphStore(tmp_path / "graph.db")
    await index_project(store, "p1", str(root))
    assert await store.imports_of("p1", "gone_user.py") == ["pkg/gone.py"]

    (root / "pkg" / "gone.py").unlink()
    await maintain_files(store, "p1", str(root), ["pkg/gone.py"])

    assert await store.imports_of("p1", "gone_user.py") == []
    assert await store.reverse_dependents("p1", "pkg/gone.py") == []
    assert _graph(store, "p1") == await _seeded_graph(root, tmp_path / "oracle.db")
    store.close()


async def test_the_appeared_file_prefilter_never_drops_an_import_that_could_name_it(
    tmp_path: Path,
) -> None:
    """The SQL filter narrows a full scan; it must be a superset of the exact check.

    A miss here is silent: an unchanged importer would keep an import that a file
    appearing beside it now answers, until somebody edited it.
    """
    from swe_mux.code_graph import EDGE_IMPORTS, ParsedFile, ResolvedEdge, _import_tail

    root = tmp_path / "repo"
    (root / "pkg" / "sub").mkdir(parents=True)
    (root / "web" / "lib").mkdir(parents=True)
    modules = {
        "pkg/sub/mod.py": ["pkg.helper", ".sibling", "..parent_mod", ".", "..", "os.path"],
        "web/lib/view.ts": [
            "./util",
            "./lib/",
            "../shared/thing.js",
            "./",
            "..",
            "../lib/..",
            "..\\win",
            "react",
            "@scope/pkg",
        ],
    }
    store = CodeGraphStore(tmp_path / "graph.db")
    for src, specs in modules.items():
        edges = [
            ResolvedEdge(EDGE_IMPORTS, src, None, None, None, spec, False, line)
            for line, spec in enumerate(specs, start=1)
        ]
        lang = "python" if src.endswith(".py") else "typescript"
        await store.replace_file("p1", ParsedFile(src, lang, "h", (), (), ()), edges)

    every = await store.import_edges("p1")
    tails: dict[int, str | None] = {}
    for edge_id, src, module, _ in every:
        spec = cg.spec_for_path(src)
        assert spec is not None
        tails[edge_id] = _import_tail(module, src, root, spec=spec)
    keys = {tail for tail in tails.values() if tail is not None}
    assert {
        *("helper", "sibling", "parent_mod", "sub", "pkg"),
        *("util", "lib", "thing.js", "web", "win"),
    } <= keys
    for key in keys:
        named = {edge[0] for edge in await store.import_edges("p1", naming={key})}
        exact = {edge_id for edge_id, tail in tails.items() if tail == key}
        assert exact <= named, key
    store.close()


async def test_the_first_turn_after_a_restart_syncs_instead_of_reseeding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two consumer services on one database are two daemon processes."""
    from swe_mux.automation_store import AutomationStore
    from swe_mux.deterministic_consumers import ConsumerContext, DeterministicConsumerService
    from swe_mux.tier0_store import Tier0Store

    root = tmp_path / "repo"
    _project(root)
    _age(root)
    database = tmp_path / "mux.db"

    class _Events:
        async def emit(self, kind: str, **payload: Any) -> None:
            pass

    async def context(_session_id: str) -> ConsumerContext:
        return ConsumerContext(
            project_id="p1",
            project_root=str(root),
            agent_run_id="r1",
            enabled=frozenset({"code_graph"}),
        )

    async def one_process() -> dict[str, Any]:
        tier0 = Tier0Store(database)
        store = AutomationStore(database)
        graph = CodeGraphStore(database)
        try:
            service = DeterministicConsumerService(
                tier0,
                store,
                type("Sessions", (), {"sessions": {}})(),
                _Events(),
                resolve_context=context,
                code_graph=graph,
            )
            await service.evaluate("s1")
            status = service.status()
            sync: dict[str, Any] = status["code_graph_sync"]["p1"]
            return sync
        finally:
            graph.close()
            store.close()
            tier0.close()

    first = await one_process()
    assert first["outcome"] == "seeded"
    parses = _count(monkeypatch, "parse_source")
    second = await one_process()
    assert (second["outcome"], second["stale"], second["parsed"]) == ("synced", 0, 0)
    assert second["reason"] == "first_turn"
    assert parses == []
