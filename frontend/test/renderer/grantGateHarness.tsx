// The gate's actual behaviour in a browser, over a stubbed daemon.
//
// Three of the gate's promises are only true at runtime and no unit test can see any of
// them: that a mixed-scope grant is *one* request rather than a sequence that can half
// land, that the disclosure the operator reads before pressing is drawn from the live
// registry rather than restated, and that the gate clears the moment the grant resolves
// instead of a websocket round trip later.
//
// `fetch` is stubbed rather than mocked at the module boundary, so what is exercised is
// the real `applyGrants` over the real component - including the request body, which is
// the part a refactor is most likely to quietly change.
import { render } from 'preact'
import { GrantGate } from '../../src/GrantGate'
import type { GrantId } from '../../src/grants'
import '../../src/style.css'

type Call = { method: string; url: string; body: unknown }

const calls: Call[] = []
const params = new URLSearchParams(location.search)
const fail = params.get('fail') === '1'

const REGISTRY = [
  { id: 'raw_store', kind: 'substrate', label: 'Raw transcript store', requires: [], implemented: true, spends: false },
  { id: 'tier0', kind: 'substrate', label: 'Deterministic fact capture', requires: ['raw_store'], implemented: true, spends: false },
  { id: 'scan_timeline', kind: 'substrate', label: 'Scan timeline', requires: ['tier0', 'raw_store'], implemented: true, spends: true },
  { id: 'code_graph', kind: 'consumer', label: 'Code-structure graph', requires: ['tier0'], implemented: true, spends: false },
]

const AUTOMATION_STATE = {
  revision: 'r1', requested: {}, enabled: [], blocked: {},
  automations: REGISTRY, scan_timeline_auto_enable: false,
}

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  const method = init?.method || 'GET'
  calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
  if (url.includes('/automations')) {
    return new Response(JSON.stringify(AUTOMATION_STATE), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  if (url === '/api/grants' && method === 'POST') {
    if (fail) {
      return new Response(
        JSON.stringify({ error: 'this Project’s .swe-mux/config.toml is read-only', code: 'project_config_read_only' }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      )
    }
    return new Response(
      JSON.stringify({ applied: { install: ['scan_timeline_enabled'], automations: ['scan_timeline', 'tier0', 'raw_store'], values: [] }, spends: true }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }
  return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

const IDS: GrantId[] = ['automation.scanTimeline', 'project.scanTimeline']

const root = document.querySelector<HTMLElement>('#root')!
document.body.style.margin = '0'
document.body.style.width = '380px'
document.documentElement.style.setProperty('--ui-scale', '1')

let granted = false
const draw = () => render(
  granted
    ? <p id="surface-live">The timeline is on.</p>
    : <GrantGate
        ids={IDS}
        projectId="p1"
        heading="Scan timeline is switched off for this install."
        confirmLabel="Turn on Scan timeline"
        onGranted={() => { granted = true; draw() }}
      >
        <p>A readable behavioural history of each conversation here.</p>
      </GrantGate>,
  root,
)
draw()

Object.assign(window as unknown as Record<string, unknown>, { __calls: calls })
