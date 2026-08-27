# Backend: history, transcripts, observation, and status

Index: `../packages.md`.
Design: `../../../design/features/history.md`, `../../../design/features/transcript-branches.md`, `../../../design/features/approvals.md`, `../../../design/features/status-detection.md`, `../../../design/features/operational-telemetry.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## History

### `history.py`

The shared schema, Project and layout persistence, run history, bounded post-startup message-search repair with a literal fallback, and durable session and run-to-commit provenance with evidence promotion and lifecycle cleanup.

`history_naming_rows` is the chunked bulk read behind display names for ended sessions.
It is keyed by both row id and owning session id, where an exact row id outranks the session fallback, so the first run of a rolled-over session is not redirected to its last.

**Not:** live PTY lifecycle, provenance evidence classification, or the naming rule itself (`session_titles.py`).

### `history_backfill.py`

Bounded cancellable complete-history jobs, project-scoped by cwd ownership.

**Not:** durable job scheduling, or native file mutation.

### `history_scan.py`

The single user-triggered global native-history reconcile: one job at a time, scoped to `enabled_backends`, with `{status, phase, backends, scanned, processed, imported}` progress and cancellation over `reconcile_external_history`'s `should_cancel` and `on_progress`.

**Not:** the reconcile itself, project ownership resolution, or native file mutation.

## Transcripts

A Claude transcript is an append-only DAG, so the indexing projection drops the branches the conversation left and the human reader marks them.
Neither may reconstruct the live branch from the parent chain alone, because a parallel tool batch parents each result to its own call and a chain walk would drop every result but the last.

### `transcript_view.py`

Bounded harness conversation parsing, and normalized native tool names and inputs with results excluded.

- **Branch linearization** (`_claude_live_uuids`, `_mark_abandoned_records`): the live branch is the newest record's ancestry plus the tool results, sidechains, and attachments hanging off it.
- The human-readable `conversation_view` reduction: CLI machinery classified out, agent turns merged per tool-free segment and never across a branch boundary, call details carried at each boundary and after the newest message, byte and message capped, with its own LRU.
- `conversation_cut_points`: the byte span each displayed message occupies and how many tool calls a cut there would leave unanswered, per dialect and `None` where no rule is measured, counting only calls the live branch made.
  `resolve_cut_offset` turns a chosen message and side into that byte offset, and is shared by the interactive branch picker and a scheduled fork so the two cannot disagree about which cuts are legal.
- `final_exchange` and `final_reply_text`: the one definition of "the agent's latest reply", shared by the reader tab, the rail's copy, and read-aloud.

**Not:** process state, transcript writes, tool results, telemetry, persistence, or redaction, since the reader is the machine's owner.

### `transcript_fork.py`

Writing a forked conversation: given a byte offset, a new native transcript holding the source's records up to it, with conversation ids rewritten, sidecar tool outputs repointed and copied, titles marked so the CLI has no name collision to break, and a queued prompt dropped rather than inherited.
It refuses a source that is too large, a cut at byte zero, and an id that already names a conversation.

**Not:** where a cut is *legal* (`transcript_view` owns that), the source file (opened read-only, never written), spawning or attaching the pane, or session and history bookkeeping.

## Observation and approvals

### `observation.py`

Provider hook and transcript normalization, root-turn state, supervisor-resumable 5 s approval stabilization with immediate delivery blocking, first and latest user-request capture, immediate `transcript_message` fanout, and standing-activity evidence including the three carriers one background-task completion rides, closed idempotently per task.

Both running-work tiers count by **registry, not by increment**: `background_open` for shells and `subagent_launches` for `Agent`/`Task` calls, each keyed by tool_use id, so a launch announced twice (a transcript record and a lifecycle hook) is still one launch and a completion names the launch it closes.
For subagents that registry is combined with a hook counter of its own (`subagent_hook_count`) using `max`, because `SubagentStop` fires while an async agent is still working and the annotation is not a place to keep a count (`design/features/status-detection.md`).
The two tiers must not read each other's completions: a `<task-notification>` is routed by the registry that holds its id before any wording is consulted.

It also owns which conversation a hook speaks for: the payload-only scope rules (`hook_event_scope`) and the session-aware refinement that recognises a thread this session's own agent spawned (`note_child_thread`, `session_hook_event_scope`).
On that sits the foreign-conversation filter and the turn-end gate that keeps a subagent thread from binding this pane's identity or closing its root turn (`root_conversation_evidence_refusal`).

It applies the approval policy on the hook path, returns the harness decision, and delivers an already-decided approval as a keystroke when the CLI ignores that decision - screen-gated, fingerprint-checked, with the ordinary stabilization timer armed underneath.

`JsonlTailer` is how it reads the file, and two rules govern that.
**Attach replay is windowed and off the loop**: the pre-existing content is read and decoded 512 KiB at a time in a worker thread, so peak memory is a window and its records rather than the file, and the loop is handed back at every window boundary.
The whole-file read this replaced cost one uninterruptible span per attach and per rebind - measured on the primary host, 290 ms for a 24 MiB transcript and 691 ms for a 48 MiB one, with nothing else in the daemon running for the duration.
Windowing may not move the replay boundary: a record's `(historical, live)` label is still its decoded byte position against the attach snapshot, and the `(None, False)` catch-up marker still follows the last historical record, whichever window it landed in.
**The 250 ms poll trusts `stat()` only to say a file *changed*, never that it did not.**
Size, file id and write time are read on every tick and a move in any of them triggers the 64-byte prefix read that proves whether the transcript was replaced; when all three hold still the read is skipped, and a 2 s backstop takes it anyway.
The backstop is not belt-and-braces: on Windows a transcript held open by its writer reports its creation time as `st_mtime` for hours, so a same-length rewrite is entitled to leave every field this tailer may trust exactly as it found them.
Before this the prefix read was unconditional - an open and a read per observed session per tick, forever, to catch that one case.

**Not:** HTTP routing, title policy, transcript rendering, opening a `background_tasks` annotation from the PTY footer (that tier may only refresh), deciding *what* is approvable (`approvals.py`), any filesystem or database read on the decision path, or writing to a PTY directly.
Delivery goes through `Session.approval_input_sink`, so the input accounting delivery readiness depends on cannot be skipped.

### `approvals.py`

The pure approval decision: rule parsing and matching, shell-command segmentation, the never-auto-approved floor, the shipped default allowlist, and bounded request descriptions.

A decision is made from the harness's structured permission request, never from the PTY screen, and the floor is checked before the mode so no configuration can reach past it.

**Not:** reading config or Project files, holding grant state, knowing about sessions or harnesses, or ever returning `deny`.

## Durable status and telemetry

### `status_timeline.py`

The durable per-session detection timeline: `LedgerRing` (seq and run-id stamping plus a guarded sink nudge), the write-behind batched drain into `status_timeline`, time-ranged and post-mortem queries, retention, and `note_layer_reading` for on-change layer entries.

**Not:** the transition contract itself, since `apply_state_transition` never touches persistence; state decisions; or HTTP handlers (`routes/`).

Incident procedure: `../../../development/STATUS_INCIDENT_RUNBOOK.md`.

### `operational_telemetry.py`

Process, quota, reset, context, and tool evidence; append-only, content-free notification decision evidence and aggregates; and provider-evidence reset after proven session-identity repair.

**Not:** credentials, notification content or endpoints, or automatic process killing.
