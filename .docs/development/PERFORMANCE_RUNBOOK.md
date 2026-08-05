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

Reference points measured 2026-08-05 on a 12-session fleet with three agents working: daemon
22.6% of one core before the `ppid` fix, 7.7% after; PTY supervisor 0.7%; WebView 15.8% across
seven processes.
A daemon materially above that with an idle fleet is worth investigating.

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

## Known-cost frames

Frames expected in a healthy profile, so they are not mistaken for defects:

| frame | what it is |
| --- | --- |
| `_poll (windows_events.py)` | the event loop idle; should dominate |
| `append (scrollback.py)` | PTY output entering the retention ring; scales with real output |
| `_fanout (session.py)` | delivering output to attached clients; scales with panes |
| `_refresh_tree (processes.py)` | the one permitted system-wide psutil snapshot per pass |
| `_read (pty_host.py)`, supervisor only | the nonblocking reader poll; high sample share, near-zero CPU |

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
