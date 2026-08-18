// The real Configure Actions modal against a stubbed daemon.
//
// Everything the editor persists goes through the device-settings cache, which
// is updated synchronously before the PUT — so a stub that just answers 200 is
// enough for the full edit loop: scope switching, delta creation, placement
// checkboxes, custom-action creation, and reset all round-trip in-page.
import { render } from 'preact'
import { ActionEditorModal } from '../../src/ActionEditorModal'
import '../../src/style.css'

const PROJECTS = [
  { id: 'p1', name: 'Project One' },
  { id: 'p2', name: 'Project Two' },
]

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
      : path.includes('/api/settings') ? { profiles: {} }
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

render(<ActionEditorModal onClose={() => {}} />, document.querySelector('#root')!)
