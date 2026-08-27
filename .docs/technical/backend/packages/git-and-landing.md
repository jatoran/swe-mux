# Backend: Git observation, provenance, and landing

Index: `../packages.md`.
Design: `../../../design/features/git.md`, `../../../design/features/land-queue.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

**A monitor must not mutate what it monitors: every read-only Git call passes `--no-optional-locks`** (`runtime-rules.md`).

## `git_review.py`

Project-scoped comparison-ref inference and bounded review reads.

- `resolve_comparison_ref` is the shared core; `infer_comparison` adds the drawer's selector candidates, and `git_monitor` deliberately imports the core so the sidebar and the drawer cannot pick different bases.
- `read_ref_index` is one `for-each-ref` giving every branch and the commit each names, and the core takes it as an optional accelerator: a ref present in that listing needs no `check-ref-format`, no `rev-parse --verify`, and no separate object-ID read, while a ref outside it (a tag, a raw object ID, a revision expression) still takes the probes it always did, so the accepted set is unchanged.
  Callers with no index - `git_monitor`, the Project settings probe - are unaffected.
  It also makes the three ref answers *consistent*: the object ID the branch memo is keyed on can no longer come from a different instant than the ref name it belongs to.
  The candidate cap is a display budget for the dropdown; the object-ID map is uncapped, so an override naming a branch past the cap still resolves.
- Exact worktree and relative-path validation, and single-flight overview reads.
- `head_commit_dates` gives per-worktree branch-tip committer dates in one batched `git show` against the shared object database, keyed back by the oid asked for.
  It is deliberately not the checkout directory's `st_mtime`, which Windows freezes while a live session holds a file open there, so a directory clock reports the busiest worktree as the most dormant and any activity ordering built on it is inverted.
  Reading from the object database also means a locked or prunable tree still dates.
  Results are memoized by oid (`_commit_date_memo`) and can never go stale, because a commit's committer date is part of the object its oid names - a commit whose date changed would be a different commit.
  An **absent** oid is not memoized as absent: it can arrive by `fetch`, and caching the miss would leave that row undated until the daemon restarted.
- `repository_identity` reads the top level and the common Git directory in **one** `rev-parse`, which also removes the possibility of two processes disagreeing about whether the folder is a repository at all.
- File-count and aggregate-byte-bounded worktree and commit summaries, numstat and porcelain parsing, commit graph reads and `--grep`/`--author` searches, capped commit-message reads, patch snapshots, stale hashes, and typed review failures.
- The overview's branch memo (`_branch_memo`, `reset_overview_cache`), keyed `(worktree, HEAD, comparison oid)` and holding the ahead/behind counts and the branch delta.
  Both are commit-to-commit, so the key is exact and the memo can never be stale; the local working-tree summaries are deliberately **not** memoized, because `status --porcelain=v2` carries no worktree blob hash and a fingerprint taken from it would go wrong about line counts while the status output stayed identical.
  A reading Git refused is never memoized, so a locked index cannot pin "unavailable" onto a healthy checkout.
- The per-checkout identity memo (`_toplevel_memo`), keyed `(exact root, worktree-listing digest)`.
  The overview proves every checkout's reported top level equals its listed root so a broken nested worktree cannot inherit status from an enclosing repository, and that costs one `rev-parse --show-toplevel` per checkout to re-derive a property of the *registration*.
  The digest of `git worktree list --porcelain` is the invalidation because every way the answer can change is a way that listing changes - added, removed, moved, repaired, or gone `prunable`.
  What is memoized is the **observation, never the verdict**: the comparison runs on every request, so a checkout that fails the guard keeps failing it.
- `shared_worktree_overview` serves the last reading for a (project, root, comparison override, scope) immediately and revalidates behind it (`_overview_cache`), calling an injected `on_refreshed` only when the revalidation lands on a *different* reading than the one already served.
  `fresh=True` bypasses the served reading for the explicit Refresh button while still sharing the in-flight computation.
  `invalidate_overview_cache` is for registration changes, which are not drift: they change which rows exist.
  A failed revalidation leaves the previous reading in place, so a transient Git error cannot turn an answer the reader has into a blocking read.
- `summarize_overview` projects the same payload with per-file lists withheld and marked, and `worktree_overview(..., only=...)` measures one listed checkout, refusing an unlisted path rather than measuring it.
- A commit-graph *search* drops `--graph`: Git draws lanes only for a contiguous walk, so lanes over a filtered subset would connect commits that are not connected.

**Not:** network fetches, Git mutations, live-session polling, HTTP caching (the `ETag` and its comparison are `routes.git._conditional_json`, shared by all three readings), emitting the refresh notice (the route owns the event bus and injects the callback), or browser presentation.

## `git_monitor.py`

Bounded read-only (`--no-optional-locks`) polling of every session's checkout for full HEAD, branch, dirty count, upstream divergence, worktree identity, root, HEAD-scoped diffstat, and merge-base comparison counts.
Also same-checkout previous-HEAD event evidence; bounded commit-range, changed-file, combined-merge-diff, excluded-range, and blob-digest readers for attribution; deduplication by `(cwd, comparison override)`; and memoization of both diffstats and of ref inference.

Every query runs through `bounded_subprocess.run_bounded`, so a repository large enough to answer with hundreds of megabytes cannot be held in the daemon's memory and a poll cancelled at shutdown cannot leave `git` running.
Three codes, and they are not interchangeable to a caller: `124` is a timeout, `125` a capture that hit the output cap (every caller here parses what it gets, so a truncated answer must read as a failure rather than as a smaller repository), and anything else is Git's own.

**Not:** comparison-ref inference itself (`git_review`), Project registry lookups (injected as a callable), the execution mechanics (`bounded_subprocess.py`), or Git mutations.

## `git_operations.py`

Daemon-owned, client-cancellation-shielded Git mutation workers with a 30-minute deadline, process-tree reaping, operation correlation, and result capture.

**Not:** worktree path policy, porcelain validation, browser presentation, or read-only Git observation.

## `git_init.py`

First-time repository creation for a Project folder: default-branch choice, the starter `.gitignore` and the marker probes that shape it, the init sequence, bounded tracked/ignored status probes, and the explicit atomic root-ignore append.

**Not:** deciding whether the folder needs a repository (`git_review.repository_identity` raises `not_git_repository`), staging, committing, or untracking an existing path.

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

## `tools/git_provenance_backfill.py`

Explicit read-only planning and idempotent batch import from provider-native tool calls and results, with fixed Git object inspection.
It lives under `swe_mux/tools/` because nothing imports it: it is run by hand as `python -m swe_mux.tools.git_provenance_backfill`, and a module with no callers sitting beside the daemon's own reads as dead code.
Inside the package rather than the repository's top-level `tools/`, so it keeps its relative imports, mypy's strict pass, ruff, and its tests.

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

It also owns *which* gate runs: between reconcile and verify it classifies the change set, records the decision on both outcomes, and either enters `verifying` or clears the gate without it - as `skipped` for a documentation-only change set, or as `reused` when a verdict this queue produced already stands over the same tree under the same digest.
It owns the *kind* too, which decides exactly one thing - whether the fast-forward happens - so a verify-only request runs every earlier step identically and settles as `verified`, and its verdict is therefore the verdict a land would have produced.
`_clear_gate` is the one place that decides where a cleared gate leads, so the three ways of clearing it cannot drift apart about it.

And it owns whether the handback may reach its author **unattended** (`_reply_arming`): only the request's own origin session, only on the run that asked, only for an agent's request, only while the Project still permits landing, and once per request.
The request is the consent, so the narrowing of the Phase 5 floor is exactly as wide as the request and no wider; the decision and its reason are recorded on the handback event, because a draft nobody delivered otherwise reads exactly like an answer that arrived.
A **refusal** answers its author through the same `_solicited_reply` under the same bounds (`_refused_body`), and carries a machine-readable `code` plus the resolved worktree root in its detail - a reason string cannot be matched on, and the landing strip has to know which checkout's bytes to offer for approval.
`origin_windows` is the other half - the open-request evidence `auto_delivery.py` reads so an origin's grant does not lapse while the pipeline is still computing the answer.

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

**Not:** which sessions are busy, injected by `server.py` at build time, or mutation of any kind.

### `land_store.py`

`land_requests`, `land_events`, `land_verify_plans`, and `land_verify_memos` on a dedicated worker thread.

- Conditional state transitions, the per-step audit trail, and restart recovery of orphaned steps.
- `verify_gate` on the request row, added at schema version 3 through the `PRAGMA table_info` column migration and backfilled to `''` rather than to `full`.
- `armed_replies` on the request row (schema version 4, same migration), spent through the conditional `claim_armed_reply` so the per-request cap on unattended handbacks is a claim rather than a read-then-write, and `open_origin_requests`, the live-request-per-origin read behind the reply window.
- The two partial unique indexes that make one-request-per-branch and one-land-per-trunk properties of the schema rather than of the worker.
- `kind` on the request row (schema version 5, same migration), backfilled to `'land'` - which here is a fact about the column rather than only about history, since nothing could ask for anything else.
- The upserted per-digest record of what a *passing* gate's steps were; a malformed one reads back as no plan, never as a wrong total.
- The upserted per-`(project_root, tree_oid, digest)` record of a gate verdict that already stands, read under a caller-supplied `not_before` floor. There is deliberately no writer here but the pipeline's own observed pass.

**Not:** policy, git, or HTTP; and never the live reading of a running gate, which is deliberately not persisted.
