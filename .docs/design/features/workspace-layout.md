# Workspace panes and tabs

## What it is

One Project-owned mixed-view workspace. Desktop renders a recursive split tree whose leaf panes
each own an independent ordered tab stack. Mobile presents every tab in one rail as a projection
of that same tree; it does not maintain a second layout.

## Layout-v7 model

```text
PaneLayout
└── root: PaneStack | PaneSplit | null
    ├── PaneStack {id, children[], active_child_id}
    └── PaneSplit {id, direction, ratio, first, second}

PaneLeaf = terminal | note | preview | history | queue
```

- A stack is a pane even when it has one tab. A split contains exactly two child branches.
- `terminal` identifies a live/ended session viewport; `preview` identifies a loopback preview;
  `history` identifies the searchable archive; `note` resource IDs encode Project note,
  session note, and individual file editors; `queue` identifies a session's prompt-queue tab
  (`features/prompt-queue.md`), its id `queue:<session_id>` so it can never collide with the
  target's own terminal leaf in focus tracking. The prompt queue's home is the utility
  drawer; the `queue` leaf is now only the explicit pop-out (the `↗` in the panel header) and
  what a layout saved before the move resolves to, so nothing creates one implicitly.
- Leaf IDs are globally unique inside a Project layout. Server validation caps layouts at 64
  leaves, nesting depth 24, and split ratios from 0.1 through 0.9.
- Layout versions 1–6 migrate on read. A visible legacy resource dock becomes an adjacent pane;
  a hidden dock becomes closed. Deprecated presentation fields remain parseable only for
  compatibility.
- A v6 `files:` leaf is **pruned**, not migrated: the Files browser is the utility drawer's Files
  tab now, so no pane can render one. A pane it emptied disappears and the split above it
  collapses into its surviving branch; a workspace that held nothing else becomes the empty
  stage. Pruning is unconditional (both `layout.ts` and `layouts.py`) rather than version-gated,
  so a stale client that still PATCHes a Files leaf is corrected rather than rejected.

## Placement and persistence

- A never-arranged Project opens on the empty stage. Nothing is seeded: the two surfaces worth
  seeding a pane with are the Project note and Files, and both are now one click away in the
  utility drawer, so a seeded pane would cost pixels and a layout write to show what a panel
  already shows.
- New terminals and resources join the focused pane by default; explicit directional actions
  create a split left/right/above/below.
- Placement has no per-resource exceptions and no implicit splits. Every open — terminal, note,
  file editor, preview — is a tab in the anchor's pane, and an unanchored open lands in the
  first pane. The two exceptions that used to exist are gone: skipping Files panes went with the
  Files leaf, and session notes no longer split a pane off to sit beside their terminal. Nothing
  rearranges the pane tree except an explicit split, drag, or move.
- Every pane has its own tab strip and active tab. There is no global tab strip, dock/pop-out
  mode, detached layout, or separate resource workspace.
- A terminal tab is labelled by the **same rule as the sidebar**: `agentTargetName` — the
  generated title while the session is auto-named, the explicit name once a human renames it.
  Every session-naming surface delegates to that one function rather than re-deriving it.
  The workspace tab strip and the mobile projection each read `session.name` directly at one
  point, which is exactly the surface the title exists for: a strip of `claude-15036b`,
  `claude-77eaca`, `claude-34cebf` is unreadable while the sidebar beside it reads fine.
- A tab strip that outgrows its pane scrolls sideways, and a plain wheel over it scrolls it.
  The strip only ever overflows on one axis, so a vertical wheel there has no other meaning;
  requiring Shift (the browser's only native way into an x-overflowing box) made the strip's
  own scrollbar the discoverable route and cost a second hand. Events already carrying
  horizontal intent — Shift+wheel, trackpad swipes — are left to the browser rather than
  applied twice, and a strip that fits keeps its wheel events instead of swallowing them.
- The active tab carries an accent outline, a thick accent underline, and a tinted fill. A bare
  background swap is not enough: the previous treatment moved only `--panel` to `--bg`, a few
  RGB points that vanish on a phone screen in daylight and is easy to miss on desktop too. The
  active fill is published as `--tab-active-bg` so the close button's fade overlay tracks the
  same colour instead of blending toward a background the tab no longer uses.
- Tab strips carry no new-tab button on any platform. The Project Run menu is the single
  launcher (desktop top bar, collapsed-rail `▶`, mobile toolbar, sidebar project row); an
  unsplit launch lands as a tab in the focused pane, which is what the old `+` did minus the
  backend choice. Explicit placement is **drag or the command palette**; no context menu
  carries it (see `ui.md` § context menus). A tab strip with nothing in it is not rendered,
  so an empty workspace shows only its stage.
- Layout changes update the local `layoutValues` ref and rendered map immediately, then serialize
  per-Project PATCH requests behind an optimistic `layout_revision`. Later writes cannot be
  overwritten by an earlier response. A stale revision refreshes authoritative Project state.
- Client-only pending terminal leaves make a launch visible before daemon spawn completes.
  Their IDs never persist or attach to PTY routes; success replaces the leaf atomically and
  failure removes it.
- Project Action steps join the pane containing the currently focused view as ordinary terminal
  tabs. A compound action does not create layout groups or import VS Code presentation/split
  hints; every returned session uses the same placement rule.
- Closing notes, editors, History, or previews is one click and closes only the view.
  Closing a Preview leaf preserves its daemon registration/sidebar service row; reopening
  reattaches the same stable leaf beside the actual listener owner.
  Closing a terminal is an inline two-click confirmation: fixed-width `×` becomes `✓` without
  shifting the tab, then kills/removes the session.
- The per-tab close control is a hover-only overlay on hover-capable pointers: it is absolutely
  positioned over the tab's right edge, so tab width is identical whether or not it is showing,
  and it masks the label under it with a gradient in the tab's own background. Confirming and
  keyboard-focused states stay visible without hover.
- Touch tab strips (including the mobile projection) render no close control at all. Closing
  and killing there go through the tab's long-press menu — the session menu for terminals, the
  tab menu for resources — which is also where the kill confirmation already lives.

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
- A running drag **owns the pointer**, and says so rather than leaving it to be inferred. It
  claims ownership when it crosses the 5 px threshold and releases when it unwinds
  (`pointerDragClaim.ts`); the mobile touch-gesture recognizer refuses to classify any touch
  sequence a drag ran inside. Coordinates cannot arbitrate this: dragging a tab along a strip
  and the swipe that toggles a panel are the same motion over the same pixels, so only the drag
  knows which one is happening. Claiming at the threshold rather than at pointer-down keeps a
  swipe that merely *starts* on a draggable tab working. See `ui.md` § touch gestures.
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
- The rail can still be rearranged *on the device*. A mobile tab's long-press menu (the session
  menu for terminals, the tab menu for resources) carries a left/right **Move tab** row that
  permutes a device-local order overlay stored per Project in local storage. It is a permutation
  only: the layout remains authoritative for membership, no layout revision is written, and no
  request is issued, so a phone's rail order can never reach the desktop pane tree or another
  client. Ordering is per device by design, not synced.
  This row is now mobile-*only*: the desktop `Move tab` row (which moved a leaf between panes)
  was removed from the session and tab menus along with the rest of the pane geometry, leaving
  drag and `pane.moveTab*` there. It survives here because it is a different action with no
  substitute — touch has no drag-reorder on the rail and no command palette, so removing it
  would leave a phone unable to reorder at all.
- A tab the saved order predates is merged in beside its layout predecessor rather than appended,
  so a session launched from a given tab still appears next to it. Once a device has its own
  order, later desktop reordering no longer moves those tabs on that device.
- Mobile menus omit split, cross-pane directional move, dissolve, and zoom controls: the mobile
  Move tab row permutes the flat rail rather than moving a leaf between panes. Touch scrolling
  never initiates tab reorder. Terminals always use xterm's built-in DOM renderer—even when desktop is
  configured to prefer WebGL—because responsive pixel-ratio changes can strand WebGL canvases.
- Terminal long-press is an xterm selection gesture, not a pane/session context-menu gesture.
  Dragging before the hold threshold scrolls; dragging after selection extends the selected
  buffer span. Copy actions remain explicit and touch-sized.
- Browser IDs use `crypto.randomUUID` when available, `getRandomValues` otherwise, then a final
  non-cryptographic uniqueness fallback. Direct tailnet HTTP therefore remains able to create
  tabs/panes when secure-context-only APIs are unavailable.

## Warm terminal panes

Switching to another tab in a stack hides the terminal it leaves rather than unmounting it, for
the last few panes shown. A tab switch therefore costs no PTY reattach and no buffer replay,
which is what made returning to a long Codex session visibly redraw for several seconds. The set
is bounded and recency-ordered (`WARM_TERMINAL_PANES`), and a hidden pane behaves as a
backgrounded tab for every shared resource — it deregisters its viewport from PTY geometry
arbitration, cannot take input ownership, and cannot write the system clipboard. The hide/show
transition itself is load-bearing: hiding withdraws the viewport immediately; showing re-fits
and fully redraws the retained xterm instance. Mechanics: `technical/frontend/workspace-state.md`
§ Warm terminal panes.

## Key files

- `frontend/src/layout.ts`
- `frontend/src/mobileWorkspace.ts`
- `frontend/src/warmPanes.ts`
- `frontend/src/App.tsx`
- `frontend/src/dragReorder.ts`
- `frontend/src/pointerDragClaim.ts`
- `src/swe_mux/layouts.py`
- `src/swe_mux/history.py`
- `frontend/test/layout.test.ts`
- `frontend/test/warmPanes.test.ts`
- `frontend/test/mobileWorkspace.test.ts`
- `frontend/test/randomId.test.ts`
- `tests/test_projects_reliability.py`

## Relates to

- `ui.md`: browser chrome, settings, focus, and overlays.
- `projects.md`: Project ownership and sidebar catalog.
- `project-resources.md`: note/file resource lifetime.
- `project-actions.md`: trusted task sessions and focused-pane placement.
