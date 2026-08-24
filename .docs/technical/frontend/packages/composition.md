# Frontend: composition and shared runtime

Index: `../packages.md`.

## Workspace composition

`App.tsx`, `fleetRefresh.ts`, `fleetLayouts.ts`, `fleetCommands.ts`, `layoutWriter.ts`, `sessionSnapshots.ts`, `sessionKills.ts`, `sessionFocusHistory.ts`, `warmPanes.ts`, `uiBuild.ts`

- Coordinates Projects, sessions, layouts, menus, and overlays.
  The controllers beside it own the parts with rules of their own: reading the fleet (`fleetRefresh.ts`), reconciling pane layouts against a snapshot (`fleetLayouts.ts`), writing a layout back (`layoutWriter.ts`), and the fleet-derived half of the command registry (`fleetCommands.ts`).
- Event recovery is watermark-based, with bounded replay and one authoritative refresh for cold or wide gaps.
- At reconnect it compares the production identity embedded in the loaded document against the served identity, reloading automatically only while hidden and raising a persistent manual banner while visible.
- Hidden terminal warming is desktop-only; mobile opens no warm sockets.
- One-minute safety refreshes and a 10-second reduced process watch run alongside.
- `sessionSnapshots.ts` is the pure REST/PTY merge contract.
  It rejects stale same-generation revisions while preserving enriched title and readiness fields across raw PTY updates.
- `sessionKills.ts` owns optimistic removal: tombstones, TTL, and `nextActiveAfterKill`.
- `sessionFocusHistory.ts` owns the bounded per-Project most-recently-focused stack that session choice consults.
  It is fed from the settled active session rather than from `setActiveId`'s many call sites, and is held in memory because it answers "where was I just now" (`../workspace-state.md`).

## Fleet refresh

`fleetRefresh.ts`, `fleetLayouts.ts`

One cycle reads five independent daemon registries - sessions, Projects, previews, Groups, harnesses - and applies whatever arrived.

- **Every read carries a deadline** (`FLEET_REFRESH_TIMEOUT_MS`), which is `api.ts`'s own rule for a request a view cannot render without.
  These five had none, and the dedupe below then pinned every later refresh - interval, visibility, socket reconnect - behind the hung promise until the page was reloaded.
- **Slices are applied one by one**, from `Promise.allSettled`.
  A fail-fast `Promise.all` used to discard the whole snapshot for that cycle over a single transient 500.
  The layout pass is the one step that needs three slices at once (sessions, Projects, previews) and is skipped when any of them is missing; the next full cycle reconciles.
- **The in-flight cycle can be abandoned.** A cycle outrunning `FLEET_REFRESH_STALL_MS` is aborted and dropped, so a request that never settles costs one slow cycle rather than every refresh for the life of the page.
  An abandoned cycle that finally returns is ignored by generation, so it cannot move the UI backwards.
- **A caller arriving mid-cycle gets the follow-up, not the cycle in flight.**
  `await refresh()` after a mutation used to be handed a promise whose GETs left before the mutation did, and so resolved with a fleet that had never seen the change.
  Follow-ups coalesce: many callers during one cycle share one queued cycle.
- `fleetLayouts.ts` is the pure half: given one snapshot it returns the layouts to store, the joins to persist quietly, and the pruned join-refusal record.
  The join rules live there - a Project mid-launch is withheld, a session this device already has a pending leaf for is not joined twice, ended sessions keep a leaf but are never given one.

## Command registry

`commands.ts`, `fleetCommands.ts`, `App.tsx`

Every keyboard chord, palette row, voice phrase and gesture runs through one list of `Command`s, built each render at the composition root.
It is in two halves, split by what each depends on:

- The **hand-written half** stays inline in `App.tsx`, because its `available` and `label` expressions read live UI state directly - the focused pane, the zoom, the drawer, the broadcast switch.
- The **fleet half** (`fleetCommands.ts`) is one command per numbered Project slot, per Project, per live session, and per Project-and-harness launch pair, with the spoken phrases each of those carries.
  That half scales with the fleet and does the string work, so it is memoized on what determines it: the Project and session records, the active Project, the sidebar order as a value (`displayOrderKey` - `displayProjects` is derived fresh every render and has no stable identity to key on), and the harness registry revision.
  Its `run` handlers reach the current render through a ref-backed facade whose identity never changes, so a memoized command can never act on a snapshot the operator has already moved past.

`paletteResults(open, commands, query)` is the gate on the search: while the palette is closed nothing is scored.
`searchCommands` fuzzy-scores the whole registry - a string build and a sort over hundreds of entries - and its only consumer is the palette's result list, yet it used to run on every render of the shell.
The gate lives in `commands.ts` rather than at the call site so the renderer harness exercises the same function the app does (`test/renderer/palette-gating.spec.ts` counts the label reads the scorer makes).

## Connection liveness

`liveness.ts`, `api.ts`

One recovery policy for every long-lived socket and load: attempt deadlines, resume signals, and backoff, plus the `fetch` wrapper's request timeout.

Nothing that reaches the daemon may end in a state only a remount can leave.
A resumed PWA can hang a WebSocket handshake or a `fetch` indefinitely without erroring, so every attempt carries a deadline (`HANDSHAKE_TIMEOUT_MS` for sockets, `REQUEST_TIMEOUT_MS` passed to `api`), failures back off, and `watchResume`/`watchLiveness` re-check on visibility, `pageshow`, `online`, focus, and a visible-only poll.
`shouldForceReconnect` is the single pure decision - stalled handshake, backoff due, or an attempt older than a suspension long enough to have killed it silently - so the `/events` socket (`App.tsx`), each `/pty` socket (`TerminalPane.tsx`), resource loads (`ProjectResource.tsx`), and autosave retries (`noteSaveQueue.ts`) all recover the same way instead of each inventing a rule.
A resume burst (visibility, focus, and online together) collapses to one attempt, and a failure surfaced to the user always offers an immediate retry.

## Server clock

`serverClock.ts`, `api.ts`

The offset between this device's clock and the daemon's, so a timestamp the daemon wrote is aged against the clock that wrote it.
It is sampled from the HTTP `Date` header inside the `api()` wrapper, the one choke point every request already passes through, so no endpoint opts in and error responses count - an outage cannot silently freeze the correction.
Latency is halved out at the round-trip midpoint and the held value only moves outside a noise floor, because the offset feeds durations the user watches count up.
`useRowClock` in `sessionRowPrefs.ts` consumes it; anything else ageing a daemon timestamp against `Date.now()` has the same bug and should read `serverNow()`.

That hook is subscribed **below** the composition root, in `SessionRowLive.tsx`, and the root must not read it.
A sidebar row is the only surface that has to re-read the wall clock on its own ("12m" is a fact about now, not about the session), and while the root derived it every five-second tick was a state change on the shell: every menu, drawer, tab strip and pane frame re-rendered to age a handful of rows, with terminal panes spared only by `TerminalPane`'s own memo comparator.
The interval behind `useRowClock` is module-scoped and shared, so N subscribed rows still cost one timer - which is what makes "one timer for the whole sidebar" and "the tick does not re-render the shell" hold at the same time.
`deriveRowFleetFacts` is the clock-free half of the row context the root does derive, once per fleet snapshot.

## Redeploy progress

`redeployProgress.ts`, `RedeployChip.tsx`

The frozen-app rebuild's two unalike stages, and everything the UI does about them.

- `redeployProgress.ts` is pure and DOM-free: the `idle | building | down` phase, the probe-to-verdict transition, the `sessionStorage` sentinel, and the outcome sentence.
- The split is the whole point.
  During the multi-minute build the current daemon keeps serving, so the app stays usable and only the corner chip appears.
  The daemon-down tail blocks and suppresses request-failure toasts, because keystrokes typed into a terminal then go nowhere - the PTY sockets are proxied by the daemon, not held open to the supervisor.
- `applyProbe`'s `sawDown` separates "the successor is up, reload into it" from "the build failed and this is the same daemon that has been serving all along"; reloading in the second case would discard the failure message and load nothing new.
- Two consecutive failed probes are required before the overlay, so a phone waking or a momentary stall cannot raise it; the authoritative `daemon_redeploy_stopping` broadcast skips that wait.
- Restored sentinels are clamped to `building` with `sawDown` cleared, because there is no offline shell: a page that loaded at all was served by a live daemon.
- `RedeployChip.tsx` is the top-bar spinner every client shows, expandable to the phase, the elapsed timer, and the daemon-served build log tail.
  Two placements, one per top bar, and `App` mounts whichever bar exists rather than both: `inline` makes it a control inside `.mobile-toolbar`, and without it a card floating under `.app-topbar`.
  Mounting both would run a second one-second elapsed timer for a chip in a `display:none` container.
  The elapsed clock is deliberately the only progress signal once the daemon is gone, because the sole process that knows more is the one that took it away.

## Shared interaction

`dragReorder.ts`, `pointerDragClaim.ts`, `menuPosition.ts`, `modalFocus.ts`, `dismissStack.ts`,
`systemBack.ts`, `viewHistory.ts`, `keys.ts`, `workspaceTabs.ts`, `sidebarProjects.ts`, `projectSort.ts`,
`projectRecency.ts`, `sessionAttention.ts`, `humanPresence.ts`, `wheelScroll.ts`, `MenuGroup.tsx`

Pure or narrowly stateful reusable behavior: modified-Tab classification, focused-pane tab cycling, `wheelScroll.ts`'s wheel-to-horizontal translation for sideways-only tab strips and the terminal Action rail, and collapsed-Project labels.

### Drag and drop

`dragReorder.ts` owns movement-versus-hold activation decisions, reorder targets, and edge auto-scroll velocity.
Every touch hold-to-lift surface - sidebar Projects and sessions, the mobile tab bar, and the Configure Actions editor's chips and catalog - uses the single `MOBILE_HOLD_DRAG` constant (350 ms, 16 px slop); the drawer strip keeps `MOBILE_HOLD_MOVE_DRAG`, and other pointer drags keep the 5 px movement threshold.
The hold slop is wide because a past-slop move is a *cancel* rather than a deferral, so a narrow one reads a resting finger's jitter as a scroll and the lift silently never happens.
One constant rather than a per-surface value is the point: a surface that picks its own drifts from the hardening the shared one accumulates.
`listDropTargetForPoint` is the sidebar-session variant, where a row is both a slot boundary and a target in its own right: the outer `insertionEdge` of each row (30% of its height, floored at 5 px and capped at 12 px, so a 44 px phone row and a dense desktop row both stay aimable) reads as insertion and the middle as grouping.
A `groupable` predicate turns a row that cannot be grouped with into pure insertion over its whole height, rather than a middle band that quietly does nothing.
`DROP_LIST_MARGIN` is how far outside a list the pointer may stray before the drop resolves to nothing, which keeps a session drag from committing to a Project the pointer has left.
`pointerDragClaim.ts` is deliberately process-wide: a running drag claims the pointer there so the mobile gesture recognizer stands down, which is arbitration between two window-level listeners and has nowhere else to live.

### Sidebar ordering and attention

`projectSort.ts` owns sidebar ordering at both levels: one Project mode applies to root Projects and Projects inside every Group, while the Group mode orders only explicit Groups.
Its device-local persistence holds only the two sort modes and Group fold state; Recently used ranks the daemon-owned `Project.last_used_at` timestamps.
Both levels take the same contract - manual order in, stable sort out - so `custom` is a pass-through and the hand arrangement is every mode's tie-break.
`projectRecency.ts` reports successful prompt submissions from terminal, Queue, and voice surfaces, and `App.tsx` records successful user-initiated session starts through the same daemon endpoint and applies monotone `project_used` events from every client.
Opening or focusing any surface is never a recency signal.
`bucketRecency` makes a Group as recent as its most recently used Project, and `mergeVisibleOrder` folds a permutation of the rendered rows back into the full list so hidden Projects and empty Groups keep their slots.

`sessionAttention.ts` derives nothing itself: unread is `turn_seq > read_turn_seq` on the record the daemon serves, and its `AckMap` is only an optimistic overlay for acknowledgements still in flight, so a snapshot that briefly omits a session can never bring it back marked read.
Only a **settled** agent counts as unread, so a mid-turn session never claims the sidebar's brightest tier - unless the user hand-marked it, since `unread_pin` is a statement rather than a measurement.
A pin also suppresses the dwell acknowledgement of the pane it was set on, for that visit only: `trackPinVisits` releases the pin once the session leaves the screen and the next dwell retires it with an explicit read, which keeps a hand-set mark from outliving the reading of the thing it marked.
`projectSetRailStatus` is the same aggregate over a set of Projects, so a folded Group reports the strongest agent state it is hiding.
`humanPresence.ts` gates acknowledgement on `visibilityState === 'visible'` and window focus, which is a different question from a pane being mounted and on screen.

### Dismissal and back

Escape, the platform back gesture, and the mobile back swipe are three window-level entry points that must agree on which single level closes, so the ordering lives in `dismissStack.ts`, one DOM-free store, rather than in any of them.
It is pure and testable: temporal ordering, an `enabled` gate that never moves a level, blocking levels that absorb a pop, a repeat guard so a double-fire cannot close two levels, and a bounded trace ring for field diagnostics.
Ordering is by activation rather than registration, which lets `App.tsx` register its always-mounted dialogs and menus up front and still have them pop in the order the user opened them.
`modalFocus.ts` is its only registration path (`useModalFocus` for a focus-trapped modal, `useDismissLevel` for a drill-down inside one), which keeps membership from drifting from focus containment.
`systemBack.ts` owns the browser half and nothing else: a single sentinel history entry maintained against a `BackTarget`'s depth, with the popstates it causes itself counted and ignored, deliberately blind to which rung answered.
`viewHistory.ts` is the second rung - a bounded MRU ring of recent `(Project, view)` pairs that back walks once nothing is layered, plus the `composeBackTarget` that puts it under the dismiss stack as one target.
It is in memory rather than in `history` because Chrome's history-manipulation intervention would make gesture-less pushes skippable and focus here moves programmatically all the time; the ring is armed on the mobile layout only, recorded on every layout, and asked about liveness at pop time so a closed pane costs no press.
That composite is not what feeds `gestureOverlayDepth`, which asks a different question ("is an overlay covering the workspace") whose answer silences every non-back gesture slot.

`MenuGroup.tsx` is the collapsible menu group (desktop flyout, touch accordion) any `.context-menu` can host.
It collapses itself when its host menu unmounts, so a group left expanded is not still expanded the next time that menu opens.
