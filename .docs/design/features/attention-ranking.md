# Attention ranking

## What it is

The layer that decides which of many concurrent sessions actually needs the human, and when.
Every earlier control-plane layer *writes* findings; this one *routes* them.
Roadmap Phase 6.5, control-plane build-order steps 6 and 7 (`../../development/CONTROL_PLANE_ROADMAP.md` §6.7, §6.8, §14).

It produces no session writes.
Routing a finding to a channel, and holding a channel back, is the entire output.

## The unit of interruption is the incident

Several detectors reporting one underlying event share an `incident_key` and therefore one budget slot.
The key is `(incident class, anchor, time bucket)`, where the anchor is the agent run when there is one and the session otherwise, and the bucket is `attention_incident_window_seconds` wide.

- Merging folds the new detector's kind and evidence into the existing incident, raises the score, and increments `contributions`.
- A merge deliberately does **not** re-route the incident.
  Channel and budget were decided when it first appeared; re-deciding per contributing finding is how one stuck run becomes four interruptions.
- A recurrence in a later window is a new incident, so a resolved problem returning is news.

Findings are classified onto an incident class before ranking:

| Incident class | Fed by | Cost to resolve |
|---|---|---|
| `stuck` | `loop-detected`, `stalled`, `runaway`, `phase-stall` | expensive-blocking (`phase-stall` cheap-blocking) |
| `unverified` | `declared-vs-verified`, `claim_unverified` | expensive-blocking |
| `context` | `context_pressure` | expensive-blocking |
| `blocked_on_human` | `unattended_attention` | cheap-blocking |
| `environment` | `port_collision` | cheap-blocking |
| `docs`, `provenance`, `knowledge`, `phase` | `doc-debt`, `provenance`, `prior-resolution`, `phase-pivot` | non-blocking |

`phase-stall` and `phase-pivot` are the Phase 7.7 phase-transition signals (`automation.md`,
`scan-timeline.md`): a semantic flat-novelty stall inside one work phase, and a genuine work_phase
pivot. `phase-stall` is cheap-blocking (it complements the deterministic `stalled` detector without
spending an interrupt on its own); `phase-pivot` is informational and never interrupts.

A kind absent from the table is unclassified and routes to the digest.
A detector added later must never be able to interrupt by default.

## Four channels, split by cost to resolve

| Channel | Meaning | Spends budget |
|---|---|---|
| `interrupt_now` | Worsening, actionable, and confident enough to interrupt | yes |
| `next_breakpoint` | Waits for the human's own pause; batches and drains | no |
| `inbox` | Schedulable, read when chosen | no |
| `digest` | A record, no action implied | no |

Merging cheap-blocking work (answer a permission prompt, seconds) with expensive-blocking work (the plan is wrong, an hour) is the clinical-alarm failure mode, so the channels are never combined in the daemon or in the UI.
Cheap-blocking work never spends interrupt budget however confident it is and however many are waiting.

`interrupt_now` requires all of: a worsening condition, a concrete action, confidence at or above 0.8, and budget remaining.
Anything short of that is demoted with an explicit reason.

## The budget is a hard bound, and demotion is never deletion

`attention_daily_interrupt_budget` (default 4) bounds interruptions per calendar day, counted per incident.
`attention_hourly_interrupt_cap` (default 2) is only a burst limiter beneath it: an hourly cap on its own silently authorizes 8 to 16 a day, which is already fatigue territory.

An incident that cannot take a slot is still recorded, still ranked, and still readable, carrying a `suppressed_reason`:

| Reason | Meaning |
|---|---|
| `budget_exhausted` | The day's interrupts are spent |
| `low_confidence` | Below the interrupt threshold |
| `superseded_run` | The conversation was replaced |
| `rule:<class>` | A user-accepted demotion rule |

Suppressed counts are always shown.
An item the ranker held back that the user cannot see is indistinguishable from a detector that silently broke.

## Rank against the live run only

A finding anchored to a conversation the session has rolled past (`backends.md`) describes work the agent can no longer act on, and interrupting for it spends a small budget on something the user already resolved by clearing.

- Supersession is checked when the finding arrives and again when the inbox is read, so an item ranked before a `/clear` is demoted after it.
- A demoted item keeps its `agent_run_id` and stays inspectable in the digest.
- Nothing is deleted, and no consumer merges two runs into one item.

## Breakpoint delivery

The strongest moment to hand someone a queued interruption is their own breakpoint, not the agent's.
swe-mux owns the human's terminals, so a shell reporting that its command finished is that moment.

- Interactive PowerShell profiles wrap `prompt` and emit OSC 133 `D` (command finished, with exit status) and `A` (prompt start), controlled by `attention_breakpoint_markers` and reported as the `breakpoint-osc133` profile capability.
- Only a shell pane counts. An agent pane's "finished" is the agent's breakpoint.
- Reaching a breakpoint drains `next_breakpoint` into the inbox as delivered. It never writes to a session.

## Fan-out and resumption lag

The headline answers "how many agents are you sustainable at", from Olsen and Goodrich: neglect time divided by interaction time, plus one.
Both halves are measured rather than assumed, from attach and input telemetry that only the layer owning the human's terminals can see.

- Interaction time is the duration of a burst of human input on one session; two events more than 60 s apart are separate bursts.
- Neglect time is the gap between bursts on the same session.
- Below `MIN_FANOUT_SAMPLES` the estimate reports `insufficient_samples` and no number. A fabricated fan-out is worse than none.
- Resumption lag, not throughput, is the cost of an interruption: interrupted work completes faster and pays in the return cost. It is sampled from the time the human left a session to the time they returned to it after an interrupt was delivered elsewhere.
- Samples persist to a checkpoint at shutdown so a daemon restart does not reset the estimate to unknown.

## Learning from behaviour, never from stated preference

Act and dismiss decisions are recorded per incident class and channel.
A class dismissed at least 5 times with a dismiss rate at or above 0.8 induces a demotion rule.

- A rule is **proposed**, with its evidence, and applies only once the user accepts it.
- An accepted rule expires after 14 days and returns as proposed. That expiry is the periodic forced judgment call: a standing suppression nobody re-confirms is how a surface goes quietly blind.
- The learning objective is avoided loss per unit attention, not engagement.

## Model narration

The one part of this feature that spends tokens, off by default (`attention_narration_enabled`), and gated per project by the `model_narration` automation.

- Presentation over evidence: a ranked item is complete and actionable before narration runs and stays complete when it fails.
- Stateless and read-only: one call sees one normalized slice of one incident.
- A slice never spans two agent runs. A "why" assembled across a `/clear` is a fabricated cause.
- Every failure path records a typed status (`disabled`, `no_model`, `budget`, `failed`, `empty`) and keeps the deterministic summary. Failure never degrades to silence and never to a guess.
- Metered on the shared ledger under `builtin:attention-narration` with its own daily budget (`attention_narration_daily_budget`), which takes tokens, dollars, or first-hit and defaults to the `usd` mode it enforced before the shared shape existed (`budgets.md`).
- Bounded per call by `attention_narration_max_output_tokens` (default 200): a narration is a sentence or two, so this is a ceiling on one line rather than on a document. It is edited with the switch, the model, and the budget under Automation → Global policy → Attention.

## Delivery boundary

Ranked items surface in-app only.
This feature holds no push route, no device routing, and no sound, and the inbox states that boundary in its response (`delivery: {push: false, surface: "in_app"}`) rather than leaving it implied.
The settle-gated `waiting` web-push alert (`notifications.md`) is a separate, older path and is unchanged by any setting here.

## Enablement

Per-project opt-in through the enablement DAG (`automation-enablement.md`):

- `attention_ranking` requires `tier0`, `scan_timeline`, `loop_detection`, `declared_vs_verified`, and `doc_debt`. Ranking has nothing to rank without the detectors and the timeline that feed it.
- `absence_report` requires `scan_timeline`.
- `model_narration` requires `attention_ranking`. With ranking off there is nothing to narrate and no way to spend tokens on one.

A session whose project has not enabled `attention_ranking` produces no items at all.

## API surface

| Route | Purpose |
|---|---|
| `GET /api/attention/inbox` | Ranked items by channel, budget, fan-out, resumption lag, suppressed counts, mined rules |
| `POST /api/attention/items/{item_id}/feedback` | Record `acted` or `dismissed`; the only learning input |
| `POST /api/attention/rules` | Accept or reject a mined demotion rule |
| `GET /api/attention/absence` | The away report plus ranked items, rollover boundaries, and suppressed counts |

Health under `attention_ranking` and `attention_narration` in `GET /api/diagnostics/background`.
Ranking emits `attention_item_ranked` and `attention_breakpoint` to the event log; shells emit `shell_command_finished`.

## Key files

- Ranking, channels, budget, telemetry, digest: `src/swe_mux/attention_ranking.py`
- Narration: `src/swe_mux/attention_narration.py`
- Storage (`attention_items`, `attention_feedback`): `src/swe_mux/automation_store.py`
- OSC 133 parsing: `src/swe_mux/runtime_cwd.py`; marker emission: `src/swe_mux/profiles.py`
- Breakpoint reporting on the PTY path: `src/swe_mux/session.py`
- Routes and wiring: `src/swe_mux/server.py`
- Surface: `frontend/src/AttentionInbox.tsx`, `frontend/src/attention.ts`, `frontend/src/Notifications.tsx`

## Relates to

- `deterministic-consumers.md` — the model-free detectors whose findings this ranks.
- `fleet-intelligence.md` — the passive cross-session evidence that also feeds it.
- `notifications.md` — the separate push path, deliberately untouched here.
- `automation-enablement.md` — the per-project opt-in DAG.
- `backends.md` — the conversation rollover that defines a superseded run.
