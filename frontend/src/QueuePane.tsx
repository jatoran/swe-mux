import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { browserUuid } from './layout'
import { stateDotClass } from './sessionStatus'
import { agentTargetName, agentTargets } from './agentTargets'
import {
  armQueueMessage, cancelQueueMessage, editQueueMessage, enqueueMessage, fetchQueue,
  isPendingQueueState, moveQueueMessage, queueHead, retargetQueueMessage, sendQueueMessage,
  type QueueMessage, type QueueSendOutcome, type QueueTargetView,
} from './queueApi'
import type { Session } from './types'

// Phase 4: the Queue workspace tab, attached to one target session/agent run.
// Everything here is manual: add, edit, arm, reorder, cancel/skip, and the
// explicit "send next now" — the daemon enforces order, revision, readiness,
// and identity; this view only shows state and forwards user acts.

type Props = {
  sessionId: string
  sessions: Session[]
  onSelectSession?: (sessionId: string) => void
}

const STATE_LABEL: Record<string, string> = {
  draft: 'draft',
  armed: 'armed',
  blocked: 'blocked',
  delivering: 'delivering…',
  sent: 'sent',
  failed: 'failed',
  cancelled: 'cancelled',
  stranded: 'stranded',
}

function describeOutcome(outcome: QueueSendOutcome): string {
  switch (outcome.status) {
    case 'sent':
      return ''
    case 'queued_behind':
      return 'An earlier pending message must go first (strict order).'
    case 'blocked':
      return outcome.protected
        ? `Blocked by a protection that cannot be overridden: ${outcome.reasons.join(', ')}`
        : `Not safe right now: ${outcome.reasons.join(', ')}`
    case 'stranded':
      return outcome.error
    case 'revision_conflict':
      return 'The message changed since this view loaded; it has been refreshed.'
    case 'error':
      return outcome.error
  }
}

export function QueuePane({ sessionId, sessions, onSelectSession }: Props) {
  const [view, setView] = useState<QueueTargetView | null>(null)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [confirmId, setConfirmId] = useState('')
  const [editing, setEditing] = useState<{ id: string; revision: number; body: string } | null>(null)
  const [composer, setComposer] = useState('')
  const [retargetFor, setRetargetFor] = useState('')
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const next = await fetchQueue(sessionId)
      if (alive.current) setView(next)
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [sessionId])

  useEffect(() => {
    alive.current = true
    void refresh()
    const onQueueChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId?: string }>).detail
      if (!detail?.sessionId || detail.sessionId === sessionId) void refresh()
    }
    window.addEventListener('mux:queue-changed', onQueueChanged)
    window.addEventListener('mux:events-connected', onQueueChanged)
    return () => {
      alive.current = false
      window.removeEventListener('mux:queue-changed', onQueueChanged)
      window.removeEventListener('mux:events-connected', onQueueChanged)
    }
  }, [sessionId, refresh])

  const session = sessions.find(item => item.id === sessionId) || null
  const messages = view?.messages ?? []
  const head = useMemo(() => queueHead(messages), [messages])
  const liveAgents = useMemo(
    () => agentTargets(sessions, session?.project_id ?? '').filter(item => item.id !== sessionId),
    [sessions, session, sessionId],
  )

  const run = async (id: string, action: () => Promise<unknown>) => {
    setBusyId(id)
    setError('')
    try {
      await action()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusyId('')
      void refresh()
    }
  }

  const send = async (message: QueueMessage, confirm: boolean) => {
    setBusyId(message.id)
    setError('')
    const outcome = await sendQueueMessage(message.id, message.revision, {
      confirm,
      idempotencyKey: browserUuid(),
    })
    setBusyId('')
    if (outcome.status === 'sent') {
      setConfirmId('')
      if (onSelectSession) onSelectSession(sessionId)
    } else if (outcome.status === 'blocked' && !outcome.protected) {
      // The daemon refused without confirmation; surface the reasons and offer
      // the explicit "send anyway" that is the queue's one override point.
      setConfirmId(message.id)
      setError(describeOutcome(outcome))
    } else {
      setConfirmId('')
      setError(describeOutcome(outcome))
    }
    void refresh()
  }

  const add = async (armed: boolean) => {
    const body = composer.trim()
    if (!body) return
    await run('composer', async () => {
      await enqueueMessage(sessionId, composer, { armed })
      setComposer('')
    })
  }

  const copyBody = (message: QueueMessage) => {
    void navigator.clipboard?.writeText(message.body).catch(() => {})
  }

  const row = (message: QueueMessage) => {
    const pending = isPendingQueueState(message.state)
    const isHead = head?.id === message.id
    const busy = busyId === message.id
    const isEditing = editing?.id === message.id
    return (
      <li
        key={message.id}
        class={`queue-item queue-item-${message.state}${isHead ? ' queue-item-head' : ''}`}
      >
        <div class="queue-item-meta">
          <span class={`queue-state queue-state-${message.state}`}>
            {STATE_LABEL[message.state] || message.state}
          </span>
          {isHead && <span class="queue-next-marker">next</span>}
          <span class="queue-item-revision">rev {message.revision}</span>
          {message.blocked_reasons?.length ? (
            <span class="queue-item-reasons">{message.blocked_reasons.join(', ')}</span>
          ) : null}
          {message.stranded_reason && (
            <span class="queue-item-reasons">{message.stranded_reason}</span>
          )}
        </div>
        {isEditing ? (
          <div class="queue-item-edit">
            <textarea
              value={editing.body}
              disabled={busy}
              onInput={event => setEditing({ ...editing, body: event.currentTarget.value })}
            />
            <div class="queue-item-actions">
              <button
                type="button"
                disabled={busy || !editing.body.trim()}
                onClick={() =>
                  void run(message.id, async () => {
                    await editQueueMessage(editing.id, editing.revision, editing.body)
                    setEditing(null)
                  })
                }
              >
                Save
              </button>
              <button type="button" disabled={busy} onClick={() => setEditing(null)}>
                Discard
              </button>
            </div>
          </div>
        ) : (
          <pre class={`queue-item-body${message.state === 'sent' ? ' queue-item-sent' : ''}`}>
            {message.body}
          </pre>
        )}
        {!isEditing && (
          <div class="queue-item-actions">
            {pending && message.state !== 'delivering' && (
              <>
                {isHead &&
                  (confirmId === message.id ? (
                    <button
                      type="button"
                      class="queue-send queue-send-confirm"
                      disabled={busy}
                      onClick={() => void send(message, true)}
                    >
                      Send anyway
                    </button>
                  ) : (
                    <button
                      type="button"
                      class="queue-send"
                      disabled={busy}
                      onClick={() => void send(message, false)}
                    >
                      Send now
                    </button>
                  ))}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(message.id, () => armQueueMessage(message.id, message.state !== 'armed'))}
                >
                  {message.state === 'armed' ? 'Unarm' : 'Arm'}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setEditing({ id: message.id, revision: message.revision, body: message.body })}
                >
                  Edit
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(message.id, () => moveMessage(message, -1))}
                >
                  ↑
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(message.id, () => moveMessage(message, 1))}
                >
                  ↓
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(message.id, () => cancelQueueMessage(message.id, isHead ? 'skipped' : 'cancelled'))}
                >
                  {isHead ? 'Skip' : 'Cancel'}
                </button>
              </>
            )}
            {message.state === 'stranded' && (
              <>
                {retargetFor === message.id ? (
                  <select
                    disabled={busy}
                    onChange={event => {
                      const target = event.currentTarget.value
                      setRetargetFor('')
                      if (target) void run(message.id, () => retargetQueueMessage(message.id, target))
                    }}
                  >
                    <option value="">Retarget to…</option>
                    {liveAgents.map(item => (
                      <option key={item.id} value={item.id}>
                        {agentTargetName(item)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <button type="button" disabled={busy || !liveAgents.length} onClick={() => setRetargetFor(message.id)}>
                    Retarget
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void run(message.id, () => cancelQueueMessage(message.id, 'cancelled'))}
                >
                  Cancel
                </button>
              </>
            )}
            <button type="button" disabled={busy} onClick={() => copyBody(message)}>
              Copy
            </button>
          </div>
        )}
      </li>
    )
  }

  // Reorder helper: move one step among pending items only.
  const moveMessage = async (message: QueueMessage, delta: 1 | -1) => {
    const pending = messages.filter(item => isPendingQueueState(item.state))
    const index = pending.findIndex(item => item.id === message.id)
    const targetIndex = index + delta
    if (index < 0 || targetIndex < 0 || targetIndex >= pending.length) return
    const after = delta === 1 ? pending[targetIndex].id : pending[targetIndex - 1]?.id ?? null
    await moveQueueMessage(message.id, after)
  }

  const targetLabel = session ? agentTargetName(session) : view?.target.label || sessionId
  const live = view?.target.live ?? !!session
  return (
    <div class="queue-pane">
      <header class="queue-pane-header">
        <span class={stateDotClass(session?.state)} />
        <strong>{targetLabel}</strong>
        <span class="queue-pane-status">
          {live ? `${view?.pending ?? 0} pending` : 'target ended — pending items are stranded'}
        </span>
      </header>
      {error && (
        <p class="queue-pane-error" role="alert">
          {error}
        </p>
      )}
      <ul class="queue-list">
        {messages.map(row)}
        {!messages.length && (
          <li class="queue-empty">
            Nothing queued. Messages staged here wait for your explicit “Send now” — nothing is
            ever delivered on a timer.
          </li>
        )}
      </ul>
      {live && (
        <footer class="queue-composer">
          <textarea
            value={composer}
            placeholder="Stage a message for this agent…"
            disabled={busyId === 'composer'}
            onInput={event => setComposer(event.currentTarget.value)}
          />
          <div class="queue-composer-actions">
            <button
              type="button"
              disabled={busyId === 'composer' || !composer.trim()}
              onClick={() => void add(false)}
            >
              Add draft
            </button>
            <button
              type="button"
              class="primary"
              disabled={busyId === 'composer' || !composer.trim()}
              onClick={() => void add(true)}
            >
              Add armed
            </button>
          </div>
        </footer>
      )}
    </div>
  )
}
