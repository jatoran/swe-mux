# Agent messaging, the fleet queue, and drafted spawns

## What it is

Bounded messaging *between sessions that already exist*, plus a way for an agent to ask a
human to create one. Roadmap Phase 5; `CONTROL_PLANE_ROADMAP.md` §7.2. Fleet Queue is the
one human review surface for both kinds of request:

- `mux.notify(target, body, delivery=, dry_run=)` — an agent stages a message in a sibling
  session's prompt queue. A caller over `PromptQueueService.enqueue`, never a second delivery
  path.
  `delivery="now"` asks for it to land in a turn that is already running, which three gates
  and two bounds decide whether the caller may even ask for.
  `dry_run=true` runs every bound and returns the same verdict without staging anything.
- `mux.revoke_message(message_id)` — the sender withdraws one message it staged that nothing
  has delivered. The narrowest write here, and the counterpart to the dry run.
- `mux.request_spawn(prompt, …)` - an agent writes an **inert draft** that appears in Fleet Queue.
  It starts nothing; a human approving it is what spawns the session.
- The **fleet queue** - an application-wide authorship view over the same `queue_messages` rows, with sender/target labels, delivery state, Project/session filters, and revocation.
  It reviews; it does not deliver and does not carry the auto-delivery brakes.
  Its view also carries the drafted `spawn_request` rows and, since Phase 7.6, drafted `control_request` rows (interrupt/end awaiting a human) so both agent-authored drafts are approved in one place.

What is deliberately absent: an agent cannot deliver, cannot spawn, and cannot claim to be
anyone else. It can address a session in another Project, but only by naming that Project -
its own is the default and nothing widens implicitly.

## Key concepts

- **Scope is the caller's Project by default and widens only when the caller says so
  (changed 2026-08-14).** Both write tools take the same `project` argument as the read
  surface — omitted for the caller's own Project, `"fleet"` for every Project, or a Project
  name or id — and `notify` also accepts a qualified `"Project name/session name"` target.
  It is re-resolved in `AgentMessagingService` rather than trusted from the MCP layer, because
  the bound belongs to the daemon operation (CP §7.1). A write is at least as sensitive as a
  read, so it takes the same argument with the same default and the same wording rather than a
  policy of its own. `request_spawn` is the exception that proves the rule: one request starts
  one session in one Project, so it accepts a Project name and refuses `"fleet"` with
  `invalid_project`.
- **A cross-Project message says so, and a same-Project one does not.** The envelope names the
  sender's Project only when the message crossed a boundary, at every level that has an
  envelope at all. The receiver cannot infer where a peer is working, and that changes how much
  the message is worth - but a clause on every message is one readers learn to skip. The same
  rule governs a cross-Project spawn request, whose Fleet Queue body names the Project it came
  from.
- **One name may match twice once a call reaches past one Project.** Two Projects may each
  hold a session called `backend`. Resolution refuses with `ambiguous_target` and the candidate
  session ids rather than answering "not found", which is unactionable when the session does
  exist — twice. Name resolution therefore happens inside the scope rather than through
  `SessionService.resolve`, which cannot distinguish the two cases.
- **Sender provenance is derived, never claimed.** `sender_kind` is one of `user`
  (loopback browser/CLI), `remote_user` (authenticated remote device — recorded, never
  privileged), `agent` (from the MCP token), `rule`, `queue_draft`. The HTTP route derives
  the human kinds from the transport; the MCP tools derive `agent` from the token. No API
  anywhere accepts a sender argument.
- **Arming is never the sender's claim, and there are exactly two forms of receiver
  authorization.** The floor is that a non-human sender's write ends at a human, and it is
  enforced in the daemon operation (`prompt_queue.enqueue`) rather than in any tool. The
  **standing** form is the target's `accept_agent_messages` grant, which admits a peer agent
  and is described below. The **per-message** form is `solicited_by`: the message names a
  request the *target itself* made, and is the bounded deterministic answer to it. That is a
  deliberate narrowing of the floor, not an erosion of it, and the distinction it turns on is
  **solicited versus unsolicited**. What the floor was written for is a write appearing in
  somebody's terminal unasked - a rule, an observer, a peer. A reply to a request that session
  explicitly made is the opposite: the consent was given by the receiver, before the message
  existed, and it reaches exactly as far as the request did.
  The first user is the land queue's handback (`land-queue.md`), whose live failure is what
  produced the rule: a `request_land` bounced on a conflict, the answer arrived as an inert
  draft, and the requesting session idled unaware until a human pressed send. Its five bounds
  are stated there and are the pattern any later one must match - only the requester, only a
  fixed daemon-authored template, only the run that asked, capped per request, and off with the
  authority that accepted the request. The second user is the **settle-watch notice**
  (`mux-mcp.md`): `watch_session` arms a read-only watch on a sibling and one fixed template
  goes back to the session that armed it, named in `solicited_by` by the watch id. It matches
  the pattern with four of the five bounds holding by construction - the tool has no recipient
  argument, the body is a daemon template, an operator cannot arm a watch, and one watch
  matures into exactly one notice - leaving the run binding and the feature's own
  `session_watch_enabled` switch to be re-read when the notice is written. Nothing in the floor
  knows what a land request or a watch is: it reads `solicited_by`, which is why the second
  caller needed no second exception.
  Arming is still not delivery: an armed solicited reply waits for head-of-line order, delivery
  readiness, and the receiver's own auto-delivery grant like everything else, and refusing to
  arm one never refuses the message - it is enqueued as a draft a human can send.
  `solicited_by` is stored on the row rather than derived, because a message that arrived armed
  from a non-human sender must be able to name what asked for it.
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

  | Bound | Configured default | Backstop (limits off) | Refusal code |
  |---|---|---|---|
  | target in the requested scope, live agent session, not the caller | caller's Project | same | `unknown_target`, `ambiguous_target`, `unknown_project`, `not_agent_target`, `self_notify` |
  | body size | 8 000 chars (raised from 4 000 on 2026-08-30: a real task brief with acceptance criteria measures 6-9k, and the old cap trimmed the constraints rather than the padding; beyond a brief a reference still beats a payload, which is why it is not larger) | same (always binds) | `body_too_large` |
  | per-origin hourly budget | 20 messages | 500 | `origin_budget_exhausted` |
  | undelivered agent messages per target | 5 | 50 | `target_backlog_full` |
  | relay propagation: distinct sessions one thread may reach | 6 | 10 | `chain_depth_exceeded` |
  | agent messages within one thread | 40 | 1 000 | `thread_budget_exhausted` |
  | ring back past the session that messaged you | — | same (always binds) | `relay_cycle` |
  | kill switch (`agent_messaging_enabled`) | on | same | `agent_messaging_disabled` |
  | expiry | 24 h | same (always binds) | item is cancelled, `cancel_kind: expired` |
  | mid-turn kill switch (`agent_interject_enabled`) | on | same | `interject_disabled` |
  | target Project's `interject_grant` | `granted` (since 2026-08-25; a Project writes `off` to withdraw it) | same | `interject_not_granted` |
  | target's `accept_agent_interjections` | on for a live run | same | `interject_refused_by_target` |
  | mid-turn deliveries per origin per hour | 10 | 120 | `interject_budget_exhausted` |
  | floor between mid-turn deliveries to one target | 60 s | 5 s | `interject_too_soon` |

- **The rate limits are a mode, off by default (2026-08-25).** `agent_message_limits_enabled`
  decides which column above binds: on, the configured values; off (the default), the fixed
  backstop ceilings in `config.agent_message_bounds()`. The backstops exist so an orchestrator
  relaying work across a fleet for hours never trips a send budget, while a runaway loop still
  ends: the thread budget is the one brake that stops two agents talking forever (a reply
  refreshes the peer's auto-delivery grant, so a two-party exchange renews itself), which is
  why "off" is a high ceiling rather than no ceiling. Size, ring detection, expiry, and the
  kill switches are not rate limits and bind in both modes. Both paths resolve through the one
  `agent_message_bounds()` helper - staging in `agent_messaging.py` and the reply window in
  `auto_delivery.py` must never disagree about which mode the install is in, or a working
  exchange's grant would lapse at the configured 40 while staging allowed 1 000.

- **The envelope states its authority, because a receiver cannot infer it** - unless the target
  Project chose `bare`, which is the one level that gives this up and the reason it is opt-in.
  A peer's note and
  an instruction a human approved arrive through the same pipe. The authority clause says
  which this is: a message auto-delivered under the target's standing grant declares that no
  human reviewed it, while a message that waited as a draft declares that a person armed it and
  released it. The auto-delivered form informs rather than forbids — a conflict with the
  operator's own instruction is neither complied with nor allowed to stall the relay, because
  an operator relaying their own release through a peer is a legitimate shape that a hard
  prohibition would block forever. Without the header a relayed "your operator says go ahead" is
  indistinguishable
  from a prompt injection, and the conservative reading — refuse — is correct often enough to
  be worth protecting and wrong often enough to be worth informing (observed 2026-08-13, a
  session correctly refused a relayed release it had no way to verify).
- **Replying to the session that messaged you is an ordinary turn, not a cycle.**
  This is the load-bearing distinction, and getting it wrong is what made replies impossible
  before 2026-08-13: a reply *is* A→B→A, so a cycle rule that tested "is the target anywhere
  upstream" refused the first reply every time and left the sender's feedback
  unacknowledged. Cycle detection now refuses only a ring that reaches *past* the session
  that spoke to you (A→B→C→A), which is the loop that routes around the propagation bound.
- **Mid-turn delivery is asked for by the sender, permitted by the Project, and vetoed by
  the receiver.** `delivery="now"` marks the queue item; whether the write is *safe* is decided
  later and elsewhere, by the readiness tracker's `interject_state`
  (`delivery-readiness.md`), which can refuse everything the gates here allowed. The three
  gates are deliberately the same shape as the rest of the control plane: an install-wide
  switch the operator holds, a per-Project standing permission (`interject_grant`, its own
  field rather than a level of `session_control_grant`, because being written to mid-turn is
  a property of a working repository; `granted` by default since 2026-08-25, withdrawn by
  writing `off`), and the receiving session's own opt-out for its run. The receiver keeps a veto
  because the receiver pays the cost: an ordinary message costs nothing until it is read, and
  a mid-turn one costs attention immediately. Every refusal names the ordinary path, because a
  sender that reads "no" without reading "send it without `delivery` instead" abandons the
  message.
- **The envelope says a message arrived mid-turn, because the receiver cannot tell.** The CLI
  buffers the paste and hands it over at the turn boundary, so it reads exactly like something
  typed between turns. The envelope names it and says what the claim is: the sender asked for
  it to arrive sooner, which is a claim about urgency and not about authority. That clause is
  carried at `compact` as well as `full`, because a message that interrupted a turn is the one
  a receiver is most likely to over-weight.
- **A reply refreshes the replying session's own auto-delivery budget.** Writing a reply is
  direct evidence that the session consumed what was delivered to it and is still working the
  exchange - the opposite of the unattended run the consecutive-send cap exists to stop - so
  it clears a grant the cap switched off (`auto-delivery.md`). This is why the volume bound
  that actually ends a runaway exchange has to be `max_thread_turns` and the per-origin hourly
  budget, and why `max_thread_turns` is 40 rather than the 12 that was about to refuse a
  working three-way conversation on its ninetieth minute (measured 2026-08-19).
- **And a *delivered* message holds the sender's grant open until the reply can land.** The
  symmetric half of the rule above: the session that has just handed work over is the one that
  then goes quiet, so the idle lapse closes its grant precisely while it waits, and the
  answer arrives armed with nothing to deliver it. `max_thread_turns` is what bounds that
  window - the grant is held only while the exchange still has messages left in it - which is
  the third job this budget does and the reason it, rather than chain depth, is the volume
  bound. The mechanism and its limits are in `auto-delivery.md`; what belongs here is that the
  exchange's own budget is what caps it, so two agents cannot renew each other's grants past
  the bound that ends their conversation.
- **A `notify` result says whether anything will deliver what it just staged.** `armed` alone
  is unactionable: it is the same word for a peer that is merely busy and for one nothing can
  reach without a human. The result and `message_status` both carry `target_delivery`
  (`auto_delivery`, `blocked_by`, `sends_remaining`, and `lapse` when the grant is off for
  idleness), derived from the install's brakes and
  the target's own grant, and the note tells a sender that finds it blocked to say so rather
  than waiting silently for a reply. Without it, three sessions in a live exchange went quiet
  on 2026-08-19 with no participant able to explain why.
- **The verdict is available *before* the message exists, and the message is withdrawable
  after.** Reporting deliverability only after arming meant an unreachable peer was something
  a sender discovered rather than chose - and once discovered, the armed item was a duplicate
  with no agent-reachable cleanup, so it sat in the peer's queue waiting to arrive out of
  context (observed live 2026-08-21). Two bounded additions close that, and neither widens
  what delivers. `dry_run` re-runs every check - target resolution, size, the per-origin
  budget, the target backlog, ring detection, chain depth, the thread budget, and the three
  mid-turn gates - and answers with `would_arm` and the same `target_delivery`, having staged
  nothing, spent no budget slot, and charged no mid-turn slot; a refusal is still a refusal,
  because a preview that said "fine" and then refused would be worse than none. `revoke`
  cancels one message the caller is *already attributed as the author of* and only while it is
  still `draft`/`armed`/`blocked` - a delivered message is text in somebody else's terminal,
  and `not_revocable` says so rather than pretending. A revoked message reads back as
  `revoked` from `message_status` rather than `refused`: nothing rejected it, and only one of
  the two means "try again differently".
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
- **The receiver sees provenance in the prompt, at the level the target Project asks for.**
  The stored queue body begins with a bounded envelope, and the original caller body follows it unchanged.
  How much that envelope says is the target Project's `message_envelope` (`automation-enablement.md`), resolved through the same four install layers as the actuation grants.
  The reply affordance is not decoration at any level that has one: `notify` is fire-and-forget into a queue, so nothing carries an answer back on its own, and without the line an agent learns whether it may answer from a refusal - which is how a reply gets abandoned as impossible.
  This is the structural difference from a synchronous orchestrator like herdr, whose `agent.prompt --wait` blocks on the target settling and therefore needs no reply route in the text at all.

  | Level | What it carries |
  |---|---|
  | `full` | The `[mux notification]` block: sender session, name, backend, cross-Project clause, reason, the whole standing-grant statement, the mid-turn paragraph, and `reply_with`. |
  | `compact` (default) | Two `[mux]` lines: sender name and session, cross-Project clause when there is one, whether a human reviewed it, the conflict instruction, the mid-turn clause when it applies, and the reply route. |
  | `bare` | The body alone, textually indistinguishable from the operator typing. |

  Three things were dropped from `full` outright on 2026-08-29, at every level, because no receiver had a tool to spend them on: `message_id` and `correlation_id` are the *sender's* bookkeeping for `message_status` and `revoke`, and `notify` already returns both to it; `from_run` is a handle no receiver-facing tool takes.
  Measured on the worst case - cross-Project, armed, mid-turn, over a 67-character body - the envelope went from 958 characters to 819 at `full`, 428 at `compact`, and 0 at `bare`.

  **The conflict instruction stays at `compact`.** A first draft of the level cut it for length and a test caught it, which is the argument for keeping it: the sentence is not provenance decoration, it is the only thing telling a receiver what to do when a peer contradicts its operator, and the conservative reading a model reaches without it - refuse, and stall - is the failure the line exists to prevent.
  The mid-turn urgency/authority clause stays for the same reason: a message that interrupted a turn is the one a receiver is most likely to over-weight.

  **`bare` is a real cost, opt-in per Project, and permitted on the mid-turn path.**
  With it, an armed peer message is indistinguishable from the operator speaking, which is exactly the confusion the authority line was written to close.
  It is allowed with `delivery="now"` deliberately rather than by oversight: reaching that combination takes four standing decisions, three of them written down in advance by a human (`agent_interject_enabled`, the Project's `interject_grant`, the Project's `message_envelope`) plus the readiness tracker agreeing at delivery.
  Attribution is not lost from the *system* at any level - the queue row, its `origin` payload, `queue_deliveries`, and Fleet Queue all still name the sender - only from the receiving model's prompt.

- **A sender may disclose more than the Project asks and never less.** `notify(envelope=...)` is clamped by the target Project's resolved level, using the same ordering the ceiling uses rather than a second comparison path.
  The effective level comes back in the result, so a caller that asked for `bare` into a `full` Project learns its message announced itself anyway.
  An unknown value is refused rather than clamped: a typo is not a request for less disclosure and must not silently become one.
- **One audit trail.** An MCP-originated message is an ordinary queue item: same states,
  same head-of-line rule, same `queue_deliveries` rows, distinguishable only by
  `sender_kind` and its provenance. Events (`queue_message_received`) carry ids and counts,
  never the body.
- **Spawn under a `draft` grant is drafted, never granted** (CP §7.2/§16). `request_spawn`
  against a Project whose `spawn_grant` is `draft` appends a typed
  `spawn_request` item to `<project>/.swe-mux/observations.json` with the proposed prompt,
  backend, model, cwd, and calling-session provenance, and emits `spawn_request_drafted`.
  Approving it (`POST …/observations/{id}/decide`) spawns through the ordinary spawn path
  with the prompt as `seed_text` and the model as `model`; dismissing marks it decided.
  A requested model is validated when the *request* is written rather than when it is
  approved, and it travels onto the card and into the approval's spawn: refusing at approval
  time refuses in front of the wrong person, since the agent that named the model is the one
  that can pick another and by then it has moved on - and a model the approval silently
  dropped would be worse still, because the human has already agreed to it. A request can only be decided
  once. Since 2026-08-25 the *default* grant is `granted` - the call creates the session
  directly, inside the per-origin hourly budget - so the draft path is what a Project
  lowered to `draft` (or with the `session_control` automation switched off) gets. The
  fan-out hazard the draft path was built for ("an agent holding real spawn authority turns
  one prompt injection into unbounded fan-out") is now answered by the budget and the audit
  trail rather than by a human per request; the per-Project lowering is the way back.
- **Session control acts the same way under the same grant** (Phase 7.6, CP §7.6). A
  `mux.interrupt`/`mux.end_session` call whose Project sits at a `draft` grant appends
  a typed `control_request` item to the same `observations.json` (action, target, reason,
  calling-session provenance), emits `agent_control_drafted`, and starts nothing; approving it
  runs the shared interrupt/graceful-end daemon operation. It mirrors `spawn_request` exactly -
  inert draft, one human decision, one place to review - which is why it is worth stating here
  even though its authority and bounds live in `session_control.py` and its full contract in
  `mux-mcp.md`. The default grant is `granted` (2026-08-25): the call skips the draft and acts
  directly, inside bounds, and a Project writes `session_control_grant = "draft"` to put the
  human back in front of each action.
- **Write status is readable by the attributed caller.** `message_status(message_id)` exposes
  drafted, armed, delivered, stranded, expired, or refused outcomes only when the MCP token
  owns the message's `sender_id`. Sender attribution is the whole check: the message row
  carries the *target's* Project, so a Project comparison would hide the status of everything
  the caller sent across a boundary — it would have written something it could not follow.
  `spawn_requests()` similarly returns only requests whose `from_session` is the caller, and
  takes the same `project` argument because a request drafted into another Project is filed
  there.

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
  It is a modal rather than a drawer tab because nothing in it delivers: it needs no terminal beside it, and a wide table-shaped surface is what a modal is for.
  It partitions by author (`all | non_human | human`), not message direction, because the operator observes messages sent between agents rather than being every message's recipient.
  It opens on `non_human`: the rows the operator wrote are the ones they already know about.
  Project and target-session filters are applied by the daemon before the result limit.
  Message rows show sender and target labels, delivery state, per-item revoke, and an "Open queue" transition to the target's session-scoped Queue.
  Spawn rows name no target session, show requesting-session provenance and the proposed prompt, and expose the explicit once-only approve or dismiss act.
  It reports install-wide auto-delivery state and the proving-period counters, and owns neither: the brakes are on the Queue tab and `autodelivery.pause` (`auto-delivery.md`).
  It is not a transcript and shows only delivery state and provenance.
- **Queue rows** show `from <sender>` and the hop number for relayed messages.
- **A cross-Project message files under the Project it was sent *to*.** The queue row's
  `project_id` is the target's, so the fleet queue's Project filter groups a message with the
  session that has to act on it rather than with the one that wrote it. The sender's Project is
  recorded in the row's provenance and stated in the receiver's envelope.
- **Project notes and Scratchpad replace human observation capture.**
  The retired Observation Inbox is not a command or mounted view.
  Its JSON file remains compatibility storage for typed spawn requests.

## API surface

```text
GET  /api/queue/mailbox?author=all|non_human|human[&project_id=...][&target_session_id=...]
     (the route keeps its original name; the surface it backs is the fleet queue)
POST /api/queue/messages/{id}/cancel            {kind: revoked}
POST /api/projects/{pid}/observations/{oid}/decide  {decision: approve|dismiss, …overrides}
MCP  notify(target, body, delivery, reason?, correlation_id?, project?, dry_run?)
MCP  revoke_message(message_id, reason?)
MCP  request_spawn(prompt, backend?, model?, name?, reason?, project?,
                   watch?, watch_timeout_minutes?, pane?, correlation_id?)
MCP  message_status(message_id)
MCP  spawn_requests(project?)
```

`delivery` became **required** on 2026-08-30 (`"when_idle"` or `"now"`, and `"now"` also
requires a `reason`): with a silent `when_idle` default, senders never weighed *when* the
message should land, and a correction that should have interrupted a worker's plan arrived
after the worker had finished executing it. Requiring the argument forces that choice at
every call, and the reason on `"now"` is provenance for a delivery that costs the receiver
attention immediately.

`dry_run` stages nothing and returns `{dry_run, would_arm, state, thread_messages_remaining,
would_deduplicate, target_delivery, note}`. `revoke_message` refuses with `unknown_message`
(not the caller's) or `not_revocable` (already delivered, expired, or cancelled).

`project` is omitted for the caller's own Project, `"fleet"` for every Project, or a Project
name or id. `notify` also accepts `"Project name/session name"` as its `target`.
`request_spawn` refuses `"fleet"`.

## Configuration

`agent_messaging_enabled`, `agent_message_limits_enabled` (off by default - the configured
rate bounds bind only while it is on, with the fixed backstops of
`config.agent_message_bounds()` binding otherwise), `agent_message_max_chars`,
`agent_message_hourly_budget`, `agent_message_pending_per_target`,
`agent_message_max_chain_depth`, `agent_message_max_thread_turns`,
`request_spawn_enabled` (`config.py`).
All of them are edited in Settings → Prompt queue: the message bounds under **Agent messaging**,
and `request_spawn_enabled` with its hourly budget under **Agent actuation**, because a spawn
acts on the fleet where a message is text a human still reads.
Per-session `accept_agent_messages` is runtime state
in `queue_auto_policy`, defaulted on per run by the conversation-default grant rather than by
a config key - it is a per-conversation decision, and the config file is the wrong place for
state that has to be flippable instantly and per session.

## Key files

- `src/swe_mux/agent_messaging.py` — `AgentMessagingService` (relay policy, the `dry_run`
  projection, sender-attributed `revoke`, drafts, the `mailbox()` authorship projection the
  fleet queue reads).
- `src/swe_mux/project_scope.py` — the `project` argument both write tools share with the read surface.
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
- `land-queue.md` - the first solicited reply, and the consent rationale for narrowing the floor.
- `observations.md` - compatibility storage after the human inbox surface was retired.
- `../development/CONTROL_PLANE_ROADMAP.md` §7.1–7.2, §16.
