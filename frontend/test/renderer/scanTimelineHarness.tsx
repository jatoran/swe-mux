// The Timeline segment at a real drawer width, over a stubbed daemon.
//
// This surface's job is *scannability*: a run's records read as a sequence of moments, and
// the reader opens the one they want. The failure mode is entirely geometric — every record
// rendering its asked/intent/claim/blocked/targets stack at once turned a fifteen-record run
// into a page of prose where no unit test could see that the sequence had stopped being
// legible. What a row costs, whether it still says who it is while closed, and whether the
// liveness block survives above the list are all computed box geometry.
//
// `?viewport=phone` is the same tab at phone width, where the row is also a tap target.
import { render } from 'preact'
import { ScanTimelineTab } from '../../src/ScanTimelineTab'
import type { Session } from '../../src/types'
import '../../src/style.css'

const NOW = 1_770_000_000

const SESSION = {
  id: 's1', name: 'claude-0e7d93', generated_title: 'Scan Timeline Rows',
  project_id: 'p1', backend: 'claude', state: 'running', cwd: 'D:/PROJECTS/swe-mux',
  agent_run_id: 'run-1', pid: 4242,
} as Session

const record = (index: number, over: Record<string, unknown> = {}) => ({
  id: `r${index}`,
  agent_run_id: 'run-1',
  t0: NOW - (20 - index) * 300,
  t1: NOW - (20 - index) * 300 + 240,
  lifecycle_state: 'active',
  behavior: ['reading', 'editing'],
  work_phase: 'implementing',
  target: ['frontend/src/ScanTimelineTab.tsx', 'frontend/src/style.css'],
  intent: 'Make the timeline scan as a timeline.',
  claim: 'Compact rows render and expand individually.',
  user_ask: 'Collapse the timeline entries so the tab is readable.',
  blocked_on: 'none',
  summary: `Record ${index}: read the drawer tab, rewrote its record row as a compact head plus a mounted-on-demand detail, and moved the evidence disclosures inside it.`,
  novelty: 0.42,
  confidence: 0.8,
  trigger: 'turn_ended',
  observer_model: 'deepseek/deepseek-v4-flash',
  coverage: { messages_seen: 9, facts_seen: 3, truncated: false },
  repairs: [],
  ...over,
})

const STATE = {
  session_id: 's1', project_id: 'p1', agent_run_id: 'run-1',
  global_enabled: true, project_enabled: true, run_enabled: true,
  auto_enable: true, run_decided: true,
  model: 'deepseek/deepseek-v4-flash',
  daily_budget: { tokens: 3_000_000, usd: 5, mode: 'either' as const },
  spend_today: { tokens: 412_000, cost_usd: 0.94 },
  run_budget: { tokens: 500_000, usd: null, mode: 'tokens' as const },
  run_spend: { tokens: 61_000, cost_usd: 0.12 },
  metrics: { record_reads: 7, rehydrations: 7, rehydration_rate: 1 },
  gates: [
    { id: 'scan_daily_tokens', label: 'scan tokens today', unit: 'tokens', used: 412_000, limit: 3_000_000 },
    { id: 'scan_daily_usd', label: 'scan $ today', unit: 'usd', used: 0.94, limit: 5 },
    { id: 'scan_run_tokens', label: 'this conversation', unit: 'tokens', used: 61_000, limit: 500_000 },
  ],
  // A stopped scanner and a quiet one both return an empty tail, so the reason the
  // service reported is on screen. It must stay on screen, above the list.
  skip_reason: 'the hourly call cap for scan timeline is spent',
  last_scan_at: NOW - 120,
  scanning: false,
  records: [
    record(1),
    record(2, { work_phase: 'debugging', blocked_on: 'waiting on the operator to confirm the row layout' }),
    record(3, { coverage: { messages_seen: 40, facts_seen: 6, truncated: true, remaining: 12 } }),
    record(4, { repairs: ['behavior "vibing" is not a known label', 'confidence clamped to 1.0'] }),
    record(5, { work_phase: 'reviewing', approach_status: 'abandoned', dead_end: 'A details/summary per field kept the wall of prose and only added chrome.' }),
    record(6, { summary: '' , intent: '' , claim: '' }),
    record(7),
    record(8, { work_phase: 'testing' }),
  ],
  boundaries: [
    { id: 'b1', previous_run_id: 'run-0', next_run_id: 'run-1', reason: 'cleared', created_at: NOW - 20 * 300 - 60 },
  ],
  backfill: { state: 'idle', processed_chunks: 0, total_chunks: 0, created_records: 0, reason: null },
}

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  if (/scan-timeline\/r\d+/.test(path)) {
    return new Response(
      JSON.stringify({ source: [{ role: 'user', text: 'rehydrated transcript message' }], metrics: STATE.metrics }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }
  return new Response(JSON.stringify(STATE), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

const phone = new URLSearchParams(location.search).get('viewport') === 'phone'

// The real drawer chrome, because a record row is measured against the width it actually
// gets: 380px docked, and the full viewport on a phone.
render(
  <aside class="utility-drawer docked" style={`width:${phone ? '100%' : '380px'};height:100dvh;display:flex`}>
    <section class="drawer-pane" style="flex:1;min-width:0;min-height:0;display:flex;flex-direction:column">
      <div class="drawer-body" style="flex:1;min-height:0;display:flex;flex-direction:column">
        <ScanTimelineTab session={SESSION} onOpenProjectSettings={() => {}} />
      </div>
    </section>
  </aside>,
  document.querySelector('#root')!,
)
