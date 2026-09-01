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
import type { AssistantAction, AssistantMessage } from '../assistant.ts'
import type { PaneLayout, PaneNode } from '../layout.ts'
import type { Preview } from '../processFleet.ts'
import type { QueueMessage, SpawnRequestRow } from '../queueApi.ts'
import type { TranscriptMessage } from '../transcriptView.ts'
import type { Project, ProjectGroup, Session } from '../types.ts'
import { demoRandom, DETERMINISTIC, nextOrdinal, trueRandom } from './determinism.ts'
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

/**
 * One record in the demo's notification history, shaped as `NotificationsTab` reads it.
 *
 * Held in the store rather than answered flat because a scenario's whole point is that
 * something *arrives*: the app toasts and badges only the ids it has not seen, so a
 * constant list can never fire a notification, however many events are broadcast at it.
 */
export type DemoNotification = {
  id: string
  session_id?: string
  kind: string
  title: string
  message: string
  severity: string
  created_at: number
  read_at?: number
}

/**
 * A land-queue request, in the shape `GET /api/land` returns.
 *
 * The land surface was a constant too, and a landing is the one control-plane act whose
 * interest is entirely in its *transitions* - queued, reconciling, verifying, landed -
 * so a fixture that showed only the last of them demonstrated a row rather than a queue.
 */
export type DemoLandRequest = {
  id: string
  project_id: string
  branch: string
  worktree_root: string
  state: string
  requested_by: string
  requested_by_name: string
  created_at: number
  updated_at: number
  landed_at?: number | null
  error?: string
  verification?: { kind: string; reason?: string; duration_s?: number; step?: string }
  events: Array<{ at: number; state: string; note: string }>
}

/**
 * The Mux assistant's dialog, as the demo's daemon owns it.
 *
 * In the product this state is genuinely daemon-side: the panel rebuilds from one detail
 * fetch and then advances on `assistant_*` events, so two devices watching one dialog
 * render the same turn. Keeping it in the store rather than in a module beside the panel
 * is what preserves that here - the desktop and the phone frame are separate JavaScript
 * realms, and a conversation held in one of them would be a conversation the other could
 * not see, which is the opposite of the claim the scenario is making.
 */
export type DemoAssistant = {
  dialogId: string
  createdAt: number
  title: string
  messages: AssistantMessage[]
  actions: AssistantAction[]
  turnRunning: boolean
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
  /** Which saved provider account is selected, per provider. Held here rather than in
   *  the payload builder so a switch survives a reload and reaches the other frame. */
  providerSelection: Record<string, string>
  /**
   * The daemon's device-class settings store (`/api/settings`), profile to domain to blob.
   *
   * State rather than a constant for the usual reason, plus one specific to it: the app
   * writes here whenever the visitor edits Actions or a top bar, and a route that accepted
   * the write and then served the seed back would undo their edit on the next read.
   */
  deviceSettings: Record<string, Record<string, unknown>>
  /**
   * The control plane, which is the half of the product a terminal recording cannot
   * show. All four were read-only constants until the scenario director needed them to
   * move; they are state now for the same reason the fleet is - a surface that cannot
   * change cannot demonstrate anything, and a scenario that mutated a payload builder
   * would not reach the other frame.
   */
  queue: QueueMessage[]
  notifications: DemoNotification[]
  spawnRequests: SpawnRequestRow[]
  lands: DemoLandRequest[]
  /** Sessions opted in to auto-delivery, by id. A list rather than a flag because the
   *  product's switch is per session inside a per-install master, and the demo's point
   *  is that a prompt delivers into *this* pane because someone allowed it to. */
  autoDelivery: string[]
  /** The voice assistant's conversation. See `DemoAssistant`. */
  assistant: DemoAssistant
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
  | { kind: 'settings-put'; profile: string; domains: Record<string, unknown> }
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
  | { kind: 'provider-select'; provider: string; accountId: string }
  // ---------------------------------------------------------------- control plane
  | { kind: 'queue-add'; message: QueueMessage }
  | { kind: 'queue-patch'; id: string; patch: Partial<QueueMessage> }
  | { kind: 'queue-remove'; id: string }
  | { kind: 'notification-add'; notification: DemoNotification }
  | { kind: 'notification-patch'; id: string; patch: Partial<DemoNotification> }
  /** `id: ''` marks every record, which is what the tab's "dismiss all" does. */
  | { kind: 'notification-read-all'; read: boolean }
  | { kind: 'spawn-request-add'; request: SpawnRequestRow }
  | { kind: 'spawn-request-patch'; id: string; patch: Partial<SpawnRequestRow> }
  | { kind: 'land-add'; request: DemoLandRequest }
  | { kind: 'land-patch'; id: string; patch: Partial<DemoLandRequest>; event?: { state: string; note: string } }
  | { kind: 'land-remove'; id: string }
  | { kind: 'auto-delivery-set'; id: string; enabled: boolean }
  // ------------------------------------------------------------------- the assistant
  /** What the operator said, accepted. One message id per turn, as the daemon does it. */
  | { kind: 'assistant-turn'; turnId: string; text: string }
  /** One sentence of the reply, appended to this turn's assistant bubble. Sentence at a
   *  time rather than all at once because that is how the product streams it - synthesis
   *  runs behind the words - and a demo that pasted the paragraph would be showing a
   *  different product. */
  | { kind: 'assistant-say'; turnId: string; messageId: string; display: string; speech?: string }
  /** A confirmation card, opened or updated. */
  | { kind: 'assistant-action'; action: AssistantAction }
  /** A card resolved: the card closes and a line saying what happened stays. */
  | { kind: 'assistant-resolved'; actionId: string; display: string; status: string }
  | { kind: 'assistant-done'; turnId: string; messageId: string }
  | { kind: 'reset' }

const STORAGE_KEY = 'swemux-demo-state-v1'
const CHANNEL_NAME = 'swemux-demo-v1'
/** Scrollback kept per session; beyond this the oldest bytes fall off. */
const SCROLLBACK_CAP = 120_000

/** Deliberately off the *real* random source even under determinism - see `trueRandom`. */
const FRAME_ID = `frame-${trueRandom().toString(36).slice(2, 10)}`

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
      const fresh = now - (30 + Math.floor(demoRandom() * 240))
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
  // A deterministic run starts from the seed and nowhere else: loading a visitor's saved
  // fleet would make the run a function of their history, and writing an epoch-stamped
  // one back would leave the *next* visitor with sessions dated 2026-03-14.
  if (DETERMINISTIC) return null
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
  // Outside a browser there is no `window.setTimeout` and no storage to write to; the
  // unit suite drives this reducer directly to check the control plane's shapes.
  if (DETERMINISTIC || typeof window === 'undefined' || persistTimer !== undefined) return
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
    case 'settings-put':
      // Merged per domain, not replaced: the daemon's PUT carries only the domains the
      // caller touched, and a whole-profile overwrite would drop the ones it did not.
      next.deviceSettings = {
        ...current.deviceSettings,
        [mutation.profile]: { ...current.deviceSettings[mutation.profile], ...mutation.domains },
      }
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
    case 'provider-select':
      next.providerSelection = { ...current.providerSelection, [mutation.provider]: mutation.accountId }
      return next
    case 'queue-add':
      next.queue = [...current.queue.filter(item => item.id !== mutation.message.id), mutation.message]
      return next
    case 'queue-patch':
      next.queue = current.queue.map(item => (item.id === mutation.id
        // The revision moves on every patch, because the app sends the revision it holds
        // back with an edit and a queue that never bumped one could not refuse a stale
        // write - which is the single most load-bearing thing about the real queue.
        ? { ...item, ...mutation.patch, revision: item.revision + 1, updated_at: nowSeconds() }
        : item))
      return next
    case 'queue-remove':
      next.queue = current.queue.filter(item => item.id !== mutation.id)
      return next
    case 'notification-add':
      next.notifications = [...current.notifications, mutation.notification]
      return next
    case 'notification-patch':
      next.notifications = current.notifications.map(item =>
        item.id === mutation.id ? { ...item, ...mutation.patch } : item)
      return next
    case 'notification-read-all':
      next.notifications = current.notifications.map(item => ({
        ...item,
        ...(mutation.read ? { read_at: nowSeconds() } : { read_at: undefined }),
      }))
      return next
    case 'spawn-request-add':
      next.spawnRequests = [
        ...current.spawnRequests.filter(item => item.id !== mutation.request.id),
        mutation.request,
      ]
      return next
    case 'spawn-request-patch':
      next.spawnRequests = current.spawnRequests.map(item =>
        item.id === mutation.id ? { ...item, ...mutation.patch } : item)
      return next
    case 'land-add':
      next.lands = [...current.lands.filter(item => item.id !== mutation.request.id), mutation.request]
      return next
    case 'land-patch': {
      const stamp = nowSeconds()
      next.lands = current.lands.map(item => (item.id === mutation.id
        ? {
          ...item,
          ...mutation.patch,
          updated_at: stamp,
          // The trail is append-only on purpose: a refusal has to go on saying it
          // happened, which is the same rule the real land queue's event trail follows.
          events: mutation.event
            ? [...item.events, { at: stamp, state: mutation.event.state, note: mutation.event.note }]
            : item.events,
        }
        : item))
      return next
    }
    case 'land-remove':
      next.lands = current.lands.filter(item => item.id !== mutation.id)
      return next
    case 'auto-delivery-set':
      next.autoDelivery = mutation.enabled
        ? [...new Set([...current.autoDelivery, mutation.id])]
        : current.autoDelivery.filter(item => item !== mutation.id)
      return next
    case 'assistant-turn': {
      const id = `user:${mutation.turnId}`
      const messages = current.assistant.messages.some(item => item.id === id)
        ? current.assistant.messages
        : [...current.assistant.messages, {
          id,
          dialog_id: current.assistant.dialogId,
          turn_id: mutation.turnId,
          created_at: nowSeconds(),
          role: 'user' as const,
          display: mutation.text,
          speech: '',
          status: 'done' as const,
        }]
      next.assistant = { ...current.assistant, messages, turnRunning: true }
      return next
    }
    case 'assistant-say': {
      const messages = [...current.assistant.messages]
      const index = messages.findIndex(item => item.id === mutation.messageId)
      if (index < 0) {
        messages.push({
          id: mutation.messageId,
          dialog_id: current.assistant.dialogId,
          turn_id: mutation.turnId,
          created_at: nowSeconds(),
          role: 'assistant',
          display: mutation.display,
          speech: mutation.speech ?? mutation.display,
          status: 'streaming',
        })
      } else {
        messages[index] = {
          ...messages[index],
          display: `${messages[index].display} ${mutation.display}`.trim(),
        }
      }
      next.assistant = { ...current.assistant, messages }
      return next
    }
    case 'assistant-action': {
      const actions = [...current.assistant.actions]
      const index = actions.findIndex(item => item.id === mutation.action.id)
      if (index < 0) actions.push(mutation.action)
      else actions[index] = mutation.action
      next.assistant = { ...current.assistant, actions }
      return next
    }
    case 'assistant-resolved': {
      const actions = current.assistant.actions.map(item => (
        item.id === mutation.actionId
          ? { ...item, status: mutation.status as AssistantAction['status'], resolved_at: nowSeconds() }
          : item
      ))
      const messages = current.assistant.messages.some(item => item.action_id === mutation.actionId)
        ? current.assistant.messages
        : [...current.assistant.messages, {
          id: `action:${mutation.actionId}`,
          dialog_id: current.assistant.dialogId,
          turn_id: '',
          created_at: nowSeconds(),
          role: 'action' as const,
          display: mutation.display,
          speech: '',
          status: 'done' as const,
          action_id: mutation.actionId,
        }]
      next.assistant = { ...current.assistant, actions, messages }
      return next
    }
    case 'assistant-done': {
      const messages = current.assistant.messages.map(item => (
        item.id === mutation.messageId ? { ...item, status: 'done' as const } : item
      ))
      next.assistant = { ...current.assistant, messages, turnRunning: false }
      return next
    }
    case 'reset':
      return initialDemoState()
  }
}

/**
 * The cross-frame fan-out, in a browser only.
 *
 * `typeof BroadcastChannel === 'function'` is not enough on its own: Node has one too, so
 * importing this module in the unit suite opened a real channel, and an open channel is a
 * live handle that keeps the event loop alive - the run simply never exited. The `window`
 * check says what was actually meant, which is "there is another frame to mirror to".
 */
const channel: BroadcastChannel | null
  = typeof window !== 'undefined' && typeof BroadcastChannel === 'function'
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

/**
 * A fresh id for something the demo mints.
 *
 * Counter-based under determinism, and the timestamp is what had to go: an id carrying
 * `Date.now()` is unique twice over but reproducible neither time, so a scenario that
 * spawned a pane could not name the pane it had just spawned on the second run. Outside
 * determinism the timestamp stays, because two *frames* mint ids independently and a
 * bare counter would collide across them.
 */
let idCounter = 0
export const demoId = (prefix: string): string => (DETERMINISTIC
  ? `${prefix}-d${nextOrdinal()}`
  : `${prefix}-${Date.now().toString(36)}-${(idCounter += 1)}`)
