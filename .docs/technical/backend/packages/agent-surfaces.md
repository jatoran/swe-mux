# Backend: MCP, queues, messaging, and scheduled runs

Index: `../packages.md`.
Design: `../../../design/features/mux-mcp.md`, `../../../design/features/prompt-queue.md`, `../../../design/features/auto-delivery.md`, `../../../design/features/agent-messaging.md`, `../../../design/features/scheduled-runs.md`, `../../../design/features/observations.md`.

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

Writes, all thin callers into services that hold the authority: `notify`, `request_spawn`, `run_action`, `interrupt`, `end_session`, and `request_land` - whose worktree is read from the caller's own live cwd rather than accepted as an argument.

Also token-derived identity, exact display-name resolution, cursors, output budgets, redaction, and content-free per-tool result diagnostics.

**Not:** history indexing and ranking (`history.py`), relay policy and queue and request storage (`agent_messaging.py` and existing services), session-control authority and bounds (`session_control.py`), land authority and bounds (`land_queue.py`), title generation (read from `automation_store.py`), delivery, PTY writes, spawn, or aiohttp handlers (`server.py`).

### `mcp_contract.py`

The shared closed read and write tool declarations, and the generated Claude read-permission names.

**Not:** tool implementation, transport, or write approval.

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

**Not:** the interrupt and graceful-end PTY operations themselves and the daemon-owner check (both in `server.py`), MCP transport (`mcp.py`), or observation storage (`project_files.py`).

## Prompt delivery

### `prompt_queue.py`

The persistent prompt queue.

- The durable message store: states, strict head-of-line, revisions, sender provenance, correlation, relay depth.
- Typed operations: enqueue, edit, arm, move, cancel, delete, retarget, schedule, send-next.
- Content-erasing delete tombstones and delivery constraints.
- Auto-policy and proving-counter tables.
- Event-driven stranding plus startup reconcile, and the delivery audit.
- Seed-prompt staging (`stage_seed_argv`).

**Not:** *when* an automatic send happens (`auto_delivery.py`), who may address whom (`agent_messaging.py`), PTY ownership (delivery writes go through the injected operator-input helper), or aiohttp handlers.

### `auto_delivery.py`

The gate on automatic sends: the install master, a default-on bounded grant per live agent run, conversation opt-out, run binding, expiry, the consecutive cap, the stability window over `delivery_state`, quiet hours, the persisted emergency pause, the expiry sweep, and proving-period counters with `promotion_status`.

**Not:** delivery itself - it calls `send_next` and cannot pass `confirm` - readiness evaluation, or HTTP.

### `agent_messaging.py`

Relay policy for agent-authored messages: requested Project scope re-resolved through `project_scope.py`, size, per-origin budget, target backlog, propagation depth, per-thread turn budget, ring detection, kill switch, and expiry.
Also sender-only message and request status, inert `spawn_request` drafts, and the Fleet Queue projection over messages plus targetless spawn approvals and drafted `control_request` interrupt and end rows.

**Not:** delivery, spawning (approval is a `server.py` human act), session-control authority (`session_control.py`), or MCP protocol.

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
