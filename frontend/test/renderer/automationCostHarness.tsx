// The real automation dashboard over a stubbed daemon, opened on the spend tab.
//
// The fixture is shaped from a live fleet rather than invented: ten-figure token counts, a
// four-figure agent bill beside sub-cent observer calls, a rule that bills without appearing
// in the rule list, and one that bills under an id nothing can turn off any more. Those are
// exactly the rows that used to truncate or read as free.
import { render } from 'preact'
import { AutomationDashboard } from '../../src/AutomationDashboard'
import '../../src/style.css'
import { SETTINGS_CONFIG_FIXTURE } from './settingsConfigFixture'
import type { Project } from '../../src/types'

const NOW = 1_770_000_000

const builtin = (id: string, name: string, automationId: string, settingLabel: string, enabled: boolean, description: string) => ({
  id, name, automation_id: automationId, setting_label: settingLabel, enabled, shadow: false,
  source: 'builtin', trigger: 'turn_ended', input: 'Last completed turn', model: 'Cheap model',
  result: 'Run note', description,
})

const DASHBOARD = {
  observer_calls: { cancelled: 39, completed: 446, failed: 196 },
  annotations: { title: 409, provenance: 512 },
  unread_notifications: 2,
  spend_today: { tokens: 2269, cost_usd: 0.0006258 },
  controls: { automation_enabled: true, scan_timeline_enabled: true },
  engine: {
    enabled: true,
    rules: [{ id: 'custom.doc-drift', name: 'Doc drift watch', enabled: true, shadow: false, trigger: 'turn_ended', revision: 'r3', source: 'file', actions: [{ kind: 'llm', model: 'openai/gpt-5-mini', on_result: { kind: 'annotate' } }] }],
    built_in_rules: [
      builtin('builtin.session-titler-initial', 'Session titler', 'session_titler', 'Session titler', true, 'Names a pane from its opening request, then refines the title while the work is still taking shape.'),
      builtin('builtin.session-titler', 'Session titler (no prompt)', 'session_titler', 'Session titler', true, 'Fallback for runs with no captured request, such as Codex.'),
      builtin('builtin.stalled-triage', 'Stalled run triage', 'attention_observers', 'Attention observers', false, 'Explains whether a detected stall appears to need user attention.'),
    ],
    queue: { size: 0, capacity: 256, dropped: 0, loop_rejections: 0 },
    capabilities: { triggers: [], observer_schemas: [] },
  },
  provider: { secret: { configured: true, source: 'stored' }, models: { models: [], stale: false }, cheap_model: 'openai/gpt-5-mini', standard_model: 'anthropic/claude-sonnet-5' },
  recent_firings: [], recent_action_results: [], recent_observer_calls: [], recent_annotations: [],
  spend_breakdown: {
    days: 7, today: '2026-08-15', start_day: '2026-08-09',
    rules: [
      // The expensive one is a feature, not a rule: invisible before this view existed.
      { rule_id: 'builtin:scan-timeline', label: 'Scan timeline', detail: 'Per-run scans that extract timeline records', kind: 'feature', enabled: true, setting_label: '', calls: 214, tokens: 4_182_664, cost_usd: 1.8342, today_calls: 31, today_tokens: 612_004, today_cost_usd: 0.2611, input_tokens: 3_900_000, cached_tokens: 3_000_000, today_input_tokens: 580_000, today_cached_tokens: 460_000, models: ['anthropic/claude-sonnet-5'], last_at: NOW - 400 },
      { rule_id: 'builtin.session-titler-initial', label: 'Session titler', detail: 'Names a pane once, from the request that started the run.', kind: 'observer', enabled: true, setting_label: 'Session titler', calls: 409, tokens: 288_114, cost_usd: 0.0412, today_calls: 22, today_tokens: 16_204, today_cost_usd: 0.0006258, input_tokens: 250_000, cached_tokens: 0, today_input_tokens: 14_000, today_cached_tokens: 0, models: ['openai/gpt-5-mini'], last_at: NOW - 90 },
      { rule_id: 'builtin:voice-summary', label: 'Read aloud', detail: 'Spoken summaries of agent replies', kind: 'feature', enabled: true, setting_label: '', calls: 88, tokens: 41_002, cost_usd: 0.0091, today_calls: 0, today_tokens: 0, today_cost_usd: 0, models: ['openai/gpt-5-mini'], last_at: NOW - 90_000 },
      { rule_id: 'custom.doc-drift', label: 'Doc drift watch', detail: '', kind: 'custom', enabled: true, setting_label: '', calls: 12, tokens: 9_004, cost_usd: 0.0004, today_calls: 1, today_tokens: 700, today_cost_usd: 0.00002, models: ['openai/gpt-5-mini'], last_at: NOW - 3600 },
      // Billed under an id the page has no control for: it must still be visible.
      { rule_id: 'builtin.removed-triage', label: 'builtin.removed-triage', detail: '', kind: 'retired', enabled: false, setting_label: '', calls: 41, tokens: 88_120, cost_usd: 0.0000009, today_calls: 0, today_tokens: 0, today_cost_usd: 0, models: [], last_at: NOW - 400_000 },
    ],
    totals: { calls: 764, tokens: 4_608_904, cost_usd: 1.8849009, today_calls: 54, today_tokens: 628_904, today_cost_usd: 0.2617458, input_tokens: 4_150_000, cached_tokens: 3_000_000, today_input_tokens: 594_000, today_cached_tokens: 460_000 },
  },
}

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
    { backend: 'claude', model: 'claude-opus-4-5-20251101', tokens: 31_415_777, cost_usd: 50.176618, cost_is_estimate: true, attribution: 'ccusage_provider_model_aggregate' },
  ],
  cost_note: 'ccusage costs are backend/model aggregates and are not attributed to individual runs',
}

// `install_default` is the daemon's resolved answer for an id no Project file
// mentions - the operator's `project_defaults` merged over the registry's own
// `default_on`, closure completed. Both halves ship because the Default column
// renders the first and writes the second, and a fixture carrying only one of
// them could not tell an inherited row from a pinned one.
const MATRIX = {
  automations: [
    { id: 'raw_store', kind: 'substrate', label: 'Raw event store', requires: [], implemented: true, spends: false, install_default: true, globally_allowed: true, install_switch: null },
    { id: 'tier0', kind: 'substrate', label: 'Tier 0 facts', requires: ['raw_store'], implemented: true, spends: false, install_default: true, globally_allowed: true, install_switch: null },
    { id: 'scan_timeline', kind: 'substrate', label: 'Scan timeline', requires: ['raw_store', 'tier0'], implemented: true, spends: true, needs_llm: true, install_default: false, globally_allowed: true, install_switch: 'scan_timeline_enabled' },
    { id: 'loop_detection', kind: 'consumer', label: 'Loop detection', requires: ['tier0'], implemented: true, spends: false, install_default: true, globally_allowed: true, install_switch: null },
    // One row under the install-wide ceiling, so the spec can see the greyed cell.
    { id: 'doc_debt', kind: 'consumer', label: 'Doc-debt ledger', requires: ['tier0'], implemented: true, spends: false, install_default: false, globally_allowed: false, install_switch: null },
    { id: 'session_control', kind: 'consumer', label: 'Agent session control', requires: [], implemented: true, spends: false, default_on: true, install_default: true, globally_allowed: true, install_switch: null },
    { id: 'catch_me_up', kind: 'consumer', label: 'Catch-me-up digest', requires: ['scan_timeline'], implemented: true, spends: false, install_default: false, globally_allowed: true, install_switch: null },
    { id: 'cross_session_interlocks', kind: 'consumer', label: 'Cross-session interlocks', requires: ['tier0'], implemented: false, spends: false, install_default: false, globally_allowed: true, install_switch: null },
  ],
  projects: [
    // p1 pins loop detection off against an install that defaults it on, so the
    // Project cell has a state neither "follow global" nor the inherited answer.
    { project_id: 'p1', project_name: 'swe-mux', status: 'ready', revision: 'r1', requested: { raw_store: true, tier0: true, loop_detection: false, doc_debt: true }, enabled: ['raw_store', 'tier0', 'session_control'], blocked: {}, unverified: [], globally_disabled: ['doc_debt'], llm: { ready: true, reason: '' }, scan_timeline_auto_enable: false, scan_timeline_auto_enable_own: null, authority: { session_control_grant: 'draft' }, authority_effective: { session_control_grant: 'draft', spawn_grant: 'granted', land_grant: 'draft', land_verify_grant: 'granted', interject_grant: 'granted', message_envelope: 'compact' } },
    // p2 wrote nothing at all and runs the install's defaults.
    { project_id: 'p2', project_name: 'orca', status: 'ready', revision: 'r2', requested: {}, enabled: ['raw_store', 'tier0', 'loop_detection', 'session_control'], blocked: {}, unverified: [], globally_disabled: [], llm: { ready: true, reason: '' }, scan_timeline_auto_enable: false, scan_timeline_auto_enable_own: null, authority: {}, authority_effective: { session_control_grant: 'granted', spawn_grant: 'granted', land_grant: 'draft', land_verify_grant: 'granted', interject_grant: 'granted', message_envelope: 'compact' } },
  ],
  global_allow: { doc_debt: false },
  project_defaults: { raw_store: true, tier0: true, loop_detection: true },
  scan_timeline_auto_enable_default: false,
  install_switches: { automation_enabled: true, scan_timeline_enabled: true, scheduled_runs_enabled: true, land_queue_enabled: true },
  authority_fields: [
    { field: 'session_control_grant', label: 'Interrupt and end sessions', levels: ['draft', 'granted'], builtin: 'granted', gated_by: 'session_control' },
    { field: 'spawn_grant', label: 'Start new sessions here', levels: ['draft', 'granted'], builtin: 'granted', gated_by: 'session_control' },
    { field: 'land_grant', label: 'Land a branch onto the trunk', levels: ['draft', 'granted'], builtin: 'draft', gated_by: 'land_queue' },
    { field: 'land_verify_grant', label: 'Change the verification command', levels: ['draft', 'granted'], builtin: 'granted', gated_by: 'land_queue' },
    { field: 'interject_grant', label: 'Write into a running turn', levels: ['off', 'granted'], builtin: 'granted', gated_by: null },
    { field: 'message_envelope', label: 'Metadata on delivered agent messages', levels: ['full', 'compact', 'bare'], builtin: 'compact', gated_by: null },
  ],
  authority_default: {},
  authority_ceiling: {},
}

const GRANTS = {
  install: [], values: {}, automations: MATRIX.automations,
  recommended_project_automations: ['loop_detection'],
  project_starting_sets: {
    recommended: { automations: ['loop_detection'], values: {} },
    llm: { automations: ['scan_timeline'], values: { scan_timeline_auto_enable: true } },
    autonomy: { automations: ['session_control'], values: {} },
  },
  llm: { ready: true, reason: '' },
}

const ROUTES: Array<[string, unknown]> = [
  ['/api/projects/p1/automations', { revision: 'r1', requested: MATRIX.projects[0].requested, enabled: MATRIX.projects[0].enabled, blocked: {}, unverified: [], automations: MATRIX.automations, scan_timeline_auto_enable: false }],
  ['/api/projects/p2/automations', { revision: 'r2', requested: {}, enabled: [], blocked: {}, unverified: [], automations: MATRIX.automations, scan_timeline_auto_enable: false }],
  ['/api/project/config', { revision: 'repo-1', values: { automations: {}, scan_timeline_auto_enable: false } }],
  ['/api/config', SETTINGS_CONFIG_FIXTURE],
  ['/api/automation/dashboard', DASHBOARD],
  ['/api/automation/projects', MATRIX],
  ['/api/automation/rules', { version: 1, text: 'version = 1\n', rules: [], diagnostic: null }],
  ['/api/automation/notifications', { items: [] }],
  ['/api/telemetry/workloads', TELEMETRY],
  ['/api/experiences', { items: [] }],
  ['/api/automation/injection-safety', { version: 3, research_only: true, authorizes_actuation: false, shadow_metrics: { evaluations: {}, reasons: {}, tracked_sessions: 0, unknown_duration_s: 0, transitions: 0 }, parser_coverage: [], sessions: [] }],
  ['/api/history', { items: [] }],
  ['/api/automation/batches', { items: [] }],
  ['/api/grants', GRANTS],
  // The Activity tab mounts the real AttentionInbox, which reads the inbox and
  // the per-Project ranking opt-in; both answer empty-but-working here.
  ['/api/attention/inbox', {
    generated_at: NOW,
    channels: { interrupt_now: [], next_breakpoint: [], inbox: [], digest: [] },
    suppressed: {}, suppressed_total: 0,
    budget: { day: '2026-08-15', daily_budget: 4, used: 0, remaining: 4, hourly_cap: 2, burst_used: 0, burst_remaining: 2 },
    fanout: { status: 'insufficient_samples', samples: 0, required: 6, interaction_seconds: null, neglect_seconds: null, sustainable_agents: null, attended_now: 0 },
    resumption_lag: { samples: 0, mean_seconds: null, max_seconds: null },
    rules: [], delivery: { push: false, surface: 'in_app' },
  }],
]

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  const match = ROUTES.find(([route]) => path.startsWith(route))
  return new Response(JSON.stringify(match ? match[1] : {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')
// `?project=` is how App threads the Project the operator is standing in.
const initialProjectId = new URLSearchParams(location.search).get('project') || undefined
render(
  <AutomationDashboard projects={[
    { id: 'p1', name: 'swe-mux', root: 'D:/PROJECTS/swe-mux' } as Project,
    { id: 'p2', name: 'orca', root: 'D:/PROJECTS/orca' } as Project,
  ]} initialProjectId={initialProjectId} onClose={() => {}} onOpenSession={() => {}} />,
  document.querySelector('#root')!,
)
