# Daemon load isolation

## Purpose

swe-mux must stay responsive however many agents are running.
Today one Python process serves terminals, the UI, and every observation, ingestion and bookkeeping job, and all of them share one event loop and one GIL.
Under fleet load that process starves its own event loop, and every keystroke, terminal frame and API response waits with it.
This plan isolates the interactive terminal path in its own process, moves bulk work into a worker process, removes avoidable load from the daemon, and fixes the supervisor coupling that lets a slow client throttle agents.
It is one ordered checklist.
Items are worked in order, and an item may reorder, split or remove later items when its findings say so.

## Evidence

### The 2026-10-16 incident

- 20:49-21:06 CDT: 26 loop stalls of 3-9 s, 145 s in total (`loop_stalls` rows in `mux.db`, `loop-stalls.log`).
- Fleet: about 36 managed agents, after an orchestrator session spawned 20 Codex `xhigh` agents through `request_spawn` between 20:41 and 20:44.
- Host: 80-90% memory in use, 1,090-1,276 processes, CPU intermittently at 90-100%.
- The desktop monitor marked the daemon unresponsive about 20 times and correctly did not replace it; sessions survived because the PTY supervisor owns them.
- 68 of 73 main-thread samples sat at GIL-releasing, non-blocking syscalls (IOCP `_poll`, `recv_into`, `send`, log `flush`, file reads), with a different frame in each sample.
  The loop was waiting to reacquire the GIL rather than running one slow callback: the CPython convoy effect, which costs up to the 5 ms switch interval per release while another thread is runnable.
- `canary_starved` was false in 25 of 26 stalls.
  It detects one long continuous GIL hold and cannot see many medium ones.
- The daemon ran 83-86 threads; the non-parked ones were doing SQLite store operations, psutil process inspection, Codex rollout scans, transcript reads and window enumeration.
- Request mix 21:07-21:17: 4,085 requests, including 1,573 hook POSTs, 897 OTLP posts and 88 full fleet refreshes.
- Hook POSTs average 50.2 KiB; 6.4 GB of hook bodies arrived over 3.7 days of uptime.
- 1,688 hook POSTs were refused with 413 between 21:06 and 23:37, all over the 256 KiB cap (`routes/agent_ingress.py:210,256`), and `hook_client._post` resent each refused body three times (`hook_client.py:102-115`).

### Code facts the design rests on

- The supervisor accepts several authenticated connections and keeps a subscriber set per session (`supervisor.py:194,481-514,550-570`).
- Its fanout awaits `drain()` on any connection whose buffer exceeds 2 MiB, serially and with no timeout (`supervisor.py:76,881-920`).
  The buffer is per connection across all sessions, so a daemon that stops reading stalls every session's output to every subscriber.
- The PTY reader queue holds 1,024 chunks (`pty_host.py:239`) and the reader blocks while it is full (`pty_host.py:369-430`), so a long daemon stall can back-pressure ConPTY and the agents themselves.
- `_serve_connection` drains after every request (`supervisor.py:404-405`), so a connection that stops reading also stops having its own writes processed.
- The daemon reads supervisor frames on its event loop (`supervisor_client.py:670-709`).
  When its consumers fall behind it drops the oldest bytes (`supervisor_client.py:400-436`), after which its ring positions silently diverge from the supervisor's.
- The daemon ignores unsolicited supervisor frames it does not recognise (`supervisor_client.py:685-709`), so new frame types are backward compatible.
- The terminal socket is bound to in-process `Session` state: input ownership, viewports and geometry, replay and resync, flow control, repaint scheduling, and input side effects (`routes/pty.py:180-1058`; `session.py:2047-2786,7502-7548`).
- Delivery readiness reads `input_revision` and `last_input_event_ts` (`delivery_readiness.py:507-535`), and `send_next` evaluates and writes with no await in between (`prompt_queue.py:2397-2446`).
  That atomicity exists only because input accounting and delivery share one loop.
- Two daemon PTY writers bypass input accounting entirely: `meta_hooks.py:350-353` and `processes.py:2213-2215`.
- The frontend opens every socket same-origin and relative (`frontend/src/api.ts:80-88`).
  The phone reaches the daemon through Tailscale Serve's root mapping on 443 (`tailscale.py:654-656`) or directly over HTTP.
- `security_middleware` accepts only allowed hosts and requires `Origin` to match `Host` exactly on WebSocket upgrades (`server.py:481-545`); the CSP already allows cross-port `ws:` and `wss:` (`http_support.py:117`).
- The updater decides supervisor replacement only on `supervisor_protocol` (`update_install.py:44-61`; `bundle_metadata.py:62`), so a supervisor change that keeps the protocol never reaches an installed copy.
- `multiprocessing` is not safe in the frozen app: no entry point calls `freeze_support()`, and a spawned child would land in the desktop argument parser (`desktop.py:1165-1184`).

## Decisions

- **Terminal gateway on a dedicated port.**
  A new long-lived process serves `/pty/{sid}` on `terminal_gateway_port` (default: the daemon port plus one), and everything else stays on the daemon.
  The daemon's own `/pty` route remains the fallback for sessions the gateway cannot serve and for any time the gateway is down.
  An edge proxy owning 8765 was rejected.
  It would make the gateway a full streaming reverse proxy and the single point of failure for every request, and it would force the loopback-peer checks in `routes/system.py`, `routes/maintenance.py`, `routes/frontend.py` and `routes/agent_ingress.py` onto a forwarded-peer header.
- **One deliberate supervisor update.**
  Every supervisor change in this plan ships together, once, on an operator-scheduled reap of all sessions.
  Every other item works before that update lands; the update completes the isolation guarantee.
- **The gateway outlives daemon restarts.**
  The daemon discovers or spawns it the way it does the supervisor, so terminals keep working through a daemon restart, hang or replacement.
  It runs from the app bundle as `swe-mux.exe --gateway-child`, so a redeploy or an update restarts it and clients reconnect with a delta replay.
- **One implementation of the terminal protocol.**
  The terminal core is extracted from the daemon first and then runs unchanged both in process (the fallback) and in the gateway.
- **Input accounting lives where input is written.**
  The process hosting a session's terminal core owns `input_revision` and the last-input time, and serializes every PTY write for that session.
  Writes that depend on input state are conditional and evaluated atomically in that process.
- **One worker process for bulk work, owned by the daemon.**
  It hosts process inspection, the canonical telemetry ledgers and, if the evidence holds, transcript I/O.
  It dies with the daemon, and `mux.db` keeps a single writing process.
- **No `multiprocessing`.**
  Every new process is a frozen-app entry flag plus framed IPC over token-authenticated loopback, the same shape as the supervisor.

## Rules for every item

- Measure with the load harness before and after, and record the numbers in the item.
- Never weaken an assertion to green a gate.
- New supervisor messages and capabilities degrade gracefully on an older supervisor and never bump `PROTOCOL_VERSION` (`technical/backend/packages/runtime-rules.md`, the supervisor contract).
- No item may add synchronous work to the daemon's event loop.
- Runtime validation (daemons, harness runs, redeploys) happens from the primary checkout only, against an isolated data dir and port unless the item says otherwise.
- Each item updates the documents that `.docs/CLAUDE.md` routes its change to.
- A finding that changes a later item is written into that item before moving on.

## Checklist

- [x] **1. Load harness and baseline.**
  - **Why:** every later item needs a reproducible before and after, and the incident cannot be replayed on the live fleet.
  - **Built:** `tools/fleet_load_harness.py` starts its own daemon from the checkout (`python -m swe_mux`) on an OS-allocated port, with the data dir and a re-homed `HOME` under a scratch directory, and scrubs `MUX_*`, `SWE_MUX_*`, `CLAUDE*` and `CODEX_*` from its environment, so it never reaches 8765, `~/.mux` or the operator's conversations.
    It is safe to run from a worktree for the reason the `live_daemon` tier is.
    - N shell sessions each run `tools/load_session_generator.py`, which promotes its pane to a Claude session the way the agent launcher does, appends a transcript, writes `scroll` or `redraw` terminal output at a set rate, posts `PostToolUse` hooks (lognormal around 50 KiB) and OTLP `tool_result` batches through the session's own credentials, and starts child processes.
    - One xterm-like client per session acknowledges output; one quiet probe session is timed keystroke-to-echo continuously; one `/events` client reports desktop presence, without which the readiness watcher skips its pass; the window's own polls (`/api/sessions` 1 s, `/api/health` 2 s, `/api/processes?summary=1` 10 s) are timed.
    - `--burn N` adds below-normal CPU-bound processes standing in for agent builds; `--set key=value` writes config lines; `--profile N` records `py-spy record --gil --threads`.
    - `tools/perf_snapshot.py` gained `--gil` and `--threads` and reports per-thread GIL share.
  - **Found on the way:** a hook into a plain shell pane raised `KeyError: 'shell'` and answered 500 with a traceback per POST; it is now a 409 `not_an_agent_session` (`routes/agent_ingress.py`, `tests/test_security_phase5.py`).
  - **Baseline** (2026-10-17, this host with the live fleet running beside it; 120 s windows unless noted; 36 sessions, `scroll` 20 KB/s each, 3 hooks/s and 1.5 OTLP posts/s fleet-wide):

    | Run | Echo p50 / p95 / p99 | `/api/sessions` p50 / p99 | Hook p95 / p99 | Stalls >= 3 s | Lag samples >= 100 ms |
    | --- | --- | --- | --- | --- | --- |
    | idle (1 session, no load) | 2.7 / 5.7 / 8.9 ms | 3.5 / 5.1 ms | - | 0 | 0 |
    | 36 sessions, no browser | 11 / 178 / 292 ms | 161 / 417 ms | 159 / 326 ms | 0 | 17 / 236 |
    | 36 sessions + `/events` browser | 134 / 346 / 400 ms | 135 / 388 ms | 156 / 399 ms | 0 | 20 / 234 |
    | 48 `redraw` 40 KB/s, 6 hooks/s (90 s) | 173 / 524 / 905 ms | 184 / 567 ms | 222 / 597 ms | 0 | 24 / 170 |
    | 36 sessions + browser + `--burn 32` (90 s) | 316 / 1,254 / 3,502 ms | 326 / 4,372 ms | 2,408 / 3,704 ms | 1 (3.88 s) | 50 / 134 |

    The `--burn 32` run is the incident shape: a saturated host turns sub-second lag into multi-second stalls even though the daemon runs above normal priority and the burners below it.
  - **GIL profile at 36 sessions with a browser** (`py-spy --gil --threads`, 45 s): the main thread held the GIL in 83% of samples.
    Screen classification (`pty_tail_explain`, `screen_region_text`, `_normalize_tail_text`) was about 47% of all samples, and 32.5% came from `GET /api/sessions` alone: its one-second screen-verdict cache misses on a one-second poll, so every poll re-classified every session.
    The readiness watcher was the costliest loop (15.3 s busy in 121 s), `_revalidate_unseen` building fresh `psutil.Process` objects was 8-10% (on an executor thread), and the plugin event dispatcher ran 10,865 iterations with SQLite reads.
  - **Where:** `tools/fleet_load_harness.py`, `tools/load_session_generator.py`, `tools/perf_snapshot.py`, `development/PERFORMANCE_RUNBOOK.md`.

- [x] **2. Stall diagnostics that separate convoy from saturation and name the burner.**
  - **Why:** the incident's stall lines blamed parked threads and reported `cpu=0.0`, and nothing could say who held the GIL.
  - **Built** (`design/features/daemon-resilience.md` § What kind of stall it was):
    - `parked_threads.py`: threads mark their own blocking waits, written into each capture as `[parked:reason]`; marked are the `win_process_files` name worker's inbox, each git watch's wait inside watchfiles, and the `mux.db` operation lock, whose waiters are counted as `db_lock_waiters`.
    - `host_metrics.py`: per-thread CPU (`GetThreadTimes` on cached handles with the GIL held on Windows, `psutil.Process().threads()` elsewhere; `pthread_getcpuclockid` rejected because an expired id is undefined behaviour), host CPU from `cpu_times()` deltas, and memory and process count through GIL-releasing ctypes on Windows.
    - The canary samples thread, process and host CPU once a second and at each capture, and the loop's ready-queue depth while it is overdue; the explanation differences the last sample before the stall against the first after it.
    - `diagnose` states one verdict per stall: `loop_blocking_call`, `loop_saturated`, `gil_convoy`, `host_starved`, `blocked_wait` or `unsampled`, with the GIL holder from an external dump or the CPU ranking.
    - `executor_accounting.py`: every daemon executor, the default one included, charges each work item's thread CPU to a label naming the callable, so a CPU-burning worker is named by what it ran; `/api/diagnostics/background` gains `executor_work`, and a guard test refuses a bare `ThreadPoolExecutor(`.
    - An optional `py-spy dump --nonblocking` at a stall's first capture (`MUX_STALL_PROFILER`, or `py-spy` on `PATH`), at most every two minutes; `swemux doctor` reports it as `background.stall_profiler`.
    - The diagnosis persists as `loop_stalls.diagnosis_json` (telemetry schema 10), and the `loop_stall` line leads with the verdict.
    - `MUX_STALL_THRESHOLD_SECONDS` (0.5-60) lowers the capture threshold for the harness; `--stall-threshold` and `--stall-profiler` pass it and a `py-spy` path through the harness's environment scrub.
    - Retention: `access.log` keeps 20 backups of 10 MB, about 64 hours at the 2026-10-16/17 average of 3.3 MB an hour and 33 at the incident's 6.4; `daemon.log` already spans weeks.
  - **Found on the way:**
    - The first harness run with verdicts named `asyncio_21` and `asyncio_1` as the GIL holders of two convoys, both default-executor workers that had finished their burst before the stack capture; hence the executor accounting above.
    - The explanation itself held the GIL for about 55 ms: `psutil.virtual_memory()` and `psutil.pids()` cost 29 ms and 27 ms per call on a 1,200-process host. Now ctypes calls that release it.
    - The lag probe waited on each explanation, and the canary captured a loop that was merely slow: one run wrote 35 dumps for four stalls, and each stray dump was later read as the first dump of the next stall. The explanation now runs beside the probe, a capture fires only once the loop is a threshold *late* (the probe interval counted), and each capture is stamped so an explanation reads only its own.
    - `host_starved` is real: with the scratch daemon at above-normal priority and its sessions below normal, the loop got only 33-56% of a core in those stalls while the host sat at 100%. Priority alone does not shield the daemon, so per-keystroke work has to shrink, not only move.
    - A plain shell pane's hook POST no longer 500s (item 1), and the race in item 3 reproduces deterministically (`tests/test_win_process_files.py`).
  - **Measured** (48 `redraw` sessions at 40 KB/s, 6 hooks/s, `--burn 32`, capture threshold 0.75 s; runs on this host vary several-fold with the live fleet beside them):
    - Every stall got a verdict. Across the runs: `gil_convoy` with the holder `ProcessInspector._collect_all` at 38-47% of the window (once a worker at 80%), `host_starved` with the loop at 33-56%, and `loop_saturated` with the loop at 74% and 257 callbacks queued.
    - Ready queues were 99-320 deep in every stall, so at this load the loop is never short of work; `READY_DEEP` (64) separates a saturated loop from one long call only when the loop itself was busy.
    - Executor CPU over a run: `ProcessInspector._collect_all` 9.5-11.7 s in 20-28 passes (0.42-0.59 s each, GIL held), `CanonicalTelemetryService._record_batch` 5.4 s, `run_sqlite_operation:HistoryIndex.append_event` 2.4-2.8 s over 16,700 calls, `JsonlTailer._read_and_decode` 0.8-1.1 s, `Path.stat` 0.3-0.7 s over 9,700-16,100 calls, `PluginStore._run` 0.3-0.5 s over 12,000-17,000 calls.
  - **Where:** `stall_watchdog.py`, `host_metrics.py`, `parked_threads.py`, `executor_accounting.py`, `server.py` (`_loop_lag_loop`, `_explain_stalls`, `_loop_stall_line`), `__main__.py`, `operational_telemetry.py`, `sqlite_store.py`, `win_process_files.py`, `git_watch.py`, `doctor.py`, `routes/diagnostics.py`, `logsetup.py`, every store's executor, `tools/fleet_load_harness.py`, `design/features/daemon-resilience.md`, `design/interfaces.md`, `design/features/operational-telemetry.md`, `technical/backend/packages/daemon-runtime.md`, `technical/backend/packages/runtime-rules.md`, `development/PERFORMANCE_RUNBOOK.md`.

- [x] **3. Fix the abandoned name-worker race in `win_process_files`.**
  - **Why:** a worker that finishes just as its caller's 100 ms wait expires checks `retired` before `_abandon` sets it, returns to `inbox.get()` and blocks forever (`win_process_files.py:235-253,296-304,376-381`).
    It counts toward `MAX_STUCK_WORKERS` and quarantines its pid permanently; the live daemon reports `stuck_workers: 2`.
  - **Built:** retirement through a sentinel. `_abandon` posts `None` to the worker's inbox after setting `retired`, and the worker returns on receiving it; a worker still in the kernel returns to find `retired` set and never reads the sentinel.
    A re-check under `_workers_lock` was rejected: it narrows the window without closing it, since the worker can still pass the check before the caller takes the lock.
  - **Tests:** `tests/test_win_process_files.py` forces both orders, the worker already back in its inbox when abandoned (failed before the fix: the thread was still alive after 5 s) and the worker still in a blocked name query, and asserts the thread exits and the pid's quarantine lifts.
  - **Where:** `win_process_files.py`, `technical/backend/packages/history-and-observation.md`.

- [x] **4. Rank the GIL consumers under load.**
  - **Why:** which work leaves the daemon, and in what order, should follow measurement rather than the code reading this plan started from.
  - **Measured** (2026-10-17, 36 `scroll` sessions at 20 KB/s with an `/events` browser, no burners, `py-spy record --gil --threads` for 45 s, 1,950 GIL samples; attribution by `.trash`-local scripts walking each stack to its subsystem entry point):

    | Share of GIL samples | Where | Consumer |
    | --- | --- | --- |
    | 44.3% | loop | Screen classification (`pty_tail_explain`): 34.1% from the readiness watcher's per-second `evaluate`, 6.8% from the state watchdog's SSH-boundary check, 3.2% from `GET /api/sessions` fleet rows |
    | 19.1% | loop | asyncio and aiohttp dispatch: IOCP polling, websocket sends, JSON encoding |
    | 11.5% | executor | Process inspection, 8.0% of it `psutil` `create_time` in `_revalidate_unseen` building fresh `Process` objects |
    | 4.9% | loop and executor | Transcript observation |
    | 2.5% | loop and executor | Canonical telemetry |
    | 2.5% | loop | `/pty` websocket handling |
    | 1.8% | loop | Supervisor frame reading |
    | 0.7% | loop | Fleet intelligence |
    | 0.6% | executor and loop | Event persistence |
    | 0.2% each or less | | Plugin events, `/events` fanout, session recovery, status timeline, ghost-window sweep, git watch |

    The main thread held the GIL in 81% of samples (item 1: 83%).
    Which caller pays for screen classification depends on which one reaches a session first each second, because they share `_pty_state`'s time-based cache: item 1's profile charged 32.5% to the fleet rows, this one 34.1% to the readiness watcher.
    The off-loop half agrees with item 2's executor accounting: `ProcessInspector._collect_all` 6.0 s of CPU in 29 passes, `CanonicalTelemetryService._record_batch` 3.8 s, `HistoryIndex.append_event` 2.1 s over 12,700 calls, `JsonlTailer._read_and_decode` 1.0 s, `PluginStore._run` 0.7 s over 12,700 calls, `Path.stat` 0.5 s over 17,200 calls.
  - **What it changes:**
    - The predicted order (process inspection, transcript tailing, telemetry, event persistence, screen classification, ...) was wrong at the top: screen classification on the loop is the largest consumer by a factor of four, and event persistence is negligible.
    - Loop-busy time is not GIL share: the plugin dispatcher logged 19 s busy in the window and 0.2% of GIL samples, because its time is spent awaiting SQLite. Rank by the profile and `executor_work`, never by `costliest`.
  - **Work order** for the mitigations, replacing numeric order: **7** (screen classification, 44%), **6** (per-tick waste, now including `_revalidate_unseen`'s fresh `Process` objects), **9** and **10** (hook payloads and reply path: lost events and timeouts rather than GIL), **8**, **11**, then **5** last, so the switch interval is chosen on the mix that remains rather than on one dominated by a consumer about to disappear.
    For the worker: **20** (process inspection, 11.5% and the convoy holder in item 2's stalls) before **21** (telemetry, 2.5%); **22** stays gated on a re-measure after item 6's batched stats; **23** is removed (below).
  - **Where:** this document.

- [x] **5. GIL switch interval.**
  - **Why:** each GIL release by the loop can cost up to the switch interval, 5 ms by default, to win the lock back while another thread is runnable.
  - **Change:** a validated `gil_switch_interval_ms` config key, applied with `sys.setswitchinterval` in `__main__.main()` after `raise_timer_resolution()` (`__main__.py:428-444`) and in every new process entry point, and reported on `/api/diagnostics/background`.
    The supervisor is left alone.
  - **Settle first:** measure 5, 2, 1 and 0.5 ms on the harness (loop-lag p99, echo latency, background throughput).
    Windows GIL waits are millisecond-granular, so a sub-millisecond value may truncate.
  - **Done when:** the default is the measured best and the measurements are recorded here.
  - **Built:**
    - `gil_switch_interval_ms` (1-20 ms, default 5), applied in `__main__` after the timer period is raised, again live by `runtime_config` on a change because the interval is process-wide and immediate, and reported as `gil_switch_interval_ms` on `/api/diagnostics/background`; a Settings control under Processes.
    - In `gil_switch.py`, not `timer_resolution.py` where it would naturally sit: that file is in the supervisor's hash-gated source closure (`packaging/build_desktop.py`), so editing it would have made the next update the session-reaping supervisor rebuild for a daemon-only knob. A test holds the new module out of the closure, and no closure file differs from master anywhere on this branch.
    - "Every new process entry point" is the daemon today; the worker process (item 18) applies the same key when it exists.
  - **Measured** (36 `scroll` sessions at 20 KB/s, 3 hooks and 1 decision a second, no burners; the scratch daemon's log confirmed each value in force; the last two runs repeat the first and third in reverse order):

    | Interval | Echo p50 / p95 / p99 | `GET /api/sessions` p99 | Hook p99 | Decision p99 | OTLP p99 | Lag samples >= 100 ms |
    | --- | --- | --- | --- | --- | --- | --- |
    | 5 ms | 3.6 / 13.1 / 47.0 ms | 90 ms | 21.8 ms | 25.4 ms | 18.5 ms | 0 / 241 |
    | 2 ms | 3.7 / 22.4 / 42.8 ms | 62 ms | 31.1 ms | 33.0 ms | 38.5 ms | 0 / 242 |
    | 1 ms | 3.2 / 7.1 / 13.3 ms | 56 ms | 33.7 ms | 23.0 ms | 19.2 ms | 0 / 241 |
    | 0.5 ms | 6.1 / 52.8 / 132 ms | 162 ms | 85.1 ms | 109 ms | 48.0 ms | 0 / 242 |
    | 1 ms, again | 4.4 / 14.2 / 32.9 ms | 80 ms | 33.8 ms | 33.8 ms | 31.1 ms | 0 / 241 |
    | 5 ms, again | 3.5 / 7.8 / 37.2 ms | 55 ms | 18.0 ms | 17.9 ms | 39.0 ms | 0 / 241 |

    1, 2 and 5 ms cannot be told apart: the first 1 ms run looked best for echo, and its repeat lost to the repeat at 5 ms on echo p95 and on hooks. 0.5 ms was worse on every latency, by two to four times at p99, and is below the 1 ms timer period the GIL's timed wait runs on, so the range starts at 1 ms. Background throughput did not move with the interval: the event journal wrote 13,127-13,188 events in every run, and the transcript-read pool's p99 wait ranged 41-77 ms from run to run with no relation to the value (41 and 43 ms in the two repeats, at 5 and 1 ms).
    **The default stays at CPython's 5 ms**, the best measured by being no worse than anything else, and the knob remains for a fleet unlike this one. The items before this one removed most of what competed with the loop (classification, per-tick hops, inserts, compression), which is consistent with the interval no longer mattering at this load; under saturation (item 11's runs) the stalls were long GIL holds by process inspection, which a switch interval cannot shorten because the holder releases on its own schedule inside C calls.
  - **Tests:** `tests/test_gil_switch.py` (applying sets and reports the interval, the range refuses 0.5 ms and above 20, a change applies live without a restart, the module stays out of the supervisor closure), `frontend/test/settingsCoverage.test.ts` (the control).
  - **Where:** `gil_switch.py`, `config.py`, `__main__.py`, `runtime_config.py`, `routes/diagnostics.py`, `frontend/src/Settings.tsx`, `frontend/src/demo/fixtures.ts`, `frontend/test/renderer/settingsConfigFixture.ts`, `design/interfaces.md`, `technical/backend/packages/{daemon-runtime,runtime-rules}.md`.

- [x] **6. Remove per-session no-op hops and per-tick waste.**
  - **Why:** each hop or poll is a GIL round trip multiplied by the fleet.
  - **Built:**
    - Switch watcher: adapters declare `announces_pending_transcript` (true only for omp's breadcrumb), and the watcher no longer makes a worker hop every 2 s per session to ask the rest; the harness adapter matrix requires every harness to declare it and an adapter declaring false to really answer `None`.
    - Transcript tailer: `batched_stat.py` answers every tailer's stat, including the wait for a file to appear, from one worker pass per 250 ms boundary of the loop clock; the boundaries are aligned, so tailers started at different moments share passes, and awaiting the stat is the poll's pacing. Measured: 4.0 passes a second carrying 36 files each, where each tailer used to make its own hop (about 150 a second).
    - Aligning the ticks exposed a burst: every tailer whose file grew submitted its read and decode in the same loop iteration, and the default pool ran 18 of them at once (peak, run 1 below), all contending with the loop for the GIL. Read and decode now run on their own two-worker `transcript-read` pool; the default pool's peak fell to 3, at a cost of 19 ms p50 and 100 ms p99 of queueing before a read.
    - `config-watch` takes its once-a-second stat in a worker. The parse stays on the loop because `load_config` may save a migrated file, and in a worker that save could interleave with a settings write.
    - Plugins: the store holds the registry in memory and every registry write drops the copy on the store's worker right after it commits; the dispatcher's per-event SQLite read (12.6k per run) is gone.
    - Prompt queue: the per-tick reads (the readiness watcher's summary, auto-delivery's policies and pause row, and each enabled session's head item, now `pending_head`, one row instead of the session's whole visible queue) are answered from memory between writes. Every store operation compares the connection's `total_changes` before and after, so a write that changed anything retires every cached read before its caller resumes, and one that changed nothing retires none. Measured: 2.0 cached reads a second and no database reads at all in the window.
    - Ghost-window sweep: the verdict cache is keyed on a start stamp read with the GIL released (`host_metrics.process_start_stamp`), and a `psutil.Process` is built only for a process not judged yet.
    - Process inspection: `_revalidate_unseen` marks a row exited when its pid is missing from the pass's own process table instead of constructing a `psutil.Process` to find out, which on Windows costs a host-wide pid enumeration per exiting child. The harness inventory had 538 exited rows against 155 active ones after 75 s, each of which used to pay that.
    - Process inspection, the rest of the same cost, found after items 9 and 10 by attributing the remaining `create_time` samples: every one (3.4-3.7% of GIL samples across two runs) came from `_handle` building handles for job members.
      Measured on this host: `psutil.Process(pid)` costs 0.045 ms for a live pid and 27 ms for one that has exited, including an exited process whose parent still holds a handle to it, and `psutil.pid_exists` costs the same (28 ms for an exited pid, 14 ms for one nothing has), so asking psutil first saves nothing.
      The membership list is read from the supervisor before the pass takes its process table, so a member the table lacks exited in between, and `_job_handles` now skips it without a syscall; short-lived tool children are job members, which is why nearly all the lookups came from there.
      For a pid the table lists that exits before the pass reaches it, `_handle` and `_revalidate_unseen` first ask `host_metrics.process_exited`: one `OpenProcess` and a zero-timeout `WaitForSingleObject` with the GIL released, about 0.03 ms either way, and `None` (ask psutil) for a process this one may not open.
      Measured against the run just before it (36 sessions, the same build otherwise): `create_time` samples 31 to 0, `ProcessInspector._collect_all` 82 ms to 49 ms of CPU a pass, `process-inspector` busy 3.5 s to 1.5 s in the window.
      Tests that install a fake `processes.psutil` describe a fake process table, so each also stubs the probe, which would otherwise ask the real host about those pids; one fixture listed a live detached server as a job member but left it out of the table, which no host can produce, and now lists it under its dead parent.
    - Screen classification, which this item's first measurement still showed at 23.8% of GIL samples (18.1% of it the readiness watcher): rules carry *needles*, ASCII text every match contains (`ScreenRule.requires`, implicit for `_contains`), and a rule whose needle is missing from the lowered tail is not run. Ten marker scans of the whole 32 KiB tail at about 90 us each (one regex at 430 us) were spent proving absences; a classification went from 2.2 ms to 1.2 ms. It is exact: `re.IGNORECASE` and `str.lower` fold onto ASCII letters identically except for U+0130, U+0131 and U+017F (measured over every code point), a tail holding one of those takes the full path, and a randomized test compares both paths. Also dropped from the key: the OSC title and progress, which no rule reads (`_RULES_READ_OSC`), so the state watchdog shares entries with every other caller; its SSH check fell to 1.1% of samples and needed no further narrowing.
    - The supervisor meta mirror serialized each changed session's meta, parsed it back and serialized it again to frame it; `meta_frame` frames the JSON it already has, byte-identical by test.
    - Logging: no thread that logs writes any more (`logsetup._LogWriter`). Measured with CPU-bound Python threads beside the emitter, a line written by the stdlib rotating handler cost the emitting thread p50 61 ms and p99 296 ms with four of them and 185 ms and 1.06 s with sixteen, because each of its stats, writes and flushes gave up the GIL. A plain `QueueHandler` with a `QueueListener` moved the cost rather than removing it: the listener fell 0.15-0.5 s behind with four contenders and 10-15 s with sixteen, which is what a hard kill would lose. The emitting thread now formats and queues, and one writer thread writes each pass with one write and one flush per file: about 0.1 ms per line for the emitter at any contention, and a lag of 39 ms p50 and 187 ms max with four contenders, 144 ms and 665 ms with sixteen.
    - Pool occupancy (`executor_work.pools`): peak running and queued items per executor and how long items waited for a worker, which is what the default-executor decision below needed.
  - **Not done, on the evidence:**
    - The default executor stays at `min(32, cpu + 4)`. With decoding on its own pool it peaked at 3 running and 4 queued with a 3 ms p99 wait at 36 sessions, so a bound would buy nothing measured, and it would risk queueing every `to_thread` behind slow blocking calls a real fleet makes and the harness does not. The live daemon reports the same occupancy once redeployed, and the bound can be set from that.
    - Git watch keeps its 300 ms `step`. Under a sustained write storm a longer step halves the paths delivered (2,200 to 1,200 in 8 s at 800 ms), but classifying a path costs about 0.5 us, so the whole storm costs well under a millisecond a second, and the price would be half a second more before Git status follows a change. watchfiles offers no filter that runs before paths become Python objects.
  - **Measured** (the item 4 configuration: 36 `scroll` sessions at 20 KB/s, an `/events` browser, `py-spy --gil --threads` for 45 s; run 1 is everything except the needle prefilter, the transcript-read pool, the log writer and the meta frame):

    | | Item 7 | Item 6, run 1 | Item 6, run 2 |
    | --- | --- | --- | --- |
    | GIL samples in 45 s | 1,092 | 1,004 | 825 |
    | Screen classification share of samples | 19.1% | 23.8% | 7.2% |
    | Keystroke echo p50 / p95 / p99 | 5.1 / 102 / 214 ms | 6.4 / 88 / 100 ms | 3.9 / 7.7 / 40 ms |
    | `GET /api/sessions` p50 / p99 | 31 / 257 ms | 19 / 107 ms | 10 / 68 ms |
    | `GET /api/health` p95 | - | 91 ms | 5.2 ms |
    | Hook round trip p95 / p99 | 102 / 160 ms | 26 / 77 ms | 21 / 39 ms |
    | Lag samples >= 100 ms | 3 / 239 | 0 / 240 | 0 / 240 |
    | `readiness-watch` busy in the window | 2.5 s | 3.0 s | 0.5 s |
    | Default pool peak running | - | 18 | 3 |

    In run 1 every latency maximum sat near 100 ms (echo 100.5, fleet read 109, health 99.6, processes 104), the signature of the aligned decode burst; run 2's are 44, 80, 30 and 30 ms.
    What remains on top: canonical telemetry (the costliest loop, 8.6 s busy, and `_record_batch` 2.6 s of CPU), event persistence (`HistoryIndex.append_event`, 12,600 calls with the history pool queueing up to 35 deep and waiting up to 66 ms, which is item 10), process inspection (`_collect_all` 51 ms a pass, item 20), and `SessionRecord.snapshot` through `dataclasses.asdict` at 4.6% of samples from session updates, the meta mirror and fleet rows.
  - **Tests:** `test_batched_stat.py`, `test_prompt_queue_read_cache.py`, `test_screen_needle_prefilter.py`, `test_log_writer.py`, `test_host_metrics.py` (including the exit probe against a running child, an exited one whose handle is still held, and a pid nothing has), `test_executor_accounting.py` (pool occupancy), `test_plugin_event_dispatch_cost.py`, `test_ghost_windows.py`, `test_processes_phase4.py` (a job member the table lacks is never asked about; a pid the probe calls exited gets no handle and its row exits), `test_pty_tail_cache.py`, `test_session_identity.py`, `test_harness_adapter_matrix.py`, `test_supervisor_protocol.py` (meta frame).
  - **Where:** `adapters/*.py`, `session.py`, `batched_stat.py`, `observation.py`, `runtime_config.py`, `plugin_store.py`, `prompt_queue.py`, `auto_delivery.py`, `ghost_windows.py`, `host_metrics.py`, `processes.py`, `executor_accounting.py`, `logsetup.py`, `supervisor_client.py`, `routes/diagnostics.py`, `tools/fleet_load_harness.py`, `design/interfaces.md`, `design/features/{backends,auto-delivery,plugins,ghost-windows,status-detection,delivery-readiness,processes-and-previews}.md`, `technical/backend/packages/{daemon-runtime,history-and-observation,agent-surfaces}.md`.

- [x] **7. Screen classification keyed on the bytes it reads.**
  - **Why:** item 4 measured screen classification at 44.3% of all GIL samples at 36 sessions, almost all of it on the loop: the readiness watcher re-classified every repainting screen every second (34.1%), the state watchdog's SSH-boundary check re-classified on its own per-session cache (6.8%), and fleet rows paid whenever they reached a session first (item 1 measured them at 32.5%).
    Two incident stalls caught the loop in `_normalize_tail_text`.
  - **Built:**
    - `session.SCREEN_VERDICTS`, one process-wide exact-content cache every scrollback classifier reads through (`session_screen_explanation`): the readiness tracker, the state watchdog (`SessionManager._pty_tail_explanation`, whose per-session cache it replaces), and the observation helpers.
      The key is a 128-bit BLAKE2b digest of the raw 32 KiB tail plus the backend, the normalized CLI status, the OSC title and progress, and the classifier function itself, so a hit is by construction what a recompute returns, authorization may take it, and a test that substitutes the classifier can neither read nor leave real verdicts.
      512 entries, LRU; hits, classifications and classifying time are on `/api/diagnostics/background` as `screen_classification`.
    - `DISPLAY_SCREEN_MAX_AGE_SECONDS` (2 s): the readiness watcher (previously 0), fleet rows and the queue summary (previously 1 s) may show a verdict measured on older bytes for that long while a screen keeps repainting. `screen_state`, the authorization path, passes zero.
      The keying the plan first proposed, `(agent_run_id, scrollback.written, cli_state_status)`, was not used: it would have needed a generation counter in `scrollback.py`, which is supervisor source and would have forced the supervisor update for a daemon-only change, and the content digest makes the generation unnecessary.
  - **Measured** (the item 4 configuration: 36 `scroll` sessions at 20 KB/s, an `/events` browser, `py-spy --gil --threads` for 45 s):

    | | Item 4 | Item 7 |
    | --- | --- | --- |
    | GIL samples in 45 s | 1,950 | 1,092 |
    | Screen classification samples (share) | about 864 (44.3%) | about 209 (19.1%) |
    | Main-thread share of GIL samples | 81% | 71% |
    | Keystroke echo p50 / p95 / p99 | 135 / 362 / 426 ms | 5.1 / 102 / 214 ms |
    | `GET /api/sessions` p50 / p99 | 136 / 687 ms | 31 / 257 ms |
    | Hook round trip p95 / p99 | 322 / 1,337 ms | 102 / 160 ms |
    | Lag samples >= 100 ms | 25 / 230 | 3 / 239 |
    | `readiness-watch` busy in the window | 17.1 s | 2.5 s |

    Runs on this host vary with the live fleet beside them, so single-run latencies are indicative; the GIL sample counts are the direct measure and moved by the same factor.
    What remains of classification is fleet rows 8.1%, the watcher 5.6% and the state watchdog's SSH check 5.4%; the last keys on the OSC title as well, so a repainting screen always misses for it, and is item 6's to narrow.
    Process inspection is now the largest single consumer off the loop (18.0%, `create_time` alone 12.4%).
  - **Tests:** `tests/test_pty_tail_cache.py` (every caller shares one classification; authorization re-measures changed bytes even under a warm display verdict; the cache is bounded), `tests/test_delivery_readiness_evidence.py`, `tests/test_session_row_routes.py`.
  - **Where:** `session.py`, `delivery_readiness.py`, `readiness_watch.py`, `observation.py`, `routes/sessions.py`, `prompt_queue.py`, `routes/diagnostics.py`, `design/features/delivery-readiness.md`, `development/PERFORMANCE_RUNBOOK.md`.

- [x] **8. WebSocket compression off for loopback peers.**
  - **Why:** aiohttp negotiates permessage-deflate by default, compressing frames of 16 KiB or less on the loop and larger ones on an executor thread (`network_usage.py:302-311`; aiohttp `_websocket/writer.py`), and on loopback that buys nothing.
  - **Change:** `metered_websocket` disables compression for loopback peers on `/pty` and `/events`, and keeps it for tailnet peers unless the harness shows it costs more than it saves there.
  - **Done when:** harness numbers are recorded for both peer kinds.
  - **Built:**
    - `metered_websocket` declines compression when the peer is loopback, so every WebSocket that goes through it (`/pty`, `/events`, and the preview relay) negotiates permessage-deflate only off-host; a caller that passes `compress` still decides.
    - The decision reads the forwarded peer (`request_peer`), not the socket's address. That matters: the phone does not connect to the tailnet listener but to loopback, through Tailscale Serve. Verified on the live daemon read-only: every request from the Android browser in `access.log` arrives from 127.0.0.1 with the `.ts.net` origin, and the network meter attributes 952 of its WebSocket connections to its tailnet address, which only `X-Forwarded-For` could have told it. Judging the direct address would have taken compression away from exactly the one peer on a real network.
    - The harness gained `--peer tailnet` (every request carries `X-Forwarded-For`, as through Serve) and `--ws-compress {offer,none}`, and reports how many sockets negotiated compression.
  - **Measured:**
    - What compression saves and costs on real terminal bytes, framed as aiohttp sends them (one deflate context per connection, level 1, a sync flush per message): the repository's captured Claude screens deflate to 16-18% of their size, coloured `git log --stat` and `git log -p` to 31-38%, for 4.5-13 ms of CPU per MiB on the screens and 9-20 ms on the logs, the higher figures at 512-byte frames.
    - The harness at 36 sessions (`scroll`, 20 KB/s each, 38 sockets; the same build both times):

      | | Loopback peer | Tailnet peer |
      | --- | --- | --- |
      | Sockets that negotiated compression | 0 of 38 | 38 of 38 |
      | GIL samples in deflate | 0 | 48 (5.8%) |
      | GIL samples in the WebSocket writer, deflate included | 36 (4.5%) | 73 (8.8%) |
      | Keystroke echo p50 / p99 | 3.6 / 47.6 ms | 3.9 / 43.2 ms |
      | `GET /api/sessions` p99 | 55 ms | 79 ms |

      Compression doubled the cost of sending: about 0.5 s of GIL in 45 s for 32 MiB, the 15 ms per MiB the offline measurement predicts. At this load that is not visible in latency, which is why the change is worth making on what it removes rather than on a latency number.
    - Tailnet keeps compression. Paying 10-20 ms of CPU per MiB to send a third or less of the bytes is the right trade where bytes cross a network, a phone on a cellular link most of all; and a phone attaches the one or two panes it shows, not the 36 the tailnet run gave it, so its real cost is a small fraction of the 5.8% above.
  - **Tests:** `tests/test_network_usage.py` (a loopback client that offers compression is declined, a peer forwarded from loopback keeps it, an explicit `compress` wins), each through a real handshake.
  - **Where:** `network_usage.py`, `tools/fleet_load_harness.py`, `design/features/remote-access.md`, `technical/backend/packages/daemon-runtime.md`, `development/PERFORMANCE_RUNBOOK.md`.

- [x] **9. Bounded hook payloads at the source.**
  - **Why:** hook bodies average 50 KiB and are parsed on the loop, oversized ones are refused and their events lost (1,688 in 2.5 hours), and the client resends every refused body three times.
  - **Change:**
    - Before choosing caps, list every reader of full hook payloads (the `events` table, canonical telemetry, tier0, observation's `PostToolUse` handling at `observation.py:4756-4791`) and confirm what each one needs.
    - `hook_client` bounds the body before sending: large fields such as `tool_response` and `tool_input` contents are cut to a per-field cap, keeping the original byte length and a digest, so the body always fits the daemon's cap.
    - `hook_client._post` does not retry an HTTP 4xx.
  - **Where:** `hook_client.py`, `routes/agent_ingress.py`, `observation.py`, `design/features/status-detection.md`.
  - **Done when:** the harness and the live daemon show no hook 413s, mean hook body size is recorded before and after, and every reader still passes its tests.
  - **Readers of a full hook body, and what each needs:**
    - Canonical telemetry (`PostToolUse`, `PostToolUseFailure`): the output's character count, byte count and SHA-256, never the output.
    - Tier 0 and the loop detector (`PreToolUse`): the call's normalized target, its content hash over the written content, and the digest of a target too long to store (`tool_call_evidence`).
    - Approvals (`PermissionRequest` and the other approval requests): the tool input's subject fields whole, and every other string value of 4,096 characters or fewer for the floor.
    - Conversation identity, cwd and transcript tracking: small identifying fields (`session_id`, `cwd`, `transcript_path`, `source`).
    - The generic EventBus fan-out, persisted into `mux.db`'s `events` table: nothing reads a tool's output from it. Yet it carried `tool_response` whole, so every `PostToolUse` output was written to `mux.db` for 90 days, 53 KB a row on average in the harness and whatever secret a tool printed, despite a code comment saying the output never entered that table.
  - **Built:**
    - `hook_bounds.py`, standard library only, run by the shim on every body: a tool result is measured whole and travels as its first 2,048 characters with the measurement; a reported tool input (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`) that is oversized is cut after its evidence is computed over the whole of it; other long text is cut only if the body still exceeds 192 KiB; the cap halves until the body fits. An approval request's input is never cut, because a rule matched against the head of a command could approve what its tail does.
    - What was measured travels as `_mux_bounded` (`output`, `tool_evidence`, `fields`), and the daemon prefers it to anything it could compute from what arrived, so no recorded value changes; a body without it (an older shim, or a harness plugin that posts directly) is measured on the daemon as before.
    - `tool_evidence.py`: `tool_call_evidence` moved out of `observation.py` so the shim can compute it without importing the daemon; the shim's marginal import cost is about 4 ms per hook.
    - The generic fan-out omits `tool_response` and `_mux_bounded`.
    - A 4xx is final: the shim neither resends nor spools a refused body.
    - The ingress cap is one constant (`HOOK_BODY_LIMIT`) shared with the shim.
    - The load generator posts through the same bounding (`--hook-client bounded`, the default) or unbounded (`raw`), with its body tail no longer clipped under the cap, and the harness reports the mean body size.
  - **Measured** (36 `scroll` sessions, 3 `PostToolUse` hooks a second fleet-wide, bodies lognormal around 50 KiB):

    | Client | Mean hook body | Refused (413) |
    | --- | --- | --- |
    | Raw, as before | 53,925 bytes | 5 of 437 |
    | Bounded to the 192 KiB budget only | 45,802 bytes | 0 of 437 |
    | Bounded, output sent as its head | 2,469 bytes | 0 of 436 |

    The live daemon before (read-only from `access.log`): 72 refused hook POSTs out of 4,931 in 71 minutes, 24 events each sent three times, all from one session whose tools printed large outputs. The after half needs the redeploy at the end of this work and is recorded there.
    Hook round-trip latency did not move measurably at this load (p95 18-21 ms in all three runs); what the head buys is 22 times less JSON parsed on the loop per hook and 22 times less written per row.
  - **Tests:** `tests/test_hook_bounds.py` (each reader against what it needs; an approval request's input survives a body over budget; the fan-out never carries an output; a 413 and a 404 are sent once and a 500 three times; a refused durable event is not spooled), plus the existing hook, approval and observation suites.
  - **Where:** `hook_bounds.py`, `tool_evidence.py`, `hook_client.py`, `routes/agent_ingress.py`, `observation.py`, `tools/load_session_generator.py`, `tools/fleet_load_harness.py`, `design/interfaces.md`, `design/data-model.md`, `design/features/tier0-facts.md`, `technical/backend/packages/harnesses.md`.

- [x] **10. Hook replies off the persistence path.**
  - **Why:** `emit` awaits its history insert before any subscriber sees the event (`event_bus.py:172-173`; `history.py:2922-2934`), so every hook reply waits behind at least two inserts under the `mux.db` operation lock.
    A `PermissionRequest` decision has a 3.0 s client timeout (`hook_client.py:35-45`), so during a stall auto-approval silently degrades to asking the user.
  - **Change:**
    - Answer `PermissionRequest` from in-memory approval state before persisting its records, and persist them afterwards in order.
    - Move the `PostToolUse` `json.dumps(..., sort_keys=True)` and SHA-256 off the loop, or hash the bounded field from item 9 directly (`observation.py:4756-4791`).
  - **Done when:** decision round-trip p99 under the harness sits far inside the client timeout, and the approval tests cover reply-before-persist ordering.
  - **Built:**
    - The change is not specific to `PermissionRequest`, because the wait was not: every emit awaited its own insert before any subscriber saw the event, so every hook reply, approval or not, queued behind the history worker once per event its handler emitted.
      `event_journal.py` takes persistence off the emit: the `EventBus` sink is synchronous, numbers the event in memory and fans it out at once, and a writer task writes what has queued in one ordered transaction on the history worker.
      Readers of the `events` table (`events`, `recent_events`, the workload telemetry) flush the journal first, so nothing that reads it can see a gap the fan-out already announced.
    - Sequence numbers are assigned before they are written, so a restart starts `JOURNAL_LIMIT` (10,000) past the highest one on disk rather than reusing numbers a hard kill handed out unwritten; a write that collides anyway renumbers rather than dropping.
      A failed write retries with backoff and is counted; past `JOURNAL_LIMIT` pending events the oldest are dropped and counted, which is what a database that stays unwritable for that long costs, in place of an emit that blocks forever.
      Shutdown writes what is pending synchronously, after the batch in flight finishes (that one runs shielded, because cancelling it at loop shutdown lost it in a first version).
    - The `PostToolUse` serialization and hash went off the loop in item 9: the shim measures the output and the daemon takes its measurement.
    - `/api/diagnostics/background` reports the journal (`event_journal`: written, batches, events per batch, pending, max pending, failures, dropped), and so does the harness.
    - The harness gained a decision probe (`--decision-rate`, default 1 a second fleet-wide): it turns auto-approval on in the scratch config, grants every session `allow_all` through the approval route, and times `PermissionRequest`s the daemon answers.
      Its first version shared the `PostToolUse` thread's hook source and lost 111 of 146 requests to ingress's per-source sequence ordering (`duplicate_or_stale_sequence`); it now posts under its own source and reports an ingress refusal separately from an unanswered decision.
  - **Measured** (36 `scroll` sessions, 3 hooks and 1 decision a second fleet-wide, bounded bodies; the same build both times except the sink, and neither with item 6's exit probe):

    | | Insert awaited per emit | Event journal |
    | --- | --- | --- |
    | Decision round trip p50 / p95 / p99 / max | 10.3 / 23.4 / 36.0 / 89 ms | 3.9 / 18.4 / 159 / 827 ms |
    | Decisions answered / at or past 3 s | 146 of 146 / 0 | 146 of 146 / 0 |
    | Hook round trip p50 / p95 / p99 | 5.1 / 19.7 / 25.9 ms | 3.3 / 16.9 / 46.0 ms |
    | Event writes | 13,121 single-row transactions, 2.19 s CPU | 13,168 events in 4,414 transactions, 1.08 s CPU |
    | History pool queued peak / wait p99 | 33 / 11.9 ms | 3 / 1.7 ms |

    The medians are the change: a decision no longer waits behind the inserts for the events its own handler emitted.
    The journal run's tail is one host-wide stall rather than the sink: keystroke echo (1.79 s), the fleet read (1.19 s), hooks (1.02 s) and OTLP (1.42 s) all peaked in it together.
    Neither run came near Claude's 3 s timeout, so the harness cannot show the failure this item exists for; the proof is `test_a_decision_is_answered_before_its_records_are_written`, which holds the history worker busy and requires the decision within a second and its records written afterwards, in emission order.
  - **Tests:** `tests/test_event_journal.py` (emit before write, order and batching, readers flush, shutdown writes what is queued, the restart gap, retry after a failed write, an unserializable field, a sink that is awaited), `tests/test_approval_decisions.py::test_a_decision_is_answered_before_its_records_are_written`, plus the event, approval and observation suites.
  - **Where:** `event_journal.py`, `history.py`, `event_bus.py`, `server.py`, `routes/diagnostics.py`, `tools/load_session_generator.py`, `tools/fleet_load_harness.py`, `design/interfaces.md`, `technical/backend/packages/history-and-observation.md`, `development/PERFORMANCE_RUNBOOK.md`.

- [x] **11. Lag-driven load shedding for deferrable loops.**
  - **Why:** housekeeping keeps its cadence while the loop is drowning, and nothing can defer it today (`background_tasks.py:167-327`).
  - **Change:**
    - `background.pace(name, cadence, *, deferrable)` replaces the `asyncio.sleep` in each loop.
      It reads a cheap recent-max accessor on the loop-lag monitor, which is attached to `background` rather than only published on the app (`loop_lag.py:102-118`; `server.py:2753`).
    - While recent lag is over a threshold, deferrable loops stretch their cadence up to a bounded cap and count the deferrals; nothing is deferred forever.
    - Deferrable: git sweeps, canonical telemetry rollup, reconcile, backfill and retention, the ghost-window sweep, usage refresh, provider quota polling, plugin registry refresh, store retention, media cleanup, the update check, the operational telemetry reconcile, and the process inspection cadence.
    - Never deferrable: the lifecycle heartbeat (desktop recovery reads it), the state watchdog, the session-recovery flush, the status-timeline flush, the listener guard, the loop-lag probe, the readiness watcher and auto-delivery.
    - Per-session tasks (transcript observer, switch watcher, discovery and exit checks) register with the accounting, so their cost appears in `costliest`.
  - **Done when:** governor state appears on `/api/diagnostics/background`, and the harness shows less stall time with housekeeping still completing.
  - **Built:**
    - `LoopLagMonitor.recent_max(seconds)` walks only the samples inside the window, newest first, and the monitor is attached to `background` at start (`background.attach_lag`).
    - `background.pace(name, cadence, deferrable=...)`: the cadence, then, for a deferrable loop while the worst lag of the last 5 s is at least 0.25 s, more waiting in 1 s steps until the lag clears or the allowance runs out. The allowance is three cadences and never more than 120 s, so under sustained lag a loop still runs at most that late, and it resumes within a step of recovery.
    - `background.defer(name, overdue=, cadence=)` for loops that tick for their work and decide inside the tick what is due: the Git monitor's safety sweeps (`git-sweep`) wait, and a change its watch reported, and polling where there is no watch, never do; and a process read finding a stale sample keeps it inside the same allowance unless the caller forces live state, so a request cannot quietly run the pass the governor just deferred.
    - Deferrable, as planned: the Git sweeps, the canonical telemetry rollup, reconcile and backfill, the operational telemetry retention and reconcile, the ghost-window sweep, usage refresh, provider quota polling, quota-usage catch-up, store retention, media cleanup, the update check, and process inspection. There is no plugin registry refresh to defer any more: item 6 made the registry an in-memory copy dropped on write.
    - `NEVER_DEFERRABLE` names the loops whose lateness is itself harm, and `pace` and `defer` ignore `deferrable` for them; a test holds the names to the constants their modules define. The listener guard belongs on the list and is not under this supervisor at all.
    - `background.account(name)` times per-session work under one name each, with faults left to the session's own handling: `session:transcript-observer` (per record), `session:transcript-switch` (per tick), `session:agent-discovery`, `session:agent-exit-check`. They rank in `costliest` as `aggregate` entries; the first saturated run below put the observer at 2.6 s busy over 6,542 records and, in the second, the switch watcher at 5.0 s over 2,088 ticks, figures nothing reported before.
    - `governor` on `/api/diagnostics/background` (shedding now, the recent lag, the bounds, and per name how often, how long and the longest), and the harness reports the window's deferrals beside how many times each deferred loop still ran.
  - **Measured** (36 `scroll` sessions, `--burn 32`, stalls counted from 1 s; the governor detached in the off runs and nothing else changed; run in the order shown):

    | Run | Governor | Stalls >= 1 s | Lag samples >= 100 ms | Echo p50 / p99 | Decision p99 | Deferrals (deferred loops' runs) |
    | --- | --- | --- | --- | --- | --- | --- |
    | 1 | off | 0 | 14 / 235 | 75 / 597 ms | 463 ms | - |
    | 2 | on | 3, 4.3 s in all | 23 / 227 | 53 / 1,467 ms | 3,100 ms | 8 (process inspection 21, ghost sweep 20) |
    | 3 | on | 0 | 20 / 232 | 95 / 460 ms | 278 ms | 7 (21, 21; rollup 3) |
    | 4 | off | 0 | 6 / 239 | 127 / 290 ms | 121 ms | - |

    **The second half of the done-when is not shown.** The governor deferred seven or eight passes in two minutes, every deferred loop kept running, and the work it moved out of lagging windows (three process-inspection passes of 80-200 ms, a few ghost sweeps, one rollup) is far smaller than this host's run-to-run spread under saturation, which here ran from no stall to 4.3 s of them. Both governor runs counted more lag samples than both runs without it; with the spread this wide that is not attributable either way, and no mechanism found would make deferral add work.
    What the runs did show is where saturated stall time comes from: all three stalls in run 2 were GIL convoys held by `ProcessInspector._collect_all` (41-77% of each window's CPU), whose pass cost 198 ms of CPU there against 68-87 ms in the other runs. The governor can only postpone that pass by its allowance; moving it out of the daemon's interpreter is item 20, and that is where the stall time is.
    The governor stays: it is bounded, observable, cheap when idle, and the incident's sustained multi-second lag is the shape it is for, which this harness reproduces only intermittently.
  - **Tests:** `tests/test_background_shedding.py` (the window, the allowance and its absolute cap, resuming within a step of recovery, a non-deferrable loop's cadence, every protected name refused whatever it asks, `defer` inside and past its allowance, a broken reading never sheds, the governor on the health report, accounting that keeps the caller's faults, the protected names against their modules, a Git sweep that waits while a reported change does not, and a process read that keeps a stale sample unless forced).
  - **Where:** `background_tasks.py`, `loop_lag.py`, `server.py`, `git_monitor.py`, `processes.py`, `ghost_windows.py`, `telemetry_service.py`, `operational_telemetry.py`, `usage.py`, `update_check.py`, `provider_accounts.py`, `quota_usage.py`, `observation.py`, `session.py`, `tools/fleet_load_harness.py`, `design/interfaces.md`, `development/PERFORMANCE_RUNBOOK.md`, `technical/backend/packages/{daemon-runtime,runtime-rules}.md`.

- [x] **12. Supervisor: non-blocking per-connection fanout.**
  - **Why:** one slow subscriber stalls every other subscriber and eventually the child (`supervisor.py:881-920`; `pty_host.py:369-430`).
  - **Change:**
    - Each connection gets an outbound queue with a byte cap, drained by its own writer task.
    - `_fanout` never awaits a client.
      When a connection is over its cap for a session, that session's output frames are dropped for that connection and one `gap {sid, position}` frame is queued; the client resubscribes to recover.
    - `_serve_connection` stops draining after every request, and replies go through the same queue.
    - The lossy behaviour applies only to connections that advertise the `output_gap` capability in `hello`; every other connection keeps today's behaviour.
    - Tests cover concurrent subscribers and prove a connection that stops reading never delays another subscriber or the child; `tests/test_pty_supervisor.py` has neither today.
  - **Where:** `supervisor.py`, `packaging/build_desktop.py` (`SUPERVISOR_SOURCES` and the hash gate), `technical/backend/packages/runtime-rules.md`.
  - **Done when:** the tests pass, and a harness run with a deliberately stalled client shows unchanged echo latency on another connection.
  - **Done (2026-10-17).**
    An `output_gap` connection has a `deque` outbox and one writer task that writes it in order and awaits only that connection's `drain()`.
    The cap counts queued *output* only (`OUTPUT_GAP_QUEUE_BYTES`, 8 MiB): replies, `gap` and `pty_exit` frames are never dropped and do not count, so a multi-megabyte `subscribe` reply cannot cause a gap.
    The transport's own buffer was the alternative queue and was rejected for exactly that reason - it cannot tell a reply's bytes from output's.
    A `gap` names the stream position of the first dropped byte, every later chunk of that session is dropped for that connection until it resubscribes, and the supervisor logs one line when a connection starts dropping and one when it has caught up, not one per session.
    A client that keeps sending requests past 64 MiB of unread replies (`REQUEST_PAUSE_QUEUE_BYTES`) has its own request reading paused until it is back under the cap; nothing else waits on it.
    For a direct connection, every subscriber is now written before any is waited on, so a lagging direct client delays a session's next chunk rather than the other subscribers' copy of this one; it is still lossless and still couples, which is what an older daemon needs.
    The `hello` reply lists `capabilities`.
    No new imports, so `SUPERVISOR_SOURCES` is unchanged and the hash gate sees the edit as it should.
  - **Found on the way: supervisor teardown could not reap while any client stayed connected.**
    Since Python 3.12.1 `asyncio.Server.wait_closed()` waits for every connected client (probed on this host's 3.12.11), and `_teardown` awaited it before closing connections, so a second daemon, a stalled reader or a future gateway held `reap_all_and_exit` open indefinitely.
    Connections now close first and the wait is bounded by `SHUTDOWN_DRAIN_SECONDS`.
  - **Tests:** `tests/test_supervisor_fanout.py` against a real loopback listener with queue-backed sessions (`tests/support/supervisor_wire.py`), on every host: a stalled lossy subscriber never back-pressures the child (32 MiB through a 1,024-chunk queue fed with `put_nowait`, so a single wait would raise `QueueFull`); never delays a direct subscriber, which receives every byte; a subscriber that did not ask is still waited on (the queue fills, and closing it releases the fanout); the gap position equals the bytes delivered and nothing follows it until a resubscribe; `pty_exit` reaches a gapped client; a stalled client's keystrokes are still written; teardown reaps with clients connected; and the outbox's order, single gap per session, never-dropped replies, request pause and close.
    Six of the socket tests fail with the capability ignored, and the teardown test fails with the old order.
  - **Measured** (36 sessions, 60 s, a second supervisor client subscribed to every session including the probe, never reading):

    | Stalled client | Echo p50 | Echo p95 | Echo p99 | Echo timeouts (30 s) | Screens classified |
    | --- | --- | --- | --- | --- | --- |
    | none | 4.8 ms | 68.9 ms | 107.6 ms | 0 of 60 | 992 |
    | direct (pre-item-12 contract) | 10.0 ms (n=1) | - | - | 2 | 1 |
    | `output_gap` | 3.7 ms | 16.8 ms | 23.9 ms | 0 of 60 | 997 |

    With a direct stalled client the probe got one echo before every session's fanout parked in `drain()`, and the daemon saw no output for the rest of the window.
    With `output_gap` the client began dropping 20 s after start, once its outbox reached 8 MiB, and echo was indistinguishable from the run with no stalled client.
    Generator throughput did not move in any run (606-609 KB/s), because at 20 KB/s a session takes longer than the window to fill its 1,024-chunk reader queue; agent throttling is item 17's measurement.
  - **Harness:** `--stalled-subscriber {none,direct,gap}`. The harness also resolves `--workdir` now: a relative one made the scratch daemon, whose cwd is its data dir, miss its config and fall back to port 8765.

- [x] **13. Supervisor: subscribe-since, stream identity, terminal scope, notices.**
  - **Why:** the gateway needs delta resubscription, cursors that cannot cross streams and no session secrets, and the daemon needs true geometry when another connection resizes.
  - **Change:**
    - `subscribe` accepts `since` and replies with `bytes_since(since)` and `delta: true` when those bytes are retained, and with the full buffer otherwise (`supervisor.py:493-510`).
    - Every session info and subscribe reply carries `stream`, the session id plus `started_at`.
    - `hello` accepts `scope: "terminal"`: its replies omit `meta`, which carries `hook_secret` and `mcp_token`, and the connection may only `subscribe`, `unsubscribe`, `write`, `resize` and `list`.
    - An unsolicited `resized {sid, cols, rows}` goes to every subscriber after any resize, and `removed {sid}` goes out before `remove` cancels a fanout (`supervisor.py:850-879`).
  - **Done when:** tests cover each message, and an unmodified daemon still drives the new supervisor.
  - **Done (2026-10-17), with three deliberate departures from the text above.**
    - `stream` is the session id plus the reservation's wall-clock milliseconds and a random suffix, minted when the spawn is reserved: `started_at` does not exist until the child does, and a cursor taken while a spawn is in flight must still name its stream.
    - `since` is honoured only when the request also carries the matching `stream`; a bare `since` gets the whole buffer, because a cursor that cannot be checked against its stream cannot be trusted.
      Item 15 sends the pair anyway.
    - `resized` and `removed` go to every subscriber *except* the connection that acted: the resizer knows what it asked for, and an echo of an earlier resize arriving after its next one would set the daemon's mirror back to a stale size.
    - A terminal-scope connection may also `ping`. A refused message is answered with an error and the connection stays, so a gateway probing for a capability does not lose its terminals; an unknown scope is refused and the connection closed, failing closed rather than widening to the daemon's scope; and a connection's scope cannot change on a second `hello`.
    The `hello` reply lists `subscribe_since`, `terminal_scope` and `notices` beside `output_gap`, and echoes `scope`.
  - **Tests:** `tests/test_supervisor_streams.py`: `stream` on hello, `list` and `subscribe`, and a new one for a respawned id; a delta while retained and an empty delta at the end; the whole buffer for an evicted cursor, one from the future, another stream, no stream, a non-number and a negative; a terminal connection never sees `meta` (and the daemon's still does), may drive the terminal, is refused nine daemon-only messages without disconnecting or side effects, and an unknown scope is dropped; the capability list; `resized` with the clamped geometry to another subscriber only; `removed` before the fanout stops and a live session stopped rather than released; and a client sending none of the new fields gets every key it read before.
    Dropping the stream check fails the two fallback cases it guards.
    The unmodified daemon is `tests/test_pty_supervisor.py`: its 20 real-ConPTY tests run today's `SupervisorClient`, which sends none of the new fields, against this supervisor, and all pass.

- [x] **14. Daemon side of the supervisor changes.**
  - **Why:** the daemon must use the new capabilities and keep working against the older supervisor that is running until item 17.
  - **Change:**
    - Advertise `output_gap`; on `gap`, resubscribe with `since`, and `scrollback.seed` on a full reply (`scrollback.py:67-78`).
    - Replace the silent oldest-bytes drop in `_trim_pending` (`supervisor_client.py:400-436`) with the same gap-and-resubscribe path, so the daemon's ring never diverges from the supervisor's positions.
    - Apply `resized` to `RemotePtyHost.cols` and `rows`, and handle `removed`.
    - Reconnect after a lost supervisor connection instead of staying lost until the next daemon start (`supervisor_client.py:726-761`).
    - Degrade on "unknown message type" and on a missing capability, and log the degraded mode once.
  - **Done when:** tests run the daemon against both the new supervisor and a stub of the old one.
  - **Done (2026-10-17).**
    - Each `RemotePtyHost` tracks the supervisor's `stream` and its position at the end of what it took off the wire, plus the position at the end of what reached its bounded queue.
      The spawn reply now names `position` and `stream` (a supervisor addition shipped with items 12-13); an older supervisor names neither and the host's base - the end of its seed - is the same number.
    - A hole from either side - a `gap`, or staging outgrowing its budget - becomes one resubscribe from where the copy stops, with `since` and `stream` when the supervisor announced `subscribe_since`.
      An overflow drops everything staged, keeps what is already queued (the session is consuming it), and asks from the end of the queue.
      A delta reply is offered as output; anything else travels through the same queue as an `OutputReseed`, so it lands after every chunk already queued, and `Session.reseed_from_supervisor` seeds the ring, rebuilds screen mode, bracketed paste, sticky modes and OSC signals from the new bytes, restarts the prompt scanners without replaying them, and resyncs every attached terminal.
    - **Found while writing it:** a reply that positions a host must be applied inside the read loop, not when its awaiting caller resumes.
      The read loop dispatches buffered frames back to back without yielding, so the output right behind a reply was being dispatched while the host still thought it was recovering, and dropped.
      `request(on_reply=...)` runs a hook as the reply is read; spawn, adoption and recovery replies all use it.
      `test_output_that_follows_a_recovery_reply_is_kept` fails when the reply is applied late.
    - Frames for a recovering session are discarded because the reply carries them; an exit in that window is deferred until the reply, or delivered at once if no reply is coming (`removed`, an unknown session, a dead supervisor).
      A recovery whose own reply overflows staging re-enters the same task, and recoveries within ten seconds of each other back off from 0.5 s to 10 s, so a consumer that is still not draining is not handed the same buffer as fast as the link carries it.
    - `resized` updates the host's mirror and is never echoed; `removed` ends the session.
    - A dropped socket to a live supervisor is reattached by the client with backoff from 0.5 s to 10 s, only to the same pid, and every live or recovering session is resubscribed from its position; a stop asked for while unreachable is sent on reattach, followed by the `remove` the release could not send.
    - Against a supervisor without `output_gap` the client logs one warning per supervisor pid and works as before, except that a staging overflow now refills with the whole buffer instead of skipping bytes.
    - `/api/diagnostics/background` gains `supervisor_link`.
  - **Tests:** `tests/test_supervisor_client_recovery.py`. Against the real supervisor: the hello negotiates `output_gap`; a daemon that stopped reading recovers every byte after a gap with one delta; output right behind the recovery reply is kept; a consumer that falls behind is refilled rather than skipped; a hole the supervisor no longer retains is reseeded with its 64 KiB; another connection's resize reaches the mirror; a removal ends the session; a dropped socket is reattached and the output produced meanwhile recovered; a stop asked for while unreachable is sent on reattach.
    Against `FakeSupervisor`, which announces nothing: the compatibility warning once per supervisor, and an overflow refilled by a whole-buffer subscribe with no `since`.
    Unit level: an exit during recovery waits for the recovered bytes, a full refill is queued in order, an exit waiting on a recovery is delivered when none is coming, the session adopts a reseed and resyncs, and the fanout applies a reseed between the chunks around it.
    The two ticker tests in `test_supervisor_client_liveness.py` were rewritten, not weakened: the freeze test now removes the listener too so the session stays unreachable, and the "socket returns" test drives the real reattach instead of flipping the client's flags.

- [x] **15. Stream-bound `since` cursors.**
  - **Why:** a `since` answered from a different stream silently corrupts the terminal, and nothing identifies a stream beyond the cold check.
  - **Change:** `replay_end` carries `stream`, the client sends its `since` together with that `stream`, and a mismatch always gets a full attach.
    In-process and cold sessions get a daemon-minted stream id.
  - **Where:** `routes/pty.py:251-289`, `session.py:2499-2553`, `frontend/src/TerminalPane.tsx:1237-1244,2347-2417`, `design/interfaces.md`.
  - **Done when:** a test proves a cursor from one stream is never answered with a delta from another.
  - **Done (2026-10-17).**
    `Session.stream_id` is the supervisor's `stream` when the host names one (item 13), which survives a daemon restart together with the positions, so a browser's cursor stays good across a reload; otherwise it is an id each `Session` object mints, which covers a respawn, a cold or inactive session rebuilt from disk, an in-process pane, and a session under a supervisor too old to name its stream.
    `attach_and_subscribe(since, stream)` serves a delta only when `stream` equals it, and a `since` with no `stream` is refused as well: an old cached frontend loses deltas until it reloads, which is a wasted parse, where honouring an unbound cursor risks a corrupted terminal.
    Both `replay_end` frames - attach and resync - carry `stream`, read in the same synchronous step as `position`.
    The frontend keeps `serverStream` beside `serverPosition`, clears them together, and `terminalAttachReadyFrame` never sends a stream without a cursor; against a daemon that predates streams it offers `since` alone, as before.
    The cold check in `_delta_covers` stays as a second guard.
  - **Tests:** `tests/test_pty_ws.py::test_a_cursor_from_another_stream_is_never_answered_with_a_delta` (another stream, and no stream, for a position a delta would be served for); `tests/test_session_reliability.py` (a covered position refused for another or no stream, the supervisor's stream winning when named, and two `Session` objects for one id minting different streams; every delta-expecting fixture now offers its stream); `frontend/test/terminalRenderer.test.ts` (the pair travels together and a stream never alone).

- [x] **16. Supervisor revision for updates.**
  - **Why:** installed copies replace the supervisor only on a protocol change, so items 12 and 13 would never reach them.
  - **Change:**
    - A hand-bumped `SUPERVISOR_REVISION` in `supervisor.py`, written to `supervisor.json` and `bundle.json`, with a drift test that fails when the content of `SUPERVISOR_SOURCES` changes without a bump.
      The drift test hashes file content only, because the build hash mixes in the build machine's package versions and never matches across machines.
    - When the revision differs and the protocol matches, the update plan reports that a supervisor update is available and ends every session; swap mode stays the default, and only a consented request installs in replace mode.
    - `swemux doctor` reports a running supervisor older than its bundle.
  - **Where:** `update_install.py:44-61,1618-1638`, `bundle_metadata.py`, `packaging/build_desktop.py:26-95`, `design/features/desktop-shell.md`, `RELEASING.md`.
  - **Done when:** the plan endpoint offers the optional replace against a fixture with a lower revision.
  - **Done (2026-10-17), with two deliberate departures from the text above.**
    - **"Only a consented request" became a separate word, `with_supervisor_update`.**
      `accept_supervisor_update` was documented, tested and shipped as a *permission* ("a release that can be installed around the sessions is, whatever the flag says"), and folding the optional replace into it would have made it an instruction for any caller that sends it by habit - a script passing `--accept-supervisor-update` on every install would start reaping the first time a release carried a newer supervisor.
      The new word implies the consent, because it names the same cost, so a request needs only one of them; it changes nothing for a release with no newer revision; and `accept_supervisor_update` alone never installs an optional supervisor (`test_only_asking_for_it_replaces_a_compatible_supervisor`, four cases).
      The dialog sends it from the unticked "Also update the PTY supervisor, ending every live session" box, the CLI from `--with-supervisor-update`, and `swemux update --plan` prints the second command beneath the default one.
    - **"Differs" became "is higher".**
      An equal or older incoming revision offers nothing: a downgrade on the same protocol gains nothing and costs every session, unlike a protocol downgrade, which strands the fleet and so stays `!=`.
    - The pin is `packaging/supervisor_revisions.json`, one `build_desktop.supervisor_content_digest()` per revision: the closure's bytes with line endings normalised and the `SUPERVISOR_REVISION` line excluded, so a bump alone changes nothing a pin covers and two pins for one content fail.
      Revision 1 pins the closure as items 12-14 left it, and a running supervisor that predates the field reads as revision 0 (`running_supervisor_revision`), so every existing install is offered revision 1 by the first release that carries it.
      `bundle.json`'s `supervisor_revision` is optional - absent reads as 0, which is what an archive built before this looks like - and a present value that is not a non-negative integer makes the metadata malformed rather than guessed.
    - The supervisor states its revision in `supervisor.json` and its `hello` reply; the daemon reports it in `supervisor_link`, and the doctor health payload carries it beside the build's own (`supervisor_revision_shipped`).
      `daemon.supervisor_revision` is a warning with optional severity, never a failure: an older supervisor keeps working.
    - **Known limit, accepted:** a declined supervisor is offered again by every later release until it is taken, and there is no path to take it on the *same* version.
      Adding one would mean admitting a same-version install past `not_newer` for a single case, which is a second install shape to keep correct for an act the next release offers anyway; a checkout already has `redeploy_desktop.py --from-archive <zip> --replace-supervisor`.
  - **Tests:** `tests/test_supervisor_revision.py` (the pin matches the closure, one pin per revision with none skipped or repeated; the digest ignores line endings and the revision line and nothing else, in any closure file; the pins file is outside the closure it pins; the running supervisor writes its revision).
    `tests/test_update_install.py`: the plan offers a newer supervisor and stays swap with no consent word; no offer for an equal revision, a downgrade, or two builds that both predate revisions; a running supervisor without a revision is offered the first one; consent alone swaps and the instruction replaces, with or without consent; asking for a supervisor the release does not improve swaps; the instruction is accepted as consent for a required protocol replacement; and the metadata revision is optional but never guessed.
    `tests/test_doctor.py` (the warning, and silence for an equal, newer or unreported revision), `tests/test_cli.py` (the body carries both words as typed; the plan prints the optional command last), `frontend/test/updateCheck.test.ts` (`planSupervisorOfferLine`: offered with both revisions and the session count, revision 0 for a running supervisor that predates them, and nothing for an older daemon, a declined offer, or a release that reaps anyway).

- [x] **17. Run the supervisor update.**
  - **Why:** items 12-14 do nothing until the running supervisor is replaced, and its bundle has been stale since 2026-08-27.
  - **Change:** none in code.
    The operator schedules the reap and runs the documented sequence from a terminal outside swe-mux: `swemuxd --shutdown`, confirm no `swe-mux` or `swe-mux-supervisor` process remains, `packaging/build_desktop.py --supervisor-only`, relaunch.
  - **Done when:** `supervisor_bundle_current()` is true, the daemon reports the new capabilities, and a harness run with a stalled subscriber leaves the agents unthrottled.
  - **Status (2026-10-17): ready for the operator, and the one item in this plan an agent must not run.**
    The harness half is already answered: the harness starts its own daemon and supervisor from source on scratch ports, and item 12 recorded a stalled `output_gap` subscriber leaving echo unchanged where a stalled direct one stopped it.
    What remains is the live machine, and every step of it ends or depends on ending every session.
    The exact sequence, from a terminal **outside** swe-mux, in the primary checkout, after this branch has landed on `master`:
    1. `uv run swemuxd --shutdown` - ends every session and stops the supervisor.
    2. `Get-Process swe-mux, swe-mux-supervisor -ErrorAction SilentlyContinue` - expect nothing; do not relaunch before step 3.
    3. `uv run python packaging/build_desktop.py` - with no supervisor alive the full build rebuilds the stale supervisor bundle as well as the app and the console client, so the daemon-side changes (items 1-16) ship in the same reap.
       `--supervisor-only` followed later by `packaging/redeploy_desktop.py` is equivalent and splits the wait: an old daemon drives the new supervisor correctly, which is what `tests/test_pty_supervisor.py` runs.
    4. Relaunch the app.
    Then check, all read-only:
    - `uv run python -c "import sys; sys.path.insert(0, 'packaging'); import build_desktop; print(build_desktop.supervisor_bundle_current())"` prints `True`.
    - `curl -s http://127.0.0.1:8765/api/diagnostics/background` shows `supervisor_link` with `output_gap`, `subscribe_since`, `terminal_scope` and `notices` among its capabilities and `supervisor_revision` 1.
    - `swemux doctor` has no `daemon.supervisor_revision` warning.
    Until it runs, items 12-14 are inert on this machine and harmless: the daemon logs the older supervisor once and works as it did, and the doctor warns.
  - **Done (2026-10-17), at the operator's go-ahead, with every other session closed first (0 live).**
    Items 1-17 landed on `master` as a fast-forward (`8f1615c6`), and the sequence ran from a session outside swe-mux.
    - `swemuxd --shutdown` stopped the supervisor, but the frozen daemon and the tray stayed up; `bundle_apply.stop_everything` (quit intent, then the stragglers) ended them, and nothing named `swe-mux` was left.
    - **The first supervisor build failed on a lock no swe-mux process held.**
      `dist\swe-mux-supervisor\_internal\VCRUNTIME140.dll` was mapped by a vaultspaces `postgres.exe` started from a swe-mux session weeks earlier, whose PATH names no supervisor directory - so a program a session starts can inherit the supervisor bundle's DLL search directory and lock the bundle against an in-place rebuild.
      The database was left alone; the half-deleted old bundle was moved into `.trash` (a rename works on a mapped DLL) and the rebuild succeeded.
      It is a supervisor bug, and minor: the staged swap renames rather than deletes and is unaffected, and only `build_desktop.py --supervisor-only` is blocked. Resetting the DLL directory around the PTY spawn would fix it, and that needs its own supervisor revision.
    - The split form ran: `--supervisor-only`, a relaunch of the old app (old daemon on the new supervisor), then `redeploy_desktop.py`, which staged, swapped and came up healthy.
    Checked afterwards: `supervisor_bundle_current()` is `True` from the primary venv; `supervisor_link` reports revision 1 with `notices`, `output_gap`, `subscribe_since` and `terminal_scope`; and `swemux doctor` is 26 ok, 0 warn, 0 fail, with no `daemon.supervisor_revision` check.

- [x] **18. Shared framed IPC module.**
  - **Why:** the worker and the gateway need the supervisor's framing without importing `supervisor.py`, which drags in the PTY stack.
  - **Change:** a stdlib-only `framed_ipc.py` with wire-identical framing (`supervisor.py:99-137`), a token `hello` with a deadline and a capability list, request ids, unsolicited frames, bounded inbound sizes, and a test asserting byte equality with `supervisor.encode_frame`.
    Frames stay small, because the receiving loop decodes every header as JSON.
  - **Done when:** the module and its tests exist and the supervisor is unchanged.
  - **Done (2026-10-17).**
    - The message conventions are the supervisor's as well as its framing: a request carries `t` and `id`, a reply the `id` and `ok`, a notice no `id`.
      `ok` is what tells a reply from a request, so ids are per direction and both ends may ask on one connection, and a `FramedPeer` can hold the gateway's terminal-scope conversation with the supervisor as it is today - a test does exactly that against a real `SupervisorServer`.
    - The handshake checks the token by constant-time compare and the protocol by equality, and a `hello_reply` hook may add fields or refuse (a scope the server does not grant); a hook that fails rather than refuses is an internal error, logged with its traceback.
      Before `hello` a connection gets a 64 KiB header, no payload and 15 s; afterwards a 1 MiB header and a 64 MiB payload.
      The caller's extra `hello` fields go in first, so they cannot override the token or protocol.
    - A peer's requests run as tasks created in arrival order, so whatever a handler does before its first await happens in that order, and a slow one never delays the replies its own side is waiting for; past 64 in flight a request is answered `busy`.
      Notices and reply hooks run inside the read loop, the item 14 lesson: a reply that positions state must be applied before the frames behind it are dispatched.
    - A request's timeout covers the transport taking it as well as the reply, and a late reply is dropped.
      A peer that stops reading is dropped once 16 MiB is unwritten, and `FramedServer.close` closes its peers before awaiting the listener, for the Python 3.12.1 `wait_closed` reason item 12 found in the supervisor.
    - Every connect, accept, refusal (with its reason, never the token), protocol error and close (with the connection's counters) is logged, and `stats()` on a peer and a server is ready for a diagnostics endpoint.
    - The supervisor is unchanged: it keeps its own encoder, the closure digest test still matches revision 1, and the new module is outside the closure.
  - **Tests:** `tests/test_framed_ipc.py`, 40: byte equality with `supervisor.encode_frame` and each side reading the other's frames; every length refused before it is honoured; the handshake's capability agreement and its refusals (token, a non-string token, protocol, a first frame that is not `hello`, a payload, an oversized header, silence past the deadline, a declined scope, a failing check, and extra fields trying to override the token); requests with payloads, deliberate refusals that are not logged as failures, handler crashes that are; an end with no handler; requests and notices in both directions; start order; a reply hook before the next frame; `busy`; a timeout and its late reply; an oversized frame closing the connection; a peer that stops reading; closing failing what is pending and reporting once; a server closing with clients connected; stats; the supervisor conversation; and an import in a fresh `-S` interpreter loading nothing outside the standard library.
    Three mutations - skipping the reply hook, letting extra fields override the token, and a server close that leaves its peers open - each fail exactly one test.

- [x] **19. Worker host.**
  - **Why:** bulk work needs its own GIL without a new lifecycle model for each job.
  - **Change:**
    - Daemon side, `worker_host.py`: spawns the child off the loop (the `bounded_subprocess` spawn lane or a thread, never `CreateProcess` on the loop), passes a token over stdin, connects over `framed_ipc`, supervises the child as a background loop with capped restart backoff, and reports its health on `/api/diagnostics/background`.
    - The child dies with the daemon: a daemon-held kill-on-close Job on Windows, which never contains a supervised session pid, a process group on POSIX, and exit on control-connection EOF everywhere.
    - Child entry `swe_mux.worker`, frozen as `swe-mux.exe --worker-child`.
      Never `-m swe_mux.*`, which the redeploy spares and the inspector treats as a session helper (`bundle_apply.py:109,514`; `processes.py:62`).
    - Its own rotating `worker.log`, the switch interval and timer resolution set, normal priority.
    - One worker process hosts every service; a crash restarts all of them, and each service rebuilds its state from the daemon on reconnect.
  - **Where:** `worker_host.py`, `worker.py`, `desktop.py:1141-1225`, `technical/backend/packages.md`, a new `design/features/worker-process.md`.
  - **Done when:** a no-op service round-trips, a killed worker is restarted, and daemon quit leaves no worker behind (`tests/test_live_daemon.py:228-323` extended).
  - **Done (2026-10-17), with three departures from the text above.** Design: `design/features/worker-process.md`.
    - **From source the worker is `python -m swe_mux.worker`, not `-m swe_mux --worker-child`.**
      The "never `-m swe_mux.*`" rule is about the frozen app's own image, where that shape is a session helper to the redeploy and the process inspector; both checks are frozen-only, the source supervisor already runs as `-m swe_mux.supervisor`, and `-m swe_mux` would have imported the whole daemon (aiohttp, the server, every store) into the worker.
      Frozen it is `swe-mux.exe --worker-child`, dispatched by `desktop.main`, and a test holds the frozen command to "never a session helper" with the inspector's own predicate.
    - **The hello may come from the spawned pid's direct child.**
      A Windows virtual environment's `python.exe` is a launcher that starts the real interpreter as a child, so the worker saying hello is one level below the pid `Popen` returned; the first run of the tests refused every real worker for exactly that.
      The host accepts that pid or its direct child and nothing else, reports the worker's own pid, and keeps the launcher's as `launcher_pid`.
    - **The supervision loop polls the child's exit once a second rather than parking a thread in `wait()`**, which would hold one executor thread for the worker's whole life; asyncio has no awaitable exit for a child it did not spawn, and spawning on the loop is what the item forbids.
    - A switch, `worker_process_enabled` (default on, at the next start), with a Settings control under Processes, and every future caller keeps a daemon fallback: `call` raises `WorkerUnavailable` at once rather than waiting out a restart.
    - Dying with the daemon is three mechanisms deep: the worker's own kill-on-close reaper (never one holding a supervised session pid), the control connection's EOF, and the worker's five-second check that its daemon's pid exists.
      Its environment withholds `SWE_MUX_DESKTOP_CONTROL_TOKEN`, it runs at normal priority below the daemon's, and it logs to its own `worker.log` (10 MB × 3) with `worker-console.log` for what the interpreter printed before logging started.
  - **Measured** (source mode on this host, five fresh workers): spawn to ready 188-225 ms (median 197 ms), 23.7 MiB resident, and a `noop.echo` round trip 184-214 us p50 and 285-408 us p99 over 200 calls each.
    The daemon pays nothing on its loop for the spawn, which runs in a thread inside the supervised loop.
  - **Live (2026-10-17):** landed and redeployed with 8 sessions running, all kept; the frozen worker (`swe-mux.exe --worker-child`, no launcher) reported `running` and ready 24 s after the swap, which is the frozen half of the entry point that no test exercises.
  - **Tests:** `tests/test_worker_host.py`, 15, every one but the unit cases against a real worker process: the round trip and the built-in stats; a killed worker restarted, with the ready hook having rebuilt its state from the daemon and the new worker remembering nothing else; a stop that leaves neither the worker nor its launcher; a worker that leaves with exit code 0 when its connection closes; notices routed to their service; a stranger holding the token but not the pid refused, and a non-worker role refused; a worker that exits before its hello restarted with its exit code; one that never says hello killed at the deadline with no hung child left; disabled means never spawned; health serializable in every state; the worker held in the host's own reaper and the reaper closed on stop; a stand-in daemon killed outright taking its worker with it; the frozen and source commands; the bounded token read; and the daemon-pid check.
    `tests/test_live_daemon.py`: a real `muxd` reports a ready worker among its descendants and a `quit` reaps it, and a detach ends the worker while the supervisor survives.

- [x] **20. Process inspection into the worker.**
  - **Why:** psutil holds the GIL on Windows, and the inspector walks about 1,200 processes per pass plus request-triggered extra passes (`processes.py:501-573,817-829,1300-1431,1558-1684`).
  - **Change:**
    - The worker runs the sampling pass, priority enforcement, the Codex process proofs (`codex_process_identity.py`), `win_process_files` queries and the ghost-window sweep.
    - The daemon pushes the inputs: session roots (pid and creation time), states and ids, the supervisor's job-pid map, infrastructure pids, and the priority target.
    - The worker returns snapshot deltas (owned rows, the listener index, infrastructure ports, system CPU), listener events and diagnostics.
    - `act()` and preview `register(force)` become RPCs with a timeout; request handlers read the latest snapshot instead of triggering a pass.
    - `process_evidence` writes to `mux.db` stay in the daemon, fed by the deltas, and `_fast_clear_background_annotations` stays in the daemon, driven by snapshots.
  - **Where:** `processes.py`, `ghost_windows.py`, `codex_process_identity.py`, `win_process_files.py`, `design/features/processes-and-previews.md`.
  - **Done when:** the Processes tab, previews, fleet intelligence and process evidence match the in-process behaviour in tests, and the harness shows the inspector's GIL share gone from the daemon.
  - **Done (2026-10-17).** Design: `design/features/processes-and-previews.md` § Where the pass runs, and `design/features/worker-process.md` § Moving a service into it.
    - **The worker runs the daemon's own sampler, not a port of it.**
      `process_service.py` gives a `ProcessInspector` the eight session-record fields a pass reads (`SESSION_FACT_FIELDS`) through a stand-in session manager, plus the job-pid map, the supervisor pid, the daemon's pid (`daemon_pid_override`, so the worker samples the daemon as the daemon and counts itself among swe-mux's own processes), the orphan grace and the priority target, and calls the same `_collect_all`.
      The daemon's `owned` becomes a mirror, so every reader - the Processes surfaces, previews, fleet intelligence, `process_evidence` persistence, listener events and the background-annotation clear - is untouched and still in-process.
    - **A delta, mostly patches.** A process the daemon has never held crosses whole, one it holds crosses as the fields that changed, and a pruned one as its key; a live process's command line, most of its bytes, never changes, so a pass after the first carries no whole records at all on a steady fleet.
      The daemon decodes in a thread and applies on the loop.
    - **Reloaded, never trusted.** `WorkerHost.generation` counts connected workers; the daemon loads its whole mirror into a worker it has not loaded, and an unloaded worker refuses to sample rather than answer with a delta that forgets every process.
      A patch for a process the mirror lacks marks the copies diverged and reloads on the next pass; any failure samples in the daemon for that pass, exactly as before the worker existed.
    - **Actions, port-conflict probes and unique memory go to the worker** because they need its handles and process table. `_deliver` is shared by both ends; a session root's interrupt is the one thing the worker only verifies, because the daemon owns the PTY that delivers it. A refusal there is the same `ValueError` and 400.
    - **Priority enforcement moved with the pass**, and `process_priority` reports the worker's counts while passes run there.
    - **The ghost-window sweep moved too**, as a `ghost_windows` worker service the daemon's loop calls once a tick with an in-process fallback, because `EnumWindows` calls back into Python, taking the GIL, once per top-level window.
  - **Three departures from the text above.**
    - **Listener events are derived in the daemon from the mirror**, not returned by the worker: the mirror holds the same rows the worker's pass produced, and deriving there keeps the event bus, its ordering and its consumers where they were.
    - **Reads read the mirror only while passes run in the worker.** In-process the old rule stands (a read samples when the sample is older than the cadence and not shed), because every freshness guarantee the in-process tests hold the inspector to is written against it.
      With the worker a non-forced read never waits on a pass, which is the plan's text, and it went in after the first saturated run showed why: a read that queued behind a pass in another process paid that whole pass, 580 ms for one `/api/processes` read.
      An action, and preview `register(force)`, still force a pass, with the pass's own 60 s timeout.
    - **The Codex process proofs (`codex_process_identity.py`) and `win_process_files` stay in the daemon.** They are event-gated - once per Codex hook start or per session event that could move a conversation (`SessionManager._process_proof_due`) - and they sit on paths where a round trip is latency a person waits on.
      Measured on the live daemon at 20 minutes of uptime with 8 sessions: neither appears among the 15 costliest executor labels (each below 1.5 s of CPU), against `ProcessInspector._collect_all` at 37.0 s over 235 passes (157 ms each); item 4's GIL ranking did not see them at all.
  - **Measured** (the item 4 configuration: 36 `scroll` sessions at 20 KB/s, 3 hooks, 1 decision and 1.5 OTLP posts a second, an `/events` browser, no burners, `py-spy --gil --threads` on the daemon pid for 45 s; one run each, the same build, `worker_process_enabled` off and then on):

    | | Worker off | Worker on |
    | --- | --- | --- |
    | Process inspection share of the daemon's GIL samples | 6.5% (70 of 1,080) | 0.4% (4 of 1,065, all serializing snapshots for readers) |
    | `ProcessInspector._collect_all` CPU in the daemon | 3.02 s over 28 passes | none |
    | Keystroke echo p50 / p95 / p99 / max | 6.3 / 102 / 602 / 1,083 ms | 5.0 / 18.5 / 27.0 / 28.6 ms |
    | `GET /api/sessions` p99 | 582 ms | 38.5 ms |
    | `GET /api/health` p99 | 537 ms | 25.7 ms |
    | Hook / decision / OTLP p99 | 153 / 48.9 / 103 ms | 22.3 / 18.7 / 27.3 ms |
    | Lag samples >= 100 ms | 3 / 239 | 0 / 241 |

    The GIL share and the executor CPU are the measurement this item was for, and they are unambiguous: the pass and its socket-table and parent-map reads (`_refresh_tree` alone was 2.8% of samples) are gone from the daemon, and decoding and applying the delta does not appear in a single sample.
    The latency tails are one run each and the worker-off run's are one episode - every p99 near 540-600 ms, against item 6's in-process 40 ms echo p99 - so they show what an inspector-held convoy costs when it lands, not a steady-state ratio.
    `process-inspector` busy time rose from 3.6 s to 5.2 s in the window: busy is wall time per iteration, and an iteration now awaits the worker's pass instead of running it on a daemon thread.
    The ghost sweep was already small - 0.11 s of daemon CPU over 27 sweeps in the worker-off run, and no GIL sample in either profile - so it moved for the per-window callback convoy rather than for a measured share.
  - **Saturated checkpoint** (the item 11 configuration: 36 `scroll` sessions, `--burn 32`, stalls counted from 1 s, `py-spy --gil` on the daemon for 45 s; four runs in the order shown, the third and fourth with the two fixes below):

    | Run | Worker | Stalls >= 1 s | Lag samples >= 100 ms | Echo p50 / p95 / p99 | `GET /api/sessions` p99 | Hook p99 | Process inspection share of daemon GIL samples | `process-inspector` busy per pass |
    | --- | --- | --- | --- | --- | --- | --- | --- | --- |
    | 1 | off | 0 | 0 / 242 | 15.6 / 121 / 276 ms | 738 ms | 192 ms | 6.2% | 0.13 s |
    | 2 | on | 0 | 0 / 242 | 13.4 / 470 / 1,660 ms | 712 ms | 22.6 ms | 1.5% | 0.61 s |
    | 3 | on, fixed | 0 | 0 / 241 | 10.9 / 45.5 / 64.4 ms | 46.6 ms | 17.9 ms | 0.7% | 0.18 s |
    | 4 | off | 0 | 0 / 241 | 12.5 / 84.5 / 166 ms | 355 ms | 38.4 ms | 6.1% | 0.11 s |

    None of the four reproduced item 11's convoy stalls, so this pair cannot show them gone; what it does show is the inspector's share of the daemon's GIL falling from about 6% to under 1% under saturation too.
    Run 2 found two faults in the first cut, and both are fixed in run 3:
    - **Two host walks in the worker convoyed on its one GIL.** The process pass and the ghost sweep ran on two worker threads, and the sweep takes the GIL once per top-level window; under load a sweep averaged 290 ms (6.7 s of `ghost-window-sweep` in the window, against about 4 ms idle) and a pass 0.6 s. They now share `Worker.host_scan_lock`: 0.21 s of sweeps in run 3, 8.8 ms each.
    - **A read waited on a remote pass**, one `/api/processes` read taking 580 ms behind it. In worker mode a non-forced read now answers from the mirror (the departure above).
    Run 2's echo tail sat outside the daemon's loop - no lag sample reached 100 ms in any run - and fell back with the fixes, so it is recorded as the worker's slow passes competing with the probe shell and its console host rather than as the loop; run 3's tail is the best of the four, and one run is not a ratio.
  - **Found by the checkpoint: the decided-approval keystroke watcher.** With the inspector gone, the worker-on profile's largest single line was screen classification reached from `_deliver_decided_approval`, 13.0% of the daemon's GIL samples.
    Every auto-approved `PermissionRequest` on a harness with an accept key (Claude) arms a watcher that classifies the session's screen every 0.5 s for up to 30 s, waiting for the dialog to type into; the harness's panes draw no dialog, so each of its one-a-second decisions cost 60 classifications of a scrolling screen.
    A real Claude session draws it 6.02 s after mux's answer (`design/features/approvals.md`), so production paid about thirteen looks a decision rather than sixty - but a CLI that honours the answer, or a dialog the operator answers first, paid the full sixty.
    - The watcher now retires on evidence: the decided tool's `PostToolUse` (it ran, so its approval was answered), or anything that cancels the stabilization timer for that same tool.
    - It looks every 0.5 s for the first 10 s, covering the measured 6.02 s with room to spare, and then backs off, doubling to 4 s, for the rest of its window: about 27 looks instead of 60 when nothing ever comes.
    - The load harness's decision traffic now does what a real approved tool does: the granted tool's `PostToolUse` follows 7 s later (dialog, keystroke, a quick read). Runs before this change carry the old cost, which is worth knowing before comparing against them.
    Measured with the retirement and a 0.5 s follow-up, the watcher's share fell from 13.0% to 0.8% of GIL samples; with the realistic 7 s follow-up and the 10 s fast window it is 1.1%, beside 0.4% for everything process inspection still does in the daemon.
    What remains of screen classification (12.0-14.4% across those two runs) is fleet rows and the readiness watcher (4.1-6.6% each) and the state watchdog's SSH check (1.8%), each re-classifying a screen that scrolls every second, which is item 7's worst case rather than anything item 20 moved.
  - **What the checkpoint decides.** Item 21 goes ahead: with the inspector gone, canonical telemetry is the daemon's costliest loop in every run (19-48 s busy in two minutes) and 5.9% of its GIL samples at the item 4 load, the largest consumer left that is not the terminal itself. Item 22 still waits on its re-measure, and the worker-on profile is the input it needs: transcript observation's frames sit above 21% of GIL samples, but most of that is screen classification reached through `session_screen_explanation`, which is item 7's territory rather than transcript I/O.
  - **Live (2026-10-17):** landed (`924a66ce`) and redeployed with 8 sessions running, all kept.
    The frozen worker hosts `ghost_windows`, `noop` and `processes`; its first passes sampled the live fleet's 148 processes as 35.6 KB of patches with no whole records, in a 35 ms round trip, after one load; every ghost sweep ran in the worker; and `ProcessInspector._collect_all` is absent from the daemon's executor accounting, where it had been 37.0 s of CPU over 235 passes on the previous build.
  - **Tests:** `tests/test_process_sampling_worker.py`, 9: a mirror kept only from deltas equals the sampler it mirrors (new, patched, exited, pruned processes and the machine-wide readings), patches carry only changed fields, a quiet pass sends nothing, diagnostics cross only when changed, a patch the mirror cannot apply reports divergence, session facts are exactly the fields the pass reads, an unloaded worker refuses to sample, a failing worker pass samples in the daemon; and against a real worker and a real process tree under a stand-in supervisor, a pass in the worker attributes exactly what a pass in the daemon does (and a second pass is patches only, and unique memory is read there), actions run in the worker with a root interrupted through its PTY and a refusal raised as before, and a killed worker is reloaded before it samples.
    `tests/test_ghost_windows.py`: a sweep in the worker is counted in the daemon and never repeated there, a failing or absent worker sweeps in the daemon, and the worker service sweeps on its own sweeper.
    `tests/test_live_daemon.py`: a daemon a default install builds hands its passes to the worker, which sampled that daemon as the daemon.
    `tests/test_worker_host.py`: the generation counts each connected worker.
  - **Where:** `process_service.py`, `process_fingerprint.py`, `processes.py`, `ghost_windows.py`, `worker.py`, `worker_host.py`, `server.py`, `routes/diagnostics.py`, `operational_telemetry.py`, `tools/fleet_load_harness.py` (now reports `process_sampling` and `worker`), `design/features/{processes-and-previews,worker-process,ghost-windows}.md`, `design/interfaces.md`, `technical/backend/packages/{processes-and-devices,daemon-runtime}.md`.

- [x] **21. Canonical telemetry into the worker.**
  - **Why:** the reducer, reconcile, rollups and seals run in daemon threads, OTLP bodies of up to 4 MiB are parsed on the loop, and `_dimensions` runs on the loop for every event (`telemetry_service.py:151-368`; `routes/agent_ingress.py:433-476`).
  - **Change:**
    - Move all three ledgers together (catalog, segments and the `LedgerShared` write lock) with their maintenance and queries.
      Splitting them across processes would reintroduce the lock-ordering deadlock `LedgerShared` exists to prevent (`telemetry_ledger.py:106-172`).
    - The daemon forwards the bus events the ledger consumes plus a session-dimension table whenever it changes, and forwards OTLP bodies as raw binary payloads without parsing them on the loop.
    - A bounded daemon-side buffer covers worker restarts, and `provider_dropped` keeps counting losses.
    - Usage queries and the `canonical_telemetry` diagnostics block are answered over RPC.
  - **Where:** `telemetry_service.py`, `telemetry_ledger.py`, `routes/agent_ingress.py`, `design/features/operational-telemetry.md`, `development/TELEMETRY_HARDENING.md`.
  - **Done when:** replaying a recorded event stream yields identical ledger rows in and out of process (`tools/telemetry_benchmark.py`, `tools/telemetry_ingest_latency.py`).
  - **Done (2026-10-17).** Design: `design/features/worker-process.md` § A service whose state is a store, and `design/features/operational-telemetry.md`.
    - **The worker runs the daemon's own ledgers.** `TelemetryLedgers` - the three ledgers over one root, each on its own thread under one `LedgerShared` - runs in the daemon with the worker off and in the worker's `telemetry` service (`telemetry_worker.py`) with it on.
      `CanonicalTelemetryService` keeps the bus subscription, the batching, the backfill, rollup and reconcile loops with their pacing and shedding, and health, and calls the ledgers by method name through `WorkerLedgers`; a method outside the two whitelists (`MAINTENANCE_METHODS`, `QUERY_METHODS`) is refused in the worker.
    - **One writer per root, so no daemon fallback.** In worker mode the daemon never opens a ledger file, even for one call while the worker restarts: `LedgerShared` orders locks inside a process and cannot order them across two.
      Ingestion waits in a buffer of at most 50,000 observations, offered again every second ahead of anything newer, oldest dropped first past the bound; a query waits up to 10 s for a starting worker and then raises `errors.TelemetryUnavailable`, which the middleware answers `503 telemetry_unavailable`.
    - **Replay is what makes waiting enough.** The evidence id digests session, type, source, sequence and payload under `INSERT OR IGNORE`, and the wire encodes a payload with the digest's own `default=str`, so a resent batch records nothing twice.
    - **Opened per worker generation, as item 20's mirror is loaded:** an unopened worker refuses every call, and the daemon reopens it rather than counting the refusal as a fault.
    - **OTLP crosses as the raw body** with the session's dimensions and is parsed, reduced and recorded in the worker in one call, signatures included; with no worker the route parses it as before and the events wait in the buffer.
    - **Quota attribution waits for the ledger.** It is the one internal consumer of query answers; catch-up and per-sample attribution both retry on `TelemetryUnavailable` instead of skipping the interval.
  - **Three departures from the text above.**
    - **Each batch carries its own dimensions rather than the worker keeping a session table.** The daemon resolves them per event exactly as before (`_dimensions`, 2.5 us an event measured) and the batch crosses self-describing, so a restarted worker needs no table pushed before it can record, and nothing about session lifecycle has to be mirrored into it.
      `_dimensions` therefore stays on the loop, which the measurement below shows is not where the cost was.
    - **Buffer losses are counted on their own, in `buffer_dropped`**, beside `buffered_events` and `worker_unavailable`; `provider_dropped` keeps meaning what it meant, the bounded ingress queue refusing an event.
    - **The equivalence is a test rather than a benchmark run**, because it has to hold on every push: `tests/test_telemetry_worker.py` replays one stream into ledgers in process and through a real worker and compares every row of every ledger file and fifteen query answers.
      Every column matched except five a ledger stamps from its own clock (`telemetry_evidence.received_at`, `ledger_segments.last_written_at`, `parser_signatures.first_seen_at`/`last_seen_at`, `dirtied_at` on both rollup dirty tables), found by running the comparison with none excluded, and the test holds each to a reading taken during its own run.
      `tools/telemetry_ingest_latency.py` gained `--worker` for the cost half.
  - **Two faults found on the way, both fixed here.**
    - **The teardown stopped the worker before canonical telemetry**, which stops after the sessions so that their shutdown evidence is recorded; in worker mode that final drain would have been left in the daemon's buffer as it exited.
      The worker now stops last, and `tests/test_shutdown_teardown.py` holds it after every service that calls it.
    - **Quota catch-up ran each chunk inside `background.iteration`, which absorbs what it raises**, so a worker restart mid-catch-up would have skipped those chunks and reported catch-up finished; the refusal is caught inside the block and re-raised after it, and the test for it fails against the old code.
  - **Measured, ingestion alone** (`tools/telemetry_ingest_latency.py`, 30,000 observations at 1,000 a second, three alternating rounds; `development/TELEMETRY_HARDENING.md` § The ledgers in the worker process): ingestion cost the daemon a median 10.4 s of CPU in process and 1.9 s with the ledgers in the worker, between a fifth and two fifths in every round, with the worker spending 7.2 s; at 2,000 a second the bus dropped 15,123 of 50,000 observations in process and 2,327 with the worker.
  - **Measured, the item 4 configuration** (36 `scroll` sessions at 20 KB/s, 3 hooks, 1 decision and 1.5 OTLP posts a second, no burners, `py-spy --gil` on the daemon for 45 s), against the last item 20 run (worker on, the same configuration including the 7 s decision follow-up):

    | | Item 20 build | Item 21 build |
    | --- | --- | --- |
    | Canonical telemetry share of the daemon's GIL samples | 5.9% (49 of 831) | 0.9% (7 of 792: batch encoding, the consume loop, the call) |
    | `CanonicalTelemetryService._record_batch` CPU in the daemon | 3.86 s over 1,207 batches | none |
    | `canonical-telemetry` loop busy in the window | 16.3 s | 8.7 s |
    | Keystroke echo p50 / p95 / p99 | 7.0 / 48.9 / 59.1 ms | 4.5 / 20.3 / 71.7 ms |
    | `GET /api/sessions` p99 | 67.5 ms | 68.8 ms |
    | Hook / decision p99 | 21.5 / 30.3 ms | 20.1 / 18.6 ms |
    | OTLP round trip p50 / p99 | 8.8 / 37.2 ms | 12.1 / 114 ms |
    | Lag samples >= 100 ms | 0 / 241 | 0 / 241 |

    The share and the executor CPU are the measurement this item was for: the reducer and its writes are gone from the daemon, and what is left is encoding a batch, the consume loop and the call (0.9%), plus the OTLP route reading a body and handing it over (0.3%, where parsing it was 0.1% before - the body is read either way).
    The echo and fleet-read tails are one run each and move both ways, so they are recorded rather than claimed.
    **The OTLP round trip grew on purpose.** The route used to answer once the parsed events were queued in the daemon; it now answers once the worker has recorded them, behind whatever live ingestion is ahead on the worker's single ingest thread.
    The daemon's loop is not in that wait - hook and decision round trips are unchanged and no lag sample reached 100 ms - and an exporter sends in the background, so nothing waits on that reply but the exporter's own next batch.
    In exchange a body is acknowledged only once it is recorded, so a worker that dies mid-call costs the exporter a retry, whose events carry the same evidence ids, rather than costing the body.
  - **Tests:** `tests/test_telemetry_worker.py`, 9: the in- and out-of-process replay above, including the scrubbed real Claude 2.1.259 and Codex 0.153.0 OTLP captures and a known, a second known and an unknown session; the wire keeping every evidence-id input (a `Path` and a tuple in the payload, a float timestamp, a transient event with no session) and refusing a batch whose dimensions do not match its events; a call's path argument restored and quota rows returned as tuples; a batch recorded twice recorded once, a malformed or foreign OTLP body the same `ValueError`, a method outside the whitelist refused; a worker killed and restarted, with observations buffered while it was away, a query waiting for its successor, the successor opened before it recorded and every observation delivered; a buffer past its bound keeping the newest observations, and keeping a single batch larger than the bound; a query with no worker raising `TelemetryUnavailable` and the middleware answering it 503; worker mode refusing to start with no worker attached.
    `tests/test_quota_usage.py`: catch-up waits out a ledger that refuses part-way through, and a sample refused once is attributed when the ledger returns.
    `tests/test_shutdown_teardown.py`: the worker stops after every service that calls it.
    `tests/test_live_daemon.py`: a default daemon records an event from its own bus in the worker and serves it back over `/api/telemetry/v2/runs/{id}` with nothing buffered.
    `tests/test_telemetry_otlp.py`: the route forwards the unparsed body when the ledgers are in the worker, and parses it itself when no worker takes it.
  - **Where:** `telemetry_service.py`, `telemetry_worker.py`, `worker.py`, `errors.py`, `server.py` (the attach, the 503, the teardown order), `routes/agent_ingress.py`, `quota_usage.py`, `tools/telemetry_ingest_latency.py`, `design/features/{worker-process,operational-telemetry}.md`, `design/interfaces.md`, `development/TELEMETRY_HARDENING.md`, `technical/backend/packages/processes-and-devices.md`, `technical/backend/sqlite.md`.

- [x] **22. Transcript I/O into the worker, if item 4 still ranks it after item 6.**
  - **Why:** tailing every transcript reads and JSON-decodes in daemon threads and dispatches every record on the loop (`observation.py:946-1093,1206-1306`).
  - **Change:** the worker stats, reads and decodes, strips each record to the fields `_claude` and `_codex` consume, and streams compact records; the session state machine stays in the daemon.
    The hourly `reconcile_transcripts` moves with it (`operational_telemetry.py:749-815`).
  - **Done when:** the status-detection golden tests pass unchanged and the harness shows the gain; otherwise this item is removed with its measurement recorded.
  - **Decided (2026-10-17): the tailer half is removed, and the transcript I/O that mattered was a bug in the startup reconcile, fixed at the root; its discovery walk moved to the worker.**
  - **The tailer half, measured and removed.** On the item 21 build at the item 4 load, stat, read and decode - everything this item would have moved - were 1.5% of the daemon's GIL samples (12 of 792; 1.7% on the item 20 build).
    Record dispatch and the session state machine, which stay in the daemon whatever happens, were 9.8%, and screen classification reached through them 7.8% (item 7's territory).
    Moving 1.5% would have put a cross-process hop on the status-detection path for every record, so it is not done.
    The hourly `reconcile_transcripts` is already incremental past a cursor (`telemetry_cursor.py`): 2.4 s of CPU over 1,996 calls in a live daemon's first five minutes.
  - **What the live daemon showed instead.** Its executor accounting 41 minutes after a redeploy, with 11 sessions: `summarize_transcript` 49.9 s over 1,319 calls and `scan_external_transcripts` 5.7 s, both from the startup history reconcile; after the next redeploy, 81.0 s over 1,310 calls and 9.3 s in the first 4.6 minutes.
    The same ~1,300 conversations were being read whole at every start, which is not a placement problem.
    Three faults, each fixed at its source (`design/features/history.md`):
    - **Owned conversations were summarized and the summary discarded.** `upsert_external` keeps nothing a scan measured for a conversation a mux session owns (only a path repair), and `external_watermarks` reads only `external=1` rows, so an owned conversation could never be skipped: 2,340 owned rows had a transcript and none a watermark. The reconcile now leaves one alone unless its path needs repair or its message index is stale.
    - **"Message index current" compared the index's watermark with the file's stat.** The index stores `conversation_watermark`, which for Codex is `(0, length across its forked segments)`, so no Codex conversation was ever current and all 481 external ones were re-summarized at every start. `reconcile.index_currency` now asks `index_transcript`'s own question, in one pass on a thread - the same bug class the history listing's time summaries had fixed on 2026-10-12.
    - **Every store-backed conversation's watermark was keyed `""`**, because it records an empty path; it is now keyed `backend:native_id`, the key the reconcile looks it up by.
    Measured read-only against the development host's corpus of 2,115 discovered conversations: a start summarized 1,481 (the 867 the owned rule alone skips cost 70.4 s of CPU and 16.9 GB read); it now summarizes 133, costing 0.2 s, plus a 1.4 s currency pass.
    The remaining 133 are omp and pi sessions left in `~/.omp` and `~/.pi` by old test runs and opencode store rows, recorded here rather than chased.
  - **The discovery walk moved to the worker** (`transcript_discovery.py`, the `transcripts` service), behind the startup reconcile, "scan now", project backfills and onboarding.
    It is the same `scan_external_transcripts` either way; progress crosses as notices, a cancelled caller stops the worker walking, and a worker that is away or fails walks here. The startup reconcile waits up to 30 s for the worker before walking here.
    The walk itself also got cheaper: a Codex rollout's header is decoded only up to its first `session_meta` (`_header_events`) rather than twenty lines, taking it from 9.8 s to 6.0 s of CPU on the development host's trees.
  - **Found on the way: a 3.4-3.9 s loop stall at every start.** The one explained stall at each of two consecutive starts was the loop importing `faster_whisper` and `huggingface_hub` inside `/api/voice` status (`WhisperModelStore._resolve_local`), convoyed with the discovery walk. Every async reader now answers those probes in a thread (`VoiceService.probe_whisper_models`), committed separately (`64e37ffc`).
  - **Live, after the redeploy that shipped it (`fd96b80f`, 10 sessions).** The startup reconcile logged `discovered=1940 summarized=0 unchanged=1112 owned_unchanged=808 imported=19 skipped=1 elapsed_s=17.35`; the two starts before it each ran about three minutes and each logged a `gil_convoy` stall naming `scan_external_transcripts`, and this one logged no stall.
    The walk ran in the worker (`transcript_discovery`: one remote scan, 8.7 s, no fallback), and the daemon's executor accounting for its first three minutes holds no `summarize_transcript` and no `scan_external_transcripts` - against 81.0 s and 9.3 s in the first 4.6 minutes of the start before.
    What the reconcile still costs the daemon is the currency pass (1.6 s of CPU) and indexing the 19 conversations it imported (7.1 s).
    The one skip is a forked Codex rollout whose parent transcript no longer exists; it was skipped at every earlier start too.
  - **Found on the way: the code graph is the live daemon's largest executor cost.** Over the same 41 minutes: `code_graph._read_and_parse` 46.1 s over 6,079 files and `CodeGraphStore` SQL 33.2 s (`reverse_dependents`, `replace_file`, `callers_of_symbol`), more than the reconcile and canonical telemetry together. The harness never exercises it, because its sessions edit no code. Recorded as item 22a.
  - **Tests:** `tests/test_perf_caches.py`: an owned conversation is never summarized, its message index is still built and refreshed when it grows, and an unchanged one costs nothing; an owned conversation whose file moved is still repaired; an unchanged Codex conversation is skipped and a grown one is not; a store-backed external watermark has its own key. Each was run against the old behaviour and failed.
    `tests/test_transcript_discovery.py`, 8: a scan answered by a real worker returns exactly what a scan here returns, scoped and unscoped, over every harness's layout; the wire keeps every field; no worker, a worker that is not ready and a worker that fails all walk here; the service walks until cancelled and reports progress; a caller that cancels stops the worker and sees its progress; a cancelled task still tells the worker to stop.
    `tests/test_history_scan.py`: the scan manager walks through the scan it was given.
  - **Where:** `reconcile.py`, `history.py` (`owned_transcript_paths`, `repair_owned_transcript`, `transcript_path_key`, `external_watermarks`), `history_backfill.py`, `history_scan.py`, `transcript_discovery.py`, `worker.py`, `server.py`, `routes/onboarding.py`, `routes/diagnostics.py`, `app_keys.py`, `design/features/{history,worker-process}.md`, `design/interfaces.md`, `technical/backend/packages/history-and-observation.md`.

- [x] **22a. The code graph off the daemon's GIL, if it still ranks once its re-parse rate is understood.**
  - **Why:** found by item 22's re-measure: on the live daemon the code graph was the largest executor cost - `_read_and_parse` 46.1 s over 6,079 files and `CodeGraphStore` SQL 33.2 s in 41 minutes - and no harness configuration exercises it.
  - **First:** establish whether 6,079 parses in 41 minutes are real edits or the reconcile's failure shape - unchanged files parsed again - before deciding where parsing runs; a re-parse bug is fixed at its source, as item 22's was.
  - **Then, if what remains still ranks:** parse in the worker and return the parsed facts, with `CodeGraphStore` writes staying where the store's single writer is.
  - **Done when:** the live executor ranking shows the gain, or the item is removed with its measurement recorded.
  - **Decided (2026-10-17): the parses were the failure shape, fixed at the source; nothing moved to the worker.**
  - **The cause.** `deterministic_consumers._code_graph` ran `index_project` at the first turn of every process: clear the Project's graph and re-parse its whole tree, although the graph and the commit it reflects persist in `mux.db`.
    The live log shows it at both of the evening's starts, `code_graph_indexed project=42c9c46b... files=6000` on the same head each time; that Project is `D:\PROJECTS\vaultspaces`, at the 6,000-file bound.
    So 6,000 of the 6,079 parses were one Project's unchanged tree, and the rest were the write stream.
  - **The fix: a stat-based sync** (`code_graph.sync_project`, schema version 3).
    Each file's row now carries the stat stamp it was parsed at; the first turn in a process walks the tree as the seed does, reads and hashes only the files whose stamp moved, parses only those whose bytes moved, and drops the files the tree no longer holds.
    A stamp taken within three seconds of its file's modification time is recorded as unknown, git's "racily clean" rule, so a same-tick write is verified by content rather than missed.
    The trunk refresh's fallback for an unusable or oversized delta is the same sync rather than a re-seed.
  - **What the re-seed was quietly doing, kept.** A file's edges are resolved against its imports' symbols and against the filesystem when it is parsed, so an importer nobody re-parses goes stale when a module it imports changes shape; the per-start re-seed healed that at every restart.
    Every write, delta and sync now re-links exactly the affected importers from their stored edges (`_relink_importers`), and a turn's files are all parsed before any is resolved.
    The test oracle is a fresh seed of the same tree into a second database, compared row for row; that also fixed a live drift the re-seed had been hiding until the next restart, a caller written one turn before its callee.
  - **Measured against the live Project's tree (`D:\PROJECTS\vaultspaces`, 6,000 files, into a scratch database):** the seed takes 141 s and 82.4 s of CPU; a sync of the same unchanged tree 0.6-0.8 s and 0.56-0.75 s of CPU, about 110 times less; the one-time upgrade sync of a version-2 graph, which reads and hashes every file and parses none, 2.4 s.
    On the swe-mux tree (1,637 files): seed 18.2 s of CPU, sync 0.03 s.
    Re-linking costs 29-78 ms for swe-mux's three largest import hubs (145, 95 and 78 importers) and 17-50 ms for a file appearing, after an SQL prefilter took the second from 0.33 s.
  - **Live (2026-10-18), the same Project on the redeployed daemon:** the first start's upgrade sync took 6.6 s (6,000 stale stamps rehashed, 2 parsed, 41 removed), and the next start's 0.99 s (372 stale stamps rehashed, none parsed, 54 removed), against the 141 s re-seed each start used to cost.
    The removals are the cap at work rather than deletions: the tree holds more indexable files than the 6,000 bound, so files the trunk refresh and session writes added outside the walk's first 6,000 leave again at the next sync, which is what keeps the graph equal to a seed.
  - **Why nothing moved to the worker:** what remains is the write stream - about 80 parses in 41 minutes - and the graph's own queries, neither of which ranks once the 6,000 are gone.
  - **Tests:** `tests/test_code_graph_sync.py`, 11: an unseeded Project is seeded; a sync over an unchanged tree reads nothing; a sync over every way a tree changes (a module gaining and losing names an unchanged caller uses, a module appearing that an unchanged import could not resolve, one outranking the package an import resolved to, one disappearing, a body-only change, a touch, a gained TypeScript export, a new package index) parses exactly the changed files and equals a fresh seed; a touched file is read once and then trusted; a fresh stamp is verified by content until it is old enough; a version-2 graph keeps its rows and is verified once; a caller maintained before its callee in one turn resolves; a callee written a turn after its caller re-links the caller; deleting a module unlinks its importers; the appeared-file prefilter never drops an import that could name the file; and two consumer services on one database sync at the second's first turn instead of re-seeding.
    Each was run against a deliberately broken build - re-linking off, batch resolution off, the racy rule off, the prefilter's suffix patterns off, the per-process re-seed back - and failed.
    `tests/test_code_graph_trunk_refresh.py`: the two fallback tests now assert a sync that parses the two changed files rather than a re-seed of the tree.
  - **Where:** `code_graph.py`, `deterministic_consumers.py` (`_code_graph`, `status`), `design/features/code-graph.md`, `design/interfaces.md`.

- [x] **23. Event persistence write-behind, if item 4 ranks it.** Removed.
  - **Why removed:** item 4 measured event persistence at 0.6% of GIL samples, so write-behind would trade durability (a crash losing up to one flush window, sequence numbers from memory) for almost nothing on the GIL.
    The latency half of the original concern - a hook reply waiting behind its event's insert under the `mux.db` lock - is item 10's, which answers the reply before persisting rather than changing what is durable.

- [x] **24. Extract the terminal core, in process.**
  - **Why:** the gateway must run the same protocol implementation as the daemon's fallback, and today that implementation is spread across `routes/pty.py` and `Session`.
  - **Change:**
    - A terminal core package owns the connection lifecycle and the 0.25 s attach handshake; replay, delta and resync with the ring and the replay-prefix parsers (`screen`, `bracketed_paste`, `sticky_modes`); `PtySubscriber` queues; `PtyOutputFlow`; input ownership and arbitration (`terminal_arbitration.py`); viewports and geometry; the repaint jiggle and the resize-settle scheduler; client-frame dispatch and diagnostic rate limits; and input accounting (`input_revision`, last-input age, the composer-estimate input).
    - The host supplies session descriptors (backend traits, state, created and cold flags, whether input is accepted), snapshot frames with revision and generation, the presence leader, a PTY sink and an output source.
    - The core reports input events, attach and detach, terminal mode, geometry, claim decisions, client diagnostics, repaint requests and metering back to the host.
    - `routes/pty.py` becomes an adapter over the core.
      `Session` keeps its existing attributes as views onto the core, so `delivery_readiness`, automation's attended checks and every other reader are untouched.
  - **Where:** a new `terminal_core` package, `routes/pty.py`, `session.py:2047-2053,2150-2786,2879-2906`, `design/features/terminal-input.md` (whose key-file list still names `server.py` for the socket handlers).
  - **Done when:** the full gate and `tests/test_pty_ws.py` pass with no behaviour change, and harness echo latency is unchanged.
  - **Done (2026-10-18).** `terminal_core` is four modules plus their policy constants: `stream.py` (`TerminalStream` - the ring, the three replay-prefix parsers, attach, delta, resync, the subscriber queues), `core.py` (`TerminalCore` - ownership and the claim log, viewports and geometry, the repaint pulse and the resize-settle scheduler, client-diagnostic rate limits, `input_revision`, the last input's time and the composer estimate), `connection.py` (the handshake-to-detach protocol and the sender with its credit), and `host.py` (the interfaces).
    `routes/pty.py` is now `SessionTerminalHost` plus a ten-line adapter, and `Session` keeps all 29 of its terminal attributes as views onto the core, so no reader changed; its terminal methods are delegates.
  - **The host interface is what the gateway will have to implement remotely**, so it was kept to what the code actually needed: a live descriptor (session id, backend, state, created and stopping flags, ended, four harness traits), the record snapshot with its revision, the presence leader, an event sink, three input hooks (typed text, typed bytes, an owner's submit - the side effects approvals, status detection, the composer estimate and interrupt intent need), and broadcast.
    The one ordering moved in the input path - cancelling a pending approval now runs just after `input_revision` advances instead of just before - was checked: none of those side effects reads the revision or the last input's time, and the block has no await, so no observer can tell.
  - **A boundary test makes the separation a contract**: `tests/test_terminal_core_boundary.py` imports the package in a fresh interpreter and fails if it loads anything of the daemon beyond four leaf modules.
  - **Measured, because "unchanged" is the bar.** The first build cost 0.6 us more per output chunk and about 10 us more per keystroke against the build before it, in an in-process benchmark of the two paths that changed (the per-chunk fanout work, and keystroke frames through the real `/pty` handler to their `input_ack`).
    The descriptor was a dataclass rebuilt on every read with four harness lookups (1.8 us), and the fanout reached the ring through Session views (0.12 us more per read than direct); the descriptor became a protocol read attribute by attribute and the fanout takes the stream once per chunk.
    Interleaved runs then measured the new build at 3.31 and 3.35 us per chunk against 3.96 and 4.10 before, and 59.8 and 63.1 us per keystroke against 60.0 and 64.5.
    The fleet harness (36 sessions, 90 s, four alternating pairs) cannot resolve a difference that small: keystroke echo p50 was 3.6-7.1 ms after against 3.6-4.3 ms before and p95 12-41 ms against 5-41 ms, every pair's spread wider than any gap between builds, with one ~740 ms host-wide spike landing in a run of each build.
  - **Tests:** the full gate, with `tests/test_pty_ws.py` and `tests/test_session_reliability.py` changed only where they named moved code (the sender's signature, the policy constants' module, and a fixture that built a `Session` without its constructor); two fanout stubs gained the stream the fanout now reads.

- [x] **25. Single-writer PTY input with conditional writes, in process.**
  - **Why:** once input arrives in another process, a delivery decided on the daemon's mirror can paste over a keystroke still in transit, and writes from two connections have no defined order.
  - **Change:**
    - Every PTY write and resize goes through the core's write API: browser input, the prompt queue, auto-delivery, voice, MCP interrupt, REST input, broadcast, approval keystrokes, spawn-stage paste, graceful end, repaint jiggles, and the two writers that skip accounting today (`meta_hooks.py:350-353`, `processes.py:2213-2215`).
    - Delivery writes carry a condition (`expect_input_revision`, `operator_quiet_seconds`, no pending submit) evaluated atomically with the write; a refusal sends the caller back to re-evaluate (`prompt_queue.py:2397-2450`; `delivery_readiness.py:532-535`).
    - Input times cross the API as ages, never as `time.monotonic()` values.
    - A guard test fails on any `pty.write` or `pty.resize` outside the core, in the style of the unsupervised-loop sweep in `tests/test_background_tasks.py`.
  - **Done when:** the guard is green and the delivery tests cover a refused condition.
  - **Done (2026-10-18).** `TerminalCore.write` is the only PTY write in the daemon, and `tests/test_pty_single_writer.py` sweeps every module's AST for a `.write(` or `.resize(` on anything named `pty` outside the core and the PTY implementations under it (the in-process host, the two backends, the supervisor and its client proxy).
    Besides the two writers the plan named, the sweep found exactly two more: the shared accounting helper `routes/terminal.py::_record_operator_input`, through which every accounted path already wrote, and the browser's text and binary input in `terminal_core/connection.py`.
    The helper moved into `operator_input.py` and writes through `TerminalCore.write_operator_input`, because the queue wiring, the approval sink, voice and the session routes all reached into a route module's private function for it; the browser input calls `TerminalCore.write`.
  - **The two unaccounted writers stay unaccounted.** Automation `write_pty` and the process-action interrupt now take the one writer, but not the operator accounting: `design/features/delivery-readiness.md` records that exclusion as deliberate, since advancing the operator-quiet clock for a write no operator made would let an automation's own write hold back, or wave through, a delivery.
    A test pins each (`test_an_automation_write_goes_through_the_core_and_is_not_operator_input`, and the process-action test asserting the revision stays at zero).
  - **The condition is exactly what the verdict relied on.** `prompt_queue.delivery_condition` turns a safe or interject-safe verdict into a `WriteCondition`: the `input_revision` in its evidence, the `READINESS_DEBOUNCE_SECONDS` quiet window, and no pending submit.
    Both verdicts already require all three - an interject-safe verdict refuses every hard-block reason except a running turn, and the quiet window and the pending submit are hard-block reasons - so the write re-checks nothing a verdict did not already demand.
    A refused write re-judges at most `DELIVERY_JUDGEMENT_ATTEMPTS` (3) times and then refuses `delivery_not_safe` with the refusal reasons; a confirmed send carries no condition, and neither does the submit after a paste.
    The core holds `pending_submit` now (Session keeps a view), so the condition is checked against state the core owns.
  - **Measured.** The in-process hot-path benchmark reads 3.06 and 3.21 us per output chunk and 59.2 and 51.6 us per keystroke frame, against item 24's 3.31-3.35 and 59.8-63.1, so the browser path - which passes no condition - pays nothing measurable for the single writer.
  - **Tests:** the sweep (with a self-check that it finds the core's own writes); the three refusal reasons and the holding case at the core; operator input advancing the revision and crossing as an age; and four queue cases with a readiness double that lands a keystroke between its verdict and the write - refused then blocked, refused then delivered exactly once, refused three times and given up with `input_revision_moved`, and a human override writing with no condition.
    Writer stubs across the queue, auto-delivery and agent-messaging tests gained the `condition` keyword; the voice and process-action tests now build a real `Session` over a recording PTY (`tests/support/pty_sessions.py`), because a stub with a bare `pty.write` no longer exercises the path production takes.

- [x] **26. Shared host and origin policy.**
  - **Why:** the gateway must enforce the daemon's browser policy without copying it, and a cross-port gateway origin fails the daemon's exact `Origin == Host` rule (`server.py:481-514`).
  - **Change:** `http_origin_policy.py` holds `allowed_browser_host` and origin matching, and the daemon uses it unchanged.
    The gateway accepts an `Origin` whose host is allowed and whose port is the daemon's port, its own port, or 443 for a `*.ts.net` page served through Tailscale Serve.
  - **Done when:** both processes use the module and tests cover each accepted and refused origin.
  - **Done (2026-10-18), with one rule tightened.** `http_origin_policy.py` holds the Host allowlist, `is_tailscale_ip` (moved out of `tailscale.py` so the module needs no subprocess machinery), `origin_to_check` (which requests are judged on their `Origin`), the daemon's exact rule, the gateway's rule, and `gateway_middleware`, which item 28 installs.
    `server.security_middleware` now calls the module and keeps only what is the daemon's own - the sandboxed-preview `Origin: null` exception and the response headers.
  - **The gateway's rule requires the page's host to be the host the gateway was reached on**, which the wording above does not say.
    "An `Origin` whose host is allowed" would accept a page from any machine on the tailnet: every Tailscale address and every `*.ts.net` name is on the allowlist, so a page served by another device's swe-mux on `100.64.0.5:8765` could drive this machine's terminals through `100.101.102.103:8766`.
    The daemon's own rule has never allowed that, and the gateway now matches it; only the port relaxes.
    The daemon's exact rule is accepted too, which is what a reverse proxy presents - Serve's `/pty` mount (Host and Origin both the `ts.net` name, no port) and Vite in development.
  - **Tests:** `tests/test_http_origin_policy.py` - ten accepted origins (loopback by address, name and IPv6, direct tailnet HTTP, the gateway's own port, Serve through its mount and direct, an explicit 443, a reverse proxy, mixed case) and sixteen refused (another tailnet machine, another Serve name, a different name for this host, an unrelated port, HTTP on 80, 443 off a `ts.net` name, a LAN host, a disallowed host presenting itself as same-origin, a suffix trick, `null`, a non-web scheme, unparseable Origin and Host, no Host); the middleware's 101/403/421 answers over real upgrades, and a GET judged on its Host alone; the daemon refusing a page on the gateway's port; and a fresh-interpreter import loading no other `swe_mux` module.
    Each of five deliberate breaks - host equality off, 443 on any host, the origin's own host unchecked, any port, the middleware skipping the origin - failed the file.
    The gateway half of "both processes" is the install in item 28; the policy it will run is the one tested here.

- [x] **27. Gateway control protocol and the remote terminal hub.**
  - **Why:** the daemon must drive terminal cores in the gateway through the same interface it uses in process.
  - **Change:**
    - Typed messages over token-authenticated `framed_ipc`, with capability negotiation and a protocol version the daemon checks.
    - Daemon to gateway: session upserts and removals (descriptors), snapshot frames with revision and generation, the presence leader, broadcast membership, replay and flow configuration, conditional writes and repaints with request ids, and diagnostic queries.
    - Gateway to daemon: attach and detach with counts and device, coalesced per-session input summaries carrying the authoritative `input_revision`, the last-input age and what the daemon's side effects need (submit, interrupt intent, pointer kind, composer text), terminal mode, geometry, claim decisions, client diagnostics, repaint requests, metering counters, and write results.
    - Input summaries are coalesced per session rather than queued, so a stalled daemon costs memory bounded by the session count, and a reconnect resyncs the full input state.
    - The daemon's `Session` views read the mirror when the hub is remote.
  - **Where:** `terminal_core` (the remote hub), `design/interfaces.md`.
  - **Done when:** the core's test suite runs against both the local and the remote hub.
  - **Done (2026-10-18).** Three process-agnostic modules in `terminal_core`: `protocol.py` (the vocabulary, version 1), `gateway.py` (`GatewayHub` - a `framed_ipc` server for the daemon and one stream-plus-core per session it is sent, with `serve` for a browser and `feed_output` for the supervisor's bytes, which item 28 wires), and `remote.py` (`RemoteHub` and `RemoteTerminal`, the daemon's mirror, which stands on `Session.terminal` where a `TerminalCore` stands in process).
    The message set is in `design/interfaces.md`; the decisions are below.
  - **Keystrokes cross as writes, not as a summary of them.** The plan's "input summary" would have merged text, and two consumers cannot take that: interrupt intent matches a lone `\x03` or `\x1b`, and the composer estimate classifies each write on its own.
    So a session's `state` carries the keystroke writes since the daemon's last acknowledgement, in order, and the daemon runs its own `TerminalHost` side effects on each - the same functions, in the same order, as in process, only later.
    What coalesces is the sending: at most one `state` per session is unacknowledged, and everything that happens meanwhile folds into the next.
    A stalled daemon therefore costs the gateway one pending summary per session - at most 1024 writes or 256 KiB, oldest dropped and read by the daemon as typed-in composer text - and not a queue in a socket buffer.
  - **Exactly once per daemon.** The writes are numbered; a daemon applies each number once, so a summary resent after an acknowledgement lost to a reconnect changes nothing, while a new daemon applies everything outstanding and a new gateway (a new `gateway_id`) numbers from zero again.
  - **Who owns what.** The gateway owns the input accounting, ownership, viewports, geometry and the terminal mode, because keystrokes arrive there; the daemon's mirror merges reports so a revision only grows and the last input only gets more recent, and a stale report never moves either backwards.
    The daemon owns what typing means and `pending_submit`, which it sends the other way because the gateway needs it only as a write condition.
  - **Writes are ordered notices with a request id**, not requests: `framed_ipc` runs a request's handler as a task but a notice inline, so mixing the two would let a later unconditional write overtake an earlier conditional one.
    `write` and `write_operator_input` do not wait, so their callers keep their shape; the queue's conditional write is `write_when`, awaited on either terminal, so the queue's writer became async (`operator_input.deliver_operator_input`).
  - **Deviations from the wording above, each for a reason.** Broadcast membership is not sent: the gateway forwards a broadcast keystroke to the daemon, which delivers it through `deliver_broadcast` exactly as in process, because target liveness and the accounting every operator write owes are the daemon's, and one implementation beats two.
    Client diagnostics, repaint requests and attach and detach reach the daemon as the events the core already emits (`event`), rather than as messages of their own.
    Metering counters wait for item 28, which has the sockets to meter.
  - **`Session.attended` replaced `bool(session.subscribers)`** at all five readers (automation's attended checks, both of them, fleet intelligence, the Git monitor, and readiness evidence), because `subscribers` counts only browsers attached in this process.
    `routes/pty.py` closes a `/pty` upgrade for a session whose terminal is remote with 4012, the first of item 32's handoff rules, so a terminal can never be served from two processes at once.
  - **Measured: the in-process path pays nothing.** The core's mutations now call `on_change`, unset in process; the hot-path benchmark reads 3.02-3.08 us per output chunk and 51.3-51.6 us per keystroke frame against item 25's 3.06-3.21 and 51.6-59.2.
  - **Tests:** `tests/test_terminal_hub.py`, 43: sixteen behaviours run against both a local and a remote hub (`tests/support/terminal_rig.py`, the remote one over a real loopback connection) - keystroke accounting, writes reaching the daemon once and in order, pointer clicks, a condition refused after a keystroke lands and accepted while it holds, accounted and unaccounted daemon writes, `pending_submit` as a condition, snapshot updates and output reaching browsers, the leader deciding a passive claim, ownership, geometry, mode and attachment reaching the daemon, a repaint, events, broadcast, and a `Session` reading either terminal through its views - plus the gateway's own contract: coalescing under a stalled daemon, a reconnect resyncing once, a resend after a lost acknowledgement applying once, a restarted daemon keeping the browsers' state, the sessions set, diagnostics, protocol and token refusal, a disconnected gateway refusing a conditional write, and the daemon refusing `/pty` for a remote terminal.
    Ten deliberate breaks - sending while unacknowledged, applying writes without their numbers, ignoring a write's condition, ignoring the reported revision, a re-upsert forgetting the browsers' state, ignoring the sessions set, dropping the leader, dropping `pending_submit`, not reporting keystrokes, never sending the claim log - each failed the file.
    The queue, auto-delivery and agent-messaging writer doubles became async; four attended doubles gained `attended`.

- [x] **28. The terminal gateway process.**
  - **Why:** this is the isolation itself.
  - **Change:**
    - `swe_mux.terminal_gateway`, frozen as `swe-mux.exe --gateway-child`: an aiohttp app serving `/pty/{sid}` and a health route, hosting terminal cores.
    - It connects to the supervisor with `scope: "terminal"` and `output_gap`, subscribes with `since`, reconnects after a loss, and degrades against an older supervisor.
    - **Settle first:** subscribe on first attach with a linger, or keep every live session subscribed; measure memory and attach latency for both.
    - It binds the daemon's host set (`listener_hosts()`, `__main__.py:188-214`) on `terminal_gateway_port` with `resilient_event_loop` and `ListenerGuard`, applies `http_origin_policy`, and answers 503 until its supervisor connection and its first daemon attach are ready.
    - Discovery file `terminal-gateway.json` (pid, creation time, ports, token, build id, protocol); single instance on every platform, since the supervisor has no POSIX lock to copy; `cwd` in the data dir; breakaway spawn; above-normal priority; switch interval and timer resolution set; its own rotating `terminal-gateway.log`.
    - It keeps last-known descriptors through a daemon restart, and with no daemon attached it still carries output and input and holds the input summaries.
    - It exits on the daemon's quit intent, and after a linger with no daemon attached and no live sessions.
  - **Where:** `terminal_gateway.py`, `desktop.py:1141-1225`, a new `design/features/terminal-gateway.md`.
  - **Done when:** a terminal attached through the gateway keeps echoing while the daemon's loop is deliberately blocked.
  - **Done (2026-10-18).** `src/swe_mux/terminal_gateway.py` is the process (`design/features/terminal-gateway.md`): a `GatewayHub` for the cores, a `SupervisorLink` at the `terminal` scope, `/pty/{sid}` and `/health` under `gateway_middleware`, `InstanceLock`, discovery, metering and its lifecycle.
    `desktop.py` dispatches `--gateway-child`; from source it is `python -m swe_mux.terminal_gateway`, and `gateway_command` builds both.
    Nothing starts it yet - that is item 29 - so the running app is unchanged.
  - **Measured: typing echoes through a blocked daemon.** The gateway as a process over a real supervisor, a browser on its own thread typing every 50 ms, and the daemon's loop blocked three times for 2 s each: 145 keystrokes, 97 of them during a block, echo p50 0.81 ms and max 1.18 ms, and the daemon applied all 145 in order once each block ended.
    The test version (`test_typing_keeps_echoing_while_the_daemon_is_blocked`) blocks for 1 s and asserts every echo under 500 ms, which is two orders of magnitude of headroom for a loaded runner rather than a timing bet.
  - **Settled: subscribe eagerly.** 36 sessions with a 2 MiB retained ring each, the gateway as a process:

    | Policy | RSS settled | RSS after 10 attaches | Attach p50, cold | Attach p50, warm |
    | --- | --- | --- | --- | --- |
    | `eager` | 114 MiB (41 empty) | 114 MiB | 2.7 ms | 2.7 ms |
    | `on_attach` | 41 MiB | 64 MiB | 15.5 ms | 2.7 ms |

    Parsing all 36 sessions' output at 20 KiB/s each for ten seconds cost an immeasurable amount of CPU under both, so CPU did not decide it.
    Eager costs the gateway the bound the daemon already pays for its own copy and makes every attach a read of local memory, which is the point of the process; `on_attach` stays as a flag for a machine where the memory matters.
    The first version of `on_attach` kept the ring after its linger and so saved nothing, which the measurement found: it now releases the ring and the next subscribe is a whole-buffer one, which is what the 15.5 ms is.
  - **Decisions.**
    - **The single instance is a file lock**, `terminal-gateway.lock` (`msvcrt.locking` / `fcntl.flock`), because the supervisor's named mutex has no POSIX twin; the OS drops it however the process dies.
    - **The build id is a digest over compiled code**, not `__version__`, over `GATEWAY_MODULES`, the exact closure; a fresh-interpreter test fails when the list drifts from what the module loads, so item 29's version-skew check cannot be blind to a module it forgot.
    - **Health is 503 until a supervisor and a first daemon**, because before its first daemon the gateway does not know which sessions it holds.
    - **Metering is cumulative**, a `NetworkUsage` snapshot every five seconds when it changed, so a report the daemon misses costs nothing; the daemon's side of it is item 32's.
    - **A subscribe reply is applied in the read loop** (`framed_ipc`'s reply hook), so output after the reply can never land before the bytes the reply carried.
    - **It keeps every session through a daemon's absence** and exits only on `quit`, or after 120 s with no daemon *and* nothing live under the supervisor - a live session keeps it up however slow a redeploy is.
  - **Left to later items, as the plan assigns them.** The breakaway spawn, the `terminal_gateway_port` and `terminal_gateway_enabled` config fields and the stderr redirect to `terminal-gateway-console.log` belong to the spawner (item 29); the process inspector must classify the gateway as infrastructure and reserve its ports (frozen `--gateway-child` is the app image, source `-m swe_mux.terminal_gateway` matches the session-helper pattern), and redeploy must stop it gracefully (item 33).
  - **Tests:** `tests/test_terminal_gateway.py`, 17, against a real in-process `SupervisorServer`: the supervisor vocabulary it speaks, the build-id closure, the command shapes, the instance lock, discovery written and removed, health 503 then 200, quit, idle exit, a browser typing and seeing its echo under both policies at the `terminal` scope, `on_attach` releasing the ring and resubscribing whole, a gap and a lost supervisor connection each resubscribing as a delta, an unknown session closed with 4010, a foreign `Origin` refused, metering reaching the daemon, and the blocked-daemon test above with the gateway as a real process.
    Ten deliberate breaks - the hello without the terminal scope, resubscribes never sending `since`, the linger keeping the ring, eager never subscribing early, health always ready, idle never ending it, the daemon's quit ignored, a gap ignored, no browser policy, metering never sent - each failed the file.

- [x] **29. Daemon-owned gateway lifecycle.**
  - **Why:** the gateway must exist wherever the daemon runs, including source mode, standalone daemons, and Linux and macOS, which have no tray.
  - **Change:** discover-or-spawn at startup after the supervisor connects; an orchestrated gateway restart when its build id differs, with clients told to reconnect; respawn on death with capped backoff; left running on detach restarts; stopped on quit.
    `daemon-recovery.json` records its identity, and desktop recovery never kills it.
    `/api/health` reports `terminal_transport` (`gateway` or `daemon`, the port, and the state), and `swemux doctor` checks it.
  - **Where:** `server.py`, `daemon_recovery.py`, `lifecycle.py`, `doctor_local.py`, `design/features/daemon-resilience.md`.
  - **Done when:** tests cover spawn, rediscovery after a daemon restart, the version-skew restart, and quit.
  - **Done (2026-10-18).** `gateway_host.GatewayHost` (`design/features/terminal-gateway.md`, "Owned by the daemon"), built in the `services` phase beside the worker host and started only with a supervisor attached; `terminal_gateway_enabled` (off) and `terminal_gateway_port` (0, the daemon's port plus one) are new restart-applied config fields with Settings controls under Processes.
    `/api/health` carries `terminal_transport`, `/api/diagnostics/background` a `terminal_gateway` block, `swemux doctor` a `daemon.terminal_transport` row, and the recovery record the gateway's identity; the lifecycle ledger gets a line for every start, adoption, replacement and quit.
  - **The version-skew restart is a replacement rule, with one exemption.** A found gateway is replaced when its build id, protocol, browser port or daemon port differs from this daemon's - so a config port change applies at the next start the same way a new build does.
    A gateway this daemon started itself is exempt from the build comparison: it runs this daemon's code by construction, and without the exemption any disagreement in how the two processes compute the digest would be a restart loop rather than a log line.
    The two computations are asserted equal by a test that runs one in a fresh interpreter, because the exemption covers a daemon's own gateway only and every successor depends on the digest agreeing.
  - **"Clients told to reconnect" is the gateway's job, not the daemon's.** `GatewayHub.close` now closes every browser with 4011 before the process exits, whichever way it is stopped - replaced, quit or idle - so no path drops a socket without saying what to do next.
  - **Quit asks first and terminates second.** `quit` is sent over the daemon's connection or, for a gateway the daemon is not connected to, over a connection of its own; a gateway that stays ten seconds is terminated by its pid and creation time, never a recycled pid, and one speaking another protocol is terminated after the grace because the request cannot be expressed to it.
    Measured in the tests: a quit gateway exits with code 0 and removes its discovery record, and a replaced one exits 0 too, so the orchestrated path is the one taken rather than the termination behind it.
  - **The process inspector reserves the gateway by pid**, through the daemon's host and through the sampling request's mirror in the worker, because after a daemon restart it is no descendant of the daemon and from source it is not the app's image either - so neither existing rule finds it.
  - **Desktop recovery and the gateway.** Recovery already terminated only the daemon's own pid, so the gateway survived it by construction; `terminate_generation` now also refuses a record whose daemon pid is the supervisor's or the gateway's, and a test runs a daemon-like process whose child stands for the gateway and proves the child outlives the termination.
  - **`doctor_local.py` gained no check**, deliberately: which process carries the terminals is a fact about a live daemon, so its `daemon` row's reason now says so instead.
  - **Not here, as the plan assigns it.** Metering into the daemon's `NetworkUsage` and handing the gateway sessions are item 32; a gateway that is alive but unresponsive is reported (its connection stays up, so `terminal_transport` keeps saying `gateway`) and not acted on, because killing on a missed probe would let a stalled *daemon* kill a healthy gateway, which is the incident shape this plan exists to end - item 32 decides what a hung gateway means for the sessions it holds.
  - **Measured live** against an isolated daemon from the primary checkout (port 8799, gateway 8800, its own data directory and supervisor): the daemon started the gateway and reported `terminal_transport` `gateway` on 8800; the gateway's own health was 200 with the supervisor connected at the terminal scope with `output_gap` and `subscribe_since`, and both processes computed build `c28181d8c00a0f0f7889d540`.
    A detach restart (`POST /api/daemon/restart`) brought a successor up in 2.0 s that adopted the same gateway pid (spawns 0, adoptions 1), the gateway answered its health throughout the daemon's absence, and a quit through the desktop shutdown endpoint ended it on the daemon's `quit`, leaving no process on that data directory and nothing listening on either port.
    The run also found the gateway logging as `__main__` from source, because `-m` makes that its module name; its logger is now named explicitly.
  - **The derived port has to exist only for a daemon that will start a gateway.** The first validation refused a daemon on port 65535 outright, because its default gateway port would be 65536, and the existing range test for `port` caught it: that config is valid while the gateway is off, so the overflow is refused only with `terminal_gateway_enabled` on, while an explicit gateway port equal to the daemon's is refused always.
  - **Tests:** `tests/test_gateway_host.py`, 20, every lifecycle case against the real gateway as a process: a daemon starts one and connects; a quitting daemon takes it (exit 0, record removed); a restarted daemon adopts the one it left (spawns 0, adoptions 1); a gateway from another build is asked to quit and replaced (exit 0), and the replacement is then never replaced for its own build across a reconnect; one started for another browser or daemon port is replaced; one speaking another protocol is terminated and replaced; a dead one is started again; a spawn that loses the instance lock reports exit 3 and succeeds once the lock frees; a host stopped mid-spawn leaves nothing; disabled and unsupervised hosts send browsers to the daemon; a daemon with no supervisor says why on `/api/health` and the diagnostics block; the doctor row; the port rules, including a daemon on 65535 with the gateway off; recovery sparing the gateway; and the cross-process build id.
    Elsewhere: a stopping gateway closes its browsers with 4011 (`test_terminal_gateway.py`), the gateway reserved by pid from the host and from the mirror (`test_process_infrastructure_identity.py`), the mirror's pid validation (`test_process_sampling_worker.py`), and `terminal_transport` on a default daemon's health (`test_startup_gate.py`).
    Ten deliberate breaks - accepting another build, replacing its own gateway for its build, a quitting daemon never asking, a restarting daemon quitting it, trusting a dead gateway's record, accepting a port mismatch, recovery allowed to terminate the gateway, the inspector not reserving it, browsers dropped rather than told, health silent on the transport - each failed the suite; the port case was missed at first and is what the port test was written for.

- [x] **30. Frontend transport selection and fallback.**
  - **Why:** the browser must reach the gateway on every access path and fall back without user action.
  - **Change:**
    - The `/pty` URL rule in `api.ts`: an `https:` page (Tailscale Serve) uses same-origin `/pty/`, which the Serve mount routes to the gateway; an `http:` page uses `ws://<hostname>:<terminal_gateway_port>/pty/<sid>`; and when the gateway is down, refuses the session, or `terminal_transport` says `daemon`, the page uses same-origin `/pty/` on the daemon.
    - The gateway port and state come from `/api/health`, which is already polled, and are cached for reconnects while the daemon is unreachable.
    - Close codes distinguish "use the daemon for this session" (in-process and cold sessions) from "gateway restarting".
    - Vite proxies `/pty` to the gateway in development (`frontend/vite.config.ts:74`), and `demo/fakeSocket.ts` keeps its `^/pty/` match.
  - **Where:** `frontend/src/api.ts`, `frontend/src/TerminalPane.tsx`, `frontend/src/liveness.ts`, `technical/frontend/packages.md`.
  - **Done when:** unit tests cover the URL rule and a renderer test covers fallback and reconnect.
  - **Done (2026-10-18), with one deviation.** `frontend/src/terminalTransport.ts` holds the transport state and the URL rule (`design/features/terminal-gateway.md`, "Browsers"); `daemonLiveness.probeDaemon` feeds it each healthy `/api/health` body; `TerminalPane.tsx` asks it on every connect and hands it every close, a stalled handshake included.
  - **The deviation: an `https:` page reaches the gateway at `/gateway/pty/<id>`, not at same-origin `/pty/`.** With Serve's mount at `/pty`, a down gateway would leave an `https:` page with no route to the daemon at all, because the daemon's `/pty` would be behind the mount - and falling back to the daemon is the whole safety property.
    So `/pty` stays the daemon's on every access path, the gateway answers under `/gateway/pty/{sid}` as well as `/pty/{sid}` (so it works whether Serve strips the mount prefix or not, which item 31 settles), and the Vite dev server proxies `/gateway` the same way.
  - **The last report is remembered**, in memory and for ten minutes in `localStorage`, because the moment the gateway matters most - a reconnect during a daemon restart, or a page the service worker reloads while the daemon is down - is the moment the daemon cannot be asked.
  - **Every close that names where to go is answered at once** (50 ms rather than the one-second-and-up backoff): 4010 holds that session on the daemon for a minute, 4011 holds the gateway off for two seconds, a gateway that never opened is backed off from (five seconds doubling to a minute), and 4012 from the daemon outranks a stale report for ten seconds and, before any report named the gateway's port, asks `/api/health` once more.
    A session redirected more than four times in ten seconds takes the ordinary backoff, so two processes disagreeing about one session cannot make a busy loop of it.
  - **Tests:** `frontend/test/terminalTransport.test.ts`, 14: the URL rule on loopback, the tailnet, IPv6, Serve and the dev proxy; report parsing; each close code; the failure backoff doubling and resetting on an answer; an ordinary drop keeping the transport; 4012 outranking a stale report and asking for one when there is none; the bounce guard; a newly-up gateway tried at once; the remembered report surviving a reload and expiring.
    `daemonLiveness.test.ts` gained the body hand-off (a 503's body is not read).
    `frontend/test/renderer/terminal-transport.spec.ts`, 6, drives the real `TerminalPane` over scripted sockets (`terminal-transport-harness.html`) and reads off the screen which process served it: the daemon with no report, the gateway with one, 4010 falling back to the daemon, an unreachable gateway falling back inside the handshake window, a 4011 to the daemon and a 4012 back to the gateway, and a 4012 followed before any report named the gateway.
    `tests/test_terminal_gateway.py` gained a terminal reached through the `/gateway` prefix with a reverse proxy's Host and Origin.

- [x] **31. Tailscale Serve mount and firewall.**
  - **Why:** an `https:` phone page cannot open `ws:` to another port, and direct tailnet HTTP needs the gateway port reachable.
  - **Change:**
    - **Settle first:** confirm on this host how `tailscale serve --set-path=/pty` maps the mount path onto the target; Serve stores handlers keyed by path under each host and port.
    - `tailscale.py` adds and reconciles the `/pty` mount beside the root mapping; `_loopback_target_port`, which takes the first loopback port it finds (`tailscale.py:125-144`), and the reclaim logic (`tailscale.py:583-652`) learn about both handlers; boot reconfiguration keeps both.
    - A Windows firewall rule for the gateway port (`windows_firewall.py:331-348`).
  - **Where:** `tailscale.py`, `windows_firewall.py`, `design/features/remote-access.md`.
  - **Done when:** the phone reaches a terminal through the gateway both over Serve and over direct tailnet HTTP.
  - **Done (2026-10-18), with the mount at `/gateway` (item 30's deviation) and the terminal half of "done when" carried by items 32 and 35.**
  - **Settled on this host (Tailscale 1.102.2), on scratch HTTPS ports and with the operator's Serve config restored exactly each time.**
    Serve strips the mount from the path it forwards (`/gateway/pty/x` arrives as `/pty/x`), preserves Host including a non-443 port, adds `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto` and the `Tailscale-User-*` headers, and proxies WebSocket upgrades.
    Re-running the root command keeps every other handler on the listener, so the daemon's idempotent boot `serve --bg --https=443 <daemon>` does not drop the mount; `--set-path=/gateway <target>` replaces that one handler in place; `--set-path=/gateway off` removes only it and exits 1 with "handler does not exist" when it is absent; and a mount whose backend is not listening answers 502, which the page treats as a gateway that never opened.
    One trap for anyone repeating this from Git Bash: MSYS rewrites a `/gateway` argument into a Windows path and Serve then mounts that, so CLI experiments need `MSYS_NO_PATHCONV=1`; the daemon passes an argv through `run_bounded` and is unaffected.
  - **The ownership reads were the real hazard, and they are fixed before anything is mounted.** `_loopback_target_port` took the first loopback port anywhere in the listener's config, so with two handlers it could return the gateway's port; the reclaim rule would then probe the *gateway's* health, find no daemon there, call a live daemon's address abandoned, and take it - the 2026-08-17 incident again, by a new route.
    Every ownership read now goes through `_mount_loopback_port`, which reads one handler by its path, and `_web_target_matches` and `_loopback_target_port` read the root alone whenever the listener has handlers; a test runs the reclaim against a config with the mount listed first and asserts the liveness probe asked the daemon's port and nothing else.
  - **The mount follows the root route** (`tailscale.reconcile_gateway_serve`): touched only while `/` on 443 proxies to this daemon, set to the gateway's port while it is enabled, removed when it is disabled and a loopback mount is found, never replacing a non-loopback handler at the path, and never added to a listener Funnel exposes.
    It runs after the root is ready, at startup (`__main__._auto_enable_mobile_voice`) and on the mobile-voice setup route, whose response carries it as `gateway_mount` without letting its failure make mobile voice not ready; `/api/remote/status` reports `gateway_mount_port`.
    It follows config rather than the gateway's state, because a mount that came and went with each gateway restart would churn the operator's Serve config, and the page already falls back from a gateway that does not answer.
  - **Firewall.** The Windows inspection judges the gateway's port with the same rules in the same pass over the program's rules and reports it beside the daemon's (`gateway_port`, `gateway_rule_allowed`; `rule_allowed`, `blocking_rule_detected` and `needs_repair` cover both), with a detail that names the gateway when it is the only port not admitted; the repair writes one rule with `-LocalPort <daemon>,<gateway>` and removes a Block rule on either port.
    One rule is enough because the frozen gateway is `swe-mux.exe --gateway-child`, the same program.
    The POSIX probe tries the gateway's port too and names the allow command for whichever failed.
    All of it reads `Config.active_terminal_gateway_port()`, so a disabled gateway is not judged at all - its absence from the inspection must not read as a missing rule.
  - **Tests:** `tests/test_gateway_serve_mount.py`, 20: the mount never read as the root in either order; the reclaim asking the daemon behind its mount; added beside the root with the root untouched and the exact argv; a correct mount running nothing; a moved port; a disabled gateway removing its mount, and running nothing when there is none (the CLI's exit 1); a daemon that does not own the root moving nothing in either direction; a foreign handler at the path left alone; a Funnel listener getting no mount; no Serve config, no CLI and an unreadable config; a failed and an unretained mount reported as errors; `gateway_mount_port` on the status; and startup reconciling only after the root is ready.
    `tests/test_release_readiness.py` gained eight: an unadmitted and a blocked gateway port, both admitted, a disabled gateway not judged, the inspection and repair scripts, the inspect and repair functions carrying the port (the elevated script decoded), and the POSIX probe naming the gateway's port.
    Twelve deliberate breaks - the first loopback port read as the root, root ownership unchecked, a foreign handler replaced, Funnel ignored, an absent mount removed anyway, the mount not verified after setting, `rule_allowed` blind to the gateway, a gateway Block rule not counted, a repair admitting only the daemon's port, the inspection not given the port, the POSIX probe ignoring it, and startup reconciling before the root is ready - each failed the suite.
  - **Measured live.** After the redeploy the live daemon (gateway disabled) logged `terminal gateway Serve mount unchanged` once mobile voice was ready, reported `gateway_mount_port: null`, and left the 443 listener holding its root handler alone.
    Against an isolated daemon from the primary checkout (port 8799, gateway 8800, `--local-only`, its own data directory) behind a scratch Serve listener on HTTPS 45699, `reconcile_gateway_serve` running the real CLI added the mount (and then reported `unchanged` without running anything), Serve held exactly the two handlers, and a daemon that did not own the root moved nothing.
    Through the tailnet HTTPS address from this host: the gateway's `/health` answered 200 at `/gateway/health`, the daemon's `/api/health` answered beside it, a `wss` upgrade to `/gateway/pty/<sid>` crossed Serve, the gateway's browser policy and its router to be closed 4010 "not served by the gateway" (the gateway holds no sessions until item 32), the same upgrade with a foreign `Origin` was refused 403, and the daemon's own `/pty/<sid>` still served the terminal through the root route.
    Disabling the gateway removed the mount and left the root, removing again ran nothing, and the operator's Serve config was byte-for-byte what it was before.
  - **Not verifiable yet, and where it lands.** A terminal actually carried by the gateway needs the daemon to hand it sessions (item 32); the phone over direct tailnet HTTP needs the gateway enabled in the frozen app, whose program the firewall rule names (items 33 and 34); item 35 measures both on the live fleet.

- [ ] **32. Switch-over and hub handoff.**
  - **Why:** a session's terminal core must live in exactly one process at a time, or ownership and accounting split between two.
  - **Change:**
    - While the gateway is healthy, the daemon refuses new `/pty` upgrades for gateway-served sessions with the "use gateway" close code; in-process and cold sessions always stay on the daemon.
    - When the gateway comes up, the daemon closes its own sockets for those sessions with "moved", and clients reconnect with a delta.
    - When the gateway is gone, the daemon takes the cores over from its mirror and bumps `input_revision` once, so any in-flight condition fails safe.
    - `/pty` stays registered on the daemon, so the frontend overlay's API digest still covers it, and the gateway protocol version joins that digest so an overlay against an incompatible gateway is refused (`frontend_overlay.py:276-304`).
  - **Done when:** tests cover both handoff directions with clients attached.

- [ ] **33. Packaging, redeploy, update and the live tier.**
  - **Why:** the gateway is a new long-lived `swe-mux.exe` that holds the bundle image.
  - **Change:**
    - `stop_app_processes` stops the gateway gracefully before the swap (`bundle_apply.py:633`), the bundle-holder scan expects it, the relaunched daemon spawns the new gateway, and `wait_healthy` also reports gateway health.
    - The update applier follows the same path; the installer needs only the firewall rule.
    - The `live_daemon` tier runs a terminal through the gateway, asserts that quit leaves no descendant, and asserts that echo continues across a detach restart of the daemon.
    - CI runs the gateway from source on the Linux and macOS legs.
    - The root `CLAUDE.md` reload flows state what a daemon restart, a redeploy and an update each do to the gateway.
  - **Where:** `bundle_apply.py`, `bundle_locks.py`, `packaging/redeploy_desktop.py`, `tests/support/live_daemon.py`, `tests/test_live_daemon.py`, `.github/workflows/ci.yml`, `CLAUDE.md`, `design/features/desktop-shell.md`.
  - **Done when:** a staged redeploy on this host keeps every session and reconnects every terminal, and CI is green on every leg.

- [ ] **34. Default the gateway on.**
  - **Why:** `terminal_gateway_enabled` stays off until the gateway has proven itself on the live fleet.
  - **Change:** flip the default after a sustained live period with no terminal regressions, and keep the switch as the fallback.
  - **Done when:** the default is on and the changelog says so.

- [ ] **35. Verify at incident scale and close out.**
  - **Why:** the plan is done only when the original failure no longer reproduces.
  - **Change:**
    - Harness at and beyond the 2026-10-16 fleet: keystroke-echo p99 through the gateway unaffected by daemon stalls, daemon stall time against the item 1 baseline, hook decisions inside their timeout, and no agent throttled by a stalled subscriber.
    - Live fleet: `loop_stalls` per day and gateway echo latency (`tools/pty_latency_bench.py`) against the baseline.
    - Documents: `design/architecture.md` (process model), `design/features/daemon-resilience.md` (terminal traffic no longer pauses on a daemon stall or restart), `design/features/terminal-input.md`, `design/interfaces.md`, `design/features/remote-access.md`, `design/features/desktop-shell.md`, `technical/backend/packages.md`, `technical/backend/packages/runtime-rules.md`, `development/PERFORMANCE_RUNBOOK.md`, the root `CLAUDE.md`, and `.docs/CLAUDE.md`.
  - **Done when:** the numbers are recorded here and this plan moves to `development/archive/`.
