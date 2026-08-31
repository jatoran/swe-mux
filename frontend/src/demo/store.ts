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
import type { Preview } from '../processFleet.ts'
import type { Project, ProjectGroup, Session } from '../types.ts'
import { initialDemoState } from './fixtures.ts'

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
  /** Raw ANSI scrollback per session id, replayed on every pane attach. */
  terminals: Record<string, string>
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
  | { kind: 'note-add'; note: DemoNote }
  | { kind: 'note-patch'; noteId: string; patch: Partial<DemoNote> }
  | { kind: 'note-remove'; noteId: string }
  /** Bytes the fake PTY produced (echo or scripted reply). Appended to the
   *  stored scrollback and written into every attached pane, in every frame. */
  | { kind: 'term-append'; id: string; data: string }
  /** Bytes the user typed. Recorded so every frame's line-buffer state agrees;
   *  only the originating frame runs the responder. */
  | { kind: 'term-input'; id: string; data: string }
  | { kind: 'reset' }

const STORAGE_KEY = 'swemux-demo-state-v1'
const CHANNEL_NAME = 'swemux-demo-v1'
/** Scrollback kept per session; beyond this the oldest bytes fall off. */
const SCROLLBACK_CAP = 120_000

const FRAME_ID = `frame-${Math.random().toString(36).slice(2, 10)}`

function loadPersisted(): DemoState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as DemoState
    if (!Array.isArray(parsed.sessions) || !Array.isArray(parsed.projects)) return null
    if (parsed.version !== initialDemoState().version) return null
    return parsed
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
      return next
    case 'config-patch':
      next.config = { ...current.config, ...mutation.patch, revision: Number(current.config.revision ?? 0) + 1 }
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
