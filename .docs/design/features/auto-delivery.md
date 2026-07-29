# Gated auto-delivery

## What it is

An opt-in, bounded controller that presses "send next" on a session's prompt queue when —
and only when — delivery readiness has been continuously `safe` for a held window. Roadmap
Phase 5. It is the **only** non-human caller of `PromptQueueService.send_next`, and it is
not a second delivery path: ordering, revision checks, readiness, identity, constraints,
and audit all stay in the typed queue operation (`CONTROL_PLANE_ROADMAP.md` §7.1).

Off by default at two independent levels. Nothing here can be armed by an agent, a rule, or
a remote request.

## Key concepts

- **Two opt-ins, both required.** `auto_delivery_enabled` (config, default off) is the
  install's master switch; a per-session opt-in on top of it is what actually authorizes
  sends. Turning the master switch on delivers nothing by itself.
- **The per-session grant is bounded and dies on its own.** It records the
  `agent_run_id` it was made against and carries an expiry (`auto_delivery_session_ttl_minutes`,
  default 60) and a consecutive-send cap (`auto_delivery_max_consecutive`, default 3).
  Standing authorization is what turns a bounded convenience into an unattended actuator, so
  the grant is deliberately not sticky. A **manual** send resets the consecutive count —
  a human at the keyboard is exactly the evidence the cap exists to require.
- **Same live run only.** A replaced run (resume, branch, restart into a new conversation,
  or an in-CLI `/clear`/`/new` — `backends.md`) disables the opt-in rather than inheriting it.
  Consent was for *that* conversation.
- **Stability, not a snapshot.** `delivery_state` must read `safe` continuously for
  `auto_delivery_stable_seconds` (default 8) for the *same* message revision. One safe
  sample is a race; a held window is evidence. Any flap resets the window.
- **Only an armed head, and only an authorized sender.** A draft was never armed and a
  `blocked` item carries an unresolved refusal — both need a human act first. Messages from
  other agents are eligible only if the receiving session also opted in to
  `accept_agent_messages` (`agent-messaging.md`).
- **It can never override.** The controller cannot pass `confirm`: `send_next` rejects a
  confirmation from a non-human initiator (`confirm_requires_user`). Blocked or unknown
  readiness always means "not now", never "anyway".
- **Fail closed and stay stopped.** A *failed* delivery — where the PTY write may or may not
  have landed — disables that session's opt-in and asks the user to verify the terminal. It
  never retries blindly. A *refused* attempt backs the session off
  (`auto_delivery_refusal_backoff_seconds`) instead of spinning the audit log.
- **Emergency disable.** A persisted `paused` flag in SQLite (`queue_auto_policy`), not in
  the config file: it must be instant, survive a restart, and depend on no provider. Quiet
  hours (`auto_delivery_quiet_start`/`_end`, local time) pause automatic sends only —
  manual sends are unaffected.
- **Who pressed send is audited.** `queue_deliveries.initiator` is `user` or `auto`, and the
  `queue_delivery` event carries it.

## Time-based delivery

"Send in 5 minutes" / "send at a time" is a **constraint on the queue item**
(`constraints_json`: `not_before`, `expires_at`; a `delay_seconds` convenience is resolved
to `not_before` at write time, bounded by a 30-day horizon). Never a browser timer (it dies
with the tab) and never a private daemon timer (an unaudited second delivery path).
Consequences:

- Both paths honour it. A manual send before `not_before` is refused as `delivery_not_due`
  and the item keeps its state; "Send now" (`confirm`) is the human override of the clock.
- An expired item is cancelled (`cancel_kind: expired`), never delivered late. The sweep
  runs even when auto-delivery is off — expiry is a promise about *any* delivery path.

## Promotion criteria (Phase 1 shadow readiness)

Quantitative, machine-checked, and visible rather than asserted:

1. **Zero known false-safe deliveries.** Enforced by
   `tests/test_delivery_readiness_promotion.py` across the six fixture classes —
   `approval_required`, `awaiting_user_input`, `rate_limited`, `subagent_activity`,
   `active_operator_input`, `run_replacement` — both directly against the readiness tracker
   and over the golden replay corpus, plus the operator's `report_unsafe` at runtime, which
   resets the proving clock and pauses the feature.
2. **Volume**: at least 50 automatic deliveries (`PROVING_MIN_SENDS`).
3. **Duration**: at least 14 days (`PROVING_MIN_DAYS`) since the capability was first armed
   or since the last unsafe report.

These gate *widening* the capability (defaulting it on, dropping the TTL or the consecutive
cap) — they do not gate the opt-in itself, which is bounded by construction. Current values
are on `GET /api/queue/auto` and in the mailbox's status line.

## UI

- **Queue tab strip**: the per-session toggle, the live bounds (sends left, minutes left,
  quiet hours, why it is off), and the `accept agent messages armed` toggle. The toggle is
  unavailable when the install's master switch is off, with the reason stated.
- **Per-item schedule**: `+5m` / `+15m` / `+1h` presets and `Clear schedule`; a scheduled
  item shows its time in the row.
- **Mailbox overlay** (app menu → Mailbox): pause-all / resume, `report unsafe delivery`,
  and the proving-period counters — reachable in one gesture from desktop or phone.

## API surface

```text
GET  /api/queue/auto                       policy, per-session rows, counters, promotion
POST /api/queue/auto/pause                 {paused}          emergency disable
PUT  /api/queue/auto/sessions/{sid}        {enabled, ttl_minutes, max_sends,
                                            accept_agent_messages}
POST /api/queue/auto/report-unsafe         {note}            operator review input
PATCH /api/queue/messages/{id}             {constraints}     schedule / clear
```

## Configuration

`auto_delivery_enabled`, `auto_delivery_stable_seconds`, `auto_delivery_max_consecutive`,
`auto_delivery_session_ttl_minutes`, `auto_delivery_quiet_start`, `auto_delivery_quiet_end`,
`auto_delivery_refusal_backoff_seconds` (`config.py`, validated with lower bounds — a
zero-length stability window would defeat the gate it exists to be). Runtime state (pause,
per-session opt-ins, counters) lives in SQLite, not config.

## Key files

- `src/swe_mux/auto_delivery.py` — `AutoDeliveryController` (the loop, the gate, quiet
  hours, counters), `promotion_status`.
- `src/swe_mux/prompt_queue.py` — `queue_auto_policy` / `queue_auto_counters` tables,
  constraint enforcement in `send_next`, `normalize_constraints`, `schedule_status`.
- `src/swe_mux/server.py` — the `/api/queue/auto*` handlers and lifecycle wiring.
- `frontend/src/QueuePane.tsx` (strip + schedule), `frontend/src/Mailbox.tsx` (emergency
  controls), `frontend/src/queueApi.ts` (typed client).
- Tests: `tests/test_auto_delivery.py`, `tests/test_delivery_readiness_promotion.py`,
  `tests/test_frontend_phase5_contract.py`.

## Relates to

- `prompt-queue.md` — the queue and the delivery operation this drives.
- `delivery-readiness.md` — the evidence the gate consumes.
- `agent-messaging.md` — the other Phase 5 half: who may put messages in a queue.
- `../development/ROADMAP.md` Phase 5.
