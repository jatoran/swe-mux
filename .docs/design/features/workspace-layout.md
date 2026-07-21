# Workspace panes and tabs

## What it is

One Project-owned mixed-view workspace. Desktop renders a recursive split tree whose leaf panes
each own an independent ordered tab stack. Mobile presents every tab in one rail as a projection
of that same tree; it does not maintain a second layout.

## Layout-v6 model

```text
PaneLayout
└── root: PaneStack | PaneSplit | null
    ├── PaneStack {id, children[], active_child_id}
    └── PaneSplit {id, direction, ratio, first, second}

PaneLeaf = terminal | note | preview | history
```

- A stack is a pane even when it has one tab. A split contains exactly two child branches.
- `terminal` identifies a live/ended session viewport; `preview` identifies a loopback preview;
  `history` identifies the searchable archive; `note` resource IDs encode Project note,
  session note, Files, and individual file editors.
- Leaf IDs are globally unique inside a Project layout. Server validation caps layouts at 64
  leaves, nesting depth 24, and split ratios from 0.1 through 0.9.
- Layout versions 1–5 migrate on read. A visible legacy resource dock becomes an adjacent pane;
  a hidden dock becomes closed. Deprecated presentation fields remain parseable only for
  compatibility.

## Placement and persistence

- A never-arranged Project seeds its workspace on first browser open: a narrow Files column
  beside a wider pane holding the Project note, which starts focused. Both leaves are viewports,
  so a first open still spawns no process, and the first terminal joins the note's pane under the
  ordinary focused-pane rule. Seeding requires layout revision 0 and an empty root together, so a
  Project whose panes were all deliberately closed stays empty and is never re-seeded.
- New terminals and resources join the focused pane by default; explicit directional actions
  create a split left/right/above/below.
- Placement never defaults a new view into a pane holding the Files browser: an unanchored open,
  a Files-focused open, and companion-note reuse all skip Files panes and prefer the first
  non-Files pane. An explicit drop or drag target is still honored exactly. Files is used as a
  last resort only when it is the sole pane.
- Every pane has its own tab strip, active tab, and add action. There is no global tab strip,
  dock/pop-out mode, detached layout, or separate resource workspace.
- Layout changes update the local `layoutValues` ref and rendered map immediately, then serialize
  per-Project PATCH requests behind an optimistic `layout_revision`. Later writes cannot be
  overwritten by an earlier response. A stale revision refreshes authoritative Project state.
- Client-only pending terminal leaves make a launch visible before daemon spawn completes.
  Their IDs never persist or attach to PTY routes; success replaces the leaf atomically and
  failure removes it.
- Project Action steps join the pane containing the currently focused view as ordinary terminal
  tabs. A compound action does not create layout groups or import VS Code presentation/split
  hints; every returned session uses the same placement rule.
- Closing notes, Files, editors, History, or previews is one click and closes only the view.
  Closing a Preview leaf preserves its daemon registration/sidebar service row; reopening
  reattaches the same stable leaf beside the actual listener owner.
  Closing a terminal is an inline two-click confirmation: fixed-width `×` becomes `✓` without
  shifting the tab, then kills/removes the session.

## Pointer drag contract

- Project rows, sidebar sessions, and workspace tabs use one pointer gesture rather than native
  HTML drag-and-drop. Movement begins after 5 px and creates one fixed-position DOM ghost.
- Pointer movement keeps the latest target in synchronous refs and updates one DOM drop-indicator
  attribute. It must not drive Preact render state on every move.
- Tab-strip gaps and source positions resolve to the nearest insertion slot. A line previews
  before/after insertion, a tab bar highlight previews joining a pane, and edge overlays preview
  a new split. Sidebar insertion and grouping targets use equivalent explicit indicators.
- Pointer capture makes pointer-up deterministic. Escape, pointer cancel, lost capture, and
  window blur cancel, clear the indicator/ghost, restore embedded-preview pointer behavior, and
  persist nothing.
- Do not reintroduce `draggable`/native Chromium drag handlers for these surfaces. Responsive
  layout changes can strand that native loop with a permanent grabbing cursor and frozen UI.

## Mobile projection

- At `max-width: 760px`, all leaves are flattened depth-first in desktop visual order into one
  horizontally scrolling, non-wrapping tab rail. Exactly one selected view renders full-screen.
- Selection prefers the focused view, then active terminal, then pane-active tabs, then the first
  tab. Closing selects an adjacent projected tab.
- Opening a tab on mobile places it in the selected tab's underlying desktop pane, or the first
  pane when no selection exists. Activating it also updates that pane's active child.
- Mobile never rewrites split geometry, pane membership, or tab order merely because it is
  narrow. Returning to desktop restores the saved pane tree.
- Mobile menus omit split, directional move, dissolve, and zoom controls. Touch scrolling never
  initiates tab reorder. Terminals use xterm's built-in renderer because Chromium device
  emulation can strand WebGL canvases after responsive pixel-ratio changes.
- Terminal long-press is an xterm selection gesture, not a pane/session context-menu gesture.
  Dragging before the hold threshold scrolls; dragging after selection extends the selected
  buffer span. Copy actions remain explicit and touch-sized.
- Browser IDs use `crypto.randomUUID` when available, `getRandomValues` otherwise, then a final
  non-cryptographic uniqueness fallback. Direct tailnet HTTP therefore remains able to create
  tabs/panes when secure-context-only APIs are unavailable.

## Key files

- `frontend/src/layout.ts`
- `frontend/src/mobileWorkspace.ts`
- `frontend/src/App.tsx`
- `frontend/src/dragReorder.ts`
- `src/swe_mux/layouts.py`
- `src/swe_mux/history.py`
- `frontend/test/layout.test.ts`
- `frontend/test/mobileWorkspace.test.ts`
- `frontend/test/randomId.test.ts`
- `tests/test_projects_reliability.py`

## Relates to

- `ui.md`: browser chrome, settings, focus, and overlays.
- `projects.md`: Project ownership and sidebar catalog.
- `project-resources.md`: note/file resource lifetime.
- `project-actions.md`: trusted task sessions and focused-pane placement.
