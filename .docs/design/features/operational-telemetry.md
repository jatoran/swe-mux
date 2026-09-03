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
- `loop_stalls`: one row per explained event-loop stall (`stall_watchdog.py`): start, duration,
  whether the canary thread was starved too, how many stack dumps fired, the main thread's frames
  leaf first, the worker threads that were not parked, a host reading at explanation time, and
  the trace file the full dumps live in.
  Frames are capped at 40 per thread and 12 threads per row; the trace file holds the rest.
  Pruned on `operational_telemetry_retention_days` with the other evidence.

Creating the store on an existing mux database is additive. History migrations add the
compaction summary columns without replacing existing history rows.

### Canonical activity ledger

Long-term tool, skill, turn, run, and verification analytics use a second store under
`<data_dir>/telemetry/`.
The existing operational tables remain readable during migration and are never deleted by the
new ledger.

- `catalog.sqlite3` owns the schema version, segment inventory, entity-to-segment locations,
  exact closed-day tool, workload, skill, verification, and compaction rollups, exact
  closed-hour tool and workload rollups, dirty rollup days and hours, segment seal history,
  parser signatures, finding reviews, native reconciliation records, and resumable
  legacy-import cursors.
- `segments/YYYY-MM.sqlite3` owns content-free evidence and canonical run, turn, tool-call,
  model-request, compaction, skill-invocation, verification, and provider-metric entities
  whose activity began in that UTC month.
- Every file is versioned (`telemetry_schema.py`, `LEDGER_SCHEMA_VERSION`, 4 since
  2026-09-03).
  A migration step is a SQL statement or a callable, for the shapes SQL cannot express
  additively: recreating a derived rollup table whose key changed (version 3 put evidence
  quality into the tool rollup key), backfilling a new column from existing ones, and
  deriving a maintained table once (version 4's per-run repeat counts) while dirtying every
  rolled bucket so a new rollup (version 4's quality rollups) is built from the entities.
  Opening a file creates missing tables from the current schema and then applies each
  additive migration the file has not seen, checking every `ADD COLUMN` against the table
  first, so a data directory written by an older daemon gains exactly the columns the current
  code reads and a fresh file is stamped without re-applying anything.
  A file stamped newer than the daemon is refused.
  Background health reports the version, which files applied what, and a structural
  signature per file compared with the one a fresh file would carry, so an incomplete
  migration reads as drift rather than as an `INSERT` failure later.
- One entity has one home segment even when its completion evidence arrives in a later month.
- Closed UTC days are served from exact day rollups (`tool_daily`, `workload_daily`,
  `quality_daily`, `skill_daily`, `verification_daily`, `compaction_daily`), and the closed
  hours of a partial day from exact hour rollups (`tool_hourly`, `workload_hourly`,
  `quality_hourly`), so a 24-hour window that spans two partial days is answered from at
  most 23 hour rows plus the current hour.
  A tool page's exact `matching_calls` is summed from the same rollups whenever its filters
  are rollup columns, and counted from rows only for an identity filter (`run_id`,
  `turn_id`), so the count beside a page never scans the window.
  A raw query writes its dimension filters as `+column=?`, which keeps SQLite on the time
  index for a span that is at most a partial hour rather than driving the query from a
  backend index that reads every row of that backend; measured at ten million calls, the
  hint is the difference between 398 ms and the unfiltered cost.
  The repeated-identical-call finding reads `telemetry_call_repeats`, which every tool-call
  write keeps current per (run, agent, tool, input hash); a group is in the window when its
  latest repeat is, and its count is the run's whole count for that input, because the run
  is the unit the finding is about.
  The current hour, and any day or hour dirtied by late evidence, are read from canonical
  entities until the rollup worker rebuilds them; day rollups never delete hour rollups.
  A run or turn that ends on a later day than it started dirties its start day and hour,
  because that is where its rollup row lives.
  Consecutive raw stretches inside one month are read with one query, so a window of dirty
  days costs one query per month rather than one per day.
  Rolled-up and raw reads agree on every figure and differ only in the `coverage` block that
  says how the window was answered; the tests and `tools/telemetry_benchmark.py` assert it.
- Aggregates accept exact-match filters on Project, backend, and model (tools also on layer,
  family, status, and evidence quality).
  Every filter column exists on both the entity tables and the rollup tables, so a filtered
  answer is as exact as an unfiltered one.
- Every tool call carries an `evidence_quality` (`native`, `transcript`, `hook`,
  `reconciled`, `legacy`, `none`) derived from the rank of the source that closed it, and an
  approval wait paired from the `approval_needed` request that named the call to its
  resolution or its own result; nothing is estimated, and a resolution with no recorded
  request leaves the wait unknown.
  A run's `started_at_source` says whether its start was declared by the session record or
  history row or is only the earliest evidence; a declared start replaces an estimate and
  never the reverse.
  A denied status is a failure whose cause is known: the provider's own failed result for
  the same call arrives after its sandbox or permission verdict at the same rank, fills the
  fields it carries, and does not turn `denied` back into `failed`.
- Native stores are reconciled directly (`telemetry_reconcile.py`): every five minutes, and
  on `POST /api/telemetry/v2/reconcile`, each run's Claude, Codex, OMP, Pi, or OpenCode
  record is read past its per-run watermark by the same dialect scanner the legacy store
  uses and fed to the reducer as `reconciled_transcript` evidence, ranked below live native
  and transcript evidence and above hooks and legacy imports.
  The pass never writes a native store; a test proves every store byte-identical afterwards.
- Provider metrics (Codex's `codex.tool.call`, token usage, turn durations, guardian reviews,
  and their siblings) are kept as aggregated points with allow-listed attributes and never
  become entities; the metric summary compares `codex.tool.call` per run against the
  ledger's own count of that run's calls, and disagreement names the run.
- Inefficiency findings carry a stable `finding_key`; an operator's review (`useful`,
  `noise`, `already_known`) is the only feedback a finding collects, and
  `propose_adaptive_change` is the one gate a configuration change could ever pass: a
  `useful` verdict, a comparison window, and a rollback rule, none of which any code path
  supplies today.
- Cohort comparison splits one dimension and reports `comparable: false` with the reason when
  the cohorts differ on a dimension the split does not fix, so a model comparison cannot
  quietly be a Project comparison.
- The legacy dashboard stays reachable while `canonical_telemetry_legacy_dashboard_enabled`
  is on (the default); the shadow comparison classifies every disagreement between the legacy
  `tool_events` table and the canonical calls, and the switch is the operator's to turn off
  after reading it.
- Inactive segments are integrity-checked, WAL-checkpointed, SHA-256 sealed, and retained.
  Late evidence invalidates the prior seal with a durable reason before updating the segment.
- Detailed pages are cursor-bounded, while matching counts and aggregates cover the complete
  requested time range.
- The default query cohort is mux-owned runs from the last seven days.
  Imported provider history is a separately selected cohort.
- Provider output is not copied into the ledger.
  Evidence stores the native locator, native identifiers, byte and character counts, SHA-256,
  bounded target preview, parser version, and source precedence.
  A full-output hash paired with a bounded transcript preview records size as unknown rather than
  mislabelling the preview length as the result length.
- Hook output that exists only long enough to measure and hash is delivered to the canonical
  consumer alone.
  It is never written to the generic `events` table or broadcast to unrelated subscribers.
- Presentation events such as `state_changed` are excluded.
  The ledger accepts only auditable run, turn, tool, skill, approval, stall, subagent,
  verification, Git, land, and compaction evidence.

Canonical tool identity is `(run, agent, invocation layer, native call id)`.
The invocation layer keeps a Codex model-facing `exec` call distinct from the nested runtime
`Bash` call it dispatches.
Missing parent provenance remains `provider_unavailable`; timestamp proximity never upgrades it
to a proven parent relationship.

Canonical merge precedence is field-specific.
Native provider OTel outranks transcript/store evidence, which outranks hooks, reconciliation,
and legacy imports.
A lower-ranked source may fill a field the higher-ranked source did not provide, such as a hook
duration paired with a transcript result.
A measurement label describes the value beside it: a row that recorded no output size and
said so takes a later provider's size together with that provider's measurement, and a row
that already carries a measurement keeps the one its values came with.
The requested target and the executed arguments are two hashes kept apart
(`input_sha256` from what the model asked for, `executed_input_sha256` from what the provider
reports it ran).
Every contributing observation remains linked through `telemetry_entity_evidence`.

### Native provider telemetry

`canonical_telemetry_native_otel_enabled` hands each new Claude session an OTLP/JSON exporter
environment and each new Codex session an exporter configuration argument, both pointed at the
daemon's loopback ingress and authenticated with the session's hook secret.
The contracts were measured against Claude Code 2.1.259 and Codex CLI 0.153.0 on 2026-09-03
(`development/TELEMETRY_HARDENING.md` records the attribute sets); the scrubbed captures are
committed as fixtures and `tests/test_live_otlp.py` re-measures them.

The reducer (`telemetry_otlp.py`) copies only named metadata attributes.
Content attributes (`tool_input`, `arguments`, `output`, `prompt`, `response`, `error`) are
hashed and measured; identity attributes (`user.*`, `organization.id`, `session.id`,
`host.name`) are dropped by construction, and a test asserts that none of either class
survives reduction of the real captures.
A Codex call id prefixed `exec-` is the nested code-mode execution and maps to the runtime
layer, where it merges with the hook's entity; Codex model traffic completes as
`response.completed` and its successful HTTP calls are not counted as model requests.

Codex also exports OTLP metrics when its descriptor declares `exports_metrics` (measured on
0.153.0: one delta-temporality batch of 64 metrics at exit); the reducer keeps the allow-listed
names and attributes as provider metrics and counts the rest as recognised signatures.
A `codex.sandbox_outcome` verdict (`denied`, `timed_out`, `signal`, `escalated`; first seen live
on 2026-09-03 when the sandbox refused a write) reduces to a denied or failed result whose
`error_type` names the sandbox, or to a resolved approval when the unsandboxed retry ran.
Each descriptor's `provides` set names the facts its export was measured to carry, so the
quality view reports an absent fact as unsupported for that harness rather than as missing on
every row.

Every provider event name is counted per harness version in `parser_signatures`, recognised
or not.
A future CLI that renames an attribute therefore shows as an unrecognised name in the
Collection health readout rather than as a ledger that quietly stopped filling a column.
That is how `sandbox_outcome` was found: the live denial canary failed on an unrecognised name
rather than on a quietly emptier ledger.

### Exports

`GET /api/telemetry/v2/export/{kind}` streams every canonical row of one kind in the
selected window, cohort, and filters as JSONL or CSV, paged through the ledger's worker so
neither the event loop nor memory holds the whole answer.
Rows carry their evidence identifiers and source locator; provider output is not in the
ledger and so is not in an export.

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
- The per-harness known-record vocabulary is the drift signal, so a record type the observer
  already knows must be known here too. Codex 0.149's `item_completed` envelope was missing
  from this side only, and because it restates every item the CLI completes it put real
  sessions at a 0.31-0.34 unknown ratio - above the 0.25 the live canary fires at - while
  nothing had drifted. Two vocabularies for one dialect is how that happens; a payload type
  added to `observation.py` belongs in `operational_telemetry.py` in the same change.

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
GET /api/telemetry/v2/tools/summary?from=&to=&origin=&project=&backend=&model=&layer=&family=&status=&evidence=
GET /api/telemetry/v2/tools?from=&to=&cursor=&limit=&project=&backend=&model=&origin=&layer=&family=&status=&evidence=&raw_name=&run_id=&turn_id=
GET /api/telemetry/v2/tools/{tool_call_id}
GET /api/telemetry/v2/runs/{run_id}
GET /api/telemetry/v2/turns/{turn_id}
GET /api/telemetry/v2/workload?from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/skills/summary?from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/verifications/summary?from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/metrics/summary?from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/quality?from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/inefficiencies?from=&to=&origin=&project=&backend=&model=&layer=&family=&status=&evidence=&reviewed=
POST /api/telemetry/v2/inefficiencies/review {finding_key, kind, verdict, note?}
GET /api/telemetry/v2/compare?split=&from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/compactions?from=&to=&origin=&project=&backend=&model=
GET /api/telemetry/v2/parsers
GET /api/telemetry/v2/shadow?from=&to=
POST /api/telemetry/v2/reconcile
GET /api/telemetry/v2/export/{kind}?from=&to=&origin=&project=&backend=&model=&format=jsonl|csv
GET /api/telemetry/v2/{kind}?from=&to=&cursor=&limit=&origin=&project=&backend=&model=
POST /api/telemetry/otlp/{session_id}/v1/logs
POST /api/telemetry/otlp/{session_id}/v1/metrics
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
- `session_process_priority = "below_normal"` (`normal` turns enforcement off; applies at the
  next daemon start)
- `daemon_process_priority = "above_normal"` (`normal` leaves the daemon where the OS put it;
  applies at the next daemon start)
- `process_evidence_retention_days = 30`
- `operational_telemetry_retention_days = 180`
- `canonical_telemetry_native_otel_enabled = false` enables authenticated loopback OTLP/JSON
  capture for newly spawned supported harness sessions.
  Existing sessions retain the exporter environment they started with.
- `canonical_telemetry_legacy_dashboard_enabled = true` keeps the legacy tool table and the
  shadow comparison as a tab of Fleet activity; it is the operator's switch to turn off once
  the comparison has been read, and turning it off deletes nothing.
- `provider_quota_poll_minutes = 15`
- `provider_quota_turn_refresh_enabled = false`
- `provider_quota_turn_refresh_min_minutes = 5`

Settings validates and hot-applies these values. Telemetry collection never blocks PTY input
or session lifecycle.

## Key files

- Store and native reconciliation: `src/swe_mux/operational_telemetry.py`
- Canonical ledger schema and migrations: `src/swe_mux/telemetry_schema.py`
- Canonical ledger write path (identity, precedence, rollups, seals): `src/swe_mux/telemetry_ledger.py`
- Canonical ledger queries, filters, and exports: `src/swe_mux/telemetry_queries.py`
- Legacy stream importers: `src/swe_mux/telemetry_imports.py`
- Direct native-store reconciliation: `src/swe_mux/telemetry_reconcile.py`
- Daemon adapter (batching, catch-up, reconciliation, rollup worker, health): `src/swe_mux/telemetry_service.py`
- Provider OTLP normalization: `src/swe_mux/telemetry_otlp.py`
- Query benchmark on a seeded scratch ledger (`--fast-seed` for ten million calls): `tools/telemetry_benchmark.py`
- Event-loop lag under an ingestion flood, with a no-ledger control: `tools/telemetry_ingest_latency.py`
- Call-by-call audit of a window against the providers' own records: `tools/telemetry_audit_window.py`
- Process reconciliation/actions: `src/swe_mux/processes.py`
- Event-loop stall explanation: `src/swe_mux/stall_watchdog.py`
- Scheduling-class policy: `src/swe_mux/process_priority.py`
- Provider polling: `src/swe_mux/provider_accounts.py`
- Native event parsing: `src/swe_mux/observation.py`
- History summaries/migrations: `src/swe_mux/history.py`
- Composition/API: `src/swe_mux/server.py`
- Process UI: `frontend/src/ProcessPanel.tsx`
- Telemetry UI: `frontend/src/FleetActivityView.tsx`, `frontend/src/WorkloadTelemetry.tsx`, `frontend/src/telemetryCaption.tsx`, `frontend/src/LegacyToolTelemetry.tsx`, `frontend/src/QuotaAnalytics.tsx`, `frontend/src/ProviderAccounts.tsx`
- Frontend payload shapes: `frontend/src/operationalTelemetry.ts`
- Fixtures/tests: `tests/fixtures/telemetry/` (OTLP captures and the per-dialect native-store
  fixtures), `tests/test_telemetry_otlp.py`, `tests/test_telemetry_ledger.py`,
  `tests/test_telemetry_hardening.py`, `tests/test_telemetry_reconcile.py`,
  `tests/test_telemetry_analytics.py`, `tests/test_operational_telemetry_phase2.py`,
  `tests/test_live_agent_conformance.py`, `tests/test_live_otlp.py`

## Relates to

- `processes-and-previews.md`: live process tree and preview behavior.
- `provider-accounts.md`: auth ownership and provider quota acquisition.
- `history.md`: durable run context/compaction summaries.
- `usage.md`: separate optional `ccusage` historical cost/token cache.
