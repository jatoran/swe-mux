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
- Worktrees have no first-class sidebar row or workspace tab. Their one surface is the
  utility drawer's Git tab, below — plus one launcher: creation may start a session in the
  new tree (see "Spawning a session into a worktree").

## Git drawer tab

- The Project-scoped Git tab has Map and Log readings of one repository.
- Map reports every registered worktree, its exact root, checked-out branch or detached commit, locks, prune warnings, live-session attribution, local changes, and comparison-ref changes.
- Local changes are separate `CONFLICTS`, `UNSTAGED`, and `STAGED` groups.
- Unstaged means working tree versus index, staged means index versus `HEAD`, and conflicted means unresolved index state.
- A path changed on both sides of the index appears independently in staged and unstaged groups.
- Branch changes compare the worktree `HEAD` with the merge base of the effective comparison ref.
- Comparison ahead and behind counts are distinct from the session monitor's upstream ahead and behind values.
- Every group reports exact file count, text additions, text deletions, binary count, and at most 200 typed file rows.
- Unmeasured values remain `null`; a failed or unavailable comparison never becomes a zero or a clean claim.

### Comparison ref

- Each Project has a nullable machine-local `git_compare_ref` database override.
- `null` means Auto and never writes repository configuration.
- Auto prefers `origin/HEAD`, then the symbolic default of exactly one non-origin remote, then local `main`, then local `master`.
- Auto returns unavailable when none resolves; local staged, unstaged, and conflicted information remains usable.
- The backend returns the effective ref, display value, inference source, reason, and a bounded selector candidate list.
- An explicit override is bounded, validated by Git, and must resolve to a commit.
- A stale explicit override remains visibly unavailable and never silently falls back.
- Comparison reads are local-only and never fetch.

### Commit log

- Log preserves Git's `--graph` topology and loads 80 commits initially, bounded at 200.
- Connector-only rows are inert.
- Expanding a commit lazily loads its typed file summary and reuses the shared file rows.
- An ordinary or merge commit defaults to its first parent.
- A merge commit permits selecting another actual parent and caches immutable summaries by full commit and parent OID.
- A root commit uses Git's initial-commit comparison support and has no hardcoded empty-tree object ID.

### Patch review

- File-name actions open a full review session; caret actions toggle one bounded unified inline preview per change group.
- Patches are fetched one file at a time and capped at 1 MiB and 10,000 lines before browser parsing.
- Patch commands disable external diffs, text conversion, and color, and place `--` before browser-derived paths.
- Binary, submodule, deleted, renamed, untracked, oversized, and unavailable states remain explicit.
- The full modal defaults to split at a measured content width of 900 CSS pixels and unified below it.
- A manual unified or split choice survives modal resizing, and narrow manual split remains horizontally scrollable.
- Line-number gutters anchor ephemeral old-side or new-side single-line and same-side range annotations.
- Annotations, selected file, loaded patch snapshots, layout override, and wrapping choice live only in the mounted modal.
- Local Git events mark an open local review stale without replacing frozen patches or moving annotations.
- Commit reviews are immutable by full commit and parent OID.
- Review packets include Project and repository identity, scope, comparison or commit identity, local `HEAD`, patch hashes, ordered annotations, and bounded hunk excerpts.
- Copying review packets or raw patches bypasses clipboard-history capture.
- Sending opens the existing explicit agent picker and never writes directly to a PTY.

### Refresh and mutation boundary

- Overview measurement is explicit drawer work with concurrency four and does not expand the five-second session monitor.
- Map refreshes on Git and worktree events; an open Log refreshes its graph while retaining immutable commit caches.
- Explicit Refresh covers Git changes created outside swe-mux event paths.
- The Git surface mutates only through the existing worktree create and remove operations.
- It does not stage, unstage, commit, reset, switch, fetch, merge, rebase, prune, or discard files.
- Removal validates the exact current worktree root, refuses the main tree and live-session roots in the UI, and requires explicit force before Git may discard uncommitted files.

## Spawning a session into a worktree

Parallel agent work needs a session *inside* the worktree, which can collide with spawn
containment: `resolve_contained_cwd` refuses any cwd outside the owning Project's root,
and provider-managed worktrees may live outside that root.

- `POST /api/git/worktrees` accepts an optional `spawn` object. When present, the worktree
  is created first and a session is then started with its cwd forced to the new tree; the
  caller's own `cwd` is ignored, so this cannot be used to redirect a session elsewhere.
  `spawn.project_id` is required, and the rest of the body is an ordinary spawn request.
- **The worktree is the durable artefact, so spawn failures are reported, not raised.**
  The response always carries `spawn.status` — `not_requested`, `spawned` (with
  `session_id`), or `error` (with `error`) — and a failed spawn never unwinds the worktree
  or fails the request. The caller retries the spawn alone.
- Containment is widened by exactly one allow-list, `resolve_listed_cwd`, keyed on
  `git worktree list --porcelain` output. Git is the authority on which paths are worktrees
  of a given repository, so this admits parallel checkouts of the same codebase without
  admitting arbitrary absolute paths. Only worktree **roots** qualify; a subdirectory of a
  worktree is not something Git reported and is refused.
- The git query runs **only** when plain containment has already failed, so ordinary spawns
  into the Project root or a subdirectory pay nothing for it.
- **Project Actions deliberately do not get this widening.** An action is repo-authored
  script content, so its reach stays bounded by the Project root; only spawns, which are
  user- or client-initiated, can reach a sibling worktree.
- Project config (`.swe-mux/`) is still read from the **Project root**, not the worktree.
  The session's cwd is the worktree, which is what the agent and its `CLAUDE.md` discovery
  care about, but per-worktree project configuration is not a thing yet.
- **Dependency bootstrap is not performed.** A fresh worktree has no `.venv` or
  `node_modules`, and running a repo-authored setup script from an HTTP endpoint is
  untrusted code execution that belongs behind the Project Actions trust gate
  (`project-actions.md`). Until that is wired, the spawned agent runs `.worktree-setup`
  or installs its own dependencies.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Project-scoped review domain and bounded patch runner: `src/swe_mux/git_review.py`
- Routes: `src/swe_mux/server.py`
- Drawer tab and defensive response parsing: `frontend/src/GitTab.tsx`, `frontend/src/gitWorktrees.ts`
- Shared file rows, lazy renderer, modal, and pure review state: `frontend/src/GitFileRow.tsx`, `frontend/src/LazyGitDiff.tsx`, `frontend/src/GitDiffView.tsx`, `frontend/src/GitReviewModal.tsx`, `frontend/src/gitReview.ts`
- Pane-header chip and the `mux:git-changed` re-dispatch: `frontend/src/App.tsx`
