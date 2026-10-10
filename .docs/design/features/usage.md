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

Current shows saved-account cards with selected-account markers, remaining capacity, percent used, reset countdowns with their absolute reset time, and reading freshness.
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
  Under the lanes, each account's rise over the range is given as shares by source, and the readout names where the rise ending at the inspected reading (or on the inspected day) came from.

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
- Refresh runs only manually or on a configured low-priority cadence.
- Startup and PTY input never wait for it.
- At most one refresh runs at a time.
- Each command has a timeout and output cap.
- The default collector command is `ccusage daily --json --by-agent`.
- **The timeout is 120s because the collector's cost is the size of this host's transcript corpus, not anything the daemon controls.**
  It was 30s, and every scheduled refresh timed out from 2026-08-21 on.
  Measured 2026-08-24 on the primary host (36,529 Claude transcripts, ~21 GB across `~/.claude/projects` and `~/.codex/sessions`), running the daemon's exact command from a shell: 33.9s cold, 10.3s with the OS file cache warm, 5.8s warm with `--offline`.
  Exit code 0 and an empty stderr in every run - the command was never hung, and there is no update check to blame; the bound was simply under the cost, and the corpus only grows.
  `--offline` is deliberately not added to the default command: it would buy back ~4s of pricing fetch by changing what the dollar figures are computed from, and a cost basis is not a thing to trade for latency.
  A refresh that does exceed the bound says so with the bound and the time it spent, because "timed out" alone cannot tell a hung command from a slow one.
- Each run carries the refresh's own operation id, so the bounded runner's timeout or cap line, the adapter's failure line, and the `usage_refresh_failed` event are three readings of one failure rather than three unrelated ones.
- Settings installs or updates the CLI explicitly with `npm install -g ccusage@latest`.
- Refresh uses the installed executable and never downloads or updates code.
- The primary override is the single `usage_command` array.
- Migrated custom per-source commands remain in `usage_commands` as legacy overrides and replace that source after the unified scan.
- Exact old Claude and Codex defaults, including the former `npx --no-install` commands, migrate to the unified collector command.
- Executables are resolved before launch.
- On Windows, npm `.cmd` and `.bat` shims run through `COMSPEC`.
- Linux and macOS execute the resolved native command directly.
- The adapter validates the unified payload, splits nested `agents` rows by source, and normalizes daily, monthly, model, token, and cost aggregates.
- The adapter accepts legacy `modelBreakdowns` arrays and current `models` maps.
- Tokens are transcript aggregates reported by ccusage.
- Source-provided costs are marked `source_estimate`.
- When a model map omits cost, the adapter allocates daily cost in proportion to model tokens and marks it `proportional`.
- Read-only model labels may use the frontend compact display mapping.
- Grouping, sorting, cache rows, tooltips, accessibility labels, and configuration preserve exact model identifiers.
- Cache version 3 stores a dynamic `sources` map and one `collector` refresh state.
- Cache version 2 provider rows migrate in memory to source rows.
- A successful refresh atomically replaces the last-known-good cache.
- Failure preserves cached data and exposes stale or error state.
- Tests consume version-labelled JSON fixtures and never invoke external tools.

## Navigation

- `usage.open` opens Overview.
- `usage.quota` opens Quota → Current.
- `fleetActivity.open` opens Activity → Runs in Usage & activity.
- `resources.open` opens System → Processes.
- Existing command IDs remain stable for saved keybindings and voice navigation.

## Key files

- Adapter and cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Dialog shell and segment list: `frontend/src/UsageModal.tsx`, `frontend/src/usageSegments.ts`
- Overview: `frontend/src/UsageOverview.tsx`, `frontend/src/usagePots.ts`
- Agents: `frontend/src/UsageDashboardView.tsx` (`UsageAgentsView`), `frontend/src/UsageModelBreakdown.tsx`
- Automation: `frontend/src/AutomationSpendView.tsx`, `frontend/src/automationCost.ts`
- Quota: `frontend/src/QuotaAnalytics.tsx`, `frontend/src/quotaTimeline.ts`, `frontend/src/providerAccountDisplay.ts`
- Shared presentation: `frontend/src/AnalyticsPrimitives.tsx`, `frontend/src/analytics.css`
- Historical analytics helpers: `frontend/src/usageAnalytics.ts`, `frontend/src/analyticsPresentation.ts`
- Operational store: `src/swe_mux/operational_telemetry.py`; frontend shapes: `frontend/src/operationalTelemetry.ts`
- Fixtures and tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`,
  `tests/test_frontend_usage_phase5_contract.py`, `frontend/test/usagePots.test.ts`,
  `frontend/test/quotaTimeline.test.ts`, `frontend/test/renderer/usage-layout.spec.ts`
