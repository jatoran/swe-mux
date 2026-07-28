# Persistent manual prompt queue

## What it is

Durable, ordered messages a user stages against a target agent run, delivered only by an
explicit user act, surviving daemon and browser restarts without duplicate delivery.
Roadmap Phase 4. The storage model is mailbox-shaped so later senders (the Phase 5
mailbox and agent-to-agent messages, the control-plane queue-draft channel of
`CONTROL_PLANE_ROADMAP.md` §13) are added as new callers of the same typed operations, not
as new delivery paths.

Nothing in this feature delivers on a timer or autonomously. Time-based and auto-delivery
are Phase 5, gated behind the shadow-readiness promotion criteria.

## Key concepts

- **The daemon owns the queue and every safety predicate (CP §7.1).** Ordering, revision
  checks, readiness evaluation, target-identity checks, and the audit trail live in
  `PromptQueueService`; the browser (and later MCP/CLI/mailbox callers) are thin clients of
  `POST /api/queue/send-next` and its sibling routes.
- **Stable targeting, bind-on-first-run.** A message keys to its target session *and* the
  `agent_run_id` it was staged against. An item staged against a still-starting session
  binds to the first run that session gets and is never re-bound: an ended session or a
  replaced run **strands** the item (visible, exportable, retarget-by-explicit-act only) —
  the daemon never silently retargets a successor conversation.
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
- **Every delivery is a user act.** `send-next` claims the head atomically (state,
  revision, head-of-line in one transaction), then re-checks target liveness, run identity,
  and `delivery_readiness` immediately before writing. Blocked/unknown readiness requires
  an explicit per-send `confirm`; the protections that can never be overridden are
  approval/Q&A (`approval_required`, `awaiting_user_input`, the `awaiting` sub-reasons
  `approval|question|elicitation` — text typed at an approval prompt can *answer* it) and
  target identity/liveness (those strand instead). Overridable-with-confirm mirrors what
  the send-to-agent dialog allowed before the queue owned delivery: working target,
  operator recently typed, alternate screen, unknown evidence.
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
- **Provenance-rich sender model, day one.** `sender_kind` (`user | queue_draft`),
  `sender_id`, `origin_json` (originating rule/observer id, source Tier 0
  facts/fingerprints, annotation snapshot), `payload_json` (a typed action payload
  re-validated at send time), and `constraints_json` (Phase 5 delivery constraints) are
  persisted now so the queue-draft channel needs no schema migration. The HTTP create
  route pins `sender_kind="user"`; non-human senders are in-process callers only and can
  create inert drafts exclusively — arming is a human act.
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

- **Queue workspace tab** per target session (`queue` pane leaf, id `queue:<session_id>`),
  opened from the pane header's `queue[:N]` chip. Ordinary mixed-view tab: split, move,
  mobile projection. Shows the ordered queue with the head marked `next`, per-state
  actions (arm/unarm, edit inline, move, cancel/skip, copy, send now / send anyway,
  retarget for stranded), sent items crossed out in place, and a composer to stage
  drafts/armed messages. Live-updates off `mux:queue-changed` (re-dispatched
  `queue_updated`/`queue_delivery` events).
- **The send-to-agent dialog is a queue sender.** "Add to queue" stages armed without
  delivering. "Send" stages armed and immediately asks the queue to deliver: an occupied
  queue answers `head_of_line_blocked` and the dialog closes into the Queue tab (the
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
  "queued but blocked" and points at the Queue tab.
- **New-session seeds** travel as `seed_text` on the spawn request; the daemon inlines
  short bodies into argv and stages long ones into `.swe-mux/seeds/` with a reader prompt
  (`stage_seed_argv`), removing the former 20,000-character client-side ceiling.

## Boundaries

- Targets are live Claude/Codex sessions only; shells are never offered (a paste would
  execute) and the daemon enforces it (`not_agent_target`).
- No timers, no auto-delivery, no agent-initiated sends. Phase 5 adds those as gated
  callers over these same operations.
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
- `frontend/src/queueApi.ts` — typed client + refusal mapping; `frontend/src/QueuePane.tsx`
  — the Queue tab; `frontend/src/SendToAgentPicker.tsx` — queue sender + confirm flow;
  `frontend/src/App.tsx` — `deliverToAgent`, `openQueueForSession`, pane chip, event
  re-dispatch; `frontend/src/layout.ts` — `queue` leaf kind.
- Tests: `tests/test_prompt_queue.py`, `frontend/test/queueApi.test.ts`.

## Relates to

- `delivery-readiness.md` — the readiness evidence `send-next` consumes and the shared
  operator-input accounting the delivery writes go through.
- `../development/ROADMAP.md` Phase 4 (completion record), Phase 5 (what wraps these
  operations next).
- `../development/CONTROL_PLANE_ROADMAP.md` §7.1 (daemon owns the queue), §13 (the
  queue-draft channel this sender model carries).
