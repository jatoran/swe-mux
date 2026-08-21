/**
 * The prompt an operator hands an agent to set up worktree verification somewhere else.
 *
 * The land queue's whole value is that a machine runs the gate, and a repository with no
 * verification command gets nothing from it: `verify` refuses rather than runs, and every
 * land refuses with it. Writing that command is real work in an unfamiliar repository -
 * which suite is the full one, what it must not collide with, what its exit code has to
 * mean - and all of it is already written down here, in this document and in the docs the
 * strip points at. So the strip hands it over as text rather than making each operator
 * reconstruct it from memory in a new repo.
 *
 * **It is a proposal, and the last paragraph is what keeps it one.** The prompt ends by
 * telling the receiving agent that it cannot approve what it just wrote, because a
 * verification command is authority - it decides what reaches a trunk unattended - and
 * approval is a human act against the exact bytes, made in this very section afterwards
 * (`land-queue.md`, "Editing never approves"). Without that ending, a copyable prompt is
 * a nicer way of saying "an agent set up its own gate", which is the one thing the
 * approval model exists to prevent. It is not a mechanism - the daemon enforces the
 * separation whatever any prompt says - but a prompt that omitted it would be sending an
 * agent off to do work whose last step it is not allowed to take, without saying so.
 *
 * Deliberately not fetched from the daemon. Every fact in it is a property of the land
 * queue's design rather than of this install, the strip already holds the one variable
 * (the script convention's name), and a template is the thing most likely to be edited by
 * whoever is reading the section it sits in.
 */

/** The conventional override key's file, relative to the repository root. */
const CONFIG_PATH = '.swe-mux/config.toml'

/**
 * A ready-to-paste prompt for setting up verification in another repository.
 *
 * `scriptName` is the executable-file convention as this install resolves it, so the
 * prompt names the same file the reader's own strip is talking about rather than a
 * hard-coded one.
 */
export function verifySetupPrompt(scriptName: string): string {
  const script = scriptName || '.worktree-verify'
  return `Set up worktree verification for this repository, so swe-mux's land queue can land branches here.

## What the command is for

swe-mux's land queue lands a finished worktree branch onto the trunk with no human driving it.
For one branch at a time, in order, it does three things: merges the trunk into the branch inside that branch's own worktree (reconcile), runs this repository's verification command there (the gate), then fast-forwards the trunk onto the branch.

The gate's process **exit code is the only verdict**. Zero lands the branch. Nonzero hands the request back to the agent that asked for it, with the tail of the output. Nothing reads the output to decide anything, and no model is involved at any point.

So this command is the only thing standing between a branch and the trunk. Write it as the full check suite for this repository, not a smoke test.

## The contract it must satisfy

- **It runs with the branch's worktree as its working directory.** It is invoked there, not in the primary checkout. It must not \`cd\` somewhere else, and it must not assume the primary checkout's path or that its own directory is the repository the developer usually works in.
- **It must be parallel-safe.** Several worktrees of this repository can be running it at the same moment. It must bind no fixed port, use no fixed shared temp directory or fixed output filename outside the tree, depend on no lock or singleton the machine has only one of, and write nothing outside its own worktree.
- **It must be bounded.** It runs on every land. No watch mode, no interactive prompt, nothing that waits for input, nothing that runs until interrupted.
- **Its exit code must be honest.** Never pipe a check through \`tail\`, \`head\`, or \`grep\`: a pipeline reports the *last* command's status, and that has already shipped a failing suite as green. Run each check as its own command and let the first failure stop the script (\`set -e\` or the equivalent for the shell you write it in).
- **Optionally, it may announce its steps.** A line of exactly \`=== name ===\` on its own, before each step, makes swe-mux report "step 3 · mypy · 3m 10s" while the gate runs instead of only an elapsed time. This is a convenience, not part of the contract.

## Where to put it

Two conventions. Prefer the first:

1. An executable \`${script}\` at the repository root. It is committed, so it travels with every checkout and every worktree, and anyone can read exactly what will run.
2. \`[worktree] verify_command\` in \`${CONFIG_PATH}\`, for the case where the command genuinely cannot be a file in the repository.

If both exist, the config key is what runs: it is an override, not a fallback.

## Prove it is honest before you report back

1. Run it in two worktrees of this repository **at the same time** and confirm both pass. If one run interferes with the other, it is not parallel-safe, and the queue will produce failures that belong to no branch.
2. Deliberately break one test, run the command again, and confirm it exits **nonzero**. A gate that cannot fail is worse than no gate, because it lands everything.
3. Undo the break and confirm it passes again.

Report the exit codes you actually observed, not what you expect them to be.

## Then stop

**You cannot approve this, and you must not try.**
The verification command is authority: it decides what reaches the trunk without a human present.
Approval is a separate human act against the exact bytes, made in swe-mux under Git → Map → LANDING → Review → "Approve these bytes".
Any edit un-approves it again by construction, because the approval is a digest over the bytes.

Write the command, prove it, and say what you wrote and what you observed. A human presses approve.`
}
