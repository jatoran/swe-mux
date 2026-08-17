# Git awareness and worktrees

## What it is

- Attached sessions poll the latest accepted live cwd (or spawn cwd until live telemetry is available) for HEAD, branch, dirty count, upstream divergence, linked-worktree identity, working-tree root, lines changed against HEAD, and lines and files changed against the comparison ref.
- User-initiated worktree API wraps `git worktree` without performing other mutating git operations.
- Durable provenance connects a commit to the session and agent run whose evidence observed or created it without changing the repository.

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

### Every field is a property of the checkout

`GitState` describes the working tree a session is in, never the session.
`git status` answers for the whole repository however it is invoked, so two agents in one
checkout cannot be told apart by anything Git can answer, and the monitor's deduplication by
checkout is a consequence of that rather than a cause of it.
`GitState.root` is served so a client can say so: a per-session row printing a per-checkout
quantity invites reading it as "what this agent changed", and `root` is the only key by which
two rows can be known to be quoting one measurement.
The sidebar marks a quantity whose root carries more than one live session
(`design/features/ui.md`, "Configurable session rows").

### Session-to-commit provenance

- `GitProvenanceService` consumes existing `tool_use`, `tool_result`, and `git_changed` events and never writes a hook, trailer, ref, note, or repository file.
- A recognized successful `git commit` command snapshots the session, run, Project, checkout root, and starting HEAD at tool start, then reads the resulting HEAD after the paired tool result.
- Command recognition is deliberately narrow.
  It accepts explicit ordinary `git commit` and `git commit --amend` invocations from command tools and rejects repository-redirection flags such as `-C`, `--git-dir`, and `--work-tree` rather than interpreting shell quoting.
- Two questions are recorded separately, because they have separate answers.
  The **committer** is the one session whose process ran `git commit`; the **contributors** are the sessions whose file writes the commit contains.
  Each row therefore carries a `role` of `committer`, `contributor`, or `observer` alongside the `relationship` the reference underwent, and one commit can hold several rows.
- The commit a command produced is isolated by object, not by reading `HEAD` back.
  The service lists `started_head..current_head` and selects within it: a single commit settles it, otherwise the command's own `-m` subject decides, then the command's time window.
  Reading `HEAD` after the command answers "what is on top now", which is a different question and names the wrong commit whenever a sibling session commits in between.
- An isolated commit records `created` or `rewrote` with `exact` confidence.
  Exact means swe-mux observed that commit appear across that session's successful commit-tool boundary; it is session provenance, not a cryptographic claim about the Git author identity.
- A shared checkout is **not** ambiguity and never downgrades a committer.
  A shared `HEAD` is a fact about the starting point, not about the commit event, and the retired rule that treated it as ambiguity stamped nearly every commit in a multi-session checkout `ambiguous` even on the path that watched the exact session run the command.
- `ambiguous` is reserved for two named cases: several commits in one command's range that neither subject nor time can tell apart, and a reference that moved many commits at once (a merge or a rebase).
- A HEAD transition first found by the checkout monitor records `observed` with `correlated` confidence.
  It proves that the session occupied that checkout when the transition was observed, not that the session ran the mutating command.
- Contributors are matched from Tier 0 write facts against the commit's own changed files, read once per commit with `git diff-tree`.
  A write is attributed to a file when its normalized target is one the commit changed and the write can be placed in that checkout: an absolute target inside the worktree places itself, and a relative one is placed by its session's checkout.
- A write *result* fact is read too, but only as content evidence, never as placement.
  A result hash is the CLI's rendering of what happened for most harnesses and the file's real bytes for a codex `patch_apply_end`, and nothing in the fact says which, so hash equality is what decides.
  This is what keeps a codex write attributable at all: codex applies patches through its shell/exec tool, so its call records as a command and the result is the only fact carrying the written path.
- A whole-file write is confirmed by content: the SHA-256 of the bytes Git stored equals the SHA-256 the adapter took of the bytes the agent wrote, and that contributor is `exact`.
  Content confirmation is unavailable for an edit tool (which hashes the replacement fragment), so those are matched by path and time and recorded as `correlated`.
  A Git object id is never compared with a content hash; it is SHA-1 over a `blob <len>\0` header rather than a digest of the bytes.
- Measured live per harness (`tests/test_live_git_attribution.py`, which drives the real CLIs): claude and pi confirm by content, codex confirms by content through its `patch_apply_end` result, and omp records a relative target with no content hash at all and is therefore matched by path.
  Each harness's expected strength is declared in that canary, so a harness added to the registry fails the guard until its strength is stated rather than inheriting one silently.
- Without a content match only the last write to a path counts, because an earlier write another session replaced is not in the commit.
- The contributor set is plural by design.
  One session staging files and another running `git commit` resolves to committer B and contributors {A, B}, an answer no Git tool records because Git keeps one configured author.
- Contributor attribution needs Tier 0, which is a per-Project opt-in.
  With it off the contributor set is empty and committer attribution is unaffected; an empty set is never a claim that the commit had one author.
- Attribution stays observational.
  No `GIT_AUTHOR_*` value, commit trailer, hook, or identity is ever injected to make the answer easier, because that would mutate the bytes the agent committed.
- The monitor emits `previous_head` only when the old and new observations name the same checkout.
  The initial daemon poll establishes a baseline and does not create provenance for commits that predate the session evidence.
- Rows are unique by session, run, checkout root, and commit.
  A later stronger observation promotes the existing row while retaining its earliest observation time, so polling cannot duplicate or downgrade exact tool evidence.
- Commit metadata is copied into the row at capture time: full OID, parent OIDs, subject, Git commit time, previous HEAD, checkout root, session label, Project, run, evidence source, tool-call id, source event sequence, role, match method, and the contributed file paths.
  This keeps the association readable after a branch moves or the worktree is removed.
- The match method names how the attribution was made, so a reader can judge it: `command_range`, `command_subject`, `command_window`, `command_ambiguous`, `monitor_head`, `monitor_range`, `write_content`, `write_path`, `reattributed_ancestry`, or `transcript_*`.
- Contributed paths are evidence, not classification.
  A later stronger observation that identified none of them never erases the ones an earlier pass proved.
- Every commit is answered once regardless of how many sessions occupy the checkout: contributor resolution is claimed per commit, so a HEAD move seen by ten sessions reads the commit once.
- Provenance follows History lifecycle rather than the optional Tier 0 retention window.
  Deleting a Project removes its rows, and deleting a History run removes the rows bound to that run.
- Capture status is part of daemon background health and reports running state, captured rows, dropped observations, pending commit calls, and the last error.
  Failures are rate-limited in logs and never interrupt Git polling or terminal event delivery.
- Historical provenance is an explicit, idempotent operator action, never a startup migration.
  `python -m swe_mux.git_provenance_backfill PROJECT` is read-only and reports the proposed evidence classes; `--apply` writes the same plan in one bounded transaction.
  `--all-projects` sweeps every registered Project instead of one, which is what re-attributing existing history needs.
- The pass has three parts: import commits from native transcripts, promote rows the retired shared-checkout rule downgraded, and derive contributors for the commits already recorded.
- Re-attribution touches live command evidence only.
  It re-checks that the row's recorded previous HEAD really is an ancestor of the commit, refuses when two sessions' commands claim one object, and leaves a transcript match's confidence alone because ancestry cannot improve an identification the transcript made.
- The contributor pass runs the same matching code as the live path, so a historical answer and a live one cannot drift apart.
  It is bounded: the newest 500 recorded commits, write facts no older than `--since-days` (30 by default, matching Tier 0 retention), and 400 object reads.
- The importer reads provider-native tool calls and their call-id-paired results, then validates candidate objects against the Project repository without executing transcript text.
  A unique output hash is `exact`, a unique commit-subject/time match is `correlated`, and timestamp-only or cross-session matches are `ambiguous`.
  Failed, unpaired, unresolved, and multiply matching commands are not written.
- Retained Tier 0 command identities recover the actual mux session for resumed conversations when available.
  Older evidence falls back to the canonical History run, while the row always keeps that run id for History lookup.
- The importer logs only operation ids and aggregate counts to the size-rotated `<data_dir>/git-provenance-backfill.log`; transcript commands and outputs never enter that log.

### Branch-scoped comparison

`compare_ref`, `compare_added`, `compare_removed`, and `compare_files` measure the working tree
against its **merge base** with the checkout's comparison ref, so they cover committed and
uncommitted work together.

- They exist because `added`/`removed` are measured against HEAD and therefore drop to zero the
  moment a session commits. A worktree-per-branch fleet that commits as it goes reports `+0 -0`
  on the HEAD-scoped pair while having changed a great deal.
- The merge base, not the ref itself: diffing a branch straight against a base that has advanced
  reports the base's inbound commits as this branch's deletions, which reads as work destroyed
  rather than work not yet merged.
- The ref is resolved by `git_review.resolve_comparison_ref`, the same inference the Git drawer
  uses, so the sidebar and the drawer cannot disagree about which base a number is measured from.
  `git_review.infer_comparison` is that function plus the drawer's bounded selector candidate
  list, which the monitor must not pay `for-each-ref` for on a five-second cadence.
- Resolution is cached per `(root, project override)` for `COMPARE_REF_TTL_SECONDS`; its answer
  changes when a remote HEAD is re-pointed or a branch appears, never between two polls seconds
  apart. A cached ref that stops resolving is dropped immediately rather than waiting out its TTL.
- The measurement is memoized per `(root, ref)` on `(comparison oid, HEAD, dirty_hash)` — all
  three, because the branch diff moves for a strictly larger set of reasons than the working-tree
  diff: committing changes it while leaving the dirty fingerprint untouched, and the base advances
  underneath it. On the memoized path the poll costs one extra `rev-parse`, issued inside the
  existing parallel gather.
- All four are `None` when no base resolves or the diff failed. A zero would claim a branch
  identical to its base, which is the one thing a reader would act on.
- The poll's deduplication key is the checkout **and** its comparison override, because two
  Projects may point at one directory with different bases; collapsing them onto the cwd would
  serve one Project the other's number. The override is injected into `GitMonitor` as a
  `project_id -> ref` callable, so the monitor keeps knowing nothing about the Project registry,
  and a lookup that raises degrades to automatic inference rather than to no Git state.

## Git drawer tab

- The Project-scoped Git tab has Map, Log, and Provenance readings of one repository.
- Map reports every registered worktree, its exact root, checked-out branch or detached commit, locks, prune warnings, live-session attribution, local changes, and comparison-ref changes.
- A prunable worktree is unmeasured and shown as unavailable rather than clean.
  Overview measurement first requires Git's reported top-level to equal the exact listed root, so a broken nested worktree cannot inherit status from an enclosing checkout.
- Each collapsed Map row gives the branch identity its own bounded line and wraps status metrics on a separate line, so divergence and state cannot overlap the title at narrow drawer widths.
  The worktree indicator is inline with the identity, while the left-aligned expand control is inline with the metrics; neither control reserves an otherwise empty row.
  The worktree leaf appears beside the branch only when it adds information; the exact root remains in expanded detail.
  Zero comparison divergence is omitted rather than rendered as separate ahead and behind labels.
- Local changes are separate `CONFLICTS`, `UNSTAGED`, and `STAGED` groups.
- Unstaged means working tree versus index, staged means index versus `HEAD`, and conflicted means unresolved index state.
- A path changed on both sides of the index appears independently in staged and unstaged groups.
- Branch changes compare the worktree `HEAD` with the merge base of the effective comparison ref.
- Comparison ahead and behind counts are distinct from the session monitor's upstream ahead and behind values.
- A nonzero comparison-ahead count uses the graph palette's violet emphasis so committed branch work remains visible beside a clean local tree.
- Every group reports exact file count, text additions, text deletions, binary count, and at most 200 typed file rows.
- Untracked content inspection is limited to the 200 returned rows and a 16 MiB aggregate read budget.
  A deleted ignore file therefore cannot make Map open every file in a surviving dependency tree before applying the response limit.
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
- The log context strip names the main-tree branch and commit, effective comparison ref, linked-worktree count, and `all refs` scope before the graph.
- All refs remain in the graph so a local branch without a registered worktree cannot disappear from repository history.
- Git's lane geometry remains authoritative: a linear branch ahead of its base stays on one lane rather than receiving a fabricated fork.
- Colored lane edges and solid commit nodes preserve the terminal graph while making crossings and tips easier to trace.
- Commit decorations render in priority order as Project-root `HEAD`, comparison ref, checked-out worktree refs, tags, and muted other refs.
- A commit at a registered checkout tip carries `MAIN TREE`, `WT <leaf>`, or a collapsed `<count> WORKTREES` marker derived by exact commit OID.
- Ref and worktree markers wrap below the commit subject so narrow drawers retain identity instead of clipping it.
- Connector-only rows are inert.
- Expanding a commit lazily loads its typed file summary and reuses the shared file rows.
- Expanding a commit also shows its whole message, subject and body, wrapped and unclamped; the collapsed row keeps the one elided subject line it has room for.
- Commit messages are served capped at 16,384 characters against a pathological commit, and a response without one still parses.
- An ordinary or merge commit defaults to its first parent.
- A merge commit permits selecting another actual parent and caches immutable summaries by full commit and parent OID.
- A root commit uses Git's initial-commit comparison support and has no hardcoded empty-tree object ID.
- Commits with recorded provenance show their session-link count in the collapsed row and the associated session, role, confidence, and contributed files when expanded.

### Provenance ledger

- Provenance lists durable session-to-commit associations newest first for the selected Project.
- Each row shows the short commit OID, copied subject, session label, run-id prefix, what the session did (committed, amended, wrote N files in it, or was in the checkout), confidence, the contributed file paths, checkout root, and first observation time.
- Ambiguous rows state which of the two named cases applies: concurrent commits in one window, or a reference that moved many commits at once.
- The ledger accepts any evidence source rather than an allowlist of them.
  An allowlist silently discarded every imported row, whose source is a compound `transcript_backfill:<method>`.
- The ledger is read-only and refreshes on both Git state and provenance events.
- Opening a History transcript queries the same ledger by History run id and shows a bounded `Commits from this run` strip, each row naming what the run did for that commit.
  A run appears there for a commit it contributed files to as well as for one it made, which is the point of separating the two questions.

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
- Review packets include Project and repository identity, scope, comparison or commit identity, local `HEAD`, available session provenance, patch hashes, ordered annotations, and bounded hunk excerpts.
- Copying review packets or raw patches bypasses clipboard-history capture.
- Sending opens the existing explicit agent picker and never writes directly to a PTY.

### Refresh and mutation boundary

- Overview measurement is explicit drawer work with concurrency four and does not expand the five-second session monitor.
- Concurrent overview requests for the same Project root and comparison ref share one daemon computation.
  A timed-out or disconnected browser cannot create an overlapping Git-process storm by refreshing again.
- Map refreshes on Git and worktree events; an open Log refreshes its graph while retaining immutable commit caches.
- Explicit Refresh covers Git changes created outside swe-mux event paths.
- The Git surface mutates only through the existing worktree create and remove operations.
- It does not stage, unstage, commit, reset, switch, fetch, merge, rebase, prune, or discard files.
- Removal validates the exact current worktree root, refuses the main tree and live-session roots in the UI, and requires explicit force before Git may discard uncommitted files.
- Worktree add, repair, and remove run as daemon-owned mutations with a 30-minute deadline rather than the four-second read-only Git deadline.
  Client cancellation cannot interrupt a mutation after Git starts changing repository state.
- Removing an exact prunable root whose directory still exists but whose `.git` link is missing first runs path-specific `git worktree repair`, then validates the resulting exact registration, `.git` link, and reported top-level before removal.
  Post-state validation always runs because Git repair can restore the requested root while returning nonzero for another repair problem; a usable exact root continues to removal and an unusable root returns the repair failure.
  Missing directories and other non-repairable prune states return a typed conflict instead of globally pruning unrelated worktrees.
- A nonzero remove result is also re-listed.
  If Git already removed the exact registration but could not delete a filesystem object, the daemon atomically moves the orphaned directory into the worktree parent's `.swe-mux-orphans` directory and reports successful removal with cleanup metadata.
  If quarantine fails, the typed error states that registration is already gone and identifies the remaining exact path.
- A failed removal refreshes Map because repair or an interrupted Git command may have changed the row even though the request failed.
- Mutation logs carry one operation id across start, repair, completion, failure, timeout, Git result, force intent, exact path, and duration.

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
  A client-only unpanned pending session appears and receives focus immediately, with the selected backend, worktree path, and explicit setup status.
  While selected it occupies the full workspace without changing the existing pane tree; selecting another session restores that tree with no setup placeholder left visible.
  The daemon session replaces that pending row in place.
  If the user moves elsewhere before completion, replacement preserves the newer focus.
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
- Durable session-to-commit capture and explicit historical import: `src/swe_mux/git_provenance.py`, `src/swe_mux/git_provenance_backfill.py`, `src/swe_mux/history.py`
- Project-scoped review domain and bounded patch runner: `src/swe_mux/git_review.py`
- Routes: `src/swe_mux/server.py`
- Bootstrap runner: `src/swe_mux/worktree_setup.py`
- Drawer Map, Log, Provenance ledger, Run launcher, and defensive response parsing: `frontend/src/GitTab.tsx`, `frontend/src/gitWorktrees.ts`, `frontend/src/ProjectRunMenu.tsx`, `frontend/src/worktreeLaunch.ts`
- Shared file rows, lazy renderer, modal, and pure review state: `frontend/src/GitFileRow.tsx`, `frontend/src/LazyGitDiff.tsx`, `frontend/src/GitDiffView.tsx`, `frontend/src/GitReviewModal.tsx`, `frontend/src/gitReview.ts`
- Pane-header chip and the `mux:git-changed` re-dispatch: `frontend/src/App.tsx`
