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

`verify` is the step that takes minutes, and it reports on itself while it runs.
See "What a running gate says about itself" below.

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

The command is resolved from the **Project's config values**, not from the envelope `read_project_config` returns.
Handed the envelope, `resolve_worktree_command` looked for `worktree` at the top level, found nothing, and fell through to the script convention - so `[worktree] verify_command` was declared, documented, and silently ignored while a different gate ran.
The failure had no symptom, because both paths produce a working gate.
`read_project_config_values` is the named accessor that exists so the mistake cannot be made quietly again.

### Editing it

The resolved command, which of the two mechanisms produced it, its approval standing, and an editor for the override are drawn together in the Land segment.
Both mechanisms were documented and neither was ever stated on screen, so "why is it running that" had no answer in the app; and the only way to change the answer was to hand-edit a committed TOML file.

**Editing never approves, and the two are separate acts against separate routes.**
An edit cannot produce an approved command even by accident: the approval is a digest over the bytes, so moving the bytes invalidates it without the write saying anything about approval at all.
That is what keeps "an agent cannot approve the command its own land runs" true regardless of who reaches the editor - writing a verification script is a proposal, and a human turns it into an authority.

The editor writes exactly one key, guarded by the Project config's own revision, so a concurrent edit to another field loses the race rather than being clobbered by a surface that round-tripped the whole file.
An empty command **clears** the override and falls back to the `.worktree-verify` convention, which is a decision ("run the script in the tree") rather than a no-op.
A read-only or malformed config is not offered as editable.

### What a running gate says about itself

`verifying` alone said nothing about whether a four-minute gate was thirty seconds or three minutes in.
Three signals answer that, and **each is reported only when it was really observed**:

- **Steps.** A `\n=== <name> ===\n` line on the gate's own output is a boundary the script *chose* to announce, which is the convention `.worktree-verify` already follows. The step's number, its name, and its elapsed time are facts.
  The pattern is exactly three equals signs on each side and rejects a captured name containing one, so pytest's own section rules (`===== short test summary info =====`) are not read as verification steps - which is precisely when the reading has to stay trustworthy.
- **A step count**, and only from a previous **passing** run of byte-identical bytes. A failing gate stops at its first bad step under `set -e`, so its step list is a *prefix*; recording that would predict a permanently shorter run and make every later gate read as nearly finished. A run that overruns its plan withdraws the total rather than stretching it - never "step 8 of 7".
- **Output lines.** The fallback for a gate that announces nothing. Reported as what it is: evidence the process is still producing output, never progress toward an end.

**No percentage is derived from any of it, at either end.**
A percent implies a denominator, and the steps of this repository's own gate take 175s and 3s in one run, so there is no honest one; line-of-script would be worse still, because the lines are not the work.
`frontend/test/gitLand.test.ts` and `frontend/test/renderer/git-land.spec.ts` both assert the absence rather than trusting it.

The reading is **in memory and lives exactly as long as the process does**.
It is attached to the request row only while that row is `verifying` under this daemon: a snapshot left on a finished row would be a claim about a run that is over, and a row a restart returned to `queued` has no run at all - both would read on screen exactly like a gate that is moving.
Plans are durable (`land_verify_plans`, keyed by trunk root and digest) because they are a measurement of bytes rather than of a run.

## Authority

- **Install-wide** `land_queue_enabled` is the emergency stop. Off means no branch lands anywhere, whatever any Project opted into, and the sweep does nothing.
- **Per-Project** the `land_queue` automation must be opted in (`automation-enablement.md`). It gates a capability rather than a read, so it depends on no substrate and is off by default.
- **Per-Project** `land_grant` is `off` / `draft` / `granted`, defaulting to `draft`.
  Its own field rather than a level of `session_control_grant` for the same reason it is its own automation: session control acts on a *session*, this moves a *repository's trunk*.
- **Per-origin** `land_hourly_budget` bounds a runaway requester. A land costs wall-clock rather than tokens, so the cap is about a request loop, not spend.

An operator request bypasses the grant, because the operator is the authority the grant defers to.
It passes every precondition unchanged.

All three are reported by `GET /api/land` (`installed_enabled`, `project_enabled`, `agent_grant`), because none of them could be told apart from an ordinary quiet queue.
The install stop is the sharpest case: it is checked by the sweep before anything else, so with it off a request enqueues and then sits at `queued` forever - identical, on screen, to a pipeline working through a backlog.
It also had no control in any overlay until it gained one in Settings → Automation → Land queue.

Where each is drawn follows what it makes inert.
The **install stop** is what makes an individual row's own Land button pointless, so its gate is on that row - and in the Land segment, above everything, because it stops the whole queue too.
The **Project opt-in** and **`land_grant`** decide what happens to an *agent's* `request_land` and never touch the operator's own button, so they stay in the Land segment as one Project-wide statement; copying them into every Map row would make a Project-wide control a standing fixture in a per-checkout pane, which is the thing `setting-links.md` forbids.
All three grant in place through the ordinary additive path; the Projects registry's **Agent authority** table is where any of them is lowered again.

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

Landing is drawn in **two** places, split by what each part is about.

**The act is on the Map row of the worktree it acts on.**
Landing a branch is something you do to a checkout you are already looking at, and it used to live on a segment carrying its own list of the same worktrees - two lists of one thing, and the button furthest from the diff that decides whether to press it.
Expanding a worktree row shows that branch's Land control, its live land state (including the running gate's own reading of itself), the gate as it resolves *for that checkout*, and a Cancel while the request is still cancellable.
The main tree is the trunk these land *onto* and is never offered.
A detached worktree states why rather than offering a button that would be refused.

**The Land segment is what has no row to live in**: the Project's verification command with its approval and editor, who besides the operator may start a land, the queue in the order the pipeline will reach it, and finished requests.
It keeps its own drawer segment - and therefore its own palette command and voice phrase - rather than becoming a strip Map draws, but it no longer lists checkouts at all.

The gate appears **on the row** while it blocks that row, and the **editor** lives in the segment, which is the ordinary setting-links asymmetry: many surfaces may grant in place, exactly one owns withdrawal (`setting-links.md`).
The per-worktree reading is not a duplicate of the Project one: the script convention is fingerprinted from the *worktree's* copy, so a branch that edited `.worktree-verify` is unapproved on its own row while the Project's reading still says approved.
Reviewing either shows the approved bytes beside the current ones.

Queue order is oldest-first - the order the pipeline will actually reach them.
The daemon lists newest-first because that is what a history read wants; read backwards, the request about to run sat at the bottom.
Finished requests are folded into a history disclosure, newest first.

Nothing in either surface lands anything: the daemon's own supervised sweep is the only thing that moves a trunk.

## API

```text
GET    /api/land?project_id=                      # the queue and its bounds
POST   /api/land            {project_id, worktree_root}
DELETE /api/land/{request_id}                     # cancel a queued or waiting request
GET    /api/land/{request_id}/events              # the per-step audit trail
GET    /api/land/verify-command?project_id=&worktree_root=
PUT    /api/land/verify-command          {project_id, command, revision, worktree_root?}
POST   /api/land/verify-command/approve  {project_id, worktree_root, digest}
```

`GET /api/land` attaches `verify_progress` to a row that is `verifying` under this daemon, and `null` to every other row.

`GET /api/land/verify-command` additionally reports the editable half (`config_command`, `config_revision`, `config_status`, `config_path`), which convention applies (`script_name`, `script_present`), and the `plan` a byte-identical passing run recorded, if any.

`PUT` sets or clears `[worktree] verify_command` and never approves; it refuses `409 revision_conflict` on a stale revision and `409 project_config_malformed` on a config it cannot parse, and writes nothing when it refuses.
Approval requires the digest the caller was shown; a stale one is refused, because the bytes moved between the prompt and the click.
Both leave an audit record (`land_verify_command_changed`, `land_verify_approved`).

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
- Durable rows, the audit trail, and recorded gate plans: `src/swe_mux/land_store.py`
- Precondition reads and their dispositions: `src/swe_mux/land_preconditions.py`
- The gate, its approval store, and its runner: `src/swe_mux/worktree_verify.py`
- What a running gate reports about itself: `src/swe_mux/verify_progress.py`
- Shared command resolution and bounded execution: `src/swe_mux/worktree_exec.py`
- Routes, the busy-session probe, the drafted-request approval: `src/swe_mux/server.py`
- Landing on the Map row: `frontend/src/GitLandRow.tsx`
- Land segment (queue, verification command, agent authority): `frontend/src/GitLandPanel.tsx`
- Shared queue/gate reads: `frontend/src/landState.ts`; parsing and labels: `frontend/src/gitLand.ts`
- Tests: `tests/test_land_queue.py`, `tests/test_land_api.py`, `tests/test_verify_progress.py`,
  `frontend/test/gitLand.test.ts`, `frontend/test/renderer/git-land.spec.ts`

## Relates to

- `git.md` - worktree tooling, the main-tree test, and the comparison ref this lands onto.
- `project-actions.md` - the exact-content approval model, and why an action cannot be the gate.
- `prompt-queue.md` - the channel a handback rides.
- `automation-enablement.md` - the per-Project opt-in and the grant shape.
- `mux-mcp.md` - the `request_land` tool and its bounds.
- `tier0-facts.md` - the joinable copy of each step.
