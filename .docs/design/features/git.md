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
- A recognized successful commit-creating command snapshots the session, run, Project, checkout root, and starting HEAD at tool start, then reads the resulting HEAD after the paired tool result.
- Command recognition is narrow in *form* and complete in *subcommand*.
  It accepts `commit`, `merge`, `cherry-pick`, `revert`, `rebase`, and `am` from command tools, rejects repository-redirection flags such as `-C`, `--git-dir`, and `--work-tree` rather than interpreting shell quoting, and rejects the forms that resolve or abandon an operation (`--abort`, `--quit`, `--skip`, `--no-commit`).
  Matching the literal token `commit` alone meant the most common commit-creating command in a worktree workflow — `git merge`, which both reconciles and lands — produced no committer evidence at all, so the session that ran it was recorded exactly like the bystanders whose HEAD it dragged along.
- Recognizing a command is not believing it made a commit.
  Argv cannot tell a fast-forward from a merge, because a plain `git merge` fast-forwards whenever it can, so the *outcome* decides: a recognized command whose reference gained nothing it wrote records a movement and no committer row.
- Three questions are recorded separately, because they have separate answers.
  The **committer** is the one session whose process ran the command; the **contributors** are the sessions whose file writes the commit contains; the **reference movement** is what a *checkout* did and names no session at all.
  Each session row carries a `role` of `committer`, `integrator`, `contributor`, `branch_author`, or `observer` alongside the `relationship` the reference underwent, and one commit can hold several rows; movements live in their own checkout-keyed table.
- The commit a command produced is isolated by object, not by reading `HEAD` back.
  The service lists `started_head..current_head` and selects within it: a single commit settles it, otherwise the command's own `-m` subject decides, then the command's time window.
  Reading `HEAD` after the command answers "what is on top now", which is a different question and names the wrong commit whenever a sibling session commits in between.
- An isolated commit records `created` or `rewrote` with `exact` confidence.
  Exact means swe-mux observed that commit appear across that session's successful commit-tool boundary; it is session provenance, not a cryptographic claim about the Git author identity.
- A shared checkout is **not** ambiguity and never downgrades a committer.
  A shared `HEAD` is a fact about the starting point, not about the commit event, and the retired rule that treated it as ambiguity stamped nearly every commit in a multi-session checkout `ambiguous` even on the path that watched the exact session run the command.
- `ambiguous` is reserved for one named case: several commits one command authored that neither subject nor time can tell apart.
  A reference moving many commits at once is **not** one of them.
  That was a second retired rule of the same shape as the shared-checkout one: it described a merge and a rebase — two structurally distinguishable events — as a single undecidable one, on a code path that never asked git which had happened.
  Measured against this repository's own ledger before the change, every move recorded as undecidable classifies, and not one of them was a rebase.

#### A landing merge has more than one author

- A merge commit is the one shape where "the session that ran the command" and "the session whose work this is" are different answers, and giving it one `committer` row always chose the first.
  In this repository's own flow that is exactly backwards: an orchestrator session runs `git merge master` inside another agent's worktree, resolves the conflicts, commits the merge, and fast-forwards the trunk onto it - so the commit that carries a branch onto `master` was recorded as the merger's, with `created`/`exact`, the ledger's strongest claim, while the branch's own agent appeared nowhere on it.
- The creator of a merge commit is therefore an **integrator** (`relationship: merged`) rather than a committer.
  Nothing about the evidence is weakened: it is the same observation of the same session running the same command across the same successful tool boundary, and it ranks identically, so a row written before the distinction reclassifies in place. Only the claim is narrowed to what was actually done.
- The parent count is the whole test, and it is a property of the object rather than of the command.
  A plain `git merge` fast-forwards whenever it can and leaves no merge commit at all, and a merge commit reached by any other route is still a merge.
- **What a merge itself authored is the conflict resolution, and only that.**
  It is read with `git diff-tree -c`, the combined diff, which lists exactly the paths that match *none* of the parents.
  The choice of `-c` is the scoping rule rather than an optimization: a first-parent diff of a landing merge is everything the trunk brought in, and a `-m` diff is that plus the entire branch, so either one would attribute one session's whole branch to whoever ran the merge.
  A file taken wholesale off either side never appears in the combined diff; a file somebody settled by hand always does.
  Those paths go through the same contributor matching as any other commit, so the merger is credited with the bytes it decided - which the previous behaviour recorded nowhere at all, because plain `diff-tree` says nothing about a merge.
  The combined raw format is not the ordinary one (one leading colon per parent, N+1 modes, N+1 object ids, one status letter per parent) and the single-parent parser reads it as nothing, which is indistinguishable from "this merge changed no files".
- **Whose branch a merge carries is answered from the ledger, never guessed.**
  Git says *which* commits the merge's own side had that the other side did not - `rev-list p0 ^p1...`, the symmetric half of the first-parent rule that classifies the move - and the rows already written say *whose* those commits are.
  The first parent is that side deliberately: it is the merge's own line of development, and for a reconcile run inside a branch's worktree it is exactly that branch's work.
  Every session the ledger credits for one of those commits, as committer, integrator, or contributor, gets a `branch_author` row on the merge.
  Occupancy is excluded, because "had the checkout open" is not authorship of a branch; retracted and ambiguous rows are excluded, because a claim that was not an answer where it was written does not become one here.
- Drawing branch authors from contributor rows as well as committer rows is not a convenience.
  Measured against the seven landings this repository performed on 2026-08-21, two of the seven branch tips had **only** a contributor row and no committer row at all, so a committer-only rule would have silently missed two of the seven agents it exists to name.
- A `branch_author` row carries **no** contributed paths.
  Those files are in that session's own commits, and copying them onto the merge would put A's branch content on a commit A did not write - the mirror image of the defect this exists to fix, and one that would reach the per-session change map through the provenance seeds.
- It ranks below every direct match and above occupancy: "wrote the branch this merge carries" is a better answer than "had the directory open" and a worse one than "wrote these bytes".
  A merge whose side commits mux never attributed produces no rows rather than a guess.
- Both halves of the answer are surfaced together, so a landing reads as what it was: one session merged it and resolved N files, another wrote the branch it merges.

#### Authorship versus arrival

- Every reference move is classified rather than counted, from three facts and no guesses: whether the old position is still reachable from the new one, whether the new one is reachable from the old (a rewind), and what the reference's own **first-parent** line gained.
- `--first-parent` is the whole discriminator and is not an optimization.
  Full ancestry counts the side branch a merge absorbed, so `git merge master` creating exactly one commit and a two-commit fast-forward both report two, which is precisely the collision that stamped the session that ran the merge `ambiguous`.
- A move that goes forward either **authored** the commits it gained or **received** them.
  A commit counts as authored when its own timestamp falls in the command's window — or, for the monitor, within the widest gap in which a commit can first appear on a HEAD it was written on — *and* the ledger does not already hold it under another checkout that saw it earlier.
- That last clause is the arrival oracle, and it costs no extra Git work: when a worktree branch lands, mux recorded those commits in the worktree minutes before the primary checkout ever saw them.
- The oracle is deliberately one-directional in time.
  "Recorded under another checkout" without an ordering is symmetric — after a landing *both* checkouts hold the commit — and a symmetric test retracts the one true answer along with the noise, reading the worktree that made a merge as a bystander to its own merge commit.
- An observation's *time* survives the withdrawal of the claim built on it.
  The oracle therefore reads when each checkout first held a commit from every row, retracted or not, while "who is already known to have made it" reads only standing rows.
  Conflating the two made the repair pass forget its own inputs and restore exactly the rows it should have kept withdrawn.
- A reference move is recorded once for the checkout it happened to, never once per attached session, and carries the classified kind, how many commits the first-parent line gained, and how many of those were authored.
- A move that authored nothing writes no session row at all.
  This is the single largest source of the ledger's retired noise: a landing fast-forward used to write one `ambiguous` row per session in the checkout, for commits none of them had touched.
- A replay — a rebase, a run of cherry-picks — authors every commit it produced, and all of them belong to the session that ran it, so they are recorded together rather than reduced to one answer plus an apology.
  The run is bounded, and contributor resolution runs for only the first few of them per event, because one command must not turn one event into fifty object reads.
- A HEAD transition first found by the checkout monitor records `observed` with `correlated` confidence, and only for the commits the move authored.
  It proves that the session occupied that checkout when those commits appeared, not that the session ran the mutating command.
- Occupancy is not recorded at all once another session is known to have run the command that created the commit.
  An answered question does not need ten more rows saying nothing, and ten of them buried the answer in the ledger view.
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
- Retraction is the ledger's only weakening operation, and it exists because promotion alone is not enough.
  Every field is gated on evidence rank, so a row that later turned out to record occupancy rather than authorship had no way out: "this session had nothing to do with it" is not a stronger claim than the one it replaces, and so could not arrive as one.
  A retracted row keeps its evidence and its reason, is excluded from every read that does not explicitly ask for it, and is cleared by evidence strictly stronger than what was withdrawn — never by re-observing the same thing.
- Commit metadata is copied into the row at capture time: full OID, parent OIDs, subject, Git commit time, previous HEAD, checkout root, session label, Project, run, evidence source, tool-call id, source event sequence, role, match method, and the contributed file paths.
  This keeps the association readable after a branch moves or the worktree is removed.
- The match method names how the attribution was made, so a reader can judge it: `command_range`, `command_subject`, `command_window`, `command_ambiguous`, `command_<kind>` for a replay the command's nature identifies rather than a selection, `monitor_<kind>` for occupancy during a move of that kind, `write_content`, `write_path`, `merge_branch_line`, `reattributed_ancestry`, or `transcript_*`.
  It stays on the "how was this picked" axis: that a commit was a merge is in the row's own two parent OIDs and in the movement recorded for the checkout, not in the method name.
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
  The sweep skips removed Projects, whose checkout is usually gone; naming one explicitly still imports it.
- The pass has five parts: import commits from native transcripts, promote rows the retired shared-checkout rule downgraded, derive contributors for the commits already recorded, name the branch each merge commit unified, and reclassify the occupancy the monitor wrote before it could tell authorship from arrival.
- Existing merge rows are re-derived by this pass, so a landing recorded before the integrator/branch-author split reads correctly afterwards; nothing is rewritten at startup, because a durable ledger is not something a version bump may silently reclassify.
- The last part is the only one that withdraws rows, and it withdraws exactly two kinds: a session that merely had a checkout open when someone else's work landed in it, and a bystander to a commit whose author is already known.
  It records the movement for the checkout either way, because the movement happened.
- It runs *after* branch authorship and is handed those records, and the ordering is not cosmetic.
  A retraction names a row id and is written last, while a branch-author record promotes the very same `(session, run, checkout, commit)` row - the occupancy row that session had while its branch was merged under it.
  Planning the withdrawal from a snapshot without them withdrew the promotion a moment after it landed: measured on this repository's own ledger, the `voice-dock` landing named its branch's agent and then immediately retracted it as a bystander to the merge that carried its work.
- It re-examines its own previous verdicts rather than skipping them, and restores what it now reads differently.
  A classifier that could not revisit its own mistakes would leave the first version's errors in the ledger permanently, which is the failure the retraction column exists to avoid rather than to create.
  Retractions made for any other reason are left exactly as they are, so reconsideration is not a general licence to overwrite the ledger.
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
- `git_review.branch_changed_paths` is the same measurement asked for *paths* rather than counts,
  and is what the Change Map's branch scope seeds from (`code-graph.md`).
  It diffs the working tree against the merge base — one read covering committed, staged, and
  unstaged work — and lists untracked files separately, because no diff can see those and a file
  a branch has only just created is exactly the one worth drawing.
  Deliberately not a `GitChangeSummary`: that truncates at `GIT_CHANGE_FILE_LIMIT` for a list a
  human reads, while this feeds a graph query that must either cover the branch or report
  `truncated`. `None` means no base resolved, never an empty list.

## Git drawer tab

- The Project-scoped Git tab has **three** readings of one repository: Map, Log, and Provenance.
- **Landing lives inside Map** (`land-queue.md`), which is what a fourth reading called Land turned into.
  Land answered "what is happening to this worktree" beside a Map answering "what is in it", and the split cost more than it bought: the act sat on a surface with a second copy of Map's own list of checkouts and none of the diff that decides whether to press it.
  Moving the act onto the row left the segment holding one Project-wide block, which is not a view.
- Landing is split by what each part is a property of, and drawn in one place each.
  A worktree **row** owns the act: its Land button, its live land state including what a running verification gate reports about itself, a Cancel, and what stopped it last time.
  A compact **strip at the head of the map** owns everything Project-wide: the verification command with its approval and editor, who besides the operator may start a land, and the queue in run order with its history.
  Nothing Project-wide is drawn on a row - a fact true of the Project drawn on a row is drawn once per worktree, which is what the verification block did under each of eight expansions before it moved up.
  The strip is one summary line with the rest behind a disclosure, so the tab still opens on a map; it opens itself only when landing is actually blocked.
- The retired Land segment keeps its palette command and voice phrases, migrated onto Map (`RETIRED_DRAWER_SEGMENTS`), and so does a stored selection and a `drawer.git.land` keybinding.
- The three readings are drawn **in the pane's heading row**, inline with the Project scope, rather than as a full-width strip under a heading.
  Git is the one tab whose heading is always exactly its selected segment's label, so the row above the control was the control's own selected chip spelled out, costing a line of a panel people keep narrow.
  Everywhere else the heading names something the segments do not (`Change Map` under Activity), and those tabs are unchanged.
  It is the same control from the same registry (`DrawerSegmentControl`), with the same keyboard behaviour, drawn compactly and sized to its labels so the Project scope still fits beside it.
- The toolbar under it carries one search box for the current reading at its leading edge; refresh and worktree creation sit together at the trailing edge, and refresh is its glyph alone with an explicit accessible name.

### Searching each reading

- Each reading searches the thing it is a reading *of*, and each searches it where that search is cheapest.
  The three are complementary rather than three routes to one answer, and none of them is a filter over a page that was already fetched.
- **Map** filters client-side over the payload it already has, matching a case-insensitive substring against the branch and the checkout path together.
  Every name it matches on is on screen, so asking the daemon would be a round trip to re-send what the reader is looking at.
  It is a substring rather than a fuzzy match: a filter that matches things the reader cannot see the reason for is worse than one that matches less.
  The count of matches against the total is stated, so a filter that hides forty checkouts never reads as a repository with ten.
- **Log** asks Git, over `--grep` or `--author` (`GET /api/git/graph`).
  The reason to search a log is to reach the commit that is *not* in the first eighty, and a client-side filter over a bounded page can only ever hide rows it already had.
  Patterns are case-insensitive, and literal unless the reader opts into `regex`: `.` and `*` are ordinary characters in a commit subject, and someone typing one means it.
- **`--graph` is dropped while Log is filtering, deliberately.**
  Git draws lanes for a contiguous walk; over a filtered subset the ASCII it emits connects commits that have no such relationship, which is a picture of a DAG that does not exist.
  A filtered row carries a bare node and no lanes, the payload says `filtered`, and the context strip's scope changes from `all refs` to what is being matched so the missing lanes are explained rather than merely absent.
- **Provenance** asks SQLite, with a `LIKE` over `git_provenance.subject` inside one Project's indexed rows - instant, and no subprocess.
  User text is escaped against `%` and `_`, so a subject containing `100%` is matched literally rather than matching everything.
  It covers only the commits swe-mux observed, which is a strictly smaller set than Git's, so its empty state says so and points at Log's search rather than implying the commit does not exist.
  A subject search narrows the reference-movement list to the same commits, because leaving it listing the whole Project under a result set of three reads as the search having failed.

### A Project with no repository

- A Project folder Git knows nothing about is a state of this tab beside its three readings, not an error in it.
  The tab is never hidden for it: the drawer's tab visibility is a user choice, and a folder that is not a repository yet is exactly the case where the one available Git decision has to be reachable.
- The daemon distinguishes that state from every other Git failure with its own `not_git_repository` code, raised by repository identity resolution when Git exits fatal, the folder exists, and it carries no `.git` of its own.
  A missing Git binary, a timeout, and a corrupt or unreadable repository all keep the generic failure, because offering to initialize one of those would reinitialize a repository the user still has.
- In that state the tab replaces Map, Log, Provenance, the comparison control, and the worktree form with the folder's path and a single **Initialize repository** action.
  There is nothing to read, so nothing is rendered present-and-empty.
- Initialization creates the repository and writes a starter `.gitignore` whose language sections are chosen from marker files already in the folder (Node, Python, Rust, Go), over a fixed base covering secrets, environment files, and operating-system noise.
- **Nothing is staged and no commit is made.**
  `git init` over existing work is reversible by deleting `.git`; a first commit that swept in a stray `.env` or a virtualenv is not, and an ignore file inferred from filename probes is not trustworthy enough to make that call for someone.
- An existing `.gitignore` is never rewritten.
  A folder can carry one long before it carries a repository, and it is the user's file either way.
- The branch is named `main` unless the host sets `init.defaultBranch`, in which case Git's own configured answer stands.
- The daemon re-resolves the folder's repository state inside the request rather than trusting what the client last read, and refuses an already-tracked folder with `already_initialized`.
- Success emits `git_changed` for the Project, so every other client re-reads rather than holding the pre-init view.
- The no-commit contract leaves HEAD unborn, and `git worktree add` cannot branch from an unborn HEAD.
  Worktree creation refuses that state before mutating, with a typed `repository_has_no_commits` error naming the fix (make a first commit) - a deliberate consequence, not a reason to auto-commit.
  An explicit `start_point` skips the check, because Git resolves that ref without HEAD.
- The assistant's `create_project` tool may chain this initialization for a brand-new project folder (`assistant.md`); the contract is unchanged there - nothing staged, no commit.

### Reaching a session from the repository

- Every place the tab names a session is a link to that session, in all three views: a worktree's live occupants, a commit's session links, and the provenance ledger.
- One destination rule serves all three, decided by the session's own liveness rather than by which view named it.
  A live session is focused in its pane, activating an already-open tab instead of duplicating it.
  Anything ended goes to its History conversation, which is where its work now is.
  An entry with neither is inert and says so, rather than offering a click that resolves to nothing.
- Lists open at the pointer, so the row that was pressed stays visible and identifiable.
  The list is a dismiss level of its own, so back and Escape close it rather than the drawer under it.
- A session is named by the same rule as the sidebar and the tab strip, so one session has one name everywhere.
  The daemon resolves the current name for every provenance row: from the live session when the fleet still holds it, from the row's History conversation otherwise.

### Worktree occupancy

- A worktree's live-session count excludes ended sessions, whose processes no longer occupy the checkout and therefore no longer block a worktree removal.
  A session still starting up does occupy it.
- The count is a control rather than a label: it opens that worktree's sessions at the pointer, and it is a sibling of the row's expand button rather than a span inside it.
- Map reports every registered worktree, its exact root, checked-out branch or detached commit, locks, prune warnings, live-session attribution, local changes, and comparison-ref changes.
- Worktrees list most recently active first, and this is now the tab's only list of checkouts: the branch a reader just finished work on is the one they came here to act on, and the Land control is on that row rather than on a second list that could disagree about the order.
  The main tree is pinned first regardless of its own date: it is the trunk the others are measured against and is never offered as something to land, so it is an anchor rather than a candidate.
  A tree whose tip date could not be read sorts last rather than as epoch-old, and ties fall back to path order so a refresh cannot shuffle the list under the pointer.
- Activity is the **branch tip's committer date** (`head_committed_at`), never the worktree directory's modification time.
  Windows freezes a file's `st_mtime` while a handle is open on it, so a checkout a live session is working in reports a directory timestamp hours stale while every Win32 API agrees with it - ordering by directory mtime would sink the busiest worktree to the bottom.
  The tip date is read from the shared object database, so a locked or prunable checkout still reports when its branch last moved and does not sink for the wrong reason.
- A row states **when its branch last landed**, when the land queue holds a record of it: the newest `landed` request for that branch, to the minute (`landedAtByBranch`).
  It is a floor and never a guess.
  `already_landed` is the queue saying the trunk already contained the branch, which is not a landing and carries no moment one happened, and `GET /api/land` returns the newest hundred rows for the Project - so a branch that landed long enough ago carries no date at all.
  A row with no date means "this queue has no record of it landing", never "it has not landed".
- A prunable worktree is unmeasured and shown as unavailable rather than clean.
  Overview measurement first requires Git's reported top-level to equal the exact listed root, so a broken nested worktree cannot inherit status from an enclosing checkout.
- Each collapsed Map row gives the branch identity its own bounded line and wraps status metrics on a separate line, so divergence and state cannot overlap the title at narrow drawer widths.
  The worktree indicator is inline with the identity, while the left-aligned expand control is inline with the metrics; neither control reserves an otherwise empty row.
  The worktree leaf appears beside the branch only when it adds information; the exact root remains in expanded detail.
  Zero comparison divergence is omitted rather than rendered as separate ahead and behind labels.
- A worktree being removed is dimmed with a spinner from the press until the refreshed inventory no longer lists it, and the pending set is a property of the **list** rather than of any row (`worktreeRemoval.ts`).
  A row holding its own removing state stopped saying it the moment the row was collapsed, and the removal's response ended it too early on both paths - the daemon answers a renamed removal before Git has deleted a byte, and a fallback removal while Git still is - so the checkout sat in the map looking like every other one until a later poll dropped it.
  The refreshed inventory is therefore the only thing that ends the indication, and a refusal is the only thing that clears an entry early.
  While a worktree is pending its row offers neither Land nor Remove; the list already said what is happening to it.
- Map has a **selection mode**, reached from one toolbar control, that draws a checkbox on every linked worktree row and a bulk bar at the head of the list.
  It is a mode rather than a permanent column because a checkbox under every branch name is weight on a surface people open to read a diff, and it exists at all because a repository accumulates worktrees faster than anyone removes them one at a time.
- **Bulk land** is one land request per selected branch, in map order, and nothing more: the queue serializes them, which is the queue doing exactly its job (`land-queue.md`).
  The main tree and a detached HEAD are named as unable to land rather than being enqueued and refused.
- **Bulk remove** rides the same pending-removals machinery as a single removal and applies the row's own refusals unchanged: the main tree is never a candidate, a checkout with a live session in it is not offered, and a locked one is Git's to refuse.
  What bulk adds is that a checkout carrying uncommitted files or unlanded commits has to be **named and separately agreed to** before it can be swept up with thirty others, because "remove 30" is not a sentence a reader can check.
  The default press takes only the clean, landed ones; one checkbox adds the rest, and the bar states the counts either way.
  An unmeasured checkout is warned about rather than called clean, and takes the side of needing force.
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
  The count is a control rather than a label: it opens the commit's sessions at the pointer without expanding the commit, and it is a sibling of the expand button rather than a span inside it, because interactive content nested in a button is neither valid nor reliably clickable.

### Provenance ledger

- Provenance lists durable session-to-commit associations newest first for the selected Project, **one card per commit** rather than one per row.
  The table stores a row per session per commit because that is what each piece of evidence is about; read back flatly, ten occupancy rows buried the one naming who made the commit.
- A card names the committer (or, for a merge, the integrator and the branch's authors) and the contributors individually, then collapses everyone else into a single "N sessions had this checkout open" line with their names beside it.
  The committer and contributors come from the daemon's own per-commit rollup, so the rule for "who made this" has one home; only the occupancy list is assembled in the browser, because it is the part the rollup deliberately leaves out.
  A session that committed or contributed is never also counted as a bystander to its own work.
- Reference movements render in their own section below, as checkout facts that name no session: the checkout, the commit, where it moved from, and what it did in plain language — fast-forwarded onto commits written elsewhere, merged, rewritten, or moved back.
  Keeping them out of the session cards is the point.
  A branch landing moves every attached session's HEAD and says nothing about any of them, and drawing it in the same shape as a session claim is what made a landing read as an accusation against whoever had the directory open.
- Each session line shows the session label, run-id prefix, what the session did (committed, amended, wrote N files in it, or was in the checkout when it appeared), confidence, and the contributed file paths; the card carries the short commit OID, subject, checkout root, and observation time.
- A row carries two names and keeps them apart.
  `session_name` is durable evidence: what the session was called when the commit was observed, never rewritten by a later read.
  `display_name` is what that session is called now, resolved on read so a reader sees the name the rest of the app uses.
  `history_id` names the conversation to open, and is absent for a session with no History row rather than pointing at one that does not exist.
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
- Concurrent overview requests for the same Project root, comparison ref, and worktree scope share one daemon computation.
  A timed-out or disconnected browser cannot create an overlapping Git-process storm by refreshing again.

#### What the Map costs, and what it stops re-deriving

The Map recomputed everything on every request, and at the fifty worktrees this repository reached that was around eight `git` subprocesses per checkout - four hundred process spawns for one read, with nothing retained between them (diagnosed 2026-08-21).
Four things changed, and the ordering principle across all of them is that **nothing is memoized that could be wrong**.

- The **branch half is memoized on two object IDs**: `(worktree, HEAD, comparison oid)` -> the ahead/behind counts and the branch delta.
  Both readings are commit-to-commit, so nothing in a working tree can affect either; given the same two commits they are the same answer, and re-deriving it costs five subprocesses.
  That is why the key is object IDs rather than a clock: an unattended worktree is never polled, so a TTL would be guessing about exactly the case it exists for, while a tree whose HEAD has not moved has not moved.
  The comparison ref is resolved to an OID once per request, not once per checkout, so the base advancing invalidates fifty memoized readings at once.
  A reading Git refused is never memoized, so a locked index cannot pin "unavailable" onto a healthy checkout until its HEAD happens to move.
- The **local half is read live every time**, and is not memoized at all.
  `git status --porcelain=v2` carries the HEAD and index blob names but no *worktree* blob hash, so an edit that leaves a file `.M` produces byte-identical status output with different line counts - a fingerprint taken from it would go quietly wrong about the one number the row shows.
  What is saved there instead is unconditional work: the two `git diff --numstat` calls now run only for a scope that actually has tracked modifications, so a clean checkout spends none.
  A clean, unchanged worktree therefore costs two processes rather than eight.
- `detail=summary` **withholds every per-file list** and is what the Map asks for.
  A row draws counts; the files are needed on expand and nowhere else, and serving four lists of up to two hundred file records per worktree to draw a badge is the payload's real cost - one compression on the way out cannot recover, because gzip makes bytes smaller rather than absent.
  A withheld list is marked `files_omitted` rather than left empty, because "12 local" over an empty list is otherwise indistinguishable from an empty change set.
  An expanded row fetches its own full reading for that one checkout (`worktree=<path>`), which is one checkout's worth of Git rather than the Project's; a path Git does not list is refused rather than measured, and `main` is still read off the *full* listing so a single-row read cannot call every checkout the main one.
- The overview is the daemon's first **conditional** response: a weak `ETag` over the exact bytes being served, plus `Cache-Control: no-cache`, which means "revalidate before every use" rather than "do not store" and is what makes a browser send `If-None-Match` at all.
  The client code is unchanged - `fetch` turns the 304 back into a 200 from its own cache - so only the bytes on the wire go away.
  The two readings never share a tag, so a client holding the summary is never told the full reading is unchanged.
- Map refreshes on Git and worktree events, **filtered by Project and debounced**.
  `git_changed` is raised by every session's five-second dirty tick, so an unfiltered listener re-read one Project's whole worktree map on another Project's poll, and ten sessions in one repository raised it ten times inside a few hundred milliseconds for one answer.
  The event carries its Project; an event naming none (a reconnect, a worktree act) is never filtered out, because treating "unknown" as "not mine" would stop the tab refreshing after a reconnect.
- The provenance ledger is fetched only by the readings that draw it - Log's per-commit session links and Provenance itself.
  Map draws none of it and used to fetch five hundred rows of it on every one of the refreshes above.
- An open Log refreshes its graph while retaining immutable commit caches.
- Explicit Refresh covers Git changes created outside swe-mux event paths.
- The Git surface mutates only through the worktree create and remove operations and the one-time repository initialization offered to a Project whose folder has no repository.
- It does not stage, unstage, commit, reset, switch, fetch, merge, rebase, prune, or discard files.
- The **land queue** is the one path that merges, and it is not this surface: it is a daemon-owned pipeline with its own fixed vocabulary, its own preconditions, and its own approval (`land-queue.md`).
  Its controls in this tab - the Land button on a Map row, and the landing strip's cancel, verification-command editor, and approval - enqueue and cancel *requests*, write one Project config key, and approve the bytes that will run.
  No control there moves a trunk, and the read-mostly rule for the rest of the tab is unchanged.
- Removal validates the exact current worktree root, refuses the main tree and live-session roots in the UI, and requires explicit force before Git may discard uncommitted files.
- Worktree add, repair, and remove run as daemon-owned mutations with a 30-minute deadline rather than the four-second read-only Git deadline.
  Client cancellation cannot interrupt a mutation after Git starts changing repository state.
- Removal renames the checkout out of the way and deletes it afterwards (`worktree_graveyard.py`).
  Deleting an agent worktree is ten to twenty seconds of honest filesystem work - a checkout carrying `node_modules` and `.venv` is tens of thousands of small files, and NTFS unlink plus per-file antivirus scanning is what that costs - and none of that time is spent deciding anything.
  The directory is moved into the repository's graveyard with one rename, Git is told to forget the registration, and a background task deletes the bytes.
- The graveyard is `<git-common-dir>/swe-mux-graveyard`, and its location is a correctness constraint rather than a tidiness preference.
  It is outside every working tree, so a buried checkout can never appear as untracked files in `git status` - which would raise dirty counts and make the land queue refuse to land that checkout.
  `.git` is the first entry of the default project-ignore list, so a purge deleting thirty thousand files does not become thirty thousand watcher events.
  And it is never in `git worktree list`, so Map cannot draw a row for it.
  A sibling directory beside the worktree would always be same-volume, but `.claude/worktrees/` is gitignored only in repositories that happen to say so, which is not a fact about anyone else's.
- What drops the registration after the rename is `git worktree remove` on the original path, measured to succeed and to drop that entry alone.
  `git worktree prune` is global: it would also drop every other checkout whose directory is merely missing, and with it that checkout's index and reflog.
- The rename is declined rather than attempted in every case where it would change what the removal means: the main tree (Git refuses to remove it at all, so renaming it first would move the primary checkout out of the way for a removal that was never going to happen), a locked worktree (Git refuses to remove one even once its directory is gone, so renaming first would leave a renamed tree beside a live registration), a worktree containing submodules, and a worktree that is not clean when force was not given (Git refuses in about fifty milliseconds, so the in-place path costs nothing and states its own reason).
  The main tree is identified by Git listing it first, never by the shape of its `.git`: a main tree with a `.git` file is legal (`git init --separate-git-dir`) and the obvious probe would answer the opposite.
- A rename the filesystem refuses - a cross-volume graveyard, or the Windows class where an open handle inside the tree defeats the move - falls back to the in-place deletion.
  The move is atomic and its failure is total, so a defeated rename is a clean signal rather than a half-moved tree.
  Git keeping the registration after a successful rename puts the tree back exactly where it was and lets the ordinary in-place removal answer.
- Purging is idempotent and never removes the graveyard root, so a purge racing a burial cannot delete the directory another removal is renaming into.
  Whatever it cannot delete stays for the next purge: the next removal, or the sweep at daemon start that clears what a killed purge left behind.
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
- `[worktree].verify_command` and the executable `.worktree-verify` convention are the same shape one step later: the command the land queue runs as a branch's gate (`land-queue.md`).
  Resolution and bounded execution are shared with bootstrap; the authority is not.
  Bootstrap runs once, for a tree a human just asked to create, before anything starts in it, so it needs no approval of its own.
  Verification runs repeatedly on an agent's request, so it carries an exact-content approval and any edit to it un-approves it.
  Both read the Project's *config values* through `read_project_config_values`, never the envelope `read_project_config` returns: handed the envelope, resolution finds no `worktree` table, falls through to the convention, and runs a different command than the one the repository declared - with no symptom, because both paths produce a working command.
- Harness preparation is adapter-owned and best effort.
  Claude atomically clones the primary checkout's `~/.claude.json` trust entry to the canonical forward-slashed worktree key, copies the primary `.claude/settings.local.json` permission allowlist, and adds `--add-dir <primary-root>`.
  Codex atomically writes `trust_level = "trusted"` under the canonical worktree key in the runtime `CODEX_HOME` config and grants the primary root through `sandbox_workspace_write.writable_roots`.
  OMP and shell currently need no trust preflight or extra-directory argument.
  A preparation failure falls back to the harness's interactive behavior and does not fail the spawn.

## Key files

- Monitor and git runner: `src/swe_mux/git_monitor.py`
- Durable session-to-commit capture and explicit historical import: `src/swe_mux/git_provenance.py`, `src/swe_mux/git_provenance_backfill.py`, `src/swe_mux/history.py`
- Project-scoped review domain and bounded patch runner: `src/swe_mux/git_review.py`
- First-time repository creation and the starter ignore file: `src/swe_mux/git_init.py`
- Routes: `src/swe_mux/server.py`
- Bootstrap runner: `src/swe_mux/worktree_setup.py`
- Display-name resolution shared by every surface that names a session: `src/swe_mux/session_titles.py`, `frontend/src/sessionNames.ts`
- Drawer Map, Log, Provenance ledger, Run launcher, and defensive response parsing: `frontend/src/GitTab.tsx`, `frontend/src/gitWorktrees.ts`, `frontend/src/ProjectRunMenu.tsx`, `frontend/src/worktreeLaunch.ts`
- Landing on a Map row, the Project-wide landing strip above it, and the shared queue/gate reads both use: `frontend/src/GitLandRow.tsx`, `frontend/src/GitLandBar.tsx`, `frontend/src/landState.ts` (`land-queue.md`)
- Session-link list and its destination rule: `frontend/src/GitSessionLinks.tsx`
- Shared file rows, lazy renderer, modal, and pure review state: `frontend/src/GitFileRow.tsx`, `frontend/src/LazyGitDiff.tsx`, `frontend/src/GitDiffView.tsx`, `frontend/src/GitReviewModal.tsx`, `frontend/src/gitReview.ts`
- Pane-header chip and the `mux:git-changed` re-dispatch: `frontend/src/App.tsx`
