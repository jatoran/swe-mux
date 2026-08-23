# Changing swe-mux itself

Read this before touching a line of swe-mux's own code, and read it *before*
agreeing that a code change is possible at all.

## First: is there anything to edit?

`configurator_capabilities` reports `install.mode`, and it decides everything below.

- **`frozen`** - this is the packaged desktop app. There is no source checkout.
  Nothing you edit will be what this app runs. Say so plainly; do not go looking for
  files.
- **`installed`** - swe-mux is a package in an environment. An installed copy is
  replaced by the next install, not amended. Edits there are lost and are not a
  supported way to change anything.
- **`source`** - a real repository checkout, reported as `install.source_checkout`.
  This is the only case where a code change is meaningful.

Settings and configuration work identically in all three. Only *code* is gated.

## The trap that costs the most time

**A verified-correct change that "still does not work".**

Two builds can be running the same feature:

- A daemon started from source serves the frontend from the source tree and, on
  restart, respawns *your* code.
- The frozen desktop app serves its **own bundled copy** of the frontend and, on
  restart, respawns its **own bundled backend**.

So on a frozen app, rebuilding the frontend does nothing visible, and restarting the
daemon re-runs the old backend. The change is right; it never loaded.

Before debugging a change that appears to have no effect, settle which build is being
served. Comparing the hashed asset the live daemon returns against the one that was
just built is the direct check: if they differ, a frozen app is being served and a
plain frontend build is not enough.

This is the number one cause of "your fix does not work" on this project.

## How a change reaches the running app

**Sessions survive all of this.** None of the supported flows below stop a terminal.

- **Frontend change, source daemon** - rebuild the frontend, then reload the UI.
- **Backend change, source daemon** - restart the daemon (there is an endpoint, a
  menu item, and a CLI command). Every session survives; the successor process runs
  the new code because it is the same executable running from source.
- **Frozen app, either kind of change** - a rebuild-and-redeploy is the only path.
  It is a multi-minute packaging build, and it is staged: it builds beside the
  running app, stops it only after a successful build, swaps, and rolls back if the
  new build never becomes healthy. A failed build leaves the running app untouched.

Do not run any of these on your own initiative. Say which one is needed and how long
it takes, and let the operator decide.

## The one that stops every session

Changes to the PTY supervisor - the separate process that owns the terminals and is
the reason sessions survive daemon restarts at all - **cannot be shipped by the
redeploy**, and the redeploy says nothing when it does not ship them. The bundle
updates, the supervisor stays stale, and the change silently does nothing.

Updating the supervisor requires stopping everything, which **reaps every live
session**. It is a deliberate act, not part of a normal update, and it must be run
from a terminal outside swe-mux.

The right instinct is to avoid needing it. A change that an older supervisor can
reject cleanly, while the daemon degrades gracefully, needs no reap; a protocol
version bump forces one.

If the operator asks for a change in that area, tell them the cost before writing
any code. "This will end every session you have running" is not a footnote.

## Never do these as part of an update

Shutting the daemon down, killing the supervisor process, or force-killing swe-mux
processes. Every one of them reaps live sessions. They exist for intentionally
stopping everything, and for the deliberate supervisor update above - not for
applying a change.

## Working in a repository at all

If the operator does want code changes and there is a source checkout, the normal
discipline applies: work on a branch (a worktree, if others are working at the same
time), run the repository's verification command, and land through the queue or the
two manual commands. See the `worktrees` guide.

And the rule that outranks all of it: **a worktree is not a place to run the app.**
It isolates the working tree, not the port or the data directory.
