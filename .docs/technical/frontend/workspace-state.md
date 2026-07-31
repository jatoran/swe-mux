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
| mobile unified tab rail | projection of layout, permuted by the order overlay | derived only |
| mobile tab order overlay | local storage (`mux.mobileTabOrder.v1`), per Project | device-local |
| sidebar width/collapse | local storage | device-local |
| pointer drag target/ghost | refs and direct DOM attributes | one gesture |
| note/file draft | resource component/save queue | resource-local |
| terminal touch selection | xterm buffer selection + component-local action state | one selection |
| Project Action catalog/trust prompt | `ProjectRunMenu` fetched snapshot | one open menu |
| Preview registration and actual listener owner | daemon `PreviewRegistry` | daemon lifetime |
| open/active Preview tabs | Project layout v7 | durable, multi-client |

## Warm terminal panes

A stack renders its active child **plus** its most recently shown terminal siblings, hidden with
`display:none` (`.terminal-pane.pane-warm`). Unmounting them instead made every tab switch a cold
attach: xterm disposed, the PTY socket dropped, and on return the daemon replays the retained
buffer while xterm parses it in time-sliced chunks, so a long session is watched redrawing. Worst
for Codex, launched in raw scrollback mode (`codex_tui.py`), whose retained bytes are real lines
that each allocate and scroll rather than repaints of one alternate screen.

`warmPanes.ts` owns the policy, pure and unit-tested: `recordPaneVisits` moves the currently
shown panes to the front of a capped recency list, and `warmPaneIds` returns the hidden set —
most recent first, excluding panes already on screen (they are mounted anyway, and counting them
would evict the ones this exists to keep), restricted to panes the layout still has, and bounded
by `WARM_TERMINAL_PANES` (3) across the whole workspace rather than per stack.

The mobile projection deliberately keeps none. It renders one selected pane with the default
`paneVisible`, so a phone still holds exactly one live terminal — the memory a warm set costs is
worth more there than the reattach it saves, and the unified rail has no second pane on screen to
switch back to.

A warm pane is live but not being looked at, so `TerminalPane` treats it exactly as it treats a
backgrounded browser tab: `paneIsHidden()` is `document.hidden || !visible`, and it gates viewport
registration (a warm pane deregisters, so it cannot reshape the shared PTY), input-ownership
focus, and OSC 52 clipboard writes. `visible` is read through a ref and is deliberately **not** a
mount-effect dependency — listing it would dispose and rebuild the terminal on every switch, which
is the cost warm panes remove. Becoming visible again schedules a full redraw and a tail scroll,
since output that arrived while the pane was hidden moved `baseY` with no viewport following it.

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

This separation is required for responsive transitions: rotating a phone, using desktop device
emulation, or resizing through 760 px must never erase desktop splits.

### Device-local rail order

The rail can be rearranged on the device without touching the layout. `mobileTabOrder` holds one
ordered id list per Project in local storage; the projection applies it as a **permutation only**,
so the layout stays authoritative for membership and the overlay can never invent or drop a tab.
Reordering writes no layout revision and issues no request — that is the whole reason a phone
rearranging its rail cannot rearrange desktop panes, and the phase-4 contract test asserts the
move handler contains neither `updateLayout` nor an API call.

Merge rules (`orderMobileTabs`): saved ids that no longer exist are ignored; ids the save predates
are inserted after their nearest *layout* predecessor rather than appended, so a session launched
from a given tab still appears beside it; a run of new tabs anchors on the previously inserted one
and stays in layout order. Every move stores the full displayed order, so the save self-heals.
Because the whole mobile surface reads `projection.tabs`, swipe navigation and close-focus
adjacency follow the rearranged rail without extra wiring. Desktop never calls the projection, and
widening a narrow window back past 760 px restores pure layout order.

## Test focus

- `frontend/test/layout.test.ts`: migrations and all stack/split transforms.
- `frontend/test/mobileWorkspace.test.ts`: complete flattening, selection priority, close fallback,
  and that a saved order permutes without changing membership.
- `frontend/test/mobileTabOrder.test.ts`: merge rules for new/stale ids, move bounds, storage
  round-trip and malformed payloads, project pruning.
- `frontend/test/randomId.test.ts`: secure and non-secure browser identity fallbacks.
- `frontend/test/warmPanes.test.ts`: eviction order, the on-screen and closed-tab exclusions, the
  recency cap, and (by source inspection) that a warm pane is hidden from layout/pointer/assistive
  tech, that `visible` never reaches the mount deps, and that nothing still gates on
  `document.hidden` alone.
- `frontend/test/previewLinks.test.ts`: loopback-only terminal link normalization.
- Pointer behavior tests/inspection must cover threshold, exact insertion, split edges, cross-pane
  movement, Escape, lost capture, and cleanup after responsive changes.
