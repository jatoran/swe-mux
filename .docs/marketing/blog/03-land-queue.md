# Letting agents land their own branches without trusting them

*Engineering post. Submit to HN and r/programming. The angle: safety by construction, not by model alignment.*

---

If you run coding agents in parallel, the merge step is where the wheels come off.
Each agent works in its own git worktree on its own branch - that part is solved.
Then five branches finish within an hour of each other and you become a human merge queue: reconcile, verify, fast-forward, repeat, in order, without fat-fingering it.

I automated that, and the interesting part isn't the automation.
It's the constraint set that makes it safe to let an agent trigger a merge into trunk without me reading the diff first.

## The pipeline decides nothing

The land queue runs a fixed sequence for one branch at a time:

1. Merge current trunk into the branch (reconcile).
2. Run the project's verification gate - the same pytest/ruff/mypy/tsc/npm-test command a human would run.
3. Fast-forward the trunk onto the branch.

The load-bearing choice is that **fast-forward-only is the only merge the trunk ever sees.**
Git refuses a fast-forward if the branch diverged, and refuses it if it would overwrite local changes.
So the trunk step cannot lose work *by construction* - not because the pipeline is smart, but because it's only allowed a git operation that has no destructive failure mode.

Everything that requires judgment is deliberately not the pipeline's job.
A merge conflict or a failed gate doesn't get "handled" - it goes back to the agent that owns the branch, as a message in that agent's queue.
The pipeline never resolves a conflict, never retries with force, never decides a failure is probably fine.

## An agent cannot approve its own gate

The verification command is approved by a human as exact bytes - a content digest, not a filename.
An agent can edit the command; that edit invalidates the approval.
An agent can run the gate itself in its own shell; that run counts for nothing, because a self-reported green has an obvious loophole (run modified bytes, restore the approved file).
Only a gate the queue itself executed, over a recorded git tree, with an approved command digest, skips re-verification later.

This sounds paranoid until you've watched an agent confidently declare tests passing that it never ran.

## Don't re-verify what didn't change

The naive version of this re-runs the full gate for every branch, and documentation-only changes eat three minutes of pytest.
The queue classifies the incoming diff against a **closed allowlist** - markdown anywhere, doc assets under the docs tree - and skips the gate when every path matches.
Closed allowlist, not a heuristic: matching paths is a total function with no model in it.
Anything it can't answer with certainty - a rename, a submodule, an unreadable diff - answers "run the full gate."
And the skip is recorded with its reason, because a doc-only land that never entered verification must not read identically to one that passed the tests.

## Results

[verify: current counts] Whole waves of parallel branches - 7 in one afternoon, 16 across a weekend - landed serially with no human doing the mechanical work, and every conflict routed to the agent that had the context to resolve it.
The human's job collapsed to two things: approving the gate command once, and reading the event trail when something bounced.

That's the general shape I'd argue for in any agent system: don't make the automation smart, make the failure modes impossible, and route everything that needs intelligence back to something that has it.

swe-mux is open source (Apache 2.0): github.com/[org]/swe-mux.
