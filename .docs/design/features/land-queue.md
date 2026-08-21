# Land queue

## What it is

Serialized, deterministic landing of a finished worktree branch onto its Project's trunk.
The daemon owns the three mechanical commands an operator otherwise runs by hand - merge the trunk into the branch inside its worktree, run the repository's own verification command, fast-forward the trunk - so N parallel branches land in sequence and the operator touches only the one that genuinely conflicts.

A conflict and a verification failure are the two steps that need intelligence, and both belong to the branch's own agent, which holds the context.
Neither is resolved here; both return to that agent as a bounded deterministic message.

## The fixed vocabulary

The pipeline runs exactly four git commands and no others: `merge <trunk-ref>` inside the worktree, `merge --abort` to undo a conflicted reconcile, `merge --ff-only <branch>` in the primary checkout, and the read-only queries the preconditions ask.
Nothing rebases, forces, resets, cleans, stages, or checks out, and no model chooses any of it.

**Fast-forward-only is the whole safety proof.**
Git refuses it on divergence and refuses to overwrite overlapping local changes, so the pipeline cannot lose work by construction.
That is the same property that already makes it the one merge shape permitted outside a worktree.
A refusal is a reported failure and never a retried force.

## The steps

| Step | What it does | Failure |
|---|---|---|
| `reconcile` | Merges the trunk ref into the branch, inside the branch's worktree. | A conflict aborts the merge, leaving the worktree exactly as it was found, and hands the request back with the conflicting paths. |
| `verify` | Runs the repository's declared verification command in the worktree and records the commit OID that passed. | A nonzero exit hands the request back with the output tail. An unapproved or absent gate refuses instead: neither is a branch problem. |
| `land` | Fast-forwards the trunk in the primary checkout. | Divergence, a dirty checkout, or a branch that moved past the verified OID refuses. |

A branch already reachable from the trunk skips all three: there is nothing to land, and running the gate would spend three minutes proving it.

After each successful land the next queued item runs from `reconcile` against the new trunk, so one landing never strands another agent's now-stale reconcile.
That is the `advance` rule, and it is the queue's ordinary behaviour rather than a separate step.

Re-verification is skipped in exactly one case: a reconcile that reported nothing to merge, on a request whose verified OID still stands.
Re-running the gate there proves nothing.

## Preconditions, checked before every mutation

The hazard the pipeline lives inside is that the worktree it reconciles is a checkout a live agent session owns and may be mid-turn writing into.
Preconditions are therefore evaluated before **each** mutation rather than once at enqueue, and they fail closed: an unreadable repository blocks exactly like a dirty one, because "the check could not be made" and "the check passed" must never be conflated.

They divide into two dispositions, and the difference is the queue's whole operator experience:

- **`hold`** - a busy tree, a working session, an unreadable repository, or local changes in the primary checkout **to a file this land would overwrite**.
  The request waits in a `waiting` state carrying its cause, retries on the next sweep, and only a bounded timeout (`land_hold_timeout_seconds`) converts the wait into a handback.
  An agent that asks to land and keeps working is the common case, not an error.
- **`refuse`** - a trunk that resolves to a linked worktree, a branch the worktree is not on, a checkout Git does not list as a worktree of this repository, a detached HEAD.
  Waiting cannot fix any of them.

A session counts as busy when its live `git_cwd` is the worktree and its state is `starting`, `working`, or `awaiting`.
Starting counts: a harness that has not settled is exactly the one whose first act may be writing files.

**The dirty-trunk check is scoped to the incoming change set**, read as `git diff --name-only <trunk>..<branch>`, and holds only on the intersection with the trunk's own uncommitted paths.
That is the same question `--ff-only` asks, and a broader one is not a stricter safety net: it is wrong.
It also deadlocks by construction on any machine whose daemon writes into its own primary checkout - enabling this feature writes `.swe-mux/config.toml` there, which under a whole-checkout test held every land forever, so the act of turning the feature on was what stopped it working.
An incoming set that cannot be read holds rather than being treated as empty, because an empty set would skip the check entirely.

A branch the trunk already contains settles as **`already_landed`** without reconciling or verifying.
It is its own terminal state rather than `landed` or `refused`, because it is neither: nothing was refused, and reporting a land would claim a trunk movement that did not happen in a ledger whose whole purpose is recording which OID moved what.
Pressing Land on such a branch is refused at request time so the panel answers at once; the pipeline keeps the same check for the case the request cannot see, where the trunk gains those commits while the request waits its turn.

The trunk is identified by comparing `--absolute-git-dir` with `--git-common-dir`, never by name, for the reasons `git.md` states.

## The verification command

Declared by the repository as `[worktree] verify_command` in `.swe-mux/config.toml`, else the executable `.worktree-verify` convention in the worktree.
Resolution and bounded execution are the shared worktree-command seam that bootstrap already uses; what landing adds is authority.

**It is not a Project Action, and cannot be.**
An action's cwd is bounded by the canonical Project root and is deliberately denied the sibling-worktree widening spawns get (`project-actions.md`), and an action step becomes a one-shot terminal session rather than a captured exit code.
The pipeline needs an exit code and bounded output inside a tree that lives outside the Project root.

It carries the exact-content approval model Project Actions established: a machine-local SHA-256 over the bytes that will run, keyed by canonical Project root, retained alongside the approved text so the prompt can show a diff.
Any edit un-approves it.
**An agent therefore cannot approve the command its own land runs** - writing a verification script is a proposal, and a human turns it into an authority.

The digest covers the command's *source kind* alongside its bytes, so approving a config string never silently approves a script with identical content.
The script is fingerprinted from the **worktree's** copy, because that is the copy that will run: a branch that edits its verification script must present for approval again.

The gate's exit status is reported exactly as the process gave it.
Nothing pipes, filters, or re-derives it; a gate command trimmed inside its own pipeline has already shipped a failing suite green in this repository once.
Running under the daemon's own `base_session_env` rather than an agent shell also removes the known intermittent false failure `.worktree-verify` shows in an agent shell.

Retries are bounded and explicit: at most one, only when `land_retry_verification` is set, and a retry that fails **differently** from the first attempt stops rather than retrying again - two unlike failures are evidence about the gate, not about the branch.

## Authority

- **Install-wide** `land_queue_enabled` is the emergency stop. Off means no branch lands anywhere, whatever any Project opted into, and the sweep does nothing.
- **Per-Project** the `land_queue` automation must be opted in (`automation-enablement.md`). It gates a capability rather than a read, so it depends on no substrate and is off by default.
- **Per-Project** `land_grant` is `off` / `draft` / `granted`, defaulting to `draft`.
  Its own field rather than a level of `session_control_grant` for the same reason it is its own automation: session control acts on a *session*, this moves a *repository's trunk*.
- **Per-origin** `land_hourly_budget` bounds a runaway requester. A land costs wall-clock rather than tokens, so the cap is about a request loop, not spend.

An operator request bypasses the grant, because the operator is the authority the grant defers to.
It passes every precondition unchanged.

A drafted request writes an inert `land_request` observation that appears as a Fleet Queue approval row; approval is what enqueues it, and the originating session is retained as the request's origin so a handback reaches the agent that asked rather than the human who approved.

## The handback

A conflict, a verification failure, or an expired hold returns the request to the originating session through the ordinary Phase 5 prompt queue as `sender_kind: "rule"` - a bounded deterministic template, not a new agent-to-agent path.
It is a draft unless that session's own auto-delivery grant promotes it, exactly like every other queued item.

The body is a fixed template naming the branch, the two roots, the conflicting paths, and the tail of the gate's output.
No model writes any part of it.
The output tail is bounded to a few KiB and passed through the same `looks_like_secret` redaction gate every other excerpt uses; the full capture stays in the land record.
The request id is the message's `correlation_id`, so the queue's existing uniqueness index dedupes a repeat.

A target session that has ended is not an error: the branch's agent is gone, and the land row already records why it stopped.

## Serialization and durability

Serialization is a property of the schema rather than of the worker's care.
Two partial unique indexes carry it:

- `land_requests_active`, unique on `(project_root, branch)` over the live states, makes enqueue itself the claim - an agent that asks twice, or asks again after a restart, cannot create a second pipeline for one branch.
- `land_requests_inflight`, unique on `project_root` over the running states, means two workers cannot both mark a step running against one primary checkout even if both believe they should.

Verification is measured parallel-safe across worktrees, but `advance` re-runs every remaining item after each land anyway, so concurrency would buy only the first item.
The store's shape permits a later ceiling; v1 is strictly serial.

Rows are machine-local, like scheduled runs: a queue committed to a repository would arm itself in every clone and every worktree of it.
The Project opt-in stays portable and is inert on its own.

A row left in a step state by a daemon that died mid-flight returns to `queued` on restart rather than resuming.
Every step re-checks the repository from scratch, so re-running one is safe and guessing how far it got is not.

## Audit

`land_events` is the authoritative per-step trail: request, reconcile, verify, land, and every refusal, handback, and orphan, each with its reason and detail.
A step is additionally mirrored into Tier 0 when the request has an originating session, so a land appears beside that run's other facts; an operator-initiated land has no session and simply has no such row.

## Surface

The Git drawer's **Land** segment, per Project.
A segment rather than a strip inside Map, for the same watch-here/act-there split the prompt Queue has with the Fleet Queue: Map answers "what is in this worktree", Land answers "what is happening to it".

The verification gate is drawn above the queue, because nothing below it can run until its bytes are approved and a queue drawn first would read as ready.
Reviewing it shows the approved bytes beside the current ones.
A detached worktree is omitted from the launch list rather than offered and then refused.
The launch list is in Map's order - most recently committed branch first, by tip date rather than directory mtime (`git.md`) - because the branch a reader just finished work on is the one they came here to land, and the two lists disagreeing about order would make them hunt for it twice.

Nothing in the surface lands anything: the daemon's own supervised sweep is the only thing that moves a trunk.

## API

```text
GET    /api/land?project_id=                      # the queue and its bounds
POST   /api/land            {project_id, worktree_root}
DELETE /api/land/{request_id}                     # cancel a queued or waiting request
GET    /api/land/{request_id}/events              # the per-step audit trail
GET    /api/land/verify-command?project_id=&worktree_root=
POST   /api/land/verify-command/approve  {project_id, worktree_root, digest}
```

Approval requires the digest the caller was shown; a stale one is refused, because the bytes moved between the prompt and the click.

## The agent surface

`request_land` (`mux-mcp.md`), a caller over the same service.
It has **no target argument**: the checkout comes from the caller's own live cwd, so "an agent lands the checkout it is working in, and no other" is true by construction rather than by a check something could be routed around.

## Configuration

| Setting | Where | Meaning |
|---|---|---|
| `land_queue_enabled` | global | Install-wide emergency stop. |
| `land_hourly_budget` | global | Requests per origin session per hour. |
| `land_hold_timeout_seconds` | global | How long a busy worktree holds before handing back. |
| `land_retry_verification` | global | Whether a failed gate is retried once. |
| `land_queue` | `<project>/.swe-mux/config.toml` `automations` | Per-Project opt-in. |
| `land_grant` | `<project>/.swe-mux/config.toml` | `off` / `draft` / `granted`, default `draft`. |
| `[worktree] verify_command` | `<project>/.swe-mux/config.toml` | Explicit override of the `.worktree-verify` convention. |

## Key files

- Service, pipeline, handbacks: `src/swe_mux/land_queue.py`
- Durable rows and the audit trail: `src/swe_mux/land_store.py`
- Precondition reads and their dispositions: `src/swe_mux/land_preconditions.py`
- The gate, its approval store, and its runner: `src/swe_mux/worktree_verify.py`
- Shared command resolution and bounded execution: `src/swe_mux/worktree_exec.py`
- Routes, the busy-session probe, the drafted-request approval: `src/swe_mux/server.py`
- The agent tool: `src/swe_mux/mcp.py`, `src/swe_mux/mcp_contract.py`
- Land segment and its parsing: `frontend/src/GitLandPanel.tsx`, `frontend/src/gitLand.ts`
- Tests: `tests/test_land_queue.py`, `tests/test_land_api.py`, `frontend/test/gitLand.test.ts`

## Relates to

- `git.md` - worktree tooling, the main-tree test, and the comparison ref this lands onto.
- `project-actions.md` - the exact-content approval model, and why an action cannot be the gate.
- `prompt-queue.md` - the channel a handback rides.
- `automation-enablement.md` - the per-Project opt-in and the grant shape.
- `mux-mcp.md` - the `request_land` tool and its bounds.
- `tier0-facts.md` - the joinable copy of each step.
