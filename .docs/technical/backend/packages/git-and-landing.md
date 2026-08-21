# Backend: Git observation, provenance, and landing

Index: `../packages.md`.
Design: `../../../design/features/git.md`, `../../../design/features/land-queue.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

**A monitor must not mutate what it monitors: every read-only Git call passes `--no-optional-locks`** (`runtime-rules.md`).

## `git_review.py`

Project-scoped comparison-ref inference and bounded review reads.

- `resolve_comparison_ref` is the shared core; `infer_comparison` adds the drawer's selector candidates, and `git_monitor` deliberately imports the core so the sidebar and the drawer cannot pick different bases.
- Exact worktree and relative-path validation, and single-flight overview reads.
- `head_commit_dates` gives per-worktree branch-tip committer dates in one batched `git show` against the shared object database, keyed back by the oid asked for.
  It is deliberately not the checkout directory's `st_mtime`, which Windows freezes while a live session holds a file open there, so a directory clock reports the busiest worktree as the most dormant and any activity ordering built on it is inverted.
  Reading from the object database also means a locked or prunable tree still dates.
- File-count and aggregate-byte-bounded worktree and commit summaries, numstat and porcelain parsing, commit graph reads, capped commit-message reads, patch snapshots, stale hashes, and typed review failures.

**Not:** network fetches, Git mutations, live-session polling, or browser presentation.

## `git_monitor.py`

Bounded read-only (`--no-optional-locks`) polling of every session's checkout for full HEAD, branch, dirty count, upstream divergence, worktree identity, root, HEAD-scoped diffstat, and merge-base comparison counts.
Also same-checkout previous-HEAD event evidence; bounded commit-range, changed-file, combined-merge-diff, excluded-range, and blob-digest readers for attribution; deduplication by `(cwd, comparison override)`; and memoization of both diffstats and of ref inference.

**Not:** comparison-ref inference itself (`git_review`), Project registry lookups (injected as a callable), or Git mutations.

## `git_operations.py`

Daemon-owned, client-cancellation-shielded Git mutation workers with a 30-minute deadline, process-tree reaping, operation correlation, and result capture.

**Not:** worktree path policy, porcelain validation, browser presentation, or read-only Git observation.

## `git_init.py`

First-time repository creation for a Project folder: default-branch choice, the starter `.gitignore` and the marker probes that shape it, and the init sequence.

**Not:** deciding whether the folder needs one (`git_review.repository_identity` raises `not_git_repository`), staging, committing, or every later Git mutation.

## `git_provenance.py`

Content-free event correlation that answers "who made this commit".

- Correlation around explicit successful commit-creating tool calls (`commit`, `merge`, `cherry-pick`, `revert`, `rebase`, `am`) and monitor-observed HEAD transitions.
- Reference-move classification from the first-parent range plus two ancestry answers.
- Authored-versus-arrived separation, with a time-ordered ledger arrival oracle.
- Committer isolation by commit range, message subject, and command window.
- Integrator classification of a merge commit's creator, and combined-diff scoping of what a merge itself resolved.
- Branch authorship of a merge derived from the ledger's own rows for the commits its first-parent side had.
- Bystander suppression once a committer or integrator is known.
- Contributor matching of Tier 0 write facts to a commit's changed files, with content confirmation.
- Immutable commit-metadata capture, ranked durable upsert, per-commit rollup, bounded pending state, attribution and move memos, and health diagnostics.

**Not:** shell interpretation, repository mutation, Git author identity, injected commit identity, or browser presentation.
It must never claim authorship of a commit that merely arrived, credit a merge's creator with the branch content it carries, or put a branch author's paths on a merge they did not write.

## `git_provenance_backfill.py`

Explicit read-only planning and idempotent batch import from provider-native tool calls and results, with fixed Git object inspection.

- Ancestry re-attribution of rows the retired shared-checkout rule downgraded.
- Bounded historical contributor derivation through `git_provenance`'s own matchers.
- Retroactive integrator reclassification of recorded merge commits and branch-author derivation for them, planned **before** the withdrawal pass so a promotion and a retraction cannot name one row.
- Reclassification of monitor occupancy through `git_provenance`'s own move classifier, withdrawing arrivals and answered bystanders and reconsidering only its own prior verdicts.
- Single-Project or all-Project sweeps, and an aggregate-only rotating audit log.

**Not:** transcript mutation, transcript-command execution, automatic startup migration, author identity claims, or withdrawing a row retracted by anything but itself.

## Landing

The pipeline executes a *fixed* git vocabulary and never decides anything.
Fast-forward-only is what makes the trunk step safe for a machine, because Git refuses it on divergence and refuses to overwrite local changes, so the pipeline cannot lose work by construction.
A conflict and a failed gate both need intelligence and both belong to the branch's own agent, so they leave as a bounded deterministic message rather than being resolved here.

The verification command is *not* a Project Action: an action's cwd is bounded by the Project root and deliberately denied the sibling-worktree widening, so landing borrows only the exact-content approval.
That is what stops an agent approving the command its own land runs - editing the command and approving it stay two acts against two routes, and a write can never produce an approved command because the approval is a digest over the bytes it just moved.

### `land_queue.py`

`LandQueueService`: the install switch, the per-Project `land_queue` opt-in and its `off`/`draft`/`granted` grant, the per-origin hourly budget, the supervised sweep, the fixed four-command git vocabulary of reconcile, verify, fast-forward, and abort, plus the bounded redacted handback template and the refusal-versus-hold decision.

It owns the *lifetime* of a gate's progress reading: created per attempt, seeded from the recorded plan for that digest, attached to a `verifying` row by `status()` and dropped the instant the process ends, and recorded as a new plan only when the run passed.

It also owns *which* gate runs: between reconcile and verify it classifies the change set, records the decision on both outcomes, and either enters `verifying` or transitions straight to `landing` with the `verify` step recorded as `skipped`.

**Not:** the precondition reads (`land_preconditions.py`), the allowlist itself (`land_classify.py`), durability and serialization (`land_store.py`), the gate's authority (`worktree_verify.py`), how progress is parsed (`verify_progress.py`), delivery (an injected `PromptQueueService.enqueue`), or HTTP.

### `land_classify.py`

The closed documentation allowlist, the `git diff --raw -z` parser it runs over, and the total function from a change set to `full` or `docs_only`.

- A path is documentation when it ends in `.md` anywhere, or lies under `.docs/`/`docs/` at the root and ends in a documentation asset suffix.
- The raw form is used rather than `--name-status` because it is the only one carrying file modes, which is what excludes a submodule gitlink and a symlink by mode rather than by name.
- Everything unrecognised - an unreadable or empty diff, a rename or copy, an exotic mode, a mode change, an undecodable path, an unparseable record - returns `full`.

**Not:** a judgement about whether a change is risky, any repository-specific knowledge, any configuration, or the decision to act on the answer, which is `land_queue.py`'s.

### `land_preconditions.py`

One consistent reading of both checkouts and the disposition it implies: `ready`, a transient `hold` (busy tree, working session, unreadable repository), or a permanent `refuse` (a trunk that is a linked worktree, the wrong branch, an unregistered checkout).
It fails closed, and identifies the main tree by `--absolute-git-dir` versus `--git-common-dir` rather than by name.

**Not:** which sessions are busy, injected by `server.py`, or mutation of any kind.

### `land_store.py`

`land_requests`, `land_events`, and `land_verify_plans` on a dedicated worker thread.

- Conditional state transitions, the per-step audit trail, and restart recovery of orphaned steps.
- `verify_gate` on the request row, added at schema version 3 through the `PRAGMA table_info` column migration and backfilled to `''` rather than to `full`.
- The two partial unique indexes that make one-request-per-branch and one-land-per-trunk properties of the schema rather than of the worker.
- The upserted per-digest record of what a *passing* gate's steps were; a malformed one reads back as no plan, never as a wrong total.

**Not:** policy, git, or HTTP; and never the live reading of a running gate, which is deliberately not persisted.
