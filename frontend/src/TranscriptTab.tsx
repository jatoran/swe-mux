import { Fragment } from 'preact'
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'
import { agentTargetName } from './agentTargets'
import { api } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { copyPreparedText } from './terminalClipboard'
import {
  expandedTranscriptMessageIds,
  isPinnedToBottom,
  parseTranscriptExpansions,
  recallTranscriptScroll,
  rememberTranscriptScroll,
  serializeTranscriptExpansions,
  setTranscriptMessageExpanded,
  transcriptClamped,
  transcriptConversationText,
  transcriptEmptyMessage,
  transcriptMatchesQuery,
  transcriptSearchParts,
  transcriptSpeaker,
  transcriptTimestampIso,
  transcriptTimestampLabel,
  transcriptToolBoundaryLabel,
  TRANSCRIPT_CHANGED_EVENT,
  TRANSCRIPT_EXPANSION_KEY,
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

const readTranscriptExpansions = () => {
  try {
    return parseTranscriptExpansions(window.localStorage.getItem(TRANSCRIPT_EXPANSION_KEY))
  } catch {
    return []
  }
}

const writeTranscriptExpansions = (entries: ReturnType<typeof readTranscriptExpansions>) => {
  try {
    window.localStorage.setItem(TRANSCRIPT_EXPANSION_KEY, serializeTranscriptExpansions(entries))
  } catch {
    // Storage can be unavailable in private or locked-down browser contexts.
    // The component state still preserves the choice for this mount.
  }
}

function Message({ message, copied, expanded, query, onCopy, onExpand }: {
  message: TranscriptMessage
  copied: boolean
  expanded: boolean
  query: string
  onCopy: (message: TranscriptMessage) => void
  onExpand: (messageId: string) => void
}) {
  const clamped = transcriptClamped(message.text) && !expanded && !query
  const stamp = transcriptTimestampLabel(message.ts)
  return <article class={`transcript-message ${message.role}`} data-message-ordinal={message.ordinal}>
    <div class="transcript-copy-anchor">
      <button
        class={copied ? 'transcript-copy copied' : 'transcript-copy'}
        aria-label={`Copy this ${transcriptSpeaker(message.role) === 'you' ? 'message' : 'reply'}`}
        title={`Copy this ${transcriptSpeaker(message.role) === 'you' ? 'message' : 'reply'}`}
        onClick={() => onCopy(message)}
      >{copied ? 'Copied' : 'Copy'}</button>
    </div>
    <header>
      <span class="transcript-speaker">{transcriptSpeaker(message.role)}</span>
      {stamp && <time dateTime={transcriptTimestampIso(message.ts)}>{stamp}</time>}
    </header>
    <p class={clamped ? 'clamped' : ''}>{query
      ? transcriptSearchParts(message.text, query).map((part, index) =>
        part.match ? <mark key={index}>{part.text}</mark> : part.text,
      )
      : message.text}</p>
    {transcriptClamped(message.text) && !query && <button class="transcript-expand" onClick={() => onExpand(message.message_id)}>
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
  const [expansion, setExpansion] = useState<{ scope: string; messageIds: string[] }>({ scope: '', messageIds: [] })
  const [search, setSearch] = useState<{ sessionId: string; value: string }>({ sessionId: '', value: '' })
  const body = useRef<HTMLDivElement>(null)
  const manualArea = useRef<HTMLTextAreaElement>(null)
  const searchOrigin = useRef<{ sessionId: string; scrollTop: number } | null>(null)
  // Refs rather than state: the scroll placement below runs in a layout effect and
  // must read the value this render was laid out with, not a queued update.
  const pinned = useRef(true)
  const placedFor = useRef('')
  const shown = useRef(0)
  const requestSequence = useRef(0)
  const query = search.sessionId === sessionId ? search.value : ''
  const normalizedQuery = query.trim()
  const transcriptRunId = runId || (data?.session_id === sessionId ? data.agent_run_id : '') || 'legacy'
  const expansionScope = JSON.stringify([sessionId, transcriptRunId])
  const expanded = expansion.scope === expansionScope ? expansion.messageIds : []

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
    setData(null); setUnseen(0); setError('')
    placedFor.current = ''
    shown.current = 0
    if (sessionId) void load(sessionId)
  }, [sessionId, runId])

  // Explicitly unfolded messages follow their session and agent run across drawer
  // unmounts and navigation. Search showing a full message never enters this path.
  useEffect(() => {
    if (!sessionId) { setExpansion({ scope: '', messageIds: [] }); return }
    const entries = readTranscriptExpansions()
    setExpansion({
      scope: expansionScope,
      messageIds: expandedTranscriptMessageIds(entries, sessionId, transcriptRunId),
    })
  }, [expansionScope])

  // The observer signals after it has consumed a user record, avoiding both timer
  // polling and the race where UserPromptSubmit arrives before the transcript write.
  // Turn end remains the assistant-message boundary.
  useEffect(() => {
    if (!sessionId) return
    const onTurnEnded = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId?: string }>).detail
      if (detail?.sessionId && detail.sessionId !== sessionId) return
      void load(sessionId)
    }
    window.addEventListener(TRANSCRIPT_CHANGED_EVENT, onTurnEnded)
    window.addEventListener(TURN_ENDED_EVENT, onTurnEnded)
    return () => {
      window.removeEventListener(TRANSCRIPT_CHANGED_EVENT, onTurnEnded)
      window.removeEventListener(TURN_ENDED_EVENT, onTurnEnded)
    }
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
    if (normalizedQuery) {
      if (arrived > 0) setUnseen(current => current + arrived)
      return
    }
    if (pinned.current) {
      element.scrollTop = element.scrollHeight
      setUnseen(0)
    } else if (arrived > 0) {
      setUnseen(current => current + arrived)
    }
  }, [data])

  // Search is an ephemeral lens over the conversation, not a new reading place.
  // Start each query at its first match and restore the normal scroll offset when
  // the query clears, without replacing the one-slot transcript scroll memory.
  useLayoutEffect(() => {
    const element = body.current
    if (!element) return
    if (searchOrigin.current && searchOrigin.current.sessionId !== sessionId) searchOrigin.current = null
    if (normalizedQuery) {
      if (!searchOrigin.current) searchOrigin.current = { sessionId, scrollTop: element.scrollTop }
      element.scrollTop = 0
      return
    }
    if (searchOrigin.current?.sessionId === sessionId) {
      element.scrollTop = searchOrigin.current.scrollTop
      searchOrigin.current = null
    }
  }, [normalizedQuery, sessionId])

  const onScroll = () => {
    const element = body.current
    if (!element || !sessionId) return
    if (normalizedQuery) return
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

  const toggleExpand = (messageId: string) => {
    const shouldExpand = !expanded.includes(messageId)
    const entries = setTranscriptMessageExpanded(
      readTranscriptExpansions(),
      sessionId,
      transcriptRunId,
      messageId,
      shouldExpand,
    )
    writeTranscriptExpansions(entries)
    setExpansion({
      scope: expansionScope,
      messageIds: expandedTranscriptMessageIds(entries, sessionId, transcriptRunId),
    })
  }

  const updateSearch = (value: string) => {
    // Capture before filtering shortens the DOM and the browser clamps scrollTop.
    // A layout effect is already too late to recover the original offset.
    if (!normalizedQuery && value.trim() && body.current) {
      searchOrigin.current = { sessionId, scrollTop: body.current.scrollTop }
    }
    setSearch({ sessionId, value })
  }

  if (!session) return <p class="drawer-empty">Focus a session to read its conversation.</p>

  const messages = data?.messages || []
  const visibleMessages = normalizedQuery
    ? messages.filter(message => transcriptMatchesQuery(message, normalizedQuery))
    : messages
  const stale = data?.observation_stale_since
  return <div class="transcript-tab">
    <div class="transcript-tab-head">
      <div class="transcript-tab-meta">
        <strong>{agentTargetName(session)}</strong>
        <span aria-live="polite">
          {loading && !data
            ? 'Reading transcript…'
            : normalizedQuery
              ? `${visibleMessages.length} of ${messages.length} messages`
              : `${messages.length} message${messages.length === 1 ? '' : 's'}`}
          {data && data.hidden > 0 ? ` · ${data.hidden} CLI record${data.hidden === 1 ? '' : 's'} hidden` : ''}
        </span>
      </div>
      <button
        class={copied === COPY_ALL ? 'copied' : ''}
        disabled={!messages.length}
        title="Copy the whole conversation, with speakers"
        onClick={() => void copy(transcriptConversationText(messages), COPY_ALL)}
      >{copied === COPY_ALL ? 'Copied' : 'Copy all'}</button>
      <div class="transcript-search">
        <input
          type="search"
          aria-label="Search transcript"
          placeholder="Search transcript"
          autocomplete="off"
          spellcheck={false}
          value={query}
          disabled={!messages.length}
          onInput={event => updateSearch(event.currentTarget.value)}
          onKeyDown={event => {
            if (event.key !== 'Escape' || !query) return
            event.stopPropagation()
            updateSearch('')
          }}
        />
        {query && <button
          type="button"
          aria-label="Clear transcript search"
          title="Clear search"
          onClick={() => updateSearch('')}
        >×</button>}
      </div>
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
        ? visibleMessages.length
          ? visibleMessages.map((message, index) => <Fragment key={message.message_id}>
            {/* Only in the unfiltered column. Under a search the neighbours are
                whatever matched, so "12 tool calls" between them would describe
                a gap the reader is not looking at. */}
            {!normalizedQuery && index > 0 && message.preceding_tool_calls > 0 && <p class="transcript-tool-boundary">
              {transcriptToolBoundaryLabel(message.preceding_tool_calls)}
            </p>}
            <Message
              message={message}
              copied={copied === message.ordinal}
              expanded={expanded.includes(message.message_id)}
              query={normalizedQuery}
              onCopy={item => void copy(item.text, item.ordinal)}
              onExpand={toggleExpand}
            />
          </Fragment>)
          : <p class="drawer-empty">No messages match “{normalizedQuery}”.</p>
        : !loading && <p class="drawer-empty">{transcriptEmptyMessage(data?.reason ?? null, session.backend)}</p>}
    </div>
    {!normalizedQuery && unseen > 0 && <button class="transcript-jump" onClick={jumpToLatest}>
      {unseen} new ↓
    </button>}
    <textarea ref={manualArea} class="transcript-manual" readOnly aria-hidden="true" tabIndex={-1} />
  </div>
}
