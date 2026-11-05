import { locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
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
        <header><strong>{t("UsageOverview.33d92926a4")}</strong><em>{t("UsageOverview.aecc53f9f0")}</em></header>
        {usage?.enabled ? <>
            <b title={exactMoney(agents.cost_usd)}>{formatMoney(agents.cost_usd)}</b>
            <span>{rich("UsageOverview.0ac073e6ad", { value1: formatCount(agents.total_tokens), days: agents.days, value3: agents.latest_date || t("UsageOverview.606b075d81") })}</span>
            <small>{agents.latest_date ? t("UsageOverview.70a9d8c28d", { latest_date: agents.latest_date, value2: formatMoney(agents.latest?.cost_usd || 0) }) : t("UsageOverview.7c0c708a52")}</small>
          </> : <>
            <b class="usage-pot-off">{usage ? t("UsageOverview.ca7981b46e") : t("UsageOverview.ca18449697")}</b>
            <span>{usage ? t("UsageOverview.63671d44b6") : t("UsageOverview.2b4f3c4a95")}</span>
            <small>{t("UsageOverview.55ee5b02a7")}</small>
          </>}
      </button>
      <button class="usage-pot" onClick={() => onOpen('automation')}>
        <header><strong>{t("UsageOverview.d909750b1b")}</strong><em>{t("UsageOverview.d5dd1f4c70")}</em></header>
        <b title={dashboard ? exactMoney(totals?.cost_usd || 0) : undefined}>{dashboard ? t("UsageOverview.5c838b4d81", { value1: (unpriced) ? 'yes' : 'no', value2: formatMoney(totals?.cost_usd || 0) }) : t("UsageOverview.ca18449697")}</b>
        <span>{rich("UsageOverview.da4bd5c60d", { value1: formatCount(totals?.calls || 0), observerDays: observerDays })}</span>
        <small class={unpriced ? 'warn' : ''}>{unpriced ? t("UsageOverview.76112bcaf9", { value1: formatCount(unpriced) }) : t("UsageOverview.e168e6a207", { value1: formatMoney(dashboard?.spend_today.cost_usd || 0), value2: formatCount(totals?.today_calls || 0) })}</small>
      </button>
      <button class="usage-pot" onClick={() => onOpen('quota')}>
        <header><strong>{t("UsageOverview.002bb26db9")}</strong><em>{t("UsageOverview.425ecce917")}</em></header>
        {quota ? <>
            <b class={quota.headroom_percent <= 15 ? 'warn' : ''}>{Math.round(quota.headroom_percent)}%</b>
            <span>{rich("UsageOverview.778ebbda8a", { provider: quota.provider, value2: quotaWindowLabel(quota.window) })}</span>
            <small>{quota.resets_at ? t("UsageOverview.96a9c5fb0a", { value1: Math.round(quota.used_percent), value2: formatResetRemaining(quota.resets_at, now) }) : t("UsageOverview.04fa7c407e", { value1: Math.round(quota.used_percent) })}</small>
          </> : <>
            <b>{t("UsageOverview.ca18449697")}</b>
            <span>{t("UsageOverview.ed705e09e2")}</span>
            <small>{t("UsageOverview.d7525e33c0")}</small>
          </>}
      </button>
    </div>
    <button class="analytics-live-summary" onClick={()=>onOpen('activity')}>
      <strong>{t("UsageOverview.2ac2e731cf")}</strong>
      <span>{rich("UsageOverview.4341334a33", { length: sessions.filter(session=>['starting','running','working','idle','awaiting'].includes(session.state)&&session.backend!=='shell').length })}</span>
      <span>{rich("UsageOverview.0d1601bd8a", { length: sessions.filter(session=>session.state==='awaiting'&&session.backend!=='shell').length })}</span>
      <small>{t("UsageOverview.5772a41694")}</small>
    </button>
    <p class="usage-overview-freshness">
      {usage?.cache?.updated_at ? t("UsageOverview.bc73fc7ea3", { value1: new Date(usage.cache.updated_at * 1000).toLocaleString(uiLocale.value) }) : t("UsageOverview.34b26d3540")}
      {accounts?.accounts.length ? t("UsageOverview.93832beb0e", { length: accounts.accounts.length }) : ''}
    </p>
  </main>
}
