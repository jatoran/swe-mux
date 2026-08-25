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

- The origin is **install configuration, never a caller parameter**. It was a module constant
  until Phase 15's bring-your-own endpoint; the substitution that replaced it is `LlmEndpoint`
  (`llm_endpoint.py`), resolved from `Config`, and no method on `OpenRouterClient` accepts a URL.
  Redirects are still refused. An agent, an MCP tool, or an HTTP body cannot reach a destination
  the operator did not type into Settings, which is the property the constant was protecting and
  the reason agent-chosen network destinations stay on the decision-gated list.
- Two endpoints exist. `openrouter` is the default and is unchanged for anyone who never opens
  the provider screen. `custom` is any OpenAI-compatible `/chat/completions` - llama.cpp, Ollama,
  vLLM, LM Studio with one shape - described by `{base_url, api_key, model}` (`ui.md` for the
  surface). The endpoint is re-resolved per request rather than fixed at daemon start, so a
  corrected base URL takes effect on the next call, which is the verify press itself.
- Three things are deliberately **not** inferred for a custom endpoint, because each would be a
  silent wrong answer rather than a loud failure:
  - **Cost.** `usage.cost` absent means unknown, never zero, and `/generation` is not asked at
    all - it is an OpenRouter accounting API, not part of the OpenAI-compatible surface.
  - **Routing.** `provider: {require_parameters, allow_fallbacks}` is OpenRouter's vocabulary for
    choosing between hosts of one model. A single-origin server has no hosts to choose between
    and never made that promise, so the block is omitted.
  - **Caching.** This is the one that would fail invisibly. `marks_cache_breakpoints` gates on the
    endpoint, not the model: OpenRouter translates a cache marker into whatever the routed
    provider understands, and a custom server has nothing in front of it doing that - it may also
    legitimately serve a model called `anthropic/claude-sonnet-4.5` and reject the marker. A
    custom endpoint's policy is `unknown`: no breakpoint is sent, no sticky-routing `session_id`
    is sent (an unknown field is exactly what a strict local server refuses), and no implicit hit
    is assumed either, so a zero in the ledger reads as unmeasured rather than as a caching
    regression somebody should investigate.
- A custom endpoint serves **one** model, and every model setting in the app names an OpenRouter
  id it has never heard of, so the client redirects all of them to `custom_llm_model` at the seam.
  That is what lets the assistant, the scan timeline, and the titler work against a local model
  without any of them learning about providers; Settings → Accounts says so above the index rather
  than letting it list ids nothing will request.
- A custom endpoint with no key sends no `Authorization` header at all: llama.cpp and Ollama serve
  unauthenticated, and demanding a placeholder would make the commonest local setup fail with a
  message about a credential the server does not want. OpenRouter still refuses without one.
- **Verification** proves an endpoint with one tiny plain completion and records it durably
  (`llm_provider_verification`, keyed by provider). The stored fingerprint is a digest of the
  whole triple - base URL, model, key - so editing any part of the endpoint un-verifies it *by
  construction* rather than by every write path remembering to, including an edit made by hand
  in `config.toml` while the daemon was down. Nothing key-shaped is recoverable from the digest.
  A record that no longer matches is kept and reported as `stale`, because "you changed it" and
  "you never did it" need different next steps. A failed verification records nothing and does
  not disprove a previous success: an endpoint that worked yesterday and is unreachable this
  minute has not been disproven, and deleting the record would turn a network blip into a
  Project-wide switch-off.
  OpenRouter requires no separate verification - storing its key already tests it against an
  origin swe-mux ships, so configuring it *is* verifying it, and demanding a second act would
  switch off every existing install's model-backed automations on upgrade.
- What an unverified provider gates, and how it is stated rather than failing downstream:
  `automation-enablement.md`.
- Requests are non-streaming, strict JSON-schema, parameter-required, bounded, timed out,
  cancellable, retried for transient transport failures, and locally validated.
- Routing may fall back between providers for the exact requested model when the provider
  supports the required schema parameters.
- Settings owns every install-wide switch and bound: the `automation_enabled` master switch and the `scan_timeline_enabled` gate (Settings → Automation, beside the budgets they govern), the global, per-rule, scan-timeline, and Project-card spending budgets, hourly caps, concurrency, the queue depth, input/output limits, and retention. Each spending budget carries the shared `{tokens?, usd?, mode}` shape and is edited through one control; `design/features/budgets.md` owns that contract. The OpenRouter key controls, exact cheap/standard model IDs, and model refresh are on Accounts.
  `automation_queue_size` is restart-scoped and says so on its own control, because the queue is allocated once at daemon start; the Project context card's model and its two per-build token ceilings sit under the same heading as the budget that bounds it, since a feature is configured in one pass (`design/features/project-card.md`).
- The Automation dashboard owns the rule corpus and the runtime: per-rule enable and shadow/live state for the built-in titler, the shared attention-observer group, and custom rules, plus the canonical `rules.toml` editor beside them. It shows the two global switches as read-only state with links into Settings, and never owns a second copy of a switch. The turn summarizer was retired in Phase 7.7; the scan timeline is the single behavioral-summary producer.
- Cheap and standard model controls are searchable comboboxes whose result popovers have a
  bounded scroll height on desktop and mobile.
- Windows persistent keys use current-user DPAPI in `automation.secrets.json`;
  `OPENROUTER_API_KEY` is the headless override, and `SWE_MUX_CUSTOM_LLM_API_KEY` is the same
  override for a custom endpoint. Neither key is ever returned.
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
  explanation of this pipeline already lived; the observed-workload telemetry moved out,
  following the cost column that had already left it on that reasoning, and now lives in
  Resources → Fleet activity; and the away report moved to Alerts, which is the inbox it
  summarizes (`ui.md`).
  What is left is what only this dashboard can do: configure the pipeline, account for what it
  spent, run bounded knowledge batches, and show its own diagnostics.
- **The spend view is mirrored into Usage → Automation as the same component**, not as a second
  view over one endpoint (`AutomationSpendView`). Both readings are legitimate and neither is
  the real one: from here you ask which rule burned this, and the rules are beside it; from
  Usage you ask what you are burning in total, and the other two pots are beside it.
  Re-implementing the markup to serve both would have reproduced exactly the drift removed
  above. It fetches `/api/automation/dashboard` itself, since a shared component owns its data.
- **Its agent-model table is a subset and says so.** `provider_cost_dimensions` covers only
  runs swe-mux observed, while Usage → Agents reads ccusage over every transcript the harness
  wrote - the same pot, two denominators. Drawn as a bare total beside the observer total it
  read as a second, competing answer to "what did the agents cost", which is the
  one-number-under-two-names failure the shared component exists to prevent. It is therefore
  labelled by its denominator in its tile, its heading, and its table foot, and the observer
  and agent figures are never summed (`usage.md`).
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
- **The prompt-cache hit rate is measured beside the spend it explains.** Every ledger row
  carries `cached_tokens` alongside its input and output counts, so the breakdown and the
  headline both answer "how much of this bill was a repeat of the last one". Cached tokens are
  a *subset* of the prompt tokens, never added to them: they were sent and counted, only billed
  at a discount, and summing the two would inflate every token figure on the page by exactly
  the amount caching saved. The denominator is prompt tokens rather than the `tokens` total,
  since output is not cacheable and including it would cap the achievable rate below 100% by an
  amount that varies with reply length.
  A dash and `0%` are different answers and the view keeps them apart: a dash means no billed
  prompt tokens in the window, which is what an unused rule and a database predating the column
  both look like, while `0%` means tokens were billed and none of them were cached - the
  actionable reading. One honest limit: a provider that caches implicitly and reports no
  `cached_tokens` also reads as `0%`, because the ledger records what the usage payload said
  and nothing more (`assistant.md` for where the breakpoint that earns a nonzero rate is
  placed).
  Beside the rate sits what caching did to the *bill*, which the rate cannot say: the write
  count and a signed dollar figure. That figure has two provenances and the view keeps them
  apart, because they are not equally authoritative: `cache_discount_usd` is the provider's own
  reading and is currently absent on every completion, while `cache_saving_usd` is derived from
  the token counts and the catalog's published read and write prices. A run writing a cache on every call and reading it
  on none reports 0% and costs 25% more per prompt token than not caching at all, because
  GPT-5.6 and Anthropic bill a write at 1.25x input - so a negative discount is flagged as the
  placement bug it is rather than averaged into a hit rate.
- **A cost nobody reported is recorded as unmeasured, never as zero.** `cost_known` is the
  column that says so, and `unpriced_calls` is what the spend rows carry beside the money. A
  bring-your-own endpoint reports no `usage.cost` at all, so writing those calls in at `$0.00`
  would leave every dollar figure on the page - and every dollar *cap* reading the same ledger -
  looking correct while approaching nothing. Any total drawn from a window containing unpriced
  calls is prefixed as a floor and names the count. Existing rows backfill to `cost_known = 1`,
  the opposite direction to `cached_tokens`, and for the same reason: every one of them went to
  OpenRouter, which prices every completion, so their zero means free rather than unknown.
  `design/features/budgets.md` carries what a dollar cap does about it.
- **A row's `enabled` is read from the switch that governs it, and a spender missing from the
  table is the dangerous case.** `enabled` is what separates a live bill from spent history, so
  a feature row asserting `True` regardless told the reader to go turn off something already
  off. Worse, a spender absent from `FEATURE_SPENDERS` falls through to the `retired` default,
  which is indistinguishable from the truth and says the opposite of it: `builtin:assistant`
  shipped unlisted and the spend view described the assistant as `retired · off` while it
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
  context observers share the `attention_observers_enabled` attention setting.
  That setting was named `phase7_observers_enabled` before config schema 31 and is migrated by
  value on load, because `load_config` copies only known dataclass fields: an unmigrated rename
  would silently drop an enabled switch and re-save the config without it, turning three
  observers off on upgrade with no record that they had ever been on.
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
**The parameter a model actually accepted is then remembered, per endpoint and per model, and tried first from then on.**
The catalog says what a model *advertises*, and for some models that is wrong in a way that costs a whole HTTP round-trip on every call: `deepseek/deepseek-v4-flash` advertises `max_completion_tokens`, rejects it, and takes `max_tokens` on the retry, which put 23,132 identical "rejected completion parameter profile" lines in `daemon.log` between 2026-08-20 and the D3 soak - one per scan-timeline completion.
Remembering it makes that a once-per-model cost instead of a per-call one.
It is a *reordering* and never a filter, so a provider that changes its mind costs the same retry it always did; only a compatibility rejection forgets the remembered answer, because a 429 or a schema error says nothing about which parameter shape a model takes; a refreshed model catalog clears the whole memory, since it is a new statement about the same question; and nothing is persisted, because one rejection per model per daemon start is nothing and a durable copy would need an invalidation story for a provider changing its mind while the daemon is not running.
No compatibility profile changes the exact model, removes strict `response_format`, removes `provider.require_parameters`, or makes output unbounded.
Other 404s do not mutate the request: an unknown model remains a terminal configuration fault, while OpenRouter's explicit no-provider-available response is retryable by the title ladder.
Models absent from a stale catalog use the same bounded profile sequence, so a newly configured exact model is not coupled to catalog refresh timing.
The selectable catalog still excludes non-text models and models without structured-output support because every current swe-mux OpenRouter consumer requires a strict JSON object.
Malformed, empty, non-object, and schema-invalid structured responses are retryable title faults.

**Every schema `complete_json` sends is bound by strict mode, and the binding is absolute rather than stylistic.**
`strict: true` is what makes `require_parameters` routing return schema data instead of prose, and it imposes two rules ordinary JSON Schema does not: `required` must list every key in `properties`, and `additionalProperties` must be `false` on every object.
A schema breaking either is rejected with HTTP 400 before a single token is billed, so an "optional" property is not a looser contract but a call that can never succeed.
The failure is silent in the worst direction: a rejected call bills nothing, writes no spend row, and is therefore indistinguishable in the spend table from an automation that simply never fired.
`TITLE_SCHEMA` shipped with `confidence` declared and not required, and `builtin:adaptive-title` consequently failed 100% of its live calls from the day it was first enabled until 2026-08-23.
Consumer tests cannot catch this and a similar test never will, because they inject a fake provider that ignores the `schema` argument entirely - correct for testing pivot logic, and exactly why the schemas carry their own guard (`tests/test_llm_schemas.py`, discovery by source scan so a new schema is covered without being remembered).

Observer-call rows retain only safe response diagnostics: generation and resolved model, provider,
finish reason, HTTP status, token and cost usage, response content type and length, and retryability.
The response content itself is not retained.
Every caller records those fields on failure as well as on success, because a failed call that bills nothing leaves the row as its *only* trace, and a row saying merely that something failed cannot be acted on.
A rejected request also keeps the provider's own explanation of the rejection: HTTP 400 joins the statuses whose body is retained (`SAFE_ERROR_DETAIL_STATUSES`), since it names which part of the request was refused and is a request error rather than an auth error.
Auth statuses stay excluded because their body can echo the rejected credential, and key-shaped text is scrubbed from every retained detail regardless.

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

That zero is deliberately ambiguous and must not be read as health.
Working-and-stable and rejected-on-every-call produce the identical count, and neither produces a spend row, so the discriminator is the observer-call ledger: a healthy quiet run has *no* rows for `builtin:adaptive-title`, while a broken one has rows carrying a terminal status and an HTTP code.
The Automation dashboard's Observer calls list is where that is read.

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
