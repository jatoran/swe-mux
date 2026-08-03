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

### Geometry

The input owner's viewport sizes the PTY; with no owner, the smallest visible one, so no
attached client is asked to render columns it lacks. Clients reporting themselves hidden
deregister their viewport entirely — a minimized window still has layout and must not
reshape the PTY for the device in use. Every client is told the result and any client whose
own fit differs LETTERBOXES: shrink the font, never re-fit, because re-fitting is what put
two devices into a resize loop.

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
None user-facing.

## Key files

- Rules (pure): `src/swe_mux/terminal_arbitration.py`
- Ownership/viewport state, geometry fanout: `src/swe_mux/session.py`
- Frame handling, claim decisions, decision log: `src/swe_mux/server.py` (`pty_ws`,
  `_claim_terminal_input`, `_handle_terminal_input`, `_apply_client_viewport`)
- Client ownership model (pure): `frontend/src/inputOwnership.ts`
- Letterbox math (pure): `frontend/src/terminalLetterbox.ts`
- Socket, DOM, take-over strip: `frontend/src/TerminalPane.tsx`

## Relates to

- `features/device-presence.md` — supplies LEADING, the device-level tier of the claim
  rules.
- `features/sessions.md` — attach/replay lifecycle these frames ride on.
- `features/ui.md` — when the take-over strip is shown.
