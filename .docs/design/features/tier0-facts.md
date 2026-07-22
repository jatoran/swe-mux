# Tier 0 deterministic facts

## What it is

Durable, no-model capture of deterministic facts about agent sessions (file writes,
commands, tests, git, tools) with pointers back to the immutable event log. The
substrate that provenance, loop detection, and declared-vs-verified read from. Per-project
opt-in and gated (`automation-enablement.md`). Vision: `../../development/CONTROL_PLANE_ROADMAP.md` §5.

## Key concepts

- **Fact**: one deterministic observation — `kind`, `target`, `content_hash`,
  `fingerprint`, bounded `detail`, and a `source_seq` pointer into the event log / raw store.
- **Source pointer**: `source_seq` (event sequence) ties every fact to its origin so it can
  be rehydrated; a summary is never the only copy.
- **Fingerprint**: canonical action signature (`event_type`, `kind`, casefolded `target`,
  exit class, `content_hash`) for loop detection. Identical repeated edits share a
  fingerprint (loop signal); changed content differs (progress). Strips volatile detail.
- **Content hash**: computed at the adapter boundary from the tool input (the exact bytes
  the agent wrote), not by reading the file back off disk — race-free. File content is not
  re-hashed on the event path.

## Data model

- Table `tier0_facts` on the shared WAL `mux.db`:
  `id, session_id, agent_run_id, project_id, kind, target, content_hash, fingerprint,
  detail_json, source_seq, source_ref, created_at`. Indexed by session/time, kind/time,
  content_hash, and (session, fingerprint). Command text is never stored beyond bounded detail.
- `kind` derives from the event: tool classification (`file_write | file_read | command |
  test | tool`), plus `git`, `compaction`.

## Operations

- Consumes normalized events (`tool_use`, `tool_result`, `git_changed`,
  `context_compacted`); everything else is ignored so capture stays cheap.
- Capture runs off the event loop on a single-worker executor behind the shared SQLite
  operation coordinator; failures can never break the event loop or a terminal.
- The adapter emits a normalized `target` + parse-time `content_hash` on `tool_use`
  (`observation.tool_call_evidence`); Tier 0 prefers those, falling back to key-scan.
- Gated per session: capture only for sessions whose owning Project opted `tier0` in,
  resolved off the loop with a short TTL cache.
- Retention: bounded by age (`prune`), reusing the process-evidence retention window.

## API surface

- None yet. Read internally by future consumers; no dedicated route.

## Configuration

- Enabled via `automations = { raw_store = true, tier0 = true }` in
  `<project>/.swe-mux/config.toml` (see `automation-enablement.md`).

## Key files

- Store + extraction + gated consumer: `src/swe_mux/tier0_store.py`
- Adapter-boundary target/content-hash: `src/swe_mux/observation.py` (`tool_call_evidence`)
- Construction, prune, gate resolver, lifecycle: `src/swe_mux/server.py`

## Relates to

- `automation-enablement.md` — the opt-in DAG that gates capture.
- `../technical/backend/sqlite.md` — shared `mux.db` operation-coordinator rules.
