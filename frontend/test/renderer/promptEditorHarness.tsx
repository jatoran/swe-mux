// The real Prompt templates section at a real drawer width, over a stubbed daemon.
//
// The claim under test is that a template can be written and edited *here*, without
// the full library — so the harness mounts only `PromptsTab`, and the stub records
// what reached the daemon. A form that renders but posts the wrong scope, the wrong
// Project, or a stale revision would pass a source-level assertion and fail a user.
import { render } from 'preact'
import { PromptsTab } from '../../src/PromptsTab'
import type { PromptTemplate } from '../../src/promptTemplates'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

declare global {
  interface Window {
    promptWrites: Array<{ method: string; path: string; body: unknown }>
    promptItems: () => PromptTemplate[]
  }
}

const NOW = 1_770_000_000

const template = (index: number, overrides: Partial<PromptTemplate> = {}): PromptTemplate => ({
  id: `0000000${index}-0000-4000-8000-00000000000${index}`,
  key: `global:0000000${index}-0000-4000-8000-00000000000${index}`,
  scope: 'global',
  title: `Template number ${index}`,
  body: `Body of template ${index}.`,
  tags: ['review'],
  variables: [],
  backends: ['shell', 'claude', 'codex'],
  created_at: NOW - 100,
  updated_at: NOW - 10,
  revision: `rev-${index}`,
  favorite: false,
  last_used_at: null,
  use_count: 0,
  conflict: false,
  project_id: null,
  project_name: null,
  ...overrides,
})

let items = [template(1), template(2)]
window.promptWrites = []
window.promptItems = () => items

window.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
  const path = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
  const method = (init?.method || 'GET').toUpperCase()
  const body = init?.body ? JSON.parse(String(init.body)) : null
  if (path.includes('/api/prompts') && method === 'GET') {
    return json({ items, projects: [{ id: 'p1', name: 'Project One' }], configured_scope: 'both' })
  }
  window.promptWrites.push({ method, path, body })
  if (method === 'POST' && path.endsWith('/api/prompts')) {
    const created = template(items.length + 1, {
      title: String(body.title),
      body: String(body.body),
      scope: body.scope,
      project_id: body.scope === 'project' ? String(body.project_id) : null,
      project_name: body.scope === 'project' ? 'Project One' : null,
    })
    items = [...items, created]
    return json(created, 201)
  }
  if (method === 'PUT') {
    items = items.map(item => item.id === body.id || path.includes(item.id)
      ? { ...item, title: String(body.title), body: String(body.body), revision: `${item.revision}-next` }
      : item)
    return json(items.find(item => path.includes(item.id)))
  }
  return json({ ok: true })
}) as typeof fetch

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

const project = { id: 'p1', name: 'Project One' } as unknown as Project

render(
  <div class="utility-drawer actions-tab" style="width:380px;height:620px;display:flex;flex-direction:column;overflow:hidden">
    <PromptsTab
      project={project}
      onInsert={() => 'terminal'}
      onDone={() => {}}
      onManage={() => {}}
      sessions={[] as Session[]}
      onSend={async () => ({ status: 'done' as const })}
    />
  </div>,
  document.querySelector('#root')!,
)
