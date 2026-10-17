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

- [ ] **5. GIL switch interval.**
  - **Why:** each GIL release by the loop can cost up to the switch interval, 5 ms by default, to win the lock back while another thread is runnable.
  - **Change:** a validated `gil_switch_interval_ms` config key, applied with `sys.setswitchinterval` in `__main__.main()` after `raise_timer_resolution()` (`__main__.py:428-444`) and in every new process entry point, and reported on `/api/diagnostics/background`.
    The supervisor is left alone.
  - **Settle first:** measure 5, 2, 1 and 0.5 ms on the harness (loop-lag p99, echo latency, background throughput).
    Windows GIL waits are millisecond-granular, so a sub-millisecond value may truncate.
  - **Done when:** the default is the measured best and the measurements are recorded here.

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

- [ ] **8. WebSocket compression off for loopback peers.**
  - **Why:** aiohttp negotiates permessage-deflate by default, compressing frames of 16 KiB or less on the loop and larger ones on an executor thread (`network_usage.py:302-311`; aiohttp `_websocket/writer.py`), and on loopback that buys nothing.
  - **Change:** `metered_websocket` disables compression for loopback peers on `/pty` and `/events`, and keeps it for tailnet peers unless the harness shows it costs more than it saves there.
  - **Done when:** harness numbers are recorded for both peer kinds.

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

- [ ] **11. Lag-driven load shedding for deferrable loops.**
  - **Why:** housekeeping keeps its cadence while the loop is drowning, and nothing can defer it today (`background_tasks.py:167-327`).
  - **Change:**
    - `background.pace(name, cadence, *, deferrable)` replaces the `asyncio.sleep` in each loop.
      It reads a cheap recent-max accessor on the loop-lag monitor, which is attached to `background` rather than only published on the app (`loop_lag.py:102-118`; `server.py:2753`).
    - While recent lag is over a threshold, deferrable loops stretch their cadence up to a bounded cap and count the deferrals; nothing is deferred forever.
    - Deferrable: git sweeps, canonical telemetry rollup, reconcile, backfill and retention, the ghost-window sweep, usage refresh, provider quota polling, plugin registry refresh, store retention, media cleanup, the update check, the operational telemetry reconcile, and the process inspection cadence.
    - Never deferrable: the lifecycle heartbeat (desktop recovery reads it), the state watchdog, the session-recovery flush, the status-timeline flush, the listener guard, the loop-lag probe, the readiness watcher and auto-delivery.
    - Per-session tasks (transcript observer, switch watcher, discovery and exit checks) register with the accounting, so their cost appears in `costliest`.
  - **Done when:** governor state appears on `/api/diagnostics/background`, and the harness shows less stall time with housekeeping still completing.

- [ ] **12. Supervisor: non-blocking per-connection fanout.**
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

- [ ] **13. Supervisor: subscribe-since, stream identity, terminal scope, notices.**
  - **Why:** the gateway needs delta resubscription, cursors that cannot cross streams and no session secrets, and the daemon needs true geometry when another connection resizes.
  - **Change:**
    - `subscribe` accepts `since` and replies with `bytes_since(since)` and `delta: true` when those bytes are retained, and with the full buffer otherwise (`supervisor.py:493-510`).
    - Every session info and subscribe reply carries `stream`, the session id plus `started_at`.
    - `hello` accepts `scope: "terminal"`: its replies omit `meta`, which carries `hook_secret` and `mcp_token`, and the connection may only `subscribe`, `unsubscribe`, `write`, `resize` and `list`.
    - An unsolicited `resized {sid, cols, rows}` goes to every subscriber after any resize, and `removed {sid}` goes out before `remove` cancels a fanout (`supervisor.py:850-879`).
  - **Done when:** tests cover each message, and an unmodified daemon still drives the new supervisor.

- [ ] **14. Daemon side of the supervisor changes.**
  - **Why:** the daemon must use the new capabilities and keep working against the older supervisor that is running until item 17.
  - **Change:**
    - Advertise `output_gap`; on `gap`, resubscribe with `since`, and `scrollback.seed` on a full reply (`scrollback.py:67-78`).
    - Replace the silent oldest-bytes drop in `_trim_pending` (`supervisor_client.py:400-436`) with the same gap-and-resubscribe path, so the daemon's ring never diverges from the supervisor's positions.
    - Apply `resized` to `RemotePtyHost.cols` and `rows`, and handle `removed`.
    - Reconnect after a lost supervisor connection instead of staying lost until the next daemon start (`supervisor_client.py:726-761`).
    - Degrade on "unknown message type" and on a missing capability, and log the degraded mode once.
  - **Done when:** tests run the daemon against both the new supervisor and a stub of the old one.

- [ ] **15. Stream-bound `since` cursors.**
  - **Why:** a `since` answered from a different stream silently corrupts the terminal, and nothing identifies a stream beyond the cold check.
  - **Change:** `replay_end` carries `stream`, the client sends its `since` together with that `stream`, and a mismatch always gets a full attach.
    In-process and cold sessions get a daemon-minted stream id.
  - **Where:** `routes/pty.py:251-289`, `session.py:2499-2553`, `frontend/src/TerminalPane.tsx:1237-1244,2347-2417`, `design/interfaces.md`.
  - **Done when:** a test proves a cursor from one stream is never answered with a delta from another.

- [ ] **16. Supervisor revision for updates.**
  - **Why:** installed copies replace the supervisor only on a protocol change, so items 12 and 13 would never reach them.
  - **Change:**
    - A hand-bumped `SUPERVISOR_REVISION` in `supervisor.py`, written to `supervisor.json` and `bundle.json`, with a drift test that fails when the content of `SUPERVISOR_SOURCES` changes without a bump.
      The drift test hashes file content only, because the build hash mixes in the build machine's package versions and never matches across machines.
    - When the revision differs and the protocol matches, the update plan reports that a supervisor update is available and ends every session; swap mode stays the default, and only a consented request installs in replace mode.
    - `swemux doctor` reports a running supervisor older than its bundle.
  - **Where:** `update_install.py:44-61,1618-1638`, `bundle_metadata.py`, `packaging/build_desktop.py:26-95`, `design/features/desktop-shell.md`, `RELEASING.md`.
  - **Done when:** the plan endpoint offers the optional replace against a fixture with a lower revision.

- [ ] **17. Run the supervisor update.**
  - **Why:** items 12-14 do nothing until the running supervisor is replaced, and its bundle has been stale since 2026-08-27.
  - **Change:** none in code.
    The operator schedules the reap and runs the documented sequence from a terminal outside swe-mux: `swemuxd --shutdown`, confirm no `swe-mux` or `swe-mux-supervisor` process remains, `packaging/build_desktop.py --supervisor-only`, relaunch.
  - **Done when:** `supervisor_bundle_current()` is true, the daemon reports the new capabilities, and a harness run with a stalled subscriber leaves the agents unthrottled.

- [ ] **18. Shared framed IPC module.**
  - **Why:** the worker and the gateway need the supervisor's framing without importing `supervisor.py`, which drags in the PTY stack.
  - **Change:** a stdlib-only `framed_ipc.py` with wire-identical framing (`supervisor.py:99-137`), a token `hello` with a deadline and a capability list, request ids, unsolicited frames, bounded inbound sizes, and a test asserting byte equality with `supervisor.encode_frame`.
    Frames stay small, because the receiving loop decodes every header as JSON.
  - **Done when:** the module and its tests exist and the supervisor is unchanged.

- [ ] **19. Worker host.**
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

- [ ] **20. Process inspection into the worker.**
  - **Why:** psutil holds the GIL on Windows, and the inspector walks about 1,200 processes per pass plus request-triggered extra passes (`processes.py:501-573,817-829,1300-1431,1558-1684`).
  - **Change:**
    - The worker runs the sampling pass, priority enforcement, the Codex process proofs (`codex_process_identity.py`), `win_process_files` queries and the ghost-window sweep.
    - The daemon pushes the inputs: session roots (pid and creation time), states and ids, the supervisor's job-pid map, infrastructure pids, and the priority target.
    - The worker returns snapshot deltas (owned rows, the listener index, infrastructure ports, system CPU), listener events and diagnostics.
    - `act()` and preview `register(force)` become RPCs with a timeout; request handlers read the latest snapshot instead of triggering a pass.
    - `process_evidence` writes to `mux.db` stay in the daemon, fed by the deltas, and `_fast_clear_background_annotations` stays in the daemon, driven by snapshots.
  - **Where:** `processes.py`, `ghost_windows.py`, `codex_process_identity.py`, `win_process_files.py`, `design/features/processes-and-previews.md`.
  - **Done when:** the Processes tab, previews, fleet intelligence and process evidence match the in-process behaviour in tests, and the harness shows the inspector's GIL share gone from the daemon.

- [ ] **21. Canonical telemetry into the worker.**
  - **Why:** the reducer, reconcile, rollups and seals run in daemon threads, OTLP bodies of up to 4 MiB are parsed on the loop, and `_dimensions` runs on the loop for every event (`telemetry_service.py:151-368`; `routes/agent_ingress.py:433-476`).
  - **Change:**
    - Move all three ledgers together (catalog, segments and the `LedgerShared` write lock) with their maintenance and queries.
      Splitting them across processes would reintroduce the lock-ordering deadlock `LedgerShared` exists to prevent (`telemetry_ledger.py:106-172`).
    - The daemon forwards the bus events the ledger consumes plus a session-dimension table whenever it changes, and forwards OTLP bodies as raw binary payloads without parsing them on the loop.
    - A bounded daemon-side buffer covers worker restarts, and `provider_dropped` keeps counting losses.
    - Usage queries and the `canonical_telemetry` diagnostics block are answered over RPC.
  - **Where:** `telemetry_service.py`, `telemetry_ledger.py`, `routes/agent_ingress.py`, `design/features/operational-telemetry.md`, `development/TELEMETRY_HARDENING.md`.
  - **Done when:** replaying a recorded event stream yields identical ledger rows in and out of process (`tools/telemetry_benchmark.py`, `tools/telemetry_ingest_latency.py`).

- [ ] **22. Transcript I/O into the worker, if item 4 still ranks it after item 6.**
  - **Why:** tailing every transcript reads and JSON-decodes in daemon threads and dispatches every record on the loop (`observation.py:946-1093,1206-1306`).
  - **Change:** the worker stats, reads and decodes, strips each record to the fields `_claude` and `_codex` consume, and streams compact records; the session state machine stays in the daemon.
    The hourly `reconcile_transcripts` moves with it (`operational_telemetry.py:749-815`).
  - **Done when:** the status-detection golden tests pass unchanged and the harness shows the gain; otherwise this item is removed with its measurement recorded.

- [x] **23. Event persistence write-behind, if item 4 ranks it.** Removed.
  - **Why removed:** item 4 measured event persistence at 0.6% of GIL samples, so write-behind would trade durability (a crash losing up to one flush window, sequence numbers from memory) for almost nothing on the GIL.
    The latency half of the original concern - a hook reply waiting behind its event's insert under the `mux.db` lock - is item 10's, which answers the reply before persisting rather than changing what is durable.

- [ ] **24. Extract the terminal core, in process.**
  - **Why:** the gateway must run the same protocol implementation as the daemon's fallback, and today that implementation is spread across `routes/pty.py` and `Session`.
  - **Change:**
    - A terminal core package owns the connection lifecycle and the 0.25 s attach handshake; replay, delta and resync with the ring and the replay-prefix parsers (`screen`, `bracketed_paste`, `sticky_modes`); `PtySubscriber` queues; `PtyOutputFlow`; input ownership and arbitration (`terminal_arbitration.py`); viewports and geometry; the repaint jiggle and the resize-settle scheduler; client-frame dispatch and diagnostic rate limits; and input accounting (`input_revision`, last-input age, the composer-estimate input).
    - The host supplies session descriptors (backend traits, state, created and cold flags, whether input is accepted), snapshot frames with revision and generation, the presence leader, a PTY sink and an output source.
    - The core reports input events, attach and detach, terminal mode, geometry, claim decisions, client diagnostics, repaint requests and metering back to the host.
    - `routes/pty.py` becomes an adapter over the core.
      `Session` keeps its existing attributes as views onto the core, so `delivery_readiness`, automation's attended checks and every other reader are untouched.
  - **Where:** a new `terminal_core` package, `routes/pty.py`, `session.py:2047-2053,2150-2786,2879-2906`, `design/features/terminal-input.md` (whose key-file list still names `server.py` for the socket handlers).
  - **Done when:** the full gate and `tests/test_pty_ws.py` pass with no behaviour change, and harness echo latency is unchanged.

- [ ] **25. Single-writer PTY input with conditional writes, in process.**
  - **Why:** once input arrives in another process, a delivery decided on the daemon's mirror can paste over a keystroke still in transit, and writes from two connections have no defined order.
  - **Change:**
    - Every PTY write and resize goes through the core's write API: browser input, the prompt queue, auto-delivery, voice, MCP interrupt, REST input, broadcast, approval keystrokes, spawn-stage paste, graceful end, repaint jiggles, and the two writers that skip accounting today (`meta_hooks.py:350-353`, `processes.py:2213-2215`).
    - Delivery writes carry a condition (`expect_input_revision`, `operator_quiet_seconds`, no pending submit) evaluated atomically with the write; a refusal sends the caller back to re-evaluate (`prompt_queue.py:2397-2450`; `delivery_readiness.py:532-535`).
    - Input times cross the API as ages, never as `time.monotonic()` values.
    - A guard test fails on any `pty.write` or `pty.resize` outside the core, in the style of the unsupervised-loop sweep in `tests/test_background_tasks.py`.
  - **Done when:** the guard is green and the delivery tests cover a refused condition.

- [ ] **26. Shared host and origin policy.**
  - **Why:** the gateway must enforce the daemon's browser policy without copying it, and a cross-port gateway origin fails the daemon's exact `Origin == Host` rule (`server.py:481-514`).
  - **Change:** `http_origin_policy.py` holds `allowed_browser_host` and origin matching, and the daemon uses it unchanged.
    The gateway accepts an `Origin` whose host is allowed and whose port is the daemon's port, its own port, or 443 for a `*.ts.net` page served through Tailscale Serve.
  - **Done when:** both processes use the module and tests cover each accepted and refused origin.

- [ ] **27. Gateway control protocol and the remote terminal hub.**
  - **Why:** the daemon must drive terminal cores in the gateway through the same interface it uses in process.
  - **Change:**
    - Typed messages over token-authenticated `framed_ipc`, with capability negotiation and a protocol version the daemon checks.
    - Daemon to gateway: session upserts and removals (descriptors), snapshot frames with revision and generation, the presence leader, broadcast membership, replay and flow configuration, conditional writes and repaints with request ids, and diagnostic queries.
    - Gateway to daemon: attach and detach with counts and device, coalesced per-session input summaries carrying the authoritative `input_revision`, the last-input age and what the daemon's side effects need (submit, interrupt intent, pointer kind, composer text), terminal mode, geometry, claim decisions, client diagnostics, repaint requests, metering counters, and write results.
    - Input summaries are coalesced per session rather than queued, so a stalled daemon costs memory bounded by the session count, and a reconnect resyncs the full input state.
    - The daemon's `Session` views read the mirror when the hub is remote.
  - **Where:** `terminal_core` (the remote hub), `design/interfaces.md`.
  - **Done when:** the core's test suite runs against both the local and the remote hub.

- [ ] **28. The terminal gateway process.**
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

- [ ] **29. Daemon-owned gateway lifecycle.**
  - **Why:** the gateway must exist wherever the daemon runs, including source mode, standalone daemons, and Linux and macOS, which have no tray.
  - **Change:** discover-or-spawn at startup after the supervisor connects; an orchestrated gateway restart when its build id differs, with clients told to reconnect; respawn on death with capped backoff; left running on detach restarts; stopped on quit.
    `daemon-recovery.json` records its identity, and desktop recovery never kills it.
    `/api/health` reports `terminal_transport` (`gateway` or `daemon`, the port, and the state), and `swemux doctor` checks it.
  - **Where:** `server.py`, `daemon_recovery.py`, `lifecycle.py`, `doctor_local.py`, `design/features/daemon-resilience.md`.
  - **Done when:** tests cover spawn, rediscovery after a daemon restart, the version-skew restart, and quit.

- [ ] **30. Frontend transport selection and fallback.**
  - **Why:** the browser must reach the gateway on every access path and fall back without user action.
  - **Change:**
    - The `/pty` URL rule in `api.ts`: an `https:` page (Tailscale Serve) uses same-origin `/pty/`, which the Serve mount routes to the gateway; an `http:` page uses `ws://<hostname>:<terminal_gateway_port>/pty/<sid>`; and when the gateway is down, refuses the session, or `terminal_transport` says `daemon`, the page uses same-origin `/pty/` on the daemon.
    - The gateway port and state come from `/api/health`, which is already polled, and are cached for reconnects while the daemon is unreachable.
    - Close codes distinguish "use the daemon for this session" (in-process and cold sessions) from "gateway restarting".
    - Vite proxies `/pty` to the gateway in development (`frontend/vite.config.ts:74`), and `demo/fakeSocket.ts` keeps its `^/pty/` match.
  - **Where:** `frontend/src/api.ts`, `frontend/src/TerminalPane.tsx`, `frontend/src/liveness.ts`, `technical/frontend/packages.md`.
  - **Done when:** unit tests cover the URL rule and a renderer test covers fallback and reconnect.

- [ ] **31. Tailscale Serve mount and firewall.**
  - **Why:** an `https:` phone page cannot open `ws:` to another port, and direct tailnet HTTP needs the gateway port reachable.
  - **Change:**
    - **Settle first:** confirm on this host how `tailscale serve --set-path=/pty` maps the mount path onto the target; Serve stores handlers keyed by path under each host and port.
    - `tailscale.py` adds and reconciles the `/pty` mount beside the root mapping; `_loopback_target_port`, which takes the first loopback port it finds (`tailscale.py:125-144`), and the reclaim logic (`tailscale.py:583-652`) learn about both handlers; boot reconfiguration keeps both.
    - A Windows firewall rule for the gateway port (`windows_firewall.py:331-348`).
  - **Where:** `tailscale.py`, `windows_firewall.py`, `design/features/remote-access.md`.
  - **Done when:** the phone reaches a terminal through the gateway both over Serve and over direct tailnet HTTP.

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
