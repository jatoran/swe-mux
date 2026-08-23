import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import {
  announceAction, applyAssistantEvent, ASSISTANT_DIALOG_RESET_EVENT, cancelAction, confirmAction,
  dialogDetail, ensureDialog, interruptTurn, openFollowUpWindow, sendTurn, startNewDialog,
} from './assistant'
import type { AssistantAction, AssistantClientContext, AssistantMessage } from './assistant'
import {
  beginTurnSpeech, cancelTurnSpeech, endTurnSpeech, speakAnnouncement, speakTurnText,
} from './assistantSpeech'
import { playEarcon } from './earcons'
import type { VoiceBodyVariant } from './voiceDock'

/**
 * The conversation view inside the voice dock (Phase 10.6).
 *
 * Dialog state is daemon-owned; this component rebuilds its view from one
 * detail fetch and then advances it from `mux:assistant-event` window events,
 * so two devices watching the same dialog render the same turn. Confirmation
 * is typed state rendered as cards, never prose — voice mode drives the same
 * cards through spoken confirm/cancel.
 *
 * It is mounted exactly once, at one fixed place in the tree, for the life of
 * the app: `variant` changes what it draws, never whether it exists. That is a
 * correctness rule, not a nicety — `announcedRef` below is the client half of
 * the once-per-card announcement cut, and it lives in this component's memory,
 * so a remount is indistinguishable from a device that has never seen the card
 * and would speak an open card's line again. Collapsing the dock, switching the
 * microphone's addressee, and moving between breakpoints must all leave this
 * component mounted.
 *
 * Starting a fresh conversation is likewise not a local action: both the `new`
 * button and the voice alias call `startNewDialog`, and the panel reacts to the
 * `mux:assistant-dialog-reset` it announces. The cleared conversation is stashed
 * behind a disclosure rather than dropped, which is what lets clearing context
 * run with no confirmation.
 */
export function AssistantPanel({
  enabled,
  clientContext,
  speechEnabled,
  voiceActive,
  pendingSpeech = '',
  pendingSpeechNote = 'holding — say “go ahead” to send',
  variant = 'full',
  onOpenActions,
  onReply,
}: {
  enabled: boolean
  clientContext: () => AssistantClientContext
  /** Read aloud is on, so this turn's sentences may be spoken as they stream. */
  speechEnabled: boolean
  voiceActive: boolean
  /**
   * Live transcription accumulating on this device, shown as a pending user
   * bubble so the operator watches their thinking land in the conversation
   * itself, not only in the talk tab. It is client-local by design — the daemon
   * sees nothing until the turn is really sent, which replaces this bubble with
   * the message.
   *
   * Three states feed it, composed by `pendingUtterance` in
   * `utteranceDeferral.ts`: the brainstorm hold buffer, a fragment the deferral
   * pen is holding, and the speculative decode's provisional reading of the
   * breath in progress. The last of those is why this row matters for latency:
   * the accurate transcript cannot exist until the endpoint has proved the turn
   * is over, seconds later, and without this the surface looks deaf until then.
   */
  pendingSpeech?: string
  /** Header for that row. Which of the three states it is naming matters: a
   * heuristic hold expires into a turn on its own, a park never does, and a
   * provisional reading is not held at all - it is simply not finished. */
  pendingSpeechNote?: string
  /** Full conversation, the dock's one-row peek, or mounted and drawing nothing. */
  variant?: VoiceBodyVariant
  /**
   * How many confirmation cards are open. The dock raises itself off the chip on
   * the first one, because a countdown nobody can see is a decision made by
   * timeout rather than by a human.
   */
  onOpenActions?: (count: number) => void
  /** A turn finished; the chip marks it unseen while the dock is collapsed. */
  onReply?: () => void
}) {
  const [dialogId, setDialogId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  /**
   * The conversation the last "new conversation" cleared, kept behind a
   * disclosure. This is what makes clearing context reversible enough to run
   * with no confirmation, by voice as well as by the button, so it is part of
   * the feature rather than a convenience.
   */
  const [previousMessages, setPreviousMessages] = useState<AssistantMessage[]>([])
  const [previousOpen, setPreviousOpen] = useState(false)
  const [actions, setActions] = useState<AssistantAction[]>([])
  const [thinking, setThinking] = useState<string | null>(null)
  const [turnRunning, setTurnRunning] = useState(false)
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement | null>(null)
  // Read from the once-mounted event handler, so they must be refs rather than
  // closed-over props.
  const speechRef = useRef(speechEnabled); speechRef.current = speechEnabled
  const voiceActiveRef = useRef(voiceActive); voiceActiveRef.current = voiceActive
  const dialogRef = useRef<string | null>(null); dialogRef.current = dialogId
  const onReplyRef = useRef(onReply); onReplyRef.current = onReply
  const messagesRef = useRef<AssistantMessage[]>([]); messagesRef.current = messages
  /** True while this turn's speech may be spoken: decided once, at turn start. */
  const speakingTurnRef = useRef<string | null>(null)
  /** Cards already announced on this device; an announcement is per card, not per event. */
  const announcedRef = useRef<Set<string>>(new Set())

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
        setMessages(detail.messages.filter(
          item => item.role === 'user' || item.role === 'assistant' || item.role === 'action',
        ))
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
      const turnId = String(payload.turn_id || '')
      // A queued turn is not running yet, so it must not claim the composer's
      // running state or start a speech stream; the start event does both when
      // it actually begins.
      if (event.type === 'assistant_turn_queued') playEarcon('tick')
      if (event.type === 'assistant_turn_started') {
        setTurnRunning(true)
        // Whether this turn speaks is decided once, here. Deciding per sentence
        // would let a mid-turn toggle produce half a spoken reply.
        if (turnId && voiceActiveRef.current && speechRef.current) {
          speakingTurnRef.current = turnId
          beginTurnSpeech(turnId)
        } else speakingTurnRef.current = null
      }
      // Sentences are spoken as they arrive rather than at the end of the turn:
      // the model writes for seconds and synthesis takes seconds more, and
      // serialising the two is most of the wait before the assistant says
      // anything. `speech` is empty when the daemon suppressed it — the turn
      // opened a confirmation card, and the card's own line is what gets spoken.
      if (event.type === 'assistant_sentence' && speakingTurnRef.current === turnId) {
        const speech = String(payload.speech || '')
        if (speech) void speakTurnText(turnId, speech).catch(() => {})
      }
      if (event.type === 'assistant_turn_done' || event.type === 'assistant_turn_failed') {
        setTurnRunning(false)
        if (event.type === 'assistant_turn_done') {
          // A held turn made no sound and said nothing, so neither the done
          // earcon nor the reply hook belongs to it - both would announce an
          // answer that deliberately does not exist. The follow-up window still
          // opens: the operator is mid-thought and their next breath has to reach
          // the assistant to join the fragment it is holding.
          if (payload.held === true) {
            openFollowUpWindow()
            if (speakingTurnRef.current === turnId) {
              void endTurnSpeech(turnId, '').catch(() => {})
              speakingTurnRef.current = null
            }
            return
          }
          playEarcon('done')
          onReplyRef.current?.()
          if (speakingTurnRef.current === turnId) {
            openFollowUpWindow()
            // `speech` here is only a fallback for a turn that produced no
            // sentence at all; everything else was already spoken above.
            void endTurnSpeech(turnId, String(payload.speech || '')).catch(() => {})
            speakingTurnRef.current = null
          }
        } else {
          playEarcon('error')
          if (speakingTurnRef.current === turnId) {
            cancelTurnSpeech(turnId)
            speakingTurnRef.current = null
          }
        }
      }
      // A notice belongs to no turn, so it is spoken on the announcement path:
      // it joins whatever stream is live rather than taking the floor, which is
      // the same rule a confirmation card follows. A Project Action's outcome
      // arriving mid-sentence must not cut the sentence off.
      if (event.type === 'assistant_notice') {
        const notice = String(payload.speech || '')
        playEarcon('tick')
        if (notice && voiceActiveRef.current && speechRef.current) {
          void speakAnnouncement(notice).catch(() => {})
        }
      }
      if (event.type === 'assistant_action') {
        const status = String(payload.status || '')
        const actionId = String(payload.id || '')
        // A card is announced at most once, ever, per device. The event is not
        // the unit — the *card* is: a scheduled card is re-emitted whenever its
        // countdown moves, and announcing per event turned that into a loop
        // that spoke the same sentence eighty times and kept speaking after the
        // microphone was closed. Nothing may reintroduce a per-event
        // announcement here, whatever else re-emits the row.
        if ((status === 'scheduled' || status === 'pending')
          && actionId && !announcedRef.current.has(actionId)) {
          announcedRef.current.add(actionId)
          playEarcon('tick')
          // Eyes-free confirmation: the card's line is spoken so the operator
          // can say "confirm" or "cancel" without looking. The wording comes
          // from the daemon because it encodes the trust policy — a scheduled
          // card runs on its own and can only be stopped. The spoken verdict
          // then resolves deterministically (assistant.ts), never via the model.
          const announcement = String(payload.announcement || '')
          if (announcement && voiceActiveRef.current && speechRef.current) {
            void speakAnnouncement(announcement).catch(() => {})
            // Restart the cancel window from now rather than spending it on
            // synthesizing the sentence that announces it. Fired on arrival,
            // not on playback: the daemon accepts one extension per card, so
            // there is no second chance to spend on a better moment.
            if (status === 'scheduled') void announceAction(actionId).catch(() => {})
          }
        }
      }
    }
    window.addEventListener('mux:assistant-event', handler)
    return () => window.removeEventListener('mux:assistant-event', handler)
  }, [])

  // One reset path for both surfaces. The `new` button and the voice alias both
  // call `startNewDialog`, which announces here; the panel never clears itself
  // directly, so a conversation started by voice and one started by the button
  // leave the panel in exactly the same state.
  useEffect(() => {
    const handler = (raw: Event) => {
      const id = String(((raw as CustomEvent).detail as { dialog_id?: string } || {}).dialog_id || '')
      if (!id) return
      // Kept, not deleted. The cleared conversation stays readable right here,
      // which is the whole reason clearing needs no confirmation. An empty one
      // must not displace a real previous conversation.
      if (messagesRef.current.length) { setPreviousMessages(messagesRef.current); setPreviousOpen(false) }
      setMessages([]); setActions([]); setThinking(null); setError(null)
      setDialogId(id)
    }
    window.addEventListener(ASSISTANT_DIALOG_RESET_EVENT, handler)
    return () => window.removeEventListener(ASSISTANT_DIALOG_RESET_EVENT, handler)
  }, [])

  useLayoutEffect(() => {
    const element = logRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages, actions, thinking, pendingSpeech, pendingSpeechNote, variant])

  const openActions = actions.filter(item => item.status === 'pending' || item.status === 'scheduled')
  const openActionCount = openActions.length
  // Reported rather than rendered upward: the dock owns whether it is on screen, and this
  // is the one signal that may raise it. Keyed on the count so a re-render with the same
  // cards does not re-fire.
  const reportActionsRef = useRef(onOpenActions); reportActionsRef.current = onOpenActions
  useEffect(() => { reportActionsRef.current?.(openActionCount) }, [openActionCount])

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
    try {
      await startNewDialog()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  // `hidden` is still a render, never an unmount: the event listener, the dialog state,
  // the speech streams, and the announced-card set all live here and must survive the
  // dock being collapsed to the top bar or switched to the dictation body.
  if (variant === 'hidden') return <div class="assistant-panel hidden-variant" hidden />
  if (!enabled) {
    return variant === 'peek'
      ? <div class="assistant-panel peek disabled"><p>The assistant is off.</p></div>
      : <div class="assistant-panel disabled">
        <p>The assistant is off. Enable it in Settings → Assistant to converse with the fleet.</p>
      </div>
  }
  if (variant === 'peek') {
    // One row: what was last said, what is being thought, and every open card in full.
    // The cards are the reason peek exists — they are the only part of a conversation
    // that expires, so they keep their buttons and their countdown here rather than
    // being summarised into a badge the operator would have to expand to act on.
    const latest = messages[messages.length - 1]
    return <div class="assistant-panel peek">
      {error && <p class="assistant-error" role="alert">{error}</p>}
      {thinking
        ? <p class="assistant-thinking">{thinking}</p>
        : latest
          ? <p class="assistant-peek-line" title={latest.status === 'failed' ? (latest.error || 'The turn failed.') : latest.display}>
            <b>{latest.role === 'user' ? 'you' : latest.role === 'action' ? 'done' : 'mux'}</b>
            <span>{latest.status === 'failed' ? (latest.error || 'The turn failed.') : latest.display}</span>
          </p>
          : <p class="assistant-peek-line empty"><span>No conversation yet — expand to start one.</span></p>}
      {openActions.map(action => <AssistantActionCard key={action.id} action={action} />)}
    </div>
  }
  return <div class="assistant-panel">
    <div ref={logRef} class="assistant-log" role="log" aria-live="polite">
      {previousMessages.length > 0 && <details
        class="assistant-previous"
        open={previousOpen}
        onToggle={event => setPreviousOpen(event.currentTarget.open)}
      >
        <summary>
          previous conversation · {previousMessages.length} message{previousMessages.length === 1 ? '' : 's'} · kept, not deleted
        </summary>
        {previousMessages.map(message => message.role === 'action'
          ? <p key={`previous-${message.id}`} class={`assistant-record ${message.status}`}>{message.display}</p>
          : <article key={`previous-${message.id}`} class={`assistant-message ${message.role}`}>
            <header>{message.role === 'user' ? 'you' : 'mux'}</header>
            <p>{message.status === 'failed' ? (message.error || 'The turn failed.') : message.display}</p>
          </article>)}
      </details>}
      {messages.length === 0 && !thinking && <p class="assistant-empty">
        Ask about the fleet, queue or reword messages, spawn sessions, or navigate — in plain language.
      </p>}
      {messages.map(message => message.role === 'action'
        // A resolved card, in the position it resolved in. Its own row rather
        // than a bubble, because it is neither party speaking: it is the record
        // of what the operator's yes or no actually did, which the panel used to
        // drop the instant the card stopped being open.
        ? <p key={message.id} class={`assistant-record ${message.status}`}>{message.display}</p>
        : <article key={message.id} class={`assistant-message ${message.role}${message.status === 'failed' ? ' failed' : ''}`}>
          <header>{message.role === 'user' ? 'you' : 'mux'}</header>
          <p>{message.status === 'failed' ? (message.error || 'The turn failed.') : message.display}</p>
        </article>)}
      {pendingSpeech.trim() && <article class="assistant-message user assistant-pending-speech">
        <header>you · {pendingSpeechNote}</header>
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
      <button class="assistant-new" title="Start a fresh conversation. The current one is kept, not deleted." onClick={() => void newDialog()}>new</button>
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
