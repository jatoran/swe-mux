// The real Usage dialog over a stubbed daemon, opened on Overview.
//
// Two things live here that no unit test can see.
//
// The Overview itself. Its whole claim is that three figures of three different kinds can be
// compared at a glance without ever being added up, and whether that reads is a layout
// question: the tiles have to sit side by side, each basis label has to survive next to its
// figure, and none of it may push the caveat that explains the arrangement off the screen.
//
// The spend table, which is the *same component* the Automation dashboard draws. Its
// human-scale formatting assertions used to run against the Resources harness and moved
// with it. If the two surfaces ever disagree, the mirroring has been re-implemented as a
// copy, and this is where that shows up.
import { render } from 'preact'
import { UsageModal } from '../../src/UsageModal'
import '../../src/deviceMode'
import '../../src/style.css'

const NOW = 1_770_000_000

const TELEMETRY = {
  since: 0,
  dimensions: [],
  event_counts: {},
  interpretation: 'observational_correlation_only',
  observer_spend: { tokens: 2269, cost_usd: 0.0006258 },
  provider_cost_dimensions: [
    { backend: 'claude', model: 'claude-fable-5', tokens: 5_545_899_176, cost_usd: 8600.754787, cost_is_estimate: true, attribution: 'ccusage_provider_model_aggregate' },
    { backend: 'claude', model: 'claude-haiku-4-5-20251001', tokens: 1_444_576_243, cost_usd: 354.726475, cost_is_estimate: true, attribution: 'ccusage_provider_model_aggregate' },
  ],
  cost_note: 'ccusage costs are backend/model aggregates and are not attributed to individual runs',
}

const DASHBOARD = {
  observer_calls: { cancelled: 39, completed: 446, failed: 196 },
  unread_notifications: 0,
  spend_today: { tokens: 2269, cost_usd: 0.0006258 },
  spend_breakdown: {
    days: 7, today: '2026-08-15', start_day: '2026-08-09',
    rules: [
      { rule_id: 'builtin:scan-timeline', label: 'Scan timeline', detail: 'Per-run scans that extract timeline records', kind: 'feature', enabled: true, setting_label: '', calls: 214, tokens: 4_182_664, cost_usd: 1.8342, today_calls: 31, today_tokens: 612_004, today_cost_usd: 0.2611, input_tokens: 3_900_000, cached_tokens: 3_000_000, today_input_tokens: 580_000, today_cached_tokens: 460_000, models: ['anthropic/claude-sonnet-5'], last_at: NOW - 400 },
      { rule_id: 'custom.doc-drift', label: 'Doc drift watch', detail: '', kind: 'custom', enabled: true, setting_label: '', calls: 12, tokens: 9_004, cost_usd: 0.0004, today_calls: 1, today_tokens: 700, today_cost_usd: 0.00002, models: ['openai/gpt-5-mini'], last_at: NOW - 3600 },
    ],
    totals: { calls: 764, tokens: 4_608_904, cost_usd: 1.8849009, today_calls: 54, today_tokens: 628_904, today_cost_usd: 0.2617458, input_tokens: 4_150_000, cached_tokens: 3_000_000, today_input_tokens: 594_000, today_cached_tokens: 460_000 },
  },
}

const daily = (date: string, cost: number, tokens: number) => ({
  date,
  input_tokens: Math.round(tokens * 0.08),
  output_tokens: Math.round(tokens * 0.02),
  cache_creation_tokens: Math.round(tokens * 0.1),
  cache_read_tokens: Math.round(tokens * 0.8),
  total_tokens: tokens,
  cost_usd: cost, cost_method: 'source_estimate',
})

const USAGE = {
  enabled: true, refreshing: false, refresh_minutes: 60, package: 'ccusage', install_command: '',
  collector: { status: 'ready', refreshed_at: NOW },
  cache: {
    version: 3,
    updated_at: NOW,
    sources: {
      'claude-code': {
        source_id: 'claude-code', source_label: 'Claude Code', collector_id: 'ccusage',
        daily: [
          daily('2026-08-15', 214.5512, 6_120_040_000),
          daily('2026-08-14', 188.204, 5_402_118_000),
          daily('2026-08-13', 96.7731, 2_884_502_000),
        ],
        monthly: [], sessions: [], models: [],
        totals: daily('2026-08', 499.5283, 14_406_660_000),
      },
      codex: {
        source_id: 'codex', source_label: 'Codex', collector_id: 'ccusage',
        daily: [
          daily('2026-08-15', 12.114, 402_881_000),
          daily('2026-08-14', 9.55, 331_004_000),
        ],
        monthly: [], sessions: [], models: [],
        totals: daily('2026-08', 21.664, 733_885_000),
      },
    },
  },
}

const ACCOUNTS = {
  providers: ['claude', 'codex'],
  selected: { claude: 'account-claude', codex: 'account-codex' },
  current: {},
  poll_minutes: 5,
  stale_minutes: 30,
  refreshing: false,
  accounts: [
    {
      id: 'account-claude', provider: 'claude', label: 'work', created_at: NOW, updated_at: NOW,
      // 91% of a 5h window: the tightest reading, so this is the one the tile has to pick.
      quota: {
        status: 'ready',
        session: { used_percent: 91.4, window_minutes: 300, resets_at: NOW + 3300 },
        // Where this week's 38.2% came from; parts sum to the reading.
        weekly: {
          used_percent: 38.2, window_minutes: 10080, resets_at: NOW + 300_000,
          sources: { since: NOW + 300_000 - 604_800, reset_seen: true, swe_mux: 24, outside: 1.2, elsewhere: 11, uncertain: 0, untracked: 2 },
        },
        refreshed_at: NOW,
      },
    },
    {
      id: 'account-codex', provider: 'codex', label: 'personal', created_at: NOW, updated_at: NOW,
      quota: { status: 'ready', session: { used_percent: 12, window_minutes: 300, resets_at: NOW + 1200 }, weekly: { used_percent: 44.5, window_minutes: 10080, resets_at: NOW + 400_000 }, refreshed_at: NOW },
    },
  ],
}

const ROUTES: Array<[string, unknown]> = [
  ['/api/telemetry/v2/workload', {dimensions:[]}],
  ['/api/telemetry/v2/runs', {matching:0,items:[],next_cursor:null}],
  ['/api/telemetry/v2/quality', {totals:{calls:0,with_duration:0},backends:[],versions:[],capabilities:{},parsers:[]}],
  ['/api/telemetry/v2/shadow', {legacy_dashboard_enabled:false}],
  ['/api/telemetry/workloads', TELEMETRY],
  ['/api/automation/dashboard', DASHBOARD],
  ['/api/provider-accounts', ACCOUNTS],
  ['/api/usage', USAGE],
]

// Quota history over whatever range the view asked for: a 15-minute poll of two accounts,
// each weekly window climbing to its own reset, with the matching daily UTC rollups. Built
// from the request's `since`/`until` because the view bounds its query on the live clock.
const QUOTA_ACCOUNTS = [
  { id: 'account-claude', provider: 'claude', uuid: 'uuid-claude', rate: 0.95, offset: 2 * 86400 },
  { id: 'account-codex', provider: 'codex', uuid: 'uuid-codex', rate: 0.6, offset: 5 * 86400 },
]
const WEEK = 7 * 86400
const weeklyUsed = (time: number, account: typeof QUOTA_ACCOUNTS[number]) =>
  Math.round(((time + account.offset) % WEEK) / WEEK * 100 * account.rate)
const sessionUsed = (time: number) => Math.round((time % 18000) / 18000 * 70)
const SOURCES = ['swe_mux', 'outside', 'elsewhere', 'uncertain'] as const
// Where each rise came from: the claude account is used elsewhere in the small hours
// (UTC), the codex account outside swe-mux late in the morning, swe-mux otherwise.
const sourceOf = (time: number, account: typeof QUOTA_ACCOUNTS[number]) => {
  const hour = new Date(time * 1000).getUTCHours()
  if (account.provider === 'claude' && hour >= 2 && hour < 6) return 'elsewhere'
  if (account.provider === 'codex' && hour >= 10 && hour < 12) return 'outside'
  return 'swe_mux'
}
const rise = (before: number, after: number) => after >= before ? after - before : after
const parts = (amount: number, source: string) => ({
  rise: amount, reset: false, ...Object.fromEntries(SOURCES.map(item => [item, item === source ? amount : 0])),
})

function quotaSeries(path: string) {
  const query = new URL(path, 'http://harness').searchParams
  const until = Number(query.get('until')) || Date.now() / 1000
  const since = Number(query.get('since')) || until - WEEK
  const resolution = query.get('resolution') === 'raw' ? 'raw' : 'daily'
  const first = Math.ceil(since / 900) * 900
  const series = QUOTA_ACCOUNTS.map(account => {
    const samples: Array<Record<string, unknown>> = []
    for (let time = first; time <= until; time += 900) {
      samples.push({
        id: time, provider: account.provider, account_id: account.id, provider_account_uuid: account.uuid,
        sampled_at: time, status: 'ready', freshness: 'fresh', raw_precision: 0, active: true,
        session: { used_percent: sessionUsed(time), resets_at: Math.ceil(time / 18000) * 18000 },
        weekly: { used_percent: weeklyUsed(time, account), resets_at: time - ((time + account.offset) % WEEK) + WEEK },
      })
    }
    const used = (sample: Record<string, unknown>, window: string) => (sample[window] as { used_percent: number }).used_percent
    const intervals = samples.slice(1).map((sample, index) => {
      const previous = samples[index], end = sample.sampled_at as number, source = sourceOf(end, account)
      const session = rise(used(previous, 'session'), used(sample, 'session'))
      const weekly = rise(used(previous, 'weekly'), used(sample, 'weekly'))
      return {
        sample_id: end, provider: account.provider, account_id: account.id, provider_account_uuid: account.uuid,
        start: previous.sampled_at, end, billing: source === 'elsewhere' ? 'not_selected' : 'selected',
        requests: source === 'swe_mux' ? 40 : 0, runs: source === 'swe_mux' ? 1 : 0, cost_usd: source === 'swe_mux' ? 6.5 : 0,
        session: parts(session, source), weekly: parts(weekly, source),
      }
    }).filter(item => item.session.rise > 0 || item.weekly.rise > 0)
    if (resolution === 'raw') return { provider: account.provider, account_id: account.id, provider_account_uuid: account.uuid, identity: 'verified', points: samples, usage: intervals }
    const days = new Map<string, Array<Record<string, unknown>>>()
    for (const sample of samples) {
      const day = new Date((sample.sampled_at as number) * 1000).toISOString().slice(0, 10)
      days.set(day, [...(days.get(day) || []), sample])
    }
    const usageDays = new Map<string, typeof intervals>()
    for (const item of intervals) {
      const day = new Date(item.end * 1000).toISOString().slice(0, 10)
      usageDays.set(day, [...(usageDays.get(day) || []), item])
    }
    const sum = (items: typeof intervals, window: 'session' | 'weekly') => Object.fromEntries(
      ['rise', ...SOURCES].map(key => [key, items.reduce((total, item) => total + Number((item[window] as Record<string, unknown>)[key]), 0)]),
    )
    const usage = [...usageDays].map(([day, items]) => ({
      day, requests: items.reduce((total, item) => total + item.requests, 0),
      cost_usd: items.reduce((total, item) => total + item.cost_usd, 0),
      session: sum(items, 'session'), weekly: sum(items, 'weekly'),
    }))
    const points = [...days].map(([day, rows]) => ({
      provider: account.provider, account_id: account.id, provider_account_uuid: account.uuid, day, samples: rows.length, errors: 0,
      ...Object.fromEntries(['session', 'weekly'].flatMap(window => {
        const values = rows.map(row => used(row, window))
        return [[`${window}_first`, values[0]], [`${window}_last`, values[values.length - 1]], [`${window}_min`, Math.min(...values)], [`${window}_max`, Math.max(...values)]]
      })),
    }))
    return { provider: account.provider, account_id: account.id, provider_account_uuid: account.uuid, identity: 'verified', points, usage }
  })
  const resets = QUOTA_ACCOUNTS.flatMap(account => {
    const observed = until - ((until + account.offset) % WEEK)
    return observed >= since ? [{
      id: `reset-${account.id}`, provider: account.provider, account_id: account.id, window: 'weekly',
      before_value: Math.round(100 * account.rate), after_value: 0, observed_at: observed, expected_reset_at: observed,
      classification: 'scheduled', confidence: 'high', confirmed: 1,
    }] : []
  })
  return { schema_version: 1, interpretation: 'quota_utilization_not_token_usage', resolution, since, until, series, resets }
}

// The operational snapshot's quota half is the last two days of the same intervals, newest first.
function operational() {
  const until = Date.now() / 1000
  const raw = quotaSeries(`/api/telemetry/quota-series?resolution=raw&since=${until - 2 * 86400}&until=${until}`)
  const usage = raw.series.flatMap(item => (item.usage || []) as Array<{ end: number }>).sort((a, b) => b.end - a.end)
  return {
    schema_version: 1, interpretation: '', quota: { samples: [], resets: [], usage_intervals: usage, rollups: [] },
    tools: { metrics: [], skills: [], unknown_or_unmapped: 0, parser_version: 'v1', parser_versions: {}, coverage: [] }, compactions: [],
  }
}

// Every URL the dialog asked for, so a spec can check how a request was built.
const requested: string[] = []
Object.assign(window, { requested })

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  requested.push(path)
  const match = ROUTES.find(([route]) => path.startsWith(route))
  const body = path.startsWith('/api/telemetry/quota-series') ? quotaSeries(path)
    : path.startsWith('/api/telemetry/operational') ? operational()
    : match ? match[1] : {}
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  <UsageModal onClose={() => {}} onConfigure={() => {}} />,
  document.querySelector('#root')!,
)
