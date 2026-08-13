# Data model

## Ownership

- `ProjectRecord`: stable ID, name, canonical root, optional Group, position, layout,
  layout revision, registration time, shared explicit-use time, nullable Project-local `git_compare_ref`, and default backend/profile. A deprecated resource-presentation field may
  still be loaded from older records but has no browser behavior.
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
- `SessionRecord.standing_activity`: the standing-engagement annotation axis — a list of
  `StandingActivity {kind: loop|cron|background_tasks|subagents, source, evidence, since,
  expires_at, count, detail}`. Not states: SessionState, `awaiting_reason`, and delivery are
  untouched. Run-scoped (cleared wherever observation identity resets — rollover, heal,
  promote, demote, session end) and TTL'd where the evidence implies an expiry. Serialized
  in the record snapshot, so supervisor adoption round-trips it across daemon restarts;
  drift-tolerant like the rest of `from_snapshot` (unknown keys dropped, malformed
  annotations skipped). Contract and detection sources: `features/status-detection.md`.
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
- `history`: durable agent-run lifecycle, canonical `project_id`, legacy terminal `note_id`
  retained for note migration provenance,
  native identity, transcript pointer, derived Git metadata, context/model telemetry, explicit
  compaction summary, exit state, materialized chronological native start/final conversational message
  time and role, plus source mtime/size watermarks for bounded timestamp-summary refreshes.
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
  `observer_fault`, hook-spool records) keyed `(session_id, agent_run_id, seq)` with `ts`,
  `kind`, and the entry payload verbatim as JSON. Run-keyed so a conversation rollover's
  successor rows never mix with its predecessor's. Written behind the in-memory rings by a
  batched sink (never on the transition path), pruned by
  `status_timeline_retention_days` (default 30), and queried by time range for
  post-mortems (`features/status-detection.md` § durable timeline,
  `development/STATUS_INCIDENT_RUNBOOK.md`).
- `tier0_facts`: deterministic no-model fact capture (file writes, commands, tests, git, tools)
  with `content_hash`, canonical `fingerprint`, the owning `agent_run_id`/`project_id`, and a
  `source_seq` pointer into the event log. Test results additionally carry structured
  pass/fail counts and failing-test ids inside the bounded detail. Command text is never
  stored beyond bounded detail, and that detail is bounded per value so the row always
  re-parses. Per-project opt-in and gated; see `features/tier0-facts.md`.
- `project_cards`: one distilled architecture card per Project — `project_id` (primary key),
  `project_root`, `fingerprint`, `card_json`, `schema_version`, the requested/resolved model,
  token counts, `cost_usd`, `created_at`. A cache, not a record: the row is served only while
  its `fingerprint` still matches the Project's current `.docs`, so it is replaced in place
  and never pruned by age. Per-project opt-in and gated; see `features/project-card.md`.
- `scan_timeline_runs`: the current authorization and delta cursor for one `agent_run_id`.
  It records the persistent terminal `session_id`, Project, enabled/disabled timestamps, last
  scan time, and last source timestamp.
  A successor conversation has another primary key and therefore starts disabled.
- `scan_timeline_records`: append-only structured Tier 1 records keyed to both `session_id` and
  `agent_run_id`, with the bounded source interval, trigger, validated semantic JSON, transcript
  input hash, requested/resolved model, generation, token counts, cost, and creation time.
  Transcript text remains in the authoritative provider transcript.
- `scan_timeline_boundaries`: explicit predecessor-to-successor run boundaries for one persistent
  session, including rollover reason and time.
- `scan_timeline_metrics`: one bounded aggregate row measuring record reads, source rehydrations,
  and their derived rate.
- `automation_budget_ledger` additionally carries nullable `project_id` and `agent_run_id` so a
  continuously costing substrate can enforce and display Project and run budgets even when a
  failed provider call creates no semantic record.
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
  `chain_depth`, `origin_json` (relay path / rule id / Tier 0 fact fingerprints),
  `payload_json` (typed action payload for control-plane drafts), `constraints_json`
  (`not_before`, `expires_at`) — plus blocked reasons, stranded reason, `cancel_kind`
  (`cancelled|skipped|revoked|expired`), `retargeted_from_json`, and lifecycle timestamps
  including `deleted_at`. Delete blanks the body and action-bearing JSON immediately, hides
  the row from every read surface, and retains the content-free row until normal retention
  so sender correlation retries resolve to the deleted identity instead of recreating it.
- `queue_deliveries`: the delivery audit — per attempt: revision, target identity,
  readiness state + reasons, explicit-confirmation flag, `initiator` (`user|auto` — who
  pressed send), outcome (`pending|sent|refused|failed`), error, byte count, and a
  partial-unique `idempotency_key` (a repeated key replays the recorded outcome instead of
  delivering twice). Deliberately carries no prompt text; bodies live in `queue_messages`
  only.
- `queue_auto_policy` / `queue_auto_counters` (Phase 5, `features/auto-delivery.md`):
  runtime auto-delivery state, deliberately not config — the default-on per-conversation grant
  or override (bound
  `agent_run_id`, `expires_at`, `max_sends`/`sends_used`, `accept_agent_messages`,
  `disabled_reason`), one reserved `*` row for the emergency pause, and the persisted
  proving-period counters (`auto_sent`, `auto_refused`, `auto_failed`, `unsafe_reported`,
  `proving_since`). The store carries a v1→v2 migration: the Phase 5 columns are added in
  place, because `CREATE TABLE IF NOT EXISTS` would otherwise reach only fresh databases.
  `accept_agent_messages` keeps a column default of `0` while the conversation-default grant
  writes `1` explicitly. A column default would also land on rows inserted by an opt-out and
  on the reserved pause row, where "on" is not what was meant, so the per-run default belongs
  in the one code path that grants a run rather than in the DDL.
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
- `automation_observer_calls`: bounded provider-call audit records with requested and resolved
  model, generation, token and cost usage, latency, provider, finish reason, HTTP status,
  retryability, and response content type and length.
  Provider response content is not stored.
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
  notification-permission, additive ignore overrides, and an `automations` opt-in table gating
  control-plane substrate/consumers (`features/automation-enablement.md`). Legacy
  `resource_open_mode` input remains parseable for compatibility but is omitted from current
  effective/public options.
- `<project>/.swe-mux/observations.json`: the Project's capture inbox — a bounded list of
  `{id, body, done, created_at}` notes-to-self, append-only capture with revision-checked
  edits. An item may also carry `kind: "spawn_request"` and a typed, inert `request`
  payload written by `mux.requestSpawn` (prompt, backend, cwd, calling-session provenance,
  decision status) — text in the user's own file until a human approves it. Not stored in
  SQLite. See `features/observations.md`, `features/agent-messaging.md`.
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
- `<project>/.swe-mux/notes/.gitignore`: generated `*` rule that excludes the entire Project-owned note tree, including current notes, legacy session notes, and migration archives, from Git.
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
is a local rebuildable derivative deleted with its history index row. Backfill job progress is
daemon-local and disposable; completed index writes remain durable. Provider auth and the
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
