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
  scopes: all content, user prompts, agent replies, or metadata.
- **One control picks the timestamp the listing is organised by, and it both orders the page
  and scopes the date range.**
  It used to scope only the range, so toggling it re-ran the query and got the same
  `spawned_at DESC` page back - a sort control that did nothing unless a date range happened
  to be set as well.
  The default is **last activity**, because every caller means "the most recent
  conversations": a long conversation spawned yesterday outranks a short one spawned an hour
  ago and abandoned, and the MCP listing scans the same bounded window to rank ended runs
  beside live ones.
  The other basis is session start.
  The two halves use deliberately different expressions (`_HISTORY_ORDER_COLUMNS`,
  `_HISTORY_FILTER_COLUMNS`), because they answer different questions.
  A **filter** may answer "no" for a row with no such timestamp, and must: a range on
  last-message that admitted runs with no messages would not be a range on last-message, and
  an external row that never reported a native start has no known start to compare.
  An **order** may not, because every row on the page needs a position, so it falls back
  through what is known - last message, then exit, then native start, then spawn.
  Ordering on the nullable column instead would collect every unindexed conversation at one
  end of a listing it belongs in the middle of.
  The page's cursor is that order's own answer rather than a second expression, so "Load
  more" continues the list instead of resuming a differently-ordered one.
- **The Project headings follow the feed, not the alphabet** (`historyFeed.ts`).
  A heading appears where its most recent conversation does and its rows stay in the order
  they arrived in, so changing the sort moves the top of the list.
  Sorting headings by name was the other half of "changing the sort does nothing": the rows
  under each heading did reorder, and the alphabetically-first Project stayed pinned above
  them all. The Project dropdown is how a reader asks for one Project; the headings are
  orientation. The unassigned bucket still sorts last, being a catch-all rather than a
  Project.
- **A search asks each index a number of questions that does not depend on how many
  conversations it finds, and every search is bounded.**
  The cost of a search is a query *shape*, and the two shapes that were wrong were both
  invisible on any fixture and quadratic on a real archive.
  A **correlated** `EXISTS (... MATCH ...)` re-runs the full-text scan once per candidate
  conversation - and the page's `ORDER BY` needs every row before it can sort, so "per
  candidate" means the whole archive; the uncorrelated `IN (SELECT ...)` the page uses now
  materializes the id set once.
  A **statement per result row** for the page's excerpts asks the same question fifty times
  and keeps a fiftieth of each answer, because a `MATCH` restricted by `history_id` still
  scans the entire match set: the restriction is on the joined table, not on the index.
  One statement fetches the page's excerpts and the per-row caps are applied in Python,
  where a global `LIMIT` would have starved the page's later rows.
  Measured 2026-09-04 on a 3.07 GB archive of 5,735 conversations: 5.59 s → 0.033 s for the
  page query, 2.9 s → 0.052 s for its excerpts.
- **Every search carries a wall-clock budget; a listing carries none.**
  The exemption the browser's own search used to hold rested on a premise the daemon
  disproves: that a human watching a spinner can abandon the query.
  Abandoning the *view* releases nothing.
  Nothing above SQLite can stop a running statement, and aiohttp does not cancel a handler
  when its client disconnects, so an unbounded search outlived its reader while holding the
  single history executor thread that every other history read queues behind.
  A page with no query is index-driven and cursor-keyed, has no scan to bound, and a budget
  there could only ever refuse an ordinary listing on a slow disk.
- **Searches run one at a time, and one whose reader has gone does not run at all.**
  A search box fires per keystroke pause, so a typed phrase queues several searches of which
  only the last is wanted.
  Superseded ones wait on the event loop, which costs nothing, and are dropped at the front
  of the queue when their connection has closed - which is what makes the browser's own
  abort meaningful, since the abort by itself stops nothing in the daemon.
  On 2026-09-04 five searches typed in nineteen seconds each ran to completion: they took
  20 s, 68 s, 65 s and 55 s, and behind them `/api/sessions` took 46 s and live agents' hook
  posts took 50 s. The daemon read as frozen, and the reader saw an empty result list for a
  minute - which is what "search is broken" turned out to mean.
- **A search with no word characters matches no messages rather than quietly matching
  something else.**
  `MATCH ''` is an FTS syntax error, so a query like `???` has nothing to look for in the
  message index; a message-scoped search says so by returning nothing. It used to fall
  through to a *metadata* search, which answered a question about names and paths while the
  scope said "user prompts". Metadata is a literal `LIKE` and still answers for the scopes
  that include it.
- Searchable user/assistant text lives in rebuildable local SQLite FTS5 token-prefix and trigram indexes.
  Native transcripts remain authoritative and are never mutated.
  The ordinary History UI keeps its session-oriented query path and previous/next match navigation.
  The agent MCP uses a separate message-oriented retrieval path over the same index: it globally ranks message hits, caps hits per conversation for diversity, and returns bounded excerpts before any transcript text crosses the MCP boundary.
  Hybrid matching combines literal token-prefix recall with literal substring recall; explicit all-term, any-term, phrase, and substring modes are also available.
  One- and two-character explicit substring searches use a bounded literal `LIKE` fallback because FTS5 trigram matching starts at three characters.
  Filters compose across user/assistant role, raw or generated title, backend, persisted state, exact run ids, session-start time, and provider-native message time.
  Session and message lower bounds are inclusive and upper bounds are exclusive.
  Provider-native message timestamps are materialized as epoch seconds during indexing so date predicates run in SQLite rather than after retrieval.
  Upgrades add the epoch column without rewriting existing messages during daemon startup.
  Post-startup maintenance resets both external-content FTS indexes and materializes old timestamps in resumable 250-row transactions.
  Search remains complete during that repair through a bounded literal `LIKE` fallback and reports whether ranked indexes are ready.
  FTS update triggers run only when searchable text is in the `UPDATE` statement, so timestamp and parser metadata changes cannot delete and reinsert index terms.
  An MCP message hit carries the transcript-index watermark and ordinal; reading around it returns indexed neighboring messages only while that watermark is current.
- **The token index carries a prefix index, `prefix='1 2 3'`** (`_FTS_PREFIX_OPTION`).
  Every search word is a prefix query, and between keystrokes the word being typed is one or two characters long: without the index, each such word walked every term in the archive that starts with it.
  Measured 2026-10-12 on a 120k-message sample of the live archive: `"s"*` went from 88 ms to 2 ms and `"contact"* AND "s"*` from 85 ms to 1 ms, for an index about 42% larger.
  `prefix='2 3'` alone left the one-character case at 38 ms.
  An FTS5 table's options are fixed when the table is created, so an archive indexed by an earlier build is recognised from the definition stored in `sqlite_master` and rebuilt through the same post-startup reset as any other index repair.
  The reset drops and recreates the token table, both indexes are repopulated in 250-row batches, and search answers through the bounded `LIKE` fallback until they are ready.
  The browser also waits 450 ms rather than 220 ms before sending a search whose last word is one letter (`historySearchDelay`), because that search is the broadest a typist sends and the one most certain to be replaced.
- **A per-conversation match lookup walks only that conversation's matches.**
  A run's messages are written by one `executemany` in one transaction, so their row ids form one contiguous range, and FTS5 takes a `rowid BETWEEN` constraint into its own index walk.
  The page's excerpts are one statement made of one ranked sub-select per conversation, each bounded by that conversation's range, and the ranges come from one `GROUP BY` statement for the whole page (`_message_id_ranges`).
  A reader's in-conversation matches are one range-bounded statement too.
  Filtering on `history_id` alone was applied after FTS5 had produced, and `bm25` had ranked, every match in the archive: 145-550 ms per page and 200-320 ms per open for a common prefix, measured 2026-10-12.
  The statements still filter on `history_id`, so a range that ever included another run's row would cost speed, never correctness.
- Session rows show the chronological minimum and maximum provider-native conversational
  timestamps plus the final speaker, so out-of-order native JSONL records cannot produce a start
  after the last message. Transcript cards show each provider-native message timestamp.
  Missing/invalid native message timestamps remain unavailable; process exit and file mtime are
  never presented as message time.
- **Resume has one implementation, in `session_resume.py`.** The Resume button and a schedule
  whose action is `resume` (`scheduled-runs.md`) both call it, so the refusals below are the same
  five refusals worded the same way for both. Only what the two genuinely differ on stays with
  the caller: the route attaches the pane beside the one the operator was looking at and answers
  HTTP, while a schedule has no such pane and records a run row instead. A second resume path
  would be a second authority, and the one that drifted would do so unattended.
  `GET /history/{id}/branch-points` is the same reason in the other direction: the cut points a
  scheduled fork pins are chosen from a History row, whose pane usually ended long ago, and they
  must be the cut points the interactive picker would offer.
- Resume requires a valid target Project, native ID, transcript, cwd record, and adapter. It
  creates a new Project-owned session at the target root and atomically updates its layout.
  A conversation a live session currently claims is refused (`409 conversation_live`, naming
  the owning session): resuming it would put two live sessions on one conversation — the
  cross-attribution the identity invariant forbids. Branch is the flow for opening a second
  conversation from a live one; rows whose pane has since rolled onward resume fine.
- **A branch is a new conversation, so it opens a new row.** A `transcript_fork` branch resumes a
  conversation file mux has just written and nothing has ever held, so it inherits no run and
  claims no existing entry - the opposite of a resume, which continues its conversation's row.
  The two are related by a `branch` lineage edge naming the message the fork was cut at
  (`sessions.md`), which is the only record that they share a prefix at all.
- **The Work lineage section reads in the direction of the entry it sits on.** An edge names two
  runs, but the reader is standing on one of them, so it renders as "Branched from X" or
  "Branched into Y" rather than as `X -> Y`. The other end is named the way every other surface
  names it and opens on click (`viewById`).
- **Naming both ends is the daemon's job** (`_decorate_lineage_endpoints`). Each end is a *run* in
  one of three states the browser cannot see: a live session names itself through the session
  manager, an ended one through its History row, and a deleted one has neither. A client holding
  one page of results has none of those for the far end of an edge, which is how the section came
  to print raw ids. A deleted row is reported as `known: false` rather than dropped, because the
  edge still records that the fork happened and removing it would silently reshape the lineage.
- **A branch edge keeps a bounded excerpt of the message it was cut at**, written when the branch
  is made rather than resolved from `cut_offset` on demand. The only reader is a human asking
  where a conversation came from weeks later, by which time the parent transcript may have been
  compacted, relocated by a cwd change, or deleted; re-reading a whole conversation to render one
  line is the wrong shape even when it is still there. Branches made before the transcript-fork
  rewrite carry no cut at all - the fork was the CLI's and mux never saw a message id - so they
  render as the relation alone rather than an invented position.
- **A conversation held by a process mux does not own is refused too**
  (`409 conversation_held`, naming the holder's kind, pid and job).
  A CLI opens a conversation once and answers a second opener by exiting, so such a resume
  produces a pane that dies about 1.5 s after the request returned 201.
  The holder is read live from the CLI's own per-process state files
  (`cli_state.conversation_holders`), never stored, because ownership ends when that process
  does.
  The case that produced this: Claude parks a conversation into a background agent, which
  outlives the pane that parked it and keeps the conversation checked out under the CLI's own
  daemon.
  History rows carry `held_by` while it lasts, so the listing states the fact instead of
  offering a Resume action that cannot work.
- **A resumed pane is proved to have survived before it is handed back**
  (`spawn_probe.py`, 2.5 s, two attempts, `503 resume_failed`).
  A refusal mux cannot predict — a changed CLI message, a conversation another terminal opened a
  moment ago — still reaches the operator as the pane's own dying output rather than as a grey
  pane with no reason.
  The window ends early on positive proof that the pane took the conversation (its own pid
  against the conversation in the CLI's state file), so only harnesses that publish no such
  state pay it in full.
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
  and `POST /api/history/duplicates/repair` (`swemux history-duplicates [repair]`, dry by default)
  folds each conversation back into one entry: the keeper takes the latest observation, a rename
  a later pane carried, the widest native timestamp span and the last pane's exit markers, then
  the duplicates and their rebuildable message copies are deleted. It refuses a group whose
  duplicate a live pane is still writing to, never touches native transcripts, and never
  resurrects or absorbs a quarantined row. Merging is explicit because it rewrites entries and
  has no undo, so no daemon start or migration does it. Records outside the history index that
  key on a removed run id (Tier 0 facts, the status timeline) are left as they are, exactly as
  a manual entry deletion leaves them.
- **A recorded `transcript_path` is where the file *was*, and `transcript_repair.py` is what
  makes that survivable.**
  The CLI owns these files and moves them: Claude re-homes a conversation into the project slug
  for a new working directory when a session enters or leaves a native worktree, reports the move
  through its hook, and the daemon follows it - correctly, with the file's own first record
  proving the identity.
  What the row keeps afterwards is that moment's address, so a file that moves once more on the
  way out leaves the row wrong forever, with the follower that would have noticed dead alongside
  the pane.
  Measured on the development host on 2026-09-02: 301 of 1420 mux-owned agent rows named a file
  that was not there, 131 of them a `--claude-worktrees-` slug, and all 132 recoverable ones still
  on disk under their own conversation id.
  Three layers now keep the row honest, and no layer makes another redundant:
  - **Session end settles the address**, because that is the moment it stops moving, and the same
    resolution decides what the final index reads.
  - **Every row-based read resolves before it refuses** — the transcript route, Resume (so the
    scheduler's resume too), branch points, and a fork — and writes the repair back, so one miss
    on any surface heals the row for all of them.
    A dead pane's own reader (`GET /sessions/{id}/transcript`) searches too, since its observer
    died with its process; a live pane's does not, because an agent that has not written its first
    record yet is the ordinary early state of every agent session and its observer is already
    resolving the binding.
  - **The History scan repairs the row it finds**, rather than walking past a mux-owned row whose
    file it is holding the location of.
  The search is `Adapter.locate_transcript` and is safe for the reason that method is safe: mux
  dictates the conversation id at spawn, so a file named after it is that conversation's by
  construction.
  It is also the expensive answer (a directory probe per project slug), so it runs only after the
  recorded path has already missed, and a located path is verified before it is believed — Codex's
  implementation computes rather than searches, and an unchecked answer would record a second dead
  path as a repair.
  A repair clears every watermark on the row and its transcript-index cursor, because each of them
  measured a file the row can no longer see and a stale cursor lets the next pass conclude that
  nothing changed; the indexed messages stay, being the same conversation at a new address.
  It emits `history_transcript_repaired`.
- **Resuming lands you in the resumed pane, from the click rather than from the answer.**
  A resume is proved rather than reported (2.5 s, up to about six with the retry), and awaiting
  that before touching anything left the overlay open over a workspace where nothing was
  happening.
  So the browser places a **pending pane** for the conversation immediately, focuses it, and
  closes the History overlay (and, on a phone, the sidebar) on the press; the real session
  replaces the placeholder when the daemon answers, and a refusal removes it and hands focus back.
  This is the same placeholder mechanism a spawn and a worktree launch use (`ui.md`), with one
  difference: the daemon attaches the resumed pane in its own layout, so the browser never writes
  a layout back for a resume - it swaps the placeholder locally and the refresh that carries the
  daemon's layout finds the same pane already in place.
  Placing the pane is also what makes the *phone* land on it: the mobile projection has no
  equivalent of the desktop reconciler's outstanding focus request, so a focused id no layout held
  yet fell through to whatever tab the pane was already showing.
  The daemon still attaches the pane and makes it its stack's active tab, and desktop focus is
  still *requested* rather than set, because the client learns the new leaf's id from the response
  but learns where it sits only on the next layout refresh — see `ui.md` for why a plain focus in
  that gap is undone, and which other flows share the mechanism.
  A **retained pane's** own Resume gets no placeholder: that pane is already on screen and
  focused, and replacing its retained scrollback with a splash would take a readable post-mortem
  away for the seconds the proof takes. Its banner button reports the attempt and refuses a
  second one instead (`session-recovery.md`).
- **A conversation opens on the row that was clicked, and its text is read out of the message index a page at a time.**
  The reader used to wait for `GET /history/{id}/transcript`, which parsed the entire native file, rewrote the run's indexed messages and shipped every tool input, before it drew anything - Resume included.
  Measured 2026-10-12: 1,462 ms and 1.36 MB to open a 6.9 MiB Claude conversation, and 1,329 ms and 4.82 MB for a 48.7 MiB Codex rollout, while the text it waited for was readable from `history_messages` in 2-9 ms.
  - The header, the action bar and Resume draw from the list row on the click.
  - `GET /history/{id}/detail` refreshes the row and reports what the message index holds, without parsing.
  - `GET /history/{id}/messages` pages the indexed text by ordinal.
    Matches, run notes, lineage and commits each arrive on a request of their own, and nothing waits on anything else.
  - The reader is mounted per conversation, so opening another one clears the previous one at once and aborts its requests.
- **An index that is missing or behind its conversation is rebuilt in the background, never on the reader's path** (`HistoryIndex.transcript_index_state`).
  `/detail` compares the index's watermark with the conversation's, schedules a reindex when they differ, and answers with `transcript.indexing`.
  The reader shows what is indexed, polls `/detail` with a backoff, and reloads where the reader is when the reindex finishes.
  The reindex is single-flight per conversation, and it re-checks the watermark before it parses, because a session end or a reconcile may have indexed the conversation while the job queued.
  It parses outside the shared transcript cache, so a reindex cannot evict a live reader's entry or pin a large conversation in memory.
  A conversation is reindexed at most once per 30 s (`REINDEX_COOLDOWN_SECONDS`), because a live one goes stale again the moment it is indexed and a polling reader would otherwise reparse it on every poll.
  The jobs run on thread pools rather than as event-loop tasks, so a job that outlives its request can never call back into a loop that has closed, and `HistoryIndex.close` drains them before it closes the database.
  An index written before the abandoned-message count existed is reindexed once on open, which is how `abandoned_messages` and `tool_calls` reach older conversations.
  `GET /history/{id}/transcript` remains for callers that want a whole conversation, and it no longer rewrites an index whose watermark already matches the parse it holds.
- **The reader holds a bounded window of the conversation** (`historyMessages.ts`).
  Pages of 60 are fetched as either edge of the window comes within 600 px of view, and past 360 messages the far edge is let go, so scrolling a 5,000-message conversation on a phone does not leave 5,000 rendered articles behind.
  A page that no longer touches the edge it was asked for is dropped rather than joined, because joining it would leave a gap that nothing fills.
  The reader keeps its own place when a page lands above it, by hand: Safari has no scroll anchoring and Chromium skips anchoring at scroll position 0, which is exactly where a fling lands.
  A search match outside the window replaces the window with the page around the match, so stepping to the fortieth match of a long conversation is one page read rather than a scroll through everything before it.
  Opening a conversation from a search jumps to its first match; a reload under a finished reindex keeps the reader where they were.
- **Tool calls are the one History read that still parses**, because their inputs are deliberately not indexed (`GET /history/{id}/tool-calls`).
  They are fetched only while the toggle is on, only for the ordinal span the window covers, and never twice for one span; spans the window leaves are let go.
  The read goes through the cached parse, so paging through one conversation with the toggle on parses it once.
  A message that is nothing but tool calls has no indexed text, so the route returns it with `has_text: false` and the reader places it at its own ordinal.
- **An open History browser re-renders only for what it draws.**
  It is mounted in the app root, which re-renders on every session status event in the fleet.
  The browser, its result rows and its messages are memoized, and the root hands it a memoized Project list and ref-backed handlers whose identities do not change when nothing the browser shows has.
  Result rows skip offscreen layout and paint (`content-visibility: auto`).
  Messages do not, because a message first drawn above the view changes height there, and the reader's hand-kept place would not account for it.
- **Another surface can open History on one conversation.**
  The overlay accepts an entry id and opens that conversation directly instead of a filtered list, which is how a surface that already knows *which* session it means (the Git tab's session links and a recovered pane's transcript action) reaches an ended one.
  The request is unscoped on purpose: the row is named by id, and pre-filtering to a Project would hide a conversation that belongs to another one.
  A deleted row is the one miss left (404), and it is reported as itself with the browser left open on its list, so the reader can still search by hand.
  A conversation whose transcript is gone still opens, on what was indexed, with `transcript.available: false`, and Resume is withheld there because it would be refused.
  That state says something definite: `/detail` searches for a conversation whose recorded path went stale before it answers, so reaching it means no file on disk answers to that conversation id at all.
  The overlay clears its deep-linked entry when it closes, or the next plain "Session history" open would land on a conversation nobody asked for.
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
  tab (`ui.md`) uses `GET /sessions/{id}/transcript`, not the History routes: History reads a
  run's indexed copy and keeps it current in the background, which is right for an ended
  conversation and wrong for a surface that refreshes on observed user messages and assistant
  turn boundaries.
  Both parse the same native files through `transcript_view.py` and neither mutates them.
  History indexes everything text-shaped because search wants recall.
  The reader keeps conversational prose as its default projection.
  Neither indexes a turn the conversation branched away from: a Claude transcript is an
  append-only DAG, and a prompt resent through an outage would otherwise be indexed once per
  attempt (`transcript-branches.md`). The drawer's reader shows those turns folded and marked;
  history reports only how many there were.
  Both transcript views can additionally disclose native tool names and input arguments behind a default-off toggle.
  Tool results, operational telemetry, and extra transcript persistence are excluded from that disclosure.
- **The single-conversation view is transcript-first, and every band collapses - including the
  transcript.**
  The detail view is an action bar over six bands - the run's figures, its commits, its work
  lineage, its run notes, its behavioural timeline, and the conversation itself - and each band
  is a disclosure whose closed line already says what a reader would open it to find: the state,
  model, final message time and cost for the run; the number of links, notes, timeline entries,
  or messages.
  **A conversation opens on its transcript and on nothing else**, on every device.
  Every other band is metadata about the run, its closed line already carries the fact most
  readers want, and expanding is one click - so the cheaper default is closed, and the
  conversation outweighs every band above it put together.
  Only the transcript grows; every metadata band is bounded and scrolls when open, so a run with
  forty commits cannot push the conversation off the view by being expanded.
  Defaults are applied when a conversation is opened rather than tracked on a media query, so a
  reader who expands a band keeps it expanded while they read and a window dragged across the
  mobile breakpoint does not fold up what they were reading.
- **The Commits band is the one whose closed line is suppressed.** Every other closed line is a
  count, which is one short line; Commits carries a whole commit subject, so on any realistic
  width the "collapsed" band wrapped to a taller block than several of the bands it sits between
  - the opposite of what collapsing it was for. Open, its rows say the same thing per commit.
- **The transcript's control is a sibling of the conversation, not a `<details>` around it.**
  A `<details>` interposes a UA-owned content box between the element and its children, and that
  box is the flex item, so a conversation inside one sizes to its own text and leaves the bottom
  of the view empty. The band and the conversation are tied together by `aria-controls` instead,
  and the band is drawn to match the five above it exactly.
- **The behavioural timeline previews its two newest entries.** The rest are behind a second
  disclosure inside the band, and the preview is sorted here rather than trusted from
  `GET /history/{id}/notes`, which returns a run's records oldest-first: a mis-ordered preview is
  indistinguishable from a run that did those things in that order.
- **A commit, a lineage link, a run note, and a timeline entry are all one row that opens.**
  Commits and lineage were a sideways strip of fixed-width cards, which put the subject - the
  only part anyone scans for - behind a horizontal scroll on every viewport.
- **Back to results lives in the detail view's own top bar.** It used to be a full-width row
  above the title on a phone, which cost the transcript a band of chrome on every conversation
  opened, and did not exist at all on desktop.
- **The filter block is a wrapping row of controls sized to their own widest option.**
  `Dropdown` already reserves exactly that width, so stretching each filter to an equal column
  made a four-item list occupy half the sidebar for nothing.
  A floor keeps a short list from collapsing to a stub and is capped at the container, so a
  narrow sidebar wraps rather than overflowing.
- **The cross-vendor review button is gone from the detail view**, and with it the review
  preview dialog it was the sole entry point to.
  `POST /history/{id}/second-opinion` remains a supported daemon route with its own tests; it
  simply has no in-app caller.
- **Phase 7.7 scan-timeline surfaces.** `GET /history/{id}/notes` (and the whole-transcript
  payload) carries the run's `scan_records` alongside its annotations, so the Run-notes view renders the behavioral spine; the
  handoff export (`GET /history/{id}/handoff`) is regenerated phase-structured from that spine when
  the run's Project opts into `timeline_handoff`, falling back to annotation summaries otherwise; and
  the second-opinion prompt sources its prior-run summaries from the spine. The turn summarizer that
  used to write those summaries is retired (`automation.md`, `scan-timeline.md`).
- Index deletion never deletes or edits the native transcript.
- Removing a Project registration never deletes its History rows.
  The tombstoned Project row retains the stable name and canonical root used by History grouping, and the History UI marks that group as removed.
  Re-registering the same canonical root restores the original Project identity, so its existing conversations require no reassignment or reimport.
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
- **A listing answers within a bounded wait and ships what the list draws.**
  A row whose conversation changed since its time summary was read is re-read from a bounded head and tail on a worker pool.
  The listing waits at most 250 ms for those reads (`TIME_SUMMARY_WAIT_SECONDS`), and the rest land in SQLite for the next listing.
  The pair stored against a summary is the conversation's watermark (`conversation_watermark`), which is the pair it is compared against.
  It used to be the file's `stat`, which a Codex rollout's `(0, total)` watermark never equals, so every listing re-read every Codex row (122 rows on the development host, 2026-10-12).
  A listing selects explicit columns without `argv_json`, and the route trims `generated_title_annotation` to its `stability`: the two were 66% and 16% of a 199 KB page.
  The single-row read keeps `argv_json`, because Resume reads it from there.
- Codex indexing prefers current `response_item/message` user/assistant records and
  suppresses their duplicate legacy `event_msg` copies; older event-only transcripts remain
  supported.
- `Scan historical sessions` is an explicit, project-scoped background job. It bypasses the
  startup cap, scans the complete shared native history, fingerprints unchanged transcripts,
  batches serialized SQLite writes, exposes progress/results, and supports cancellation.
  A scan only claims rows with no canonical owner yet: a run's Project is decided at spawn,
  so scanning Project A must not rewrite the history of a session that ran under nested
  Project B. For the same reason startup reconcile leaves an already-assigned row's
  Project label/root alone rather than re-deriving them from Git.
- Both the startup reconcile and the on-demand global scan are scoped to the enabled
  harnesses (`harness.enabled_backends`, resolved from `config.harness_enabled` and detection;
  `features/backends.md`). A disabled harness's own past conversations are simply not indexed
  that run and are picked up on the next scan after it is enabled. The scope is an import
  filter only, not a capability one: an already-indexed conversation on a now-disabled harness
  still renders in History.
- The global scan is `HistoryScanManager` (`src/swe_mux/history_scan.py`), the interruptible,
  user-triggered counterpart of the silent startup reconcile. It runs one scan at a time,
  reports `{status, phase, backends, scanned, processed, imported}`, and supports cancellation
  through `reconcile_external_history`'s `should_cancel`/`on_progress` plumbing. It exists
  because a first import can be expensive on a machine holding tens of thousands of transcripts,
  so importing is opt-in and interruptible rather than a startup stall.
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
- A cancelled scan stops walking, and it stops within one file rather than at the end of the
  tree.
  `scan_external_transcripts` polls `should_cancel` in both halves - the discovery walk and the
  indexing pass - and `scan_external_transcripts_async` is the only seam through which the walk
  is put on a thread, because it is what turns a cancelled coroutine into something the worker
  thread can see.
  Cancelling a bare `asyncio.to_thread` abandons the worker rather than stopping it, and the
  event loop joins every abandoned worker in `shutdown_default_executor` at the very end of
  shutdown: a startup reconcile still walking made the daemon wait out a full scan of the user's
  transcript tree after the last log line already claimed a clean stop.
- EventBus persistence precedes fanout; reconnect catch-up uses monotonic sequence IDs.
- Current context telemetry remains on live sessions and history. Explicit provider-native
  compaction records increment durable count/last-time/capability/confidence summaries;
  token drops alone never count as compaction.
- A separate bounded reconciliation indexes explicit Claude/Codex tool results, durations,
  skill invocations, compactions, and unknown/parser coverage across recent histories. Native
  source identities deduplicate hook/transcript copies.

## Key files

- `src/swe_mux/history.py` (the index, its background reindex and time-summary pools, `read_history_conversation` as History's one parse seam)
- `src/swe_mux/routes/history.py` (the listing and the reader's routes)
- `src/swe_mux/event_bus.py`
- `src/swe_mux/reconcile.py`
- `src/swe_mux/history_backfill.py`
- `src/swe_mux/history_scan.py`
- `src/swe_mux/transcript_view.py` (branch linearization: `transcript-branches.md`)
- `src/swe_mux/transcript_fork.py`
- `src/swe_mux/transcript_repair.py` (finding a conversation the CLI moved, and repairing its row)
- `frontend/src/TranscriptTab.tsx`, `frontend/src/transcriptView.ts`
- `src/swe_mux/operational_telemetry.py`
- `frontend/src/HistoryBrowser.tsx`, `frontend/src/historyDetail.ts`, `frontend/src/historyMessages.ts` (the reader's window), `frontend/src/historyFeed.ts`
