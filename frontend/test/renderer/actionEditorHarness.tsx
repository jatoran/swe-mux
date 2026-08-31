// The real Configure Actions modal against a stubbed daemon.
//
// Everything the editor persists goes through the device-settings cache, which
// is updated synchronously before the PUT — so a stub that just answers 200 is
// enough for the full edit loop: scope switching, delta creation, placement
// checkboxes, custom-action creation, and reset all round-trip in-page.
import { render } from 'preact'
import { ActionEditorModal } from '../../src/ActionEditorModal'
import { defaultRailConfig, writeRailConfigBlob } from '../../src/commandRail'
import { loadSettings } from '../../src/deviceSettings'
import '../../src/style.css'

const PROJECTS = [
  { id: 'p1', name: 'Project One' },
  { id: 'p2', name: 'Project Two' },
]

// `?compact=1` serves a small saved layout instead of the shipped default. The touch
// drag specs use it: they exercise hold-to-drag *mechanics*, and the shipped mobile
// default is now two dense rows whose wrapping puts chip geometry (midpoints, the
// row's bounding box) at the mercy of the layout - exactly the coupling a mechanics
// test should not have. Built through the real serializer so the blob's shape can
// never drift from what the app writes.
const compactBlob = () => {
  const config = defaultRailConfig()
  const items = ['esc', 'enter', 'tab', 'ctrlC', 'paste']
  config.layouts.desktop.strip = [{ id: 'desktop-strip', items: [...items] }]
  config.layouts.mobile.strip = [{ id: 'mobile-strip', items: [...items] }]
  return writeRailConfigBlob(undefined, config)
}
const compact = new URLSearchParams(location.search).get('compact') === '1'
const SETTINGS = compact
  ? { profiles: { desktop: { commandRail: compactBlob() } } }
  : { profiles: {} }

const TEMPLATES = {
  items: [{
    id: 'tpl-1', scope: 'global', key: 'global:tpl-1', title: 'Ship checklist',
    body: 'ship it', tags: [], variables: [], backends: ['claude', 'codex'],
    favorite: false, use_count: 0, last_used_at: null,
  }],
}

window.fetch = (async (input: RequestInfo | URL) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const body = path.includes('/api/projects') ? PROJECTS
    : path.includes('/api/prompts') ? TEMPLATES
      : path.includes('/api/settings') ? SETTINGS
        : null
  return new Response(JSON.stringify(body ?? { error: `unstubbed request: ${path}` }), {
    status: body ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  })
}) as typeof fetch

// `?seen=1` starts with the first-open callout already dismissed.
if (new URLSearchParams(location.search).get('seen') === '1') {
  localStorage.setItem('mux.actions.intro.v1', '1')
} else {
  localStorage.removeItem('mux.actions.intro.v1')
}

// The editor reads the rail through the device-settings cache synchronously at
// render, so the cache is populated first - which is also the app's own order.
await loadSettings()
render(<ActionEditorModal onClose={() => {}} />, document.querySelector('#root')!)
