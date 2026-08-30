// The two densest drawer tabs at a real drawer width, over a stubbed daemon.
//
// Both are surfaces whose whole job is *legibility of structure*, and structure is exactly
// what no unit test can see. Actions stacks three independently collapsible catalogs in one
// scroller, and its failure mode is that the boundary between two of them stops reading as
// a boundary. Agent → Instructions stacks three disclosures over a file viewer, and its
// failure mode is that the viewer stops reading as a separate region from the list above it.
//
// `?tab=agent` renders the Instructions segment; anything else renders Actions.
import { render } from 'preact'
import { ActionsTab } from '../../src/ActionsTab'
import { AgentContextTab } from '../../src/AgentContextTab'
import { DrawerSegmentControl } from '../../src/DrawerSegmentControl'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

const NOW = 1_770_000_000
const params = new URLSearchParams(location.search)

const PROJECT = { id: 'p1', name: 'swe-mux', root: 'D:/PROJECTS/swe-mux' } as Project

const SESSION = {
  id: 's1', name: 'claude-0e7d93', generated_title: 'Schedule Session Resume',
  project_id: 'p1', backend: 'claude', state: 'running', cwd: PROJECT.root, pid: 4242,
} as Session

const SKILLS = {
  backend: 'claude',
  cwd: PROJECT.root,
  generated_at: NOW,
  agent_loaded_at: NOW - 7200,
  agent_run_started_at: NOW - 7200,
  roots: ['.claude/skills', '~/.claude/skills'],
  skills: [
    { name: 'documentation', invocation: '/documentation', kind: 'skill', scope: 'project', origin: '.claude/skills', path: 'a', implicit: true, short_description: 'Create or update project documentation', description: '' },
    { name: 'learn', invocation: '/learn', kind: 'skill', scope: 'project', origin: '.claude/skills', path: 'b', implicit: true, short_description: 'Read the docs before diving in', description: '' },
    { name: 'code-review', invocation: '/code-review', kind: 'command', scope: 'user', origin: '~/.claude/skills', path: 'c', implicit: false, short_description: 'Review the current diff', description: '' },
  ],
  errors: [],
  truncated: false,
  skipped_plugins: [],
}

const CLIPBOARD = {
  enabled: true, persist: true, retention_hours: 48, count: 2,
  entries: [
    { id: 'c1', preview: 'uv run pytest tests -q', source: 'terminal', updated_at: NOW - 60, char_count: 22, line_count: 1, pinned: false, device: '' },
    { id: 'c2', preview: 'git merge --ff-only worktree-ui-polish', source: 'editor', updated_at: NOW - 900, char_count: 38, line_count: 1, pinned: true, device: '' },
  ],
}

const source = (id: string, label: string, provider: string, detail: string) => ({
  id, label, provider, detail, scope: 'project', status: 'available',
  size: 4096, modified_at: NOW - 3600, revision: `rev-${id}`, revealable: true,
  changed_after_start: false,
})

const AGENT_CONTEXT = {
  instructions: {
    comparison: 'different',
    items: [source('instruction:claude', 'CLAUDE.md', 'claude', 'Project root instructions'),
      source('instruction:codex', 'AGENTS.md', 'codex', 'Project root instructions')],
  },
  global_instructions: {
    items: [source('global:claude', '~/.claude/CLAUDE.md', 'claude', 'User-level instructions')],
  },
  providers: [
    {
      id: 'claude', label: 'Claude', status: 'available', detail: 'Learned project memory',
      item_count: 2, truncated: false,
      items: [source('memory:one', 'release-readiness-p0.md', 'claude', 'Learned memory'),
        source('memory:two', 'phase7-doctor-cli.md', 'claude', 'Learned memory')],
    },
  ],
  sync_options: [{ direction: 'claude_to_codex', source: 'CLAUDE.md', target: 'AGENTS.md' }],
  backups: [],
}

/** Every source the inventory offers, so a read can answer with the file that was asked for. */
const SOURCES = [
  ...AGENT_CONTEXT.instructions.items,
  ...AGENT_CONTEXT.global_instructions.items,
  ...AGENT_CONTEXT.providers.flatMap(provider => provider.items),
]

const ROUTES: Array<[string, unknown]> = [
  ['/api/sessions/s1/skills', SKILLS],
  ['/api/prompts', { items: [], projects: [] }],
  ['/api/clipboard', CLIPBOARD],
  ['/api/projects/p1/agent-context', AGENT_CONTEXT],
]

window.fetch = (async (input: RequestInfo | URL) => {
  const path = String(typeof input === 'string' ? input : input instanceof URL ? input.pathname : input.url)
  // Reads echo the id they were given. Answering every read with the same file would make
  // "which file is open" a property of the stub rather than of the selection, which is the
  // one thing the spec above it is checking.
  const read = path.match(/agent-context\/sources\/([^?]+)/)
  if (read) {
    const id = decodeURIComponent(read[1])
    const item = SOURCES.find(entry => entry.id === id) || SOURCES[0]
    return new Response(
      JSON.stringify({ source: item, text: `# ${item.label}\n\nBody of ${item.label} in swe-mux.\n` }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )
  }
  const match = ROUTES.find(([route]) => path.includes(route))
  return new Response(JSON.stringify(match ? match[1] : {}), { status: 200, headers: { 'Content-Type': 'application/json' } })
}) as typeof fetch

document.body.style.margin = '0'
document.documentElement.style.setProperty('--ui-scale', '1')

// The real content-first drawer chrome: Actions owns its catalog rail and Agent uses the
// shared registered-segment rail. Neither receives a redundant pane heading.
render(
  <aside class="utility-drawer docked" style="width:380px;height:100dvh;display:flex">
    <section class="drawer-pane" style="flex:1;min-height:0;display:flex;flex-direction:column">
      <div class="drawer-body" style="flex:1;min-height:0;display:flex;flex-direction:column">
        {params.get('tab') === 'agent'
          ? <><DrawerSegmentControl tab="agent" active="instructions" context={{hasTranscript:true,isAgentSession:true}} onSelect={()=>{}}/><AgentContextTab project={PROJECT} session={SESSION} /></>
          : <ActionsTab
            session={SESSION}
            project={PROJECT}
            backend="claude"
            onDone={() => {}}
            onInsert={() => 'terminal'}
            onManage={() => {}}
            sessions={[SESSION]}
            onSend={async () => ({ status: 'sent' }) as never}
            onClipboardInsert={() => 'terminal'}
            onClipboardDone={() => {}}
            onOpenSettings={() => {}}
          />}
      </div>
    </section>
  </aside>,
  document.querySelector('#root')!,
)
