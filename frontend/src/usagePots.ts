import { sumUsageRows, type UsageRow, type UsageSource } from './usageAnalytics'
import type { ProviderQuotaWindows, QuotaWindowDisplay } from './providerAccountDisplay'

// The three pots, reduced to what one tile each can hold.
//
// The whole point of the Overview is that these are never summed. Agent spend is a
// subscription read back out of transcripts and is an estimate; automation spend is a
// metered key billed by the call and is a measurement with a known hole in it; quota is a
// percentage of a window and is not money at all. A total across the three would be a
// number that is true of nothing, so nothing here returns one, and each pot carries the
// basis that makes its figure mean something.

export type AgentPot = {
  /** Cost and tokens over the cached window the tile names. */
  cost_usd: number
  total_tokens: number
  days: number
  /** The newest day present in the cache, and that day's row. */
  latest_date: string | null
  latest: UsageRow | null
  /** How many distinct historical sources contributed. */
  sources: number
}

/**
 * Agent subscription spend from the ccusage cache.
 *
 * The recent day is reported as *the newest cached day*, named by its date, rather than as
 * "today". The cache is refreshed manually or on a slow cadence, so its newest row is
 * routinely yesterday's or older, and a stale figure captioned "today" is the one reading
 * here that could send someone to look for a spike that has already been paid for.
 */
export function agentPot(sources: UsageSource[], days: number): AgentPot {
  const byDate = new Map<string, UsageRow[]>()
  for (const source of sources) for (const row of source.daily) {
    if (!row.date) continue
    const rows = byDate.get(row.date) || []
    rows.push(row)
    byDate.set(row.date, rows)
  }
  const dates = [...byDate.keys()].sort((a, b) => b.localeCompare(a))
  const window = dates.slice(0, days)
  const totals = sumUsageRows(window.flatMap(date => byDate.get(date) || []))
  const latest_date = dates[0] || null
  return {
    cost_usd: totals.cost_usd || 0,
    total_tokens: totals.total_tokens || 0,
    days,
    latest_date,
    latest: latest_date ? sumUsageRows(byDate.get(latest_date) || []) : null,
    sources: sources.length,
  }
}

export type QuotaHeadroom = {
  provider: string
  window: 'session' | 'weekly' | 'fable'
  used_percent: number
  /** What is left, which is the direction the tile is actually read in. */
  headroom_percent: number
  resets_at?: number | null
}

const WINDOW_LABELS: Record<QuotaHeadroom['window'], string> = {
  session: '5h',
  weekly: 'weekly',
  fable: 'Fable weekly',
}

export const quotaWindowLabel = (window: QuotaHeadroom['window']) => WINDOW_LABELS[window]

/**
 * The window closest to running out, across every provider's selected account.
 *
 * The tightest window is the only one worth a tile: quota is read to answer "am I about to
 * be cut off", and the answer is governed by whichever window empties first, not by an
 * average across windows that would hide it. Which window that is gets named, because "83%"
 * means something different on a 5-hour window than on a weekly one.
 *
 * Returns `null` when no selected account has a readable reading. That is deliberately not
 * "100% free": an account whose quota poll is erroring has unknown headroom, and rendering
 * unknown as full is the failure direction that matters.
 */
export function tightestQuota(windows: Record<string, ProviderQuotaWindows>): QuotaHeadroom | null {
  let worst: QuotaHeadroom | null = null
  for (const [provider, group] of Object.entries(windows)) {
    for (const name of ['session', 'weekly', 'fable'] as const) {
      const window: QuotaWindowDisplay | null | undefined = group[name]
      if (!window || typeof window.used_percent !== 'number') continue
      const used_percent = Math.max(0, Math.min(100, window.used_percent))
      if (worst && worst.used_percent >= used_percent) continue
      worst = {
        provider,
        window: name,
        used_percent,
        headroom_percent: 100 - used_percent,
        resets_at: window.resets_at,
      }
    }
  }
  return worst
}
