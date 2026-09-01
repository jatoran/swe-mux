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
import { hashText, PREVIEW_PAGE_IDS } from './fixtures.ts'
import {
  commitChangesPayload, graphPayload, provenancePayload,
  sweMuxSetupPayload, verifyCommandPayload, worktreesPayload,
} from './gitFixtures.ts'
import {
  networkPayload, operationalPayload, processesPayload, storagePayload, workloadsPayload,
} from './fleetFixtures.ts'
import {
  agentEnvironmentPayload, attentionInboxPayload, automationDashboardPayload,
  automationMatrixPayload, clipboardPayload, filesTreePayload, grantsPayload,
  historyProjectsPayload, injectionSafetyPayload, lastReplyPayload, projectAutomationsPayload,
  projectConfigPayload, promptsPayload, providerAccountsPayload, schedulesPayload,
  skillsPayload, usagePayload,
} from './supportPayloads.ts'
import {
  deliverQueuedMessage, landEventsPayload, landPayload, makeLandRequest, makeQueueMessage,
  notificationsPayload, queueAutoPayload, queueMailboxPayload, queueMessagesPayload,
  queueSummaryPayload,
} from './controlPlane.ts'
import { KEYMAP_FIXTURE } from './keymapFixture.ts'
import { DETERMINISTIC, fixtureSeconds } from './determinism.ts'
import { apply, demoId, nowSeconds, project, session, state } from './store.ts'
import { composerInfo, spawnScrollback } from './terminalSim.ts'

/**
 * The page's own `fetch`, captured at install time rather than at module load.
 *
 * `installFakeFetch()` is the first thing `main.tsx` runs, so the two are the same
 * instant in the browser - but only one of them lets this module be imported outside one.
 * The unit suite reads the scenario catalogue, which reaches here through two hops, and a
 * module-scope `window.fetch` made that a `ReferenceError` in Node.
 */
let realFetch: typeof fetch = (...args) => window.fetch(...args)

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
 *
 * The *resolution* is generated too, and that half used to be a lie worth naming: this
 * function asserted "every demo rule is deliverable in a browser" and returned an empty
 * `undeliverable` and `contested`, while the shipped Vim preset's own warning says a
 * browser tab closes on Ctrl+W before the page ever sees it. What the daemon actually
 * answers is subtler and better: the browser-impossible chords are declared `host:
 * "desktop"` in the preset documents, so a tab never resolves them at all, and what is
 * left over is `contested` - chords that work but cost the visitor something, Ctrl+Shift+P
 * against Firefox's private window among them. Serving the generated table is what lets
 * the demo's Settings panel say that instead of drawing a dead chord as live.
 */
function keybindingsPayload(): unknown {
  const preset = state.keymapPreset || 'swemux'
  const known = (id: string): id is keyof typeof KEYMAP_FIXTURE.rules =>
    id in KEYMAP_FIXTURE.rules
  const id = known(preset) ? preset : 'swemux'
  const rules = KEYMAP_FIXTURE.rules[id]
  const resolution = KEYMAP_FIXTURE.resolution[id]
  const summary = KEYMAP_FIXTURE.presets.find(item => item.id === id)
  return {
    preset: id,
    presets: KEYMAP_FIXTURE.presets,
    host: KEYMAP_FIXTURE.host,
    platform: KEYMAP_FIXTURE.platform,
    rules,
    resolved: resolution.bindings,
    prefixes: summary?.prefix ? [summary.prefix, ...summary.prefix_alternates] : [],
    labels: {},
    undeliverable: resolution.undeliverable,
    contested: resolution.contested,
    commands: KEYMAP_FIXTURE.commands,
    groups: [],
    when_flags: [
      'terminalFocused', 'editorFocused', 'inputFocused', 'overlayOpen', 'paletteOpen',
      'drawerFocused', 'sidebarFocused', 'settingsOpen', 'mobile', 'desktop', 'zoomed',
      'multiplePanes', 'multipleTabs', 'hasSelection', 'agentFocused',
    ],
    policy: KEYMAP_FIXTURE.policy,
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

/** Exported for the scenario director, which spawns panes as a *daemon* act (an approved
 *  request, a scripted orchestration) rather than by pressing the Run menu. Same function
 *  the route calls, so a scripted spawn and a visitor's spawn cannot diverge. */
export function spawnSession(body: Record<string, unknown>): unknown {
  const backend = String(body.backend ?? 'shell')
  const projectId = String(body.project_id ?? state.projects[0]?.id ?? '')
  const target = project(projectId)
  if (!target) return error(404, 'Unknown project.')
  const id = demoId('s')
  const created = DETERMINISTIC ? fixtureSeconds(id) : nowSeconds()
  const newSession: Session = {
    id, name: backend === 'shell' ? 'shell' : `${backend} session`,
    project_id: projectId, backend,
    native_session_id: `native-${id}`, cwd: target.root, exe: backend, args: [],
    // Derived from the id rather than drawn, which is what the seeded sessions already do
    // (`fixtures.makeSession`). A pid in an invented install is data about that session,
    // not a random number - and a drawn one made a scripted spawn unreproducible.
    pid: 50000 + (Math.abs(hashText(id)) % 9000), created_at: created,
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
  apply({
    kind: 'session-add',
    session: newSession,
    scrollback: spawnScrollback(composerInfo(newSession)),
  })
  // A spawned pane settles quickly: running → idle, like a CLI finishing startup.
  window.setTimeout(() => {
    if (session(id)) apply({ kind: 'session-patch', id, patch: { state: 'idle', state_since: nowSeconds() } })
  }, 1500)
  return newSession
}

/** Exported for the same reason as `spawnSession`: the preview scenario registers a
 *  listener the way the daemon does, through the one function the route also uses. */
export function createPreview(body: Record<string, unknown>): unknown {
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

/**
 * `GET /api/sessions/{id}/transcript`.
 *
 * Read out of the store rather than invented per request, so the reader shows the
 * turn the visitor just drove - which is the whole reason the tab is worth putting
 * in a demo. A shell has no conversation and says so through `reason`, exactly as
 * the daemon does, instead of rendering an empty list that looks like a failure.
 */
function transcriptPayload(sessionId: string): unknown {
  const target = session(sessionId)
  const messages = state.transcripts[sessionId] ?? []
  const reason = !target ? 'no_transcript'
    : target.backend === 'shell' ? 'not_agent'
      : messages.length ? null : 'no_transcript'
  return {
    session_id: sessionId,
    agent_run_id: target?.agent_run_id ?? null,
    backend: target?.backend ?? '',
    messages: reason === 'not_agent' ? [] : messages,
    trailing_tool_calls: [],
    hidden: reason ? 0 : 4,
    abandoned_messages: 0,
    truncated: false,
    reason,
  }
}

/** `GET /api/sessions/{id}/scan-timeline`. */
function scanTimelinePayload(sessionId: string): unknown {
  const target = session(sessionId)
  const records = state.timelines[sessionId] ?? []
  const agent = Boolean(target) && target?.backend !== 'shell'
  return {
    session_id: sessionId,
    project_id: target?.project_id ?? null,
    agent_run_id: target?.agent_run_id ?? null,
    global_enabled: state.config.scan_timeline_enabled !== false,
    project_enabled: true,
    run_enabled: agent,
    auto_enable: true,
    run_decided: true,
    model: String(state.config.scan_timeline_model || 'demo-observer'),
    daily_budget: { tokens: null, usd: 1, mode: 'usd' },
    spend_today: { tokens: 4_820, cost_usd: 0.02, unpriced_calls: 0 },
    run_budget: { tokens: null, usd: 0.25, mode: 'usd' },
    run_spend: { tokens: 1_140, cost_usd: 0.004 },
    metrics: { record_reads: records.length, rehydrations: 0, rehydration_rate: 0 },
    gates: [
      { id: 'run', label: 'this run', unit: 'usd', used: 0.004, limit: 0.25 },
      { id: 'daily', label: 'today', unit: 'usd', used: 0.02, limit: 1 },
      { id: 'calls', label: 'hourly calls', unit: 'calls', used: records.length, limit: 1_200 },
    ],
    skip_reason: agent ? null : 'this is a shell session, so there is no conversation to scan',
    last_scan_at: records.length ? records[records.length - 1].t1 : null,
    scanning: false,
    records,
    boundaries: [],
    backfill: {
      state: 'idle', processed_chunks: 0, total_chunks: 0,
      created_records: 0, reason: null,
    },
  }
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
  { method: 'GET', pattern: /^\/api\/notifications$/, handler: () => notificationsPayload() },
  {
    method: 'PATCH', pattern: /^\/api\/automation\/notifications$/,
    handler: (_match, body) => {
      apply({ kind: 'notification-read-all', read: (body as Record<string, unknown>)?.read !== false })
      return { ok: true }
    },
  },
  {
    method: 'PATCH', pattern: /^\/api\/automation\/notifications\/([^/]+)$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      const read = (body as Record<string, unknown>)?.read !== false
      apply({
        kind: 'notification-patch', id,
        patch: read ? { read_at: nowSeconds() } : { read_at: undefined },
      })
      return { ok: true }
    },
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
    handler: (_match, _body, url) => processesPayload(url.searchParams.get('session') || undefined),
  },
  { method: 'POST', pattern: /^\/api\/processes\/action$/, handler: () => ({ ok: true, applied: false, diagnostic: 'The demo does not stop invented processes.' }) },
  { method: 'GET', pattern: /^\/api\/telemetry\/workloads$/, handler: () => workloadsPayload() },
  { method: 'GET', pattern: /^\/api\/telemetry\/operational$/, handler: () => operationalPayload() },
  { method: 'GET', pattern: /^\/api\/diagnostics\/network$/, handler: () => networkPayload() },
  { method: 'DELETE', pattern: /^\/api\/diagnostics\/network$/, handler: () => ({ ok: true }) },
  { method: 'GET', pattern: /^\/api\/diagnostics\/storage$/, handler: () => storagePayload() },
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
  // The device-class settings store. It has to be a real read/write pair rather than the
  // unmatched-route fallback: `deviceSettings.ts` reads `profiles` off the GET (and an
  // absent one silently means "every default"), while every Actions and top-bar edit the
  // visitor makes arrives here as a PUT of just the domains it touched.
  {
    method: 'GET', pattern: /^\/api\/settings$/,
    handler: () => ({ profiles: state.deviceSettings }),
  },
  {
    method: 'PUT', pattern: /^\/api\/settings\/([^/]+)$/,
    handler: (match, body) => {
      const profile = decodeURIComponent(match[1])
      if (!(profile in state.deviceSettings)) return error(404, `Unknown settings profile: ${profile}`)
      apply({ kind: 'settings-put', profile, domains: (body ?? {}) as Record<string, unknown> })
      return { profiles: state.deviceSettings }
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
  // The configurator launcher, reported as having nothing to launch into - which is the
  // truth about this install rather than a way of greying a button out. It opens a *real*
  // agent CLI in a conversation about the operator's own swe-mux, reading their config and
  // their health report; the demo has no CLI, no config and no health report, and the
  // canned responder that stands in for an agent elsewhere here would be answering
  // questions about a machine that does not exist.
  //
  // `launchState` turns an empty harness list into a disabled control with a reason, so
  // the press never happens. The launch route below is answered anyway, because "nothing
  // reaches it" is an assumption and the failure if it were wrong is the bad one: an
  // unmatched mutation answers `{ok:true}`, and a spawn that reads `id` off that mints a
  // pane leaf pointing at `undefined` which then persists into the visitor's layout.
  {
    method: 'GET', pattern: /^\/api\/configurator\/options$/,
    handler: () => ({
      harnesses: [], default_harness: null, configured_default: '',
      install_mode: 'installed', source_checkout: '', projects: state.projects.length,
    }),
  },
  {
    method: 'POST', pattern: /^\/api\/configurator\/launch$/,
    handler: () => error(501, 'This demo has no agent CLI to open a configurator session in.'),
  },
  { method: 'GET', pattern: /^\/api\/grants$/, handler: () => grantsPayload() },
  { method: 'GET', pattern: /^\/api\/clipboard$/, handler: () => clipboardPayload() },
  { method: 'GET', pattern: /^\/api\/prompts$/, handler: () => promptsPayload() },
  { method: 'GET', pattern: /^\/api\/history$/, handler: () => ({ items: [], total: 0 }) },
  { method: 'GET', pattern: /^\/api\/history\/projects$/, handler: () => historyProjectsPayload() },
  { method: 'GET', pattern: /^\/api\/history\/backfills$/, handler: () => ({ items: [] }) },
  { method: 'GET', pattern: /^\/api\/schedules$/, handler: () => schedulesPayload() },

  // ---------------------------------------------------------------- the queue
  { method: 'GET', pattern: /^\/api\/queue$/, handler: () => queueSummaryPayload() },
  { method: 'GET', pattern: /^\/api\/queue\/auto$/, handler: () => queueAutoPayload() },
  {
    method: 'GET', pattern: /^\/api\/queue\/messages$/,
    handler: (_match, _body, url) => queueMessagesPayload(url.searchParams.get('target_session_id') || ''),
  },
  {
    method: 'GET', pattern: /^\/api\/queue\/mailbox$/,
    handler: (_match, _body, url) => queueMailboxPayload(url.searchParams.get('author') || 'non_human'),
  },
  {
    method: 'POST', pattern: /^\/api\/queue\/messages$/,
    handler: (_match, body) => {
      const request = (body ?? {}) as Record<string, unknown>
      const targetSessionId = String(request.target_session_id ?? '')
      if (!session(targetSessionId)) return error(404, 'Unknown session.')
      const message = makeQueueMessage({
        targetSessionId,
        body: String(request.body ?? ''),
        state: request.armed === true ? 'armed' : 'draft',
      })
      apply({ kind: 'queue-add', message })
      return message
    },
  },
  {
    method: 'PATCH', pattern: /^\/api\/queue\/messages\/([^/]+)$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      const existing = state.queue.find(item => item.id === id)
      if (!existing) return error(404, 'Unknown message.')
      const request = (body ?? {}) as Record<string, unknown>
      // The revision check is the one thing about the real queue worth reproducing
      // exactly: two devices editing one row is the case it exists for, and a demo that
      // accepted any revision would be showing a field that means nothing.
      if (typeof request.revision === 'number' && request.revision !== existing.revision) {
        return { __status: 409, error: 'That message changed since you read it.', code: 'revision_conflict', revision: existing.revision }
      }
      const patch: Record<string, unknown> = {}
      if (typeof request.body === 'string') { patch.body = request.body; patch.edited_at = nowSeconds() }
      if (typeof request.armed === 'boolean') {
        patch.state = request.armed ? 'armed' : 'draft'
        patch.armed_at = request.armed ? nowSeconds() : null
      }
      if ('constraints' in request) patch.constraints = request.constraints ?? null
      if (typeof request.retarget_session_id === 'string') {
        patch.target_session_id = request.retarget_session_id
        patch.retargeted_from = { session_id: existing.target_session_id, label: existing.target_label }
      }
      apply({ kind: 'queue-patch', id, patch })
      return state.queue.find(item => item.id === id)
    },
  },
  {
    method: 'POST', pattern: /^\/api\/queue\/messages\/([^/]+)\/cancel$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      if (!state.queue.some(item => item.id === id)) return error(404, 'Unknown message.')
      const kind = String((body as Record<string, unknown>)?.kind ?? 'cancelled')
      apply({
        kind: 'queue-patch', id,
        patch: { state: 'cancelled', cancel_kind: kind as 'cancelled' | 'skipped' | 'revoked' },
      })
      return state.queue.find(item => item.id === id)
    },
  },
  {
    method: 'DELETE', pattern: /^\/api\/queue\/messages\/([^/]+)$/,
    handler: match => {
      const id = decodeURIComponent(match[1])
      const known = state.queue.some(item => item.id === id)
      if (known) apply({ kind: 'queue-remove', id })
      return { deleted: true, message_id: id, already_deleted: !known }
    },
  },
  {
    method: 'POST', pattern: /^\/api\/queue\/send-next$/,
    handler: (_match, body) => {
      const request = (body ?? {}) as Record<string, unknown>
      const id = String(request.message_id ?? '')
      const message = state.queue.find(item => item.id === id)
      if (!message) return error(404, 'Unknown message.')
      if (typeof request.revision === 'number' && request.revision !== message.revision) {
        return { __status: 409, error: 'That message changed since you read it.', code: 'revision_conflict', revision: message.revision }
      }
      return deliverQueuedMessage(id)
        ? { status: 'sent', confirmed: true }
        : error(409, 'That message is no longer deliverable.')
    },
  },
  {
    method: 'PUT', pattern: /^\/api\/queue\/auto\/sessions\/([^/]+)$/,
    handler: (match, body) => {
      const id = decodeURIComponent(match[1])
      const enabled = (body as Record<string, unknown>)?.enabled
      if (typeof enabled === 'boolean') apply({ kind: 'auto-delivery-set', id, enabled })
      return queueAutoPayload()
    },
  },
  { method: 'POST', pattern: /^\/api\/queue\/auto\/pause$/, handler: () => queueAutoPayload() },

  // ------------------------------------------------------------- the fleet's
  // conversation surfaces, both read out of the same store the panes write to.
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/transcript$/,
    handler: match => transcriptPayload(decodeURIComponent(match[1])),
  },
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/scan-timeline$/,
    handler: match => scanTimelinePayload(decodeURIComponent(match[1])),
  },
  {
    // Arming is per conversation in the product; the demo has nothing to arm, so it
    // answers with the same state rather than pretending the switch did something.
    method: 'PUT', pattern: /^\/api\/sessions\/([^/]+)\/scan-timeline$/,
    handler: match => scanTimelinePayload(decodeURIComponent(match[1])),
  },
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/scan-timeline\/([^/]+)$/,
    handler: match => {
      const records = state.timelines[decodeURIComponent(match[1])] ?? []
      const record = records.find(item => item.id === decodeURIComponent(match[2]))
      return {
        source: record
          ? [{ note: 'The demo stores no source messages; this record is a fixture.', record_id: record.id }]
          : [],
        metrics: { record_reads: records.length, rehydrations: 1, rehydration_rate: 1 / Math.max(1, records.length) },
      }
    },
  },
  { method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/last-reply$/, handler: () => lastReplyPayload() },
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/agent-environment$/,
    handler: match => agentEnvironmentPayload(decodeURIComponent(match[1])),
  },
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/agent-environment\/mcp-tools$/,
    handler: () => ({
      server: 'mux', backend: '', evidence: 'swe_mux_owned', status: 'ok',
      tools: [
        { name: 'list_sessions', description: 'Sibling sessions and their live status', read_only: true },
        { name: 'read_transcript', description: 'A pageable read of another session’s conversation', read_only: true },
      ],
      total: 2, truncated: false, note: 'Invented for the demo.', diagnostic: '',
      observed_at: nowSeconds(), ttl_ms: 60_000, cache_scope: 'public',
      server_version: '0.0.0-demo', fingerprint: 'demo', cached: false,
    }),
  },
  {
    method: 'GET', pattern: /^\/api\/sessions\/([^/]+)\/skills$/,
    handler: match => skillsPayload(decodeURIComponent(match[1])),
  },

  // -------------------------------------------------------------------- git
  {
    method: 'GET', pattern: /^\/api\/git\/worktrees$/,
    handler: (_match, _body, url) => worktreesPayload(
      url.searchParams.get('project_id') || '',
      url.searchParams.get('detail') || 'summary',
      url.searchParams.get('worktree') || '',
    ),
  },
  {
    method: 'GET', pattern: /^\/api\/git\/graph$/,
    handler: (_match, _body, url) => graphPayload(
      url.searchParams.get('project_id') || '',
      Number(url.searchParams.get('limit')) || 80,
      url.searchParams.get('grep') || '',
      url.searchParams.get('author') || '',
    ),
  },
  {
    method: 'GET', pattern: /^\/api\/git\/provenance$/,
    handler: (_match, _body, url) => provenancePayload(
      url.searchParams.get('project_id') || '',
      url.searchParams.get('subject') || '',
    ),
  },
  {
    method: 'GET', pattern: /^\/api\/git\/commits\/([^/]+)\/changes$/,
    handler: (match, _body, url) => commitChangesPayload(
      url.searchParams.get('project_id') || '',
      decodeURIComponent(match[1]),
    ) ?? error(404, 'Unknown commit.'),
  },
  { method: 'GET', pattern: /^\/api\/git\/swe-mux-setup$/, handler: () => sweMuxSetupPayload() },
  {
    method: 'GET', pattern: /^\/api\/land$/,
    handler: (_match, _body, url) => landPayload(url.searchParams.get('project_id') || ''),
  },
  { method: 'GET', pattern: /^\/api\/land\/verify-command$/, handler: () => verifyCommandPayload() },
  {
    method: 'GET', pattern: /^\/api\/land\/([^/]+)\/events$/,
    handler: match => landEventsPayload(decodeURIComponent(match[1])),
  },
  {
    method: 'POST', pattern: /^\/api\/land$/,
    handler: (_match, body) => {
      const request = (body ?? {}) as Record<string, unknown>
      const worktreeRoot = String(request.worktree_root ?? '')
      const projectId = String(request.project_id ?? '')
      // The branch is read off whichever session is standing in that checkout, which is
      // also how the daemon knows: a landing is about a worktree, and the session in it
      // is the thing that can say what branch it is on.
      const standing = state.sessions.find(item => item.runtime_cwd === worktreeRoot || item.cwd === worktreeRoot)
      if (!standing) return error(400, 'No session is standing in that checkout.')
      apply({
        kind: 'land-add',
        request: makeLandRequest({
          projectId,
          branch: standing.git?.branch || 'detached',
          worktreeRoot,
          requestedBy: standing.id,
        }),
      })
      return { ok: true }
    },
  },
  {
    method: 'DELETE', pattern: /^\/api\/land\/([^/]+)$/,
    handler: match => {
      apply({ kind: 'land-remove', id: decodeURIComponent(match[1]) })
      return { ok: true }
    },
  },
  {
    // Spawn and control requests share one decision endpoint. Approving a spawn is what
    // starts a session; drafting one never does, and that boundary is the whole reason
    // the surface exists.
    method: 'POST', pattern: /^\/api\/projects\/([^/]+)\/observations\/([^/]+)\/decide$/,
    handler: (match, body) => {
      const requestId = decodeURIComponent(match[2])
      const request = state.spawnRequests.find(item => item.id === requestId)
      if (!request) return error(404, 'Unknown request.')
      const approve = String((body as Record<string, unknown>)?.decision ?? '') === 'approve'
      if (!approve) {
        apply({ kind: 'spawn-request-patch', id: requestId, patch: { done: true, status: 'dismissed', decided_by: 'you' } })
        return {}
      }
      const created = spawnSession({ backend: request.backend, project_id: request.project_id }) as { id?: string }
      if (created?.id) apply({ kind: 'session-patch', id: created.id, patch: { name: request.name } })
      apply({
        kind: 'spawn-request-patch', id: requestId,
        patch: { done: true, status: 'approved', decided_by: 'you', session_id: created?.id ?? null },
      })
      return { session: created?.id ? { id: created.id, name: request.name } : undefined }
    },
  },

  // ------------------------------------------------------- money and policy
  { method: 'GET', pattern: /^\/api\/usage$/, handler: () => usagePayload() },
  { method: 'GET', pattern: /^\/api\/attention\/inbox$/, handler: () => attentionInboxPayload() },
  { method: 'GET', pattern: /^\/api\/attention\/rules$/, handler: () => ({ rules: [] }) },
  { method: 'GET', pattern: /^\/api\/automation\/dashboard$/, handler: () => automationDashboardPayload() },
  { method: 'GET', pattern: /^\/api\/automation\/projects$/, handler: () => automationMatrixPayload() },
  { method: 'GET', pattern: /^\/api\/automation\/injection-safety$/, handler: () => injectionSafetyPayload() },
  { method: 'GET', pattern: /^\/api\/automation\/batches$/, handler: () => ({ items: [] }) },
  { method: 'GET', pattern: /^\/api\/automation\/rules$/, handler: () => ({ text: '# The demo ships no automation rules.\n' }) },
  { method: 'GET', pattern: /^\/api\/automation\/notifications$/, handler: () => ({ items: [] }) },
  { method: 'GET', pattern: /^\/api\/provider-accounts$/, handler: () => providerAccountsPayload() },
  {
    // Switching accounts is the one act on this surface the demo can honestly perform:
    // it is a choice about which saved credential is current, and the demo owns both.
    method: 'POST', pattern: /^\/api\/provider-accounts\/([^/]+)\/([^/]+)\/select$/,
    handler: match => {
      apply({
        kind: 'provider-select',
        provider: decodeURIComponent(match[1]),
        accountId: decodeURIComponent(match[2]),
      })
      return providerAccountsPayload()
    },
  },
  // A refresh re-reads quotas the demo invents, so it answers with the same figures
  // rather than pretending to have gone anywhere.
  { method: 'POST', pattern: /^\/api\/provider-accounts\/refresh$/, handler: () => providerAccountsPayload() },
  { method: 'POST', pattern: /^\/api\/provider-accounts\/([^/]+)\/verify$/, handler: () => providerAccountsPayload() },
  {
    method: 'POST', pattern: /^\/api\/provider-accounts\/([^/]+)\/login$/,
    handler: () => error(400, 'Signing in reaches a real provider, so the demo does not offer it.'),
  },
  { method: 'GET', pattern: /^\/api\/experiences$/, handler: () => ({ items: [] }) },

  // ------------------------------------------------------------ per-Project
  {
    method: 'GET', pattern: /^\/api\/project\/config$/,
    handler: (_match, _body, url) => projectConfigPayload(url.searchParams.get('project_id') || ''),
  },
  { method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/automations$/, handler: () => projectAutomationsPayload() },
  { method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/schedules$/, handler: () => schedulesPayload() },
  {
    method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/files\/tree$/,
    handler: match => filesTreePayload(decodeURIComponent(match[1])),
  },
  { method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/files\/recent$/, handler: () => ({ items: [] }) },
  { method: 'GET', pattern: /^\/api\/projects\/([^/]+)\/observations$/, handler: () => ({ items: [] }) },
]

// ------------------------------------------------------------------ install

export function installFakeFetch(): void {
  realFetch = window.fetch.bind(window)
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
