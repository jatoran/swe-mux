# Worktrees, verification, and landing

Only relevant to a Project that is a Git repository, and mostly relevant when more
than one agent is working in it at once.

## What a worktree isolates, and what it does not

A Git worktree gives a branch its own checkout on disk, so two agents can edit the
same repository without stepping on each other.

**It isolates the working tree. It does not isolate the runtime.**

That distinction is the single most important thing to convey. The daemon owns one
port and one data directory, both process-wide singletons. Starting a second daemon
from inside a worktree, or running the app there, collides with the live one and with
the operator's real sessions.

So: a worktree is for editing and testing. Never for running the app.

## Setup and verification commands

A Project can declare two commands in its committed `.swe-mux/config.toml` under
`[worktree]`:

- `setup_command` - what bootstraps a fresh worktree (dependency install, typically).
- `verify_command` - the full check suite.

Alternatively, and preferably, verification is an executable script committed at the
repository root, so it travels with every checkout and anyone can read exactly what
will run. If both exist, the config key wins - it is an override, not a fallback.

## The verification contract

The command's **process exit code is the only verdict.** Zero means the branch may
land. Nonzero refuses it. Nothing reads the output to decide anything and no model is
involved at any point.

That makes it the only thing standing between a branch and the trunk, so it must be
the full suite rather than a smoke test, and it has four properties:

- **It runs with the branch's worktree as its working directory.** It must not `cd`
  elsewhere or assume the primary checkout's path.
- **It must be parallel-safe.** Several worktrees can run it simultaneously: no fixed
  port, no fixed shared temp path, no lock the machine has only one of, and nothing
  written outside its own tree.
- **It must be bounded.** No watch mode, no interactive prompt, nothing waiting for
  input.
- **Its exit code must be honest.** Never pipe a check through `tail`, `head`, or
  `grep` - a pipeline reports the *last* command's status, and that has genuinely
  shipped a failing suite as green.

If a repository has no verification command, the queue refuses rather than runs, and
every land refuses with it. That is correct: a gate that cannot fail is worse than no
gate, because it lands everything.

## The land queue

Off by default, per Project.

When it is on, it automates a fixed sequence for one branch at a time: merge the
trunk into the branch inside that branch's own worktree (reconcile), run the
verification command there (the gate), then fast-forward the trunk onto the branch.

Three properties are worth knowing:

- **It never resolves a conflict.** A conflict comes back to the requesting session
  as a message.
- **It never runs a gate whose exact bytes a human has not approved.** The approval is
  a digest over the bytes, so any edit un-approves it by construction. An agent
  therefore cannot approve its own gate, which is the whole point - a verification
  command is authority, since it decides what reaches a trunk unattended.
- **It skips the gate for documentation-only changes**, classified against a closed
  allowlist, and records the class and its reason in the request's trail.

The fallback, and the thing to reach for when the queue is not enabled, is the two
manual commands: reconcile in the worktree, then a fast-forward merge from the
primary checkout. The fast-forward is deliberately the only merge allowed outside a
worktree because it cannot lose work - Git refuses it if the branch diverged and
refuses it if it would overwrite uncommitted local changes.

## Advising on this

If someone is setting up verification in a new repository, the useful thing is not to
write the command for them from here - it is to say what the contract requires and
have them prove it: run it in two worktrees simultaneously and confirm both pass,
deliberately break one test and confirm it exits nonzero, then undo and confirm it
passes again. Reported exit codes, not expected ones.

Then a human approves the bytes. That step is not yours and is not optional.
