# Telemetry hardening

## Objective

Produce a complete, provenance-preserving activity ledger for auditing harness behavior over
time and within and across sessions.
The resulting analytics must expose collection completeness, support exact filtered totals, and
identify inefficiencies without claiming causation from correlation.

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
- [x] Versioned additive schema migrations (`telemetry_schema.py`, schema 2 on 2026-09-03) with
  per-file signatures and a drift readout in the daemon's background health.
- [x] Module split: schema and migrations, legacy importers, the query surface, the write path,
  and the daemon adapter are five files rather than one.

## Measured provider contracts (2026-09-03)

Both native exporters were exercised against the daemon's own ingress path from a loopback
receiver, and the payloads are committed with identities and content removed
(`tests/fixtures/telemetry/otlp-claude-2.1.259.json`, `otlp-codex-0.153.0.json`).
The live canary that re-measures them is `tests/test_live_otlp.py` (`live_agent` +
`live_telemetry`, gated on `SWEMUX_RUN_LIVE_PHASE2_TESTS=1`); it passed for both CLIs on the
day of the capture.

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
- Neither provider sends OTLP metrics to the logs endpoint.
  The `codex.skill.injected` reducer is unit-tested and stands ready; wiring a metrics
  exporter is a separate, unverified step and is deliberately not configured.
- Every event name a provider sends is counted per harness version in `parser_signatures`,
  recognised or not, so a rename in a future CLI shows as drift in the Collection health
  readout rather than as a quietly emptier ledger.

## Measured query cost (2026-09-03)

`tools/telemetry_benchmark.py --calls 100000 --days 30` on the development host, medians of
five runs:

| view | raw days | closed days rolled up |
| --- | --- | --- |
| tool summary, 7 days | 35 ms | 20 ms |
| tool summary, 30 days | 254 ms | 61 ms |
| workload summary, 7 days | 39 ms | 4 ms |
| workload summary, 30 days | 193 ms | 14 ms |
| tool page (100 rows) | 19 ms | 15 ms |
| export page (1000 rows) | 29 ms | 20 ms |

A rolled-up day costs one indexed read per group regardless of how many calls it held, so
the closed-day figures scale with the window length rather than with volume; the raw path is
what the current day and any day dirtied by late evidence pay.
The ten-million-call gate below is a statement about the rolled-up path and has not yet been
measured at that size.

## Remaining implementation

### Native evidence

- [x] Add a loopback OTLP receiver with strict metadata allowlists.
- [x] Configure Claude launches to export tool, decision, skill, compaction, request, and subagent
  events to the receiver.
- [x] Configure Codex launches to export tool-result events to the receiver.
- [x] Discard Codex and Claude arguments and output after hashing and structured parsing.
- [x] Preserve provider sequence, turn, agent, parent-agent, conversation, and harness-version
  fields.
- [x] Persist parser signatures (event name by harness version, recognised or not).
- [ ] Add provider capability records for unavailable duration, skill, parent, and decision data.

### Reconciliation

- [x] Keep importing what the legacy store reconciles after the initial catch-up: every stream
  is re-read past its cursor on a five-minute cycle, so a transcript the legacy reconciler
  parses later still reaches the ledger.
- [ ] Reconcile Claude, Codex, OMP, Pi, and OpenCode native stores directly into the canonical
  reducer, without the legacy `tool_events` hop.
- [ ] Persist field-completeness denominators by harness version (the per-backend quality view
  exists; the per-version split does not).
- [x] Classify incomplete calls as abandoned only when the owning run closes.
- [x] Backfill turn boundaries from durable status timelines where exact run and turn identities
  survive.
- [ ] Mark unrecoverable legacy fields unknown rather than estimating them.

### Storage and queries

- [x] Build exact daily tool and workload rollups.
- [ ] Build hourly rollups, and skill and verification rollups.
- [x] Recompute dirty days after late evidence, including a run or turn that ends on a later
  day than it started.
- [x] Query clean days from rollups and dirty days from canonical entities, merging consecutive
  raw days into one query per month.
- [x] Seal inactive monthly segments after reconciliation and record segment hashes.
- [x] Add integrity, schema-signature, and disk-pressure diagnostics for the telemetry directory.
- [ ] Add skill, verification, and audit-detail APIs beyond the tool-call audit.
- [x] Add JSONL and CSV exports with provenance references.
- [x] Add Project, backend, model, layer, family, and status filters to the aggregates.

### Product surface

- [x] Replace Fleet activity workload with the canonical workload endpoint.
- [x] Replace tool and skill tables with exact seven-day mux-owned aggregates.
- [x] Add range, cohort, backend, and layer controls shared by every tab.
- [ ] Add Project, model, family, outcome, and evidence-quality controls.
- [ ] Add aggregate-to-run-to-turn-to-call-to-evidence drill-down (call-to-evidence exists).
- [x] Show field coverage and parser drift beside every affected metric.
- [ ] Keep the legacy dashboard accessible until shadow comparison passes.

### Inefficiency analysis

- [x] Detect high failure, denial, interruption, and abandonment rates.
- [ ] Detect excessive approval wait, repeated no-progress actions.
- [x] Detect repeated polling and large discarded results.
- [ ] Compare skill activation and verification outcomes only within explicit comparable cohorts.
- [ ] Collect user feedback on findings before suggesting configuration changes.
- [ ] Require a comparison window and rollback rule before any adaptive change can be offered.

## Completion gates

- [ ] Sanitized production-shaped fixtures cover every registered transcript harness
  (Claude and Codex OTLP fixtures exist; OMP, Pi, and OpenCode have no native exporter).
- [x] Live canaries cover the OTLP contract for Claude and Codex.
- [ ] Live canaries cover turns, parallel calls, failures, denials, skills, subagents, compaction,
  rollover, and Codex code mode.
- [ ] One audited 24-hour window matches native provider evidence call by call.
- [ ] Every displayed total names its time range, cohort, denominator, and coverage.
- [ ] Ten-million-call aggregate queries remain below 200 ms for standard dashboard views.
- [ ] Ten-million-call detail queries remain below 500 ms.
- [ ] Background ingestion adds no measurable PTY latency and reports every dropped observation.
- [ ] Legacy migration completes without changing session history or native transcripts.
- [ ] Shadow and canonical dashboards agree where the legacy metric was valid, and every expected
  disagreement is classified.
- [ ] The legacy analytics path is retired only after operator review.
