import { Fragment, type VNode } from 'preact'
import { memo } from 'preact/compat'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'preact/hooks'
import { agentTargetName } from './agentTargets'
import { api } from './api'
import { withoutClipboardCapture } from './clipboardHistory'
import { useModalFocus } from './modalFocus'
import { CheckIcon, CopyIcon, SelectTextIcon } from './railIcons'
import { copyPreparedText } from './terminalClipboard'
import { TranscriptLoader } from './transcriptLoader'
import { TranscriptToolCalls } from './TranscriptToolCalls'
import {
  expandedTranscriptMessageIds,
  groupTranscriptMessages,
  isPinnedToBottom,
  parseTranscriptExpansions,
  readTranscriptCache,
  recallTranscriptAnchor,
  recallTranscriptScroll,
  rememberTranscriptScroll,
  serializeTranscriptExpansions,
  setTranscriptMessageExpanded,
  transcriptAbandonedLabel,
  transcriptArrivals,
  transcriptCacheKey,
  transcriptClamped,
  transcriptConversationText,
  transcriptEmptyMessage,
  transcriptIndexingLabel,
  transcriptLiveMessages,
  transcriptMatchesQuery,
  transcriptRenderCount,
  transcriptSearchParts,
  transcriptSelectedSlice,
  transcriptSelectionActive,
  transcriptSpeaker,
  transcriptTimestampIso,
  transcriptTimestampLabel,
  transcriptToolBoundaryLabel,
  writeTranscriptCache,
  TRANSCRIPT_APPENDED_EVENT,
  TRANSCRIPT_CHANGED_EVENT,
  TRANSCRIPT_EXPANSION_KEY,
  TRANSCRIPT_RENDER_STEP,
  TRANSCRIPT_RESYNC_EVENT,
  TURN_ENDED_EVENT,
  type SessionTranscript,
  type TranscriptGroup,
  type TranscriptMessage,
  type TranscriptScrollAnchor,
  type TranscriptToolCall,
} from './transcriptView'
import {
  indexTranscriptAudio, transcriptAudioClip, transcriptAudioHint, transcriptAudioMark,
  transcriptAudioState, TRANSCRIPT_AUDIO_KINDS,
} from './transcriptAudio'
import type { MessageAudio } from './transcriptAudio'
import type { Session, VoiceClip, VoiceContent } from './types'
import {
  beginRequestedStream, cancelRequestedStream, newVoiceStreamId, playClipGroup, unlockPlayback,
} from './voice'
import { clipParts } from './voiceGroups'

// The drawer's reader: what the focused session has said, in a column you can
// scroll and copy from without touching the terminal.
//
// Deliberately write-nothing. It has no composer, no insert button and no send:
// every other session-scoped tab exists to put text *into* an agent, and mixing
// that into the one surface meant for reviewing what already happened is how a
// stray click becomes a message nobody meant to send.
//
// The rule is about what a tap can reach, not about a count of buttons. Copy,
// select, and the per-message read-aloud markers all leave the conversation, the
// PTY, and the session exactly as they were; the one thing playback spends - a
// summary's model call - is named on the chip before it is pressed, and its
// verbatim twin spends nothing (`design/features/voice.md`).
//
// Reading is incremental (`design/features/transcript-branches.md`, "Reading"): the
// tab opens on the newest page, drawn at once from the last reading of this session
// if there is one, then asks the daemon only for what changed. Older pages load as
// the reader scrolls up into them, and only the trailing groups of what is loaded
// are drawn, growing as the reader scrolls.

const COPIED_FLASH_MS = 1200
/** Copy-all's key in the flash state, where per-message keys are ordinals. */
const COPY_ALL = -1
/** The select sheet's key in that same state, which its own button reads. */
const COPY_SELECTION = -2
/** Within this many pixels of the top, the column draws or loads what is above. */
const GROW_THRESHOLD_PX = 600
const EMPTY_MESSAGES: TranscriptMessage[] = []
const EMPTY_TOOLS: TranscriptToolCall[] = []

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

// Tool inputs read for one seam, kept for the life of the page. A seam's calls are
// fixed once the message after it exists, so a reopened disclosure, or the same
// session read again later, costs nothing. Bounded, oldest first.
const TOOL_INPUT_CACHE_ENTRIES = 200
const toolInputCache = new Map<string, Promise<TranscriptToolCall[]>>()

function seamToolInputs(sessionId: string, runId: string, messageId: string | null): Promise<TranscriptToolCall[]> {
  const query = messageId === null ? 'trailing=1' : `message_id=${encodeURIComponent(messageId)}`
  const request = () => api<{ calls: TranscriptToolCall[]; reason: string | null }>(
    'GET', `/api/sessions/${encodeURIComponent(sessionId)}/transcript/tool-calls?${query}`,
  ).then(result => {
    if (result.reason) throw new Error(result.reason)
    return result.calls
  })
  // The calls after the newest message are still being made: never cached.
  if (messageId === null) return request()
  const key = JSON.stringify([sessionId, runId, messageId])
  const hit = toolInputCache.get(key)
  if (hit) return hit
  const pending = request()
  toolInputCache.set(key, pending)
  pending.catch(() => toolInputCache.delete(key))
  while (toolInputCache.size > TOOL_INPUT_CACHE_ENTRIES) {
    const oldest = toolInputCache.keys().next().value
    if (oldest === undefined) break
    toolInputCache.delete(oldest)
  }
  return pending
}

/**
 * The reading column's answer to "copy part of this, not all of it".
 *
 * A flat `<textarea>` rather than the column itself, because selecting across the column
 * on a phone does not work and cannot be made to. The messages live in a nested
 * `overflow-y:auto` scroller, and once the anchor handle scrolls out of view Chrome
 * re-derives the selection's base from a screen coordinate that is no longer over the
 * anchor, so extending the other handle swallows every message above it. Nothing in this
 * app's DOM or CSS decides that; making the chrome unselectable only changed where the
 * runaway base landed. A text control is a different selection path entirely: offsets
 * into one buffer, with the control's own drag autoscroll, which is why every mobile
 * note app can do this and a scrolled column cannot.
 *
 * Read-only on purpose. This tab never writes, and a `readonly` control still selects and
 * scrolls while raising no soft keyboard on either mobile platform.
 */
function SelectSheet({ title, text, copied, onCopy, onClose }: {
  title: string
  text: string
  copied: boolean
  onCopy: (text: string) => void
  onClose: () => void
}) {
  const host = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLTextAreaElement>(null)
  // Close is first in the DOM, so the trap's opening focus lands there rather than in the
  // text: a focused text control on a phone is one stray keystroke from looking editable.
  useModalFocus(host, onClose, true, 'transcript-select')
  const copySelection = () => {
    const element = area.current
    if (!element) { onCopy(text); return }
    onCopy(transcriptSelectedSlice(element.value, element.selectionStart, element.selectionEnd))
  }
  return <div class="modal-layer transcript-select-layer">
    <div class="transcript-select-sheet" ref={host} role="dialog" aria-label={`Select text from ${title}`}>
      <header>
        <button type="button" aria-label="Close" title="Close" onClick={onClose}>×</button>
        <strong>{title}</strong>
      </header>
      <textarea ref={area} class="transcript-select-text" readOnly spellcheck={false} value={text} />
      <footer>
        <span>Drag to select, then copy. Nothing selected copies all of it.</span>
        <button type="button" class={copied ? 'copied' : ''} onClick={copySelection}>
          {copied ? 'Copied' : 'Copy selection'}
        </button>
      </footer>
    </div>
  </div>
}

/**
 * Read one reply aloud, from the reader, in either kind.
 *
 * Two chips rather than one button because the choice between a spoken summary and the
 * reply read out is exactly the choice this surface exists to offer, and the two cost
 * different things - a summary is a model call against the daily read-aloud budget, and
 * verbatim never touches a model. Each chip is its own marker: nothing yet, being made,
 * ready to play, or a failed attempt worth retrying.
 *
 * A ready chip *plays*, it does not regenerate. That is the whole point of anchoring a
 * clip to its message: automatic read-aloud and this button produce identical audio for
 * the same reply, so the second request is a lookup (`transcriptAudio.ts`).
 *
 * Rendered only for assistant messages, and only while read aloud is on. This is a
 * per-item surface repeated once per reply, so it carries no gate of its own - the one
 * gate for the master switch lives in the voice panel's `tts` tab
 * (`design/features/setting-links.md`).
 */
function MessageAudioChips({ audio, requested, onSpeak, onPlay }: {
  audio: MessageAudio | undefined
  requested: readonly VoiceContent[]
  onSpeak: (kind: VoiceContent) => void
  onPlay: (clip: VoiceClip) => void
}) {
  return <>{TRANSCRIPT_AUDIO_KINDS.map(kind => {
    const state = transcriptAudioState(audio, kind, requested.includes(kind))
    const clip = transcriptAudioClip(audio, kind)
    return <button
      key={kind}
      class={`transcript-copy transcript-audio ${state}`}
      disabled={state === 'generating'}
      aria-label={transcriptAudioHint(state, kind)}
      title={transcriptAudioHint(state, kind)}
      onClick={() => (clip ? onPlay(clip) : onSpeak(kind))}
    >{transcriptAudioMark(state)} {kind}</button>
  })}</>
}

/** What a row can do. One stable object per mounted tab, so a memoised row only
 *  re-renders when something about its own message changed. */
type MessageActions = Readonly<{
  onReveal: (ordinal: number) => void
  onCopy: (message: TranscriptMessage) => void
  onExpand: (messageId: string) => void
  onSelectText: (message: TranscriptMessage) => void
  onSpeak: (message: TranscriptMessage, kind: VoiceContent) => void
  onPlay: (clip: VoiceClip) => void
}>

const Message = memo(function Message({ message, copied, expanded, query, audio, audioRequests, revealed, actions }: {
  message: TranscriptMessage
  copied: boolean
  expanded: boolean
  query: string
  /** Clips that already speak this message, by kind; `undefined` while read aloud is off. */
  audio: MessageAudio | undefined
  /** Every `<messageId>:<kind>` this reader has asked for; `null` while read aloud is off. */
  audioRequests: readonly string[] | null
  /** Touch only: this is the message whose chip row a tap has opened. */
  revealed: boolean
  actions: MessageActions
}) {
  const clamped = transcriptClamped(message.text) && !expanded && !query
  const stamp = transcriptTimestampLabel(message.ts)
  const kind = transcriptSpeaker(message.role) === 'you' ? 'message' : 'reply'
  const chips = audioRequests !== null && message.role === 'assistant'
  const requested = chips
    ? TRANSCRIPT_AUDIO_KINDS.filter(item => audioRequests.includes(`${message.message_id}:${item}`))
    : []
  const classes = ['transcript-message', message.role]
  // The gutter the header keeps clear of the floating chip row is sized to what that row
  // actually holds, which is two icon buttons unless this reply also carries the audio pair.
  if (chips) classes.push('has-audio')
  if (revealed) classes.push('actions-open')
  return <article
    class={classes.join(' ')}
    data-message-ordinal={message.ordinal}
    data-message-id={message.message_id}
    onClick={() => actions.onReveal(message.ordinal)}
  >
    {/* Stops a press on a chip from closing the row it is in - the tap that opened this
        one is the same event the article listens for. Inert with a mouse, where hover
        governs and this handler never decides anything. */}
    <div class="transcript-copy-anchor" onClick={event => event.stopPropagation()}>
      <button
        class="transcript-copy transcript-copy-icon"
        aria-label={`Select text from this ${kind}`}
        title={`Select part of this ${kind}`}
        onClick={() => actions.onSelectText(message)}
      ><SelectTextIcon /></button>
      <button
        class={copied ? 'transcript-copy transcript-copy-icon copied' : 'transcript-copy transcript-copy-icon'}
        aria-label={copied ? `Copied this ${kind}` : `Copy this ${kind}`}
        title={copied ? 'Copied' : `Copy this ${kind}`}
        onClick={() => actions.onCopy(message)}
      >{copied ? <CheckIcon /> : <CopyIcon />}</button>
      {/* Replies only: read aloud speaks what the agent said, and a prompt is
          something the operator wrote. */}
      {chips && <MessageAudioChips
        audio={audio}
        requested={requested}
        onSpeak={item => actions.onSpeak(message, item)}
        onPlay={actions.onPlay}
      />}
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
    {transcriptClamped(message.text) && !query && <button class="transcript-expand" onClick={event => {
      // Unfolding a long reply is not a request for its chip row.
      event.stopPropagation()
      actions.onExpand(message.message_id)
    }}>
      {expanded ? 'Show less' : 'Show more'}
    </button>}
  </article>
})

/**
 * One run of abandoned messages, folded to a line the reader can open.
 *
 * Folded rather than removed, and named rather than left implicit. A conversation
 * retried through an outage holds the same prompt eight times, and a reader shown
 * eight of them cannot tell their transcript from a broken one - but a reader shown
 * none of them, after having watched themselves resend it, cannot either. The fold
 * says which of the two happened, and opens if they want to see it.
 *
 * Collapsed by default and not remembered between mounts: an abandoned branch is
 * something to check once, not a preference.
 */
function AbandonedRun({ groupKey, messages, open, onToggle, renderMessage }: {
  groupKey: string
  messages: TranscriptMessage[]
  open: boolean
  onToggle: () => void
  renderMessage: (message: TranscriptMessage) => VNode
}) {
  const label = transcriptAbandonedLabel(messages.length)
  return <section class={open ? 'transcript-abandoned open' : 'transcript-abandoned'} data-message-id={groupKey}>
    <button type="button" aria-expanded={open} onClick={onToggle}>
      <span class="transcript-abandoned-mark" aria-hidden="true">{open ? '▾' : '▸'}</span>
      {label}
    </button>
    {open && <div class="transcript-abandoned-body">
      {messages.map(message => <Fragment key={message.message_id}>{renderMessage(message)}</Fragment>)}
    </div>}
  </section>
}

const groupKey = (group: TranscriptGroup): string =>
  group.kind === 'abandoned' ? group.key : group.message.message_id

/** The anchored row at the top of the view, and how far into it the view sits.
 *
 * A binary search over the anchored rows in document order, which is top-to-bottom:
 * reading offsets is a layout read, and a long column has hundreds of rows. */
function topAnchor(element: HTMLElement, rendered: number): TranscriptScrollAnchor | null {
  const rows = element.querySelectorAll<HTMLElement>(':scope > [data-message-id], :scope > * > [data-message-id]')
  if (!rows.length) return null
  const top = element.scrollTop
  let low = 0
  let high = rows.length - 1
  while (low < high) {
    const middle = (low + high) >> 1
    const row = rows[middle]
    if (row.offsetTop + row.offsetHeight <= top) low = middle + 1
    else high = middle
  }
  const row = rows[low]
  const messageId = row.dataset.messageId
  return messageId ? { messageId, delta: top - row.offsetTop, rendered } : null
}

function anchoredRow(element: HTMLElement, messageId: string): HTMLElement | null {
  return element.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(messageId)}"]`)
}

export function TranscriptTab({ session, readAloud = false }: {
  session: Session | null
  /** `tts_enabled`. Off means the per-message markers are not drawn at all. */
  readAloud?: boolean
}) {
  const sessionId = session?.id || ''
  const runId = session?.agent_run_id || ''
  const nativeId = session?.native_session_id || ''
  const [data, setData] = useState<SessionTranscript | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [olderLoading, setOlderLoading] = useState(false)
  // Copy all, Select and a search over everything first walk back to the first message.
  const [wholePending, setWholePending] = useState(false)
  const [renderCount, setRenderCount] = useState(TRANSCRIPT_RENDER_STEP)
  const [unseen, setUnseen] = useState(0)
  const [copied, setCopied] = useState<number | null>(null)
  const [expansion, setExpansion] = useState<{ scope: string; messageIds: string[] }>({ scope: '', messageIds: [] })
  const [search, setSearch] = useState<{ sessionId: string; value: string }>({ sessionId: '', value: '' })
  const [selectionLive, setSelectionLive] = useState(false)
  const [sheet, setSheet] = useState<{ title: string; text: string } | null>(null)
  const [showToolCalls, setShowToolCalls] = useState(false)
  const [openBranches, setOpenBranches] = useState<string[]>([])
  // Touch only: the one message whose chip row a tap has opened. The controls are chrome
  // over the prose, so on a phone - where there is no hover to reveal them with and no
  // room to keep them out of the timestamp's way - they stay out of the column until the
  // reader asks for them, one entry at a time. A mouse ignores this entirely: the reveal
  // there is `:hover`, and this state changing under a click decides nothing.
  const [openActions, setOpenActions] = useState<number | null>(null)
  // Every clip of this run, indexed by the message it speaks. Fetched once per run and
  // refreshed on clip events rather than polled: `mux:voice-clip` is raised for every
  // ready or failed clip, which is exactly when this index can change.
  const [clips, setClips] = useState<VoiceClip[]>([])
  // What this reader has asked for and not yet seen land, keyed `<messageId>:<kind>`.
  // Local, because another device generating the same clip is not something this tab can
  // observe until the clip arrives - a shared "generating" would spin forever here.
  const [audioRequests, setAudioRequests] = useState<string[]>([])
  const body = useRef<HTMLDivElement>(null)
  const manualArea = useRef<HTMLTextAreaElement>(null)
  const searchOrigin = useRef<{ sessionId: string; scrollTop: number } | null>(null)
  const loaderRef = useRef<TranscriptLoader | null>(null)
  // Refs rather than state: the scroll placement below runs in a layout effect and
  // must read the value this render was laid out with, not a queued update.
  const pinned = useRef(true)
  const selecting = useRef(false)
  const placedFor = useRef('')
  // The newest message the column has accounted for, which is what "new" is counted from.
  const newest = useRef<string | null>(null)
  // The first drawn group at the last layout: a different one now means rows were added
  // above the reader, whose place `anchor` holds.
  const firstDrawn = useRef<string | null>(null)
  const anchor = useRef<TranscriptScrollAnchor | null>(null)
  // The oldest message when an older page last failed to arrive: automatic growth stops
  // there until something changes, so a failing read is not retried in a tight loop.
  const olderStalled = useRef<string | null>(null)
  const query = search.sessionId === sessionId ? search.value : ''
  const normalizedQuery = query.trim()
  const transcriptRunId = runId || (data?.session_id === sessionId ? data.agent_run_id : '') || 'legacy'
  const expansionScope = JSON.stringify([sessionId, transcriptRunId])
  const expanded = expansion.scope === expansionScope ? expansion.messageIds : []

  // A rollover (`/clear`, `/new`) swaps the transcript under the same pane, so the
  // run id belongs beside the session id. A fresh fork's initial binding changes
  // only the native id, and must replace its empty "no transcript" reading too.
  useEffect(() => {
    const key = transcriptCacheKey(sessionId, runId, nativeId)
    const cached = sessionId ? readTranscriptCache(key) : null
    setData(cached); setUnseen(0); setError('')
    setOpenBranches([])
    setOlderLoading(false); setWholePending(false)
    setRenderCount(sessionId ? recallTranscriptAnchor(sessionId)?.rendered ?? TRANSCRIPT_RENDER_STEP : TRANSCRIPT_RENDER_STEP)
    placedFor.current = ''
    newest.current = null
    firstDrawn.current = null
    anchor.current = null
    olderStalled.current = null
    selecting.current = false
    setSelectionLive(false)
    // The sheet holds a snapshot of text from the conversation being left behind, so it
    // closes with it rather than presenting one session's words over another's.
    setSheet(null)
    // Clips are anchored per run, so a rollover invalidates the index with the
    // transcript. In-flight requests go with it: their markers belong to messages that
    // are no longer on screen.
    setClips([]); setAudioRequests([])
    if (!sessionId) { setLoading(false); return }
    const loader = new TranscriptLoader(
      queryString => api<SessionTranscript>('GET', `/api/sessions/${encodeURIComponent(sessionId)}/transcript?${queryString}`),
      (reading, failure) => {
        if (reading) writeTranscriptCache(key, reading)
        setData(reading)
        setError(failure)
        setLoading(false)
      },
      cached,
    )
    loaderRef.current = loader
    // A cached reading is drawn at once; "Reading transcript…" is only for a first sight.
    setLoading(!cached)
    loader.refresh()
    return () => {
      loader.dispose()
      if (loaderRef.current === loader) loaderRef.current = null
    }
  }, [sessionId, runId, nativeId])

  // The run's audio. `limit` is the store's own ceiling: a conversation with more clips
  // than that has older ones no longer worth a marker, and the reader's window is
  // bounded well below it anyway.
  useEffect(() => {
    if (!readAloud || !sessionId) { setClips([]); return }
    let live = true
    const loadClips = () => api<{ items: VoiceClip[] }>(
      'GET',
      `/api/voice/clips?session=${encodeURIComponent(sessionId)}&limit=200`,
    ).then(result => { if (live) setClips(result.items) }).catch(() => { /* the reader still reads */ })
    void loadClips()
    const onClip = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId: string }>).detail
      if (detail?.sessionId && detail.sessionId !== sessionId) return
      void loadClips()
    }
    window.addEventListener('mux:voice-clip', onClip)
    return () => { live = false; window.removeEventListener('mux:voice-clip', onClip) }
  }, [sessionId, runId, readAloud])

  // A manual selection held over the column freezes the follow-scroll below.
  //
  // `selectionchange` is the only signal a native selection-handle drag produces: the
  // handles are browser chrome drawn outside the page, so their movement never arrives
  // as a pointer or touch event and there is nothing else to watch. It fires
  // continuously for the length of a drag, hence the flip check before touching state.
  useEffect(() => {
    const onSelectionChange = () => {
      const element = body.current
      const active = transcriptSelectionActive(document.getSelection(), element)
      if (active === selecting.current) return
      selecting.current = active
      // Releasing a selection re-reads where the column actually is. Messages that
      // arrived during the freeze can have pushed the bottom out of view without ever
      // firing a scroll event, and carrying the stale "still pinned" answer forward
      // would jump the reader on the next arrival instead of offering the pill.
      if (!active && element) {
        pinned.current = isPinnedToBottom(element.scrollTop, element.scrollHeight, element.clientHeight)
      }
      setSelectionLive(active)
    }
    document.addEventListener('selectionchange', onSelectionChange)
    return () => document.removeEventListener('selectionchange', onSelectionChange)
  }, [])

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

  // Every reason to look again is a trigger, and the loader turns any number of them
  // arriving together into one request. The observer signals after it has consumed a
  // user record (`transcript_message`), while the agent writes (`transcript_appended`,
  // throttled by the daemon), and at turn end; a reconnect across a gap, and the tab
  // coming back to the foreground, re-ask once because events may have been missed.
  useEffect(() => {
    if (!sessionId) return
    const onEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId?: string }>).detail
      if (detail?.sessionId && detail.sessionId !== sessionId) return
      loaderRef.current?.refresh()
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible') loaderRef.current?.refresh()
    }
    const names = [TRANSCRIPT_CHANGED_EVENT, TURN_ENDED_EVENT, TRANSCRIPT_APPENDED_EVENT, TRANSCRIPT_RESYNC_EVENT]
    for (const name of names) window.addEventListener(name, onEvent)
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      for (const name of names) window.removeEventListener(name, onEvent)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [sessionId])

  const messages = data?.messages || EMPTY_MESSAGES
  const liveMessages = useMemo(() => transcriptLiveMessages(messages), [messages])
  const visibleMessages = useMemo(
    () => normalizedQuery ? messages.filter(message => transcriptMatchesQuery(message, normalizedQuery)) : messages,
    [messages, normalizedQuery],
  )
  // Under a search the reader asked for every match, so abandoned messages stand
  // on their own rather than inside a fold nothing would reveal them through.
  const groups: TranscriptGroup[] = useMemo(() => normalizedQuery
    ? visibleMessages.map(message => ({ kind: 'message', message }) as const)
    : groupTranscriptMessages(visibleMessages), [visibleMessages, normalizedQuery])
  // Every match is drawn under a search; otherwise the trailing groups only.
  const drawn = normalizedQuery ? groups.length : transcriptRenderCount(groups.length, renderCount)
  const drawStart = groups.length - drawn
  const hasOlder = !!data?.has_older
  const moreAbove = !normalizedQuery && (drawStart > 0 || hasOlder)

  const oldestLoaded = messages[0]?.message_id || ''
  // Draw, or load, what is above the reader. Drawn first: everything loaded is already
  // in memory, and only once it is all drawn does the column ask the daemon for more.
  // `deliberate` is a press of the button; scrolling and filling a short column are not,
  // and they stop at a page that failed to load rather than asking again in a loop.
  const growUp = (deliberate = false) => {
    const loader = loaderRef.current
    if (normalizedQuery || !loader) return
    if (drawStart > 0) {
      setRenderCount(current => Math.max(current, drawn) + TRANSCRIPT_RENDER_STEP)
      return
    }
    if (!hasOlder || olderLoading) return
    if (!deliberate && olderStalled.current === oldestLoaded) return
    setOlderLoading(true)
    void loader.loadOlder().then(grew => {
      setOlderLoading(false)
      olderStalled.current = grew ? null : oldestLoaded
      if (grew) setRenderCount(current => Math.max(current, drawn) + TRANSCRIPT_RENDER_STEP)
    })
  }

  // Where a load leaves the viewport. First sight of a session opens at its newest
  // message; returning to a session still focused restores where reading stopped
  // (the drawer unmounts this body on every tab switch, so that memory lives
  // outside the component), by the message that was at the top of the view. After
  // that, only a reader already at the bottom is carried along by new messages -
  // otherwise the position holds and the arrival is offered as a button, because
  // yanking the column mid-sentence every time an agent speaks is what makes a live
  // log unreadable. Rows drawn above the reader (an older page, a step up the column)
  // keep the message they were reading where it was.
  useLayoutEffect(() => {
    const element = body.current
    if (!element || !data) return
    // What sits above the first drawn group counts too: the "Earlier messages" row
    // leaving when the last page lands moves everything under it.
    const first = `${moreAbove}|${groups[drawStart] ? groupKey(groups[drawStart]) : ''}`
    const latest = messages.length ? messages[messages.length - 1].message_id : null
    if (placedFor.current !== sessionId) {
      placedFor.current = sessionId
      const remembered = recallTranscriptAnchor(sessionId)
      const row = remembered ? anchoredRow(element, remembered.messageId) : null
      const offset = recallTranscriptScroll(sessionId)
      element.scrollTop = row && remembered
        ? row.offsetTop + remembered.delta
        : offset === null ? element.scrollHeight : offset
      pinned.current = isPinnedToBottom(element.scrollTop, element.scrollHeight, element.clientHeight)
      anchor.current = topAnchor(element, drawn)
      newest.current = latest
      firstDrawn.current = first
      setUnseen(0)
      return
    }
    const arrived = transcriptArrivals(messages, newest.current)
    newest.current = latest
    const grewAbove = first !== firstDrawn.current
    firstDrawn.current = first
    if (normalizedQuery) {
      if (arrived) setUnseen(current => current + arrived)
      return
    }
    // Following the bottom is suspended while the reader is holding a selection. Moving
    // the column out from under a half-made selection destroys it, and an agent
    // finishing a turn is exactly the moment someone is selecting what it just said.
    // The arrival is offered as the pill instead, which is the same answer this tab
    // already gives a reader who scrolled up.
    if (pinned.current && !selecting.current) {
      element.scrollTop = element.scrollHeight
      setUnseen(0)
    } else {
      if (grewAbove && anchor.current) {
        // An absolute target, not a shift: a browser that already anchored the
        // scroll itself has put the row exactly here, and this changes nothing.
        const row = anchoredRow(element, anchor.current.messageId)
        if (row) element.scrollTop = row.offsetTop + anchor.current.delta
      }
      if (arrived) setUnseen(current => current + arrived)
    }
    anchor.current = topAnchor(element, drawn)
  }, [data, drawn])

  // A column too short to scroll cannot be scrolled up into, so it draws or loads what
  // is above it by itself until it fills the view or there is nothing more.
  useLayoutEffect(() => {
    const element = body.current
    if (!element || !data || !moreAbove || olderLoading) return
    if (element.scrollHeight <= element.clientHeight + 1) growUp()
  })

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
    anchor.current = topAnchor(element, drawn)
    rememberTranscriptScroll(sessionId, element.scrollTop, anchor.current)
    if (pinned.current) setUnseen(0)
    if (moreAbove && element.scrollTop < GROW_THRESHOLD_PX) growUp()
  }

  const jumpToLatest = () => {
    const element = body.current
    if (!element) return
    element.scrollTop = element.scrollHeight
    pinned.current = true
    anchor.current = topAnchor(element, drawn)
    rememberTranscriptScroll(sessionId, element.scrollTop, anchor.current)
    setUnseen(0)
  }

  const copy = async (text: string, key: number): Promise<boolean> => {
    // The textarea is the synchronous fallback `copyPreparedText` selects when the
    // async clipboard is unavailable (an insecure-context phone), so it has to hold
    // the text before the call rather than after a failure.
    if (manualArea.current) manualArea.current.value = text
    // Not into the clipboard ring. Copy is this tab's primary verb and agent
    // replies run to kilobytes; capturing them would evict the short snippets the
    // ring exists to hand back. The suppression only has to span the synchronous
    // part of the copy, which is where both capture hooks fire.
    const ok = await withoutClipboardCapture(() => copyPreparedText(text, manualArea.current))
    if (!ok) { setError('The browser refused the copy. Select the text and copy it manually.'); return false }
    setError('')
    setCopied(key)
    window.setTimeout(() => setCopied(current => (current === key ? null : current)), COPIED_FLASH_MS)
    return true
  }

  /** The whole conversation's live messages, walking back to its first page if needed. */
  const wholeConversation = async (): Promise<TranscriptMessage[] | null> => {
    const loader = loaderRef.current
    if (!loader?.current?.has_older) return transcriptLiveMessages(loader?.current?.messages || messages)
    setWholePending(true)
    try {
      const complete = await loader.loadAll()
      if (!complete) {
        setError('The whole conversation could not be read just now. Try again in a moment.')
        return null
      }
      return transcriptLiveMessages(loader.current?.messages || [])
    } finally {
      setWholePending(false)
    }
  }

  const copyAll = async () => {
    if (!hasOlder) { await copy(transcriptConversationText(liveMessages), COPY_ALL); return }
    const whole = await wholeConversation()
    if (!whole || !session) return
    const text = transcriptConversationText(whole)
    // The pages took a moment, and a browser that wants the copy inside the click's own
    // turn refuses it now. The sheet's button is a fresh click, so it always works.
    if (!(await copy(text, COPY_ALL))) {
      setError('')
      setSheet({ title: agentTargetName(session), text })
    }
  }

  const selectAll = async () => {
    if (!session) return
    const whole = await wholeConversation()
    if (whole) setSheet({ title: agentTargetName(session), text: transcriptConversationText(whole) })
  }

  const toggleBranch = (key: string) => setOpenBranches(keys =>
    keys.includes(key) ? keys.filter(entry => entry !== key) : [...keys, key],
  )

  const updateSearch = (value: string) => {
    // Capture before filtering shortens the DOM and the browser clamps scrollTop.
    // A layout effect is already too late to recover the original offset.
    if (!normalizedQuery && value.trim() && body.current) {
      searchOrigin.current = { sessionId, scrollTop: body.current.scrollTop }
    }
    setSearch({ sessionId, value })
  }

  const playMessageClip = (clip: VoiceClip) => {
    unlockPlayback()
    // The whole reply, not its opening segment: a long reply is stored in pieces
    // until the daemon joins them, and playing the first piece alone read as the
    // agent answering in a third of a sentence.
    void playClipGroup(clip, clip.session_id).catch(() => setError('Playback failed.'))
  }
  /**
   * Ask for one reply's audio, in one kind.
   *
   * The daemon answers from the store when a clip for this (run, message, kind) already
   * exists, so a second reader pressing the same chip costs a lookup rather than another
   * summary call. The clip is played either way - the reader pressed play, and whether
   * the audio was found or made is not their question.
   */
  const speakMessage = async (message: TranscriptMessage, kind: VoiceContent) => {
    if (!sessionId) return
    const key = `${message.message_id}:${kind}`
    if (audioRequests.includes(key)) return
    setAudioRequests(current => [...current, key])
    unlockPlayback()
    // A per-message request is its own stream: it must not join, or cut, whatever a
    // pane's automatic read-aloud happens to be speaking.
    const streamId = newVoiceStreamId()
    // Claimed before the request, so the reply's later segments are accepted as they
    // are synthesized. Without the claim a long reply spoke only its opening segment
    // - the response's - and silently dropped the rest.
    beginRequestedStream(streamId, sessionId, 'agent')
    try {
      const clip = await api<VoiceClip>('POST', `/api/sessions/${sessionId}/voice/generate`, {
        message_id: message.message_id,
        content_mode: kind,
        stream_id: streamId,
      })
      // Optimistic, and then corrected by the refetch the clip event triggers. Without
      // it the chip would sit on `generating` until an event that has already fired.
      if (clip?.id) {
        setClips(current => [clip, ...current.filter(item => item.id !== clip.id)])
        // A clip answered from the store has its own (finished) stream, so the one
        // claimed for this request will never carry anything and is released.
        if (clip.reused) cancelRequestedStream(streamId)
        // Anything already made is played: a segmented reply is `synthesizing`
        // until its last piece lands, and waiting for that is the silence the
        // segmenting exists to remove.
        if (clipParts(clip).some(part => part.status === 'ready')) playMessageClip(clip)
      } else {
        cancelRequestedStream(streamId)
      }
    } catch (cause) {
      // The stream was opened before the request, so a failed generation would
      // otherwise leave this device waiting for a clip that will never arrive and
      // holding every later one behind it.
      cancelRequestedStream(streamId)
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setAudioRequests(current => current.filter(item => item !== key))
    }
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

  // The latest closures, behind one object that never changes identity.
  const latestActions = useRef<MessageActions | null>(null)
  latestActions.current = {
    onReveal: ordinal => setOpenActions(current => (current === ordinal ? null : ordinal)),
    onCopy: item => void copy(item.text, item.ordinal),
    onExpand: toggleExpand,
    onSelectText: item => setSheet({
      title: `${transcriptSpeaker(item.role)}${transcriptTimestampLabel(item.ts) ? ` · ${transcriptTimestampLabel(item.ts)}` : ''}`,
      text: item.text,
    }),
    onSpeak: (item, kind) => void speakMessage(item, kind),
    onPlay: playMessageClip,
  }
  const actions = useMemo<MessageActions>(() => ({
    onReveal: ordinal => latestActions.current?.onReveal(ordinal),
    onCopy: item => latestActions.current?.onCopy(item),
    onExpand: messageId => latestActions.current?.onExpand(messageId),
    onSelectText: item => latestActions.current?.onSelectText(item),
    onSpeak: (item, kind) => latestActions.current?.onSpeak(item, kind),
    onPlay: clip => latestActions.current?.onPlay(clip),
  }), [])
  const audioIndex = useMemo(() => readAloud ? indexTranscriptAudio(clips) : null, [readAloud, clips])

  if (!session) return <p class="drawer-empty">Focus a session to read its conversation.</p>

  const abandonedCount = messages.length - liveMessages.length
  const trailingToolCalls = data?.trailing_tool_calls || EMPTY_TOOLS
  const availableToolCalls = messages.reduce(
    (total, message) => total + (message.preceding_tools?.length || 0),
    trailingToolCalls.length,
  )
  const stale = data?.observation_stale_since
  const unloaded = Math.max(0, (data?.total ?? messages.length) - messages.length)
  const renderMessage = (message: TranscriptMessage) => <Message
    message={message}
    copied={copied === message.ordinal}
    expanded={expanded.includes(message.message_id)}
    query={normalizedQuery}
    audio={audioIndex?.get(message.message_id)}
    audioRequests={audioIndex ? audioRequests : null}
    revealed={openActions === message.ordinal}
    actions={actions}
  />
  const emptyText = data?.reason === 'indexing'
    ? transcriptIndexingLabel(data.progress)
    : transcriptEmptyMessage(data?.reason ?? null, session.backend)
  return <div class={selectionLive ? 'transcript-tab transcript-selecting' : 'transcript-tab'}>
    <div class="transcript-tab-head">
      {/* The message count, and only the count. The session's name is the pane heading
          immediately above this row, and printing it twice in twenty pixels of chrome made
          the count - the one thing this row knows that the heading does not - read as a
          subtitle of the name rather than as the state of the transcript. */}
      <div class="transcript-tab-meta">
        <span aria-live="polite">
          {loading && !data
            ? 'Reading transcript…'
            : normalizedQuery
              ? `${visibleMessages.length} of ${messages.length} messages`
              : `${liveMessages.length} message${liveMessages.length === 1 ? '' : 's'}`}
          {!normalizedQuery && abandonedCount > 0 ? ` · ${abandonedCount} abandoned` : ''}
          {unloaded > 0 ? ` · ${unloaded} earlier not loaded` : ''}
          {data && data.hidden > 0 ? ` · ${data.hidden} CLI record${data.hidden === 1 ? '' : 's'} hidden` : ''}
        </span>
      </div>
      {/* Whole-conversation selection goes through the same sheet as one message, which
          is what makes a span *across* messages copyable at all: in the sheet it is one
          flat buffer, so there is no scroller for a handle drag to lose its anchor in. */}
      <button
        disabled={!liveMessages.length || wholePending}
        title="Select part of the conversation"
        onClick={() => void selectAll()}
      >Select</button>
      <button
        class={copied === COPY_ALL ? 'copied' : ''}
        disabled={!liveMessages.length || wholePending}
        title="Copy the whole conversation, with speakers"
        onClick={() => void copyAll()}
      >{wholePending ? 'Loading…' : copied === COPY_ALL ? 'Copied' : 'Copy all'}</button>
      <label class="transcript-tool-toggle">
        <input
          type="checkbox"
          checked={showToolCalls}
          disabled={!availableToolCalls}
          onChange={event => setShowToolCalls(event.currentTarget.checked)}
        />
        Tool calls
      </label>
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
    {normalizedQuery && hasOlder && <p class="transcript-note">
      Searching the {messages.length} loaded messages.{' '}
      <button type="button" class="transcript-note-action" disabled={wholePending} onClick={() => void wholeConversation()}>
        {wholePending ? 'Loading…' : `Load the ${unloaded} earlier ones`}
      </button>
    </p>}
    <div class="transcript-tab-body" ref={body} onScroll={onScroll}>
      {moreAbove && <button type="button" class="transcript-older" disabled={olderLoading} onClick={() => growUp(true)}>
        {olderLoading ? 'Loading earlier messages…' : 'Earlier messages'}
      </button>}
      {messages.length
        ? visibleMessages.length
          ? groups.slice(drawStart).map((group, offset) => {
            const index = drawStart + offset
            return group.kind === 'abandoned'
              ? <AbandonedRun
                key={group.key}
                groupKey={group.key}
                messages={group.messages}
                open={openBranches.includes(group.key)}
                onToggle={() => toggleBranch(group.key)}
                renderMessage={renderMessage}
              />
              : <Fragment key={group.message.message_id}>
                {/* Only in the unfiltered column. Under a search the neighbours are
                    whatever matched, so "12 tool calls" between them would describe
                    a gap the reader is not looking at. */}
                {!normalizedQuery && (index > 0 || hasOlder) && group.message.preceding_tool_calls > 0 && (
                  showToolCalls && group.message.preceding_tools?.length
                    ? <TranscriptToolCalls
                      calls={group.message.preceding_tools}
                      load={() => seamToolInputs(sessionId, transcriptRunId, group.message.message_id)}
                    />
                    : <p class="transcript-tool-boundary">{transcriptToolBoundaryLabel(group.message.preceding_tool_calls)}</p>
                )}
                {renderMessage(group.message)}
              </Fragment>
          })
          : <p class="drawer-empty">No messages match “{normalizedQuery}”.</p>
        : !loading && !(showToolCalls && trailingToolCalls.length) && <p class="drawer-empty">{emptyText}</p>}
      {!normalizedQuery && trailingToolCalls.length > 0 && (showToolCalls
        ? <TranscriptToolCalls calls={trailingToolCalls} load={() => seamToolInputs(sessionId, transcriptRunId, null)} />
        : <p class="transcript-tool-boundary">{transcriptToolBoundaryLabel(trailingToolCalls.length)}</p>)}
    </div>
    {!normalizedQuery && unseen > 0 && <button class="transcript-jump" onClick={jumpToLatest}>
      {unseen} new ↓
    </button>}
    {sheet && <SelectSheet
      title={sheet.title}
      text={sheet.text}
      copied={copied === COPY_SELECTION}
      onCopy={text => void copy(text, COPY_SELECTION)}
      onClose={() => setSheet(null)}
    />}
    <textarea ref={manualArea} class="transcript-manual" readOnly aria-hidden="true" tabIndex={-1} />
  </div>
}
