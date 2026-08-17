# Code-structure graph

## What it is

A deterministic, always-fresh graph of how the code connects: who imports whom, who calls whom, where each symbol is defined.
swe-mux already captured a *behavioral* graph (Tier 0 facts, provenance edges, doc-debt ownership, git attribution); this is the missing *structural* graph.
It is built with tree-sitter, never LSP, and maintained incrementally off the Tier 0 `file_write` stream, so it costs no tokens and stays current without a separate file watcher.
Roadmap Phase 7.9.

It powers three surfaces: pull-only agent MCP tools (blast radius, navigation, context, test-gap), human-passive annotations (blast-radius reach, callers-edited-but-not-examined, dead code, god nodes, import cycles), and the per-session change map.

## Key concepts

- **Model-free and gated.** The graph is a deterministic query surface, so it registers as one consumer (`code_graph`) in the automation registry, requires `tier0`, and is per-Project opt-in through the enablement DAG.
- **tree-sitter, not LSP.** LSP buys type-accurate rename precision this feature does not need, does not close the dynamic-dispatch recall gap, and imposes a per-language, per-OS, venv-coupled server burden a frozen Windows app should not carry.
  The grammars come from `tree-sitter-language-pack`, whose prebuilt wheels avoid per-platform grammar compilation.
  Python, TypeScript, TSX, and JavaScript have full extraction and import-aware resolution.
- **Import-aware resolution against the filesystem.** A call to `X` in a file resolves to a definition only through an actual import or a same-file definition, so a same-named symbol in an unrelated module is never a false caller.
  Modules resolve against real files on disk, so resolution is order-independent: it does not depend on which file was parsed first.
- **Every static reverse-caller set is a lower bound.** Callers reached through `getattr`, dict dispatch, decorators, dependency injection, or dynamic imports are edges tree-sitter cannot see.
  Every query and annotation says so, and the git co-change net is the recall safety net for exactly those misses.
- **Unresolved is recorded, never guessed.** A reference whose target cannot be resolved is stored with `resolved=0` and its raw name, so it feeds counts and the co-change net without inventing an edge.
- **One path identity.** Nodes and edges key on the same `normalize_target()` path identity every other consumer uses, so the graph joins cleanly with provenance, doc-debt, and git facts.

## The engine

- Nodes are file-level by default; symbol detail (`code_graph_symbols`) is resolved on demand.
- Edges are `imports` (file to file), `calls` and `references` (symbol to symbol, or to an unresolved name), and `defines` (file to symbol).
- A `file_write` fact re-parses that one file and replaces its edges; the parse is skipped when the file's bytes already match the stored content hash.
- A **one-time bounded seed index** parses a Project's existing source tree once, because a reverse-dependency query needs the importers in the graph and an importer a session never edited would otherwise be invisible.
  This is not the rejected full-rebuild-per-edit watcher: it runs at most once per Project per process, on a worker thread, bounded by file count and wall-clock.
- The reverse-dependency query is a bounded SQLite recursive CTE over `imports`/`calls` edges, N hops.
- The **git co-change net** is mined from `git_provenance` contributor rows grouped by commit: files repeatedly committed together are coupled whether or not a static edge exists.
  It is the required recall net for the dynamic edges tree-sitter misses, not decorative.

## Surface 1 — agent pull tools

Six pull-only mux MCP tools, each gated on the per-Project `code_graph` opt-in, returning empty rather than a low-confidence guess, and labelling static results a lower bound.
No signal is ever pushed into an agent; it consults the tools on its own initiative.

| Tool | Returns |
|---|---|
| `blast_radius` | reverse callers (hop-ordered), the git co-change net, covering tests among the reachable set, and the owning docs for one file |
| `find_definition` | where a symbol is defined, by leaf name or qualname |
| `find_callers` | the (file, symbol) pairs that call into a file or symbol, import-aware, with unresolved same-name callers reported separately |
| `find_references` | every call or reference to a symbol in a file |
| `code_context` | a compact structural neighborhood for context packing: each file's key symbols, imports, and direct callers |
| `test_gap` | recently-changed files whose blast radius contains no covering test |

## Surface 2 — human-passive annotations

These are written as annotations only, never a PTY write, and render in the Phase 7.10 Findings pane which decides whether they interrupt.

- **Blast-radius notice.** An edit whose reverse reach meets `BLAST_MIN_REACH` gets one annotation naming the reach count, deduped to one row per edited file per run.
- **Callers edited but not examined.** The mux-unique signal: an edited file's reverse callers intersected with the session's own Tier 0 `file_read` facts, flagging callers the session may have broken without opening them.
  No standalone code-graph tool can produce this because none observe the agent's reads.
- **Structural findings.** Dead-code candidates (files with no inbound reference), god nodes (high inbound fan-in), and import cycles, each a project-scoped annotation deduped to one row and bounded per pass.

## Surface 3 — the per-session change map

`GET /api/sessions/{sid}/change-map?unify=<bool>&hops=<int>` returns a bounded, server-side subgraph for a WebGL renderer.

- **Red seeds** are this session's own edited source files, taken from `file_write` facts filtered by run, so concurrent other-session edits are excluded by construction.
- **Yellow** is their blast radius (reverse dependents); **blue** is their immediate imports (context).
- Only the changed nodes plus blast radius plus one hop ship, never the whole codebase graph, so frontend performance is independent of codebase size.
- The **unify** toggle widens to the union of every session's edits since the baseline, colouring each session a distinct hue: the multi-session and multi-worktree change map the fact attribution makes possible.
- `available` is false with a typed `disabled_reason` (`unsupported`, `no_project`, `automation_disabled`) rather than a fake empty graph.

## Additional derivations (same substrate)

- Dead-code / orphan, import-cycle, and god-node detection ride the same graph as ordinary annotations (Surface 2).
- **Doc-debt precision upgrade.** When the graph is enabled, a doc that owns a *dependent* of a changed file also owes an update, because changing a file can invalidate the documentation of the code that calls it.
  This is an additive, optional refinement over the existing doc-debt ledger and is off when the graph is absent.

## Packaging

The PyInstaller spec (`packaging/swe_mux.spec`) collects `tree_sitter` and `tree_sitter_language_pack` grammar shared libraries into the frozen bundle.
Without them the frozen app parses nothing and the graph is silently empty; `parsing_available()` is the named acceptance check.

## Configuration

Per-project opt-in in `<project>/.swe-mux/config.toml`, e.g.

```toml
automations = { raw_store = true, tier0 = true, code_graph = true }
```

## Key files

- Engine, store, resolution, queries, co-change net: `src/swe_mux/code_graph.py`
- Turn-boundary maintenance and the human-passive detectors: `src/swe_mux/deterministic_consumers.py` (`_code_graph`, `_blast_radius`, `_unexamined_callers`, `_code_structure`)
- Agent pull tools: `src/swe_mux/mcp.py`, `src/swe_mux/mcp_contract.py`
- Change-map endpoint: `src/swe_mux/server.py` (`session_change_map`)
- Registry entry: `src/swe_mux/automation_registry.py`
- Packaging: `packaging/swe_mux.spec`
- Frontend change map: `frontend/src/ChangeMapPane.tsx`, `frontend/src/changeMapLayout.worker.ts`
- Tests: `tests/test_code_graph.py`, `tests/test_code_graph_detectors.py`, `tests/test_mcp_code_graph.py`, `tests/test_change_map_endpoint.py`

## Relates to

- `tier0-facts.md` — the `file_write` stream the graph is maintained off.
- `deterministic-consumers.md` — the turn-boundary detector runner the graph maintenance and annotations ride.
- `automation-enablement.md` — the per-Project opt-in DAG that gates the `code_graph` consumer.
- `mux-mcp.md` — the agent pull-tool surface the structural reads join.
- `git.md` — the git-provenance contributor rows the co-change net is mined from.
