// The real Resources dialog over a stubbed daemon, opened on Fleet activity.
//
// It exists for the surface the split moved into it: the workload telemetry that was a
// domain of the old Tokens segment, and before that lived in the Automation dashboard's
// health view. It is a dense figures table, which is exactly the class of surface a unit
// test cannot check and a real layout can.
//
// The workload fixture is the one `automationCostHarness` used, unchanged, so the
// human-scale formatting assertions that have followed this table through two homes keep
// running against it and would notice a regression introduced by the move itself.
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
  provider_cost_dimensions: [],
  cost_note: 'ccusage costs are backend/model aggregates and are not attributed to individual runs',
}

const OPERATIONAL = {
  schema_version: 1,
  interpretation: '',
  quota: { samples: [], resets: [], attributions: [], rollups: [] },
  tools: {
    metrics: [
      { backend: 'claude', model: 'claude-opus-4-8', project_id: 'project-aaaaaaaa', session_id: 'session-bbbbbbbb', taxonomy: 'edit_file', raw_tool: 'Edit', events: 4821, uses: 4610, errors: 211, average_duration_ms: 143.7 },
    ],
    skills: [
      { explicit_skill: 'documentation', backend: 'claude', project_id: 'project-aaaaaaaa', uses: 12, last_used_at: NOW - 7200 },
    ],
    unknown_or_unmapped: 17,
    parser_version: 'v4',
    parser_versions: { claude: 'v4', codex: 'v2' },
    coverage: [
      { session_id: 'session-bbbbbbbb', backend: 'claude', parser_version: 'v4', status: 'reconciled', recognized_records: 9814, unknown_records: 17, tool_events: 4821, skill_events: 12, compaction_events: 3, reconciled_at: NOW - 900 },
    ],
  },
  compactions: [
    { session_id: 'session-bbbbbbbb', backend: 'claude', project_id: 'project-aaaaaaaa', count: 3, last_compaction_at: NOW - 1800, capability: 'native_record', confidence: 'explicit' },
  ],
}

const ROUTES: Array<[string, unknown]> = [
  ['/api/telemetry/workloads', TELEMETRY],
  ['/api/telemetry/operational', OPERATIONAL],
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
    initial="fleet"
    sessions={SESSIONS}
    projects={PROJECTS}
    onClose={() => {}}
    onAttached={() => {}}
  />,
  document.querySelector('#root')!,
)
