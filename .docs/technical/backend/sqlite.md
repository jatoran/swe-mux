# Shared SQLite concurrency

## Why this exists

History, Automation, Operational Telemetry, and Voice use separate connections and serialized
executors against one WAL database. SQLite still has one writer slot. A transaction left open on
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

Connections enable WAL, `synchronous=NORMAL`, foreign keys, and a bounded busy timeout. Those
settings reduce contention but do not replace the operation lock or explicit transaction end.

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
- `tests/test_automation_phase6.py`
