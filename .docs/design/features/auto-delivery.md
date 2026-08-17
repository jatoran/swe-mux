# Gated auto-delivery

## What it is

A bounded controller that presses "send next" on a session's prompt queue when — and only
when — delivery readiness has been continuously `safe` for a held window. Roadmap
Phase 5. It is the **only** non-human caller of `PromptQueueService.send_next`, and it is
not a second delivery path: ordering, revision checks, readiness, identity, constraints,
and audit all stay in the typed queue operation (`CONTROL_PLANE_ROADMAP.md` §7.1).

The install-wide master switch is off by default. Once an operator enables it, every live
Claude/Codex conversation receives a bounded default-on grant. Nothing here can arm a queue
message on behalf of the user; a per-conversation opt-out remains available.

## Key concepts

- **One master authorization, bounded per-conversation grants.** `auto_delivery_enabled`
  (config, default off) is the install's master switch. When it is on, the daemon materializes
  an enabled policy for each live Claude/Codex `agent_run_id`; no extra toggle is required.
  Turning a conversation off is sticky for that run.
- **The per-conversation grant is bounded and dies on its own.** It records the
  `agent_run_id` it was made against and carries a consecutive-send cap
  (`auto_delivery_max_consecutive`, default 3) and a lapse window
  (`auto_delivery_session_ttl_minutes`, default 60).
  Standing authorization is what turns a bounded convenience into an unattended actuator, so
  the lapse and the send cap still stop it. A **manual** send resets the consecutive count —
  a human at the keyboard is exactly the evidence the cap exists to require.
- **The grant is bounded by idleness, not by the conversation's age.** The window is measured
  from the session's own last activity, so a conversation in use keeps its grant and one
  nobody has touched for the window loses it. Measuring it from the grant's creation instead
  meant every session older than the window silently lost auto-delivery while being actively
  worked in: observed live 2026-08-13 with the whole fleet reading
  `disabled_reason: grant expired` at `sends_used: 0`, so an agent-authored message arrived
  `armed` and then waited for a human indefinitely.
- **A lapse is recoverable; a decision is not.** Lapsing records only that time passed, so the
  conversation default restores it once the session is in use again, without touching a
  separate `accept_agent_messages` choice made during that run. Every other disabled state —
  an explicit opt-out, an exhausted consecutive-send budget, an ambiguous failed delivery —
  records something that happened and stays until a human clears it.
- **Same live run only.** A replaced run (resume, branch, restart into a new conversation,
  or an in-CLI `/clear`/`/new` — `backends.md`) receives a fresh default grant rather than
  inheriting the prior run's expiry or send count. A user opt-out applies only to the run it
  names. The exception is an ambiguous failed delivery: its disabled state survives a run
  replacement until a human verifies the terminal and re-enables it.
- **Stability, not a snapshot.** `delivery_state` must read `safe` continuously for
  `auto_delivery_stable_seconds` (default 8) for the *same* message revision. One safe
  sample is a race; a held window is evidence. Any flap resets the window.
- **It does not require anyone to be watching.** Until 2026-07-30 the readiness gate demanded
  an attached browser and an exclusive input owner, which made `safe` unreachable for every
  session the operator was not looking at — so this controller could never fire for the
  sessions it exists to serve, and the manual path always asked for an override. See the
  correction in `delivery-readiness.md`.
- **Only an armed head, and only an authorized sender.** A draft was never armed and a
  `blocked` item carries an unresolved refusal — both need a human act first. Messages from
  other agents are eligible unless the receiving session opted *out* of
  `accept_agent_messages`, which rides along with the same per-run default grant
  (`agent-messaging.md`). The two remain separate switches - arming is authorization,
  auto-delivery is who presses send - so cycling one never rewrites the other; only the
  conversation default writes both, and only for a run that has none.
- **It can never override.** The controller cannot pass `confirm`: `send_next` rejects a
  confirmation from a non-human initiator (`confirm_requires_user`). Blocked or unknown
  readiness always means "not now", never "anyway".
- **Fail closed and stay stopped.** A *failed* delivery — where the PTY write may or may not
  have landed — disables that session's grant and asks the user to verify the terminal. It
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

These gate *widening* the capability (enabling the install master by default, dropping the TTL
or the consecutive cap) — they do not gate a conversation grant itself, which is bounded by
construction. Current values
are on `GET /api/queue/auto`, in the Queue tab's `auto:` disclosure, and in the fleet queue's
status line.

## UI

- **Queue panel, `auto:` strip**: a one-line status (on/off, sends left, minutes left, quiet
  hours, why it is off) that discloses the default-on per-conversation toggle and the
  `accept agent messages armed` toggle. Both are on by default for a live agent conversation,
  so the disclosure is an opt-out surface. The auto-delivery toggle alone is unavailable when
  the install's master switch is off, with the reason stated. Collapsed by default because it is carried permanently
  above the queue in a narrow column; it used to cost three wrapped lines there.
- **Per-item schedule**: `+5m` / `+15m` / `+1h` presets and `Clear schedule`, behind the
  row's `⋯`; a scheduled item shows its time in the row.
- **The install-wide brakes live where a person already is when they want them.** Pause-all /
  resume, `report unsafe delivery`, and the proving-period counters sit under the Queue tab's
  same `auto:` disclosure, separated by a rule because they are not per-session. That
  disclosure survives an empty target: the install-wide state is true with no session
  focused, and it is the state that makes every per-session reading a lie when it is off.
  `autodelivery.pause` reaches the same operation with nothing open at all, from desktop or
  phone. They are deliberately **not** in the fleet queue: a brake reachable only by opening
  an overlay is a brake you cannot reach in the moment you want it. The fleet queue reports
  the state and never owns it.
- **Settings → Prompt queue → Auto-delivery**: the install-wide master switch and the bounds every
  grant runs under (stability window, consecutive-send cap, grant expiry, refusal back-off,
  quiet hours). The queue strip's "off for this install" note names that control.

## API surface

```text
GET  /api/queue/auto                       policy, per-session rows, counters, promotion
POST /api/queue/auto/pause                 {paused}          emergency disable
PUT  /api/queue/auto/sessions/{sid}        {enabled, ttl_minutes, max_sends,
                                            accept_agent_messages}
POST /api/queue/auto/report-unsafe         {note}            operator review input
PATCH /api/queue/messages/{id}             {constraints}     schedule / clear
```

Per-session rows cover live sessions only.
Policy rows themselves are never deleted — an explicit opt-out or a failed-delivery hold
must survive a restart — so the table holds one row per session ever granted, and both the
one-second controller tick and this endpoint read it filtered to the sessions that exist
(`auto_policies(session_ids)`); unfiltered it had grown to ~9x the live count on a real
install, scanned every second.

## Configuration

`auto_delivery_enabled`, `auto_delivery_stable_seconds`, `auto_delivery_max_consecutive`,
`auto_delivery_session_ttl_minutes`, `auto_delivery_quiet_start`, `auto_delivery_quiet_end`,
`auto_delivery_refusal_backoff_seconds` (`config.py`, validated with lower bounds — a
zero-length stability window would defeat the gate it exists to be), all editable in
Settings → Prompt queue. Runtime state (pause, per-conversation grants/opt-outs, counters) lives in
SQLite, not config, so the emergency pause never waits on a config write.

## Key files

- `src/swe_mux/auto_delivery.py` — `AutoDeliveryController` (the loop, the gate, quiet
  hours, counters), `promotion_status`.
- `src/swe_mux/prompt_queue.py` — `queue_auto_policy` / `queue_auto_counters` tables,
  constraint enforcement in `send_next`, `normalize_constraints`, `schedule_status`.
- `src/swe_mux/server.py` — the `/api/queue/auto*` handlers and lifecycle wiring.
- `frontend/src/QueuePane.tsx` - the `auto:` strip, schedule presets, and the install-wide
  emergency controls with the proving-period counters.
- `frontend/src/App.tsx` - `autodelivery.pause`, the command that needs nothing open.
- `frontend/src/FleetQueue.tsx` - read-only report of install-wide state and proving counters.
- `frontend/src/queueApi.ts` - typed client.
- Tests: `tests/test_auto_delivery.py`, `tests/test_delivery_readiness_promotion.py`,
  `tests/test_frontend_phase5_contract.py`.

## Relates to

- `prompt-queue.md` — the queue and the delivery operation this drives.
- `delivery-readiness.md` — the evidence the gate consumes.
- `agent-messaging.md` — the other Phase 5 half: who may put messages in a queue.
- `../development/ROADMAP.md` Phase 5.
