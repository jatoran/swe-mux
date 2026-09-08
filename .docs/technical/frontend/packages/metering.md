# Frontend: Resources dialog, Usage dialog, and accounts

Index: `../packages.md`.
Design: `../../../design/features/usage.md`, `../../../design/features/processes-and-previews.md`, `../../../design/features/operational-telemetry.md`, `../../../design/features/remote-access.md`.

## System and Usage & activity

`ResourcesModal.tsx` owns **System** with Processes, Network, and Storage.
`UsageModal.tsx` owns **Usage & activity** with Overview, Agent usage, Quota, Activity, and Automation.
Both use the viewport-bounded flex shell; analytics-specific responsive rules live in `analytics.css`.
Inactive main sections unmount so opening one does not retain another section's pollers.
The session-scoped Processes drawer remains available beside the terminal.

`AnalyticsPrimitives.tsx` provides responsive navigation, touch/keyboard exact-value expansion, expandable historical rows, token details, and bounded date-positioned charts.
Desktop primary tabs become a native section selector on mobile.
Secondary navigation retains short visible tabs, and extra filters use a bounded disclosure.
`analyticsPresentation.ts` owns compact number formatting and calendar-based historical windows.
`usagePots.ts` uses the same window helper for the Overview; it also owns tightest-window quota selection.

`UsageDashboardView.tsx` owns Summary, Trends, and Models, the source picker, and explicit collection operations.
`UsageModelBreakdown.tsx` aggregates by exact source/model identity and offers selected-model history.
`QuotaAnalytics.tsx` owns Current, History, Resets, and Attribution and fetches only the evidence needed by the selected view.
Current readings are reloaded once a minute while the document is visible; quota provider polling remains owned by the backend.
Attribution requests 500 recent rows filtered by provider/account, then labels and applies its date filter to that bounded result.

`FleetActivityView.tsx` owns Runs, Tools, Checks, Context, and Patterns.
The selected date window is frozen in a memoized query until a filter changes or the operator reloads, so result renders cannot restart reads and cursor pages share one upper bound.
Project/model/harness filters apply across views; layer, family, outcome, and evidence filters apply only to Tools and Patterns.
`WorkloadTelemetry.tsx` owns the run browser, Live agents now projection, and run/turn inspection.
The historical browser reads `/api/telemetry/v2/runs` and uses the canonical matching count, not displayed page length.
It retains exact run identity when opening History or matching a live session.
The workload aggregate endpoint only supplies available filter dimensions.
Tools and Checks use cursor-paged entity reads; totals and per-field measurement denominators remain explicit.
Collection diagnostics contain field coverage, parser and reconciliation state, export links, and lazy legacy comparison.

`AutomationSpendView.tsx` remains shared with the Automation dashboard.
Metered call costs remain primary; observed-agent estimates are labelled by their subset and collapsed.
`automationCost.ts` owns its cost, count, cache, and duration formatting.
`operationalTelemetry.ts` supplies the quota attribution types; Activity uses canonical v2 routes.

## Bandwidth, storage, and processes

`networkUsage.ts`, `NetworkUsageModal.tsx`, `StorageUsageModal.tsx`, `processRows.ts`,
`sessionProcesses.ts`

The bandwidth modal reads and resets daemon-local application-payload counters.
Process rollups reuse App's fleet sample, while sidebar child rows come only from backend-listed Preview registrations after browser classification or explicit promotion.

## Accounts and the resource rail

`ProviderAccounts.tsx`, `providerAccountDisplay.ts`, `noticePrefs.ts`, `ResourceUsage.tsx`, `resourceTotals.ts`, `resourceTooling.ts`

Anchored viewport popovers and summaries.
The switcher's stranded-session notice (a Codex login a switch left live sessions on) is one collapsed line per login that expands to the sentence, a dismiss, and a "never show this again" checkbox.
`noticePrefs.ts` owns the persistent half: a closed vocabulary of notice ids written to the `notices` device-settings domain under the canonical `desktop` profile, republished on `mux:settings-changed`, with the undo under the explainer in Settings -> Accounts.
The for-now half is a module-level set in `ProviderAccounts.tsx` keyed by `strandedNoticeKey` (provider and login, never the count) and pruned when the login stops being stranded.
Each saved-account row names quota periods inline with its figures (`remaining/5h`, `remaining/7d`, `fable`) instead of relying on a detached heading row.
The quota refresh age stays right-aligned on the account identity line, leaving the quota line to compare usage only.
The expanded sidebar uses one icon-led row for a boxed live-session count, boxed process-tree count, rounded whole-system CPU, and swe-mux process-tree working set, with full labels in its tooltip and accessible name.

The session count leads because it is the operator's own unit of work and the one figure there that is always knowable.
It is counted from the fleet the sidebar already holds - `sessionAttention.ts`'s `liveSessionCount`, the same predicate a Project's own badge uses - rather than from process inspection, so it carries no unavailable fallback and stays truthful on a host that refuses psutil.

The popover shows three figures only - whole-system CPU, one RAM box, and the owned process count; the RAM box prefers the reclaimable (USS) total when the open panel's sample carries it and falls back to working set (RSS).
The per-Project, daemon/infrastructure, and duplicated-tooling breakdowns were removed from the popover (2026-08-26); `resourceTotals.ts`'s `projectResourceTotals` and `resourceTooling.ts`'s classifier remain as tested pure helpers with no current UI consumer.
The rail uses the shared reduced `?summary=1` poll, while the open popover fetches the full `?unique_memory=1` projection on its own timer, because that sample is far too costly for a background poll.
