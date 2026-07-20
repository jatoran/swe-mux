# Data model

## Ownership

- `ProjectRecord`: stable ID, name, canonical root, optional Group, position, layout,
  layout revision, and default backend/profile. A deprecated resource-presentation field may
  still be loaded from older records but has no browser behavior.
- `ProjectGroupRecord`: stable ID, name, and position. It has no behavioral ownership.
- `SessionRecord.project_id`: immutable canonical Project ownership. `cwd`/`spawn_cwd` begin
  at the Project root; validated runtime cwd remains telemetry.
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
  explicit provider evidence plus versioned parser coverage.
- `project_scopes`, `repo_groups`, and `artifacts`: derived Git/filesystem inventory retained
  for diagnostics and future Git expansion, not session containment.
- Automation, notification, lineage, experience, batch, and voice tables retain their
  feature-specific contracts.
- History, operational telemetry, automation, and voice use separate serialized connections to
  one WAL database plus a process-wide per-database operation coordinator. Complete operations
  cannot compete for SQLite's single writer slot; every failed worker operation rolls back, and
  an operation may not return with an implicit transaction still open. Expected uniqueness
  deduplication also rolls back before returning.

## Filesystem records

- `<project>/.swe-mux/config.toml`: versioned, typed portable Project profile, prompt-scope,
  notification-permission, and additive ignore overrides. Legacy `resource_open_mode` input
  remains parseable for compatibility but is omitted from current effective/public options.
- `<project>/.swe-mux/notes/project.md`: the Project's one canonical note.
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
