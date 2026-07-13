# Usage analytics

## What it is

- Optional cached summaries for Claude and Codex from one unified, locally installed
  `ccusage` CLI.
- This is historical cost/token analytics, not live context-window truth or quota failover.

## Operations and invariants

- Disabled by default. Refresh runs only manually or on a configured low-priority cadence;
  startup and PTY input never wait for it.
- At most one refresh runs at a time. Each command has a timeout and output cap.
- Configured defaults invoke `ccusage claude daily --json` and
  `ccusage codex daily --json`. The pinned `ccusage@20.0.17` package is installed once;
  swe-mux never downloads or updates it implicitly. Settings reports the exact install
  command and any execution error.
- Exact legacy defaults using `npx --no-install` and the deprecated separate Codex package
  migrate automatically. Custom commands remain untouched.
- Executables are resolved before launch. On Windows, npm `.cmd`/`.bat` shims run through
  `COMSPEC`, while Linux and macOS execute the resolved native command directly.
- Claude/Codex JSON is validated and normalized to daily, monthly, session, model, token,
  and cost aggregates with source/version provenance. Calculated costs remain labelled
  estimates.
- A successful refresh atomically replaces the last-known-good cache. Failure preserves
  cached data and exposes stale/error state.
- `: menu` and the command palette open the dedicated Usage dashboard. It shows combined
  or provider-specific totals, recent daily use, model use, provider freshness/errors,
  and immediate progress/completion feedback for refreshes. Settings owns enablement,
  cadence, command configuration, and a shortcut back to the dashboard.
- Tests consume pinned JSON fixtures and never invoke external tools.

## Key files

- Adapter/cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Dashboard UI: `frontend/src/UsageDashboard.tsx`
- Fixtures/tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`
