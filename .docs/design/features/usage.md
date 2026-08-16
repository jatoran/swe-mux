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

- Historical, quota, tools, and context remain separate top-level telemetry categories.
- Historical usage has a scalable multi-select source popover derived from cached source metadata.
- Source freshness and collector errors live in that popover instead of one fixed card per source.
- Range, interval, metric, and overview or series controls apply only to historical data.
- Quota keeps a separate provider and account filter because those values have verified account semantics.
- Configure and clear-cache actions live in the overflow menu.
- Refresh is one API request and one unified collector run.
- The model view renders a stacked per-period series and per-source, per-model detail without collapsing the date dimension.

## Key files

- Adapter and cache: `src/swe_mux/usage.py`
- Config: `src/swe_mux/config.py`
- Settings UI: `frontend/src/Settings.tsx`
- Dashboard UI: `frontend/src/UsageDashboardView.tsx`, `frontend/src/UsageModelBreakdown.tsx`
- Historical analytics helpers: `frontend/src/usageAnalytics.ts`
- Operational store: `src/swe_mux/operational_telemetry.py`
- Fixtures and tests: `tests/fixtures/usage/`, `tests/test_usage_phase4.py`
