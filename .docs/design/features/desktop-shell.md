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
- Startup probes `/api/health`. A healthy daemon is reused; otherwise the supervisor starts a
  consoleless child and waits up to 30 seconds. Daemon logs use
  `<data_dir>/desktop-daemon.log`.
- Daemon-owned maintenance commands (Git, Tailscale, usage/account probes, hooks, profile
  discovery, forced cleanup, and SAPI) use Windows no-window process creation. Interactive
  shells and agents remain attached only through ConPTY; background work never flashes a console.
- WebView2 uses persistent `<data_dir>/webview` storage and enables text selection. External
  links continue in the system browser.
- Window close is cancelled and hidden. Minimize hides after the native transition. Tray Open
  shows/restores the same window; Open in browser preserves the ordinary browser surface.
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
- `packaging/swe_mux.spec` emits windowed `dist/swe-mux/swe-mux.exe`, console-subsystem
  `dist/swe-mux/swe-mux-action.exe`, and `_internal/`. The complete `onedir` folder is the
  distributable unit; neither executable is standalone.
- `packaging/swe_mux_supervisor.spec` emits the dedicated PTY supervisor bundle
  `dist/swe-mux-supervisor/` — a separate artifact precisely so rebuilding `dist/swe-mux`
  never collides with a running supervisor's file image (Windows locks running
  executables). `build_desktop.py` rebuilds it only when the supervisor's small source
  closure changes (hash gate: a supervisor rebuild requires reaping sessions first anyway).
  Frozen daemons prefer this bundle; `--supervisor-child` remains the fallback when the
  bundle is absent, and `SWE_MUX_SUPERVISOR_EXE` overrides resolution in any mode.
- `packaging/redeploy_desktop.py` is the frozen update workflow (usable by an agent from
  inside a supervised session): preflight (dedicated supervisor running, no live
  `swe-mux-action.exe` task terminals), detach-stop the daemon and shell, rebuild, relaunch;
  the fresh daemon reattaches every live session.
- Packaged `--daemon-child` re-enters the daemon entry inside a separate process; source mode
  uses `python -m swe_mux`. Packaged `--supervisor-child` mirrors the same split for the PTY
  supervisor; source mode uses `python -m swe_mux.supervisor`. The daemon discovers-or-spawns
  the supervisor itself, so the desktop shell never needs to know it exists.
- Frozen Project Actions use the sibling console executable as their ConPTY root. It shares the
  package but inherits the pseudoconsole correctly, so build tools cannot detach into a visible
  external CMD window. Source mode uses `python -m swe_mux.action_runner`.
- The windowed executable emulates only allowlisted internal `-m` entrypoints for hook delivery
  and nested-agent launch. Arbitrary module dispatch is rejected.

## Key files

- Desktop runtime: `src/swe_mux/desktop.py`
- Daemon runner: `src/swe_mux/__main__.py`
- Shutdown boundary: `src/swe_mux/server.py`
- Package metadata: `pyproject.toml`, `uv.lock`
- Bundle entries/spec: `packaging/desktop_entry.py`, `packaging/action_entry.py`,
  `packaging/swe_mux.spec`
- Reproducible build: `packaging/build_desktop.py`
- Lifecycle tests: `tests/test_desktop.py`

## Relates to

- `sessions.md`: daemon-owned terminals survive viewport closure.
- `remote-access.md`: tailnet access remains separate from loopback desktop shutdown.
- `ui.md`: the same browser UI runs inside WebView2.
