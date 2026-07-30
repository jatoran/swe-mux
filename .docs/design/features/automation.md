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
- Attention records are **dismissible from every surface that lists them** — the drawer's
  Alerts tab as well as the dashboard inbox — individually (`PATCH
  /api/automation/notifications/{id}`) or all at once (`PATCH /api/automation/notifications`,
  which returns how many open records it closed). Dismissing sets `read_at`; it deletes
  nothing, so the history stays readable and retention still owns removal. Records survive
  90 days (`automation_retention_days`), which is far longer than a surface with no clear
  can stay useful.
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
**A run is titled once, from the request it opened with.** `builtin.session-titler-initial` fires
on `turn_started` and names the pane from that request; `builtin.session-titler` fires on
`turn_ended` and reads the completed turn instead, but **only for a run whose request was never
captured** — Codex has no prompt hook, and an agent adopted mid-conversation was never observed
being asked anything. With a request on hand the `turn_ended` stage stands down entirely, even
while the prompt-driven call is still pending or retrying. First title wins; nothing replaces one.

The two rule ids read backwards (`-initial` is the primary) because they kept their original
strings when the roles swapped, so existing annotations, user rules, and the `observer_titler_enabled`
setting still resolve.

Titling from the completed turn was the earlier design and it drifted badly in practice: the last
turn describes a step inside the session rather than the session, so a run that opened with "fix
the flaky login test" ended up titled `OK`, `FrozenClaude`, `Reply FROZENCODEX`. Retries made it
worse — the prompt slice originally read the session's *latest* request, so an attempt that landed
three turns late named the tab after whatever detour was in flight. The pinned first prompt is what
makes a retry, a restart, and the original attempt all produce the same name.

The prompt is pinned twice over. `Session.first_user_prompt` is set once per run from the hook
ingress (bounded, cleared on rollover), and the first observer to read it copies it into an
`automation_checkpoints` row keyed `run-prompt:<agent_run_id>` — because the daemon restarts on
every reload and redeploy while its sessions keep running, and the in-memory copy dies with it.
Without that, a post-restart retry would fall back to the newest prompt and rename the tab.

Reading the prompt rather than the transcript is also what keeps titling working when observation
degrades: it is the one observer input that needs neither a transcript on disk nor semantic
observation. The action declares `minimum_capability = "telemetry"`; every other observer keeps the
`semantic` default.

**A title lost to the provider is retried in the background**, at 30s / 2min / 5min, up to three
attempts (`TITLE_RETRY_DELAYS_SECONDS`). Only `OpenRouterError` qualifies: budget exhaustion, a
degraded transcript, and a missing prompt are decisions a retry would reach again unchanged. Each
retry re-enters `evaluate` through the normal guards, so a run that ended, rolled over, or got
titled by the other stage simply stops. Retry firings carry a negative `event_seq` because firings
are unique on `(event_seq, rule_id, rule_revision)`; negatives can never collide with the event
bus's own. This exists because the previous behaviour was to wait for the next turn boundary, and
an idle pane has none — sessions were measured sitting nameless for 20+ minutes after a burst of
HTTP 429, which was 20 of 70 titler calls in a day. The provider client's backoff was widened at
the same time (`openrouter.py`: 5 attempts, equal-jitter exponential, `Retry-After` honoured and
capped); the two are complements, not alternatives — jittered backoff absorbs the burst, the
scheduled retry covers an outage that outlasts it.

A conversation rollover (an in-CLI `/clear` or `/new` — `backends.md`) always retitles, because it
mints a new `agent_run_id` and the existing title describes work the conversation no longer
contains. An explicit user rename pins the title and disables auto-update for that session, and the
pin is a property of the session, so it survives a rollover too — a human who named a tab did not
un-name it by clearing the conversation. The automation never overwrites a human-chosen name.
Generated titles are compact task labels for tabs/sidebar, without backend or “terminal session”
prefixes: the prompt targets 2-3 words and caps at 4, because the tab strip and sidebar rows are
narrow enough that a longer-but-equally-accurate title only buys an ellipsis.

The continuous titler once planned in `../../development/CONTROL_PLANE_ROADMAP.md` §6.11 —
recomputing on a material shift, with debounce and hysteresis — is **not being built**. Its premise
was that a title should track the work; the observed failure is the opposite one, that a title
which moves stops being a handle the user can find a tab by.
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
