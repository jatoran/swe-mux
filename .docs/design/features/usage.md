# Usage analytics

## What it is

- Optional cached summaries for registry harnesses that declare an external usage command.
- Claude Code and Codex currently use one unified, locally installed `ccusage` CLI.
- Refresh state and all-provider iteration use the independent `external_usage_command` harness capability.
  OMP is managed but excluded because its native transcript already reports exact tokens, cache traffic, and cost.
- This is historical cost/token analytics, not live context-window truth or quota failover.
- `ccusage` scans provider transcript roots and does not expose a trustworthy saved-account identity for each historical row.
- Historical totals and model rows must never be presented as belonging to a saved Claude or Codex account slot.
- Provider subscription windows and account switching belong to
  `provider-accounts.md`; the two caches and refresh workers are independent.
- Durable quota/reset/correlation, explicit tool/skill metrics, and compaction history belong
  to `operational-telemetry.md`; they share the dashboard but not the `ccusage` cache.

## Operations and invariants

- Disabled by default. Refresh runs only manually or on a configured low-priority cadence;
  startup and PTY input never wait for it.
- At most one refresh runs at a time. Each command has a timeout and output cap.
- Configured defaults invoke `ccusage claude daily --json` and
  `ccusage codex daily --json`. Settings installs or updates the unified CLI explicitly
  with `npm install -g ccusage@latest`; the npm tag resolves only during that operator
  action. Refreshes use the installed executable and never download or update code.
- Per-harness command overrides live in `usage_commands`; descriptors that measure usage from
  their own transcripts can opt out of an external command.
- OMP usage is aggregated across assistant messages on the active transcript branch.
  `message.usage.input`, `output`, `cacheRead`, and `cacheWrite` populate the four token counters,
  while `message.usage.cost.total` is summed into `cost_usd`.
  This cost is provider-reported native cost, not a mux price-table estimate.
  The latest assistant message supplies provider and model, and OMP's cached model catalog supplies
  the context window used for final and peak context percentages.
  A packaged Anthropic probe measured all four token counters, exact cost, model, provider, context,
  and credential pin in the live session and the same finalized values in history after exit.
- Exact legacy defaults using `npx --no-install` and the deprecated separate Codex package
  migrate automatically. Custom commands remain untouched.
- Executables are resolved before launch. On Windows, npm `.cmd`/`.bat` shims run through
  `COMSPEC`, while Linux and macOS execute the resolved native command directly.
- Each supported command's JSON is validated and normalized to daily, monthly, session, model,
  token, and cost aggregates with source/version provenance. The current adapter accepts legacy
  `modelBreakdowns` arrays and current Codex `models` maps; model rows retain their daily
  key so range-scoped breakdowns can be derived.
  Tokens in those rows are exact transcript aggregates.
  Source-provided costs are marked `source_estimate`; when a Codex model map omits cost, the adapter allocates the daily cost in proportion to model tokens and marks it `proportional`.
- A successful refresh atomically replaces the last-known-good cache. Failure preserves
  cached data and exposes stale/error state.
- `: menu` and the command palette open the dedicated Usage dashboard. Provider selection
  composes with Overview, Time series, and Model breakdown views; daily/monthly interval,
  cached-day range, and token/cost metric controls derive views from daily cache rows.
  The model view renders a stacked per-period series and a per-provider, per-model detail table without collapsing the date dimension.
  Provider freshness/errors and refresh progress remain visible. Settings owns enablement,
  cadence, advanced command overrides, and a shortcut back to the dashboard.
- Tests consume version-labelled JSON fixtures and never invoke external tools.

## Key files

- Adapter/cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Dashboard UI: `frontend/src/UsageDashboardView.tsx`, `frontend/src/UsageModelBreakdown.tsx`
- Operational store: `src/swe_mux/operational_telemetry.py`
- Fixtures/tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`
