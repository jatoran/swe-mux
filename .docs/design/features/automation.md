# Universal hooks and OpenRouter observers

## What it is

Asynchronous, provider-neutral rules over persisted mux events. Deterministic actions and
bounded read-only observers annotate or notify without entering an agent's execution path.

## Contract

Canonical machine-owned `~/.mux/rules.toml` uses versioned `[[rule]]` entries with stable
IDs/revision hashes and `on`, `when`, and `do`. The complete file validates before atomic
replacement; malformed reloads retain the last-known-good rules. Repository
`.swe-mux/rules.toml` is parsed for diagnostics and never executes.

New rules can only `annotate`, provider-neutral `notify`, or call the `llm` observer action.
An LLM result is strict schema data and maps through the rule-authored `on_result`; it cannot
construct an action. Legacy `hooks.toml` keeps its former guarded actions in a separate
compatibility engine.

## Evaluation flow

```text
persisted mux event
  -> allowlisted normalized envelope + source confidence/capability
  -> bounded queue/worker
  -> trigger + condition trace + guards/checkpoints
  -> deterministic action OR normalized transcript slice
  -> fixed-origin OpenRouter JSON-schema call
  -> local schema validation
  -> annotation/notification + provenance/spend
```

Rules support debounce/coalescing, timer intervals, rate/quiet windows, thresholds with
hysteresis, annotation guards, shadow mode, history dry-run, idempotency by event/rule
revision, and chain-depth/same-rule loop rejection. Observer slices are bounded by message,
byte, token, and parse-time limits and include `last_turn`, `last_n_messages`, `since_event`,
`since_annotation`, and summary chains. Automatic calls require a live mux-owned agent run
and the declared minimum observation capability.

## OpenRouter and economics

- Only `https://openrouter.ai/api/v1` is accepted; redirects and caller URLs are impossible.
- Requests are non-streaming, strict JSON-schema, parameter-required, no-provider-fallback,
  bounded, timed out, cancellable, retried only for transient status, and locally validated.
- Settings provides write-only set/replace/test/clear key controls, exact cheap/standard
  model IDs, explicit model refresh, global/per-rule token+dollar budgets, hourly caps,
  concurrency, input/output limits, retention, and independent observer toggles.
- Windows persistent keys use current-user DPAPI in `automation.secrets.json`;
  `OPENROUTER_API_KEY` is the headless override. The key is never returned.
- The Automation dashboard exposes rules/shadow state, firings, traces, action/call results,
  annotations, provenance, cost, queue/degradation state, inbox, and no-side-effect dry-run.

## Control-plane presentation

- `Automations` is the complete effective inventory: built-in system observers plus canonical
  `rules.toml` rules. Disabled built-ins remain visible.
- Each built-in row exposes trigger, bounded input slice, model tier, result destination, and
  owning config setting. Titler and summarizer toggle independently; stall, approval, and
  context observers share the `phase7_observers_enabled` attention setting.
- `Run notes` is the user-facing label for persisted annotations. `Attention` contains
  notification records that may require user action.
- Provider traces, event dry-run, queue state, and research-only injection evidence live under
  Diagnostics rather than the primary workflows.
- Injection diagnostics use the provider-neutral `safe|blocked|unknown` delivery-readiness
  contract, expose bounded reasons/parser coverage, and are permanently unauthorized in this
  phase. They never write the PTY; see `delivery-readiness.md`.

## Built-ins and safety

Built-ins are an explicit-name-preserving session titler, one-line turn summarizer, stalled-run
triage, approval-request triage, and context-handoff suggestion. Duplicate hook/transcript
completion evidence is coalesced before completion-triggered calls.
The titler additionally reserves its run before provider I/O and checks durable annotations,
guaranteeing at most one paid title call per agent run even with concurrent workers. Generated
titles are compact task labels for tabs/sidebar, without backend or “terminal session” prefixes.
Provider failure, invalid output, cancellation, queue pressure, or budget failure cannot block
or change the agent/PTY lifecycle. Canonical observers have no PTY write, approval,
worker/spawn, script, arbitrary HTTP, project-write, or relay path.

## Key files

- `src/swe_mux/automation.py`
- `src/swe_mux/automation_store.py`
- `src/swe_mux/openrouter.py`
- `src/swe_mux/secret_store.py`
- `frontend/src/AutomationDashboard.tsx`
- `frontend/src/Settings.tsx`
