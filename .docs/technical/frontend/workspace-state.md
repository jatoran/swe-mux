# Workspace state and persistence

## State authorities

| State | Authority | Lifetime |
|---|---|---|
| split tree, pane tab order, pane active tab | Project layout v7 in daemon/SQLite | durable, multi-client |
| latest optimistic layout | `layoutValues.current[project_id]` | browser session |
| rendered layout | `layoutMap[project_id]` | component state |
| layout revision/write chain | refs keyed by Project | browser session |
| focused view and active terminal | App/view preference | device/session-local |
| warm-pane recency (which terminals stay mounted) | `warmHistory` in App, derived from layout | device/session-local |
| mobile unified tab rail | projection of layout | derived only |
| sidebar width/collapse | local storage | device-local |
| utility drawer split tree, membership, order, ratios | `mux.drawer.layout.v1` through `drawerLayout.ts` | device-local, global across Projects |
| utility drawer width | `mux.drawer.width.v1` | device-local, global across Projects |
| utility drawer pane selections, focused tab, desktop expansion | `mux.drawer.projects.v2` keyed by Project | device-local |
| mobile utility drawer visibility | `mobileDrawerOpen` in App | browser session |
| drawer-tab and desktop-right-rail icon/title modes | `drawer_tab_display` and `utility_rail_display` config fields | live presentation settings, independent |
| pointer drag target/ghost | refs and direct DOM attributes | one gesture |
| note/file draft | resource component/save queue | resource-local |
| terminal touch selection | xterm buffer selection + component-local action state | one selection |
| Project Action catalog/trust prompt | `ProjectRunMenu` fetched snapshot | one open menu |
| Preview registration and actual listener owner | daemon `PreviewRegistry` | daemon lifetime |
| open/active Preview tabs | Project layout v7 | durable, multi-client |
| canonical file tab identity | `file:<project_id>:<relative_path>` layout leaf | durable, multi-client |
| worktree file tab identity | `worktree-file:<project_id>:<encoded_root>:<encoded_relative_path>` layout leaf | durable, multi-client |
| open Git review snapshot and annotations | `GitReviewModal` component memory | one modal |

## Utility drawer authority and migration

`DrawerLayout` is a browser-local recursive split tree that is structurally independent from Project layout v7.
It stores stable stack and split IDs, tab membership, tab order, split directions, and ratios, but it stores no active selection.
`normalizeDrawerLayout` is the read and write boundary that repairs invalid JSON, malformed branches, duplicate IDs, duplicate or missing registered tabs, invalid ratios, stale tabs, empty stacks, and trees deeper than 24.

Each Project's `DrawerProjectPresentation` stores a selected tab per current stack, one focused utility tab, and desktop expanded state.
Normalization prefers that Project's focused tab in its owner stack, preserves each other valid stack selection, and otherwise selects the stack's first tab.
A global layout edit reconciles every saved Project presentation while preserving expanded state and the focused tab in its new owner stack.
Deleting a Project prunes only its presentation record and does not alter global drawer geometry.
Before any Project exists, App uses a transient normalized presentation so application-scoped utilities remain reachable.

The first v1 layout waits for the asynchronous device-settings cache, then seeds from the former normalized server `drawerTabs` order if no local layout was created while that read was in flight.
The former `mux.drawer.projects.v1` tab and expansion pair migrates to `mux.drawer.projects.v2`, and the former `mux.drawer.tab.v1` value seeds only the initially active Project when valid.
The old keys are removed only after both new serializations succeed.
The server continues accepting the legacy flat domain for compatibility, but recursive edits never write it or adopt later settings changes.

Mobile visibility remains transient in `mobileDrawerOpen`.
Mobile derives one depth-first tab rail and one selected body from the desktop tree, and activation updates only the owning stack's Project presentation.
Responsive transitions never flatten or persist replacement geometry.
The drawer tab strips and desktop right rail have separate icon/title config fields; only `utility_rail_display` feeds `--utility-rail-width`, so changing drawer tabs never reflows the workspace grid.

`OverflowRail` wraps workspace and utility tablists without taking over their ARIA roles or drag targets.
It owns endpoint detection, non-layout-consuming fade chevrons, wheel translation, boundary-aware paging, and selected or focused tab reveal.
Selection changes and pane-focus changes are separate reveal triggers because moving focus to an already-active pane tab does not change the selected child ID.
The underlying tablist remains the native horizontal touch and trackpad scroller, and the controls do not render when the content fits.

Notes is the only inactive drawer body kept mounted.
Its singleton host is derived from the unique Notes tab's owner stack, remains hidden while another tab is selected there, and preserves cursor, undo, save-queue ownership, and editor insert targeting.
The per-Project selected note survives drawer close in `mux.drawer.note.v1`.
Editor ownership is derived from that selection plus drawer visibility, so closing the drawer restores any matching workspace editor without erasing the Notes sub-tab.

## Warm terminal panes

A stack renders its active child **plus** its most recently shown terminal siblings.
`.terminal-pane.pane-warm` removes a hidden sibling from flex layout with absolute positioning and makes it non-interactive with `visibility:hidden` and `pointer-events:none`, but preserves a measurable host box.
Collapsing that box with `display:none` left xterm without cell metrics and produced intermittent stale or mangled terminal surfaces that browser-only redraws could not reliably reconstruct.
Unmounting warm panes instead made every tab switch a cold attach: xterm disposed, the PTY socket dropped, and on return the daemon replays the retained buffer while xterm parses it in time-sliced chunks, so a long session is watched redrawing.
This is worst for Codex, whose transcript lives in scrollback (`tui.alternate_screen="never"`, `codex_tui.py`) and whose retained bytes are therefore real lines that each allocate and scroll rather than repaints of one alternate screen.

`warmPanes.ts` owns the policy, pure and unit-tested: `recordPaneVisits` moves the currently
shown panes to the front of a capped recency list, and `warmPaneIds` returns the hidden set —
most recent first, excluding panes already on screen (they are mounted anyway, and counting them
would evict the ones this exists to keep), restricted to panes the layout still has, and bounded
by `WARM_TERMINAL_PANES` (3) across the whole workspace rather than per stack.

The mobile projection deliberately keeps none. It renders one selected pane with the default
`paneVisible`, so a phone still holds exactly one live terminal — the memory a warm set costs is
worth more there than the reattach it saves, and the unified rail has no second pane on screen to
switch back to.

A warm pane is live but not being looked at, so `TerminalPane` treats it exactly as it treats a backgrounded browser tab.
`paneIsHidden()` is `document.hidden || !visible`, and it gates viewport registration, input-ownership focus, and OSC 52 clipboard writes.
A warm pane deregisters immediately, so its measurable retained box cannot reshape the shared PTY.
The hidden viewport pass returns after deregistration and cannot refit the local xterm model while its PTY retains the last visible geometry.
`visible` is read through a ref and is deliberately **not** a mount-effect dependency because listing it would dispose and rebuild the terminal on every switch, which is the cost warm panes remove.
It is, however, part of `TerminalPane`'s custom memo comparator; without that comparison a visibility-only render is discarded before the lightweight effect can update the ref, withdraw the hidden viewport, or redraw the shown surface.
Becoming visible again schedules a fit, persistent same-grid renderer repair, full redraw, and tail scroll.
The repair is recorded before either reveal frame and is cleared only after a measurable host successfully reflows and redraws.
Zero-sized frames retain it for the next successful viewport measurement, reveal signal, or health sweep.
The reflow is distinct
from fitting: FitAddon and public `term.resize` both short-circuit when cols/rows match, while the
DOM or canvas surface can still retain stale pixel dimensions after a retained hidden interval. Toggling and
immediately restoring xterm's public `customGlyphs` option reaches `RenderService.handleResize`
without changing the cell grid or reporting a PTY resize. Output that arrived while the pane was
hidden moved `baseY` with no viewport following it, so the tail repair remains separate.
The fit itself is also persistent debt: a newly visible host can still produce no FitAddon dimensions while layout and xterm cell metrics settle, and that pass is not considered successful.
The slow health sweep independently compares the current grid with a fresh fit proposal, excluding intentional letterboxes, so stale half-height grids recover even if every event signal was missed.
Reveal is also when a warm pane that attached hidden judges its replayed transcript: if the parse left less than one screen of scrollback on a normal-screen `repaintsScrollback` harness, the pane sends one `repaint` frame per parsed buffer and the daemon makes the child restate its transcript (`scrollbackRepaintNeeded` in `terminalHealth.ts`; the same check runs when a visible pane finishes replay).

Viewport measurement also owns the agent width envelope.
Claude's host is centered and capped at 120 columns, so parent growth beyond that width does not emit another PTY resize.
For narrow desktop Codex panes, the measurement first proposes at the configured base font, derives a smaller font when the proposal is below 80 columns, and proposes again before changing xterm or sending the viewport frame.
The policy runs on pre-connect attach, ordinary resize, reconnect, explicit Resize, and health repair through the same `fitVisiblePane` path, so replay bytes cannot be parsed at a different width from the one registered with ConPTY.

## Viewport passes are coalesced once they get expensive

A "viewport pass" is a fit: `term.resize`, a `resize` frame to the daemon, and a full
`refresh()`. On a pane showing one screen that is microseconds. On one holding tens of
thousands of real scrollback lines it is not — `term.resize` appends a `BufferLine` (each
with its own `Uint32Array`) per gained row on a ConPTY-backed buffer and can rebuild the
whole `CircularList` backing array, and the `resize` frame resizes the real pseudoconsole,
which makes the CLI repaint everything it is showing.

Three triggers arrive in floods: `visualViewport` resize, `window` resize, and the host's
`ResizeObserver` (which also sees `--app-height` change on every `visualViewport` event).
A soft keyboard fires ~20 of them across its open animation, so a long Codex session paid
all of the above ~20 times and then visibly scrolled while the repaints streamed in.

`createViewportScheduler` (`terminalViewport.ts`) runs the first pass of a burst — it is
both the responsive thing to do and the measurement — and coalesces the rest until the
burst settles, but *only* when the last pass exceeded `EXPENSIVE_VIEWPORT_PASS_MS` (half a
60 Hz frame). The decision is adaptive rather than keyed on the backend or on a buffer-size
guess, so it also covers cases nobody enumerated: a Claude session that left the alternate
screen, a shell with a huge `cat` in its scrollback. A cheap pane keeps fitting on every
event exactly as before. `VIEWPORT_SETTLE_MAX_MS` caps the coalescing so a continuous
gesture (dragging a splitter) still updates.

Each pass also restores the tail when the viewport was on it beforehand. A ConPTY-backed
buffer gains blank rows on a resize instead of pulling scrollback back down, so `baseY`
moves and the viewport is left above the newest line; a viewport the user had deliberately
scrolled up is left alone.

## Optimistic layout writes

`updateLayout(projectId, next)` first updates `layoutValues` and `layoutMap`, then appends a PATCH
to that Project's promise chain. The request carries the latest known `layout_revision`.
Responses update the revision and authoritative Project snapshot; only the newest local
generation may replace the optimistic rendered layout. A stale-revision response refreshes all
Project state and reports the conflict.

Correct:

```ts
const latest = layoutValues.current[projectId] ?? activeLayout
void updateLayout(projectId, moveLeafToStack(latest, kind, id, targetStackId))
```

Incorrect:

```ts
// activeLayout may be stale after rapid pointer drops or pending writes.
void updateLayout(projectId, moveLeafToStack(activeLayout, kind, id, targetStackId))
```

Never persist pending client terminal IDs. Insert them into optimistic state for launch feedback,
then replace/remove them when the spawn request resolves.

## Worktree file leaves

Canonical `file:` resource IDs are unchanged.
Git Map file actions use a separate `worktree-file:` identity that encodes the exact absolute worktree root and repository-relative path independently, so sibling trees can keep the same relative path open at once.
`layout.ts` is the only parser and formatter for both identities, and malformed worktree identities never fall back to a canonical file.
`ProjectResource` carries the worktree root through reads, image content, revision-checked writes, reveal, and watcher renewal.
Closing or reopening a tab does not require the worktree to be the Project root, but every daemon operation revalidates membership in the Project repository.
A removed worktree leaves the durable tab in a recoverable unavailable state instead of reading another filesystem location.
Git Log actions are explicitly labeled `Open current file` because the durable leaf opens the current Project working copy, not the historical commit blob.

Project Action sessions are already daemon identities when returned. Insert each with `openTab`
against the latest `layoutValues` state, advance the target to the inserted terminal, and persist
one resulting layout. Do not synthesize a task-group node or interpret imported presentation
hints.

Preview registration and Preview layout are deliberately separate. Closing a Preview leaf
removes only the layout leaf; its live service remains nested in the sidebar. Reopening posts
the existing endpoint to `/api/previews`, which reuses its project-wide registration and stacks
the leaf beside the session that actually owns the listener. If the leaf already exists,
`stack_leaf` activates it rather than producing a no-op layout response.

## Mobile input boundaries

- `ProjectNoteEditor.tsx` selects the native textarea path for narrow or coarse-pointer clients.
  The textarea is uncontrolled by render props during one mount; `input` snapshots feed the same
  resource save queue as the desktop Continuity editor.
- `TerminalPane.tsx` maps long-press coordinates into xterm buffer cells. Movement before the
  hold threshold remains scroll input; movement after word selection extends the xterm span.
- Touch-originated `contextmenu` is suppressed without opening the desktop terminal menu.
- Clipboard writes do not clear xterm selection until success. OSC 52 and failed manual copy
  retain bounded text in the visible prepared-clipboard surface for user-gesture retry.

## Pointer gesture state machine

The shared gesture has four stages: pending threshold, active with pointer capture, drop, and
cancel. Activation at 5 px installs one ghost and calls the feature's `onStart`. Each move
computes its target synchronously, stores it in a ref, and changes at most one
`data-pointer-drop-indicator`. Drop reads that latest ref after cleanup; cancel persists nothing.

Every exit path—pointer up, cancel, lost capture, Escape, and window blur—must remove global
listeners, release capture, delete the ghost, remove the body class/indicator, and restore
preview pointer input. Native HTML `dragstart`/`dragend` is not part of this state machine.

## Mobile projection

`mobileWorkspaceProjection` walks the desktop tree in visual depth-first order and returns all
leaves plus one selected leaf. It does not synthesize/persist a replacement pane. Mobile tab
activation may update the owning desktop stack's `active_child_id`; new tabs target the selected
leaf's owning stack. Closing uses `adjacentMobileTab` before applying the ordinary leaf removal.
Opening a tab menu is not activation: mobile long-press and desktop right-click retain the current
focused view/active terminal, and the touch gesture consumes its follow-up synthetic click. The
adjacent-tab close fallback runs only when the removed mobile leaf was selected; closing a
background leaf leaves selection untouched.

This separation is required for responsive transitions: rotating a phone, using desktop device
emulation, or resizing through 760 px must never erase desktop splits.

### Rail order (removed: the device-local overlay)

Rail order is the projection's order — depth-first over the pane tree — with nothing layered on
top. `mobileWorkspaceProjection` takes no order argument.

A device-local permutation overlay used to sit here (`mobileTabOrder`, one ordered id list per
Project in local storage), driven by a `Move tab` row in the mobile long-press menu. Both were
removed when context menus stopped carrying tab ordering and pane geometry. The overlay could not
be left orphaned: with its only writer gone it would have kept permuting any device that had
already saved one, unwritable and unclearable. Removing it also removed the one piece of state
whose whole design constraint was "must never write layout" — the phase-4 contract test now
asserts the key, the module, and `orderMobileTabs` are all absent rather than asserting the move
handler stayed API-free.

## Test focus

- `frontend/test/layout.test.ts`: migrations and all stack/split transforms.
- `frontend/test/mobileWorkspace.test.ts`: complete flattening, selection priority, close fallback,
  and that the projection takes no order argument for a stale permutation to re-enter through.
- `frontend/test/randomId.test.ts`: secure and non-secure browser identity fallbacks.
- `frontend/test/warmPanes.test.ts`: eviction order, the on-screen and closed-tab exclusions, the
  recency cap, and (by source inspection) that a warm pane is hidden from layout/pointer/assistive
  tech, that `visible` reaches the memo comparator but never the mount deps, that the explicit
  resize path reflows renderer dimensions and force-registers before claiming input, and that
  nothing still gates on `document.hidden` alone.
- `frontend/test/renderer/terminal-webgl.spec.ts`: browser-level WebGL hidden-pane stability and
  DOM same-grid renderer-dimension repair; the latter proves `fit()` leaves a stale half-size
  surface untouched and `reflowVisibleTerminalRenderer` restores it.
- `frontend/test/previewLinks.test.ts`: loopback-only terminal link normalization.
- Pointer behavior tests/inspection must cover threshold, exact insertion, split edges, cross-pane
  movement, Escape, lost capture, and cleanup after responsive changes.
