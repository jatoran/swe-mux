# Git awareness and worktrees

## What it is

- Attached sessions poll the latest accepted live cwd (or spawn cwd until live telemetry is
  available) for branch, dirty count, and upstream divergence.
- User-initiated worktree API wraps `git worktree` without performing other mutating git operations.

## Operations

- Canonical Project identity probes for worktree root, common Git directory, and origin run
  concurrently behind one timeout window. Results are cached briefly per canonical cwd so a burst
  of terminal launches does not repeatedly cross the Git process boundary.
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
- Worktree mutation endpoints remain guarded backend capability for future Git tooling.
  Worktrees have no first-class sidebar row, tab, launcher, or session context action.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Routes: `src/swe_mux/server.py`
- Pane-header chip: `frontend/src/App.tsx`
