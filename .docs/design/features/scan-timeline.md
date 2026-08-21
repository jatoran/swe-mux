# Scan timeline

## What it is

The scan timeline is a read-only, run-scoped semantic index over bounded transcript deltas and deterministic Tier 0 facts.
It produces a readable behavioral history without writing to a PTY, changing agent state, or ranking attention.
It is the Tier 1 substrate for dead-end memory and later cross-session semantic consumers.

## Authorization and lifetime

Three independent gates must all be open before a scan can call OpenRouter:

1. The global `scan_timeline_enabled` master switch is on.
   It is in **Settings → Automation**, beside the scan budgets; the Automation dashboard
   shows its state and links there.
2. The Project enables `scan_timeline` and its `raw_store` and `tier0` dependencies.
3. The current `agent_run_id` is enabled from that session's Timeline drawer tab.

The Timeline tab's off state names whichever of the first two gates is closed and links to that
exact switch (`setting-links.md`), rather than describing where it lives.

The Timeline drawer exposes the Project permission directly.
Turning it on also enables the required dependencies and creates a blank `.swe-mux/project-context.md` if needed.
Turning it off disables Scan timeline and consumers that require it.
Changing Project permission never authorizes a current run and never starts a backfill.

The run gate defaults off.
It belongs to one provider conversation, not the persistent terminal session.
`/clear`, `/new`, another conversation rollover, session exit, and session crash disable the old run and never authorize the successor.
A rollover writes a visible boundary record and resets the transcript cursor, continuity window, and novelty comparison.

That boundary is right as a cost decision and, repeated per conversation, is pure friction for a Project that has already decided it wants a timeline.
`scan_timeline_auto_enable` in the Project config answers "yes, always" once.
It only ever creates a grant for a run that has **no row at all**: a run the human switched off, or one an ended session disabled, stays off, because an off switch that re-arms itself is not an off switch.
The snapshot reports `auto_enable` but never applies it - a read that started scanning would make opening the drawer a spending decision - so a brand-new run reads as off until its first trigger arms it.
Turning the Project's permission off clears the flag, so re-permitting later does not silently re-arm every conversation.

## Capture flow

Event triggers are `turn_started`, `turn_ended`, `tool_result`, Git change, context compaction, session exit, and session crash.
A supervised three-minute heartbeat covers long-running work that has no event boundary.
Triggers debounce per session and a per-session lock prevents overlapping scans.

Each request contains only:

- the bounded transcript delta since the prior same-run record;
- the prior six records from the same run;
- bounded Tier 0 fact identifiers and targets;
- the current user-authored Project context Markdown, when non-empty.

The delta is a **forward window**: the oldest unscanned messages, not the newest.
The distinction is the difference between a bounded window and a lossy one.
A newest-first window trims from its front and then advances the transcript cursor past what it trimmed, so a busy stretch is skipped permanently and nothing reports it.
The forward window walks oldest first, so the cursor only ever moves to the end of what was actually scanned, and it reports the count of in-scope messages it did not reach.
A record whose window left a remainder schedules an immediate catch-up scan, bounded per trigger, and carries `coverage.remaining` so a timeline that is behind the transcript says so.
The read budget and the window size are separate: the window is 40 messages and 40 KiB, read out of the whole transcript rather than only its trailing bytes.

A tool call is rendered as its name plus a bounded digest of its arguments.
Tool *results* are not available: the shared transcript parser keeps conversation text and tool inputs and drops results by design.
Arguments are therefore the only evidence of what a call touched, and discarding them left the summariser guessing which file was read or which command ran.

The fixed default model is the OpenRouter latest alias `deepseek/deepseek-v4-flash`.
The call requests strict JSON schema, disables reasoning, and locally validates every semantic field.

Validation **repairs** rather than rejects.
Every field is descriptive or has a defined "we do not know" value, so a response wrong in one field is still most of a timeline entry, and discarding it costs a window of the conversation that nothing revisits.
Unknown behaviour labels are dropped, repeats are removed, off-enum values fall back to `unknown`/`none`, overlong text is truncated, and confidence is clamped.
`maxItems` and `uniqueItems` are the two schema keywords structured-output backends most often ignore, so an unclean value is expected rather than exceptional.
Every coercion is recorded in the record's `repairs` list and shown in the drawer.
Only a response with no usable semantic content at all is refused, because storing that would put a blank row on the timeline and still move the cursor past real transcript.

A refused response is retried exactly once, as is a retryable provider fault.
Every attempt closes its own observer-call row with the provider's usage, model, generation id, finish reason, and a bounded excerpt of what it returned.
Any attempt the provider billed for enters the spend ledger whether or not it produced a record.

### Full-session scan

**Scan full session** is an explicit drawer action that scans uncovered messages from the beginning of the current run to a fixed current watermark.
It parses the authoritative transcript once, removes intervals already represented by stored records, chunks the remaining messages oldest first under the ordinary input limits, and uses only earlier records for continuity and novelty.
Chunking bounds native tool arguments to the same digest the live path renders, and bounds oversized text while recording truncated coverage instead of aborting the job.

**One bad chunk is not the job.**
A chunk that fails validation, or throws, increments `failed_chunks` and the job continues; abandoning the remaining chunks over one bad sample left a permanent hole that only another manual scan could fill.
A chunk skipped because its interval is already covered increments `skipped_chunks` and the job continues.
Only a skip that no later chunk could survive either - a closed gate, an exhausted budget, a degraded parser - stops the job, and then the state is `partial` with that exact reason.
The terminal states are `completed`, `completed_with_gaps` (finished, with failed or skipped chunks), `partial` (stopped early), and `failed` (the job itself threw).

The job takes the per-session lock **per chunk**, not for its whole life, so a multi-minute full scan does not freeze live scanning.
Its state is persisted, not held in daemon memory: a restart used to report `idle` for a job that had actually stopped half way, and any row left at `running` by a dead daemon is closed out as `partial` at startup.
`DELETE .../backfill` stops a running job; the result is `partial` naming the operator, and every record already written stays readable.
Budgets, provider availability, observation health, all three gates, and output validation remain in force.
Backfilled writes never move the live transcript cursor backwards.
Later live events or the heartbeat capture messages appended after the fixed watermark.

## Record contract

Every record carries `session_id`, `agent_run_id`, `t0`, `t1`, lifecycle state, behavior labels, work phase, intent, claim, user ask, blockers, target paths, summary, confidence, coverage, mechanical novelty, model identity, prompt version/hash, and source evidence.
Source evidence names the authoritative run, bounded time span, message timestamps, and transcript input hash.
Expanding source reparses the authoritative current or historical transcript for that run and returns messages inside the record's exact time interval.
No transcript text is copied into the scan database.

Novelty is deterministic lexical Jaccard distance against same-run semantic records in v1.
This is deliberately mechanical and run-local.
Changing the algorithm later does not change the persisted field or its rollover boundary.

### Trigger vocabulary

The trigger stored on a record is a wider set than the event-bus triggers.
`SCAN_TRIGGERS` names what the event bus can raise; the store additionally holds `heartbeat`, `enabled`, `manual`, and `full_session`, which reach a record by another path.
`STORED_TRIGGERS` is the union, and anything classifying records by trigger must read that rather than `SCAN_TRIGGERS`.
Across the live store's 379 records, 84 (22%) carry one of the four non-event triggers, so a classifier written from the event set alone silently mis-buckets every one of them.

Trigger name is not a proxy for window width, either.
Measured mean `messages_seen` per trigger ranges from 1.25 (`turn_started`) to 35.6 (`full_session`), and `heartbeat` (10.2) sits above `turn_ended`'s neighbours rather than below them.

### The run-level fields and their continuity (prompt v4)

`approach_status` and `dead_end` are judgments about the whole run; every other field describes the window.
Until prompt v4 they were the only run-level fields with no run-level memory: the continuity records handed to each scan carried `summary`, `intent`, `claim`, `user_ask`, `blocked_on` and `work_phase`, and never what the observer had previously concluded about the approach.
The observer therefore re-derived "was an approach tried and dropped in this run" from scratch roughly every five messages, having been shown six prior windows that never mentioned its own earlier verdict.

v4 adds `approach_status` and `dead_end` to `CONTINUITY_FIELDS` and instructs the observer to repeat a prior verdict unless the delta shows it changed.
A field a prior record withheld is omitted from the continuity payload rather than sent as null, so absence never reads as "previously decided: nothing".
v3 records keep their own semantics and are not rewritten; a consumer reading `approach_status` across the boundary must tolerate both.

Window width was measured as an alternative explanation and does not hold.
`abandoned` fires at 22.6% on the wide triggers (`full_session`, `turn_ended`, `session_exited`) against 24.9% on narrow ones, so the trigger label does not separate them.
Measured width does correlate (13.9% at `messages_seen >= 8`), but gating the fields on width has a cost the diagnosis did not predict: all five records in the live store that satisfy `abandoned` plus a non-empty `dead_end` came from narrow windows, and several of their texts are correct.
A wide-trigger allowlist would therefore suppress the entire dead-end corpus rather than only its false positives.
Restricting the fields by window is not scheduled; the precondition for reopening it is evidence that v4 continuity did not move the wide-window `abandoned` rate.

## Agent-readable surface (Phase 7.11)

The `scan_timeline` and `scan_search` MCP tools expose this substrate to agents (`mux-mcp.md`).
Two properties are owned here rather than there.

`ScanTimelineService.liveness()` is the single owner of the enablement/liveness block - `scanning`, `last_scan_at`, `skip_reason`, `run_decided`, `run_enabled`, `project_enabled`, `auto_enable`, and the closest-to-binding gate.
The drawer's `snapshot()` and the MCP tool both read it, so the two surfaces cannot disagree about whether a timeline is stopped.
It serves an **ended** session as well: records outlive their session, and reviewing a finished sibling is the read the tool exists for.
An ended session reports `session_live: false`, and its Project-context-derived fields report unknown rather than `false`, because a context that cannot be resolved is not an opt-in that is off.

`AutomationStore.scan_records` filters in SQL, including the semantic fields through `json_extract`.
A bounded page therefore means "rows returned" rather than "rows scanned": a `blocked_only` page filtered in Python after the read would come back short of its limit, and a caller could not tell that from the end of the run.
`since_t1` is exclusive so it composes as a monitoring cursor - feeding back the newest `t1` already seen returns strictly newer records and never repeats the boundary one.
The default ordering stays oldest-first because the derivations in `scan_consumers.py` require it; `newest_first` is what a bounded read asks for.

No scan or backfill trigger is exposed through MCP.
Reads cost nothing; a scan spends the human's gated budget against caps set in Settings → Automation → Scan timeline.

## Budgets and visibility

**Scan timeline is a continuous sampler, and it is budgeted as one.**
It is deliberately exempt from `automation_rule_daily_token_budget`, `automation_rule_daily_budget_usd`, and `automation_rule_hourly_call_cap`.
Those bound an observer that fires once per session, such as the session titler.
Sharing that envelope with an event-triggered, three-minute-heartbeat sampler capped the whole feature at roughly ten scans a day across the entire fleet, and it stopped at 0.2% of the dollar budget that was supposed to be the real ceiling.
The token axis must never be the binding constraint while the dollar axis is untouched.

The caps that do apply are:

- `scan_timeline_daily_budget_usd` ($5.00), the feature's dollar ceiling;
- `scan_timeline_daily_token_budget` (3,000,000), its own daily token budget;
- `scan_timeline_hourly_call_cap` (600), its own burst limiter;
- `scan_timeline_run_token_budget` (500,000), one conversation's share;
- `automation_daily_token_budget` and `automation_daily_budget_usd`, the global emergency ceiling over every automation.

All five are **global**, edited in Settings → Automation → Scan timeline, and apply to every Project.
The model they route to is chosen in Settings → Accounts with the OpenRouter key that unlocks it.
The dollar ceiling used to be a per-Project field in the committed `.swe-mux/config.toml`.
That put the cap most likely to stop scanning inside a file nobody opens, gave every checkout a different value, and meant raising it was a per-Project chore.
It is one setting now; a `scan_timeline_daily_budget_usd` still present in a Project file is read tolerantly, ignored, and dropped on the next write.
The global ceiling must stay above the scan's own daily budget, or it silently becomes the new invisible binding cap.
The dollar budget should stay above what the daily token budget can cost, so the tokens run out first.
Successful calls, provider failures that report billable usage, and locally refused responses all enter the shared spend ledger with Project and run attribution.
An unpriced billable call reserves the conservative preflight estimate so missing provider accounting cannot weaken a budget.
The ledger day is UTC.

The Timeline surface is session-scoped, and everything Project-wide lives in the Project's settings instead: permission, the auto-arm flag, and the Project context Markdown.
Since Phase 7.10 the Timeline is a segment rather than a standalone tab. It now sits in the utility drawer's **Activity** tab (renamed from Insight in the drawer consolidation) beside the deterministic Findings pane and the Change Map; its behaviour is unchanged, and it keeps its own palette command and voice phrase because segments are registered rather than local state.
Hosting those in the drawer meant every session in a Project showed the same three Project controls, competing with the tab's actual job.
Every timeline tab carries a button to that Project's settings, and a tab whose Project has not permitted scanning shows only that fact and the same button.
It lists **every** cap with its current usage, collapsed to one row naming whichever is closest to binding and expandable to the full set, because a drawer that shows only the caps with headroom makes a stopped timeline look healthy, and six budget lines was most of a narrow drawer.
The footer reports whether a scan request is actually out, since "working on it" and "nothing is happening" otherwise look identical while waiting.
The record list opens scrolled to the newest entry and stays pinned there until the reader scrolls up.
When scanning is actually stopped, the drawer states the scanner's own reason; a merely idle run says nothing.
It also shows Project permission and context, current-run permission, and full-session chunk arithmetic on every terminal state, not just while running.
Each record shows the count of deterministic evidence targets and keeps their paths, symbols, and command strings inside a collapsed, scroll-bounded disclosure, plus any repairs applied to the model's output and any messages the window did not reach.
The rehydration rate is a Tier 2 metric with no Tier 2 consumer yet, and the only caller hard-codes `rehydrate=1`, so it is structurally 1.0 and is no longer given a headline slot.
There is no scan button or scan-spend control in the application topbar.

## Behavioral consumers (Phase 7.7)

The scan timeline is the single behavioral-summary substrate, and several consumers are cheap
derivations over its per-record spine rather than new transcript reads.
Each is independently toggleable through the same per-Project enablement DAG as the timeline it
reads (`automation-enablement.md`), obeys "empty beats plausible-but-wrong", and attributes every
derived result to the `agent_run_id` it came from.

Two consumers ride a *freshly saved* live scan record.
When a record is saved on the live path (never a full-session backfill, whose chunks replay out of
order), the scan service calls `on_record_saved` with the session, context, new record, and the
run's prior records.
`BehavioralConsumerService` (`behavioral_consumers.py`) evaluates one shared pivot definition
(`evaluate_pivot`: a novelty spike plus a `work_phase`/`target`/`user_ask` transition, with debounce
and hysteresis) and drives **adaptive titling** (`continuous_title`) and **phase-transition signals**
(`phase_transitions`).
A fault in either is contained by the scan service and never breaks scanning.
Adaptive titling's re-title count is surfaced in the snapshot's `adaptive_title` field, so a
stable-subject run's zero re-titles is a measured number.
Design detail for both lives in `automation.md`.

Three consumers are pull-only reads over stored records:

- **Timeline-based handoff** (`timeline_handoff`) regenerates `GET /api/history/{sid}/handoff`
  phase-structured from the run's scan spine when the Project opts in, falling back to annotation
  summaries otherwise (`history.md`).
- **Catch-me-up digest** (`catch_me_up`) is `GET /api/sessions/{sid}/catch-me-up`: an on-demand
  rollup of one run's phases, claims, and current blocker.
- **Live blockers** (`live_blockers`) is `GET /api/attention/blockers`: a fleet glance aggregating
  each active session's current `blocked_on` across opted-in Projects.
- **Semantic history search** (`semantic_history_search`) is `GET /api/history/scan-search`:
  a query over distilled `summary`/`intent`/`target` records scoped to one run or Project.

## Dead-end memory

Dead-end memory has its own Project opt-in.
It writes a `dead-end` annotation only when one valid same-run record explicitly classifies an approach as `abandoned` and supplies a non-empty reason.
A rollover never creates a dead end.

The scan timeline has no deterministic path: every record comes from the OpenRouter model call.
So the `mux.dead_ends` reader (which reads these records) and `mux.prior_resolutions` (which reads the model-scored experience corpus) have no offline producer.
The live automations tier (`tests/test_live_automations.py`) proves those two readers against a real store round-trip, seeding a scan record and an experience row and asserting the tools read them with run attribution and the confidence gate.
The real semantic producer is exercised by hand with an OpenRouter key, because an isolated test daemon has none.

## API

```text
GET  /api/sessions/{session_id}/scan-timeline
PUT  /api/sessions/{session_id}/scan-timeline          {enabled: boolean}
PUT  /api/sessions/{session_id}/scan-timeline/project  {enabled: boolean}
POST /api/sessions/{session_id}/scan-timeline/scan
POST /api/sessions/{session_id}/scan-timeline/backfill
GET  /api/sessions/{session_id}/scan-timeline/{record_id}?rehydrate=0|1
GET  /api/sessions/{session_id}/catch-me-up
GET  /api/attention/blockers
GET  /api/history/scan-search?q=&run_id=|project_id=
```

The last three are the Phase 7.7 pull consumers; each returns `enabled: false` rather than a fake
empty when its Project opt-in is off.

Agents reach the timeline through the `scan_timeline` and `scan_search` MCP tools, never through
these endpoints, and the two write endpoints (`.../scan`, `.../backfill`) are not exposed to them
at all.
The `catch-me-up` digest is bounded (`DIGEST_MAX_*` in `scan_consumers.py`): `progress` keeps the
most recent phase segments and every line is length-capped, with `phase_segments_omitted` and
`claims_omitted` reporting what was dropped.
Unbounded, a 230-record run rendered a 17 KB digest, which is not a digest.

## Key files

- `src/swe_mux/scan_timeline.py`
- `src/swe_mux/behavioral_consumers.py` (Phase 7.7 adaptive title + phase-transition signals)
- `src/swe_mux/scan_consumers.py` (Phase 7.7 handoff/catch-me-up/live-blockers/search
  derivations; Phase 7.11 compact projection, digest bounds, repair classifier)
- `src/swe_mux/mcp.py` (the `scan_timeline` / `scan_search` tools)
- `src/swe_mux/automation.py` (`TranscriptSliceService.build_forward`, `tool_input_digest`)
- `src/swe_mux/automation_store.py`
- `src/swe_mux/server.py`
- `src/swe_mux/project_context.py`
- `frontend/src/ScanTimelineTab.tsx`
- `frontend/src/ProjectContextEditor.tsx`, `frontend/src/ProjectsManager.tsx`
- `tests/test_scan_timeline.py`
- `tests/test_mcp_scan_timeline.py`
- `tests/test_transcript_forward_slice.py`

## Related design

- `mux-mcp.md`
- `automation-enablement.md`
- `tier0-facts.md`
- `project-card.md`
- `automation.md`
- `../data-model.md`
- `../../development/CONTROL_PLANE_ROADMAP.md` §5.5
