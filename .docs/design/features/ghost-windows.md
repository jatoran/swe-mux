# Ghost windows

A ghost window is a browser window that Windows reports as hidden while DWM composites it onto the desktop anyway.
The operator sees an opaque rectangle that cannot be moved, focused, closed, or clicked through.
`src/swe_mux/ghost_windows.py` detects these and parks them off-screen.

## The defect

Chromium launched with `--headless=new` creates a genuine top-level window rather than rendering purely off-screen.
The window carries every attribute of a real one except `WS_VISIBLE`.

| Attribute | Value |
|---|---|
| Class | `Chrome_WidgetWin_1` |
| Style | `0x06CF0000` (caption, sysmenu, thick frame, min/max boxes, `WS_VISIBLE` clear) |
| Title | `"<page title> - Google Chrome for Testing"` |
| Position | Default origin `(10,10)`, sized to the viewport |

The missing `WS_VISIBLE` bit is the whole failure mode.
Every Win32 interaction path filters on it, so the window has no taskbar button, no Alt+Tab entry, and no hit-testing:
`WindowFromPoint` skips it and clicks fall through to whatever is behind.
DWM still presents its surface, so the pixels are on screen with no route to the window that owns them.

The compositing is measured, not inferred.
A fixed screen region shows zero bytes of change across seven seconds with no headless browser running, and 34,767 changed bytes in the same region and interval with one running.

## Scope

The defect belongs to Chromium, not to any harness, so detection keys on the window signature rather than on a provider.

| Stack | Affected | Reason |
|---|---|---|
| Puppeteer | Yes | `headless: true` resolves to new headless driving full Chrome |
| Selenium, chromedp | Yes | Same new-headless path |
| Playwright | No | Ships and defaults to `chromium_headless_shell`, a binary that creates no windows |

Puppeteer is the common trigger because its default is the affected mode and its browser cache holds only full Chrome.

## Detection contract

A window is swept only when every condition holds.
The conjunction is the safety property: each condition alone matches legitimate windows.

- Class is exactly `Chrome_WidgetWin_1`.
  `Chrome_WidgetWin_0` is the hidden message-only window that every Chromium and Electron process owns and is never a ghost.
- `IsWindowVisible` is false.
  A visible window is a real one and belongs to the operator.
- The title is non-empty.
- The window rect intersects the virtual screen, so it can actually paint.
- The owning process command line contains `--headless`.

The command-line condition is the discriminator that excludes legitimate Electron windows.
Mullvad VPN, Signal, Visual Studio Code, and Docker Desktop each own a hidden, titled `Chrome_WidgetWin_1` window and none of them pass it.

## Remediation

The sweep moves the window to `(-32000,-32000)` with `SetWindowPos` and then forces a full desktop repaint.

Relocation is chosen over destruction because both destructive options cost the operator real work.
`WM_CLOSE` destroys the agent's page, and terminating the process destroys its entire browser.
Relocation leaves both intact: a headless surface is captured from the compositor and never reads its own screen coordinates, so a parked browser still serves correct `Page.captureScreenshot` output.

The repaint is part of the fix rather than cosmetic.
The vacated region holds the ghost's last composited frame until something invalidates it.

The sweep is idempotent.
A parked window no longer intersects the virtual screen, so it stops matching and is never touched twice.
That property is what makes a fixed-cadence loop safe to run against another process's windows indefinitely.

## Cost

The condition order is load-bearing for cost, not only for correctness.
Visibility and class are checked first, so the cross-process command-line read happens only for windows that already look like ghosts.
Verdicts are memoized per `(pid, create_time)` because a browser's command line never changes, and the creation time keeps a recycled PID from inheriting a stale verdict.

Measured against 580 live top-level windows: 3.99 ms cold, 2.76 ms median warm, with one memoized command-line lookup.
This satisfies the periodic-loop rule in `../../technical/backend/packages.md`, which exists because an earlier unmemoized per-tick `cmdline()` scan in `processes.py` cost 930-1110 ms per pass.

## Configuration

| Option | Default | Bounds |
|---|---|---|
| `ghost_window_sweep_enabled` | `true` | - |
| `ghost_window_poll_seconds` | `5.0` | 0.5 to 60 seconds |

Both are hot-reloadable and both are edited in Settings → Processes → **Ghost windows**, which is
its own section rather than more rows under process evidence: this is the one thing on that tab
that changes what the machine looks like rather than what swe-mux records about it.
The loop reads `enabled` each tick, so toggling it takes effect without restarting the task.
The control says the sweep *parks* a window off screen rather than closing it, because that is
what it does and because the difference is the reason the browser and its screenshots keep
working.

## Boundaries

- Windows only.
  The artifact is a Win32 and DWM interaction and the service is inert on other platforms.
- Daemon-side.
  The supervisor is deliberately uninvolved because a ghost is a desktop artifact rather than session state, and it must not survive into a session-preserving reload as owned state.
- Requires `psutil` for the command-line condition.
  Without it the service reports unavailable and does nothing rather than sweeping on a weaker signature.

## Upstream

The definitive fix belongs in each affected harness: select the headless shell binary instead of full Chrome.
For Puppeteer that is `headless: "shell"`.
A weaker harness-side mitigation is `--window-position=-32000,-32000`, which is verified to place the window off-screen at creation.
The sweep remains necessary regardless, because swe-mux does not control which browser stack an agent invokes.

## Implementation

- `src/swe_mux/ghost_windows.py` owns detection, remediation, and the loop.
- `src/swe_mux/server.py` constructs `GhostWindowSweeper`, starts and stops it with the other background loops, and applies hot-reloaded configuration.
- `src/swe_mux/config.py` defines and validates both options.
