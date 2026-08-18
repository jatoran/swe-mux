# Scheduled runs

## What it is

A schedule starts an agent session in one Project on its own: on a cron expression, on an interval, or once at a time.
It carries the seed prompt, and optionally a queue of messages to send after it.
It is the Run menu's launch, deferred: the same `SpawnRequest` (project, backend, launch profile, cwd, name, `seed_text`), plus a trigger and an owner.

That framing is the authorization argument.
A schedule is a **user-authored deferred spawn**, so it inherits the authority of the human who wrote it, exactly as if they had pressed Run later themselves.
It is not the decision-gated "model-authored action selection, autonomous worker spawning" in `../../development/ROADMAP.md`: no model chooses to create, edit, or fire one.
What it did owe that gate is the rest of the checklist, and those are the guards below.

## Where it lives

- **The drawer's Schedule tab** (`frontend/src/ScheduleTab.tsx`) owns authoring, the inventory, pause/resume, Run now, and the run history.
  It sits immediately after Processes and closes the Project-scoped block, because the two answer the same question at different times: Processes is what this Project's sessions are running now, Schedule is what it will start later.
  Like Processes it carries its own Project/all-Projects scope rather than a companion modal, because "what fires tonight" spans Projects even though every schedule belongs to exactly one.
- **The Project opt-in** is the `scheduled_runs` automation in the Projects manager (`automation-enablement.md`).
- **The install-wide limits** are in Settings, Automation: the master switch, the concurrency ceiling, the sweep cadence, and run-history retention.
  These are global for the same reason spend limits are - what a scheduled fleet may do to this computer is not a per-repository decision.
- **Failures** reach the drawer's Alerts tab through the ordinary automation notification path, so there is no second alerting surface.

## Where a schedule is stored, and why not in the repository

Definitions are machine-local rows in the daemon's database (`schedules`, `schedule_runs` in `mux.db`), never in the Project's committed `.swe-mux/config.toml`.
A schedule that travelled with the repository would arm itself in every clone and every worktree the moment someone opened it.
This is the boundary Project Action trust already draws by keeping approval in the data directory and never in portable repository state (`project-actions.md`).

The **opt-in** is portable and is inert on its own: a clone inherits permission to run schedules and has none, which is the correct pair.

## Triggers and wall-clock time

`schedules.py` owns the arithmetic and nothing else - no storage, no spawning, no I/O.

- `cron`: five fields (`minute hour day-of-month month day-of-week`), with `*`, `a`, `a-b`, `*/n`, `a-b/n`, comma lists, and three-letter month/weekday names.
  Day-of-month and day-of-week follow the Vixie rule: when both are restricted, the day matches if either does.
  There is deliberately no seconds field and no `@daily` macro: a second axis buys a surface where a typo costs an unattended agent run every second.
  The editor puts a preset dropdown beside the field rather than instead of it: choosing one writes the expression into the input, so the next edit is to a working expression instead of to a blank box, and the field stays the source of truth.
  The presets are matched back from the expression rather than remembered from the click, so an edited one reads as `Custom` and an edit that lands back on a preset is recognised again.
  Between them they demonstrate every part of the grammar (fixed values, weekday names, ranges, lists, steps, and the day-of-month field), and the hint under the row is that preset's one-line explanation of the piece it uses.
  One of them exists to say what cron *cannot* do: there is no "every other Wednesday", because cron counts days and months and never weeks, so the fortnightly ask is answered by the 1st-and-15th preset or by an interval trigger rather than by an expression that quietly fires weekly.
- `interval`: every N seconds, from 5 minutes to 90 days, anchored on the previous fire.
  A new interval schedule waits a full interval rather than firing on save, because pressing Save is not a request to start an agent this second.
- `once`: one absolute instant, within a one-year horizon. It disables itself after firing.

A cron schedule means a **local wall-clock** time, which is not a fixed number of seconds from the last one.
Every candidate is therefore built as local wall time and converted to an instant at the last moment, through one small abstraction with two implementations: a named IANA zone, or this host's own local time.
Host-local is the default and is the only correct one on a Windows machine, where the platform has no IANA name to hand and any fixed-offset stand-in would be wrong for half the year.
Named zones need the `tzdata` package on Windows, which is why it is a dependency: without it every named timezone fails on the primary platform, and the failure reads as a user error ("unknown timezone") rather than a missing database.

The two daylight-saving edges are decided rather than accidental, and both are pinned by tests:

- **Spring forward**, where the local time does not exist. The candidate resolves to the equivalent instant just after the jump, so a 02:30 daily job runs once that day instead of being silently skipped.
- **Fall back**, where the local time happens twice. The first occurrence is authoritative (`fold=0`), so the job fires once rather than twice; the second pass is not a later instant than the one already recorded.

A 09:00 daily job is 09:00 local on both sides of a transition, which is 23 or 25 real hours from its neighbour.

## Missed windows

This daemon lives in a desktop app that sleeps, restarts, and gets redeployed, so "the machine was off at 03:00" is the normal case rather than an incident.

A fire more than 15 minutes late (`MISSED_GRACE_SECONDS`) is either replayed once, when the schedule sets `catch_up`, or recorded with outcome `missed`.
It is never replayed once per missed occurrence: the window advances past now first, so a laptop shut for a week produces one row, not a backlog of sessions.
Nothing is silently dropped either way - a missed window is a visible run row with a reason.

Daemon start repairs rather than recomputes.
A cron schedule's next fire is derived again, because a timezone database or a definition can change under the cached value; a schedule with no stored fire is armed, because an armed schedule that can never fire is the failure this feature must not have.
An interval's stored fire is left alone: recomputing it would restart the interval on every daemon reload, and a six-hourly job on a desktop app that reloads several times an hour would then never run.

## What a fire does, and every guard around it

The sweep runs on the shared background-task supervisor (`background_tasks.py`), so a fault costs one occurrence rather than the feature.

1. **Advance the window first.** A fire refused for any reason still moves `next_fire_at`, or the sweep spins on the same row for as long as the refusal lasts.
2. **Claim the occurrence.** A run row is inserted under a unique `(schedule_id, fire_key)` index *before* anything is spawned, so a daemon that dies mid-fire cannot spawn the same occurrence again on restart and two sweeps cannot race. A manual run gets its own key and never collides with the timer's claim.
3. **Check permission at fire time, not at write time**: the install-wide switch, then the Project's `scheduled_runs` opt-in. Revoking either takes effect immediately.
4. **Overlap.** `skip`, the default, refuses to start a second run while the previous one's session is still alive. Nothing ends an agent session automatically, so this is what stops a nightly job becoming a fleet of forgotten panes.
5. **Caps.** An optional per-schedule daily cap, and the install-wide ceiling on concurrently live scheduled sessions.
6. **Spawn** through `_spawn_from_body`, the identical path the Run menu, the Fleet Queue approval, and the granted agent spawn use. A second spawn path would be a second authority.
7. **Queue the follow-ups** through `PromptQueueService.enqueue` with `sender_kind: "rule"`. Bind-on-first-run keys each message to the session *and* its first agent run, so one can never land in a conversation it was not written for, and a per-message delay becomes a `not_before` constraint the queue enforces (`prompt-queue.md`, `auto-delivery.md`). Never a timer owned by this feature.
8. **Record the outcome** (`spawned`, `skipped`, `failed`, `missed`) with its reason, and notify on a failure.

A failed spawn is contained: it is recorded, alerted, and the loop continues.
The session it could not start is the only thing lost.

## What it deliberately does not do

- It does not end the session it started. Scheduled agent runs are interactive panes, because `completion_mode: one_shot` is shell-only, so the ceiling above is the bound rather than an automatic teardown.
- It does not decide what to run. Every definition is human-authored; there is no path from a model to a schedule.
- It does not compute a fire time in the browser. The editor previews through `POST /api/schedules/preview` so that what a user is shown before saving comes from the code that will fire it.
- It does not run a repository-provided command. A schedule seeds an agent prompt; executing repository content stays behind Project Action trust.

## Configuration

| Setting | Default | What it bounds |
|---|---|---|
| `scheduled_runs_enabled` | `true` | The emergency stop. Off means nothing fires anywhere. |
| `scheduled_runs_max_concurrent` | `3` | Live schedule-started sessions at once. |
| `scheduled_runs_poll_seconds` | `5.0` | How promptly a due minute is noticed. |
| `scheduled_run_retention_days` | `60` | How long run history is kept. |

## Key files

- `src/swe_mux/schedules.py` - triggers, wall-clock arithmetic, validation.
- `src/swe_mux/schedule_store.py` - definitions and run history, machine-local.
- `src/swe_mux/scheduler.py` - the sweep, the guards, the fire.
- `src/swe_mux/server.py` - the routes and the live `blocked` answer.
- `frontend/src/ScheduleTab.tsx`, `frontend/src/schedules.ts` - the drawer tab and its pure helpers.

## Relates to

- `automation-enablement.md` - the `scheduled_runs` per-Project opt-in.
- `prompt-queue.md`, `auto-delivery.md` - how the follow-up messages are delivered.
- `launch-profiles.md` - where the model flag for a scheduled agent lives.
- `project-actions.md` - the machine-local trust boundary this storage decision follows.
- `ui.md` - the drawer tab registry and rail.
