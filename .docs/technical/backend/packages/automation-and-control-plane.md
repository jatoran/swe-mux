# Backend: automation, detectors, scanning, attention, and budgets

Index: `../packages.md`.
Design: `../../../design/features/automation.md`, `../../../design/features/tier0-facts.md`, `../../../design/features/deterministic-consumers.md`, `../../../design/features/scan-timeline.md`, `../../../design/features/attention-ranking.md`, `../../../design/features/budgets.md`, `../../../design/features/automation-enablement.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

**Detectors and observers are different tiers and must not blur.**
Anything under `deterministic_consumers.py` is a query over Tier 0 facts: no model call, no spend, no transcript interpretation beyond a literal claim pattern, and no output but annotations.
A finding carries the *set* of facts it rests on, because a single event pointer cannot express "this repeated three times and nothing moved".

## Rules and enablement

### `automation.py`, `automation_store.py`

Bounded rule evaluation and observer lifecycle, including provisional and settled title state, retries, budgets, and append-only annotations.
Scan-record queries run their time, trigger, and semantic predicates (`json_extract`) in SQL, so a bounded page means rows *returned* rather than rows scanned.

**Not:** PTY writes, provider transcript mutation, or browser presentation.

### `automation_registry.py`

The control-plane enablement DAG: substrate and consumer deps, cycle-checked resolution, whether an entry `spends`, whether it `needs_llm`, and the model-free starting set a new Project is offered.
`resolve(..., llm_ready=False)` moves the `needs_llm` entries out of `enabled` into `unverified` - its own field, not a `blocked` entry, because `blocked` values are ids a grant can switch on and no automation's enabling fixes an unproven endpoint.
`resolve(..., global_allow=...)` subtracts the install-wide ceiling *with* each blocked id's dependents into `globally_disabled` - deliberately unlike `llm_ready`, because the ceiling is a standing operator decision rather than an outage. `effective_global_allow` composes the `automation_global_allow` map with `scan_timeline_enabled`, and `DEDICATED_INSTALL_SWITCHES` names the three ids whose ceiling is a dedicated Config switch and therefore may never appear in the map.

**Not:** storage, execution, or knowing whether a provider is proven or what the ceiling holds - both are the caller's answers to pass in.

## Facts and detectors

### `tier0_store.py`

Deterministic no-model fact capture (the Tier 0 substrate), gated per-project, with source pointers, run and project fact queries, and targeted project write-fact windows for commit attribution.

**Not:** model calls, or actuation.

### `deterministic_consumers.py`

Model-free detectors over Tier 0 - loop and stall, declared-versus-verified, doc debt, provenance edges - and the turn-boundary runner.

**Not:** model calls, spend, or anything that writes toward a session.

## Scanning

### `scan_timeline.py`

The three-gated, run-scoped Tier 1 scanner.

- Event and heartbeat scheduling, both supervised loops.
- Forward (oldest-first) transcript windows with catch-up chaining.
  The window is forward rather than newest-first **because it advances a cursor**: a window that trims its own front and then moves the cursor past the trimmed messages loses them permanently and reports nothing.
- Same-run continuity carrying the run-level verdict forward (`CONTINUITY_FIELDS`, prompt v4), user Project context, and Tier 0 facts.
- Cancellable and durably tracked full-session scans.
- Strict DeepSeek V4 Flash extraction with repair-and-retry; a response with no usable semantic content writes no record even after its one retry.
- Rollover boundaries, which end the grant and every run-local comparison before a successor can scan.
- Its own daily, run, and hourly budget enforcement, exact-interval source rehydration, dead-end annotation candidates, and the stored-trigger vocabulary (`STORED_TRIGGERS`, wider than the event-bus set).
- `liveness()`, the one owner of the enablement and liveness block that the drawer snapshot and the `scan_timeline` MCP read both serve, including for an ended session.
  A scanner stopped by a budget cap and a quiet session both return an empty tail, and two implementations would eventually disagree about which one you are looking at.

Being a continuous-cost substrate is why it has three gates rather than one - global master, Project DAG permission, and an off-by-default grant on the exact current `agent_run_id` - and why it does **not** share the `automation_rule_*` caps.
Those bound an observer that fires once per session; charging a sampler to them stopped the feature after ten calls costing under half a cent, while its dollar budget sat at 0.2% used.

**Not:** PTY writes, attention ranking, cross-run continuity, guessed records when the provider fails, the per-rule episodic-observer caps, or record projection and digest shaping (`scan_consumers.py`).

### `scan_consumers.py`

Pure model-free derivations over the scan spine.

- Phase segments and the live-blocker streak.
- The bounded `catch_me_up` digest: most-recent segments, length-capped lines, and a count of what each bound dropped.
- The distilled-record search and the compact `project_record` projection.
- `repaired_fields`, which classifies the repair strings `_validate_semantics` writes, so a cosmetic `behavior` dedup is distinguishable from an enum fallback.

**Not:** any read of its own, since every function takes records in; model calls; storage; or transport.

## Attention

### `attention_ranking.py`

Incident grouping over detector findings and fleet fault events, the four cost-to-resolve channels, the hard daily interrupt budget and its hourly burst limiter, live-run-only ranking, suppression records, breakpoint draining, fan-out and resumption-lag telemetry, behaviour-mined demotion rules, and the absence digest.

It owns routing and nothing else: it reads findings that already exist (annotations from `deterministic_consumers.py`, fault events from `fleet_intelligence.py`) and never detects anything itself, so a detector change lands in one place and a routing change lands in another.
Its budget accounting is per incident, which is why the store's `incident_key` upsert refuses to re-route an existing incident: the merge path is what keeps three detectors describing one stuck run from spending three interrupt slots.

**Not:** detection itself, any session write, push or device routing, or model calls (`attention_narration.py`).

### `attention_narration.py`

One budgeted cheap-model "why" per ranked incident over a normalized single-run slice, with typed failure statuses.
It is the only model tier over ranked items and is separable by construction: it is passed in, it returns a status alongside its text, and every failure status leaves the deterministic item untouched.
Nothing in ranking waits on it.

**Not:** ranking, routing, evidence, or anything that survives its own failure.

## Spend and endpoints

### `budget.py`

The one spending-cap shape and the only two comparisons that enforce it: `Budget{tokens?, usd?, mode}`, which axes a mode enforces, `spent_out` (inclusive) and `would_exceed` (strict preflight), the `BudgetVerdict` sentence a surface renders verbatim, and `cost_blind` - the statement that a dollar axis cannot bind over calls whose provider reported no cost.

Absent cost is unknown, never zero, so the ledger records it as unmeasured (`cost_known`), every total drawn over it reads as a floor, and the token axis is the honest backstop.
Rate limits and per-call ceilings are deliberately *not* budgets: they count acts and bound one request, never a period's spend.

**Not:** reading config (`config.py` owns the fields, their bounds, and the migration), reading the ledger (callers pass a `spend()` row in), or deciding what an exhausted budget *does* to a feature.

### `llm_endpoint.py`

Which language-model endpoint the install talks to, and whether it is proven.

- The `LlmEndpoint` descriptor: origin, secret name, single-model override, cache policy, and what OpenRouter-specific behaviour does or does not apply.
- `EndpointCapabilities`, the measured record those flags are derived from - catalog shape, whether cost is reported, whether cache detail is reported.
- `CapabilityStore`, the live cache the per-request endpoint resolver reads.
  It exists because resolution is synchronous while the durable record is in SQLite behind an async store, and a verification has to take effect on the next call rather than the next restart.
- `capabilities_of_record`, which parses the stored column and is total.
  An absent, empty, or unparseable value is the unproven default, never an exception on the readiness path.
- Base-URL and model validation.
- The fingerprint over the whole `{base_url, model, api_key}` triple, which makes an edit un-verify the endpoint by construction.
- The `readiness` verdict, with the sentence a surface renders verbatim.

**Not:** HTTP (`openrouter.py`), storage (`automation_store.py`), reading config files, or deciding which *features* a verdict gates.

### `grants.py`

What a gate may switch on and what it must never touch: the two closed allowlists (validated against `Config` and `PROJECT_CONFIG_FIELDS` at import), the additive-only rule, and the validated plan `server.apply_grants` applies.

Turning something **off** is deliberately absent - that is the owning editor's - which is what lets many surfaces grant one switch.
Every grantable Project field must have a control in the Projects registry, pinned by `frontend/test/settingTargets.test.ts`, because a field enforced and reachable only by hand-editing a committed TOML file is the failure that test exists to prevent.

**Not:** storage, execution, or network.
