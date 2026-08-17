"""Deterministic code-structure graph (roadmap Phase 7.9).

swe-mux already holds a *behavioral* graph — Tier 0 facts, provenance edges,
doc-debt ownership, git attribution. This module adds the missing *structural*
graph: how the code connects, not who touched it.

The graph is built with tree-sitter (never LSP — see the roadmap), keyed on the
same ``normalize_target()`` path identity every other consumer uses, and
maintained incrementally: on a ``file_write`` fact mux re-parses that one file and
replaces its edges. There is no file watcher and no full rebuild — mux observes
every edit already, which is the freshness advantage a standalone index lacks.

Three honesty rules run through the whole module:

- **Every static reverse-caller set is a lower bound.** ``getattr``, dict
  dispatch, decorators, dependency injection, and dynamic imports are edges
  tree-sitter cannot see. Queries say so, and the git co-change net
  (``co_change``) is the recall safety net for exactly those misses.
- **Resolution is import-aware, against the filesystem.** A call to ``X`` in a
  file resolves to a definition only through an actual import or a local
  definition; a bare same-named symbol in an unrelated module is never a false
  edge. Modules resolve against real files on disk, so resolution is
  order-independent (it does not depend on which file was parsed first).
- **Unresolved is recorded, never guessed.** A reference whose target cannot be
  resolved is stored with ``resolved=0`` and a raw name, so it feeds counts and
  the co-change net without inventing an edge.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .deterministic_consumers import normalize_target
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    run_sqlite_operation,
    write_schema_version,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

CODE_GRAPH_SCHEMA_VERSION = 1

#: Edge kinds. `defines` is file -> symbol; `imports` is file -> file; `calls`
#: and `references` are symbol -> symbol (or symbol -> unresolved name).
EDGE_IMPORTS = "imports"
EDGE_CALLS = "calls"
EDGE_REFERENCES = "references"
EDGE_DEFINES = "defines"

#: The blast-radius traversal only follows the two edge kinds that mean "depends
#: on": an importer and a caller are downstream of a change; a `defines` edge is
#: internal structure and a `references` edge is a weaker signal counted but not
#: walked by default.
BLAST_EDGE_KINDS = (EDGE_IMPORTS, EDGE_CALLS)

#: Hard bounds so a query never returns an unbounded neighbourhood.
DEFAULT_BLAST_HOPS = 2
MAX_BLAST_HOPS = 4
DEFAULT_RESULT_LIMIT = 40
MAX_RESULT_LIMIT = 200
#: A file re-parse never emits more than this many edges — a generated or minified
#: file would otherwise flood the table. Recorded as a truncation, not silent.
MAX_EDGES_PER_FILE = 4000
#: Largest source file parsed. Beyond this tree-sitter cost is not worth it and the
#: file is almost certainly generated.
MAX_PARSE_BYTES = 2_000_000


# --------------------------------------------------------------------------- #
# Language specs — tree-sitter queries per language.                          #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    """One language's tree-sitter identity, extensions, and capture query.

    The query names captures with a fixed vocabulary the extractor understands:
    ``def.function``/``def.class``/``def.method`` (definitions, the captured node
    is the *name*), ``call`` (a call whose function is a bare name),
    ``call.attr`` (a call through an attribute — method/namespace call, name
    only), ``import.module`` (a module path string), ``ref`` (a non-call name
    reference). Enclosing-symbol attribution is computed structurally by walking
    ancestors, not by the query.
    """

    name: str
    ts_name: str
    extensions: tuple[str, ...]
    query: str
    #: Node types that introduce a named definition scope, for enclosing-symbol
    #: attribution and qualname building.
    scope_types: tuple[str, ...]
    #: Extensions (without dot) an import path may resolve to, most specific first.
    module_suffixes: tuple[str, ...]
    #: Index-file basenames a package import resolves to (``__init__.py``,
    #: ``index.ts``). Empty for languages with no package-directory convention.
    index_basenames: tuple[str, ...]


_PYTHON_QUERY = """
(function_definition name: (identifier) @def.function)
(class_definition name: (identifier) @def.class)
(call function: (identifier) @call)
(call function: (attribute attribute: (identifier) @call.attr))
(import_statement name: (dotted_name) @import.module)
(import_statement name: (aliased_import name: (dotted_name) @import.module))
(import_from_statement module_name: (dotted_name) @import.module)
(import_from_statement module_name: (relative_import) @import.module)
(attribute attribute: (identifier) @ref)
"""

# TS and TSX name a class with `type_identifier`; JS names it with `identifier`.
# tree-sitter rejects an entire query if any single pattern is impossible for the
# grammar, so the class-name pattern cannot be shared — hence two near-identical
# queries rather than one. Everything else is common to all three grammars.
_TS_COMMON = """
(function_declaration name: (identifier) @def.function)
(method_definition name: (property_identifier) @def.method)
(call_expression function: (identifier) @call)
(call_expression function: (member_expression property: (property_identifier) @call.attr))
(import_statement source: (string) @import.module)
(call_expression
  function: (identifier) @_req
  arguments: (arguments (string) @import.module)
  (#eq? @_req "require"))
"""
_TS_QUERY = _TS_COMMON + "\n(class_declaration name: (type_identifier) @def.class)\n"
_JS_QUERY = _TS_COMMON + "\n(class_declaration name: (identifier) @def.class)\n"

LANGUAGE_SPECS: tuple[LanguageSpec, ...] = (
    LanguageSpec(
        name="python",
        ts_name="python",
        extensions=(".py", ".pyi"),
        query=_PYTHON_QUERY,
        scope_types=("function_definition", "class_definition"),
        module_suffixes=("py", "pyi"),
        index_basenames=("__init__.py", "__init__.pyi"),
    ),
    LanguageSpec(
        name="typescript",
        ts_name="typescript",
        extensions=(".ts",),
        query=_TS_QUERY,
        scope_types=("function_declaration", "method_definition", "class_declaration"),
        module_suffixes=("ts", "tsx", "d.ts", "js", "jsx", "mjs", "cjs"),
        index_basenames=("index.ts", "index.tsx", "index.js", "index.jsx"),
    ),
    LanguageSpec(
        name="tsx",
        ts_name="tsx",
        extensions=(".tsx",),
        query=_TS_QUERY,
        scope_types=("function_declaration", "method_definition", "class_declaration"),
        module_suffixes=("ts", "tsx", "d.ts", "js", "jsx", "mjs", "cjs"),
        index_basenames=("index.ts", "index.tsx", "index.js", "index.jsx"),
    ),
    LanguageSpec(
        name="javascript",
        ts_name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        query=_JS_QUERY,
        scope_types=("function_declaration", "method_definition", "class_declaration"),
        module_suffixes=("js", "jsx", "mjs", "cjs", "ts", "tsx"),
        index_basenames=("index.js", "index.jsx", "index.ts", "index.tsx"),
    ),
)

_SPEC_BY_EXT: dict[str, LanguageSpec] = {}
for _spec in LANGUAGE_SPECS:
    for _ext in _spec.extensions:
        _SPEC_BY_EXT[_ext] = _spec


def spec_for_path(path: str) -> LanguageSpec | None:
    """The language spec for a path, by extension, or None if unsupported.

    ``.tsx`` and ``.d.ts`` are matched before ``.ts``/``.js`` so a TSX file is not
    parsed with the plain-TypeScript grammar (which rejects JSX).
    """
    lowered = path.lower()
    if lowered.endswith(".d.ts"):
        return _SPEC_BY_EXT.get(".ts")
    suffix = Path(lowered).suffix
    return _SPEC_BY_EXT.get(suffix)


# --------------------------------------------------------------------------- #
# Parsing — pure, testable, no store.                                         #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Symbol:
    name: str  # qualname, e.g. "Foo.method"
    kind: str  # function | class | method
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class RawImport:
    module: str  # the literal module/source string as written
    line: int


@dataclass(frozen=True, slots=True)
class RawRef:
    name: str  # the referenced/called bare name
    kind: str  # calls | references
    src_symbol: str | None  # enclosing symbol qualname, or None at module scope
    line: int


@dataclass(frozen=True, slots=True)
class ParsedFile:
    path: str  # normalized identity
    lang: str
    content_hash: str
    symbols: tuple[Symbol, ...]
    imports: tuple[RawImport, ...]
    refs: tuple[RawRef, ...]
    truncated: bool = False


def content_hash(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


class _ParserCache:
    """Lazily-built tree-sitter parsers/queries, one per language.

    Import of ``tree_sitter``/``tree_sitter_language_pack`` is deferred to first
    use so the module imports cleanly on a host where the grammars are absent
    (the graph simply parses nothing, reported by ``parsing_available``).
    """

    def __init__(self) -> None:
        self._parsers: dict[str, Any] = {}
        self._queries: dict[str, Any] = {}
        self._cursor_cls: Any = None
        self._available: bool | None = None

    def available(self) -> bool:
        if self._available is None:
            try:
                import tree_sitter  # noqa: F401
                import tree_sitter_language_pack  # noqa: F401

                self._available = True
            except Exception as exc:  # pragma: no cover - environment dependent
                log.warning("code-graph: tree-sitter unavailable: %s", exc)
                self._available = False
        return self._available

    def for_spec(self, spec: LanguageSpec) -> tuple[Any, Any, Any] | None:
        if not self.available():
            return None
        if spec.ts_name not in self._parsers:
            from tree_sitter import Query, QueryCursor
            from tree_sitter_language_pack import get_language, get_parser

            try:
                parser = get_parser(spec.ts_name)
                language = get_language(spec.ts_name)
                query = Query(language, spec.query)
            except Exception as exc:  # pragma: no cover - grammar dependent
                log.warning("code-graph: grammar %s failed: %s", spec.ts_name, exc)
                self._parsers[spec.ts_name] = None
                return None
            self._parsers[spec.ts_name] = parser
            self._queries[spec.ts_name] = query
            self._cursor_cls = QueryCursor
        cached: Any = self._parsers.get(spec.ts_name)
        if cached is None:
            return None
        return cached, self._queries[spec.ts_name], self._cursor_cls


_PARSERS = _ParserCache()


def parsing_available() -> bool:
    """Whether tree-sitter grammars loaded — a named acceptance check for the
    frozen app, where the grammar shared libraries must be bundled."""
    return _PARSERS.available()


def _enclosing_symbol(node: Any, spec: LanguageSpec) -> str | None:
    """Walk ancestors to build the qualname of the definition enclosing a node."""
    parts: list[str] = []
    current = node.parent
    while current is not None:
        if current.type in spec.scope_types:
            name_node = current.child_by_field_name("name")
            if name_node is not None and name_node.text is not None:
                parts.append(name_node.text.decode("utf-8", "replace"))
        current = current.parent
    if not parts:
        return None
    return ".".join(reversed(parts))


def _symbol_qualname(name_node: Any, spec: LanguageSpec) -> tuple[str, str]:
    """(qualname, kind) for a captured definition name node."""
    definition = name_node.parent
    kind_map = {
        "function_definition": "function",
        "class_definition": "class",
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    }
    kind = kind_map.get(definition.type if definition is not None else "", "function")
    leaf = name_node.text.decode("utf-8", "replace") if name_node.text is not None else ""
    # Walk enclosing scopes starting *above* this definition, so a symbol never
    # names itself as its own parent scope.
    enclosing = _enclosing_symbol(definition, spec) if definition is not None else None
    qualname = f"{enclosing}.{leaf}" if enclosing else leaf
    # A method inside a class reads as method even when the grammar tagged the def
    # generically (Python has no method node type).
    if kind == "function" and enclosing:
        kind = "method"
    return qualname, kind


def parse_source(path: str, source: bytes, *, project_root: str | None = None) -> ParsedFile | None:
    """Parse one file's bytes into structural facts, or None if unsupported.

    ``path`` may be absolute or repo-relative; the returned ``path`` is the
    normalized identity every consumer joins on.
    """
    spec = spec_for_path(path)
    if spec is None:
        return None
    identity = normalize_target(path, project_root)
    if identity is None:
        return None
    digest = content_hash(source)
    if len(source) > MAX_PARSE_BYTES:
        # Too large to be worth parsing; record the file node with no edges so it
        # still appears in the graph and its staleness is tracked.
        return ParsedFile(identity, spec.name, digest, (), (), (), truncated=True)
    loaded = _PARSERS.for_spec(spec)
    if loaded is None:
        return None
    parser, query, cursor_cls = loaded
    try:
        tree = parser.parse(source)
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("code-graph: parse failed for %s: %s", identity, exc)
        return None
    captures = cursor_cls(query).captures(tree.root_node)

    symbols: list[Symbol] = []
    imports: list[RawImport] = []
    refs: list[RawRef] = []
    truncated = False

    def emit_ref(node: Any, kind: str) -> None:
        nonlocal truncated
        if len(refs) >= MAX_EDGES_PER_FILE:
            truncated = True
            return
        text = node.text.decode("utf-8", "replace") if node.text is not None else ""
        if not text:
            return
        refs.append(RawRef(text, kind, _enclosing_symbol(node, spec), node.start_point[0] + 1))

    for capture_name, nodes in captures.items():
        for node in nodes:
            if node.text is None:
                continue
            if capture_name.startswith("def."):
                qualname, kind = _symbol_qualname(node, spec)
                if qualname:
                    symbols.append(
                        Symbol(qualname, kind, node.start_point[0] + 1, node.end_point[0] + 1)
                    )
            elif capture_name == "import.module":
                text = node.text.decode("utf-8", "replace").strip("'\" ")
                if text:
                    imports.append(RawImport(text, node.start_point[0] + 1))
            elif capture_name == "call":
                emit_ref(node, EDGE_CALLS)
            elif capture_name == "call.attr":
                emit_ref(node, EDGE_CALLS)
            elif capture_name == "ref":
                emit_ref(node, EDGE_REFERENCES)

    # A call through an attribute (`self.paint()`) is captured both as a call and,
    # by the broad attribute rule, as a reference. Drop the reference twin so one
    # source event is one edge.
    call_keys = {(r.name, r.src_symbol, r.line) for r in refs if r.kind == EDGE_CALLS}
    deduped = [
        r
        for r in refs
        if r.kind == EDGE_CALLS or (r.name, r.src_symbol, r.line) not in call_keys
    ]

    return ParsedFile(
        identity,
        spec.name,
        digest,
        tuple(symbols),
        tuple(imports),
        tuple(deduped),
        truncated=truncated,
    )


# --------------------------------------------------------------------------- #
# Import resolution — against the real filesystem, so it is order-independent. #
# --------------------------------------------------------------------------- #


def resolve_import(
    module: str, from_path: str, project_root: str, *, spec: LanguageSpec
) -> str | None:
    """Resolve an import module string to a normalized in-project file path.

    Returns None for a third-party/stdlib import (nothing under ``project_root``
    matches) — a deliberate non-edge, not a failure. Resolution reads the actual
    filesystem, so it never depends on parse order.
    """
    root = Path(project_root)
    from_dir = (root / from_path).parent

    def try_candidates(base: Path) -> str | None:
        for suffix in spec.module_suffixes:
            candidate = base.with_suffix("." + suffix) if base.suffix == "" else Path(
                str(base) + "." + suffix
            )
            if candidate.is_file():
                return _rel(candidate, root)
        for index in spec.index_basenames:
            candidate = base / index
            if candidate.is_file():
                return _rel(candidate, root)
        # A file that already carries its extension (JS/TS "./mod.js").
        if base.is_file():
            return _rel(base, root)
        return None

    if spec.name == "python":
        if module.startswith("."):
            # Relative import: leading dots count parent hops.
            dots = len(module) - len(module.lstrip("."))
            rest = module.lstrip(".")
            base = from_dir
            for _ in range(dots - 1):
                base = base.parent
            if rest:
                base = base / Path(rest.replace(".", "/"))
            return try_candidates(base)
        base = root / Path(module.replace(".", "/"))
        return try_candidates(base)

    # JS/TS: only path-like specifiers ("./x", "../x", "/x") are in-project;
    # bare "pkg" specifiers are node_modules and resolve to no edge.
    if module.startswith("."):
        base = (from_dir / module).resolve()
        try:
            base.relative_to(root.resolve())
        except ValueError:
            return None
        return try_candidates(base)
    return None


def _rel(path: Path, root: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return normalize_target(str(rel), None)


@dataclass(frozen=True, slots=True)
class ResolvedEdge:
    kind: str
    src_path: str
    src_symbol: str | None
    dst_path: str | None
    dst_symbol: str | None
    dst_name: str
    resolved: bool
    line: int


def import_targets(parsed: ParsedFile, project_root: str) -> list[str]:
    """The in-project files this file imports (resolved paths, deduped).

    Pure filesystem resolution, so a caller can prefetch each target's symbol
    leaf set before calling ``resolve_edges`` (whose store reads are async).
    """
    spec = spec_for_path(parsed.path)
    if spec is None:
        return []
    targets: list[str] = []
    for imp in parsed.imports:
        target = resolve_import(imp.module, parsed.path, project_root, spec=spec)
        if target is not None and target not in targets:
            targets.append(target)
    return targets


def resolve_edges(
    parsed: ParsedFile,
    project_root: str,
    *,
    known_symbols: dict[str, set[str]] | None = None,
) -> list[ResolvedEdge]:
    """Turn a parsed file's raw refs/imports into resolved graph edges.

    ``known_symbols`` maps a resolved module path to the set of top-level symbol
    *leaf* names it defines, used to attribute an imported name to its defining
    module. It is a prefetched dict (not a callback) because the store it comes
    from is read on a worker thread. When absent, an import edge is still recorded
    file->file and call edges resolve only to same-file definitions or stay
    unresolved (recorded, never guessed).
    """
    spec = spec_for_path(parsed.path)
    if spec is None:
        return []
    known = known_symbols or {}

    edges: list[ResolvedEdge] = []
    local_leaves = {sym.name.split(".")[-1] for sym in parsed.symbols}
    local_qualnames = {sym.name for sym in parsed.symbols}

    # Imports: file -> file, and a name->module map for call resolution.
    name_to_module: dict[str, str] = {}
    for imp in parsed.imports:
        target = resolve_import(imp.module, parsed.path, project_root, spec=spec)
        edges.append(
            ResolvedEdge(
                EDGE_IMPORTS,
                parsed.path,
                None,
                target,
                None,
                imp.module,
                target is not None,
                imp.line,
            )
        )
        if target is not None:
            # Best-effort: the imported leaf name maps to this module. We do not
            # parse the import clause's imported-names list per language here; the
            # call resolver instead tries every imported module that defines a
            # matching symbol, which is why known_symbols is consulted.
            name_to_module[Path(imp.module).name.split(".")[-1]] = target

    imported_modules = [
        e.dst_path for e in edges if e.kind == EDGE_IMPORTS and e.dst_path is not None
    ]

    for ref in parsed.refs:
        leaf = ref.name
        dst_path: str | None = None
        dst_symbol: str | None = None
        resolved = False
        if leaf in local_leaves:
            # A same-file definition — the safe, always-correct resolution.
            dst_path = parsed.path
            dst_symbol = next(
                (q for q in local_qualnames if q.split(".")[-1] == leaf), leaf
            )
            resolved = True
        elif known:
            # Import-aware: attribute the name to an imported module that defines
            # it. This is what stops a same-named symbol in an unrelated module
            # from becoming a false caller.
            for module_path in imported_modules:
                if leaf in known.get(module_path, ()):
                    dst_path = module_path
                    dst_symbol = leaf
                    resolved = True
                    break
        edges.append(
            ResolvedEdge(
                ref.kind,
                parsed.path,
                ref.src_symbol,
                dst_path,
                dst_symbol,
                leaf,
                resolved,
                ref.line,
            )
        )

    # Defines: file -> symbol.
    for sym in parsed.symbols:
        edges.append(
            ResolvedEdge(
                EDGE_DEFINES,
                parsed.path,
                None,
                parsed.path,
                sym.name,
                sym.name,
                True,
                sym.start_line,
            )
        )
    return edges


# --------------------------------------------------------------------------- #
# Store.                                                                       #
# --------------------------------------------------------------------------- #

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS code_graph_files (
  project_id TEXT NOT NULL,
  path TEXT NOT NULL,
  lang TEXT,
  content_hash TEXT,
  symbol_count INTEGER NOT NULL DEFAULT 0,
  truncated INTEGER NOT NULL DEFAULT 0,
  parsed_at REAL NOT NULL,
  PRIMARY KEY (project_id, path)
);
CREATE TABLE IF NOT EXISTS code_graph_symbols (
  project_id TEXT NOT NULL,
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  start_line INTEGER,
  end_line INTEGER,
  PRIMARY KEY (project_id, path, name)
);
CREATE TABLE IF NOT EXISTS code_graph_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  src_path TEXT NOT NULL,
  src_symbol TEXT,
  dst_path TEXT,
  dst_symbol TEXT,
  dst_name TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0,
  src_line INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cg_edges_src ON code_graph_edges(project_id, src_path);
CREATE INDEX IF NOT EXISTS idx_cg_edges_dst ON code_graph_edges(project_id, dst_path, kind);
CREATE INDEX IF NOT EXISTS idx_cg_edges_name ON code_graph_edges(project_id, dst_name, kind);
CREATE INDEX IF NOT EXISTS idx_cg_symbols_name ON code_graph_symbols(project_id, name);
CREATE INDEX IF NOT EXISTS idx_cg_files_project ON code_graph_files(project_id);
"""


@dataclass(frozen=True, slots=True)
class BlastNode:
    path: str
    hop: int
    via: str  # imports | calls
    symbols: tuple[str, ...] = ()


class CodeGraphStore:
    """SQLite-backed structural graph on the shared ``mux.db``.

    One writer thread, mirroring ``Tier0Store`` — the operation coordinator
    serialises against every other store sharing the file.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._operation_lock = database_operation_lock(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mux-codegraph-db")
        self._executor.submit(self._connect).result()

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _connect(self) -> None:
        with self._operation_lock:
            self._db = connect_or_quarantine(self.path, self._open)
            self._db.executescript(_SCHEMA)
            write_schema_version(self._db, "code_graph", CODE_GRAPH_SCHEMA_VERSION)
            self._db.commit()

    async def _run(self, fn: Callable[[], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, run_sqlite_operation, self._db, self._operation_lock, fn
        )

    def close(self) -> None:
        try:
            self._executor.submit(self._db.close).result(timeout=5)
        except Exception:  # pragma: no cover - best effort
            pass
        self._executor.shutdown(wait=False)

    # -- Maintenance ------------------------------------------------------- #

    def file_hash(self, project_id: str, path: str) -> str | None:
        row = self._db.execute(
            "SELECT content_hash FROM code_graph_files WHERE project_id=? AND path=?",
            (project_id, path),
        ).fetchone()
        return row["content_hash"] if row else None

    async def known_hash(self, project_id: str, path: str) -> str | None:
        return await self._run(lambda: self.file_hash(project_id, path))

    async def leaf_names(self, project_id: str, path: str) -> set[str]:
        def op() -> set[str]:
            rows = self._db.execute(
                "SELECT name FROM code_graph_symbols WHERE project_id=? AND path=?",
                (project_id, path),
            ).fetchall()
            return {str(r["name"]).split(".")[-1] for r in rows}

        return await self._run(op)

    def _replace_file_sync(
        self, project_id: str, parsed: ParsedFile, edges: Sequence[ResolvedEdge]
    ) -> None:
        db = self._db
        db.execute(
            "DELETE FROM code_graph_edges WHERE project_id=? AND src_path=?",
            (project_id, parsed.path),
        )
        db.execute(
            "DELETE FROM code_graph_symbols WHERE project_id=? AND path=?",
            (project_id, parsed.path),
        )
        db.execute(
            "INSERT INTO code_graph_files(project_id,path,lang,content_hash,symbol_count,"
            "truncated,parsed_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,path) DO UPDATE SET lang=excluded.lang,"
            "content_hash=excluded.content_hash,symbol_count=excluded.symbol_count,"
            "truncated=excluded.truncated,parsed_at=excluded.parsed_at",
            (
                project_id,
                parsed.path,
                parsed.lang,
                parsed.content_hash,
                len(parsed.symbols),
                1 if parsed.truncated else 0,
                time.time(),
            ),
        )
        db.executemany(
            "INSERT INTO code_graph_symbols(project_id,path,name,kind,start_line,end_line) "
            "VALUES(?,?,?,?,?,?)",
            [
                (project_id, parsed.path, s.name, s.kind, s.start_line, s.end_line)
                for s in parsed.symbols
            ],
        )
        db.executemany(
            "INSERT INTO code_graph_edges(project_id,kind,src_path,src_symbol,dst_path,"
            "dst_symbol,dst_name,resolved,src_line) VALUES(?,?,?,?,?,?,?,?,?)",
            [
                (
                    project_id,
                    e.kind,
                    e.src_path,
                    e.src_symbol,
                    e.dst_path,
                    e.dst_symbol,
                    e.dst_name,
                    1 if e.resolved else 0,
                    e.line,
                )
                for e in edges[:MAX_EDGES_PER_FILE]
            ],
        )
        db.commit()

    async def replace_file(
        self, project_id: str, parsed: ParsedFile, edges: Sequence[ResolvedEdge]
    ) -> None:
        await self._run(lambda: self._replace_file_sync(project_id, parsed, edges))

    async def remove_file(self, project_id: str, path: str) -> bool:
        """Drop a file and its edges from the graph. Returns True if it existed."""

        def op() -> bool:
            cur = self._db.execute(
                "DELETE FROM code_graph_files WHERE project_id=? AND path=?",
                (project_id, path),
            )
            existed = cur.rowcount > 0
            self._db.execute(
                "DELETE FROM code_graph_edges WHERE project_id=? AND src_path=?",
                (project_id, path),
            )
            self._db.execute(
                "DELETE FROM code_graph_symbols WHERE project_id=? AND path=?",
                (project_id, path),
            )
            self._db.commit()
            return existed

        return await self._run(op)

    # -- Queries ----------------------------------------------------------- #

    async def reverse_dependents(
        self, project_id: str, path: str, *, hops: int = DEFAULT_BLAST_HOPS
    ) -> list[BlastNode]:
        """Files that import or call into ``path``, up to ``hops`` levels, via a
        bounded recursive CTE. Each result names the nearest hop and the edge kind
        that reached it. This is a **lower bound** — dynamic dispatch is invisible.
        """
        hops = max(1, min(hops, MAX_BLAST_HOPS))

        def op() -> list[BlastNode]:
            rows = self._db.execute(
                """
                WITH RECURSIVE reach(path, hop, via) AS (
                    SELECT src_path, 1, kind FROM code_graph_edges
                      WHERE project_id=:pid AND dst_path=:target AND kind IN ('imports','calls')
                    UNION
                    SELECT e.src_path, r.hop + 1, e.kind
                      FROM code_graph_edges e JOIN reach r ON e.dst_path = r.path
                      WHERE e.project_id=:pid AND e.kind IN ('imports','calls')
                        AND r.hop < :hops AND e.src_path <> :target
                )
                SELECT path, MIN(hop) AS hop,
                       (SELECT via FROM reach r2 WHERE r2.path = reach.path
                          ORDER BY hop LIMIT 1) AS via
                  FROM reach GROUP BY path ORDER BY hop, path LIMIT :lim
                """,
                {
                    "pid": project_id,
                    "target": path,
                    "hops": hops,
                    "lim": MAX_RESULT_LIMIT,
                },
            ).fetchall()
            return [BlastNode(r["path"], r["hop"], r["via"]) for r in rows if r["path"] != path]

        return await self._run(op)

    async def callers_of_symbol(
        self, project_id: str, path: str, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        """The (file, symbol) pairs that call a symbol in ``path``. When
        ``symbol`` is None, every caller of any symbol in the file."""

        def op() -> list[dict[str, Any]]:
            clause = "AND dst_symbol=?" if symbol else ""
            params: list[Any] = [project_id, path]
            if symbol:
                params.append(symbol)
            rows = self._db.execute(
                f"SELECT DISTINCT src_path, src_symbol, dst_symbol, dst_name, src_line, resolved "
                f"FROM code_graph_edges WHERE project_id=? AND dst_path=? AND kind='calls' "
                f"{clause} ORDER BY src_path LIMIT ?",
                (*params, MAX_RESULT_LIMIT),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def unresolved_callers_by_name(
        self, project_id: str, name: str
    ) -> list[dict[str, Any]]:
        """Callers of a bare name whose target was never resolved — the recall
        gap tree-sitter leaves. Named, not hidden."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT DISTINCT src_path, src_symbol, src_line FROM code_graph_edges "
                "WHERE project_id=? AND dst_name=? AND kind='calls' AND resolved=0 "
                "ORDER BY src_path LIMIT ?",
                (project_id, name, MAX_RESULT_LIMIT),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def definitions(
        self, project_id: str, name: str
    ) -> list[dict[str, Any]]:
        """Where a symbol (by leaf or qualname) is defined."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT path, name, kind, start_line, end_line FROM code_graph_symbols "
                "WHERE project_id=? AND (name=? OR name LIKE ?) ORDER BY path LIMIT ?",
                (project_id, name, f"%.{name}", MAX_RESULT_LIMIT),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def symbols_in(self, project_id: str, path: str) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT name, kind, start_line, end_line FROM code_graph_symbols "
                "WHERE project_id=? AND path=? ORDER BY start_line LIMIT ?",
                (project_id, path, MAX_RESULT_LIMIT),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def references_to(
        self, project_id: str, path: str, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        def op() -> list[dict[str, Any]]:
            clause = "AND dst_symbol=?" if symbol else ""
            params: list[Any] = [project_id, path]
            if symbol:
                params.append(symbol)
            rows = self._db.execute(
                f"SELECT DISTINCT src_path, src_symbol, dst_symbol, kind, src_line "
                f"FROM code_graph_edges WHERE project_id=? AND dst_path=? "
                f"AND kind IN ('calls','references') {clause} ORDER BY src_path LIMIT ?",
                (*params, MAX_RESULT_LIMIT),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def imports_of(self, project_id: str, path: str) -> list[str]:
        """Files ``path`` imports (its forward dependencies)."""

        def op() -> list[str]:
            rows = self._db.execute(
                "SELECT DISTINCT dst_path FROM code_graph_edges WHERE project_id=? "
                "AND src_path=? AND kind='imports' AND dst_path IS NOT NULL",
                (project_id, path),
            ).fetchall()
            return [r["dst_path"] for r in rows]

        return await self._run(op)

    async def orphans(self, project_id: str) -> list[dict[str, Any]]:
        """Files with no inbound import or call edge — dead-code candidates.

        Guarded against entry points is the *caller's* job: this returns raw
        candidates, and unresolved dynamic callers mean every result is only a
        candidate. Files that other files never reference statically."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                """
                SELECT f.path, f.lang, f.symbol_count FROM code_graph_files f
                WHERE f.project_id=:pid AND f.symbol_count > 0
                  AND NOT EXISTS (
                    SELECT 1 FROM code_graph_edges e
                    WHERE e.project_id=:pid AND e.dst_path=f.path
                      AND e.kind IN ('imports','calls') AND e.src_path <> f.path)
                ORDER BY f.path LIMIT :lim
                """,
                {"pid": project_id, "lim": MAX_RESULT_LIMIT},
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def god_nodes(
        self, project_id: str, *, min_fan_in: int = 12
    ) -> list[dict[str, Any]]:
        """Files with high inbound fan-in (many distinct importers/callers)."""

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                """
                SELECT dst_path AS path, COUNT(DISTINCT src_path) AS fan_in
                FROM code_graph_edges
                WHERE project_id=? AND dst_path IS NOT NULL AND kind IN ('imports','calls')
                  AND src_path <> dst_path
                GROUP BY dst_path HAVING fan_in >= ? ORDER BY fan_in DESC LIMIT ?
                """,
                (project_id, min_fan_in, MAX_RESULT_LIMIT),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(op)

    async def import_cycles(self, project_id: str, *, limit: int = 20) -> list[list[str]]:
        """Detect import cycles by DFS over the file-level import graph.

        Bounded: at most ``limit`` cycles, and the adjacency is read once. Tarjan
        would be tighter but the graph is small and re-read per turn, so a bounded
        DFS is simpler to keep correct."""

        def op() -> list[list[str]]:
            rows = self._db.execute(
                "SELECT DISTINCT src_path, dst_path FROM code_graph_edges "
                "WHERE project_id=? AND kind='imports' AND dst_path IS NOT NULL "
                "AND src_path <> dst_path",
                (project_id,),
            ).fetchall()
            adjacency: dict[str, list[str]] = {}
            for r in rows:
                adjacency.setdefault(r["src_path"], []).append(r["dst_path"])
            return _find_cycles(adjacency, limit=limit)

        return await self._run(op)

    async def file_count(self, project_id: str) -> int:
        return await self._run(
            lambda: int(
                self._db.execute(
                    "SELECT COUNT(*) AS c FROM code_graph_files WHERE project_id=?",
                    (project_id,),
                ).fetchone()["c"]
            )
        )

    async def subgraph(
        self, project_id: str, seed_paths: Iterable[str], *, hops: int = 1
    ) -> dict[str, Any]:
        """The bounded subgraph a change-map view needs: the seed files, their
        reverse dependents (blast radius), one hop of forward imports (context),
        and every edge among that node set. Server-side so the client never holds
        the whole codebase graph."""
        seeds = [s for s in dict.fromkeys(seed_paths) if s]

        async def collect() -> dict[str, Any]:
            nodes: dict[str, dict[str, Any]] = {}
            for seed in seeds:
                nodes.setdefault(seed, {"path": seed, "role": "seed"})
            # Reverse dependents (yellow = blast radius).
            for seed in seeds:
                for dep in await self.reverse_dependents(project_id, seed, hops=hops):
                    node = nodes.setdefault(dep.path, {"path": dep.path, "role": "blast"})
                    if node["role"] != "seed":
                        node["role"] = "blast"
                        node["hop"] = dep.hop
            # Forward context (blue = immediate imports).
            for seed in seeds:
                for imp in await self.imports_of(project_id, seed):
                    nodes.setdefault(imp, {"path": imp, "role": "context"})
            node_set = set(nodes)
            edges = await self._edges_among(project_id, node_set)
            return {"nodes": list(nodes.values()), "edges": edges}

        return await collect()

    async def _edges_among(self, project_id: str, node_set: set[str]) -> list[dict[str, Any]]:
        if not node_set:
            return []

        def op() -> list[dict[str, Any]]:
            rows = self._db.execute(
                "SELECT DISTINCT src_path, dst_path, kind FROM code_graph_edges "
                "WHERE project_id=? AND kind IN ('imports','calls') AND dst_path IS NOT NULL",
                (project_id,),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for r in rows:
                if r["src_path"] in node_set and r["dst_path"] in node_set:
                    out.append(
                        {"source": r["src_path"], "target": r["dst_path"], "kind": r["kind"]}
                    )
            return out

        return await self._run(op)


# --------------------------------------------------------------------------- #
# Maintenance, initial index, and the git co-change recall net.               #
# --------------------------------------------------------------------------- #

#: Directories the initial index never descends into — vendored, generated, or
#: mux's own worktree/state trees (the swe-mux primary checkout nests every
#: worktree under .claude, which would otherwise be indexed many times over).
_SKIP_DIRS = frozenset(
    {
        "node_modules",
        "__pycache__",
        "site-packages",
        "dist",
        "build",
        "out",
        "coverage",
        "vendor",
        "target",
    }
)
#: Largest one-time index. Beyond this the parse cost outweighs the recall, and a
#: repo this large is better served incrementally as its files are edited.
MAX_INDEX_FILES = 6000


def iter_source_files(project_root: str, *, limit: int = MAX_INDEX_FILES) -> Iterator[Path]:
    """Yield in-project source files a supported grammar can parse, bounded.

    Prunes vendored/generated/hidden directories so a one-time index over a large
    repository stays cheap. Hidden directories (``.git``, ``.venv``, ``.claude``,
    ``.mux``) are skipped wholesale.
    """
    root = Path(project_root)
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        for filename in filenames:
            if spec_for_path(filename) is None:
                continue
            yield Path(dirpath) / filename
            count += 1
            if count >= limit:
                return


def _abspath(project_root: str, target: str) -> Path:
    if os.path.isabs(target):
        return Path(target)
    return Path(project_root) / target


def _read_and_parse(path: str, project_root: str) -> ParsedFile | None:
    """Read a file's bytes and parse it — the CPU-bound half, run on a worker
    thread so a large index never blocks the event loop."""
    try:
        source = Path(path).read_bytes()
    except OSError:
        return None
    return parse_source(path, source, project_root=project_root)


async def index_project(
    store: CodeGraphStore, project_id: str, project_root: str, *, limit: int = MAX_INDEX_FILES
) -> int:
    """One-time bounded parse of a project's source tree.

    The maintenance model is incremental (re-parse one file on each write), but a
    reverse-dependency query needs the *importers* in the graph too, and an
    importer a session never edited would otherwise be invisible. This seeds those
    edges once; every later edit refreshes its own file. It is not the rejected
    full-rebuild-on-every-change watcher — it runs at most once per project.
    """
    files = await asyncio.to_thread(lambda: list(iter_source_files(project_root, limit=limit)))
    parsed_files: list[ParsedFile] = []
    leaf_index: dict[str, set[str]] = {}
    # Read and parse each file off the event loop: tree-sitter parsing is CPU-bound
    # and a one-time index of a large tree would otherwise stall the daemon loop.
    for path in files:
        parsed = await asyncio.to_thread(_read_and_parse, str(path), project_root)
        if parsed is None:
            continue
        parsed_files.append(parsed)
        leaf_index[parsed.path] = {s.name.split(".")[-1] for s in parsed.symbols}
    for parsed in parsed_files:
        edges = resolve_edges(parsed, project_root, known_symbols=leaf_index)
        await store.replace_file(project_id, parsed, edges)
    return len(parsed_files)


async def maintain_files(
    store: CodeGraphStore, project_id: str, project_root: str, paths: Iterable[str]
) -> int:
    """Re-parse each written file and replace its edges. A vanished file is
    removed from the graph rather than left stale. Returns the count re-parsed."""
    seen: set[str] = set()
    updated = 0
    for raw in paths:
        identity = normalize_target(raw, project_root)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        if spec_for_path(identity) is None:
            continue
        abspath = _abspath(project_root, raw)
        try:
            exists = await asyncio.to_thread(abspath.is_file)
        except OSError:
            exists = False
        if not exists:
            await store.remove_file(project_id, identity)
            continue
        parsed = await asyncio.to_thread(_read_and_parse, str(abspath), project_root)
        if parsed is None:
            continue
        if await store.known_hash(project_id, parsed.path) == parsed.content_hash:
            continue  # unchanged since last parse — nothing to do
        # Prefetch each imported module's symbol leaves (async store reads) so the
        # pure resolver gets a plain dict, never a callback into the worker thread.
        leaf_index: dict[str, set[str]] = {}
        for target in import_targets(parsed, project_root):
            leaf_index[target] = await store.leaf_names(project_id, target)
        edges = resolve_edges(parsed, project_root, known_symbols=leaf_index)
        await store.replace_file(project_id, parsed, edges)
        updated += 1
    return updated


def co_change_net(
    git_rows: Iterable[dict[str, Any]], target_path: str, *, project_root: str | None = None
) -> list[tuple[str, int]]:
    """Files that changed in the same commits as ``target_path``, most-shared first.

    The recall safety net for the dynamic edges tree-sitter cannot see: two files
    that keep being committed together are coupled whether or not a static edge
    exists. Mined from git-provenance contributor rows (each carries a commit's
    contributed paths), grouped by commit.
    """
    target = normalize_target(target_path, project_root)
    commit_files: dict[str, set[str]] = {}
    for row in git_rows:
        oid = str(row.get("commit_oid") or "")
        if not oid:
            continue
        paths = row.get("contributed_paths") or []
        normalized = {
            n for p in paths if (n := normalize_target(str(p), project_root)) is not None
        }
        commit_files.setdefault(oid, set()).update(normalized)
    counts: dict[str, int] = {}
    for paths in commit_files.values():
        if target not in paths:
            continue
        for other in paths:
            if other != target:
                counts[other] = counts.get(other, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _find_cycles(adjacency: dict[str, list[str]], *, limit: int) -> list[list[str]]:
    """Bounded DFS cycle enumeration over a small directed graph."""
    cycles: list[list[str]] = []
    seen_signatures: set[frozenset[str]] = set()
    on_stack: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        if len(cycles) >= limit:
            return
        on_stack[node] = len(stack)
        stack.append(node)
        for nxt in adjacency.get(node, ()):
            if nxt in on_stack:
                cycle = stack[on_stack[nxt] :]
                signature = frozenset(cycle)
                if len(cycle) > 1 and signature not in seen_signatures:
                    seen_signatures.add(signature)
                    cycles.append(list(cycle))
            elif len(cycles) < limit:
                dfs(nxt)
        stack.pop()
        del on_stack[node]

    for start in list(adjacency):
        if len(cycles) >= limit:
            break
        dfs(start)
    return cycles
