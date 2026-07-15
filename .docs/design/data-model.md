# Data model

## Ownership

- A PTY session is ephemeral process state. A durable agent run/history row owns observer
  annotations and agent-run notes.
- A project scope is one concrete worktree/filesystem root. It owns project config and
  project/agent-run Markdown under `.swe-mux/`; a repository group is display-only.
- A space is app-owned workflow state. It owns layout and its app-data Markdown note, never
  a project scope.
- Runtime OSC cwd is display/convenience telemetry only. Spawn/run scope remains the trusted
  rule-matching and artifact-ownership input.

## Core SQLite records

- `spaces`: stable space identity, defaults, layout, and optimistic layout revision. Layout
  v4 stores the terminal/preview split-stack tree separately from a bounded per-space
  `note_dock` containing ordered open note resource IDs, active ID, and desktop size.
- `history`: one agent-run lifecycle with backend/native ID, immutable scope, transcript
  pointer, model/context telemetry, generated-title policy, and exit state.
- `events`: monotonically sequenced mux events. Automation first converts these to bounded
  normalized envelopes; native payload schemas do not escape adapters.
- `project_scopes`, `repo_groups`, `artifacts`: concrete project roots, display grouping,
  and durable project-file relationships.

## Automation SQLite records

- `automation_annotations`: durable agent-run output with tag/content, source event,
  rule/revision, provider/model/generation, tokens, cost, confidence, and provenance.
- `automation_firings` and `automation_action_results`: idempotent event/rule-revision
  evaluation, complete condition trace, shadow/live status, and bounded errors/results.
- `automation_observer_calls`: input hash/size—not prompt or transcript—plus provider/model,
  generation, usage, latency, cost, status, and redacted error.
- `automation_checkpoints`: debounce, threshold, rate, and fleet activity checkpoints.
- `automation_budget_ledger`: UTC-day global/per-rule token and dollar accounting linked to
  observer calls; missing provider cost is reconciled by generation ID.
- `automation_notifications`: provider-neutral attention records with evidence/read state.
- `automation_model_cache`: one explicit-refresh OpenRouter catalog snapshot and error state.
- `session_lineage`: idempotent resume/handoff/continuation/review edges between atomic runs.
- `experience_entries`: normalized error/resolution evidence and source-run provenance.
- `observer_batches`: reviewed ended-run selection plus preview-only results and spend.
- `voice_clips`: one read-aloud clip per generation — session/run identity, trigger,
  content mode, engine/voice, spoken text, file pointer into `<data_dir>/voice/`, size,
  status/error, and summary model/token/cost fields. Byte-cap pruned; snapshots omit paths.

## Retention and secrecy

Firing, action, call, and notification diagnostics use configured retention. Annotations,
lineage, and experience records are durable history substrate. Native transcripts remain in
their vendor locations and are never copied into SQLite. The OpenRouter key lives only in an
environment variable or the separate DPAPI-protected secrets file; all public records expose
configured/source status only.
