# Daemon resilience

## What it is

Safe stall diagnostics and desktop-owned daemon recovery preserve access to supervisor-owned sessions after a daemon failure.
The terminal WebSockets still traverse the daemon, so a stall or replacement temporarily interrupts input and output.
The PTY supervisor keeps the processes and scrollback alive during that interruption.
This is bounded recovery, not an independent terminal transport.

## Stall diagnostics

The event loop publishes only a monotonic heartbeat timestamp.
A Python sampler checks it every 250 ms and captures bounded stacks after three seconds without progress, at most once per threshold interval.
`MUX_STALL_THRESHOLD_SECONDS` (0.5-60) overrides the three seconds for diagnosis, chiefly for the load harness, whose stalls are real but often shorter; an invalid value is logged and ignored.
`sys._current_frames()` supplies owned frame references; automatic `faulthandler.dump_traceback_later()` traversal is forbidden because its asynchronous native frame reads can crash the interpreter.
The ordinary fatal-crash handler remains enabled.
The sampler writes at most 100 threads and 40 frames per thread to `loop-stalls.log`, with one predecessor and rotation at 4 MiB, including during a persistent stall.
File writes, rotation, explanation, and sampler shutdown run outside the event loop.

A native call holding the GIL prevents Python sampling too.
`capture_mode=python_gil_required` and `gil_held_stacks_available=false` expose that limitation on `/api/diagnostics/background`.
Canary starvation records the inability to run; missing stack samples never claim to identify the blocking frame.
After the loop resumes, its measured duration and available samples become the existing durable `loop_stalls` record.
A hard crash before explanation can leave only the trace file, OS crash evidence, and desktop recovery ledger.

### What kind of stall it was

A stack sample shows where every thread is, not who ran.
In the 2026-10-16 incident every main-thread sample sat at a different GIL-releasing syscall and every executor thread looked busy, so no record could say whether the loop had too much work or was waiting for the GIL behind another thread.
Every explanation now carries a `diagnosis` that answers that from CPU time, not stacks.

- **Per-thread CPU.** The canary records every thread's CPU time once a second (`host_metrics.py`), plus one sample at the stall's first capture and one at explanation.
  The explanation differences the last sample before the stall against the one at its end, so the window is the stall's own.
  On Windows the reads are `GetThreadTimes` on cached handles through a ctypes library that keeps the GIL, because releasing it around each of ~90 microsecond-long calls would cost a GIL round trip apiece during the very convoy being measured.
  Elsewhere `psutil.Process().threads()` reads the process's own task list; `time.pthread_getcpuclockid` is not used because an expired thread id is undefined behaviour.
- **Executor work.** A thread name such as `asyncio_21` is not actionable, and in the first harness run with verdicts the top CPU thread of both convoys was a default-executor worker that had finished its burst before the stack capture looked at it.
  Every executor the daemon owns, the default one included, is an `AccountedThreadPoolExecutor` (`executor_accounting.py`) that charges each work item's thread CPU to a label naming the callable, with store operations named through `run_sqlite_operation` (`run_sqlite_operation:HistoryIndex.recent_runs`).
  Each top CPU thread in a diagnosis carries the `work` it spent the window on, and `top_work` ranks the window's work by CPU.
  `tests/test_executor_accounting.py` fails on any bare `ThreadPoolExecutor(` in the package.
- **Process and host CPU** over the same window (`time.process_time`, `psutil.cpu_times`).
  The previous `psutil.cpu_percent(interval=None)` reading measured whatever interval preceded the call on the calling thread, which is how the incident's lines reported `cpu=0.0` on a saturated host.
- **Ready-queue depth.** While the loop is overdue, the canary reads `len(loop._ready)`, a racy hint, and each capture writes it as `# ready_queue=N`.
- **Parked marks.** A thread that blocks waiting for work marks itself for the duration (`parked_threads.py`), and each capture writes the mark into the thread's header as `[parked:reason]`.
  The marked waits are the `win_process_files` name worker's inbox, each git watch's wait inside watchfiles, and the shared `mux.db` operation lock, whose waiters are counted as `db_lock_waiters` rather than hidden or blamed.
  Leaf guessing (`IDLE_LEAVES`) remains for waits that cannot mark themselves.
- **An optional external dump.** At a stall's first capture the watchdog runs `py-spy dump --nonblocking` against its own process, at most once per two minutes, on its own thread, when `py-spy` is on `PATH` or named by `MUX_STALL_PROFILER` (`off` disables it).
  Its `active+gil` marker names the GIL holder even inside a native call, which no in-process sampler can see; the explanation waits up to five seconds for a dump still running.
  `swemux doctor` reports whether it is available (`background.stall_profiler`).

From those, `diagnose` states one verdict per stall:

| Verdict | Evidence | Meaning |
| --- | --- | --- |
| `loop_blocking_call` | loop thread ran at least 60% of the window, ready queue under 64 | one long synchronous call on the loop |
| `loop_saturated` | loop thread ran at least 60%, ready queue 64 or deeper | more callbacks than the loop can run |
| `gil_convoy` | other threads burned at least 0.5 cores, or at least 0.25 and more than the loop | the loop was waiting for the GIL behind them |
| `host_starved` | nothing in the process ran much, host at least 90% busy | the OS did not schedule the daemon |
| `blocked_wait` | nothing ran much, host had headroom | the loop was blocked in I/O, a lock, paging, or a native call holding the GIL |
| `unsampled` | no CPU sample covered the window | not classified |

The GIL holder is the external dump's `active+gil` thread when one caught the stall, otherwise the top CPU thread other than the loop for a convoy, the loop itself for a busy loop, and nobody when nothing ran.
A thread that was not parked but used under 5% of the window is reported as blocked rather than busy, so the busy list holds only threads that ran.
The `loop_stall` line in `daemon.log` leads with the verdict, the loop's and other threads' CPU, host CPU, the ready-queue maximum, the GIL holder and the top CPU threads; the full diagnosis is in the `loop_stalls` row (`diagnosis_json`) and on `/api/diagnostics/background`.

Explanations run beside the lag probe, never inside it: stalls that end are queued (newest eight kept) and explained in order by one task.
A probe that waited on its explanation stopped beating, so the canary captured a running loop as stalled and the lag window went unsampled for as long as the explanation took, which with the external dump's wait is seconds.
The explanation itself must not add to the stall: its host reading uses GIL-releasing calls on Windows (`host_metrics.py`), where psutil's held the GIL for 55 ms per explanation, and it parses stacks only from the stall's first dump.

## Desktop recovery

The desktop shell starts a monitor thread after its initial daemon startup wait.
It runs in the desktop process, independently of the daemon's event loop and GIL, and polls every two seconds.
It follows the current daemon generation through `daemon-recovery.json`, including successors launched by a daemon self-restart.
The record's desktop-token digest must match this desktop installation.
Unmanaged daemons and old builds without the record are observed without automatic intervention.

The probe is `GET /api/health` with a five-second timeout.
Whether a run of failed probes means a hang is decided with the daemon's heartbeat (`daemon-heartbeat.json`, written from the event loop every ten seconds): a heartbeat older than thirty seconds is a stopped loop, a fresh one is a loop that is running but cannot answer in time.

| Observed condition | Behavior |
| --- | --- |
| Successful health probe | Clear the outage and pending replacement state. |
| Confirmed dead generation | Launch a replacement after four seconds of failed probes. |
| Previously ready process alive, heartbeat stale, unresponsive for 45 seconds | Recheck health and protected-session evidence, then terminate only that daemon and launch its replacement (`unresponsive_loop_stalled`). |
| Previously ready process alive, heartbeat fresh, unresponsive for 180 seconds | The same replacement, three minutes later (`unresponsive`). A live loop that misses probes is load or a listener being rebound, and a kill costs a 70-110s restart to cure a slowness. |
| Process still starting | Wait without terminating it, including slow database maintenance. |
| Unknown process identity, local PTY allocation, or unavailable/changed supervisor | Refuse forced termination. |
| Intentional quit or live redeploy | Suppress automatic recovery. |
| Planned detach/restart | Allow five minutes for the handoff; recover a failed handoff afterward under the ordinary protection gates. |
| Three attempted replacements within ten minutes | Suppress further attempts until the rolling window permits another. |
| Replacement healthy for two minutes | Forget the attempts that produced it; the budget is for crash loops, not for the next outage. |

Thresholds exclude probe time and replacement startup time.
A spawned replacement is tracked before it publishes its startup record, preventing duplicate launches during a slow process start.
A daemon that starts while the record names a live generation with no planned handoff does not take the record (`register_daemon` returns `False` and says so in `lifecycle.log`): it is a duplicate about to lose its port bind, and taking the record made the monitor restart a daemon that was fine.
Manual tray restart pauses and fences the monitor; tray Quit stops it before requesting shutdown.
Recovery never invokes supervisor shutdown, kills a process tree, or rebuilds a bundle.
It never signals the terminal gateway either (`terminal-gateway.md`): the record names it, `terminate_generation` refuses a record whose daemon pid is the gateway's or the supervisor's, and the replacement daemon finds the same gateway and connects to it, so terminals keep echoing through the recovery.
Every recovery state transition and failure goes to the rotating `lifecycle.log`.

## Listener guard

On Windows the proactor event loop closes a *listening* socket when one accept completion fails with `OSError`, and never listens on it again.
The ordinary way to produce that is a client whose connect timed out against a blocked loop: its half-open connection is reset, and the queued accept fails with `WinError 64` when the loop resumes.
The daemon is then alive, answering on every other interface, and unreachable on loopback, which is the address every local client uses.

`listener_guard.ListenerGuard` checks every bound site every two seconds.
A closed listening socket is one whose `fileno()` reads -1, the only trace asyncio leaves; the site is stopped and a new one bound on the same host and port.
Each finding is an ERROR in `daemon.log`, a line in `lifecycle.log`, and a counter under `listener_guard` on `/api/diagnostics/background`; a rebind that fails is retried every tick and logged at most every thirty seconds.
A site that never started has no sockets and is not reported dead, because that is the startup path's failure to report.

The guard is the backstop; the close itself is prevented first.
`__main__` runs the daemon on `proactor_accept.resilient_event_loop()` (`asyncio.run(..., loop_factory=...)`), whose `ResilientIocpProactor.accept` absorbs a per-connection accept failure and arms the next accept on the same, still-open listener.
Per-connection means `ConnectionResetError`, `ConnectionAbortedError`, or one of `TRANSIENT_ACCEPT_WINERRORS` (64, 121, 1236, 10053, 10054, 10057), and only while the listener's `fileno()` is still valid.
After `MAX_CONSECUTIVE_ABSORBED` (64) consecutive failures on one accept the error is handed to asyncio after all, which closes the listener and leaves the guard to rebind it, so a genuinely broken listener is never masked forever.
It wraps the public `IocpProactor.accept` rather than copying CPython's private accept loop, so a Python upgrade that changes that loop cannot silently change this.
Before it, one morning of 2026-09-24 had 36 listener deaths, each costing every local client up to the guard's two-second tick of refused connections.
Absorbed failures are counted under `listener_guard.accept_failures_absorbed` on `/api/diagnostics/background` (`absorbed`, `last_error`, `last_at`) and logged as `accept_failure_absorbed` at WARNING at most once a minute.
POSIX selector loops already survive a failed accept, so there the factory is the ordinary loop.

## Session protection and durable authority

`daemon-recovery.json` records the PID, OS creation timestamp, desktop-token digest, readiness, lifetime local-PTY revocation, attached supervisor identity, the terminal gateway's identity (`terminal_gateway`, pid and creation time, once the daemon has connected to one), and planned shutdown intent.
It contains no raw authentication secret and is replaced atomically.
`daemon-recovery.lock` is a persistent file whose byte lock on Windows or `flock` on POSIX is owned by the kernel and released on process exit.
Daemon registration, authority changes, and the recovery decision share this fence.
The record is separate from the periodic forensic heartbeat so a delayed heartbeat cannot overwrite a revocation or handoff.

Before starting any in-process PTY, the session manager must durably set `local_pty=true` under the fence.
This flag never resets within a daemon generation, even when that session ends or startup finishes.
A failed revocation prevents that degraded spawn.
The desktop holds the same fence while checking authority and replacing a hung daemon, so a local PTY cannot appear between the protection check and termination.
The attached supervisor is confirmed through its authenticated read-only hello exchange and its PID plus creation timestamp.
PID reuse and access-denied results never authorize termination of an unrelated process.

## Limits and operator recovery

Automatic recovery requires the desktop shell to remain running.
Standalone daemons need an external service manager or an explicit restart.
Native GIL stalls in the desktop itself, whole-host stalls, blocked filesystems, and operating-system process termination failures can still delay recovery.
The banner reports missed probes and reconnection attempts; it does not promise session survival or inevitable recovery.
The desktop tray's `Restart daemon (keep sessions)` remains the operator recovery control when the daemon HTTP endpoint is unavailable.

## Key files

- `src/swe_mux/stall_watchdog.py`: bounded Python sampling, canary, trace retention, explanations, verdicts, the external dump.
- `src/swe_mux/host_metrics.py`: per-thread and host CPU time.
- `src/swe_mux/executor_accounting.py`: per-callable CPU for every daemon executor.
- `src/swe_mux/parked_threads.py`: the parked-wait registry.
- `src/swe_mux/daemon_recovery.py`: durable authority, process fence, protection checks, recovery state machine.
- `src/swe_mux/desktop.py`: monitor lifetime, replacement launch, manual restart/quit coordination.
- `src/swe_mux/lifecycle.py`: planned-handoff integration and lifecycle ledger.
- `src/swe_mux/server.py`: generation registration, readiness, and local-PTY revocation wiring.
- `src/swe_mux/session.py`: revocation before a local PTY allocation.
- `frontend/src/daemonLiveness.ts`: client symptom reporting.
- `tests/test_daemon_recovery.py` and `tests/test_stall_watchdog.py`: recovery and diagnostic regressions.
