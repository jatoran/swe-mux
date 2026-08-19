import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import {
  applyAssistantEvent, cancelAction, confirmAction, dialogDetail, ensureDialog,
  interruptTurn, openFollowUpWindow, rememberDialogId, sendTurn,
} from './assistant'
import type { AssistantAction, AssistantClientContext, AssistantMessage } from './assistant'
import { playEarcon } from './earcons'

/**
 * The conversation view inside the voice overlay (Phase 10.6).
 *
 * Dialog state is daemon-owned; this component rebuilds its view from one
 * detail fetch and then advances it from `mux:assistant-event` window events,
 * so two devices watching the same dialog render the same turn. Confirmation
 * is typed state rendered as cards, never prose — voice mode drives the same
 * cards through spoken confirm/cancel.
 */
export function AssistantPanel({
  enabled,
  clientContext,
  speak,
  voiceActive,
  pendingSpeech = '',
}: {
  enabled: boolean
  clientContext: () => AssistantClientContext
  /** Speak an assistant reply through the application-speech pipeline; null when read aloud is off. */
  speak: ((text: string) => Promise<void>) | null
  voiceActive: boolean
  /**
   * Live transcription accumulating on this device (the brainstorm hold
   * buffer), shown as a pending user bubble so the operator watches their
   * thinking land in the conversation itself, not only in the talk tab. It is
   * client-local by design — the daemon sees nothing until the release cue
   * turns it into a real turn, which replaces this bubble with the message.
   */
  pendingSpeech?: string
}) {
  const [dialogId, setDialogId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [actions, setActions] = useState<AssistantAction[]>([])
  const [thinking, setThinking] = useState<string | null>(null)
  const [turnRunning, setTurnRunning] = useState(false)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement | null>(null)
  const speakRef = useRef(speak); speakRef.current = speak
  const voiceActiveRef = useRef(voiceActive); voiceActiveRef.current = voiceActive
  const dialogRef = useRef<string | null>(null); dialogRef.current = dialogId

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    void (async () => {
      try {
        const id = await ensureDialog()
        if (cancelled) return
        const detail = await dialogDetail(id)
        if (cancelled) return
        setDialogId(id)
        setMessages(detail.messages.filter(item => item.role === 'user' || item.role === 'assistant'))
        setActions(detail.actions)
        setTurnRunning(detail.turn_running)
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause))
      }
    })()
    return () => { cancelled = true }
  }, [enabled])

  // Live events keep every device's view of the daemon-owned dialog current.
  useEffect(() => {
    const handler = (raw: Event) => {
      const event = (raw as CustomEvent).detail as {
        type: string; payload: Record<string, unknown>; replay?: boolean
      }
      const payload = event.payload || {}
      if (String(payload.dialog_id || '') !== dialogRef.current) return
      if (event.replay) return
      setMessages(current => {
        const next = applyAssistantEvent({ messages: current, actions: [], thinking: null }, event)
        return next.messages
      })
      setActions(current => applyAssistantEvent({ messages: [], actions: current, thinking: null }, event).actions)
      setThinking(current => applyAssistantEvent({ messages: [], actions: [], thinking: current }, event).thinking)
      if (event.type === 'assistant_turn_started') setTurnRunning(true)
      if (event.type === 'assistant_turn_done' || event.type === 'assistant_turn_failed') {
        setTurnRunning(false)
        if (event.type === 'assistant_turn_done') {
          playEarcon('done')
          const speech = String(payload.speech || '')
          if (speech && voiceActiveRef.current && speakRef.current) {
            openFollowUpWindow()
            void speakRef.current(speech).catch(() => {})
          }
        } else playEarcon('error')
      }
      if (event.type === 'assistant_action') {
        const status = String(payload.status || '')
        if (status === 'scheduled' || status === 'pending') {
          playEarcon('tick')
          // Eyes-free confirmation: the card's restatement is spoken so the
          // operator can say "confirm" or "cancel" without looking. The spoken
          // verdict resolves deterministically (assistant.ts), never via the model.
          const restatement = String(payload.restatement || '')
          if (restatement && voiceActiveRef.current && speakRef.current) {
            const line = status === 'scheduled'
              ? `About to ${restatement}. Say cancel to stop it.`
              : `The assistant wants to ${restatement}. Say confirm or cancel.`
            void speakRef.current(line).catch(() => {})
          }
        }
      }
    }
    window.addEventListener('mux:assistant-event', handler)
    return () => window.removeEventListener('mux:assistant-event', handler)
  }, [])

  useLayoutEffect(() => {
    const element = logRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages, actions, thinking, pendingSpeech])

  const submit = async () => {
    const text = input.trim()
    if (!text || !dialogId || turnRunning) return
    setInput('')
    setError(null)
    try {
      await sendTurn(dialogId, text, clientContext())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  const newDialog = async () => {
    rememberDialogId(null)
    setMessages([]); setActions([]); setThinking(null); setError(null)
    try {
      const id = await ensureDialog()
      setDialogId(id)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  if (!enabled) {
    return <div class="assistant-panel disabled">
      <p>The assistant is off. Enable it in Settings → Assistant to converse with the fleet.</p>
    </div>
  }
  const openActions = actions.filter(item => item.status === 'pending' || item.status === 'scheduled')
  return <div class="assistant-panel">
    <div ref={logRef} class="assistant-log" role="log" aria-live="polite">
      {messages.length === 0 && !thinking && <p class="assistant-empty">
        Ask about the fleet, queue or reword messages, spawn sessions, or navigate — in plain language.
      </p>}
      {messages.map(message => <article key={message.id} class={`assistant-message ${message.role}${message.status === 'failed' ? ' failed' : ''}`}>
        <header>{message.role === 'user' ? 'you' : 'mux'}</header>
        <p>{message.status === 'failed' ? (message.error || 'The turn failed.') : message.display}</p>
      </article>)}
      {pendingSpeech.trim() && <article class="assistant-message user assistant-pending-speech">
        <header>you · holding — say “go ahead” to send</header>
        <p>{pendingSpeech}</p>
      </article>}
      {thinking && <p class="assistant-thinking">{thinking}</p>}
      {openActions.map(action => <AssistantActionCard key={action.id} action={action} />)}
    </div>
    {error && <p class="assistant-error" role="alert">{error}</p>}
    <footer class="assistant-input-row">
      <textarea
        class="assistant-input"
        value={input}
        rows={1}
        placeholder="Message the assistant… (Enter to send)"
        onInput={event => setInput(event.currentTarget.value)}
        onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit() }
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); event.currentTarget.blur() }
        }}
      />
      {turnRunning
        ? <button class="assistant-stop" title="Interrupt the running turn" onClick={() => { if (dialogId) void interruptTurn(dialogId) }}>stop</button>
        : <button class="assistant-send" disabled={!input.trim()} onClick={() => void submit()}>send</button>}
      <button class="assistant-new" title="Start a fresh conversation" onClick={() => void newDialog()}>new</button>
    </footer>
  </div>
}

/** One pending/scheduled action: the typed confirmation state as a card. */
function AssistantActionCard({ action }: { action: AssistantAction }) {
  const [remaining, setRemaining] = useState<number | null>(null)
  useEffect(() => {
    if (action.status !== 'scheduled' || !action.expires_at) { setRemaining(null); return }
    const update = () => setRemaining(Math.max(0, Math.round((action.expires_at! * 1000 - Date.now()) / 1000)))
    update()
    const timer = setInterval(update, 500)
    return () => clearInterval(timer)
  }, [action.status, action.expires_at])
  const scheduled = action.status === 'scheduled'
  return <aside class={`assistant-action ${action.action_class}`}>
    <p>
      <strong>{scheduled ? 'about to' : 'wants to'}</strong> {action.restatement}
      {scheduled && remaining !== null && <span class="assistant-countdown"> · {remaining}s</span>}
    </p>
    <div>
      {!scheduled && <button class="confirm" onClick={() => void confirmAction(action.id).catch(() => {})}>confirm</button>}
      {scheduled && <button class="confirm" onClick={() => void confirmAction(action.id).catch(() => {})}>run now</button>}
      <button class="cancel" onClick={() => void cancelAction(action.id).catch(() => {})}>cancel</button>
    </div>
  </aside>
}
