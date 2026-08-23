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

Routing is decided by the terminal's measured mouse mode, not by which harness is running.
An application that turned mouse reporting on asked for clicks and positions its own caret from them, so a still touch release in such a pane is forwarded and nothing harness-specific is involved.
Claude and opencode both negotiate tracking and both behave this way; a shell application that enables reporting gets the same treatment for free.
Desktop mouse events already enter xterm directly - measured against a live opencode pane, a plain click moves its caret with no help from this layer - so only touch needs the synthesized `mousedown`/`mouseup` pair, because a touch's own compatibility mouse event is the one the pane suppresses.
That suppression is also why one tap produces exactly one press/release pair.

Everything else enables no terminal mouse mode, so its path is bounded cursor steering rather than a fabricated mouse sequence.
`caretResolverForBackend` is the single declared home for those per-harness deviations: a harness either appears in that registry or gets no tap behaviour, and adding one means measuring its composer rather than adding a name check anywhere else.
Codex, OMP, and pi are the three entries.
The client recognizes the Codex composer from its `›`/`!`/`»` prefix, two-column text inset, visible hardware cursor, tail position, and either its background block or its blank-row textarea frame, then sends unicast Left/Right batches through xterm's ordinary input path.
The frame fallback is required because Codex deliberately uses the terminal's default background when its palette probe is unavailable.
The OMP composer is its measured 17.2.10 box: a top border embedding the status line that reads `╭── π` at columns 0-4, `│` interior draft rows, the final draft line fused into the `╰─ … ─╯` bottom border, and text starting at column 3 on every draft row.
The `π` brand cell is the discriminator against OMP's other bordered surfaces - a model picker or dialog carries a title there instead, and refusing those matters because arrows sent into a picker move its selection.
The pi composer is its measured 0.84.1 shape: two rules of `─` spanning every column bracket the draft, text starts at column 0 with no gutter or border, and the rows between the rules are exactly the wrapped draft, with an empty draft rendering as one blank row.
pi hides the hardware cursor and paints its own reverse-video caret, so the caret's written blank is excluded from a row's reachable end - counting it would leave the loop pushing an arrow key pi has nowhere to apply.
pi's own pickers (`/model`, `/settings`) reuse the same bracketing rules, and two positive detections refuse them: a picker lays a blank row directly under the top rule with its content below that, and marks its selected row with `→` in column 0.
`/settings` additionally parks the hardware cursor at the bottom-right corner, outside the rules entirely.
Neither refusal is a contiguity test, because Ctrl+J inserts a newline and a draft may legitimately contain blank rows further down.
Each batch waits for the CLI's redraw and re-reads the hardware cursor before continuing.
If the movement crosses the target it switches to single-key precision; popup height changes are handled by anchoring the target row to the composer's own anchor row.
The operation stops on user input, selection, resize, replay, ownership loss, buffer changes, hidden panes, missing progress, or a changed composer.
The hidden mobile textarea is not used as a document mirror: it remains an end-pinned IME delta bridge and cannot represent the agent's whole draft.

### Wheel scroll to an application-owned viewport

When the application holds the mouse, a scroll gesture becomes a run of SGR scroll reports, and each report is a full repaint by the CLI (~2-20 KB of output).
xterm emits exactly one report per wheel event whatever magnitude that event carries, so the pane dispatches one line-mode wheel event per report (`forwardApplicationScroll`), bounded at `APPLICATION_SCROLL_MAX_ROWS` per gesture event against a degenerate row-height measurement.
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
Terminal scrollback converts its pixel budget with `terminalScrollSteps`, which carries the sub-row remainder into the next move event.
Truncating each event on its own discards up to a row of travel per event, which at a 120 Hz pointer rate is most of the gesture; the fallback it replaces ("any movement scrolls at least one row") corrected for that by over-scrolling every slow drag instead.
Application-owned scrolling carries its remainder the same way through `applicationTouchScroll`, but converts against the application's row unit rather than the terminal's, because a wheel report is not universally worth one row.
Claude Code 2.1.229 moves three rows per report, so a drag on it forwards one report per three finger rows; other mouse-aware applications keep one report per row.
The daemon publishes that multiplier as `touch_scroll_rows_per_report` through the harness registry; the browser never selects this behavior from a harness name.
Nothing on this side rate limits or discards travel: shedding a fast flick's excess belongs to the wheel pacer above, which bounds it against the CLI's measured repaint rate rather than a fixed interval, and a second limiter here could only drop travel a drag asked for.
That is not hypothetical - a 120 ms minimum report interval, tried once to absorb pacer delay, capped every drag at three rows per 120 ms and read as a viewport ignoring the finger.
Travel is first scaled by the user's `mobile_scroll_sensitivity`, and each completed application gesture records aggregate input pixels and report count in `mobile_application_scroll` without terminal content.
Forwarded scroll is what the pane can count, not everything that moves the application's viewport: a submission moves it too, which is why a harness declares `app_tail_reset` and the jump-to-latest estimate is dropped on one (`features/ui.md`, `features/backends.md`).

### Geometry

The input owner's viewport sizes the PTY; with no owner, the smallest visible one, so no
attached client is asked to render columns it lacks. Clients reporting themselves hidden
deregister their viewport entirely — a minimized window still has layout and must not
reshape the PTY for the device in use. Every client is told the result and any client whose
own fit differs LETTERBOXES: shrink the font, never re-fit, because re-fitting is what put
two devices into a resize loop.

**An alternate-screen child is pulsed into repainting once its size settles**
(`needs_resize_repaint`, `RESIZE_REPAINT_SETTLE_SECONDS`).
Three participants disagree after a width change and none of them can see it: xterm never reflows the alternate buffer, because reflow is gated on the buffer having scrollback and the alternate buffer has none, so lines are padded or truncated in place with the old wrapping intact; ConPTY does rewrap its own console buffer and then emits only what changed against it; and the child's SIGWINCH repaint lands in that already-rewrapped buffer and mostly diffs away to nothing.
The browser is left holding cells from the old wrapping with nothing on the way to overwrite them.
Growing is the direction users see, because rewrapping wider changes least in ConPTY's buffer and therefore emits least exactly when the browser needs most, while shrinking pushes text down and emits enough to repair the screen incidentally.
So a settled arbitrated geometry change schedules one `repaint_current_geometry` pulse on a trailing-edge debounce: a drag emits changes at frame rate and costs exactly one pulse after the pointer stops, on the size the user actually stopped on.
Every pulse is recorded as `terminal_repaint_requested` with `source=daemon` and `reason=resize_settled`, so its firing rate is measurable rather than assumed.
Normal-screen harnesses are excluded because they keep repainting their live region and fill any gap within a frame; their opposite problem, a ring wrapped into a scrollback-free replay, stays client-requested under `repaints_scrollback`.
The same pulse also runs once after an attach whose replay was a bounded window rather than everything retained, which is the other way an alternate-screen pane arrives incomplete (`replay_needs_repaint`, `features/sessions.md`); that one carries `reason=truncated_replay`.

**Desktop agent width policy is applied before viewport registration.**
Claude's host has a centered column maximum, so making its pane wider adds margin instead of repeatedly resizing the PTY through Claude Code's known stale-cell and duplicate-live-region failure.
The maximum is the `claude_max_columns` setting (Settings → Terminals), one of a fixed set of steps with `0` meaning no cap at all, defaulting to the 120 columns the app has always used.
It is configurable because its evidence is a rendering defect in a CLI that ships on its own schedule, so a number measured once is not permanently correct, while a stale one costs the user width with no way to tell a deliberate envelope from a terminal refusing to resize.
An uncapped Claude pane carries no host style at all, so "the envelope is disabled" and "this build has no envelope" are one code path.
Compact panes are never capped, on the same grounds as the Codex minimum: every offered step is wider than the device.
A pane whose width change is clamped by the cap raises a transient notice naming the limit, with a route into the setting, because the clamp is otherwise indistinguishable from a broken resize.
Codex's own diagnostics recommend at least 80 columns.
When an ordinary desktop pane would fall below that, mux reduces the xterm font to a floor of 8 px, proposes dimensions again, and sends only the resulting grid; replay therefore starts with the same width xterm is already rendering.
Compact mobile panes are excluded because preserving readable touch-device type is more important than imposing a desktop minimum.

**A pane returning to screen adopts its own fit instead of waiting to be told**
(`adoptsOwnGeometryOnReveal`). `serverGeometry` is one round trip stale by construction, and a
warm pane has usually retained an old fit while its measurable hidden box changed (the window
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
(`VIEWPORT_MEASURE_RETRY_FRAMES`). A newly visible pane can still measure zero for a frame or
two while layout settles, and that pass is the only thing that would register its
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

**Live terminal output is credit-limited at the parser boundary.**
`attach_ready` advertises `output_flow_control:true`, and the client returns byte credit with `output_ack` only from xterm write callbacks.
The daemon stops sending binary output to that connection when unparsed output reaches 128 KiB and resumes below the threshold.
This is separate from TCP and WebSocket backpressure, which only prove that Chromium accepted a frame and say nothing about whether xterm parsed it.
Attach and resync replay use the same accounting so a late replay acknowledgement cannot release credit belonging to newer live output.
Clients that do not advertise the capability keep the previous protocol and cannot deadlock waiting for acknowledgements they never send.
The health sweep records `write_pipeline_backlog` when at least 32 KiB has remained queued for 750 ms; backlog is diagnostic because credit already bounds it, while a dead parser still follows the existing remount path.
A hidden warm pane withholds parsed-byte credit after its first bounded window and returns the accumulated credit when revealed.
This keeps hidden busy agents from continuously consuming the browser UI thread while preserving their subscriber and session.

**Physical input carries content-free latency correlation.**
The client timestamps the native keyboard, IME, or paste event, xterm `onData`, and WebSocket send on the browser performance clock.
It attaches a bounded sequence number, client wall time, stage durations, input source, and WebSocket backlog to the input frame without duplicating the typed payload into telemetry.
The daemon acknowledges accepted traced input with `input_ack` and adds the timing fields to the existing sampled `terminal_input` event.
The client correlates the next live output batch through xterm parse completion and the next animation frame.
Only a stage reaching 400 ms becomes a durable `terminal_input_diagnostic` event.
A single 250 ms page clock also reports an input-adjacent main-thread stall at 500 ms or longer, but only for a visible pane whose physical input predates the stall by at most 10 seconds.
Pending correlations expire after 30 seconds and are capped at 128, diagnostic detail is clamped by the daemon, each phase is rate-limited independently, and no diagnostic contains terminal input text.
The phase breakdown and incident procedure live in `../../development/TERMINAL_INPUT_INCIDENT_RUNBOOK.md`.

A client registers a viewport only when it fitted itself *while on screen*
(`attachRegistersViewport`). Both halves are load-bearing, and getting either wrong pins a
session to a size nobody chose: a pane's own visibility is not `document.hidden` (a warm
pane is logically hidden inside a foreground tab), and a pane that could not fit — its host
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
- A finished attach does not take DOM focus out of a text field elsewhere in the app
  (`focusHeldByOtherField`). The socket opens on the daemon's clock, not the user's, so an
  attach landing a few hundred milliseconds into a sidebar filter or a rename dialog would
  swallow the words already typed there. This is the same rule as the passive claim beside
  it: attaching is not the user asking for the keyboard. It is deliberately narrow - only a
  real text field, and only one outside a terminal - so a pane still takes focus from a
  button, from the document body, and from another terminal, which is where the attach focus
  earns its keep.
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
- Synthetic paste-and-submit actions append through the mounted pane, wait 180 ms for an interactive TUI to commit bracketed paste, and only then send carriage return.
  Append-only actions do not wait or submit.
  The action acknowledgement follows the carriage return, so a caller cannot clear its source draft before submission was actually attempted.
- Native terminal text paste and the command-rail Paste action converge on the mounted pane's paste path.
  The pane takes ownership of the native clipboard event before xterm can turn unbracketed newlines into carriage returns.
  Agent multiline text is manually bracketed when xterm's mode mirror is stale, while shell and single-line paste behavior stays unchanged.
- Pointer-generated mouse reports and caret-steering keys (Codex, OMP, pi) are unicast regardless of broadcast membership.
  A pointer target belongs only to the pane in which it was chosen.

## Inserting authored text

Everything that pushes text a person wrote somewhere else into a composer - a prompt template, a skill or slash item, a custom text item, a clipboard entry, a dictated draft, a note or file selection, the branch seed - builds its bytes in one place (`frontend/src/composerInsertion.ts`, `src/swe_mux/composer_input.py::composer_insertion`) and, in the browser, performs them in one place (`injectText` in the pane).
The two builders are kept in step by the harness registry rather than by matching constants, and `frontend/test/composerInsertion.test.ts` and `tests/test_composer_input.py` assert the same wire bytes on both sides.

- **A paste wrapper does not protect the payload's first character.**
  Measured 2026-08-22 against Codex v0.149.0 over a real pseudoterminal, with `PREVIOUSTEXT` sitting unsent in the composer: `ESC[200~A\rB\rC ESC[201~` lands as a three-line draft, while the same paste with one *leading* CR **submits the standing draft** and pastes the rest as a fresh one.
  A leading LF is not a repair either - Codex drops it and concatenates the paste onto the draft.
  Claude Code v2.1.240 keeps the draft on all three.
  This is not theoretical: the live `Tree` prompt template begins with a newline, so pressing its button over a half-typed draft sent that draft to the model.
- **So a leading newline run is lifted out of the paste** and sent ahead of it as the harness's own `composer_newline` key presses, which is exactly what an author who began a template with a newline meant - start below whatever is already there.
  It is one PTY write, measured: Codex reads `ESC+CR` followed by a bracketed paste as "newline, then paste".
  Interior newlines stay inside the paste, where both agents read them as content.
- **Only a harness measured as needing it gets the lift.**
  `HarnessDescriptor.paste_leading_newline_submits` is true for Codex and false everywhere else, and false is also the answer for a harness nobody has characterised - which keeps it on the bytes mux has always sent.
- **The queue drops leading newlines instead of lifting them.**
  A delivery has already passed the readiness gate, so the composer is empty by contract and a leading blank line separates nothing ahead of a body that is about to be submitted.
  Dropping is also the defence that needs no per-harness measurement, so `delivery_payload` does it on every backend.
  An insert lands on top of whatever someone was mid-way through typing, which is why the two differ.

### An insert refuses a dialog

An insert writes into whatever the CLI is showing.
When that is an approval or a question, the text does not fill a composer - it *answers the dialog*, and is gone.
`injectText` therefore refuses when the target session is `awaiting` with a sub-reason in `DIALOG_AWAITING_REASONS` (`frontend/src/terminalActions.ts`), which is exactly `PROTECTED_AWAITING_REASONS` in `prompt_queue.py`; the queue has refused this since it existed and nothing stopped the insert paths doing it.
`rate_limit` and `authentication` are waits rather than dialogs and keep their composer, and an `awaiting` with no declared sub-reason is not treated as a dialog - absent evidence is not evidence of one.

- **The refusal has to be visible, so the insert paths wait for it.**
  A prompt rail button, the library modal, and the clipboard picker previously dispatched `mux:terminal-action` and walked away, reporting every insert as done including the ones that never happened.
  They now go through `insertIntoTerminal`, which carries a request id and resolves on the pane's acknowledgement; `insertIntoFocusedSurface` takes an `onRefused` callback because its own return value is synchronous and a refusal would otherwise have nowhere to go.
- **A keystroke paste is not an insert.**
  Ctrl+V, the rail Paste button, the mobile paste handler, and the manual-paste fallback all reach `pasteIntoTerminal` directly, so they get the leading-newline repair but not the refusal: someone pasting with the pane in front of them may legitimately be answering the prompt they can see.
  The refusal is for text pushed from elsewhere in the UI, where the operator may not be looking at the pane at all.

## Unsent composer text

The daemon holds a write log, not a terminal cell grid, and no harness publishes what is in its
input box.
What it does hold is every byte an operator path writes, so the composer's *emptiness* is
derivable from the input side alone: count what was typed, drop the count on the keys that
submit or discard it (`src/swe_mux/composer_input.py`).
The estimate is published as `unsent_input` and is what marks a sidebar row that has something
half-typed in it, from any device (`features/ui.md`).

- **Display evidence only.**
  `delivery_readiness` keeps blocking on its own `input_revision` boundary and never reads this.
  A flag that said "empty" while the composer held a line would turn a refused send into a
  corrupted one, and a false *safe* is the dangerous direction.
- **Every operator write is counted.**
  Text inserted by a voice append, a send-to-agent, or a mobile draft is as unsent as text
  someone typed, so `_note_composer_write` sits on both the WebSocket path and
  `_record_operator_input`, exactly where `input_revision` is advanced.
- **Only the crossing is announced.**
  `composer_input_changed` fires when the composer goes empty ↔ non-empty, never per keystroke,
  and the same two moments are the only `kind: "composer"` entries in the transition ledger.
- **A turn opening empties it, whatever submitted it.**
  A queue delivery, a voice send, and a keystroke are the same bytes to the PTY but only one of
  them passes through the input handler, so the state funnel clears the estimate on any
  transition into `working` (and on `exited`/`crashed`, which have no composer at all).
- **Bracketed paste is content, not a submit**, and its open/closed state is carried between
  writes: a paste large enough to matter is the one most likely to arrive split, and a
  continuation frame carries no opening marker of its own.
- **What discards a composer is a harness fact, not a constant.**
  Ctrl+C is universal. Everything else arrives from `HarnessDescriptor.composer_clear_keys`,
  because the constant this module used to carry was wrong on the harness most people run:
  measured 2026-08-20 against Claude Code v2.1.238 on a four-line draft, `Ctrl+U` killed one
  line and left the other three, and a bare `Esc` did nothing to the draft at all - only a
  double `Esc` cleared it.
  Counting either as a clear reported an empty composer over a standing draft, which is the
  false *safe* named above, in the one estimate a gate can act on.
  The declared sequence is matched against the raw frame rather than the escape-stripped
  remainder, since Claude's is itself an escape sequence.
- **The composer newline key is composed text, and has to be named to be seen as such.**
  `HarnessDescriptor.composer_newline` (ESC+CR on both measured agents) counts as one character.
  It is not a control sequence the escape stripper matches, so its bare CR used to survive the
  strip and classify the whole write as a submit - the same false *empty*, and already live,
  because the rail's Markdown divider and code-fence buttons have always sent exactly these bytes.
- **Known imprecision, in the safe direction.**
  Keystrokes a slash menu consumed can leave the mark standing until the next submit;
  history recall (`↑`) puts text in the composer that this cannot see and is deliberately not
  counted, because inventing a count for bytes nobody sent is worse than missing one.

## Reading the composer back

The count above is all the daemon can offer, so the Action rail's **Copy input** and **Clear
input** read the draft off the client's own cell grid instead (`frontend/src/composerText.ts`).
Reconstructing text from the write log was the alternative and is rejected: it would have to
emulate a line editor, and would still be wrong the moment `↑` puts characters in the composer
that mux never sent. The screen is the truth.

- **One measurement per harness.** `terminalCaretPlacement.ts` already measures each composer
  for caret steering; `ComposerRegion` re-expresses that same measurement as a readable
  rectangle, and the readers for Codex, OMP and pi are their existing caret resolvers - which is
  also what refuses a dialog, a picker, or ordinary output sitting under the cursor.
- **Claude appears there and only there.** It negotiates mouse tracking, so it positions its own
  caret and needs no caret resolver, but its draft is still on screen. Measured against
  v2.1.238 at 100x30: two full-width `─` rules bracket the draft, the first visible row carries
  `❯` plus U+00A0, continuation rows leave those two columns blank, text runs from column 2, and
  the editor wraps at `cols-2`. Its `/` completion list renders above the top rule, outside the
  composer entirely, so unlike pi there is no picker to refuse.
- **Dim cells are dropped**, which removes a placeholder hint without matching its wording -
  every harness paints the hint with SGR 2 (verified in Claude's own output bytes), and nothing
  an operator types arrives dim. It also removes the ghost completion painted after the caret.
- **Soft wraps are inferred.** A row whose content reaches the wrap column is joined to the
  next; anything shorter ends a line. A hard newline landing exactly on the wrap column is read
  as a wrap, which costs one newline rather than any characters, and is the only ambiguity a
  cell grid cannot resolve.
- **Only what is drawn.** A draft taller than its box scrolls inside it and no harness marks
  that on screen - Claude's first visible row still carries `❯`. What comes back may be the tail.
  This is why Clear does not depend on the read: the clear keys go to the CLI either way, and the
  scrape is a best-effort clipboard-history record whose character count is shown to the operator.
- **Three outcomes, never two.** `null` means "this screen is not showing a readable draft";
  an empty string means "read it, and it is empty". Callers must not collapse them.

## API surface

PTY WebSocket frames, typed in `design/interfaces.md`: `claim_input`, `input_owner`,
`input_owner_released`, `input_rejected`, `resize`/`attach_ready` (with `hidden`),
`geometry`, `repaint` (client-requested transcript restatement, `features/sessions.md`),
`output_ack` (xterm parser credit), `client_diagnostic` (durable client repair telemetry).

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
- Unsent-composer estimate (pure): `src/swe_mux/composer_input.py`
- Composer text assembly from the cell grid (pure): `frontend/src/composerText.ts`;
  the per-harness rectangles it reads: `frontend/src/terminalCaretPlacement.ts`
- Ownership/viewport state, geometry fanout: `src/swe_mux/session.py`
- Frame handling, claim decisions, decision log: `src/swe_mux/server.py` (`pty_ws`,
  `_claim_terminal_input`, `_handle_terminal_input`, `_apply_client_viewport`)
- Client ownership model (pure): `frontend/src/inputOwnership.ts`
- Letterbox math and the reveal-adoption rule (pure): `frontend/src/terminalLetterbox.ts`
- Wheel-report pacing to an application-owned viewport (pure): `frontend/src/terminalWheelPacing.ts`
- Viewport pass scheduling and the resize-sending cost charge (pure): `frontend/src/terminalViewport.ts`
- Socket, DOM, take-over strip: `frontend/src/TerminalPane.tsx`
- Mouse-mode tap routing, the per-harness caret resolver registry, and steering math (pure): `frontend/src/terminalCaretPlacement.ts`

## Relates to

- `features/device-presence.md` — supplies LEADING, the device-level tier of the claim
  rules.
- `features/sessions.md` — attach/replay lifecycle these frames ride on.
- `features/ui.md` — when the take-over strip is shown.
