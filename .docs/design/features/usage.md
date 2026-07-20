# Usage analytics

## What it is

- Optional cached summaries for Claude and Codex from one unified, locally installed
  `ccusage` CLI.
- This is historical cost/token analytics, not live context-window truth or quota failover.
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
- Exact legacy defaults using `npx --no-install` and the deprecated separate Codex package
  migrate automatically. Custom commands remain untouched.
- Executables are resolved before launch. On Windows, npm `.cmd`/`.bat` shims run through
  `COMSPEC`, while Linux and macOS execute the resolved native command directly.
- Claude/Codex JSON is validated and normalized to daily, monthly, session, model, token,
  and cost aggregates with source/version provenance. The adapter accepts legacy
  `modelBreakdowns` arrays and current Codex `models` maps; model rows retain their daily
  key so range-scoped breakdowns can be derived. Calculated costs remain labelled estimates.
- A successful refresh atomically replaces the last-known-good cache. Failure preserves
  cached data and exposes stale/error state.
- `: menu` and the command palette open the dedicated Usage dashboard. Provider selection
  composes with Overview, Time series, and Model breakdown views; daily/monthly interval,
  cached-day range, and token/cost metric controls derive views from daily cache rows.
  Provider freshness/errors and refresh progress remain visible. Settings owns enablement,
  cadence, advanced command overrides, and a shortcut back to the dashboard.
- Tests consume version-labelled JSON fixtures and never invoke external tools.

## Key files

- Adapter/cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Dashboard UI: `frontend/src/UsageDashboard.tsx`
- Operational store: `src/swe_mux/operational_telemetry.py`
- Fixtures/tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`
