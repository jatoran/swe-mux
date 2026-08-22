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
| talk capture, draft, focus-following target, exact-sink pin | `useConversation` in App plus `conversationTarget.ts` | browser session |
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
| sessions removed on screen but not yet gone from the daemon | `pendingKills.current` keyed by session ID | until that session's DELETE settles |
| daemon-started sessions whose automatic join the server refused | `joinAttempts.current` keyed by session ID | browser session, pruned to the live fleet |

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
The stored presentation map is read through three tiers, newest first: `mux.drawer.projects.v3` (the current shape), `mux.drawer.projects.v2` (the same shape without `selected_segments`, parsed directly since normalization fills the missing map), and the original `mux.drawer.projects.v1` tab/expansion pair.
The former `mux.drawer.tab.v1` value seeds only the initially active Project when valid.

A retired tab id is forward-mapped at every read through **`migratedTabTarget`** in `drawerLayout.ts`, which returns a tab *and, where one exists, a segment*.
That second half is what makes a consolidation non-destructive: `changemap` maps to `{activity, changes}` and `context` to `{agent, instructions}`, so a reader who had one of those selected lands on the surface they chose rather than on the absorbing tab's first segment.
The full table is `commands`/`prompts`/`clipboard` to `actions`, `insight` to `activity`, `timeline` and `changemap` to `activity` (with segments), and `context` to `agent/instructions`.
Every entry stays forever: a saved arrangement is device-local and can be arbitrarily old, and an unrecognised id is dropped rather than repaired, which silently loses whichever pane the user had dragged it into.
The legacy `mux.drawer.tab.v1` seed in `App.tsx` calls that same helper rather than re-spelling the table inline, which is what it used to do and what drifted every time a tab was folded into another.
The old keys — v1, v2, and `mux.drawer.tab.v1` — are removed only after the new serializations succeed, so an interruption anywhere before that leaves an older record intact and the migration simply runs again.

`selected_segments` records which segment each segmented tab is showing, per Project, keyed by **tab rather than by stack**: a tab lives in exactly one stack, and keying by stack would lose the choice the moment the tab were dragged into another pane.
It is stored loosely as `string` rather than validated against the segment registry, because an unknown id costs nothing — `resolveDrawerSegment` falls back to the first available segment anyway — and because this module stays the layout's own vocabulary.
The server continues accepting the legacy flat domain for compatibility, but recursive edits never write it or adopt later settings changes.

Mobile visibility remains transient in `mobileDrawerOpen`.
Mobile derives one depth-first tab rail and one selected body from the desktop tree, and activation updates only the owning stack's Project presentation.
Responsive transitions never flatten or persist replacement geometry.
The drawer tab strips and desktop right rail have separate icon/title config fields; only `utility_rail_display` feeds `--utility-rail-width`, so changing drawer tabs never reflows the workspace grid.

The desktop right rail renders only while the drawer is closed, and the docked drawer takes the same last grid column when open.
`--utility-rail-width` is reserved in both states: closed it sizes the rail, open it is added to `--drawer-width` to size the drawer's column.
The workspace's remaining width is therefore identical whether the rail or the drawer holds that column, so `drawerMaximumWidth` keeps reserving the launcher width and the outer resizer keeps reading and writing the stored drawer width unchanged.
Both `.utility-drawer.docked` and `.drawer-resizer` address that column with negative grid lines, which silently retarget if the open template's column count changes.

`OverflowRail` wraps workspace and utility tablists without taking over their ARIA roles or drag targets.
It owns endpoint detection, non-layout-consuming fade chevrons, wheel translation, boundary-aware paging, and selected or focused tab reveal.
Selection changes and pane-focus changes are separate reveal triggers because moving focus to an already-active pane tab does not change the selected child ID.
The underlying tablist remains the native horizontal touch and trackpad scroller, and the controls do not render when the content fits.

Notes is the only inactive drawer body kept mounted.
Its singleton host is derived from the unique Notes tab's owner stack, remains hidden while another tab is selected there, and preserves cursor, undo, save-queue ownership, and editor insert targeting.
Focused Continuity editors publish their named resource identity through `insertTarget.ts`; Queue publishes its composer the same way, while terminals publish only live agent sessions.
Spoken Notes navigation issues a bounded one-shot claim for the selected note without DOM focus; consuming it clears the request, and later ordinary focus publication remains authoritative.
The app-level Conversation controller follows the latest published target unless its exact sink is pinned, and a detached pinned sink becomes unavailable instead of being silently retargeted.
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
A warm pane also pauses its renderer (`terminalRenderPause.ts`): xterm's own pause is IntersectionObserver-driven and intersection is geometric, so the measurable `visibility:hidden` box would otherwise render every frame with pending writes - continuous invisible main-thread DOM work multiplied by the warm-cache size, paid while a visible pane competes for input latency.
The pause drives xterm's own intersection handler and shadows it while paused, because the observer's asynchronous deliveries would otherwise silently unpause the pane; parsing continues throughout, so the model a reveal repaints is always current.
The pane axis owns both transitions: hide and warm-mount pause, and the reveal resumes before scheduling its redraw.
The internals are pinned to the vendored xterm 6.0.0; if an upgrade moves them the control degrades to a no-op (warm panes simply render again) and the renderer Playwright suite fails loudly.
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
Claude's host is centered and capped at the configured `claude_max_columns` (`claudeWidthCap` in `terminalViewport.ts`, `0` for none), so parent growth beyond that width does not emit another PTY resize.
The cap is applied to the host box as a `max-width` and never to the proposed column count, so FitAddon measures the clamped result and xterm, the registered geometry, and the pixels on screen all derive from one number.
The transient width notice is driven by a second `ResizeObserver` on the host's track rather than by the viewport pass, because the pass is scheduled from an observer on the host and a capped host is exactly the one that stops resizing: a pane already at the cap and dragged wider produces no host resize at all.
Each track resize compares host against track (`claudeWidthCapClamping`) and raises the notice only when a width that actually changed comes back clamped, which keeps a restored wide layout from explaining itself at boot.
The observer is separate from `scheduleBurstFit` so that a drag the cap absorbs costs two `clientWidth` reads rather than a fit proposal per frame, none of which could change the grid.
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

Never persist pending client terminal IDs.
Ordinary terminal launches insert them into optimistic pane state, then replace/remove them when spawn resolves.
Worktree setup keeps its pending identity unpanned, so active-session focus can show it as a temporary full-workspace surface without mutating the durable pane tree.

## Optimistic session removal

Killing a session is slow at the daemon (`design/features/sessions.md`), so `killNow` removes it locally first and settles `DELETE /api/sessions/{id}` afterwards.
The row leaves `sessions`, the leaf leaves the rendered layout, and focus moves to the survivor `nextActiveAfterKill` picks, all before the request is issued.
This is the mirror of `spawnTerminal`'s pending-terminal path, and it needs the same kind of reconciler guard.

`sessionKills.ts` owns that guard.
A `KillTombstone` per in-flight kill excludes the session from two things in `refresh`: the incoming fleet handed to `reconcileSessionSnapshots`, and the live set that `reconcileTerminals` prunes layout leaves against.
Both are required.
`reconcileSessionSnapshots` rebuilds membership from the daemon's list, which still contains the session, so without the first filter any refresh landing mid-kill restores the row.
`reconcileTerminals` only ever removes leaves, so the second filter is what keeps the leaf gone.

The tombstone became the *only* thing that removes a leaf when ended sessions started keeping their panes.
The live set handed to `reconcileTerminals` is now every session the daemon still holds, ended and cold ones included, so a session leaves the layout when it leaves the fleet — killed, or dismissed — rather than when it stops running.
Before that, a session that ended on its own kept its sidebar row and lost its tab in the same instant, and the pruned layout was written back, so the pane showing what it printed was destroyed at exactly the moment somebody wanted to read it (`design/features/session-recovery.md`).

The layout PATCH deliberately waits for the DELETE to succeed, which is the opposite of the ordinary optimistic-write rule and is the point.
Nothing on screen depends on that write while the tombstone stands, and deferring it means a failed kill has no persisted state to undo: the next refresh finds the session live, restores the row and the leaf, and reports the failure.
The write re-derives from `layoutValues.current` rather than replaying the snapshot taken before the wait, because a drag may have landed in between and `removeLeaf` on an already-absent ID is a no-op.

Two rules keep a tombstone from outliving what it stands for, since a tombstone with nothing behind it hides a session that is still running:

- The request carries `KILL_TOMBSTONE_TTL_MS` as an explicit deadline, so it always settles and always clears its own tombstone.
- `refresh` expires tombstones past that TTL anyway, covering a frozen tab whose continuation never ran.

Bulk close (`closeWorkAndHideProject`) tombstones its sessions but still awaits every DELETE, because hiding a Project is refused while live work is attached.

### Where focus lands, and why it is not layout order

`nextActiveAfterKill` prefers **recency over position**: the most recently focused surviving session, preferring one that shared the killed session's pane.
Layout order is only the fallback (visible in the layout, then anywhere in it, then any survivor in the Project), and it used to be the whole rule - which meant closing the pane you were working in dropped you on whatever happened to be leftmost, often a session untouched since the morning.
A recent session that has since left the layout ranks *below* the layout fallbacks: something on screen beats something that is not, whatever was last touched.
Focus still moves only when the killed session held it.

`sessionFocusHistory.ts` is the small most-recently-focused stack behind that, one per Project, bounded and held in a ref.

- **Per Project**, because focus never crosses a Project boundary on a close, so fleet-wide entries would only ever be discarded.
- **Not keyed by pane.** A session moves between panes - dragged into a split, stacked as a tab, dissolved back out - so a pane-keyed stack would strand its entries the moment the layout changed; the pane preference is applied at read time against `stackForView` on the *pre-kill* layout, which is the only version of the pane still true.
- **In memory, not persisted.** It answers "where was I just now", a question about this sitting at this device; surviving a reload would make it a claim about a session nobody has looked at since.

It is fed from the *settled* active session in an effect, the same discipline `viewHistory.ts` uses, because `setActiveId` has dozens of call sites and per-call-site recording rots the first time a new flow forgets.
`killNow` drops the killed id from every Project's stack before handing focus on, so a dead id cannot sit at the head shadowing the live session behind it; reads additionally skip anything not in the surviving set.

## Joining daemon-started sessions

`sessionJoin.ts` is the placement rule for a session that reached the fleet without a leaf, and it
is pure so the rule can be tested without a browser: `joinSessions(layout, ids, preferredViewId)`
returns the layout those sessions belong in, or the same object when nothing was missing.

It runs inside `refresh`, in the same pass that reconciles terminals and previews, because that
GET is the authoritative snapshot: it already carries whatever the daemon attached server-side
(`attach_terminal` for branch, resume, and review spawns), so a session the daemon placed itself is
seen as placed rather than joined a second time somewhere else.

The pass computes the next layout map outside the `setLayoutMap` updater and issues the join
PATCHes after it. The updater stays free of side effects, and the write goes through the ordinary
optimistic `updateLayout` chain - with `quiet: true`, which suppresses only the failure toast and
never the reload behind it.

Four guards keep the join from writing something it should not:

- **`layoutWriteChains`** already skips a Project with a PATCH in flight, so the join inherits
  that: it never bases itself on a layout an in-flight drag has already moved past.
- **`pendingSpawns[…].resolvedId`** excludes a session this device just spawned. Its leaf exists
  under the client-only pending id, and `replaceTerminal` is about to swap the real id into it;
  joining as well would leave the layout holding that id twice.
- **A pending spawn with no `resolvedId` withholds the whole pass from that Project.** The daemon
  creates and announces the session before the POST returns, so a refresh landing in that window
  carries a session that is ours under an id this client does not know yet, and nothing
  distinguishes it from a daemon-started one. The next refresh joins whatever is still floating.
- **`MAX_JOIN_ATTEMPTS`** retires a session whose PATCH the server keeps refusing. A Project at
  `MAX_LAYOUT_LEAVES` refuses every write and each refusal refreshes, which would otherwise
  recompute the same join forever. The record is pruned to the live fleet on every refresh so it
  cannot grow without bound.

Multi-device needs no coordination beyond that. Every connected client computes the same join from
the same fleet; the first PATCH wins and the others take a stale revision, refresh, find the leaf
present and propose nothing. Convergence is structural - a leaf id exists at exactly one place in
one tree - so the worst case is that whichever device wrote first chose the pane.

A reload cannot duplicate or re-float a joined session for the same reason: the leaf is persisted,
and `joinSessions` proposes only ids no leaf in the layout holds.

Mobile needs nothing of its own. `mobileWorkspaceProjection` derives the rail from the pane tree,
so a joined leaf appears there as soon as it exists - which is the mobile half of the defect, since
an unpanned session had no leaf for the rail to project at all.

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

A **static** preview has no owning session, so it takes the same path with two differences.
The POST carries `target_view_id`, the id of the view the request was made from, and
`stack_leaf` groups the preview into that pane; the sidebar lists it as a Project-level row
rather than under a session, and that row reattaches the leaf through `openTab` at the
current anchor instead of re-posting. `previewLabel` in `processFleet.ts` is what every
surface titles a preview by, because a static one has no port to name it with. Nothing else
in the layout code distinguishes the kinds: a `preview` leaf is a `preview` leaf.

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
  that the projection takes no order argument for a stale permutation to re-enter through, and that
  a joined daemon session reaches the rail without moving the selection.
- `frontend/test/sessionJoin.test.ts`: the anchor rule (focused pane, terminal pane, new pane only
  for an empty layout), that the receiving pane keeps its active tab except when the joining
  session is the focused one, fleet ordering into a single pane, idempotence across a reload, the
  refusal budget and its pruning, and (by source inspection) the two `refresh` guards and the quiet
  write.
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
- `frontend/test/sessionKills.test.ts`: tombstone filtering and TTL expiry, 404-means-removed, and
  every branch of the post-kill focus choice (unfocused kill, recency over layout order, the
  vacated pane outranking a more recent session elsewhere, a dead entry in the stack being
  skipped, on-screen outranking a recent session that left the layout, visible sibling,
  stacked-behind fallback, ended and cross-Project exclusions, last session in a Project).
- `frontend/test/sessionFocusHistory.test.ts`: the recency stack itself - head ordering without
  duplicates, identity on an unchanged head, per-Project separation, the bound, and forgetting a
  killed session everywhere.
- Pointer behavior tests/inspection must cover threshold, exact insertion, split edges, cross-pane
  movement, Escape, lost capture, and cleanup after responsive changes.
