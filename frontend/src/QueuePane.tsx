import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
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
      return t("QueuePane.4d0f820550")
    case 'blocked': {
      // Worded, not code-dumped. `Not safe right now: terminal_input_after_completion`
      // named the check that fired and answered none of what the reader needs, which
      // is what the readiness strip above the list now says continuously — this is the
      // same vocabulary so the refusal and the strip cannot disagree.
      const said = wordReasons(outcome.reasons)
      return outcome.protected
        ? t("QueuePane.856b0a9aa8", { value1: said })
        : t("QueuePane.50e3e8391c", { value1: said })
    }
    case 'stranded':
      return outcome.error
    case 'revision_conflict':
      return t("QueuePane.f50bfa553f")
    case 'not_due':
      return t("QueuePane.48d8c7c8c1", { value1: new Date(outcome.notBefore * 1000).toLocaleString(uiLocale.value) })
    case 'expired':
      return outcome.error
    case 'error':
      return outcome.error
  }
}

/** Presets for "send later"; the daemon resolves the delay to an absolute time. */
const DELAY_PRESETS: { label: string; seconds: number }[] = [
  { get label() { return t("QueuePane.d28629e18d") }, seconds: 300 },
  { get label() { return t("QueuePane.d2cd9daf2e") }, seconds: 900 },
  { get label() { return t("QueuePane.4e9e1f6011") }, seconds: 3600 },
]

/** Autosave, said in the fewest words that still distinguish "your text is on the daemon"
 *  from "it is not". Silence is the one thing an autosaving field may not do: the whole
 *  reason this replaced a Save button is that people did not trust the drawer with their
 *  typing, and a field that never says anything earns exactly that. */
function draftStatusLabel(state: QueueDraftState | null): string {
  if (!state) return ''
  switch (state.status) {
    case 'saving':
      return t("QueuePane.945d2c0350")
    case 'pending':
      return t("QueuePane.9c80e8331a")
    case 'saved':
      return state.dirty ? t("QueuePane.9c80e8331a") : t("QueuePane.d81c55f49c")
    case 'empty':
      return t("QueuePane.2e1cfa82b0")
    case 'error':
      return t("QueuePane.5539672827")
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
  if (lapse.idle_seconds !== null) parts.push(t("QueuePane.9e8dd48d9f", { value1: Math.round(lapse.idle_seconds / 60) }))
  if (lapse.window_minutes !== null) parts.push(t("QueuePane.2c3e127a0b", { value1: Math.round(lapse.window_minutes) }))
  if (lapse.pending) parts.push(`${lapse.pending} waiting`)
  return parts.length ? ` (${parts.join(', ')})` : ''
}

function describeAuto(status: QueueAutoStatus | null, sessionId: string): string {
  if (!status) return '…'
  if (!status.master_enabled) return t("QueuePane.b57f160c0f")
  if (status.paused) return t("QueuePane.358a4d279a")
  if (!sessionId) return t("QueuePane.23dbd34e88")
  const row = status.sessions.find(item => item.session_id === sessionId)
  if (!row?.enabled) {
    return row?.disabled_reason ? t("QueuePane.846f1fe30a", { value1: row.disabled_reason, value2: describeLapse(row.lapse) }) : t("QueuePane.b4dc66dde8")
  }
  const minutes = row.expires_in_s === null ? null : Math.max(0, Math.round(row.expires_in_s / 60))
  const parts = [t("QueuePane.1005c959d6", { value1: row.sends_remaining, value2: (row.sends_remaining === 1) ? 'yes' : 'no' })]
  if (minutes !== null) parts.push(t("QueuePane.1f96d599e4", { value1: minutes }))
  // Why the grant is still here rather than lapsed: this conversation is owed an
  // answer — by a peer it messaged, or by the land queue it asked to land a branch.
  if (row.reply_window)
    parts.push(
      row.reply_window.kind === 'land'
        ? t("QueuePane.cc74fa0bd6")
        : row.reply_window.kind === 'watch'
          ? t("QueuePane.0d31e8c699")
          : t("QueuePane.32371990bf"),
    )
  if (status.quiet_hours.active) parts.push(t("QueuePane.13ed902f40"))
  return t("QueuePane.011d7823b6", { value1: parts.join(' · ') })
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
  const editorTargetLabel = session ? agentTargetName(session) : sessionId || t("QueuePane.64f06c9698")
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
  }, [sessionId, uiLocale.value, catalogRevision.value])
  const editorSurface = {
    id: `queue:${sessionId}`,
    kind: 'queue' as const,
    get label() { return t("QueuePane.ad1a2d921d", { editorTargetLabel: editorTargetLabel }) },
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
  const head = useMemo(() => queueHead(messages), [messages, uiLocale.value, catalogRevision.value])
  const editingMessage = editing?.messageId
    ? messages.find(item => item.id === editing.messageId) ?? null
    : null
  const active = useMemo(
    () => messages.filter(item => !isDoneState(item.state)), [messages, uiLocale.value, catalogRevision.value])
  // A floating editor draws its own row, so the created item must not also draw one.
  const listed = editing?.floating && editing.messageId
    ? active.filter(item => item.id !== editing.messageId)
    : active
  const done = useMemo(() => messages.filter(item => isDoneState(item.state)), [messages, uiLocale.value, catalogRevision.value])
  /** Nothing on screen but the compose control - not merely "no pending items", because a
   *  collapsed `N delivered or closed` disclosure is content and centring around it would
   *  put the button over the top of it. */
  const listEmpty = !listed.length && !editing?.floating && !done.length
  const liveAgents = useMemo(
    () => agentTargets(sessions, session?.project_id ?? '').filter(item => item.id !== sessionId), [sessions, session, sessionId, uiLocale.value, catalogRevision.value])

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
          aria-label={t("QueuePane.76e243c4e8")}
          title={t("QueuePane.76e243c4e8")}
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
        aria-label={armed ? t("QueuePane.3031f3bb96") : t("QueuePane.989ad86f28")}
        title={armed ? t("QueuePane.41e0bd0fbe") : t("QueuePane.989ad86f28")}
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
        {armed ? t("QueuePane.280b10c89c") : <TrashIcon />}
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
        {pending && message.state !== 'delivering' && (<>
            <button type="button" title={t("QueuePane.736612d4d0")} disabled={busy} onClick={() => void run(message.id, () => moveMessage(message, -1))}>↑</button>
            <button type="button" title={t("QueuePane.d6e856081e")} disabled={busy} onClick={() => void run(message.id, () => moveMessage(message, 1))}>↓</button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(message.id, () => cancelQueueMessage(message.id, head?.id === message.id ? 'skipped' : 'cancelled'))}
            >
              {head?.id === message.id ? t("QueuePane.28d03596d2") : t("QueuePane.19766ed6cc")}
            </button>
            {!isEditing && (<button
                type="button"
                title={message.constraints?.delivery === 'now' ? t("QueuePane.a624284888") : t("QueuePane.dc96a020ca")}
                disabled={busy}
                onClick={() => void run(message.id, () => setMessageDelivery(message, message.constraints?.delivery !== 'now'))}
              >
                {message.constraints?.delivery === 'now' ? t("QueuePane.7545a8a377") : t("QueuePane.90cc978f71")}
              </button>)}
            {schedule === 'scheduled' ? (<button type="button" disabled={busy} onClick={() => void run(message.id, () => clearSchedule(message))}>{t("QueuePane.83e854047d")}</button>) : (DELAY_PRESETS.map(preset => (
                <button
                  key={preset.label}
                  type="button"
                  class="queue-schedule-preset"
                  title={t("QueuePane.6d4cb6b0b6", { label: preset.label })}
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
              )))}
          </>)}
        {message.state === 'stranded' && (<button type="button" disabled={busy} onClick={() => void run(message.id, () => cancelQueueMessage(message.id, 'cancelled'))}>{t("QueuePane.19766ed6cc")}</button>)}
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
          placeholder={t("QueuePane.c3a5d000c9")}
          aria-label={t("QueuePane.a2d0c4fa53")}
          title={t("QueuePane.d6587c66fe")}
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
        {status && (<span class={`queue-edit-status queue-edit-${draftState?.status ?? 'idle'}`} role="status">
            {status}
          </span>)}
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
        {isEditing && (<label
            class="queue-edit-interrupt"
            title={t("QueuePane.a74defd9b5")}
          >
            <input
              type="checkbox"
              checked={midTurn}
              disabled={busy || detached}
              onChange={event => void toggleInterrupt(message, event.currentTarget.checked)}
            />
            <span>{t("QueuePane.3a56461823")}</span>
          </label>)}
        {(pending && settled) || (isEditing && !message) ? (<>
            {isHead && message && (confirmId === message.id ? (<button
                  type="button"
                  class="queue-send queue-send-confirm"
                  disabled={busy}
                  onClick={() => void sendFromRow(message, isEditing, true)}
                >{t("QueuePane.3bdd9831a4")}</button>) : (<button
                  type="button"
                  class={`queue-send${sendHint ? ' queue-send-hinted' : ''}`}
                  title={sendHint}
                  disabled={busy}
                  onClick={() => void sendFromRow(message, isEditing, false)}
                >{t("QueuePane.5880328783")}</button>))}
            <button
              type="button"
              class={message?.state === 'armed' ? 'queue-armed' : ''}
              title={isEditing && !message ? t("QueuePane.cc2a6867d8") : undefined}
              disabled={busy || (isEditing && !canStage)}
              onClick={() => void armFromRow(message, isEditing)}
            >
              {message?.state === 'armed' ? t("QueuePane.69c8264e75") : t("QueuePane.d5cb968264")}
            </button>
          </>) : null}
        {message?.state === 'stranded' && (retargetFor === message.id ? (<Dropdown
              value=""
              disabled={busy}
              ariaLabel={t("QueuePane.e9b29c3345")}
              onChange={target => {
                setRetargetFor('')
                if (target) void run(message.id, () => retargetQueueMessage(message.id, target))
              }}
              options={[
                { value: '', get label() { return t("QueuePane.5d156220b6") } },
                ...liveAgents.map(item => ({ value: item.id, label: agentTargetName(item) })),
              ]}
            />) : (<button type="button" disabled={busy || !liveAgents.length} onClick={() => setRetargetFor(message.id)}>{t("QueuePane.e6c0d040b8")}</button>))}
        <span class="queue-item-spacer" />
        {/* The marks travel together. At the drawer's 300px floor the strip does not fit
            beside "Send now" and "Arm", and letting it wrap control by control leaves a
            lone red bin on a line of its own — so it wraps as one right-aligned group. */}
        <span class="queue-item-marks">
          {!isEditing && message && pending && settled && (<button
              type="button"
              class="queue-item-icon"
              aria-label={t("QueuePane.03014809ab")}
              title={t("QueuePane.03014809ab")}
              disabled={busy}
              onClick={() => startEdit(message)}
            >
              <RenameIcon />
            </button>)}
          {!isEditing && message && (<button
              type="button"
              class="queue-item-icon"
              aria-label={t("QueuePane.e3a42b6c3b")}
              title={t("QueuePane.e3a42b6c3b")}
              disabled={busy}
              onClick={() => copyBody(message)}
            >
              <CopyIcon />
            </button>)}
          {message && (<button
              type="button"
              class={`queue-item-menu${menuId === message.id ? ' open' : ''}`}
              aria-expanded={menuId === message.id}
              aria-label={t("QueuePane.6c59d8038d")}
              title={t("QueuePane.f8d46c2570")}
              disabled={busy}
              onClick={() => {
                setDeleteConfirmId('')
                setMenuId(current => (current === message.id ? '' : message.id))
              }}
            >
              ⋯
            </button>)}
          {/* Last, and after the overflow rather than before it: a destructive control
              does not sit where a distracted hand aims for the menu. */}
          {deleteButton(message, busy)}
        </span>
        {isEditing && (<button
            type="button"
            class="queue-edit-done"
            title={t("QueuePane.84decadec7")}
            disabled={busy}
            onClick={() => void closeEditor()}
          >{t("QueuePane.11a6767d56")}</button>)}
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
        {isHead && <span class="queue-next-marker">{t("QueuePane.c6c1c9a9c8")}</span>}
        {from && (<span class="queue-item-sender" title={message.origin?.reason || undefined}>
            {from}
            {message.chain_depth > 1 ? t("QueuePane.5c33ed947d", { chain_depth: message.chain_depth }) : ''}
          </span>)}
        {schedule === 'scheduled' && message.constraints?.not_before && (<span class="queue-item-schedule">{rich("QueuePane.cabc4287ed", { value1: new Date(message.constraints.not_before * 1000).toLocaleTimeString(uiLocale.value) })}</span>)}
        {pending && message.constraints?.delivery === 'now' && (<span
            class="queue-item-schedule"
            title={t("QueuePane.b35bb2daa4")}
          >{t("QueuePane.a254c3daa3")}</span>)}
        {message.blocked_reasons?.length ? (<span class="queue-item-reasons" title={message.blocked_reasons.join(', ')}>
            {wordReasons(message.blocked_reasons)}
          </span>) : null}
        {message.stranded_reason && (<span class="queue-item-reasons">{message.stranded_reason}</span>)}
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
        {isEditing ? editorField() : (<pre
            class={`queue-item-body${message.state === 'sent' ? ' queue-item-sent' : ''}${pending && message.state !== 'delivering' ? ' queue-item-editable' : ''}`}
            // Double-click, not single: this element is also the only place the message
            // can be selected from, and a single click would take the selection away.
            onDblClick={pending && message.state !== 'delivering' ? () => startEdit(message) : undefined}
          >
            {message.body}
          </pre>)}
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
        {message ? itemMeta(message) : (<div class="queue-item-meta">
            <span class="queue-state queue-state-draft">{t("QueuePane.7743ce348d")}</span>
            <span class="queue-item-reasons">{t("QueuePane.b690df13a5")}</span>
          </div>)}
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
      ? t("QueuePane.1c65f8557d", { value1: verdict.summary })
      : t("QueuePane.fe59df8919", { value1: verdict.summary })
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
        <strong>{targetable ? targetLabel : t("QueuePane.52271b2288")}</strong>
        {targetable && (<span class="queue-pane-status">
            {live ? t("QueuePane.6187c64929", { value1: view?.pending ?? 0 }) : t("QueuePane.8d553d2d68")}
          </span>)}
        {onOpenFleetQueue && (<button
            type="button"
            class={`queue-fleet-open${fleetPending > 0 ? ' has-pending' : ''}`}
            title={t("QueuePane.beed3fdf66")}
            onClick={onOpenFleetQueue}
          >{rich("QueuePane.f43a3a268d", { value1: fleetPending > 0 ? t("QueuePane.50843318c9", { value1: fleetPending > 99 ? '99+' : fleetPending }) : '' })}</button>)}
        {targetable && onOpenAsTab && (<button
            type="button"
            class="queue-popout"
            title={t("QueuePane.fcb8385637")}
            aria-label={t("QueuePane.fcb8385637")}
            onClick={() => onOpenAsTab(sessionId)}
          >
            ↗
          </button>)}
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
      {sessionTargeted && verdict && (<div class={`queue-readiness queue-readiness-${verdict.state}`}>
          <div class="queue-readiness-line">
            <span class="queue-readiness-headline">{verdict.headline}</span>
            {verdict.summary && <span class="queue-readiness-summary">{verdict.summary}</span>}
            {/* Shown only once it is old enough to matter. The stream holds this at
                zero for a followed session, so a visible age is itself the signal
                that this reading is a poll result rather than a live one. */}
            {readinessAgeLabel(verdict) && (<span class="queue-readiness-age" title={t("QueuePane.c37ee26d66")}>
                {readinessAgeLabel(verdict)}
              </span>)}
          </div>
          {verdict.state !== 'safe' && verdict.clears && (<p class="queue-readiness-clears">{verdict.clears}</p>)}
          {/* Narration, never evidence. This reason now means "typed since the turn
              ended, and no pane has read the composer since": the estimate behind
              `unsent_input` can block a delivery but never lift one, so an estimate of
              nothing is what makes the block look wrong - and a pane drawing the
              composer is what actually clears it. */}
          {verdict.state === 'blocked'
            && readiness?.reasons?.includes('terminal_input_after_completion')
            && !session?.unsent_input && (<p class="queue-readiness-clears">{t("QueuePane.d180396165")}</p>)}
          {verdict.protected && (<p class="queue-readiness-protected">{t("QueuePane.9c0df3ed9a")}</p>)}
          {verdict.also.length > 0 && (<p class="queue-readiness-also">{rich("QueuePane.84608cecf1", { value1: verdict.also.join('; ') })}</p>)}
          {/* A mid-turn message is decided by a second, strictly narrower predicate,
              so "this agent is mid-turn" is not the end of the story for one. */}
          {verdict.state === 'blocked' && readiness?.interject_state === 'safe' && (<p class="queue-readiness-also">{t("QueuePane.69e7de319b")}</p>)}
        </div>)}
      <div class="queue-auto-strip">
        <button
          type="button"
          class={`queue-auto-summary${autoOn ? ' queue-auto-on' : ''}`}
          aria-expanded={autoOpen}
          title={t("QueuePane.559be70522")}
          onClick={() => setAutoOpen(value => !value)}
        >{rich("QueuePane.19bc9b1fb2", { value1: <span aria-hidden="true">{autoOpen ? '▾' : '▸'}</span>, value2: describeAuto(auto, sessionTargeted ? sessionId : '') })}</button>
      </div>
      {autoOpen && (<div class="queue-auto-detail">
          {sessionTargeted && (<>
              <label class="queue-auto-toggle">
                <input
                  type="checkbox"
                  checked={autoOn}
                  disabled={busyId === 'auto' || !auto?.master_enabled}
                  onChange={event => setPolicy({ enabled: event.currentTarget.checked })}
                />
                <span>{t("QueuePane.4e88db33e9")}</span>
              </label>
              <label class="queue-auto-toggle" title={t("QueuePane.7ffdd2dd6b")}>
                <input
                  type="checkbox"
                  checked={!!policy?.accept_agent_messages}
                  disabled={busyId === 'auto'}
                  onChange={event => setPolicy({ acceptAgentMessages: event.currentTarget.checked })}
                />
                <span>{t("QueuePane.547fc3d387")}</span>
              </label>
              {/* Independent of the two above: arming decides whether a peer's
                  message counts as authorized, auto-delivery decides who presses
                  send, and this decides whether send may happen while a turn is
                  still running. Only a peer whose Project was granted mid-turn
                  delivery can ask for it, and the readiness tracker still has to
                  agree the turn is really running. */}
              <label
                class="queue-auto-toggle"
                title={t("QueuePane.80ad6e22f4")}
              >
                <input
                  type="checkbox"
                  checked={!!policy?.accept_agent_interjections}
                  disabled={busyId === 'auto'}
                  onChange={event => setPolicy({ acceptAgentInterjections: event.currentTarget.checked })}
                />
                <span>{t("QueuePane.85c148c745")}</span>
              </label>
            </>)}
          {/* A lapse is the one "off" a reader cannot act on from the reason alone.
              The window is a value rather than a switch, so this is a link and not a
              gate: a gate can honestly offer "turn this on", never "pick a number". */}
          {sessionTargeted && policy?.lapse && (<p class="queue-auto-lapse">{rich("QueuePane.916f659105", { value1: policy.lapse.idle_seconds !== null ? t("QueuePane.2df8def6d8", { value1: Math.round(policy.lapse.idle_seconds / 60) }) : t("QueuePane.c05749bfbc"), value2: policy.lapse.window_minutes !== null && t("QueuePane.2ff113af25", { value1: Math.round(policy.lapse.window_minutes) }), value3: !!policy.lapse.pending && t("QueuePane.fa25363563", { pending: policy.lapse.pending }), value4: <SettingLink variant="link" target="queue.grantWindow">{t("QueuePane.850d678087")}</SettingLink> })}</p>)}
          {sessionTargeted && policy?.reply_window && (<p class="queue-auto-lapse">
              {policy.reply_window.kind === 'land' ? (<>{rich("QueuePane.a7b4c61782", { value1: policy.reply_window.branch || t("QueuePane.15e628b344") })}</>) : policy.reply_window.kind === 'watch' ? (<>{rich("QueuePane.0be4604e3b", { value1: policy.reply_window.target_name || t("QueuePane.d885d2d3c4") })}</>) : (<>{rich("QueuePane.eed769804e", { thread_messages_used: policy.reply_window.thread_messages_used, thread_messages_limit: policy.reply_window.thread_messages_limit })}</>)}
            </p>)}
          {auto && !auto.master_enabled && (<CompactGrantFlag id="queue.autoDelivery"
              heading={t("QueuePane.21b48d8f7b")}
              consequence={t("QueuePane.eb60ac7e09")}
              onGranted={() => void run('auto', async () => setAuto(await fetchAutoStatus()))}/>)}
          {/* The two install-wide brakes. They are not per-session, and they are here
              rather than in the fleet overlay because a stop reachable only by opening
              something is not reachable at the moment you want it. */}
          <div class="queue-auto-emergency">
            <button
              type="button"
              class={auto?.paused ? 'primary' : 'danger'}
              disabled={busyId === 'auto'}
              title={t("QueuePane.24460aaed7")}
              onClick={() => void run('auto', async () => setAuto(await setAutoPaused(!auto?.paused)))}
            >
              {auto?.paused ? t("QueuePane.2520e73720") : t("QueuePane.d4fa94833a")}
            </button>
            <button
              type="button"
              disabled={busyId === 'auto'}
              title={t("QueuePane.e56deccbbf")}
              onClick={() => void run('auto', async () => {
                setAuto(await reportUnsafeDelivery(t("QueuePane.0aa3dff18c")))
                setError(t("QueuePane.9d27c6fbd8"))
              })}
            >{t("QueuePane.c755588ba5")}</button>
            {auto?.promotion && (<span class="queue-promotion">{rich("QueuePane.4beb1c328d", { auto_sends: auto.promotion.auto_sends, required_sends: auto.promotion.required_sends, proving_days: auto.promotion.proving_days, required_days: auto.promotion.required_days, unsafe_reports: auto.promotion.unsafe_reports })}</span>)}
          </div>
        </div>)}

      {error && (<p class="queue-pane-error" role="alert">
          {error}
        </p>)}

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
        {!targetable && (<li class="queue-empty">{t("QueuePane.7d086337f3")}</li>)}
        {done.length > 0 && (<li class="queue-done">
            <button type="button" aria-expanded={showDone} onClick={() => setShowDone(value => !value)}>{rich("QueuePane.29dcece0e9", { value1: <span aria-hidden="true">{showDone ? '▾' : '▸'}</span>, length: done.length })}</button>
          </li>)}
        {showDone && done.map(row)}
        {targetable && live && (<li class={`queue-compose${listEmpty ? ' queue-compose-alone' : ''}`}>
            <button
              type="button"
              class="queue-new"
              title={t("QueuePane.da0569490c")}
              onClick={startDraft}
            >
              <PlusIcon />
              <span>{t("QueuePane.78f5975a5d")}</span>
            </button>
            {/* Said beside the button rather than instead of it: with the control right
                there, "press New message" was a sentence explaining a thing already on
                screen, and the part worth keeping is the queue's contract. */}
            {listEmpty && (<p class="queue-empty-note">{t("QueuePane.a269959dae")}</p>)}
          </li>)}
      </ul>
    </div>
  )
}
