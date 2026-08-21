# Data model

## Ownership

- `ProjectRecord`: stable ID, name, canonical root, optional Group, position, layout,
  layout revision, registration time, shared explicit-use time, nullable Project-local `git_compare_ref`, and default backend/profile. A deprecated resource-presentation field may
  still be loaded from older records but has no browser behavior.
  `default_profile_id` is the shell launch profile; `default_agent_profiles` is one launch
  profile id per harness, stored as the JSON column `default_agent_profiles_json`. One column
  rather than one per harness, because the harness set is a registry and a per-harness schema
  would make adding a harness a database migration (`features/launch-profiles.md`).
- `ProjectGroupRecord`: stable ID, name, and position. It has no behavioral ownership.
- `SessionRecord.project_id`: immutable canonical Project ownership. `cwd`/`spawn_cwd` default
  to the Project root and may be a containment-checked subdirectory of it; validated runtime cwd
  remains telemetry. `spawn_env` retains a task step's declared environment beside them, so a
  relaunch reproduces the spawn even after a daemon restart adopts the record.
- `SessionRecord.spawn_backend` and `spawn_native_session_id`: immutable root-process identity.
  The compatibility `backend`/`native_session_id` pair may describe a promoted agent only when
  the root is a shell.
- `SessionRecord.agent_run_id`: **the scope every run-owned record is keyed by** — history rows,
  Tier 0 facts, annotations, queue bindings, auto-delivery grants, telemetry. One run is one
  provider conversation on one PTY. `agent_run_seq` counts the in-CLI conversation replacements
  (`/clear`, `/new`) that produced it; 0 is the run the session spawned or was promoted with.
  A root agent's run id is otherwise pinned to its session id, and adoption repairs any other
  value as misattribution, so `agent_run_seq > 0` is what marks a differing id as the daemon's
  own successor run rather than corruption.
- `SessionRecord.agent_loaded_at`: the start of the current Claude or Codex process generation.
  It is set at direct agent spawn or shell-to-agent promotion, survives conversation rollover and daemon adoption, and clears on demotion.
  Agent Environment and skill drift compare current file mtimes with this field rather than with `agent_run_started_at`.
- `SessionRecord.observation_stale_since`: volatile. Set when the followed transcript is
  provably no longer this PTY's conversation and no successor could be corroborated; it
  revokes the transcript's authority over hooks and hard-blocks delivery.
- `SessionRecord.turn_epoch`, `active_turn_id`, and `turn_started_at` identify the open root-turn generation and its clock.
  The epoch increases on every root open, the opaque ID is provider-native or mux-synthesized when available, and both the ID and start timestamp clear on terminal evidence.
  A mismatched terminal ID is diagnosable stale evidence and cannot close a newer generation.
- `SessionRecord.running_work_since`: volatile, run-scoped. The start of the current stretch of
  running work, latched when a `RUNNING_ACTIVITY_KINDS` annotation opens and anchored to the turn
  that dispatched it. It exists because a harness that hands off to background agents clears
  `turn_started_at` and freezes `last_turn_ms`, leaving nothing on the record that dates a request
  still in flight. Released only when a root turn closes with nothing running, so it spans both the
  hand-off and the gaps between a workflow's phases.
- `SessionRecord.interrupt_pending_at` and `interrupt_pending_source` expose operator interrupt intent separately from lifecycle proof.
  They freeze the user-visible timer and status wording while delivery remains blocked, clear on terminal evidence or a new root generation, and expire when an interrupt cannot be confirmed.
- `SessionRecord.requested_end_reason`: the end reason to persist when this session terminates,
  set by a deliberate Phase 7.6 end operation before it sends the exit sequence.
  It lets an agent-initiated graceful end record `agent_ended` even when the CLI exits on its
  own and the ordinary process-exit path is what marks the record; `None` leaves the terminal
  path to classify the exit as it always has (`features/sessions.md`).
  Round-tripped through the record snapshot, so supervisor adoption preserves it across a daemon restart.
- `SessionRecord.standing_activity`: the standing-engagement annotation axis — a list of
  `StandingActivity {kind: loop|cron|background_tasks|subagents, source, evidence, since,
  expires_at, count, detail}`. Not states: SessionState, `awaiting_reason`, and delivery are
  untouched. Run-scoped (cleared wherever observation identity resets — rollover, heal,
  promote, demote, session end) and TTL'd where the evidence implies an expiry. Serialized
  in the record snapshot, so supervisor adoption round-trips it across daemon restarts;
  drift-tolerant like the rest of `from_snapshot` (unknown keys dropped, malformed
  annotations skipped). Contract and detection sources: `features/status-detection.md`.
- `SessionRecord.cold` and its `cold_since` / `cold_reason` / `cold_terminal_at` /
  `cold_terminal_skipped` companions: this session was rebuilt from durable recovery data rather
  than observed, because its process died with a daemon that never recorded how it ended.
  Deliberately a flag beside `state="crashed"` rather than a new `SessionState`: every consumer
  that gates on `state in {"exited","crashed"}` must exclude a cold session, and the flag makes
  that structural instead of an audit. `cold_terminal_at` bounds how stale the replayed screen is
  and is absent when there is none; `cold_terminal_skipped` names why bytes were never kept, which
  is what lets a deliberately empty pane say so (`features/session-recovery.md`).
- `SessionRecord.approval_policy`: the live auto-approval grant —
  `ApprovalPolicy {mode: wait|allowlisted|allow_all, run_id, expires_at, granted_at, set_by,
  rules, auto_approved, max_auto, last_decision_at, last_request, floor_deferred}`.
  Keyed on `agent_run_id` and always carrying an expiry for a non-`wait` mode, both checked at
  read time by `effective_mode` rather than swept: a sweep that does not run leaves authority
  standing. `rules` is the Project's allowlist as it stood when the grant was made, so the
  decision needs no file I/O on the agent's blocked turn and an edit to the committed file
  cannot widen a grant already standing. On the record rather than in SQLite so it rides the
  supervisor snapshot through a session-preserving restart, which is routine here; restored
  `allowlisted` with no rules drops to `wait` rather than becoming an empty allowlist. Full
  contract: `features/approvals.md`.
- Git `repository_id`, project scope, root, and repository group fields are derived metadata,
  separate from canonical Project ownership.

## Core SQLite records

- `projects` and `project_groups`: sidebar ownership and organization. Both carry a normalized
  `position`; `projects.created_at` dates the registration, and `0` means unknown — databases
  written before the column are backfilled from the earliest session ever spawned in the
  Project, and one that never ran a session keeps `0` rather than being dated at upgrade time.
  `projects.last_used_at` is the shared explicit prompt-submit/session-start recency stamp used by Recently used sorting; `0` means unmeasured.
  Existing databases seed it from the latest non-imported session start because older exact prompt-submit evidence does not exist.
  General `last_activity` remains derived per request from `history` and is not a recency-sort input.
  `projects.git_compare_ref` is a nullable exact ref override for Git review display, migrated additively for existing databases and preserved by unrelated Project patches.
  It is intentionally outside `.swe-mux/config.toml`, so changing the display comparison does not dirty the repository.
  `projects.deleted_at` is nullable.
  Active registry reads exclude rows with a deletion timestamp, while History joins retain them as stable ownership tombstones.
  Re-registering the same canonical root clears `deleted_at` instead of allocating a new Project ID.
- `history`: durable agent-run lifecycle, canonical `project_id`, legacy terminal `note_id`
  retained for note migration provenance,
  native identity, transcript pointer, derived Git metadata, context/model telemetry, explicit
  compaction summary, exit state, materialized chronological native start/final conversational message
  time and role, plus source mtime/size watermarks for bounded timestamp-summary refreshes.
- `git_provenance`: durable evidence connecting a full commit OID to a session, optional agent run, Project, and exact checkout root.
  It copies parent OIDs, subject, Git commit time, previous HEAD, relationship (`created|rewrote|merged|observed|contributed|authored_branch`), confidence (`exact|correlated|ambiguous`), ambiguity flag, evidence source, source event sequence, optional tool-call id, and first/latest observation times.
  `role` (`committer|integrator|contributor|branch_author|observer`) records what the session did, which is a different question from what the reference did, so one commit legitimately holds one committer row and several contributor rows.
  A merge commit is the one shape with more than one true answer, and it holds one `integrator` row for the session that ran the merge and one `branch_author` row per session the ledger already credits for the commits on the merge's own side.
  `committer` and `integrator` are mutually exclusive for one commit and rank identically, so a row recorded before the distinction existed reclassifies in place through the ordinary upsert rather than needing a migration.
  A `branch_author` row carries no contributed paths by design: it says whose branch the merge carries, never that those bytes are in the merge.
  `match_method` names how the attribution was made and `contributed_paths_json` holds the commit files that session's observed writes account for, bounded to 200 paths.
  Existing rows migrate additively to `observer` with no method, which is exactly what they recorded; re-attribution is the explicit backfill's job, never a startup rewrite.
  The same rule covers the integrator/branch-author split: rows written before it keep saying `committer` until the backfill is run, because a startup rewrite of a durable ledger is not something a version bump may do silently.
  The uniqueness key is `(session_id, agent_run_id, worktree_root, commit_oid)` with shell runs represented by the empty run id.
  `worktree_root` is stored in one canonical spelling (forward slashes, no trailing separator) because it is part of that key: Git prints `D:/PROJECTS/x` and `pathlib` prints `D:\PROJECTS\x` for one directory, and both spellings made the daemon's row and the backfill's row for one session and one commit into two rows.
  Opening a database written before that rule collapses the duplicates in favour of the stronger row.
  An internal evidence rank permits only equal or stronger observations to replace classification fields while preserving the earliest observation time.
  Contributed paths are the one exception: they are evidence rather than classification, so they accumulate — an empty set never replaces a populated one, and a populated set fills a row that has none at any rank.
  Explicit transcript backfills use the same ranked upsert in batches of at most 1,000 rows, so rerunning an import cannot duplicate or weaken existing live evidence.
  `retracted_at` and `retracted_reason` are the ledger's only weakening operation.
  The ranked upsert can promote a row but never withdraw one, because "this session had nothing to do with it" is not a stronger claim than the one it replaces; without retraction a row that turned out to record occupancy had no way out.
  Retraction is applied from an explicit id list produced by a pass that examined each row, never a predicate evaluated at read time, and reads exclude retracted rows unless asked for them.
  The upsert clears a retraction only for evidence strictly stronger than what was withdrawn, so re-observing the same thing cannot undo a repair while a contributor match proving the session's bytes are in the commit does; the repair pass can additionally restore its own verdicts, because a reclassification offers the same strength of evidence and a different answer.
  Project removal retains Project and provenance rows as a tombstoned historical identity, and explicit History-entry deletion removes rows for that agent run.
- `git_ref_moves`: what a *checkout's* reference did, which is a different question from what any session did.
  It records the Project, canonical checkout root, the commit moved to, the previous HEAD, the classified kind (`created|merged|fast_forward|rebased|reset|unknown`), how many commits the reference's own first-parent line gained, how many of those the move authored, the tip's subject and Git commit time, and first/latest observation times, keyed uniquely by `(worktree_root, commit_oid, previous_head)`.
  It exists because every session attached to a checkout watches the same reference move, so recording the move per session wrote one row each for sessions that had nothing to do with the commits involved — and a landing fast-forward, which authors nothing at all, wrote the most of them.
  A move is classified from the repository rather than accumulated from sightings, so the newest reading replaces the previous one outright; taking a maximum of the counts pinned a miscount in place where a later classifier fix could never displace it.
- `history_messages`: derived role-aware user/assistant text, provider-native optional timestamp, and nullable materialized `ts_epoch` used for indexed message-time boundaries.
  `history_messages_fts` provides Unicode token-prefix lookup and `history_messages_trigram` provides case-insensitive literal substring lookup.
  Both FTS5 tables are external-content derivatives of `history_messages` and stay synchronized by triggers.
  `history_transcript_index` stores source mtime/size, parser version, message count, and index time so empty/unchanged transcripts remain incremental and MCP hit watermarks can reject stale pointers.
- `events`: monotonically sequenced mux events.
- `process_evidence`: bounded PID+creation-time fingerprints, owner/lineage/Job Object
  evidence, stable attribution version/source and confirmation times, mutable state/reason/confidence, and exit or ownership-rejection evidence; command text is never stored.
  Version 1 is legacy root-relative attribution; version 2 proves current per-edge causal validation or live Job Object membership.
- `quota_samples` and `quota_sample_rollups`: durable raw observations and daily retention summaries keyed by local account slot and verified provider account identity.
  `quota_reset_events` retains reset/correlation evidence plus nullable durable user
  review (`manual_usage | discarded`, timestamp); `quota_attributions` retains correlation
  estimates.
- `notification_decisions`: append-only, content-free notification planning and delivery evidence.
  Each row carries a candidate id, decision time, source event time and sequence, session, event type, category, stage, optional device profile, planner verdict, actual outcome, and stable reason code.
  It never stores notification text, terminal content, subscription endpoints, settings payloads, or credentials.
  Rows follow operational-telemetry retention.
- `context_compactions`, `tool_events`, and `transcript_telemetry_coverage`: deduplicated
  explicit provider evidence plus versioned parser coverage. These are rebuildable for one
  session after a proven identity repair; its process evidence is retained and re-attributed.
- `status_timeline`: the durable per-session detection timeline — every transition-ledger
  entry (transitions *and* the non-transition kinds: `watchdog_recovery`,
  `standing_activity`, `cli_state`, `layer_reading`, `screen_classifier_blind`,
  `foreign_conversation_hook_ignored`, `transition_refused`, `reopen_blocked`,
  `observer_fault`, `approval_auto_decision`, `approval_mode_set`, `approval_mode_revoked`,
  hook-spool records) keyed `(session_id, agent_run_id, seq)` with `ts`,
  `kind`, and the entry payload verbatim as JSON. Run-keyed so a conversation rollover's
  successor rows never mix with its predecessor's. Written behind the in-memory rings by a
  batched sink (never on the transition path), pruned by
  `status_timeline_retention_days` (default 30), and queried by time range for
  post-mortems (`features/status-detection.md` § durable timeline,
  `development/STATUS_INCIDENT_RUNBOOK.md`).
- `session_recovery`: one row per session this daemon has run, holding the redacted metadata blob
  it can be rebuilt from and an **open marker**. `closed_at IS NULL` means nobody was able to
  record how that session ended, which is the whole signal a cold restore reads. Credentials are
  never persisted (`hook_secret`, `mcp_token` are dropped), terminal bytes live in files rather
  than in this table, and rows are bounded by `session_recovery_retention_days` (closed) and
  `session_recovery_max_sessions` (open). See `features/session-recovery.md`.
- `tier0_facts`: deterministic no-model fact capture (file writes, commands, tests, git, tools)
  with `content_hash`, canonical `fingerprint`, the owning `agent_run_id`/`project_id`, and a
  `source_seq` pointer into the event log. Test results additionally carry structured
  pass/fail counts and failing-test ids inside the bounded detail. Command text is never
  stored beyond bounded detail, and that detail is bounded per value so the row always
  re-parses. Per-project opt-in and gated; see `features/tier0-facts.md`.
- Project context has no SQLite entity.
  Its source of truth is the bounded user-owned `<project>/.swe-mux/project-context.md` file with content-derived revisions (`features/project-card.md`).
- `project_cards` is a retained legacy table from the retired generated-card implementation.
  Active runtime code never reads, writes, refreshes, or spends against it.
- `scan_timeline_runs`: the current authorization and delta cursor for one `agent_run_id`.
  It records the persistent terminal `session_id`, Project, enabled/disabled timestamps, last
  scan time, and last source timestamp.
  A successor conversation has another primary key and therefore starts disabled.
  Backfilled historical records update the cursor with a monotonic maximum and cannot move live delta capture backwards.
- `scan_timeline_records`: append-only structured Tier 1 records keyed to both `session_id` and
  `agent_run_id`, with the bounded source interval, trigger, validated semantic JSON, transcript
  input hash, requested/resolved model, generation, token counts, cost, and creation time.
  Transcript text remains in the authoritative provider transcript.
  Reads order records by source start time and then creation time, so records added by a later full-session scan appear at their historical position.
- `scan_timeline_boundaries`: explicit predecessor-to-successor run boundaries for one persistent
  session, including rollover reason and time.
- `scan_timeline_metrics`: one bounded aggregate row measuring record reads, source rehydrations,
  and their derived rate.
  It is a Tier 2 instrument with no Tier 2 consumer yet, and its only caller always rehydrates,
  so the rate is structurally 1.0 and is diagnostic rather than a headline.
- `scan_timeline_backfills`: one row per full-session scan job, keyed by `agent_run_id`, holding
  its state, chunk counts (processed, total, created, failed, skipped), reason, and timestamps.
  A job is multi-minute and its outcome is the only record of which parts of a conversation were
  reached, so it cannot live in daemon memory: a restart reported `idle` for a job that had
  actually stopped half way.
  Rows left at `running` by a dead daemon are closed out as `partial` at startup.
- `automation_observer_calls.response_excerpt`: a bounded copy of what the model returned, written
  only when a response is refused or repaired.
  Without it, "the model returned something invalid" could never be turned into "*this* is what it
  returned".
- `automation_budget_ledger` additionally carries nullable `project_id` and `agent_run_id` so a
  continuously costing substrate can enforce and display Project and run budgets even when a
  failed provider call creates no semantic record.
  A call the provider billed for reaches the ledger even when local validation refused its output;
  the scan path previously discarded that usage entirely.
  It also carries `cached_tokens` (schema 9): the prompt tokens the provider served from its
  cache, a subset of `input_tokens` and never added to it, backfilled to 0 on an existing
  database because every pre-migration row was billed by a request that carried no cache
  breakpoint.
  And `cost_known` (schema 11): whether the provider reported what this call cost.
  A bring-your-own OpenAI-compatible endpoint reports no `usage.cost`, and an absent cost is
  unknown rather than zero — recorded as `$0.00` it would leave every dollar figure, and every
  dollar *cap* reading the same rows, looking enforced while approaching nothing.
  The stored `cost_usd` stays 0 for such a row so existing `SUM(cost_usd)` keeps meaning "the
  cost we know about", and `spend()` / `spend_breakdown()` carry `unpriced_calls` so a total can
  be read as the floor it is (`design/features/budgets.md`).
  A cost that arrives late through `/generation` clears the flag as it fills the figure.
  Existing rows backfill to `1`, the **opposite** direction to `cached_tokens` and for the same
  reason — it is the true reading: every pre-migration row went to OpenRouter, which prices every
  completion, so its zero means free.
- `llm_provider_verification` (schema 10): one row per configured model provider, holding the
  completion that proved it — `fingerprint`, `base_url`, `model`, `resolved_model`, a bounded
  `sample` of the reply, `latency_ms`, and `verified_at`.
  `fingerprint` is a digest of the whole endpoint triple (base URL, model, key) and is never
  compared as a string by a caller: readers recompute the live fingerprint and compare, which is
  what makes editing the endpoint un-verify it *by construction* rather than by every write path
  remembering to — including an edit made by hand in `config.toml` while the daemon was down.
  Nothing key-shaped is recoverable from it, and this table holds no other copy of the secret.
  `sample` is kept because the point of verifying is that a person reads what came back: an
  endpoint answering with an empty string or a chat template's own scaffolding is reachable and
  unusable at once.
  One row per provider rather than a history — the question is whether the endpoint *as it
  stands* is proven, and a log of superseded fingerprints would only make that harder to read.
- `clipboard_entries`: the clipboard-history ring — copied text with a unique `content_hash`
  (re-copying promotes rather than duplicates), character/line counts, provenance
  (`source`, `session_id`, `project_id`, `device`), and `pinned`. **Unlike every other table here
  it is normally empty**: the ring is authoritative in memory and this table is written only while
  `clipboard_history_persist` is on. Turning that setting off — or turning history off entirely —
  deletes the rows. Secret-shaped and oversized copies never become rows at all; see
  `features/ui.md`.
- `queue_messages`: the persistent manual prompt queue (`features/prompt-queue.md`).
  Keyed to `target_session_id` + the bound `target_agent_run_id` (nullable until the
  target's first run binds, then never re-bound), with a target label/backend/project
  snapshot for stranded queues, gap-free `position` per target, state
  (`draft|armed|blocked|delivering|sent|failed|cancelled|stranded|deleted`), body, `revision`,
  and the provenance-rich sender model — `sender_kind`
  (`user|remote_user|agent|rule|queue_draft`, derived from the transport or the caller's
  MCP token, never claimed), `sender_id`/`sender_label`, `origin_session_id`,
  `correlation_id` (partial-unique per sender: a retried send returns the original row),
  `thread_id` (the relay exchange, assigned by the daemon at the head of a chain and
  inherited by every message continuing it — deliberately *not* the correlation id, which is
  a per-sender idempotency key and would dedup a sender's second message in one exchange),
  `chain_depth` (distinct sessions that have spoken in the thread),
  `solicited_by` (the target's *own* request this message is the bounded answer to, and the
  only thing that lets a non-human sender other than `agent` be staged `armed` — recorded
  rather than derived, because arming is never the sender's claim and a row that arrived
  armed has to be able to name what asked for it; `features/agent-messaging.md`),
  `origin_json` (relay path
  with the most recent sender last / sender Project label and whether the message crossed a
  Project / rule id / Tier 0 fact fingerprints),
  `payload_json` (typed action payload for control-plane drafts), `constraints_json`
  (`not_before`, `expires_at`, `delivery` — `now` for an item that asked to land in a running
  turn; the `when_idle` default is never persisted, so an item without the key means what
  every item meant before the mode existed) — plus blocked reasons, stranded reason, `cancel_kind`
  (`cancelled|skipped|revoked|expired`), `retargeted_from_json`, and lifecycle timestamps
  including `deleted_at`. Delete blanks the body and action-bearing JSON immediately, hides
  the row from every read surface, and retains the content-free row until normal retention
  so sender correlation retries resolve to the deleted identity instead of recreating it.
- `queue_deliveries`: the delivery audit — per attempt: revision, target identity,
  readiness state + reasons, explicit-confirmation flag, `interjected` (the write landed in a
  running turn — not derivable from the other two, since an interject is neither `safe` nor a
  human override, and it is the one delivery shape that had to be separately authorized),
  `initiator` (`user|auto` — who pressed send), outcome (`pending|sent|refused|failed`), error, byte count, and a
  partial-unique `idempotency_key` (a repeated key replays the recorded outcome instead of
  delivering twice). Deliberately carries no prompt text; bodies live in `queue_messages`
  only.
- `queue_auto_policy` / `queue_auto_counters` (Phase 5, `features/auto-delivery.md`):
  runtime auto-delivery state, deliberately not config — the default-on per-conversation grant
  or override (bound
  `agent_run_id`, `expires_at`, `max_sends`/`sends_used`, `accept_agent_messages`,
  `accept_agent_interjections`, `disabled_reason`), one reserved `*` row for the emergency
  pause, and the persisted
  proving-period counters (`auto_sent`, `auto_refused`, `auto_failed`, `unsafe_reported`,
  `auto_lapsed`, `proving_since`). The store carries a v1→v2 migration: the Phase 5 columns are added in
  place, because `CREATE TABLE IF NOT EXISTS` would otherwise reach only fresh databases.
  `accept_agent_messages` and `accept_agent_interjections` both keep a column default of `0`
  while the conversation-default grant writes `1` explicitly. A column default would also land
  on rows inserted by an opt-out and on the reserved pause row, where "on" is not what was
  meant, so the per-run default belongs in the one code path that grants a run rather than in
  the DDL.
  That has one consequence a later column has to pay for: the defaults are written once, when
  a run is granted, so a column added afterwards reads as "opted out" on every conversation
  already live. `accept_agent_interjections` therefore carries a one-time backfill in the same
  migration, over exactly the rows that are `enabled=1 AND accept_agent_messages=1` - a grant
  that is on, whose run also accepts agent messages, and which said nothing about mid-turn ones
  because the concept did not exist. A disabled row, an opted-out row, and the pause row each
  said something and are left saying it. Without the backfill the capability looks dead on the
  whole fleet until each conversation happens to roll over, which from the operator's side is
  indistinguishable from it being broken.
  `disabled_reason` is read as well as written: the consecutive-send cap is the one disable
  reason that clears on evidence rather than on a human act, so the store recognizes it (the
  current string and the two earlier spellings) and restores the grant when a human sends by
  hand or the session writes a reply (`features/auto-delivery.md`).
  Schema v5 adds the **lapse audit** - `disabled_at`, `lapse_idle_seconds`,
  `lapse_window_minutes`, `lapse_pending` - written only by the idle lapse and cleared by every
  other write to the row. The reason it is stored rather than derived is that a lapse is the
  only disable with no act behind it: an opt-out, a failed delivery, and a spent send budget
  are each explained by the thing that happened, while a lapse leaves nothing but a sentence,
  and the operator asking whether the window is too short and the sender asking why its message
  never moved both need the numbers that were true at that moment. All four columns are
  nullable and are deliberately **not** backfilled: a row that lapsed before they existed lost
  the evidence, and inventing a zero for it would read as "lapsed the instant it was granted".
  The counterpart bound has no column at all: whether an exchange is holding a lapse off is
  derived from `queue_messages` (the caller's most recent `sent` agent message, inside the
  reply window, in a thread that still has budget), like thread identity and chain depth, so no
  second table can disagree with the audit trail. The second source of that evidence is derived
  the same way from a different table - a live `land_requests` row this session originated
  (`features/land-queue.md`) - and neither is stored, for the same reason.
- `schedules` / `schedule_runs` (`features/scheduled-runs.md`): the definitions that start or
  reopen an agent session on their own, and their run history.
  A definition is a deferred `SpawnRequest` (`project_id`, `backend`, `profile_id`, `cwd`,
  `session_name`, `prompt`) plus a trigger (`trigger_kind` `cron|interval|once` with `cron`,
  `interval_seconds`, `run_at`, `timezone`), the policies (`catch_up`, `overlap`,
  `daily_run_cap`), `follow_ups_json` (the messages pre-queued behind the seed prompt), a
  `revision` for the same optimistic-concurrency contract the Project files use, and the
  cached trigger state (`next_fire_at`, `last_fire_at`, `last_session_id`, `last_outcome`).
  `action` (`spawn|resume`, schema 2) selects which button is being deferred.
  A `resume` adds `target_run_id` - a **history run id, never a session id**, because a session
  is exactly the thing that drifts while agent history rows are not pruned - plus `target_kind`
  (`run|latest_of_session|fork_point`), `target_cut_message_id` and `target_cut_mode` for a
  pinned fork, and `context_ceiling_pct` for a rolling continuation.
  A fork stores the message it cuts at rather than a byte offset, so a conversation that moved
  past that message is refused by name instead of cut somewhere plausible.
  `backend`, `profile_id` and `cwd` stay empty on a `resume` and are rejected at write time:
  the conversation's history row and its adapter already fix all three.
  **These rows are deliberately machine-local rather than portable Project config**: a
  schedule committed to a repository would arm itself in every clone and worktree, the same
  boundary Project Action trust draws.
  `schedule_runs` records one row per occurrence with `fire_key`, `due_at`, `outcome`
  (`started|spawned|skipped|failed|missed`), `reason`, `session_id`, and `origin`
  (`timer|manual`).
  `(schedule_id, fire_key)` is **unique, and that index is the idempotency mechanism**: the
  row is inserted before the spawn, so a daemon that dies mid-fire cannot start the same
  occurrence twice on restart.
- `land_requests` / `land_events` (`features/land-queue.md`, `land-queue.sqlite3`): one row per
  branch asked to land, and its per-step audit trail. A request carries the two checkout roots,
  the branch and the OID it was requested at, the trunk ref, the origin (operator or agent, with
  the requesting session and run), its state, the OID that passed verification, and the trunk's
  before and after positions.
  **Machine-local for the same reason schedules are**: a queue committed to a repository would
  arm itself in every clone and worktree of it, while the `land_queue` opt-in stays portable and
  is inert on its own.
  Two **partial unique indexes carry the design rather than merely guarding it**.
  `land_requests_active`, unique on `(project_root, branch)` over the live states, makes enqueue
  itself the claim, so an agent asking twice cannot create a second pipeline over one worktree.
  `land_requests_inflight`, unique on `project_root` over the running states, is what makes
  "one land at a time per trunk" a property of the schema: two workers cannot both mark a step
  running against one primary checkout even if both believe they should.
  A row left in a step state by a daemon that died mid-flight returns to `queued` on restart
  rather than resuming, because every step re-checks the repository from scratch and guessing
  how far it got is the part that is not safe.
  `armed_replies` counts the unattended handbacks this one request has spent, capped at one and
  claimed by a conditional `UPDATE`: the consent a `request_land` carries is for the answer to
  *that* request, so the cap is denominated per request rather than left to the state machine
  happening to allow one handback (`features/land-queue.md`).
  `land_events` records `step`, `outcome`, `reason`, and a detail payload per transition, and is
  the authoritative trail; a step is additionally mirrored into Tier 0 only when the request has
  an originating session to attribute it to.
  A request also carries `verify_gate` - `''`, `full`, `docs_only`, or `reused` - which is
  **which gate it ran**: `docs_only` is decided from the change set's paths against a closed
  documentation allowlist, `reused` means a queue-executed verdict already stood over this exact
  content. It is persisted rather than left in the event trail alone because a skipped gate must
  be visible wherever the row is drawn: neither a documentation-only land nor a reusing one ever
  enters `verifying`, so their states read identically to one that passed the full gate. `''`
  means "never classified" and is never collapsed into `full`, at either end.
  And it carries `kind` - `land` or `verify` - which decides **exactly one thing**: whether the
  fast-forward happens. A `verify` request runs every earlier step identically (which is what
  makes its verdict reusable by a land) and settles as `verified`, its own terminal state rather
  than `landed`, because nothing moved and this ledger's purpose is recording which OID moved
  what.
- `land_verify_plans` (same database): what a verification gate's steps were the last time these
  **exact bytes passed**, keyed by `(project_root, digest)` with the step names and the run's
  duration. It is what lets a running gate say "step 3 of 7" instead of an opaque "verifying",
  and it is a measurement rather than an estimate: a gate whose bytes changed has a different
  digest and therefore no plan, and a run that overruns its plan reports no total at all.
  **Only a passing run writes one.** A gate stopped by a failure announced a *prefix* of its
  steps, so recording that would predict a permanently shorter run and make every later gate read
  as nearly finished from its second step onward. A row is replaced rather than accumulated: the
  newest passing run of one set of bytes is the whole statement about them.
  The *live* reading of a gate that is running right now is deliberately **not** stored - it is a
  fact about a process, and a daemon restart returns the step to `queued` and re-runs it from
  scratch, so a persisted half-progress would describe a run that no longer exists.
- `land_verify_memos` (same database): a gate verdict that **already stands**, keyed by
  `(project_root, tree_oid, digest)` - the git tree the gate ran over and the digest of the
  command that ran, which are the whole of what decides a verdict. The tree rather than the
  commit, because a reconcile that merged an unchanged trunk produces a new commit over identical
  content, which is exactly the case a commit-keyed row would miss. A later request whose
  post-reconcile tree matches skips the gate and records the reuse with this key.
  **Only a run the queue executed writes one**, and there is no route that accepts a result from
  anywhere else: an agent's own shell run is self-reported, and a self-report can be produced by
  running modified bytes and restoring the approved file, so it proves nothing about the approved
  gate (`features/land-queue.md`). Reads are bounded by `land_verify_memo_seconds`, because a tree
  hash is a claim about *content* while the verdict also depends on the machine underneath -
  an installed dependency, a toolchain, an OS update, none of which changes the tree.
- **Session-settle watches have no table, deliberately** (`features/mux-mcp.md`). A watch is a
  promise made to one live conversation about another live session, and both halves of that are
  process state: it is dropped when the watcher session ends and when the watcher's conversation
  rolls over, so a durable row would outlive everything that gives it meaning and would have to be
  swept for exactly those two conditions. The thing a watch *produces* is durable - one ordinary
  `rule`-sender row in `queue_messages`, correlation-keyed on the watch id - which is where the
  audit trail belongs. What in-memory costs is that a daemon restart loses armed watches, and that
  is paid rather than hidden: the service flushes each open watch as a notice on its way out, so a
  restart reads as "your watch was dropped, re-arm it" instead of as a watch that never fired.
- `project_scopes`, `repo_groups`, and `artifacts`: derived Git/filesystem inventory retained
  for diagnostics and future Git expansion, not session containment.
- Git review patches and line annotations are not SQLite records.
  Patch snapshots, selected files, display choices, and annotation anchors live only in one open browser modal and disappear when it closes.
- `automation_annotations`: observer/rule/detector output. Anchored to `agent_run_id` **or**
  `project_id` — both nullable, at least one required — because a project-scoped detector
  (doc debt) has no run to attach to. Alongside the single `source_event_seq` it carries
  `evidence_json`, the *set* of Tier 0 facts a finding rests on: a loop's case is "this
  fingerprint repeated three times", which one pointer cannot express. `dedupe_key` makes a
  re-running detector idempotent — a conflicting write returns the existing row.
- `attention_items`: one row per ranked *incident*, not per finding
  (`features/attention-ranking.md`). `incident_key` is unique and is what folds several
  detectors reporting one underlying event into one row, so `kinds_json`, `evidence_json`, and
  `contributions` accumulate while `channel` and `budget_day` stay as first decided — the
  routing decision, and the interrupt slot it spent, belong to the incident rather than to
  each contributing finding. `suppressed_reason` records why an incident was demoted
  (`budget_exhausted`, `low_confidence`, `superseded_run`, `rule:<class>`); a demoted row is
  never deleted, because a held-back item the user cannot see is indistinguishable from a
  detector that broke. `budget_day` plus a non-null `delivered_at` is what the daily interrupt
  budget counts.
- `attention_feedback`: act/dismiss samples per incident class and channel, with the latency
  from surfacing to decision. It is the only input to mined demotion rules; acceptance of a
  rule lives in `automation_checkpoints` under `attention:rule:<class>:<channel>` and carries
  an expiry, so a standing suppression has to be re-confirmed.
- `automation_observer_calls`: bounded provider-call audit records with requested and resolved
  model, generation, token and cost usage, latency, provider, finish reason, HTTP status,
  retryability, and response content type and length.
  Provider response content is not stored.
- `session_lineage(parent_run_id, child_run_id, relation, metadata_json)`: how one run came from
  another, unique per triple. `relation` is one of `resume`, `handoff`, `continuation`, `review`,
  `branch`. A `branch` edge is the only record that a fork happened: the branch is a separate
  conversation in its own file, so without the edge it is indistinguishable from an unrelated
  conversation that happens to share a prefix. Its metadata carries the strategy, both
  conversation ids, the message and cut the fork was made at, and a bounded excerpt of that
  message's text, so the fork point outlives the request *and* the transcript it was cut from.
  The excerpt is denormalised deliberately: its only reader renders one line weeks later, when
  the parent conversation may have been compacted, relocated by a cwd change, or deleted. The edge is written after the branch's pane is proved up, and a failure to write it is
  logged rather than raised — losing the edge degrades the lineage view, not the conversation.
- Automation, notification, lineage, experience, batch, and voice tables retain their
  feature-specific contracts. `AutomationStore` has an additive migration path; the
  annotations rebuild that relaxed `agent_run_id` to nullable is gated on the new column
  being absent, so it runs once.
- `schema_versions(store, version)`: per-store schema version on the shared file.
  `PRAGMA user_version` is a property of the *database*, so several stores stamping it made
  the last connect overwrite the rest and each store read a neighbour's number.
- History, operational telemetry, automation, prompt-queue, and voice use separate serialized
  connections to
  one WAL database plus a process-wide per-database operation coordinator. Complete operations
  cannot compete for SQLite's single writer slot; every failed worker operation rolls back, and
  an operation may not return with an implicit transaction still open. Expected uniqueness
  deduplication also rolls back before returning.

## Filesystem records

- `<project>/.swe-mux/config.toml`: versioned, typed portable Project profile, prompt-scope,
  notification-permission, additive ignore overrides, an `automations` opt-in table gating
  control-plane substrate/consumers (`features/automation-enablement.md`), and the
  `session_control_grant` field (`"draft"` | `"granted"`, default `"draft"`) that sets the
  authority of the Phase 7.6 `interrupt`/`end_session` tools once the `session_control`
  automation is opted in - read by `project_session_control_grant()`, and never machine-wide.
  A sibling `spawn_grant` field (same values, same default, same automation gate, read by
  `project_spawn_grant()`) sets whether `mux.requestSpawn` creates a session in this Project
  directly (`granted`) or writes the Phase 5 inert draft (`draft`); authority is by target
  Project, so an agent spawns into a Project the operator granted. The install caps the granted
  path with `agent_spawn_hourly_budget` (default 10).
  Two further fields govern control-plane approvals (`features/approvals.md`): `approval_allow`,
  the `Tool` / `Tool(pattern)` rules a session's `allowlisted` mode resolves against, and
  `approval_ceiling` (`"wait"` | `"allowlisted"` | `"allow_all"`) capping the strongest mode any
  session here may hold. The rules live in the Project file because "reading this repo's
  `.claude` config is fine" is a property of the codebase, and because a rules editor is the
  wrong thing to hand someone in the moment they want to switch a mode on. Unset `approval_allow`
  means the built-in `approvals.DEFAULT_ALLOW_RULES`; an explicit `[]` is a different and real
  answer ("approve nothing automatically here") and is preserved on write rather than dropped.
  A malformed config yields ceiling `"wait"`, because an unreadable ceiling is not evidence of
  permission. Neither field can reach past the floor in `approvals.py`.
  Legacy `resource_open_mode` input remains parseable for compatibility but is omitted from current
  effective/public options.
- `<project>/.swe-mux/observations.json`: the Project's capture inbox — a bounded list of
  `{id, body, done, created_at}` notes-to-self, append-only capture with revision-checked
  edits. An item may also carry `kind: "spawn_request"` and a typed, inert `request`
  payload written by `mux.requestSpawn` (prompt, backend, cwd, calling-session provenance,
  decision status) — text in the user's own file until a human approves it. Not stored in
  SQLite. An item may instead carry `kind: "control_request"` and an inert Phase 7.6 `request`
  payload written by a drafted `mux.interrupt`/`mux.end_session` (action, target session id and
  name, reason, calling-session provenance, decision status) — likewise inert until a human
  approves it in the Fleet Queue. See `features/observations.md`, `features/agent-messaging.md`,
  `features/mux-mcp.md`.
- `<project>/.swe-mux/preview-shots/<id>.png`: headless preview screenshots saved into the
  owning Project (data-dir fallback) so a local agent can read them. See
  `features/processes-and-previews.md`.
- `<project>/.swe-mux/seeds/seed-*.md`: staged new-session seed prompts whose bodies exceed
  the argv bound, gitignored via a generated `.gitignore` and pruned after 14 days — staged
  *inside* the workspace so both agent CLIs can read them without leaving it. See
  `features/prompt-queue.md`.
- `<workspace>/.swe-mux/attachments/<safe-session-id>/<uuid>-<safe-name>`: user-selected files
  copied into the registered Project or the session's explicitly validated Git worktree. The
  generated `attachments/.gitignore` excludes all contents from Git. These files are persistent,
  bounded per upload/session, and are not removed with the live session. See
  `features/project-resources.md`.
- `<project>/.swe-mux/notes/project.md`: the Project's initial ordinary note, seeded at creation
  with a Project-named title and heading only when the file is absent.
- `<project>/.swe-mux/notes/items/<safe-note-id>.md`: additional flat Project-owned notes.
  Note contents are ordinary Project files and are not stored in SQLite.
  Each note has a `swe_mux_note = 1` TOML identity header carrying its ID, title, creation time,
  and optional migration provenance.
  The header is stripped on read and rebuilt on save, matched byte-exactly, and written LF-only.
- `<project>/.swe-mux/notes/legacy/`: recoverable source archive for migrated pre-collection
  session-note files, including empty legacy artifacts that are not promoted to notes.
- `<project>/.swe-mux/notes/trash/`: deleted Project notes, moved here rather than unlinked,
  keeping their identity header and their filename with a short suffix on collision.
  Nothing reads this tree: it is outside `items/`, so it never re-enters a listing or the
  note count, and no sweep removes it.
- `<project>/.swe-mux/notes/.gitignore`: generated `*` rule that excludes the entire Project-owned note tree, including current notes, deleted notes, legacy session notes, and migration archives, from Git.
- `<data_dir>/notes/items/scratchpad.md`: global Scratchpad Markdown with a `global-notes` identity header.
  The file is absent until the first save and is independent of Project registration, deletion, and Git state.
- `<project>/.swe-mux/prompts/<uuid>.md`: Project prompt templates with TOML frontmatter and
  inert Markdown-like text bodies. `<data_dir>/prompts/` holds global templates;
  `<data_dir>/prompt-library-state.json` holds bounded device-independent favorites/recents.
- `<project>/.vscode/tasks.json`, root `package.json`, and
  `<project>/.swe-mux/actions.toml`: optional repository task sources. Their contents remain
  inert until an explicit Run and local exact-content approval.
- `<data_dir>/project-action-trust.json`: canonical Project-root to SHA-256 task-file fingerprint
  mapping. It stores no commands or credentials and is invalidated by any supported file's
  presence/content change.
- `<data_dir>/provider-accounts.json` and provider snapshot directories: private account
  metadata/auth, never project data or public API payloads.

## Retention and secrecy

Native transcripts remain in vendor locations and are authoritative; searchable message text
is a local rebuildable derivative deleted with its history index row.
Git provenance follows the corresponding History and Project lifecycle instead of operational or optional Tier 0 age retention.
Backfill job progress is daemon-local and disposable; completed index writes remain durable.
Provider auth and the
OpenRouter key are never stored in SQLite or project files. Raw operational telemetry is time-bounded; old
quota samples roll into daily summaries before deletion. Process and operational retention
are independently configurable. Quota history contains account IDs and utilization, never
credentials; process history contains command hashes, never command text.

Retention runs **hourly** from the daemon's supervised retention loop, and only there.
Session-preserving reload makes weeks-long uptimes the norm, so a startup-only prune would mean
"bounded by age" holds only across restarts; the loop's first pass runs shortly after startup
rather than during it, because a prune is a scan whose cost tracks database size and page cache,
and on the startup path it delays the listener bind by exactly that much. Every automation table with
unbounded growth is covered — firings, action results, observer calls, notifications, the
spend ledger, observer batches and rule checkpoints on the configured
`automation_retention_days`, and the three derived-knowledge tables (run-note annotations,
learned resolutions, session lineage) on a deliberately longer window, because a user reads
those long after the operational trail that produced them. Prompt-queue history
(terminal-state messages and their delivery audit) ages out on
`prompt_queue_retention_days`; pending queue items never age out. Staged new-session seed
files (`.swe-mux/seeds/`) are pruned opportunistically after 14 days.
Session attachments are intentionally outside automated retention: they are workspace files the
user explicitly supplied, so session removal or a daemon sweep must not silently delete them.

Clipboard history is the one store that holds arbitrary user text verbatim, so it is the one
store that defaults to keeping nothing durable: memory-only unless persistence is opted into,
count- and time-bounded (pins exempt), secret-shaped copies refused before an entry exists, and
no copied text in the `clipboard_changed` events that announce ring changes.
