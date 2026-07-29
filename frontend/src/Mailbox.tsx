import { useEffect, useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
import {
  cancelQueueMessage, fetchMailbox, reportUnsafeDelivery, scheduleStatus, senderLabel,
  setAutoPaused, fetchAutoStatus, type QueueAutoStatus, type QueueMessage,
} from './queueApi'

// Phase 5: the mailbox. One view over the *same* message store the Queue tabs
// read — inbox is what other actors addressed to this install's sessions,
// outbox is what you authored — plus the two controls that must be reachable
// in one gesture from any device: the emergency pause and the "that delivery
// was unsafe" report that resets the proving period.
//
// Deliberately not a second transcript: it shows delivery state and
// provenance, never a conversation.

type Role = 'inbox' | 'outbox'

const STATE_NOTE: Record<string, string> = {
  draft: 'waiting for you to arm it',
  armed: 'armed — waits for order and readiness',
  blocked: 'refused; reasons below',
  delivering: 'delivering…',
  sent: 'delivered',
  failed: 'failed — verify the terminal',
  cancelled: 'cancelled',
  stranded: 'target ended or was replaced',
}

export function Mailbox(
  { onClose, onOpenQueue }:
  { onClose: () => void; onOpenQueue?: (sessionId: string) => void },
) {
  const [role, setRole] = useState<Role>('inbox')
  const [messages, setMessages] = useState<QueueMessage[]>([])
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const panel = useRef<HTMLElement>(null)
  useModalFocus(panel, onClose)

  const load = async (next: Role = role) => {
    try {
      const [page, status] = await Promise.all([fetchMailbox(next), fetchAutoStatus()])
      setMessages(page.messages)
      setAuto(status)
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err))
    }
  }
  useEffect(() => {
    void load(role)
    const onChanged = () => void load(role)
    window.addEventListener('mux:queue-changed', onChanged)
    return () => window.removeEventListener('mux:queue-changed', onChanged)
  }, [role])

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await action()
      await load(role)
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const revoke = (message: QueueMessage) =>
    void act(() => cancelQueueMessage(message.id, 'revoked'))

  const reportUnsafe = () =>
    void act(async () => {
      await reportUnsafeDelivery('reported from the mailbox')
      setNote('Recorded. Auto-delivery is paused and the proving period restarted.')
    })

  const promotion = auto?.promotion
  return (
    <div class="usage-layer mailbox-layer" role="dialog" aria-modal="true" aria-label="Mailbox"
      onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <section class="usage-panel mailbox-panel" ref={panel}>
        <header>
          <div><span>MAILBOX</span><strong>messages toward your sessions · one queue, one audit trail</strong></div>
          <div class="usage-header-actions"><button aria-label="Close mailbox" onClick={onClose}>×</button></div>
        </header>
        <div class="mailbox-controls">
          <div class="mailbox-roles">
            <button class={role === 'inbox' ? 'primary' : ''} onClick={() => setRole('inbox')}>inbox</button>
            <button class={role === 'outbox' ? 'primary' : ''} onClick={() => setRole('outbox')}>outbox</button>
          </div>
          <div class="mailbox-auto">
            <button
              class={auto?.paused ? 'primary' : 'danger'}
              disabled={busy}
              title="Stops every automatic delivery immediately, on every session"
              onClick={() => void act(async () => setAuto(await setAutoPaused(!auto?.paused)))}
            >
              {auto?.paused ? 'resume auto-delivery' : 'pause all auto-delivery'}
            </button>
            <button disabled={busy} title="Record a delivery that should not have happened" onClick={reportUnsafe}>
              report unsafe delivery
            </button>
          </div>
        </div>
        <div class="usage-progress" role="status" aria-live="polite">
          <span>·</span>
          <strong>
            {note || (promotion
              ? `auto sends ${promotion.auto_sends}/${promotion.required_sends} · proving ${promotion.proving_days}/${promotion.required_days} days · unsafe reports ${promotion.unsafe_reports}`
              : `${messages.length} message${messages.length === 1 ? '' : 's'}`)}
          </strong>
        </div>
        <main>
          {messages.length === 0
            ? <div class="automation-empty">
                <strong>Nothing here</strong>
                <span>{role === 'inbox'
                  ? 'No agent, rule, or device has addressed a message to your sessions.'
                  : 'You have not staged any messages.'}</span>
              </div>
            : <ul class="mailbox-list">{messages.map(message => (
                <li key={message.id} class={`mailbox-item mailbox-${message.state}`}>
                  <div class="mailbox-item-meta">
                    <span class={`queue-state queue-state-${message.state}`}>{message.state}</span>
                    <span>{senderLabel(message) || 'from you'}</span>
                    <span>→ {message.target_label || message.target_session_id}</span>
                    {scheduleStatus(message) === 'scheduled' && message.constraints?.not_before && (
                      <span class="queue-item-schedule">
                        scheduled {new Date(message.constraints.not_before * 1000).toLocaleString()}
                      </span>
                    )}
                    <span class="mailbox-item-note">{STATE_NOTE[message.state] || ''}</span>
                  </div>
                  <p class="mailbox-item-body">{message.body.slice(0, 400)}</p>
                  <div class="mailbox-item-actions">
                    <button disabled={busy} onClick={() => onOpenQueue?.(message.target_session_id)}>
                      open queue
                    </button>
                    {['draft', 'armed', 'blocked'].includes(message.state) && (
                      <button disabled={busy} onClick={() => revoke(message)}>revoke</button>
                    )}
                  </div>
                </li>
              ))}</ul>}
        </main>
      </section>
    </div>
  )
}
