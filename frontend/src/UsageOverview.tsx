import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { exactMoney, formatCount, formatMoney, type SpendBreakdown } from './automationCost'
import { formatResetRemaining, providerQuotaWindows } from './providerAccountDisplay'
import type { ProviderAccountsStatus } from './ProviderAccounts'
import { serverNow } from './serverClock.ts'
import type { UsageSource } from './usageAnalytics'
import { agentPot, quotaWindowLabel, tightestQuota } from './usagePots'
import type { UsageSegment } from './usageSegments'

// The headline the old Tokens segment never had.
//
// Six domain tabs sat behind that name and not one of them answered "what am I burning".
// Three of the six were not money at all, and the three that were are three different
// things that must not be added up, so no tab could hold the total and none of them tried.
// The result was a surface named for spending where the first screen was a filter row.
//
// This is that missing screen, and the constraint that shaped it is the same one that broke
// the old layout: the pots do not sum. So the tile is the unit rather than the row - three
// figures, each stamped with the basis that makes it mean something, each a door into the
// segment that explains it. What a reader takes away is "which of these three is large",
// which is a comparison they can make and the only one that is honest.

type UsageCache = {
  enabled: boolean
  cache?: { updated_at?: number; sources?: Partial<Record<string, UsageSource>> }
}
type Dashboard = {
  spend_today: { tokens: number; cost_usd: number }
  spend_breakdown?: SpendBreakdown
}

const AGENT_WINDOW_DAYS = 30

export function UsageOverview({ onOpen }: { onOpen: (segment: UsageSegment) => void }) {
  const [usage, setUsage] = useState<UsageCache | null>(null)
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [accounts, setAccounts] = useState<ProviderAccountsStatus | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let stale = false
    // Three reads because there are three pots and no endpoint owns more than one of them.
    // Failing them together rather than one-by-one is deliberate: a tile that silently
    // renders `$0` because its fetch died is indistinguishable from a tile that is telling
    // the truth, and $0 is the reading a reader is least likely to question.
    Promise.all([
      api<UsageCache>('GET', '/api/usage'),
      api<Dashboard>('GET', '/api/automation/dashboard'),
      api<ProviderAccountsStatus>('GET', '/api/provider-accounts'),
    ])
      .then(([cache, spend, saved]) => {
        if (stale) return
        setUsage(cache); setDashboard(spend); setAccounts(saved)
      })
      .catch(cause => { if (!stale) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { stale = true }
  }, [])

  const sources = Object.values(usage?.cache?.sources || {}).filter(
    (item): item is UsageSource => !!item,
  )
  const agents = agentPot(sources, AGENT_WINDOW_DAYS)
  const totals = dashboard?.spend_breakdown?.totals
  const observerDays = dashboard?.spend_breakdown?.days || 7
  // Calls the provider never priced contribute zero to every figure drawn from this ledger,
  // so a total over a window containing them is a floor and has to say so.
  const unpriced = totals?.unpriced_calls || 0
  const quota = tightestQuota(providerQuotaWindows(accounts?.accounts || [], accounts?.selected || {}))
  const now = serverNow()

  return <main class="usage-overview">
    {error && <div class="usage-error" role="alert">{error}</div>}
    <div class="usage-pots">
      <button class="usage-pot" onClick={() => onOpen('agents')}>
        <header><strong>Agents</strong><em>subscription · estimated</em></header>
        {usage?.enabled
          ? <>
            <b title={exactMoney(agents.cost_usd)}>{formatMoney(agents.cost_usd)}</b>
            <span>{formatCount(agents.total_tokens)} tokens over the last {agents.days} cached days</span>
            <small>{agents.latest_date
              ? `newest cached day ${agents.latest_date} · ${formatMoney(agents.latest?.cost_usd || 0)}`
              : 'no cached historical usage yet'}</small>
          </>
          : <>
            <b class="usage-pot-off">off</b>
            <span>Historical collection is switched off, so agent spend is unmeasured.</span>
            <small>Turn on ccusage in Agents to read it.</small>
          </>}
      </button>
      <button class="usage-pot" onClick={() => onOpen('automation')}>
        <header><strong>Automation</strong><em>metered · billed</em></header>
        <b title={exactMoney(totals?.cost_usd || 0)}>{unpriced ? '≥ ' : ''}{formatMoney(totals?.cost_usd || 0)}</b>
        <span>{formatCount(totals?.calls || 0)} calls over {observerDays} days</span>
        <small class={unpriced ? 'warn' : ''}>{unpriced
          ? `${formatCount(unpriced)} calls reported no cost, so this is a floor`
          : `today ${formatMoney(dashboard?.spend_today.cost_usd || 0)} · ${formatCount(totals?.today_calls || 0)} calls`}</small>
      </button>
      <button class="usage-pot" onClick={() => onOpen('quota')}>
        <header><strong>Quota headroom</strong><em>% of window</em></header>
        {quota
          ? <>
            <b class={quota.headroom_percent <= 15 ? 'warn' : ''}>{Math.round(quota.headroom_percent)}%</b>
            <span>left on the tightest window · {quota.provider} {quotaWindowLabel(quota.window)}</span>
            <small>{quota.resets_at
              ? `${Math.round(quota.used_percent)}% used · resets ${formatResetRemaining(quota.resets_at, now)}`
              : `${Math.round(quota.used_percent)}% used`}</small>
          </>
          : <>
            <b>—</b>
            <span>No selected account is reporting a readable quota window.</span>
            <small>Unknown headroom is not full headroom.</small>
          </>}
      </button>
    </div>
    <p class="telemetry-caveat usage-pots-caveat">
      These three are never added together. Agent spend is a subscription read back out of
      transcripts and is an estimate; automation spend is a metered key billed by the call;
      quota is a share of a provider window and is not money. A total across them would be a
      number that is true of nothing.
    </p>
    <p class="usage-overview-freshness">
      {usage?.cache?.updated_at
        ? `ccusage cache updated ${new Date(usage.cache.updated_at * 1000).toLocaleString()}`
        : 'No ccusage cache has been written yet.'}
      {accounts?.accounts.length ? ` · ${accounts.accounts.length} saved provider account(s)` : ''}
    </p>
  </main>
}
