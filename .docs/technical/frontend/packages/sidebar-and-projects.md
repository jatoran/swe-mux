# Frontend: sidebar, session rows, and Projects

Index: `../packages.md`.
Design: `../../../design/features/projects.md`, `../../../design/features/ui.md`.

## Session rows

`sessionRowConfig.ts`, `sessionRowFields.ts`, `sessionRowPrefs.ts`, `SessionRowBody.tsx`,
`SessionRowSettings.tsx`, `StateIndicator.tsx`, `dotShapes.ts`

### `sessionRowConfig.ts` - the browser-free model

The field catalog with per-field notability descriptions, degradation priorities, collapse marks, and truncation floors, plus the separator table, the shipped default and presets, the normalizer, and the pure placement algebra.

Its normalizer is where the layout invariants live, so the renderer never has to ask whether a configuration makes sense:

- The title is always placed.
- Identity fields sit on the top line and nowhere else.
- Non-identity fields sit on the bottom line and nowhere else.
- No field appears twice.

The normalizer is also where the stored blob is migrated.
Version 2 moved presence-only flags into the top line's right section (the flag strip) and placed the `draft` field, because changing the shipped default reaches nobody who has ever saved a layout.
Version 3 places `voice` on the same rule, next to `approvals`.
Each step runs only for a blob written before it, so a layout from a later build runs none of them and a relocation never repeats.
Identity tokens are exempt from shedding, since the strip's section may hold nothing else and the narrow widths that trigger shedding are exactly where a flag is worth most.

### `sessionRowFields.ts` - the DOM-free engine

Turns (session, config, fleet context) into ordered tokens.
It owns every notability threshold and the duration semantics, and returns the separator alongside the tokens rather than baking it between them, which lets the renderer emit a separator only between tokens that drew.

`deriveRowContext` computes the "differs from the project default" comparisons once per snapshot, because asking each row about the other rows is quadratic.
It also counts live sessions per checkout root, which lets a Git quantity say that several rows are quoting it.

`fitLine` owns the width ladder - truncated by CSS to `ROW_MIN_CHARS`, then collapsed to the field's own mark, then dropped, lowest `priority` first - against a budget in characters that `sessionRowPrefs.useRowBudget` measures off the `.row-metric` probe.
The sidebar element is the wrong probe: its width overstates a row's room by the gutter, the tree's padding, and the scrollbar.

The unsent-input field unions the daemon's `unsent_input` with this device's mobile draft registry, reporting the older of the two.

### `sessionRowPrefs.ts` - persistence and clocks

The persistence bridge, the one shared quantized clock (`useRowClock`, 5 s, stopped while the tab is hidden - one timer for the whole sidebar, not one per row), and the publisher of the configured indicator size as the `--session-dot` root custom property (`applySessionDotSize`, re-resolved by `watchSessionDotProfile` when a window crosses the device-class breakpoint).
The size goes through CSS rather than through a prop on `StateIndicator`, because the sidebar's gutter column, the stack thread's geometry, the title line's height, and the row's own height are all derived from that variable; handing the number to the component would resize the glyph and leave every one of them behind.

### Rendering

`SessionRowBody.tsx` renders tokens and owns only three structural rules: separators between drawn tokens; sections that meet but never overlap, with the right one laid out first so a value pinned to the row's edge cannot be pushed off it; and a token drawn at the `icon` rung rendering its field's mark, whatever `gitGlyphs` says.
The title is deliberately a `<strong>` so `sessionAttention.ts`'s attention-tier colour rules keep applying.
`dotShapes.ts` is pure geometry, split out from `StateIndicator.tsx` so the node test runner can import it.

`sessionStandingMark` is the indicator-side counterpart of `sessionContextArc`: one setting decides whether standing activity draws as row glyphs or as a pip, so the two renderings can never both be on, and every surface (sidebar, tab strips, menus) reads the same setting.

## Sidebar filter

`sidebarSearch.ts`, `fuzzyText.ts`, `App.tsx` orchestration

`sidebarSearch.ts` is the browser-free model for the sidebar's typed filter.
It produces **three sets of ids plus a ranking**, never a list to render: `sidebarTreeFilter` returns the Groups, Projects, and sessions still drawn, the drawn rows in sidebar order (what arrows walk), and `best` (what Enter opens).
The host then walks its ordinary tree and skips what is missing from the sets, so nesting, layout clusters, ordering, and row rendering stay exactly where they already live.

Two containment rules make the sets a *tree* filter:

- A matched node keeps its subtree, or a matched Project renders as an empty heading.
- A kept node keeps its ancestors, or a session row has no heading saying where it lives.

`best` is drawn only from direct hits, never from a row kept by containment.
An empty query returns `null`, meaning *not filtering*, which is what makes opening the filter change nothing on screen.
It also owns cursor clamping and non-wrapping arrow movement around an explicit `NO_SEARCH_CURSOR`, the idle-expiry predicate, and the debounce and idle constants.
Its input types are structural and narrower than `Project`/`Session`, and a session's label comes from `sessionDisplayName` rather than a second copy of the naming rule.

`App.tsx` owns everything with a DOM in it:

- The header's search state and its debounce.
- The polled idle clock, fed by a ref so pointer movement costs no re-render.
- The dismiss level, the `search-cursor` mark on the tree's own rows, and the filtered-open fold rule.
- The guard that makes every sidebar drag inert while a query is up, since a drop's insertion index is computed from drawn rows.
- The `sidebar.search` command that opens the sidebar with the filter.

## Session display names

`sessionNames.ts`

The single naming rule every surface reads, in two entry points because the payloads disagree about types: `sessionDisplayName` for a live session (`auto_named` boolean) and `runDisplayName` for a History or automation run row (SQLite `0`/`1`).
A generated title wins only while the session is auto-named, and an absent field means auto-named.
`agentTargets.ts`'s `agentTargetName`, `App.tsx`, `HistoryBrowser.tsx`, and `AutomationDashboard.tsx` all delegate here rather than restating it.
`sessionDisplayName` takes a structural `NamedSession` - the three fields it reads - rather than a whole `Session`, so a DOM-free row model that declares its own narrow session shape can call it instead of restating the rule to stay pure.
The backend twin is `src/swe_mux/session_titles.py`.

## Model labels

`modelDisplay.ts`, `ModelName.tsx`

`modelDisplay.ts` owns one boundary-safe rule: drop the provider path, drop a leading vendor-brand token, keep everything else.
A per-family table would have to be extended for every new model and would print the raw id until someone did.
`ModelName.tsx` applies it only at read-only render boundaries and retains the exact identifier in the tooltip and accessible name.
Data keys, comparisons, filters, configuration controls, and API values never use display labels.

## Projects

`ProjectsManager.tsx`

The configured catalog UI plus the single per-Project settings editor: missing-folder state, removal preflight with live and history counts, preserved-history disclosure, the committed Git/worktree setup command, and both default storage layers.
It does not own workspace placement.
It is also the one editor for every per-Project switch, which is what makes a `GrantGate` elsewhere additive-only (`settings-and-gates.md`).

## Project actions

`ProjectRunMenu.tsx`, `worktreeLaunch.ts`, `pendingSession.ts`, `App.tsx` orchestration

The Run catalog, trust, and ordinary launch interaction.
Durable worktree creation is followed by an unpanned full-workspace setup placeholder, with active-session-gated non-focus-stealing resolution and a setup-failure warning.
Also branch-whitespace normalization, a long background setup deadline, config-root loading, and pure `<root>/<project>/<branch>` path derivation.
