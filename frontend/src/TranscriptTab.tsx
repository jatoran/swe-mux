import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import { agentTargetName } from './agentTargets'
import { api } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { copyPreparedText } from './terminalClipboard'
import {
  isPinnedToBottom,
  recallTranscriptScroll,
  rememberTranscriptScroll,
  transcriptClamped,
  transcriptConversationText,
  transcriptEmptyMessage,
  transcriptSpeaker,
  TURN_ENDED_EVENT,
  type SessionTranscript,
  type TranscriptMessage,
} from './transcriptView'
import type { Session } from './types'

// The drawer's reader: what the focused session has said, in a column you can
// scroll and copy from without touching the terminal.
//
// Deliberately inert. It has no composer, no insert button and no send: every
// other session-scoped tab exists to put text *into* an agent, and mixing that
// into the one surface meant for reviewing what already happened is how a
// stray click becomes a message nobody meant to send. Copy is the only verb.

const COPIED_FLASH_MS = 1200
/** Copy-all's key in the flash state, where per-message keys are ordinals. */
const COPY_ALL = -1

const timeLabel = (value?: string): string => {
  if (!value) return ''
  const numeric = /^\d+(?:\.\d+)?$/.test(value) ? Number(value) : null
  const date = new Date(numeric === null ? value : numeric > 10_000_000_000 ? numeric : numeric * 1000)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

function Message({ message, copied, expanded, onCopy, onExpand }: {
  message: TranscriptMessage
  copied: boolean
  expanded: boolean
  onCopy: (message: TranscriptMessage) => void
  onExpand: (ordinal: number) => void
}) {
  const clamped = transcriptClamped(message.text) && !expanded
  const stamp = timeLabel(message.ts)
  return <article class={`transcript-message ${message.role}`} data-message-ordinal={message.ordinal}>
    <header>
      <span class="transcript-speaker">{transcriptSpeaker(message.role)}</span>
      {stamp && <time>{stamp}</time>}
      <button
        class={copied ? 'transcript-copy copied' : 'transcript-copy'}
        title={`Copy this ${transcriptSpeaker(message.role) === 'you' ? 'message' : 'reply'}`}
        onClick={() => onCopy(message)}
      >{copied ? 'Copied' : 'Copy'}</button>
    </header>
    <p class={clamped ? 'clamped' : ''}>{message.text}</p>
    {transcriptClamped(message.text) && <button class="transcript-expand" onClick={() => onExpand(message.ordinal)}>
      {expanded ? 'Show less' : 'Show more'}
    </button>}
  </article>
}

export function TranscriptTab({ session }: { session: Session | null }) {
  const sessionId = session?.id || ''
  const runId = session?.agent_run_id || ''
  const [data, setData] = useState<SessionTranscript | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [unseen, setUnseen] = useState(0)
  const [copied, setCopied] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<number[]>([])
  const body = useRef<HTMLDivElement>(null)
  const manualArea = useRef<HTMLTextAreaElement>(null)
  // Refs rather than state: the scroll placement below runs in a layout effect and
  // must read the value this render was laid out with, not a queued update.
  const pinned = useRef(true)
  const placedFor = useRef('')
  const shown = useRef(0)
  const requestSequence = useRef(0)

  const load = async (id: string) => {
    const sequence = ++requestSequence.current
    setLoading(true)
    try {
      const result = await api<SessionTranscript>('GET', `/api/sessions/${id}/transcript`)
      if (sequence !== requestSequence.current) return
      setData(result)
      setError('')
    } catch (cause) {
      if (sequence !== requestSequence.current) return
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      if (sequence === requestSequence.current) setLoading(false)
    }
  }

  // A rollover (`/clear`, `/new`) swaps the transcript under the same pane, so the
  // run id belongs in here beside the session id: without it the tab would keep
  // showing the retired conversation until something else forced a reload.
  useEffect(() => {
    setData(null); setUnseen(0); setExpanded([]); setError('')
    placedFor.current = ''
    shown.current = 0
    if (sessionId) void load(sessionId)
  }, [sessionId, runId])

  // Turn boundaries are the only moment the conversation gains a message, so they
  // are the refresh signal. Polling would re-read a whole transcript on a timer to
  // learn nothing for most of an agent's working minute.
  useEffect(() => {
    if (!sessionId) return
    const onTurnEnded = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId?: string }>).detail
      if (detail?.sessionId && detail.sessionId !== sessionId) return
      void load(sessionId)
    }
    window.addEventListener(TURN_ENDED_EVENT, onTurnEnded)
    return () => window.removeEventListener(TURN_ENDED_EVENT, onTurnEnded)
  }, [sessionId])

  // Where a load leaves the viewport. First sight of a session opens at its newest
  // message; returning to a session still focused restores where reading stopped
  // (the drawer unmounts this body on every tab switch, so that memory lives
  // outside the component). After that, only a reader already at the bottom is
  // carried along by new messages — otherwise the position holds and the arrival
  // is offered as a button, because yanking the column mid-sentence every time an
  // agent speaks is what makes a live log unreadable.
  useLayoutEffect(() => {
    const element = body.current
    if (!element || !data) return
    if (placedFor.current !== sessionId) {
      placedFor.current = sessionId
      const remembered = recallTranscriptScroll(sessionId)
      element.scrollTop = remembered === null ? element.scrollHeight : remembered
      pinned.current = isPinnedToBottom(element.scrollTop, element.scrollHeight, element.clientHeight)
      shown.current = data.messages.length
      setUnseen(0)
      return
    }
    const arrived = data.messages.length - shown.current
    shown.current = data.messages.length
    if (pinned.current) {
      element.scrollTop = element.scrollHeight
      setUnseen(0)
    } else if (arrived > 0) {
      setUnseen(current => current + arrived)
    }
  }, [data])

  const onScroll = () => {
    const element = body.current
    if (!element || !sessionId) return
    pinned.current = isPinnedToBottom(element.scrollTop, element.scrollHeight, element.clientHeight)
    rememberTranscriptScroll(sessionId, element.scrollTop)
    if (pinned.current) setUnseen(0)
  }

  const jumpToLatest = () => {
    const element = body.current
    if (!element) return
    element.scrollTop = element.scrollHeight
    pinned.current = true
    rememberTranscriptScroll(sessionId, element.scrollTop)
    setUnseen(0)
  }

  const copy = async (text: string, key: number) => {
    // The textarea is the synchronous fallback `copyPreparedText` selects when the
    // async clipboard is unavailable (an insecure-context phone), so it has to hold
    // the text before the call rather than after a failure.
    if (manualArea.current) manualArea.current.value = text
    // Not into the clipboard ring. Copy is this tab's primary verb and agent
    // replies run to kilobytes; capturing them would evict the short snippets the
    // ring exists to hand back. The suppression only has to span the synchronous
    // part of the copy, which is where both capture hooks fire.
    const ok = await withoutClipboardCapture(() => copyPreparedText(text, manualArea.current))
    if (!ok) { setError('The browser refused the copy. Select the text and copy it manually.'); return }
    setError('')
    setCopied(key)
    window.setTimeout(() => setCopied(current => (current === key ? null : current)), COPIED_FLASH_MS)
  }

  const toggleExpand = (ordinal: number) =>
    setExpanded(current => current.includes(ordinal) ? current.filter(item => item !== ordinal) : [...current, ordinal])

  if (!session) return <p class="drawer-empty">Focus a session to read its conversation.</p>

  const messages = data?.messages || []
  const stale = data?.observation_stale_since
  return <div class="transcript-tab">
    <div class="transcript-tab-head">
      <div>
        <strong>{agentTargetName(session)}</strong>
        <span>
          {loading && !data ? 'Reading transcript…' : `${messages.length} message${messages.length === 1 ? '' : 's'}`}
          {data && data.hidden > 0 ? ` · ${data.hidden} CLI record${data.hidden === 1 ? '' : 's'} hidden` : ''}
        </span>
      </div>
      <button
        class={copied === COPY_ALL ? 'copied' : ''}
        disabled={!messages.length}
        title="Copy the whole conversation, with speakers"
        onClick={() => void copy(transcriptConversationText(messages), COPY_ALL)}
      >{copied === COPY_ALL ? 'Copied' : 'Copy all'}</button>
    </div>
    {/* The observer can end up following a conversation that is no longer this
        PTY's. Everywhere else that shows up as odd telemetry; here it would be a
        stranger's words presented as yours, so it is said out loud. */}
    {stale ? <p class="transcript-note warn" role="alert">
      This pane's transcript link went stale {new Date(stale * 1000).toLocaleTimeString()}. What follows may belong to another conversation.
    </p> : null}
    {error && <p class="transcript-note error" role="alert">{error}</p>}
    {data?.truncated && <p class="transcript-note">Older messages are not loaded. The full conversation is in History.</p>}
    <div class="transcript-tab-body" ref={body} onScroll={onScroll}>
      {messages.length
        ? messages.map(message => <Message
          key={message.ordinal}
          message={message}
          copied={copied === message.ordinal}
          expanded={expanded.includes(message.ordinal)}
          onCopy={item => void copy(item.text, item.ordinal)}
          onExpand={toggleExpand}
        />)
        : !loading && <p class="drawer-empty">{transcriptEmptyMessage(data?.reason ?? null, session.backend)}</p>}
    </div>
    {unseen > 0 && <button class="transcript-jump" onClick={jumpToLatest}>
      {unseen} new ↓
    </button>}
    <textarea ref={manualArea} class="transcript-manual" readOnly aria-hidden="true" tabIndex={-1} />
  </div>
}
