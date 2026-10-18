# Worker process

The daemon serves every terminal keystroke, every WebSocket frame and every API answer from one event loop, and every thread it runs competes with that loop for one GIL.
The worker process is where bulk work that is heavy and not interactive goes instead: a second process with its own interpreter, spawned, owned and restarted by the daemon.
It exists because measuring the daemon under fleet load showed the stall time sitting in GIL convoys held by exactly that kind of work (`development/DAEMON_LOAD_ISOLATION.md` items 2, 4 and 11).

It hosts four services today:

| Service | What it does | Daemon side |
| --- | --- | --- |
| `processes` | The process inspector's sampling pass, priority enforcement, process actions, port-conflict probes and unique-memory reads (item 20, `process_service.py`) | `ProcessInspector`, which keeps a mirror (`design/features/processes-and-previews.md`) |
| `ghost_windows` | The headless-browser ghost-window sweep (item 20) | `GhostWindowSweeper`, which keeps the counts |
| `telemetry` | The canonical telemetry ledgers: the reducer and every write, OTLP parsing, the legacy importer, native reconciliation, rollups, seals, and every query (item 21, `telemetry_worker.py`) | `CanonicalTelemetryService`, which keeps the bus subscription, the batching, the loops and a bounded buffer (`design/features/operational-telemetry.md`) |
| `noop` | Echo, pushed state and a notice: the service that proves the host | none |

## The shape

- **One worker process hosts every service.**
  A crash restarts all of them at once, and each service is rebuilt from what the daemon pushes to it on reconnect, so the worker holds no state the daemon cannot give it back.
- **The daemon stays the only process that writes `mux.db`.**
  A service returns results; the daemon persists them.
  The one store the worker writes is the canonical telemetry root (`<data_dir>/telemetry/`), and while it does the daemon never opens it: that store has exactly one writing process at a time (see below).
- **It dies with the daemon, three ways over.**
  The daemon holds it in a kill-on-close reaper of its own (a Job object on Windows, a process group with a guardian on POSIX) that never contains a supervised session pid; the worker exits when its control connection closes; and it checks every five seconds that its daemon's pid still exists.
  A daemon restart, including a detach that keeps every session, therefore starts a fresh worker, unlike the PTY supervisor, which is built to outlive the daemon.
- **Nothing waits on it.**
  The daemon's `WorkerHost.call` raises `WorkerUnavailable` at once when no worker is connected, and every caller falls back to doing the work in the daemon, which is how it ran before the worker existed.
  Canonical telemetry is the one exception, and its fallback is a buffer rather than the work: see below.
- **`worker_process_enabled`** (default on, applies at the next daemon start) turns it off; Settings → Processes has the switch.

## Moving a service into it

The process inspector is the pattern, and each rule in it answers a way the move could have changed behaviour.

- **The worker runs the daemon's own code, not a copy.**
  `process_service.py` feeds a `ProcessInspector` the facts it reads through a stand-in for the session manager and calls the same `_collect_all` the daemon calls when no worker is ready, so there is one implementation and two processes that can run it.
- **The daemon keeps what its readers read, as a mirror updated from deltas.**
  Every reader of `ProcessInspector.owned` - the Processes surfaces, previews, fleet intelligence, process evidence - is unchanged and in-process; only the pass moved.
- **A restarted worker is reloaded before it is trusted.**
  `WorkerHost.generation` counts connected workers; a service compares it with the generation it last pushed its state to, and a worker that has not been loaded refuses to answer rather than answering from empty state.
- **Every failure falls back to the daemon for that call**, and the next call reloads the worker first.
- **Host walks share one lock.**
  The worker is one interpreter with one GIL, so two services walking the host on two threads convoy on it exactly as they did in the daemon; services that walk the host hold `Worker.host_scan_lock` (the process pass and the ghost-window sweep do).
- **What only the daemon holds stays in the daemon.**
  A session root is interrupted through its PTY, so the worker verifies and answers "this is the root" and the daemon writes the byte; evidence is persisted by the daemon from its mirror.

The Codex process proofs (`codex_process_identity.py`) and the handle queries behind them (`win_process_files.py`) stay in the daemon on purpose: they run once per hook or once per session event rather than per tick, on paths where an extra round trip is latency a person waits on, and they cost too little to see in any measurement taken (`development/DAEMON_LOAD_ISOLATION.md` item 20).

### A service whose state is a store: canonical telemetry

The ledgers broke one rule above on purpose, and the reason generalizes to any service whose state is files on disk rather than something the daemon can push.

- **No daemon fallback, because the fallback would be a second writer.**
  `LedgerShared` serializes the catalog and segment writes of the three ledgers inside one process; it cannot see another process, and two processes writing one root could take the catalog and a segment in opposite orders.
  So in worker mode the daemon never opens the ledgers, even for a single call while the worker restarts.
  With `worker_process_enabled` off they run in the daemon exactly as before, which is the fallback at the level where it is safe: a whole daemon start.
- **Ingestion waits instead.**
  A batch no worker can take goes into a buffer bounded at 50,000 observations (`BUFFER_EVENT_LIMIT`), offered again every second and ahead of anything newer; past the bound the oldest batches go first, counted in `buffer_dropped`.
- **Replaying is safe, which is what makes waiting enough.**
  An observation's evidence id is a digest of its session, type, source, sequence number and payload, inserted with `OR IGNORE`, so a batch the worker committed and then died before acknowledging records nothing twice when it is sent again.
  The wire encodes a payload with the same `default=str` the digest does, so the id the worker computes is the one the daemon would have.
- **Reloaded before trusted, as files rather than state.**
  The daemon opens the ledgers in each worker generation before its first call; a worker that has not been opened refuses every call, and the daemon reopens it rather than reading the refusal as a fault.
- **A query nobody can answer is a 503.**
  It waits up to 10 s for a worker that is starting (`QUERY_WAIT_SECONDS`) and then raises `TelemetryUnavailable`, which the error middleware answers as `503 telemetry_unavailable`; quota attribution, the one internal consumer, retries rather than skipping (`quota_usage.py`).
- **An OTLP body crosses unparsed.**
  The route hands the worker the raw bytes and the session's dimensions; it is parsed, reduced and recorded there, with its parser signatures, in one call.
  With no worker the route parses it as before and the reduced events wait in the buffer.
- **The worker outlives the services that call it.**
  The daemon's teardown stops the worker after canonical telemetry, which itself stops after the sessions, so the observations a shutdown emits are recorded rather than left in a buffer the daemon exits with.

## Lifecycle

`worker_host.py` runs one supervised loop, `worker-host`, incarnation after incarnation.

1. Spawn the child in a thread, never on the loop: `CreateProcess` on a loaded Windows host can take seconds.
   The token goes to its stdin and the child is assigned to the reaper in the same thread.
2. Wait up to 60 s for its `hello`, which must name the pid that was spawned, or that pid's direct child under a Windows virtual environment's `python.exe` launcher.
3. Run the ready hooks, which push each service its state; a hook that fails ends the incarnation, because a worker missing its state is worse than one restarting.
4. Serve until the connection closes or the child exits.
5. Close the connection, give the child 3 s to exit, kill it if it has not, record its exit code, and restart after a backoff from 0.5 s to 30 s that resets after a minute of good service.

Stopping asks the worker to leave over the connection, waits for the incarnation to end by itself, and only then cancels whatever is left and closes the reaper.

## How it is started

| Mode | Command |
| --- | --- |
| Frozen app | `swe-mux.exe --worker-child --port N --data-dir D --log-level L --switch-interval-ms S --daemon-pid P` |
| Source | `python -m swe_mux.worker` with the same arguments |

The frozen form is an entry flag (`desktop.main`) and never `swe-mux.exe -m swe_mux.worker`: on the app's own image that shape is a session helper to the redeploy, which spares it, and to the process inspector, which keeps it session-owned.
The source form is a module run, as the source supervisor is `-m swe_mux.supervisor`, because those checks apply only to the frozen image and `-m swe_mux` would load the whole daemon into a process that needs none of it.
The token is never on the command line or in the environment, which any process of the same user can read; `SWE_MUX_DESKTOP_CONTROL_TOKEN` is withheld from the worker's environment.
The worker sets the configured GIL switch interval and the raised timer period, and runs at normal priority, below the daemon's.

## The protocol

`framed_ipc` (`technical/backend/packages/daemon-runtime.md`), protocol `WORKER_PROTOCOL` 1.
The daemon listens on an OS-allocated loopback port; the worker connects with `role: "worker"`, its `pid`, `build` and `services`.
Requests are `<service>.<method>`, plus the built-ins `worker.stats` and `worker.shutdown`; notices from the worker route to the handler the daemon registered for that service.

## Logs and diagnostics

- `worker.log` in the data directory, rotating at 10 MB with three backups; each start names its pid and its daemon's.
- `worker-console.log`: what the interpreter printed before logging started, truncated at a spawn once it passes 1 MiB.
- `/api/diagnostics/background` → `worker`: `enabled`, `state` (`disabled`, `stopped`, `starting`, `running`, `backoff`), `ready`, `pid`, `launcher_pid`, `port`, `spawns`, `restarts`, `generation`, `last_exit_code`, `last_error`, `uptime_seconds`, `services`, `build`, `notices_unrouted`, and the connection's own counters.
- `process_sampling` and `ghost_windows` on the same endpoint say where each service's work actually ran - in the worker, or in the daemon while it was away - and how often a call to it failed.
- `canonical_telemetry` says whether the ledgers are in the worker (`in_worker`), how many worker generations they have been opened in (`worker_opens`), and what is waiting for one (`buffered_events`, `buffer_dropped`, `worker_unavailable`).

## Where it is tested

`tests/test_worker_host.py` spawns real workers: a round trip, a killed worker restarted and rebuilt from the daemon, a stop that leaves nothing behind, a worker that leaves on its own when its connection closes, a stand-in daemon killed outright taking its worker with it, a stranger holding the token refused, and the failure shapes (exiting before `hello`, never saying it, disabled).
The `live_daemon` tier asserts that a real `muxd` reports a ready worker, that a `quit` reaps it, and that a detach ends it while the supervisor survives.
`tests/test_process_sampling_worker.py` holds the process service to the in-process behaviour against a real worker and a real process tree, and the `live_daemon` tier asserts that a daemon a default install builds hands its sampling passes to the worker.
`tests/test_telemetry_worker.py` replays one event stream, including the scrubbed real Claude and Codex OTLP captures, into ledgers in process and through a real worker, and requires every row of every ledger file and every query answer to match, with the columns a ledger stamps from its own clock held to a reading taken during their own run; it also covers the replay, buffering, reopening and 503 paths.
`tests/test_shutdown_teardown.py` holds the teardown to stopping the worker after every service that calls it.
