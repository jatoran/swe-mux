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
  from: NOW - 7 * 86400,
  to: NOW,
  origin: 'mux_owned',
  dimensions: [
    { backend: 'codex', model: 'gpt-5.6-sol', runs: 32, ended_runs: 31, average_wall_duration_s: 8403.96, average_turn_duration_ms: 94500, average_tool_duration_ms: 4300, average_model_wait_ms: 12400, model_requests: 212, model_request_failures: 2, input_tokens: 9_664_898_958, output_tokens: 38_032_396, cache_read_tokens: 4_000_000_000, cache_write_tokens: 0, average_final_context_pct: 0.4338, average_peak_context_pct: 0.5432, turns: 143, completed_turns: 141, model_tool_calls: 880, runtime_tool_calls: 602, completed_tool_calls: 862, failed_tool_calls: 18, approval_events: 8, stall_events: 2, subagent_events: 12, verifications: 44, successful_verifications: 41 },
    { backend: 'claude', model: 'claude-opus-5', runs: 26, ended_runs: 23, average_wall_duration_s: 5691.24, average_turn_duration_ms: 71200, average_tool_duration_ms: 2100, input_tokens: 35_087_852, output_tokens: 276_238, cache_read_tokens: 20_000_000, cache_write_tokens: 100_000, average_final_context_pct: 0.2592, average_peak_context_pct: 0.2662, turns: 98, completed_turns: 96, model_tool_calls: 461, runtime_tool_calls: 0, completed_tool_calls: 450, failed_tool_calls: 11, approval_events: 5, stall_events: 3, subagent_events: 18, verifications: 36, successful_verifications: 34 },
    { backend: 'claude', model: 'claude-sonnet-5', runs: 14, ended_runs: 12, average_wall_duration_s: 2200, average_turn_duration_ms: null, average_tool_duration_ms: null, input_tokens: 1_024_543, output_tokens: 11_889, cache_read_tokens: 700_000, cache_write_tokens: 0, average_final_context_pct: 0.063, average_peak_context_pct: 0.063, turns: 0, completed_turns: 0, model_tool_calls: 0, runtime_tool_calls: 0, completed_tool_calls: 0, failed_tool_calls: 0, approval_events: 0, stall_events: 0, subagent_events: 0, verifications: 0, successful_verifications: 0 },
  ],
  interpretation: 'observational_correlation_only',
  collection: { backfilled: 1200, backfill_completed: false, backfill_stream: 'tool_events', provider_dropped: 0 },
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

const CANONICAL = {
  from: NOW - 7 * 86400,
  to: NOW,
  origin: 'mux_owned',
  matching_calls: 4821,
  groups: [
    { backend: 'claude', model: 'claude-opus-5', project_id: 'project-aaaaaaaa', origin: 'mux_owned', invocation_layer: 'model', family: 'file', operation: 'write', transport: 'native', raw_name: 'Edit', calls: 4821, statuses: { succeeded: 4610, failed: 211 }, duration_count: 4700, average_duration_ms: 143.7 },
  ],
  skills: {
    matching_invocations: 12,
    groups: [
      { backend: 'claude', model: 'claude-opus-5', project_id: 'project-aaaaaaaa', skill_name: 'documentation', invocation_trigger: 'claude-proactive', skill_source: 'userSettings', skill_scope: 'user', invocations: 12 },
    ],
  },
  collection: { backfilled: 1200, backfill_completed: false, backfill_stream: 'tool_events', provider_dropped: 0 },
}

const QUALITY = {
  totals: { calls: 4821, with_request: 4821, with_result: 4610, with_provider_result: 3900, with_duration: 4700, with_input_hash: 4800, with_executed_input_hash: 3900, with_output_hash: 4550, with_output_size: 4300, with_harness_version: 3900, truncated_outputs: 2, runtime_parent_unavailable: 0, other_family: 0 },
  backends: [
    { backend: 'claude', calls: 4821, with_request: 4821, with_result: 4610, with_provider_result: 3900, with_duration: 4700, with_input_hash: 4800, with_executed_input_hash: 3900, with_output_hash: 4550, with_output_size: 4300, with_harness_version: 3900, truncated_outputs: 2, runtime_parent_unavailable: 0, other_family: 0 },
  ],
  parsers: [
    { backend: 'claude', harness_version: '2.1.259', parser_version: 'otlp-json-v2', event_name: 'tool_result', recognized: 1, occurrences: 3900, first_seen_at: NOW - 86400, last_seen_at: NOW - 60 },
  ],
  collection: CANONICAL.collection,
}

const COMPACTIONS = {
  total: 3,
  groups: [
    { backend: 'claude', model: 'claude-opus-5', project_id: 'project-aaaaaaaa', trigger: 'auto', count: 3, failures: 0, duration_count: 3, average_duration_ms: 825, token_count: 3, average_tokens_reclaimed: 48200 },
  ],
  collection: CANONICAL.collection,
}

const TOOL_PAGE = {
  matching_calls: 4821,
  next_cursor: null,
  items: [
    { tool_call_id: 'call-aaaaaaaa', run_id: 'run-bbbbbbbb', turn_id: 'turn-cccccccc', session_id: 'session-dddddddd', backend: 'claude', model: 'claude-opus-5', invocation_layer: 'model', raw_name: 'Edit', family: 'file', operation: 'write', transport: 'native', started_at: NOW - 60, finished_at: NOW - 59, status: 'succeeded', duration_ms: 143.7, target_preview: 'src/swe_mux/server.py', output_measurement: 'full_hash_size_unknown', request_source: 'transcript', result_source: 'transcript' },
  ],
}

const ROUTES: Array<[string, unknown]> = [
  ['/api/telemetry/v2/workload', TELEMETRY],
  ['/api/telemetry/v2/tools/summary', CANONICAL],
  ['/api/telemetry/v2/tools?', TOOL_PAGE],
  ['/api/telemetry/v2/quality', QUALITY],
  ['/api/telemetry/v2/compactions', COMPACTIONS],
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
