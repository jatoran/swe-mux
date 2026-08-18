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

`GET /api/sessions/{sid}/change-map?scope=<session|branch|project>&hops=<int>` returns a bounded, server-side subgraph for a WebGL renderer.

- **Red seeds** are edited source files; **yellow** is their blast radius (reverse dependents); **blue** is their immediate imports (context).
- Only the changed nodes plus blast radius plus one hop ship, never the whole codebase graph, so frontend performance is independent of codebase size.
- `available` is false with a typed `disabled_reason` (`unsupported`, `no_project`, `automation_disabled`) rather than a fake empty graph.

### Three scopes, because "what changed" expires three ways

| Scope | Seeds from | Survives |
|---|---|---|
| `session` | this run's `file_write` facts **plus** every path the session has landed, from the git provenance ledger | the ledger half survives the fact window, the run rollover, and the merge |
| `branch` | everything the session's checkout has changed against its comparison base — committed, staged, unstaged, and untracked | both fact expiries; it is git state, not recorded history |
| `project` | every session's edits, one hue each (the former `unify=true`, still accepted as an alias) | as `session` |

Tier 0 write facts are precise and short-lived: they are read within `RUN_FACT_WINDOW_SECONDS` (6 h) **and** keyed to the current `agent_run_id`, so a conversation rollover drops them too.
A session whose branch merged hours ago therefore reported "no source edits in this session yet" — the facts had expired, not the work.
The provenance union fixes that for landed work (`contributed_paths` is repository-relative, per commit, per session, and merging does not disturb it), and the branch scope fixes it for work in flight.

`scopes` names what this session can be asked for; `branch` is offered only when the checkout has a comparison base (read free from the `compare_ref` the git monitor already cached, so the branch diff itself runs only when the branch scope is actually served).
A `branch` request that cannot be served falls back to `session` and says so in `scope_fallback`, because an empty branch map would read as "this branch changed nothing", which is a claim rather than an absence.

**A branch delta is checkout-scoped, not session-scoped.**
Two sessions sharing a worktree cannot be told apart by anything git can answer — the same reason `GitState.dirty` describes the checkout — so branch seeds carry no `sessions` and no per-session hue. Claiming one would be an invention.

### Which checkout a session is working in

`project_root` is where the **Project** was registered. It is not where the agent is working, and for every worktree session the two differ.
The git monitor already resolves the live working tree (`rev-parse --show-toplevel`) and already knows whether it is a *linked* worktree (`--git-dir` ≠ `--git-common-dir`), so the endpoint reads `record.git.root` / `record.git.worktree` rather than guessing.

This is the difference between a worktree session having a map and having none.
Its writes are absolute paths under `.claude/worktrees/<name>/…`; normalized against the Project root they keep that prefix, trip the hidden-directory rule, and every one of them is refused — the whole session reads as unmappable.
Normalized against the checkout it is actually in, they are ordinary repository-relative paths that join the canonical graph.

Two roots differing is **not** enough to re-anchor. A nested repository inside a Project (a vendored checkout, a sub-project) reports its own root with no worktree name, and joining its paths to this Project's identities would merge two unrelated trees. Only a worktree validated against `git worktree list` for this Project's repository re-anchors, TTL-cached per (project root, checkout) because worktrees are added by hand, never between two turns.

**Re-anchor reads; never ingestion.** `is_indexable_path` keeps worktree copies *out* of the graph on purpose — otherwise every worktree leaks a near-duplicate of the repository and pollutes every blast radius. So there is one structural graph per repository, built from the primary checkout, and a worktree's edits are *located* in it by repository-relative path. Blast radius is therefore "what this change reaches **when it lands**", which the standing lower-bound caption already covers.

**A seed must be a file the graph could index.**
The endpoint applies the engine's own admission rules — inside the checkout (`is_project_relative`) and not under a generated, vendored, or hidden directory (`is_indexable_path`) — because the graph only ever contains files that pass both.
A seed that fails either can never acquire an edge, never show a blast radius, and never be opened; drawing it anyway is what put scratchpad and temp-directory scripts on the map as permanently isolated dots.
The omission is stated rather than silently applied: `excluded: {outside_root, unindexable}` counts the distinct files dropped, and a map with nothing left returns `empty_reason: "excluded"` instead of the misleading `"no_edits"`.

**Re-anchoring takes the deepest root, and the best candidate.**
A worktree usually lives *inside* the Project root, so stripping the Project root off a worktree write does yield a relative path — the useless one. Candidate roots are tried deepest-first and ranked (project-relative *and* indexable beats project-relative beats absolute), so the most specific containing checkout owns the path.
The project scope re-anchors against every contributing session's own checkout for the same reason: without it a sibling worktree's whole session disappears from the unified map.

**A seed the index has never parsed is drawn and marked.**
A file created on a branch exists in that worktree alone, while the graph is built from the primary checkout, so it has no node — `indexed: false`.
Drawing it is right (it is the file the reader is most likely thinking about) but reading its empty neighbourhood as "nothing depends on this" would be wrong, so the pane marks it rather than letting an absence of data pass as a finding.

**`path` is an identity, not a filesystem path; `display_path` is the openable one.**
Every graph path is casefolded by `normalize_target`, which is what makes cross-tool comparison work and what makes it unusable for opening a file: a case-sensitive host cannot open `frontend/src/changemappane.tsx` at all, and a case-insensitive one opens it under a second, colliding pane identity — two editors and two save revisions on one file.
`resolve_display_paths` recovers the real casing by listing each directory (never by `stat`, which succeeds on the wrong case on Windows and would silently keep it), memoised to one `scandir` per distinct directory per request.
A node whose file no longer exists carries no `display_path` and the pane offers a disabled button rather than a dead link.
The payload also names the checkout those paths are relative to: `worktree` is the session's own root when it differs from the Project root, so the client opens the worktree's copy rather than the primary checkout's.

### Reading the map

The pane is a drawer tab and a poppable workspace pane, drawn two ways: Sigma over graphology on desktop, and the same three roles as lists on mobile, where a WebGL canvas strands on the pixel ratio and a force layout is unreadable at 380px anyway.

- **Focus.** Hovering a node previews its neighbourhood; clicking pins it until the node is clicked again, the stage is clicked, or the detail card is cleared.
  A hover takes precedence while it lasts and falls back to the selection on leave, so the pinned highlight survives the pointer crossing the pane.
  Everything outside the focused node's undirected neighbour set is dimmed by mixing its colour toward the pane background and dropping its label; only edges *incident to the focused node* light up, because an edge between two of its neighbours is not a link the focused file has.
  The state lives in refs and is applied by Sigma's per-frame `nodeReducer`/`edgeReducer`, so a hover costs one repaint rather than a Preact render that would re-seed the graph on every pointer move.
- **Scope.** A selector offers whatever the daemon says this session can be asked for, and shows the scope actually *served* — so a request that fell back never leaves the control lying.
  The client sends no scope on first load, which is what lets a worktree default to its branch without the client having to know it is in one.
  The header names what the map is measured from: `worktree <name> vs <ref>` in the branch scope, because "since `<sha>`" cannot tell one worktree of several apart, and several open at once is exactly when the reader needs it to.
- **Unindexed seeds.** A node with `indexed: false` carries a `◌` mark in its graph label, its list row, and a line in the detail card.
  Sigma's node programs are filled discs with no border channel, and colour is already spent on the three roles, so the label is the only per-node surface left that can carry the distinction.
- **Open in a pane.** A selected node opens as an ordinary file pane, always through `display_path` and through `worktree` when the map names one.
  The button is disabled, never hidden, when the file no longer exists — an absent path is information, not a reason to make the control vanish.
- **Neighbours on mobile.** The list projection has no picture, so the detail card spells out what the selected file links to, ordered by role then path, each row selecting that file in turn.

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
- Change-map endpoint: `src/swe_mux/server.py` (`session_change_map`, `_change_map_checkout`, `_SeedAdmission`, `_change_map_scope`)
- Branch change set: `src/swe_mux/git_review.py` (`branch_changed_paths`)
- Landed-work seeds: `src/swe_mux/history.py` (`git_provenance`, `contributed_paths_json`)
- Registry entry: `src/swe_mux/automation_registry.py`
- Packaging: `packaging/swe_mux.spec`
- Frontend change map: `frontend/src/ChangeMapPane.tsx`, `frontend/src/changeMap.ts`, `frontend/src/changeMapLayout.worker.ts`
- Tests: `tests/test_code_graph.py`, `tests/test_code_graph_detectors.py`, `tests/test_mcp_code_graph.py`, `tests/test_change_map_endpoint.py` (which builds **real** git worktrees — a stubbed `git worktree list` proves nothing about a checkout being mistaken for another), `tests/test_git_review.py`

## Relates to

- `tier0-facts.md` — the `file_write` stream the graph is maintained off.
- `deterministic-consumers.md` — the turn-boundary detector runner the graph maintenance and annotations ride.
- `automation-enablement.md` — the per-Project opt-in DAG that gates the `code_graph` consumer.
- `mux-mcp.md` — the agent pull-tool surface the structural reads join.
- `git.md` — the git-provenance contributor rows the co-change net is mined from.
