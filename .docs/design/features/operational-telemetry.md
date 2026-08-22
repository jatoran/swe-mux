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
  stable attribution version/source and confirmation times, evidence state/reason/confidence, observation times, and exit or ownership-rejection evidence.
- `quota_samples`: append-only raw observations within the configured retention window.
- `quota_sample_rollups`: daily first/last/min/max/error summaries produced before old raw
  samples are pruned.
  The primary key includes the verified provider account UUID, so a reused local account slot cannot merge different credential owners into one day.
- `quota_reset_events`: before/after evidence, expected/observed time, classification,
  confirmation, confidence, suppression reason, and nullable durable user review status/time.
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
2. Normal reconciliation keys each process by PID plus creation time, validates creation-time causality on every parent edge, and records parent lineage, command hash, owner, resource counters, and Job Object assignment outcome.
3. A descendant outside the current tree is `escaped`. After its root session ends and the
   configurable grace expires, a matching live fingerprint becomes `suspected_orphan`. The
   grace runs from a deadline stamped once, when the root is first observed ended; it is never
   re-derived per pass from `last_seen`, which every pass refreshes and which therefore made
   the window slide forever for a session already dropped by the manager.
4. Startup restores candidates, then revalidates creation time and available fingerprints.
   PID reuse becomes `stale`; inaccessible startup evidence stays stale/unverifiable rather
   than attaching to the process.
   Current tree and Job Object members upgrade to attribution version 2; uncorroborated version-1 survivors become stale `ownership_rejected` evidence with cleared listeners.
5. Startup also retires every live durable claim for a reused OS process fingerprint when multiple sessions claim it.
   The current reconciliation pass may then re-establish exactly one supported owner.
6. Interrupt/terminate requests include the durable `identity_id` and perform another live
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
   rules. Only confirmed unsuppressed unexpected resets raise an alert.
7. Confirmed alerts are coalesced per provider for 60 seconds before the
   `unexpected_quota_reset` event is emitted.
   A provider rolls its whole plan over at once, so every enabled account of that provider
   confirms the same rollover inside one sequential 15-minute polling pass; emitting per
   account-window turned one fact into up to `2N` sounds and lock-screen pushes.
   The event carries `count` and a `resets` array alongside the scalar
   `reset_id`/`account_id`/`window` of the newest member.
   Daemon shutdown flushes anything still held rather than dropping it.
8. The active alert is the whole unreviewed set, not its newest row, so one rollover is
   triaged once instead of once per account.
   It drives the purple account indicator and the optional alert sound, whose preference
   defaults off.
9. User review preserves the detector evidence while removing it from the active alert summary,
   and applies to every id in the submitted group.
   `seen` is a plain acknowledgement available for any row; a Codex row may be `manual_usage`;
   any provider row may be `discarded`.
   Review state is durable and server-side, so dismissing an alert on one device silences it on
   every device, and it is shown in the evidence log.

Positive quota deltas produce probabilistic attribution records. Mux session overlap defines
the correlated range; unclaimed movement remains an explicit external/unassigned range.
Explicit native tokens may weight allocation among overlapping mux sessions, but never
translate transcript tokens into provider quota weighting.

## Transcript reconciliation

- Live normalized events enter through the persisted EventBus and deduplicate by native call
  identity/source identity.
- A bounded background reconciliation scans recent Claude, Codex, and OMP transcript tails, at most
  32 MiB per file and 2,000 histories per pass. It runs after startup and hourly.
- The event consumer and the hourly prune/reconcile loop both run under the shared
  background-task supervisor. A transient SQLite error costs one iteration, not the feature:
  unguarded, the consumer's death silently ended tool and compaction capture, and the hourly
  loop's death ended *all* retention enforcement, for the remainder of a daemon lifetime that
  session-preserving reload is designed to measure in weeks. Loop liveness, fault counts and
  last fault are at `GET /api/diagnostics/background`.
- Reconciliation loads possible live duplicates once per transcript and batch-inserts native
  tool/compaction evidence. It never performs one SQLite lookup per parsed tool event or blocks
  terminal visibility/attachment while durable telemetry is queued.
- Provider-specific parser versions prevent stale coverage from being treated as current.
  Unknown records/tools and truncated-tail diagnostics remain visible in the dashboard.
- OMP reconciliation recognizes its versioned session-entry union, extracts assistant `toolCall`
  blocks and matching `toolResult` messages, records an explicit skill only when the tool arguments
  name one, and keys native compactions by their entry id.
  The same compaction id is used by live hook and transcript ingestion, so reconciliation collapses
  legacy cross-source duplicates only inside the measured 250 ms hook-to-transcript window.
  A complete non-truncated scan republishes the repaired live and historical compaction summary.
- If supervisor adoption repairs a session whose provider/transcript identity was
  misattributed, its tool/compaction rows and coverage cursor are deleted and rebuilt from the
  corrected native transcript. Process fingerprints are retained because they describe the
  real PTY tree, but their run owner is restored to the root run.
- Historical collision repair is narrower: it removes tool/compaction rows only for the proven
  false run, clears the potentially borrowed per-session coverage row, and reassigns matching
  process evidence to the canonical root.
- Claude, Codex, and OMP versioned fixtures cover explicit tools, failures, skills, compactions,
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

## Surfaces

`/api/telemetry/operational` is read by two dialogs, for different halves of one payload.

- **Usage → Quota** takes `quota.attributions`, beside the quota charts and the reset log (`usage.md`).
- **Resources → Fleet activity** takes `tools` and `compactions`, and draws `runs + workload` from `/api/telemetry/workloads` beside them (`ui.md`).

The split follows the question rather than the endpoint.
Quota movement is one of the three pots of spend; tool calls, skill invocations, and compaction events measure behavior and are not a currency, so they sit beside Processes rather than beside a bill.
Parser and reconciliation coverage is drawn collapsed under the tool metrics it qualifies: it says whether those figures were collectable, which is asked only once they already look wrong.

## API surface

```text
GET /api/telemetry/operational?provider=&account=&limit=
GET /api/telemetry/quota-series?provider=&account=&since=&until=&resolution=raw|daily&limit=
POST /api/telemetry/quota-resets/review {ids: [reset_id], resolution: seen|manual_usage|discarded}
GET /api/provider-accounts
GET /api/processes[?session=]
POST /api/processes/action {session_id, pid, identity_id, action}
```

The operational snapshot is bounded to 1–1,000 rows per collection and returns
`interpretation: observational_correlation_only`. Provider-account responses carry
`reset_alert` as the true unreviewed `count` plus up to 100 evidence `items`, newest first.
Process snapshots expose evidence states
`active|exited|escaped|suspected_orphan|stale|inaccessible`, stable attribution provenance, derived server eligibility, and bounded ownership diagnostics.
The quota-series endpoint applies provider, local account, and epoch-range filters in SQLite instead of truncating a global sample list in the browser.
Daily resolution combines retained rollups with current raw samples and returns one series per verified provider identity.
Raw resolution defaults to the last seven days when `since` is omitted.
Its `interpretation` is `quota_utilization_not_token_usage`.

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
- Telemetry UI: `frontend/src/FleetActivityView.tsx`, `frontend/src/WorkloadTelemetry.tsx`, `frontend/src/QuotaAnalytics.tsx`, `frontend/src/ProviderAccounts.tsx`
- Frontend payload shapes: `frontend/src/operationalTelemetry.ts`
- Fixtures/tests: `tests/fixtures/telemetry/v1/`, `tests/test_operational_telemetry_phase2.py`,
  `tests/test_live_agent_conformance.py`

## Relates to

- `processes-and-previews.md`: live process tree and preview behavior.
- `provider-accounts.md`: auth ownership and provider quota acquisition.
- `history.md`: durable run context/compaction summaries.
- `usage.md`: separate optional `ccusage` historical cost/token cache.
