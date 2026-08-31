/**
 * The demo's `window.fetch`: every `/api/...` request the real UI makes is
 * answered here, from the shared demo store, without touching any network.
 *
 * Route coverage is deliberately partial - the demo enables a handful of key
 * surfaces and lets the rest render their empty states. Unmatched GETs answer
 * `{}` and unmatched mutations answer `{ok:true}`, both logged to the console
 * so extending coverage is a matter of reading what the app just asked for.
 */
import { HARNESS_REGISTRY_SEED } from '../harnessRegistrySeed.ts'
import type { Preview } from '../processFleet.ts'
import type { PaneLayout, PaneLeaf, PaneNode } from '../layout.ts'
import type { Session } from '../types.ts'
import { PREVIEW_PAGE_IDS } from './fixtures.ts'
import { KEYMAP_FIXTURE } from './keymapFixture.ts'
import { apply, demoId, nowSeconds, project, session, state } from './store.ts'
import { spawnScrollback } from './terminalSim.ts'

const realFetch = window.fetch.bind(window)

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json', Date: new Date().toUTCString() },
  })
}

type RouteHandler = (match: RegExpMatchArray, body: unknown, url: URL) => unknown | { __status: number }

type Route = { method: string; pattern: RegExp; handler: RouteHandler }

const error = (status: number, message: string) => ({ __status: status, error: message })

// ------------------------------------------------------------------ helpers

function harnessPayload(): unknown {
  // The static seed, with the two demo harnesses marked present so the Run menu
  // offers them and everything else left to the user's enablement choices.
  return {
    version: HARNESS_REGISTRY_SEED.version,
    harnesses: HARNESS_REGISTRY_SEED.harnesses.map(harness => ({
      ...harness,
      installed: harness.name === 'claude' || harness.name === 'codex',
    })),
  }
}

function voicePayload(): unknown {
  return {
    enabled: false, engine: 'kokoro', engine_available: false,
    diagnostic: 'Voice is switched off in the demo.',
    content: 'summary', default_mode: 'off', voice: 'af_heart', summary_model: 'demo',
    spend_today: { tokens: 0, cost_usd: 0 }, daily_budget: { tokens: null, usd: null, mode: 'either' },
    cache_bytes: 0, cache_limit_bytes: 0, clip_count: 0, stt_enabled: false,
    stt_engine: 'sapi', stt_available: false, stt_diagnostic: 'Demo build.',
    stt_language: 'en-US', stt_whisper_model: 'base',
  }
}

function profilesPayload(): unknown {
  return {
    default_profile_id: 'default',
    profiles: [
      {
        id: 'default', label: 'Demo shell', executable: 'bash', args: [], env: {},
        platforms: [], cwd_strategy: 'native', marker: 'demo',
        capabilities: ['interactive'], cwd_integration: false, enabled: true, backend: 'shell',
      },
    ],
    detected: [],
  }
}

/**
 * `GET /api/keybindings`, over the daemon's own preset documents.
 *
 * `keymapFixture.ts` is generated from `swe_mux.keymaps`, so the tmux, Vim,
 * Emacs and VS Code presets a visitor switches between here carry the real
 * chords rather than an invented handful - which is the whole point of offering
 * the switch in a demo at all.
 */
function keybindingsPayload(): unknown {
  const preset = state.keymapPreset || 'swemux'
  const rules = KEYMAP_FIXTURE.rules[preset as keyof typeof KEYMAP_FIXTURE.rules]
    ?? KEYMAP_FIXTURE.rules.swemux
  const summary = KEYMAP_FIXTURE.presets.find(item => item.id === preset)
  // What this host dispatches on. Every demo rule is deliverable in a browser,
  // so `resolved` is the rule list keyed by chord and `undeliverable` is empty.
  const resolved: Record<string, Array<{ command: string; when: string }>> = {}
  for (const rule of rules) {
    const entry = { command: rule.command, when: (rule as { when?: string }).when ?? '' }
    ;(resolved[rule.keys] ??= []).push(entry)
  }
  return {
    preset,
    presets: KEYMAP_FIXTURE.presets,
    host: 'browser',
    platform: 'win',
    rules,
    resolved,
    prefixes: summary?.prefix ? [summary.prefix, ...summary.prefix_alternates] : [],
    labels: {},
    undeliverable: [],
    contested: [],
    commands: KEYMAP_FIXTURE.commands,
    groups: [],
    when_flags: [
      'terminalFocused', 'editorFocused', 'inputFocused', 'overlayOpen', 'paletteOpen',
      'drawerFocused', 'sidebarFocused', 'settingsOpen', 'mobile', 'desktop', 'zoomed',
      'multiplePanes', 'multipleTabs', 'hasSelection', 'agentFocused',
    ],
    policy: {
      hosts: ['browser', 'desktop'], platforms: ['win', 'mac', 'linux'], max_sequence: 3,
      browser_unreachable: ['ctrl+n', 'ctrl+t', 'ctrl+w'],
      browser_contested: { 'ctrl+f': 'find in page', 'ctrl+p': 'print' },
      wm_reserved: { win: { 'alt+tab': 'switch windows' }, mac: {}, linux: {} },
      application_reserved: ['ctrl+-', 'ctrl+0', 'ctrl+='],
      terminal_reserved: { 'ctrl+c': 'interrupt', 'ctrl+d': 'end of file' },
      rules: [
        'A chord may use Ctrl, Alt, or Meta plus a non-modifier key.',
        'Known browser and terminal shortcuts are reserved.',
        'This is a demo: nothing you bind here reaches a real keyboard.',
      ],
    },
    rejected: {},
  }
}

function noteSummaries(projectId: string | null): unknown {
  const items = state.notes
    .filter(note => note.project_id && (!projectId || note.project_id === projectId))
    .map(note => ({
      note_id: note.note_id, project_id: note.project_id,
      project_name: project(note.project_id)?.name ?? note.project_id,
      title: note.title, created_at: note.updated_at - 3600, updated_at: note.updated_at,
      bytes: note.content.length, revision: String(note.revision),
      excerpt: note.content.slice(0, 160), origin_session_id: null,
    }))
  return { items }
}

/** Append a leaf to the stack holding `anchorId` (or the first stack found). */
function placeLeafBeside(layout: PaneLayout, anchorId: string, leaf: PaneLeaf): PaneLayout {
  let placed = false
  const visit = (node: PaneNode): PaneNode => {
    if (node.type === 'stack') {
      if (placed) return node
      const holds = node.children.some(child => child.id === anchorId)
      if (!holds) return node
      placed = true
      return { ...node, children: [...node.children, leaf], active_child_id: leaf.id }
    }
    return { ...node, first: visit(node.first) as PaneNode, second: visit(node.second) as PaneNode }
  }
  const root = layout.root ? visit(layout.root) : null
  if (!placed) {
    if (!root) return { version: 7, root: { type: 'stack', id: demoId('stack'), children: [leaf], active_child_id: leaf.id } }
    const firstStack = (function find(node: PaneNode): PaneNode | null {
      if (node.type === 'stack') return node
      return find(node.first) ?? find(node.second)
    })(root)
    if (firstStack && firstStack.type === 'stack') {
      const patched = (function patch(node: PaneNode): PaneNode {
        if (node === firstStack && node.type === 'stack') {
          return { ...node, children: [...node.children, leaf], active_child_id: leaf.id }
        }
        if (node.type === 'split') return { ...node, first: patch(node.first), second: patch(node.second) }
        return node
      })(root)
      return { version: 7, root: patched }
    }
  }
  return { version: 7, root }
}

function spawnSession(body: Record<string, unknown>): unknown {
  const backend = String(body.backend ?? 'shell')
  const projectId = String(body.project_id ?? state.projects[0]?.id ?? '')
  const target = project(projectId)
  if (!target) return error(404, 'Unknown project.')
  const id = demoId('s')
  const created = nowSeconds()
  const newSession: Session = {
    id, name: backend === 'shell' ? 'shell' : `${backend} session`,
    project_id: projectId, backend,
    native_session_id: `native-${id}`, cwd: target.root, exe: backend, args: [],
    pid: 50000 + Math.floor(Math.random() * 9000), created_at: created,
    state: 'running', state_since: created,
    tokens_in: 0, tokens_out: 0, tokens_cache_read: 0, tokens_cache_write: 0, cost_usd: 0,
    context_window: 200000, context_pct: 0, context_peak_pct: 0,
    last_activity_ts: created, turn_seq: 0, read_turn_seq: 0,
    pinned_attention: false, broadcast: false, process_job_assignment: 'assigned',
    compaction_count: 0,
    model: backend === 'claude' ? 'claude-opus-4-8' : backend === 'codex' ? 'gpt-demo' : undefined,
    runtime_cwd: target.root, runtime_cwd_live: true, runtime_cwd_source: 'demo',
    runtime_cwd_dropped: 0, runtime_boundary: 'local',
    git: { branch: 'feature/faster-cart', dirty: 2, ahead: 2, behind: 0, root: target.root },
    delivery_readiness: { state: 'safe', reason: '', observed_at: created, authorized: false },
  }
  apply({ kind: 'session-add', session: newSession, scrollback: spawnScrollback(backend) })
  // A spawned pane settles quickly: running → idle, like a CLI finishing startup.
  window.setTimeout(() => {
    if (session(id)) apply({ kind: 'session-patch', id, patch: { state: 'idle', state_since: nowSeconds() } })
  }, 1500)
  return newSession
}

function createPreview(body: Record<string, unknown>): unknown {
  const sessionId = String(body.session_id ?? '')
  const owner = session(sessionId) ?? state.sessions[0]
  if (!owner) return error(404, 'No session to attach the preview to.')
  const used = new Set(state.previews.map(item => item.id))
  const pageId = PREVIEW_PAGE_IDS.find(candidate => !used.has(candidate)) ?? PREVIEW_PAGE_IDS[0]
  const preview: Preview = {
    // Static previews belong to the Project, not a session (session_id ''), so
    // the sidebar draws one row for them rather than a phantom "server :0".
    id: pageId, session_id: '', project_id: owner.project_id,
    url: '', host: '', port: 0, source: 'demo', viewport: 'desktop', listed: true,
    kind: 'static', label: pageId === PREVIEW_PAGE_IDS[0] ? 'landing.html' : 'metrics.html',
    entry: 'index.html', doc_root: `${owner.cwd}/site`, doc_root_relative: 'site', worktree: '',
  }
  apply({ kind: 'preview-add', preview })
  const target = project(owner.project_id)
  if (target && body.attach !== false) {
    const layout = placeLeafBeside(target.layout as PaneLayout, owner.id, { type: 'leaf', kind: 'preview', id: preview.id })
    apply({ kind: 'project-patch', id: target.id, patch: { layout, layout_revision: target.layout_revision + 1 } })
  }
  return { preview, project: project(owner.project_id) }
}

// -------------------------------------------------------------------- routes

const ROUTES: Route[] = [
  { method: 'GET', pattern: /^\/api\/sessions$/, handler: () => state.sessions },
  { method: 'GET', pattern: /^\/api\/projects$/, handler: () => state.projects },
  { method: 'GET', pattern: /^\/api\/previews$/, handler: () => ({ items: state.previews }) },
  { method: 'GET', pattern: /^\/api\/project-groups$/, handler: () => state.groups },
  { method: 'GET', pattern: /^\/api\/harnesses$/, handler: () => harnessPayload() },
  { method: 'GET', pattern: /^\/api\/config$/, handler: () => state.config },
  { method: 'GET', pattern: /^\/api\/voice$/, handler: () => voicePayload() },
  { method: 'GET', pattern: /^\/api\/profiles$/, handler: () => profilesPayload() },
  {
    method: 'GET', pattern: /^\/api\/notifications$/,
    handler: () => ({ notifications: [], deliveries: [], automation: [] }),
  },
  {
    method: 'GET', pattern: /^\/api\/notes$/,
    handler: (_match, _body, url) => noteSummaries(url.searchParams.get('project_id')),
  },
  {
    method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/notes\/([^/]+)$/,
    handler: match => {
      const note = state.notes.find(item => item.note_id === decodeURIComponent(match[2]))
      if (!note) return error(404, 'Note not found.')
      // The editor's NotePayload: {revision, markdown, status, path, title}.
      return { revision: String(note.revision), markdown: note.content, status: 'ready', path: '', title: note.title }
    },
  },
  {
    method: 'PUT', pattern: /^\/api\/projects\/([^/]+)\/notes\/([^/]+)$/,
    handler: (match, body) => {
      const noteId = decodeURIComponent(match[2])
      const note = state.notes.find(item => item.note_id === noteId)
      if (!note) return error(404, 'Note not found.')
      const markdown = String((body as Record<string, unknown>)?.markdown ?? '')
      apply({ kind: 'note-patch', noteId, patch: { content: markdown, revision: note.revision + 1, updated_at: nowSeconds() } })
      return { revision: String(note.revision + 1), status: 'ready' }
    },
  },
  {
    method: 'GET', pattern: /^\/api\/global-notes\/([^/]+)$/,
    handler: match => {
      const noteId = `global:${decodeURIComponent(match[1])}`
      let note = state.notes.find(item => item.note_id === noteId)
      if (!note) {
        note = {
          note_id: noteId, project_id: '', title: 'Scratchpad', revision: 1,
          updated_at: nowSeconds(), content: '# Scratchpad\n\nShared across every Project. Type here - it even persists (locally).\n',
        }
        apply({ kind: 'note-add', note })
      }
      return { revision: String(note.revision), markdown: note.content, status: 'ready', path: '', title: note.title }
    },
  },
  {
    method: 'PUT', pattern: /^\/api\/global-notes\/([^/]+)$/,
    handler: (match, body) => {
      const noteId = `global:${decodeURIComponent(match[1])}`
      const note = state.notes.find(item => item.note_id === noteId)
      if (!note) return error(404, 'Note not found.')
      const markdown = String((body as Record<string, unknown>)?.markdown ?? '')
      apply({ kind: 'note-patch', noteId, patch: { content: markdown, revision: note.revision + 1, updated_at: nowSeconds() } })
      return { revision: String(note.revision + 1), status: 'ready' }
    },
  },
  {
    method: 'POST', pattern: /^\/api\/projects\/([^/]+)\/notes$/,
    handler: (match, body) => {
      const projectId = decodeURIComponent(match[1])
      const title = String((body as Record<string, unknown>)?.title ?? 'untitled')
      const note = {
        note_id: demoId('n'), project_id: projectId, title,
        revision: 1, updated_at: nowSeconds(), content: `# ${title}\n\n`,
      }
      apply({ kind: 'note-add', note })
      return { id: note.note_id }
    },
  },
  {
    method: 'PATCH', pattern: /^\/api\/projects\/([^/]+)\/notes\/([^/]+)$/,
    handler: (match, body) => {
      const noteId = decodeURIComponent(match[2])
      const note = state.notes.find(item => item.note_id === noteId)
      if (!note) return error(404, 'Note not found.')
      const patch = body as Record<string, unknown>
      apply({
        kind: 'note-patch', noteId,
        patch: {
          ...(typeof patch.title === 'string' ? { title: patch.title } : {}),
          ...(typeof patch.content === 'string' ? { content: patch.content } : {}),
          revision: note.revision + 1, updated_at: nowSeconds(),
        },
      })
      const updated = state.notes.find(item => item.note_id === noteId)!
      return {
        note_id: updated.note_id, project_id: updated.project_id,
        project_name: project(updated.project_id)?.name ?? '',
        title: updated.title, created_at: updated.updated_at - 3600, updated_at: updated.updated_at,
        bytes: updated.content.length, revision: String(updated.revision),
        excerpt: updated.content.slice(0, 160), origin_session_id: null,
      }
    },
  },
  {
    method: 'DELETE', pattern: /^\/api\/projects\/([^/]+)\/notes\/([^/]+)$/,
    handler: match => {
      apply({ kind: 'note-remove', noteId: decodeURIComponent(match[2]) })
      return { ok: true }
    },
  },
  { method: 'POST', pattern: /^\/api\/sessions$/, handler: (_match, body) => spawnSession(body as Record<string, unknown>) },
  {
    method: 'DELETE', pattern: /^\/api\/sessions\/([^/]+)$/,
    handler: match => {
      const id = decodeURIComponent(match[1])
      apply({ kind: 'session-patch', id, patch: { state: 'exited' } })
      apply({ kind: 'session-remove', id })
      return { ok: true }
    },
  },
  {
    method: 'POST', pattern: /^\/api\/sessions\/([^/]+)\/read$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      const target = session(id)
      if (!target) return { ok: true }
      const request = (body ?? {}) as Record<string, unknown>
      const turnSeq = typeof request.turn_seq === 'number' ? request.turn_seq : target.turn_seq ?? 0
      apply({ kind: 'session-patch', id, patch: { read_turn_seq: turnSeq, unread_pin: request.read === false } })
      return { ok: true }
    },
  },
  {
    method: 'POST', pattern: /^\/api\/sessions\/([^/]+)\/broadcast-set$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      apply({ kind: 'session-patch', id, patch: { broadcast: (body as Record<string, unknown>)?.include === true } })
      return session(id) ?? error(404, 'Unknown session.')
    },
  },
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/approvals$/,
    handler: () => ({
      supported: false, enabled: false, ceiling: 'wait', rules: [], rules_source: 'default',
      unavailable: 'Approvals are not part of the demo.', ttl_seconds: 0, max_auto: 0,
      policy: {
        mode: 'wait', run_id: null, expires_at: null, granted_at: null, set_by: 'demo',
        rules: [], auto_approved: 0, max_auto: 0, last_decision_at: null, last_request: null,
        floor_deferred: 0,
      },
      effective_mode: 'wait', modes: ['wait'],
    }),
  },
  {
    method: 'PATCH', pattern: /^\/api\/projects\/([^/]+)$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      const target = project(id)
      if (!target) return error(404, 'Unknown project.')
      const patch = { ...(body as Record<string, unknown>) }
      if ('layout' in patch) {
        patch.layout_revision = target.layout_revision + 1
        delete (patch as Record<string, unknown>).layout_revision_expected
      }
      apply({ kind: 'project-patch', id, patch: patch as Partial<typeof target> })
      return project(id)
    },
  },
  {
    method: 'POST', pattern: /^\/api\/projects\/([^/]+)\/used$/,
    handler: match => {
      const id = decodeURIComponent(match[1])
      const stamp = nowSeconds()
      apply({ kind: 'project-patch', id, patch: { last_used_at: stamp } })
      return { project_id: id, last_used_at: stamp }
    },
  },
  { method: 'POST', pattern: /^\/api\/previews$/, handler: (_match, body) => createPreview(body as Record<string, unknown>) },
  {
    method: 'DELETE', pattern: /^\/api\/previews\/([^/]+)$/,
    handler: match => {
      apply({ kind: 'preview-remove', id: decodeURIComponent(match[1]) })
      return { ok: true }
    },
  },
  {
    method: 'PUT', pattern: /^\/api\/config$/,
    handler: (_match, body) => {
      apply({ kind: 'config-patch', patch: body as Record<string, unknown> })
      return state.config
    },
  },
  {
    method: 'PATCH', pattern: /^\/api\/config$/,
    handler: (_match, body) => {
      apply({ kind: 'config-patch', patch: body as Record<string, unknown> })
      return state.config
    },
  },
  {
    method: 'GET', pattern: /^\/api\/processes$/,
    handler: () => ({
      available: false, diagnostic: 'Process telemetry is not part of the demo.',
      sessions: [], totals: { processes: 0, cpu_pct: 0, memory_bytes: 0, listeners: 0, connections: 0 },
    }),
  },
  { method: 'GET', pattern: /^\/api\/keybindings$/, handler: () => keybindingsPayload() },
  {
    method: 'PUT', pattern: /^\/api\/keybindings$/,
    handler: () => keybindingsPayload(),
  },
  {
    method: 'POST', pattern: /^\/api\/keymap-preset$/,
    handler: (_match, body) => {
      const requested = String((body as Record<string, unknown>)?.preset ?? '')
      if (!KEYMAP_FIXTURE.presets.some(item => item.id === requested)) {
        return error(400, `Unknown keymap preset: ${requested}`)
      }
      apply({ kind: 'keymap-preset', preset: requested })
      return { keybindings: keybindingsPayload() }
    },
  },
  {
    method: 'POST', pattern: /^\/api\/settings\/apply$/,
    handler: (_match, body) => {
      const request = (body ?? {}) as Record<string, unknown>
      const draft = (request.config ?? {}) as Record<string, unknown>
      apply({ kind: 'config-patch', patch: draft })
      const bindings = (request.keybindings as { bindings?: Record<string, string> } | undefined)?.bindings
      return {
        config: { ...state.config, hot_applied: Object.keys(draft), restart_required: [] },
        keybindings: bindings ? { bindings } : null,
        committed: ['config', 'keybindings'],
      }
    },
  },
  {
    method: 'GET', pattern: /^\/api\/settings\/bundle$/,
    handler: (_match, _body, url) => {
      const parts = url.searchParams.get('parts') ?? ''
      const bundle: Record<string, unknown> = {
        config: state.config, keybindings: keybindingsPayload(),
        projects: state.projects, errors: {},
      }
      if (parts.includes('profiles')) bundle.profiles = { profiles: (profilesPayload() as { profiles: unknown[] }).profiles, detected: [] }
      if (parts.includes('automation')) bundle.automation = null
      if (parts.includes('usage')) bundle.usage = null
      if (parts.includes('provider')) bundle.provider = null
      return bundle
    },
  },
  {
    method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/actions$/,
    handler: match => ({
      project_root: project(decodeURIComponent(match[1]))?.root ?? '',
      fingerprint: 'demo', trusted: true, sources: [], files: [],
      actions: [], diagnostics: [],
    }),
  },
  {
    method: 'GET', pattern: /^\/api\/configurator\/options$/,
    handler: () => ({
      harnesses: ['claude', 'codex'], default_harness: 'claude', configured_default: '',
      install_mode: 'installed', source_checkout: '', projects: state.projects.length,
    }),
  },
  { method: 'GET', pattern: /^\/api\/grants$/, handler: () => ({ items: [] }) },
  { method: 'GET', pattern: /^\/api\/clipboard$/, handler: () => ({ items: [], enabled: true }) },
  { method: 'GET', pattern: /^\/api\/prompts$/, handler: () => ({ items: [], projects: [] }) },
  { method: 'GET', pattern: /^\/api\/history$/, handler: () => ({ items: [], total: 0 }) },
  { method: 'GET', pattern: /^\/api\/schedules$/, handler: () => ({ items: [] }) },
  { method: 'GET', pattern: /^\/api\/queue$/, handler: () => ({ items: [] }) },
]

// ------------------------------------------------------------------ install

export function installFakeFetch(): void {
  window.fetch = (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input instanceof URL ? input.toString() : input, init)
    const url = new URL(request.url, location.href)
    // Anything that is not the daemon API (module chunks, wasm, icons, the
    // preview iframe's own subresources) goes to the real network - those are
    // static files on the same static host as the demo itself.
    if (!url.pathname.startsWith('/api/')) return realFetch(input as RequestInfo, init)
    let body: unknown
    const rawBody = init?.body
    if (typeof rawBody === 'string') {
      try { body = JSON.parse(rawBody) } catch { body = undefined }
    }
    for (const route of ROUTES) {
      if (route.method !== request.method) continue
      const match = url.pathname.match(route.pattern)
      if (!match) continue
      const result = route.handler(match, body, url)
      if (result && typeof result === 'object' && '__status' in (result as Record<string, unknown>)) {
        const { __status, ...payload } = result as Record<string, unknown>
        return jsonResponse(payload, Number(__status))
      }
      return jsonResponse(result ?? {})
    }
    if (request.method === 'GET') {
      console.debug(`[demo] unmatched GET ${url.pathname}${url.search} -> {}`)
      return jsonResponse({})
    }
    console.debug(`[demo] unmatched ${request.method} ${url.pathname} -> {ok:true}`)
    return jsonResponse({ ok: true })
  }) as typeof fetch
}
