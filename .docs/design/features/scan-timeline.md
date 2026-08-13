# Scan timeline

## What it is

The scan timeline is a read-only, run-scoped semantic index over bounded transcript deltas and deterministic Tier 0 facts.
It produces a readable behavioral history without writing to a PTY, changing agent state, or ranking attention.
It is the Tier 1 substrate for dead-end memory and later cross-session semantic consumers.

## Authorization and lifetime

Three independent gates must all be open before a scan can call OpenRouter:

1. The global `scan_timeline_enabled` master switch is on.
2. The Project enables `scan_timeline` and its `raw_store` and `tier0` dependencies.
3. The current `agent_run_id` is enabled from that session's Timeline drawer tab.

The Timeline drawer exposes the Project permission directly.
Turning it on also enables the required dependencies and creates a blank `.swe-mux/project-context.md` if needed.
Turning it off disables Scan timeline and consumers that require it.
Changing Project permission never authorizes a current run and never starts a backfill.

The run gate defaults off.
It belongs to one provider conversation, not the persistent terminal session.
`/clear`, `/new`, another conversation rollover, session exit, and session crash disable the old run and never authorize the successor.
A rollover writes a visible boundary record and resets the transcript cursor, continuity window, and novelty comparison.

## Capture flow

Event triggers are `turn_started`, `turn_ended`, `tool_result`, Git change, context compaction, session exit, and session crash.
A supervised three-minute heartbeat covers long-running work that has no event boundary.
Triggers debounce per session and a per-session lock prevents overlapping scans.

Each request contains only:

- the bounded transcript delta since the prior same-run record;
- the prior two or three records from the same run;
- bounded Tier 0 fact identifiers and targets;
- the current user-authored Project context Markdown, when non-empty.

The fixed default model is the OpenRouter latest alias `deepseek/deepseek-v4-flash`.
The call requests strict JSON schema, disables reasoning, and locally validates every semantic field.
Provider failure, missing output, or invalid output produces no scan record.

### Full-session scan

**Scan full session** is an explicit drawer action that scans uncovered messages from the beginning of the current run to a fixed current watermark.
It parses the authoritative transcript once, removes intervals already represented by stored records, chunks the remaining messages oldest first under the ordinary 32-message and 24 KiB input limits, and uses only earlier records for continuity and novelty.
Chunking strips native tool arguments because the scan representation carries tool names only, and bounds oversized text while recording truncated coverage instead of aborting the job.
The operation runs in a background task under the same per-session lock as live scans.
The drawer polls its in-process job state and shows running progress plus `completed`, `partial`, or `failed` outcomes.
Budgets, provider availability, observation health, all three gates, and strict output validation remain in force.
If one stops the job, the result is `partial` with the exact reason and all already completed records remain readable.
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

## Budgets and visibility

The scanner enforces the shared global and per-rule daily token and dollar budgets, shared hourly call caps, the Project's `scan_timeline_daily_budget_usd`, and `scan_timeline_run_token_budget`.
The per-run token budget defaults to 100,000 so ordinary full-session scans can backfill substantial conversations while the independent daily, hourly, and dollar gates remain effective.
Successful calls and provider failures that report billable usage enter the shared spend ledger with Project and run attribution.
An unpriced billable call reserves the conservative preflight estimate so missing provider accounting cannot weaken a budget.

The Timeline tab is the only scan control and status surface.
It shows Project permission and context, current-run permission, daily spend, daily tokens, current-run tokens and budget, record-source reads, source rehydrations, and the measured rehydration rate.
Each record shows the count of deterministic evidence targets and keeps their paths, symbols, and command strings inside a collapsed, scroll-bounded disclosure.
There is no scan button or scan-spend control in the application topbar.

## Dead-end memory

Dead-end memory has its own Project opt-in.
It writes a `dead-end` annotation only when one valid same-run record explicitly classifies an approach as `abandoned` and supplies a non-empty reason.
A rollover never creates a dead end.

## API

```text
GET  /api/sessions/{session_id}/scan-timeline
PUT  /api/sessions/{session_id}/scan-timeline          {enabled: boolean}
PUT  /api/sessions/{session_id}/scan-timeline/project  {enabled: boolean}
POST /api/sessions/{session_id}/scan-timeline/scan
POST /api/sessions/{session_id}/scan-timeline/backfill
GET  /api/sessions/{session_id}/scan-timeline/{record_id}?rehydrate=0|1
```

## Key files

- `src/swe_mux/scan_timeline.py`
- `src/swe_mux/automation_store.py`
- `src/swe_mux/server.py`
- `src/swe_mux/project_context.py`
- `frontend/src/ScanTimelineTab.tsx`
- `tests/test_scan_timeline.py`

## Related design

- `automation-enablement.md`
- `tier0-facts.md`
- `project-card.md`
- `automation.md`
- `../data-model.md`
- `../../development/CONTROL_PLANE_ROADMAP.md` §5.5
