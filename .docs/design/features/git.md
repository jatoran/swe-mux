# Git awareness and worktrees

## What it is

- Attached session cwd values are polled for branch, dirty count, and upstream divergence.
- User-initiated worktree API wraps `git worktree` without performing other mutating git operations.

## Operations

- Poll only cwd values with at least one attached terminal pane.
- Deduplicate polling by cwd.
- Emit `git_changed`; mirror state into the session snapshot and attached pane-header chip.
- Add/list/remove worktrees through argument-vector subprocesses; no shell interpolation.
- The session context menu opens a manager for existing worktrees; opening a terminal
  is non-mutating, while removal requires a two-click inline confirmation.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Routes: `src/swe_mux/server.py`
- Pane-header chip: `frontend/src/App.tsx`
