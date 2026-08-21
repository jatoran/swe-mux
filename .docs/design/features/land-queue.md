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

## The two kinds of request

A request asks for one of two things, and the kind decides **exactly one thing**: whether the last step happens.

- **`land`** runs the whole pipeline and ends by moving the trunk.
- **`verify`** runs everything except that and reports the verdict back to the session that asked.

Every step before the last is identical, and that is what makes a verify-only verdict worth anything: it is the verdict a land would have produced, over the same reconciled content, under the same approved bytes.
A verdict that came from a different pipeline would be a different claim.

Both kinds share one claim on a branch (`land_requests_active`), so a branch has one request in flight whatever it asked for - two would reconcile one worktree twice and run one gate twice over.
Both count against the same per-origin budget, because the budget bounds wall-clock and the gate costs the same minutes whichever step follows it.

## The steps

| Step | What it does | Failure |
|---|---|---|
| `reconcile` | Merges the trunk ref into the branch, inside the branch's worktree. | A conflict aborts the merge, leaving the worktree exactly as it was found, and hands the request back with the conflicting paths. |
| `classify` | Matches the paths the trunk would gain against a closed documentation allowlist, and decides which gate the next step runs. | It cannot fail. Every question it cannot answer - an unreadable diff, an unrecognised status or file mode - answers "the full gate". |
| `verify` | Runs the repository's declared verification command in the worktree and records the commit OID that passed, unless `classify` said the change set was documentation only or a queue-executed verdict already stands over this exact content. | A nonzero exit hands the request back with the output tail. An unapproved or absent gate refuses instead: neither is a branch problem. |
| `land` | Fast-forwards the trunk in the primary checkout. **A `verify` request stops before this step**, settling as `verified`. | Divergence, a dirty checkout, or a branch that moved past the verified OID refuses. |

`verify` is the step that takes minutes, and it reports on itself while it runs.
See "What a running gate says about itself" below.
`classify` is the step that decides whether those minutes are spent at all; see "The documentation-only fast path".

A branch already reachable from the trunk skips all three: there is nothing to land, and running the gate would spend three minutes proving it.

After each successful land the next queued item runs from `reconcile` against the new trunk, so one landing never strands another agent's now-stale reconcile.
That is the `advance` rule, and it is the queue's ordinary behaviour rather than a separate step.

Re-verification is skipped in exactly three cases, and each is "the gate would prove nothing", never "the gate is probably unnecessary".
The first is a reconcile that reported nothing to merge, on a request whose verified OID still stands.
The second is a change set every path of which is documentation, which is the fast path below.
The third is a queue-executed verdict that already stands over this exact tree with these exact bytes, which is "verifying without landing" below.

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

### The documentation-only fast path

The gate costs the same three or four minutes whether the branch rewrote the scheduler or fixed a typo in a heading, and this repository's own manual triage rule already says that is wrong: docs-only lands immediately (`CLAUDE.md`).
`classify` is that rule written down deterministically, so the pipeline applies it without anybody deciding anything.

**Matching paths against a closed allowlist stays on the executing side of the design's line, and that is the load-bearing claim of this whole section.**
The pipeline runs a fixed vocabulary and never decides anything intelligent.
A total function from a change set to one of two fixed answers is not a decision: it has no model in it, no heuristic, no configuration, and no repository-specific knowledge, and it returns the same answer for the same input forever.
What *would* cross the line is asking whether a change "looks risky" or "seems safe", and nothing here asks that.
The test of whether a future addition still belongs is the same one: can it be stated as a path pattern that a human can read off the allowlist and predict, or does it require judging the content of a change?

**The allowlist, exactly.**
A path is documentation when one of these holds, and is not documentation otherwise:

1. it ends in `.md`, compared case-insensitively, anywhere in the tree; or
2. it lies inside `.docs/` or `docs/` at the repository root **and** ends in `.md`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, or `.webp`.

The tree prefix in rule 2 is doing real work rather than decorating rule 1.
A `.png` under `.docs/` is a diagram; a `.png` under `frontend/` or `tests/` may be a fixture a test compares bytes against, so the same suffix is documentation in one place and not in the other.
The prefix is matched case-sensitively and anchored at the repository root, because `Docs/` is a different directory from `docs/` on the filesystems Git is honest about, and `frontend/.docs/` is not this repository's documentation tree.
There is deliberately no `.py`, `.ts`, `.toml`, `.json`, or `.sh` at any depth: a script that happens to live under `.docs/` is the doubt case, not the easy one.

**Everything fails closed, in one direction.**
The full gate is the answer to every question the classifier cannot answer with certainty, and the reasons are recorded rather than swallowed:

- an unreadable diff, and an *empty* one - "there were no paths" is evidence of nothing, and a branch with genuinely nothing to land settles as `already_landed` before ever reaching here;
- any status other than added, modified, or deleted - a rename or a copy runs the gate **even when both of its paths are documentation**, because a rename is the shape most likely to be reported differently by a differently-configured Git and three minutes is a cheap price for never having to reason about which form arrived;
- any file mode other than `100644` and `100755`, which is what excludes a submodule gitlink (`160000`) and a symlink (`120000`) by their modes rather than by their names, and a mode *change* on an otherwise ordinary file;
- a path whose bytes did not decode as UTF-8, because a mojibaked name still ending in `.md` would otherwise pass as markdown on the strength of bytes nobody read;
- any output shape the raw parser has not seen, including a combined diff.

The change set is read as `git diff --raw -z -M <trunk HEAD>..<branch tip>`, in the trunk's checkout, after the reconcile.
The raw form rather than `--name-status` because it is the only one that carries the file modes, and the modes are the whole submodule and symlink test.
Against the trunk's **actual HEAD** rather than the merge base or the comparison ref, because the trunk's HEAD is what `merge --ff-only` moves from and therefore what the trunk really gains.
After the reconcile those two readings coincide - the trunk is an ancestor of the branch by then, so this *is* "merge base to tip" - and the one that stays correct when they do not is this one: a branch that merged an upstream ref the local trunk has not seen would otherwise have those commits classified as somebody else's problem while landing them here.
It also means the trunk's own source commits, which the reconcile just merged into the branch, are correctly not the branch's incoming change - classifying the branch's whole history instead would put every documentation branch back on the full gate the moment anybody else landed anything.

**A skipped gate is never silent** (`no silent caps`).
`classify` writes a `land_events` row on **both** outcomes, before either gate runs, carrying the class, the sentence that says why, the paths it matched, and what disqualified them.
Recording only the skips would produce a trail that answers "did anything unusual happen" rather than "which gate ran", which is the question an audit is for.
The `verify` step is still *in* the trail on the fast path, as an outcome of `skipped` rather than as an absent row, because an absent step is exactly the shape a silent skip would take.
The class is also persisted on the request itself (`verify_gate`) and drawn wherever the row is, for the reason the surface section gives.

**What this trades away, stated rather than discovered later.**
The rule's premise is that changing documentation cannot change what a gate does, and that premise is not universally true: a repository may have tests that read its own documents.
This one does - `tests/test_package_map_shape.py` asserts on the shape of `.docs/technical/*/packages*.md` - so a documentation-only branch here *can* put a change on the trunk that the next branch's gate fails on, and that next branch's agent is the one who finds out.
The manual triage rule in `CLAUDE.md` has always made exactly this bet; the fast path makes the machine make it too, at the same odds and with a durable record of every time it did.
The mitigations are the fail-closed direction (a needless three-minute gate costs three minutes, a wrongly-skipped one costs an innocent handback) and the audit trail, which is what makes such an incident diagnosable in one read instead of being a mystery about a bystander branch.
Narrowing the gate to "just the doc tests" is **not** the fix and is not available: the queue may only run bytes a human approved, so a subset is a different command needing its own approval.

### Verifying without landing, and never running one gate twice

The gate is the expensive thing in this repository - minutes of pytest - and it was being spent twice over the same bytes.
Observed 2026-08-21: a session ran `.worktree-verify` itself, confirmed it was green, asked to land, and the queue immediately ran the identical command over the identical content.
Neither run was wrong; the second one just proved what the first had already proved.

Two halves fix that, and they are one design rather than two features.

**The first half is a verify-only request.**
An agent asks the queue to run the gate instead of running it by hand, and gets the verdict back on the handback channel.
That is a better place for the gate to run than an agent's own shell for three reasons that hold independently: it is serialised with everything else the queue does, it runs under the daemon's own environment rather than an agent shell (which is where `.worktree-verify`'s known intermittent false failure lives), and - the load-bearing one - **its result is the only kind this queue will ever reuse**.

**The second half is that a green verdict is kept.**
It is keyed by the **git tree** the gate ran over and the **digest of the command that ran**, because those two are the whole of what decides the verdict.
The tree rather than the commit: a reconcile that merged an unchanged trunk produces a new commit over identical content, which is exactly the case a commit-keyed record would miss.
The digest is the same one the approval model already computes, so a command that was edited has no verdict standing rather than an old one.

A later `request_land` whose post-reconcile tree matches a standing verdict **skips the gate**, and records the reuse and its key in the event trail.
If the trunk moved in between, the reconcile produced a tree nothing has ever verified and the gate runs again - **that is correct rather than a miss**, and it is the same reasoning the classifier's fail-closed direction runs on.

**Only a run this queue executed is ever kept.**
There is no route that accepts a result from anywhere else, and the absence is the trust boundary rather than an omission.
An agent's own shell run is self-reported, and self-reporting here has a file-swap loophole: run modified bytes, restore the approved file, report a pass.
Every one of the queue's own runs, by contrast, resolved the command, checked its digest against the approval, and watched the process exit - so what is recorded is a fact the daemon observed rather than a claim it was handed.
A landing's own passing gate is kept on the same terms and for the same reason: it is the same fact, produced the same way.

**The verdict is bounded in time, and the bound is not hygiene.**
A tree hash is a claim about *content*, and the gate's verdict also depends on the machine underneath it - an installed dependency, a toolchain version, an OS update - none of which changes the tree.
So a verdict stands for `land_verify_memo_seconds` (24 hours by default, and `0` disables reuse entirely), after which the gate runs again.
The direction is the fail-closed one everything else here uses: a needless run costs minutes, a wrongly reused verdict costs a trunk.
The records are machine-local like every other row here, so a green from another machine is not merely unwanted, it is unreachable.

**What this trades away, stated rather than discovered later.**
The verdict is a claim about a *tree*, not about a checkout, so a land from worktree B can reuse a verdict produced in worktree A when both hold the same content.
Their untracked state may differ - one bootstrapped, one stale - and a gate that would have failed in B is skipped.
That is deliberate: what reaches the trunk is the tree, and the tree was proven; B's local install being stale is a fact about B rather than about what lands.
The mitigation is the same one the documentation fast path has - the reuse is in the trail with its key, so an incident is one read rather than a mystery about a bystander branch.

**A skipped gate is never silent**, on this path exactly as on the documentation one.
The `verify` step is present in the trail with an outcome of `reused`, carrying the tree, the digest, and which request produced the verdict; and the row's `verify_gate` reads `reused` rather than `full`.
The two skips are drawn differently on purpose: `documentation only · verification skipped` means nobody has ever run this content through the suite, and `verified earlier · gate reused` means this queue ran exactly it.
A reader deciding whether to trust a row needs those apart.

**Why an agent cannot simply land itself when its own run is green.**
This is the obvious shortcut and it is worse than it looks, because a green gate is only one of the things a land needs.
Landing from the agent's own shell puts a **second writer on the primary checkout** - the queue's whole serialisation is a property of its schema (`land_requests_inflight`), and a writer outside the schema is outside the serialisation.
It also skips the preconditions, which are re-checked before *every* mutation and not once at enqueue: whether the trunk is the main tree, whether the branch moved, whether the primary checkout has local changes to a file this land would overwrite.
And it produces no `land_events` row, no Tier 0 fact, and no ledger entry naming which OID moved what - so provenance attributes the movement to nobody (`provenance.md`), and the audit that makes an incident diagnosable simply has a hole in it where that land was.
The gate is the expensive part, not the authoritative part; reusing the expensive part is the whole of what this section grants.

**Verify-only runs need not serialise with landings** - they mutate no trunk, and verification is measured parallel-safe across worktrees.
They do serialise today, because they share the runner, and the queue advances one request at a time per trunk.
That is the same "the store's shape permits a later ceiling; v1 is strictly serial" the durability section already states, and lifting it is a change to the worker rather than to the schema.

### Editing it

The resolved command, which of the two mechanisms produced it, its approval standing, and an editor for the override are drawn together in the landing strip at the head of the worktree map - **once for the Project, not once per worktree**.
Both mechanisms were documented and neither was ever stated on screen, so "why is it running that" had no answer in the app; and the only way to change the answer was to hand-edit a committed TOML file.

It shipped first under every expanded Map row, which is what a per-checkout reading of the gate implies, and it was the wrong trade.
The gate *is* fingerprinted per worktree - a branch that edits `.worktree-verify` must present for approval again - but the answer is the same on almost every row almost always, and the approval act is the same act whichever row happens to be open.
Drawn per row it was the same paragraph about approved bytes under each of eight checkouts, which buries the thing an expanded row is for.
A branch whose own script really does differ is reported by its land refusing, which names the branch, rather than by eight blocks that mostly agree.

**Editing never approves, and the two are separate acts against separate routes.**
An edit cannot produce an approved command even by accident: the approval is a digest over the bytes, so moving the bytes invalidates it without the write saying anything about approval at all.
That is what keeps "an agent cannot approve the command its own land runs" true regardless of who reaches the editor - writing a verification script is a proposal, and a human turns it into an authority.

The editor writes exactly one key, guarded by the Project config's own revision, so a concurrent edit to another field loses the race rather than being clobbered by a surface that round-tripped the whole file.
An empty command **clears** the override and falls back to the `.worktree-verify` convention, which is a decision ("run the script in the tree") rather than a no-op.
A read-only or malformed config is not offered as editable.

### Setting one up in another repository

A repository with no verification command gets nothing from the land queue: `verify` refuses rather than runs, and every land refuses with it.
Writing that command in an unfamiliar repository is real work - which suite is the full one, what it must not collide with, what its exit code has to mean - and all of it is already stated in this document.
So the strip's verification section carries a copyable prompt that hands an agent exactly that, rather than leaving each operator to reconstruct it from memory in a new repo.

The prompt states four things and ends with a fifth:
the pipeline the command sits in (reconcile, gate, fast-forward) and that its **exit code is the only verdict**;
the contract it must satisfy (the worktree is its cwd, parallel-safe with no fixed port or shared temp or write outside the tree, bounded, and never laundered through a `tail`/`grep` pipeline that reports the wrong status);
the two conventions and which is preferred for *authoring* - an executable `.worktree-verify` at the root, because it is committed and travels with every checkout, else `[worktree] verify_command`, with the config key stated as the **override that wins** when both exist rather than as a fallback;
and how to prove it honest - two worktrees running it simultaneously, then a deliberately broken test that must exit nonzero.

**The last paragraph is what keeps the button from being an authority leak, and it is not decoration.**
A verification command is authority: it decides what reaches a trunk with no human present.
Everything else in the prompt asks an agent to write that authority, so without a stated ending a copyable setup prompt reads as "an agent sets up its own gate" - the one thing the approval model exists to prevent.
So the prompt ends by telling the receiving agent that it **cannot approve what it just wrote**, that approval is a separate human act against the exact bytes made in this very section, and that any edit un-approves it again by construction.

This is deliberately a *statement* rather than a mechanism, because the mechanism already exists and is not weakened here.
Nothing in the prompt can approve anything: approval is a digest over bytes, submitted through its own route, and an agent that wrote a script has moved the bytes rather than authorised them.
What the ending prevents is a narrower and more ordinary failure - sending an agent off to do work whose final step it is not permitted to take, without saying so, and having it either stall or start looking for a way to finish the job.

The prompt is **shown as well as copied**, in a collapsed disclosure beside the editor.
It is an instruction being handed to an agent that will write somebody's gate, so a copy button whose payload nobody can read before pressing it is the wrong shape for it; and the copy itself is best-effort, because `navigator.clipboard` is absent in an insecure context and refusable everywhere, so a refusal says so and the text is already on screen to select by hand.
It is a frontend template rather than a daemon read: every fact in it is a property of this design rather than of an install, and the one variable (the script convention's name) is already in the strip's own gate payload.

### What a running gate says about itself

`verifying` alone said nothing about whether a four-minute gate was thirty seconds or three minutes in.
Three signals answer that, and **each is reported only when it was really observed**:

- **Steps.** A `\n=== <name> ===\n` line on the gate's own output is a boundary the script *chose* to announce, which is the convention `.worktree-verify` already follows. The step's number, its name, and its elapsed time are facts.
  The pattern is exactly three equals signs on each side and rejects a captured name containing one, so pytest's own section rules (`===== short test summary info =====`) are not read as verification steps - which is precisely when the reading has to stay trustworthy.
- **A step count**, and only from a previous **passing** run of byte-identical bytes. A failing gate stops at its first bad step under `set -e`, so its step list is a *prefix*; recording that would predict a permanently shorter run and make every later gate read as nearly finished. A run that overruns its plan withdraws the total rather than stretching it - never "step 8 of 7".
- **Output lines.** The fallback for a gate that announces nothing. Reported as what it is: evidence the process is still producing output, never progress toward an end.

**No percentage is derived from any of it, at either end.**
A percent implies a denominator, and the steps of this repository's own gate take 45s and 3s in one run, so there is no honest one; line-of-script would be worse still, because the lines are not the work.
`frontend/test/gitLand.test.ts` and `frontend/test/renderer/git-land.spec.ts` both assert the absence rather than trusting it.

The reading is **in memory and lives exactly as long as the process does**.
It is attached to the request row only while that row is `verifying` under this daemon: a snapshot left on a finished row would be a claim about a run that is over, and a row a restart returned to `queued` has no run at all - both would read on screen exactly like a gate that is moving.
Plans are durable (`land_verify_plans`, keyed by trunk root and digest) because they are a measurement of bytes rather than of a run.

## Authority

- **Install-wide** `land_queue_enabled` is the emergency stop. Off means no branch lands anywhere, whatever any Project opted into, and the sweep does nothing.
- **Per-Project** the `land_queue` automation must be opted in (`automation-enablement.md`). It gates a capability rather than a read, so it depends on no substrate and is off by default.
- **Per-Project** `land_grant` is `off` / `draft` / `granted`, defaulting to `draft`.
  Its own field rather than a level of `session_control_grant` for the same reason it is its own automation: session control acts on a *session*, this moves a *repository's trunk*.
  **It means something narrower for a verify-only request, and the reason is what the grant is about.**
  `off` refuses both, because `off` is the operator saying agents do not drive this machinery here.
  `draft` drafts a *land* - a human decides before a trunk moves - and enqueues a *verify*, because a verify-only run moves nothing: it merges the trunk into the requester's own branch, in the requester's own worktree, and runs bytes a human already approved.
  There is nothing for a human to decide in advance about that, and drafting it would put the cheap half of the pipeline behind the approval the expensive half exists to protect - which is precisely how a gate ends up being run by hand instead.
- **Per-origin** `land_hourly_budget` bounds a runaway requester. A land costs wall-clock rather than tokens, so the cap is about a request loop, not spend.

An operator request bypasses the grant, because the operator is the authority the grant defers to.
It passes every precondition unchanged.

All three are reported by `GET /api/land` (`installed_enabled`, `project_enabled`, `agent_grant`), because none of them could be told apart from an ordinary quiet queue.
The install stop is the sharpest case: it is checked by the sweep before anything else, so with it off a request enqueues and then sits at `queued` forever - identical, on screen, to a pipeline working through a backlog.
It also had no control in any overlay until it gained one in Settings → Automation → Land queue.

**All three are Project-wide or wider, so all three are drawn once, in the landing strip.**
A control that answers "for every branch in this repository" copied into each expanded row is a standing fixture in a per-checkout pane, which is exactly what `setting-links.md` forbids - and it is the same repetition that sent the verification block up here.
The **install stop**'s gate is rendered outside the strip's disclosure so a collapsed strip cannot hide it: a gate is what a surface renders *instead of* working, and hiding one behind a summary is the same defect as rendering the surface empty.
The **Project opt-in** and **`land_grant`** decide what happens to an *agent's* `request_land` and never touch the operator's own button, so they sit inside the disclosure as one statement.
All three grant in place through the ordinary additive path; the Projects registry's **Agent authority** table is where any of them is lowered again.

A Map row that cannot land because of one of them names it and **sends the reader to the control** rather than drawing a second copy: one press opens the strip.
Naming a switch still obliges offering it, and pointing one section up on the same pane is offering it - the rule was written against a walk to an overlay, not against a scroll.

A drafted request writes an inert `land_request` observation that appears as a Fleet Queue approval row; approval is what enqueues it, and the originating session is retained as the request's origin so a handback reaches the agent that asked rather than the human who approved.

## The handback

A conflict, a verification failure, or an expired hold returns the request to the originating session through the ordinary Phase 5 prompt queue as `sender_kind: "rule"` - a bounded deterministic template, not a new agent-to-agent path.

**A verify-only request's *pass* rides the same channel**, and has to.
A land announces itself by the trunk moving; a verify-only run leaves no such evidence, so the message *is* the result.
It is the same authority under every one of the same bounds - the request is the consent, the target is the request's own origin, the template is fixed - and it spends the same single armed reply, which is why one request cannot both report a pass and hand back.
The body says what was proven, says that **nothing was landed**, says that the trunk was merged into the branch to verify it, and says that a later land of this tree will reuse the result rather than spend the gate again.

### It arrives armed, because the request is the consent

Observed live twice on 2026-08-21: a conflict handback reached its requesting session as an inert **draft**, and the session idled forever unaware its land had bounced, until a human noticed and pressed send.
Every other part of the queue had worked - the conflict was detected, the worktree was left untouched, the template was written, the row recorded why - and the answer sat in a list nobody was looking at.
A handback nobody delivers is a handback that did not happen, and the queue's whole promise is that N branches land while the operator touches only the one that genuinely conflicts.

The Phase 5 floor that produced it - **a non-human sender's write ends at a human** - is right about the thing it was written for, and this is not that thing.
That floor exists for an *unsolicited* write appearing in somebody's terminal: a rule, an observer, a peer agent, arriving unasked.
A handback is the bounded, deterministic, daemon-authored **answer to a `request_land` this very session made**, addressed to nobody else, containing nothing a model wrote.
The request is the consent, and it was given by the session that receives the reply.

So the floor is narrowed by exactly the width of the request and no further.
Five bounds, each of which is the request's own shape rather than a new permission:

- **Only the origin.** The target is the request's recorded `origin_session_id`. There is no argument that could make it another session, the same way `request_land` has no target argument - "an agent is answered about the checkout it asked to land, and no other" is true by construction.
- **Only the queue's own templates.** No model writes any part of the body, and the only messages this authority can carry are the ones this document describes.
- **Only an agent's request.** An operator's Land has no originating session, so there is nothing that asked and no consent to spend; it hands back as a draft exactly as before.
- **Only the run that asked.** A session that resumed, branched, or restarted into a new conversation is a different correspondent, and its predecessor's consent is not its own - the same run binding every auto-delivery grant carries (`auto-delivery.md`).
- **Once.** One request has one outcome, so one bounded answer is the whole of what it consented to. The cap is a number claimed atomically (`armed_replies`) rather than an inference from the state machine happening to allow only one handback today.

And it is **off with the Project's `land_queue` automation**, read at the moment the handback is written rather than trusted from when the request was accepted.
An operator who switches landing off mid-flight is switching off the thing they can see, and a request already in the queue must not keep the authority it was granted under.
There is deliberately no sixth switch: the install stop, the Project opt-in, and `land_grant` already decide whether any of this happens, and `setting-links.md` forbids a second control for one decision.

**Arming is not delivery**, and nothing here is an override.
The message is eligible for the ordinary auto-delivery controller, which still requires the install master switch, the origin's own grant, head-of-line order, the stability window, delivery readiness, quiet hours, the consecutive cap, and the emergency pause - every one of which can still refuse it.
And refusing *arming* never refuses the message: it is still enqueued, as the draft it used to always be, for a human to send.

The mechanism is one field on the queue row.
`solicited_by` names the request being answered, and it is what lets a `rule` sender arrive armed at all (`agent-messaging.md`, `prompt_queue.enqueue`).
It is recorded rather than inferred, because arming must never be the sender's claim: a row that arrived armed from a non-human sender has to be able to name what asked for it.
The handback's `land_events` row carries `armed` and, when it is false, the `arming_reason` - a draft nobody delivered otherwise reads, from the trail alone, exactly like an answer that arrived.

**The same authority is available to any bounded reply a session solicited**, and is not land-specific.
`watch_session` (`mux-mcp.md`) is the other one in the tree and is now wired in on exactly this authority: a watch is likewise an explicit request whose single bounded notice goes back to the session that armed it, and until it was wired it landed as the same inert draft for the same reason.
It is one call site passing `armed=True` and `solicited_by=<watch id>`, under its own equivalents of the five bounds above.
Four of them it satisfies by construction rather than by checking: the watcher is the target because `watch_session` has no recipient argument, the body is a fixed `session_watch` template, an operator has no way to arm a watch at all so there is no non-agent case, and one watch matures into exactly one notice - the service pops it from its register before staging - so the cap is 1 structurally and there is nothing to claim atomically.
What it re-checks at write time is the run binding and the feature's own switch: `session_watch_enabled`, which is install-wide and has no per-Project half, read when the notice is written rather than trusted from arming, for the same reason the Project's `land_queue` opt-in is.
The notice's resolution carries `armed` and, when it is false, the `arming_reason`, and the service counts `armed_notices` beside `resolved` - the two diverging is what a queue full of undelivered notices looks like from the outside.

One half of the land pattern is deliberately not copied yet: a watching session gets no `reply_windows` entry.
A watcher that arms a watch and idles keeps its grant through the 60-minute idle TTL, which covers the 30-minute default timeout, so the common case delivers; a watch configured past the TTL still lapses precisely while it waits, exactly as a land request did before `origin_windows`.
That is the same failure with the same fix and is worth doing, but it widens `set_solicited_requests` from one evidence source to several and is a separate change.

### A session that asked to land does not lapse while it waits

Arming alone is not enough, and the second half is easy to miss because it fails the same way.
A session that requests a land goes quiet **by definition** - it is waiting - so its auto-delivery grant lapses on the idle window precisely while the pipeline computes the answer, and the armed handback then arrives with nothing to deliver it.
The window is 60 minutes by default; a hold runs to 30 and can be configured to 24 hours, and a serial queue of seventeen branches at three minutes each is an hour on its own.

This is the same shape `auto-delivery.md` already gives a delivered agent message, with the land request in place of the message: the session is the waiting half of a bounded exchange it opened itself, except that what owes the answer is the daemon rather than a peer.
So it is the same mechanism rather than a parallel one - one more source of evidence behind `reply_windows`, reported as `kind: "land"`, holding off the **idle lapse** and nothing else.
It is bounded the same two ways: a request that reached a terminal state opens no window, because the answer has already been written; and the clock runs from the last step the pipeline actually recorded, so a queue that has stopped moving stops holding the grant.

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

`land_events` is the authoritative per-step trail: request, reconcile, classify, verify, land, and every refusal, handback, and orphan, each with its reason and detail.
A handback additionally records whether its message was **armed**, and when it was not, why not - a draft nobody delivered is otherwise indistinguishable in the trail from an answer that arrived, which is the defect the arming rule exists to fix.
`classify` is written on both of its outcomes and `verify` is written even when it was skipped - as `skipped` for a documentation-only change set, and as `reused` *with its key* (the tree, the digest, and the request whose run produced the verdict) when a standing verdict was accepted - so "which gate ran" is answerable from the trail alone for every request that reached the gate, and a reused verdict is checkable rather than merely asserted.
A verify-only request's result additionally records a `verify`/`reported` row carrying whether its answer reached its author armed, for the same reason a handback does.
A step is additionally mirrored into Tier 0 when the request has an originating session, so a land appears beside that run's other facts; an operator-initiated land has no session and simply has no such row.

**A gate that ran is recorded as a `test_result` fact**, not only as a land event.
The gate is the only test run most branches ever get and it runs out-of-band - the daemon executes it, so no tool call and no transcript records it - which left the substrate holding one `test_result` fact against 4,485 `command_result` facts in a measured 24-hour window and made declared-vs-verified a statement about capture rather than about an agent (`tier0-facts.md`, `deterministic-consumers.md`).
Only `passed` and `failed` become facts: `not_configured`, `unapproved` and `timed_out` are statements about the setup or about a run that never finished, and recording them as a failed test run would put a verdict on the branch that nothing ever tested.
A failed gate states a failure count and **omits** `failing_tests` unless the output named tests, because an empty list reads everywhere as "nothing is failing".

## Surface

**Landing is part of the Git tab's worktree map, and has no surface of its own.**
It shipped as a fourth Git reading beside Map, on the same watch-here/act-there split the prompt Queue has with the Fleet Queue: Map answers "what is in this worktree", Land answers "what is happening to it".
That split did not survive contact. Landing a branch is an act on a checkout you are already looking at, and a separate view meant a launch list that was a second copy of Map's, with the button furthest from the diff that decides whether to press it.
Moving the act onto the row fixed that and exposed the other half: what was left on the segment was one Project-wide block, and a parallel view holding one block is not a view.

So the map holds both halves, split by **what each part is a property of**:

- **The row owns the act**, and only the act. Expanding a worktree shows that branch's Land button, its live land state (including the running gate's own reading of itself), a Cancel while the request is still cancellable, and what stopped it last time - a conflict's paths, a refusal's reason, which are facts about *this* branch.
  A land that **skipped** the gate says so here and in the strip's queue and history, because it is the one thing about a finished land that the states cannot show: a documentation-only row goes from merging the trunk straight to fast-forwarding, never passing through `Verifying`, and afterwards reads exactly like a land that passed three minutes of pytest.
  A *full* gate is deliberately not labelled, because it is what every land does and the states already narrate it; drawing both would put a redundant chip on every row and bury the one that matters.
  A **verify-only** row is labelled on exactly the same grounds and in exactly the same place: it moves through `Merging trunk` and `Verifying` in a landing's own words and stops one step early, which is when nobody is still watching, so `verify only` is drawn beside the branch - before the states it qualifies - and its green reads `Verified` rather than `Landed`.
  The main tree is the trunk these land *onto* and is never offered; a detached worktree states why rather than offering a button that would be refused.
  There is deliberately **no operator button for a verify-only run**: an operator with a worktree open has a terminal in it, and the value the queue adds here is the reusable verdict, which their next Land consumes without being asked. Verify-only rows still appear in the strip's queue and history like any other, so nothing is hidden - only unstartable from the map.
- **Map's selection mode can start many lands at once, and that is all it does** (`git.md`).
  Selecting worktrees and pressing Land sends one ordinary request per branch, in map order, through the same route and the same preconditions as the row's own button - the queue then runs them one at a time, which is the serialization it already guarantees rather than anything the bulk control arranges.
  It waits for nothing, reorders nothing, and cannot skip a precondition: a request the queue refuses is reported beside the branch it refused, and the rest are enqueued regardless.
  The main tree and a detached HEAD are named as unable to land rather than enqueued and refused, for the same reason the row states them.
  A bulk press starts ordinary lands only, for the same reason a single row does: there is no operator button for a verify-only run.
- **A compact strip at the head of the map owns everything Project-wide**: the verification command with its source, approval, recorded plan and editor; who besides the operator may start a land; the queue in the order the pipeline will reach it; and what finished.

Nothing Project-wide is drawn on a row, and that is the whole point of the split rather than a detail of it.
A fact that is true of the Project is drawn N times if it lives on a row, and the verification block shipped that way once: the same paragraph about approved bytes under each of eight expansions, burying the diff the expansion was opened for.

**The strip stays a strip, so the tab still reads as a map.**
It is one summary line - the gate's standing, and what the queue is doing right now, including a running gate's step - with everything else behind a disclosure.
It **opens itself when landing is blocked**, which is exactly two states (the install stop is off, or the bytes a land would run are not approved) and in both the act that clears it is inside; a surface that cannot work must not render as merely quiet (`setting-links.md`).
An explicit collapse wins after that and nothing re-opens under the reader, which stays honest because the summary line goes on stating the block while closed.

**A bounced request stops speaking for the queue once its branch gets another answer.**
The summary line picks the most interesting row, and a handed-back or refused request is terminal *and* unresolved, so it outranks a quiet queue.
Nothing ever closed one: an agent's redo is a **new** request with a new id, so the bounced row sits in the history for good and the summary resurrects it forever.
Observed 2026-08-21 - the collapsed strip read `worktree-watch-session-settle · returned to agent` for hours after that very branch's redo had landed, through several unrelated landings, which is the queue reporting a state it had already left.

The supersession rule is therefore: a bounced request stops being the queue's headline the moment a **later** request for the same branch reaches a state that answered the branch (`landed`, `verified`, `already_landed`, `refused`, or another `handed_back`).
`verified` counts, and has to: the redo loop a handback asks for now often runs through a verify-only request first, so leaving it out would reproduce the exact defect this rule was written for, one request kind over.
`cancelled` deliberately does not supersede, because withdrawing a re-request is not an answer about the branch and the earlier handback is still the standing fact about it.
Ties do not supersede either: two rows created in the same second are not ordered by anything the reading can see, and the safe direction for a row whose whole job is asking for attention is to keep asking.

It is **derived at the reading, not written back onto the row**, and that is the load-bearing half.
The handback really did happen, and `land_events` and the history disclosure are an audit that must go on saying so - what was wrong was never the record, only which row spoke for the queue.
A "closed" column would also be a second writer's opinion about a terminal row, in a store whose serialization is a property of its schema.
With nothing left to report, the strip renders an idle summary carrying what recently landed (`nothing queued · 3 landed recently`) rather than the stalest historical row; the count is `landed` only, inside a 24-hour window, over the newest 100 rows `GET /api/land` returns, so it is a floor and is drawn only where the alternative is a bare "nothing queued".
`verified` is not counted there, because nothing moved and the line says *landed*.

Queue order is oldest-first - the order the pipeline will actually reach them.
The daemon lists newest-first because that is what a history read wants; read backwards, the request about to run sat at the bottom.
Finished requests are folded into a history disclosure, newest first.

**The retired segment is migrated, not deleted.**
`RETIRED_DRAWER_SEGMENTS` keeps `git/land` pointed at `git/map`, so the palette entry "Open Land" and the voice phrases "open/show/go to Land" still answer and now land on the map; a stored segment selection migrates on read rather than falling through to "the tab's first segment", which is Map today by coincidence; and `drawer.git.land` migrates in `keybindings.py`, where an unmigrated id is *rejected* rather than ignored.
These rows stay forever, exactly like the retired tab ids beside them.

Nothing in the surface lands anything: the daemon's own supervised sweep is the only thing that moves a trunk.

## API

```text
GET    /api/land?project_id=                      # the queue and its bounds
POST   /api/land            {project_id, worktree_root, kind?}   # kind: "land" (default) | "verify"
DELETE /api/land/{request_id}                     # cancel a queued or waiting request
GET    /api/land/{request_id}/events              # the per-step audit trail
GET    /api/land/verify-command?project_id=&worktree_root=
PUT    /api/land/verify-command          {project_id, command, revision, worktree_root?}
POST   /api/land/verify-command/approve  {project_id, worktree_root, digest}
```

`GET /api/land` attaches `verify_progress` to a row that is `verifying` under this daemon, and `null` to every other row.
Every row also carries `verify_gate`: `""` until it is classified, then `"full"`, `"docs_only"`, or `"reused"`.
The empty value is not collapsed into `"full"`, at either end - a row that claims a classification nothing recorded is the wrong direction for a field whose entire job is making a skip visible - and the client reads any value it does not recognise as `""` rather than as a skip.

Every row also carries `kind`: `"land"` or `"verify"`.
`POST /api/land` takes it too and defaults it to `"land"`, so a caller written before verify-only existed asks for exactly what it always asked for; an unrecognised value is a `400` rather than a silent land.
The client reads an unrecognised value as `"land"`, which is what every row written before this existed actually was, and which is the smaller lie of the two directions: a verify-only run drawn as a land under-claims, a land drawn as a verify-only run tells a reader a trunk did not move when it did.

`GET /api/land/verify-command` additionally reports the editable half (`config_command`, `config_revision`, `config_status`, `config_path`), which convention applies (`script_name`, `script_present`), and the `plan` a byte-identical passing run recorded, if any.

`PUT` sets or clears `[worktree] verify_command` and never approves; it refuses `409 revision_conflict` on a stale revision and `409 project_config_malformed` on a config it cannot parse, and writes nothing when it refuses.
Approval requires the digest the caller was shown; a stale one is refused, because the bytes moved between the prompt and the click.
Both leave an audit record (`land_verify_command_changed`, `land_verify_approved`).

## The agent surface

`request_land` and `request_verify` (`mux-mcp.md`), two callers over the same service.
Neither has a **target argument**: the checkout comes from the caller's own live cwd, so "an agent acts on the checkout it is working in, and no other" is true by construction rather than by a check something could be routed around.

They are **two tools rather than one tool with a flag**, and the reason is which call is the default spelling.
A flag would make the request that moves a repository's trunk the plain form of the request that moves nothing, so a caller that omitted it would land; and it would put both under one grant, when the grant exists for the trunk.
Two tools make the safe call the short one and let the grant say different things about each.

## Configuration

| Setting | Where | Meaning |
|---|---|---|
| `land_queue_enabled` | global | Install-wide emergency stop. |
| `land_hourly_budget` | global | Requests per origin session per hour. |
| `land_hold_timeout_seconds` | global | How long a busy worktree holds before handing back. |
| `land_retry_verification` | global | Whether a failed gate is retried once. |
| `land_verify_memo_seconds` | global | How long a queue-executed green verdict stands for its (tree, digest). `0` disables reuse. |
| `land_queue` | `<project>/.swe-mux/config.toml` `automations` | Per-Project opt-in. |
| `land_grant` | `<project>/.swe-mux/config.toml` | `off` / `draft` / `granted`, default `draft`. |
| `[worktree] verify_command` | `<project>/.swe-mux/config.toml` | Explicit override of the `.worktree-verify` convention. |

Every `global` row above is edited in Settings → Automation → **Land queue**.
The install stop had a control from the start and the other four did not, which is the shape
this feature's own prose already names for the verification command: a bound that only a
config-file edit can reach is a bound nobody adjusts and nobody can see.

## Key files

- Service, pipeline, handbacks, verdict reuse: `src/swe_mux/land_queue.py`
- Durable rows, the audit trail, recorded gate plans, and standing gate verdicts: `src/swe_mux/land_store.py`
- Precondition reads and their dispositions: `src/swe_mux/land_preconditions.py`
- The closed documentation allowlist and the raw-diff parser behind it: `src/swe_mux/land_classify.py`
- The gate, its approval store, and its runner: `src/swe_mux/worktree_verify.py`
- What a running gate reports about itself: `src/swe_mux/verify_progress.py`
- Shared command resolution and bounded execution: `src/swe_mux/worktree_exec.py`
- Routes, the busy-session probe, the drafted-request approval: `src/swe_mux/server.py`
- The act, on the Map row: `frontend/src/GitLandRow.tsx`
- The strip at the head of the map (queue, verification command, agent authority): `frontend/src/GitLandBar.tsx`
- The retired segment and its migration: `frontend/src/drawerSegments.ts`, `frontend/src/drawerLayout.ts`, `src/swe_mux/keybindings.py`
- Shared queue/gate reads: `frontend/src/landState.ts`; parsing, labels, supersession, and the strip's summary line: `frontend/src/gitLand.ts`
- The copyable setup prompt for another repository: `frontend/src/landSetupPrompt.ts`
- Tests: `tests/test_land_queue.py`, `tests/test_land_api.py`, `tests/test_verify_progress.py`,
  `tests/test_land_classify.py`,
  `frontend/test/gitLand.test.ts`, `frontend/test/renderer/git-land.spec.ts`

## Relates to

- `git.md` - worktree tooling, the main-tree test, and the comparison ref this lands onto.
- `project-actions.md` - the exact-content approval model, and why an action cannot be the gate.
- `prompt-queue.md` - the channel a handback rides.
- `agent-messaging.md` - the arming floor a solicited reply narrows, and where `solicited_by` is defined.
- `auto-delivery.md` - the controller that delivers an armed handback, and the reply window an open land request holds open.
- `automation-enablement.md` - the per-Project opt-in and the grant shape.
- `mux-mcp.md` - the `request_land` tool and its bounds.
- `tier0-facts.md` - the joinable copy of each step.
