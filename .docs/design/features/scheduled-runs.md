# Scheduled runs

## What it is

A schedule does one of two things in one Project on its own, on a cron expression, on an interval, or once at a time.

- **Start a new session** (`action: spawn`). The Run menu's launch, deferred: the same `SpawnRequest` (project, backend, launch profile, cwd, name, `seed_text`), plus a trigger and an owner.
- **Reopen an existing conversation** (`action: resume`). The History browser's Resume button, deferred: a conversation named by its history run id, plus a trigger and an owner.

Either carries a prompt, and optionally a queue of messages behind it.

A schedule has one of two authors, and the author is the authorization argument.

- **A person's schedule** is a deferred press of a button its author could have pressed themselves, so it inherits their authority.
- **An agent's schedule** (`mux.request_schedule`, "Agent-authored schedules") carries the authority its *target* Project granted agents through `schedule_grant`.
  That is the same grant model an agent's immediate spawn already answers to (`spawn_grant`, `mux-mcp.md`), with the bounds persistence needs added on top, because a schedule keeps firing after the agent that wrote it has gone.

Every guard in "What a fire does" applies to both, and both go through the same spawn, resume and queue paths.

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
`backend`, `profile_id`, `cwd` and `model` are therefore **refused** on a resume rather than ignored, and the editor does not offer them: accepting one silently would make the form promise control it does not have.
`overlap: allow` is refused for the same class of reason - a CLI opens a conversation once - so saying it at write time beats a nightly `conversation_live` skip its author reads as a bug.

## When the conversation is already open

`when_live` is a resume's answer to finding its conversation open in a mux pane at fire time.

| `when_live` | What happens | Run outcome |
|---|---|---|
| `skip` (default for a person's schedule) | The fire is recorded and nothing opens. | `skipped`, reason `conversation_live` |
| `deliver` (default for an agent's) | The prompt, then the follow-ups, are queued into the pane that holds the conversation. | `delivered` |

`deliver` is the resume its author asked for - the same conversation - arriving through the ordinary prompt queue instead of a second pane, so readiness, head-of-line order and the auto-delivery gate decide when it lands.
It is decided before the overlap and concurrency guards, because both exist to stop a schedule *opening* a pane, and delivering opens none.
The rolling-continuation ceiling still applies: delivering into a conversation that is already too full is the accumulation the ceiling exists to stop.
A delivered run names the pane in its run row but never becomes the schedule's `last_session_id`, so a pane somebody else opened is not counted as this schedule's live session.
A conversation held by a process mux does not own (`conversation_held`) is still skipped either way: there is no pane to queue into.
`deliver` is refused on a spawn, which opens a conversation nobody holds.

## End dates

Any schedule may carry `expires_at`.
The sweep disarms a schedule past its end date before it looks for due fires (`disabled_reason = expired`), so it cannot fire in the tick it expired in, and it stays in the list with its history.
A past end date is refused at write time rather than accepted and immediately expired, and re-arming an expired schedule from the tab is refused until the end date moves.
Every recurring agent schedule carries one; a person's has none unless they set it.

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
3. **Check permission at fire time, not at write time**: the install-wide switch, then the Project's `scheduled_runs` opt-in, then - for a schedule an agent armed under its grant - the Project's `schedule_grant` as it stands now. Revoking any of them takes effect immediately.
4. **Daily cap.** An optional per-schedule cap over a rolling 24 hours, counting `spawned` and `delivered` runs.
5. **Deliver into an open conversation**, for a resume whose `when_live` is `deliver` ("When the conversation is already open").
6. **Overlap.** `skip`, the default, refuses to start a second run while the previous one's session is still alive. Nothing ends an agent session automatically, so this is what stops a nightly job becoming a fleet of forgotten panes.
7. **Concurrency.** The install-wide ceiling on concurrently live scheduled sessions.
8. **Act.**
   A spawn goes through `_spawn_from_body`, the identical path the Run menu, the Fleet Queue approval, and the granted agent spawn use.
   A resume goes through `session_resume.resume_run`, the identical path the History browser's Resume button uses, after resolving its target and (for `fork_point`) writing the fork.
   A second spawn path or a second resume path would be a second authority, and the one that drifted would do so silently at 3 a.m.
9. **Queue the messages** through `PromptQueueService.enqueue`. Bind-on-first-run keys each message to the session *and* its first agent run, so one can never land in a conversation it was not written for, and a per-message delay becomes a `not_before` constraint the queue enforces (`prompt-queue.md`, `auto-delivery.md`). Never a timer owned by this feature. Who the message is *from* decides how it may be armed ("What an agent's schedule says").
10. **Record the outcome** (`spawned`, `delivered`, `skipped`, `failed`, `missed`) with its reason, and notify on a failure.

A failed spawn is contained: it is recorded, alerted, and the loop continues.
The session it could not start is the only thing lost.

### Where a resume's prompt goes, and why not argv

A spawn's prompt rides argv as the seed (`stage_seed_argv`).
A resume's does not: the resumed pane's argv is already `--resume <id>`, and whether a positional prompt may follow that is per-harness luck rather than a contract.
It is enqueued as the first queue item instead, ahead of the follow-ups.
For a person's schedule the consequence is deliberate and is stated in the editor: a `rule`-authored message is never self-arming, so it waits in the Queue tab for a person to send it.
Granting a person's rule-authored message automatic delivery is a decision this feature does not take; an agent's schedule is armed by a different, narrower argument ("What an agent's schedule says").

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

## Agent-authored schedules

An agent writes a schedule through `mux.request_schedule`, lists them through `mux.list_schedules`, and withdraws its own through `mux.cancel_schedule` (`mux-mcp.md`).
Before these existed an agent with a shell could already POST the Project route directly, and the row it wrote was indistinguishable from a person's and bounded by nothing an agent answers to.
The tools close that by giving the capability an attributed, bounded path, and the agent skill steers agents to them.

### The verdict

The target Project's `schedule_grant` decides, resolved through the four authority layers (`agent_authority.py`), and `schedules.agent_schedule_verdict` applies it:

| `schedule_grant` | A one-off | A recurring schedule |
|---|---|---|
| `draft` | waits for a person | waits for a person |
| `limited` (built-in default) | armed directly | armed directly at most `agent_schedule_daily_fire_limit` fires in any 24 hours, otherwise waits |
| `granted` | armed directly | armed directly |

A Project that has not opted into `scheduled_runs` collapses to `draft`, the way an agent spawn collapses when `session_control` is off: the request still has somewhere to go, and the approval card offers the opt-in.
An unknown level fails closed to the same place.

Frequency is measured, not estimated.
`schedules.fires_per_day` walks the real occurrences the scheduler would produce over the next eight days through the same wall-clock arithmetic, and takes the most inside any rolling 24 hours, so a weekday-only expression is judged on a weekday and a DST transition is counted the way it will fire.
Counting stops past 96, so anything busier than every fifteen minutes is "more than 96" at bounded cost.

### The bounds persistence needs

The per-origin spawn budget is counted while its author is alive, and a schedule is the thing that outlives its author, so these exist in addition to the grant:

| Config | Default | What it bounds |
|---|---|---|
| `agent_schedule_max_days` | `30` | A recurring agent schedule expires after at most this many days (sooner if it asks); a one-off may be parked at most this far ahead. |
| `agent_schedules_per_project` | `10` | Agent schedules standing in one Project, armed or pending. `0` stops agents scheduling. |
| `agent_schedule_hourly_budget` | `6` | Requests one agent session may make per hour, in any state, counted from the table so a restart does not refill it. |
| `agent_schedule_daily_fire_limit` | `2` | The `limited` level's frequency line. |

An identical request from the same author returns the schedule it already made instead of arming a second copy, so an agent that retries a call it could not see the answer to is safe.
`dry_run` answers with the verdict, the measured frequency and the next fire times and writes nothing, including the refusal a real call would meet.

### Pending requests are the rows themselves

A request that waits is the schedule row with `approval_state = pending`, disarmed with `disabled_reason = awaiting_approval`; there is no second copy of the definition for an approval to drift from.
The Fleet Queue lists it beside drafted spawns with the measured frequency, the next fire times from the daemon, the end date and the reason it needed a person, and the Schedule tab shows the same row with Approve and Dismiss.
`POST /api/schedules/{id}/decide` is one conditional update, so the desktop and a phone cannot both win; arming a pending row from the tab's pause switch is the same approval.
Approval arms from now - a one-off whose moment passed while it waited fires on the next sweep - and records who decided.
Dismissal keeps the row, disarmed, so the record of what an agent asked for survives.
Either way the author is told in its own conversation, if that conversation is still the one that asked, through a `rule` message armed by naming the schedule in `solicited_by`.

### Re-judged at every fire

A schedule an agent armed under its grant (`approval_state = granted`) is judged again at every timer fire against the level as it stands.
Lowering the Project to `draft`, or lowering the install's daily limit below its frequency, turns it back into a pending request in the Fleet Queue instead of letting it keep firing on an authority nobody would grant it today.
A schedule a person approved (`approved`) carries that person's authority and is never re-judged, and a person's edit of an agent-armed schedule makes it `approved` for the same reason; editing a pending request does not approve it.
A manual Run now is a person's own press and is never withheld.

### What an agent's schedule says

Agent-authored text is attributed rather than anonymous.
A spawn's seed and a resume's first message are headed by a daemon-written line naming the schedule, its id, the authoring agent and when it was set up - the disclosure `notify`'s envelope makes, at its fullest, because the schedule fires long after anyone could ask the author what it meant.

How the message is armed follows the receiver-side consent rule (`agent-messaging.md`), never the sender's claim:

- A resume of **the author's own conversation** (`target_run_id` is the author's run - the "pick this up later" case) is the answer to a request that conversation made, so it is staged armed by naming the schedule in `solicited_by`, exactly like a settle-watch notice. It is sent without a person wherever the conversation's auto-delivery grant and the install brakes allow, and the request result says so when an install brake would hold it.
- A resume of **another conversation** is the authoring agent's message (`sender_kind = agent`), armed only under that conversation's own `accept_agent_messages` grant, like `notify`.
- A **person's** schedule is unchanged: `rule` messages that never arm on their own.

### What an agent may not do

- Withdraw or edit a schedule a person wrote, or another agent wrote. `cancel_schedule` disarms only a row attributed to the caller's session or to the conversation it is running.
- Choose a fixed fork point. Agents resume a conversation as it stands (`run`) or wherever it has got to (`latest_of_session`); `fork_point` needs a message id and is set from the UI.
- Reopen a conversation under another Project's root. A resume runs in the conversation's own Project, and naming another is refused.
- Write anything while the install switch is off. That is the emergency stop, so it refuses rather than filing requests nobody can act on.

## What it deliberately does not do

- It does not end the session it started or reopened. Scheduled agent runs are interactive panes, because `completion_mode: one_shot` is shell-only, so the concurrency ceiling is the bound rather than an automatic teardown.
- It does not let an agent schedule outside its target Project's grant. A model may write a schedule only through `mux.request_schedule`, under `schedule_grant` and the bounds in "Agent-authored schedules"; there is no other agent path to a schedule that the daemon recognises.
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
| `agent_schedule_hourly_budget` | `6` | Agent schedule requests per session per hour. |
| `agent_schedule_daily_fire_limit` | `2` | Fires a day a recurring agent schedule may have and still be armed at `limited`. |
| `agent_schedules_per_project` | `10` | Agent schedules standing in one Project. |
| `agent_schedule_max_days` | `30` | Longest an agent schedule may run or be parked. |

The four agent bounds are edited in Settings → Prompt queue → Agent actuation beside the spawn budget; `schedule_grant` is a row in the Automation policy's agent authority table.

## Key files

- `src/swe_mux/schedules.py` - triggers, wall-clock arithmetic, the action/target model, validation, `fires_per_day` and `agent_schedule_verdict`.
- `src/swe_mux/schedule_store.py` - definitions, authorship, approval state, and run history, machine-local.
- `src/swe_mux/schedule_authoring.py` - the one write path: a person's create, an agent's request with every bound, approval, dismissal, cancellation, and the agent listing.
- `src/swe_mux/scheduler.py` - the sweep, expiry, the guards, the agent authority re-check, the fire, the resume and deliver paths, and how a message is attributed and armed.
- `src/swe_mux/session_resume.py` - the single resume authority, shared with the History route.
- `src/swe_mux/transcript_view.py` - `conversation_cut_points` and `resolve_cut_offset`, shared with the branch picker.
- `src/swe_mux/server.py` - the routes, the live `blocked` answer, the resolved `target`, and `GET /api/history/{id}/branch-points`.
- `src/swe_mux/routes/schedules.py` - the routes, including `POST /api/schedules/{id}/decide`; `src/swe_mux/routes/queue.py` adds `schedule_requests` to the Fleet Queue view.
- `src/swe_mux/mcp.py`, `src/swe_mux/mcp_contract.py` - `request_schedule`, `cancel_schedule`, `list_schedules`.
- `frontend/src/ScheduleTab.tsx`, `frontend/src/schedules.ts` - the drawer tab and its pure helpers.
- `frontend/src/FleetQueue.tsx` - the agent schedule request cards.
- `frontend/src/HistoryBrowser.tsx`, `frontend/src/App.tsx` - the two places a resume is seeded from.

## Relates to

- `automation-enablement.md` - the `scheduled_runs` per-Project opt-in.
- `mux-mcp.md` - the three schedule tools and the authority model they share with `request_spawn`.
- `agent-messaging.md` - the receiver-side consent rule that decides how an agent schedule's message is armed.
- `prompt-queue.md`, `auto-delivery.md` - how a resume's prompt and the follow-up messages are delivered.
- `history.md` - the run rows a resume targets, and the Resume button that shares its authority.
- `transcript-branches.md` - what a fork may legally be cut at.
- `launch-profiles.md` - where the model flag for a scheduled *spawn* lives; a resume has none.
- `project-actions.md` - the machine-local trust boundary this storage decision follows.
- `ui.md` - the drawer tab registry and rail.
