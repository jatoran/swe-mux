# Workspace state and persistence

## State authorities

| State | Authority | Lifetime |
|---|---|---|
| split tree, pane tab order, pane active tab | Project layout v6 in daemon/SQLite | durable, multi-client |
| latest optimistic layout | `layoutValues.current[project_id]` | browser session |
| rendered layout | `layoutMap[project_id]` | component state |
| layout revision/write chain | refs keyed by Project | browser session |
| focused view and active terminal | App/view preference | device/session-local |
| mobile unified tab rail | pure projection of layout | derived only |
| sidebar width/collapse | local storage | device-local |
| pointer drag target/ghost | refs and direct DOM attributes | one gesture |
| note/file draft | resource component/save queue | resource-local |
| terminal touch selection | xterm buffer selection + component-local action state | one selection |

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

## Test focus

- `frontend/test/layout.test.ts`: migrations and all stack/split transforms.
- `frontend/test/mobileWorkspace.test.ts`: complete flattening, selection priority, close fallback.
- `frontend/test/randomId.test.ts`: secure and non-secure browser identity fallbacks.
- Pointer behavior tests/inspection must cover threshold, exact insertion, split edges, cross-pane
  movement, Escape, lost capture, and cleanup after responsive changes.
