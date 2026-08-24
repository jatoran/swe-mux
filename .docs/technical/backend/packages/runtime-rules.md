# Backend: background-work and process rules

Index: `../packages.md`.
Investigation procedure: `../../../development/PERFORMANCE_RUNBOOK.md`.

Rules every daemon worker, poller, and subprocess obeys.
Each one exists because of a measured failure; the measurements are kept because they are what stops the failure being reintroduced.

## Loop supervision

Blocking ConPTY creation, filesystem scans, Git probes, and SQLite work stay off the asyncio event loop.

**Every long-lived loop is supervised.**
A bare `while True:` dies permanently on its first uncaught exception, with no log at failure time, no restart, and no health signal.
This daemon is designed to run for weeks behind the PTY supervisor, so that is not a degradation but a feature that silently stops.

Two layers are required, both in `background_tasks.py`:

- Wrap the *iteration* in `background.iteration(name)`, so a transient fault costs one cycle and is attributed.
- Start the *loop* with `background.start(name, factory)`, so anything that escapes anyway is restarted with capped backoff.
  `factory` must be a factory, not an already-created coroutine.

Health is surfaced at `GET /api/diagnostics/background`; a new loop that appears there with `running: false` is the diagnostic that used to be missing.
`tests/test_background_tasks.py` sweeps the source for unguarded `while True:` and fails on any new one.
A loop that genuinely should not join the registry - scoped to a single connection or session, or already guarded in place - opts out with an inline `# unsupervised-loop-ok: <reason>` marker, so the exemption is reviewable where it lives instead of in a list that quietly grows.

The lifecycle heartbeat is supervised for a sharper reason than the rest: its death is indistinguishable from the daemon's.
One failed write used to end it silently, after which the daemon kept running normally while every later start reported it as "died without a clean shutdown" - a false forensic that sends the next investigation after a crash that never happened.

## Pollers and watchers

Every poller and scan has an explicit bound, a cancellation or stop path, a freshness contract, and an unavailable result.
Optional integrations cannot make terminal operations fail.

A `stat()` in a polling watcher belongs inside the try.
Editors save by delete-and-rename, so `exists()` and `stat()` genuinely disagree; treat the failure as "unchanged".

### The PTY reader polls by choice, and its cadence follows the session

`pty.read()` is called nonblocking, because `blocking=True` parks the reader thread somewhere neither `_stop` nor a dead child can reach it.
A single fixed interval then serves two opposite cases badly: while output flows it is pure added latency on every chunk, and while a session sits at its prompt it is a wakeup per interval, per session, forever.

`read_poll_interval` grades it by time since the last read *or write* (`_last_io_at`) across an active window, the previous fixed 10 ms, and a deep-idle interval reached only after seconds of silence.
`write()` arms the active window deliberately: a keystroke's echo is the one latency a human is timing, and a session quiet at its prompt is exactly the one whose reader would otherwise be sitting on the slowest rung.
The ladder only ever slows down, and never below what the fixed interval already gave a session that was doing anything.

**Re-tiering alone is not enough, and shipping it alone was a regression.**
Choosing the next interval does nothing about the one already running, so a keystroke arriving mid-sleep still waited it out: measured end to end at 30 ms p50 and 40 ms max against a 40 ms rung, worse than the fixed 10 ms it replaced, in exactly the case the ladder exists to improve.
`write()` therefore also sets `_io_wake`, and the reader waits on that event rather than sleeping.
With the wake in place, typed-input latency is independent of the rung entirely: p50 ~16 ms at idle gaps of 0.05 s, 1 s and 6 s alike, where the remaining 16 ms is the rest of the stack.
`tools/pty_latency_bench.py` is what caught this and is what re-checks it.

### The PTY read path must never fabricate end-of-output

A cross-thread handoff that cannot complete is backpressure, and the correct response is to keep waiting while the child is alive.
`b""` reaching the supervisor is checked against `isalive()` before it is believed: the removal path closes a kill-on-close job, so a fabricated exit kills a live agent tree.

PTY attach and input paths never wait for observational event persistence.

Natural root exit captures status before detaching the dead ConPTY from the retained session.
Final output drains through the reader's local handle, and finalization cancels a frozen read after root exit so that local reference cannot leak.
`pty_host.py` also binds pywinpty's daemon-sibling `OpenConsole` helper (or its delayed frozen-build `conhost` replacement) by creation time, and revalidates PID, creation time, executable, and parent before reaping it.
Durable or in-memory scrollback, not an OS pseudoconsole reference, supplies ended-session replay.

Frozen Windows startup may surface pywinpty's private `pyo3_runtime.PanicException` once with `ERROR_SEM_NOT_FOUND`.
`pty_host.py` retries only that exact allocation panic, with a strict bound; other `BaseException` values retain normal control-flow semantics.

## Sampling cost

### `asyncio.to_thread` does not make psutil work free

Its Windows calls are C extension calls that hold the GIL, so a long sampling pass in a worker thread starves the event loop just as a blocking call would - it only stops looking like a blocking call.
`processes.py` once rebuilt every `psutil.Process` and re-read every `cmdline()` on each 5-second tick, costing ~930-1110 ms per pass.
The observable symptom was terminal keystrokes lagging and then arriving in a burst, plus a daemon that idled at 15-19% CPU.

The rule for any periodic psutil work: take **one** system-wide snapshot per pass (`_ppid_map()`, not `children(recursive=True)` per root), cache process handles across passes, and re-read only attributes that actually change per tick - never name, command line, or creation time.

### `Process.ppid()` is that same snapshot, and `oneshot()` does not cache it

On Windows psutil implements it as `ppid_map()[pid]`, rebuilding the whole parent table per call, and it carries no `@memoize_when_activated` unlike the `name`, `cmdline`, and `memory_info` calls beside it in the same `oneshot()` block.
One unguarded call site in `_revalidate_unseen` therefore made every sampling pass O(processes²): measured 2026-08-05 with py-spy against the live daemon it was **45.2% of all samples**, with `processes.py` the outermost module for 50.1% and the daemon holding 22.6% of a core at rest.

`_refresh_tree` already builds the full table for the pass, so `_parents` is the only permitted source of a parent pid; `ppid()` is a fallback for a pid younger than that refresh and nothing else.
Iteration-count health could never have shown this: `process-inspector` ticks about every 6.5 s, making it one of the *least* frequent loops in the daemon.

### Tuning a wait below the OS timer tick is not tuning

Windows expires waitable timers on a global ~15.625 ms boundary, so `threading.Event.wait(0.0005)` and `asyncio.sleep(0.0005)` both cost ~15.6 ms, and a 40 ms wait rounds *up* to 46.6 ms.
That silently defeated the PTY reader's three-rung poll ladder: every rung resolved to the same value, and the rung meant to be cheapest cost exactly as much as the idle one.

`timer_resolution.raise_timer_resolution()` is therefore called before the event loop in the daemon and before any reader starts in the supervisor.
Since Windows 10 2004 `timeBeginPeriod` is per-process, so this asks for a sharper timer for swe-mux and leaves the rest of the system alone.
Measured end-to-end keystroke-to-echo across the full websocket, daemon, supervisor, and ConPTY path: **p50 16.3 ms to 2.35 ms, min 15.65 ms to 2.03 ms, max 17.1 ms to 6.4 ms**.
`time.sleep` was never affected, because CPython already backs it with a high-resolution timer on Windows; only the primitives this code waits on were.
Anything measuring latency must read the *effective* period from `timer_resolution.effective_period_seconds()` rather than assuming either value, or it reports the scheduler as congestion at 15.6 ms and dismisses real stalls as noise at 1 ms.

### Directory walks read mtime from the walk, not from a second syscall

Windows fills a `DirEntry`'s stat fields during enumeration, so `os.scandir` answers for free what `Path.glob` plus `path.stat()` pays a syscall per file to re-fetch.
Codex rollout discovery (`adapters/codex.py`) walks a tree that mux never prunes, on a 2 s switch-watch tick, and it runs **synchronously on the event loop**: measured against 1,314 rollouts in 158 directories, 35.8 ms to 7.5 ms (4.8x), turning a recurring daemon-wide stall into a much shorter one.

That tree cannot be pruned by directory name to go faster, because `codex resume` appends to the original rollout and a file under an old date routinely holds the newest mtime.
The walk is cached whole for that window rather than sliced to the newest few, because locating a *known* conversation (`transcript_path`) matches on the file name and would otherwise pay a second walk.
Expensive-but-honest metrics such as unique set size are opt-in per request, never on the cadence.

## Git

**A monitor must not mutate what it monitors: every read-only Git call passes `--no-optional-locks`.**
`git status` and `git diff` refresh the index and *write it back* whenever a tracked file's mtime has moved, taking `.git/index.lock` to do so.
In a repository where agents are editing files that is every poll, so a 5-second read of the branch name was writing to the user's repository and contending with the agents it was watching.

Verified 2026-08-05 by touching a tracked file and comparing `.git/index` mtime across both forms: plain `status` rewrote it, `--no-optional-locks status` did not, with byte-identical output.
Effect on `git-monitor`, measured over 133 polls before and after: p50 321 ms to 73 ms, **p95 4,415 ms to 83 ms**, worst 5,629 ms to 169 ms, `busy_share` 9.1% to 1.5%.

The failure mode this removes is worse than the waste: a write in flight when the daemon is killed strands `index.lock`, which blocks *every* Git operation in that repository for every agent until someone removes it by hand.
The flag is global and must precede the subcommand; `tests/test_git_phase4.py` pins both facts.

## Session identity

Interactive readiness and durable registration are distinct.
Once a ConPTY is usable, publish the in-memory session and return, then serialize history registration behind it.

Keep root-process identity separate from active nested-agent identity.
Promotion is a `shell → agent` transition, matching demotion returns only that promoted shell, and every transcript candidate is checked against other live native and path claims.
Supervisor snapshots mirror mutable observation state, not authority: adoption reasserts immutable spawn identity, repairs legacy conflicts, and quarantines any history run proven to contain sibling evidence.

Conversation identity is a third axis beneath both.
An in-CLI `/clear` or `/new` keeps the PTY and the root identity while replacing the provider conversation, so it is a **new agent run** (`_apply_conversation_rollover`), never an in-place rekey.

Stop the observer before rewriting that identity.
It re-derives `native_session_id` from the file it is tailing at the top of every loop, so a cancellation that has not landed will put the retired id back.
The observer's own switch path applies the rollover in place for the same reason: calling the public entry point would cancel the caller.

Adoption treats `agent_run_id != session id` on a root agent as corruption.
`agent_run_seq > 0` is the marker that exempts a legitimately rolled run - but not one whose conversation a sibling's root identity claims, since that roll is itself corruption and is repaired back to the spawn anchor.
Backends whose CLI reports rollovers over the hook ingress (`reports_conversation_rollover`: Claude) never take the transcript-switch heuristic.
`agent_lifecycle_id` only moves on CLI-confirmed rollovers, which is what makes it the heal target for the state watchdog's live identity sweep (`_reconcile_identity_collisions`): no two live sessions may claim one `(backend, native_id)`, and a Claude session provably off its own conversation is rebound to its anchor.

## Project identity

Once a route has resolved an explicit Project, Project-resource helpers must receive that canonical identity: `_registered_identity(project)` into the `project=` keyword on `read_note`, `write_note`, `initialize_note`, `read_project_config`, `write_project_config`, and the observation helpers.
Git discovery answers "which worktree contains this path", which is the wrong question once the owner is known: a Project registered *inside* a larger worktree resolves to the enclosing toplevel, and every derived path - notes, config, observations - lands in the wrong Project.
`git_projects.rebase_identity` re-anchors the root and scope while keeping repository-group metadata describing the real worktree.

## Task subprocesses

A one-shot Project Action spawns its target directly under the supervisor's ConPTY: the shell for a `shell` step, and the PATH-resolved program (or `%COMSPEC%` for a `.cmd`/`.bat` shim) for a `process` step.

**No swe-mux executable may appear in a task's process tree.**
One did once (`swe-mux-action.exe`, built into `dist/swe-mux`), and a live task terminal then held that directory open and blocked the redeploy swap.
Exit code zero maps to completed or exited; nonzero remains crashed and observable.

Step `cwd` and `env` travel as spawn-request fields, not as an encoded argv payload.
A relaunch replays them from the record (`spawn_cwd`, `spawn_env`), because neither is recoverable from the argv alone.

Windowed builds must route only allowlisted internal module entrypoints through `desktop.py`.
Daemon-owned maintenance subprocesses use `subprocess_flags.py`, while interactive commands remain under ConPTY, so suppressing console flashes never suppresses terminal output.

## The supervisor contract

The PTY supervisor (`supervisor.py`) cannot be hot-updated without killing every live session, so volatile code belongs in the daemon.

In supervisor mode the daemon must never assign a supervised PID to a daemon-held Job handle, which would reap agents on daemon exit; nested per-session Jobs are created supervisor-side.
Shutdown intent comes from outside the daemon: quit reaps through `reap_all_and_exit`, and detach only flushes mirrored session metadata.

Because those per-session Jobs live supervisor-side, anything the daemon needs to know about Job membership must come over the wire (`job_pids`, consumed by process attribution).
**A new supervisor message must not bump `PROTOCOL_VERSION` unless the wire format genuinely changed.**
A mismatch stops a new daemon from driving the already-running supervisor, and the only way to update the supervisor is to kill every live session.
Add the message, let an older supervisor answer "unknown message type", and degrade on the daemon side.
`spawn_status` is the current example: it exists so a daemon whose spawn reply was lost can learn the true outcome instead of guessing, and against a supervisor that predates it the daemon keeps its previous behaviour and logs the ambiguity.

`spawn` is idempotent on the session id, and the reservation - created the moment the request is accepted - is the deduplication key.
That matters on the daemon side too: only a `spawn_status` of `unknown` proves nothing was reserved, so it is the one state under which an in-process fallback cannot leave two agents mutating one workspace.

Supervisor teardown quiesces before it reaps: the closing flag goes up, the listener closes, new spawns are refused, in-flight ones are drained and any child born during shutdown is stopped, and only then do the Jobs close.
Closing the reaper Job first orphans anything created a moment later, which is the one failure a reap cannot report.

The frozen supervisor ships as its own bundle (`dist/swe-mux-supervisor`), never inside `dist/swe-mux`, so app rebuilds cannot collide with a running supervisor's image.
Keep the supervisor's import closure inside the hash-gated source list in `packaging/build_desktop.py`; adding an import to `supervisor.py` or `pty_host.py` without updating that list ships a stale bundle.
A module the daemon *also* wants (`nested_job.py` is the one) may be shared only while its own imports are already in the closure - otherwise the daemon's need drags volatile code into the near-frozen half, and every future change to it costs a session reap.

Daemon self-restart (`/api/daemon/restart`) must spawn the successor with `--relaunch-wait` and detach intent.
It is refused without an attached supervisor unless forced, because an unpreserved restart is a session-killing action.
The frozen-app redeploy (`/api/daemon/redeploy`) follows the same authority rule, and must spawn `packaging/redeploy_desktop.py` detached from the daemon's process group and lifetime, because the script stops this very daemon mid-run.
Its cwd is the source root - never inside `dist/` - and the child env is scrubbed of parent-Claude session markers.

## Windows Job objects and process lifetime

Windows Job membership is inherited by every descendant, and the supervisor's per-session Jobs are kill-on-close: anything relaunched from a shell inside a session dies silently when that session is removed.

The session Jobs therefore set `JOB_OBJECT_LIMIT_BREAKAWAY_OK`; containment is unchanged, since escape is opt-in per spawn.
Every relaunch that must outlive sessions - supervisor spawn, daemon successor, tray-to-daemon, the redeploy script and its app relaunch - goes through `subprocess_flags.popen_outside_job`, which requests `CREATE_BREAKAWAY_FROM_JOB` and falls back to a plain spawn if denied on pre-BREAKAWAY_OK Jobs.
`CREATE_NEW_PROCESS_GROUP` does **not** escape a Job; never treat it as detachment.

The daemon, tray, and supervisor each call `win_jobobj.process_in_job()` at startup and leave a loud warning when they find themselves inside a Job - the poisoned-launch breadcrumb.
That same inheritance is what makes Job membership a sound *attribution* source: since only an opt-in breakaway leaves the Job, a member is provably the session's even when the parent chain to it has been broken by an intermediate exit.

A Windows process's working directory locks that directory against deletion.
Every long-lived swe-mux process - shell-spawned daemon, self-restart successor, supervisor - must anchor its cwd in the data dir, and the supervisor chdirs itself defensively at startup: a supervisor whose cwd landed inside `dist/` would silently block every session-preserving rebuild even though its own image lives elsewhere.

## Death forensics and logs

Nothing in-process can observe an external TerminateProcess, so death forensics live outside the daemon.
`lifecycle.py` keeps `daemon-heartbeat.json` fresh (~10 s) and marks clean exits with their intent, and the next daemon reports a predecessor whose record has no clean exit and a dead pid.
The tray, which holds the daemon's process handle, ledgers the observed exit code.

Rotating logs are per-process-owned files: `daemon.log`, `access.log`, and `supervisor.log` via stdlib handlers.
Console redirects (`desktop-daemon.log`, `supervisor-console.log`, `daemon-relaunch.log`) stay append-only crash catchers and are never shared with a rotating handler, because the child holds the redirect handle for life and rotation of the same file can never succeed.

## Desktop lifetime

Desktop presentation and daemon lifetime remain separate processes.
Close or minimize hides the WebView; only authenticated loopback Quit stops the daemon.
Never expose shutdown through the ordinary remote-control authority.
