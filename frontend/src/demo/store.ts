/**
 * The demo's whole "daemon": a JSON state blob, a reducer over named mutations,
 * and two fan-outs - BroadcastChannel (so the desktop and phone iframes on the
 * marketing page mirror each other live) and localStorage (so a visitor's
 * fiddling survives a reload, which is cute rather than load-bearing).
 *
 * Every fake surface reads through this module: the fetch shim serves GETs from
 * `state`, mutating routes call `apply()`, and the fake sockets subscribe to
 * `onMutation` to push event frames / terminal bytes into the app. Nothing here
 * talks to any network - that is the entire point of the demo build.
 */
import type { PaneLayout, PaneNode } from '../layout.ts'
import type { Preview } from '../processFleet.ts'
import type { TranscriptMessage } from '../transcriptView.ts'
import type { Project, ProjectGroup, Session } from '../types.ts'
import { initialDemoState } from './fixtures.ts'
import type { DemoScanRecord } from './conversation.ts'

export type DemoNote = {
  note_id: string
  project_id: string
  title: string
  revision: number
  updated_at: number
  content: string
}

export type DemoState = {
  /** Fixture-schema version. Bump `DEMO_STATE_VERSION` in fixtures.ts when the
   *  seed shape changes; persisted copies from an older demo build are discarded. */
  version: number
  sessions: Session[]
  projects: Project[]
  groups: ProjectGroup[]
  previews: Preview[]
  notes: DemoNote[]
  config: Record<string, unknown>
  /** Which keymap preset is in force. The demo ships the daemon's own preset
   *  documents (`keymapFixture.ts`), so switching to tmux or Vim really does
   *  redraw every chord in Settings rather than only changing a label. */
  keymapPreset: string
  /** Raw ANSI scrollback per session id, replayed on every pane attach. */
  terminals: Record<string, string>
  /**
   * The structured conversation behind each agent pane, which is a different
   * artifact from the ANSI above and has to be: the drawer's Transcript tab reads
   * merged messages and tool-call boundaries, not bytes. Both are appended by the
   * same submit, so the box and the reader can never tell different stories.
   */
  transcripts: Record<string, TranscriptMessage[]>
  /** Scan-timeline records per session, one written per completed turn. */
  timelines: Record<string, DemoScanRecord[]>
  /** Event-bus watermark; monotonic across every frame via the reducer. */
  seq: number
}

export type DemoMutation =
  | { kind: 'session-patch'; id: string; patch: Partial<Session> }
  | { kind: 'session-add'; session: Session; scrollback: string }
  | { kind: 'session-remove'; id: string }
  | { kind: 'project-patch'; id: string; patch: Partial<Project> }
  | { kind: 'preview-add'; preview: Preview }
  | { kind: 'preview-remove'; id: string }
  | { kind: 'config-patch'; patch: Record<string, unknown> }
  | { kind: 'keymap-preset'; preset: string }
  | { kind: 'note-add'; note: DemoNote }
  | { kind: 'note-patch'; noteId: string; patch: Partial<DemoNote> }
  | { kind: 'note-remove'; noteId: string }
  /** Bytes the fake PTY produced (echo or scripted reply). Appended to the
   *  stored scrollback and written into every attached pane, in every frame. */
  | { kind: 'term-append'; id: string; data: string }
  /** Bytes the user typed. Recorded so every frame's line-buffer state agrees;
   *  only the originating frame runs the responder. */
  | { kind: 'term-input'; id: string; data: string }
  /** One side of the conversation, appended as the fake turn produces it. */
  | { kind: 'transcript-append'; id: string; message: TranscriptMessage }
  | { kind: 'timeline-append'; id: string; record: DemoScanRecord }
  | { kind: 'reset' }

const STORAGE_KEY = 'swemux-demo-state-v1'
const CHANNEL_NAME = 'swemux-demo-v1'
/** Scrollback kept per session; beyond this the oldest bytes fall off. */
const SCROLLBACK_CAP = 120_000

const FRAME_ID = `frame-${Math.random().toString(36).slice(2, 10)}`

/**
 * Pull every open turn back to the recent past.
 *
 * A turn clock is an absolute timestamp measured against wall time, so a visitor
 * who returns a day later would find a pane that has been "working" for 26
 * hours - which reads as a hung demo rather than as a busy agent. Rebasing on
 * load keeps the pulse honest without persisting anything about when they left.
 */
function rebaseWorkingTurns(loaded: DemoState): DemoState {
  const now = Math.floor(Date.now() / 1000)
  const CEILING_SECONDS = 15 * 60
  return {
    ...loaded,
    sessions: loaded.sessions.map(item => {
      const started = item.turn_started_at
      if (item.state !== 'working' || !started || now - started <= CEILING_SECONDS) return item
      const fresh = now - (30 + Math.floor(Math.random() * 240))
      return {
        ...item,
        turn_started_at: fresh,
        state_since: fresh,
        ...(item.running_work_since ? { running_work_since: fresh } : {}),
      }
    }),
  }
}

function loadPersisted(): DemoState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as DemoState
    if (!Array.isArray(parsed.sessions) || !Array.isArray(parsed.projects)) return null
    if (parsed.version !== initialDemoState().version) return null
    return rebaseWorkingTurns(parsed)
  } catch {
    return null
  }
}

export let state: DemoState = loadPersisted() ?? initialDemoState()

type MutationListener = (mutation: DemoMutation, local: boolean) => void
const listeners = new Set<MutationListener>()

export function onMutation(listener: MutationListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

let persistTimer: number | undefined
function schedulePersist(): void {
  if (persistTimer !== undefined) return
  persistTimer = window.setTimeout(() => {
    persistTimer = undefined
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state))
    } catch {
      // Storage full or blocked (private mode): the demo simply stops persisting.
    }
  }, 400)
}

/**
 * Drop one leaf from a Project's pane tree, collapsing whatever that empties.
 *
 * The real daemon prunes the layout when a session ends; the demo has to do the same
 * or a killed pane leaves a leaf behind, and because the layout is persisted the
 * wreckage survives a reload - which is exactly the "I have to reset the demo" shape.
 * A split that loses one side becomes its surviving side rather than a split with a
 * hole, and a stack that loses its active child promotes the next one.
 */
function pruneLayoutLeaf(project: Project, leafId: string): Project {
  const layout = project.layout as PaneLayout | undefined
  if (!layout?.root) return project
  const visit = (node: PaneNode): PaneNode | null => {
    if (node.type === 'stack') {
      const children = node.children.filter(child => child.id !== leafId)
      if (!children.length) return null
      if (children.length === node.children.length) return node
      const active = children.some(child => child.id === node.active_child_id)
        ? node.active_child_id
        : children[children.length - 1].id
      return { ...node, children, active_child_id: active }
    }
    const first = visit(node.first)
    const second = visit(node.second)
    if (!first) return second
    if (!second) return first
    if (first === node.first && second === node.second) return node
    return { ...node, first, second }
  }
  const root = visit(layout.root)
  if (root === layout.root) return project
  return {
    ...project,
    layout: { ...layout, root },
    layout_revision: project.layout_revision + 1,
  }
}

function reduce(current: DemoState, mutation: DemoMutation): DemoState {
  const next: DemoState = { ...current, seq: current.seq + 1 }
  switch (mutation.kind) {
    case 'session-patch':
      next.sessions = current.sessions.map(item =>
        item.id === mutation.id ? { ...item, ...mutation.patch } : item)
      return next
    case 'session-add':
      next.sessions = [...current.sessions.filter(item => item.id !== mutation.session.id), mutation.session]
      next.terminals = { ...current.terminals, [mutation.session.id]: mutation.scrollback }
      return next
    case 'session-remove': {
      next.sessions = current.sessions.filter(item => item.id !== mutation.id)
      const terminals = { ...current.terminals }
      delete terminals[mutation.id]
      next.terminals = terminals
      // A session's conversation and scan records die with it. Left behind they would
      // accumulate for the life of the persisted state and, worse, be served to a
      // freshly spawned pane that happened to reuse the id.
      const transcripts = { ...current.transcripts }
      delete transcripts[mutation.id]
      next.transcripts = transcripts
      const timelines = { ...current.timelines }
      delete timelines[mutation.id]
      next.timelines = timelines
      // The layout is pruned here rather than by the route that removed the session,
      // because every path that ends a session goes through this mutation. A leaf left
      // pointing at a dead session is what used to persist into localStorage and make a
      // reloaded demo draw a pane with nothing behind it.
      next.projects = next.projects.map(project => pruneLayoutLeaf(project, mutation.id))
      return next
    }
    case 'project-patch':
      next.projects = current.projects.map(item =>
        item.id === mutation.id ? { ...item, ...mutation.patch } : item)
      return next
    case 'preview-add':
      next.previews = [...current.previews.filter(item => item.id !== mutation.preview.id), mutation.preview]
      return next
    case 'preview-remove':
      next.previews = current.previews.filter(item => item.id !== mutation.id)
      next.projects = next.projects.map(project => pruneLayoutLeaf(project, mutation.id))
      return next
    case 'config-patch':
      next.config = { ...current.config, ...mutation.patch, revision: Number(current.config.revision ?? 0) + 1 }
      return next
    case 'keymap-preset':
      next.keymapPreset = mutation.preset
      return next
    case 'note-add':
      next.notes = [...current.notes, mutation.note]
      return next
    case 'note-patch':
      next.notes = current.notes.map(item =>
        item.note_id === mutation.noteId ? { ...item, ...mutation.patch } : item)
      return next
    case 'note-remove':
      next.notes = current.notes.filter(item => item.note_id !== mutation.noteId)
      return next
    case 'term-append': {
      const existing = current.terminals[mutation.id] ?? ''
      const joined = existing + mutation.data
      next.terminals = {
        ...current.terminals,
        [mutation.id]: joined.length > SCROLLBACK_CAP ? joined.slice(joined.length - SCROLLBACK_CAP) : joined,
      }
      return next
    }
    case 'term-input':
      // Recorded for cross-frame line-buffer agreement; no state change beyond seq.
      return next
    case 'transcript-append': {
      const existing = current.transcripts[mutation.id] ?? []
      next.transcripts = {
        ...current.transcripts,
        [mutation.id]: [...existing, { ...mutation.message, ordinal: existing.length + 1 }],
      }
      return next
    }
    case 'timeline-append': {
      const existing = current.timelines[mutation.id] ?? []
      next.timelines = { ...current.timelines, [mutation.id]: [...existing, mutation.record] }
      return next
    }
    case 'reset':
      return initialDemoState()
  }
}

const channel: BroadcastChannel | null = typeof BroadcastChannel === 'function'
  ? new BroadcastChannel(CHANNEL_NAME)
  : null

channel?.addEventListener('message', event => {
  const payload = event.data as { from?: string; mutation?: DemoMutation }
  if (!payload || payload.from === FRAME_ID || !payload.mutation) return
  state = reduce(state, payload.mutation)
  // The originating frame already persisted; a second write from here would only
  // race it with identical bytes, so persistence stays with the origin.
  for (const listener of listeners) listener(payload.mutation, false)
})

/** Apply a mutation locally, persist it, and mirror it into every other frame. */
export function apply(mutation: DemoMutation): void {
  state = reduce(state, mutation)
  schedulePersist()
  channel?.postMessage({ from: FRAME_ID, mutation })
  for (const listener of listeners) listener(mutation, true)
}

export function session(id: string): Session | undefined {
  return state.sessions.find(item => item.id === id)
}

export function project(id: string): Project | undefined {
  return state.projects.find(item => item.id === id)
}

export const nowSeconds = (): number => Math.floor(Date.now() / 1000)

let idCounter = 0
export const demoId = (prefix: string): string => `${prefix}-${Date.now().toString(36)}-${(idCounter += 1)}`
