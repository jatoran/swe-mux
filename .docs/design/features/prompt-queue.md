# Persistent manual prompt queue

## What it is

Durable, ordered messages staged against a target agent run, delivered through one typed
operation, surviving daemon and browser restarts without duplicate delivery. Roadmap
Phase 4. The storage model is mailbox-shaped, so Phase 5's senders (agent messages, remote
devices) and the control-plane queue-draft channel (`CONTROL_PLANE_ROADMAP.md` §13) are new
*callers* of the same typed operations, not new delivery paths.

Phase 4 delivers only on an explicit user act. Phase 5 adds two bounded callers on top,
documented separately: `auto-delivery.md` (who else may press send, and under what gate)
and `agent-messaging.md` (who else may put a message in a queue). The auto-delivery install
master is off by default; once enabled, each live Claude/Codex conversation gets a bounded
default-on grant that can be turned off for that conversation. Agent-authored queueing stays
separately opt-in.

## Key concepts

- **The daemon owns the queue and every safety predicate (CP §7.1).** Ordering, revision
  checks, readiness evaluation, target-identity checks, and the audit trail live in
  `PromptQueueService`; the browser (and later MCP/CLI/mailbox callers) are thin clients of
  `POST /api/queue/send-next` and its sibling routes.
- **Stable targeting, bind-on-first-run.** A message keys to its target session *and* the
  `agent_run_id` it was staged against. An item staged against a still-starting session
  binds to the first run that session gets and is never re-bound: an ended session or a
  replaced run **strands** the item (visible, exportable, retarget-by-explicit-act only) —
  the daemon never silently retargets a successor conversation. An in-CLI `/clear` or `/new`
  counts: it retires the run and opens a successor (`agent_conversation_rolled`,
  `backends.md`), which strands pending items with "target agent conversation was replaced".
  Before the run boundary existed this was the one way to defeat bind-on-first-run — the
  session and its run id both survived, so a message written for one conversation delivered
  into the wiped one.
- **Explicit states.** `draft | armed | blocked | delivering | sent | failed | cancelled |
  stranded | deleted`. `deleted` is an internal, content-free tombstone excluded from every
  queue, fleet, summary, and export view. Transitions are transactional (conditional UPDATEs on one store worker) and
  idempotent. `blocked` records the last refused delivery's reasons; an edit or re-arm
  clears them. `delivering` is transient but persisted so a daemon death mid-send is
  distinguishable: startup reconcile flips it to `failed` ("verify the terminal") because
  whether the PTY write landed is unknowable, and guessing either way risks a duplicate or
  lost delivery. Cancel records `cancel_kind: cancelled | skipped`.
- **Strict head-of-line.** Any later item may be armed in advance, but only the earliest
  pending (draft/armed/blocked/delivering) item can deliver; everything behind it waits
  until the head is sent, cancelled, explicitly skipped, or deleted.
- **Delete is distinct from cancel/skip.** `DELETE /api/queue/messages/{id}` accepts every
  state except `delivering`, erases the body and action payloads, hides the item immediately,
  and releases head-of-line order. A `delivering` item returns `409 delivery_in_progress`
  because its PTY write may already be underway. The content-free tombstone retains sender
  correlation until normal retention so an agent or automation retry cannot resurrect the item.
- **The body shown is the body delivered.** No hidden rendered variant exists. Edits
  increment `revision`; `send-next` validates the exact revision the user last saw and
  refuses (`revision_conflict`) otherwise. Sent/delivering items are immutable.
  For an agent-authored `mux.notify`, the stored body itself includes the visible mux provenance envelope before the caller's original text.
- **Every delivery is an audited act with a named initiator.** `send-next` claims the head atomically (state,
  revision, head-of-line in one transaction), then re-checks target liveness, run identity,
  and `delivery_readiness` immediately before writing. Blocked/unknown readiness requires
  an explicit per-send `confirm`; the protections that can never be overridden are
  approval/Q&A (`approval_required`, `awaiting_user_input`, the `awaiting` sub-reasons
  `approval|question|elicitation` — text typed at an approval prompt can *answer* it) and
  target identity/liveness (those strand instead). Overridable-with-confirm mirrors what
  the send-to-agent dialog allowed before the queue owned delivery: working target,
  operator recently typed, a screen that is not the agent's own, unknown evidence.
  `initiator` (`user` |
  `auto`) is recorded on every attempt, and a non-human initiator may never pass `confirm`
  (`confirm_requires_user`) — an override is a human act by construction.
- **Delivery constraints belong to the item.** `constraints_json` carries `not_before` and
  `expires_at` (Phase 5 scheduling), bounded by a 30-day horizon. Both paths honour them: an
  early manual send is refused as `delivery_not_due` and keeps its state ("Send now"
  overrides the clock), and an expired item is cancelled (`cancel_kind: expired`) rather
  than delivered late. `delivery` (`when_idle` | `now`) is on the item for the same reason:
  the manual and automatic paths must not be able to disagree about what an item asked for.
  A `now` item is authorized from the readiness tracker's separate `interject_state` rather
  than from `delivery_state`, which is a second predicate and not an override - the
  non-overridable protections still run first, a non-human initiator still cannot `confirm`,
  and a refusal reports the reasons that stopped *it* rather than `root_agent_working`, which
  is the one thing it was allowed to step over. The `when_idle` default is never persisted, so
  an item without the key means what every item meant before the mode existed
  (`auto-delivery.md`, `delivery-readiness.md`, `agent-messaging.md`).
  A *human* sender asks for `now` too (2026-08-25): the draft editor's **Mid-turn**
  checkbox (off by default - interrupting is a per-message choice, not a mode the pane
  drifts into) carries the constraint into the create, a `mid-turn` mark on the row says so,
  and the tray flips a pending item between the two modes when no editor is open on it. The three sender-side gates
  in `agent-messaging.md` (install switch, Project `interject_grant`, receiver opt-out)
  bound *agent* senders at staging time and are not re-imposed on a human staging into
  their own fleet; the delivery-time `interject_state` predicate and the receiver policy on
  the automatic path still decide every actual write. A constraints PATCH replaces the
  whole object, so the browser merges over the item's existing constraints - scheduling a
  message must not silently drop its delivery mode, nor the mode its schedule.
- **Delivery bytes mirror the browser paste path.** Bracketed paste with newlines as CR,
  a 180 ms settle, then a separate `\r` — both writes through the shared operator-input
  accounting helper (`source="queue"`, `input_owner=False`), so `input_revision` /
  `last_input_event_ts` / `terminal_input` evidence stays whole
  (`delivery-readiness.md`).
- **No-duplicate-delivery.** The optional `idempotency_key` is unique across delivery
  attempts; a repeat replays the recorded outcome without touching the PTY — across HTTP
  retries and daemon restarts.
- **Audit without duplication.** `queue_deliveries` records each attempt's revision,
  target identity, readiness state + reasons, confirmation flag, outcome, byte count, and
  error. Prompt text lives only in visible `queue_messages`; delete blanks it while retaining
  content-free correlation and delivery-audit linkage. `queue_updated` / `queue_delivery`
  events carry ids and counts only.
- **Provenance-rich sender model.** `sender_kind` (`user | remote_user | agent | rule |
  queue_draft`), `sender_id`/`sender_label`, `origin_session_id`, `correlation_id` (per
  sender: a retry returns the original message, never a duplicate - enforced by
  `create_message`'s SELECT-before-INSERT, which compares `IFNULL(sender_id,'')`, and *not*
  by the partial unique index, which NULL-sender rows escape because SQLite treats NULLs in a
  UNIQUE index as distinct), `thread_id` (the
  relay exchange a message continues; daemon-assigned, and distinct from `correlation_id`
  for exactly the reason that key is per-sender), `chain_depth`,
  `origin_json` (relay path, originating rule/observer id, source Tier 0
  facts/fingerprints), `payload_json` (a typed action payload re-validated at send time),
  and `constraints_json`. **The kind is derived, never claimed**: the HTTP route reads it
  from the transport (loopback → `user`, remote device → `remote_user`), the MCP tools from
  the caller's token. Observer/rule senders create inert drafts only; an `agent` sender may
  arrive armed only because the *receiving* session granted it (`agent-messaging.md`).
  A scheduled run stages its follow-up messages as `rule` senders against the session it just
  started, with any per-message delay written as an ordinary `not_before` constraint rather
  than a timer that feature owns (`scheduled-runs.md`).
  A land queue handback is staged the same way: a conflict, a failed verification, or an
  expired hold returns to the requesting session as a `rule` sender carrying a fixed
  template, keyed by the land request id as its `correlation_id` so a repeat dedupes on
  the existing uniqueness index (`land-queue.md`). It is a message rather than an action
  because the pipeline has no way to resolve a conflict and no business trying.
  A **settle-watch notice** is the third caller of that same shape, and the one addressed to
  the session that asked for it: `watch_session` arms a read-only watch on a sibling, and when
  that sibling settles, ends, or the caller's timeout elapses, one fixed template is staged
  against the *watcher* as a `rule` sender keyed by the watch id (`mux-mcp.md`). It is armed
  the same way the handback is, and for the same reason: `solicited_by` names the watch, which
  is the request the receiver itself made, so the arming is the receiver's own consent rather
  than the sender's claim. The consequence is stated in the arming result rather than
  discovered later - staged armed, and armed is not delivered.
  A scheduled **resume** stages its opening prompt the same way rather than as an argv seed:
  the resumed pane's argv is already `--resume <id>`, and whether a positional prompt may follow
  that is per-harness luck rather than a contract.
  The consequence is the ordinary one and is not special-cased - a `rule` sender is never
  self-arming, so that prompt is delivered automatically only where the conversation has an
  auto-delivery grant and otherwise waits for a human.
  Bind-on-first-run is what makes that safe: each message keys to the new session *and* the
  first agent run it gets, so a message written for tonight's scheduled conversation can never
  land in a different one.
- **Stranding triggers.** `session_exited` / `session_crashed` / `backend_demoted` events;
  send-time identity failures; and the startup reconcile after supervisor adoption (target
  missing, ended, or running a different bound run). Stranded items offer copy, explicit
  retarget (to a live agent session, as a draft at the new queue's tail, with the previous
  binding recorded in `retargeted_from`), or cancel.
- **Retention and export.** Terminal-state items (sent/failed/cancelled/stranded), deleted
  tombstones, and their audit rows age out on `prompt_queue_retention_days` (default 90, edited
  in Settings → Prompt queue → Queue history); pending items
  never do. `GET /api/queue/export` snapshots one queue; credential-shaped bodies
  (`looks_like_secret`) are redacted unless the user opts out.

## UI

- **The Queue tab of the utility drawer** (`QueuePane`) is session-scoped and follows the focused session like Clipboard, Commands, and Prompts.
  The placement keeps the target terminal visible while the operator decides whether it is safe to interrupt that agent.
  The pane header's `queue[:N]` chip focuses its named session before opening Queue, while `queue.open` and the rail open the focused session's queue.
  Queue has no application-wide or Project-wide mode.
  It live-updates from `mux:queue-changed`, re-dispatched from `queue_updated` and `queue_delivery` events.
- **The Queue tab states whether its target will take a message, and why not, continuously.**
  A strip under the header reads `deliverable`, `not deliverable — <reason>`, or `readiness unknown — <reason>`, and when it is not safe it also says what would clear it.
  It is permanently on screen rather than behind a disclosure because that is the question the pane is opened to decide, and the alternative is learning the answer by pressing Send and reading back the name of the check that fired.
  The vocabulary lives once, in `deliveryReadiness.ts`, and every surface that prints a daemon refusal reason goes through it - the queue's own refusal line, the send-to-agent dialog, the prompt library's staging note, and the per-message blocked marks all used to `join(', ')` the raw codes independently.
  An unmapped code passes through as itself: the vocabulary is the daemon's and it grows, and a reader shown a code they can search for is better served than one shown nothing.
  A frontend test reads the reason list out of `delivery_readiness.py`, so a new code lands as a failing test rather than as a raw identifier in front of a user.
- **The strip is advisory and never disables the Send button.**
  The daemon re-evaluates at send and its verdict is the only one that acts; the browser's copy can be stale, and a stale advisory that removed the operator's only override would be a false block with no way out - strictly worse than a wrong label, and the same failure mode the four corrections in `delivery-readiness.md` were about.
  So the two-step is unchanged: the first press asks, the daemon refuses with reasons measured at that instant, and `Send anyway` confirms *that* refusal.
  What the advisory adds is that a protection which cannot be overridden is named **before** the press, rather than being indistinguishable from an ordinary block until the button behaves differently.
  `protected_reasons` in `prompt_queue.py` is the single implementation of that classification, shared by `send_next` and by the display payload.
- **Readiness reaches the tab three ways, and the ordering between them is by `observed_at`.**
  `GET /api/sessions` carries it on every session row; `GET /api/queue/messages` carries the target's own reading, so opening the tab never paints a verdict it corrects a moment later; and the daemon's readiness watcher pushes changes in between.
  Neither of the first two is reliably the newer one, so the freshest stamp wins and an unstamped payload loses by construction.
  A reading older than a few seconds renders its age, which is not decoration: `sessionSnapshots.ts` preserves the last known readiness across raw PTY snapshots, so without a stamp a minute-old verdict renders identically to a current one.
- **The composer estimate narrates a block and never clears one.**
  `terminal_input_after_completion` is the reason an operator is most likely to read as a bug: the composer really is empty, the session really does read idle, and the queue still refuses, because the guard counts keystrokes and backspaces advance the count too.
  The strip is allowed to say "nothing is sitting in the composer now" beside it, using `unsent_input`, precisely because that is the fact making it look wrong - and it is allowed to do nothing else.
  An estimate that concluded "empty" must never be an input to the verdict (`delivery-readiness.md`, and `composer_input.py`).
- **The focused Queue draft editor is a named Conversation text sink.**
  Voice Send fills the open editor at its caret but never stages, arms, delivers, or presses Enter; those remain explicit Queue acts.
  With no editor open the handle reports itself detached, so a dictation falls back to the focused terminal rather than being swallowed by a pane with nowhere to put it.
- **The fleet queue is a modal overlay** (`FleetQueue`) over the same message store, not a second drawer tab.
  It partitions rows by explicit authorship (`agents + automation | human | all authors`, opening on non-human) and filters server-side by Project or target session.
  It is a modal because it has no send button: the Queue tab is docked so the target terminal stays visible while the operator decides to interrupt, and a view that decides nothing needs no terminal beside it.
  It is reached from the app menu, `queue.fleet`, the Project menu, and the Queue tab's `fleet` control, which carries the fleet-wide pending count.
  It reports install-wide auto-delivery state and owns none of it.
  It also projects pending and decided `request_spawn` records as targetless approval rows.
  Approve and dismiss remain explicit human acts; Fleet Queue does not gain general spawn authority and message rows still have no send control.
- **There is one writing surface and it is a queue row** (2026-08-31).
  The pane used to carry a permanent composer footer *as well as* a row editor: two text fields with different rules - one that staged new items and autosaved nothing, one that edited existing items behind an explicit Save - sharing a 300 px column.
  Composing is now the same act as editing.
  `+ New message` appends a blank draft, opens it, and focuses it; `⋯`'s Edit became a pencil on the row itself, and a double-click on a pending body opens the same editor (double, not single, because the body is also the only place the text can be selected from).
- **Drafts autosave, and an empty one is never written.**
  `queueDraftSaver.ts` owns a debounced (500 ms) write per open editor, deliberately outside the component: the Queue tab lives in the right-edge drawer, and swiping the drawer shut - or moving focus to another session, which retargets the whole pane - unmounts `QueuePane` mid-sentence, so a timer held in component state is cancelled by exactly the gesture that most often interrupts someone typing.
  Blur, closing the editor, unmount, retarget, `pagehide` and `visibilitychange` all flush; the report behind this was "sometimes u might be typing and swipe away the right sidebar without thinking and then u lose what u were typing".
  The first non-empty body is a `POST` that creates the item, every later one a `PATCH`; a body that is empty or whitespace is never sent, because the daemon refuses it (`invalid_body`) - which is also what makes `+` cheap, since an abandoned draft leaves no row.
  A `revision_conflict` re-anchors on the revision the daemon reported and retries once.
  Arming is deliberately not part of the create: an item must exist before it can be armed, so `Arm` (and `Ctrl+Enter`) flush first and then `PATCH {armed}` - which is also what stops an arm authorizing the *previous* body while the newest keystrokes sit in the debounce.
  Autosave says `saving…`/`saved`/`unsaved` beside the field, because a field that saves silently is precisely the field nobody trusts with a half-written message.
- **The editor keeps every control the resting row has.**
  Save/Cancel used to replace the whole action strip, so staging an armed mid-turn message meant Save, re-find the row, arm, open the tray, set the mode.
  The open editor carries the `Mid-turn` checkbox, `Arm`/`Unarm`, `Send now` when it is the head, the `⋯` tray, delete, and `Done` (`Esc`); the mid-turn choice made before the item exists rides the create rather than being lost.
  A `+` draft keeps its own row at the tail of the list for as long as it is open, *including after autosave has created it*: moving the field into the created row would replace the DOM node under the caret half a second after the person started typing.
- **Built for the drawer's 300 px minimum as well as its viewport-derived maximum.**
  Rows carry `Send now` (head) and the arm toggle inline on the left, then a right-aligned strip of marks - edit, copy, `⋯`, delete - which wraps as one group rather than control by control, because letting it wrap individually left a lone red bin on a line of its own.
  Only the two acts whose glyphs nobody has to learn became marks; arm, the delivery mode and the schedule presets keep their words, because each of those is a sentence.
  Move, cancel/skip, the delivery mode and the schedule presets live behind the per-row `⋯` that opens a tray under the row rather than a floating menu.
  The draft field is about four rows tall before it is typed into and grows to its content up to `40vh`.
  Terminal-state items (sent/failed/cancelled) collapse behind a `N delivered or closed` disclosure instead of rendering crossed out in place.
  The auto-delivery controls collapse to a one-line `auto: …` status with a disclosure.
- **Delete is drawn once and implemented once** (changed 2026-08-31).
  It used to be drawn twice - a `×` end-cap inline and a worded copy in the tray - which put two copies of a destructive control on screen together the moment the tray was open.
  The inline copy is the one that survives, now a bin rather than a mark that also means "close", because it is the one reachable without opening anything.
  Same arm-then-confirm (one click marks, the second deletes) through one `deleteConfirmId`, the same busy guard, and the same absence while the item is `delivering`, which is the one state the daemon will not accept a delete in.
  A draft autosave never created is a plain local discard instead: there is nothing to confirm about text no daemon has.
  Delete is also available in the fleet queue.
- **Opening the Queue to read it is not a request to write in it.**
  A deliberate open starts a draft only where a physical keyboard is already present (`hasSoftKeyboard()`, not the mobile breakpoint - a narrowed desktop window has a real keyboard and a landscape tablet does not).
  On a phone, focusing a field is a layout change rather than a convenience: the on-screen keyboard rises over most of the drawer, so the tab arrives with the list it was opened to show already covered.
  Underneath that, the two *reasons* the tab opens are now distinguished at the caller.
  A queue chip, the palette command, or a keybinding is someone about to write, and earns the caret; a send that came back `queued_behind` or `not_due` opens the same tab to say where an already-written message went, and earns nothing.
  Conflating them is what made the caret appear precisely when something was already queued.
- **The `queue:<session_id>` pane leaf survives as an explicit pop-out** (the `↗` in the
  panel header) for wide review or two queues side by side, and is what a persisted layout
  holding one resolves to. It renders the same component with its target pinned instead of
  following focus. Nothing creates one implicitly any more: a Queue tab per session
  inspected, each competing with the terminal for pane space, is why the queue moved into
  the drawer.
- **The send-to-agent dialog is a queue sender.** "Add to queue" stages armed without
  delivering. "Send" stages armed and immediately asks the queue to deliver: an occupied
  queue answers `head_of_line_blocked` and the dialog closes into the Queue panel (the
  message waits in the one audited place); a not-safe target keeps the dialog open with
  the daemon's reasons and flips the button to "Send anyway" (`confirm: true`, delivering
  the *same* staged message — edited text is written through a revision-checked edit
  first). A protected refusal is shown as non-overridable; the message stays queued.
  The dialog's readiness banner is advisory — the queue operation is the single place a
  not-safe target is actually refused or overridden.
- **"Press Enter after inserting" off** remains a plain composer fill over
  `POST /sessions/{id}/input`: it never submits, so it is not a delivery and does not
  enter the queue.
- **Sender coverage.** Continuity note/Markdown views (selection or document), plain-text
  file editors (whole document), and the Files tree context menu ("Send to an agent
  session", same fetch/gating as the copy action) all open the same dialog. The prompt
  library's send flow routes through the queue the same way; a refusal there reports
  "queued but blocked" and points at the Queue tab beside it in the same drawer.
- **New-session seeds** travel as `seed_text` on the spawn request; the daemon inlines
  short bodies into argv and stages long ones into `.swe-mux/seeds/` with a reader prompt
  (`stage_seed_argv`), removing the former 20,000-character client-side ceiling.
  Either way the agent runs the seed — it is submitted by construction.
  Text that must open a session *unsent* travels as `stage_text` instead: the spawn
  handler waits for readiness and writes the queue's bracketed-paste bytes with no
  carriage return (`_stage_spawn_text`, `interfaces.md`), through
  `_record_operator_input` so the parked text counts as partial input for
  delivery readiness.

## Boundaries

- Targets are live Claude/Codex sessions only; shells are never offered (a paste would
  execute) and the daemon enforces it (`not_agent_target`).
- Delivery has exactly one implementation. Phase 5's schedule constraints, auto-delivery
  controller, and agent messages are callers and gates over these operations — nothing
  writes to a target PTY except `send_next`.
- The queue holds messages *toward* sessions; it is not a transcript, a conversation
  archive, or a second history store.

## Delivery mechanics

The body is written wrapped in bracketed paste, then the submit is a separate
write after a settle.

- **The settle scales with the payload.** A CLI turns a large paste into a
  placeholder chip and is busy building one, so a fixed delay sized for a spoken
  sentence lands the submit mid-consumption and the keystroke is swallowed. The
  body then sits in the composer, unsent, while the queue reports success
  (observed live 2026-08-13: two relay messages parked as
  `[Pasted Content 2784 chars][Pasted Content 4230 chars]` in a codex composer).
  The settle is bounded so a huge body cannot stall delivery.
- **A large paste gets one more submit if nothing reacted.** An extra carriage
  return on an empty composer is a no-op, while a swallowed one loses the
  message outright; the costs are asymmetric. Bodies under the large-paste
  threshold never had the problem and get exactly one submit.
- **Reaction, not state, is the confirmation signal.** Any PTY byte after the
  submit is the evidence, because a consumed submit redraws immediately. Session
  state is derived from transcripts and hooks that lag seconds behind a
  keystroke, so it would report healthy deliveries as unconfirmed.
- **An unconfirmed submit is reported, not hidden.** The write happened either
  way, so the audit outcome stays `sent`; `submit_confirmed` on the
  `queue_delivery` event and the send result carry the difference, with a log
  line naming the message and target. Silence here is what made a lost relay
  message look delivered.

## Key files

- `src/swe_mux/prompt_queue.py` — `PromptQueueStore` (SQLite, single-worker,
  per-store schema version `prompt_queue`), `PromptQueueService` (typed operations,
  event-driven stranding, startup reconcile), `stage_seed_argv`.
- `src/swe_mux/server.py` — thin `queue_*` handlers, `QueueError` → typed JSON in
  `error_middleware`, service wiring + `_record_operator_input(source="queue")` injection,
  retention loop, `seed_text`/`stage_text` handling in `_spawn_from_body` +
  `_stage_spawn_text`.
- `src/swe_mux/spawn_contract.py` — `SpawnRequest.seed_text` / `SpawnRequest.stage_text`
  (mutually exclusive).
- `frontend/src/queueApi.ts` - typed session-queue and fleet-queue clients plus refusal mapping.
- `frontend/src/QueuePane.tsx` - session-scoped Queue in drawer-following and pinned-pop-out renderings, the install-wide auto-delivery brakes, and the control that opens the fleet queue.
- `frontend/src/FleetQueue.tsx` - the fleet-wide modal: authorship and target filters, provenance rows, revocation.
- `frontend/src/drawerTabs.ts` + `railIcons.tsx` - the `queue` drawer tab and its mark.
- `frontend/src/UtilityDrawer.tsx` - drawer rendering and the Queue-to-fleet-queue handoff.
- `frontend/src/SendToAgentPicker.tsx` - queue sender and confirm flow.
- `frontend/src/App.tsx` - `deliverToAgent`, `openQueueForSession` versus `openQueueTab`, `openFleetQueue`, `toggleAutoPaused`, pane chip, fleet pending total, and event re-dispatch.
- `frontend/src/layout.ts` - `queue` leaf kind.
- Tests: `tests/test_prompt_queue.py`, `frontend/test/queueApi.test.ts`.

## Relates to

- `auto-delivery.md` — the Phase 5 gate that may press send, and item scheduling.
- `agent-messaging.md` — the Phase 5 senders (agent, remote device) and the mailbox view.
- `delivery-readiness.md` — the readiness evidence `send-next` consumes and the shared
  operator-input accounting the delivery writes go through.
- `../development/ROADMAP.md` Phase 4 (completion record), Phase 5 (what wraps these
  operations next).
- `../development/CONTROL_PLANE_ROADMAP.md` §7.1 (daemon owns the queue), §13 (the
  queue-draft channel this sender model carries).
