# Worker process

The daemon serves every terminal keystroke, every WebSocket frame and every API answer from one event loop, and every thread it runs competes with that loop for one GIL.
The worker process is where bulk work that is heavy and not interactive goes instead: a second process with its own interpreter, spawned, owned and restarted by the daemon.
It exists because measuring the daemon under fleet load showed the stall time sitting in GIL convoys held by exactly that kind of work (`development/DAEMON_LOAD_ISOLATION.md` items 2, 4 and 11).

It hosts three services today:

| Service | What it does | Daemon side |
| --- | --- | --- |
| `processes` | The process inspector's sampling pass, priority enforcement, process actions, port-conflict probes and unique-memory reads (item 20, `process_service.py`) | `ProcessInspector`, which keeps a mirror (`design/features/processes-and-previews.md`) |
| `ghost_windows` | The headless-browser ghost-window sweep (item 20) | `GhostWindowSweeper`, which keeps the counts |
| `noop` | Echo, pushed state and a notice: the service that proves the host | none |

The canonical telemetry ledgers are next (item 21).

## The shape

- **One worker process hosts every service.**
  A crash restarts all of them at once, and each service is rebuilt from what the daemon pushes to it on reconnect, so the worker holds no state the daemon cannot give it back.
- **The daemon stays the only process that writes `mux.db`.**
  A service returns results; the daemon persists them.
- **It dies with the daemon, three ways over.**
  The daemon holds it in a kill-on-close reaper of its own (a Job object on Windows, a process group with a guardian on POSIX) that never contains a supervised session pid; the worker exits when its control connection closes; and it checks every five seconds that its daemon's pid still exists.
  A daemon restart, including a detach that keeps every session, therefore starts a fresh worker, unlike the PTY supervisor, which is built to outlive the daemon.
- **Nothing waits on it.**
  The daemon's `WorkerHost.call` raises `WorkerUnavailable` at once when no worker is connected, and every caller falls back to doing the work in the daemon, which is how it ran before the worker existed.
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
- **What only the daemon holds stays in the daemon.**
  A session root is interrupted through its PTY, so the worker verifies and answers "this is the root" and the daemon writes the byte; evidence is persisted by the daemon from its mirror.

The Codex process proofs (`codex_process_identity.py`) and the handle queries behind them (`win_process_files.py`) stay in the daemon on purpose: they run once per hook or once per session event rather than per tick, on paths where an extra round trip is latency a person waits on, and they cost too little to see in any measurement taken (`development/DAEMON_LOAD_ISOLATION.md` item 20).

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

## Where it is tested

`tests/test_worker_host.py` spawns real workers: a round trip, a killed worker restarted and rebuilt from the daemon, a stop that leaves nothing behind, a worker that leaves on its own when its connection closes, a stand-in daemon killed outright taking its worker with it, a stranger holding the token refused, and the failure shapes (exiting before `hello`, never saying it, disabled).
The `live_daemon` tier asserts that a real `muxd` reports a ready worker, that a `quit` reaps it, and that a detach ends it while the supervisor survives.
`tests/test_process_sampling_worker.py` holds the process service to the in-process behaviour against a real worker and a real process tree, and the `live_daemon` tier asserts that a daemon a default install builds hands its sampling passes to the worker.
