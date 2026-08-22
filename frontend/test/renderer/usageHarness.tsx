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
  cost_usd: cost,
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
      quota: { status: 'ok', session: { used_percent: 91.4, window_minutes: 300, resets_at: NOW + 3300 }, weekly: { used_percent: 38.2, window_minutes: 10080, resets_at: NOW + 300_000 }, refreshed_at: NOW },
    },
    {
      id: 'account-codex', provider: 'codex', label: 'personal', created_at: NOW, updated_at: NOW,
      quota: { status: 'ok', session: { used_percent: 12, window_minutes: 300, resets_at: NOW + 1200 }, weekly: { used_percent: 44.5, window_minutes: 10080, resets_at: NOW + 400_000 }, refreshed_at: NOW },
    },
  ],
}

const ROUTES: Array<[string, unknown]> = [
  ['/api/telemetry/workloads', TELEMETRY],
  ['/api/telemetry/operational', { schema_version: 1, interpretation: '', quota: { samples: [], resets: [], attributions: [], rollups: [] }, tools: { metrics: [], skills: [], unknown_or_unmapped: 0, parser_version: 'v1', parser_versions: {}, coverage: [] }, compactions: [] }],
  ['/api/automation/dashboard', DASHBOARD],
  ['/api/provider-accounts', ACCOUNTS],
  ['/api/usage', USAGE],
]

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  const match = ROUTES.find(([route]) => path.startsWith(route))
  return new Response(JSON.stringify(match ? match[1] : {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  <UsageModal onClose={() => {}} onConfigure={() => {}} />,
  document.querySelector('#root')!,
)
