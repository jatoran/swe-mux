# Proposal: what to do about the residual "ready" notification volume

Status: **proposed, not built.** Written 2026-08-02 alongside the correctness fixes that
landed with it. This document is the design decision those fixes deliberately left open,
because it changes what the feature *means* rather than repairing what it claims.

Its main result is negative, and that is the point: the two mechanisms that look obviously
right were simulated against a real day and **do not work**. Do not build them on intuition.

## The problem the fixes do not solve

Over one measured 10-hour day (2026-08-02, noon to 22:00 CDT), the daemon raised **211**
`state_changed → idle` events across **17** sessions. Every one was a candidate
"The agent is waiting for your input." push, because the mobile profile has `waiting: true`
and the category is fleet-wide.

The fixes that landed remove the ones that were simply *false*:

| filter | remaining |
| --- | --- |
| baseline | 211 |
| running-work suppression (`subagents`/`background_tasks`, `waiting_on_background`) | 129 |
| \+ startup idle (`previous: starting`) | 121 |
| \+ 120 s settle hold cancelled by the agent resuming | **62** |

62 alerts in 10 hours is a 71% reduction and still roughly one every ten minutes. Nothing
is wrong with any of them: they are real turn ends of real sessions. The residue is not a
detection defect, it is a **scope** question — `waiting` means "some session somewhere
became idle", and on a 17-session fleet that is not obviously an actionable signal.

## What does not work (measured, not argued)

### Recency scoping — rejected

The intuition: notify only for sessions the human has touched recently; an alert about a
session left alone for hours is noise even when true. Simulated against the 62 survivors,
using `terminal_input` as the human-interaction signal (1835 of 1841 in the window carry
`input_owner: 1`, so it is a clean human signal):

| window | kept | dropped |
| --- | --- | --- |
| 60 min | 61 | 1 |
| 30 min | 58 | 4 |
| 15 min | 49 | 13 |
| 5 min | 37 | 25 |

At any window loose enough to be safe it drops almost nothing. The premise was wrong: the
user was **actively working in every noisy session**. The single loudest one (16 of the 62)
received 448 human input events that day. There is no forgotten-session tail to cut,
because there were no forgotten sessions.

### Rate limiting — rejected as a mechanism

| limit | kept of 62 |
| --- | --- |
| per session, 10 min | 55 |
| per session, 15 min | 48 |
| per session, 30 min | 40 |
| fleet-wide, 5 min | 40 |
| fleet-wide, 10 min | 30 |

The alerts are genuinely spread out, so a limit tight enough to matter (fleet-wide 10 min,
30 of 62) is also tight enough to swallow the alert the user was waiting for, with no way
to tell which one it ate. Rate limiting trades a known noise problem for an unknown silence
problem. It is a reasonable *backstop* under an explicit cap; it is not a scoping mechanism.

## What is actually left

The 62 are irreducible by heuristic. They are 62 real turn ends over 10 hours across a
fleet the human was working in continuously. Anything that cuts them further is a policy
decision about what the user wants to be told, and there are only two honest ones:

### 1. Turn `waiting` off on mobile and keep `attention` (recommended, no code)

Measured over the same window: `attention` fired **2** times (2 `approval_needed`), and the
failure class 4 times (`turn_aborted`). Those are the events that genuinely block on a
human. `waiting` is not a "you are needed" signal at all — it is a progress feed, and a
progress feed on a lock screen is a firehose by construction when you run 15 agents.

This is a settings change (`profiles.mobile.notifications.events.waiting = false`), and it
should be the default for the mobile profile rather than the current `true`. The current
default was set when the fleet was small enough for "a session went idle" to mean
something.

### 2. Explicit per-session opt-in (the only mechanism that discriminates)

A "notify me about this one" toggle on the session, persisted like the pin state, gating
the `waiting` category only. It is the sole option the data does not refute, precisely
because it does not try to infer intent — the user names the one session they are waiting
on before they walk away.

Cost: it is bookkeeping the user must remember to do *before* leaving, which is exactly
when they will not. That is a real objection and the reason this is second, not first.
Build it only if option 1 turns out to lose something the user misses.

## Prerequisite for any of this: log what was actually sent

`push.py` logs only failures. Reconstructing the 2026-08-02 incident meant inferring sends
from `status_timeline` transitions, because no record exists of what the sender decided —
every number in this document is a simulation over state transitions, not an observation of
notifications. Before changing routing policy again, persist one row per notification
decision: session, category, plan verdict (`send`/`skip`/`defer`/`settled-cancelled`), and
outcome. Then the next question about notification behaviour is answerable from data.

This is worth doing regardless of which option above is chosen, and it is the one item here
with no design risk.

## Relates to

- `../design/features/notifications.md` — the shipped contract this would extend
- `../design/features/device-presence.md` — supplies "is the human at this device"; the
  open question is the orthogonal "does the human care about this *session*"
- `../design/features/status-detection.md` — where the idle transitions come from
