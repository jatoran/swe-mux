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
- the optional cached Project card.

The fixed default model is the OpenRouter latest alias `deepseek/deepseek-v4-flash`.
The call requests strict JSON schema, disables reasoning, and locally validates every semantic field.
Provider failure, missing output, or invalid output produces no scan record.

## Record contract

Every record carries `session_id`, `agent_run_id`, `t0`, `t1`, lifecycle state, behavior labels, work phase, intent, claim, user ask, blockers, target paths, summary, confidence, coverage, mechanical novelty, model identity, prompt version/hash, and source evidence.
Source evidence names the authoritative run, bounded time span, message timestamps, and transcript input hash.
Expanding source reparses the authoritative current or historical transcript for that run and returns only the bounded slice.
No transcript text is copied into the scan database.

Novelty is deterministic lexical Jaccard distance against same-run semantic records in v1.
This is deliberately mechanical and run-local.
Changing the algorithm later does not change the persisted field or its rollover boundary.

## Budgets and visibility

The scanner enforces the shared global and per-rule daily token and dollar budgets, shared hourly call caps, the Project's `scan_timeline_daily_budget_usd`, and `scan_timeline_run_token_budget`.
Successful calls and provider failures that report billable usage enter the shared spend ledger with Project and run attribution.
An unpriced billable call reserves the conservative preflight estimate so missing provider accounting cannot weaken a budget.

The active session header shows the Project's scan spend and budget whenever the global and Project gates permit the feature, including when the current run is off.
The Timeline tab shows daily spend, daily tokens, current-run tokens, current-run budget, record-source reads, source rehydrations, and the measured rehydration rate.

## Dead-end memory

Dead-end memory has its own Project opt-in.
It writes a `dead-end` annotation only when one valid same-run record explicitly classifies an approach as `abandoned` and supplies a non-empty reason.
A rollover never creates a dead end.

## API

```text
GET  /api/sessions/{session_id}/scan-timeline
PUT  /api/sessions/{session_id}/scan-timeline          {enabled: boolean}
POST /api/sessions/{session_id}/scan-timeline/scan
GET  /api/sessions/{session_id}/scan-timeline/{record_id}?rehydrate=0|1
```

## Key files

- `src/swe_mux/scan_timeline.py`
- `src/swe_mux/automation_store.py`
- `src/swe_mux/server.py`
- `frontend/src/ScanTimelineTab.tsx`
- `frontend/src/ScanSpendStatus.tsx`
- `tests/test_scan_timeline.py`

## Related design

- `automation-enablement.md`
- `tier0-facts.md`
- `project-card.md`
- `automation.md`
- `../data-model.md`
- `../../development/CONTROL_PLANE_ROADMAP.md` §5.5
