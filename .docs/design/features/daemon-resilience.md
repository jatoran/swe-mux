# Daemon resilience

## What it is

Safe stall diagnostics and desktop-owned daemon recovery preserve access to supervisor-owned sessions after a daemon failure.
The terminal WebSockets still traverse the daemon, so a stall or replacement temporarily interrupts input and output.
The PTY supervisor keeps the processes and scrollback alive during that interruption.
This is bounded recovery, not an independent terminal transport.

## Stall diagnostics

The event loop publishes only a monotonic heartbeat timestamp.
A Python sampler checks it every 250 ms and captures bounded stacks after three seconds without progress, at most once per threshold interval.
`sys._current_frames()` supplies owned frame references; automatic `faulthandler.dump_traceback_later()` traversal is forbidden because its asynchronous native frame reads can crash the interpreter.
The ordinary fatal-crash handler remains enabled.
The sampler writes at most 100 threads and 40 frames per thread to `loop-stalls.log`, with one predecessor and rotation at 4 MiB, including during a persistent stall.
File writes, rotation, explanation, and sampler shutdown run outside the event loop.

A native call holding the GIL prevents Python sampling too.
`capture_mode=python_gil_required` and `gil_held_stacks_available=false` expose that limitation on `/api/diagnostics/background`.
Canary starvation records the inability to run; missing stack samples never claim to identify the blocking frame.
After the loop resumes, its measured duration and available samples become the existing durable `loop_stalls` record.
A hard crash before explanation can leave only the trace file, OS crash evidence, and desktop recovery ledger.

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
Every recovery state transition and failure goes to the rotating `lifecycle.log`.

## Listener guard

On Windows the proactor event loop closes a *listening* socket when one accept completion fails with `OSError`, and never listens on it again.
The ordinary way to produce that is a client whose connect timed out against a blocked loop: its half-open connection is reset, and the queued accept fails with `WinError 64` when the loop resumes.
The daemon is then alive, answering on every other interface, and unreachable on loopback, which is the address every local client uses.

`listener_guard.ListenerGuard` checks every bound site every two seconds.
A closed listening socket is one whose `fileno()` reads -1, the only trace asyncio leaves; the site is stopped and a new one bound on the same host and port.
Each finding is an ERROR in `daemon.log`, a line in `lifecycle.log`, and a counter under `listener_guard` on `/api/diagnostics/background`; a rebind that fails is retried every tick and logged at most every thirty seconds.
A site that never started has no sockets and is not reported dead, because that is the startup path's failure to report.

## Session protection and durable authority

`daemon-recovery.json` records the PID, OS creation timestamp, desktop-token digest, readiness, lifetime local-PTY revocation, attached supervisor identity, and planned shutdown intent.
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

- `src/swe_mux/stall_watchdog.py`: bounded Python sampling, canary, trace retention, explanations.
- `src/swe_mux/daemon_recovery.py`: durable authority, process fence, protection checks, recovery state machine.
- `src/swe_mux/desktop.py`: monitor lifetime, replacement launch, manual restart/quit coordination.
- `src/swe_mux/lifecycle.py`: planned-handoff integration and lifecycle ledger.
- `src/swe_mux/server.py`: generation registration, readiness, and local-PTY revocation wiring.
- `src/swe_mux/session.py`: revocation before a local PTY allocation.
- `frontend/src/daemonLiveness.ts`: client symptom reporting.
- `tests/test_daemon_recovery.py` and `tests/test_stall_watchdog.py`: recovery and diagnostic regressions.
