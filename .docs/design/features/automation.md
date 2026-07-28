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

- The modal groups its views into three top-level tabs — **Configure** (rules & observers),
  **Attend** (attention · all-session health), **Review** (run notes · learned fixes) — with a
  secondary sub-tab row shown only when a group has more than one view. A `?` in the header
  opens a nested help modal (the how-it-works pipeline + glossary); Escape/focus-trap transfer
  to it while open.
- Configure is the complete effective inventory: built-in system observers plus canonical
  `rules.toml` rules, with an at-a-glance status strip (automation on/off, observer counts,
  spend today). Disabled built-ins remain visible.
- Each built-in row exposes trigger, bounded input slice, model tier, result destination, and
  owning config setting. Titler and summarizer toggle independently; stall, approval, and
  context observers share the `phase7_observers_enabled` attention setting.
- `Run notes` is the user-facing label for persisted annotations. `Attention` contains
  notification records that may require user action.
- Diagnostics is demoted out of the primary tab row to a header action: provider traces, event
  dry-run, queue state, firings/observer-call traces, and research-only injection evidence are
  developer-grade and sit beside daily surfaces no longer.
- Injection diagnostics use the provider-neutral `safe|blocked|unknown` delivery-readiness
  contract, expose bounded reasons/parser coverage, and are permanently unauthorized in this
  phase. They never write the PTY; see `delivery-readiness.md`.

## Built-ins and safety

Built-ins are an explicit-name-preserving session titler, one-line turn summarizer, stalled-run
triage, approval-request triage, and context-handoff suggestion. Duplicate hook/transcript
completion evidence is coalesced before completion-triggered calls.
The titler adapts a session's title continuously as the session progresses rather than emitting
a single label per run. It derives the title from the scan timeline where enabled (falling back
to bounded transcript slices otherwise), keying on the user's actual asks and the agent's salient
responses rather than interim tool activity, and recomputes only on a material shift (novelty
spike, work-phase or target change, new user request) with debounce and hysteresis so the title
neither flickers nor costs a call every turn. An explicit user rename pins the title and disables
auto-update for that session; the automation never overwrites a human-chosen name. Generated
titles are compact task labels for tabs/sidebar, without backend or “terminal session” prefixes:
the prompt targets 2-3 words and caps at 4, because the tab strip and sidebar rows are narrow
enough that a longer-but-equally-accurate title only buys an ellipsis.
Provider failure, invalid output, cancellation, queue pressure, or budget failure cannot block
or change the agent/PTY lifecycle. Canonical observers have no PTY write, approval,
worker/spawn, script, arbitrary HTTP, project-write, or relay path.

## Reliability and diagnostics

Ingest, rules-file watch, timer triggers and every worker run under the shared background-task
supervisor (`background_tasks.py`): one iteration's failure is counted and logged and the loop
keeps its cadence, and a loop that dies anyway is restarted with capped backoff. Health is at
`GET /api/diagnostics/background`.

The rules-file watcher treats an unreadable stat as "unchanged" rather than an error: editors
save by delete+rename, and one unlucky poll used to end hot reload for the daemon's lifetime.

`status()['queue']` reports `dropped` and `loop_rejections` as before, plus `worker_failures`
and `worker_last_error` — worker faults used to share the single `diagnostic` slot with
rules-file errors and were cleared by the next reload — and `bus`, the per-subscriber
event-bus drop counts. An event dropped before automation ever saw it is invisible to every
counter downstream of it, so it is attributed at the bus.

## Key files

- `src/swe_mux/automation.py`
- `src/swe_mux/automation_store.py`
- `src/swe_mux/background_tasks.py`
- `src/swe_mux/openrouter.py`
- `src/swe_mux/secret_store.py`
- `frontend/src/AutomationDashboard.tsx`
- `frontend/src/Settings.tsx`
