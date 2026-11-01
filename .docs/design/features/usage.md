# Usage analytics

## What it is

- Optional cached historical token and cost summaries collected by the locally installed `ccusage` CLI.
- One `ccusage daily --json --by-agent` process discovers every source for which ccusage finds data.
- ccusage 20 currently supports Claude Code, Codex, OpenCode, Amp, Droid, CodeBuff, Hermes, Pi, Goose, OpenClaw, Kilo Code, Kimi, Qwen Code, GitHub Copilot, and Gemini CLI.
- Historical source discovery is independent of the swe-mux harness registry.
- A source may appear even when swe-mux cannot launch or manage that tool.
- A managed harness may exist without appearing in historical usage when ccusage finds no compatible transcript data.
- Historical sources and quota providers are different concepts.
- Historical rows carry `source_id`, `source_label`, and `collector_id`; quota rows retain provider and account identity.
- This is historical cost and token analytics, not live context-window truth or quota failover.
- ccusage scans tool transcript roots and does not expose a trustworthy saved-account identity for each historical row.
- Historical totals and model rows must never be presented as belonging to a saved provider account slot.
- Provider subscription windows and account switching belong to `provider-accounts.md`.
- Durable quota, reset, correlation, tool, skill, and compaction telemetry belongs to `operational-telemetry.md`.
- The surface is the **Agent usage** segment of the **Usage & activity** dialog, which is its own modal and its own app-menu row (`ui.md`).
  Every control that filters, refreshes, or clears the ccusage cache lives on that one segment, because it applies to nothing else.

## Organization and measurement boundaries

**Usage & activity** contains Overview, Agent usage, Quota, Activity, and Automation.
**System** contains Processes, Network, and Storage.
Agent behavior belongs with consumption and capacity; host resource inspection remains separate.

| Segment | Views | Measurement |
| --- | --- | --- |
| Overview | One landing view | Independent usage, automation, quota, and live-agent summaries |
| Agent usage | Summary, Trends, Models | ccusage transcript totals and estimated token cost |
| Quota | Current, History, Resets, Attribution | Provider-account capacity and durable utilization evidence |
| Activity | Runs, Tools, Checks, Context, Patterns | Canonical observed execution evidence |
| Automation | Metered costs, with observed-agent estimates collapsed | Observer ledger grouped by feature or rule |

Agent token-cost estimates, metered automation spend, and quota percentages are never summed.
A transcript estimate is not a subscription bill and cannot identify a saved account slot.
A total containing unpriced automation calls remains a lower bound.
The observed-agent cost subset stays labelled and collapsed inside Automation; it is not a competing agent total.
The overview's quota figure names the tightest reported window on selected accounts.
Unavailable quota is unknown capacity, never full headroom.

## Responsive presentation

The desktop dialog is bounded by the viewport and has primary tabs, one secondary tab row, and one scrolling content region.
Mobile uses a full-screen dialog with a primary section selector and visible secondary tabs.
Date range stays visible; additional filters use a disclosure with an active count.
Controls apply only to their own data source and retain their values while switching views within a section.
Inactive main sections unmount and issue no background reads.

Counts use compact K/M/B notation with one fractional digit throughout historical summaries, charts, and detail lists.
Exact values can be expanded by touch or keyboard and are also exposed on hover.
Historical rows expand to input, output, cache read, cache write, and cost-basis details.
Source and model summaries initially expose only identity, tokens, and estimated cost.
Trends have fixed height and position points by date; a period selector provides a keyboard and touch alternative to selecting a chart point.
Models are ranked across the selected period, with a selected model's history alongside the breakdown.

A historical date range is a calendar window ending on the newest cached date, including days with no records.
The same helper windows the Overview and Agent usage, so sparse history cannot produce conflicting totals.
The actual date bounds and collector freshness remain visible.
Collection settings, cache clearing, and source provenance live in a disclosure.

## Quota views

Current shows saved-account cards with selected-account markers, percent used, reset countdowns with their absolute reset time, and reading freshness.
Each window states only its percent used; no surface restates it as percent left, because one number per window read the same way everywhere is what makes two windows comparable at a glance.
Each window draws exactly one bar, on the limit's own 0-100 scale: its fill ends at the percent used and its empty rest is what is left.
Where attribution reaches the window (weekly and 5-hour), the fill is split by source - swe-mux sessions, not swe-mux, used elsewhere, unclear, and untracked for usage from before attribution covered it - and the parts sum to the reading, so the split and the reading are one bar rather than two (`operational-telemetry.md` § Quota usage attribution).
The same parts follow in words, with when the window began.
An account attribution has not reached yet fills the same bar with its plain reading and claims no split.
Every account name in the Quota views (the cards, History's rows and readings table, the reset log, and the attribution list) is led by its provider's mark - Anthropic's burst for Claude, OpenAI's knot for Codex - which carries the provider as its accessible name, so the words drop the provider suffix.
Text that cannot carry a mark (the Account filter's options, tooltips) keeps the provider in words.
The account-management link opens Settings → Accounts; this surface does not duplicate account switching.
Every Quota view names a window the same way: Weekly, 5-hour, and Fable weekly.

History opens on the **Weekly** window over the **last 7 days**, with one point per UTC day; the toolbar switches to the 5-hour window, a 30-day, 90-day, or all-retained range, and every raw reading.
The chart exists to answer what percent of a window each account had used at a given time, so its labelling follows from that question:

- The time axis spans the requested range rather than only the readings, with calendar ticks, so where a point sits says when it was read.
- Raw readings are drawn and labelled in the viewer's local time.
- Daily points are UTC calendar days, because the daemon rolls up by `date(sampled_at,'unixepoch')` and the browser cannot re-bucket them; each sits at its day's UTC noon and is always labelled as a UTC day.
- A line breaks across an outage (more than three times an account's median raw spacing, at least 30 minutes, or a missing day) instead of claiming a percentage nobody read.
- A readout under the chart lists every account's percent used at the inspected moment, defaulting to the newest reading; hovering, tapping, dragging, or the arrow, Home, and End keys move it.
- Raw readouts use as-of semantics: each account shows its latest reading at or before the moment, with that reading's own time when it differs, and the window's next reset as reported with it.
- Daily readouts never borrow another day's value, and add the day's first reading, peak, and sample count.
- Reset markers carry the account, the before and after values, and the observed time.
- Under the time axis, one source lane per account shows where each rise came from (`operational-telemetry.md` § Quota usage attribution): swe-mux sessions, not swe-mux, used elsewhere, or unclear.
  A cell is a rising interval or a UTC day, stacked by the share of its points from each source; raw rises of one source less than 90 minutes apart are drawn as one period, because a whole-percent window ticks up once every few polls while the usage behind the tick accrued across all of them.
  Under the lanes, each account's charted window as it stands now gets the same split as its Current card, whatever range is charted; then each account's rise over the range is given as shares by source, and the readout names where the rise ending at the inspected reading (or on the inspected day) came from.

The readings disclosure is a newest-first table with the charted window's column first; a daily cell gives the day's first and last reading, plus its peak when that differs.
Resets shows recorded movements and confirmation/review state.
Attribution lists every rise in the latest 500 readings matching the provider/account filter, further restricted to the chosen date range: each window's points by source, how the account was billed, and the swe-mux requests and API-equivalent cost billed to it.
Quota history is account-specific; ccusage history is not joined to it.

## Activity views

Runs is a cursor-paged browser of runs started in the selected period, with a separate Live agents now view that includes older still-live sessions.
Each historical row keeps its Project and run identity, name, model, elapsed wall time, and latest linked evidence.
An ended run is not labelled as a completed task, and an absent end record does not establish a live process.
Run inspection links to the exact transcript or matching live session and exposes turns, tool calls, recorded checks, and lifecycle evidence.
Historical names and transcript links are joined by exact run ID, never a session fallback that could resolve to a later conversation.

Tools groups calls by Project, harness, model, invocation layer, tool, and operation dimensions.
Each group expands to outcomes, measured duration coverage, and cursor-paged calls with evidence and run links.
Explicit skill activations are a disclosure in Tools.
Checks lists actual verification records and their originating runs.
Context summarizes compaction records and measured reclamation/timing coverage.
Patterns exposes descriptive findings and operator review, never a productivity ranking.
Collection health, parser/reconciliation detail, exports, and the optional legacy comparison remain collapsed diagnostics.
The API's workload summary is used to populate filter choices, not presented as a table mixing independently windowed run and activity totals.

## Operations and invariants

- Historical collection is disabled by default.
- Refresh runs only manually or on a configured cadence.
- Startup and PTY input never wait for it.
- At most one refresh runs at a time.
- The default collector command is `ccusage daily --json --by-agent`.
- Each run carries the refresh's own operation id, so the bounded runner's timeout, stall or cap line, the adapter's failure line, and the `usage_refresh_failed` event are three readings of one failure rather than three unrelated ones.

### Why a refresh is windowed and contained

On 2026-10-31 the scheduled refresh was found freezing the operator's whole PC for seconds at a time, every three hours.
The collector re-read the entire transcript corpus each run (46 GB: `~/.claude/projects` 28.2 GB in 19,516 files, `~/.codex/sessions` 18.1 GB in 2,872 files), on one thread per logical CPU, briefly holding ~10 GB of RAM, while Windows Defender scanned what it opened.
It ran at normal priority: the daemon runs above normal, but Windows passes a parent's class to a child only when that class is idle or below normal, so every child of the daemon starts level with the browser in front of the operator.
Durations in `daemon.log` were 27-88 s, every one of them a full read of history that had not changed.
Each rule in this section answers one part of that.

- **A refresh reads only what can have changed.**
  With cached history, `auto` appends `--since <date of the last refresh minus one day>` and merges the answer over the cache.
  The window is anchored on the last successful refresh rather than on today, so a machine that was off for a week reads the week.
  The window is authoritative for every date it covers: a cached day inside it that the window no longer reports is dropped, and monthly, model and total rows are derived again from the merged days, so a total never mixes a cached aggregate with fresh days.
  An empty window (`{"daily": []}`) is "no usage since", not a failure.
  The whole history is read only when there is no cache to merge into, or on **Rebuild history**.
  A configured command that already sets a window (`--since`, `--until`, `--last`) runs as configured and replaces the cache, as before.
- **How much `--since` saves is ccusage's.**
  From ccusage 20.0.26 it skips Claude Code transcripts last written before the window (mtime, one day of margin, grouped by session id); 20.0.23 read every Claude transcript and filtered afterwards; Codex files are skipped by date in both.
  A transcript written inside the window is still read whole, and ccusage still opens every Claude transcript to read its session id, so a host whose live sessions have very large transcripts still reads gigabytes: measured 2026-10-31 on the primary host, a three-day window with 20.0.46 read more than 4.4 GB, against 3.3 GB of transcripts touched in those days.
  `ccusage --version` is probed once per installed binary, and a version older than 20.0.26 is named in `advisories` with the update command.
- **Every ccusage run is contained** (`background_containment.py` through `bounded_subprocess.run_bounded`).
  On Windows the child starts suspended inside a Job object that pins every member to the idle class, hard-caps the tree at 25% of all CPUs, lowers I/O and memory priority to very low, and ends any straggler when the run closes; macOS uses `taskpolicy -b`, Linux `ionice -c 3` and `nice -n 19`.
  The job's accounting (`read_bytes`, `cpu_seconds`, `peak_memory_bytes`) is logged on `ccusage refresh completed` and kept in `last_run.resources`.
  A mechanism that could not be applied is listed in `degraded` and in `advisories`; the run still happens.
  CPU priority alone was not the fix: agents held the CPU at 100% for 18 s the same day without the browser lagging, while the collector froze it through I/O, memory and Defender.
- **The npm shim is bypassed.**
  ccusage 20's `cli.js` only spawns its platform binary (`@ccusage/ccusage-<platform>-<arch>/bin/ccusage`) with the same arguments, so the collector resolves that binary the way Node would and runs it directly (`launcher: native`), and the containment lands on the process doing the reading instead of on `cmd.exe` and node.
  Any layout it cannot resolve falls back to the shim (`launcher: shim`); a command that is not ccusage itself (`npx ...`) is never rewritten.
- **The bound is "no progress", not a wall clock.**
  A contained scan is slow by design and yields to everything else, so a wall-clock guess at its cost would kill legitimate runs - the 30 s bound did exactly that from 2026-08-21, and the 120 s bound that replaced it was sized to an uncontained 21 GB read.
  A run that uses no CPU and reads nothing for 300 s is stopped as hung, with an error that says so; two hours is the absolute ceiling.
  Both errors state what the run spent (time, bytes read, CPU seconds).
- **A scheduled refresh waits for a quieter host.**
  Before a scheduled run the host is sampled over five seconds; at 85% CPU or 90% memory it waits, re-checking each minute, for at most an hour, then runs contained anyway.
  `phase: deferred` and `deferred_reason` say so while it waits.
  An operator's own refresh never waits.
- **The schedule is re-read every minute.**
  A refresh is due one cadence after the later of the last attempt and the cache's `updated_at`, and never within five minutes of daemon start.
  A cadence or switch changed in Settings applies at the next minute rather than after the old cadence's sleep, which is what made turning the cadence down useless as a stopgap before.
- **A refresh request answers at once.**
  `POST /api/usage/refresh` starts the run and answers 202; both surfaces read `GET /api/usage` until `refreshing` is false, because no request should be held open for minutes.
- **Provenance is recorded, and a version change advises rather than rebuilds.**
  `collection.history` names the ccusage version and timezone that last computed the whole history; when either differs from now, `advisories` says older days may differ and offers Rebuild.
  A rebuild is not automatic, because it is the one run that reads everything, and ccusage releases often.
  A cache from before provenance existed keeps `null` provenance until its next rebuild rather than being claimed by the version that topped it up.
  Merging also means history survives Claude Code deleting transcripts after `cleanupPeriodDays`: a full rescan would forget those days, and a windowed refresh keeps them.
- `--offline` is deliberately not added to the default command: it would buy back ~4 s of pricing fetch by changing what the dollar figures are computed from, and a cost basis is not a thing to trade for latency.

### Collector command and adapter

- Settings installs or updates the CLI explicitly with `npm install -g ccusage@latest`.
- Refresh uses the installed executable and never downloads or updates code.
- The primary override is the single `usage_command` array, which must accept ccusage's `--since YYYYMMDD`.
- Migrated custom per-source commands remain in `usage_commands` as legacy overrides; they are windowed like the primary command and merge over that source, or replace it when they set their own window.
- Exact old Claude and Codex defaults, including the former `npx --no-install` commands, migrate to the unified collector command.
- Executables are resolved before launch, and a ccusage npm shim is resolved further to the platform binary behind it.
- When that binary cannot be found, npm `.cmd` and `.bat` shims run through `COMSPEC` on Windows, and Linux and macOS execute the resolved command directly.
- The adapter validates the unified payload, splits nested `agents` rows by source, and normalizes daily, monthly, model, token, and cost aggregates.
- The adapter accepts legacy `modelBreakdowns` arrays and current `models` maps.
- Tokens are transcript aggregates reported by ccusage.
- Source-provided costs are marked `source_estimate`.
- When a model map omits cost, the adapter allocates daily cost in proportion to model tokens and marks it `proportional`.
- Read-only model labels may use the frontend compact display mapping.
- Grouping, sorting, cache rows, tooltips, accessibility labels, and configuration preserve exact model identifiers.
- Cache version 3 stores a dynamic `sources` map, the `collection` provenance block, and one `collector` refresh state.
- Cache version 2 provider rows migrate in memory to source rows.
- A successful refresh atomically replaces the last-known-good cache with the merged (or rebuilt) result.
- Clearing the cache is refused while a refresh runs, so a finishing merge cannot resurrect cleared history.
- Failure preserves cached data and exposes stale or error state.
- Tests consume version-labelled JSON fixtures and never invoke external tools.

## Navigation

- `usage.open` opens Overview.
- `usage.quota` opens Quota → Current.
- `fleetActivity.open` opens Activity → Runs in Usage & activity.
- `resources.open` opens System → Processes.
- Existing command IDs remain stable for saved keybindings and voice navigation.

## Key files

- Adapter, cache, window planning and merge, schedule: `src/swe_mux/usage.py`
- Containment and the stall bound: `src/swe_mux/background_containment.py`, `src/swe_mux/bounded_subprocess.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Following a background refresh and wording its state: `frontend/src/usageRefresh.ts`
- Dialog shell and segment list: `frontend/src/UsageModal.tsx`, `frontend/src/usageSegments.ts`
- Overview: `frontend/src/UsageOverview.tsx`, `frontend/src/usagePots.ts`
- Agents: `frontend/src/UsageDashboardView.tsx` (`UsageAgentsView`), `frontend/src/UsageModelBreakdown.tsx`
- Automation: `frontend/src/AutomationSpendView.tsx`, `frontend/src/automationCost.ts`
- Quota: `frontend/src/QuotaAnalytics.tsx`, `frontend/src/quotaTimeline.ts`, `frontend/src/providerAccountDisplay.ts`
- Shared presentation: `frontend/src/AnalyticsPrimitives.tsx`, `frontend/src/analytics.css`
- Historical analytics helpers: `frontend/src/usageAnalytics.ts`, `frontend/src/analyticsPresentation.ts`
- Operational store: `src/swe_mux/operational_telemetry.py`; frontend shapes: `frontend/src/operationalTelemetry.ts`
- Fixtures and tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`,
  `tests/test_usage_incremental.py`, `tests/test_background_containment.py`,
  `tests/test_frontend_usage_phase5_contract.py`, `frontend/test/usagePots.test.ts`,
  `frontend/test/usageRefresh.test.ts`, `frontend/test/quotaTimeline.test.ts`,
  `frontend/test/renderer/usage-layout.spec.ts`
