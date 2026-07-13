# Usage analytics

## What it is

- Optional cached summaries from separately installed Claude and Codex ccusage tools.
- This is historical cost/token analytics, not live context-window truth or quota failover.

## Operations and invariants

- Disabled by default. Refresh runs only manually or on a configured low-priority cadence;
  startup and PTY input never wait for it.
- At most one refresh runs at a time. Each command has a timeout and output cap.
- Configured defaults are version-pinned `npx --no-install` argv. swe-mux never downloads
  `@latest` or installs packages implicitly; Settings reports install/configuration errors.
- Claude/Codex JSON is validated and normalized to daily, monthly, session, model, token,
  and cost aggregates with source/version provenance. Calculated costs remain labelled
  estimates.
- A successful refresh atomically replaces the last-known-good cache. Failure preserves
  cached data and exposes stale/error state. Settings can refresh or clear the cache.
- `: menu` and the command palette open Settings directly at Usage analytics instead of
  requiring the user to discover the section by scrolling.
- Tests consume pinned JSON fixtures and never invoke external tools.

## Key files

- Adapter/cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Fixtures/tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`
