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
import re
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .deterministic_consumers import normalize_target
from .executor_accounting import AccountedThreadPoolExecutor
from .sqlite_store import (
    connect_or_quarantine,
    database_operation_lock,
    like_contains,
    run_sqlite_operation,
    write_schema_version,
)

log = logging.getLogger(__name__)

T = TypeVar("T")

#: 2 adds `code_graph_index_state`: which commit each Project's graph reflects,
#: so a branch that arrived by `git merge` can be detected and re-parsed instead
#: of being answered from a seed taken before it landed.
#: 3 adds each file's stat stamp (`source_mtime_ns`, `source_size`), so a graph that
#: persisted across a daemon restart is brought up to the tree by reading only the
#: files whose stamp moved, instead of being cleared and re-parsed whole.
CODE_GRAPH_SCHEMA_VERSION = 3

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
#: Hard cap on the nodes one change-map view ships. A hub edit can reach hundreds
#: of files; past a few hundred the WebGL force layout and render stall and the
#: picture is unreadable anyway. Seeds are always kept, then blast nodes
#: nearest-hop first, then context; the drop is reported, never silent.
MAX_MAP_NODES = 250
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
    base = _import_base(module, from_path, root, spec=spec)
    if base is None:
        return None
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


def _import_base(module: str, from_path: str, root: Path, *, spec: LanguageSpec) -> Path | None:
    """The path an import names, before any suffix or index file is tried.

    None for an import that can never be in-project: a bare JS/TS specifier is a
    `node_modules` package, and a relative one may climb out of the root.
    """
    from_dir = (root / from_path).parent
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
            return base
        return root / Path(module.replace(".", "/"))

    # JS/TS: only path-like specifiers ("./x", "../x", "/x") are in-project;
    # bare "pkg" specifiers are node_modules and resolve to no edge.
    if module.startswith("."):
        base = (from_dir / module).resolve()
        try:
            base.relative_to(root.resolve())
        except ValueError:
            return None
        return base
    return None


def _import_tail(module: str, from_path: str, root: Path, *, spec: LanguageSpec) -> str | None:
    """The last component of the path an import names, casefolded, or None.

    Every file an import could resolve to is named by that component: its stem,
    its whole name, or - for a package index - its directory's name
    (`_resolution_keys`). That is what lets a file that appeared find the imports
    it may now answer without re-resolving every import in the Project. The
    common shapes are read straight off the string; anything else asks
    `_import_base`, which is the authority.
    """
    if spec.name == "python":
        rest = module.lstrip(".")
        if rest:
            return rest.rsplit(".", 1)[-1].casefold()
    elif module.startswith("."):
        last = module.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        if last not in ("", ".", ".."):
            return last.casefold()
    else:
        return None
    base = _import_base(module, from_path, root, spec=spec)
    return base.name.casefold() if base is not None else None


def _resolution_keys(identity: str, root: Path) -> set[str]:
    """The import tails (`_import_tail`) that could resolve to this file."""
    name = identity.rsplit("/", 1)[-1].casefold()
    keys = {name, Path(name).stem}
    if name.endswith(".d.ts"):
        keys.add(name[: -len(".d.ts")])
    spec = spec_for_path(name)
    if spec is not None and name in spec.index_basenames:
        # A package index answers for its directory, which at the top level is the
        # Project root itself.
        parent = identity.rsplit("/", 2)[-2] if "/" in identity else root.resolve().name
        keys.add(parent.casefold())
    return keys


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
    known_symbols: Mapping[str, set[str]] | None = None,
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

#: A stamp this close to the moment it was taken cannot tell a write landing later
#: in the same timestamp tick from no write at all, so it is recorded as unknown and
#: its file is verified by content at the next sync - git's "racily clean" rule.
#: Generous on purpose: a file timestamp's real granularity is the filesystem's
#: (two seconds on FAT) and the writer's clock tick, not the 100 ns NTFS stores, and
#: the price of a racy stamp is one extra read and hash, never a parse.
RACY_STAMP_NS = 3_000_000_000


@dataclass(frozen=True, slots=True)
class SourceStamp:
    """What a file's stat said when it was read: modification time and size.

    ``mtime_ns`` is None when the stamp was too fresh to trust (``RACY_STAMP_NS``),
    which makes the next sync read the file and compare its content hash.
    """

    mtime_ns: int | None
    size: int


@dataclass(frozen=True, slots=True)
class FileStamp:
    """A graph row's content hash and the stamp of the bytes it was parsed from."""

    content_hash: str | None
    mtime_ns: int | None
    size: int | None

    def matches(self, stamp: SourceStamp) -> bool:
        """True when a fresh stat says the file is the one this row was parsed from.

        An unknown recorded time never matches, so a legacy row or a racy stamp is
        verified by content rather than trusted.
        """
        return (
            self.mtime_ns is not None
            and stamp.mtime_ns is not None
            and self.mtime_ns == stamp.mtime_ns
            and self.size == stamp.size
        )


def stamp_of(st: os.stat_result, now_ns: int) -> SourceStamp:
    """The stamp to record for a stat taken at ``now_ns``."""
    mtime_ns = st.st_mtime_ns
    if now_ns - mtime_ns < RACY_STAMP_NS:
        return SourceStamp(None, st.st_size)
    return SourceStamp(mtime_ns, st.st_size)


#: Bound on the parameters one ``IN (...)`` list carries, well under SQLite's
#: default host-parameter limit.
_IN_CHUNK = 500


def _chunks[V](items: Sequence[V], size: int) -> Iterator[Sequence[V]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


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
  source_mtime_ns INTEGER,
  source_size INTEGER,
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
CREATE TABLE IF NOT EXISTS code_graph_index_state (
  project_id TEXT PRIMARY KEY,
  head TEXT,
  indexed_at REAL NOT NULL,
  file_count INTEGER NOT NULL DEFAULT 0
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
        self._executor = AccountedThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mux-codegraph-db"
        )
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
            columns = {
                str(row["name"]) for row in self._db.execute("PRAGMA table_info(code_graph_files)")
            }
            # A version-2 graph keeps every row; its stamps start unknown, so the
            # first sync reads each file once and compares hashes rather than parsing.
            for column in ("source_mtime_ns", "source_size"):
                if column not in columns:
                    self._db.execute(f"ALTER TABLE code_graph_files ADD COLUMN {column} INTEGER")
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

    def _leaf_sets_sync(self, project_id: str, paths: Iterable[str]) -> dict[str, set[str]]:
        wanted = sorted(set(paths))
        out: dict[str, set[str]] = {path: set() for path in wanted}
        for chunk in _chunks(wanted, _IN_CHUNK):
            marks = ",".join("?" * len(chunk))
            for row in self._db.execute(
                "SELECT path, name FROM code_graph_symbols "
                f"WHERE project_id=? AND path IN ({marks})",
                (project_id, *chunk),
            ):
                out[str(row["path"])].add(str(row["name"]).split(".")[-1])
        return out

    async def leaf_sets(self, project_id: str, paths: Iterable[str]) -> dict[str, set[str]]:
        """Each path's symbol leaf names (empty for a file the graph does not hold)."""
        wanted = list(paths)
        return await self._run(lambda: self._leaf_sets_sync(project_id, wanted))

    async def file_stamps(self, project_id: str) -> dict[str, FileStamp]:
        """Every file this Project's graph holds, with the stamp it was parsed at."""

        def op() -> dict[str, FileStamp]:
            rows = self._db.execute(
                "SELECT path, content_hash, source_mtime_ns, source_size "
                "FROM code_graph_files WHERE project_id=?",
                (project_id,),
            ).fetchall()
            return {
                str(row["path"]): FileStamp(
                    row["content_hash"], row["source_mtime_ns"], row["source_size"]
                )
                for row in rows
            }

        return await self._run(op)

    async def record_stamps(
        self, project_id: str, stamps: Sequence[tuple[str, SourceStamp]]
    ) -> None:
        """Re-stamp files whose bytes were read and found to be the ones parsed."""
        if not stamps:
            return

        def op() -> None:
            self._db.executemany(
                "UPDATE code_graph_files SET source_mtime_ns=?, source_size=? "
                "WHERE project_id=? AND path=?",
                [(stamp.mtime_ns, stamp.size, project_id, path) for path, stamp in stamps],
            )
            self._db.commit()

        await self._run(op)

    def _replace_file_sync(
        self,
        project_id: str,
        parsed: ParsedFile,
        edges: Sequence[ResolvedEdge],
        stamp: SourceStamp | None = None,
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
            "truncated,parsed_at,source_mtime_ns,source_size) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(project_id,path) DO UPDATE SET lang=excluded.lang,"
            "content_hash=excluded.content_hash,symbol_count=excluded.symbol_count,"
            "truncated=excluded.truncated,parsed_at=excluded.parsed_at,"
            "source_mtime_ns=excluded.source_mtime_ns,source_size=excluded.source_size",
            (
                project_id,
                parsed.path,
                parsed.lang,
                parsed.content_hash,
                len(parsed.symbols),
                1 if parsed.truncated else 0,
                time.time(),
                stamp.mtime_ns if stamp is not None else None,
                stamp.size if stamp is not None else None,
            ),
        )
        # One file can legitimately define the same qualname twice — @overload
        # stubs, a TYPE_CHECKING branch, a property/setter pair. The symbols table
        # keys on (project_id, path, name), so dedupe by name (first wins) before
        # the insert; a raw executemany would raise UNIQUE and abort the whole
        # index pass, which once left the backend graph empty in production.
        # INSERT OR IGNORE is the belt-and-suspenders backstop.
        seen_symbols: set[str] = set()
        symbol_rows: list[tuple[Any, ...]] = []
        for s in parsed.symbols:
            if s.name in seen_symbols:
                continue
            seen_symbols.add(s.name)
            symbol_rows.append(
                (project_id, parsed.path, s.name, s.kind, s.start_line, s.end_line)
            )
        db.executemany(
            "INSERT OR IGNORE INTO code_graph_symbols(project_id,path,name,kind,"
            "start_line,end_line) VALUES(?,?,?,?,?,?)",
            symbol_rows,
        )
        # Collapse call-site duplicates before storing: a caller that calls the
        # same target twenty times produces twenty identical edges save the line,
        # and only the first is a distinct relationship. Deduping here keeps the
        # edge table ~14x smaller on a real repo (measured 25k -> ~2k rows), which
        # is what keeps the reverse-dependency walk and every edge read fast. One
        # representative line is retained per relationship.
        seen_edges: set[tuple[Any, ...]] = set()
        edge_rows: list[tuple[Any, ...]] = []
        for e in edges:
            key = (e.kind, e.src_symbol, e.dst_path, e.dst_symbol, e.dst_name)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edge_rows.append(
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
            )
            if len(edge_rows) >= MAX_EDGES_PER_FILE:
                break
        db.executemany(
            "INSERT INTO code_graph_edges(project_id,kind,src_path,src_symbol,dst_path,"
            "dst_symbol,dst_name,resolved,src_line) VALUES(?,?,?,?,?,?,?,?,?)",
            edge_rows,
        )
        db.commit()

    async def replace_file(
        self,
        project_id: str,
        parsed: ParsedFile,
        edges: Sequence[ResolvedEdge],
        stamp: SourceStamp | None = None,
    ) -> None:
        await self._run(lambda: self._replace_file_sync(project_id, parsed, edges, stamp))

    async def remove_file(self, project_id: str, path: str) -> bool:
        """Drop a file and its edges from the graph. Returns True if it existed."""
        return await self.remove_files(project_id, [path]) > 0

    async def remove_files(self, project_id: str, paths: Sequence[str]) -> int:
        """Drop files and the edges they own. Returns how many the graph held."""
        if not paths:
            return 0

        def op() -> int:
            removed = 0
            for path in paths:
                cur = self._db.execute(
                    "DELETE FROM code_graph_files WHERE project_id=? AND path=?",
                    (project_id, path),
                )
                removed += max(cur.rowcount, 0)
                self._db.execute(
                    "DELETE FROM code_graph_edges WHERE project_id=? AND src_path=?",
                    (project_id, path),
                )
                self._db.execute(
                    "DELETE FROM code_graph_symbols WHERE project_id=? AND path=?",
                    (project_id, path),
                )
            self._db.commit()
            return removed

        return await self._run(op)

    async def import_edges(
        self,
        project_id: str,
        *,
        targets: Iterable[str] | None = None,
        naming: Iterable[str] | None = None,
    ) -> list[tuple[int, str, str, str | None]]:
        """``(id, src_path, module, dst_path)`` for import edges.

        Every import edge in the Project; or only those currently resolved to one
        of ``targets``; or only those whose module string could name a file with
        one of the ``naming`` tails (`_import_tail`): it contains one, or it ends
        in a dot or a separator (`.`, `..`, `./`), whose tail is a directory's
        name the string does not spell.
        The ``naming`` filter is a superset by construction for ASCII tails - SQL
        ``LIKE`` folds ASCII case only - so it is applied only when every tail is
        ASCII, and the caller still checks each row exactly.
        """
        wanted = None if targets is None else sorted(set(targets))
        tails = None if naming is None else sorted(set(naming))
        if tails is not None and not all(tail.isascii() for tail in tails):
            tails = None

        def op() -> list[tuple[int, str, str, str | None]]:
            select = (
                "SELECT id, src_path, dst_name, dst_path FROM code_graph_edges "
                "WHERE project_id=? AND kind='imports'"
            )
            rows: list[sqlite3.Row] = []
            if wanted is not None:
                for chunk in _chunks(wanted, _IN_CHUNK):
                    marks = ",".join("?" * len(chunk))
                    rows.extend(
                        self._db.execute(
                            f"{select} AND dst_path IN ({marks})", (project_id, *chunk)
                        )
                    )
            elif tails is not None:
                for chunk in _chunks(tails, _IN_CHUNK):
                    likes = " OR ".join(["dst_name LIKE ? ESCAPE '\\'"] * len(chunk))
                    rows.extend(
                        self._db.execute(
                            f"{select} AND ({likes} OR dst_name LIKE '%.' "
                            "OR dst_name LIKE '%/' OR dst_name LIKE '%\\')",
                            (project_id, *(like_contains(tail) for tail in chunk)),
                        )
                    )
            else:
                rows = list(self._db.execute(select, (project_id,)))
            unique = {int(row["id"]): row for row in rows}
            return [
                (edge_id, str(row["src_path"]), str(row["dst_name"]), row["dst_path"])
                for edge_id, row in unique.items()
            ]

        return await self._run(op)

    async def relink(
        self, project_id: str, importers: Sequence[str], import_targets: Mapping[int, str | None]
    ) -> int:
        """Re-resolve the edges of files that were not re-parsed.

        ``import_targets`` carries the new resolution of each import edge, by id,
        that the filesystem now answers differently. Every other edge of each
        importer is re-resolved the way ``resolve_edges`` resolves a fresh parse:
        a name the file defines stays local, and any other name goes to the first
        imported module that defines it, in import order, or to nothing.
        Returns how many edge rows changed.
        """

        def op() -> int:
            changed = 0
            for src in importers:
                changed += self._relink_file_sync(project_id, src, import_targets)
            self._db.commit()
            return changed

        return await self._run(op)

    def _relink_file_sync(
        self, project_id: str, src: str, import_targets: Mapping[int, str | None]
    ) -> int:
        db = self._db
        changed = 0
        modules: list[str] = []
        # Edge ids follow insertion order, which is the order the parse produced
        # the imports in - the order `resolve_edges` searches them.
        for row in db.execute(
            "SELECT id, dst_path FROM code_graph_edges "
            "WHERE project_id=? AND src_path=? AND kind='imports' ORDER BY id",
            (project_id, src),
        ).fetchall():
            dst = row["dst_path"]
            edge_id = int(row["id"])
            if edge_id in import_targets and import_targets[edge_id] != dst:
                dst = import_targets[edge_id]
                db.execute(
                    "UPDATE code_graph_edges SET dst_path=?, resolved=? WHERE id=?",
                    (dst, 1 if dst is not None else 0, edge_id),
                )
                changed += 1
            if dst is not None:
                modules.append(str(dst))
        local = self._leaf_sets_sync(project_id, [src])[src]
        leaves = self._leaf_sets_sync(project_id, modules)
        updates: list[tuple[str | None, str | None, int, int]] = []
        for row in db.execute(
            "SELECT id, dst_name, dst_path, dst_symbol, resolved FROM code_graph_edges "
            "WHERE project_id=? AND src_path=? AND kind IN (?, ?)",
            (project_id, src, EDGE_CALLS, EDGE_REFERENCES),
        ).fetchall():
            name = str(row["dst_name"])
            if name in local:
                continue  # resolved to this file's own definition, which did not change
            target = next((module for module in modules if name in leaves[module]), None)
            wanted = (target, name if target is not None else None, 1 if target else 0)
            if wanted != (row["dst_path"], row["dst_symbol"], int(row["resolved"])):
                updates.append((*wanted, int(row["id"])))
        if updates:
            db.executemany(
                "UPDATE code_graph_edges SET dst_path=?, dst_symbol=?, resolved=? WHERE id=?",
                updates,
            )
        return changed + len(updates)

    async def clear_project(self, project_id: str) -> None:
        """Drop the whole graph for a Project. Used before a fresh seed so stale
        rows (a file since deleted, or a worktree copy a prior build wrongly
        indexed) do not survive as phantom nodes and edges."""

        def op() -> None:
            for table in (
                "code_graph_edges",
                "code_graph_symbols",
                "code_graph_files",
                # A graph with no rows reflects no commit. Leaving the claim behind
                # would let the next pass conclude "already current" over nothing.
                "code_graph_index_state",
            ):
                self._db.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
            self._db.commit()

        await self._run(op)

    async def index_state(self, project_id: str) -> tuple[str | None, float] | None:
        """The commit this Project's graph was built from, and when — or None.

        None means the graph has never been seeded in any process; a row with a
        `None` head means it was seeded from a directory that is not a git
        checkout, which is a different fact and must not read as "never indexed".
        """

        def op() -> tuple[str | None, float] | None:
            row = self._db.execute(
                "SELECT head, indexed_at FROM code_graph_index_state WHERE project_id=?",
                (project_id,),
            ).fetchone()
            return (row["head"], float(row["indexed_at"])) if row else None

        return await self._run(op)

    async def record_index_state(
        self, project_id: str, *, head: str | None, file_count: int
    ) -> None:
        """Record which commit the graph now reflects."""

        def op() -> None:
            self._db.execute(
                "INSERT INTO code_graph_index_state(project_id,head,indexed_at,file_count) "
                "VALUES(?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
                "head=excluded.head, indexed_at=excluded.indexed_at, "
                "file_count=excluded.file_count",
                (project_id, head, time.time(), int(file_count)),
            )
            self._db.commit()

        await self._run(op)

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
            # `dep` collapses the raw edge rows to distinct (src, dst, kind) pairs
            # *before* the recursion. This is load-bearing, not cosmetic: the edge
            # table carries one row per call site, so a file that calls another 20
            # times is 20 identical (src, dst) edges, and recursing over the raw
            # table made a 3-hop reverse walk explode from ~2k distinct pairs to
            # tens of thousands of duplicate traversals (measured 58s for one hub;
            # 35ms deduped). Every reverse-dependency read goes through here.
            rows = self._db.execute(
                """
                WITH RECURSIVE dep(src, dst, kind) AS (
                    SELECT DISTINCT src_path, dst_path, kind FROM code_graph_edges
                      WHERE project_id=:pid AND kind IN ('imports','calls')
                        AND dst_path IS NOT NULL
                ),
                reach(path, hop, via) AS (
                    SELECT src, 1, kind FROM dep WHERE dst=:target
                    UNION
                    SELECT d.src, r.hop + 1, d.kind
                      FROM dep d JOIN reach r ON d.dst = r.path
                      WHERE r.hop < :hops AND d.src <> :target
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

        Admission first, orphan-hood second. A path the graph could never have
        drawn an inbound edge *to* is not evidence of dead code, and reporting it
        as such is a false positive by construction: an agent's scratchpad script
        outside the Project root, a hashed bundle asset, a worktree copy, or a
        test module a runner discovers rather than imports. Every one of the 76
        `dead-code` findings in a measured 24-hour window was one of those four
        (2026-08-21), and their volume also starved the two structural rules that
        were accurate, because the per-pass budget is spent in path order.

        The remaining candidates are still only candidates: a dynamic caller the
        graph cannot see is exactly what `is_dead_code_candidate` cannot rule out,
        which is why the annotation states it.

        Guarded against entry points is still the *caller's* job.
        """

        def op() -> list[dict[str, Any]]:
            cursor = self._db.execute(
                """
                SELECT f.path, f.lang, f.symbol_count FROM code_graph_files f
                WHERE f.project_id=:pid AND f.symbol_count > 0
                  AND NOT EXISTS (
                    SELECT 1 FROM code_graph_edges e
                    WHERE e.project_id=:pid AND e.dst_path=f.path
                      AND e.kind IN ('imports','calls') AND e.src_path <> f.path)
                ORDER BY f.path
                """,
                {"pid": project_id},
            )
            # Admitted rows are counted against the limit, never raw ones: a LIMIT
            # applied before the predicate would spend the whole budget on
            # scratchpad paths and report "no other orphans" as if it had looked.
            out: list[dict[str, Any]] = []
            for row in cursor:
                if not is_dead_code_candidate(row["path"]):
                    continue
                out.append(dict(row))
                if len(out) >= MAX_RESULT_LIMIT:
                    break
            return out

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

    async def known_files(self, project_id: str, paths: Iterable[str]) -> set[str]:
        """Which of these paths the graph has actually parsed.

        A seed the graph has never seen is not an error and not a reason to hide
        it: a file created on a branch exists only in that worktree, and the
        canonical index is built from the primary checkout. It simply has no
        structure yet, which is a different statement from "nothing depends on
        it" and has to be drawn as one.
        """
        wanted = [p for p in dict.fromkeys(paths) if p]
        if not wanted:
            return set()

        def op() -> set[str]:
            found: set[str] = set()
            # Chunked against SQLite's variable limit: a branch-scoped map can seed
            # far more paths than a single run's writes ever would.
            for start in range(0, len(wanted), 400):
                block = wanted[start : start + 400]
                rows = self._db.execute(
                    "SELECT path FROM code_graph_files WHERE project_id=? "
                    f"AND path IN ({','.join('?' * len(block))})",
                    (project_id, *block),
                ).fetchall()
                found.update(str(row["path"]) for row in rows)
            return found

        return await self._run(op)

    async def subgraph(
        self, project_id: str, seed_paths: Iterable[str], *, hops: int = 1
    ) -> dict[str, Any]:
        """The bounded subgraph a change-map view needs: the seed files, their
        reverse dependents (blast radius), one hop of forward imports (context),
        and every edge among that node set. Server-side and hard-capped at
        ``MAX_MAP_NODES`` so a god-node seed cannot ship thousands of nodes that
        would stall the WebGL layout and render; seeds are always kept, then blast
        nodes nearest-hop first, then context. ``truncated`` and ``totals`` report
        what the cap dropped so the reader is never silently shown a partial map."""
        seeds = [s for s in dict.fromkeys(seed_paths) if s]

        # Seeds are drawn whether or not the graph knows them, and are told apart:
        # a branch's brand-new file has no node here because the canonical index is
        # built from the primary checkout, and reading its empty neighbourhood as
        # "nothing depends on this" would be wrong.
        indexed = await self.known_files(project_id, seeds)
        nodes: dict[str, dict[str, Any]] = {}
        for seed in seeds:
            entry: dict[str, Any] = {"path": seed, "role": "seed"}
            if seed not in indexed:
                entry["indexed"] = False
            nodes.setdefault(seed, entry)

        # Blast radius: the nearest hop each dependent sits at (a file reached from
        # two seeds keeps the smaller hop), collected fully so the totals are honest
        # even when the cap drops the far tail.
        blast_hop: dict[str, int] = {}
        for seed in seeds:
            for dep in await self.reverse_dependents(project_id, seed, hops=hops):
                if dep.path in nodes:
                    continue
                prev = blast_hop.get(dep.path)
                if prev is None or dep.hop < prev:
                    blast_hop[dep.path] = dep.hop
        blast_total = len(blast_hop)

        # Forward context: what the seeds import, minus anything already a seed or a
        # blast node.
        context: set[str] = set()
        for seed in seeds:
            for imp in await self.imports_of(project_id, seed):
                if imp not in nodes and imp not in blast_hop:
                    context.add(imp)
        context_total = len(context)

        truncated = False
        budget = MAX_MAP_NODES - len(nodes)
        for path, hop in sorted(blast_hop.items(), key=lambda kv: (kv[1], kv[0])):
            if budget <= 0:
                truncated = True
                break
            nodes[path] = {"path": path, "role": "blast", "hop": hop}
            budget -= 1
        for path in sorted(context):
            if budget <= 0:
                truncated = True
                break
            nodes[path] = {"path": path, "role": "context"}
            budget -= 1

        edges = await self._edges_among(project_id, set(nodes))
        result: dict[str, Any] = {"nodes": list(nodes.values()), "edges": edges}
        if truncated:
            result["truncated"] = True
            result["totals"] = {
                "shown": len(nodes),
                "blast": blast_total,
                "context": context_total,
            }
        return result

    async def _edges_among(self, project_id: str, node_set: set[str]) -> list[dict[str, Any]]:
        """Edges whose endpoints are both in the (bounded) node set.

        Filters in SQL against the node set rather than scanning every project edge
        and filtering in Python: the map's node set is capped at ``MAX_MAP_NODES``,
        so two bounded ``IN`` clauses stay well under SQLite's parameter limit and
        ride the src/dst indexes, instead of loading a repository's whole edge table
        (tens of thousands of rows) on every view."""
        paths = list(node_set)
        if not paths:
            return []

        def op() -> list[dict[str, Any]]:
            placeholders = ",".join("?" * len(paths))
            rows = self._db.execute(
                f"SELECT DISTINCT src_path, dst_path, kind FROM code_graph_edges "
                f"WHERE project_id=? AND kind IN ('imports','calls') "
                f"AND src_path IN ({placeholders}) AND dst_path IN ({placeholders})",
                (project_id, *paths, *paths),
            ).fetchall()
            return [
                {"source": r["src_path"], "target": r["dst_path"], "kind": r["kind"]}
                for r in rows
            ]

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
    for entry in _iter_source_entries(project_root, limit=limit):
        yield Path(entry.path)


def _iter_source_entries(project_root: str, *, limit: int) -> Iterator[os.DirEntry[str]]:
    """The directory entries ``iter_source_files`` names, in the same order.

    The walk is ``os.walk``'s, top-down and depth-first with each directory's
    files before its subdirectories, because the order decides which files a tree
    past ``limit`` keeps and the seed and the sync must keep the same ones. It is
    written out rather than borrowed so the sync gets each file's ``DirEntry``:
    on Windows its ``stat()`` comes from the directory listing and costs no call
    per file.
    """
    count = 0
    stack = [str(Path(project_root))]
    while stack:
        top = stack.pop()
        files: list[os.DirEntry[str]] = []
        subdirs: list[os.DirEntry[str]] = []
        try:
            with os.scandir(top) as scan:
                for entry in scan:
                    try:
                        is_dir = entry.is_dir()
                    except OSError:
                        is_dir = False
                    (subdirs if is_dir else files).append(entry)
        except OSError:
            continue  # os.walk skips a directory it cannot list; so does the seed
        for entry in files:
            if spec_for_path(entry.name) is None:
                continue
            yield entry
            count += 1
            if count >= limit:
                return
        for entry in reversed(subdirs):
            name = entry.name
            if name in _SKIP_DIRS or name.startswith(".") or entry.is_symlink():
                continue
            stack.append(entry.path)


def _abspath(project_root: str, target: str) -> Path:
    if os.path.isabs(target):
        return Path(target)
    return Path(project_root) / target


def is_indexable_path(path: str) -> bool:
    """Whether a normalized path belongs in the graph.

    A supported source file that is not under a skipped or hidden directory. The
    seed walk (``iter_source_files``) prunes these dirs already; the incremental
    maintenance path must apply the identical rule, or a session that edits a
    nested worktree (``.claude/worktrees/…``), a dependency, or generated output
    leaks near-duplicate copies of the whole repository into the Project's
    canonical graph — bloating it without bound and polluting every blast radius
    with worktree copies of the real callers.
    """
    if spec_for_path(path) is None:
        return False
    segments = path.replace("\\", "/").split("/")
    for segment in segments[:-1]:
        if not segment or segment in (".", ".."):
            continue
        if segment in _SKIP_DIRS or segment.startswith("."):
            return False
    return True


#: A Windows drive-qualified path. Identities arrive casefolded from
#: ``normalize_target``, but the check is case-insensitive so a raw ``C:/…`` from
#: any other caller is classified the same way rather than read as relative.
_DRIVE_PREFIX = re.compile(r"^[a-z]:(?:/|$)", re.IGNORECASE)


def is_project_relative(path: str) -> bool:
    """Whether a normalized identity names a file *inside* its Project root.

    ``normalize_target`` strips the root only when the path sits under it, so an
    edit outside the checkout keeps its absolute form. That distinction is load
    bearing twice over: the graph only ever indexes the project tree, so an
    outside path can never acquire an edge and is drawn as a permanently isolated
    dot; and the Project file endpoints refuse to serve anything outside the root,
    so it can never be opened either.

    Host-independent by construction. A POSIX daemon must still recognise the
    ``c:/…`` identities a Windows-recorded fact carries (and vice versa), so this
    tests both absolute spellings rather than deferring to ``os.path.isabs``,
    which answers only for the host it runs on.
    """
    if not path:
        return False
    text = path.replace("\\", "/")
    if text.startswith("/") or _DRIVE_PREFIX.match(text):
        return False
    return not any(segment == ".." for segment in text.split("/"))


#: Directories holding files that are *served or generated* rather than imported:
#: bundler output, the static payload a browser fetches by URL. Every
#: `npm run build` mints new hashed files under `assets/`, so each one becomes a
#: permanent new "dead-code candidate" that no edit will ever resolve, and a
#: service worker under `static/` is fetched by URL and imported by nothing on
#: purpose. `_SKIP_DIRS` already covers `dist`/`build`/`node_modules` through
#: `is_indexable_path`; these are the ones it does not.
_GENERATED_DIR_SEGMENTS = ("assets", "static", "public", "generated", "__generated__")

#: A test module or renderer harness. Discovered by a runner (pytest collection,
#: Playwright's spec glob) rather than imported, so "nothing references it" is how
#: it is *supposed* to look, and the graph is structurally unable to say otherwise.
_TEST_DIR_SEGMENTS = ("tests", "test", "__tests__", "spec", "e2e")
_TEST_NAME_MARKERS = (".spec.", ".test.", "_test.", "test_")
_TEST_NAME_SUFFIXES = ("harness.tsx", "harness.ts", "conftest.py")


def is_test_path(path: str) -> bool:
    """Whether a normalized path is a test or a test harness."""
    text = path.replace("\\", "/").casefold()
    segments = text.split("/")
    name = segments[-1]
    if any(segment in _TEST_DIR_SEGMENTS for segment in segments[:-1]):
        return True
    if name.endswith(_TEST_NAME_SUFFIXES):
        return True
    return any(marker in name for marker in _TEST_NAME_MARKERS)


def is_generated_output(path: str) -> bool:
    """Whether a normalized path is served or generated rather than imported."""
    segments = path.replace("\\", "/").casefold().split("/")
    return any(segment in _GENERATED_DIR_SEGMENTS for segment in segments[:-1])


def is_dead_code_candidate(path: str) -> bool:
    """Whether "nothing imports this file" is evidence about *this file*.

    The four exclusions are not taste. A path outside the Project root or under a
    skipped directory can never acquire an inbound edge, so its isolation is a
    property of the graph's boundary; generated output is rewritten by a build
    rather than referenced; and a test file is reached by a runner the graph does
    not model. In each case the absence of an edge says nothing about whether the
    code is dead.
    """
    return (
        is_project_relative(path)
        and is_indexable_path(path)
        and not is_generated_output(path)
        and not is_test_path(path)
    )


def resolve_display_paths(project_root: str, identities: Iterable[str]) -> dict[str, str]:
    """True-cased, project-relative paths for casefolded graph identities.

    Every path identity in the graph is casefolded (``normalize_target``), which
    is what makes cross-tool comparison work and what makes an identity unusable
    as a *filesystem* path: ``frontend/src/ChangeMapPane.tsx`` is stored as
    ``frontend/src/changemappane.tsx``. A case-sensitive host cannot open that at
    all, and a case-insensitive one opens it under a second, colliding pane
    identity — two editors and two save revisions on one file. Recovering the real
    casing is therefore a correctness requirement, not a cosmetic one.

    Matching is done by listing each directory rather than by ``stat``: on Windows
    a wrong-cased path *succeeds*, so a stat fast path would silently keep the
    wrong casing and defeat the whole purpose. Listings are memoised per call, so
    a whole change map costs one ``scandir`` per distinct directory.

    Identities with no matching file are simply absent from the result — a file
    the session wrote and then deleted has no path to offer.

    Blocking filesystem work: call it from a thread.
    """
    listings: dict[str, dict[str, tuple[str, bool]]] = {}

    def entries(directory: Path) -> dict[str, tuple[str, bool]]:
        key = str(directory)
        cached = listings.get(key)
        if cached is None:
            cached = {}
            try:
                with os.scandir(directory) as scan:
                    for entry in scan:
                        try:
                            is_dir = entry.is_dir()
                        except OSError:
                            is_dir = False
                        cached[entry.name.casefold()] = (entry.name, is_dir)
            except OSError:
                pass
            listings[key] = cached
        return cached

    root = Path(project_root)
    resolved: dict[str, str] = {}
    for identity in identities:
        if identity in resolved or not is_project_relative(identity):
            continue
        segments = [s for s in identity.replace("\\", "/").split("/") if s and s != "."]
        if not segments:
            continue
        current = root
        real: list[str] = []
        for index, segment in enumerate(segments):
            match = entries(current).get(segment.casefold())
            # The last segment must be a file, every earlier one a directory —
            # otherwise a directory named like the file would resolve happily and
            # produce a path the file endpoint then rejects.
            if match is None or match[1] != (index < len(segments) - 1):
                real = []
                break
            real.append(match[0])
            current = current / match[0]
        if real:
            resolved[identity] = "/".join(real)
    return resolved


@dataclass(frozen=True, slots=True)
class _Loaded:
    """One file read for upkeep.

    ``parsed`` is None when ``unchanged`` (the bytes hash to what the graph already
    holds, so only the stamp is news) and also when a changed file failed to parse,
    which leaves its row as it was and its stamp unrecorded.
    """

    identity: str
    stamp: SourceStamp
    unchanged: bool
    parsed: ParsedFile | None


def _load(
    path: str,
    project_root: str,
    identity: str,
    known_hash: str | None,
    stamp: SourceStamp | None,
) -> _Loaded | None:
    """Read a file, and parse it only when its bytes are not the ones the graph holds.

    The CPU-bound half of upkeep, run on a worker thread so a large index never
    blocks the event loop. The stamp is taken *before* the read (by the walk, or
    here when there was no walk), so a write landing in between leaves a stamp
    older than the bytes - which the next sync reads again - and never a newer
    one, which would hide that write for good.
    """
    try:
        if stamp is None:
            stamp = stamp_of(os.stat(path), time.time_ns())
        source = Path(path).read_bytes()
    except OSError:
        return None
    if known_hash is not None and content_hash(source) == known_hash:
        return _Loaded(identity, stamp, True, None)
    return _Loaded(identity, stamp, False, parse_source(path, source, project_root=project_root))


def _scan_tree(project_root: str, limit: int) -> list[tuple[str, str, SourceStamp | None]]:
    """``(path, identity, stamp)`` for each file the seed indexes, in walk order.

    The stamp is None where the stat failed, which makes the read stat again. Two
    names that fold to one identity (only possible on a case-sensitive host) keep
    the later one, which is the row a seed's last write would have left.
    """
    now_ns = time.time_ns()
    found: dict[str, tuple[str, str, SourceStamp | None]] = {}
    for entry in _iter_source_entries(project_root, limit=limit):
        identity = normalize_target(entry.path, project_root)
        if identity is None:
            continue
        try:
            stamp: SourceStamp | None = stamp_of(entry.stat(), now_ns)
        except OSError:
            stamp = None
        found.pop(identity, None)
        found[identity] = (entry.path, identity, stamp)
    return list(found.values())


def _leaves(parsed: ParsedFile) -> set[str]:
    return {symbol.name.split(".")[-1] for symbol in parsed.symbols}


#: Files a trunk move may bring in before a whole-tree re-seed is the cheaper
#: answer. One file's delta parse costs a read, a tree-sitter parse and one
#: import-resolution round-trip per import, which is roughly what the seed pays
#: per file — so past a few hundred there is nothing to win by going one at a
#: time, and the seed additionally drops rows for files that vanished outside the
#: delta's view. Below it, a landing touching a dozen files costs a dozen parses
#: instead of six thousand.
MAX_TRUNK_DELTA_FILES = 400

#: What `git rev-parse HEAD` looks like when it answered with a commit. An
#: unborn branch answers with the literal ref name and a nonzero status; this is
#: what keeps that from being stored as a commit id.
_COMMIT_OID = re.compile(r"^[0-9a-fA-F]{40,64}$")


async def _read_git(project_root: str, *args: str) -> tuple[int, str]:
    """One bounded, read-only Git query, through the monitor's own runner.

    Imported at call time: `git_monitor` imports `session`, and `code_graph` is
    imported *from* the consumer that `session` reaches, so a module-level import
    would close a cycle. Borrowed rather than reimplemented because
    `--no-optional-locks` is not optional here - a graph refresh that took
    `.git/index.lock` would contend with the very agents whose landing triggered
    it (`git_monitor.read_git`).
    """
    from .git_monitor import read_git

    return await read_git(project_root, *args)


async def repo_head(project_root: str) -> str | None:
    """The commit `project_root` is on, or None when it is not a git checkout.

    This is the whole trunk-movement signal. It is asked once per turn boundary
    per Project, which is a ~15ms read-only query beside a turn's transcript
    parse, and it is deliberately not memoised: a cache with a TTL would answer
    "unchanged" for a window right after the merge that changed it, which is the
    exact moment the answer matters.
    """
    code, output = await _read_git(project_root, "rev-parse", "HEAD")
    head = output.strip()
    if code != 0 or not _COMMIT_OID.match(head):
        return None
    return head


async def changed_between(project_root: str, previous: str, head: str) -> list[str] | None:
    """Repository-relative paths differing between two commits, or None.

    None means git could not answer - an unknown commit after a history rewrite, a
    pruned object, a checkout that is no longer a repository - and the caller must
    treat it as "unknown", never as "nothing changed". The distinction is the whole
    point: an empty list is a merge that touched nothing this graph cares about,
    while None is the case that has to fall back to a full re-seed.

    `--no-renames` so a rename yields both paths: the old one has to leave the
    graph and the new one has to enter it, and rename detection reports only the
    new. `-z` because git quotes and escapes non-ASCII paths in its default
    output, and a mangled path resolves to no file at all. `--relative` because
    git answers in repository-relative paths while the graph's identities are
    relative to the Project root, and the two differ for a Project registered at a
    subdirectory - where it also drops the paths outside that root, which could
    never have been in the graph anyway.
    """
    code, output = await _read_git(
        project_root,
        "diff",
        "--name-only",
        "--no-renames",
        "--relative",
        "-z",
        f"{previous}..{head}",
    )
    if code != 0:
        return None
    return [path for path in output.split("\0") if path]


async def refresh_indexed_project(
    store: CodeGraphStore,
    project_id: str,
    project_root: str,
    *,
    limit: int = MAX_INDEX_FILES,
    delta_limit: int = MAX_TRUNK_DELTA_FILES,
) -> tuple[str, int]:
    """Bring an already-seeded Project's graph up to the checkout's commit.

    The gap this closes: the seed runs once per process and incremental
    maintenance only ever sees files *this daemon's own sessions* wrote, so
    everything that arrives by `git merge` - which is every landing - was invisible
    to the graph until some session happened to edit it. After Wave 3 landed, three
    new modules were absent from `code_context` outright and `logsetup.py` answered
    with its pre-S7 symbols: a stale-but-plausible answer, which is worse than the
    empty one the tools already warn about.

    Cheap by construction, because a stale graph is not the only failure mode a
    reindex-per-request would have: one `rev-parse` when nothing moved, one `diff`
    plus one parse per changed file when it did, and a stat-based `sync_project`
    only when the delta is unusable or larger than going one file at a time is
    worth.

    Returns `(outcome, files)` where outcome is one of `unversioned` (not a git
    checkout), `unseeded` (no recorded state - the caller has not seeded it yet),
    `current` (HEAD has not moved), `delta`, or `sync`; `files` is how many were
    re-parsed.
    """
    head = await repo_head(project_root)
    if head is None:
        return ("unversioned", 0)
    state = await store.index_state(project_id)
    if state is None:
        return ("unseeded", 0)
    previous = state[0]
    if previous == head:
        return ("current", 0)
    paths = await changed_between(project_root, previous, head) if previous else None
    if paths is None or len(paths) > delta_limit:
        result = await sync_project(
            store,
            project_id,
            project_root,
            limit=limit,
            reason="delta_unavailable" if paths is None else f"delta_over_limit:{len(paths)}",
        )
        return ("sync", result.parsed)
    updated = await maintain_files(store, project_id, project_root, paths)
    # Recorded even when `updated` is zero: a merge of documentation alone changes
    # no node, and not recording it would re-run the same diff on every turn
    # boundary from here on.
    await store.record_index_state(project_id, head=head, file_count=updated)
    log.info(
        "code_graph_delta_applied project=%s previous_head=%s head=%s changed=%d updated=%d",
        project_id,
        previous,
        head,
        len(paths),
        updated,
    )
    return ("delta", updated)


async def index_project(
    store: CodeGraphStore, project_id: str, project_root: str, *, limit: int = MAX_INDEX_FILES
) -> int:
    """Bounded parse of a project's whole source tree, from nothing.

    The maintenance model is incremental (re-parse one file on each write), but a
    reverse-dependency query needs the *importers* in the graph too, and an
    importer a session never edited would otherwise be invisible. This seeds those
    edges; every later edit refreshes its own file. It is not the rejected
    full-rebuild-on-every-change watcher: it runs once per Project, ever, unless
    its graph is lost. The graph and each file's stamp persist, so a later process
    brings it up to the tree with `sync_project`, and `refresh_indexed_project`
    follows the trunk after that.
    """
    # Read before the walk, never after: a commit landing while this parses would
    # otherwise be recorded as indexed when its files were not, and that staleness
    # would be permanent, because nothing afterwards would ever see HEAD move.
    # Recording the older commit costs one delta pass and cannot lose a file.
    head = await repo_head(project_root)
    # A fresh seed is authoritative for the whole tree, so clear first: a prior
    # build's stale rows (a since-deleted file, or worktree copies the incremental
    # path leaked in before this rule existed) must not survive as phantom nodes.
    await store.clear_project(project_id)
    entries = await asyncio.to_thread(_scan_tree, project_root, limit)
    loaded: list[tuple[ParsedFile, SourceStamp]] = []
    # Read and parse each file off the event loop: tree-sitter parsing is CPU-bound
    # and a one-time index of a large tree would otherwise stall the daemon loop.
    for path, identity, stamp in entries:
        item = await asyncio.to_thread(_load, path, project_root, identity, None, stamp)
        if item is not None and item.parsed is not None:
            loaded.append((item.parsed, item.stamp))
    leaf_index = {parsed.path: _leaves(parsed) for parsed, _ in loaded}
    indexed = 0
    for chunk in _chunks(loaded, RESOLVE_CHUNK):
        # Resolution reads the filesystem for every import, so it runs off the
        # loop too.
        resolved = await asyncio.to_thread(_resolve_all, chunk, project_root, leaf_index)
        for (parsed, stamp), edges in zip(chunk, resolved, strict=True):
            try:
                await store.replace_file(project_id, parsed, edges, stamp)
                indexed += 1
            except Exception as exc:  # noqa: BLE001 - one bad file must not abort the seed
                log.warning("code-graph: skipped %s during index: %s", parsed.path, exc)
    await store.record_index_state(project_id, head=head, file_count=indexed)
    log.info(
        "code_graph_indexed project=%s files=%d head=%s",
        project_id,
        indexed,
        head or "",
    )
    return indexed


async def maintain_files(
    store: CodeGraphStore, project_id: str, project_root: str, paths: Iterable[str]
) -> int:
    """Re-parse each written file and replace its edges. A vanished file is
    removed from the graph rather than left stale. Returns the count re-parsed."""
    seen: list[str] = []
    loaded: list[_Loaded] = []
    removed: list[str] = []
    for raw in paths:
        identity = normalize_target(raw, project_root)
        if identity is None or identity in seen:
            continue
        seen.append(identity)
        if not is_indexable_path(identity):
            # A worktree copy or generated file the seed would never walk: keep it
            # out of the canonical graph, and remove any copy a prior build leaked.
            removed.append(identity)
            continue
        abspath = _abspath(project_root, raw)
        try:
            exists = await asyncio.to_thread(abspath.is_file)
        except OSError:
            exists = False
        if not exists:
            removed.append(identity)
            continue
        known_hash = await store.known_hash(project_id, identity)
        item = await asyncio.to_thread(
            _load, str(abspath), project_root, identity, known_hash, None
        )
        if item is not None:
            loaded.append(item)
    known = await store.known_files(project_id, seen)
    parsed, _ = await _apply(store, project_id, project_root, loaded, removed, known)
    return parsed


#: Files resolved per thread hop during a seed: enough to amortise the hop, few
#: enough that the store's writes interleave with other work.
RESOLVE_CHUNK = 200
#: Unchanged files re-linked per store operation, so a hub's many importers never
#: hold the shared database lock in one long stretch.
RELINK_CHUNK = 50


def _resolve_all(
    chunk: Sequence[tuple[ParsedFile, SourceStamp]],
    project_root: str,
    leaf_index: Mapping[str, set[str]],
) -> list[list[ResolvedEdge]]:
    return [resolve_edges(parsed, project_root, known_symbols=leaf_index) for parsed, _ in chunk]


async def _apply(
    store: CodeGraphStore,
    project_id: str,
    project_root: str,
    loaded: Sequence[_Loaded],
    removed: Sequence[str],
    known: set[str],
) -> tuple[int, int]:
    """Write what upkeep read, then re-link the files that were not re-parsed.

    Every changed file is parsed before any is resolved, and resolved against the
    others' *new* symbols, so the order the files arrive in cannot leave a call
    unresolved - the seed's rule, kept for a batch of any size. ``known`` is the
    set of these paths the graph held before. Returns ``(parsed, relinked)``.
    """
    await store.record_stamps(
        project_id, [(item.identity, item.stamp) for item in loaded if item.unchanged]
    )
    batch = [(item.parsed, item.stamp) for item in loaded if item.parsed is not None]
    if not batch and not removed:
        return (0, 0)
    paths = {parsed.path for parsed, _ in batch}
    before = await store.leaf_sets(project_id, paths)
    fresh = {parsed.path: _leaves(parsed) for parsed, _ in batch}
    targets = await asyncio.to_thread(
        lambda: {target for parsed, _ in batch for target in import_targets(parsed, project_root)}
    )
    leaf_index = await store.leaf_sets(project_id, targets - paths)
    leaf_index.update(fresh)
    edges = await asyncio.to_thread(_resolve_all, batch, project_root, leaf_index)
    written = 0
    for (parsed, stamp), file_edges in zip(batch, edges, strict=True):
        try:
            await store.replace_file(project_id, parsed, file_edges, stamp)
            written += 1
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the pass
            log.warning("code-graph: skipped %s during upkeep: %s", parsed.path, exc)
    await store.remove_files(project_id, removed)
    relinked = await _relink_importers(
        store,
        project_id,
        project_root,
        leaves_changed={path for path in paths if path in known and before[path] != fresh[path]},
        appeared=paths - known,
        removed={path for path in removed if path in known},
        exclude=paths,
    )
    return (written, relinked)


async def _relink_importers(
    store: CodeGraphStore,
    project_id: str,
    project_root: str,
    *,
    leaves_changed: set[str],
    appeared: set[str],
    removed: set[str],
    exclude: set[str],
) -> int:
    """Re-resolve the unchanged files whose edges depended on files that changed.

    A file's edges are resolved when it is parsed, against the filesystem (which
    file an import names) and against its imports' symbols (which module defines a
    name it calls). So a file nobody re-parses goes stale when a module it imports
    gains or loses a symbol, disappears, or is joined by a file its import now
    names instead. The per-start re-seed used to heal that at every restart; with
    the graph persisting, this heals it at the moment it happens, which also fixes
    the live case of a caller written in one turn and its callee in the next.

    Exact except where an import resolves to a file the walk never indexes (a
    non-source file, or one under a skipped directory), whose appearance nothing
    here observes. Returns how many edge rows changed.
    """
    if not (leaves_changed or appeared or removed):
        return 0
    importers: set[str] = set()
    pending: list[tuple[int, str, str, str | None]] = []
    for edge in await store.import_edges(project_id, targets=leaves_changed | removed):
        if edge[1] in exclude:
            continue
        importers.add(edge[1])
        if edge[3] in removed:
            pending.append(edge)  # its import may now resolve to another candidate
    if appeared:
        root = Path(project_root)
        keys = set().union(*(_resolution_keys(path, root) for path in appeared))
        named = await store.import_edges(project_id, naming=keys)

        def matching() -> list[tuple[int, str, str, str | None]]:
            out = []
            for edge in named:
                _, src, module, dst = edge
                spec = spec_for_path(src)
                if src in exclude or dst in removed or spec is None:
                    continue
                if _import_tail(module, src, root, spec=spec) in keys:
                    out.append(edge)
            return out

        pending.extend(await asyncio.to_thread(matching))
    retarget: dict[int, str | None] = {}
    if pending:

        def resolve_pending() -> dict[int, str | None]:
            answers: dict[int, str | None] = {}
            for edge_id, src, module, _ in pending:
                spec = spec_for_path(src)
                answers[edge_id] = (
                    resolve_import(module, src, project_root, spec=spec) if spec else None
                )
            return answers

        answers = await asyncio.to_thread(resolve_pending)
        for edge_id, src, _, dst in pending:
            if answers[edge_id] != dst:
                retarget[edge_id] = answers[edge_id]
                importers.add(src)
    changed = 0
    for chunk in _chunks(sorted(importers), RELINK_CHUNK):
        changed += await store.relink(project_id, chunk, retarget)
    return changed


@dataclass(frozen=True, slots=True)
class GraphSync:
    """What one `sync_project` did, for its log line and the diagnostics block."""

    outcome: str  # seeded | synced
    reason: str
    files: int  # source files the tree holds, within the bound
    stale: int  # files whose stamp did not match, so were read and hashed
    parsed: int  # of those, re-parsed because their bytes had changed
    removed: int  # files the graph held that the tree no longer does
    relinked: int  # edge rows of unchanged files re-resolved
    elapsed_s: float

    def snapshot(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "files": self.files,
            "stale": self.stale,
            "parsed": self.parsed,
            "removed": self.removed,
            "relinked": self.relinked,
            "elapsed_s": self.elapsed_s,
        }


async def sync_project(
    store: CodeGraphStore,
    project_id: str,
    project_root: str,
    *,
    limit: int = MAX_INDEX_FILES,
    reason: str = "",
) -> GraphSync:
    """Bring a persisted graph up to the tree on disk, reading only what moved.

    The graph lives in `mux.db` and outlives the process, so a daemon start owes it
    only what changed while nobody was watching: the walk the seed takes, a stat of
    each file from the directory listing, a read and hash of each file whose stamp
    differs from the one it was parsed at, and a parse of each whose bytes differ.
    It replaced a re-seed at the first turn of every process, which cleared and
    re-parsed every file - 6,000 of them, about a minute of CPU in the daemon -
    at every restart, on a tree that had usually not changed at all.

    Seeds instead when the Project has never been seeded. Files the tree no longer
    holds leave the graph, and every file that was not re-parsed is re-linked
    against the ones that were (`_relink_importers`), so the result is the graph a
    seed of the same tree would build.
    """
    started = time.perf_counter()
    if await store.index_state(project_id) is None:
        count = await index_project(store, project_id, project_root, limit=limit)
        return GraphSync(
            "seeded", reason, count, count, count, 0, 0, round(time.perf_counter() - started, 3)
        )
    # Before the walk, as the seed reads it: a commit landing mid-sync is recorded
    # as not yet seen, which costs one delta pass, never as seen.
    head = await repo_head(project_root)
    entries = await asyncio.to_thread(_scan_tree, project_root, limit)
    known = await store.file_stamps(project_id)
    present = {identity for _, identity, _ in entries}
    stale = [
        (path, identity, stamp)
        for path, identity, stamp in entries
        if stamp is None or identity not in known or not known[identity].matches(stamp)
    ]
    removed = sorted(set(known) - present)
    loaded: list[_Loaded] = []
    for path, identity, stamp in stale:
        recorded = known.get(identity)
        item = await asyncio.to_thread(
            _load,
            path,
            project_root,
            identity,
            recorded.content_hash if recorded is not None else None,
            stamp,
        )
        if item is not None:
            loaded.append(item)
    parsed, relinked = await _apply(
        store, project_id, project_root, loaded, removed, set(known)
    )
    await store.record_index_state(
        project_id, head=head, file_count=await store.file_count(project_id)
    )
    result = GraphSync(
        "synced",
        reason,
        len(entries),
        len(stale),
        parsed,
        len(removed),
        relinked,
        round(time.perf_counter() - started, 3),
    )
    log.info(
        "code_graph_synced project=%s reason=%s files=%d stale=%d parsed=%d removed=%d "
        "relinked=%d head=%s elapsed_s=%.2f",
        project_id,
        reason,
        result.files,
        result.stale,
        result.parsed,
        result.removed,
        result.relinked,
        head or "",
        result.elapsed_s,
    )
    return result


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
