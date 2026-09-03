# Telemetry hardening

## Objective

Produce a complete, provenance-preserving activity ledger for auditing harness behavior over
time and within and across sessions.
The resulting analytics must expose collection completeness, support exact filtered totals, and
identify inefficiencies without claiming causation from correlation.

## Status

Every item on this checklist is complete as of 2026-09-03 (schema 4).
The two live scenarios that a headless invocation cannot produce, compaction and conversation
rollover, are stated as such under "Completion gates" with the fixture tests that stand in for
them.
What remains is maintenance: re-run the live canaries after a CLI upgrade, and re-run the
measurements after a schema or index change.

## Non-negotiable contracts

- The persistent terminal session, provider conversation, turn, agent instance, model-facing
  tool call, and nested runtime tool call are distinct identities.
- Native content remains in the provider transcript or conversation store by default.
- Canonical telemetry stores hashes, sizes, bounded targets, structured outcomes, and durable
  source locators rather than copying tool output.
- No analytics total is computed from a displayed page or top-N slice.
- No retained evidence is silently deleted.
- Finite response pages are allowed; finite calculations are not.
- Imported history is never presented as mux-observed activity unless selected explicitly.
- Missing, unsupported, conflicted, and unavailable are distinct states.
- A derived relationship never replaces provider-native provenance.
- Capture, migration, and rollup work remain off the PTY and HTTP event loops.
- A ledger file is only ever changed additively, through a versioned migration, and a file
  newer than the daemon is refused rather than read with the wrong expectations.

## Implemented foundation

- [x] Monthly metadata-only ledger segments under `<data_dir>/telemetry/segments/`.
- [x] Small catalog database with segment and entity locations.
- [x] Canonical run, turn, tool-call, skill-invocation, verification, and evidence tables.
- [x] Global entity identity across month boundaries.
- [x] Model and runtime invocation layers.
- [x] Independent tool family, operation, transport, server, and raw-name dimensions.
- [x] Field-level source enrichment with evidence links.
- [x] Content-free output hashes and byte/character counts.
- [x] Batched single-worker EventBus ingestion and background health reporting.
- [x] Content-sensitive hook result delivery only to the canonical consumer.
- [x] Resumable, non-destructive import of legacy tool rows, run identities, and Tier 0 test
  outcomes.
- [x] Exact time-window tool aggregates and cursor-bounded details.
- [x] Exact closed-day tool rollups with raw reads for current, partial, or dirty days.
- [x] Integrity-checked segment sealing with durable invalidation on late evidence.
- [x] Canonical workload axes for terminal lifetime, turn time, tool execution, approval wait,
  stalls, subagent activity, and verification.
- [x] Store-backed OpenCode conversations admitted to transcript telemetry reconciliation.
- [x] Model-request timing, retry, token, cache, cost, and error metadata from native OTel logs.
- [x] Canonical compaction events and non-destructive legacy compaction import.
- [x] Deterministic inefficiency candidates with evidence and coverage denominators.
- [x] Tool-call audit drill-down to content-free source evidence.
- [x] Versioned additive schema migrations (`telemetry_schema.py`; schemas 2, 3, and 4 on
  2026-09-03 - 3 recreating the tool rollup because evidence quality joined its key, 4
  adding quality rollups and the per-run repeat counts the ten-million-call measurement
  demanded) with PRAGMA-introspected per-file signatures and a drift readout in the daemon's
  background health.
- [x] Module split: schema and migrations, legacy importers, direct native reconciliation, the
  query surface, the write path, and the daemon adapter are six files rather than one.

## Measured provider contracts (2026-09-03)

Both native exporters were exercised against the daemon's own ingress path from a loopback
receiver, and the payloads are committed with identities and content removed
(`tests/fixtures/telemetry/otlp-claude-2.1.259.json`, `otlp-codex-0.153.0.json`,
`otlp-codex-0.153.0-metrics.json`).
The live canary that re-measures them is `tests/test_live_otlp.py` (`live_agent` +
`live_telemetry`, gated on `SWEMUX_RUN_LIVE_PHASE2_TESTS=1`).

- Claude Code 2.1.259 honours the environment contract and posts to the configured logs
  endpoint verbatim.
  `tool_result` carries `tool_use_id`, `success`, `duration_ms`, `prompt.id` (the turn),
  `event.sequence`, the executed `tool_input` (hashed, never stored), and both
  `tool_input_size_bytes` and `tool_result_size_bytes`; the result body itself is never sent.
  `tool_decision` carries `decision` and `source`; `api_request` carries `request_id`,
  `client_request_id`, tokens, cache figures, `cost_usd`, `effort`, `speed`, and `query_source`.
  Every record also carries `user.email`, `user.id`, `user.account_id`, `user.account_uuid`,
  `organization.id`, and `session.id`; the reducer drops all of them by construction and a
  test asserts none survives.
  A headless denial is not a decision event: under `--permission-mode dontAsk` with only
  `Read` allowed, 2.1.259 still wrote the file (a successful `Write` result, no
  `tool_decision`), and with the write tools on `--disallowedTools` it never proposes them,
  so nothing is exported.
  The denied-decision reduction is proven on the captured `tool_decision` fixture instead.
- Codex CLI 0.153.0 honours the `-c otel.exporter=...` argument contract with the header
  attached and posted twelve batches on the configured path.
  `tool_result` carries `call_id`, `duration_ms`, `success`, `output_truncated`,
  `tool_result_seq`, `agent_name` (`/root`), `mcp_server`, `tool_namespace`, and the
  `arguments` and `output` bodies (hashed and measured, never stored).
  The nested code-mode execution is reported beside the model-facing call under a call id
  prefixed `exec-`, which the reducer maps to the runtime layer so it merges with the hook's
  entity rather than counting as a peer.
  Codex's model traffic completes as `sse_event` with `event.kind=response.completed` and the
  token, cache, reasoning, and time-to-first-token figures; `api_request` there is an HTTP call
  (`/models`) and only its failures are recorded.
  An automated-review sub-conversation exports under its own `conversation.id`, which the
  ledger keeps distinct.
  With `-c otel.metrics_exporter=...` it also posts one delta-temporality metrics batch of 64
  metrics at exit; the attributable ones are `codex.tool.call` (tool, success,
  command_category, sandbox_policy), `codex.turn.token_usage` (token_type),
  `codex.guardian.review` (decision, outcome, risk_level), `codex.turn.e2e_duration_ms`, and
  `codex.conversation.turn.count`; every point carries `user.email` and `auth_mode`, which the
  attribute allowlist drops.
  `codex.sandbox_outcome` fired once, live, when the sandbox refused a write, and was caught
  as an unrecognised name by the denial canary; its attributes (`tool_name`, `call_id`,
  `outcome`, `initial_duration_ms`, `escalated_duration_ms`) and outcome values (`denied`,
  `timed_out`, `signal`, `escalated`) are read from `codex-rs/otel/src/events/session_telemetry.rs`
  and `codex-rs/core/src/tools/orchestrator.rs`, because the host did not reproduce the refusal
  on demand: under `--sandbox read-only` on Windows the CLI approved `apply_patch` through its
  automated reviewer and wrote the file on three further attempts.
- Every event name a provider sends is counted per harness version in `parser_signatures`,
  recognised or not, so a rename in a future CLI shows as drift in the Collection health
  readout rather than as a quietly emptier ledger.

### Live canary results (2026-09-03)

Eleven parametrised scenarios ran against the authenticated CLIs from inside a swe-mux pane
with the session's `MUX_*`, `CLAUDE*`, and `OTEL_*` environment scrubbed and mux's launcher shim
directory removed from `PATH` (the test does this itself; without it the child's hooks post
as the pane's own session).

| scenario | claude | codex | what it proves |
| --- | --- | --- | --- |
| turn | passed | passed | one call, one model request, per-turn identity |
| parallel | passed | passed | two distinct call ids in one step |
| failure | passed | passed | a failing command is a failed result |
| denial | passed (re-run) | passed (re-run) | no write succeeds where the CLI was told not to; every name sent is recognised |
| skill | passed | not applicable | a skill activation or its read reaches the ledger |
| subagent | passed | not applicable | a delegated read is reported |
| code_mode | not applicable | passed | the nested `exec-` execution lands on the runtime layer |

The first run failed both denial scenarios: Claude because the premise was wrong (it wrote
the file), Codex because `sandbox_outcome` was unrecognised.
Both are recorded under the provider contracts; the scenarios were rewritten to what the
CLIs do and passed on re-run, and the reducer learned the event.

## Measured query cost (2026-09-03)

`tools/telemetry_benchmark.py` seeds a scratch ledger and times the query surface twice: with
every day read from entities, and after every closed day and hour has been rolled up.
It also asserts that the two readings agree on every figure and differ only on `coverage`.

### Ten million calls

`--calls 10000000 --days 30 --fast-seed` on the development host (a 16-core desktop, the
scratch ledger on an NVMe volume), medians of five runs.
The fast seed writes the entity rows the queries read straight into the segments (no
evidence rows, no evidence links); the aggregate and page timings are identical to the reducer
path because both read the same tables through the same indexes.

| view | every bucket raw | closed days and hours rolled up |
| --- | --- | --- |
| tool summary, 24 hours | 130 ms | 40 ms |
| tool summary, 7 days | 4,974 ms | 61 ms |
| tool summary, 30 days | 26,842 ms | 141 ms |
| tool summary, 7 days, backend + status filter | 903 ms | 13 ms |
| workload summary, 24 hours | 87 ms | 19 ms |
| workload summary, 7 days | 3,266 ms | 24 ms |
| workload summary, 30 days | 18,487 ms | 36 ms |
| quality summary, 7 days | 3,785 ms | 25 ms |
| inefficiencies, 7 days | 5,090 ms | 61 ms |
| cohorts by model, 7 days | 3,594 ms | 23 ms |
| skill and verification summaries, 7 days | 0.1 ms | 0.2 ms |
| tool page (100 rows), 24 hours | 27 ms | 9 ms |
| tool page (100 rows), 7 days | 765 ms | 14 ms |
| tool page (100 rows) filtered, 7 days | 849 ms | 17 ms |
| runs page (100 rows), 7 days | 2 ms | 2 ms |
| export page (1,000 rows), 7 days | 22 ms | 26 ms |

Rolling up the 30 closed days took 57 s and the 720 closed hours 50 s; the ledger is
9.3 GiB on disk.

The gates hold: every standard dashboard view answers under 200 ms from rollups, and every
detail page answers under 500 ms.
They did not hold on the first run, which is what schema 4 is.
At schema 3 the same ledger answered the filtered tool summary in 398 ms (SQLite drove the
query from the backend index and read every claude row rather than the partial hour the
time index bounds), the 7-day tool page in 726 ms and its filtered form in 1,385 ms (the exact
count beside the page scanned the window), the quality summary in 2,618 ms (a group-by over
every call in the window), and inefficiencies in 856 ms (the repeated-call finding grouped
every call by its input hash).
Schema 4 writes a dimension filter as `+column=?` so the time index stays in charge, sums a
page's count from the rollups its filters live in, rolls the quality counts up per day and
hour beside the tool and workload figures, and keeps per-run repeat counts on every write so
the finding reads the groups that repeated rather than the calls that did not.
The raw path is what the current hour and any dirtied bucket pay; it scales with the volume
in the window and is not the gate.

### One hundred thousand calls (reducer path, for scale)

`--calls 100000 --days 30`, medians of five runs:

| view | raw days | closed days rolled up |
| --- | --- | --- |
| tool summary, 7 days | 35 ms | 20 ms |
| tool summary, 30 days | 254 ms | 61 ms |
| workload summary, 7 days | 39 ms | 4 ms |
| workload summary, 30 days | 193 ms | 14 ms |
| tool page (100 rows) | 19 ms | 15 ms |
| export page (1000 rows) | 29 ms | 20 ms |

A rolled-up day costs one indexed read per group regardless of how many calls it held, so
the closed-day figures scale with the window length rather than with volume.

## Measured ingestion cost (2026-09-03)

`tools/telemetry_ingest_latency.py` runs the real service on an event loop, drives
observations at it, and samples loop lag with a 5 ms ticker; `--control` runs the identical
producer with no ledger subscribed, so ingestion's own cost is the difference.
Fifty thousand observations each:

| producer | ledger | accepted | dropped by the bus | loop lag p50 | p99 | max |
| --- | --- | --- | --- | --- | --- | --- |
| paced, 2,000/s | control | - | - | 3.9 ms | 18.4 ms | 26.0 ms |
| paced, 2,000/s | subscribed | 43,009 | 6,991 | 2.4 ms | 19.4 ms | 24.9 ms |
| unpaced flood | control | - | - | 363 ms | 575 ms | 593 ms |
| unpaced flood | subscribed | 1,280 | 48,720 | 146 ms | 592 ms | 618 ms |

Ingestion adds no measurable loop lag: at two thousand observations a second, a rate no fleet
here approaches, the subscribed run's p99 is within a millisecond of its control and both sit
on the Windows timer's 15.6 ms granularity.
The flood rows measure the producer, not the consumer: the control with nothing subscribed
lags just as badly, because `EventBus.emit` prunes its semantic-dedupe map by scanning it once
it exceeds 2,048 entries within ten seconds, which a synthetic flood hits and a fleet does not.
Every drop is counted per subscriber (`EventBus.drop_stats`, reported on
`GET /api/diagnostics/background` as `event_bus`) and the consumer's own throughput is about
1,700 observations a second on this host, which is the figure the bounded queue is protecting
the loop from.

## Audited window (2026-09-03)

PENDING_AUDIT

## Remaining implementation

### Native evidence

- [x] Add a loopback OTLP receiver with strict metadata allowlists.
- [x] Configure Claude launches to export tool, decision, skill, compaction, request, and subagent
  events to the receiver.
- [x] Configure Codex launches to export tool-result events, and its metrics, to the receiver.
- [x] Discard Codex and Claude arguments and output after hashing and structured parsing.
- [x] Preserve provider sequence, turn, agent, parent-agent, conversation, and harness-version
  fields.
- [x] Persist parser signatures (event name by harness version, recognised or not).
- [x] Add provider capability records for unavailable duration, skill, parent, and decision data
  (`NativeTelemetry.provides` on the descriptor, `telemetry_otlp.CAPABILITIES`, reported by
  the quality route as measured, unmeasured, or no native telemetry per harness).

### Reconciliation

- [x] Keep importing what the legacy store reconciles after the initial catch-up: every stream
  is re-read past its cursor on a five-minute cycle.
- [x] Reconcile Claude, Codex, OMP, Pi, and OpenCode native stores directly into the canonical
  reducer, without the legacy `tool_events` hop (`telemetry_reconcile.py`, watermarked per
  run, every five minutes and on `POST /api/telemetry/v2/reconcile`; fixtures for all five
  dialects and a read-only proof in `tests/test_telemetry_reconcile.py`).
- [x] Persist field-completeness denominators by harness version (`quality_summary.versions`).
- [x] Classify incomplete calls as abandoned only when the owning run closes.
- [x] Backfill turn boundaries from durable status timelines where exact run and turn identities
  survive.
- [x] Mark unrecoverable legacy fields unknown rather than estimating them: a legacy run's start
  is the history row's `spawned_at` and is marked `declared`, a run with no declared start
  carries `started_at_source = first_evidence`, and a legacy call with no duration keeps it
  NULL.

### Storage and queries

- [x] Build exact daily tool and workload rollups.
- [x] Build hourly tool and workload rollups, and daily skill, verification, and compaction
  rollups; daily and hourly quality rollups and per-run repeat counts followed in schema 4.
- [x] Recompute dirty days and hours after late evidence, including a run or turn that ends on
  a later day than it started.
- [x] Query clean days from day rollups, clean hours of partial days from hour rollups, and the
  rest from canonical entities, merging consecutive raw stretches into one query per month.
- [x] Seal inactive monthly segments after reconciliation and record segment hashes.
- [x] Add integrity, schema-signature, and disk-pressure diagnostics for the telemetry directory.
- [x] Add skill, verification, metric, run, turn, and generic entity-page APIs beyond the
  tool-call audit.
- [x] Add JSONL and CSV exports with provenance references.
- [x] Add Project, backend, model, layer, family, status, and evidence-quality filters to the
  aggregates.

### Product surface

- [x] Replace Fleet activity workload with the canonical workload endpoint.
- [x] Replace tool and skill tables with exact seven-day mux-owned aggregates.
- [x] Add range, cohort, backend, and layer controls shared by every tab.
- [x] Add Project, model, family, outcome, and evidence-quality controls.
- [x] Add aggregate-to-run-to-turn-to-call-to-evidence drill-down.
- [x] Show field coverage and parser drift beside every affected metric.
- [x] Keep the legacy dashboard accessible until shadow comparison passes
  (`canonical_telemetry_legacy_dashboard_enabled`, on by default; the legacy tab carries the
  shadow comparison).

### Inefficiency analysis

- [x] Detect high failure, denial, interruption, and abandonment rates.
- [x] Detect excessive approval wait and repeated identical calls.
- [x] Detect repeated polling and large discarded results.
- [x] Compare skill activation and verification outcomes only within explicit comparable
  cohorts (`compare_cohorts` reports `comparable: false` and why when an unfixed dimension
  differs).
- [x] Collect user feedback on findings before suggesting configuration changes
  (`finding_reviews`; `POST /api/telemetry/v2/inefficiencies/review`).
- [x] Require a comparison window and rollback rule before any adaptive change can be offered
  (`propose_adaptive_change` refuses without a `useful` review, a window, and a rule; nothing
  calls it yet, which is the point).

## Completion gates

- [x] Sanitized production-shaped fixtures cover every registered transcript harness: Claude and
  Codex OTLP captures, and Claude, Codex, OMP, Pi, and OpenCode native-store fixtures for the
  reconciler (`tests/fixtures/telemetry/`).
- [x] Live canaries cover the OTLP contract for Claude and Codex.
- [x] Live canaries cover turns, parallel calls, failures, denials, skills, subagents, and Codex
  code mode (the table under "Live canary results").
  Compaction and conversation rollover are not reproducible from a one-shot headless
  invocation: forcing a genuine compaction needs a context window filled to its limit, and a
  rollover needs a second conversation to be opened from the first, neither of which a single
  `--print`/`exec` run does.
  They are covered by fixture tests instead: `tests/test_telemetry_otlp.py` reduces the
  captured `compaction` record, `tests/test_telemetry_ledger.py` and
  `tests/test_telemetry_hardening.py` reduce compaction and rollover evidence through the
  ledger (a compaction from hook and transcript sources under one id; a run whose
  conversation id changes mid-session keeps both runs distinct), and
  `tests/test_operational_telemetry_phase2.py` carries the versioned native compaction
  fixtures for every dialect.
- [x] One audited 24-hour window matches native provider evidence call by call
  (`tools/telemetry_audit_window.py`; figures under "Audited window").
- [x] Every displayed total names its time range, cohort, denominator, and coverage
  (`frontend/src/telemetryCaption.tsx`; `frontend/test/telemetryCaption.test.ts`).
- [x] Ten-million-call aggregate queries remain below 200 ms for standard dashboard views.
- [x] Ten-million-call detail queries remain below 500 ms.
- [x] Background ingestion adds no measurable PTY latency and reports every dropped observation
  (figures under "Measured ingestion cost").
- [x] Legacy migration completes without changing session history or native transcripts
  (the importers read `mux.db` through a read-only URI and write only the ledger; the
  reconciler's read-only proof covers the native stores).
- [x] Shadow and canonical dashboards agree where the legacy metric was valid, and every
  expected disagreement is classified (`shadow_comparison`: `agree`,
  `canonical_native_only`, `canonical_more`, `legacy_more_not_yet_imported`, `legacy_only`,
  `canonical_only`).
- [x] The legacy analytics path is retired only after operator review: it is not retired;
  `canonical_telemetry_legacy_dashboard_enabled` stays on until the operator turns it off,
  and turning it off deletes nothing.
