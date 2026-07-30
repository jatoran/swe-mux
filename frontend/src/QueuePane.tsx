import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { browserUuid } from './layout'
import { stateDotClass } from './sessionStatus'
import { agentTargetName, agentTargets } from './agentTargets'
import {
  armQueueMessage, cancelQueueMessage, editQueueMessage, enqueueMessage, fetchAutoStatus,
  fetchMailbox, fetchQueue, isPendingQueueState, moveQueueMessage, queueHead, reportUnsafeDelivery,
  retargetQueueMessage, scheduleQueueMessage, scheduleStatus, senderLabel, sendQueueMessage,
  setAutoPaused, setSessionAutoPolicy,
  type QueueAutoStatus, type QueueMessage, type QueueSendOutcome, type QueueTargetView,
} from './queueApi'
import type { Session } from './types'

// The prompt queue's one surface, in two renderings and three scopes.
//
// Renderings: normally the Queue tab of the right-edge utility drawer, where it sits
// *beside* the terminal it acts on. That adjacency is the whole argument for the
// placement — deciding whether to interrupt an agent is a judgement about that agent's
// live state, and the terminal is the only place that state is legible. A workspace tab
// replaces the terminal; a modal covers it. The `queue:<session>` pane leaf survives as
// an explicit pop-out (wide review, two queues side by side) and renders this same
// component with its target pinned instead of following focus.
//
// Scopes: `session` is the working view (the ordered queue for one target, plus the
// composer); `inbox`/`outbox` are the former Mailbox modal, folded in here because
// "what is queued for this agent" and "what is queued anywhere" were two surfaces over
// one store with two different action sets. Deliberately not a second transcript in any
// scope: it shows delivery state and provenance, never a conversation.
//
// Every bound is the daemon's — this view shows state and forwards user acts.

type Props = {
  /** The `session` scope's target. The drawer passes the focused session; the pane leaf
   *  passes its own pinned id. Empty when nothing is focused. */
  sessionId: string
  sessions: Session[]
  /** "Show me this terminal" — after a delivery. Closes the drawer on mobile. */
  onSelectSession?: (sessionId: string) => void
  /** "Make this session the queue's target" — from a mailbox row. Keeps the panel open. */
  onFocusTarget?: (sessionId: string) => void
  /** Drawer only: pop this target's queue out into a workspace tab. */
  onOpenAsTab?: (sessionId: string) => void
  /** Set by the caller to mean "you were just opened deliberately": land on this scope,
   *  and focus the composer if it is the working one. A counter rather than a boolean so
   *  clicking the same chip twice focuses twice; switching to the tab by hand passes
   *  nothing and leaves the panel where it was. */
  openRequest?: { token: number; scope: QueueScope }
}

export type QueueScope = 'session' | 'inbox' | 'outbox'

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
    case 'not_due':
      return `Scheduled for ${new Date(outcome.notBefore * 1000).toLocaleString()}. “Send now” overrides the clock.`
    case 'expired':
      return outcome.error
    case 'error':
      return outcome.error
  }
}

/** Presets for "send later"; the daemon resolves the delay to an absolute time. */
const DELAY_PRESETS: { label: string; seconds: number }[] = [
  { label: '+5m', seconds: 300 },
  { label: '+15m', seconds: 900 },
  { label: '+1h', seconds: 3600 },
]

/** One line, because it is a status the working view carries permanently: two labelled
 *  checkboxes and a sentence cost three wrapped lines of a 380px column, above the thing
 *  the panel was opened for. The controls live behind the disclosure. */
function describeAuto(status: QueueAutoStatus | null, sessionId: string): string {
  if (!status) return '…'
  if (!status.master_enabled) return 'off for this install'
  if (status.paused) return 'paused (emergency stop)'
  const row = status.sessions.find(item => item.session_id === sessionId)
  if (!row?.enabled) return row?.disabled_reason ? `off — ${row.disabled_reason}` : 'off'
  const minutes = row.expires_in_s === null ? null : Math.max(0, Math.round(row.expires_in_s / 60))
  const parts = [`${row.sends_remaining} send${row.sends_remaining === 1 ? '' : 's'} left`]
  if (minutes !== null) parts.push(`${minutes} min left`)
  if (status.quiet_hours.active) parts.push('quiet hours — paused')
  return `on · ${parts.join(' · ')}`
}

/** Targets are Claude/Codex sessions only; a shell would execute a paste. */
const isAgentSession = (session: Session | null): boolean =>
  !!session && (session.backend === 'claude' || session.backend === 'codex')

/** Terminal-state items are audit, not work: collapsed by default in the working view. */
const isDoneState = (state: QueueMessage['state']): boolean =>
  state === 'sent' || state === 'failed' || state === 'cancelled'

export function QueuePane({ sessionId, sessions, onSelectSession, onFocusTarget, onOpenAsTab, openRequest }: Props) {
  const [scope, setScope] = useState<QueueScope>('session')
  const [view, setView] = useState<QueueTargetView | null>(null)
  const [mailbox, setMailbox] = useState<QueueMessage[]>([])
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [confirmId, setConfirmId] = useState('')
  const [menuId, setMenuId] = useState('')
  const [editing, setEditing] = useState<{ id: string; revision: number; body: string } | null>(null)
  const [composer, setComposer] = useState('')
  const [retargetFor, setRetargetFor] = useState('')
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [autoOpen, setAutoOpen] = useState(false)
  const [showDone, setShowDone] = useState(false)
  const alive = useRef(true)
  const composerRef = useRef<HTMLTextAreaElement>(null)

  const session = sessions.find(item => item.id === sessionId) || null
  // An id with no session record is an ended target the daemon still holds a queue for
  // (the pop-out tab outliving its session); only a *live non-agent* is refused here.
  const targetable = !!sessionId && (isAgentSession(session) || !session)

  const refresh = useCallback(async () => {
    try {
      const [policy, next] = await Promise.all([
        fetchAutoStatus(),
        scope === 'session'
          ? (targetable ? fetchQueue(sessionId) : Promise.resolve(null))
          : fetchMailbox(scope),
      ])
      if (!alive.current) return
      setAuto(policy)
      if (next === null) setView(null)
      else if ('target' in next) setView(next)
      else setMailbox(next.messages)
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [sessionId, scope, targetable])

  useEffect(() => {
    alive.current = true
    void refresh()
    const onQueueChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId?: string }>).detail
      // A mailbox scope reads every target, so any queue event is its event.
      if (scope !== 'session' || !detail?.sessionId || detail.sessionId === sessionId) void refresh()
    }
    window.addEventListener('mux:queue-changed', onQueueChanged)
    window.addEventListener('mux:events-connected', onQueueChanged)
    return () => {
      alive.current = false
      window.removeEventListener('mux:queue-changed', onQueueChanged)
      window.removeEventListener('mux:events-connected', onQueueChanged)
    }
  }, [sessionId, scope, refresh])

  // Retargeting drops per-message UI that named a message of the previous target: the
  // drawer's target changes under it every time focus moves to another pane.
  useEffect(() => {
    setEditing(null); setConfirmId(''); setMenuId(''); setRetargetFor(''); setError('')
  }, [sessionId, scope])

  // Opened deliberately (the pane chip, a command): land on the scope that act named, and
  // for the working view put the caret where the next act is. Keyed on the token alone —
  // an inline object literal from the caller is a new identity every render.
  const openToken = openRequest?.token
  const openScope = openRequest?.scope
  useEffect(() => {
    if (!openToken || !openScope) return
    setScope(openScope)
    if (openScope === 'session') composerRef.current?.focus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openToken])

  const messages = view?.messages ?? []
  const head = useMemo(() => queueHead(messages), [messages])
  const active = useMemo(() => messages.filter(item => !isDoneState(item.state)), [messages])
  const done = useMemo(() => messages.filter(item => isDoneState(item.state)), [messages])
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
    if (!composer.trim()) return
    await run('composer', async () => {
      await enqueueMessage(sessionId, composer, { armed })
      setComposer('')
    })
  }

  const copyBody = (message: QueueMessage) => {
    void navigator.clipboard?.writeText(message.body).catch(() => {})
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

  /** The secondary acts, in a row that opens under the message rather than a floating
   *  menu: this column scrolls and is 300–620px wide, where a popover would need
   *  positioning, portalling, and an outside-click contract to do the same job.
   *
   *  Splitting them out is what makes the row fit at all: eleven buttons (send, arm,
   *  edit, two moves, cancel, three schedule presets, copy) wrapped to four lines per
   *  message at drawer width, which is most of a phone screen for one queued item. */
  const overflow = (message: QueueMessage, busy: boolean) => {
    const pending = isPendingQueueState(message.state)
    const schedule = scheduleStatus(message)
    return (
      <div class="queue-item-actions queue-item-more">
        {pending && message.state !== 'delivering' && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => setEditing({ id: message.id, revision: message.revision, body: message.body })}
            >
              Edit
            </button>
            <button type="button" title="Move earlier" disabled={busy} onClick={() => void run(message.id, () => moveMessage(message, -1))}>↑</button>
            <button type="button" title="Move later" disabled={busy} onClick={() => void run(message.id, () => moveMessage(message, 1))}>↓</button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(message.id, () => cancelQueueMessage(message.id, head?.id === message.id ? 'skipped' : 'cancelled'))}
            >
              {head?.id === message.id ? 'Skip' : 'Cancel'}
            </button>
            {schedule === 'scheduled' ? (
              <button type="button" disabled={busy} onClick={() => void run(message.id, () => scheduleQueueMessage(message.id, null))}>
                Clear schedule
              </button>
            ) : (
              DELAY_PRESETS.map(preset => (
                <button
                  key={preset.label}
                  type="button"
                  class="queue-schedule-preset"
                  title={`Deliver no earlier than ${preset.label} from now`}
                  disabled={busy}
                  onClick={() =>
                    void run(message.id, () =>
                      scheduleQueueMessage(message.id, { not_before: Date.now() / 1000 + preset.seconds }),
                    )
                  }
                >
                  {preset.label}
                </button>
              ))
            )}
          </>
        )}
        {message.state === 'stranded' && (
          <button type="button" disabled={busy} onClick={() => void run(message.id, () => cancelQueueMessage(message.id, 'cancelled'))}>
            Cancel
          </button>
        )}
        <button type="button" disabled={busy} onClick={() => copyBody(message)}>Copy</button>
      </div>
    )
  }

  const row = (message: QueueMessage) => {
    const pending = isPendingQueueState(message.state)
    const isHead = head?.id === message.id
    const busy = busyId === message.id
    const isEditing = editing?.id === message.id
    const schedule = scheduleStatus(message)
    const from = senderLabel(message)
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
          {from && (
            <span class="queue-item-sender" title={message.origin?.reason || undefined}>
              {from}
              {message.chain_depth > 1 ? ` · hop ${message.chain_depth}` : ''}
            </span>
          )}
          {schedule === 'scheduled' && message.constraints?.not_before && (
            <span class="queue-item-schedule">
              scheduled {new Date(message.constraints.not_before * 1000).toLocaleTimeString()}
            </span>
          )}
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
              </>
            )}
            {message.state === 'stranded' &&
              (retargetFor === message.id ? (
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
              ))}
            <button
              type="button"
              class={`queue-item-menu${menuId === message.id ? ' open' : ''}`}
              aria-expanded={menuId === message.id}
              aria-label="More actions for this message"
              title="More actions"
              disabled={busy}
              onClick={() => setMenuId(current => (current === message.id ? '' : message.id))}
            >
              ⋯
            </button>
          </div>
        )}
        {!isEditing && menuId === message.id && overflow(message, busy)}
      </li>
    )
  }

  const mailboxRow = (message: QueueMessage) => {
    const busy = busyId === message.id
    return (
      <li key={message.id} class={`queue-item queue-item-${message.state}`}>
        <div class="queue-item-meta">
          <span class={`queue-state queue-state-${message.state}`}>{message.state}</span>
          <span>{senderLabel(message) || 'from you'}</span>
          <span class="queue-item-target">→ {message.target_label || message.target_session_id}</span>
          {scheduleStatus(message) === 'scheduled' && message.constraints?.not_before && (
            <span class="queue-item-schedule">
              scheduled {new Date(message.constraints.not_before * 1000).toLocaleString()}
            </span>
          )}
          <span class="queue-item-note">{STATE_NOTE[message.state] || ''}</span>
        </div>
        <pre class={`queue-item-body${message.state === 'sent' ? ' queue-item-sent' : ''}`}>{message.body}</pre>
        <div class="queue-item-actions">
          <button
            type="button"
            disabled={busy}
            onClick={() => { setScope('session'); onFocusTarget?.(message.target_session_id) }}
          >
            Open queue
          </button>
          {['draft', 'armed', 'blocked'].includes(message.state) && (
            <button type="button" disabled={busy} onClick={() => void run(message.id, () => cancelQueueMessage(message.id, 'revoked'))}>
              Revoke
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => copyBody(message)}>Copy</button>
        </div>
      </li>
    )
  }

  const targetLabel = session ? agentTargetName(session) : view?.target.label || sessionId
  const live = view?.target.live ?? !!session
  const policy = auto?.sessions.find(item => item.session_id === sessionId) ?? null
  const autoOn = !!policy?.enabled
  const setPolicy = (patch: Parameters<typeof setSessionAutoPolicy>[1]) =>
    void run('auto', async () => {
      setAuto(await setSessionAutoPolicy(sessionId, patch))
    })
  const promotion = auto?.promotion

  return (
    <div class="queue-pane">
      <div class="queue-scope" role="tablist" aria-label="Queue scope">
        {(['session', 'inbox', 'outbox'] as QueueScope[]).map(item => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={scope === item}
            class={scope === item ? 'active' : ''}
            title={item === 'session'
              ? 'Messages staged for the focused agent'
              : item === 'inbox'
                ? 'Everything other actors addressed to your sessions'
                : 'Everything you staged, wherever it is headed'}
            onClick={() => setScope(item)}
          >
            {item === 'session' ? 'this session' : item}
          </button>
        ))}
      </div>

      {scope === 'session' ? (
        <>
          <header class="queue-pane-header">
            <span class={stateDotClass(session?.state)} />
            <strong>{targetable ? targetLabel : 'no agent focused'}</strong>
            {targetable && (
              <span class="queue-pane-status">
                {live ? `${view?.pending ?? 0} pending` : 'target ended — pending items are stranded'}
              </span>
            )}
            {targetable && onOpenAsTab && (
              <button
                type="button"
                class="queue-popout"
                title="Open this queue as a workspace tab"
                aria-label="Open this queue as a workspace tab"
                onClick={() => onOpenAsTab(sessionId)}
              >
                ↗
              </button>
            )}
          </header>
          {targetable && live && (
            <div class="queue-auto-strip">
              <button
                type="button"
                class={`queue-auto-summary${autoOn ? ' queue-auto-on' : ''}`}
                aria-expanded={autoOpen}
                title="Bounded, expiring, and never overrides a not-safe target"
                onClick={() => setAutoOpen(value => !value)}
              >
                <span aria-hidden="true">{autoOpen ? '▾' : '▸'}</span> auto: {describeAuto(auto, sessionId)}
              </button>
            </div>
          )}
          {targetable && live && autoOpen && (
            <div class="queue-auto-detail">
              <label class="queue-auto-toggle">
                <input
                  type="checkbox"
                  checked={autoOn}
                  disabled={busyId === 'auto' || !auto?.master_enabled}
                  onChange={event => setPolicy({ enabled: event.currentTarget.checked })}
                />
                <span>auto-deliver armed messages</span>
              </label>
              <label class="queue-auto-toggle" title="Agent messages arrive armed instead of as drafts">
                <input
                  type="checkbox"
                  checked={!!policy?.accept_agent_messages}
                  disabled={busyId === 'auto'}
                  onChange={event => setPolicy({ acceptAgentMessages: event.currentTarget.checked })}
                />
                <span>accept agent messages armed</span>
              </label>
              {auto && !auto.master_enabled && (
                <p class="queue-auto-note">
                  Auto-delivery is off for this install. Turn on “Allow auto-delivery” under Settings →
                  Agents → Prompt queue to make this per-session opt-in available.
                </p>
              )}
            </div>
          )}
        </>
      ) : (
        <div class="queue-mailbox-controls">
          <button
            type="button"
            class={auto?.paused ? 'primary' : 'danger'}
            disabled={busyId === 'auto'}
            title="Stops every automatic delivery immediately, on every session"
            onClick={() => void run('auto', async () => setAuto(await setAutoPaused(!auto?.paused)))}
          >
            {auto?.paused ? 'resume auto-delivery' : 'pause all auto-delivery'}
          </button>
          <button
            type="button"
            disabled={busyId === 'auto'}
            title="Record a delivery that should not have happened"
            onClick={() => void run('auto', async () => {
              await reportUnsafeDelivery('reported from the queue panel')
              setError('Recorded. Auto-delivery is paused and the proving period restarted.')
            })}
          >
            report unsafe delivery
          </button>
          {promotion && (
            <span class="queue-promotion">
              auto sends {promotion.auto_sends}/{promotion.required_sends} · proving{' '}
              {promotion.proving_days}/{promotion.required_days} days · unsafe {promotion.unsafe_reports}
            </span>
          )}
        </div>
      )}

      {error && (
        <p class="queue-pane-error" role="alert">
          {error}
        </p>
      )}

      {scope === 'session' ? (
        <ul class="queue-list">
          {active.map(row)}
          {!active.length && (
            <li class="queue-empty">
              {targetable
                ? 'Nothing queued. Messages staged here wait for your explicit “Send now” — nothing is ever delivered on a timer.'
                : 'Focus a Claude or Codex session to stage messages for it. Shells are never targets: a paste there would execute.'}
            </li>
          )}
          {done.length > 0 && (
            <li class="queue-done">
              <button type="button" aria-expanded={showDone} onClick={() => setShowDone(value => !value)}>
                <span aria-hidden="true">{showDone ? '▾' : '▸'}</span> {done.length} delivered or closed
              </button>
            </li>
          )}
          {showDone && done.map(row)}
        </ul>
      ) : (
        <ul class="queue-list">
          {mailbox.map(mailboxRow)}
          {!mailbox.length && (
            <li class="queue-empty">
              {scope === 'inbox'
                ? 'No agent, rule, or device has addressed a message to your sessions.'
                : 'You have not staged any messages.'}
            </li>
          )}
        </ul>
      )}

      {scope === 'session' && targetable && live && (
        <footer class="queue-composer">
          <textarea
            ref={composerRef}
            value={composer}
            placeholder="Stage a message for this agent…"
            title="Ctrl+Enter stages it armed"
            disabled={busyId === 'composer'}
            onInput={event => setComposer(event.currentTarget.value)}
            onKeyDown={event => {
              if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return
              event.preventDefault()
              void add(true)
            }}
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
              title="Ctrl+Enter"
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
