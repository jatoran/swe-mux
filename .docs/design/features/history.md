# History and events

## What it is

- SQLite index of agent session lifetimes plus append-only normalized daemon events.
- History survives daemon restarts; live sessions and scrollback do not.

## Data model

- `history`: agent-run/native IDs, Claude/Codex backend, immutable concrete run scope,
  display-only repository group, label/root, cwd/space, spawn/exit/reason, model,
  current/peak context, token totals, provenance, transcript path.
- `events`: monotonic sequence, timestamp, optional session ID, source, type, JSON payload.
- `links`: reserved pair relation for future relay; no runtime consumer.

## Operations

- Shell start creates only an internal provisional PTY row. It is excluded from history
  APIs and deleted on exit. Each authenticated promotion creates a separate durable agent-
  run row with its launch cwd/scope; demotion closes it. The same shell may therefore own
  several sequential Claude/Codex history entries without changing PTY identity.
- Direct agents are visible immediately; explicit and unexpected exits update the same row.
- History search is indexed and cursor-paginated across query, backend, project (including
  a distinct Ungrouped filter), state, date, space, and external origin.
- Project filters group by concrete scope/worktree. Git remote/common identity is retained
  separately as an optional broader display grouping; it never changes behavioral scope.
- Context values are persisted only when native observation provides a valid window and
  current-window measurement; otherwise the UI says `context unavailable`.
- Resume creates a new mux session and uses the selected adapter's native resume command.
- Resume validates the direct history ID, transcript, native identity, adapter, cwd, and
  target space, then atomically attaches the new session to the target layout.
- Index deletion never deletes or edits a native transcript.
- EventBus persists each event before fanout. Cursor catch-up uses stable monotonic
  sequence numbers so reconnect does not depend on wall-clock timestamp equality.
- Startup reconciliation scans recent Claude project JSONL and Codex rollout JSONL in
  a worker thread, indexes sessions not owned by mux as `external=1`, and never mutates
  native transcript files.
- The read-only viewer normalizes user, assistant, and tool-use records for both backends.

## Key files

- SQLite index: `src/swe_mux/history.py`
- Event fanout: `src/swe_mux/event_bus.py`
- API: `src/swe_mux/server.py`
- External discovery: `src/swe_mux/reconcile.py`
- Read-only parsing: `src/swe_mux/transcript_view.py`
