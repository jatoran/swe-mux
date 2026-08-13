# History and events

## What it is

- SQLite indexes durable Claude/Codex run lifetimes and append-only daemon events. Plain
  shells remain provisional and disappear from history when they exit without promotion.
- Each mux-created history row carries canonical `project_id`; Git repository/scope fields
  remain separate display metadata. Startup-reconciled native transcripts may be unassigned;
  an explicit Project scan assigns only transcripts whose recorded cwd belongs to that
  registered Project, preferring the most-specific registered root.
- The historical `note_id` column remains migration provenance for notes created by older builds.
  History no longer creates, opens, or owns notes.
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
- Searchable user/assistant text lives in rebuildable local SQLite FTS5 token-prefix and trigram indexes.
  Native transcripts remain authoritative and are never mutated.
  The ordinary History UI keeps its session-oriented query path and previous/next match navigation.
  The agent MCP uses a separate message-oriented retrieval path over the same index: it globally ranks message hits, caps hits per conversation for diversity, and returns bounded excerpts before any transcript text crosses the MCP boundary.
  Hybrid matching combines literal token-prefix recall with literal substring recall; explicit all-term, any-term, phrase, and substring modes are also available.
  One- and two-character explicit substring searches use a bounded literal `LIKE` fallback because FTS5 trigram matching starts at three characters.
  Filters compose across user/assistant role, raw or generated title, backend, persisted state, exact run ids, session-start time, and provider-native message time.
  Session and message lower bounds are inclusive and upper bounds are exclusive.
  Provider-native message timestamps are materialized as epoch seconds during indexing so date predicates run in SQLite rather than after retrieval.
  An MCP message hit carries the transcript-index watermark and ordinal; reading around it returns indexed neighboring messages only while that watermark is current.
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
- **Resuming a conversation continues its entry; it does not fork one.** A resumed pane is a
  new process and a new session record, but it inherits the conversation's `agent_run_id`
  (`spawn_agent_run_id`) and reopens that row rather than opening a second. Both CLIs append to
  the same transcript under the same conversation id, so a second row indexed one file twice,
  showed one conversation as two entries, and left the first entry's totals still moving after
  its own pane exited. The row's start, note, totals and transcript watermark are the
  conversation's and are preserved; only what the new PTY changes (argv, cwd, Project, name) is
  refreshed, and the exit markers are cleared. Because every control-plane record keys on the
  run, the scan timeline, Tier 0 facts, annotations and the settled title continue across the
  resume instead of restarting blank.
- **Whether a resume continues the conversation is the adapter's answer**
  (`resume_continues_conversation`), because it is the CLI's own transcript-resolution rule.
  Claude resolves by working directory, so a **resume into a different root** writes a different
  file and is genuinely a new conversation with its own row. Codex resolves by thread id and
  reopens the original rollout wherever the pane runs, so every Codex resume continues its
  entry. Codex was previously assumed to mint a new rollout per resume — true of an older CLI,
  and until it was corrected each resume of a Codex conversation opened another entry over the
  one file (13 surplus rows in five days on the author's install, one conversation indexed twice
  at 95 messages each). Only a genuinely new conversation records a `resume` lineage edge — an
  inherited run is the same run, and an edge to itself would read as a fork that never happened.
- **A conversation with several rows is a repair job, not a display problem.** Row ownership of
  a transcript is decided by `(backend, native_id)` ordered `external, spawned_at, id`, so the
  earliest row — the conversation's own — is the one a reconcile indexes into; without the full
  ordering the winner was whatever SQLite returned first, and the content hopped between
  duplicates across restarts. `GET /api/history/duplicates` reports the rows still split,
  and `POST /api/history/duplicates/repair` (`mux history-duplicates [repair]`, dry by default)
  folds each conversation back into one entry: the keeper takes the latest observation, a rename
  a later pane carried, the widest native timestamp span and the last pane's exit markers, then
  the duplicates and their rebuildable message copies are deleted. It refuses a group whose
  duplicate a live pane is still writing to, never touches native transcripts, and never
  resurrects or absorbs a quarantined row. Merging is explicit because it rewrites entries and
  has no undo, so no daemon start or migration does it. Records outside the history index that
  key on a removed run id (Tier 0 facts, the status timeline) are left as they are, exactly as
  a manual entry deletion leaves them.
- **Resuming lands you in the resumed pane.** The daemon attaches it and makes it its stack's
  active tab; the browser also focuses it, closing the History overlay (and, on a phone, the
  sidebar) so the pane it just focused is actually on screen. Focus is *requested* rather than
  set, because the client learns the new leaf's id from the response but learns where it sits
  only on the next layout refresh — see `ui.md` for why a plain focus in that gap is undone,
  and which other flows share the mechanism.
- Transcript controls occupy a dedicated non-shrinking action bar before verbose run metadata.
  The controls wrap at narrow widths, while metadata wraps inside a bounded scroll region, so
  provider, token, context, and compaction details cannot displace Resume or the transcript.
- **A resumed pane carries the conversation's name, unsuffixed.** The old `"<name> resumed"`
  compounded over repeated resumes (`… resumed resumed`) and, for an inherited run, renamed an
  entry the pane shares rather than replaces. The row's `auto_named` flag carries over too, so
  a conversation nobody renamed stays auto-titleable and a renamed one stays pinned. A pinned
  row resumes under the name the user pinned, not its generated title: the flag arrives from
  SQLite as `0`/`1`, and an `is not False` test matched every row, so a renamed conversation
  came back titled by the titler.
- **Live sessions read their own transcript through a separate route.** The drawer's Transcript
  tab (`ui.md`) uses `GET /sessions/{id}/transcript`, not the history transcript route: the
  history route reindexes a run's searchable messages and loads its annotations on every call,
  which is right for opening an entry once and wrong for a surface that refreshes on observed user
  messages and assistant turn boundaries.
  Both parse the same native files through `transcript_view.py` and neither mutates them; they
  differ in what they reduce to. History indexes everything text-shaped, because search wants
  recall. The reader keeps only what was said, because a reading column wants only the
  conversation.
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
- `frontend/src/TranscriptTab.tsx`, `frontend/src/transcriptView.ts`
- `src/swe_mux/operational_telemetry.py`
- `frontend/src/HistoryBrowser.tsx`
