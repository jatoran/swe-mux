# Data model

## Ownership

- `ProjectRecord`: stable ID, name, canonical root, optional Group, position, layout,
  layout revision, and default backend/profile. A deprecated resource-presentation field may
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
- `SessionRecord.observation_stale_since`: volatile. Set when the followed transcript is
  provably no longer this PTY's conversation and no successor could be corroborated; it
  revokes the transcript's authority over hooks and hard-blocks delivery.
- Git `repository_id`, project scope, root, and repository group fields are derived metadata,
  separate from canonical Project ownership.

## Core SQLite records

- `projects` and `project_groups`: sidebar ownership and organization.
- `history`: durable agent-run lifecycle, canonical `project_id`, owning terminal `note_id`,
  native identity, transcript pointer, derived Git metadata, context/model telemetry, explicit
  compaction summary, exit state, materialized chronological native start/final conversational message
  time and role, plus source mtime/size watermarks for bounded timestamp-summary refreshes.
- `history_messages` + `history_messages_fts`: derived role-aware user/assistant text and
  provider-native optional timestamp plus FTS5 lookup surface. `history_transcript_index` stores
  source mtime/size, parser version, message count, and index time so empty/unchanged transcripts
  remain incremental.
- `events`: monotonically sequenced mux events.
- `process_evidence`: bounded PID+creation-time fingerprints, owner/lineage/Job Object
  evidence, state/confidence, and exit evidence; command text is never stored.
- `quota_samples` and `quota_sample_rollups`: durable raw observations and daily retention
  summaries. `quota_reset_events` retains reset/correlation evidence plus nullable durable user
  review (`manual_usage | discarded`, timestamp); `quota_attributions` retains correlation
  estimates.
- `context_compactions`, `tool_events`, and `transcript_telemetry_coverage`: deduplicated
  explicit provider evidence plus versioned parser coverage. These are rebuildable for one
  session after a proven identity repair; its process evidence is retained and re-attributed.
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
  (`draft|armed|blocked|delivering|sent|failed|cancelled|stranded`), body, `revision`,
  and the provenance-rich sender model — `sender_kind`
  (`user|remote_user|agent|rule|queue_draft`, derived from the transport or the caller's
  MCP token, never claimed), `sender_id`/`sender_label`, `origin_session_id`,
  `correlation_id` (partial-unique per sender: a retried send returns the original row),
  `chain_depth`, `origin_json` (relay path / rule id / Tier 0 fact fingerprints),
  `payload_json` (typed action payload for control-plane drafts), `constraints_json`
  (`not_before`, `expires_at`) — plus blocked reasons, stranded reason, `cancel_kind`
  (`cancelled|skipped|revoked|expired`), `retargeted_from_json`, and lifecycle timestamps.
- `queue_deliveries`: the delivery audit — per attempt: revision, target identity,
  readiness state + reasons, explicit-confirmation flag, `initiator` (`user|auto` — who
  pressed send), outcome (`pending|sent|refused|failed`), error, byte count, and a
  partial-unique `idempotency_key` (a repeated key replays the recorded outcome instead of
  delivering twice). Deliberately carries no prompt text; bodies live in `queue_messages`
  only.
- `queue_auto_policy` / `queue_auto_counters` (Phase 5, `features/auto-delivery.md`):
  runtime auto-delivery state, deliberately not config — the per-session opt-in (bound
  `agent_run_id`, `expires_at`, `max_sends`/`sends_used`, `accept_agent_messages`,
  `disabled_reason`), one reserved `*` row for the emergency pause, and the persisted
  proving-period counters (`auto_sent`, `auto_refused`, `auto_failed`, `unsafe_reported`,
  `proving_since`). The store carries a v1→v2 migration: the Phase 5 columns are added in
  place, because `CREATE TABLE IF NOT EXISTS` would otherwise reach only fresh databases.
- `project_scopes`, `repo_groups`, and `artifacts`: derived Git/filesystem inventory retained
  for diagnostics and future Git expansion, not session containment.
- `automation_annotations`: observer/rule/detector output. Anchored to `agent_run_id` **or**
  `project_id` — both nullable, at least one required — because a project-scoped detector
  (doc debt) has no run to attach to. Alongside the single `source_event_seq` it carries
  `evidence_json`, the *set* of Tier 0 facts a finding rests on: a loop's case is "this
  fingerprint repeated three times", which one pointer cannot express. `dedupe_key` makes a
  re-running detector idempotent — a conflicting write returns the existing row.
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
- `<project>/.swe-mux/notes/project.md`: the Project's one canonical note, seeded at creation
  with a Project-named heading only when the file is absent.
- `<project>/.swe-mux/notes/sessions/<safe-session-id>.md`: lazily initialized notes owned by
  individual terminal sessions. Unsafe or external identities map to a stable hashed filename;
  note contents remain ordinary Project files and are not stored in SQLite.
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

Retention runs at startup **and hourly** from the daemon's supervised retention loop, not at
startup only: session-preserving reload makes weeks-long uptimes the norm, so a startup-only
prune means "bounded by age" holds only across restarts. Every automation table with
unbounded growth is covered — firings, action results, observer calls, notifications, the
spend ledger, observer batches and rule checkpoints on the configured
`automation_retention_days`, and the three derived-knowledge tables (run-note annotations,
learned resolutions, session lineage) on a deliberately longer window, because a user reads
those long after the operational trail that produced them. Prompt-queue history
(terminal-state messages and their delivery audit) ages out on
`prompt_queue_retention_days`; pending queue items never age out. Staged new-session seed
files (`.swe-mux/seeds/`) are pruned opportunistically after 14 days.

Clipboard history is the one store that holds arbitrary user text verbatim, so it is the one
store that defaults to keeping nothing durable: memory-only unless persistence is opted into,
count- and time-bounded (pins exempt), secret-shaped copies refused before an entry exists, and
no copied text in the `clipboard_changed` events that announce ring changes.
