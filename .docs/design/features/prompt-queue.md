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
  stranded`. Transitions are transactional (conditional UPDATEs on one store worker) and
  idempotent. `blocked` records the last refused delivery's reasons; an edit or re-arm
  clears them. `delivering` is transient but persisted so a daemon death mid-send is
  distinguishable: startup reconcile flips it to `failed` ("verify the terminal") because
  whether the PTY write landed is unknowable, and guessing either way risks a duplicate or
  lost delivery. Cancel records `cancel_kind: cancelled | skipped`.
- **Strict head-of-line.** Any later item may be armed in advance, but only the earliest
  pending (draft/armed/blocked/delivering) item can deliver; everything behind it waits
  until the head is sent, cancelled, or explicitly skipped.
- **The body shown is the body delivered.** No hidden rendered variant exists. Edits
  increment `revision`; `send-next` validates the exact revision the user last saw and
  refuses (`revision_conflict`) otherwise. Sent/delivering items are immutable.
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
  than delivered late.
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
  error. Prompt text lives only in `queue_messages`; `queue_updated` / `queue_delivery`
  events carry ids and counts only.
- **Provenance-rich sender model.** `sender_kind` (`user | remote_user | agent | rule |
  queue_draft`), `sender_id`/`sender_label`, `origin_session_id`, `correlation_id` (unique
  per sender: a retry returns the original message, never a duplicate), `chain_depth`,
  `origin_json` (relay path, originating rule/observer id, source Tier 0
  facts/fingerprints), `payload_json` (a typed action payload re-validated at send time),
  and `constraints_json`. **The kind is derived, never claimed**: the HTTP route reads it
  from the transport (loopback → `user`, remote device → `remote_user`), the MCP tools from
  the caller's token. Observer/rule senders create inert drafts only; an `agent` sender may
  arrive armed only because the *receiving* session granted it (`agent-messaging.md`).
- **Stranding triggers.** `session_exited` / `session_crashed` / `backend_demoted` events;
  send-time identity failures; and the startup reconcile after supervisor adoption (target
  missing, ended, or running a different bound run). Stranded items offer copy, explicit
  retarget (to a live agent session, as a draft at the new queue's tail, with the previous
  binding recorded in `retargeted_from`), or cancel.
- **Retention and export.** Terminal-state items (sent/failed/cancelled/stranded) and
  their audit rows age out on `prompt_queue_retention_days` (default 90); pending items
  never do. `GET /api/queue/export` snapshots one queue; credential-shaped bodies
  (`looks_like_secret`) are redacted unless the user opts out.

## UI

- **The Queue tab of the utility drawer** (`QueuePane`) is session-scoped and follows the focused session like Clipboard, Commands, and Prompts.
  The placement keeps the target terminal visible while the operator decides whether it is safe to interrupt that agent.
  The pane header's `queue[:N]` chip focuses its named session before opening Queue, while `queue.open` and the rail open the focused session's queue.
  Queue has no application-wide or Project-wide mode.
  It live-updates from `mux:queue-changed`, re-dispatched from `queue_updated` and `queue_delivery` events.
- **The fleet queue is a modal overlay** (`FleetQueue`) over the same message store, not a second drawer tab.
  It partitions rows by explicit authorship (`agents + automation | human | all authors`, opening on non-human) and filters server-side by Project or target session.
  It is a modal because it has no send button: the Queue tab is docked so the target terminal stays visible while the operator decides to interrupt, and a view that decides nothing needs no terminal beside it.
  It is reached from the app menu, `queue.fleet`, the Project menu, and the Queue tab's `fleet` control, which carries the fleet-wide pending count.
  It reports install-wide auto-delivery state and owns none of it.
- **Built for the drawer's 300 px minimum as well as its viewport-derived maximum.** Rows carry only `Send now` (head) and the arm toggle
  inline; edit, move, cancel/skip, the schedule presets and copy live behind a per-row `⋯`
  that opens a tray under the row rather than a floating menu. Terminal-state items
  (sent/failed/cancelled) collapse behind a `N delivered or closed` disclosure instead of
  rendering crossed out in place. The auto-delivery controls collapse to a one-line
  `auto: …` status with a disclosure. `Ctrl+Enter` in the composer stages armed.
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

## Boundaries

- Targets are live Claude/Codex sessions only; shells are never offered (a paste would
  execute) and the daemon enforces it (`not_agent_target`).
- Delivery has exactly one implementation. Phase 5's schedule constraints, auto-delivery
  controller, and agent messages are callers and gates over these operations — nothing
  writes to a target PTY except `send_next`.
- The queue holds messages *toward* sessions; it is not a transcript, a conversation
  archive, or a second history store.

## Key files

- `src/swe_mux/prompt_queue.py` — `PromptQueueStore` (SQLite, single-worker,
  per-store schema version `prompt_queue`), `PromptQueueService` (typed operations,
  event-driven stranding, startup reconcile), `stage_seed_argv`.
- `src/swe_mux/server.py` — thin `queue_*` handlers, `QueueError` → typed JSON in
  `error_middleware`, service wiring + `_record_operator_input(source="queue")` injection,
  retention loop, `seed_text` handling in `_spawn_from_body`.
- `src/swe_mux/spawn_contract.py` — `SpawnRequest.seed_text`.
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
