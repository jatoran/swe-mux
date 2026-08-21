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
- Settings owns every install-wide switch and bound: the `automation_enabled` master switch and the `scan_timeline_enabled` gate (Settings → Automation, beside the budgets they govern), global/per-rule token and dollar budgets, hourly caps, concurrency, input/output limits, and retention. The OpenRouter key controls, exact cheap/standard model IDs, and model refresh are on Accounts.
- The Automation dashboard owns the rule corpus and the runtime: per-rule enable and shadow/live state for the built-in titler, the shared attention-observer group, and custom rules, plus the canonical `rules.toml` editor beside them. It shows the two global switches as read-only state with links into Settings, and never owns a second copy of a switch. The turn summarizer was retired in Phase 7.7; the scan timeline is the single behavioral-summary producer.
- Cheap and standard model controls are searchable comboboxes whose result popovers have a
  bounded scroll height on desktop and mobile.
- Windows persistent keys use current-user DPAPI in `automation.secrets.json`;
  `OPENROUTER_API_KEY` is the headless override. The key is never returned.
- The Automation dashboard exposes rules/shadow state, firings, traces, action/call results,
  annotations, provenance, cost, queue/degradation state, inbox, and no-side-effect dry-run.

## Control-plane presentation

- The modal draws five flat views — **rules & observers**, **projects**, **cost breakdown**,
  **learned fixes**, **diagnostics** — with no group rail above them. A `?` in the header opens
  a nested help modal (the how-it-works pipeline + glossary); Escape/focus-trap transfer to it
  while open.
  The panel's own frame is a column flex rather than a fixed grid template, because its child
  count varies by view: hard-coded row numbers fitted exactly one case and drew the status line
  over the first heading in the others.
- **The three surfaces split by charter**: Settings → Automation is global policy (every
  install-wide switch and bound), the Projects registry is participation (which automations one
  Project opted into), and this dashboard is rules and runtime. The previous line — enablement
  on the dashboard, configuration in Settings — was invisible to a user and inconsistent with
  itself (the scheduled-runs emergency stop already lived in Settings), so the two global
  switches moved to Settings → Automation and the dashboard's `Global switches` section is
  read-only state with a `SettingLink` per switch.
- **The `projects` view answers what runs where.** Nothing runs on a Project that did not opt
  in, and before this view that fact was invisible from the one surface named "Automation": a
  green dashboard with zero activity had no path to the explanation. It reads
  `GET /api/automation/projects` (one row per registered Project — enabled count, enabled
  labels, blocked count, and "nothing" for an opted-out Project, which is listed rather than
  omitted so silence reads as off, never as covered) and links each row to that Project's own
  settings. Read-only by design: the revision-checked per-Project editor stays the only write
  path, so this view can never race one that is open.
- **The `rules.toml` editor is the dashboard's, not Settings'.** A rule's definition, its
  live/shadow state, and the firings it produces are one object; the previous arrangement put
  the text in the Settings save transaction, where a stale copy held open could silently
  overwrite a rule toggled or edited on the dashboard. The editor loads
  `GET /api/automation/rules` on open, saves through validate-then-write
  (`PUT /api/automation/rules?validate=1`, then the write), and the rules text is no longer
  part of the Settings bundle or its Save.
- **Three views left, on one rule: the pipeline produces exactly two things, and each gets
  exactly one home.** An event becomes an attention item or a run note (the help panel says so
  in as many words), and this dashboard was drawing a second copy of both.
  Its `attention` view drew the same ranked inbox and the same records as the drawer's Alerts
  tab; its `notes` view drew the same `/api/annotations` table as Activity → Findings with a
  different filter, so a run note visible in one could be missing from the other. Both are
  **links** now, in a permanent `Read elsewhere` row rather than an empty-state hint — "where
  did the attention inbox go" is asked by someone looking at a full one somewhere else. Findings
  grew a source filter (deterministic / observer / all) to cover what this dashboard's copy
  showed.
  Its `health` view was three unrelated things under one name and was split three ways: the
  explainer of what the deterministic checks watch folded into the help panel, where every other
  explanation of this pipeline already lived; the observed-workload telemetry moved to
  Resources → Tokens, following the cost column that had already left it on that reasoning; and
  the away report moved to Alerts, which is the inbox it summarizes (`ui.md`).
  What is left is what only this dashboard can do: configure the pipeline, account for what it
  spent, run bounded knowledge batches, and show its own diagnostics.
- **The spend view is mirrored into Resources → Tokens as the same component**, not as a second
  view over one endpoint (`AutomationSpendView`). Both readings are legitimate and neither is
  the real one: from here you ask which rule burned this, and the rules are beside it; from
  Resources you ask what you are burning in total, and the other three meters are beside it.
  Re-implementing the markup to serve both would have reproduced exactly the drift removed
  above. It fetches `/api/automation/dashboard` itself, since a shared component owns its data.
- **Spend answers which automation costs what, not only what automation cost.** A single daily
  total cannot be acted on, because turning something off requires knowing which something.
  `GET /api/automation/dashboard` carries `spend_breakdown`, grouped from the same
  `automation_budget_ledger` that produces `spend_today`, so the rows add up to the headline
  exactly rather than approximating it from the truncated sample of recent calls. Rows rank by
  the 7-day window rather than by today, because the decision is about a habit rather than a
  day, and each carries its share of the window as a bar.
  Several features bill that budget without being rules — Scan timeline, Read aloud, Project
  card, attention narration, the Mux assistant, adaptive titles — so the daemon labels every row
  and tags it `observer`, `custom`, `feature`, or `retired` (billed under an id the page has no
  control for). Before this they were visible only inside the aggregate.
- **A row's `enabled` is read from the switch that governs it, and a spender missing from the
  table is the dangerous case.** `enabled` is what separates a live bill from spent history, so
  a feature row asserting `True` regardless told the reader to go turn off something already
  off. Worse, a spender absent from `FEATURE_SPENDERS` falls through to the `retired` default,
  which is indistinguishable from the truth and says the opposite of it: `builtin:assistant`
  shipped unlisted and Resources → Tokens described the assistant as `retired · off` while it
  was running. `tests/test_spend_label_matrix.py` closes that by discovery rather than by
  memory — every `builtin:` rule id in the source must have an entry, and every entry's
  `setting_key` must be a real boolean `Config` field, since `getattr(…, default=False)`
  otherwise swallows a typo into a permanent "off". The two id families are told apart by
  punctuation: automation's own rules use `builtin.` and are labelled from the live engine,
  feature spenders use `builtin:` and are labelled from the table.
- Observer spend and agent-model spend are drawn as two tables and are never summed: the first
  is a metered OpenRouter key billed per call, the second is subscription usage the harness only
  ever estimates. Adding them would produce a number that is true of nothing.
- Figures are formatted by magnitude rather than at one fixed precision. These tables mix
  `$0.0006` with `$8,600.75` and `2,269` tokens with `9.7B`, and a fixed four-decimal currency
  format was most of why every one of them truncated. Exact values move to the cell's `title`
  rather than being discarded, and a cost below `$0.0001` prints as `<$0.0001` rather than
  `$0.0000`, which reads as free.
- Every figures table uses one responsive pattern (`.data-table`): a real table while there is
  width for one, and one labelled card per row below 760 px, with each cell naming itself from
  `data-label`. The alternative — a `white-space:nowrap` table inside a horizontal scroller —
  is unreadable on a phone, because the column being read and the header naming it can never be
  on screen together.
- The status strip's `calls today` reads the ledger, like the two spend tiles beside it. It
  previously summed the lifetime observer-call status counts, which was neither today's figure
  nor a count of anything the reader had asked for.
- Rules & observers is the complete effective inventory: the read-only global-switch status row, built-in system observers, and canonical `rules.toml` rules with their editor, with an at-a-glance status strip for automation state, observer counts, and daily spend.
  Disabled controls and built-ins remain visible.
- Each built-in row exposes trigger, bounded input slice, model tier, result destination, and
  owning config setting. The titler toggles on `observer_titler_enabled`; stall, approval, and
  context observers share the `phase7_observers_enabled` attention setting.
- `Run notes` is the user-facing label for persisted annotations. `Attention` contains
  notification records that may require user action.
- Attention records are **dismissible from the surface that lists them** — the drawer's
  Alerts tab, which is now the only one — individually (`PATCH
  /api/automation/notifications/{id}`) or all at once (`PATCH /api/automation/notifications`,
  which returns how many open records it closed). Dismissing sets `read_at`; it deletes
  nothing, so the history stays readable and retention still owns removal. Records survive
  90 days (`automation_retention_days`), which is far longer than a surface with no clear
  can stay useful.
- Diagnostics is the last of the four views, and only that: it carried a header toggle *as
  well* while it was the one view outside the tab row, which left one of four choices with
  two controls and no way to tell which was authoritative. Provider traces, event dry-run,
  queue state, firings/observer-call traces, and research-only injection evidence are
  developer-grade, so it sits last rather than first.
- Injection diagnostics use the provider-neutral `safe|blocked|unknown` delivery-readiness
  contract, expose bounded reasons/parser coverage, and are permanently unauthorized in this
  phase. They never write the PTY; see `delivery-readiness.md`.

## Built-ins and safety

Built-ins are an explicit-name-preserving session titler, stalled-run triage, approval-request
triage, and context-handoff suggestion. Duplicate hook/transcript completion evidence is coalesced
before completion-triggered calls.

The one-line turn summarizer (`builtin.turn-summarizer`, `observer_summarizer_enabled`) was retired
in Phase 7.7. The scan timeline already carries every completed turn's `work_phase`, `intent`,
`summary`, `blockers`, and targets plus a deterministic novelty score, so it is the single
behavioral-summary producer now. Its former consumers - the stalled-triage `summary_chain` input,
the run-notes view, the away report, the handoff export, and the second-opinion prompt - read the
scan spine instead, and the `observer_summarizer_enabled` config field is gone (a config predating
its removal still loads, because `load_config` copies only known fields). Historical `turn-summary`
run notes stay readable; they are simply no longer produced.

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

**Adaptive titling (Phase 7.7, opt-in and off by default)** builds on that floor rather than
replacing it. When a Project opts into the `continuous_title` automation and the current run's scan
timeline is enabled, the title may *broaden* on a genuine scope pivot - a novelty spike coinciding
with a `work_phase`/`target` transition or a new `user_ask`, with debounce and hysteresis so it
never rewrites twice in quick succession and routine progress never moves it. The synthesis is one
cheap-model call written to under-do it: it is handed the current title and the recent scan records
and told to keep the current title unless the subject materially changed, so "no change" is the
common, cheap outcome and writes nothing. It is `auto_named`-only (an explicit rename still wins and
survives a rollover), `agent_run_id`-scoped, budget-guarded under `builtin:adaptive-title`, and
runs off a freshly saved scan record via `BehavioralConsumerService` (`behavioral_consumers.py`) -
never in the scan path's budget or latency, and a fault in it never breaks scanning. It shares one
pivot definition (`evaluate_pivot`) with the phase-transition signals below, so the two can never
disagree about what a pivot is. Its re-title count is surfaced in the scan snapshot's
`adaptive_title` field; a stable-subject run reads zero. With the consumer off (or the scan timeline
off), titling is exactly the one-shot behaviour above.

**Phase-transition signals (Phase 7.7, `phase_transitions`, off by default)** ride the same scan
record and the same pivot gate. On a genuine `work_phase` pivot they write a non-blocking
`phase-pivot` annotation ("session changed direction"); on a prolonged flat-novelty stall within one
phase they write a cheap-blocking `phase-stall` annotation ("stuck in debug for ~40 min"). Both feed
the attention pipeline through the ordinary annotation-ranking path (`attention-ranking.md`),
expressing states deterministic status detection cannot.
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
