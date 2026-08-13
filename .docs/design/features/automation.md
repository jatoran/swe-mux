# Universal hooks and OpenRouter observers

## What it is

Asynchronous, harness-neutral rules over normalized persisted mux events.
Deterministic actions and bounded read-only observers annotate or notify without entering an agent's execution path.
Eligible event and transcript surfaces come from harness descriptor capabilities rather than a fixed provider list.

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
- Requests are non-streaming, strict JSON-schema, parameter-required, bounded, timed out,
  cancellable, retried for transient transport failures, and locally validated.
- Routing may fall back between providers for the exact requested model when the provider
  supports the required schema parameters.
- Settings provides write-only set/replace/test/clear key controls, exact cheap/standard model IDs, explicit model refresh, global/per-rule token and dollar budgets, hourly caps, concurrency, input/output limits, retention, and advanced rule configuration.
- The Automation dashboard is the single enablement surface for the automation engine, Scan timeline, built-in titler and summarizer, the shared attention-observer group, and custom rules.
- Cheap and standard model controls are searchable comboboxes whose result popovers have a
  bounded scroll height on desktop and mobile.
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
- Configure is the complete effective inventory: global controls, built-in system observers, and canonical `rules.toml` rules, with an at-a-glance status strip for automation state, observer counts, and daily spend.
  Disabled controls and built-ins remain visible.
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

The scan timeline shares the OpenRouter transport, observer-call audit, and spend ledger but is
not a user-authored rule.
It has a separate three-gate and run-lifetime contract in `scan-timeline.md`, and its provider
output can only become a scan record or an explicitly enabled dead-end annotation.
`builtin.session-titler-initial` fires on an observed user request (`turn_started` or
`transcript_message`) and gets a repair opportunity at `turn_ended` if the prompt arrived after
the opening trigger. Its `title_v2` result includes `stability=provisional|settled`. A concrete
request settles immediately. A setup-only opener such as “review/learn this repository” may be
provisional, then recomputed when a later request reveals the real task. Automatic work is bounded
to the first three distinct requests and at most three provider calls; a settled result freezes.
Every replacement is append-only, and the newest title annotation is the displayed title.

`builtin.session-titler` still fires on `turn_ended` and reads the completed turn, but only for a
run whose request was never captured, such as an adopted session or a Codex run whose hooks were
unavailable and whose prompt record was missed. With a request on hand the fallback stands down
while the prompt-driven retry ladder remains active; once that ladder is exhausted, it may provide
a weak title rather than leave the backend placeholder.

The two rule ids read backwards (`-initial` is the primary) because they kept their original
strings when the roles swapped, so existing annotations, user rules, and the `observer_titler_enabled`
setting still resolve.

Titling from the completed turn was the earlier design and it drifted badly in practice: the last
turn describes a step inside the session rather than the session, so a run that opened with "fix
the flaky login test" ended up titled `OK`, `FrozenClaude`, `Reply FROZENCODEX`. Retries made it
worse when they silently switched to whatever request was newest. Every provider attempt now pins
its active request context; the bounded provisional recomputation is the only path that
intentionally incorporates a later prompt.

Prompt state is durable twice over. `Session.first_user_prompt` and `last_user_prompt` are updated
from authenticated hook and transcript evidence, and the observer stores the first three distinct
requests plus the latest in `automation_checkpoints` at `run-prompt:<agent_run_id>`. A provider
retry also pins the exact active input in `title-state:<agent_run_id>`, so a daemon restart or a
later request cannot change the question an in-flight retry is answering.

Reading the prompt rather than the transcript is also what keeps titling working when observation
degrades: it is the one observer input that needs neither a transcript on disk nor semantic
observation. The action declares `minimum_capability = "telemetry"`; every other observer keeps the
`semantic` default.

**A title lost to the provider is retried in the background**, at 30s / 2min / 5min / 15min /
45min / 90min (`TITLE_RETRY_DELAYS_SECONDS`), a horizon of a little over two hours. Only a
*retryable* `OpenRouterError` qualifies, including a rate limit, an upstream fault, or a malformed
HTTP 200 structured response that may succeed on another provider attempt.
A rejected key fails identically forever, and budget exhaustion, a degraded transcript, and a
missing prompt are decisions a retry would reach again unchanged. Each retry re-enters `evaluate`
through the normal guards, so a run that ended, rolled over, or got titled by the other stage
simply stops, and its row is dropped rather than re-swept. Retry firings carry a negative
`event_seq` because firings are unique on `(event_seq, rule_id, rule_revision)`; negatives can
never collide with the event bus's own.

The pending attempt lives in an `automation_checkpoints` row keyed
`title-retry:<rule_id>:<agent_run_id>`, holding the serialized event, the attempt number and a
`due_at`; the automation interval loop sweeps due rows every 5s. It is deliberately *not* an
`asyncio.sleep` task: the horizon is longer than this daemon's uptime between reloads and
redeploys, so the successor process has to be the one that finishes the job. A ladder that runs
out marks its row `exhausted` rather than deleting it — that marker is what releases the
`turn_ended` fallback, and `status().queue.title_retries` reports pending and exhausted counts so
"this pane will never get a name" is visible rather than inferred.

**The last attempt switches model** (`cheap` → `standard`, only if they differ). A whole model's
provider pool can be rate-limited at once, and then no amount of waiting on that model helps.

This exists because the previous behaviour was to wait for the next turn boundary, and an idle
pane has none — sessions were measured sitting nameless for 20+ minutes after a burst of HTTP 429,
which was 20 of 70 titler calls in a day. The 30s/2m/5m ladder that replaced it was then measured
too short: on 2026-07-31 an upstream outage lasted the whole day, every ladder gave up after eight
minutes, and every session opened that day stayed nameless.

The provider client is the other half. `openrouter.py` retries in-call (5 attempts, equal-jitter
exponential, `Retry-After` honoured and capped) and, critically, **allows provider fallbacks**:
`complete_json` sends `provider.require_parameters = true` — which is the guarantee that matters,
restricting routing to providers that honour `response_format` — but no longer pins
`allow_fallbacks = false`. Pinning bought nothing and turned one provider's bad hour into a total
outage; that is the literal 2026-07-31 failure, where DeepInfra refused every call for the cheap
model while five other providers served it. There is no fallback to a different *model* at this
layer, so the answer's quality cannot silently change — only which host produced it. The layers
are complements: jittered backoff absorbs a burst, fallbacks route around one sick provider, the
scheduled retry covers an outage that outlasts both, and the model switch covers a pool-wide one.

OpenRouter model metadata is retained with the cached model catalog and loaded into the provider client at daemon startup.
`complete_json` uses each model's advertised token-limit and reasoning capabilities instead of sending one universal parameter set.
It never sends `temperature`, because sampling controls are optional for these deterministic schema calls and some reasoning endpoints reject that parameter entirely.
It prefers `max_completion_tokens` when advertised, falls back to `max_tokens`, and keeps a token limit on every attempt.
Title actions request `reasoning.effort=none` only when the model advertises a non-mandatory reasoning control that permits `none`; non-reasoning and mandatory-reasoning models omit that control.

An exact OpenRouter parameter-compatibility 404 may advance to the next bounded profile, such as omitting an optional reasoning control or changing the token-limit field.
No compatibility profile changes the exact model, removes strict `response_format`, removes `provider.require_parameters`, or makes output unbounded.
Other 404s do not mutate the request: an unknown model remains a terminal configuration fault, while OpenRouter's explicit no-provider-available response is retryable by the title ladder.
Models absent from a stale catalog use the same bounded profile sequence, so a newly configured exact model is not coupled to catalog refresh timing.
The selectable catalog still excludes non-text models and models without structured-output support because every current swe-mux OpenRouter consumer requires a strict JSON object.
Malformed, empty, non-object, and schema-invalid structured responses are retryable title faults.
Observer-call rows retain only safe response diagnostics: generation and resolved model, provider,
finish reason, HTTP status, token and cost usage, response content type and length, and retryability.
The response content itself is not retained.

**Observer input is scrubbed before it is hashed, measured, or sent.** Slice construction
and `complete_json` both run text through `text_safety.utf8_safe`, because a lone surrogate
anywhere in the input makes the whole slice unserializable and `json.dumps(...,
ensure_ascii=False).encode()` raises `UnicodeEncodeError` — a `ValueError`, so it was caught
as an observer fault, reported at a byte offset inside a JSON blob, and never retried
(correctly: retrying would fail identically). The source of those surrogates was the hook
shim decoding UTF-8 with the Windows code page, fixed at that boundary too — `backends.md`
has the byte-level account. Both layers are kept: the shim fix stops new corruption, and the
scrub means no future bad byte from a transcript, a paste, or a CLI can cost a run its name.

`OpenRouterError` carries `status`, `retryable` and `retry_after`.
For statuses that describe the far side's health (`RETRY_STATUSES`) and safe routing/parameter failures, the message also carries the provider's own explanation from `error.metadata.raw` plus `provider_name`.
That detail is required to distinguish incompatible parameters, an unknown model, and temporary provider unavailability instead of collapsing all three into "HTTP 404".
An auth failure's body is still never echoed because it can quote the rejected credential back, and key-shaped text is scrubbed regardless, since these strings land in the firings table and on the status surface.

A conversation rollover (an in-CLI `/clear` or `/new` — `backends.md`) always retitles, because it
mints a new `agent_run_id` and the existing title describes work the conversation no longer
contains. An explicit user rename pins the title and disables auto-update for that session, and the
pin is a property of the session, so it survives a rollover too — a human who named a tab did not
un-name it by clearing the conversation. The automation never overwrites a human-chosen name.
An auto-named live agent also exposes **Regenerate title** in its session menu and command palette.
That explicit action uses the latest observed request, bypasses the automatic provisional/call
guards, and records a settled replacement; it remains subject to provider availability and the
normal observer budgets.
Generated titles are compact task labels for tabs/sidebar, without backend or “terminal session”
prefixes: the prompt targets 2-3 words and caps at 4, because the tab strip and sidebar rows are
narrow enough that a longer-but-equally-accurate title only buys an ellipsis.

The bounded provisional pass is deliberately not a continuous “track the current work” titler.
After a concrete title settles, later turns do not move it automatically. This keeps the title a
stable navigation handle while fixing the specific setup-command failure.
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
