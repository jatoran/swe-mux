# Git awareness and worktrees

## What it is

- Attached sessions poll the latest accepted live cwd (or spawn cwd until live telemetry is
  available) for branch, dirty count, and upstream divergence.
- User-initiated worktree API wraps `git worktree` without performing other mutating git operations.

## Operations

- Poll only cwd values with at least one attached terminal pane; deduplicate by cwd and
  cap concurrency. Branch/status/upstream calls run in parallel with bounded timeouts.
- OSC-driven targets are existing local directories, debounced for 1.25 seconds, and limited
  to 12 accepted switches per session per minute before Git polling follows them. Invalid,
  remote, fragmented-spam, and over-limit telemetry cannot create subprocess churn.
- Detached HEAD is shown as its short commit SHA.
- Emit `git_changed`; mirror state into the session snapshot and attached pane-header chip.
- Add/list/remove worktrees through argument-vector subprocesses; no shell interpolation.
- Removal validates the exact requested path against Git's current porcelain worktree
  list before mutation.
- Typed create-and-spawn validates target/parent, creates the worktree, starts a terminal
  in it, and attaches that terminal server-side. If spawn fails, the response explicitly
  reports that the successfully created worktree was retained.
- The session context menu opens a manager for existing worktrees; opening a terminal
  is non-mutating, while removal requires a two-click inline confirmation.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Routes: `src/swe_mux/server.py`
- Pane-header chip: `frontend/src/App.tsx`
