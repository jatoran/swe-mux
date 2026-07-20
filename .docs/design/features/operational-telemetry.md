# Durable operational evidence

## What it is

Durable, bounded observations for process identity, provider quota movement, reset evidence,
quota/activity correlation, explicit context compaction, and explicit tool/skill activity.
The subsystem records evidence plus uncertainty; it does not auto-kill processes, infer skill
use, or claim that quota movement proves a person's activity.

## Evidence boundaries

- Process identity is `session_id + PID + process creation time`; PID alone is never an
  ownership key. Persisted command data is SHA-256 only, never command text.
- Quota samples preserve provider/account, sample time, session/weekly values and reset
  times, source, freshness, raw precision, error, selected-account state, and auth state.
- Quota attribution means mux-owned activity overlapped a sampled quota interval. It is not
  causal billing data or shared-account identity proof.
- Compaction counts require explicit provider-native records. A token decrease remains
  `unknown`.
- Tool metrics require explicit native hook/transcript records. Skill metrics additionally
  require an explicit skill name on a native invocation; prompt similarity, Markdown, and
  generic file reads do not qualify.

## Data model

All records live in the existing `<data_dir>/mux.db` through an independent serialized
SQLite connection:

- `process_evidence`: fingerprint, lineage, Project/run owner, Job Object assignment result,
  evidence state/reason/confidence, observation times, and exit evidence.
- `quota_samples`: append-only raw observations within the configured retention window.
- `quota_sample_rollups`: daily first/last/min/max/error summaries produced before old raw
  samples are pruned.
- `quota_reset_events`: before/after evidence, expected/observed time, classification,
  confirmation, confidence, and suppression reason.
- `quota_attributions`: estimate/range, explicit external remainder, overlapping session
  count, sample gap, provider-lag allowance, optional native-token allocation, and caveats.
- `context_compactions`: deduplicated explicit compaction evidence with backend capability,
  confidence, parser version, Project, model, run, and source identity.
- `tool_events`: deduplicated calls/results with raw name, normalized taxonomy, success,
  duration when available, backend/model/Project/run, parser version, and explicit skill.
- `transcript_telemetry_coverage`: provider-specific reconciliation version, recognized and
  unknown counts, extracted event counts, coverage status, and bounded diagnostics.

Creating the store on an existing mux database is additive. History migrations add the
compaction summary columns without replacing existing history rows.

## Process lifecycle

1. The process inspector samples only roots and descendants of live/retained mux sessions;
   one cached global network table supplies listener/connection evidence.
2. Normal reconciliation keys each process by PID plus creation time and records parent
   lineage, command hash, owner, resource counters, and Job Object assignment outcome.
3. A descendant outside the current tree is `escaped`. After its root session ends and the
   configurable grace expires, a matching live fingerprint becomes `suspected_orphan`.
4. Startup restores candidates, then revalidates creation time and available fingerprints.
   PID reuse becomes `stale`; inaccessible startup evidence stays stale/unverifiable rather
   than attaching to the process.
5. Interrupt/terminate requests include the durable `identity_id` and perform another live
   ownership/fingerprint check immediately before acting. No evidence state causes automatic
   termination.

Terminal process evidence has its own retention setting. Active, escaped, and suspected-
orphan evidence is retained for revalidation; old terminal evidence is pruned. Ignored
Project folders remain outside the leased resource-watcher set and are unrelated to process
enumeration.

## Quota and reset lifecycle

1. Provider-account polling records ready, stale, and error samples. The existing latest
   account API derives quota from the newest durable sample.
2. Scheduled polling defaults to 15 minutes. Optional selected-account refresh after an
   eligible root `turn_ended` event is disabled by default and globally rate-limited; child
   completion never triggers it.
3. A fresh downward movement is `scheduled` when the advertised reset boundary falls between
   the previous and current sample, with a bounded 15-minute early tolerance. This interval
   check keeps a scheduled reset scheduled even when polling first observes it late.
4. An `unexpected` candidate requires an advertised reset at least one hour in the future, a
   drop of at least 20 percentage points, and a resulting value no higher than 10%. Missing or
   ambiguous reset timers and smaller movements remain `uncertain`.
5. Confirmation requires a second fresh, stable-low sample from the same account/auth state
   5–45 minutes later and still before the advertised boundary. Stale or out-of-order evidence,
   rebounds, account/auth transitions, and reaching the scheduled boundary suppress it.
6. Store startup re-evaluates legacy unexpected classifications under these deterministic
   rules. Only confirmed unsuppressed unexpected resets produce the purple account indicator
   and optional browser-local sound; `localStorage` deduplicates per-device playback and the
   preference defaults off.

Positive quota deltas produce probabilistic attribution records. Mux session overlap defines
the correlated range; unclaimed movement remains an explicit external/unassigned range.
Explicit native tokens may weight allocation among overlapping mux sessions, but never
translate transcript tokens into provider quota weighting.

## Transcript reconciliation

- Live normalized events enter through the persisted EventBus and deduplicate by native call
  identity/source identity.
- A bounded background reconciliation scans recent Claude/Codex transcript tails, at most
  32 MiB per file and 2,000 histories per pass. It runs after startup and hourly.
- Reconciliation loads possible live duplicates once per transcript and batch-inserts native
  tool/compaction evidence. It never performs one SQLite lookup per parsed tool event or blocks
  terminal visibility/attachment while durable telemetry is queued.
- Provider-specific parser versions prevent stale coverage from being treated as current.
  Unknown records/tools and truncated-tail diagnostics remain visible in the dashboard.
- Claude and Codex versioned fixtures cover explicit tools, failures, skills, compactions,
  and unknown records.

Authenticated Phase 2 canaries are opt-in because they consume real provider quota and
create ordinary provider transcript history. Each CLI reads one temporary sentinel through a
read-only tool; the test then verifies native context/tool evidence, parser coverage, history,
and durable telemetry-store output. A companion quota probe uses the current access token but
explicitly refuses credential refresh, RPC fallback, or auth-file writes:

```powershell
$env:SWEMUX_RUN_LIVE_PHASE2_TESTS = '1'
uv run pytest tests/test_live_agent_conformance.py -m "live_telemetry or live_quota"
```

Run the canaries after Claude/Codex CLI upgrades and periodically in a protected authenticated
lane, not on every pull request. Compaction remains fixture-driven because forcing a genuine
context compaction is slow, costly, and nondeterministic; newly observed native compaction
records must be sanitized into the versioned fixture corpus.

## API surface

```text
GET /api/telemetry/operational?provider=&account=&limit=
GET /api/provider-accounts
GET /api/processes[?session=]
POST /api/processes/action {session_id, pid, identity_id, action}
```

The operational snapshot is bounded to 1–1,000 rows per collection and returns
`interpretation: observational_correlation_only`. Provider-account responses include the
latest confirmed reset summary. Process snapshots expose evidence states
`active|exited|escaped|suspected_orphan|stale|inaccessible`.

## Configuration

- `process_poll_seconds = 5`
- `process_orphan_grace_seconds = 15`
- `process_evidence_retention_days = 30`
- `operational_telemetry_retention_days = 180`
- `provider_quota_poll_minutes = 15`
- `provider_quota_turn_refresh_enabled = false`
- `provider_quota_turn_refresh_min_minutes = 5`

Settings validates and hot-applies these values. Telemetry collection never blocks PTY input
or session lifecycle.

## Key files

- Store and native reconciliation: `src/swe_mux/operational_telemetry.py`
- Process reconciliation/actions: `src/swe_mux/processes.py`
- Provider polling: `src/swe_mux/provider_accounts.py`
- Native event parsing: `src/swe_mux/observation.py`
- History summaries/migrations: `src/swe_mux/history.py`
- Composition/API: `src/swe_mux/server.py`
- Process UI: `frontend/src/ProcessPanel.tsx`
- Telemetry UI: `frontend/src/UsageDashboard.tsx`, `frontend/src/ProviderAccounts.tsx`
- Fixtures/tests: `tests/fixtures/telemetry/v1/`, `tests/test_operational_telemetry_phase2.py`,
  `tests/test_live_agent_conformance.py`

## Relates to

- `processes-and-previews.md`: live process tree and preview behavior.
- `provider-accounts.md`: auth ownership and provider quota acquisition.
- `history.md`: durable run context/compaction summaries.
- `usage.md`: separate optional `ccusage` historical cost/token cache.
