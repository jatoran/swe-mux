# Backend: MCP, queues, messaging, and scheduled runs

Index: `../packages.md`.
Design: `../../../design/features/mux-mcp.md`, `../../../design/features/configurator.md`, `../../../design/features/prompt-queue.md`, `../../../design/features/auto-delivery.md`, `../../../design/features/agent-messaging.md`, `../../../design/features/scheduled-runs.md`, `../../../design/features/observations.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## The agent-facing MCP surface

### `mcp.py`

The agent-facing MCP protocol plus a closed tool set.

Reads:

- Own-Project-by-default session and run briefs.
- Compact filtered history hits and stale-safe hit-neighborhood reads.
- Bidirectional run-bound transcript pages, Agent Context sources, Project notes, sender message status, and caller spawn-request status.
- The four cross-session memory reads (`provenance`, `verified_status`, `prior_resolutions`, `dead_ends`), DAG-gated per `MEMORY_TOOL_AUTOMATION`, run-attributed, answering `unsupported`/`disabled` rather than a fake empty.
- The scan-timeline reads: `scan_timeline`, session-scoped and gated on the *target* session's Project opting into `scan_reads`, serving digest, records, or full details over the `scan_consumers` projection with an exclusive `since_t1` cursor and the liveness block on every result; and `scan_search` over distilled records, gated on `semantic_history_search`.
  No scan or backfill trigger is reachable from MCP, because a read costs nothing while a scan spends the human's gated budget.
- `watch_session`, the one read that matures into a message: it reads a sibling's state and stages a single deterministic notice into the *caller's own* queue when that sibling settles, ends, or the caller's timeout elapses.
  Declared a read because it addresses nobody and actuates nothing; the bounds and the fire rules live in `session_watch.py`.
- `worktree_context`, the read-only projection of the targetless land resolver.

Writes, all thin callers into services that hold the authority: `notify`, `revoke_message`, `request_spawn`, `run_action`, `interrupt`, `end_session`, `use_worktree`, and the two land-queue callers.
`use_worktree` calls `agent_worktree_context.py`, which validates and records one run-bound linked worktree for a Codex-style session whose host cwd remains on trunk.
`request_land` and `request_verify` accept no checkout argument and share one resolver: live linked-worktree cwd first, validated selection only from primary cwd second.
Two tools rather than one flagged tool, so the call that moves a trunk is never the default spelling of the call that moves nothing.
`notify(dry_run=true)` is the one call on that list that writes nothing, and it is not counted as a write: checking before you send must not read as authority spent.
A service refusal a caller can act on is translated where the tool calls the service - the enqueue helper turns `LandRefusal` into the same typed `QueueError` the tool's own refusals raise - because the generic handler above it can only answer `500 internal server error`, which is what the land tools did until the wire canary first ran.

Also token-derived identity, exact display-name resolution, cursors, output budgets, redaction, and content-free per-tool result diagnostics.

**Not:** history indexing and ranking (`history.py`), relay policy and queue and request storage (`agent_messaging.py` and existing services), title generation (read from `automation_store.py`), delivery, PTY writes, spawn, or aiohttp handlers (`routes/`).
Nor any authority a write tool borrows: session control (`session_control.py`), worktree selection and land resolution (`agent_worktree_context.py`), landing (`land_queue.py`), settle-watch bounds and fire rules (`session_watch.py`).

Also the configurator family (`CONFIGURATOR_TOOLS`), which is a *separate* array rather than flagged entries in `TOOLS`: `tools_for` returns the ordinary list unchanged to every session but a configurator, so nothing has to remember to filter.
Listing and dispatch apply the same `SessionRecord.configurator` check, and a guessed name answers "unknown tool" rather than "not permitted" - to a session that was never shown the tool that is the literal truth.
The four handlers are thin: the guides are served straight from `configurator.py` (files in this build, no runtime state), and the other three call the injected service.

### `mcp_contract.py`

The shared closed read and write tool declarations, and the generated Claude read-permission names.

Four names sit apart from the fleet lists in `CONFIGURATOR_READ_TOOL_NAMES` and `CONFIGURATOR_WRITE_TOOL_NAMES`: they describe and change *swe-mux*, not the work any session is doing.
The reads are in the generated permission allowlist and the write is not - a rule only decides whether the CLI prompts, the daemon refuses a non-configurator caller regardless, and a settings change is exactly the thing a human should see before it happens.

**Not:** tool implementation, transport, or write approval.

### `configurator.py`

The configurator agent's substrate (`design/features/configurator.md`).

- The generated inventory: a settings catalog derived from the `Config` dataclass, `RESTART_FIELDS`, and `_validate` itself; the harness table over `public_harness_registry`; the automation DAG with transitive closures; the project-config field and forbidden sets; the MCP surface.
- **Constraints are quoted from the validator, not transcribed.** `settings_catalog` sets an impossible sentinel on a detached candidate, runs `_validate`, and keeps the sentence it objects with. Restoration is in a `finally`, and a test asserts the live config is untouched: a missed restore would corrupt the running install with a value nothing else could produce.
- Credential-shaped fields redact to `<set>`/`<unset>`. No `Config` field is a credential today; the pattern is anchored on whole singular words because a loose "token" match swallowed nine budget ceilings.
- The closed guide set and its reads. Guides live in `assets/configurator/` - the wheel and the PyInstaller bundle both carry that directory and neither carries `.docs/`.
- `install_mode()` / `source_checkout()`: whether this daemon can be edited at all.
- The seed prompt, and `ConfiguratorService`, whose diagnostics and settings write are injected callables so this module stays free of the HTTP layer.

- `rail_projection`: a *reading* of the opaque command-rail blob - rows with their items' labels, the exact path an edit would name, and every per-Project override resolved to its Project **name** with the caller's own marked.
  It degrades (reports `readable: false`, or an empty `layouts`) rather than raising, because it is a projection over a document whose schema belongs to the browser.
  It is never a writer: writes stay path-scoped operations that need no schema at all.
- Section-scoped manifests. `settings` is 45 KB of the 56 KB and is omitted by default; `settings_query` narrows it further.

**Not:** the settings write itself (`config.update_config`, reached through the injected callable), or the device-settings write (`settings_store.apply_operations`, likewise).
Nor the health report (`doctor.py`, gathered by `routes/diagnostics.py`), harness resolution (`harness.resolve_default_harness`), the routes and the session marker (`routes/configurator.py`, `models.py`), or MCP transport (`mcp.py`).

### `settings_patch.py`

Path-scoped edits to a JSON document whose schema this process does not hold.

Four operations - `set`, `remove`, `remove_values`, `insert` - over JSON Pointer paths with one addition: `[key=value]` selects the element of an array whose field matches, so a row is named by its own id rather than by a position that a reorder invalidates.
A batch applies to a private deep copy and is all-or-nothing, because half-edited is the worst outcome available on a document nothing downstream can validate.

The reason it exists rather than a whole-document write: seven of the nine device-settings domains are opaque, so anything replaced wholesale is unvalidatable, and an operation cannot touch what it did not name.
`remove_values` is the load-bearing one and its justification is correctness rather than ergonomics - four positional deletes composed against one reading remove the wrong things after the first, because each shifts the indices after it.

**Not:** storage, digests, backups, or any knowledge of what a rail, a sound map, or a drawer-tab order means.

### `settings_store.py` - the agent-facing door only

The store itself is the browser's: per-device-class UI settings, with the `alerts`/`notifications` half interpreted server-side for the push sender (`design/features/notifications.md`).
What belongs to this map is the second editor it grew.
`domain()` returns one domain's document with a content digest.
`apply_operations()` applies `settings_patch` operations behind a digest precondition and a file backup.

The digest exists because the store has **no revision** and the browser writes **whole domains**.
Without it, an agent that read, thought, and wrote back would silently discard a drag made in between.

The backup exists because nothing here can validate an opaque domain.
The honest guarantee is not "this write is correct" but "the previous document is still on disk" - which `config.toml` already had and this file did not.

**Not:** the browser's own whole-domain `update()` path, notification policy, or the event that repaints attached clients (`routes/settings.py` emits `settings_changed`; the store does not know about the event bus).

### `project_scope.py`

The `project` argument shared by the agent-facing read and write surfaces: own-Project default, `"fleet"`, Project name and id resolution with listing refusals, the `admits()` predicate over records and history rows, and qualified `Project/session` target splitting.

**Not:** any tool implementation, session or history lookup, or transport.

### `session_control.py`

`SessionControlService`: authority and bounds around the shared interrupt and graceful-end daemon operations.

- The install master switch, the per-Project `off`/`draft`/`granted` grant, and Project scope.
- The fail-closed delivery-readiness gate an interrupt must pass.
- The per-origin hourly budget and the reciprocal-cycle guard.
- Idempotency by `correlation_id`.
- Drafting an inert `control_request` under the `draft` grant.

Every refusal is a typed `QueueError`.

**Not:** the interrupt and graceful-end PTY operations themselves and the daemon-owner check (all three in `routes/terminal.py`), MCP transport (`mcp.py`), or observation storage (`project_files.py`).

### `agent_surfaces.py`

The one place the two per-harness capability maps (`harness_mcp_enabled`,
`harness_cli_enabled`) become answers, so the spawn env, the MCP endpoint's gate, and the
doctor report cannot disagree about what a harness holds.

- `harness_surfaces` / `surfaces_env_value`: the canonical `MUX_SURFACES` value stamped into
  agent panes (`session.py`), empty for the enforced "neither" state.
- `surface_gate`: the `backend -> bool` callable `mcp.resolve_caller` refuses tokens through
  when both surfaces are off (ROADMAP Phase 23 W4).
- `coherence_warnings`: the two advisory incoherences - a delivered skill with no capability
  behind it, and a CLI-only surface nothing ever advertises - consumed by `doctor.py` and
  mirrored client-side in the Settings Fleet access control.

**Not:** the toggles' storage or validation (`config.py`), the transports themselves
(`mcp.py`, `cli.py`), or skill delivery (`skill_install.py`, the adapters).

### `session_watch.py`

`SessionWatchService`: one-shot settle watches, the read that matures into one bounded
message - and, since 2026-08-30, the synchronous waits (`await_settle`) behind
`await_session`, which share the sweep and the fire rules with a different sink: an
`asyncio` future fulfilled in-band instead of a queue notice, so no arming, delivery, or
grant machinery is involved. Awaits are bounded (`AWAIT_*` constants: 50 s default under
every measured harness tool timeout, 600 s ceiling, 4 per caller), a timeout is a
re-callable result carrying the state, a target already settled *and held* answers
immediately from `record.state_since`, and `stop()` resolves every open future rather than
abandoning it (an abandoned future raises from a finalizer at some later test's expense
under `filterwarnings = error`).
The service is also the watch half of the reply-window evidence: `origin_windows` reports
open watches per watcher, each bounded by its own deadline plus `WINDOW_GRACE_SECONDS`, for
`auto_delivery.py`'s lapse hold-off.

- Arming bounds: the install switch, Project scope through `project_scope.py`, agent-only target and watcher, no self-watch, one watch per target, the per-watcher ceiling, and the timeout ceiling (a `0` timeout is refused rather than defaulted).
- The fire rules: `ended` unconditionally, `settled` only on an observed `working` -> `idle`/`awaiting` edge that holds `SETTLE_HOLD_SECONDS`, and the timeout checked last so a settle maturing on the same sweep reports the case that happened.
- The two suppressions that keep "settled" honest: `starting` is not working, and an `idle` target holding `RUNNING_ACTIVITY_KINDS` or `idle_reason: waiting_on_background` has not finished.
- Lifetime: in-memory, dropped when the watcher session ends or its conversation rolls over, and flushed as notices on `stop()` so a daemon restart is never a silent un-arming.
- The fixed notice template, and the counters `GET /api/diagnostics/background` reports.
- `_notice_arming`: whether the notice is staged armed (`solicited_by=<watch id>`) or as a draft.
  Two checks, made when the notice is written rather than at arming time: the run that armed the watch must still be live, and `session_watch_enabled` must still be on.
  The other bounds that authority requires hold by construction here (`design/features/land-queue.md`), so there is nothing else to check.
  The outcome is recorded as `armed` plus an `arming_reason`, and counted as `armed_notices` beside `resolved`.

Every refusal is a typed `WatchRefusal` (a `QueueError`).

**Not:** the arming floor itself or delivery (`prompt_queue.py` owns both; this module decides only what to *ask* for and reads the answer back off the row), status detection itself (`session.py` and `observation.py` own the state the watch reads), MCP transport (`mcp.py`), or any storage - a watch has no table on purpose (`design/data-model.md`).

## Prompt delivery

### `prompt_queue.py`

The persistent prompt queue.

- The durable message store: states, strict head-of-line, revisions, sender provenance, correlation, relay depth.
- Typed operations: enqueue, edit, arm, move, cancel, delete, retarget, schedule, send-next.
- Content-erasing delete tombstones and delivery constraints.
- Auto-policy and proving-counter tables, including the lapse-audit columns and the two derived reads behind them (`pending_message_count`, `open_reply_windows`).
- The arming floor, and the two forms of receiver authorization that pass it: the standing `accept_agent_messages` grant for an `agent` sender, and `solicited_by` naming the target's own request for anything else. Recorded on the row (schema version 6) rather than inferred, because a message that arrived armed from a non-human sender has to be able to name what asked for it.
- Event-driven stranding plus startup reconcile, and the delivery audit.
- Seed-prompt staging (`stage_seed_argv`).
- The **display projection** of a readiness verdict: `protected_reasons` (which reasons no per-send confirmation can override) and `delivery_summary` (the compact payload `GET /api/sessions`, the queue's target view, and the readiness watcher all publish).
  It lives here rather than in `delivery_readiness.py` because the protected set is a *queue* rule - it is exactly what `send_next` refuses with, and one implementation is what keeps a surface from promising an override the daemon will not honour - and because `delivery_readiness.py` stays a leaf module.

**Not:** *when* an automatic send happens (`auto_delivery.py`), who may address whom (`agent_messaging.py`), the classification itself (`delivery_readiness.py` - this projects a verdict, it never reaches one), PTY ownership (delivery writes go through the injected operator-input helper), or aiohttp handlers.

### `auto_delivery.py`

The gate on automatic sends: the install master, a default-on bounded grant per live agent run, conversation opt-out, run binding, expiry, the consecutive cap, the stability window over `delivery_state`, quiet hours, the persisted emergency pause, the expiry sweep, and proving-period counters with `promotion_status`.
Also the idle lapse and its audit (`_lapse_session`, `lapse_record`) and the bounded reply window that is the single thing allowed to hold that lapse off (`reply_windows`).
The reply window is deliberately *evidence*, not a second authority: it changes whether a grant lapses and nothing else, and it is capped by the exchange's own end - `max_thread_turns` for a message, a terminal request for a land - so nothing can renew it past the conversation that justifies it.
It draws that evidence from two sources: the queue's own `open_reply_windows`, and a second one registered through `set_solicited_requests`.
Since 2026-08-30 the second is a `merge_solicited_sources` composition of the settle-watch service's `origin_windows` and the land queue's, later sources winning for a session holding both.
Solicited entries may carry their own `kind` and `expires_at` (a watch is bounded by its own deadline, not the reply-window span); an entry naming neither gets the land defaults every pre-plural entry had.
The sources are callables rather than imports so nothing here knows what a land request or a watch is, and a source that raises is absent rather than fatal.
The consecutive-send cap's recovery gained a third evidence kind the same day: `PromptQueueStore.credit_auto_attention` is also called from the MCP dispatch (throttled per session), because an authenticated tool call is the same "somebody is reading the deliveries" fact a written reply is.

**Not:** delivery itself - it calls `send_next` and cannot pass `confirm` - readiness evaluation, relay policy or the thread model it borrows the cap from (`agent_messaging.py`), or HTTP.

### `readiness_watch.py`

The display-only sibling of the controller above: a one-second loop that announces when a session's delivery readiness *changes*, so the surfaces that show it stop depending on an unrelated event happening to trigger a fleet refresh.

- Why a loop and not an event subscriber: the clock-driven transitions (`operator_quiet` becoming true, a debounce elapsing, lifecycle evidence ageing out) have no event and never will, because each is an absence or a threshold rather than something that happened.
- Edge-triggered on `(state, reasons)`, with a first sighting establishing the baseline silently - the client's REST load already carries that verdict.
- Emitted through `EventBus.emit_transient`, so a per-second event type cannot evict the capped `events` history.
- Gated on an `/events` subscriber existing at all, so a headless daemon skips the pass including its real cost.
- Scoped to sessions with an attached terminal or a pending queue item: someone is looking, or someone is waiting on this exact verdict.
- Evaluates with `adopt=False` and `record_metrics=False`. The first is a correctness requirement, not tuning: `evaluate` mutates, and one of its adoptions snapshots the live screen as the completion baseline, so an observer on a timer could otherwise decide the verdict it is meant to be observing. The second keeps the auto-delivery promotion statistics describing delivery attempts.

**Not:** classification (`delivery_readiness.py`), the display payload's shape (`prompt_queue.delivery_summary`), anything that writes to a PTY, or HTTP - it publishes on the bus and the `/events` handler forwards.

### `agent_messaging.py`

Relay policy for agent-authored messages: requested Project scope re-resolved through `project_scope.py`, size, per-origin budget, target backlog, propagation depth, per-thread turn budget, ring detection, kill switch, and expiry.
The four rate bounds and the two interject bounds resolve through `config.agent_message_bounds()` - the configured values while `agent_message_limits_enabled` is on, fixed backstop ceilings while it is off (the default since 2026-08-25) - and `auto_delivery.py`'s reply window reads its thread cap through the same helper so the two modes cannot disagree.
Also the `dry_run` projection (every bound run, nothing staged, no budget spent), sender-attributed `revoke` of a still-undelivered message, sender-only message and request status, inert `spawn_request` drafts, and the Fleet Queue projection over messages plus targetless spawn approvals and drafted `control_request` interrupt and end rows.

**Not:** delivery, spawning (approval is a `routes/observations.py` human act), session-control authority (`session_control.py`), or MCP protocol.

## Scheduled runs

A schedule is a *user-authored deferred press of a button the author could have pressed themselves*.
So it goes through the ordinary spawn path, the ordinary resume path (`session_resume.py`, shared with the History Resume button), and the ordinary prompt queue, and never grows a second authority.
A resume names its conversation by history *run* id rather than session id, because a session is exactly the thing that drifts.
The definitions stay machine-local, because a schedule committed to a repository would arm itself in every clone and worktree.

### `schedules.py`

Pure trigger arithmetic and validation.

- The five-field cron parser (Vixie day-field union), plus interval and one-off triggers.
- The wall-clock abstraction with its host-local and named-IANA implementations.
- The two decided DST edges: a spring-forward gap fires once, a fall-back repeat fires once.
- The `spawn`/`resume` action and its three target kinds.
- Bounds on every field, including the shorter `once` horizon a resume gets, because the harnesses prune their own transcripts.
- The typed `ScheduleError` with its machine code and field map.

**Not:** storage, spawning, resuming, permission, or any I/O.

### `schedule_store.py`

Machine-local persistence for schedule definitions and run history on the shared single-thread store pattern, the unique `(schedule_id, fire_key)` claim that makes a fire idempotent across a daemon restart, revision-checked replacement, and run-history retention.

**Not:** when to fire, whether a fire is allowed, or what a fire does.

### `scheduler.py`

The sweep and every fire-time guard.

- Advance-the-window-first, then claim-then-act.
- The install switch and the per-Project `scheduled_runs` opt-in, checked at fire time rather than write time.
- The missed-window policy: `catch_up` replays once, otherwise `missed`.
- Overlap, the per-schedule daily cap, and the install-wide concurrency ceiling.
- The resume path's target resolution and context ceiling, and the transient-versus-permanent classification of a refused resume.
- Message staging, and the outcome and notification record.

**Not:** the spawn itself (injected `_spawn_from_body`), the resume itself (`session_resume.resume_run`), delivery (injected `PromptQueueService.enqueue`), trigger arithmetic (`schedules.py`), or HTTP.
