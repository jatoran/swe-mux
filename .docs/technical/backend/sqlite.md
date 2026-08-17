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

Even expected uniqueness/deduplication paths must commit or roll back before return. Do not catch
`OperationalError` and retry at the HTTP route: fix the store operation boundary so every caller
gets the same guarantee.

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

- `src/swe_mux/sqlite_store.py`
- `src/swe_mux/history.py`
- `src/swe_mux/automation_store.py`
- `src/swe_mux/operational_telemetry.py`
- `src/swe_mux/voice.py`
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
