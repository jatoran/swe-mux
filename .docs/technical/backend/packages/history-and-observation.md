# Backend: history, transcripts, observation, and status

Index: `../packages.md`.
Design: `../../../design/features/history.md`, `../../../design/features/transcript-branches.md`, `../../../design/features/approvals.md`, `../../../design/features/status-detection.md`, `../../../design/features/operational-telemetry.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## History

### `history.py`

The shared schema, Project and layout persistence, run history, bounded post-startup message-search repair with a literal fallback, and durable session and run-to-commit provenance with evidence promotion and lifecycle cleanup.

It also owns the `events` table through `event_journal.py`. The daemon's `EventBus` sink is `journal_event`, which numbers an event in memory and returns at once, and one writer task writes whatever has accumulated in a single ordered transaction on the history worker; `append_event` is the same plus a wait, for callers that need the row written.
Before this every emit awaited its own insert before any subscriber saw the event, so each hook reply (a `PermissionRequest` decision among them) queued behind single-row transactions on the one worker and the `mux.db` operation lock every store shares.
Every read of `events` (`events`, `recent_events`, `workload_telemetry`) first waits for everything numbered before it, so a cursor never sees a hole a queued event will fill; `close` writes what is still queued.
The journal's state is `event_journal` on `/api/diagnostics/background`.

`history_naming_rows` is the chunked bulk read behind display names for ended sessions.
It is keyed by both row id and owning session id, where an exact row id outranks the session fallback, so the first run of a rolled-over session is not redirected to its last.

The History reader's reads live here too:
- `read_history_conversation` is the one seam through which History reads a native conversation, and it reads through `conversation_index.indexed_parse`, whose output is a full parse's exactly, tool inputs included.
  Interactive reads pass `cached=True`: they go through the shared registry and keep the result until the conversation changes.
  The indexer and the background reindex pass `False`: they reuse an index a live reader already holds and otherwise read through a throwaway one, so walking thousands of conversations neither evicts a live reader's index nor pins anything.
- `transcript_index_state` answers what the index holds against the conversation's watermark and schedules `schedule_transcript_reindex` when it is behind.
  The reindex runs on a one-thread pool, single-flight per run with a 30 s cooldown; `refresh_time_summaries` runs the listing's head/tail reads on a four-thread pool behind a bounded wait.
  Both pools are threads rather than event-loop tasks so a job that outlives its request never calls into a closed loop, and `close` drains them before the database executor.
  `wait_for_background` is for tests and nothing on a request path calls it.
- `history_messages_page` pages `history_messages` by ordinal over `idx_history_messages_history`.

**Not:** live PTY lifecycle, provenance evidence classification, the naming rule itself (`session_titles.py`), or parsing a transcript format (`transcript_view.py`).

### `history_backfill.py`

Bounded cancellable complete-history jobs, project-scoped by cwd ownership.

**Not:** durable job scheduling, or native file mutation.

### `history_scan.py`

The single user-triggered global native-history reconcile: one job at a time, scoped to `enabled_backends`, with `{status, phase, backends, scanned, processed, imported}` progress and cancellation over `reconcile_external_history`'s `should_cancel` and `on_progress`.

**Not:** the reconcile itself, project ownership resolution, or native file mutation.

## Transcripts

### `codex_history.py` and `reply_snapshot.py`

`codex_history.py` resolves read-only pinned fork ancestry, bounded virtual pages, replacement-aware revision fingerprints, and cached native-record freshness.
`reply_snapshot.py` selects a completed surviving answer from a freshly refreshed conversation index, bracketed by two watermark reads that must agree, and returns message, turn, and revision evidence for an explicit Copy request.
The route revalidates the live session identity after off-loop parsing.

### `conversation_index.py`

One incremental reading per conversation, shared by every human-facing reader: the Transcript tab, branch points, read-aloud, Copy reply, and History (`design/features/conversation-index.md`).

- `ConversationIndex` keeps each record reduced to what the projections read, decodes only appended complete lines, re-checks prefix and boundary samples to notice a replaced file, and skips Codex records no projection reads without decoding them (`codex_line_is_skippable`).
- `ConversationSnapshot` is one immutable reading: the merged reader messages with `positions`, per-message change `stamps`, `generation`/`seq`, and the `window`, `page` and `delta` payloads the session transcript route returns.
- `ConversationIndexRegistry` (`REGISTRY`) bounds the fleet's indexes by an estimated byte budget, least recently used first, runs async refreshes single-flight on its own two-thread executor, and reports `status()` to `/api/diagnostics/background` as `transcript_index`.
- Tool inputs stay in the file: `calls_at`/`tool_calls` read one seam's inputs back by reference, and `indexed_parse`/`parsed_tool_calls` serve the indexing projection with inputs read back by the same rule the full parse applies.

**Not:** the projections themselves (`transcript_view.py`, which this runs over compact records rather than reimplementing), the bounded head/tail page readers (`transcript_message_page`, `transcript_time_summary`), or the observer's tail (`observation.JsonlTailer`).

A Claude transcript is an append-only DAG, so the indexing projection drops the branches the conversation left and the human reader marks them.
Neither may reconstruct the live branch from the parent chain alone, because a parallel tool batch parents each result to its own call and a chain walk would drop every result but the last.

### `transcript_view.py`

Harness conversation parsing and its projections, and normalized native tool names and inputs with results excluded.

- **Branch linearization** (`_claude_live_uuids`, `_mark_abandoned_records`): the live branch is the newest record's ancestry plus the tool results, sidechains, and attachments hanging off it.
- The human-readable `conversation_view` reduction: CLI machinery classified out, agent turns merged per tool-free segment and never across a branch boundary, call names carried at each boundary and after the newest message.
  It is served from `conversation_index`, whose records carry a precomputed memo (`_MEMO_KEY`) and a tool reference (`_TOOL_REF_KEY`) in place of an input; every projection honours both and is byte-identical on a record decoded whole.
- `native_tool_input` and `codex_call_input`: the one rule each for what a tool block's input is, shared by the projections and the index's read-back.
- `conversation_cut_points` and `cut_points_from`: the byte span each displayed message occupies and how many tool calls a cut there would leave unanswered, per dialect and `None` where no rule is measured, counting only calls the live branch made.
  `resolve_cut_offset` turns a chosen message and side into that byte offset, and is shared by the interactive branch picker and a scheduled fork so the two cannot disagree about which cuts are legal.
- `final_exchange` and `final_reply_text`: the one definition of "the agent's latest reply", shared by the reader tab, the rail's copy, and read-aloud.

**Not:** process state, transcript writes, tool results, telemetry, persistence, or redaction, since the reader is the machine's owner.

### `transcript_repair.py`

Where a recorded conversation actually is once its recorded path stopped being true, and the write-back that makes one miss enough.
`resolve_row_transcript` answers for a history row, mutating the row and repairing the stored path (with every watermark on it, and its transcript-index cursor) when the file has moved; `locate_conversation` is the same search without a row, for a caller that holds only a session.

Two rules the module exists to keep. A located path is **verified before it is believed**, because `Adapter.locate_transcript` searches for Claude and computes for Codex, and an unchecked answer would record a second dead path as a repair.
And a repair is **written down**, or the surface that noticed heals only itself and Resume, Branch and the reindexer go on reading the same dead row.

**Not:** deciding *when* to look (every caller asks only after the recorded path has already missed, because a search is a directory probe per project slug), the search itself (`adapters/*.locate_transcript`), or following a file while its pane is alive - that is the observer's rebind (`session.py`), and this is what covers the window after it is gone.

### `transcript_fork.py`

Writing a forked conversation: given a byte offset, a new native transcript holding the source's records up to it, with conversation ids rewritten, sidecar tool outputs repointed and copied, titles marked so the CLI has no name collision to break, and a queued prompt dropped rather than inherited.
The source's `last-prompt` resume checkpoints are dropped and one naming the prefix's last linked record is appended, because `claude --resume` follows a checkpoint's leaf and the inherited ones are stale.
It refuses a source that is too large, a cut at byte zero, and an id that already names a conversation.

**Not:** where a cut is *legal* (`transcript_view` owns that), the source file (opened read-only, never written), spawning or attaching the pane, or session and history bookkeeping.

## Observation and approvals

### `codex_process_identity.py`

Captures the nearest Codex process fingerprint in the startup hook helper and verifies the emitting CLI against the PTY root's birth-checked ancestry and a matching CLI-root transcript header.
The bounded read runs off the daemon event loop through `observation.resolve_conversation_rollover`, shared by live ingress and hook-spool replay.
Nested CLIs, native subagent transcripts, stale PIDs, and unavailable evidence cannot authorize a conversation replacement.

`owned_rollout` additionally discovers the unique CLI-root transcript held open by the verified outermost Codex process.
The observer runs this off-loop fallback only on evidence (`design/features/status-detection.md`, Codex branch and rewind recovery) and applies normal live-session ownership checks before binding or rolling over.
On Windows the held files come from `win_process_files.py`, never `psutil.Process.open_files()`; an answer that query could not complete fails the proof closed, because the unnamed handle could have been the rollout.
A proof slower than `SLOW_PROOF_WARN_SECONDS` is `codex_process_proof_slow` at WARNING.

**Not:** conversation mutation, process termination, or command-line collection.

### `win_process_files.py`

Which files one Windows process holds open, from that process's own handle table.
`NtQueryInformationProcess(ProcessHandleInformation)` lists the target's handles, file objects are recognised by the type index learned from a handle of our own, and each candidate is duplicated and named with `GetFinalPathNameByHandleW`.
Measured 2026-10-12 on a host with ~450,000 open handles: 0.6-2 ms per process, against 234-340 ms for `psutil.Process.open_files()`, which enumerates every handle in the system and holds the GIL while it does (`development/PERFORMANCE_RUNBOOK.md` § Traps).
Naming a handle can block in the kernel for as long as the target lives (a pipe with a read pending), so it runs on a worker thread that is abandoned after `NAME_QUERY_TIMEOUT_SECONDS`.
An abandoned query marks the result incomplete and quarantines that one process until its stuck worker returns; `MAX_STUCK_WORKERS` is the global backstop.
Abandonment both sets `retired` and posts a `None` sentinel to the worker's inbox, because the caller cannot know which state it timed out in: a worker still in the kernel sees `retired` when it returns, and one that answered just after the wait expired has already gone back to its inbox and is woken by the sentinel.
Without the sentinel that second worker blocked forever and quarantined its pid for the life of the daemon (`tests/test_win_process_files.py` forces both orders).
Access masks measured to hang (`HANG_PRONE_ACCESS`, which extends psutil's own list by two masks seen on node and python pipes) are skipped and counted rather than named.
Counters are `transcript_observation.open_file_queries` on `/api/diagnostics/background`.

**Not:** any decision about what a held file means - `codex_process_identity.py` owns that - or any non-Windows host, where `psutil` already reads the process's own descriptor table.

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
**The tick's stat is shared** (`batched_stat.py`): every tailer's stat, including the one that waits for a file to appear, is answered by one worker pass per 250 ms boundary rather than one worker hop per tailer, and awaiting it is what paces the poll.
At 36 sessions the per-tailer hops were about 150 a second, each a GIL round trip for a few microseconds of syscall.

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
