# Agent messaging, the fleet queue, and drafted spawns

## What it is

Bounded messaging *between sessions that already exist*, plus a way for an agent to ask a
human to create one. Roadmap Phase 5; `CONTROL_PLANE_ROADMAP.md` §7.2. Fleet Queue is the
one human review surface for both kinds of request:

- `mux.notify(target, body)` — an agent stages a message in a sibling session's prompt
  queue. A caller over `PromptQueueService.enqueue`, never a second delivery path.
- `mux.request_spawn(prompt, …)` - an agent writes an **inert draft** that appears in Fleet Queue.
  It starts nothing; a human approving it is what spawns the session.
- The **fleet queue** - an application-wide authorship view over the same `queue_messages` rows, with sender/target labels, delivery state, Project/session filters, and revocation.
  It reviews; it does not deliver and does not carry the auto-delivery brakes.

What is deliberately absent: an agent cannot deliver, cannot spawn, cannot address a
session outside its Project, and cannot claim to be anyone else.

## Key concepts

- **Sender provenance is derived, never claimed.** `sender_kind` is one of `user`
  (loopback browser/CLI), `remote_user` (authenticated remote device — recorded, never
  privileged), `agent` (from the MCP token), `rule`, `queue_draft`. The HTTP route derives
  the human kinds from the transport; the MCP tools derive `agent` from the token. No API
  anywhere accepts a sender argument.
- **The receiver decides how much a message is worth.** `accept_agent_messages` is part of
  the per-run default grant a live agent conversation receives (`auto-delivery.md`), so an
  agent-authored message lands `armed` - at which point it still waits for head-of-line
  order and delivery readiness, and is only actually delivered by a human "Send now" or by
  that session's own auto-delivery grant, which the install master switch still gates. A
  session whose operator turned the toggle off receives an inert `draft` that only a human
  can arm, and that opt-out holds for the run that made it. Arming is authorization;
  auto-delivery is who presses send. The two toggles remain independent - turning
  auto-delivery off and on again does not rewrite this one.
- **Every bound lives in the daemon operation, not in the tool** (CP §7.1), so the browser,
  the CLI, and any later client inherit them:

  | Bound | Default | Refusal code |
  |---|---|---|
  | target in the caller's Project, live agent session, not the caller | — | `unknown_target`, `not_agent_target`, `self_notify` |
  | body size | 4 000 chars | `body_too_large` |
  | per-origin hourly budget | 20 messages | `origin_budget_exhausted` |
  | undelivered agent messages per target | 5 | `target_backlog_full` |
  | relay propagation: distinct sessions one thread may reach | 6 | `chain_depth_exceeded` |
  | agent messages within one thread | 12 | `thread_budget_exhausted` |
  | ring back past the session that messaged you | — | `relay_cycle` |
  | kill switch (`agent_messaging_enabled`) | on | `agent_messaging_disabled` |
  | expiry | 24 h | item is cancelled, `cancel_kind: expired` |

- **Replying to the session that messaged you is an ordinary turn, not a cycle.**
  This is the load-bearing distinction, and getting it wrong is what made replies impossible
  before 2026-08-13: a reply *is* A→B→A, so a cycle rule that tested "is the target anywhere
  upstream" refused the first reply every time and left the sender's feedback
  unacknowledged. Cycle detection now refuses only a ring that reaches *past* the session
  that spoke to you (A→B→C→A), which is the loop that routes around the propagation bound.
- **Two bounds, because they stop different things.** `chain_depth` bounds **propagation** -
  how many distinct sessions one thread reaches - and grows only when a message lands on a
  session that has not spoken in the thread yet; a back-and-forth reaches nobody new and so
  holds its depth. `max_thread_turns` bounds **volume** inside one thread, and is what
  actually stops two agents talking forever. Chain depth cannot serve that purpose once
  replies exist, because a two-party exchange has constant depth however long it runs.
- **Depth is sized for a relay across a fleet, because that is a shape people use.** The
  default was 3 while the only pattern anyone had was "tell one sibling", and it refused the
  fourth hop of an operator-authored hand-off passed down five sessions — killing the relay
  in the middle rather than at the point it was written. The hazard the bound exists for is
  **breadth**, one injected instruction fanning out, and breadth is bounded separately by the
  per-origin hourly budget, the per-target backlog, and the ring detector. A relay that needs
  to travel further than the bound is a fresh thread a human starts, and the refusal says so
  and names the setting rather than stopping silently.
- **Threads, depth, and rings are derived from the queue itself.** Each message records a
  `thread_id`, a `chain_depth`, and an `origin.path` of session ids with the most recent
  sender last, so no separate relay-state table can drift out of sync with the audit trail.
  A session's inbound context is the peer's most recent *delivered* message when the peer
  has written to it, and otherwise the deepest live chain — following the peer is what stops
  one unrelated deep thread from wedging every other conversation a session is in. The run
  filter is applied in SQL: applying it after `LIMIT 1` let a previous run's row mask the
  current context and silently report an unrelayed session.
- **`thread_id` is not the correlation id.** Correlation is a *per-sender idempotency key*,
  so a sender's second message in the same exchange would dedup into its first. The thread is
  assigned by the daemon at the head of a chain and inherited by everything that continues
  it, which also keeps it underivable from anything the caller supplies.
- **Retry-safe correlation.** An optional `correlation_id` is unique per sender; a retry
  returns the original message instead of a duplicate in the target's queue.
- **The receiver sees provenance in the prompt.** The stored queue body begins with a bounded `[mux notification]` envelope naming message id, correlation id, sender session, sender run, sender name, sender backend, optional reason, and a `reply_with` line carrying the exact target to answer and how many messages the exchange has left.
  The daemon generates message and correlation ids before enqueue, so the visible values match the durable row and the result returned to the sender.
  The `reply_with` line is not decoration: the envelope is the only surface the receiver sees, and without it an agent learns whether it may answer from a refusal, which is how a reply gets abandoned as impossible.
  The original caller body follows the envelope unchanged.
- **One audit trail.** An MCP-originated message is an ordinary queue item: same states,
  same head-of-line rule, same `queue_deliveries` rows, distinguishable only by
  `sender_kind` and its provenance. Events (`queue_message_received`) carry ids and counts,
  never the body.
- **Spawn is drafted, never granted** (CP §7.2/§16). `request_spawn` appends a typed
  `spawn_request` item to `<project>/.swe-mux/observations.json` with the proposed prompt,
  backend, cwd, and calling-session provenance, and emits `spawn_request_drafted`.
  Approving it (`POST …/observations/{id}/decide`) spawns through the ordinary spawn path
  with the prompt as `seed_text`; dismissing marks it decided. A request can only be decided
  once. An agent holding real spawn authority turns one prompt injection into unbounded
  fan-out — that is the failure mode a queue purge cannot undo.
- **Write status is readable by the attributed caller.** `message_status(message_id)` exposes
  drafted, armed, delivered, stranded, expired, or refused outcomes only when the MCP token
  owns the message's `sender_id`.
  `spawn_requests()` similarly returns only requests whose `from_session` is the caller.

## The same-host boundary (decided 2026-07-28, re-affirmed 2026-07-29)

Phase 4.5 shipped under "same-host agents are fully trusted", with a standing requirement to
revisit before Phase 5 armed any write path. Re-examined and **re-affirmed**, with the
reasoning recorded so it is not rediscovered:

- The enforcement option on the table was a token check on the mutating `/api` routes, with
  the browser given a daemon-local bearer at page load and sessions never receiving one.
  It cannot deliver the property it appears to: an agent session runs as the same user on
  the same host, so it can request whatever the browser is given (fetch `/`, read the page,
  ask the same endpoint). Any secret reachable by the browser is reachable by the agent.
  A real boundary here needs OS-level isolation — a separate user account or an ACL'd pipe —
  which is a different product.
- What that means, stated plainly: **the bounds in this document constrain well-behaved
  callers.** A prompt-injected agent can still reach `POST /api/sessions/{id}/input` on
  localhost, exactly as it could before Phase 5. That surface predates MCP and is unchanged
  by it.
- The compensating design is the one that *is* enforceable: agent-reachable authority stays
  strictly narrower than the browser's. No tool delivers, spawns, or writes to a PTY; every
  write is attributable to a session token, bounded, expiring, revocable, and visible in the
  fleet queue; and the receiver's own policy decides whether an agent message is even armed.
- If the day comes that the local HTTP surface must be an authorization boundary, the
  enforceable path is OS-level: bind the daemon to a per-user pipe/socket with an ACL that
  spawned sessions do not hold, and give the browser the only handle. That is a deliberate
  future decision, not a config flag.

## UI

- **Fleet queue** is a modal overlay, reached by app menu -> Fleet queue, `queue.fleet`, the Project menu (pre-filtered to that Project), or the Queue tab's `fleet` control.
  It is a modal rather than a drawer tab because nothing in it delivers: it needs no terminal beside it, the same watch-here/act-there split the Processes tab has with the process fleet.
  It partitions by author (`all | non_human | human`), not message direction, because the operator observes messages sent between agents rather than being every message's recipient.
  It opens on `non_human`: the rows the operator wrote are the ones they already know about.
  Project and target-session filters are applied by the daemon before the result limit.
  Message rows show sender and target labels, delivery state, per-item revoke, and an "Open queue" transition to the target's session-scoped Queue.
  Spawn rows name no target session, show requesting-session provenance and the proposed prompt, and expose the explicit once-only approve or dismiss act.
  It reports install-wide auto-delivery state and the proving-period counters, and owns neither: the brakes are on the Queue tab and `autodelivery.pause` (`auto-delivery.md`).
  It is not a transcript and shows only delivery state and provenance.
- **Queue rows** show `from <sender>` and the hop number for relayed messages.
- **Project notes and Scratchpad replace human observation capture.**
  The retired Observation Inbox is not a command or mounted view.
  Its JSON file remains compatibility storage for typed spawn requests.

## API surface

```text
GET  /api/queue/mailbox?author=all|non_human|human[&project_id=...][&target_session_id=...]
     (the route keeps its original name; the surface it backs is the fleet queue)
POST /api/queue/messages/{id}/cancel            {kind: revoked}
POST /api/projects/{pid}/observations/{oid}/decide  {decision: approve|dismiss, …overrides}
MCP  notify(target, body, reason?, correlation_id?)
MCP  request_spawn(prompt, backend?, name?, reason?)
MCP  message_status(message_id)
MCP  spawn_requests()
```

## Configuration

`agent_messaging_enabled`, `agent_message_max_chars`, `agent_message_hourly_budget`,
`agent_message_pending_per_target`, `agent_message_max_chain_depth`,
`agent_message_max_thread_turns`, `request_spawn_enabled` (`config.py`). Per-session `accept_agent_messages` is runtime state
in `queue_auto_policy`, defaulted on per run by the conversation-default grant rather than by
a config key - it is a per-conversation decision, and the config file is the wrong place for
state that has to be flippable instantly and per session.

## Key files

- `src/swe_mux/agent_messaging.py` — `AgentMessagingService` (relay policy, drafts, the `mailbox()` authorship projection the fleet queue reads).
- `src/swe_mux/mcp.py` — the two tools as thin callers.
- `src/swe_mux/prompt_queue.py` — sender columns, correlation index, relay queries.
- `src/swe_mux/project_files.py` — typed inbox items (`kind`/`request`) and
  `update_observation_request`.
- `src/swe_mux/server.py` — `queue_mailbox` route, spawn-request decision handler.
- `frontend/src/FleetQueue.tsx` - the modal: authorship and target filters, provenance rows, revocation.
- `frontend/src/QueuePane.tsx` - the target session's ordered queue reached by "Open queue", and the control that opens the fleet queue.
- `frontend/src/queueApi.ts` - typed fleet-queue query and response.
- Tests: `tests/test_agent_messaging.py`, `tests/test_mcp.py`,
  `tests/test_frontend_phase5_contract.py`.

## Relates to

- `mux-mcp.md` — the transport and caller identity.
- `prompt-queue.md` — the message model and delivery contract.
- `auto-delivery.md` — the receiver-side machinery that may deliver an armed message.
- `observations.md` - compatibility storage after the human inbox surface was retired.
- `../development/CONTROL_PLANE_ROADMAP.md` §7.1–7.2, §16.
