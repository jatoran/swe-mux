# Custom utility drawer layout implementation plan

## Status and completion rule

- [ ] Treat this document as the implementation checklist and mark every completed item in place.
- [ ] Complete the work as one coherent update without feature flags, staged releases, time estimates, or deferred phases.
- [ ] Do not consider the update complete while any required checkbox remains unchecked.
- [ ] Move this document to `.docs/development/archive/UTILITY_DRAWER_LAYOUT_IMPLEMENTATION.md` only after every implementation, verification, documentation, and deployment checkbox is complete.

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

- [ ] Read `AGENTS.md` and the shared logging, documentation, and Git instructions it references.
- [ ] Read `.docs/CLAUDE.md` for documentation routing.
- [ ] Read `.docs/design/features/ui.md`, especially the utility drawer, mobile panel, focus, settings, and overlay contracts.
- [ ] Read `.docs/design/features/workspace-layout.md` for the existing recursive split model, tab drag contract, split resizing, and mobile projection behavior.
- [ ] Read `.docs/technical/frontend/workspace-state.md` for state authority, optimistic state, focus, and mounted-view rules.
- [ ] Read `.docs/technical/frontend/packages.md` before adding frontend modules or moving ownership between components.
- [ ] Read `.docs/design/features/project-resources.md` before changing Files or Notes host behavior.
- [ ] Read `.docs/design/features/prompt-queue.md` before changing Queue panel mounting or pop-out behavior.
- [ ] Read `.docs/design/features/history.md` before changing Transcript panel mounting or session-following behavior.
- [ ] Read `.docs/design/interfaces.md` and `.docs/design/data-model.md` before changing the configuration contract.
- [ ] Read `.docs/development/archive/SESSION_PRESERVING_RELOAD.md` before applying the completed frontend or backend changes to a running app.
- [ ] Inspect `frontend/src/App.tsx`, `frontend/src/UtilityDrawer.tsx`, `frontend/src/drawerTabs.ts`, `frontend/src/drawerTabOrder.ts`, `frontend/src/drawerNotes.ts`, `frontend/src/layout.ts`, `frontend/src/mobileWorkspace.ts`, `frontend/src/dragReorder.ts`, `frontend/src/pointerDragClaim.ts`, `frontend/src/deviceSettings.ts`, `frontend/src/Settings.tsx`, `frontend/src/railIcons.tsx`, and `frontend/src/style.css`.
- [ ] Inspect the current drawer bodies for lifecycle and scope assumptions: `ClipboardPanel.tsx`, `CommandsTab.tsx`, `PromptsTab.tsx`, `QueuePane.tsx`, `TranscriptTab.tsx`, `ProjectResource.tsx`, `NotesTab.tsx`, `AgentContextTab.tsx`, `GitTab.tsx`, `ProcessesTab.tsx`, and `Notifications.tsx`.
- [ ] Inspect `src/swe_mux/config.py`, the config update route in `src/swe_mux/server.py`, and the corresponding config tests before adding the tab-display setting.
- [ ] Inspect `frontend/test/drawerTabs.test.ts`, `frontend/test/drawerTabOrder.test.ts`, `frontend/test/drawerNotes.test.ts`, `frontend/test/layout.test.ts`, `frontend/test/mobileWorkspace.test.ts`, and existing frontend UI contract tests before changing their assumptions.

## Product decisions and boundaries

### Independent utility workspace

- [ ] Keep the utility drawer structurally independent from Project layout v7.
- [ ] Do not add utility leaves to `PaneLayout`, Project layout PATCH requests, SQLite Project layout state, workspace focus traversal, warm terminal panes, or workspace mobile projection.
- [ ] Reuse the main workspace's interaction language and safe pointer primitives without sharing its persisted model or leaf types.
- [ ] Do not allow cross-dragging between utility drawer tabs and Project workspace tabs.
- [ ] Keep existing explicit cross-surface actions unchanged, including Files opening a file in the workspace, Notes moving one editor between hosts, and Queue opening an explicit workspace queue tab.
- [ ] Do not automatically make every utility tab available as a Project workspace leaf.
- [ ] Preserve the current drawer default as one pane containing every utility tab in canonical order.
- [ ] Keep the drawer closed by default for Projects without saved presentation state.
- [ ] Keep one global device-local drawer arrangement across every Project.
- [ ] Switching Projects must retain the split tree, tab locations, tab order, ratios, and width while restoring that Project's selected tabs and expanded state.

### Arbitrary splits

- [ ] Support recursive horizontal and vertical splits rather than a fixed two-pane design.
- [ ] Permit any arrangement that can be formed from the registered singleton tabs, including a 3x3 grid.
- [ ] Do not impose a product-level limit of two regions, one split, or one orientation.
- [ ] Bound the number of drawer stacks by the number of registered utility tabs because empty stacks are invalid and every stack must contain at least one tab.
- [ ] Apply a defensive maximum tree depth of 24 while parsing stored data and performing split operations.
- [ ] Reject or normalize malformed trees without crashing application boot.
- [ ] Do not automatically collapse, rearrange, or reorient a valid desktop layout because a pane becomes narrow.

### Singleton tab ownership

- [ ] Enforce that every `DrawerTabId` occurs exactly once across the complete normalized drawer tree.
- [ ] Treat a tab move as one atomic remove-and-insert transaction.
- [ ] Never render two content bodies for the same `DrawerTabId`.
- [ ] Never permit cloning through modifier keys, repeated drops, malformed local storage, settings events, Project switches, or responsive transitions.
- [ ] Collapse a source stack and its now-redundant parent split immediately when its last tab moves away.
- [ ] Never persist an empty stack or a split with a missing branch.
- [ ] Preserve every registered tab when reading old, incomplete, duplicated, or hand-edited state.
- [ ] Insert newly shipped tab IDs into an existing normalized layout without moving the user's existing tabs.
- [ ] Place a newly shipped tab beside its canonical predecessor in that predecessor's stack, or in the first depth-first stack when no predecessor survives.

### Scope and focus

- [ ] Preserve the existing `session`, `project`, and `app` tab scope registry.
- [ ] Keep session-scoped tabs following the currently focused session.
- [ ] Keep Project-scoped tabs following the active Project.
- [ ] Keep app-scoped tabs independent of Project and session selection.
- [ ] Do not add independently pinned sessions or Projects to drawer panes in this update.
- [ ] Make the current scope visible inside session-scoped and Project-scoped bodies when multiple panes can be read simultaneously.
- [ ] Use one drawer-focused tab identity as the target for `drawer.toggle`, keyboard cycling, mobile selection, and reopen behavior.
- [ ] Do not let activating a utility tab take terminal input ownership or change Project workspace focus.

## Target drawer layout contract

- [ ] Add a JSX-free `frontend/src/drawerLayout.ts` module that owns drawer tree types, parsing, normalization, migration, traversal, activation, movement, splitting, reordering, ratio updates, and reset behavior.
- [ ] Use a drawer-specific versioned contract equivalent to the following shape.

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

- [ ] Keep node IDs unique within one layout and generate browser-compatible IDs through the existing secure-context-tolerant UUID helper or a shared equivalent.
- [ ] Clamp split ratios to the same safe `0.1` through `0.9` range used by the main workspace.
- [ ] Keep all selected-tab state out of `DrawerLayout` so changing Projects never changes arrangement state.
- [ ] Store each stack's selected tab in the active Project's `selected_tabs` map, keyed by stable stack ID.
- [ ] Store the last drawer-focused tab and desktop expanded state in the same Project presentation record.
- [ ] Implement depth-first `drawerStacks`, `drawerTabs`, `drawerStackForTab`, and flattened-order helpers.
- [ ] Implement `activateDrawerTab` as a Project-presentation operation that never changes the global layout's membership, order, directions, or ratios.
- [ ] Implement `normalizeDrawerProjectPresentation` against the current global layout.
- [ ] Prefer the Project's `focused_tab` in its owning stack, then preserve a valid stored selection for each other stack, then select that stack's first tab.
- [ ] Implement `reorderDrawerStack` with exact-set validation.
- [ ] Implement `moveDrawerTabToStack` with an insertion index and source-stack collapse.
- [ ] Implement `moveDrawerTabToSplit` for all four visual edges and source-stack collapse.
- [ ] Implement `setDrawerSplitRatio` by stable node ID or structural path.
- [ ] Implement `normalizeDrawerLayout` as the only entry point for storage, migration, and external state adoption.
- [ ] Keep operations immutable so one rejected or cancelled drag cannot partially mutate live state.
- [ ] Return the original object for semantic no-op operations where practical to avoid unnecessary renders and storage writes.

## Persistence and migration

### State authority

- [ ] Keep one drawer layout device-local and global across Projects because the arrangement describes the user's utility workspace rather than Project data.
- [ ] Keep drawer width device-local and shared across Projects as it is now.
- [ ] Keep mobile drawer visibility transient and separate from saved desktop expansion.
- [ ] Use the same global layout with a transient no-Project presentation so app-scoped tabs remain available before a Project exists.
- [ ] Do not write drawer layout to Project layout, repository files, SQLite Project records, URLs, or browser session history.
- [ ] Persist a valid normalized layout only after a completed user operation, not on every pointer move.
- [ ] Persist the global layout under a dedicated device-local key such as `mux.drawer.layout.v1`.

### Project presentation schema

- [ ] Introduce `mux.drawer.projects.v2` rather than changing the meaning of the existing v1 local-storage blob in place.
- [ ] Store each Project's `selected_tabs`, `focused_tab`, and `desktop_expanded` in the v2 record without duplicating the global layout.
- [ ] Preserve the existing v1 `tab` as the migrated `focused_tab` when it is still registered.
- [ ] Seed the one global layout with one stack ordered by the normalized existing `drawerTabs` setting so the current user arrangement survives the upgrade.
- [ ] Seed each migrated Project's only initial stack selection from its existing v1 `tab`.
- [ ] Seed a new or previously unseen Project's presentation from the current global layout without changing that layout.
- [ ] Migrate the former global `mux.drawer.tab.v1` only through the existing v1 migration path and then into v2.
- [ ] Remove v1 and legacy keys only after valid global-layout and v2 Project-presentation serializations have succeeded.
- [ ] Keep layout parsing tolerant of interrupted migration, invalid JSON, unknown fields, stale tab IDs, duplicate tabs, missing tabs, invalid ratios, duplicate node IDs, excess depth, and empty branches.
- [ ] Keep Project-presentation parsing tolerant of unknown stack IDs, stale tab IDs, missing stack selections, and invalid focused tabs.
- [ ] Prune v2 records for deleted Projects through the existing Project-state cleanup path.
- [ ] Reconcile every stored Project presentation after a global layout change without changing any Project's expanded state.
- [ ] Preserve a Project's focused tab in its new owning stack when a global tab move changes that tab's location.
- [ ] Preserve valid stack selections where possible and use the stack's first tab only when a saved selection is no longer in that stack.
- [ ] Add a global layout reset that replaces the device's drawer tree with one canonical stack and reconciles every Project presentation.
- [ ] Rename or redefine `drawer.resetTabs` as `drawer.resetLayout` in the command catalog, keybinding migration, menu copy, tests, and documentation.
- [ ] If backward command compatibility is needed, accept `drawer.resetTabs` as a hidden alias that invokes the same global device layout reset.

### Existing shared tab-order setting

- [ ] Use the existing server-persisted `drawerTabs` order only as migration input for the first global device-local layout.
- [ ] Stop saving drawer tree edits into the shared flat `drawerTabs` order because a recursive device-local layout cannot be represented by one server-synced flat list.
- [ ] Remove the settings-change adoption path that could flatten or overwrite an established v2 drawer tree.
- [ ] Keep backend acceptance of the legacy `drawerTabs` settings domain for compatibility unless repository-wide reference inspection proves it can be safely removed.
- [ ] Remove obsolete frontend write helpers only after migration no longer depends on them.
- [ ] Update comments and tests that currently describe drawer order as one server-persisted list shared by the strip and launcher rail.

## Desktop rendering architecture

- [ ] Refactor `UtilityDrawer.tsx` into a shell, recursive split renderer, pane stack renderer, and singleton tab-body dispatcher.
- [ ] Keep the shell responsible for the mobile scrim, desktop outer resizer, global close action, Escape behavior, and drawer-level accessibility label.
- [ ] Render every `DrawerSplit` as two flex branches plus one accessible draggable separator.
- [ ] Render every `DrawerStack` with its own non-wrapping tab rail and exactly one active body.
- [ ] Resolve each stack's active body from the active Project's normalized presentation rather than storing selection in the global layout.
- [ ] Give every branch and body `min-width: 0`, `min-height: 0`, and bounded overflow so deeply split layouts do not force the application beyond the viewport.
- [ ] Keep each pane tab rail independently horizontally scrollable.
- [ ] Scroll a newly activated tab into view only within its owning rail.
- [ ] Keep one close-drawer control for the complete drawer rather than placing a drawer-close button in every stack.
- [ ] Position the close control in stable drawer chrome that does not consume or obscure a pane's tab insertion targets.
- [ ] Do not add per-tab close controls because utility tabs cannot be removed or hidden from the singleton registry.
- [ ] Give the focused drawer pane a restrained focus indicator distinct from the active tab indicator.
- [ ] Ensure multiple visible bodies use their own internal scrolling and never make the whole application page scroll.

## Tab-body lifecycle and ownership

- [ ] Mount only the active body in each desktop drawer stack.
- [ ] Keep the singleton Notes host mounted and hidden when its owning stack activates another tab, preserving cursor position, undo history, save-queue ownership, and insert targeting.
- [ ] Mount the Notes host in exactly one structural location derived from the singleton Notes tab.
- [ ] Preserve the rule that one note editor exists in only one browser host at a time.
- [ ] Preserve the current placeholder and claim behavior when a note is owned by the drawer instead of a Project workspace pane.
- [ ] Release drawer note ownership when the complete drawer closes, exactly as current behavior requires.
- [ ] Audit every newly simultaneous active body for timers, WebSockets, event listeners, fetch loops, global IDs, focus effects, and assumptions that it is the drawer's only body.
- [ ] Keep Processes on the fleet sample already owned by `App` and do not introduce another process poll per visible pane.
- [ ] Keep Transcript event-driven and preserve its scroll state per focused session.
- [ ] Keep Git refresh bounded and event-driven according to the Git feature contract.
- [ ] Ensure hidden inactive bodies do not retain subscriptions unless a documented state-preservation requirement needs them.
- [ ] Do not log clipboard contents, prompts, transcripts, note text, file contents, or other utility-body payloads while adding lifecycle diagnostics.

## Pointer drag and drop

### Shared gesture contract

- [ ] Continue using the app's pointer gesture implementation rather than native HTML drag-and-drop.
- [ ] Start dragging only after the existing 5 px movement threshold.
- [ ] Claim pointer ownership through `pointerDragClaim.ts` only after the drag threshold is crossed.
- [ ] Use one fixed-position drag ghost and direct DOM drop indicators rather than rerendering the complete drawer on every pointer move.
- [ ] Keep the prospective layout in a ref during the gesture and commit it once on pointer-up.
- [ ] Cancel without persistence on Escape, pointer cancel, lost pointer capture, window blur, invalid target, or responsive transition.
- [ ] Suppress the click produced by the pointer-up that completes a drag.
- [ ] Disable iframe or embedded-preview pointer interception during a running drawer drag if any future utility body embeds such content.

### Reorder and join behavior

- [ ] Treat gaps within the source rail as before/after reorder targets.
- [ ] Treat gaps within another pane's rail as precise cross-pane insertion targets.
- [ ] Treat the center or tab-rail body of another pane as a join target when no precise insertion gap is selected.
- [ ] Moving to another pane must remove the tab from its source pane and activate it in the destination pane for the active Project.
- [ ] Reordering within one pane must preserve the active Project's selection for that pane unless the moved tab is intentionally activated by the completed drag contract.
- [ ] Do not split when the pointer is clearly over a rail insertion target.
- [ ] Show an insertion line for reorder and join operations.

### Edge split behavior

- [ ] Show left, right, top, and bottom edge targets over every eligible drawer pane body.
- [ ] Interpret left and right as `horizontal` splits with the new singleton-tab stack placed first or second respectively.
- [ ] Interpret top and bottom as `vertical` splits with the new singleton-tab stack placed first or second respectively.
- [ ] Place the top target inside the pane body after the target pane's tab rail so tab reordering remains reachable.
- [ ] Use clear edge overlays that preview the exact region the moved tab will occupy.
- [ ] Permit splitting an existing multi-tab source pane by moving one tab to an edge of that same pane.
- [ ] Treat dropping the only tab of a pane onto an edge of that same pane as a no-op because it cannot produce two non-empty branches.
- [ ] Allow repeated edge splits to produce arbitrary nested grids without special-casing rows or columns.
- [ ] Add explicit acceptance coverage for constructing a 3x3 grid entirely through tab drags.

## Split resizing and drawer width

- [ ] Give every internal split separator pointer resizing with the same ratio semantics as the main workspace.
- [ ] Persist an internal ratio only on pointer-up while rendering live ratio feedback during the drag.
- [ ] Make every internal separator keyboard focusable.
- [ ] Support arrow-key adjustment, Home and End bounded movement, and double-click reset to `0.5`.
- [ ] Expose `role="separator"`, orientation, value minimum, value maximum, and current value.
- [ ] Keep internal split resizing independent from the outer drawer width resizer.
- [ ] Make the outer drawer resizer keyboard focusable and give it arrow-key adjustment, Home and End bounds, and double-click reset to the default width.
- [x] Replace the fixed 620 px drawer maximum with a viewport-aware maximum that lets the user allocate most of a desktop window to the drawer while preserving the launcher rail and a small usable Project workspace.
- [ ] Recalculate the legal outer width when the viewport, sidebar width, sidebar collapsed state, UI scale, or launcher-rail width changes.
- [x] Clamp a restored width only for the current viewport and do not overwrite the user's stored larger width merely because one window is temporarily narrow.
- [ ] Keep the drawer in flow on desktop so widening it shrinks rather than covers the Project workspace.
- [ ] Do not prohibit side-by-side panes or a 3x3 grid at a particular width.
- [ ] Let content become compact and independently scroll rather than silently rewriting the user's valid layout.

## Launcher rail and named entry points

- [ ] Keep the desktop outer utility rail as a launcher and drawer toggle, not as another tab owner or content mount.
- [ ] Document in code that a launcher button mirrors a singleton tab identity but is not a second layout location.
- [ ] Render launcher buttons in the global device-local layout's depth-first tab order.
- [ ] Use the same global order with the transient no-Project presentation.
- [ ] Remove ambiguous reorder dragging from the outer launcher rail after pane-local ordering becomes authoritative.
- [ ] Clicking a launcher button must find the tab's owning stack, activate that tab, mark it as drawer-focused, and open the drawer without moving it.
- [ ] Preserve the current toggle behavior by closing the complete drawer when its launcher is clicked while that exact tab is already visible and drawer-focused.
- [ ] Make named actions such as `drawer.files`, `drawer.git`, `clipboard.open`, queue chips, notification buttons, and Browse Files find and activate the existing singleton tab without changing its pane membership.
- [ ] Keep named open actions non-toggling when the caller explicitly asked to show a surface.
- [ ] Make `drawer.toggle` reopen the saved layout with the last drawer-focused tab and pane.
- [ ] Make keyboard next and previous cycle only within the focused drawer pane on desktop.
- [ ] Keep badges on Queue and Alerts synchronized in every rendering without creating extra tab state.

## Mobile projection

- [ ] At the existing mobile breakpoint, flatten all drawer tab IDs depth-first into one non-wrapping rail.
- [ ] Render exactly one ordinary utility body at a time on mobile.
- [ ] Preserve the hidden singleton Notes host behavior when it owns an editor.
- [ ] Select the active Project's saved drawer-focused tab when it remains valid, then use that Project's first valid stack selection in depth-first order, then the first flattened tab.
- [ ] Selecting a mobile tab must update the active Project's owning-stack selection and drawer-focused tab without changing the global layout.
- [ ] Mobile must not rewrite split directions, ratios, stack IDs, tab membership, or per-stack ordering.
- [ ] Returning to desktop must restore the saved recursive tree.
- [ ] Do not expose edge split targets, split separators, geometry commands, or drag reordering on mobile.
- [ ] Preserve the current mobile overlay, scrim, 90vw width, mutual exclusion with the navigation sidebar, keyboard dismissal, and swipe-away behavior.
- [ ] Keep mobile action completion behavior unchanged: actions that target the covered workspace close the drawer, while inert reading and a drawer-owned Notes editor follow their existing exceptions.
- [ ] Keep the mobile close button fixed while the flattened tab rail scrolls.
- [ ] Preserve title-mode and icon-mode tab selection visibility with `scrollIntoView` in the flattened rail.

## Icon or title display setting

### Configuration contract

- [ ] Add a hot-reloadable config field named `drawer_tab_display` with accepted values `icon` and `title`.
- [ ] Default `drawer_tab_display` to `icon` so existing installations retain their current density.
- [ ] Validate invalid values in `src/swe_mux/config.py` and preserve the previous valid value on a rejected update.
- [ ] Include the field in config loading, TOML serialization, config update responses, Settings draft typing, App config typing, and live config application.
- [ ] Add `Side panel tabs` to Settings > Appearance with explicit `Icons` and `Titles` choices.
- [ ] Make the setting searchable through the existing Settings search harvesting path.
- [ ] Apply the changed value immediately without requiring a daemon restart or page reload.

### Rendering behavior

- [ ] Apply the selected display mode to every drawer pane rail, the mobile flattened rail, and the desktop outer launcher rail.
- [ ] In icon mode, render `DRAWER_TAB_ICONS` exactly as today and retain accessible labels and descriptive tooltips.
- [ ] In title mode, render the short `DrawerTab.label` text rather than the longer explanatory tooltip string.
- [ ] Render either the icon or the title as the primary tab mark, not both.
- [ ] Preserve `aria-label`, `title`, selected state, focus state, drag state, and Queue or Alerts badges in both modes.
- [ ] Keep title-mode pane rails on one horizontally scrollable line with no wrapping.
- [ ] Expand the desktop outer launcher column in title mode through a CSS custom property rather than clipping labels into the current 40 px column.
- [ ] Truncate unusually long future labels in the launcher rail with an ellipsis while preserving the full accessible name and tooltip.
- [ ] Include launcher width in desktop drawer-width clamping and workspace grid calculations.
- [ ] Keep the mobile title rail horizontally scrollable and keep its close control outside the scroller.
- [ ] Do not make the icon/title choice alter logical ordering, pane membership, active tabs, layout geometry, or stored drawer layout.

## Accessibility and keyboard behavior

- [ ] Give each pane rail `role="tablist"` and a unique accessible label derived from its pane position or stable identity.
- [ ] Give every utility tab `role="tab"`, `aria-selected`, an accessible name, and a relationship to its active panel.
- [ ] Give active bodies `role="tabpanel"` with stable IDs and `aria-labelledby` links.
- [ ] Implement roving tab focus within each pane rail so only one tab per rail is in the sequential keyboard order.
- [ ] Support Left and Right arrow navigation within the focused rail without moving tab membership.
- [ ] Keep ordinary Tab traversal available to enter the active utility body.
- [ ] Do not steal focus-navigation keys from inputs, editors, filters, or utility body controls.
- [ ] Keep Escape closing the complete drawer when focus is inside it and no child modal or editor-owned Escape behavior takes precedence.
- [ ] Announce completed cross-pane moves and splits through a concise live region for keyboard and assistive-technology users.
- [ ] Provide keyboard commands or a focused-tab menu for moving the focused utility tab left, right, up, down, and into an adjacent pane because pointer drag cannot be the only geometry control.
- [ ] Make directional move availability derive from the live drawer tree and explain disabled directions.
- [ ] Preserve visible focus outlines in icon and title modes.
- [ ] Respect reduced-motion preferences for drag ghosts, overlays, and responsive transitions.

## Failure handling, reconciliation, and performance

- [ ] Normalize state before every render boundary and before every persistence write.
- [ ] Recover an invalid stored tree to one valid stack containing every registered tab exactly once.
- [ ] Surface storage-write failures without discarding the valid in-memory layout for the current browser session.
- [ ] Do not let a failed settings save revert layout geometry or pane membership.
- [ ] Ensure a Project switch during a drag cancels the drag before restoring the other Project's selections and expanded state.
- [ ] Ensure crossing the mobile breakpoint during a drag cancels the drag and clears every indicator and ghost.
- [ ] Ensure removing the active Project prunes only its Project presentation and leaves the global layout unchanged.
- [ ] Keep pointer-move work limited to geometry inspection, refs, and direct indicator updates.
- [ ] Memoize or isolate pane bodies so resizing or dragging one split does not remount unrelated active bodies.
- [ ] Use stable keys based on tab and pane identity so ordinary layout updates preserve component state when a tab stays in the same host.
- [ ] Deliberately remount a moved tab body when its pane host changes unless that body has an explicit external state owner.
- [ ] Preserve Notes safely through its external save queue and singleton hidden host rather than relying on accidental component key retention.
- [ ] Audit simultaneous panels on a maximally split layout for duplicate polling and event-listener leaks.
- [ ] Avoid adding backend logging for ordinary presentation changes.
- [ ] Add diagnostics only for actionable parse, migration, persistence, or invariant failures, without logging user content.

## Implementation checklist by code area

### Pure drawer state

- [ ] Add `frontend/src/drawerLayout.ts` with the complete typed model and pure operations.
- [ ] Move global layout and per-Project presentation parsing and serialization out of `drawerTabs.ts` if doing so keeps registry, arrangement, and selection responsibilities separate.
- [ ] Keep `drawerTabs.ts` as the canonical registry for IDs, labels, descriptions, scope, and default order.
- [ ] Replace flat-order helpers that remain necessary with layout-aware flattening and migration helpers.
- [ ] Remove dead flat-order code only after migration and compatibility tests prove it is unused.

### App state and commands

- [ ] Replace `drawerOrder`, `orderedDrawerTabs`, and one global `drawerTabId` rendering assumptions in `App.tsx` with one normalized global device layout plus the active Project's normalized presentation.
- [ ] Add one atomic `commitDrawerLayout` path that updates global local state first, reconciles all Project presentations, and writes normalized local storage once per completed operation.
- [ ] Add a separate Project-presentation update path for selected tabs, focused tab, and expanded state that never writes the global layout.
- [ ] Route every existing drawer entry point through layout-aware find-and-activate helpers.
- [ ] Cancel active drawer drags on Project changes, drawer closure, breakpoint changes, and unmount.
- [ ] Update command availability and labels for layout reset and keyboard directional moves.
- [ ] Preserve existing Project-targeted drawer actions so a cross-Project Files, Notes, or Queue action activates the target Project's existing singleton tab.
- [ ] Apply `drawer_tab_display` from initial config and config-change events.

### Components and styles

- [ ] Refactor `UtilityDrawer.tsx` around recursive nodes without duplicating the large tab-body prop contract per pane.
- [ ] Add focused components or context for the shell, split node, stack node, tab rail, and body dispatcher where that reduces rerenders and prop drift.
- [ ] Reuse `DRAWER_TAB_ICONS` from one source in icon mode.
- [ ] Add split, pane, rail, edge-target, focus, title-mode, and separator styles to `frontend/src/style.css`.
- [ ] Replace fixed utility-rail grid columns with `--utility-rail-width` in every expanded and collapsed sidebar grid template.
- [ ] Verify all utility bodies remain usable with nested flex sizing and `min-width: 0` or `min-height: 0` constraints.
- [ ] Keep selectors scoped to drawer classes so Project workspace pane styling does not leak into the independent drawer tree.

### Configuration and Settings

- [ ] Add the config default, validation, serialization, and hot-reload classification in `src/swe_mux/config.py`.
- [ ] Add backend config tests for default, valid updates, invalid updates, and round-trip persistence.
- [ ] Add the field to `Settings.tsx` and App's narrow config type.
- [ ] Add the Appearance control and immediate application behavior.
- [ ] Update Settings search expectations if harvested labels or section mappings change.

## Automated verification

### Drawer layout unit tests

- [ ] Add `frontend/test/drawerLayout.test.ts` and register it in `frontend/test/all.ts`.
- [ ] Test the canonical one-stack default.
- [ ] Test flat-order migration into one global device-local layout.
- [ ] Test v1 Project tab migration into v2 per-Project selections without duplicating the global layout.
- [ ] Test valid global-layout and v2 Project-presentation parse and serialization round trips.
- [ ] Test invalid JSON and invalid root fallback.
- [ ] Test duplicate tab repair.
- [ ] Test missing registered tab insertion without moving existing tabs.
- [ ] Test unknown tab removal.
- [ ] Test invalid per-Project stack-selection repair.
- [ ] Test duplicate node-ID repair.
- [ ] Test ratio clamping.
- [ ] Test empty-stack and single-branch split collapse.
- [ ] Test maximum-depth recovery.
- [ ] Test same-stack reorder at the first, middle, and last insertion positions.
- [ ] Test cross-stack move and exact insertion position.
- [ ] Test left, right, top, and bottom splits.
- [ ] Test source-stack collapse when its last tab moves.
- [ ] Test same-pane sole-tab edge drop as a no-op.
- [ ] Test repeated splits that produce a 3x3 visual grid.
- [ ] Test depth-first flatten order.
- [ ] Test activation without membership changes.
- [ ] Test switching Projects preserves the global layout while restoring different selections and expanded states.
- [ ] Test a global layout change reconciles every Project presentation deterministically.
- [ ] Test global layout reset and Project-presentation reconciliation.
- [ ] Test Project-state pruning leaves the global layout unchanged.
- [ ] Run a deterministic sequence of mixed reorder, move, split, activate, and ratio operations and assert after every operation that every registered tab occurs exactly once.

### Component and interaction tests

- [ ] Update existing drawer tests that assume one strip or one active body.
- [ ] Test that each desktop stack renders one active body and its own rail.
- [ ] Test that a tab moved between panes has one button in drawer pane rails and one mounted body total.
- [ ] Test that the outer launcher is a mirror control and never mounts another body.
- [ ] Test drag cancellation paths leave layout and storage unchanged.
- [ ] Test same-rail reorder, cross-rail insert, center join, and all edge splits.
- [ ] Test pointer-up click suppression.
- [ ] Test internal and outer separator keyboard controls.
- [ ] Test named drawer commands activate the tab in its current owner without moving it.
- [ ] Test desktop next and previous cycling stays inside the focused drawer pane.
- [ ] Test Queue and Alerts badges in icon and title modes.
- [ ] Test Notes remains mounted once while inactive and never duplicates a note editor.
- [ ] Test Files still opens or drags files into the Project workspace without joining the Project layout itself.
- [ ] Test mobile action completion and Notes exceptions remain unchanged.

### Mobile projection tests

- [ ] Add pure tests for flattening a nested drawer tree into one ordered mobile rail.
- [ ] Test mobile selection fallback when the focused tab is invalid.
- [ ] Test mobile selection updates only the active Project's owning-stack selection and focused tab.
- [ ] Test mobile selection leaves the global split directions, ratios, stack IDs, membership, and order unchanged.
- [ ] Test returning to desktop restores the exact tree.
- [ ] Test mobile exposes no drag or separator controls.
- [ ] Test icon and title rails remain one-line scrollable projections.

### Settings and configuration tests

- [ ] Test `drawer_tab_display` defaults to `icon`.
- [ ] Test `icon` and `title` updates are hot reloadable and persist through config reload.
- [ ] Test invalid values are rejected.
- [ ] Test App applies the field on initial load and on a live config update.
- [ ] Test Settings search finds `Side panel tabs`, `Icons`, and `Titles` under Appearance.
- [ ] Update any config snapshots, exported-config tests, or frontend contract assertions affected by the new field.

## Manual acceptance matrix

- [ ] Start from a clean profile and confirm the drawer is unchanged in appearance: closed by default, one pane when opened, icons by default, and every utility tab present once.
- [ ] Reorder several tabs in one rail and confirm reload preserves the global order for every Project on that device.
- [ ] Move tabs between two existing panes and confirm the source and destination rails update atomically.
- [ ] Create left, right, top, and bottom splits through drag targets.
- [ ] Build and use a 3x3 layout with nine singleton tabs.
- [ ] Confirm the remaining registered tabs stay in exactly one other rail and no utility body is duplicated.
- [ ] Resize every internal boundary in the 3x3 layout and confirm ratios survive reload.
- [ ] Widen the outer drawer beyond the former 620 px limit and confirm the Project workspace remains visible and uncovered.
- [ ] Narrow the outer drawer and confirm no split is silently collapsed or reoriented.
- [ ] Configure different selected tabs and expanded states in two Projects, switch repeatedly, and confirm the global layout and width never change while each Project restores its own presentation.
- [ ] Delete a Project and confirm its saved presentation is pruned without changing the global layout.
- [ ] Use every outer launcher button and named drawer command with a multi-pane layout and confirm each activates the existing owner rather than moving or duplicating the tab.
- [ ] Confirm Clipboard, Commands, Prompts, Queue, and Transcript all follow the focused session while simultaneously visible in separate panes.
- [ ] Confirm Files, Notes, Context, Git, and Processes follow the active Project.
- [ ] Confirm Alerts remains app-scoped.
- [ ] Open a note in the drawer, switch the Notes pane to another tab, insert from Clipboard, and confirm the same editor retains cursor, undo history, save state, and insert targeting.
- [ ] Move the note to a Project workspace pane and confirm only one editor remains mounted.
- [ ] Switch Appearance from Icons to Titles and confirm every pane rail, mobile rail, and outer launcher updates immediately.
- [ ] Confirm title mode preserves badges, tooltips, accessible labels, drag behavior, scroll behavior, and layout geometry.
- [ ] Confirm title mode expands the outer launcher column without covering or horizontally overflowing the application.
- [ ] Confirm mobile flattens the 3x3 layout into one rail and one visible body.
- [ ] Select several tabs on mobile, return to desktop, and confirm geometry and membership are unchanged while the selected tabs become active in their owning stacks.
- [ ] Confirm mobile does not offer reorder, split, or separator interaction.
- [ ] Confirm responsive breakpoint changes and Project switches cancel an in-progress drag cleanly.
- [ ] Complete keyboard-only activation, cycling, directional move, split resizing, outer resizing, reset, and drawer closure.
- [ ] Inspect with a screen reader or accessibility tree and confirm tablist, tab, tabpanel, separator, selected-state, and live-move announcements are correct.
- [ ] Leave several active utility panes open during session activity and confirm there are no duplicate polls, duplicate events, runaway rerenders, or focus theft.

## Documentation updates

- [ ] Update `.docs/design/features/ui.md` with the recursive independent drawer layout, singleton ownership, global device-local arrangement, per-Project selections and expanded state, launcher semantics, scope-following behavior, responsive projection, sizing, and icon/title setting.
- [ ] Update `.docs/design/features/workspace-layout.md` to state that the utility drawer reuses split interaction language but remains a separate device-local layout and separate mobile projection.
- [ ] Update `.docs/technical/frontend/workspace-state.md` with the global layout authority, per-Project presentation authority, local-storage schemas, migration, focused-tab behavior, mobile derivation, and Notes lifecycle.
- [ ] Update `.docs/technical/frontend/packages.md` with the new drawer layout module and refactored component ownership.
- [ ] Update `.docs/design/interfaces.md` if it enumerates config fields or hot-reload behavior.
- [ ] Update `.docs/design/features/project-resources.md`, `.docs/design/features/prompt-queue.md`, or `.docs/design/features/history.md` only where the implementation changes their documented host or lifecycle contracts.
- [ ] Update `.docs/CLAUDE.md` when archiving this plan so its active-checklist route does not point at the former development path.
- [ ] Verify every file path added to technical documentation exists.
- [ ] Keep this plan in `.docs/development/` until every checkbox is complete, then move it to `.docs/development/archive/` without renaming it.

## Final verification and delivery

- [ ] Run `uv run pytest tests -q -m "not live_agent and not live_subagent and not live_telemetry and not live_quota"`.
- [ ] Run `uv run ruff check src/swe_mux tests packaging`.
- [ ] Run `uv run mypy`.
- [ ] Run `npx tsc --noEmit` from `frontend/`.
- [ ] Run `npm test` from `frontend/`.
- [ ] Run `npm run build` from `frontend/` and confirm the production bundle succeeds.
- [ ] Review the final diff for accidental Project workspace coupling, duplicate tab ownership, stale flat-order code, native drag handlers, content logging, unrelated edits, and generated static output.
- [ ] Confirm no generated `src/swe_mux/static` assets are staged because the frontend build output is gitignored deployment output.
- [ ] If applying to the running app, determine whether the daemon is source-run or frozen by comparing served and source frontend asset hashes.
- [ ] For a source-run daemon, use the documented session-preserving frontend and backend reload flow.
- [ ] For a frozen desktop app, use `uv run python packaging/redeploy_desktop.py` from the primary checkout and verify health after the staged swap.
- [ ] Never start another daemon, run a frozen app, or trigger redeploy from a worktree.
- [ ] Never use `muxd --shutdown`, kill `swe-mux-supervisor.exe`, or terminate swe-mux processes to apply this update.
- [ ] Confirm all live terminal sessions survive the selected reload or redeploy flow.
- [ ] Mark every completed checkbox in this document and archive it only after the implementation, tests, documentation, manual acceptance, and delivery checks are all complete.
