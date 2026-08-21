# Frontend: Resources dialog, usage, and accounts

Index: `../packages.md`.
Design: `../../../design/features/usage.md`, `../../../design/features/processes-and-previews.md`, `../../../design/features/remote-access.md`.

## Resources dialog

`ResourcesModal.tsx`, `NetworkUsageModal.tsx` (`NetworkUsageView`), `StorageUsageModal.tsx`
(`StorageUsageView`), `UsageDashboardView.tsx` (`UsageTokensView`), `ProcessFleetView.tsx`

One dialog for everything metered: **Processes**, **Network**, **Storage**, **Tokens**.
It replaces four separate modals that were four implementations of one shape - layer, focus trap, header, close - reached from four app-menu rows.
Tokens is the odd one and belongs anyway: three of the four are machine resources and one is money, but "how much am I burning" is asked about all four in the same breath.

`ResourcesModal.tsx` owns the shell and the segmented control.
Each view keeps its own fetching and its own toolbar controls, and is **unmounted when not selected** on purpose, since three of the four poll and a dialog holding four live pollers open would cost more than the four modals it replaced.
The panel is a flex column rather than a fixed grid template, because its segments contribute different numbers of rows.

The drawer's **Processes tab is not made redundant by it**: a modal covers the terminal, and that tab pins the focused session beside it - the same watch-here/act-there split the prompt Queue has with the Fleet Queue.

## Usage, automation spend, and processes

`UsageDashboardView.tsx`, `UsageModelBreakdown.tsx`, `usageAnalytics.ts`, `AutomationSpendView.tsx`,
`WorkloadTelemetry.tsx`, and other feature-named panels, plus `networkUsage.ts`, `NetworkUsageModal.tsx`,
`processRows.ts`, `automationCost.ts`, `sessionProcesses.ts`

`AutomationSpendView.tsx` is drawn identically by the Automation dashboard and by Resources → Tokens, as the **same component** rather than two views over one endpoint.
Both readings are legitimate - which rule burned this, beside the rules; what am I burning in total, beside the other meters - and duplicating the markup would reproduce the drift this consolidation removed elsewhere.

`WorkloadTelemetry.tsx` is the observed-workload table, following the cost column that had left the same view earlier for the same reason.
It deliberately repeats no money, since the spend domain beside it is the whole cost picture.

The usage dashboard keeps historical sources, quota providers, tools, and context as separate filter domains.
Historical source controls derive from cache metadata instead of the launch-harness registry, use one source multi-select popover, and issue one unified refresh request.
`usageAnalytics.ts` owns source and model aggregation.

`automationCost.ts` owns magnitude-aware spend formatting, ranked per-rule rows, and the prompt-cache hit rate (`cacheHit`).
Its `null` return is deliberately distinct from a `0%` rate: null is "nothing was billed in this window", which an unused rule and a daemon predating cache accounting both look like, and printing 0% for either accuses a working cache of being broken.

The bandwidth modal reads and resets daemon-local application-payload counters.
Process rollups reuse App's fleet sample, while sidebar child rows come only from backend-listed Preview registrations after browser classification or explicit promotion.

## Accounts and the resource rail

`ProviderAccounts.tsx`, `ResourceUsage.tsx`, `resourceTotals.ts`, `resourceTooling.ts`

Anchored viewport popovers and summaries.
The expanded sidebar uses one icon-led row for a boxed live-session count, boxed process-tree count, rounded whole-system CPU, and swe-mux process-tree working set, with full labels in its tooltip and accessible name.

The session count leads because it is the operator's own unit of work and the one figure there that is always knowable.
It is counted from the fleet the sidebar already holds - `sessionAttention.ts`'s `liveSessionCount`, the same predicate a Project's own badge uses - rather than from process inspection, so it carries no unavailable fallback and stays truthful on a host that refuses psutil.

The popover separates whole-system CPU from process-tree metrics and gives reclaimable RAM (USS) and working set (RSS) distinct boxes; attributed daemon and Project CPU is labeled as equivalent core load.
The rail uses the shared reduced `?summary=1` poll, while the open popover fetches the full `?unique_memory=1` projection on its own timer, because that sample is far too costly for a background poll.
`resourceTooling.ts` classifies language servers so per-session duplication is named rather than hidden among identical `node.exe` rows.
