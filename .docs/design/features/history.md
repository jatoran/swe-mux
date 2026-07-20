# History and events

## What it is

- SQLite indexes durable Claude/Codex run lifetimes and append-only daemon events. Plain
  shells remain provisional and disappear from history when they exit without promotion.
- Each mux-created history row carries canonical `project_id`; Git repository/scope fields
  remain separate display metadata. Externally reconciled native transcripts may be
  unassigned until resumed into an explicit Project.
- History search is cursor-paginated across query, backend, Project, state, date, and origin.
- Resume requires a valid target Project, native ID, transcript, cwd record, and adapter. It
  creates a new Project-owned session at the target root and atomically updates its layout.
- Index deletion never deletes or edits the native transcript.
- Startup reconciliation reads recent Claude/Codex transcript directories in a worker and
  never moves vendor files.
- EventBus persistence precedes fanout; reconnect catch-up uses monotonic sequence IDs.
- Current context telemetry remains on live sessions and history. Explicit provider-native
  compaction records increment durable count/last-time/capability/confidence summaries;
  token drops alone never count as compaction.
- A separate bounded reconciliation indexes explicit Claude/Codex tool results, durations,
  skill invocations, compactions, and unknown/parser coverage across recent histories. Native
  source identities deduplicate hook/transcript copies.

## Key files

- `src/swe_mux/history.py`
- `src/swe_mux/event_bus.py`
- `src/swe_mux/reconcile.py`
- `src/swe_mux/transcript_view.py`
- `src/swe_mux/operational_telemetry.py`
