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

export type AssistantClientContext = {
  focused_session_id?: string | null
  active_project_id?: string | null
  commands?: { id: string; label: string }[]
}

export const sendTurn = (dialogId: string, text: string, clientContext: AssistantClientContext) =>
  api<{ turn_id: string }>('POST', `/api/assistant/dialogs/${dialogId}/turns`, {
    text,
    client_context: clientContext,
  })

export const interruptTurn = (dialogId: string) =>
  api<{ interrupted: boolean }>('POST', `/api/assistant/dialogs/${dialogId}/interrupt`)

export const confirmAction = (actionId: string) =>
  api<{ result: unknown; action: AssistantAction }>('POST', `/api/assistant/actions/${actionId}/confirm`)

export const cancelAction = (actionId: string) =>
  api<{ action: AssistantAction }>('POST', `/api/assistant/actions/${actionId}/cancel`)

export const reportUiResult = (
  actionId: string,
  outcome: { ok: boolean; detail?: string; candidates?: string[] },
) => api<{ accepted: boolean }>('POST', `/api/assistant/actions/${actionId}/ui-result`, outcome)

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
  if (event.type === 'assistant_turn_started') {
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
    if (state.messages.some(item => item.id === message.id)) return state
    return { ...state, messages: [...state.messages, message], thinking: 'thinking' }
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
