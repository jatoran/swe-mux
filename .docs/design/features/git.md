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
- Worktrees have no first-class sidebar row, workspace tab, launcher, or session context
  action. Their one surface is the utility drawer's Git tab, below.

## Git drawer tab

- The drawer's **Git** tab is the only place either half of this feature is drawn as more
  than a chip. It is Project-scoped, sitting after Notes: it reports on the repository
  behind the Project rather than opening a document into a pane, so it closes the
  Project-scoped block without being a navigator (`ui.md`).
- It shows two lists over one repository, joined by path. **Branches** is every branch the
  repository has checked out, annotated with dirty count and upstream divergence wherever a
  session is attached inside that tree; **worktrees** is the porcelain list itself, with the
  main/bare/locked/prunable flags and how many live sessions are working in each.
- The join is by **path, never by branch name**. A detached HEAD reports its short commit
  SHA in the branch field, which can never match the worktree's own detached marker. Path
  comparison unifies separators and case (Git reports forward slashes on Windows; a session
  cwd carries backslashes) and matches on a segment boundary, longest match first, so a
  sibling `repo-old` is never read as inside `repo` and a nested worktree is not attributed
  to the repository root containing it.
- A branch with no attached session has **no** working-tree state rather than a clean one.
  Dirty and divergence exist only for cwds the monitor polls, and rendering an unmeasured
  tree as clean would be a claim the daemon never made.
- A live session whose cwd is in none of the listed worktrees still earns a row, marked as
  another repository: a nested or sibling checkout under the same Project root is worth
  seeing, but it is not one of this repository's trees.
- The tab performs exactly the two mutations the API wraps. It does not commit, switch
  branches, stage, fetch, or prune, and it adds no endpoint. Enumerating every *local*
  branch (as opposed to the checked-out ones) would need a new read-only route and does not
  exist yet.
- Creation refuses a relative path outright. `POST /api/git/worktrees` resolves one against
  the **daemon's** working directory rather than the repository, so accepting it would put
  the worktree somewhere nobody asked for.
- Removal arms a confirm and is refused up front on the main tree (Git refuses it too), on a
  locked tree, and on a tree a live session is working in — Git would happily remove a clean
  worktree out from under a running terminal. Git's own refusal is shown on the row along
  with the `--force` override it implies, rather than being retried silently.
- Every Git call the daemon makes is bounded at four seconds. That is right for a status
  poll and short for `worktree add`, so a timeout is reported as "may still have completed",
  never as a failure.
- Live updates need no polling. Branch, dirty, and divergence ride the session snapshots the
  tab already receives, so `git_changed` refreshes them by re-render; the worktree list is
  refetched when `worktree_created`/`worktree_removed`/`git_changed` reach the client. A
  worktree created by hand in a terminal emits no event at all, which is what the explicit
  Refresh is for.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Routes: `src/swe_mux/server.py`
- Drawer tab and its pure joining/parsing helpers: `frontend/src/GitTab.tsx`,
  `frontend/src/gitWorktrees.ts`
- Pane-header chip and the `mux:git-changed` re-dispatch: `frontend/src/App.tsx`
