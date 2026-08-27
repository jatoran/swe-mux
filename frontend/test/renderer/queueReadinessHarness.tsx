// The Queue tab's readiness strip, mounted over a stubbed daemon.
//
// The properties this exists to check are runtime ones a unit test cannot see: that the
// strip paints from the session row it was handed *before* any fetch resolves (which is
// the whole "no lag when I pull up that tab" claim), that the fetched target view then
// corrects it, and that a pushed readiness update reaches the strip without a refetch.
//
// `fetch` is stubbed rather than the module mocked, so what runs is the real `fetchQueue`
// and the real component - including the request the pane makes on open, which is where a
// regression would otherwise hide.
import { render } from 'preact'
import { QueuePane } from '../../src/QueuePane'
import type { DeliveryReadiness, Session } from '../../src/types'
import '../../src/style.css'

const params = new URLSearchParams(location.search)
//: Which reading the session row carries at mount. `stale` is deliberately old, so the
//: age label has something to say.
const rowState = params.get('row') || 'blocked'
//: Held open until the spec releases it, so the pre-fetch paint is observable at all.
const holdFetch = params.get('hold') === '1'

const READINGS: Record<string, DeliveryReadiness> = {
  blocked: {
    state: 'blocked',
    reason: 'terminal_input_after_completion',
    reasons: ['terminal_input_after_completion'],
    protected: [],
    interject_state: 'blocked',
    observed_at: Date.now() / 1000,
    authorized: false,
  },
  stale: {
    state: 'blocked',
    reason: 'root_agent_working',
    reasons: ['root_agent_working'],
    protected: [],
    interject_state: 'safe',
    observed_at: Date.now() / 1000 - 47,
    authorized: false,
  },
  protectedApproval: {
    state: 'blocked',
    reason: 'approval_required',
    reasons: ['approval_required'],
    protected: ['awaiting_approval'],
    interject_state: 'blocked',
    observed_at: Date.now() / 1000,
    authorized: false,
  },
  safe: {
    state: 'safe',
    reason: 'all_required_evidence_positive',
    reasons: ['all_required_evidence_positive'],
    protected: [],
    interject_state: 'blocked',
    observed_at: Date.now() / 1000,
    authorized: false,
  },
}

const session = {
  id: 'session',
  name: 'claude-1',
  project_id: 'p1',
  backend: 'claude',
  state: 'idle',
  cwd: '.',
  agent_run_id: 'run-1',
  delivery_readiness: READINGS[rowState],
} as Session

let releaseFetch = () => {}
const gate = new Promise<void>(resolve => { releaseFetch = () => resolve() })

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

window.fetch = (async (input: RequestInfo | URL) => {
  const url = String(input)
  if (url.includes('/api/queue/messages')) {
    if (holdFetch) await gate
    return json({
      target: {
        session_id: 'session',
        live: true,
        agent_run_id: 'run-1',
        label: 'claude-1',
        state: 'idle',
        // The corrected reading: newer than the row's, and a different verdict, so a
        // strip that ignored the fetch would keep showing the stale one.
        delivery_readiness: {
          state: 'blocked',
          reason: 'screen_not_at_agent_prompt',
          reasons: ['screen_not_at_agent_prompt'],
          protected: [],
          interject_state: 'blocked',
          observed_at: Date.now() / 1000 + 5,
          authorized: false,
        },
      },
      messages: [],
      pending: 0,
    })
  }
  if (url.includes('/api/queue/auto')) {
    return json({
      master_enabled: true, paused: false,
      quiet_hours: { start: '', end: '', active: false },
      stable_seconds: 3, max_consecutive: 3, session_ttl_minutes: 30, reply_window_minutes: 30,
      sessions: [], counters: {},
      promotion: {
        criteria: {}, met: false, auto_sends: 0, unsafe_reports: 0, proving_days: 0,
        required_sends: 0, required_days: 0, fixture_classes: [],
      },
      last_error: '',
    })
  }
  return json({})
}) as typeof fetch

const root = document.querySelector<HTMLElement>('#root')!
document.body.style.margin = '0'
document.body.style.width = '380px'
document.documentElement.style.setProperty('--ui-scale', '1')

let live: Session = session
const draw = () => render(<QueuePane sessionId="session" sessions={[live]} />, root)
draw()

Object.assign(window as unknown as Record<string, unknown>, {
  __releaseFetch: () => releaseFetch(),
  //: Stands in for the `delivery_readiness_changed` frame the composition root patches
  //: onto the row. The point of the test is that the strip follows the row it is handed,
  //: with no fetch of its own.
  __pushReadiness: (readiness: DeliveryReadiness) => {
    live = { ...live, delivery_readiness: readiness }
    draw()
  },
  __readings: READINGS,
})
