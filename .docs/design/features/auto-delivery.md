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
  The window is `auto_delivery_session_ttl_minutes`, edited in Settings → Prompt queue and
  nowhere else, conservative by default and lengthenable by the operator up to a day. It is a
  *value* rather than a switch, so every surface that reports a lapse links to that one editor
  instead of offering a second control (`setting-links.md`).
- **A lapse audits itself, because it is the one disable nobody can look up afterwards.**
  Every other disabled state records an act - a person opted out, a delivery failed, a budget
  ran down - and the act is what explains it. A lapse records only that time passed, so
  "auto-delivery grant lapsed while the conversation was idle" was the entire account
  available to an operator deciding whether the window was too short, and to a sender whose
  notification was sitting in that queue. It now writes, at the moment it fires: when, how
  long the conversation had been idle, which window it was measured against, and **how many
  messages it left waiting** - the last being what turns a lapse from a state into a stalled
  delivery. The record lives on the policy row (`disabled_at`, `lapse_idle_seconds`,
  `lapse_window_minutes`, `lapse_pending`), is logged at WARNING, is counted
  (`auto_lapsed`), and is cleared when the grant returns, because a stale audit is worse
  than none. It reaches the sender through `target_delivery.lapse` on `notify` and
  `message_status`, and the operator through the Queue tab's `auto:` strip.
- **One thing holds the lapse off: an exchange that is owed a reply.** A session whose own
  message was **delivered** to a peer inside `auto_delivery_reply_window_minutes` (30 by
  default, 0 to disable) is not an untouched conversation - it is the waiting half of a
  bounded exchange, and losing its grant there is what strands the answer. Observed live
  2026-08-21: an orchestrator's notify to a finished worker armed and could not deliver, and
  the worker's reply had nowhere to land either. Three things keep this from being a
  widening. It is evidence rather than authority - the master switch, the pause, quiet hours,
  head-of-line order, the stability window, readiness, and the consecutive cap all still
  decide each send, and it holds off the *lapse* alone, never an opt-out, a failed delivery,
  or a spent send budget. It is capped by the exchange's own `max_thread_turns` budget
  (`agent-messaging.md`), so it can never outlive the conversation that justifies it, and two
  agents cannot renew it between themselves past that bound. And **staging** a message opens
  nothing: an armed message nothing ever delivered is the symptom this exists to prevent, not
  evidence of a live exchange. The window is derived from the queue rows themselves, like
  thread identity and chain depth, so no second table can disagree with the audit trail.
- **A lapse is recoverable by time; the send cap is recoverable by evidence; a decision is
  not recoverable at all.** Lapsing records only that time passed, so the conversation default
  restores it once the session is in use again, without touching a separate
  `accept_agent_messages` choice made during that run. An explicit opt-out and an ambiguous
  failed delivery record something that happened and stay until a human clears them.
  The exhausted consecutive-send budget sits between the two: it records that *nobody was
  seen*, so anything that shows somebody is clears it - a human send, or a reply the session
  itself wrote to a peer (`PromptQueueStore.credit_auto_attention`).
- **The cap only ever bounded an unattended run, and until 2026-08-19 it could not be
  recovered from at all.** `reset_auto_sends` zeroed `sends_used` and left `enabled=0` with
  the disable reason in place, while the conversation-default pass deliberately restores only
  a lapse - so the documented "a manual send resets the count" recovery did nothing, and a
  grant that hit the cap stayed off for the rest of the run. Measured on a live install that
  day: three sessions in a working three-way exchange all read
  `enabled=0, sends_used=0, disabled_reason="reached 3 consecutive automatic sends"`, ten
  automatic sends all day against twenty manual ones, and the exchange stopped because the
  operator stopped hand-pumping it. A reply now clears it too, because a session that answers
  is the opposite of the failure the cap exists to catch, and because volume in an
  agent-to-agent exchange is bounded where it belongs - `max_thread_turns` and the per-origin
  hourly budget in `agent-messaging.md`.
- **The cap's default is not raised.** Raising it is a *widening* of standing unattended
  authority and is gated on the promotion criteria below; making it recoverable is not, since
  every recovery path requires positive evidence that something is reading the deliveries.
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
- **Mid-turn delivery is a second predicate, not an override.** An item may carry
  `constraints.delivery = "now"`, and `send_next` then authorizes it from
  `interject_state` instead of `delivery_state` - a strictly narrower reading that requires
  both the lifecycle evidence and the CLI's own screen to agree a turn is running, with no
  composer content to land on top of (`delivery-readiness.md`). Nothing about it passes
  `confirm`, and every protection still runs first, so an approval prompt, a picker, a rate
  limit, a retired transcript, and a remote boundary all still refuse. Who may *ask* for it is
  `agent-messaging.md`; the controller's own gates - master switch, grant, run binding,
  head-of-line, the consecutive cap, the stability window, quiet hours, the back-off - are all
  unchanged and all still apply.
- **What mid-turn delivery buys is latency, not preemption.** Claude and Codex both buffer
  text typed during a turn and take it at the turn boundary, so the write arrives sooner than
  the queue would have delivered it and not sooner than the turn ends. Stopping a turn is
  `interrupt`, which is a different capability with a different contract.
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
construction. Nor do they gate the reply window: it delivers nothing a lapse-free conversation
would not already have delivered, it is bounded by the exchange's own budget, and every other
gate still runs. Lengthening the *idle* window is an operator turning a dial the feature always
had, within the same validated range; raising the consecutive cap remains the widening these
criteria hold. Current values
are on `GET /api/queue/auto`, in the Queue tab's `auto:` disclosure, and in the fleet queue's
status line.

## UI

- **Queue panel, `auto:` strip**: a one-line status (on/off, sends left, minutes left, quiet
  hours, why it is off) that discloses the default-on per-conversation toggle, the
  `accept agent messages armed` toggle, and `accept mid-turn agent messages`. All three are on
  by default for a live agent conversation, so the disclosure is an opt-out surface.
  A lapsed grant states its audit there in words - how long the conversation was idle, under
  what window, how many messages were left waiting - and links to the window itself; a grant
  being held open by an exchange says so, because otherwise it is indistinguishable from one
  granted a moment ago. They are
  independent: arming decides whether a peer's message counts as authorized, auto-delivery
  decides who presses send, and the third decides whether send may happen while a turn is
  still running. Cycling one never rewrites another. The auto-delivery toggle alone is unavailable when
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
  grant runs under (stability window, consecutive-send cap, idle window, reply window, refusal
  back-off, quiet hours). The queue strip's "off for this install" note names that control, and
  a lapse notice links to the idle window here. Both windows carry `data-setting` marks so a
  deep link lands on the control rather than on the panel.

## API surface

```text
GET  /api/queue/auto                       policy, per-session rows, counters, promotion
POST /api/queue/auto/pause                 {paused}          emergency disable
PUT  /api/queue/auto/sessions/{sid}        {enabled, ttl_minutes, max_sends,
                                            accept_agent_messages,
                                            accept_agent_interjections}
POST /api/queue/auto/report-unsafe         {note}            operator review input
PATCH /api/queue/messages/{id}             {constraints}     schedule / clear
```

Per-session rows carry `lapse` (the audit, present only while the grant is off for idleness,
with individually-null fields on a row that lapsed before the audit existed) and `reply_window`
(present while an exchange is holding the lapse off, with the thread's used/limit counts).
The policy block carries `reply_window_minutes` beside the other bounds.

Per-session rows cover live sessions only.
Policy rows themselves are never deleted — an explicit opt-out or a failed-delivery hold
must survive a restart — so the table holds one row per session ever granted, and both the
one-second controller tick and this endpoint read it filtered to the sessions that exist
(`auto_policies(session_ids)`); unfiltered it had grown to ~9x the live count on a real
install, scanned every second.

## Configuration

`auto_delivery_enabled`, `auto_delivery_stable_seconds`, `auto_delivery_max_consecutive`,
`auto_delivery_session_ttl_minutes`, `auto_delivery_reply_window_minutes`,
`auto_delivery_quiet_start`, `auto_delivery_quiet_end`,
`auto_delivery_refusal_backoff_seconds` (`config.py`, validated with lower bounds — a
zero-length stability window would defeat the gate it exists to be), all editable in
Settings → Prompt queue.
`auto_delivery_reply_window_minutes` is the one bound whose lower limit is 0, because it is
the only one that *holds off* another bound rather than granting anything: switching it off
is a narrowing, where a zero-length stability window or an unbounded grant would not be.
Mid-turn delivery adds `agent_interject_enabled` (install master, on),
`agent_interject_hourly_budget` (10 per origin session) and
`agent_interject_min_interval_seconds` (60, per target). Runtime state (pause, per-conversation grants/opt-outs, counters) lives in
SQLite, not config, so the emergency pause never waits on a config write.

## Key files

- `src/swe_mux/auto_delivery.py` — `AutoDeliveryController` (the loop, the gate, quiet
  hours, counters), `_lapse_session` and `lapse_record` (the audit), `reply_windows` (the one
  thing that holds a lapse off), `promotion_status`.
- `src/swe_mux/prompt_queue.py` — `queue_auto_policy` / `queue_auto_counters` tables (schema
  v5 adds the lapse-audit columns), `open_reply_windows` and `pending_message_count` (the two
  derived reads behind the reply window and the audit),
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
