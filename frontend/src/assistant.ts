/**
 * Client half of the Mux assistant (Phase 10.6).
 *
 * Dialog state is daemon-owned: this module holds only the device's view of it,
 * rebuilt from `GET /api/assistant/dialogs/{id}` and advanced by
 * `assistant_*` events relayed from the /events socket as `mux:assistant-event`
 * window events. The current dialog id persists per device so reopening the
 * panel resumes the same conversation any other device sees.
 */
import { api } from './api.ts'

export type AssistantActionClass = 'read' | 'navigation' | 'reversible' | 'consequential'

export type AssistantAction = {
  id: string
  dialog_id: string
  turn_id: string
  created_at: number
  kind: string
  action_class: AssistantActionClass
  restatement: string
  arguments: Record<string, unknown>
  status: 'pending' | 'scheduled' | 'dispatched' | 'executing' | 'executed' | 'failed' | 'cancelled' | 'expired'
  expires_at?: number | null
  resolved_at?: number | null
  result?: string | null
}

export type AssistantMessage = {
  id: string
  dialog_id: string
  turn_id: string
  created_at: number
  role: 'user' | 'assistant'
  display: string
  speech: string
  status: 'done' | 'failed' | 'streaming'
  error?: string | null
}

export type AssistantStatus = {
  enabled: boolean
  model: string
  daily_budget_usd: number
  spend_today: { tokens: number; cost_usd: number }
  trust_reversible: 'auto' | 'cancel_window' | 'confirm'
  diagnostic?: string | null
}

export type AssistantDialogDetail = {
  dialog: { id: string; created_at: number; updated_at: number; title: string }
  messages: AssistantMessage[]
  actions: AssistantAction[]
  turn_running: boolean
}

const DIALOG_KEY = 'mux.assistant.dialog'

export function storedDialogId(): string | null {
  try { return localStorage.getItem(DIALOG_KEY) } catch { return null }
}

export function rememberDialogId(id: string | null): void {
  try {
    if (id) localStorage.setItem(DIALOG_KEY, id)
    else localStorage.removeItem(DIALOG_KEY)
  } catch { /* private mode */ }
}

export const assistantStatus = () => api<AssistantStatus>('GET', '/api/assistant')

export async function ensureDialog(): Promise<string> {
  const existing = storedDialogId()
  if (existing) {
    try {
      await api<AssistantDialogDetail>('GET', `/api/assistant/dialogs/${existing}`)
      return existing
    } catch { rememberDialogId(null) }
  }
  const dialog = await api<{ id: string }>('POST', '/api/assistant/dialogs', {})
  rememberDialogId(dialog.id)
  return dialog.id
}

export const dialogDetail = (dialogId: string) =>
  api<AssistantDialogDetail>('GET', `/api/assistant/dialogs/${dialogId}`)

/**
 * A device's current dialog has been replaced by a fresh one.
 *
 * Announced rather than returned because two surfaces start a new conversation
 * - the panel's own `new` button and the voice registry alias - and only the
 * panel holds the view being cleared. One event keeps the two from growing two
 * different notions of "the current dialog".
 */
export const ASSISTANT_DIALOG_RESET_EVENT = 'mux:assistant-dialog-reset'

/**
 * Deterministic spoken aliases for starting a fresh conversation. Declared
 * beside the reply so the registry entry and its tests cannot drift apart.
 * Nothing here collides with the spawn aliases (`new claude`, `new codex`),
 * which are `new <harness>` and never `new conversation`.
 */
export const NEW_CONVERSATION_PHRASES: string[] = [
  'new conversation',
  'start a new conversation',
  'new assistant conversation',
  'new chat',
  'start a new chat',
  'clear context',
  'clear the context',
  'clear our context',
  'clear the conversation',
]

/**
 * The spoken reply, which has to say *both* halves.
 *
 * Clearing context runs on the word with no confirmation card, and that is only
 * safe because nothing is destroyed: the daemon keeps the dialog and the panel
 * keeps it readable. A reply that said "context cleared" alone would describe
 * the same act as a deletion the operator cannot see or undo, which is the
 * failure the second half exists to prevent.
 */
export const NEW_CONVERSATION_REPLY =
  'Started a new conversation. The context is cleared, and the previous conversation is still there in the panel.'

/**
 * Forget this device's dialog and open a fresh one, then announce the swap.
 *
 * Reversible by construction: the prior dialog is neither deleted nor closed,
 * only unremembered, so it stays in the daemon's dialog list and in the panel's
 * previous-conversation disclosure.
 */
export async function startNewDialog(): Promise<string> {
  rememberDialogId(null)
  const id = await ensureDialog()
  // Guarded rather than assumed: this module is imported by tests with no DOM,
  // where there is no panel view to repaint anyway.
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(ASSISTANT_DIALOG_RESET_EVENT, { detail: { dialog_id: id } }))
  }
  return id
}

/**
 * Per-tab identity for client-executed assistant actions. The daemon stamps
 * dispatched actions with the id of the tab whose turn proposed them, and only
 * that tab executes — an untargeted broadcast would type into every mounted
 * copy of a pane and spawn one session per open workspace.
 */
export const ASSISTANT_CLIENT_ID: string =
  globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`

export type AssistantClientContext = {
  focused_session_id?: string | null
  active_project_id?: string | null
  client_id?: string
  commands?: { id: string; label: string }[]
}

export const sendTurn = (dialogId: string, text: string, clientContext: AssistantClientContext) =>
  api<{ turn_id: string; queued?: boolean }>(
    'POST', `/api/assistant/dialogs/${dialogId}/turns`,
    { text, client_context: clientContext },
  )

export const interruptTurn = (dialogId: string) =>
  api<{ interrupted: boolean }>('POST', `/api/assistant/dialogs/${dialogId}/interrupt`)

export const confirmAction = (actionId: string) =>
  api<{ result: unknown; action: AssistantAction }>('POST', `/api/assistant/actions/${actionId}/confirm`)

export const cancelAction = (actionId: string) =>
  api<{ action: AssistantAction }>('POST', `/api/assistant/actions/${actionId}/cancel`)

/**
 * Tell the daemon this device has begun reading a scheduled card aloud, which
 * restarts its cancel window. Without it the window is spent synthesizing the
 * sentence that announces the window, and the action runs before the operator
 * has heard there was anything to stop.
 */
export const announceAction = (actionId: string) =>
  api<{ extended: boolean; action: AssistantAction }>(
    'POST', `/api/assistant/actions/${actionId}/announced`,
  )

export const reportUiResult = (
  actionId: string,
  outcome: { ok: boolean; detail?: string; candidates?: string[] },
) => api<{ accepted: boolean }>('POST', `/api/assistant/actions/${actionId}/ui-result`, outcome)

/**
 * Open-action tracker: the newest pending/scheduled confirmation card, kept at
 * module level so the deterministic spoken confirm/cancel path can act on it
 * without the model in the loop. Fed by the same `assistant_action` events the
 * panel renders; replayed events never revive an old card.
 */
const openActions = new Map<string, AssistantAction>()

export function noteAssistantActionEvent(action: Partial<AssistantAction>, replay: boolean): void {
  const id = String(action.id || '')
  if (!id || replay) return
  if (action.status === 'pending' || action.status === 'scheduled') {
    openActions.set(id, action as AssistantAction)
  } else {
    openActions.delete(id)
  }
}

export function latestOpenAction(): AssistantAction | null {
  const now = Date.now() / 1000
  let newest: AssistantAction | null = null
  for (const [id, action] of [...openActions]) {
    // An expired card is not merely skipped but forgotten: the endpointing and
    // patience rules read this as "a question is open", and a stale entry would
    // keep the microphone in answer mode indefinitely.
    if (action.expires_at && action.expires_at < now) { openActions.delete(id); continue }
    if (!newest || action.created_at > newest.created_at) newest = action
  }
  return newest
}

const CONFIRM_WORDS = new Set([
  'confirm', 'confirmed', 'yes', 'yep', 'yeah', 'yup', 'sure', 'ok', 'okay', 'correct',
  'right', 'do it', 'go ahead', 'go for it', 'approve', 'approved', 'run it', 'send it',
  'that s right', 'sounds good', 'please do', 'affirmative',
])
const CANCEL_WORDS = new Set([
  'cancel', 'no', 'nope', 'nah', 'never mind', 'nevermind', 'stop', 'stop that', 'dont',
  'don t', 'abort', 'forget it', 'scratch that', 'discard', 'negative',
])
// Filler the recognizer keeps and a human never means: stripped from both ends
// before the closed sets are consulted, so "yeah, confirm that please" is the
// same verdict as "confirm".
const VERDICT_FILLER = new Set([
  'um', 'uh', 'er', 'well', 'so', 'and', 'but', 'mux', 'mucks', 'max', 'please', 'thanks',
  'thank', 'you', 'it', 'that', 'this', 'one', 'now', 'then', 'just', 'i', 'd', 'say',
])

/**
 * Deterministic spoken verdict on the open confirmation card. Confirmation is a
 * human act: it must never route through the model, which could be asked to
 * "confirm" by its own reply text — so this stays a closed vocabulary rather
 * than an intent classifier.
 *
 * It is forgiving about *shape* and strict about *meaning*. An utterance the
 * closed set does not recognize falls through to the model as an ordinary turn,
 * where it reads as a fresh request and the same action is proposed a second
 * time; every phrasing this misses is therefore a duplicate card, which is how
 * "I confirmed and it asked me again" happened. Filler and politeness are
 * stripped from the ends, and a leading affirmative followed by a verdict word
 * ("yes, cancel that") resolves to the verdict, never to the affirmative.
 */
export function spokenConfirmation(text: string): 'confirm' | 'cancel' | null {
  const normalized = text.toLowerCase().replace(/[^a-z ]+/g, ' ').replace(/\s+/g, ' ').trim()
  if (!normalized) return null
  const words = normalized.split(' ')
  let start = 0
  let end = words.length
  // Trim one filler word at a time and test after every trim, rather than
  // stripping all of it first: "do it now" is a verdict phrase whose second word
  // is also filler, and trimming greedily would eat the phrase before matching it.
  while (start < end) {
    const phrase = words.slice(start, end).join(' ')
    if (CANCEL_WORDS.has(phrase)) return 'cancel'
    if (CONFIRM_WORDS.has(phrase)) return 'confirm'
    // Leading filler first. Several verdict phrases end in a word that is also
    // filler ("do it", "run it"), so trimming from the right first would eat the
    // phrase before the address on the left ("mux, do it now") ever came off.
    if (VERDICT_FILLER.has(words[start])) { start += 1; continue }
    if (VERDICT_FILLER.has(words[end - 1])) { end -= 1; continue }
    break
  }
  // Last resort, and only for something short enough to be an answer rather
  // than a sentence. A cancel word anywhere in it wins over an affirmative:
  // "yes, cancel that" is a cancellation, and reading it as a confirmation
  // performs the very action the operator was stopping.
  const remaining = words.slice(start, end)
  if (remaining.length && remaining.length <= 3) {
    if (remaining.some(word => CANCEL_WORDS.has(word))) return 'cancel'
    if (remaining.some(word => CONFIRM_WORDS.has(word))) return 'confirm'
  }
  return null
}

/**
 * The follow-up window: for a short period after the assistant finishes a
 * spoken turn, the next utterance needs no wake word — a single addressee
 * removes the ambiguity the marker exists to resolve. Device-local because it
 * gates only this device's microphone routing.
 */
const FOLLOW_UP_MS = 8_000
let followUpUntil = 0

export function openFollowUpWindow(): void { followUpUntil = Date.now() + FOLLOW_UP_MS }
export function closeFollowUpWindow(): void { followUpUntil = 0 }
export function assistantFollowUpActive(): boolean { return Date.now() < followUpUntil }

/** Reduce raw assistant events into a dialog view. Pure, for tests. */
export function applyAssistantEvent(
  state: { messages: AssistantMessage[]; actions: AssistantAction[]; thinking: string | null },
  event: { type: string; payload: Record<string, unknown> },
): { messages: AssistantMessage[]; actions: AssistantAction[]; thinking: string | null } {
  const payload = event.payload || {}
  // Queued and started share one message id per turn, deliberately. What the
  // operator said is rendered the moment it is accepted — even though it will
  // not run until the turn ahead of it finishes — and the later start event
  // updates that same bubble rather than adding a second copy of their words.
  if (event.type === 'assistant_turn_started' || event.type === 'assistant_turn_queued') {
    const queued = event.type === 'assistant_turn_queued'
    const message: AssistantMessage = {
      id: `user:${String(payload.turn_id)}`,
      dialog_id: String(payload.dialog_id || ''),
      turn_id: String(payload.turn_id || ''),
      created_at: Date.now() / 1000,
      role: 'user',
      display: String(payload.text || ''),
      speech: '',
      status: 'done',
    }
    const messages = [...state.messages]
    const index = messages.findIndex(item => item.id === message.id)
    if (index < 0) messages.push(message)
    else if (queued) messages[index] = { ...messages[index], display: message.display }
    else if (!messages[index].display) messages[index] = message
    return {
      ...state,
      messages,
      thinking: queued ? 'queued — waiting for the current turn' : 'thinking',
    }
  }
  if (event.type === 'assistant_sentence') {
    const id = String(payload.message_id || '')
    const sentence = String(payload.display || '')
    const messages = [...state.messages]
    const index = messages.findIndex(item => item.id === id)
    if (index < 0) {
      messages.push({
        id,
        dialog_id: String(payload.dialog_id || ''),
        turn_id: String(payload.turn_id || ''),
        created_at: Date.now() / 1000,
        role: 'assistant',
        display: sentence,
        speech: String(payload.speech || ''),
        status: 'streaming',
      })
    } else {
      const current = messages[index]
      messages[index] = { ...current, display: `${current.display} ${sentence}`.trim() }
    }
    return { ...state, messages, thinking: null }
  }
  if (event.type === 'assistant_tool_status') {
    const tool = String(payload.tool || '')
    const status = String(payload.status || '')
    return { ...state, thinking: status === 'running' ? `running ${tool}…` : state.thinking }
  }
  if (event.type === 'assistant_action') {
    const action = payload as unknown as AssistantAction
    if (!action.id) return state
    const actions = [...state.actions]
    const index = actions.findIndex(item => item.id === action.id)
    if (index < 0) actions.push(action)
    else actions[index] = action
    return { ...state, actions }
  }
  if (event.type === 'assistant_turn_done') {
    const id = String(payload.message_id || '')
    const display = String(payload.display || '')
    const messages = [...state.messages]
    const index = messages.findIndex(item => item.id === id)
    const final: AssistantMessage = {
      id,
      dialog_id: String(payload.dialog_id || ''),
      turn_id: String(payload.turn_id || ''),
      created_at: Date.now() / 1000,
      role: 'assistant',
      display,
      speech: String(payload.speech || ''),
      status: 'done',
    }
    if (index < 0) messages.push(final)
    else messages[index] = final
    return { ...state, messages, thinking: null }
  }
  if (event.type === 'assistant_turn_failed') {
    const message: AssistantMessage = {
      id: `failed:${String(payload.turn_id)}`,
      dialog_id: String(payload.dialog_id || ''),
      turn_id: String(payload.turn_id || ''),
      created_at: Date.now() / 1000,
      role: 'assistant',
      display: '',
      speech: '',
      status: 'failed',
      error: String(payload.error || 'the turn failed'),
    }
    if (state.messages.some(item => item.id === message.id)) return state
    return { ...state, messages: [...state.messages, message], thinking: null }
  }
  return state
}
