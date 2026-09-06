import type { Session } from './types'
import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import { exactMoney, formatCount, formatMoney, type SpendBreakdown } from './automationCost'
import { formatResetRemaining, providerQuotaWindows } from './providerAccountDisplay'
import type { ProviderAccountsStatus } from './ProviderAccounts'
import { serverNow } from './serverClock.ts'
import type { UsageSource } from './usageAnalytics'
import { agentPot, quotaWindowLabel, tightestQuota } from './usagePots'
import type { UsageSegment } from './usageSegments'

type UsageCache = {
  enabled: boolean
  cache?: { updated_at?: number; sources?: Partial<Record<string, UsageSource>> }
}
type Dashboard = {
  spend_today: { tokens: number; cost_usd: number }
  spend_breakdown?: SpendBreakdown
}

const AGENT_WINDOW_DAYS = 30

export function UsageOverview({ onOpen, sessions=[] }: { onOpen: (segment: UsageSegment) => void; sessions?:Session[] }) {
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

  return <main class="usage-overview analytics-content">
    {error && <div class="usage-error" role="alert">{error}</div>}
    <div class="usage-pots">
      <button class="usage-pot" onClick={() => onOpen('agents')}>
        <header><strong>Agent usage</strong><em>transcript estimate</em></header>
        {usage?.enabled
          ? <>
            <b title={exactMoney(agents.cost_usd)}>{formatMoney(agents.cost_usd)}</b>
            <span>{formatCount(agents.total_tokens)} tokens over {agents.days} days ending {agents.latest_date||'with the newest record'}</span>
            <small>{agents.latest_date
              ? `newest cached day ${agents.latest_date} · ${formatMoney(agents.latest?.cost_usd || 0)}`
              : 'no cached historical usage yet'}</small>
          </>
          : <>
            <b class="usage-pot-off">{usage?'Off':'Unavailable'}</b>
            <span>{usage?'Historical collection is switched off.':'Historical usage could not be read yet.'}</span>
            <small>Turn on ccusage in Agents to read it.</small>
          </>}
      </button>
      <button class="usage-pot" onClick={() => onOpen('automation')}>
        <header><strong>Automation</strong><em>metered · billed</em></header>
        <b title={dashboard?exactMoney(totals?.cost_usd || 0):undefined}>{dashboard?`${unpriced?'≥ ':''}${formatMoney(totals?.cost_usd || 0)}`:'Unavailable'}</b>
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
            <b>Unavailable</b>
            <span>No selected account is reporting a readable quota window.</span>
            <small>Unknown headroom is not full headroom.</small>
          </>}
      </button>
    </div>
    <button class="analytics-live-summary" onClick={()=>onOpen('activity')}>
      <strong>Agent activity</strong>
      <span>{sessions.filter(session=>['starting','running','working','idle','awaiting'].includes(session.state)&&session.backend!=='shell').length} live agents</span>
      <span>{sessions.filter(session=>session.state==='awaiting'&&session.backend!=='shell').length} waiting for input or approval</span>
      <small>Open runs and recorded activity →</small>
    </button>
    <p class="usage-overview-freshness">
      {usage?.cache?.updated_at
        ? `ccusage cache updated ${new Date(usage.cache.updated_at * 1000).toLocaleString()}`
        : 'No ccusage cache has been written yet.'}
      {accounts?.accounts.length ? ` · ${accounts.accounts.length} saved provider account(s)` : ''}
    </p>
  </main>
}
