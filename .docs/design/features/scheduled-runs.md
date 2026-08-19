# Scheduled runs

## What it is

A schedule does one of two things in one Project on its own, on a cron expression, on an interval, or once at a time.

- **Start a new session** (`action: spawn`). The Run menu's launch, deferred: the same `SpawnRequest` (project, backend, launch profile, cwd, name, `seed_text`), plus a trigger and an owner.
- **Reopen an existing conversation** (`action: resume`). The History browser's Resume button, deferred: a conversation named by its history run id, plus a trigger and an owner.

Either carries a prompt, and optionally a queue of messages behind it.

That framing is the authorization argument.
A schedule is a **user-authored deferred press of a button the author could have pressed themselves**, so it inherits their authority.
It is not the decision-gated "model-authored action selection, autonomous worker spawning" in `../../development/ROADMAP.md`: no model chooses to create, edit, or fire one.
What it did owe that gate is the rest of the checklist, and those are the guards in this document.

## Where it lives

- **The drawer's Schedule tab** (`frontend/src/ScheduleTab.tsx`) owns authoring, the inventory, pause/resume, Run now, and the run history.
  A *spawn* is authored from a blank form there.
  A *resume* never is: it is seeded from the conversation it reopens, by the History row's "Resume later…" or by a live pane's "Resume selected agent later…", because the one thing the tab cannot offer is a way to find a conversation, and a form with an empty run-id box would be a worse conversation picker than the two that already exist.
  Everything else about a seeded resume - when, how its target may move, what to say on arrival - belongs to the tab.
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

## What a resume points at

A resume names its conversation by **history run id**, never by session id.
A session is exactly the thing that drifts: a pane rolls its conversation (`/clear`), is resumed into a different pane, or ends.
Agent history rows are not time-pruned, a resume that continues a conversation inherits its row rather than opening a second one over one file, and a rollover retires the run instead of silently redirecting a schedule at unrelated work.

`target_kind` answers the question a session id would have answered wrongly, and there are three answers because "resume this later" genuinely has three.
Each option states its own cost in the editor, because the cost is the whole content of the choice.

| `target_kind` | What fires | What it trades |
|---|---|---|
| `run` | The pinned conversation, exactly as it stands | Nothing drifts. The one-off. |
| `latest_of_session` | Wherever that work has got to | Continues the last run, and accumulates. Carries a ceiling. |
| `fork_point` | A fresh fork of a pinned message, resumed | Every run starts from identical context. Claude only. |

- **`latest_of_session`** is resolved at fire time by `session_resume.resolve_latest_run`, which follows exactly two kinds of continuation and no others: a **rollover** within one pane (`note_id` chains the runs, ordered by `agent_run_seq`) and a **resume** lineage edge into a new pane.
  A `branch`, `review`, or `handoff` edge is different work reading or forking this conversation, and following one would point an unattended schedule at something its author never chose.
  The walk is bounded and cycle-guarded; a target whose row has been deleted resolves to nothing.
- **`fork_point`** stores a message id and a side (`before`/`after`), never a byte offset, so a conversation that has moved past the pinned message is refused by name (`branch_point_unknown`) instead of cut somewhere plausible.
  The offset is resolved at each fire by `transcript_view.resolve_cut_offset`, the same decision the interactive branch picker makes: a schedule that fired on a rule the picker would have refused is an unattended session opened on a conversation the provider rejects.
  The source transcript is opened read-only and is never written, which is what makes the schedule repeatable.
  Each fire writes a **new** conversation file and leaves it: a fork is a real conversation with its own history row, so nothing deletes one automatically, exactly as when Branch is pressed by hand.
  A nightly fork schedule therefore accumulates transcripts at one per fire; the concurrency ceiling bounds the live panes, not the files.

### Why a rolling continuation carries a ceiling

Each resume replays the whole accumulated conversation as fresh input, and the conversation only grows.
Past a point the harness compacts its own early context into a summary, so "always a continuation" has quietly become a summary of a summary - degrading rather than failing, which is the harder thing to notice weeks later.
`context_ceiling_pct` (default 0.7, 0 disables) is checked at fire time against the row's own `final_context_pct`, so it costs a dictionary lookup and cannot disagree with what the session sidebar showed.
A row with no measurement is not evidence of a full conversation and is allowed through.
The ceiling belongs to `latest_of_session` alone: the other two kinds either pin a run or start from a fixed prefix, and a switch there would read as protection and do nothing.

## What a resume may not say

The conversation's history row and its adapter already fix the harness, the argv (`--resume <id>`), and the working directory (the Project root the conversation resolves from).
`backend`, `profile_id`, and `cwd` are therefore **refused** on a resume rather than ignored, and the editor does not offer them: accepting one silently would make the form promise control it does not have.
`overlap: allow` is refused for the same class of reason - a CLI opens a conversation once - so saying it at write time beats a nightly `conversation_live` skip its author reads as a bug.

A `once` resume is held inside a 21-day horizon rather than the one-year horizon a spawn gets.
The reason is not caution: the agent CLIs prune their own transcripts on their own timers (Claude's `cleanupPeriodDays` defaults to 30 days of inactivity), mux neither owns that file nor is consulted, and a resume parked past that window is a schedule whose most likely outcome is `transcript_unavailable`.
A recurring resume needs no such bound, because every fire touches the transcript.

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
6. **Act.**
   A spawn goes through `_spawn_from_body`, the identical path the Run menu, the Fleet Queue approval, and the granted agent spawn use.
   A resume goes through `session_resume.resume_run`, the identical path the History browser's Resume button uses, after resolving its target and (for `fork_point`) writing the fork.
   A second spawn path or a second resume path would be a second authority, and the one that drifted would do so silently at 3 a.m.
7. **Queue the messages** through `PromptQueueService.enqueue` with `sender_kind: "rule"`. Bind-on-first-run keys each message to the session *and* its first agent run, so one can never land in a conversation it was not written for, and a per-message delay becomes a `not_before` constraint the queue enforces (`prompt-queue.md`, `auto-delivery.md`). Never a timer owned by this feature.
8. **Record the outcome** (`spawned`, `skipped`, `failed`, `missed`) with its reason, and notify on a failure.

A failed spawn is contained: it is recorded, alerted, and the loop continues.
The session it could not start is the only thing lost.

### Where a resume's prompt goes, and why not argv

A spawn's prompt rides argv as the seed (`stage_seed_argv`).
A resume's does not: the resumed pane's argv is already `--resume <id>`, and whether a positional prompt may follow that is per-harness luck rather than a contract.
It is enqueued as the first queue item instead, ahead of the follow-ups.
The consequence is deliberate and is stated in the editor: a `rule`-authored message is never self-arming, so it is delivered automatically only where that conversation has an auto-delivery grant, and otherwise waits in the Queue tab.
Granting a rule-authored message automatic delivery is the decision-gated thing this feature does not do.

### The refusals only a resume has

Every one of these is normal rather than exceptional for a job armed days ahead, and each is recorded as a run row with a reason.

| Code | Outcome | Why it happens |
|---|---|---|
| `conversation_live` | `skipped`, no alert | A mux pane is in that conversation. Two sessions on one conversation is the cross-attribution the identity invariant forbids. |
| `conversation_held` | `skipped`, no alert | A process mux does not own holds it - typically a Claude background agent, which outlives the pane that parked it. Invisible to the pane check. |
| `context_ceiling` | `skipped`, no alert | The rolling target is already fuller than its ceiling. |
| `target_missing` | `skipped`, **schedule disabled** | The conversation has no History row any more, so it can never open. |
| `transcript_unavailable` | `failed`, alerted | The harness pruned the conversation. The likeliest permanent failure. |
| `cwd_missing` | `failed`, alerted | The directory the conversation ran in is gone - a removed worktree, usually. |
| `resume_failed` | `failed`, alerted | The pane spawned and died inside its settle window; the harness's own dying words are the reason. |

The two held cases are `skipped` and silent on purpose.
A schedule armed against a conversation its author also uses by hand meets them routinely, and alerting on each would teach its reader to ignore the alerts that matter.

A scheduled fork records a `branch` lineage edge, not a `resume` one.
`resolve_latest_run` follows `resume` edges, so calling a fork a resume would make every later fire of a rolling schedule chase last night's fork.

## What it deliberately does not do

- It does not end the session it started or reopened. Scheduled agent runs are interactive panes, because `completion_mode: one_shot` is shell-only, so the concurrency ceiling is the bound rather than an automatic teardown.
- It does not decide what to run. Every definition is human-authored; there is no path from a model to a schedule.
- It does not compute a fire time in the browser. The editor previews through `POST /api/schedules/preview` so that what a user is shown before saving comes from the code that will fire it.
- It does not run a repository-provided command. A schedule seeds an agent prompt; executing repository content stays behind Project Action trust.
- It does not fork a conversation to get around a refusal. A conversation somebody else is holding is skipped, because forking would silently produce a *second* conversation where the author asked to return to one. Forking is a target kind the author chooses, never a fallback.
- It does not arm its own messages. A `rule`-authored prompt obeys the auto-delivery gate like every other one.

## Configuration

| Setting | Default | What it bounds |
|---|---|---|
| `scheduled_runs_enabled` | `true` | The emergency stop. Off means nothing fires anywhere. |
| `scheduled_runs_max_concurrent` | `3` | Live schedule-started sessions at once. |
| `scheduled_runs_poll_seconds` | `5.0` | How promptly a due minute is noticed. |
| `scheduled_run_retention_days` | `60` | How long run history is kept. |

## Key files

- `src/swe_mux/schedules.py` - triggers, wall-clock arithmetic, the action/target model, validation.
- `src/swe_mux/schedule_store.py` - definitions and run history, machine-local.
- `src/swe_mux/scheduler.py` - the sweep, the guards, the fire, the resume path.
- `src/swe_mux/session_resume.py` - the single resume authority, shared with the History route.
- `src/swe_mux/transcript_view.py` - `conversation_cut_points` and `resolve_cut_offset`, shared with the branch picker.
- `src/swe_mux/server.py` - the routes, the live `blocked` answer, the resolved `target`, and `GET /api/history/{id}/branch-points`.
- `frontend/src/ScheduleTab.tsx`, `frontend/src/schedules.ts` - the drawer tab and its pure helpers.
- `frontend/src/HistoryBrowser.tsx`, `frontend/src/App.tsx` - the two places a resume is seeded from.

## Relates to

- `automation-enablement.md` - the `scheduled_runs` per-Project opt-in.
- `prompt-queue.md`, `auto-delivery.md` - how a resume's prompt and the follow-up messages are delivered.
- `history.md` - the run rows a resume targets, and the Resume button that shares its authority.
- `transcript-branches.md` - what a fork may legally be cut at.
- `launch-profiles.md` - where the model flag for a scheduled *spawn* lives; a resume has none.
- `project-actions.md` - the machine-local trust boundary this storage decision follows.
- `ui.md` - the drawer tab registry and rail.
