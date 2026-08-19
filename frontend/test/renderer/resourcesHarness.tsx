// The real Resources dialog over a stubbed daemon, opened on Tokens.
//
// It exists for the two surfaces the consolidation moved into it: the workload telemetry
// that used to sit in the Automation dashboard's health view, and the spend view that is
// now drawn from the *same component* in both places. Both are dense figures tables, which
// is exactly the class of surface a unit test cannot check and a real layout can.
//
// The workload fixture is the one `automationCostHarness` used, unchanged, so the
// human-scale formatting assertions that used to run against the dashboard now run against
// its new home and would notice a regression introduced by the move itself.
import { render } from 'preact'
import { ResourcesModal } from '../../src/ResourcesModal'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

const NOW = 1_770_000_000

const TELEMETRY = {
  since: 0,
  dimensions: [
    { backend: 'codex', model: 'unknown', runs: 1232, ended_runs: 191, average_duration_s: 8403.96, tokens_in: 9_664_898_958, tokens_out: 38_032_396, average_final_context_pct: 0.4338, average_peak_context_pct: 0.5432, turns_per_run: 0.0016, stalls_per_run: 0, approvals_per_run: 0.0008, completion_evidence_count: 0, completion_evidence_runs: 0 },
    { backend: 'claude', model: 'claude-opus-4-8', runs: 606, ended_runs: 63, average_duration_s: 5691.24, tokens_in: 35_087_852_487, tokens_out: 276_238_868, average_final_context_pct: 0.2592, average_peak_context_pct: 0.2662, turns_per_run: 2.4, stalls_per_run: 0.12, approvals_per_run: 0.34, completion_evidence_count: 84, completion_evidence_runs: 41 },
    { backend: 'claude', model: 'claude-sonnet-4-6', runs: 694, ended_runs: 0, average_duration_s: null, tokens_in: 1_024_543_568, tokens_out: 11_889_003, average_final_context_pct: 0.063, average_peak_context_pct: 0.063, turns_per_run: 0, stalls_per_run: 0, approvals_per_run: 0, completion_evidence_count: 0, completion_evidence_runs: 0 },
  ],
  event_counts: { turn_ended: 457 },
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
      { rule_id: 'builtin:scan-timeline', label: 'Scan timeline', detail: 'Per-run scans that extract timeline records', kind: 'feature', enabled: true, setting_label: '', calls: 214, tokens: 4_182_664, cost_usd: 1.8342, today_calls: 31, today_tokens: 612_004, today_cost_usd: 0.2611, models: ['anthropic/claude-sonnet-5'], last_at: NOW - 400 },
      { rule_id: 'custom.doc-drift', label: 'Doc drift watch', detail: '', kind: 'custom', enabled: true, setting_label: '', calls: 12, tokens: 9_004, cost_usd: 0.0004, today_calls: 1, today_tokens: 700, today_cost_usd: 0.00002, models: ['openai/gpt-5-mini'], last_at: NOW - 3600 },
    ],
    totals: { calls: 764, tokens: 4_608_904, cost_usd: 1.8849009, today_calls: 54, today_tokens: 628_904, today_cost_usd: 0.2617458 },
  },
}

const USAGE = {
  enabled: true, refreshing: false, refresh_minutes: 60, package: 'ccusage', install_command: '',
  states: {}, collector: { status: 'ready', refreshed_at: NOW }, sources: [], cache: {},
}

const ROUTES: Array<[string, unknown]> = [
  ['/api/telemetry/workloads', TELEMETRY],
  ['/api/telemetry/operational', { schema_version: 1, interpretation: '', quota: { samples: [], resets: [], attributions: [], rollups: [] }, tools: { metrics: [], skills: [], unknown_or_unmapped: 0, parser_version: 'v1', parser_versions: {}, coverage: [] }, compactions: [] }],
  ['/api/automation/dashboard', DASHBOARD],
  ['/api/usage', USAGE],
]

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  const match = ROUTES.find(([route]) => path.startsWith(route))
  return new Response(JSON.stringify(match ? match[1] : {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

const PROJECTS: Project[] = [{ id: 'project', name: 'swe-mux', root: 'D:/PROJECTS/swe-mux' } as Project]
const SESSIONS: Session[] = []

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
render(
  <ResourcesModal
    initial="tokens"
    sessions={SESSIONS}
    projects={PROJECTS}
    onClose={() => {}}
    onAttached={() => {}}
    onConfigureUsage={() => {}}
  />,
  document.querySelector('#root')!,
)
