import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { browserUuid } from './layout'
import { hasSoftKeyboard } from './deviceSettings'
import { Dropdown } from './Dropdown'
import { CompactGrantFlag } from './GrantGate'
import { SettingLink } from './SettingLink'
import { StateIndicator } from './StateIndicator'
import { CopyIcon, PlusIcon, RenameIcon, TrashIcon } from './railIcons'
import { useSessionRowConfig } from './sessionRowPrefs'
import { agentTargetName, agentTargets } from './agentTargets'
import {
  armQueueMessage, cancelQueueMessage, deleteQueueMessage, fetchAutoStatus,
  fetchQueue, isPendingQueueState, moveQueueMessage, queueHead, reportUnsafeDelivery,
  retargetQueueMessage, scheduleQueueMessage, scheduleStatus, senderLabel, sendQueueMessage,
  setAutoPaused, setSessionAutoPolicy,
  type QueueAutoSession, type QueueAutoStatus, type QueueConstraints, type QueueMessage,
  type QueueSendOutcome, type QueueTargetView,
} from './queueApi'
import { queueDraftSaver, type QueueDraftState } from './queueDraftSaver'
import type { Session } from './types'
import {
  describeReadiness, freshestReadiness, readinessAgeLabel, wordReasons,
} from './deliveryReadiness'
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
// There is one writing surface and it is a queue row. The pane used to carry a permanent
// composer footer as well, which meant two text fields with different rules — one that
// staged new items and autosaved nothing, one that edited existing items behind an
// explicit Save — sharing a 300px column. Composing is now the same act as editing:
// `+` appends a blank draft, opens it, and autosaves it, so a message is a row from the
// first keystroke and the drawer can be swiped shut without losing it.
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
  /** Deliberate-open counter: start a draft even when Queue was already selected. */
  openRequestToken?: number
}

/** The open editor, for a row that exists or for one that does not exist yet.
 *
 *  `messageId` is empty only until autosave's first write creates the item; from then on
 *  this is an ordinary edit of an ordinary queued message, and every act below reads the
 *  message rather than this. A `floating` editor keeps its own row at the tail of the list
 *  for as long as it is open, including after the item has been created — moving the
 *  textarea into the created row would replace the DOM node under the caret and drop
 *  focus mid-sentence, half a second after the person started typing. */
type EditingState = {
  /** Stable identity for the autosave entry and for the editor's DOM node. */
  key: string
  messageId: string
  body: string
  floating: boolean
  /** Mid-turn asked for before the item existed; carried into the create. */
  interrupt: boolean
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
    case 'blocked': {
      // Worded, not code-dumped. `Not safe right now: terminal_input_after_completion`
      // named the check that fired and answered none of what the reader needs, which
      // is what the readiness strip above the list now says continuously — this is the
      // same vocabulary so the refusal and the strip cannot disagree.
      const said = wordReasons(outcome.reasons)
      return outcome.protected
        ? `Cannot be sent: ${said}. No confirmation overrides this one.`
        : `Not safe right now: ${said}.`
    }
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

/** Autosave, said in the fewest words that still distinguish "your text is on the daemon"
 *  from "it is not". Silence is the one thing an autosaving field may not do: the whole
 *  reason this replaced a Save button is that people did not trust the drawer with their
 *  typing, and a field that never says anything earns exactly that. */
function draftStatusLabel(state: QueueDraftState | null): string {
  if (!state) return ''
  switch (state.status) {
    case 'saving':
      return 'saving…'
    case 'pending':
      return 'unsaved'
    case 'saved':
      return state.dirty ? 'unsaved' : 'saved'
    case 'empty':
      return 'empty'
    case 'error':
      return 'not saved'
    default:
      return ''
  }
}

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
    parts.push(
      row.reply_window.kind === 'land'
        ? 'land in flight'
        : row.reply_window.kind === 'watch'
          ? 'watch armed'
          : 'reply window open',
    )
  if (status.quiet_hours.active) parts.push('quiet hours — paused')
  return `on · ${parts.join(' · ')}`
}

/** Targets are registered prompt-delivery harnesses only; a shell would execute a paste. */
const isAgentSession = (session: Session | null): boolean =>
  !!session && deliversHarnessPrompts(session.backend)

/** Terminal-state items are audit, not work: collapsed by default in the working view. */
const isDoneState = (state: QueueMessage['state']): boolean =>
  state === 'sent' || state === 'failed' || state === 'cancelled'

/** Grow the field to its content instead of making the reader drag a resize handle over a
 *  message they are still writing. The ceiling is CSS (`max-height`), so the clamp holds on
 *  a phone with the keyboard up without this having to measure the visual viewport. */
function autoGrow(field: HTMLTextAreaElement | null): void {
  if (!field) return
  field.style.height = 'auto'
  field.style.height = `${field.scrollHeight}px`
}

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
  const [editing, setEditing] = useState<EditingState | null>(null)
  const [draftState, setDraftState] = useState<QueueDraftState | null>(null)
  const [retargetFor, setRetargetFor] = useState('')
  const [auto, setAuto] = useState<QueueAutoStatus | null>(null)
  const [autoOpen, setAutoOpen] = useState(false)
  const [showDone, setShowDone] = useState(false)
  const alive = useRef(true)
  const editorRef = useRef<HTMLTextAreaElement>(null)
  const editorSessionRef = useRef(sessionId)
  editorSessionRef.current = sessionId
  // Read from callbacks that outlive a render (the insert-target handle, teardown).
  const editingRef = useRef<EditingState | null>(editing)
  editingRef.current = editing
  const focusPending = useRef(false)

  const session = sessions.find(item => item.id === sessionId) || null
  const editorTargetLabel = session ? agentTargetName(session) : sessionId || 'No session'
  // An id with no session record is an ended target the daemon still holds a queue for
  // (the pop-out tab outliving its session); only a *live non-agent* is refused here.
  const targetable = !!sessionId && (isAgentSession(session) || !session)
  const live = view?.target.live ?? !!session

  /** The queue's text sink is whichever row is open for editing, and only while one is.
   *
   *  With no editor open there is no field to write into, so the handle reports itself
   *  detached and `chooseInsertTarget` falls back to the focused terminal rather than
   *  silently swallowing a dictation into a pane with nowhere to put it. */
  const editorHandle = useMemo<EditorHandle>(() => {
    const targetSessionId = sessionId
    return {
      get isConnected() {
        return editorSessionRef.current === targetSessionId
          && !!editingRef.current
          && (editorRef.current?.isConnected ?? false)
      },
      insertText: text => {
        if (editorSessionRef.current !== targetSessionId) return
        const current = editingRef.current
        if (!current) return
        const field = editorRef.current
        const rawStart = field?.selectionStart ?? current.body.length
        const start = Math.min(rawStart, current.body.length)
        const end = Math.min(Math.max(field?.selectionEnd ?? start, start), current.body.length)
        const body = `${current.body.slice(0, start)}${text}${current.body.slice(end)}`
        setEditing({ ...current, body })
        queueDraftSaver.edit(current.key, { body })
        requestAnimationFrame(() => {
          const caret = start + text.length
          editorRef.current?.setSelectionRange(caret, caret)
          autoGrow(editorRef.current)
        })
      },
    }
  }, [sessionId])
  const editorSurface = {
    id: `queue:${sessionId}`,
    kind: 'queue' as const,
    label: `Queue · ${editorTargetLabel}`,
  }
  useEffect(() => () => forgetEditorFocus(editorHandle), [editorHandle])
  useEffect(() => {
    if (document.activeElement === editorRef.current) noteEditorFocus(editorHandle, editorSurface)
  }, [sessionId, editorTargetLabel])

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

  // The autosave entry reports its own progress; the create in particular is where this
  // view learns the item's id, because nothing else in the pane performed that write.
  useEffect(() => queueDraftSaver.subscribe((key, state) => {
    if (editingRef.current?.key !== key) return
    setDraftState(state)
    if (state.messageId && state.messageId !== editingRef.current.messageId) {
      setEditing(current => (
        current && current.key === key ? { ...current, messageId: state.messageId } : current
      ))
      // The create is the one write this pane did not make itself, so nothing else would
      // fetch the row it produced — and until it does, the item has no state chip, no
      // pending count, and no id for the acts that name one.
      void refresh()
    }
  }), [refresh])

  // Retargeting drops per-message UI that named a message of the previous target: the
  // drawer's target changes under it every time focus moves to another pane.
  //
  // The open editor is *saved* rather than dropped. Moving focus to another pane is the
  // single most common way a half-written message used to disappear, and it is not a
  // decision to discard anything — so the last keystrokes are flushed on the way out and
  // the entry is retired only once the daemon has them.
  useEffect(() => () => {
    const key = editingRef.current?.key
    if (key) void queueDraftSaver.flush(key).then(() => queueDraftSaver.close(key))
  }, [sessionId])
  useEffect(() => {
    setEditing(null); setDraftState(null); setConfirmId(''); setDeleteConfirmId('')
    setMenuId(''); setRetargetFor(''); setError('')
  }, [sessionId])

  const messages = view?.messages ?? []
  const head = useMemo(() => queueHead(messages), [messages])
  const editingMessage = editing?.messageId
    ? messages.find(item => item.id === editing.messageId) ?? null
    : null
  const active = useMemo(
    () => messages.filter(item => !isDoneState(item.state)),
    [messages],
  )
  // A floating editor draws its own row, so the created item must not also draw one.
  const listed = editing?.floating && editing.messageId
    ? active.filter(item => item.id !== editing.messageId)
    : active
  const done = useMemo(() => messages.filter(item => isDoneState(item.state)), [messages])
  /** Nothing on screen but the compose control - not merely "no pending items", because a
   *  collapsed `N delivered or closed` disclosure is content and centring around it would
   *  put the button over the top of it. */
  const listEmpty = !listed.length && !editing?.floating && !done.length
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

  /** `revision` is overridable because an autosaved edit advances it and this view learns
   *  that from the save's own response, not from a fetch it has not made yet. Sending on
   *  the fetched revision after editing the row is a guaranteed `revision_conflict`. */
  const send = async (message: QueueMessage, confirm: boolean, revision = message.revision) => {
    setBusyId(message.id)
    setError('')
    const outcome = await sendQueueMessage(message.id, revision, {
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

  // ---------------------------------------------------------------- the editor

  /** Retire whatever editor is open, after the daemon has its text. */
  const retire = async (key: string) => {
    await queueDraftSaver.flush(key)
    queueDraftSaver.close(key)
  }

  const openEditor = (next: EditingState, revision: number, constraints: QueueConstraints | null) => {
    const previous = editingRef.current?.key
    if (previous === next.key) { editorRef.current?.focus(); return }
    if (previous) void retire(previous)
    queueDraftSaver.open(next.key, {
      sessionId,
      messageId: next.messageId,
      revision,
      body: next.body,
      constraints,
    })
    setEditing(next)
    setDraftState(queueDraftSaver.state(next.key))
    setMenuId('')
    setDeleteConfirmId('')
    focusPending.current = true
  }

  /** `+` — a blank queued item, in edit mode, at the tail of the queue.
   *
   *  It writes nothing until there is something to write: an empty draft is purely local,
   *  which is what makes pressing this cheap and what stops an abandoned one leaving a row
   *  behind. From the first non-empty keystroke it is an ordinary queued draft. */
  const startDraft = () => {
    if (editingRef.current?.floating) { editorRef.current?.focus(); return }
    openEditor(
      { key: `draft:${browserUuid()}`, messageId: '', body: '', floating: true, interrupt: false },
      0,
      null,
    )
  }

  /** Edit an item that already exists. The seed carries the revision the daemon reported
   *  with this fetch, so the first PATCH is not a guaranteed conflict. */
  const startEdit = (message: QueueMessage) => {
    openEditor(
      {
        key: `msg:${message.id}`,
        messageId: message.id,
        body: message.body,
        floating: false,
        interrupt: message.constraints?.delivery === 'now',
      },
      message.revision,
      message.constraints,
    )
  }

  const closeEditor = async () => {
    const current = editingRef.current
    if (!current) return
    setBusyId(current.messageId || 'draft')
    await retire(current.key)
    setBusyId('')
    forgetEditorFocus(editorHandle)
    setEditing(null)
    setDraftState(null)
    void refresh()
  }

  /** Drop a draft that was never persisted. Once autosave has created the item this is not
   *  reachable — the row's delete, with its confirmation, is. */
  const discardDraft = () => {
    const current = editingRef.current
    if (!current || current.messageId) return
    queueDraftSaver.close(current.key)
    forgetEditorFocus(editorHandle)
    setEditing(null)
    setDraftState(null)
  }

  const onEditorInput = (field: HTMLTextAreaElement) => {
    const current = editingRef.current
    if (!current) return
    const body = field.value
    autoGrow(field)
    setEditing({ ...current, body })
    queueDraftSaver.edit(current.key, { body })
  }

  /** Save, then arm — the "stage it armed" the composer's Ctrl+Enter used to be.
   *
   *  Arming is a separate write from the body, deliberately: the item has to exist before
   *  it can be armed, and flushing first is what stops an arm from authorizing the *previous*
   *  body while the newest keystrokes are still in the debounce. */
  const armFromEditor = async (force = false) => {
    const current = editingRef.current
    if (!current) return
    setBusyId(current.messageId || 'draft')
    const saved = await queueDraftSaver.flush(current.key)
    setBusyId('')
    const id = saved?.messageId || current.messageId
    if (!id) return
    const known = messages.find(item => item.id === id)
    const armed = force ? true : !(known?.state === 'armed')
    await run(id, () => armQueueMessage(id, armed))
  }

  /** Send from a row, flushing first when that row is the one being edited: delivering the
   *  body the daemon happens to be holding, rather than the one on screen, is the failure a
   *  debounce makes possible and an explicit Save used to rule out. The flush's own response
   *  carries the revision the send has to quote. */
  const sendFromRow = async (message: QueueMessage, isEditing: boolean, confirm: boolean) => {
    const key = editingRef.current?.key
    const saved = isEditing && key ? await queueDraftSaver.flush(key) : null
    await send(message, confirm, saved?.revision ?? message.revision)
  }

  /** The arm toggle, in the editor and out of it. */
  const armFromRow = async (message: QueueMessage | null, isEditing: boolean) => {
    if (isEditing) { await armFromEditor(); return }
    if (!message) return
    await run(message.id, () => armQueueMessage(message.id, message.state !== 'armed'))
  }

  /** Flip one pending message between mid-turn and wait-for-idle delivery.
   *
   *  The PATCH replaces the whole constraints object, so the schedule and expiry
   *  ride along - dropping them because the delivery mode changed would silently
   *  un-schedule the message. */
  const setMessageDelivery = (message: QueueMessage, interrupt: boolean) => {
    const { delivery: _delivery, ...rest } = message.constraints || {}
    const next: QueueConstraints = interrupt ? { ...rest, delivery: 'now' } : rest
    return scheduleQueueMessage(message.id, Object.keys(next).length ? next : null)
  }

  /** The same choice, whether the item exists yet or not. Before it exists the answer rides
   *  the create; after it exists it is a PATCH like any other. */
  const toggleInterrupt = async (message: QueueMessage | null, interrupt: boolean) => {
    const current = editingRef.current
    if (current) {
      setEditing({ ...current, interrupt })
      queueDraftSaver.edit(current.key, { constraints: interrupt ? { delivery: 'now' } : null })
    }
    if (message) await run(message.id, () => setMessageDelivery(message, interrupt))
  }

  /** Clear the schedule alone: delivery mode and expiry stay on the item. */
  const clearSchedule = (message: QueueMessage) => {
    const { not_before: _notBefore, ...rest } = message.constraints || {}
    return scheduleQueueMessage(message.id, Object.keys(rest).length ? rest : null)
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

  /** Delete, in one implementation and drawn once.
   *
   *  It used to be drawn twice — inline and again inside the tray — which put two copies of
   *  a destructive control on screen together the moment the tray was open, one of them a
   *  bare `×`. The inline end-cap is the one that survives, now a bin rather than a mark
   *  that also means "close": it is the copy that is always reachable without opening
   *  anything, which was the reason for having it inline in the first place.
   *
   *  Arm-then-confirm (one click marks, the second deletes), absent while the item is
   *  `delivering` — the one state the daemon will not accept a delete in — and, for a draft
   *  autosave has not created yet, a plain local discard, because there is nothing to
   *  confirm about text no one else has. */
  const deleteButton = (message: QueueMessage | null, busy: boolean) => {
    if (message?.state === 'delivering') return null
    if (!message) {
      if (!editing || editing.messageId) return null
      return (
        <button
          type="button"
          class="danger queue-item-delete"
          aria-label="Discard this draft"
          title="Discard this draft"
          onClick={discardDraft}
        >
          <TrashIcon />
        </button>
      )
    }
    const armed = deleteConfirmId === message.id
    return (
      <button
        type="button"
        class={`danger queue-item-delete${armed ? ' confirming' : ''}`}
        aria-label={armed ? 'Confirm deleting this message' : 'Delete this message'}
        title={armed ? 'Click again to delete permanently' : 'Delete this message'}
        disabled={busy}
        onClick={() => {
          if (!armed) {
            setDeleteConfirmId(message.id)
            return
          }
          setDeleteConfirmId('')
          if (editingRef.current?.messageId === message.id) {
            queueDraftSaver.close(editingRef.current.key)
            setEditing(null)
            setDraftState(null)
          }
          void run(message.id, () => deleteQueueMessage(message.id))
        }}
      >
        {armed ? 'Delete permanently' : <TrashIcon />}
      </button>
    )
  }

  /** The secondary acts, in a row that opens under the message rather than a floating
   *  menu: this column scrolls and can be as narrow as 300px, where a popover would need
   *  positioning, portalling, and an outside-click contract to do the same job.
   *
   *  Splitting them out is what makes the row fit at all. What stays inline is what is
   *  wanted often enough to resent a second click for, and what survives as a mark: send,
   *  arm, edit, copy, delete. The tray keeps the worded, rarer acts — reorder, cancel/skip,
   *  the delivery mode, the schedule presets — because each of those is a sentence, not a
   *  glyph. Nothing is drawn in both places: the delivery mode is a checkbox in the open
   *  editor, so the tray drops its worded copy while that editor is open. */
  const overflow = (message: QueueMessage, busy: boolean, isEditing: boolean) => {
    const pending = isPendingQueueState(message.state)
    const schedule = scheduleStatus(message)
    return (
      <div class="queue-item-actions queue-item-more">
        {pending && message.state !== 'delivering' && (
          <>
            <button type="button" title="Move earlier" disabled={busy} onClick={() => void run(message.id, () => moveMessage(message, -1))}>↑</button>
            <button type="button" title="Move later" disabled={busy} onClick={() => void run(message.id, () => moveMessage(message, 1))}>↓</button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(message.id, () => cancelQueueMessage(message.id, head?.id === message.id ? 'skipped' : 'cancelled'))}
            >
              {head?.id === message.id ? 'Skip' : 'Cancel'}
            </button>
            {!isEditing && (
              <button
                type="button"
                title={message.constraints?.delivery === 'now'
                  ? 'Stop asking for mid-turn delivery; wait for the agent to be idle'
                  : 'Ask for delivery into a turn that is already running, when it is safe'}
                disabled={busy}
                onClick={() => void run(message.id, () => setMessageDelivery(message, message.constraints?.delivery !== 'now'))}
              >
                {message.constraints?.delivery === 'now' ? 'Wait for idle' : 'Deliver mid-turn'}
              </button>
            )}
            {schedule === 'scheduled' ? (
              <button type="button" disabled={busy} onClick={() => void run(message.id, () => clearSchedule(message))}>
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
                      // Merged over the existing constraints: the PATCH replaces
                      // the whole object, and scheduling a message must not
                      // silently drop its delivery mode or expiry.
                      scheduleQueueMessage(message.id, {
                        ...(message.constraints || {}),
                        not_before: serverNow() + preset.seconds,
                      }),
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
      </div>
    )
  }

  const editorField = () => {
    if (!editing) return null
    const status = draftStatusLabel(draftState)
    return (
      <div class="queue-item-edit">
        <textarea
          ref={editorRef}
          class="queue-edit-field"
          value={editing.body}
          placeholder="Write the message for this agent…"
          aria-label="Queued message text"
          title="Saves as you type · Ctrl+Enter saves and arms · Esc closes the editor"
          onFocus={() => noteEditorFocus(editorHandle, editorSurface)}
          onInput={event => onEditorInput(event.currentTarget)}
          // The gesture this whole surface exists for: leaving the field is a save, so
          // the drawer can be swiped shut in the next frame without losing anything.
          onBlur={() => { void queueDraftSaver.flush(editing.key) }}
          onKeyDown={event => {
            if (event.key === 'Escape') {
              event.preventDefault()
              void closeEditor()
              return
            }
            if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return
            event.preventDefault()
            void armFromEditor(true)
          }}
        />
        {status && (
          <span class={`queue-edit-status queue-edit-${draftState?.status ?? 'idle'}`} role="status">
            {status}
          </span>
        )}
        {draftState?.error && <p class="queue-edit-error">{draftState.error}</p>}
      </div>
    )
  }

  /** One action row, editing or not, so opening the editor never hides the controls the
   *  decision is made with. Losing sight of the arm toggle and the delivery mode behind a
   *  Save/Cancel pair was the reason staging an armed mid-turn message took four steps. */
  const actionsRow = (message: QueueMessage | null, busy: boolean, isEditing: boolean) => {
    const pending = !!message && isPendingQueueState(message.state)
    const settled = !!message && message.state !== 'delivering'
    const isHead = !!message && head?.id === message.id
    // Between the create and the next fetch the item exists and this view has not seen it
    // yet; nothing that names an id may act in that window.
    const detached = isEditing && !!editing?.messageId && !message
    const midTurn = message ? message.constraints?.delivery === 'now' : !!editing?.interrupt
    const canStage = isEditing && !detached && (!!message || !!editing?.body.trim())
    return (
      <div class={`queue-item-actions${isEditing ? ' queue-edit-actions' : ''}`}>
        {isEditing && (
          <label
            class="queue-edit-interrupt"
            title="Ask for this message to land in a turn that is already running, when the readiness tracker says it is safe. Off, it waits for the agent to be idle like every message before the mode existed."
          >
            <input
              type="checkbox"
              checked={midTurn}
              disabled={busy || detached}
              onChange={event => void toggleInterrupt(message, event.currentTarget.checked)}
            />
            <span>Mid-turn</span>
          </label>
        )}
        {(pending && settled) || (isEditing && !message) ? (
          <>
            {isHead && message &&
              (confirmId === message.id ? (
                <button
                  type="button"
                  class="queue-send queue-send-confirm"
                  disabled={busy}
                  onClick={() => void sendFromRow(message, isEditing, true)}
                >
                  Send anyway
                </button>
              ) : (
                // Never pre-labelled "Send anyway" on the advisory, and never
                // disabled by it. The confirm is always the second press against a
                // refusal the daemon issued *this instant*, so a stale reading can
                // neither smuggle a confirmation through nor take the override
                // away. All the advisory earns here is a hint that a confirmation
                // is coming; the strip above says why.
                <button
                  type="button"
                  class={`queue-send${sendHint ? ' queue-send-hinted' : ''}`}
                  title={sendHint}
                  disabled={busy}
                  onClick={() => void sendFromRow(message, isEditing, false)}
                >
                  Send now
                </button>
              ))}
            <button
              type="button"
              class={message?.state === 'armed' ? 'queue-armed' : ''}
              title={isEditing && !message ? 'Save this draft and arm it (Ctrl+Enter)' : undefined}
              disabled={busy || (isEditing && !canStage)}
              onClick={() => void armFromRow(message, isEditing)}
            >
              {message?.state === 'armed' ? 'Unarm' : 'Arm'}
            </button>
          </>
        ) : null}
        {message?.state === 'stranded' &&
          (retargetFor === message.id ? (
            <Dropdown
              value=""
              disabled={busy}
              ariaLabel="Retarget this message"
              onChange={target => {
                setRetargetFor('')
                if (target) void run(message.id, () => retargetQueueMessage(message.id, target))
              }}
              options={[
                { value: '', label: 'Retarget to…' },
                ...liveAgents.map(item => ({ value: item.id, label: agentTargetName(item) })),
              ]}
            />
          ) : (
            <button type="button" disabled={busy || !liveAgents.length} onClick={() => setRetargetFor(message.id)}>
              Retarget
            </button>
          ))}
        <span class="queue-item-spacer" />
        {/* The marks travel together. At the drawer's 300px floor the strip does not fit
            beside "Send now" and "Arm", and letting it wrap control by control leaves a
            lone red bin on a line of its own — so it wraps as one right-aligned group. */}
        <span class="queue-item-marks">
          {!isEditing && message && pending && settled && (
            <button
              type="button"
              class="queue-item-icon"
              aria-label="Edit this message"
              title="Edit this message"
              disabled={busy}
              onClick={() => startEdit(message)}
            >
              <RenameIcon />
            </button>
          )}
          {!isEditing && message && (
            <button
              type="button"
              class="queue-item-icon"
              aria-label="Copy this message"
              title="Copy this message"
              disabled={busy}
              onClick={() => copyBody(message)}
            >
              <CopyIcon />
            </button>
          )}
          {message && (
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
          )}
          {/* Last, and after the overflow rather than before it: a destructive control
              does not sit where a distracted hand aims for the menu. */}
          {deleteButton(message, busy)}
        </span>
        {isEditing && (
          <button
            type="button"
            class="queue-edit-done"
            title="Close the editor (Esc). Your text is already saved."
            disabled={busy}
            onClick={() => void closeEditor()}
          >
            Done
          </button>
        )}
      </div>
    )
  }

  const itemMeta = (message: QueueMessage) => {
    const pending = isPendingQueueState(message.state)
    const isHead = head?.id === message.id
    const schedule = scheduleStatus(message)
    const from = senderLabel(message)
    return (
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
        {pending && message.constraints?.delivery === 'now' && (
          <span
            class="queue-item-schedule"
            title="Asks to land in a running turn when the interject predicate says it is safe; otherwise it waits like any other message"
          >
            mid-turn
          </span>
        )}
        {message.blocked_reasons?.length ? (
          <span class="queue-item-reasons" title={message.blocked_reasons.join(', ')}>
            {wordReasons(message.blocked_reasons)}
          </span>
        ) : null}
        {message.stranded_reason && (
          <span class="queue-item-reasons">{message.stranded_reason}</span>
        )}
      </div>
    )
  }

  const row = (message: QueueMessage) => {
    const isHead = head?.id === message.id
    const busy = busyId === message.id
    const isEditing = editing?.messageId === message.id && !editing.floating
    const pending = isPendingQueueState(message.state)
    return (
      <li
        key={message.id}
        class={`queue-item queue-item-${message.state}${isHead ? ' queue-item-head' : ''}${isEditing ? ' queue-item-editing' : ''}`}
      >
        {itemMeta(message)}
        {isEditing ? editorField() : (
          <pre
            class={`queue-item-body${message.state === 'sent' ? ' queue-item-sent' : ''}${pending && message.state !== 'delivering' ? ' queue-item-editable' : ''}`}
            // Double-click, not single: this element is also the only place the message
            // can be selected from, and a single click would take the selection away.
            onDblClick={pending && message.state !== 'delivering' ? () => startEdit(message) : undefined}
          >
            {message.body}
          </pre>
        )}
        {actionsRow(message, busy, isEditing)}
        {menuId === message.id && overflow(message, busy, isEditing)}
      </li>
    )
  }

  /** The `+` draft's own row, at the tail of the queue where the item will sit. */
  const draftRow = () => {
    if (!editing?.floating) return null
    const message = editingMessage
    const busy = busyId === (editing.messageId || 'draft')
    // `queue-item-new`, not `queue-item-draft`: the latter is already the *state* class
    // every row in the `draft` state carries, and reusing it dashed all of their borders.
    return (
      <li key={editing.key} class="queue-item queue-item-new queue-item-editing">
        {message ? itemMeta(message) : (
          <div class="queue-item-meta">
            <span class="queue-state queue-state-draft">draft</span>
            <span class="queue-item-reasons">not saved until you type</span>
          </div>
        )}
        {editorField()}
        {actionsRow(message, busy, true)}
        {message && menuId === message.id && overflow(message, busy, true)}
      </li>
    )
  }

  // Focus the field the moment its row exists, and size it to whatever it was seeded with.
  useEffect(() => {
    if (!editing || !focusPending.current) return
    focusPending.current = false
    const field = editorRef.current
    if (!field) return
    autoGrow(field)
    field.focus()
    const caret = field.value.length
    field.setSelectionRange(caret, caret)
  }, [editing?.key])

  // A chip or command can open an already-selected Queue tab; the token still earns a draft.
  //
  // Never on a device whose only keyboard is an on-screen one. There, focusing a field is
  // not a convenience but a layout change: the keyboard rises over most of the drawer, so
  // opening the Queue to *read* it — which is what the tab is opened for far more often
  // than composing — arrives with the list already covered and a dismissal to perform.
  // The desktop caret costs nothing and is kept. `hasSoftKeyboard` rather than the mobile
  // breakpoint, deliberately: a narrowed desktop window has a real keyboard and a landscape
  // tablet does not.
  useEffect(() => {
    if (!openRequestToken || hasSoftKeyboard()) return
    if (!targetable || !live) return
    startDraft()
  }, [openRequestToken])

  const targetLabel = session ? agentTargetName(session) : view?.target.label || sessionId
  // Two readings of the same fact, and neither is reliably the newer one: the row's
  // is kept live by the readiness stream but only for sessions the daemon is
  // following, while the target view's arrived with this pane's own fetch. Taking
  // the freshest is also what makes opening the tab lag-free — the row's copy paints
  // on mount from memory, and the fetch that was already happening corrects it.
  const readiness = freshestReadiness(session?.delivery_readiness, view?.target.delivery_readiness)
  const verdict = describeReadiness(readiness)
  const sendHint = !verdict || verdict.state === 'safe'
    ? ''
    : verdict.protected
      ? `This will be refused: ${verdict.summary}.`
      : `This will ask you to confirm: ${verdict.summary}.`
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
      {/* Whether this target can take a message right now, and why not.
         *
         * Permanently on screen rather than behind a disclosure, because the
         * question it answers is the one the pane is opened to decide, and the
         * alternative — learning the answer by pressing Send and reading a refusal
         * naming a check like `terminal_input_after_completion` — is how the
         * confirmation that exists to stop a genuinely unsafe send became a thing
         * to click through. Advisory only: the daemon re-evaluates at send and its
         * verdict is the one that counts, which is also why nothing here disables
         * the Send button. A stale advisory that removes the operator's only
         * override would be a false block with no way out, which is strictly worse
         * than the wrong label. */}
      {sessionTargeted && verdict && (
        <div class={`queue-readiness queue-readiness-${verdict.state}`}>
          <div class="queue-readiness-line">
            <span class="queue-readiness-headline">{verdict.headline}</span>
            {verdict.summary && <span class="queue-readiness-summary">{verdict.summary}</span>}
            {/* Shown only once it is old enough to matter. The stream holds this at
                zero for a followed session, so a visible age is itself the signal
                that this reading is a poll result rather than a live one. */}
            {readinessAgeLabel(verdict) && (
              <span class="queue-readiness-age" title="When the daemon last read this">
                {readinessAgeLabel(verdict)}
              </span>
            )}
          </div>
          {verdict.state !== 'safe' && verdict.clears && (
            <p class="queue-readiness-clears">{verdict.clears}</p>
          )}
          {/* Narration, never evidence. The composer estimate is deliberately not an
              input to readiness (an estimate that concluded "empty" could authorize a
              send on top of text nothing can see), but it is the one thing that makes
              this particular block stop looking like a bug: the line really is clear,
              and the block really does persist. */}
          {verdict.state === 'blocked'
            && readiness?.reasons?.includes('terminal_input_after_completion')
            && !session?.unsent_input && (
              <p class="queue-readiness-clears">Nothing is sitting in the composer now.</p>
            )}
          {verdict.protected && (
            <p class="queue-readiness-protected">
              “Send anyway” will not be offered for this one — writing into it would
              answer a prompt or write to a target that is gone.
            </p>
          )}
          {verdict.also.length > 0 && (
            <p class="queue-readiness-also">Also: {verdict.also.join('; ')}.</p>
          )}
          {/* A mid-turn message is decided by a second, strictly narrower predicate,
              so "this agent is mid-turn" is not the end of the story for one. */}
          {verdict.state === 'blocked' && readiness?.interject_state === 'safe' && (
            <p class="queue-readiness-also">
              A message marked “mid-turn” can still be written into the running turn.
            </p>
          )}
        </div>
      )}
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
              ) : policy.reply_window.kind === 'watch' ? (
                <>
                  Held open by a settle watch: this session asked to be told when{' '}
                  {policy.reply_window.target_name || 'another session'} stops
                  working, and the notice has not matured yet, so the idle window is
                  not closing its grant.
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
            <CompactGrantFlag id="queue.autoDelivery"
              heading="Auto-delivery is off."
              consequence="Armed messages wait for manual Send now."
              onGranted={() => void run('auto', async () => setAuto(await fetchAutoStatus()))}/>
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

      {/* The compose control belongs to the list, not to the panel's chrome.
         *
         * As a pinned footer it sat at the bottom of the tab whatever the list did, so a
         * queue with two items in it drew a button a screen away from the last one and an
         * *empty* queue drew it under a paragraph with nothing else on screen at all. It
         * is the last row instead: after the final item when the list is short, and
         * centred with its explanation when there is nothing queued, which is the one
         * state where the button is the whole content of the tab. */}
      <ul class={`queue-list${listEmpty ? ' queue-list-empty' : ''}`}>
        {listed.map(row)}
        {draftRow()}
        {!targetable && (
          <li class="queue-empty">
            Focus an agent session to stage messages for it. Shells are never targets:
            a paste there would execute.
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
        {targetable && live && (
          <li class={`queue-compose${listEmpty ? ' queue-compose-alone' : ''}`}>
            <button
              type="button"
              class="queue-new"
              title="Add a queued message. It opens for editing and saves as you type."
              onClick={startDraft}
            >
              <PlusIcon />
              <span>New message</span>
            </button>
            {/* Said beside the button rather than instead of it: with the control right
                there, "press New message" was a sentence explaining a thing already on
                screen, and the part worth keeping is the queue's contract. */}
            {listEmpty && (
              <p class="queue-empty-note">
                Nothing queued. A message saves as you type and waits for your explicit
                “Send now” — nothing is ever delivered on a timer.
              </p>
            )}
          </li>
        )}
      </ul>
    </div>
  )
}
