# Sessions and terminals

## What it is

Daemon-owned interactive ConPTY processes with immutable Project ownership, bounded replay,
and reattachable browser viewports.

## Model

- A session is one adapter, one ConPTY process, bounded byte scrollback, and zero or more
  browser subscribers.
- A session has one immutable canonical `project_id`. It cannot exist before a Project does.
- The daemon starts every new, split, stacked, resumed, or review session at the owning
  Project's canonical root.
- Validated OSC 7/runtime cwd is display and Git telemetry only. Navigating elsewhere does
  not change Project membership, layout, note, file browser, defaults, or history ownership.

## Operations

- Direct shell creation uses the requested/profile/Project/global profile precedence.
- Project Actions create ordinary shell-backed sessions. Every step is attributed to the
  selected Project, appears as a normal terminal, and starts through a small runner that applies
  the validated in-Project cwd/env before launching the imported command.
- The browser inserts a client-only `starting terminal…` row/tab before the spawn request
  resolves. Temporary IDs never reach Project persistence or PTY routes; success atomically
  replaces the placeholder with the daemon session, while failure removes it and restores a
  surviving focus target.
- Spawn preparation runs independent Git identity probes concurrently and briefly caches the
  stable result for repeated launches. Synchronous ConPTY creation runs outside the daemon event
  loop, keeping existing terminals, events, and HTTP responsive during Windows process startup.
- Once ConPTY exists, the daemon publishes the in-memory session and returns the spawn response;
  durable Project/history/event registration continues in the background. Transcript imports or
  other SQLite work therefore cannot hide an already-usable terminal. Lifecycle writes that
  depend on the history row wait for this registration internally.
- PTY attach replay and input handling never await attachment/input telemetry persistence.
  Startup metrics separate interactive `server_ready` from `durable_registration` latency.
- A browser fits xterm after selecting its renderer, then sends `attach_ready` with the active
  columns and rows. The daemon resizes ConPTY before sending replay bytes; older clients may use
  their first `resize` frame or the bounded compatibility timeout. Messages received while
  readiness is pending are processed only after the replay boundary.
- Desktop terminals default to WebGL with DOM fallback. The pinned xterm 6 WebGL addon carries
  the upstream missing-buffer-line guard in its runtime bundles, preventing a resize/trim race
  from aborting a model update and leaving stale glyphs. Mobile remains DOM-only.
- Agent startup state uses semantic evidence first. Claude normally becomes ready through its
  `SessionStart` hook; Codex (or a degraded Claude hook path) may use settled live PTY output as
  a startup-only, lowest-priority readiness signal until its first native transcript event.
- Claude/Codex promotion preserves the parent PTY's canonical Project and records an atomic
  agent-run history lifecycle.
- Attach, detach, browser reconnect, and pane operations never change process state.
- Slow subscribers receive a gap frame and deterministic bounded replay.
- Explicit kill attempts adapter-specific graceful exit before process-tree termination.
- Once the root exit code is captured, an ended session releases its dead ConPTY host. The
  reader keeps only a thread-local reference long enough to drain final output, and finalization
  cancels any frozen pywinpty read still parked after root exit. Retained scrollback is independent
  of the OS pseudoconsole handle. This lets ended sessions remain visible until explicitly
  dismissed without retaining `OpenConsole.exe`/`conhost.exe`.
- Ended-session history remains durable.
- Every terminal type can lazily initialize a Project-owned session note from its context menu.
  The note survives terminal exit and daemon restart as a file under `.swe-mux/notes/sessions/`;
  agent History retains the terminal note identity so it can be reopened later.
- Resume requires a target Project and a valid native identity/transcript. The new process
  starts at the selected Project root and receives a new mux identity.

## Key files

- `src/swe_mux/session.py`
- `src/swe_mux/pty_host.py`
- `src/swe_mux/git_projects.py`
- `src/swe_mux/spawn_contract.py`
- `src/swe_mux/adapters/`
- `frontend/src/App.tsx`
- `frontend/src/TerminalPane.tsx`
- `src/swe_mux/action_runner.py`

## Relates to

- `projects.md`: canonical ownership and Project registration.
- `project-resources.md`: terminal-owned session notes.
- `history.md`: durable agent-run lifecycle.
- `project-actions.md`: trusted multi-session task launch.
