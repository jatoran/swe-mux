# History and events

## What it is

- SQLite index of session lifetimes plus append-only normalized daemon events.
- History survives daemon restarts; live sessions and scrollback do not.

## Data model

- `history`: mux/native IDs, backend, name, cwd/space, spawn/exit, reason, tokens, transcript path.
- `events`: sequence, timestamp, optional session ID, source, type, JSON payload.
- `links`: reserved pair relation for future relay; no runtime consumer.

## Operations

- Session start inserts the history row before API success returns.
- Explicit and unexpected exits update the same row.
- Resume creates a new mux session and uses the selected adapter's native resume command.
- EventBus persists each event before fanout to live subscribers.
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
