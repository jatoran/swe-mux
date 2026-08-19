// The real Settings panel over a stubbed daemon, at whatever viewport the spec sets.
//
// What this exists to prove is geometry the unit tests structurally cannot see: at the
// mobile breakpoint the header has to stay one row (that is the vertical space the change
// was for), and the section list has to become a drawer that is genuinely off screen when
// closed and covers nothing but the content when open. Both are CSS, and both were
// previously a rail nobody could assert on.
//
// The panel's open state is owned here exactly as `App` owns it, so the harness exercises
// the same prop contract the shell does rather than a private one.
import { render } from 'preact'
import { useState } from 'preact/hooks'
import { Settings } from '../../src/Settings'
import { SETTINGS_CONFIG_FIXTURE } from './settingsConfigFixture'
import '../../src/style.css'

const KEYBINDINGS = {
  bindings: { 'ctrl+k': 'palette.open' },
  defaults: { 'ctrl+k': 'palette.open' },
  commands: [
    { id: 'palette.open', label: 'Open command palette', category: 'view' },
    { id: 'sidebar.toggle', label: 'Toggle navigation sidebar', category: 'view' },
  ],
  policy: { browser_reserved: [], desktop_only: [], application_reserved: [], terminal_reserved: [], rules: [] },
  rejected: {},
}

const BUNDLE = {
  config: SETTINGS_CONFIG_FIXTURE,
  keybindings: KEYBINDINGS,
  profiles: { profiles: [], detected: [] },
  projects: [],
  automation: null,
  provider: null,
  usage: null,
  errors: {},
}

// Everything the panel asks for on open. Unlisted paths answer `{}` rather than failing,
// so a new fetch added to a tab degrades to an empty section instead of a blank harness.
const RESPONSES: Record<string, unknown> = {
  '/api/settings/bundle': BUNDLE,
  '/api/remote/status': { tailnet_enabled: false, urls: [], diagnostic: '' },
  '/api/remote/firewall': { checked: false, rules: [], diagnostic: '' },
  '/api/wsl/bridge': { available: false, distributions: [], diagnostic: '' },
  '/api/diagnostics/prerequisites': { prerequisites: [] },
  '/api/voice': null,
  '/api/voice/stt-latency': null,
}

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  void init
  const raw = typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url
  const path = raw.split('?')[0]
  const body = path in RESPONSES ? RESPONSES[path] : {}
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

function Host() {
  const [navOpen, setNavOpen] = useState(false)
  return <Settings
    activeUiScale={1}
    onUiScalePreview={() => 1}
    navOpen={navOpen}
    onNavOpenChange={setNavOpen}
    onClose={() => {}}
  />
}

render(<Host />, document.querySelector('#root')!)
