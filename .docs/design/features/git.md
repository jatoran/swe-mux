# Git awareness and worktrees

## What it is

- Attached sessions poll the latest accepted live cwd (or spawn cwd until live telemetry is
  available) for branch, dirty count, upstream divergence, linked-worktree identity, and
  lines changed against HEAD.
- User-initiated worktree API wraps `git worktree` without performing other mutating git operations.

## Operations

- Canonical Project identity probes for worktree root, common Git directory, and origin run
  concurrently behind one timeout window. Results are cached briefly per canonical cwd so a burst
  of terminal launches does not repeatedly cross the Git process boundary.
- Poll cwd values with at least one attached terminal pane every cadence tick; sweep **every**
  session once a minute, and always on the first tick after daemon start. Deduplicate by cwd and
  cap concurrency. Branch/status/upstream/git-dir calls run in parallel with bounded timeouts.
- The sweep exists because `GitState` is a cache of a derived observation on a record that
  outlives the daemon that wrote it. Polling only attached sessions froze that cache for as long
  as a pane stayed closed, so a value produced by code that was later corrected survived the
  correction. It is affordable because deduplication is by cwd: a fleet of thirty sessions in one
  checkout costs one read, so cost scales with distinct working directories rather than sessions.
- A checkout is a **linked worktree** when its `--absolute-git-dir` differs from its
  `--git-common-dir`; `GitState.worktree` is then the checkout's leaf directory name.
  Comparing the two paths is the only check that stays correct for bare repositories and
  `.git`-file submodules, where comparing directory names does not.
  Both paths must be resolved against the directory git was run in before they are compared.
  `--absolute-git-dir` promises an absolute answer for the git dir alone; `--git-common-dir`
  still replies relatively whenever it can (`.git` from a repository root, `../.git` from a
  subdirectory), and relative to git's own working directory rather than the toplevel.
  Resolving those against the daemon's process directory instead makes every primary checkout
  compare unequal to itself and report as a worktree named after the repository folder.
- Lines added and removed come from `git diff --numstat HEAD`, memoized per repository root on
  the working-tree fingerprint (`GitEvidence.dirty_hash`) the cheap poll already computes.
  The diff therefore runs when the change set actually moves, not once per session and not once
  per five-second poll, and every session sharing a checkout reads one measurement.
  A clean tree answers 0/0 with no subprocess at all.
- The counts cover **tracked** changes only: an untracked file raises the dirty count but has no
  content to compare, so it contributes no lines.
- `added`/`removed` are `None` when the measurement could not be made — no HEAD yet, or a failed
  diff — which is deliberately distinct from a measured `0`. A display that conflates the two
  reports a clean tree for a repository it merely failed to read.
- OSC-driven targets are existing local directories, debounced for 1.25 seconds, and limited
  to 12 accepted switches per session per minute before Git polling follows them. Invalid,
  remote, fragmented-spam, and over-limit telemetry cannot create subprocess churn.
- Agent panes report their cwd through **hooks**, not OSC 7 (`note_hook_cwd`,
  `runtime_cwd_source: "hook"`). OSC 7 comes from a shell drawing its prompt and a CLI holding
  the terminal draws none, so before this an agent session had no live cwd at all and `git_cwd`
  fell back to the spawn directory for its whole life. A Claude session working inside a native
  worktree therefore had its Git chip, diff, and comparison reporting the primary checkout - a
  different branch and a different set of changes than the one the agent was editing. Same
  validation and the same 12-per-minute rate limit as OSC 7, but no debounce: a hook reports a
  directory the CLI has already settled on. Only hooks that speak for the session's own
  conversation are accepted, so a nested child CLI cannot move its parent's cwd.
- A live cwd never re-homes the session's Project. `project_id`, `repository_id`, and
  `project_scope_id` stay with the checkout the session was spawned in: a worktree is the same
  Project as the tree it was cut from, and a session that steps into one must not disappear from
  its sidebar group. `runtime_project_scope_id` records what the live cwd resolves to and is
  reporting only.
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
- Expanding a commit also shows its whole message, subject and body, wrapped and unclamped; the collapsed row keeps the one elided subject line it has room for.
- Commit messages are served capped at 16,384 characters against a pathological commit, and a response without one still parses.
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
- The Project Run menu exposes this as `New worktree session…` with backend, new branch, optional start point, and absolute path fields.
  The suggested branch convention is `worktree-<name>`.
  Branch-field whitespace is normalized to `-` before both branch creation and path derivation.
  It suggests `<worktree_root>/<project-name>-<project-id>/<branch>` with filesystem-safe path segments.
  The launcher waits only for worktree creation, closes once the durable tree exists, then calls `POST /api/git/worktrees/session` for setup and spawn in the background.
  The completed session appears under its Project without changing the current Project, pane, tab, or focus.
  `worktree_root` is a global Settings value under Git and worktrees; its empty/default form resolves to `<data_dir>/worktrees`, normally `~/.mux/worktrees`.
  The daemon creates a missing parent hierarchy only when the target remains below that configured root.
  A manually entered target outside the configured root retains the existing rule that its parent must already exist.
  Changing the setting affects only later suggestions and never moves existing worktrees.
- **The worktree is the durable artefact, so spawn failures are reported, not raised.**
  The response always carries `spawn.status`: `not_requested`, `spawned` with `session_id` and the `session` snapshot, or `error` with `error`.
  A failed spawn never unwinds the worktree or fails the request.
  `POST /api/git/worktrees/session` can retry setup and spawn against the durable path without repeating `git worktree add`.
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
  care about, while every worktree shares the committed Project configuration.
- Worktree creation with `spawn`, and the split `POST /api/git/worktrees/session` flow, run bootstrap before the session process starts.
  `[worktree].setup_command` in the primary checkout's `.swe-mux/config.toml` is the explicit override.
  When no override exists, an executable `.worktree-setup` in the new checkout is the convention.
  Windows treats a shebang script as executable when its interpreter can be resolved, including Git Bash for this repository's Bash convention.
  Setup is a daemon-owned subprocess with an 1800-second timeout, bounded captured output, and process-tree cleanup on cancellation or timeout.
  Its output is seeded into the new session's supervisor-owned scrollback before the harness starts.
  Setup failure never removes the worktree and never blocks session creation.
  The terminal scrollback and `spawn.setup` result state that the tree is not bootstrapped.
- Harness preparation is adapter-owned and best effort.
  Claude atomically clones the primary checkout's `~/.claude.json` trust entry to the canonical forward-slashed worktree key, copies the primary `.claude/settings.local.json` permission allowlist, and adds `--add-dir <primary-root>`.
  Codex atomically writes `trust_level = "trusted"` under the canonical worktree key in the runtime `CODEX_HOME` config and grants the primary root through `sandbox_workspace_write.writable_roots`.
  OMP and shell currently need no trust preflight or extra-directory argument.
  A preparation failure falls back to the harness's interactive behavior and does not fail the spawn.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Project-scoped review domain and bounded patch runner: `src/swe_mux/git_review.py`
- Routes: `src/swe_mux/server.py`
- Bootstrap runner: `src/swe_mux/worktree_setup.py`
- Drawer tab, Run launcher, and defensive response parsing: `frontend/src/GitTab.tsx`, `frontend/src/gitWorktrees.ts`, `frontend/src/ProjectRunMenu.tsx`, `frontend/src/worktreeLaunch.ts`
- Shared file rows, lazy renderer, modal, and pure review state: `frontend/src/GitFileRow.tsx`, `frontend/src/LazyGitDiff.tsx`, `frontend/src/GitDiffView.tsx`, `frontend/src/GitReviewModal.tsx`, `frontend/src/gitReview.ts`
- Pane-header chip and the `mux:git-changed` re-dispatch: `frontend/src/App.tsx`
