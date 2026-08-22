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
- The surface is the **Agents** segment of the **Usage** dialog, which is its own modal and its own app-menu row (`ui.md`).
  Every control that filters, refreshes, or clears the ccusage cache lives on that one segment, because it applies to nothing else.

## The three pots

The Usage dialog holds exactly three kinds of spend and never sums them.
A total across them is a number that is true of nothing, so no surface computes one and every figure carries the basis that makes it mean something.

| Pot | Segment | Basis | Source |
| --- | --- | --- | --- |
| Agents | `Agents` | subscription, estimated | ccusage over each harness's own transcripts |
| Automation | `Automation` | metered, billed by the call | the observer spend ledger (`budgets.md`) |
| Quota | `Quota` | share of a provider window, not money | durable quota samples (`operational-telemetry.md`) |

- `Overview` is the dialog's landing segment and draws one tile per pot, each stamped with its basis and each a door into the segment that explains it.
  The tile is the unit rather than a row of cells, because a row reads as something that should total.
- The quota tile reports the **tightest** window across every provider's selected account, and names which window that is.
  An average across windows reports comfortable headroom on an account about to be cut off, and `83%` means a different thing on a five-hour window than on a weekly one.
  An unreadable quota renders as unknown, never as full headroom.
- The agent tile's recent figure is **the newest cached day, named by its date**, never "today".
  The cache refreshes manually or on a slow cadence, so its newest row is routinely older than today, and a stale figure captioned "today" sends a reader hunting a spike that was already paid for.
- A dollar total is prefixed as a floor whenever its window contains calls the provider never priced, and names how many - a bring-your-own endpoint reports no cost at all, and a total presented as complete would understate the bill by an unknown amount (`budgets.md`).
- `Automation` is the Automation dashboard's `cost breakdown` view drawn from the same component rather than a second copy of it, so the two surfaces can never disagree (`automation.md`).

### Agent spend has two denominators and only one total

Two independent readings of the agent pot exist, and conflating them is the failure this
split corrected.

- **ccusage** (`Agents`) reads every transcript the harness wrote. This is the agent total, and it is the figure to compare a provider bill against.
- **`provider_cost_dimensions`** (`Automation`, and `/api/telemetry/workloads`) covers only runs swe-mux observed. It is a **subset** and therefore a floor.

Drawn as two bare totals under two names they were two competing answers to one question.
The observed-runs figure is therefore labelled by its denominator in its tile, its heading, and its table foot, and is never presented as the agent total.

## What is not in the Usage dialog

Runs and workload, tools and skills, and context compaction were domains of the retired
Tokens segment and measure neither a token nor a dollar.
They are the **Fleet activity** segment of the Resources dialog, beside Processes: one says
what the fleet is running now and the other says what it has been doing, both are opened
when something looks wrong, and neither is a bill (`ui.md`, `operational-telemetry.md`).
Money is deliberately absent there, because the Usage dialog is the whole cost picture.

## Operations and invariants

- Historical collection is disabled by default.
- Refresh runs only manually or on a configured low-priority cadence.
- Startup and PTY input never wait for it.
- At most one refresh runs at a time.
- Each command has a timeout and output cap.
- The default collector command is `ccusage daily --json --by-agent`.
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

## Dashboard behavior

- Navigation depth is dialog → segment → view.
  The third level exists only on `Agents`, where range, interval, and metric are genuinely orthogonal; every other segment is one screen.
- Each segment is unmounted when it is not selected.
  The four reach different endpoint sets, and a dialog holding all four mounted issues every one of those reads to show one of them.
- Every control belongs to the one segment it applies to.
  A shared actions row that goes inert on most tabs and prints a status line explaining that its filters apply elsewhere is the shape this replaced.
- Historical usage has a scalable multi-select source popover derived from cached source metadata.
- Source freshness and collector errors live in that popover instead of one fixed card per source.
- Range, interval, metric, and overview or series controls apply only to historical data.
- Quota keeps a separate provider and account filter because those values have verified account semantics.
- Configure and clear-cache actions live in the overflow menu; `configure` is also in the dialog header, because it is the way out to Settings from any segment.
- Refresh is one API request and one unified collector run.
- The model view renders a stacked per-period series and per-source, per-model detail without collapsing the date dimension.
- `usage.open` opens the dialog on `Overview` and `usage.quota` opens it on `Quota`.
  Quota is the one reading here that is ever urgent, so it is a command rather than two clicks inside another one.

## Key files

- Adapter and cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Dialog shell and segment list: `frontend/src/UsageModal.tsx`, `frontend/src/usageSegments.ts`
- Overview: `frontend/src/UsageOverview.tsx`, `frontend/src/usagePots.ts`
- Agents: `frontend/src/UsageDashboardView.tsx` (`UsageAgentsView`), `frontend/src/UsageModelBreakdown.tsx`
- Automation: `frontend/src/AutomationSpendView.tsx`, `frontend/src/automationCost.ts`
- Quota: `frontend/src/QuotaAnalytics.tsx`, `frontend/src/providerAccountDisplay.ts`
- Historical analytics helpers: `frontend/src/usageAnalytics.ts`
- Operational store: `src/swe_mux/operational_telemetry.py`; frontend shapes: `frontend/src/operationalTelemetry.ts`
- Fixtures and tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`,
  `tests/test_frontend_usage_phase5_contract.py`, `frontend/test/usagePots.test.ts`,
  `frontend/test/renderer/usage-layout.spec.ts`
