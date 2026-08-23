/**
 * Formatting and the row model behind the automation dashboard's cost view.
 *
 * Two problems live here, and both are about a number being technically present and
 * practically unreadable.
 *
 * The first is scale. These tables mix `$0.0006258` of observer spend with `$8,600.75` of
 * agent spend, and `2,269` tokens with `9,664,898,958`. One formatter cannot serve both, and
 * the fixed four-decimal currency format the dashboard used printed the large figure as
 * `$8,600.7548` — eleven characters of which two matter — which is most of why every table
 * here truncated. So money and counts switch representation by magnitude, and the exact value
 * stays available as a title rather than being thrown away.
 *
 * The second is attribution. `spend_today` is a single number, and a single number cannot be
 * acted on: turning something off requires knowing which something. The daemon now groups its
 * ledger by rule, and `buildSpendRows` turns that into ranked rows carrying each one's share
 * of the total, so the answer to "what is costing me" is the first row rather than an
 * inference across three screens.
 *
 * Pure, so the magnitude thresholds and the share arithmetic are testable without a DOM.
 */

export type SpendRule = {
  rule_id: string
  label?: string
  detail?: string
  /** `observer` and `custom` are automation rules; `feature` bills the same budget without
   *  being one; `retired` billed under an id nothing on the page can turn off any more. */
  kind?: 'observer' | 'custom' | 'feature' | 'retired'
  enabled?: boolean
  setting_label?: string
  calls: number
  tokens: number
  cost_usd: number
  today_calls: number
  today_tokens: number
  today_cost_usd: number
  /** Prompt tokens only. `tokens` includes output, which was never cacheable, so it is the
   *  wrong denominator for a hit rate. Absent on a daemon predating cache accounting. */
  input_tokens?: number
  /** Prompt tokens the provider served from its cache — a subset of `input_tokens`, never
   *  added to it. */
  cached_tokens?: number
  /** Prompt tokens written *into* the cache. Also a subset of `input_tokens`, and disjoint
   *  from `cached_tokens`: a token was either served from the cache or written into it. */
  cache_write_tokens?: number
  /** Signed price effect of caching. Positive saved money; negative is the write premium
   *  (1.25x input on GPT-5.6 and Anthropic) exceeding what was read back. */
  cache_discount_usd?: number
  today_input_tokens?: number
  today_cached_tokens?: number
  today_cache_write_tokens?: number
  today_cache_discount_usd?: number
  /** Calls whose provider reported no cost at all. Their `cost_usd` contribution is 0
   *  because nobody measured it, not because it was free, so a nonzero count here means
   *  the money beside it is a floor. See `src/swe_mux/budget.py`. */
  unpriced_calls?: number
  today_unpriced_calls?: number
  models?: string[]
  last_at?: number
}

export type SpendBreakdown = {
  days: number
  today: string
  start_day: string
  rules: SpendRule[]
  totals: {
    calls: number
    tokens: number
    cost_usd: number
    today_calls: number
    today_tokens: number
    today_cost_usd: number
    input_tokens?: number
    cached_tokens?: number
    cache_write_tokens?: number
    cache_discount_usd?: number
    today_input_tokens?: number
    today_cached_tokens?: number
    today_cache_write_tokens?: number
    today_cache_discount_usd?: number
    unpriced_calls?: number
    today_unpriced_calls?: number
  }
}

export type SpendRow = SpendRule & {
  label: string
  kind: 'observer' | 'custom' | 'feature' | 'retired'
  /** Fraction of the window's total cost, for the inline share bar. */
  share: number
  /** Fraction of the window's calls — the honest denominator when everything cost ~nothing. */
  callShare: number
}

const usd = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
const integer = new Intl.NumberFormat()
const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 })

/**
 * Currency at whatever precision the magnitude actually carries.
 *
 * Sub-cent observer calls and four-figure agent bills share these tables, so a fixed number of
 * decimals is wrong for one of them by construction. Below a hundredth of a cent the digits
 * stop meaning anything at all and the row says so rather than printing `$0.0000`, which reads
 * as free.
 */
export function formatMoney(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '$0'
  const magnitude = Math.abs(value)
  if (magnitude >= 0.01) return usd.format(value)
  if (magnitude >= 0.0001) return `$${value.toFixed(4)}`
  return value > 0 ? '<$0.0001' : '>-$0.0001'
}

/** The exact figure, for the title of whatever cell shows the rounded one. Eight decimals
 *  because a single cheap-model call lands around the sixth, and rounding the title too
 *  would leave the precise number nowhere at all. */
export const exactMoney = (value: number) =>
  `$${(Number.isFinite(value) ? value : 0).toFixed(8).replace(/0+$/, '').replace(/\.$/, '')}`

/**
 * Counts stay exact while they are readable and go compact once they are not.
 *
 * Token totals here reach ten figures. `9,664,898,958` is thirteen characters that no reader
 * compares against anything; `9.7B` is four that they can.
 */
export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return '0'
  return Math.abs(value) < 100_000 ? integer.format(value) : compact.format(value)
}

/** Seconds as the largest two units that matter — `8404s` is a duration nobody reads. */
export function formatDuration(seconds: number | null | undefined): string {
  const total = Math.round(Number(seconds) || 0)
  if (total <= 0) return '—'
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`
  const hours = Math.floor(total / 3600)
  const minutes = Math.round((total % 3600) / 60)
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

export const formatPercent = (fraction: number | null | undefined) =>
  `${Math.round((Number(fraction) || 0) * 100)}%`

/** A measured prompt-cache reading, or `null` when there is nothing to measure. */
export type CacheHit = { rate: number; cached: number; prompt: number }

/**
 * What fraction of a row's prompt tokens the provider served from its cache.
 *
 * `null` and `0%` are deliberately different answers. Null is "no billed prompt tokens in this
 * window", which is what an unused rule and a pre-cache-accounting daemon both look like, and
 * printing 0% for either would accuse a working cache of being broken. Zero is a real reading:
 * prompt tokens were billed and none of them were cached.
 *
 * The denominator is prompt tokens, never `tokens` — output is not cacheable, so including it
 * would cap the achievable rate below 100% by an amount that varies with reply length.
 *
 * One honest limit: a provider that caches implicitly but reports no `cached_tokens` reads as
 * 0% here. The ledger records what the usage payload said and nothing more.
 */
export function cacheHit(
  promptTokens: number | undefined,
  cachedTokens: number | undefined,
): CacheHit | null {
  const prompt = Number(promptTokens || 0)
  const cached = Number(cachedTokens || 0)
  if (!Number.isFinite(prompt) || prompt <= 0) return null
  return { rate: Math.min(1, Math.max(0, cached / prompt)), cached, prompt }
}

/** The cell's tooltip: the two exact figures the rounded percentage came from. */
export const cacheHitDetail = (hit: CacheHit | null) =>
  hit ? `${integer.format(hit.cached)} of ${integer.format(hit.prompt)} prompt tokens served from cache`
      : 'no billed prompt tokens in this window'

/** What caching did to the bill, as opposed to how often it hit. */
export type CacheEconomics = {
  /** Signed dollars: positive saved, negative is a write premium never read back. */
  discount: number
  written: number
  cached: number
  /** True when the cache cost more than it returned - the shape of a breakpoint
   *  sitting above content that changes every call. */
  netLoss: boolean
}

/**
 * The half of prompt caching a hit rate cannot show.
 *
 * A run whose every call writes a cache and never reads one reports 0% hit and
 * looks exactly like a run with no caching at all - while costing 25% more per
 * prompt token, because GPT-5.6 and Anthropic bill a write at 1.25x input. The
 * write count is what separates those two, and the signed discount is what
 * prices the difference.
 *
 * `null` when the provider reported neither figure, which is not zero: it means
 * this endpoint says nothing about caching, and inventing a $0.00 saving for it
 * would put a confident number where there is no measurement.
 */
export function cacheEconomics(
  discountUsd: number | undefined,
  writeTokens: number | undefined,
  cachedTokens: number | undefined,
): CacheEconomics | null {
  const discount = Number(discountUsd || 0)
  const written = Number(writeTokens || 0)
  const cached = Number(cachedTokens || 0)
  if (!discount && !written && !cached) return null
  return { discount, written, cached, netLoss: discount < 0 }
}

/** The tile's second line: what the cache cost or saved, and how much it wrote. */
export const cacheEconomicsDetail = (economics: CacheEconomics | null) => {
  if (!economics) return 'this provider reports no cache figures'
  const money = economics.discount >= 0
    ? `${formatMoney(economics.discount)} saved`
    : `${formatMoney(Math.abs(economics.discount))} of write premium not read back`
  return `${money} · ${integer.format(economics.written)} tokens written, ${integer.format(economics.cached)} read`
}

/**
 * Ranked spend rows with each one's share of the window.
 *
 * Ranking is by window cost, not by today's, so a rule that ran expensively yesterday and not
 * yet today does not drop out of sight — the point of the view is to find what to turn off,
 * and that decision is about the habit rather than the current day.
 *
 * When nothing has cost measurable money the share falls back to call count, because a bar
 * chart of zeroes hides the one row making hundreds of calls.
 */
export function buildSpendRows(breakdown: SpendBreakdown | null | undefined): SpendRow[] {
  const rules = breakdown?.rules || []
  const costTotal = rules.reduce((sum, rule) => sum + (rule.cost_usd || 0), 0)
  const callTotal = rules.reduce((sum, rule) => sum + (rule.calls || 0), 0)
  return rules
    .map(rule => ({
      ...rule,
      label: rule.label || rule.rule_id,
      kind: rule.kind || 'retired',
      share: costTotal > 0 ? (rule.cost_usd || 0) / costTotal : 0,
      callShare: callTotal > 0 ? (rule.calls || 0) / callTotal : 0,
    }))
    .sort((left, right) => (right.cost_usd || 0) - (left.cost_usd || 0) || (right.calls || 0) - (left.calls || 0))
}

/** The bar's width: cost share when there is cost to share, call share otherwise. */
export const spendBarShare = (row: SpendRow, costTotal: number) =>
  costTotal > 0 ? row.share : row.callShare

/**
 * A one-line reading of the observer call log.
 *
 * The dashboard summed these lifetime status counts under the heading "calls today", which was
 * wrong in both directions: the number was every call ever made, and today's real figure lives
 * in the ledger. Failures are pulled out because they are the actionable half — a rule failing
 * two hundred times is still being billed for its input.
 */
export function callHealth(counts: Record<string, number> | undefined): {
  total: number; failed: number; failureRate: number
} {
  const entries = Object.entries(counts || {})
  const total = entries.reduce((sum, [, count]) => sum + (Number(count) || 0), 0)
  const failed = entries
    .filter(([status]) => status === 'failed' || status === 'cancelled')
    .reduce((sum, [, count]) => sum + (Number(count) || 0), 0)
  return { total, failed, failureRate: total > 0 ? failed / total : 0 }
}
