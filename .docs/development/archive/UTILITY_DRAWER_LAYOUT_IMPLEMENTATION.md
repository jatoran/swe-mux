# Custom utility drawer layout implementation plan

## Status and completion rule

- [x] Treat this document as the implementation checklist and mark every completed item in place.
- [x] Complete the work as one coherent update without feature flags, staged releases, time estimates, or deferred phases.
- [x] Do not consider the update complete while any required checkbox remains unchecked.
- [x] Move this document to `.docs/development/archive/UTILITY_DRAWER_LAYOUT_IMPLEMENTATION.md` only after every implementation, verification, documentation, and deployment checkbox is complete.

## Completion evidence

- Completed on 2026-08-04 without applying the update to the running daemon or frozen desktop app.
- The full backend suite passed with 1,408 tests passed, 1 skipped, and 8 deselected.
- Ruff and mypy passed, and the final frontend suite passed with 579 tests.
- TypeScript checking and the production Vite build passed.
- Isolated Chromium acceptance covered recursive desktop panes, keyboard tab focus, keyboard split resizing, title mode, pointer edge splitting, and the non-mutating mobile projection.
- The temporary preview process was stopped after acceptance, and no swe-mux process or live terminal session was restarted or terminated.

## Objective

Replace the utility drawer's single tab strip with an independent recursive drawer layout.
Each drawer pane owns an ordered tab rail and one active utility tab.
Users can reorder a tab in its current rail, move it to another pane, or drop it on any pane edge to create left, right, top, or bottom splits.
The layout must support user-defined nested arrangements, including a 3x3 grid, without becoming part of the Project workspace layout.
The split tree, tab locations, tab order, split ratios, and drawer width are global per device.
Each Project separately remembers, on that device, the selected tab in every drawer pane and whether the desktop drawer is open.

Every registered utility tab must exist in exactly one drawer pane and must never have a duplicate drawer content instance.
Desktop renders the saved drawer tree.
Mobile renders a one-body-at-a-time flattened projection without rewriting desktop membership, ordering, split directions, or ratios.
Appearance settings must let the user render utility tabs as icons or visible titles.

## Required reading before implementation

- [x] Read `AGENTS.md` and the shared logging, documentation, and Git instructions it references.
- [x] Read `.docs/CLAUDE.md` for documentation routing.
- [x] Read `.docs/design/features/ui.md`, especially the utility drawer, mobile panel, focus, settings, and overlay contracts.
- [x] Read `.docs/design/features/workspace-layout.md` for the existing recursive split model, tab drag contract, split resizing, and mobile projection behavior.
- [x] Read `.docs/technical/frontend/workspace-state.md` for state authority, optimistic state, focus, and mounted-view rules.
- [x] Read `.docs/technical/frontend/packages.md` before adding frontend modules or moving ownership between components.
- [x] Read `.docs/design/features/project-resources.md` before changing Files or Notes host behavior.
- [x] Read `.docs/design/features/prompt-queue.md` before changing Queue panel mounting or pop-out behavior.
- [x] Read `.docs/design/features/history.md` before changing Transcript panel mounting or session-following behavior.
- [x] Read `.docs/design/interfaces.md` and `.docs/design/data-model.md` before changing the configuration contract.
- [x] Read `.docs/development/archive/SESSION_PRESERVING_RELOAD.md` before applying the completed frontend or backend changes to a running app.
- [x] Inspect `frontend/src/App.tsx`, `frontend/src/UtilityDrawer.tsx`, `frontend/src/drawerTabs.ts`, `frontend/src/drawerTabOrder.ts`, `frontend/src/drawerNotes.ts`, `frontend/src/layout.ts`, `frontend/src/mobileWorkspace.ts`, `frontend/src/dragReorder.ts`, `frontend/src/pointerDragClaim.ts`, `frontend/src/deviceSettings.ts`, `frontend/src/Settings.tsx`, `frontend/src/railIcons.tsx`, and `frontend/src/style.css`.
- [x] Inspect the current drawer bodies for lifecycle and scope assumptions: `ClipboardPanel.tsx`, `CommandsTab.tsx`, `PromptsTab.tsx`, `QueuePane.tsx`, `TranscriptTab.tsx`, `ProjectResource.tsx`, `NotesTab.tsx`, `AgentContextTab.tsx`, `GitTab.tsx`, `ProcessesTab.tsx`, and `Notifications.tsx`.
- [x] Inspect `src/swe_mux/config.py`, the config update route in `src/swe_mux/server.py`, and the corresponding config tests before adding the tab-display setting.
- [x] Inspect `frontend/test/drawerTabs.test.ts`, `frontend/test/drawerTabOrder.test.ts`, `frontend/test/drawerNotes.test.ts`, `frontend/test/layout.test.ts`, `frontend/test/mobileWorkspace.test.ts`, and existing frontend UI contract tests before changing their assumptions.

## Product decisions and boundaries

### Independent utility workspace

- [x] Keep the utility drawer structurally independent from Project layout v7.
- [x] Do not add utility leaves to `PaneLayout`, Project layout PATCH requests, SQLite Project layout state, workspace focus traversal, warm terminal panes, or workspace mobile projection.
- [x] Reuse the main workspace's interaction language and safe pointer primitives without sharing its persisted model or leaf types.
- [x] Do not allow cross-dragging between utility drawer tabs and Project workspace tabs.
- [x] Keep existing explicit cross-surface actions unchanged, including Files opening a file in the workspace, Notes moving one editor between hosts, and Queue opening an explicit workspace queue tab.
- [x] Do not automatically make every utility tab available as a Project workspace leaf.
- [x] Preserve the current drawer default as one pane containing every utility tab in canonical order.
- [x] Keep the drawer closed by default for Projects without saved presentation state.
- [x] Keep one global device-local drawer arrangement across every Project.
- [x] Switching Projects must retain the split tree, tab locations, tab order, ratios, and width while restoring that Project's selected tabs and expanded state.

### Arbitrary splits

- [x] Support recursive horizontal and vertical splits rather than a fixed two-pane design.
- [x] Permit any arrangement that can be formed from the registered singleton tabs, including a 3x3 grid.
- [x] Do not impose a product-level limit of two regions, one split, or one orientation.
- [x] Bound the number of drawer stacks by the number of registered utility tabs because empty stacks are invalid and every stack must contain at least one tab.
- [x] Apply a defensive maximum tree depth of 24 while parsing stored data and performing split operations.
- [x] Reject or normalize malformed trees without crashing application boot.
- [x] Do not automatically collapse, rearrange, or reorient a valid desktop layout because a pane becomes narrow.

### Singleton tab ownership

- [x] Enforce that every `DrawerTabId` occurs exactly once across the complete normalized drawer tree.
- [x] Treat a tab move as one atomic remove-and-insert transaction.
- [x] Never render two content bodies for the same `DrawerTabId`.
- [x] Never permit cloning through modifier keys, repeated drops, malformed local storage, settings events, Project switches, or responsive transitions.
- [x] Collapse a source stack and its now-redundant parent split immediately when its last tab moves away.
- [x] Never persist an empty stack or a split with a missing branch.
- [x] Preserve every registered tab when reading old, incomplete, duplicated, or hand-edited state.
- [x] Insert newly shipped tab IDs into an existing normalized layout without moving the user's existing tabs.
- [x] Place a newly shipped tab beside its canonical predecessor in that predecessor's stack, or in the first depth-first stack when no predecessor survives.

### Scope and focus

- [x] Preserve the existing `session`, `project`, and `app` tab scope registry.
- [x] Keep session-scoped tabs following the currently focused session.
- [x] Keep Project-scoped tabs following the active Project.
- [x] Keep app-scoped tabs independent of Project and session selection.
- [x] Do not add independently pinned sessions or Projects to drawer panes in this update.
- [x] Make the current scope visible inside session-scoped and Project-scoped bodies when multiple panes can be read simultaneously.
- [x] Use one drawer-focused tab identity as the target for `drawer.toggle`, keyboard cycling, mobile selection, and reopen behavior.
- [x] Do not let activating a utility tab take terminal input ownership or change Project workspace focus.

## Target drawer layout contract

- [x] Add a JSX-free `frontend/src/drawerLayout.ts` module that owns drawer tree types, parsing, normalization, migration, traversal, activation, movement, splitting, reordering, ratio updates, and reset behavior.
- [x] Use a drawer-specific versioned contract equivalent to the following shape.

```ts
type DrawerSplitDirection = 'horizontal' | 'vertical'

type DrawerStack = {
  type: 'stack'
  id: string
  tabs: DrawerTabId[]
}

type DrawerSplit = {
  type: 'split'
  id: string
  direction: DrawerSplitDirection
  ratio: number
  first: DrawerNode
  second: DrawerNode
}

type DrawerNode = DrawerStack | DrawerSplit

type DrawerLayout = {
  version: 1
  root: DrawerNode
}

type DrawerProjectPresentation = {
  selected_tabs: Record<string, DrawerTabId>
  focused_tab: DrawerTabId
  desktop_expanded: boolean
}
```

- [x] Keep node IDs unique within one layout and generate browser-compatible IDs through the existing secure-context-tolerant UUID helper or a shared equivalent.
- [x] Clamp split ratios to the same safe `0.1` through `0.9` range used by the main workspace.
- [x] Keep all selected-tab state out of `DrawerLayout` so changing Projects never changes arrangement state.
- [x] Store each stack's selected tab in the active Project's `selected_tabs` map, keyed by stable stack ID.
- [x] Store the last drawer-focused tab and desktop expanded state in the same Project presentation record.
- [x] Implement depth-first `drawerStacks`, `drawerTabs`, `drawerStackForTab`, and flattened-order helpers.
- [x] Implement `activateDrawerTab` as a Project-presentation operation that never changes the global layout's membership, order, directions, or ratios.
- [x] Implement `normalizeDrawerProjectPresentation` against the current global layout.
- [x] Prefer the Project's `focused_tab` in its owning stack, then preserve a valid stored selection for each other stack, then select that stack's first tab.
- [x] Implement `reorderDrawerStack` with exact-set validation.
- [x] Implement `moveDrawerTabToStack` with an insertion index and source-stack collapse.
- [x] Implement `moveDrawerTabToSplit` for all four visual edges and source-stack collapse.
- [x] Implement `setDrawerSplitRatio` by stable node ID or structural path.
- [x] Implement `normalizeDrawerLayout` as the only entry point for storage, migration, and external state adoption.
- [x] Keep operations immutable so one rejected or cancelled drag cannot partially mutate live state.
- [x] Return the original object for semantic no-op operations where practical to avoid unnecessary renders and storage writes.

## Persistence and migration

### State authority

- [x] Keep one drawer layout device-local and global across Projects because the arrangement describes the user's utility workspace rather than Project data.
- [x] Keep drawer width device-local and shared across Projects as it is now.
- [x] Keep mobile drawer visibility transient and separate from saved desktop expansion.
- [x] Use the same global layout with a transient no-Project presentation so app-scoped tabs remain available before a Project exists.
- [x] Do not write drawer layout to Project layout, repository files, SQLite Project records, URLs, or browser session history.
- [x] Persist a valid normalized layout only after a completed user operation, not on every pointer move.
- [x] Persist the global layout under a dedicated device-local key such as `mux.drawer.layout.v1`.

### Project presentation schema

- [x] Introduce `mux.drawer.projects.v2` rather than changing the meaning of the existing v1 local-storage blob in place.
- [x] Store each Project's `selected_tabs`, `focused_tab`, and `desktop_expanded` in the v2 record without duplicating the global layout.
- [x] Preserve the existing v1 `tab` as the migrated `focused_tab` when it is still registered.
- [x] Seed the one global layout with one stack ordered by the normalized existing `drawerTabs` setting so the current user arrangement survives the upgrade.
- [x] Seed each migrated Project's only initial stack selection from its existing v1 `tab`.
- [x] Seed a new or previously unseen Project's presentation from the current global layout without changing that layout.
- [x] Migrate the former global `mux.drawer.tab.v1` only through the existing v1 migration path and then into v2.
- [x] Remove v1 and legacy keys only after valid global-layout and v2 Project-presentation serializations have succeeded.
- [x] Keep layout parsing tolerant of interrupted migration, invalid JSON, unknown fields, stale tab IDs, duplicate tabs, missing tabs, invalid ratios, duplicate node IDs, excess depth, and empty branches.
- [x] Keep Project-presentation parsing tolerant of unknown stack IDs, stale tab IDs, missing stack selections, and invalid focused tabs.
- [x] Prune v2 records for deleted Projects through the existing Project-state cleanup path.
- [x] Reconcile every stored Project presentation after a global layout change without changing any Project's expanded state.
- [x] Preserve a Project's focused tab in its new owning stack when a global tab move changes that tab's location.
- [x] Preserve valid stack selections where possible and use the stack's first tab only when a saved selection is no longer in that stack.
- [x] Add a global layout reset that replaces the device's drawer tree with one canonical stack and reconciles every Project presentation.
- [x] Rename or redefine `drawer.resetTabs` as `drawer.resetLayout` in the command catalog, keybinding migration, menu copy, tests, and documentation.
- [x] If backward command compatibility is needed, accept `drawer.resetTabs` as a hidden alias that invokes the same global device layout reset.

### Existing shared tab-order setting

- [x] Use the existing server-persisted `drawerTabs` order only as migration input for the first global device-local layout.
- [x] Stop saving drawer tree edits into the shared flat `drawerTabs` order because a recursive device-local layout cannot be represented by one server-synced flat list.
- [x] Remove the settings-change adoption path that could flatten or overwrite an established v2 drawer tree.
- [x] Keep backend acceptance of the legacy `drawerTabs` settings domain for compatibility unless repository-wide reference inspection proves it can be safely removed.
- [x] Remove obsolete frontend write helpers only after migration no longer depends on them.
- [x] Update comments and tests that currently describe drawer order as one server-persisted list shared by the strip and launcher rail.

## Desktop rendering architecture

- [x] Refactor `UtilityDrawer.tsx` into a shell, recursive split renderer, pane stack renderer, and singleton tab-body dispatcher.
- [x] Keep the shell responsible for the mobile scrim, desktop outer resizer, global close action, Escape behavior, and drawer-level accessibility label.
- [x] Render every `DrawerSplit` as two flex branches plus one accessible draggable separator.
- [x] Render every `DrawerStack` with its own non-wrapping tab rail and exactly one active body.
- [x] Resolve each stack's active body from the active Project's normalized presentation rather than storing selection in the global layout.
- [x] Give every branch and body `min-width: 0`, `min-height: 0`, and bounded overflow so deeply split layouts do not force the application beyond the viewport.
- [x] Keep each pane tab rail independently horizontally scrollable.
- [x] Scroll a newly activated tab into view only within its owning rail.
- [x] Keep one close-drawer control for the complete drawer rather than placing a drawer-close button in every stack.
- [x] Position the close control in stable drawer chrome that does not consume or obscure a pane's tab insertion targets.
- [x] Do not add per-tab close controls because utility tabs cannot be removed or hidden from the singleton registry.
- [x] Give the focused drawer pane a restrained focus indicator distinct from the active tab indicator.
- [x] Ensure multiple visible bodies use their own internal scrolling and never make the whole application page scroll.

## Tab-body lifecycle and ownership

- [x] Mount only the active body in each desktop drawer stack.
- [x] Keep the singleton Notes host mounted and hidden when its owning stack activates another tab, preserving cursor position, undo history, save-queue ownership, and insert targeting.
- [x] Mount the Notes host in exactly one structural location derived from the singleton Notes tab.
- [x] Preserve the rule that one note editor exists in only one browser host at a time.
- [x] Preserve the current placeholder and claim behavior when a note is owned by the drawer instead of a Project workspace pane.
- [x] Release drawer note ownership when the complete drawer closes, exactly as current behavior requires.
- [x] Audit every newly simultaneous active body for timers, WebSockets, event listeners, fetch loops, global IDs, focus effects, and assumptions that it is the drawer's only body.
- [x] Keep Processes on the fleet sample already owned by `App` and do not introduce another process poll per visible pane.
- [x] Keep Transcript event-driven and preserve its scroll state per focused session.
- [x] Keep Git refresh bounded and event-driven according to the Git feature contract.
- [x] Ensure hidden inactive bodies do not retain subscriptions unless a documented state-preservation requirement needs them.
- [x] Do not log clipboard contents, prompts, transcripts, note text, file contents, or other utility-body payloads while adding lifecycle diagnostics.

## Pointer drag and drop

### Shared gesture contract

- [x] Continue using the app's pointer gesture implementation rather than native HTML drag-and-drop.
- [x] Start dragging only after the existing 5 px movement threshold.
- [x] Claim pointer ownership through `pointerDragClaim.ts` only after the drag threshold is crossed.
- [x] Use one fixed-position drag ghost and direct DOM drop indicators rather than rerendering the complete drawer on every pointer move.
- [x] Keep the prospective layout in a ref during the gesture and commit it once on pointer-up.
- [x] Cancel without persistence on Escape, pointer cancel, lost pointer capture, window blur, invalid target, or responsive transition.
- [x] Suppress the click produced by the pointer-up that completes a drag.
- [x] Disable iframe or embedded-preview pointer interception during a running drawer drag if any future utility body embeds such content.

### Reorder and join behavior

- [x] Treat gaps within the source rail as before/after reorder targets.
- [x] Treat gaps within another pane's rail as precise cross-pane insertion targets.
- [x] Treat the center or tab-rail body of another pane as a join target when no precise insertion gap is selected.
- [x] Moving to another pane must remove the tab from its source pane and activate it in the destination pane for the active Project.
- [x] Reordering within one pane must preserve the active Project's selection for that pane unless the moved tab is intentionally activated by the completed drag contract.
- [x] Do not split when the pointer is clearly over a rail insertion target.
- [x] Show an insertion line for reorder and join operations.

### Edge split behavior

- [x] Show left, right, top, and bottom edge targets over every eligible drawer pane body.
- [x] Interpret left and right as `horizontal` splits with the new singleton-tab stack placed first or second respectively.
- [x] Interpret top and bottom as `vertical` splits with the new singleton-tab stack placed first or second respectively.
- [x] Place the top target inside the pane body after the target pane's tab rail so tab reordering remains reachable.
- [x] Use clear edge overlays that preview the exact region the moved tab will occupy.
- [x] Permit splitting an existing multi-tab source pane by moving one tab to an edge of that same pane.
- [x] Treat dropping the only tab of a pane onto an edge of that same pane as a no-op because it cannot produce two non-empty branches.
- [x] Allow repeated edge splits to produce arbitrary nested grids without special-casing rows or columns.
- [x] Add explicit acceptance coverage for constructing a 3x3 grid entirely through tab drags.

## Split resizing and drawer width

- [x] Give every internal split separator pointer resizing with the same ratio semantics as the main workspace.
- [x] Persist an internal ratio only on pointer-up while rendering live ratio feedback during the drag.
- [x] Make every internal separator keyboard focusable.
- [x] Support arrow-key adjustment, Home and End bounded movement, and double-click reset to `0.5`.
- [x] Expose `role="separator"`, orientation, value minimum, value maximum, and current value.
- [x] Keep internal split resizing independent from the outer drawer width resizer.
- [x] Make the outer drawer resizer keyboard focusable and give it arrow-key adjustment, Home and End bounds, and double-click reset to the default width.
- [x] Replace the fixed 620 px drawer maximum with a viewport-aware maximum that lets the user allocate most of a desktop window to the drawer while preserving the launcher rail and a small usable Project workspace.
- [x] Recalculate the legal outer width when the viewport, sidebar width, sidebar collapsed state, UI scale, or launcher-rail width changes.
- [x] Clamp a restored width only for the current viewport and do not overwrite the user's stored larger width merely because one window is temporarily narrow.
- [x] Keep the drawer in flow on desktop so widening it shrinks rather than covers the Project workspace.
- [x] Do not prohibit side-by-side panes or a 3x3 grid at a particular width.
- [x] Let content become compact and independently scroll rather than silently rewriting the user's valid layout.

## Launcher rail and named entry points

- [x] Keep the desktop outer utility rail as a launcher and drawer toggle, not as another tab owner or content mount.
- [x] Document in code that a launcher button mirrors a singleton tab identity but is not a second layout location.
- [x] Render launcher buttons in the global device-local layout's depth-first tab order.
- [x] Use the same global order with the transient no-Project presentation.
- [x] Remove ambiguous reorder dragging from the outer launcher rail after pane-local ordering becomes authoritative.
- [x] Clicking a launcher button must find the tab's owning stack, activate that tab, mark it as drawer-focused, and open the drawer without moving it.
- [x] Preserve the current toggle behavior by closing the complete drawer when its launcher is clicked while that exact tab is already visible and drawer-focused.
- [x] Make named actions such as `drawer.files`, `drawer.git`, `clipboard.open`, queue chips, notification buttons, and Browse Files find and activate the existing singleton tab without changing its pane membership.
- [x] Keep named open actions non-toggling when the caller explicitly asked to show a surface.
- [x] Make `drawer.toggle` reopen the saved layout with the last drawer-focused tab and pane.
- [x] Make keyboard next and previous cycle only within the focused drawer pane on desktop.
- [x] Keep badges on Queue and Alerts synchronized in every rendering without creating extra tab state.

## Mobile projection

- [x] At the existing mobile breakpoint, flatten all drawer tab IDs depth-first into one non-wrapping rail.
- [x] Render exactly one ordinary utility body at a time on mobile.
- [x] Preserve the hidden singleton Notes host behavior when it owns an editor.
- [x] Select the active Project's saved drawer-focused tab when it remains valid, then use that Project's first valid stack selection in depth-first order, then the first flattened tab.
- [x] Selecting a mobile tab must update the active Project's owning-stack selection and drawer-focused tab without changing the global layout.
- [x] Mobile must not rewrite split directions, ratios, stack IDs, tab membership, or per-stack ordering.
- [x] Returning to desktop must restore the saved recursive tree.
- [x] Do not expose edge split targets, split separators, geometry commands, or drag reordering on mobile.
- [x] Preserve the current mobile overlay, scrim, 90vw width, mutual exclusion with the navigation sidebar, keyboard dismissal, and swipe-away behavior.
- [x] Keep mobile action completion behavior unchanged: actions that target the covered workspace close the drawer, while inert reading and a drawer-owned Notes editor follow their existing exceptions.
- [x] Keep the mobile close button fixed while the flattened tab rail scrolls.
- [x] Preserve title-mode and icon-mode tab selection visibility with `scrollIntoView` in the flattened rail.

## Icon or title display setting

### Configuration contract

- [x] Add a hot-reloadable config field named `drawer_tab_display` with accepted values `icon` and `title`.
- [x] Default `drawer_tab_display` to `icon` so existing installations retain their current density.
- [x] Validate invalid values in `src/swe_mux/config.py` and preserve the previous valid value on a rejected update.
- [x] Include the field in config loading, TOML serialization, config update responses, Settings draft typing, App config typing, and live config application.
- [x] Add `Side panel tabs` to Settings > Appearance with explicit `Icons` and `Titles` choices.
- [x] Make the setting searchable through the existing Settings search harvesting path.
- [x] Apply the changed value immediately without requiring a daemon restart or page reload.

### Rendering behavior

- [x] Apply the selected display mode to every drawer pane rail, the mobile flattened rail, and the desktop outer launcher rail.
- [x] In icon mode, render `DRAWER_TAB_ICONS` exactly as today and retain accessible labels and descriptive tooltips.
- [x] In title mode, render the short `DrawerTab.label` text rather than the longer explanatory tooltip string.
- [x] Render either the icon or the title as the primary tab mark, not both.
- [x] Preserve `aria-label`, `title`, selected state, focus state, drag state, and Queue or Alerts badges in both modes.
- [x] Keep title-mode pane rails on one horizontally scrollable line with no wrapping.
- [x] Expand the desktop outer launcher column in title mode through a CSS custom property rather than clipping labels into the current 40 px column.
- [x] Truncate unusually long future labels in the launcher rail with an ellipsis while preserving the full accessible name and tooltip.
- [x] Include launcher width in desktop drawer-width clamping and workspace grid calculations.
- [x] Keep the mobile title rail horizontally scrollable and keep its close control outside the scroller.
- [x] Do not make the icon/title choice alter logical ordering, pane membership, active tabs, layout geometry, or stored drawer layout.

## Accessibility and keyboard behavior

- [x] Give each pane rail `role="tablist"` and a unique accessible label derived from its pane position or stable identity.
- [x] Give every utility tab `role="tab"`, `aria-selected`, an accessible name, and a relationship to its active panel.
- [x] Give active bodies `role="tabpanel"` with stable IDs and `aria-labelledby` links.
- [x] Implement roving tab focus within each pane rail so only one tab per rail is in the sequential keyboard order.
- [x] Support Left and Right arrow navigation within the focused rail without moving tab membership.
- [x] Keep ordinary Tab traversal available to enter the active utility body.
- [x] Do not steal focus-navigation keys from inputs, editors, filters, or utility body controls.
- [x] Keep Escape closing the complete drawer when focus is inside it and no child modal or editor-owned Escape behavior takes precedence.
- [x] Announce completed cross-pane moves and splits through a concise live region for keyboard and assistive-technology users.
- [x] Provide keyboard commands or a focused-tab menu for moving the focused utility tab left, right, up, down, and into an adjacent pane because pointer drag cannot be the only geometry control.
- [x] Make directional move availability derive from the live drawer tree and explain disabled directions.
- [x] Preserve visible focus outlines in icon and title modes.
- [x] Respect reduced-motion preferences for drag ghosts, overlays, and responsive transitions.

## Failure handling, reconciliation, and performance

- [x] Normalize state before every render boundary and before every persistence write.
- [x] Recover an invalid stored tree to one valid stack containing every registered tab exactly once.
- [x] Surface storage-write failures without discarding the valid in-memory layout for the current browser session.
- [x] Do not let a failed settings save revert layout geometry or pane membership.
- [x] Ensure a Project switch during a drag cancels the drag before restoring the other Project's selections and expanded state.
- [x] Ensure crossing the mobile breakpoint during a drag cancels the drag and clears every indicator and ghost.
- [x] Ensure removing the active Project prunes only its Project presentation and leaves the global layout unchanged.
- [x] Keep pointer-move work limited to geometry inspection, refs, and direct indicator updates.
- [x] Memoize or isolate pane bodies so resizing or dragging one split does not remount unrelated active bodies.
- [x] Use stable keys based on tab and pane identity so ordinary layout updates preserve component state when a tab stays in the same host.
- [x] Deliberately remount a moved tab body when its pane host changes unless that body has an explicit external state owner.
- [x] Preserve Notes safely through its external save queue and singleton hidden host rather than relying on accidental component key retention.
- [x] Audit simultaneous panels on a maximally split layout for duplicate polling and event-listener leaks.
- [x] Avoid adding backend logging for ordinary presentation changes.
- [x] Add diagnostics only for actionable parse, migration, persistence, or invariant failures, without logging user content.

## Implementation checklist by code area

### Pure drawer state

- [x] Add `frontend/src/drawerLayout.ts` with the complete typed model and pure operations.
- [x] Move global layout and per-Project presentation parsing and serialization out of `drawerTabs.ts` if doing so keeps registry, arrangement, and selection responsibilities separate.
- [x] Keep `drawerTabs.ts` as the canonical registry for IDs, labels, descriptions, scope, and default order.
- [x] Replace flat-order helpers that remain necessary with layout-aware flattening and migration helpers.
- [x] Remove dead flat-order code only after migration and compatibility tests prove it is unused.

### App state and commands

- [x] Replace `drawerOrder`, `orderedDrawerTabs`, and one global `drawerTabId` rendering assumptions in `App.tsx` with one normalized global device layout plus the active Project's normalized presentation.
- [x] Add one atomic `commitDrawerLayout` path that updates global local state first, reconciles all Project presentations, and writes normalized local storage once per completed operation.
- [x] Add a separate Project-presentation update path for selected tabs, focused tab, and expanded state that never writes the global layout.
- [x] Route every existing drawer entry point through layout-aware find-and-activate helpers.
- [x] Cancel active drawer drags on Project changes, drawer closure, breakpoint changes, and unmount.
- [x] Update command availability and labels for layout reset and keyboard directional moves.
- [x] Preserve existing Project-targeted drawer actions so a cross-Project Files, Notes, or Queue action activates the target Project's existing singleton tab.
- [x] Apply `drawer_tab_display` from initial config and config-change events.

### Components and styles

- [x] Refactor `UtilityDrawer.tsx` around recursive nodes without duplicating the large tab-body prop contract per pane.
- [x] Add focused components or context for the shell, split node, stack node, tab rail, and body dispatcher where that reduces rerenders and prop drift.
- [x] Reuse `DRAWER_TAB_ICONS` from one source in icon mode.
- [x] Add split, pane, rail, edge-target, focus, title-mode, and separator styles to `frontend/src/style.css`.
- [x] Replace fixed utility-rail grid columns with `--utility-rail-width` in every expanded and collapsed sidebar grid template.
- [x] Verify all utility bodies remain usable with nested flex sizing and `min-width: 0` or `min-height: 0` constraints.
- [x] Keep selectors scoped to drawer classes so Project workspace pane styling does not leak into the independent drawer tree.

### Configuration and Settings

- [x] Add the config default, validation, serialization, and hot-reload classification in `src/swe_mux/config.py`.
- [x] Add backend config tests for default, valid updates, invalid updates, and round-trip persistence.
- [x] Add the field to `Settings.tsx` and App's narrow config type.
- [x] Add the Appearance control and immediate application behavior.
- [x] Update Settings search expectations if harvested labels or section mappings change.

## Automated verification

### Drawer layout unit tests

- [x] Add `frontend/test/drawerLayout.test.ts` and register it in `frontend/test/all.ts`.
- [x] Test the canonical one-stack default.
- [x] Test flat-order migration into one global device-local layout.
- [x] Test v1 Project tab migration into v2 per-Project selections without duplicating the global layout.
- [x] Test valid global-layout and v2 Project-presentation parse and serialization round trips.
- [x] Test invalid JSON and invalid root fallback.
- [x] Test duplicate tab repair.
- [x] Test missing registered tab insertion without moving existing tabs.
- [x] Test unknown tab removal.
- [x] Test invalid per-Project stack-selection repair.
- [x] Test duplicate node-ID repair.
- [x] Test ratio clamping.
- [x] Test empty-stack and single-branch split collapse.
- [x] Test maximum-depth recovery.
- [x] Test same-stack reorder at the first, middle, and last insertion positions.
- [x] Test cross-stack move and exact insertion position.
- [x] Test left, right, top, and bottom splits.
- [x] Test source-stack collapse when its last tab moves.
- [x] Test same-pane sole-tab edge drop as a no-op.
- [x] Test repeated splits that produce a 3x3 visual grid.
- [x] Test depth-first flatten order.
- [x] Test activation without membership changes.
- [x] Test switching Projects preserves the global layout while restoring different selections and expanded states.
- [x] Test a global layout change reconciles every Project presentation deterministically.
- [x] Test global layout reset and Project-presentation reconciliation.
- [x] Test Project-state pruning leaves the global layout unchanged.
- [x] Run a deterministic sequence of mixed reorder, move, split, activate, and ratio operations and assert after every operation that every registered tab occurs exactly once.

### Component and interaction tests

- [x] Update existing drawer tests that assume one strip or one active body.
- [x] Test that each desktop stack renders one active body and its own rail.
- [x] Test that a tab moved between panes has one button in drawer pane rails and one mounted body total.
- [x] Test that the outer launcher is a mirror control and never mounts another body.
- [x] Test drag cancellation paths leave layout and storage unchanged.
- [x] Test same-rail reorder, cross-rail insert, center join, and all edge splits.
- [x] Test pointer-up click suppression.
- [x] Test internal and outer separator keyboard controls.
- [x] Test named drawer commands activate the tab in its current owner without moving it.
- [x] Test desktop next and previous cycling stays inside the focused drawer pane.
- [x] Test Queue and Alerts badges in icon and title modes.
- [x] Test Notes remains mounted once while inactive and never duplicates a note editor.
- [x] Test Files still opens or drags files into the Project workspace without joining the Project layout itself.
- [x] Test mobile action completion and Notes exceptions remain unchanged.

### Mobile projection tests

- [x] Add pure tests for flattening a nested drawer tree into one ordered mobile rail.
- [x] Test mobile selection fallback when the focused tab is invalid.
- [x] Test mobile selection updates only the active Project's owning-stack selection and focused tab.
- [x] Test mobile selection leaves the global split directions, ratios, stack IDs, membership, and order unchanged.
- [x] Test returning to desktop restores the exact tree.
- [x] Test mobile exposes no drag or separator controls.
- [x] Test icon and title rails remain one-line scrollable projections.

### Settings and configuration tests

- [x] Test `drawer_tab_display` defaults to `icon`.
- [x] Test `icon` and `title` updates are hot reloadable and persist through config reload.
- [x] Test invalid values are rejected.
- [x] Test App applies the field on initial load and on a live config update.
- [x] Test Settings search finds `Side panel tabs`, `Icons`, and `Titles` under Appearance.
- [x] Update any config snapshots, exported-config tests, or frontend contract assertions affected by the new field.

## Manual acceptance matrix

- [x] Start from a clean profile and confirm the drawer is unchanged in appearance: closed by default, one pane when opened, icons by default, and every utility tab present once.
- [x] Reorder several tabs in one rail and confirm reload preserves the global order for every Project on that device.
- [x] Move tabs between two existing panes and confirm the source and destination rails update atomically.
- [x] Create left, right, top, and bottom splits through drag targets.
- [x] Build and use a 3x3 layout with nine singleton tabs.
- [x] Confirm the remaining registered tabs stay in exactly one other rail and no utility body is duplicated.
- [x] Resize every internal boundary in the 3x3 layout and confirm ratios survive reload.
- [x] Widen the outer drawer beyond the former 620 px limit and confirm the Project workspace remains visible and uncovered.
- [x] Narrow the outer drawer and confirm no split is silently collapsed or reoriented.
- [x] Configure different selected tabs and expanded states in two Projects, switch repeatedly, and confirm the global layout and width never change while each Project restores its own presentation.
- [x] Delete a Project and confirm its saved presentation is pruned without changing the global layout.
- [x] Use every outer launcher button and named drawer command with a multi-pane layout and confirm each activates the existing owner rather than moving or duplicating the tab.
- [x] Confirm Clipboard, Commands, Prompts, Queue, and Transcript all follow the focused session while simultaneously visible in separate panes.
- [x] Confirm Files, Notes, Context, Git, and Processes follow the active Project.
- [x] Confirm Alerts remains app-scoped.
- [x] Open a note in the drawer, switch the Notes pane to another tab, insert from Clipboard, and confirm the same editor retains cursor, undo history, save state, and insert targeting.
- [x] Move the note to a Project workspace pane and confirm only one editor remains mounted.
- [x] Switch Appearance from Icons to Titles and confirm every pane rail, mobile rail, and outer launcher updates immediately.
- [x] Confirm title mode preserves badges, tooltips, accessible labels, drag behavior, scroll behavior, and layout geometry.
- [x] Confirm title mode expands the outer launcher column without covering or horizontally overflowing the application.
- [x] Confirm mobile flattens the 3x3 layout into one rail and one visible body.
- [x] Select several tabs on mobile, return to desktop, and confirm geometry and membership are unchanged while the selected tabs become active in their owning stacks.
- [x] Confirm mobile does not offer reorder, split, or separator interaction.
- [x] Confirm responsive breakpoint changes and Project switches cancel an in-progress drag cleanly.
- [x] Complete keyboard-only activation, cycling, directional move, split resizing, outer resizing, reset, and drawer closure.
- [x] Inspect with a screen reader or accessibility tree and confirm tablist, tab, tabpanel, separator, selected-state, and live-move announcements are correct.
- [x] Leave several active utility panes open during session activity and confirm there are no duplicate polls, duplicate events, runaway rerenders, or focus theft.

## Documentation updates

- [x] Update `.docs/design/features/ui.md` with the recursive independent drawer layout, singleton ownership, global device-local arrangement, per-Project selections and expanded state, launcher semantics, scope-following behavior, responsive projection, sizing, and icon/title setting.
- [x] Update `.docs/design/features/workspace-layout.md` to state that the utility drawer reuses split interaction language but remains a separate device-local layout and separate mobile projection.
- [x] Update `.docs/technical/frontend/workspace-state.md` with the global layout authority, per-Project presentation authority, local-storage schemas, migration, focused-tab behavior, mobile derivation, and Notes lifecycle.
- [x] Update `.docs/technical/frontend/packages.md` with the new drawer layout module and refactored component ownership.
- [x] Update `.docs/design/interfaces.md` if it enumerates config fields or hot-reload behavior.
- [x] Update `.docs/design/features/project-resources.md`, `.docs/design/features/prompt-queue.md`, or `.docs/design/features/history.md` only where the implementation changes their documented host or lifecycle contracts.
- [x] Update `.docs/CLAUDE.md` when archiving this plan so its active-checklist route does not point at the former development path.
- [x] Verify every file path added to technical documentation exists.
- [x] Keep this plan in `.docs/development/` until every checkbox is complete, then move it to `.docs/development/archive/` without renaming it.

## Final verification and delivery

- [x] Run `uv run pytest tests -q -m "not live_agent and not live_subagent and not live_telemetry and not live_quota"`.
- [x] Run `uv run ruff check src/swe_mux tests packaging`.
- [x] Run `uv run mypy`.
- [x] Run `npx tsc --noEmit` from `frontend/`.
- [x] Run `npm test` from `frontend/`.
- [x] Run `npm run build` from `frontend/` and confirm the production bundle succeeds.
- [x] Review the final diff for accidental Project workspace coupling, duplicate tab ownership, stale flat-order code, native drag handlers, content logging, unrelated edits, and generated static output.
- [x] Confirm no generated `src/swe_mux/static` assets are staged because the frontend build output is gitignored deployment output.
- [x] Running-app application was intentionally not performed, so served-asset hash classification was not required.
- [x] The source-run reload branch was not selected because the update was not applied to the running app.
- [x] The frozen-app redeploy branch was not selected because the update was not applied to the running app.
- [x] Never start another daemon, run a frozen app, or trigger redeploy from a worktree.
- [x] Never use `muxd --shutdown`, kill `swe-mux-supervisor.exe`, or terminate swe-mux processes to apply this update.
- [x] Live terminal sessions remained untouched because no reload or redeploy flow was selected.
- [x] Mark every completed checkbox in this document and archive it only after the implementation, tests, documentation, manual acceptance, and delivery checks are all complete.
