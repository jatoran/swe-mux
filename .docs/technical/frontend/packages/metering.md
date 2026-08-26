# Frontend: Resources dialog, Usage dialog, and accounts

Index: `../packages.md`.
Design: `../../../design/features/usage.md`, `../../../design/features/processes-and-previews.md`, `../../../design/features/operational-telemetry.md`, `../../../design/features/remote-access.md`.

## Two dialogs, one shell

`ResourcesModal.tsx` and `UsageModal.tsx` draw the same frame - `.usage-panel.resources-panel`, a flex column with a segmented control - because they are the same shape.
The panel is a flex column rather than a fixed grid template, since segments contribute different numbers of rows.
Forking the shell to change a title is how two dialogs stop agreeing about their own chrome.

What separates them is the question, not the unit.
Resources is live readings of one host that go stale in seconds; Usage is a retrospective question asked of a ledger.

## Resources dialog

`ResourcesModal.tsx`, `NetworkUsageModal.tsx` (`NetworkUsageView`), `StorageUsageModal.tsx`
(`StorageUsageView`), `FleetActivityView.tsx`, `WorkloadTelemetry.tsx`, `ProcessFleetView.tsx`

Four segments: **Processes**, **Network**, **Storage**, **Fleet activity**.
It replaces three separate modals that were three implementations of one shape - layer, focus trap, header, close - reached from three app-menu rows.

`FleetActivityView.tsx` holds `runs + workload`, `tools + skills`, and `context + compaction`, which were domains of a retired **Tokens** segment and measure neither a token nor a dollar.
Processes says what the fleet is running now; Fleet activity says what it has been doing.
Money is deliberately absent: the Usage dialog is the whole cost picture, and a second table of one number under a second name is the drift this split removed.
Parser coverage is a collapsed `<details>` inside `tools + skills` rather than a peer table, because it says whether those figures were collectable rather than what they are.

Each view keeps its own fetching and is **unmounted when not selected** on purpose, since Processes and Network poll and a dialog holding live pollers open would cost more than the modals it replaced.

The drawer's **Processes tab is not made redundant by it**: a modal covers the terminal, and that tab pins the focused session beside it - the same watch-here/act-there split the prompt Queue has with the Fleet Queue.

## Usage dialog

`UsageModal.tsx`, `usageSegments.ts`, `UsageOverview.tsx`, `usagePots.ts`,
`UsageDashboardView.tsx` (`UsageAgentsView`), `UsageModelBreakdown.tsx`, `usageAnalytics.ts`,
`AutomationSpendView.tsx`, `automationCost.ts`, `QuotaAnalytics.tsx`

Four segments: **Overview**, **Agents**, **Automation**, **Quota**.
`usageSegments.ts` holds the segment type and descriptors so the Overview's tiles can navigate to siblings without importing the dialog that contains them.

`usagePots.ts` is the pure half of the Overview: `agentPot` windows the ccusage cache **by day rather than by row**, so two harnesses reporting the same date are one day; `tightestQuota` picks the window closest to running out across every provider's selected account and returns `null` for unreadable, never full headroom.
Both invariants are the ones that fail silently in the wrong direction, which is why they are functions with tests rather than expressions in JSX (`frontend/test/usagePots.test.ts`).

`AutomationSpendView.tsx` is drawn identically by the Automation dashboard and by Usage → Automation, as the **same component** rather than two views over one endpoint.
Both readings are legitimate - which rule burned this, beside the rules; what am I burning in total, beside the other pots - and duplicating the markup would reproduce the drift this consolidation removed elsewhere.
Its agent-model table is labelled by its denominator (`observed runs`) everywhere it appears, because `provider_cost_dimensions` is a subset of what ccusage reads and two bare totals were two competing answers to one question.

`automationCost.ts` owns magnitude-aware spend formatting, ranked per-rule rows, and the prompt-cache hit rate (`cacheHit`).
Its `null` return is deliberately distinct from a `0%` rate: null is "nothing was billed in this window", which an unused rule and a daemon predating cache accounting both look like, and printing 0% for either accuses a working cache of being broken.

Every control belongs to the one segment it applies to.
The source multi-select popover, the collector refresh, and the cache controls are on `Agents` alone; the provider filter is on `Quota` alone.
Historical source controls derive from cache metadata instead of the launch-harness registry, and issue one unified refresh request.
`usageAnalytics.ts` owns source and model aggregation.

`operationalTelemetry.ts` holds the `/api/telemetry/operational` shapes, which two dialogs now read for different halves - Usage → Quota takes `quota.attributions`, Resources → Fleet activity takes `tools` and `compactions`.
Neither owns the types, and a copy in each reader is how they drift.

## Bandwidth, storage, and processes

`networkUsage.ts`, `NetworkUsageModal.tsx`, `StorageUsageModal.tsx`, `processRows.ts`,
`sessionProcesses.ts`

The bandwidth modal reads and resets daemon-local application-payload counters.
Process rollups reuse App's fleet sample, while sidebar child rows come only from backend-listed Preview registrations after browser classification or explicit promotion.

## Accounts and the resource rail

`ProviderAccounts.tsx`, `ResourceUsage.tsx`, `resourceTotals.ts`, `resourceTooling.ts`

Anchored viewport popovers and summaries.
The expanded sidebar uses one icon-led row for a boxed live-session count, boxed process-tree count, rounded whole-system CPU, and swe-mux process-tree working set, with full labels in its tooltip and accessible name.

The session count leads because it is the operator's own unit of work and the one figure there that is always knowable.
It is counted from the fleet the sidebar already holds - `sessionAttention.ts`'s `liveSessionCount`, the same predicate a Project's own badge uses - rather than from process inspection, so it carries no unavailable fallback and stays truthful on a host that refuses psutil.

The popover shows three figures only - whole-system CPU, one RAM box, and the owned process count; the RAM box prefers the reclaimable (USS) total when the open panel's sample carries it and falls back to working set (RSS).
The per-Project, daemon/infrastructure, and duplicated-tooling breakdowns were removed from the popover (2026-08-26); `resourceTotals.ts`'s `projectResourceTotals` and `resourceTooling.ts`'s classifier remain as tested pure helpers with no current UI consumer.
The rail uses the shared reduced `?summary=1` poll, while the open popover fetches the full `?unique_memory=1` projection on its own timer, because that sample is far too costly for a background poll.
