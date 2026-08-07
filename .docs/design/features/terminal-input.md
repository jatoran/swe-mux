# Multi-device terminal input

## What it is

One session can be attached from several devices at once, and two of the things they share
cannot be shared: who may type into the PTY, and how big it is. The daemon decides both.
Whoever spoke last does not.

## Key concepts

- INPUT OWNER: the single connection whose bytes reach the PTY. Per session.
- GESTURE claim: tap, click, keystroke. The user's own hand.
- PASSIVE claim: attach, reconnect, restored DOM focus. The pane acting on its own.
- EPOCH: transfer counter on every ownership frame. Lets a client discard a notification
  that lost a race with a newer one.
- DISPLACEMENT vs REFUSAL: input this pane held moved away, versus this pane asked and was
  told no. Both arrive as `input_owner` with `active:false`; they are not the same event.
- VIEWPORT: one client's fitted size. LETTERBOX: rendering the arbitrated size at a
  reduced font instead of re-fitting.

## Operations

### Claim arbitration

Ordered; first match wins.

1. Same connection ⇒ renew, epoch unchanged (a re-claim must not read as a transfer).
2. No owner ⇒ grant.
3. GESTURE ⇒ grant, always.
4. Claimant reports itself unfocused/hidden ⇒ refuse.
5. Another device class is LEADING (`features/device-presence.md`) ⇒ refuse.
6. This device class is LEADING and the owner's is not ⇒ grant.
7. Owner had human input within 10 s ⇒ refuse.
8. Otherwise ⇒ grant.

Rules 5 and 6 are the device-level tier. Ownership is per session and rule 7's window is
seconds long, so neither can express "the human is on their phone right now" — a fact
about the whole app, not about one session. Rule 3 is the escape hatch: sitting down at
the other device and clicking a terminal always works.

### Refused input

Non-owner input is refused, not dropped: `input_rejected` echoes the payload back so the
client re-claims (a gesture — the user typed) and resends once. Losing an ownership race
costs latency, not keystrokes. xterm device replies are discarded instead; a late reply is
worse than none.

### Pointer caret placement

A still primary tap or click in the live agent composer places the editing caret at the nearest reachable terminal cell.
Selection drags, long-press selection, modified clicks, read/select mode, scrollback, and targets outside the current composer do nothing.

Claude owns a real terminal mouse protocol.
Desktop mouse events already enter xterm directly; touch release synthesizes the matching `mousedown`/`mouseup` pair so xterm encodes the coordinates in the protocol Claude negotiated.
The later browser compatibility mouse event remains suppressed, so one tap produces one press/release pair.

Codex enables no terminal mouse mode, so its path is bounded cursor steering rather than a fabricated mouse sequence.
The client recognizes the bottom composer from Codex's `›`/`!`/`»` prefix, two-column text inset, visible hardware cursor, tail position, and either its background block or its blank-row textarea frame, then sends unicast Left/Right batches through xterm's ordinary input path.
The frame fallback is required because Codex deliberately uses the terminal's default background when its palette probe is unavailable.
Each batch waits for Codex's redraw and re-reads the hardware cursor before continuing.
If the movement crosses the target it switches to single-key precision; popup height changes are handled by anchoring the target row to the live prefix.
The operation stops on user input, selection, resize, replay, ownership loss, buffer changes, hidden panes, missing progress, or a changed composer.
The hidden mobile textarea is not used as a document mirror: it remains an end-pinned IME delta bridge and cannot represent the agent's whole draft.

### Wheel scroll to an application-owned viewport

When the application holds the mouse, a scroll gesture becomes a run of SGR scroll reports, one per row of travel, and each report is a full repaint by the CLI (~2-20 KB of output).
xterm emits exactly one report per wheel event whatever magnitude that event carries, so the pane dispatches one line-mode wheel event per row it means to scroll (`forwardApplicationScroll`), bounded at `APPLICATION_SCROLL_MAX_ROWS` per gesture event against a degenerate row-height measurement.
Line mode also steps around xterm's pixel branch, which divides by the measured row height and then damps any delta under 50 pixels to 30% of itself: a touch drag reports 10-40 pixels per move, so every one of them was cut to a third.
A full-width Claude pane was measured consuming ~230 reports per second; a free-spinning wheel or trackpad flick emits thousands, and nothing else in the pipeline sheds load, so the excess was banked scrolling the terminal kept performing for 4-12 seconds after the gesture ended.
The pane therefore paces wheel reports through `terminalWheelPacing.ts`: batches of at most `WHEEL_BATCH_MAX` per animation frame, each released only after the previous batch's repaint arrived (`noteOutput`, the PTY output ack) or after `WHEEL_ACK_TIMEOUT_MS` of silence (an application at its buffer edge repaints nothing), with the queue capped at `WHEEL_QUEUE_MAX` reports.
The wheel is treated as a velocity control, not a distance ledger: past the cap, notches shorten the gesture instead of banking runaway scroll, and a direction reversal drops the stale queue outright.
Ordering is preserved by construction: any non-wheel input flushes the queue first, and a view command (jump-to-latest) discards it, since queued scrolls landing after `^End` would drag the viewport straight back off the tail.
At human scroll rates the ack returns faster than the wheel turns and the pacer is transparent; measured round-trip stays ~5 ms while a 400-notch flick's tail dropped from 4-12 s to under ~300 ms.
Only plain vertical wheel reports (`CSI < 64/65 … M`) are paced: clicks, drags, and modified wheels keep their exact ordering.

### Touch drag scrolling

A one-finger vertical drag scrolls whichever viewport owns the session: xterm's scrollback, or the application's own when it holds the mouse.
`mobile_vertical_drag` picks the target through `mobileDragTarget`, whose `smart` default follows mouse tracking.
Both targets convert one pixel budget with `terminalScrollSteps`, which carries the sub-row remainder into the next move event.
Truncating each event on its own discards up to a row of travel per event, which at a 120 Hz pointer rate is most of the gesture; the fallback it replaces ("any movement scrolls at least one row") corrected for that by over-scrolling every slow drag instead.
Travel is scaled by the user's `mobile_scroll_sensitivity`, then by `touchScrollGain`: 1:1 at or below `slowVelocity`, rising linearly to `maxGain` at `fastVelocity` (`TOUCH_SCROLL_ACCELERATION`).
Velocity is measured on the raw finger by `smoothTouchVelocity`, an exponential average in pixels per millisecond taken before direction and sensitivity are applied.
Reading velocity rather than per-event distance makes the ramp independent of the device's pointer-event rate, and reading the finger rather than the scaled result means sensitivity scales the whole curve instead of moving where acceleration begins.

### Geometry

The input owner's viewport sizes the PTY; with no owner, the smallest visible one, so no
attached client is asked to render columns it lacks. Clients reporting themselves hidden
deregister their viewport entirely — a minimized window still has layout and must not
reshape the PTY for the device in use. Every client is told the result and any client whose
own fit differs LETTERBOXES: shrink the font, never re-fit, because re-fitting is what put
two devices into a resize loop.

**A pane returning to screen adopts its own fit instead of waiting to be told**
(`adoptsOwnGeometryOnReveal`). `serverGeometry` is one round trip stale by construction, and a
warm pane has usually just measured a box that changed while it was `display:none` (the window
was resized, the drawer opened, the UI scale moved). Comparing the two on the reveal is
therefore guaranteed to disagree, so the pane letterboxed to its pre-hide grid, rendered at
that size, and snapped when the daemon confirmed the size the pane itself had just reported.
The input owner is the one client entitled to skip that wait: `effective_geometry` takes the
owner's viewport verbatim, so the confirmation can only agree. Non-owners still letterbox,
because their fit is a proposal arbitration may reduce, and rendering it as settled is the
resize loop this design exists to prevent. The licence is a single pass, armed by the
visibility transition and consumed by the next geometry application; every later resize
letterboxes normally.

**Leaving a letterbox needs no renderer reflow.** Restoring the base font re-measures xterm's
surface on its own, even at an unchanged grid, so the stale-dimension repair
`reflowVisibleTerminalRenderer` exists for does not apply to this path. Measured in
`runLetterboxExitRepair`, which asserts it so that an xterm upgrade regressing the font path
fails loudly rather than silently reinstating the symptom.

**A viewport pass whose host measures zero retries instead of dropping**
(`VIEWPORT_MEASURE_RETRY_FRAMES`). A pane revealed from `display:none` can measure zero for a
frame or two while layout settles, and that pass is the only thing that would register its
real viewport. Returning silently left the pane on its pre-hide grid indefinitely, because the
daemon broadcasts a `geometry` frame only when the arbitrated size actually *changes*
(`Session.apply_geometry`) — an unchanged arbitration is silence, not confirmation, so nothing
corrected the client. The ResizeObserver is not the safety net it appears to be: it triggers
the coalescing burst path, which is deferred on exactly the panes expensive enough for this to
matter, and it fires only if the box changes size again.

**Every resize flood coalesces, including the one the daemon reflects back.** The viewport
scheduler defers burst triggers only while passes are expensive, and the local clock alone
cannot see the expensive half: below ConPTY's reflow threshold xterm's own resize just
appends rows and measures microseconds, while the `resize` frame the pass sent resizes the
real pseudoconsole and makes the CLI repaint everything it shows. A pass that shipped a
`resize` frame is therefore charged at least `EXPENSIVE_VIEWPORT_PASS_MS`
(`effectiveViewportCost`), whatever it cost the browser. The daemon's `geometry` answer to
each registration is itself the fourth flood source and is classed as a burst trigger for
the same reason: an eager fit on that frame re-measures a still-moving divider, sends the
new grid, and the echo of *that* registration schedules the next pass — a pseudoconsole
resize (and a full CLI repaint) every websocket round-trip for as long as the gesture
lasts, invisible to the ResizeObserver's coalescing. Measured on a 2x2 grid before both
rules existed: a continuous splitter drag sent ~22 resizes per second per visible pane
(~1,200 CLI repaints in one gesture) and a window-resize sweep dropped a frame per step;
after, the same drag sends one resize per `VIEWPORT_SETTLE_MAX_MS` per pane and the sweep
drops none.

**A slow health sweep enforces the display invariant the event paths cannot guarantee**
(`frontend/src/terminalHealth.ts`, run on the pane's existing 5 s `terminal_state` interval).
Every event-driven repair above covers a named path; the sweep covers the unnamed ones by
comparing invariants against reality. Surface drift — a visible, settled, non-replaying pane
whose host box or grid differs from the surface it last confirmed drawing — schedules an
ordinary viewport pass, turning "the user drags a splitter to bump the pane" into
self-correction within one sweep. Write-pipeline death — bytes arriving on the socket while
`onWriteParsed` stops advancing (`WRITE_PIPELINE_STALL_MS`) — is a parser exception having
killed xterm's write loop, which no event reports; the pane rebuilds itself (fresh Terminal,
socket, and replay), budgeted by `remountDecision` to two attempts per five minutes so a
poison byte sequence in the retained buffer degrades into one visible error rather than a
remount loop. Both detections leave `surface_drift_repair` / `write_pipeline_dead`
breadcrumbs in the render diagnostics.

A client registers a viewport only when it fitted itself *while on screen*
(`attachRegistersViewport`). Both halves are load-bearing, and getting either wrong pins a
session to a size nobody chose: a pane's own visibility is not `document.hidden` (a warm
pane is `display:none` inside a foreground tab), and a pane that could not fit — its host
measures zero — is still holding xterm's unfitted 80x24 default, or, after a letterbox,
another device's grid, since leaving a letterbox restores the font but not the grid.
Because ownership carries geometry, an unfocused client is refused an unowned session too;
otherwise a background pane wins it by default and resizes the session for whoever can see
it. Deregistration is correspondingly unconditional: a pane going hidden withdraws whether
or not it ever recorded a fit of its own.

The visible **Take over** and **Resize** actions are geometry operations as well as ownership
claims. The client restores its base font, synchronously fits the visible host, force-registers
that measured viewport, then sends the gesture claim on the same WebSocket. Frame ordering is
intentional: a claim by the existing owner is only a lease renewal and performs no geometry
work, while a claim that changes owners must use the freshly registered viewport.

## Invariants

- A refusal is never grounds to claim again. Clients re-claim only on displacement, at most
  once per 5 s; the daemon leaves a connection's repeated passive claims unanswered for 1 s
  after refusing one. Answering every refusal is what turned one into a claim/deny loop
  running at the speed of the round trip.
- Opening a session says nothing to the user. Display needs no ownership and the first real
  keystroke claims input by itself, so a refused attach costs nothing and reporting it
  prompts the user to fix what is not broken.
- Ownership is released when its connection ends, before anything is awaited — a handler
  cancelled on disconnect re-raises at its first await.
- A pane never reports a size it did not measure on screen. Unmeasured dimensions are not
  a smaller viewport, they are no viewport.
- A user-requested resize registers the freshly measured viewport before it claims input. An
  ownership renewal alone is not a resize.
- A persistent letterbox is stated in the pane. `inputOwnerNotice` speaks only when this
  pane was refused, so without a standalone notice the case that looks most broken —
  someone else's grid, drawn with no explanation — was the one that said nothing.
- File/image attachment references are unicast regardless of the pane's broadcast membership.
  They still travel through xterm's paste/input path so replay bounds and bracketed-paste rules
  apply; only the broadcast bit is forced off for the synchronous attachment insertion.
- Pointer-generated mouse reports and Codex caret-steering keys are unicast regardless of broadcast membership.
  A pointer target belongs only to the pane in which it was chosen.

## API surface

PTY WebSocket frames, typed in `design/interfaces.md`: `claim_input`, `input_owner`,
`input_owner_released`, `input_rejected`, `resize`/`attach_ready` (with `hidden`),
`geometry`.

## Diagnostics

`GET /api/sessions/{id}/state-log` → `input_arbitration`: `active_devices`,
`leading_device`, `owner_device`, `owner_epoch`, `attached_viewports`, `geometry`,
`input_rejections`, `claim_denials`, and `claims` — the last 24 decisions with the asking
device, what it reported about itself, what the daemon believed, and the verdict. A counter
says a claim was refused; only that log says which device asked and why it lost.
Opt-in terminal diagnostics also record `caret_placement_started` and `caret_placement_finished` with the outcome, elapsed time, and number of steering keys.

## Constraints + trade-offs

- Arbitration is server-side because two clients cannot agree about a resource neither
  owns, and the daemon is the only party that sees both.
- Letterboxing by font size, not CSS transform: xterm derives cell geometry from the font,
  so selection and hit-testing stay consistent with what is drawn.
- `document.hasFocus()` is read per device class (`features/device-presence.md`). Read raw,
  it made a phone's every passive claim look like a background window's.

## Configuration

`PASSIVE_CLAIM_HOLD_SECONDS` (10) in `src/swe_mux/terminal_arbitration.py`,
`REFUSED_CLAIM_COOLDOWN_SECONDS` (1) in `src/swe_mux/server.py`, `RECLAIM_COOLDOWN_MS`
(5000) and `GESTURE_WINDOW_MS` (1500) in `frontend/src/inputOwnership.ts`,
`MIN_LETTERBOX_FONT_PX` (4) in `frontend/src/terminalLetterbox.ts`,
`LETTERBOX_NOTICE_DELAY_MS` (1500) in `frontend/src/TerminalPane.tsx` — every ordinary
resize letterboxes for one round trip, so only a letterbox that outlives this is stated.
`VIEWPORT_MEASURE_RETRY_FRAMES` (5) in `frontend/src/terminalViewport.ts`.
None user-facing.

## Key files

- Rules (pure): `src/swe_mux/terminal_arbitration.py`
- Ownership/viewport state, geometry fanout: `src/swe_mux/session.py`
- Frame handling, claim decisions, decision log: `src/swe_mux/server.py` (`pty_ws`,
  `_claim_terminal_input`, `_handle_terminal_input`, `_apply_client_viewport`)
- Client ownership model (pure): `frontend/src/inputOwnership.ts`
- Letterbox math and the reveal-adoption rule (pure): `frontend/src/terminalLetterbox.ts`
- Wheel-report pacing to an application-owned viewport (pure): `frontend/src/terminalWheelPacing.ts`
- Viewport pass scheduling and the resize-sending cost charge (pure): `frontend/src/terminalViewport.ts`
- Socket, DOM, take-over strip: `frontend/src/TerminalPane.tsx`
- Provider-aware pointer targeting and steering math: `frontend/src/terminalCaretPlacement.ts`

## Relates to

- `features/device-presence.md` — supplies LEADING, the device-level tier of the claim
  rules.
- `features/sessions.md` — attach/replay lifecycle these frames ride on.
- `features/ui.md` — when the take-over strip is shown.
