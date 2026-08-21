# Windows desktop shell

## What it is

An optional Windows WebView2 window and system-tray supervisor around the same Preact/aiohttp
surface used by ordinary desktop and mobile browsers. The daemon remains a separate process and
continues to own every terminal.

## Key concepts

- Desktop supervisor: `swe-mux`; window/tray/login-startup owner.
- Managed daemon: separate `muxd` child launched with a desktop control secret.
- Hide: close/minimize removes the window from desktop presentation without daemon mutation.
- Quit: explicit tray action that confirms live terminals and stops the managed daemon
  (shutdown `mode=quit`; with the PTY supervisor this also reaps every supervised session, so
  the end state matches the in-process mode).
- Restart daemon (keep sessions): tray action shown only when `pty_supervisor_enabled`. Sends
  shutdown `mode=restart` (the daemon detaches from the PTY supervisor without reaping), waits
  for the old daemon to exit, and starts a fresh one, which reattaches to the still-running
  sessions. This is the session-preserving "reload with my changes" path.

## Operations

- One instance exists per resolved config path. A second visible launch signals the existing
  instance to restore/focus; a duplicate hidden login launch exits silently.
- Startup probes `/api/health`. A healthy daemon is reused; otherwise the tray starts a
  consoleless child. **A daemon that answers 503 is starting, not healthy**: the daemon binds
  its listeners before it builds its runtime, so a bound port is no longer evidence of a usable
  daemon, and every probe here reads readiness (`ok is True`) rather than reachability. The
  in-progress phase is in the 503 body, and the daemon writes each phase transition to
  `<data_dir>/lifecycle.log` itself. The daemon is spawned via
  `popen_outside_job` (breakaway from any inherited Job object) so a tray relaunched from
  inside a session cannot hand the daemon that session's kill-on-close Job; the tray also
  checks `process_in_job()` at startup and records a warning in the lifecycle ledger.
- **A daemon that is still running has not failed.** Only a spawned child that *exits* is a
  startup failure, and it ends the wait immediately; uptime is never evidence against it. The
  health wait is budgeted at `DAEMON_HEALTH_TIMEOUT_SECONDS` (300s) because a daemon's runtime
  is not ready the moment its port is, and a start whose page cache was just flushed
  by a redeploy takes multiples of a warm one. Exhausting the budget with the child alive is
  not fatal either: the tray, the window and the activation signal all come up, and the window
  loads once health finally arrives (`load_when_healthy`). The tray exits only when there is
  genuinely nothing to show. A 30-second budget and a fatal verdict on expiry previously killed
  the tray during ordinary post-redeploy starts, leaving a healthy daemon with no shell
  attached and requiring a manual relaunch.
- The tray-menu restart waits `DAEMON_RESTART_WAIT_SECONDS` (30s) instead, because pystray runs
  a menu action on its message thread and a long wait there freezes the tray, and because that
  path has nothing to wait for: the window already exists and its SPA reconnects to the
  returning daemon by itself.
- Health waits are recorded in `<data_dir>/lifecycle.log`: how long the daemon took to answer,
  or that it is still starting, or that it exited before answering.
- The daemon's console output redirects to `<data_dir>/desktop-daemon.log` (rotated to `.1`
  at each spawn; it is a crash catcher — structured logs live in the rotating
  `<data_dir>/daemon.log` / `access.log`). The tray watches the daemon child and appends its
  exit code to `<data_dir>/lifecycle.log`, the only record an externally-killed daemon leaves.
- Daemon-owned maintenance commands (Git, Tailscale, usage/account probes, hooks, profile
  discovery, forced cleanup, and SAPI) use Windows no-window process creation. Interactive
  shells and agents remain attached only through ConPTY; background work never flashes a console.
- WebView2 uses persistent `<data_dir>/webview` storage and enables text selection. External
  links continue in the system browser.
- Production WebView2 runs with browser accelerator keys disabled by pywebview, so desktop-only
  bindings such as `Ctrl+Tab` reach the SPA. An ordinary Chrome/Edge tab keeps those browser
  shortcuts instead; the same saved mapping is harmless there because the page never receives
  the chord.
- Window close is cancelled and hidden. Minimize hides after the native transition. Tray Open
  shows/restores the same window; Open in browser preserves the ordinary browser surface.
- Normal window bounds and maximized state persist in
  `<data_dir>/desktop-window-state.json`, outside the replaceable application bundle.
  Move and resize events are debounced and atomically saved; minimized and hidden presentation
  are never persisted. Relaunch, redeploy, rollback, and login startup therefore restore the same
  usable geometry, while a missing or invalid state uses the centered 1440 x 920 default.
  An in-app redeploy separately samples whether any desktop app window is visible immediately before the stop, then relaunches the successful or rolled-back bundle with that same visible or tray-hidden presentation.
  Direct script invocations retain explicit `--hidden` behavior and otherwise launch visibly; `--restore-visibility` is reserved for callers that need the sampled presentation.
  Saved bounds are fitted to the current monitor working areas before window creation, so removing
  a monitor or reducing its resolution cannot restore the title bar off-screen.
- Start with Windows writes the exact current executable/config command to the current-user Run
  key. No machine-wide installation or elevation is required.
- Tray Quit uses a native topmost Windows confirmation owned by the desktop supervisor, not the
  WebView. Confirmation therefore works identically while the window is visible, minimized, or
  hidden. Confirmed Quit reports the live terminal count, requests authenticated graceful
  shutdown, stops the tray, and destroys the window. A desktop crash or forced window-process
  exit leaves the daemon running for recovery.

## Security boundary

- `<data_dir>/desktop-control.token` is random, user-local control material.
- The token reaches the child only through `SWE_MUX_DESKTOP_CONTROL_TOKEN`.
- `/api/desktop/shutdown` is absent as authority for standalone daemons, rejects non-loopback
  peers, and uses constant-time comparison. The browser never receives the token.
- An already-running unmanaged daemon remains operational when the desktop shell exits; the
  shell never guesses a PID or broadens network shutdown authority.

## Packaging

- Runtime extra: `uv sync --extra desktop`.
- Build dependencies: `uv sync --extra desktop --group package`.
- `packaging/build_desktop.py` builds the frontend in `.runtime/`, publishes hashed assets before
  `index.html`, generates the ICO, and runs PyInstaller. It never empties the live static tree;
  locked content-addressed stale assets may remain harmlessly until a later build.
- **`build_frontend` runs `vite build` directly, so npm's `postbuild` hook never fires and every
  step it performs has to be repeated explicitly.** Forgetting one is silent in the worst way: the
  bundle builds, the daemon starts and reports healthy, and the defect appears only in a browser.
  `test_the_desktop_frontend_build_repeats_every_postbuild_step` reads `frontend/package.json` and
  fails when a `postbuild` script has no counterpart here. (`prebuild` needs no such guard: this
  path runs `npm run check`, whose `precheck` performs those steps.)
- **Precompressed variants are dropped on publish and regenerated, never copied over.** Vite emits
  no `.gz`, so a publish that only copies leaves the previous build's compressed files beside the
  new source. The daemon prefers a `.gz` for any client sending `Accept-Encoding: gzip` - every
  browser - and `index.html.gz` names content-hashed assets, so a stale one serves an index whose
  every asset 404s: a blank screen on a bundle that reports itself healthy.
  This defect also defeats the asset-hash check in the repository's `CLAUDE.md`, because `curl`
  without `--compressed` is served the correct plain `index.html`. To check the file a browser
  actually receives, request it compressed:
  `curl -s --compressed http://127.0.0.1:8765/ | grep -o 'assets/index-[A-Za-z0-9_-]*\.js'`.
- `packaging/swe_mux.spec` emits windowed `dist/swe-mux/swe-mux.exe` and `_internal/`. The
  complete `onedir` folder is the distributable unit; the executable is not standalone. It is
  deliberately the *only* executable here: a second one (`swe-mux-action.exe`) used to root
  task terminals, and a live task then locked this directory against the redeploy swap.
- `packaging/swe_mux_supervisor.spec` emits the dedicated PTY supervisor bundle
  `dist/swe-mux-supervisor/` — a separate artifact precisely so rebuilding `dist/swe-mux`
  never collides with a running supervisor's file image (Windows locks running
  executables). `build_desktop.py` rebuilds it only when the supervisor's small source
  closure changes (hash gate: a supervisor rebuild requires reaping sessions first anyway).
  Frozen daemons prefer this bundle; `--supervisor-child` remains the fallback when the
  bundle is absent, and `SWE_MUX_SUPERVISOR_EXE` overrides resolution in any mode.
- `packaging/redeploy_desktop.py` is the frozen update workflow (usable by an agent from
  inside a supervised session, or via `POST /api/daemon/redeploy` behind the UI's
  "Rebuild + redeploy app" menu entry): preflight (dedicated supervisor running; a
  legacy check for `swe-mux-action.exe` terminals left over from a pre-removal bundle, which
  nothing creates any more; and the **bundle-in-use gate** — `bundle_locks.py` names any
  foreign process anchoring `dist/swe-mux` by exe or cwd, because such a process survives
  everything the redeploy may stop (sessions descend from the supervisor) and dooms the
  swap after minutes of build: typically a dev server behind a Preview tab or a terminal
  whose cwd inherited into the bundle. The gate runs pre-build, again pre-stop, and in the
  endpoint as `409 bundle_in_use`; `--force`/`force=true` downgrades it to a warning),
  then a **staged** cycle — build frontend + app bundle
  into `dist/.staging` while the old app keeps serving, detach-stop the daemon and shell only
  after the build succeeded, swap (`dist/swe-mux` → `dist/swe-mux.prev`, staging in; renames
  retry through lock stragglers), relaunch; the fresh daemon reattaches every live session.
  The `swe-mux.prev` rename target is proven free before the app is stopped — a tree Windows
  only partially deleted (mapped exe/DLL images) otherwise blocks the swap after the daemon
  is already down; a stubborn leftover is moved aside to `swe-mux.prev.stale-*`.
  A failed build never touches the running app, and a new build that never reports healthy is
  rolled back to `swe-mux.prev` (failed bundle kept at `dist/swe-mux.failed`), so a remote
  client cannot be stranded.
  **The health wait reports progress rather than waiting in silence.** The successor daemon
  binds before its runtime is built and answers 503 naming the phase it is in, so `wait_healthy`
  logs each phase *change* to `redeploy.log` and, on expiry, says which phase the budget ran out
  in. Once-per-phase and not once-per-poll: the elapsed seconds in the line move continuously, so
  comparing rendered text would write two lines a second. Nothing here estimates a remaining
  time — phase durations vary by orders of magnitude across fleets, and a wrong number is acted
  on where an absent one is not. This is what the 300s→600s budget change of 2026-08-21 was
  really about: a healthy-but-slow deploy was indistinguishable from a hung one, and raising the
  ceiling only widened the window in which nobody could tell.
  The UI endpoint invokes the script with `--restore-visibility`, so every relaunch path uses the presentation captured immediately before the old shell is stopped.
  The endpoint validates source checkout + `uv`, requires the
  attached supervisor (or `force`), and is single-flight via `<data_dir>/redeploy.lock` with
  output in `<data_dir>/redeploy.log`.
  The lock names the *script* process and every reader tests pid liveness, so a crash releases it
  and nothing has to clean it up.
  A run started straight from a terminal claims the same lock itself (`--lock-held` tells the
  script the endpoint already did), which makes a CLI redeploy single-flight too and makes it
  visible to `GET /api/daemon/redeploy` exactly like a UI one.
  Every run records a machine-readable outcome in `<data_dir>/redeploy-result.json`
  (`succeeded` / `rolled_back` / `build_failed` / `swap_failed` / `unhealthy` / `refused` /
  `failed`, plus a detail sentence and a log tail), written whole via a temp file because the
  successor daemon reads it while starting up.
  The successor serves it as `last_result`, which is what lets the reconnecting UI say that a
  rollback happened: the app comes back looking entirely normal, so otherwise nothing would tell
  the operator their change never shipped.
- Clients are told about a redeploy rather than discovering it as failed requests.
  The daemon emits `daemon_redeploy_started` when it accepts one or is asked to announce a
  terminal-launched one (`POST /api/daemon/redeploy/announce`, loopback-only and refused unless
  `redeploy.lock` names a live process - it describes a real redeploy, it is not a way to put the
  fleet's UI into a fake maintenance mode).
  It emits `daemon_redeploy_stopping` from `POST /api/desktop/shutdown` when a redeploy is in
  flight, then lingers briefly so the frame reaches the `/events` sockets the shutdown is about to
  close.
  That is the only authoritative "the outage starts now" a browser can get: the script stops the
  daemon through that endpoint, so the daemon is still alive and still has its sockets when it
  learns the build finished, whereas inferring the same thing from a dropped socket is
  indistinguishable from an ordinary blip.
  Nothing here is load-bearing for the redeploy itself - it only buys the UI a progress chip, so an
  unreachable or older daemon costs nothing but the old behaviour.
- Packaged `--daemon-child` re-enters the daemon entry inside a separate process; source mode
  uses `python -m swe_mux`. Packaged `--supervisor-child` mirrors the same split for the PTY
  supervisor; source mode uses `python -m swe_mux.supervisor`. The daemon discovers-or-spawns
  the supervisor itself, so the desktop shell never needs to know it exists.
- Project Actions have no swe-mux ConPTY root of their own in either mode: the step's shell (or
  its PATH-resolved program) is the root, spawned with the step's cwd and env as spawn fields.
  A `.cmd`/`.bat` shim goes through `%COMSPEC%`, which inherits the pseudoconsole correctly, so
  build tools still cannot detach into a visible external CMD window.
- The windowed executable emulates only allowlisted internal `-m` entrypoints for hook delivery
  and nested-agent launch. Arbitrary module dispatch is rejected.

## Key files

- Desktop runtime: `src/swe_mux/desktop.py`
- Desktop window-state validation and persistence: `src/swe_mux/desktop_window_state.py`
- Daemon runner: `src/swe_mux/__main__.py`
- Shutdown boundary: `src/swe_mux/server.py`
- Package metadata: `pyproject.toml`, `uv.lock`
- Bundle entries/spec: `packaging/desktop_entry.py`, `packaging/swe_mux.spec`
- Reproducible build: `packaging/build_desktop.py`
- Lifecycle tests: `tests/test_desktop.py`

## Relates to

- `sessions.md`: daemon-owned terminals survive viewport closure.
- `remote-access.md`: tailnet access remains separate from loopback desktop shutdown.
- `ui.md`: the same browser UI runs inside WebView2.
