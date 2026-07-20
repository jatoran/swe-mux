# Backend package responsibilities

## Composition boundary

`src/swe_mux/server.py` is the aiohttp composition root. It creates stores/managers, wires
background workers, validates transport input, and translates domain errors to HTTP/WS results.
It should call domain packages rather than acquire their storage or process responsibilities.

## Package map

| Package | Owns | Does not own |
|---|---|---|
| `session.py` | live session registry, spawn/stop, PTY fanout, bounded replay | provider transcript parsing, Project mutation |
| `pty_host.py` | ConPTY/process creation, resize, low-level I/O | HTTP, SQLite, layout |
| `projects.py` | Project/Group validation and lifecycle | Git-derived identity, file content |
| `project_files.py` | safe Project config, notes, tree, file reads/writes | layout placement, browser drafts |
| `project_watcher.py` | leased non-recursive directory watches | recursive Project crawl |
| `history.py` | shared schema, Project/layout persistence, run history, search index | live PTY lifecycle |
| `history_backfill.py` | bounded cancellable complete-history jobs | durable job scheduling, native file mutation |
| `transcript_view.py` | bounded Claude/Codex conversation parsing | process state, transcript writes |
| `layouts.py` | layout-v6 validation and migrations | UI focus or drag state |
| `operational_telemetry.py` | process/quota/reset/context/tool evidence | credentials, automatic process killing |
| `provider_accounts.py` | saved auth snapshots, explicit switching, safe quota reads | concurrent provider homes |
| `processes.py` | descendant inspection/actions and loopback preview discovery | authoritative ownership from PID alone |
| `adapters/` | provider command/resume/transcript/state normalization | public HTTP shapes |

Feature stores sharing `mux.db` use their own single-worker executor/connection and the common
operation coordinator described in `sqlite.md`.

## Dependency direction

Transport may depend on managers/stores; managers may depend on adapter and persistence
contracts; platform modules remain below both. Provider-native shapes stop at adapter/parser
boundaries. Browser response models are assembled at the transport boundary.

Correct:

```python
# server.py validates the Project and delegates the state transition.
session = await manager.spawn(project_id=project.id, profile_id=profile_id)
```

Incorrect:

```python
# A route must not open mux.db directly or duplicate a store transaction.
sqlite3.connect(data_dir / "mux.db").execute("UPDATE projects ...")
```

## Background-work rules

- Blocking ConPTY creation, filesystem scans, Git probes, and SQLite work stay off the asyncio
  event loop.
- Interactive readiness and durable registration are distinct. Once a ConPTY is usable, publish
  the in-memory session and return; serialize history registration behind it.
- PTY attach/input paths never wait for observational event persistence.
- Every poller/scan has an explicit bound, cancellation/stop path, freshness contract, and
  unavailable result. Optional integrations cannot make terminal operations fail.
- Once a route has resolved an explicit Project, Project-resource helpers must receive/use that
  canonical identity. Re-running Git discovery on `project.root` can silently retarget a nested
  registered Project to its enclosing worktree; this remains a known note-path defect until the
  helpers accept the explicit Project identity.

## Related design

- `../../design/architecture.md`
- `../../design/interfaces.md`
- `../../design/features/sessions.md`
- `../../design/features/history.md`
