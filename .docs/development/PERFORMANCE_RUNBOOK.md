# Performance runbook

The procedure for investigating "swe-mux feels slow" or "the daemon is using more of this
machine than it should".
It answers questions of the form: *"the fans are on and nothing is obviously happening — what is
the daemon doing, and is any of it waste?"*

Background reading: `technical/backend/packages.md` (the psutil, directory-walk, and PTY-reader
rules, each written from a measured incident).
This runbook is the operational half: which numbers to read, in which order, and how to tell a
cost from a defect.

## Why this exists

swe-mux was optimized once by inspection, carefully, and a defect costing 45% of the daemon's CPU
survived inside the file that audit had just rewritten.
It survived because the only cost signal available was iteration counts, and the loop responsible
ticked once every ~6.5 seconds — the second-least frequent loop in the daemon.
Reading is not measuring.
Start with the numbers.

## The short version

```
uv run python tools/perf_snapshot.py                  # loops, lag, process cost
uv run python tools/perf_snapshot.py --profile 45     # also sample the daemon
uv run python tools/perf_snapshot.py --json before.json
```

`tools/perf_snapshot.py` runs steps 1 through 4 in order and is read-only.
Use `--json` on both sides of a change so a before/after is a diff rather than a memory.
The rest of this document is what the tool is doing and how to read it.

## Measuring mobile data use

Start a clean daemon-local measurement window, use the mobile client normally, then read the
result:

```text
DELETE /api/diagnostics/network
GET /api/diagnostics/network
```

The snapshot groups HTTP counts by normalized route and peer, WebSocket counts by channel and peer, and sent PTY frames by peer and payload phase in `websocket_sent_payloads`.
HTTP response bytes are the encoded body after negotiated compression.
WebSocket bytes are application text/binary frame payloads before per-message compression.
Neither figure includes HTTP or WebSocket headers, TLS, Tailscale, TCP/IP, retransmits, or radio
overhead, so use a browser network export or OS packet capture when the carrier-billed wire total
is required.
The DELETE response includes the previous snapshot, writes its aggregate totals to the rotating
daemon log, and resets only the counters.
It does not restart the daemon or affect sessions.
Both diagnostics requests are excluded from the measurement window.

For a useful mobile comparison, measure the same scripted interval with one foreground client, then with the browser backgrounded, and compare `http_routes`, `websocket_channels`, and `websocket_sent_payloads` rather than only the aggregate.
Use the mobile peer's `attach_replay` and `resync_replay` rows to separate session switching and recovery cost from `live_output` steady-state traffic.
The classified rows are part of the WebSocket download count, not extra bytes to add to it.
Large one-time static and PTY replay transfers should be separated from steady-state idle use.
On mobile, only visible terminals should appear in `websocket_channels.pty`; hidden desktop-style
warm panes indicate a regression.
Cold `/events` connections should send a small `events_ready` watermark rather than retained
history, and reconnect replay should never exceed 64 events.
The browser stream intentionally omits the durable `PreToolUse`, `PostToolUse`, `tool_use`, and
`tool_result` audit payloads.

```
uv run python tools/pty_latency_bench.py --samples 25 --idle-gap 6
```

`tools/pty_latency_bench.py` measures the other half: keystroke to echo, end to end,
across websocket, daemon, supervisor, ConPTY and the child.
No component's own metrics report that number, and it is the one a user means by "snappy".
Run it against a quiet daemon; a working agent's output makes every reading noise.
Vary `--idle-gap` to cross the PTY reader's poll rungs
(`pty_host.read_poll_interval`) - after the reader's wake was made interruptible, all
three rungs read the same, and a gap-dependent result means that wake has regressed.

Reference, measured 2026-08-05 on a quiet fleet: **p50 ~2.4 ms, p95 ~3.6 ms, max ~6.4 ms,
independent of idle gap** (40 samples).
Two regressions to look for: a p50 that tracks `--idle-gap` means the reader's wake has
broken, and a p50 jumping to ~16 ms means
`timer_resolution.raise_timer_resolution()` is not taking effect and every wait has fallen
back to the OS timer tick.

## Order of investigation

### 1. Establish what the daemon costs at rest

```
Get-Process swe-mux, swe-mux-supervisor -ErrorAction SilentlyContinue |
  Select-Object Id, ProcessName,
    @{n='WS_MB';e={[math]::Round($_.WorkingSet64/1MB,1)}},
    @{n='CPU_s';e={[math]::Round($_.CPU,1)}}
```

Cumulative `CPU_s` answers "since boot"; it does not answer "right now".
For a rate, sample `psutil`'s `cpu_percent` over a window of 20 seconds or more.

### Reference baselines

Measured 2026-08-05 after the fixes of that day, on one machine, so treat them as shape
rather than as thresholds.
Capture your own with `--json` and compare against it; that is what the flag is for.

| fleet | daemon | supervisor | WebView | loop-lag p50 | keystroke p50 |
| --- | --- | --- | --- | --- | --- |
| 0 sessions (idle) | **0.10%** of a core | 0.00% | 0.20% | 1.99 ms | 2.35 ms |
| 10 sessions, ~340 MB of transcripts, all idle | **1.50%** of a core | 0.00% | 0.60% | 0.70 ms | 3.09 ms |

The loaded fleet was 3 Codex (174 / 35 / 15 MB rollouts), 4 Claude (74 / 57 / 30 / 26 MB
transcripts) and 3 shells, resumed and never prompted, so the figure is what swe-mux costs to
*carry* a fleet rather than what an agent costs to run.
Keystroke latency was unchanged from idle, which is the property that matters most: fleet size
does not reach the interactive path.

For scale, the same daemon held 22.6% of a core before that day's fixes.
A daemon materially above these with an idle fleet is worth investigating.

A larger fleet measured 2026-08-05: 12 sessions carrying ~2.8 GB of transcripts (Codex
rollouts up to 525 MB, resumed in parallel in ~0.8 s of wall per request batch) cost the
daemon 3.30% of a core, supervisor 0.10%, keystroke p50 2.36 ms — unchanged from idle,
which remains the property that matters: fleet size does not reach the interactive path.

### Frontend reference numbers (measured 2026-08-05, 2x2 grid, 12-session fleet)

Method: drive the real UI with Playwright against the live daemon (or `npm run dev` on
port 5173, which proxies `/api`, `/pty`, `/events` to it), wrap `WebSocket` from an init
script to timestamp frames per socket, sample `requestAnimationFrame` deltas for jank, and
dispatch wheel/pointer input through CDP. One trap: an awaited CDP `mouse.wheel` costs
~30 ms per call, so a "flick" loop written that way is actually a slow scroll — dispatch
synthetic `WheelEvent`s in-page when the notch rate is the variable under test.

- Codex scroll (xterm-local, `alternate_screen=never`): every frame on the vsync tick,
  zero PTY traffic. There is nothing to optimize on this path.
- Claude scroll (app-owned mouse, PTY round-trip per notch): round-trip p50 ~5 ms at
  human rates, every frame on the vsync tick, single pane or 2x2 grid alike.
- A 400-notch flick: ≤ ~300 ms of scroll tail after the gesture ends (the wheel pacer's
  queue draining). Before the pacer existed the tail was 4-12 seconds.
- Continuous splitter drag: one pseudoconsole resize per `VIEWPORT_SETTLE_MAX_MS` (600 ms)
  per visible pane. ~22/s per pane means the resize-cost charge or the geometry burst
  classification has regressed (`terminal-input.md` §Geometry).
- Window-resize sweep and warm-tab switching: zero frames over 25 ms.

For a terminal that accepts keystrokes but displays them several seconds later, check both queue boundaries.
The events WebSocket must not deliver audit-only `tool_use`, `tool_result`, `PreToolUse`, or `PostToolUse` payloads to browsers.
Observation-only browser events such as `project_files_changed` must not refetch the full sessions/projects/previews/groups/harnesses snapshot.
`GET /api/sessions` uses a one-second display-only PTY classification cache; authorization checks never use that cache.
The PTY WebSocket should advertise `output_flow_control` and emit `output_ack` frames from xterm write callbacks.
Durable `terminal_client_repair` events with phase `write_pipeline_backlog` mean live parsing exceeded 32 KiB for at least 750 ms, while `write_pipeline_dead` still means parse progress stopped entirely.

### 2. Ask which loop is expensive, not which is frequent

```
GET /api/diagnostics/background
```

Read `costliest` first.
It ranks by `busy_share`, the fraction of a loop's life spent inside its own iteration bodies,
which is the metric that places an expensive rare loop above a cheap frequent one.
`iterations` alone is actively misleading and is the reason the original defect was invisible.

Per loop, `p95_seconds` and `slowest_seconds` separate "always somewhat costly" from "usually
free, occasionally terrible".
The second shape is the one that produces user-visible stutter.

**`busy_seconds` is wall time with awaits included, not CPU and not proof of blocking.**
A loop that awaits inside its `iteration()` guard reports that wait as its own cost even though
the event loop was free throughout.
The first live reading of this endpoint put `status_timeline._flush_loop` at the top of
`costliest` with a 1.02 s p95 for exactly that reason: its batching sleep sat inside the guard.
Before treating a high `busy_share` as a defect, read the loop and check whether the guard wraps
work or waiting, then confirm against `loop_lag` in the next step.

### 3. Ask whether anything is blocking the loop

Same endpoint, `loop_lag`.

Everything on the event loop shares one thread, so a single synchronous call delays every
terminal write, websocket frame, and HTTP response behind it by its full duration.
No per-subsystem metric reports that; only the loop can.

- `p50_seconds` on a healthy daemon is on the order of a millisecond.
- `stalls` counts samples at or beyond 100 ms. A non-zero and climbing count means something
  synchronous is running on the loop.
- `worst_seconds` survives the sampling window, so a stall from ten minutes ago is still
  reportable.

A stall count that climbs at a steady rate points at a periodic loop; correlate with `costliest`.

Then read `stall_watchdog.recent` on the same endpoint, or `grep loop_stall daemon.log`, for
every stall of three seconds or more.
The lag number says how long the loop was gone; the Python sampler may show where it was in `<data_dir>/loop-stalls.log`.

Read the `verdict` first, because it decides what the frames mean (`design/features/daemon-resilience.md` § What kind of stall it was):

- `loop_blocking_call`: the loop thread itself ran; its frames (`main=`) are the culprit, as in the 2026-09-01 cases below.
- `loop_saturated`: the loop ran and its ready queue was deep; look at what floods it (`costliest`, request mix in `access.log`) rather than at one frame.
- `gil_convoy`: the loop barely ran while `top=` threads burned CPU; `gil=` names the holder, and the loop's own frames are innocent syscalls.
  Move the top thread's work out of the process or make it yield, not the loop's.
- `host_starved`: nothing in the daemon ran and the host was saturated; the fix is outside the daemon (priority, fleet size), and its frames say nothing.
- `blocked_wait`: nothing ran and the host had headroom; the loop waited on I/O, a lock, paging, or a native call that held the GIL, so check `db_lock_waiters`, memory, and the main frames together.
- `unsampled`: no CPU samples covered the window (a watchdog younger than the stall, or a host without per-thread CPU); fall back to the frames below.

`gil=name(py-spy)` is a measurement; `gil=name(cpu)` is the top CPU thread, an inference.
For `py-spy` to run, it must be on `PATH` or named by `MUX_STALL_PROFILER`; `swemux doctor` says whether it is (`background.stall_profiler`).
`busy=[...]` lists only threads that ran during the stall, each with its CPU seconds; `blocked=` counts unparked threads that did not, and parked threads are never listed.

Read `capture_mode` and `canary_starved` before interpreting frames.
`capture_mode=python_gil_required` means a native GIL-held stall can prevent sampling altogether.
`canary_starved=false` means another Python thread ran during the stall; available main-thread frames can identify synchronous work on the event loop.
`canary_starved=true` means native GIL contention or host scheduling also delayed the sampler; absent frames do not identify a culprit.
Explained stalls persist in `mux.db` (`loop_stalls`), while a daemon crash before explanation leaves OS crash evidence and any partial trace.
For an outage that persists, inspect `daemon_recovery` state transitions in `lifecycle.log` and the generation record in `daemon-recovery.json`.
Desktop automatic recovery and its session-protection gates are defined in `design/features/daemon-resilience.md`.
The original asynchronous native sampler is no longer used: it crashed Python while traversing a changing frame during the 2026-09-07 incident.

The 2026-09-01 freezes this was built for - 46 s and 53 s within three minutes while three
worktree agents ran cargo concurrently - left no trace anywhere: `daemon.log` was silent for the
duration, a profiler attached afterwards saw a healthy daemon, and every loop's `slowest_seconds`
read the same 51 s because they had all measured the same stall from inside it.
`loop_lag` could say 49.8 s and nothing could say why.
Twelve minutes after the watchdog shipped it named the path: a 6.6 s stall with the main thread in
`read_bytes` under `read_project_config`, called once per registered Project by every
`GET /api/projects` poll, on the loop thread - a small file on a disk saturated by three
concurrent cargo builds.
`canary_starved` was false, which is what said "move this call off the loop" rather than "look
in a worker".
That read now runs in a thread.
The next morning it named two more of the same shape, twenty stalls between them: the health
endpoint's `stat` of the served `index.html` (up to 15 s, thirteen times) and asyncio's
synchronous `CreateProcess` under the git monitor's `run_bounded` (23.5 s, six times), both with
the canary running, both during a `cargo test`.
The first is now answered from a cached reading, the second runs on the spawn loop
(`runtime-rules.md`).

**A daemon that has been up for a while, on a busy fleet, has costs a fresh idle one does not.**
On 2026-09-24 a daemon up for ten days (2 GB private, input "slightly laggy here and there", `daemon_recovery state=unresponsive` every few minutes in `lifecycle.log`) had no hung process anywhere; its loop was busy.
Every cause was a per-item cost multiplied by something that grows with uptime or with fleet activity, which is why a restart hid it and a quiet fleet never showed it:

- **The process inspector's evidence mirror.**
  It rewrote its whole retained set into `process_evidence` every ten seconds, re-serializing each row through `dataclasses.asdict` on the loop: 7,484 retained processes, 7,377 of them exited.
  Exited rows are kept for `ENDED_RETENTION_SECONDS` (a day), so the cost grew over the first day of uptime and then held there.
  It was about three quarters of the loop's busy time, in multi-second blocks, and is now changed-rows-only and built in a worker thread (`design/features/processes-and-previews.md` § Sampling cost).
- **The plugin event dispatcher's dedupe.**
  With no plugin enabled it still ran for every event, and its age-pruned dict rebuilt itself on every event once more than 2,000 had arrived inside an hour - so at ~30 events a second it cost O(events in the last hour) per event.
  It was about half of the event loop's GIL time in a `py-spy --gil` profile.
  The dispatcher now returns before any per-event work when nothing is enabled, and the window is count-bounded and O(1) (`design/features/plugins.md` § Bounds and diagnostics).
- **The desktop's fleet refresh.**
  Every event outside a short list queued a five-endpoint fleet refresh that started the moment the previous one ended, so a subagent fleet's ~17 `subagent_activity` events a second held one window at 2.5 full refreshes a second, back to back.
  The frontend now spaces event-driven refreshes by at least a second, and by twice the last cycle's duration when the daemon is slow (`technical/frontend/packages/composition.md` § Fleet refresh).
  Two of those five reads were themselves expensive per call and are cheaper now: `/api/harnesses` reuses a detection up to ten seconds old (`design/features/backends.md`), and `/api/projects` stats Project roots in one worker call instead of one `is_dir` per Project on the loop.
- **The Git monitor and the fleet refresh, again** (2026-10-12).
  The monitor re-read every attached checkout every five seconds, about seven `git` processes each, whether or not anything had moved: `busy_share` 0.34 and a 9.9 s median iteration on the operator's fleet.
  It now reads a checkout when an OS watch reports a change, with a 60 s sweep while attached and 300 s while detached, and keeps timed polling for any checkout without a live watch (`design/features/git.md`).
  Its `git_monitor` block on `GET /api/diagnostics/background` says why each read happened: `reads` by cause, plus the watches' state under `watch`.
  A healthy idle fleet reads almost only `sweep`; a climbing `poll` count means watches are not running (`watch.failures`, `watch.last_failure`, `git_watch_failed` in `daemon.log`), and `git_read_slow` names a read that took five seconds or more.
  On the browser side, 645 events an hour still asked each desktop window for a five-endpoint fleet read (about 9 a minute per endpoint), and two thirds of them changed exactly one session's row.
  `harness_status` and `git_changed` now carry that row's new values and are applied in place, every other one-row event re-reads that row alone, and only membership, Preview, Project-level and unknown events re-read the fleet (`technical/frontend/packages/composition.md` § Fleet refresh).
  From the same 24-hour event mix, fleet reads should fall from about 535 an hour per window to the 60-second safety interval plus about 20 structural events an hour; about 270 events an hour now patch a row with no request at all, and about 410 one-row events become at most about 200 single-row reads an hour, because each row is read at most once a second.
  To check it on a live daemon, count `GET /api/sessions` against `GET /api/sessions/<id>` per minute in `access.log`.
- **Filesystem calls on the loop**, each small until a disk or a provider is slow: `project_watcher` resolving watch targets (`realpath`/`stat`, now `asyncio.to_thread`, with `register_async` for the route), the observation loop's `exists`/`stat`, the state watchdog resolving every session's cwd (`CliStateMonitor.poll` now resolves them in its worker thread), and the legacy meta-hooks engine re-reading its rules file, which now runs in a thread and reloads at most every `RELOAD_INTERVAL_SECONDS` (2s) however many events arrive.

The sibling symptom was brief refusals rather than lag: a client that times out against a busy loop leaves a failed accept, and asyncio's proactor closed the listener for it (`design/features/daemon-resilience.md` § Listener guard).
When a long-lived daemon feels slow, read the request rate per path in `access.log` and the event rate per type in `mux.db` (`events`) before profiling: a cost that is cheap per call and wrong per event is invisible in any single request and obvious in the counts.
Then take a `py-spy record --gil --threads` profile, which shows only the thread holding the GIL and so names what is actually starving the loop.

**Disk that is busy with nothing visible running: read the canonical telemetry block.**
On 2026-10-12 the canonical ledger was reading about 330 GiB a day out of its own files, none of it on the loop, so no loop metric showed it.
A month that kept receiving late entity evidence was resealed after every write that unsealed it - a `quick_check` and a SHA-256 of a 6 GiB segment, 27 times that day - and the writes came from the reconcilers re-inserting the 32 MiB tail of every growing transcript as new evidence on every pass (`development/TELEMETRY_HARDENING.md` § Seal loop and full re-reads).
The same thread served live ingestion, which dropped 710 observations while it waited.
`GET /api/diagnostics/background` → `canonical_telemetry` now answers it directly:

- `seals` and `seal_invalidations` over the daemon's life, and `last_seal_seconds`; more than a handful of seals a week, or invalidations climbing with them, is the loop back.
- `rejected_observations`, observations refused for a time before 2020 or more than two days ahead; non-zero names a producer with a broken clock, whose type and source are in the catalog's `rejected_observations` table and in the WARNING that logged the first.
- `reconciliation.elapsed_s` for the last canonical pass; an incremental pass reads five minutes of appends, so seconds here mean something was read again from the start.
- `provider_dropped`, live observations lost to a full ingest queue.

The log says the same per event: `canonical telemetry segment ... sealed`/`unsealed: <reason>` at INFO, a seal of 30 seconds or more and a reconciliation pass of 30 seconds or more at WARNING, and `canonical telemetry re-read <run> from the start (<reason>)` when a transcript was replaced.

### 4. Profile, if the first three did not name the culprit

`py-spy` attaches to the frozen daemon.
It does not need a source checkout or a debug build.

```
uvx py-spy dump --pid <daemon-pid> --nonblocking
uvx py-spy record --pid <daemon-pid> --duration 45 --rate 120 --nonblocking \
  --format raw --output profile.txt
```

`--nonblocking` is not optional against a live daemon: without it py-spy pauses the process to
read its stacks, which is a stall inflicted on every attached terminal.
It costs some dropped samples, which does not matter for proportions.

Find the daemon's pid from the listening socket rather than by name, since the tray shell is also
`swe-mux.exe`:

```
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  Select-Object -First 1 -ExpandProperty OwningProcess
```

The raw format is collapsed stacks, one line per unique stack with a sample count.
Aggregate by leaf frame to find where time is spent, and by outermost non-stdlib frame to find
which subsystem is responsible.
Samples inside `_poll (windows_events.py)` are the loop idle and should dominate a healthy
profile.

**A sampled profile shows where threads are, not only where CPU is.**
A thread parked in a blocking read attributes samples to that frame while burning nothing.
Cross-check any leaf that looks expensive against the process-level CPU from step 1 before
believing it.
The PTY supervisor reads this way: 89% of its samples sit in `_read`, on 0.7% actual CPU.

### 5. Confirm the fix by re-measuring, not by reasoning

Re-run step 4 and compare proportions.
A structural fix removes a frame rather than shrinking it — after the `ppid` fix that leaf was
absent, and `processes.py` fell from 50.1% of samples to 9.0%.

Process-level CPU is the weaker check, because fleet size and agent activity move between runs
and are not controlled.
Say so when reporting a before/after number taken that way.
The controlled check is the load harness.

## Load harness

`tools/fleet_load_harness.py` is the controlled before/after for anything that should change how
the daemon behaves under a fleet.
It starts its own daemon from the checkout on an OS-allocated port, with its data directory and a
re-homed `HOME` under a scratch directory, and scrubs `MUX_*`, `SWE_MUX_*`, `CLAUDE*` and `CODEX_*`
from that daemon's environment, so it never touches 8765, `~/.mux` or the operator's conversations.
That makes it safe from a worktree for the same reason the `live_daemon` test tier is.

```
uv run python tools/fleet_load_harness.py --sessions 36 --duration 120 --json before.json
uv run python tools/fleet_load_harness.py --sessions 36 --burn 32 --profile 45
uv run python tools/fleet_load_harness.py --set gil_switch_interval_ms=1
```

- Each scratch session runs `tools/load_session_generator.py`, which promotes its pane to a Claude
  session the way the agent launcher does, appends a transcript, writes terminal output (`scroll` or
  `redraw`), posts `PostToolUse` hooks and OTLP batches through the session's own credentials, and
  starts child processes.
- One acknowledging client per session, one timed probe session, one `/events` client reporting
  desktop presence, and the open window's polls make it a daemon somebody has open; without the
  `/events` client the readiness watcher skips its pass and the run measures an empty room.
- `--burn N` adds below-normal CPU-bound processes for agents' builds. A saturated host is what turns
  sub-second lag into multi-second stalls, and it is the only way this harness reproduces them.
- `--stall-profiler PATH` hands the scratch daemon a `py-spy` executable through
  `MUX_STALL_PROFILER` (which the environment scrub would otherwise remove), so its stalls carry a
  measured GIL holder; the report prints each stall's verdict, CPU split and holder.
- `--stall-threshold SECONDS` lowers the scratch daemon's capture threshold through
  `MUX_STALL_THRESHOLD_SECONDS`. On this host the incident shape rarely crosses three seconds while
  echo p99 sits above two, so classifying the stalls a run actually produces needs about one.
- The report carries keystroke echo, the window's request latencies, hook and OTLP round trips,
  `loop_stall` records from the scratch `daemon.log`, loop-lag deltas, per-loop busy time in the
  window, and, with `--profile`, a GIL-only profile attributed to threads and frames.
- Artifacts stay in the printed work directory; the scratch daemon is quit through its
  authenticated shutdown route, and any scratch supervisor is reaped by the identity-checked
  `kill_server`.

Read the baseline in `development/DAEMON_LOAD_ISOLATION.md` item 1 before comparing a number.
The host is shared with the live fleet, so compare runs taken close together, and read p95, p99 and
stall counts rather than p50, which a quiet minute on the host moves.

## Startup latency

A distinct question from "the running daemon is slow", with its own measurement and its own
failure mode.

**The listener no longer waits for the runtime.** `runtime_context` returns immediately and the
runtime is built by a background task behind an already-open socket, so a slow start is a
reachable daemon reporting progress rather than minutes of refused connections. Until the build
finishes, `/api/health` answers HTTP 503 carrying the phase in flight, and every other route is
refused with the same body.
That is a change of what a slow start *costs*, not of how long it takes: the total is still worth
watching, and the phases are what you read it from.

Read the summary from `<data_dir>/daemon.log`:

```
daemon runtime ready in 8.4s (8 live session(s)); serving every route
```

INFO under `SLOW_STARTUP_SECONDS` (20s), WARNING above it. Then read where the time went, from the
per-phase lines that precede it:

```
startup_phase name=static-precompress elapsed=0.03s total=0.0s
startup_phase name=predecessor-drain elapsed=0.00s total=0.0s
startup_phase name=database-integrity elapsed=11.52s total=11.5s
startup_phase name=stores elapsed=0.31s total=11.8s
startup_phase name=supervisor-connect elapsed=0.04s total=11.9s
startup_phase name=session-reattach elapsed=2.41s total=14.3s
```

`predecessor-drain` is the wait for the previous daemon's process to exit after a restart, up to
60s, and is zero on a cold start. It is a phase rather than a pre-bind sleep (which is what it was
until 2026-09-14) so that it is reported and so that it runs on *every* start: the tray's restart
path spawns without `--relaunch-wait`, and that successor opened its stores against a predecessor
that was still writing.

`stores` opens every store in a worker thread and logs `store_opened name=... elapsed_s=...` at
WARNING for any open over a second. **A `stores` phase over a second is a defect to name, not a
size to accept.** The 2026-09-14 incident below is what an unnamed one costs.

`static-precompress` is first and is normally the cheapest thing on the path: it is the phase that
makes the gzip sidecars the static tree is served from, and a tree that already has current ones
costs a stat-and-CRC pass (measured 0.03s over 40 files, 16.2 MiB). It is expensive **exactly
once** - 2.02s on the first start after an install or an upgrade, because a wheel deliberately
carries no sidecars (they were 35% of the download; `design/features/remote-access.md`). If you see
seconds here on a start that is not the first after an update, the tree is being rewritten every
time, which means the CRC comparison is failing rather than that compression is slow.

A phase at or above 10s is a WARNING; a phase still *running* after 15s is reported by the
timeline's watchdog (`startup_phase_running name=... elapsed=...`) and again every 15s after that.
That second line is the one that matters most, and the reason the completion line alone is not
enough — see the incident below. The same transitions go to `lifecycle.log`, and the live phase is
readable over HTTP without either file.

Reference measurements:

| when | fleet | `mux.db` | total |
| --- | --- | --- | --- |
| 2026-08-06 warm | 6-8 sessions | 879 MB | ~8s |
| 2026-08-06 post-redeploy (cold page cache) | 6-8 sessions | 879 MB | 27-31s |
| 2026-08-21, before this work | 30 sessions | 2.73 GB | 135-227s |
| 2026-08-30 post-reboot, before the deferral | 1 session | 3.36 GB | 85.7s (77.7s of it `database-integrity`) |

**The 2026-08-21 incident, because its shape recurs.** A 226.6s start was ~170s of two stretches
that logged *nothing at all*: 98.6s between the predecessor-death warning and
`PTY supervisor connected`, and 49.0s between session reattachment and the first
process-ownership diagnostic. The silence was itself the bug — a healthy-but-slow deploy was
indistinguishable from a hung one, which is how a 300s health ceiling came to roll back a good
bundle.

What the two stretches actually were, measured rather than guessed:

- `PRAGMA quick_check`, run by `connect_or_quarantine` once per **store**, at 11.5s per pass
  against the 2.73 GB `mux.db` — and eleven stores share that file, so ~126s. It became
  once-per-file, then conditional, and since 2026-08-30 it is **off this path entirely**:
  `database-integrity` is now the milliseconds header-and-schema probe plus the decision to
  schedule the full walk behind the ready daemon. **A `database-integrity` phase over a second
  is now a defect on every start, with no exceptions** — that is the whole point of the
  paragraph below, and the exception list the previous version of this line carried is what let
  a 77.7s phase read as expected.
- A full psutil sweep in `ProcessInspector.restore()`: 20.7s cold, 6.0s warm, over 482 processes.
  Deferred to a background task.

Everything else on the path was cheap and had simply never been separated from those two. The
whole of SQLite was exonerated by measurement: every unconditional per-boot migration scan in
`history.py` came in under 10ms, and the 2.73 GB is large tables nothing reads at boot rather than
work being done. **Measure before moving anything here** — the obvious suspect (a multi-gigabyte
database) was innocent, and the actual cost was an integrity check nobody had ever timed because
it logs nothing when it passes.

**The 2026-08-30 reboot, because it is the failure the first fix left standing.** The conditional
check above kept one blocking trigger — "the previous daemon died uncleanly" — and a reboot is
exactly that, so the first reboot after it shipped produced an 85.7s start that was 77.67s of
`database-integrity` and 8s of everything else. Three things are worth taking from it:

- **A signal that correlates with nothing is not a cheap safety net, it is a cost.** `mux.db` runs
  `journal_mode=wal` with `synchronous=FULL`, under which a process kill and an OS crash both
  leave the file consistent by SQLite's own atomic-commit contract. So the trigger was spending
  77.7s of the operator's attention answering a question the storage layer had already answered.
  The check now runs behind the ready daemon and the unclean-death signal moves it to the front
  of that queue instead (`technical/backend/sqlite.md`).
- **Cold and warm differ by 6.7x here, and the gap was the access pattern rather than the disk.**
  The same check was 11.55s warm and 77.67s cold: an effective 43 MB/s on an NVMe SSD rated in
  gigabytes per second, because `quick_check` walks 4 KiB pages one at a time through a 2 MB
  cache with no readahead. Reading the file once sequentially first (`prefetch_database`) is what
  closes it. **When a number is two orders of magnitude off the hardware, measure the pattern
  before believing the device.**
- **A phase's own log line is not enough to notice this.** The 77.67s line was an INFO with a
  reason attached and read as working-as-intended, because the doc above said it was. What
  actually surfaced it was the operator saying the app took minutes to start.

**The 2026-09-14 restart storm, because it is the failure the second fix left standing.** Every
start's `stores` phase took 60-76s and the total of the phase was the only line that said so. The
cost was one statement: `OperationalTelemetryStore._repair_duplicate_process_ownership`, a
correlated `EXISTS` self-join on `process_evidence` by `(pid, creation_time)` with no index over
those columns, run on every open. Against 493,845 rows (a month of process evidence) the predicate
measured **69.6s read-only**; with a partial index over the ~500 live rows it is 0.001s. Three
things followed from those 70s that were worse than the 70s:

- The store was constructed *on the loop* (`HistoryIndex.__init__` blocks on its worker's
  connect), so health could not answer 503 and the phase watchdog could not print
  `startup_phase_running` - the mechanism above was defeated by the exact shape it was built for.
- The listener was already open. The tray's probes connected, timed out and reset, and when the
  loop resumed the first accept completion failed with `WinError 64`, on which asyncio's Windows
  proactor **closes the listening socket for good**. The daemon then ran healthy on its Tailscale
  address and unreachable on loopback (`listener_guard.py` is the answer).
- The statement holds the writer slot for its whole run, so on a restart every write the
  predecessor attempted during its drain came back `database is locked`.

Three rules it adds. **A per-open repair is a per-start cost and is timed as one**
(`process_ownership_repair elapsed_s=` at WARNING over a second). **A statement that joins a
table to itself needs the index that join uses, pinned by a query-plan test**
(`test_the_startup_ownership_repair_is_served_by_the_live_fingerprint_index`), because the table
only grows and the scan is invisible until it is not. And **the first two fixes were necessary
and were not sufficient**: the integrity check moved off the path on 2026-08-30 and the phase
reporting exists, and neither could see a cost that blocked the loop that reports.

Four rules this path earns:

- **Nothing may run unlogged for minutes.** A phase is named and timed, and a phase still running
  is reported while it runs. Completion lines alone would have reproduced the exact failure above,
  because both silent stretches were work still in flight.
- **Nothing whose answer is not needed to serve the first request may run here.** Not "is it
  fast", not "is it justified" — is it needed *now*. Verifying every page of a multi-gigabyte
  file is the case that taught it, and the tell is that the work answers a question about the
  machine rather than about this start. Housekeeping was already covered by the rule below; this
  one covers work that is genuinely important and still does not belong in front of a person.
- **Housekeeping must never gate readiness.**
  Retention prunes are scans whose cost tracks database size and cache state, and they used to run
  on this path for four stores.
  Every one of them was already covered by a supervised background loop that reruns it hourly,
  so the startup copies bought nothing.
  Anything that is not needed to answer the first request belongs in a loop.
  What may *not* move is recorded at each site in `server.py`: a reconcile that hides false data
  from the first request, and any `restore()` whose loop starts immediately after it, stay put.
- **Blocking work on this path must go off the loop.** The staged health answer and the phase
  watchdog both need the event loop, so a phase that blocks it is invisible in exactly the way
  this whole mechanism exists to prevent. The integrity probe runs in `asyncio.to_thread` for
  that reason and not merely for throughput.

## Known-cost frames

Frames expected in a healthy profile, so they are not mistaken for defects:

| frame | what it is |
| --- | --- |
| `_poll (windows_events.py)` | the event loop idle; should dominate |
| `append (scrollback.py)` | PTY output entering the retention ring; scales with real output |
| `_fanout (session.py)` | delivering output to attached clients; scales with panes |
| `_refresh_tree (processes.py)` | the one permitted system-wide psutil snapshot per pass |
| `_read (pty_host.py)`, supervisor only | the nonblocking reader poll; high sample share, near-zero CPU |
| `_newest_rollouts (adapters/codex.py)`, worker thread only | Codex switch-watch tree walk; only within two minutes of a submitted line or turn the followed rollout does not record |
| `_normalize_tail_text (session.py)` | PTY screen classification for status detection |
| `_read_and_decode (observation.py)`, worker thread only | one 512 KiB window of transcript replay; bursts at attach and rebind, silent at rest |

`_newest_rollouts` used to be the largest remaining avoidable-looking cost (~9% of a loaded
profile with three Codex sessions) and the walk itself has been left alone deliberately.
Its cache TTL is pinned below `TRANSCRIPT_SWITCH_FRESH_SECONDS`, its tree cannot be pruned by
directory date because `codex resume` appends to old rollouts, and the watcher it feeds is the
only path by which a hookless Codex `/new` is detected at all.
What changed on 2026-10-12 is *when* it runs, not what it does: the watcher searches only after
evidence a conversation could have moved (`design/features/status-detection.md`, Codex branch
and rewind recovery), so an idle Codex pane no longer walks at all, and the headers it reads
come from a per-file-instance cache (`rollout_identity`).
Seeing this frame on an idle fleet is therefore a defect, and `transcript_observation` on
`/api/diagnostics/background` says which pane is searching.

## Traps

- **`asyncio.to_thread` does not make psutil work free.** Its Windows calls hold the GIL, so a
  long sampling pass in a worker thread starves the loop exactly as a blocking call would. It
  only stops *looking* like one.
- **`psutil.Process.open_files()` on Windows is a system-wide call, whatever its name says.**
  It answers for one process by enumerating every handle in the system
  (`NtQuerySystemInformation`) and filtering, under the GIL. On the operator's host on
  2026-10-12 that was ~450,000 handles and 234-340 ms per call; with four concurrent callers
  the loop thread's own 0.2 ms `realpath`+`stat` took 500-700 ms, and the Codex ownership
  proof was making the call every 10 s for every idle Codex pane. The stall dumps of that day
  showed the loop in whatever innocent syscall it happened to be holding when the GIL came
  back - `realpath`, `stat`, `resolve` - which is why the cause was not in any of them.
  `win_process_files.py` asks the one process for its own handle table instead (0.6-2 ms),
  and the proof now runs only on evidence. Measured on two live Codex panes, one full
  ownership proof: **median 325 ms before, 1.8 ms after, same rollout found by both**. The
  rule: on Windows, nothing on a cadence may call `open_files()`, `connections()` or any other
  psutil call whose implementation walks the system table; a per-process answer, or no
  answer, is the choice. And when stall frames name a cheap syscall, look for the thread
  holding the GIL before optimising the syscall.
- **`oneshot()` does not cache everything.** `Process.ppid()` on Windows carries no
  `@memoize_when_activated` and rebuilds the whole system parent table per call, sitting in the
  same `oneshot()` block as calls that are cached.
- **A directory walk that stats separately pays twice.** Windows fills `DirEntry` stat fields
  during enumeration; `Path.glob` plus `path.stat()` spends a syscall per file to re-fetch what
  the walk already read.
- **A per-session filesystem read on a loop tick multiplies by the fleet, on the loop.** The
  transcript discovery loop ran `adapter.recent_transcripts` (glob + stat, 836 files) every 0.5s
  per session waiting for its CLI's first record, and the switch watcher resolved every live
  session's cwd (`realpath`) per tick per session. Idle, 22ms; with seven CLIs starting in one
  directory, 5.4s per call (2026-09-14), and p99 loop lag crossed the tray's probe timeout.
  `transcript_scan.TranscriptScanCache` runs the scan in a worker and shares one listing per
  `(backend, cwd)` for a second across every session in that directory; `resolve_path_cached`
  remembers a resolved path for 30s. Both are counted on `/api/diagnostics/background`
  (`transcript_scans`), and a scan over a second is `transcript_scan_slow` at WARNING. The rule:
  a reading taken per session must be shared per *thing read*, and never taken on the loop.
  A cache's *expiry* is a reading too: until 2026-10-12 an expired resolve re-ran `realpath` on
  the loop, and the stall dumps had the loop inside one for 140 s in a day. An expired entry
  now answers stale and refreshes in a worker, async callers resolve new paths off the loop
  before asking (`resolve_paths_async`), and a first-ever resolution on the loop is counted
  (`resolved_paths.loop_misses`) and warned when slow (`resolve_on_loop_slow`).
  The same audit moved Project registration and re-rooting, the onboarding record, and the
  Recent files existence filter into workers, each named in a stall dump of that day: a path on
  an unreachable provider costs the request that asked about it, never every terminal.
- **A cache keyed on a growing file's watermark misses for exactly the files people watch.**
  Until 2026-10-12 every human-facing transcript reader (the Transcript tab, branch points, Copy reply, read-aloud, History) re-decoded the whole conversation, up to a 64 MiB tail, on every ask, behind caches keyed on the file's watermark that a working agent changed on every append.
  A 49 MB Claude transcript cost up to 2.4 s per open under daemon load, and two opens in nine hit the route's 5 s timeout and read as "unreadable".
  `conversation_index.py` keeps one incremental reading per conversation instead (`design/features/conversation-index.md`), and `/api/diagnostics/background` reports it as `transcript_index`.
  - `builds` counts first reads and `extends` reads that decoded only appended bytes; `unchanged` counts refreshes that found nothing new.
    `builds` climbing with the fleet steady means indexes are being dropped and rebuilt: compare `estimated_bytes` with `budget_bytes` and read `evictions`.
  - `rebuilds` counts files that were replaced under the index (Claude cancel/revert); each is logged as `transcript_index_rebuilt` with its reason.
  - `lines_decoded`, `lines_sniffed` (Codex records skipped without decoding) and `bytes_read` say what the reads cost; `joined` counts readers that shared a read already in flight.
  - `building` lists first reads in progress, with bytes read and total; a read slower than 2 s is `transcript_index_slow` at WARNING, and `worst_seconds` keeps the slowest.
    `in_flight` counts async reads still running; it returns to zero when they finish.
  - `tool_input_misses` counts tool inputs that could not be read back because their record moved; more than a handful means files are being rewritten under live readers.
- **A window over a growing file is not a frame of reference.**
  Anything numbered from where a bounded read starts (a line index, a sequence within the read) renumbers itself when the file grows and the window moves, so an idempotent insert keyed on it inserts everything again.
  Number from the start of the file, and read past a cursor that verifies a sample of the file rather than trusting a size or an mtime (`telemetry_cursor.py`).
- **An integrity check judged by the wrong clock repeats forever.**
  A seal that becomes due by the age of a file's contents, and is invalidated by any write, re-reads the whole file after every write for as long as writes keep coming.
  Judge it by when the file was last written, and invalidate only for a write that actually changed something.
- **An unchanged arbitration is silence.** Several subsystems broadcast only on change, so
  "nothing was reported" is not evidence that nothing is wrong.
- **`asyncio.to_thread` cannot be cancelled, and the loop joins what you abandon.** Cancelling
  the awaiting task only drops the future; the worker keeps running, and because it belongs to
  the loop's default executor it is joined by `shutdown_default_executor` at the very end of
  shutdown - after every log handler has already reported a clean stop. A long worker that
  ignores a cancellation token therefore reads as an unexplained multi-second hang *after* the
  daemon says it stopped. Measured: the startup native-history reconcile's tree walk cost
  4.5-13.5s per in-process app teardown in the test suite, and scales with the user's transcript
  tree. Any `to_thread` call that can outlive its awaiter needs a token the worker polls
  (`reconcile.scan_external_transcripts_async` is the pattern), or a unit of work small enough
  that an abandoned worker finishes in milliseconds - transcript replay takes the second route,
  bounding each worker to one 512 KiB window and checking the stop event between windows rather
  than inside one (`observation.JsonlTailer._drain`).
