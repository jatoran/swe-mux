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

The snapshot groups HTTP counts by normalized route and peer, and WebSocket counts by channel
and peer.
HTTP response bytes are the encoded body after negotiated compression.
WebSocket bytes are application text/binary frame payloads before per-message compression.
Neither figure includes HTTP or WebSocket headers, TLS, Tailscale, TCP/IP, retransmits, or radio
overhead, so use a browser network export or OS packet capture when the carrier-billed wire total
is required.
The DELETE response includes the previous snapshot, writes its aggregate totals to the rotating
daemon log, and resets only the counters.
It does not restart the daemon or affect sessions.
Both diagnostics requests are excluded from the measurement window.

For a useful mobile comparison, measure the same scripted interval with one foreground client,
then with the browser backgrounded, and compare `http_routes` and `websocket_channels` rather
than only the aggregate.
Large one-time static and PTY replay transfers should be separated from steady-state idle use.

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
The events WebSocket must not refetch the full sessions/projects/previews/groups/harnesses snapshot for observation-only traffic such as `tool_use`, `tool_result`, `PreToolUse`, `PostToolUse`, or `project_files_changed`.
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

## Startup latency

A distinct question from "the running daemon is slow", with its own measurement and its own
failure mode: nothing the daemon serves exists until the listener binds, and the listener binds
only after `runtime_context` has opened every store, reattached supervised sessions, and built
every service.

Read it from `<data_dir>/daemon.log`:

```
daemon runtime ready in 8.4s (8 live session(s)); binding listeners
```

The line is INFO under `SLOW_STARTUP_SECONDS` (20s) and WARNING above it, so a drifting start is
one grep rather than an inference from the gap between two unrelated timestamps.
Cross-check against `PTY supervisor connected`, which splits the interval into store/DB work
before it and session reattachment after it.

Reference, measured 2026-08-06 on a fleet of 6-8 sessions with an 879 MB `mux.db`: **~8s warm**.
Post-redeploy starts are the slow ones, at 27s and 31s, because the bundle rebuild has just
flushed the page cache the store opens depend on - the same daemon, the same work, cold.

Two rules this path earns:

- **Housekeeping must never gate the port.**
  Retention prunes are scans whose cost tracks database size and cache state, and they used to run
  on this path for four stores.
  Every one of them was already covered by a supervised background loop that reruns it hourly,
  so the startup copies bought nothing and delayed the bind by their own cost.
  Anything that is not needed to answer the first request belongs in a loop.
- **The desktop shell budgets its health wait against this number.**
  A start that crosses `DAEMON_HEALTH_TIMEOUT_SECONDS` (300s) leaves the tray waiting.
  A start that crosses it *and* the daemon then answers is the ordinary case that
  `load_when_healthy` covers; see `design/features/desktop-shell.md`.

## Known-cost frames

Frames expected in a healthy profile, so they are not mistaken for defects:

| frame | what it is |
| --- | --- |
| `_poll (windows_events.py)` | the event loop idle; should dominate |
| `append (scrollback.py)` | PTY output entering the retention ring; scales with real output |
| `_fanout (session.py)` | delivering output to attached clients; scales with panes |
| `_refresh_tree (processes.py)` | the one permitted system-wide psutil snapshot per pass |
| `_read (pty_host.py)`, supervisor only | the nonblocking reader poll; high sample share, near-zero CPU |
| `_newest_rollouts (adapters/codex.py)` | Codex switch-watch tree walk; ~9% of a loaded profile with 3 Codex sessions |
| `_normalize_tail_text (session.py)` | PTY screen classification for status detection |

`_newest_rollouts` is the largest remaining avoidable-looking cost and has been left alone
deliberately.
Its cache TTL is pinned below `TRANSCRIPT_SWITCH_FRESH_SECONDS`, its tree cannot be pruned by
directory date because `codex resume` appends to old rollouts, and the watcher it feeds is the
only path by which Codex `/new` is detected at all.
At a daemon cost of 1.5% of a core the win is a fraction of a percent, against the subsystem
with the longest history of subtle identity bugs in this repository.

## Traps

- **`asyncio.to_thread` does not make psutil work free.** Its Windows calls hold the GIL, so a
  long sampling pass in a worker thread starves the loop exactly as a blocking call would. It
  only stops *looking* like one.
- **`oneshot()` does not cache everything.** `Process.ppid()` on Windows carries no
  `@memoize_when_activated` and rebuilds the whole system parent table per call, sitting in the
  same `oneshot()` block as calls that are cached.
- **A directory walk that stats separately pays twice.** Windows fills `DirEntry` stat fields
  during enumeration; `Path.glob` plus `path.stat()` spends a syscall per file to re-fetch what
  the walk already read.
- **An unchanged arbitration is silence.** Several subsystems broadcast only on change, so
  "nothing was reported" is not evidence that nothing is wrong.
