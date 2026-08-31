# Frontend: sidebar, session rows, and Projects

Index: `../packages.md`.
Design: `../../../design/features/projects.md`, `../../../design/features/ui.md`.

## Session rows

`sessionRowConfig.ts`, `sessionRowFields.ts`, `sessionRowPrefs.ts`, `SessionRowLive.tsx`, `SessionRowBody.tsx`,
`SessionRowSettings.tsx`, `StateIndicator.tsx`, `dotShapes.ts`

### `sessionRowConfig.ts` - the browser-free model

The field catalog with per-field notability descriptions, degradation priorities, collapse marks, and truncation floors, plus the separator table, the shipped default and presets, the normalizer, and the pure placement algebra.

Its normalizer is where the layout invariants live, so the renderer never has to ask whether a configuration makes sense:

- The title is always placed.
- Identity fields sit on the top line and nowhere else.
- Non-identity fields sit on the bottom line and nowhere else.
- No field appears twice.

The shipped default is transcribed from the primary install's own stored layout rather than composed here; the reasoning, and the three consequences worth checking before moving it, are in `design/features/ui.md`.
Two of its scalars are duplicated in `style.css` as the `--session-dot` fallback, and the equality is asserted at both breakpoints (`test/renderer/session-row-layout.spec.ts`) rather than left to the comment on either side.

The normalizer is also where the stored blob is migrated.
Version 2 moved presence-only flags into the top line's right section (the flag strip) and placed the `draft` field, because changing the shipped default reaches nobody who has ever saved a layout.
Version 3 places `voice` on the same rule, next to `approvals`.
Each step runs only for a blob written before it, so a layout from a later build runs none of them and a relocation never repeats.
Rewriting the default is the opposite case and deliberately does not bump the version: "a stored blob is authoritative" is precisely the non-repaint guarantee wanted there, so only a device that has never saved a layout sees the new one.
A new *scalar* is a third case and needs no version step either: a save writes the whole config, so an absent key can only mean the blob predates the setting, and normalization gives it the shipped value the same way a version step places a field nobody could have declined.
The witness test (`test/sessionRowFields.test.ts`, "moving the shipped default repaints no device that has stored a layout") therefore compares against the stored blob widened by exactly the new keys, which is the assertion that stops a *placement* or a rewritten default from sneaking through with it.

It also owns the context ramp: `contextWarn`/`contextHigh`/`contextCrit`, `normalizeContextThresholds`, and `contextBand`.
The band function is the single comparison chain all three context renderings share; it is called once per row in `sessionRowFields.ts` and the result travels on the gauge object, so `StateIndicator` and `SessionRowBody` never re-derive it.
`normalizeContextThresholds` clamps into `(0, 1)` and pushes upward to keep the ramp ordered and separated, because a threshold dragged past its neighbour is an edit to make room rather than an edit to discard.
Identity tokens are exempt from shedding, since the strip's section may hold nothing else and the narrow widths that trigger shedding are exactly where a flag is worth most.

### `sessionRowFields.ts` - the DOM-free engine

Turns (session, config, fleet context) into ordered tokens.
It owns every notability threshold and the duration semantics, and returns the separator alongside the tokens rather than baking it between them, which lets the renderer emit a separator only between tokens that drew.

Two of those semantics are worth naming.
`workedSeconds` adds the open turn to the daemon's `worked_ms`, because the record's sum only advances at a turn boundary and a total that freezes for the whole of a long turn reads as a stopped clock.
It dates the open turn at `interrupt_pending_at` when one is set, exactly as `liveDurationAge` does.
The `context` field takes both its colour and its notability from `contextBand`, rather than colouring on the ramp and appearing on a constant of its own.

`deriveRowContext` computes the "differs from the project default" comparisons once per snapshot, because asking each row about the other rows is quadratic.
It also counts live sessions per checkout root, which lets a Git quantity say that several rows are quoting it.

`fitLine` owns the width ladder - truncated by CSS to `ROW_MIN_CHARS`, then collapsed to the field's own mark, then dropped, lowest `priority` first - against a budget in characters that `sessionRowPrefs.useRowBudget` measures off the `.row-metric` probe.
The sidebar element is the wrong probe: its width overstates a row's room by the gutter, the tree's padding, and the scrollbar.

The unsent-input field unions the daemon's `unsent_input` with this device's mobile draft registry, reporting the older of the two.

### `sessionRowPrefs.ts` - persistence and clocks

The persistence bridge, the one shared quantized clock (`useRowClock`, 5 s, stopped while the tab is hidden), and the publisher of the configured indicator size as the `--session-dot` root custom property (`applySessionDotSize`, re-resolved by `watchSessionDotProfile` when a window crosses the device-class breakpoint).
The size goes through CSS rather than through a prop on `StateIndicator`, because the sidebar's gutter column, the stack thread's geometry, the title line's height, and the row's own height are all derived from that variable; handing the number to the component would resize the glyph and leave every one of them behind.

The interval behind that clock is module-scoped and shared by every subscriber, so the sidebar still runs one timer however many rows are on screen - which is what lets the clock live *in* the rows rather than above them.

### Rendering

`SessionRowLive.tsx` is the sidebar's row body: it subscribes to the clock, builds that row's tokens (`buildSessionRowTokens`, or `identityRowTokens` for the phone's narrow projection), and is memoized on its four props.
It exists so the five-second tick re-renders the rows that age instead of the whole shell around them; the composition root hands it the clock-free `deriveRowFleetFacts` and never reads `useRowClock` itself (`composition.md`, "Server clock").
The two surfaces that render a row against a *fixed* time - the row-settings preview and the renderer harness - keep using `SessionRowBody` directly with a context they built themselves.

`SessionRowBody.tsx` renders tokens and owns only three structural rules: separators between drawn tokens; sections that meet but never overlap, with the right one laid out first so a value pinned to the row's edge cannot be pushed off it; and a token drawn at the `icon` rung rendering its field's mark, whatever `gitGlyphs` says.
The title is deliberately a `<strong>` so `sessionAttention.ts`'s attention-tier colour rules keep applying.
`dotShapes.ts` is pure geometry, split out from `StateIndicator.tsx` so the node test runner can import it.

`sessionStandingMark` is the indicator-side counterpart of `sessionContextArc`: one setting decides whether standing activity draws as row glyphs or as a pip, so the two renderings can never both be on, and every surface (sidebar, tab strips, menus) reads the same setting.

Inactive sessions keep their ordinary tree and pane placement with a dimmed row and tab.
Their context action changes from Stand down to Resume or Restart terminal, and bulk ended-session cleanup deliberately excludes them.

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

`ProjectsManager.tsx`, `projectConfig.ts`, `projectConfigState.ts`

The configured catalog UI plus the single per-Project settings editor: missing-folder state, removal preflight with live and history counts, preserved-history disclosure, the committed Git/worktree setup command, and both default storage layers.
It does not own workspace placement.
It is also the one editor for every per-Project switch, which is what makes a `GrantGate` elsewhere additive-only (`settings-and-gates.md`).

`projectConfigState.ts` holds **one** copy of `.swe-mux/config.toml` for the whole panel and every section reads and writes through it.
Three sections drew over that one file - the defaults and repository options form, the automation opt-ins, the agent authority table - and each of them used to fetch and cache its own revision, so the first successful edit anywhere in the panel invalidated the other two and the second edit answered "project config changed externally".
Two of the three have since moved to the Automation dashboard (the opt-ins on 2026-08-26, the authority rows on 2026-08-29, which now render read-only here beside a `SettingLink`), so the shared store serves the form plus that summary; it stays shared because the writers it was built against - a grant gate, the land queue, the configurator - never went anywhere.
The hook refreshes on `mux:project-automations-changed`, which `App.tsx` re-broadcasts from the daemon's `project_configuration_changed`, so an edit made by a grant gate, the land queue, the configurator agent or another device reaches an open panel instead of staling it.

`projectConfig.ts` owns the pure rules, split out because the node test runner strips types from `.ts` and cannot load `.tsx`:

- The **delta**: a write carries only the fields that differ from the file, `null` to remove one, with `base` naming what the editor believed those fields held. Sending the document back is what lets a stale copy revert a field the editor does not draw.
- `PANEL_CONFIG_FIELDS`, the closed set the form owns. "Reset repo options to inherited" clears exactly those; it once wrote an empty document, which took the opt-ins, the authority table, the approval rules and the land queue's verify command with it.
- `nextWorktreeTable`, which edits one field of the `[worktree]` table. The land queue owns `verify_command` in the same table, and replacing the table wholesale deleted it whenever someone cleared the setup command.
- Conflict reading: a `409 revision_conflict` names the fields that moved and carries the current file, so the panel resyncs from the refusal and says what it would overwrite rather than telling the reader to reload.

The one draft in the panel is an **overlay** of touched fields on top of that shared copy, never a copy of it.
That is what makes refreshing safe while someone is typing: a refresh moves the fields nobody is editing and cannot discard an edit.

## Project actions

`ProjectRunMenu.tsx`, `worktreeLaunch.ts`, `pendingSession.ts`, `App.tsx` orchestration

The Run catalog, trust, and ordinary launch interaction.
The worktree launch is one App-owned act across both of its phases: an optimistic tab is placed in the focused pane first, then `git worktree add`, then bootstrap and spawn behind it.
The form holds only for creation and stays open on that failure's message, which is the one the operator can correct there; bootstrap failures are toasts, and resolution swaps the real id into the same leaf without stealing a tab the operator moved to meanwhile.
Also branch-whitespace normalization, a long background setup deadline, config-root loading, and pure `<root>/<project>/<branch>` path derivation.
