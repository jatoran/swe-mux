# Sessions and terminals

## What it is

Daemon-owned interactive ConPTY processes with immutable Project ownership, bounded replay,
and detachable browser viewports.

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
- Claude/Codex promotion preserves the parent PTY's canonical Project and records an atomic
  agent-run history lifecycle.
- Attach, detach, browser reconnect, and pane operations never change process state.
- Slow subscribers receive a gap frame and deterministic bounded replay.
- Explicit kill attempts adapter-specific graceful exit before process-tree termination.
- Ended sessions remain visible until explicitly dismissed; history remains durable.
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

## Relates to

- `projects-and-notes.md`: canonical ownership and resources.
- `history.md`: durable agent-run lifecycle.
