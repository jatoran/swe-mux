# Shared SQLite concurrency

## Why this exists

History, Automation, Operational Telemetry, Status Timeline, Voice, Tier 0, Prompt Queue,
Schedules, and Clipboard
history use separate
connections and serialized executors against one WAL database. SQLite still has one writer slot. A transaction left open on
one connection can otherwise make unrelated session spawn or PTY event writes fail with
`database is locked`.

## Operation contract

`src/swe_mux/sqlite_store.py` coordinates every store operation with a process-wide `RLock`
keyed by the resolved database path. Each participating store also uses one worker thread so its
connection is never used concurrently.

For every submitted callable:

1. acquire the database-path operation lock;
2. run the callable on that store's worker/connection;
3. if it raises and a transaction is open, roll back before releasing the lock;
4. if it returns with a transaction still open, roll back and raise an invariant error;
5. release the lock only after the connection is transaction-clean.

Connections enable WAL, `synchronous=NORMAL`, and a bounded busy timeout. Those settings
reduce contention but do not replace the operation lock or explicit transaction end. Foreign
keys are **not** enabled (sqlite3 defaults them off and no store turns them on), and no schema
declares one — a `FOREIGN KEY ... ON DELETE CASCADE` added today would be silently unenforced.

## Schema versions and a corrupt file

- Schema versions live in a shared `schema_versions(store, version)` table, read and written
  through `sqlite_store.read_schema_version` / `write_schema_version`. **Never
  `PRAGMA user_version`**: it is a property of the file, and several stores share `mux.db`,
  so each one stamping it meant the last connect won and every store read a neighbour's
  number — a mechanism that looked armed while being unusable.
- Every store opens through `sqlite_store.connect_or_quarantine`. It probes the file on a
  throwaway connection first (closed before anything moves it, because Windows will not
  rename a file this process holds open), and on corruption renames `mux.db`/`-wal`/`-shm`
  aside as `.corrupt-<ts>` and recreates the schema. Almost everything here is rebuildable
  derivative data — native transcripts remain authoritative — while a malformed file used to
  raise out of store construction and take the daemon down at startup, which under the
  desktop shell presents as an app that simply refuses to come up.
- **The probe is answered once per database file per process** (`sqlite_store.verify_database`,
  warmed by `prepare_database`). `PRAGMA quick_check` reads every page, so its cost is the size
  of the file, and eleven stores share `mux.db`. Measured 2026-08-21: 11.5s per pass against a
  2.73 GB `mux.db`, so probing per *store* spent ~126s of every daemon start re-answering a
  question about a file that had not changed between the answers — the largest single component
  of a measured 226.6s startup, and invisible, because a passing probe logs nothing.
  The verdict is a property of the file rather than of the store, so caching it is the stricter
  reading, not the looser one: after a corrupt file is quarantined and recreated, the later
  stores were probing a *different* file from the one the first store judged. The replacement is
  recorded as healthy explicitly (`_remember_integrity`), because a cached "corrupt" verdict
  there would quarantine the fresh file too.
  The daemon pays for it under its own startup phase, `database-integrity`, on a worker thread
  (`asyncio.to_thread`) so the health endpoint and the startup watchdog keep answering while it
  runs, and logs the elapsed seconds and the file size whenever it exceeds a second — this cost
  grows with the database and is meant to stay visible as it does.
- Writes name their columns. A positional `INSERT ... VALUES(?,…)` breaks the moment a column
  is added, and the redeploy flow keeps a roll-back-able previous bundle whose copy of the
  code would then fail on every write.

## Write patterns

Correct single-statement write:

```python
def op():
    cursor = self._db.execute("INSERT INTO events (...) VALUES (...)", values)
    self._db.commit()
    return int(cursor.lastrowid)

return await self._run(op)
```

Correct multi-step write:

```python
def op():
    try:
        self._db.execute("BEGIN IMMEDIATE")
        # validate and apply the complete state transition
        self._db.commit()
    except Exception:
        self._db.rollback()
        raise
```

Incorrect:

```python
def op():
    self._db.execute("INSERT OR IGNORE INTO evidence (...) VALUES (...)", values)
    if duplicate:
        return None  # may return while SQLite still owns an implicit transaction
```

A uniqueness violation is sometimes the *mechanism* rather than an error to swallow.
`schedule_runs(schedule_id, fire_key)` is unique, and `ScheduleStore.claim_run` inserts that row before a scheduled session is spawned: the losing insert raises `IntegrityError`, rolls back, and is surfaced as a typed `ScheduleConflict` the caller treats as "already claimed" (`../../design/features/scheduled-runs.md`).
That is what makes a fire idempotent across a daemon restart, which no in-memory guard can be.

`LandStore` uses the same mechanism twice, through **partial** unique indexes scoped to a subset of states (`../../design/features/land-queue.md`).
`land_requests_active(project_root, branch) WHERE state IN (<live states>)` makes enqueue itself the claim, so an agent asking twice for one branch gets a typed `LandConflict` rather than a second pipeline over one worktree.
`land_requests_inflight(project_root) WHERE state IN (<running states>)` is the stronger of the two: it makes "one land at a time per trunk" a property of the schema, so the losing `UPDATE` into a step state raises `IntegrityError` even if two workers both concluded they should proceed.
A partial index is what allows both: the same `(project_root, branch)` pair recurs freely across finished requests, and only the live ones are constrained.

`LandStore` reached schema version 2 with `land_verify_plans(project_root, digest)` - what a verification gate's steps were the last time those exact bytes passed, so a running one can honestly report "step 3 of 7".
It is a new table under `CREATE TABLE IF NOT EXISTS` rather than a column migration, so an older database gains it on the next open and a database written by a newer build loses nothing when an older one opens it.
The row is upserted, never accumulated, and only a *passing* run writes one (`../../design/features/land-queue.md` explains why a failing run's step list would poison the prediction).

Version 3 added `land_requests.verify_gate`, which is a **column** and therefore needs the `PRAGMA table_info` check that `LandStore._migrate` runs: `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists, so a column added to the schema string alone reaches a fresh install and no other.
It is backfilled to `''` - "this row was never classified" - rather than to `'full'`.
Every pre-migration land did run the full gate, but that is a fact about history rather than about the column, and this is the one field whose entire job is making a *skipped* gate visible: a row asserting a classification nothing recorded is the wrong direction for it.

Version 4 added `land_requests.armed_replies` through the same check, defaulted to 0, which is the truth for every pre-migration row: nothing could arm a handback then.
It is spent by a conditional `UPDATE … WHERE armed_replies<?` rather than a read-then-write, so the per-request cap on unattended handbacks is a claim two sweeps cannot both win (`../../design/features/land-queue.md`).

Version 5 added `land_requests.kind` through the same check and the `land_verify_memos` table under `CREATE TABLE IF NOT EXISTS`.
`kind` is backfilled to `'land'`, and here that is a fact about the *column* rather than only about history: nothing could ask for anything else, so the backfill states what each of those rows actually asked for.
`land_verify_memos(project_root, tree_oid, digest)` is a gate verdict that already stands - the git tree the gate ran over and the digest of the command that ran, which are the whole of what decides one.
The **tree** rather than the commit, because a reconcile that merged an unchanged trunk produces a new commit over identical content, which is exactly the case a commit-keyed row would miss; and the row is upserted rather than accumulated, like a plan.
Only a run the queue executed writes one, and the store offers no other writer - an agent's own shell run is self-reported and never accepted (`../../design/features/land-queue.md`).
Reads take a `not_before` floor rather than the table carrying a sweep: a verdict's freshness is a question the caller's configuration answers, and expiring rows out of the table would destroy the audit of what was reused when.

`AutomationStore` reached schema version 13 with index-only additions: two composite indexes
carrying `scan_timeline_records`' `ORDER BY t0` (`(project_id, t0, created_at)` and
`(agent_run_id, t0, created_at)`, both ascending, because SQLite walks an index in either
direction) and the four retention indexes.
Indexes need no `PRAGMA table_info` migration — `CREATE INDEX IF NOT EXISTS` in the schema script
reaches an existing database on the next connect — but the version still moves, because a reader
comparing two installs has to be able to tell them apart.

`queue_messages.position` is gap-free across a target's visible rows, and only the operations that
actually move rows renumber: an anchor insert, a reorder, and a delete.
A tail append takes `MAX(position)+1` and writes nothing else.
It used to read every visible row and rewrite each one's position with the value it already held —
O(n) writes per append on a surface whose normal use is appending.

`idx_queue_messages_correlation` is a partial unique index over
`(sender_kind, sender_id, correlation_id)`, and it is **not** what enforces retry-safe correlation.
SQLite treats NULLs as distinct inside a UNIQUE index, so two rows from a NULL-`sender_id` sender —
which is every sender that is not a session, `rule` and `assistant` included — both satisfy it.
The guard that dedups is `create_message`'s SELECT-before-INSERT, which compares
`IFNULL(sender_id,'')`.
The index is a real constraint for non-NULL senders and a lookup index for the rest.

`PromptQueueStore` reached schema version 6 with `queue_messages.solicited_by`, added through its own `PRAGMA table_info` migration list and nullable, because every pre-existing row was unsolicited by construction.
It is the per-message half of the arming floor: a non-human sender other than `agent` may be staged `armed` only when it names the target's own request here (`../../design/features/agent-messaging.md`).
Stored rather than derived for the reason the floor exists - arming must never be the sender's claim, so a row that arrived armed has to be able to name what asked for it.

`VoiceStore` reached schema version 2 with `voice_clips.source_ts` and `voice_clips.message_anchor`, the message a clip speaks and when that message arrived, both added through the same `PRAGMA table_info` check and both nullable.
They backfill to NULL rather than to `created_at`, which is the truth and not merely the cautious choice: a clip made before the anchor existed has no source message, and giving it a synthesis-time one would assert exactly the ordering the column exists to fix (`../../design/features/voice.md`).
Two ordering rules follow it. The list query is `ORDER BY COALESCE(source_ts, created_at) DESC, created_at DESC`, so a pre-migration row and a piece of application speech both fall back to synthesis time while a real reply sorts by arrival, and every segment of one streamed reply - which shares one anchor and one source time - stays in the order it will be spoken.
The migration also carries the store's one **index ordering hazard**: the anchor index covers `message_anchor`, so `SCHEMA` was split and its `CREATE INDEX` statements moved *after* `_migrate()`.
Run before the column migration, against a pre-existing table that `CREATE TABLE IF NOT EXISTS` correctly leaves alone, the index raises `no such column` and takes the store's construction - and with it the daemon's startup - down.
Any future store that adds both a column and an index over it has the same ordering constraint.

`voice_clips` groups by `COALESCE(stream_id, id)` (`GROUP_KEY`) but never *matches* by it.
A predicate over that expression is opaque to both of the table's indexes, so every per-stream
lookup was a full table scan — and eviction does one per candidate stream.
The membership test is `GROUP_MATCH`, `(stream_id=? OR (stream_id IS NULL AND id=?))`, which SQLite
answers with a MULTI-INDEX OR: a seek on `idx_voice_clips_stream` unioned with one on the `id`
primary key, measured 2026-08-24 over 60,000 rows at 0.002ms against 2.337ms.
The `stream_id IS NULL` guard is what keeps the rewrite *equivalent* rather than merely fast: a
bare `id=?` arm would also match a row belonging to a different stream that happens to carry the
key as its own id, which the `COALESCE` form never did.

The same migration retires interrupted work: `status` gained `synthesizing`, written before the engine runs so a clip is visible while it is being made, and a row still in that state at connect is swept to `failed`.
That is not a timeout, it is a certainty - the engine runs in this daemon, so a `synthesizing` row that survived a restart belongs to a run that no longer exists.

`ScheduleStore` is at schema version 2 and migrates by reading `PRAGMA table_info` rather than by trusting a recorded version, so a database written by a newer build, opened by an older one, and opened again still gains each added column exactly once.
The added columns are `ALTER TABLE ADD COLUMN` rather than a table rebuild because every default reads as the previous behaviour: a row written before the resume action existed *was* a deferred spawn with no target.

Even expected uniqueness/deduplication paths must commit or roll back before return. Do not catch
`OperationalError` and retry at the HTTP route: fix the store operation boundary so every caller
gets the same guarantee.

### Two rows that must be durable together go in one operation

`LandStore.transition` and `LandStore.enqueue` both take an optional `event: LandEvent`, and
passing it writes the request row and its audit row inside the same operation and the same commit.
That pairing is the mechanism, not a convenience: the state change and the trail entry used to be
two commits in whichever order the call site happened to pick, and three land-queue paths picked
event-then-transition, so a conditional `UPDATE` that then lost its race left a `verify/skipped`
entry standing over a request that never moved.
The event carries no `project_id` of its own — the writer reads it off the row it just updated —
so an event can never be filed against a different Project from the request it describes, and a
transition that raises `LandConflict` rolls the event back with it.
`LandStore.restore` writes its `orphaned` events the same way, because the crash-recovery path is
the last place that should be able to requeue a step and lose the record of having done so.
`record_event` remains for the entries that are not paired with a transition (a reconcile's
outcome, a gate's per-attempt verdict, the change-set classification).

### A retention pass is many operations, never one

`AutomationStore.prune` deletes from each table in bounded batches
(`_PRUNE_BATCH_ROWS`, 500), each its own store operation with its own commit, capped at
`_PRUNE_MAX_BATCHES` per table per pass and logging when that cap is reached.
It used to be one `op()` deleting from thirteen tables and committing once, which held the
process-wide `mux.db` operation lock across the whole sweep while history, telemetry, voice, and
PTY writes queued behind it.
Batching means *separate coordinated operations*, never a nested transaction: the lock is taken
and released once per batch.

The batch statement is deliberately `DELETE FROM t WHERE rowid IN (SELECT rowid FROM t WHERE
<column><? LIMIT ?)` with **no `ORDER BY`**.
Ordering the batch by the retention column forces a temp B-tree over every matching row on a table
that has no index on it — measured 2026-08-24 at 1563ms against 263ms for 100,000 rows — while
without it SQLite either walks the index range or scans and stops at the batch size, and both land
at ~260ms.
`VoiceStore.prune` follows the same shape for cache eviction: one operation chooses the victim
streams, then bounded batches (`_EVICTION_BATCH_STREAMS`) delete them.

### Retention indexes are measured, not assumed

Every prune index leads with the retention column; the scope-first indexes on the same tables
(`(project_id, created_at)`, `(state, created_at)`, and so on) cannot serve `WHERE created_at<?`
at all.
Four tables carry one: `automation_observer_calls` (`idx_observer_created`, pre-existing),
`automation_budget_ledger`, `automation_annotations`, `scan_timeline_records`, plus
`automation_checkpoints` on `updated_at` — the column it actually prunes by, which had no index.

Which four is a measurement.
Live 2.8 GB `mux.db` after weeks of uptime, 2026-08-24: observer calls 19,309 rows, budget ledger
7,789, checkpoints 2,966, annotations 2,472, scan records 2,400, every other prune table at or
below 1,100.
Against a synthetic table the batched delete costs 263ms unindexed and 220ms indexed at 200,000
rows, and the "nothing aged out yet" probe — the common case, since retention runs hourly — costs
8.8ms unindexed against 0.024ms indexed.
Neither figure earns an extra B-tree per insert at a thousand rows; both do at a hundred thousand.
The four indexed tables are the ones whose write rate and retention window (365 days for the
durable pair, one row per agent run forever for checkpoints) can carry them there.

### LIKE patterns are escaped in one place

`sqlite_store.escape_like` and `sqlite_store.like_contains` are the shared helpers, and every
`LIKE` over user text pairs them with `ESCAPE '\'` at the call site.
The escape character is not SQLite's default, so a pattern built by these helpers and used without
that clause matches nothing.
They live in `sqlite_store` rather than in one store because three private copies is how two
stores ended up with none: `automation_store`'s scan `target_fragment` and experience browse, and
both of `history`'s metadata filters, interpolated raw text, so `land_store` also matched
`land-store` and a `%` anywhere in a query matched everything after it.
`history._escape_like` / `history._like_pattern` are now aliases of the shared pair.

## Read and batching rules

- Reads use the same operation wrapper because a prior dirty transaction on that connection is
  a correctness failure, not just write contention.
- Batch imports keep lock duration bounded. Parse/fingerprint files outside SQLite operations,
  then submit serialized batches.
- Do not share a connection across worker threads or create feature-local database locks.
- Schema migrations and retention jobs obey the same wrapper and transaction rules.
- A migration that *backfills data* (as opposed to adding a column) must be one-shot, gated on
  the schema change that motivated it. `_migrate_schema` runs on every connect — i.e. every
  daemon start and every session-preserving reload — so an unconditional backfill re-applies
  forever and silently undoes any later deliberate state change. History's `agent_visible`
  backfill did exactly that, resurrecting every quarantined misattributed run on each restart;
  it is now gated on the column having just been added and additionally excludes the
  quarantine exit reasons.
- History message-search upgrades keep only bounded schema changes on the startup path.
  Adding `history_messages.ts_epoch`, installing maintenance state, and replacing triggers do not scan or rewrite message rows.
  A post-startup task resets both external-content FTS indexes, captures a fixed row-id watermark, and populates token, trigram, and epoch data in committed 250-row batches.
  Its cursor is durable, rows inserted above the watermark are maintained by triggers, and deletes below the cursor are indexed while deletes ahead of it wait for the batch scan.
  Until the cursor reaches its watermark, search uses bounded literal `LIKE` queries and exposes `search_index_ready=false` instead of consulting a partial or inconsistent FTS index.
  Any optional search-schema or maintenance failure is logged and leaves the daemon available on that fallback.
  FTS update triggers are `AFTER UPDATE OF text`; an update of `ts_epoch` or other metadata must never churn external-content index terms.
- A proven session-identity repair may atomically delete that session's rebuildable
  tool/compaction/coverage rows and reassign its retained process fingerprints. This remains an
  operational-telemetry transaction through the shared coordinator; History does not mutate
  another store's tables.

## Verification

Tests should cover concurrent operations from different stores, exceptions after `BEGIN`,
expected duplicate paths, and returning with an open transaction. A terminal spawn/PTY attach
regression is valuable because these user-visible paths historically exposed leaked writer locks.

## Key files

- `src/swe_mux/sqlite_store.py` — the operation wrapper, the per-file lock, the integrity cache,
  and the shared `escape_like` / `like_contains` LIKE helpers.
- `src/swe_mux/history.py`
- `src/swe_mux/automation_store.py`
- `src/swe_mux/operational_telemetry.py`
- `src/swe_mux/voice.py`
- `src/swe_mux/prompt_queue.py`
- `src/swe_mux/land_store.py` — land requests, their audit trail, and the `LandEvent` pairing that
  makes a transition and its trail entry one commit.
- `src/swe_mux/clipboard_store.py` — mirror-only participant: reads never touch SQLite (the ring
  is in memory) and writes happen only while persistence is enabled, but every write it does make
  goes through the same wrapper. `load()` also deletes rows outside the adopted window:
  they are unreachable by every later path (picker, retention, "clear history"), so leaving
  them would keep verbatim copied text on disk against this store's own bound.
- `src/swe_mux/status_timeline.py` — the durable per-session detection timeline
  (`status_timeline` table): a write-behind sink for the in-memory transition ledgers,
  batched on its own worker, with time-based retention (`status_timeline_retention_days`).
  Writes are `INSERT OR IGNORE` against the `(session_id, agent_run_id, seq)` key, so a
  replayed batch after a failed flush cannot duplicate rows.
- `src/swe_mux/session_recovery.py` — the durable session registry (`session_recovery` table):
  one row per session with the redacted metadata blob it can be rebuilt from and an open marker,
  sampled onto its own worker on an interval.
  Its **terminal bytes are files, not rows**, and its file work runs through a separate `_run_io`
  helper on the same worker but *outside* `database_operation_lock`: that lock is per database
  file and shared with the history, automation, telemetry, and voice workers, so writing a few
  hundred kilobytes of scrollback under it would make an unrelated history write wait on this
  store's disk I/O.
  Row-then-files ordering on delete, because a directory no row names is swept at boot while a row
  naming files that are gone would have a restore report content it cannot produce.
- `src/swe_mux/tier0_store.py`, `src/swe_mux/deterministic_consumers.py`
- `src/swe_mux/project_context.py` writes no SQLite rows.
  The Project-owned Markdown file is its only active store.
- The `project_cards` table remains for compatibility with existing databases, but no active runtime service reads or writes it.
- `src/swe_mux/scan_timeline.py` - writes run grants, records, rollover boundaries, and bounded
  read metrics through `AutomationStore`.
  Scan records and boundaries use the durable retention window; run state and the one-row metrics
  table are bounded by run count and construction.
  Backfill inserts use the same record table, record reads order by source time, and run-cursor updates take the maximum existing/source timestamp.
  The shared budget ledger's nullable Project/run dimensions let failed billable calls count
  toward scan budgets without fabricating a semantic record, including a call the provider billed
  for whose output local validation refused.
  `scan_timeline_backfills` holds one durable row per full-session job so a restart reports the
  job's real outcome instead of `idle`; `interrupt_running_scan_backfills()` closes out rows a
  dead daemon left at `running`.
- `tests/test_automation_phase6.py`
