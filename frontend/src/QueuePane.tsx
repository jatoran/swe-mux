import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { browserUuid } from './layout'
import { hasSoftKeyboard } from './deviceSettings'
import { GrantGate } from './GrantGate'
import { SettingLink } from './SettingLink'
import { StateIndicator } from './StateIndicator'
import { useSessionRowConfig } from './sessionRowPrefs'
import { agentTargetName, agentTargets } from './agentTargets'
import {
  armQueueMessage, cancelQueueMessage, deleteQueueMessage, editQueueMessage, enqueueMessage, fetchAutoStatus,
  fetchQueue, isPendingQueueState, moveQueueMessage, queueHead, reportUnsafeDelivery,
  retargetQueueMessage, scheduleQueueMessage, scheduleStatus, senderLabel, sendQueueMessage,
  setAutoPaused, setSessionAutoPolicy,
  type QueueAutoSession, type QueueAutoStatus, type QueueMessage, type QueueSendOutcome,
  type QueueTargetView,
} from './queueApi'
import type { Session } from './types'
import { deliversHarnessPrompts } from './harnessRegistry'
import { reportPromptSubmitted } from './projectRecency'
import { serverNow } from './serverClock.ts'
import { forgetEditorFocus, noteEditorFocus } from './insertTarget'
import type { EditorHandle } from './insertTarget'

// The prompt queue's session-scoped surface, in two renderings.
//
// Renderings: normally the Queue tab of the right-edge utility drawer, where it sits
// *beside* the terminal it acts on. That adjacency is the whole argument for the
// placement — deciding whether to interrupt an agent is a judgement about that agent's
// live state, and the terminal is the only place that state is legible. A workspace tab
// replaces the terminal; a modal covers it. The `queue:<session>` pane leaf survives as
// an explicit pop-out (wide review, two queues side by side) and renders this same
// component with its target pinned instead of following focus.
//
// This is the only queue surface that delivers, so it is also where the install-wide
// auto-delivery brakes live. They are global, not per-session, and they sit behind the
// same disclosure as the per-session policy because that is the one place a person is
// already standing when they decide automatic delivery has to stop: watching an agent
// receive something. `autodelivery.pause` reaches the same operation with nothing open.
//
// The fleet-wide review of *every* target's queue is a modal, reached from the header
// here. It has no send button, so it needs no terminal beside it.
//
// Every bound is the daemon's — this view shows state and forwards user acts.

type Props = {
  /** The `session` scope's target. The drawer passes the focused session; the pane leaf
   *  passes its own pinned id. Empty when nothing is focused. */
  sessionId: string
  sessions: Session[]
  /** "Show me this terminal" — after a delivery. Closes the drawer on mobile. */
  onSelectSession?: (sessionId: string) => void
  /** Drawer only: pop this target's queue out into a workspace tab. */
  onOpenAsTab?: (sessionId: string) => void
  /** Drawer only: open the fleet-wide review overlay. */
  onOpenFleetQueue?: () => void
  /** Pending items across every target, not just this one. Labels the fleet-queue control
   *  so "is anything waiting anywhere" is answerable without opening it. */
  fleetPending?: number
  /** Deliberate-open counter: focus the composer even when Queue was already selected. */
  openRequestToken?: number
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
 *  the panel was opened for. The controls live behind the disclosure.
 *
 *  Answers for the install first, then for the session. The install-wide states are the
 *  ones that make every other reading a lie, and they are true with no session focused —
 *  which is why this line, and the emergency stop behind it, survive an empty target. */
/** The numbers behind a lapse, in the one place a reader is already asking why.
 *
 *  A lapse is the only disable with no act behind it, so "lapsed while the conversation
 *  was idle" is the whole story anyone ever got — true, and useless for deciding whether
 *  the window is too short or the conversation really was abandoned. Absent fields stay
 *  absent: a grant that lapsed before the audit existed says nothing rather than zero. */
function describeLapse(lapse: QueueAutoSession['lapse']): string {
  if (!lapse) return ''
  const parts: string[] = []
  if (lapse.idle_seconds !== null) parts.push(`idle ${Math.round(lapse.idle_seconds / 60)} min`)
  if (lapse.window_minutes !== null) parts.push(`${Math.round(lapse.window_minutes)} min window`)
  if (lapse.pending) parts.push(`${lapse.pending} waiting`)
  return parts.length ? ` (${parts.join(', ')})` : ''
}

function describeAuto(status: QueueAutoStatus | null, sessionId: string): string {
  if (!status) return '…'
  if (!status.master_enabled) return 'off for this install'
  if (status.paused) return 'paused (emergency stop)'
  if (!sessionId) return 'armed for this install'
  const row = status.sessions.find(item => item.session_id === sessionId)
  if (!row?.enabled) {
    return row?.disabled_reason ? `off — ${row.disabled_reason}${describeLapse(row.lapse)}` : 'off'
  }
  const minutes = row.expires_in_s === null ? null : Math.max(0, Math.round(row.expires_in_s / 60))
  const parts = [`${row.sends_remaining} send${row.sends_remaining === 1 ? '' : 's'} left`]
  if (minutes !== null) parts.push(`${minutes} min left`)
  // Why the grant is still here rather than lapsed: this conversation is owed an
  // answer — by a peer it messaged, or by the land queue it asked to land a branch.
  if (row.reply_window)
    parts.push(row.reply_window.kind === 'land' ? 'land in flight' : 'reply window open')
  if (status.quiet_hours.active) parts.push('quiet hours — paused')
  return `on · ${parts.join(' · ')}`
}

/** Targets are registered prompt-delivery harnesses only; a shell would execute a paste. */
const isAgentSession = (session: Session | null): boolean =>
  !!session && deliversHarnessPrompts(session.backend)

/** Terminal-state items are audit, not work: collapsed by default in the working view. */
const isDoneState = (state: QueueMessage['state']): boolean =>
  state === 'sent' || state === 'failed' || state === 'cancelled'

export function QueuePane({
  sessionId, sessions, onSelectSession, onOpenAsTab, onOpenFleetQueue, fleetPending = 0,
  openRequestToken,
}: Props) {
  const rowConfig = useSessionRowConfig()
  const [view, setView] = useState<QueueTargetView | null>(null)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState('')
  const [confirmId, setConfirmId] = useState('')
  const [deleteConfirmId, setDeleteConfirmId] = useState('')
  const [menuId, setMenuId] = useState('')
  const [editing, setEditing] = useState<{ id: string; revision: number; body: string } | null>(null)
  const [composer, setComposer] = useState('')
  const [retargetFor, setRetargetFor] = useState('')
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [autoOpen, setAutoOpen] = useState(false)
  const [showDone, setShowDone] = useState(false)
  const alive = useRef(true)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const composerSessionRef = useRef(sessionId)
  composerSessionRef.current = sessionId

  const session = sessions.find(item => item.id === sessionId) || null
  const composerTargetLabel = session ? agentTargetName(session) : sessionId || 'No session'
  const composerHandle = useMemo<EditorHandle>(() => {
    const targetSessionId = sessionId
    return {
      get isConnected() { return composerSessionRef.current === targetSessionId && (composerRef.current?.isConnected ?? false) },
      insertText: text => {
        if (composerSessionRef.current !== targetSessionId) return
        const field = composerRef.current
        const start = field?.selectionStart ?? 0
        const end = field?.selectionEnd ?? start
        setComposer(current => {
          const safeStart = Math.min(start, current.length)
          const safeEnd = Math.min(Math.max(end, safeStart), current.length)
          const next = `${current.slice(0, safeStart)}${text}${current.slice(safeEnd)}`
          requestAnimationFrame(() => {
            const caret = safeStart + text.length
            composerRef.current?.setSelectionRange(caret, caret)
          })
          return next
        })
      },
    }
  }, [sessionId])
  const composerSurface = {
    id: `queue:${sessionId}`,
    kind: 'queue' as const,
    label: `Queue · ${composerTargetLabel}`,
  }
  useEffect(() => () => forgetEditorFocus(composerHandle), [composerHandle])
  useEffect(() => {
    if (document.activeElement === composerRef.current) noteEditorFocus(composerHandle, composerSurface)
  }, [sessionId, composerTargetLabel])
  // An id with no session record is an ended target the daemon still holds a queue for
  // (the pop-out tab outliving its session); only a *live non-agent* is refused here.
  const targetable = !!sessionId && (isAgentSession(session) || !session)

  const refresh = useCallback(async () => {
    try {
      const [policy, next] = await Promise.all([
        fetchAutoStatus(),
        targetable ? fetchQueue(sessionId) : Promise.resolve(null),
      ])
      if (!alive.current) return
      setAuto(policy)
      setView(next)
    } catch (cause) {
      if (alive.current) setError(cause instanceof Error ? cause.message : String(cause))
    }
  }, [sessionId, targetable])

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

  // Retargeting drops per-message UI that named a message of the previous target: the
  // drawer's target changes under it every time focus moves to another pane.
  useEffect(() => {
    setEditing(null); setConfirmId(''); setDeleteConfirmId(''); setMenuId(''); setRetargetFor(''); setError('')
  }, [sessionId])

  // A chip or command can open an already-selected Queue tab; the token still earns focus.
  //
  // Never on a device whose only keyboard is an on-screen one. There, focusing a field is
  // not a convenience but a layout change: the keyboard rises over most of the drawer, so
  // opening the Queue to *read* it — which is what the tab is opened for far more often
  // than composing — arrives with the list already covered and a dismissal to perform.
  // The desktop caret costs nothing and is kept. `hasSoftKeyboard` rather than the mobile
  // breakpoint, deliberately: a narrowed desktop window has a real keyboard and a landscape
  // tablet does not.
  useEffect(() => {
    if (openRequestToken && !hasSoftKeyboard()) composerRef.current?.focus()
  }, [openRequestToken])

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
      reportPromptSubmitted(sessionId)
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

  /** Delete, in exactly one implementation.
   *
   *  It is drawn twice — on the row itself and inside the overflow — because removing a
   *  message someone changed their mind about is the one act a reader wanted often enough
   *  to resent opening a menu for, while the overflow must keep it for the case where the
   *  row is scrolled or the compact mark is ambiguous. Two copies of a *destructive*
   *  control is exactly where behaviour drifts, so both call this: same arm-then-confirm
   *  (one click marks, the second deletes), same `deleteConfirmId` — so confirming from
   *  either place is the same armed state, not two — same busy guard, and the same absence
   *  while a message is mid-delivery, which is the one state the daemon will not accept a
   *  delete in. Only the resting label differs, because a full-width menu row has space
   *  for a word and an inline row does not. */
  const deleteButton = (message: QueueMessage, busy: boolean, compact: boolean) => {
    if (message.state === 'delivering') return null
    const armed = deleteConfirmId === message.id
    return (
      <button
        type="button"
        class={`danger${compact ? ' queue-item-delete' : ''}${armed ? ' confirming' : ''}`}
        aria-label={armed ? 'Confirm deleting this message' : 'Delete this message'}
        title={armed ? 'Click again to delete permanently' : 'Delete this message'}
        disabled={busy}
        onClick={() => {
          if (!armed) {
            setDeleteConfirmId(message.id)
            return
          }
          setDeleteConfirmId('')
          void run(message.id, () => deleteQueueMessage(message.id))
        }}
      >
        {armed ? 'Delete permanently' : compact ? '×' : 'Delete'}
      </button>
    )
  }

  /** The secondary acts, in a row that opens under the message rather than a floating
   *  menu: this column scrolls and can be as narrow as 300px, where a popover would need
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
                      // Daemon clock: this instant is sent to the daemon and the
                      // daemon is what waits on it, so a browser out of step
                      // would release the message early or hold it late.
                      scheduleQueueMessage(message.id, { not_before: serverNow() + preset.seconds }),
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
        {deleteButton(message, busy, false)}
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
              onClick={() => {
                setDeleteConfirmId('')
                setMenuId(current => (current === message.id ? '' : message.id))
              }}
            >
              ⋯
            </button>
            {/* Last, and after the overflow rather than before it: a destructive control
                does not sit where a distracted hand aims for the menu. */}
            {deleteButton(message, busy, true)}
          </div>
        )}
        {!isEditing && menuId === message.id && overflow(message, busy)}
      </li>
    )
  }

  const targetLabel = session ? agentTargetName(session) : view?.target.label || sessionId
  const live = view?.target.live ?? !!session
  /** A live agent this pane can stage for. The per-session auto-delivery policy is
   *  meaningless without one; the install-wide state and its brakes are not. */
  const sessionTargeted = targetable && live
  const policy = auto?.sessions.find(item => item.session_id === sessionId) ?? null
  const autoOn = !!policy?.enabled
  const setPolicy = (patch: Parameters<typeof setSessionAutoPolicy>[1]) =>
    void run('auto', async () => {
      setAuto(await setSessionAutoPolicy(sessionId, patch))
    })
  return (
    <div class="queue-pane">
      <header class="queue-pane-header">
        <StateIndicator session={session ?? undefined} shape={rowConfig.dotShape} />
        <strong>{targetable ? targetLabel : 'no agent focused'}</strong>
        {targetable && (
          <span class="queue-pane-status">
            {live ? `${view?.pending ?? 0} pending` : 'target ended — pending items are stranded'}
          </span>
        )}
        {onOpenFleetQueue && (
          <button
            type="button"
            class={`queue-fleet-open${fleetPending > 0 ? ' has-pending' : ''}`}
            title="Fleet queue - every queued message across all sessions, by who wrote it"
            onClick={onOpenFleetQueue}
          >
            fleet{fleetPending > 0 ? ` ${fleetPending > 99 ? '99+' : fleetPending}` : ''}
          </button>
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
      <div class="queue-auto-strip">
        <button
          type="button"
          class={`queue-auto-summary${autoOn ? ' queue-auto-on' : ''}`}
          aria-expanded={autoOpen}
          title="Bounded, expiring, and never overrides a not-safe target"
          onClick={() => setAutoOpen(value => !value)}
        >
          <span aria-hidden="true">{autoOpen ? '▾' : '▸'}</span> auto: {describeAuto(auto, sessionTargeted ? sessionId : '')}
        </button>
      </div>
      {autoOpen && (
        <div class="queue-auto-detail">
          {sessionTargeted && (
            <>
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
              {/* Independent of the two above: arming decides whether a peer's
                  message counts as authorized, auto-delivery decides who presses
                  send, and this decides whether send may happen while a turn is
                  still running. Only a peer whose Project was granted mid-turn
                  delivery can ask for it, and the readiness tracker still has to
                  agree the turn is really running. */}
              <label
                class="queue-auto-toggle"
                title="A peer may have an urgent message written into a running turn; the CLI takes it at the turn boundary"
              >
                <input
                  type="checkbox"
                  checked={!!policy?.accept_agent_interjections}
                  disabled={busyId === 'auto'}
                  onChange={event => setPolicy({ acceptAgentInterjections: event.currentTarget.checked })}
                />
                <span>accept mid-turn agent messages</span>
              </label>
            </>
          )}
          {/* A lapse is the one "off" a reader cannot act on from the reason alone.
              The window is a value rather than a switch, so this is a link and not a
              gate: a gate can honestly offer "turn this on", never "pick a number". */}
          {sessionTargeted && policy?.lapse && (
            <p class="queue-auto-lapse">
              This conversation's grant lapsed on its own after{' '}
              {policy.lapse.idle_seconds !== null
                ? `${Math.round(policy.lapse.idle_seconds / 60)} idle minutes`
                : 'the idle window'}
              {policy.lapse.window_minutes !== null
                && ` of a ${Math.round(policy.lapse.window_minutes)}-minute window`}
              {!!policy.lapse.pending && `, leaving ${policy.lapse.pending} message(s) waiting`}
              . It comes back on its own when the conversation is used again.{' '}
              <SettingLink variant="link" target="queue.grantWindow">Lengthen the window</SettingLink>{' '}
              if peers keep finding each other unreachable.
            </p>
          )}
          {sessionTargeted && policy?.reply_window && (
            <p class="queue-auto-lapse">
              {policy.reply_window.kind === 'land' ? (
                <>
                  Held open by a land request: this session asked to land{' '}
                  {policy.reply_window.branch || 'a branch'} and the queue has not
                  answered yet, so the idle window is not closing its grant.
                </>
              ) : (
                <>
                  Held open by an exchange: this session is owed a reply, so the idle
                  window is not closing its grant yet (
                  {policy.reply_window.thread_messages_used} of{' '}
                  {policy.reply_window.thread_messages_limit} messages used in that
                  thread).
                </>
              )}
            </p>
          )}
          {auto && !auto.master_enabled && (
            <GrantGate ids={['queue.autoDelivery']}
              heading="Auto-delivery is off for this install."
              onGranted={() => void run('auto', async () => setAuto(await fetchAutoStatus()))}>
              <p>Armed messages stay in the queue until you send them by hand, on every
              session. Turning it on delivers nothing by itself: each conversation still
              opts in, the stability window still has to be satisfied, and the emergency
              pause below still stops everything at once.</p>
            </GrantGate>
          )}
          {/* The two install-wide brakes. They are not per-session, and they are here
              rather than in the fleet overlay because a stop reachable only by opening
              something is not reachable at the moment you want it. */}
          <div class="queue-auto-emergency">
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
              title="Record a delivery that should not have happened; pauses auto-delivery and restarts the proving period"
              onClick={() => void run('auto', async () => {
                setAuto(await reportUnsafeDelivery('reported from the queue'))
                setError('Recorded. Auto-delivery is paused and the proving period restarted.')
              })}
            >
              report unsafe delivery
            </button>
            {auto?.promotion && (
              <span class="queue-promotion">
                auto sends {auto.promotion.auto_sends}/{auto.promotion.required_sends} · proving{' '}
                {auto.promotion.proving_days}/{auto.promotion.required_days} days · unsafe{' '}
                {auto.promotion.unsafe_reports}
              </span>
            )}
          </div>
        </div>
      )}

      {error && (
        <p class="queue-pane-error" role="alert">
          {error}
        </p>
      )}

      <ul class="queue-list">
        {active.map(row)}
        {!active.length && (
          <li class="queue-empty">
            {targetable
              ? 'Nothing queued. Messages staged here wait for your explicit “Send now” — nothing is ever delivered on a timer.'
              : 'Focus an agent session to stage messages for it. Shells are never targets: a paste there would execute.'}
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

      {targetable && live && (
        <footer class="queue-composer">
          <textarea
            ref={composerRef}
            value={composer}
            placeholder="Stage a message for this agent…"
            title="Ctrl+Enter stages it armed"
            disabled={busyId === 'composer'}
            onFocus={() => noteEditorFocus(composerHandle, composerSurface)}
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
