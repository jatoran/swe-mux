# Knowing when an AI coding agent is actually done

*Engineering post. Submit to HN. The angle: an unglamorous problem that turns out to be the keystone.*

---

Every multi-agent tool has to answer one question constantly: is this agent working, idle, waiting for me, or stuck?
Get it wrong in one direction and you interrupt an agent mid-task.
Get it wrong in the other and an agent sits on a permission prompt for forty minutes while you think it's working.
Every downstream feature - notifications, prompt queues, auto-delivery, watches - inherits the answer, so a wrong status silently corrupts all of them.

It sounds trivial.
It is not, and I have the scar tissue to prove it.

## Why the terminal lies

The obvious approach is reading the terminal: spinner means working, prompt means ready.
The obvious approach fails constantly:

- TUIs redraw the whole screen; a repaint is not activity.
- Spinners keep spinning during network stalls that are actually the agent being stuck.
- Some CLIs go completely silent during a large paste, then a settle-probe "confirms" data loss that didn't happen.
- An agent that finished can be visually identical to an agent waiting on an approval dialog.
- `idle` is not `input-ready`: a startup dialog reads idle and eats your delivered prompt.

Any single detector produces confident garbage.
The system that works is layered: harness-native signals where the CLI provides them (hooks, structured events), terminal heuristics where it doesn't, and reconciliation between the layers when they disagree.

## The golden corpus

The thing that actually made this reliable was boring: **captured real sessions as a regression corpus.**
Every time detection failed in the wild, the raw terminal capture became a test case.
New detector logic runs against the whole corpus; a change that fixes today's bug and re-breaks last month's fails in CI.

Detection logic without a corpus is astrology.
The corpus is why I can refactor detectors without fear, and it's the first thing I'd tell anyone building in this space to start collecting.

## Durable evidence, or it didn't happen

Every state transition is written to a ledger in SQLite: what changed, which detector layer said so, and when.
When a session shows the wrong status, the investigation isn't "stare at the code" - it's read the ledger, find the transition that lied, pull the capture, add it to the corpus, fix the layer.
There's an incident runbook, because status bugs recur in families.

A watchdog sits on top for the worst case: a session stuck reporting "working" past plausibility gets re-evaluated rather than trusted forever.

## The payoff

When status is trustworthy, you can build on it.
Notifications that only fire when an agent genuinely needs a human.
A prompt queue that delivers when the agent is *ready*, not merely quiet.
Agent-to-agent watches that report "settled" instead of guessing.
None of that is buildable on vibes.

The unglamorous layer is the load-bearing one.
It usually is.

swe-mux is open source (Apache 2.0): github.com/[org]/swe-mux.
