# History and events

## What it is

- SQLite indexes durable Claude/Codex run lifetimes and append-only daemon events. Plain
  shells remain provisional and disappear from history when they exit without promotion.
- Each mux-created history row carries canonical `project_id`; Git repository/scope fields
  remain separate display metadata. Startup-reconciled native transcripts may be unassigned;
  an explicit Project scan assigns only transcripts whose recorded cwd belongs to that
  registered Project, preferring the most-specific registered root.
- Each row also carries the owning terminal `note_id`. Opening `Session note` lazily creates or
  reopens the Project file for that terminal; nested agent-run IDs never fork the terminal note.
- **One row per agent run, and an in-CLI `/clear` or `/new` ends a run** (`backends.md`). The
  retired conversation keeps its own row with its own `native_id`, transcript path, indexed
  messages, and final token/context figures; the successor gets a separate row under a new
  `agent_run_id`. Both remain searchable and resumable. This is what the run boundary bought:
  the previous behaviour rewrote the live row's `native_id` and `transcript_path` in place,
  and the message indexer — which replaces a row's messages wholesale from its current
  transcript — then deleted the pre-clear conversation from search, leaving it recoverable
  only as a detached `external=1` backfill row with no link to the session that produced it.
- **Pane-level metadata writes land on the current run's row.** A rename
  (`update_session_metadata`) and a pane exit (`session_ended`) key on
  `agent_run_id or id`, never on the mux session id alone: after a rollover the row keyed by
  the mux id is a retired conversation, and writing there is how a custom title ended up on
  an entry that resumed a conversation the user never named. Earlier rows keep the name they
  had when their conversation ended.
- History is an ordinary workspace pane tab. Desktop panes can split/move it; mobile projects
  it into the unified tab rail without changing desktop layout.
- History search is cursor-paginated across provider, Project, state, origin, and four text
  scopes: all content, user prompts, agent replies, or metadata. Date ranges explicitly target
  either session start or the final timestamped conversational message.
- Searchable user/assistant text lives in a rebuildable local SQLite FTS5 index. Native
  transcripts remain authoritative and are never mutated. Search results include role-aware
  excerpts; opening a result provides ordered previous/next transcript match navigation.
- Session rows show the chronological minimum and maximum provider-native conversational
  timestamps plus the final speaker, so out-of-order native JSONL records cannot produce a start
  after the last message. Transcript cards show each provider-native message timestamp.
  Missing/invalid native message timestamps remain unavailable; process exit and file mtime are
  never presented as message time.
- Resume requires a valid target Project, native ID, transcript, cwd record, and adapter. It
  creates a new Project-owned session at the target root and atomically updates its layout.
  A conversation a live session currently claims is refused (`409 conversation_live`, naming
  the owning session): resuming it would put two live sessions on one conversation — the
  cross-attribution the identity invariant forbids. Branch is the flow for forking a live
  conversation; rows whose pane has since rolled onward resume fine.
- Index deletion never deletes or edits the native transcript.
- When session adoption proves that a lifecycle bug indexed another live session's transcript,
  the false run is quarantined (`agent_visible=0`), its rebuildable message/index cursor is
  removed, and the native transcript remains untouched. The direct root run is reopened under
  its stable mux history ID.
- **A quarantine is permanent.** Its exit reason (`root_identity_reconciled`,
  `historical_provider_collision_reconciled`) marks the row as a proven cross-attribution
  artifact, and no migration or backfill may make it visible again — only an explicit repair
  path may. The historical `agent_visible` backfill is one-shot (gated on the column having
  just been added) and additionally excludes those reasons; running it unconditionally, as it
  used to on every connect, resurrected every quarantined run under the sibling's identity
  within one session-preserving reload.
- The same repair runs at daemon startup after the original PTY has ended, but only when the
  database proves all three sides of the collision: the retained executable names a different
  provider, its note owner is that provider's canonical root row, and another canonical row
  owns the borrowed native ID or transcript. Legitimate shell-promoted runs do not qualify.
- Startup reconciliation reads the newest 2,000 Claude/Codex transcript files in a worker,
  incrementally indexes changed message bodies, and never moves vendor files.
- Managed transcripts are indexed once more after session exit so the list's last-message
  summary is current without waiting for the periodic reconciler.
- Visible History pages refresh changed timestamp summaries from bounded transcript head/tail
  reads. Codex indexing prefers current `response_item/message` user/assistant records and
  suppresses their duplicate legacy `event_msg` copies; older event-only transcripts remain
  supported.
- `Scan historical sessions` is an explicit, project-scoped background job. It bypasses the
  startup cap, scans the complete shared native history, fingerprints unchanged transcripts,
  batches serialized SQLite writes, exposes progress/results, and supports cancellation.
  A scan only claims rows with no canonical owner yet: a run's Project is decided at spawn,
  so scanning Project A must not rewrite the history of a session that ran under nested
  Project B. For the same reason startup reconcile leaves an already-assigned row's
  Project label/root alone rather than re-deriving them from Git.
- Provider housekeeping is excluded, not indexed. `<id>.orphaned-<ts>-<hash>.jsonl` still
  reports the original conversation's `sessionId`, so treating it as a transcript maps the
  fragment onto the real conversation's row; the two then alternate ownership of one
  watermark and both re-parse on every startup, with a stale snippet shown as the
  conversation. Claude sidechain (`isSidechain`) records are likewise not root messages —
  indexing them put subagent chatter in history search and let a subagent's clock become the
  run's first/last message time.
- One unreadable transcript never aborts a scan. Provider transcript cleanup and antivirus
  operate in these very directories, so a file can vanish between glob and stat; per-file
  failures are skipped and counted, and the whole-scan task logs its own death.
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
- `src/swe_mux/history_backfill.py`
- `src/swe_mux/transcript_view.py`
- `src/swe_mux/operational_telemetry.py`
- `frontend/src/HistoryBrowser.tsx`
