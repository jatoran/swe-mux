// The drawer's Transcript tab: shared types, its scroll memory, and the pure
// helpers behind reading and copying.
//
// Split out of the component because the drawer unmounts a tab body on every tab
// switch. Anything that has to survive that — the scroll position, above all —
// cannot live in the component's own state, and everything here is testable
// under the plain node runner precisely because none of it renders.

export type TranscriptToolCall = {
  id: string
  name: string
  input?: unknown
}

export type TranscriptContentBlock = {
  type: string
  id?: string
  text?: string
  name?: string
  input?: unknown
}

/** One side of the conversation, already merged and filtered by the daemon. */
export type TranscriptMessage = {
  message_id: string
  ordinal: number
  role: 'user' | 'assistant'
  ts?: string
  text: string
  phase?: 'commentary' | 'final_answer'
  turn_id?: string
  turn_complete?: boolean
  /** Tool calls the agent made between the previous message and this one.
   *
   * Zero for anything a human typed and for the opening of a turn. Non-zero
   * wherever a reply resumed after tool use, which is the boundary the daemon
   * stopped merging across. */
  preceding_tool_calls: number
  /** Useful native call details at that boundary. Results are never included. */
  preceding_tools?: TranscriptToolCall[]
  /** True when the conversation branched away from this message.
   *
   * A Claude transcript is an append-only DAG: a retried turn, a `/rewind`, or a
   * resend after a failed request appends a new sibling and leaves the previous
   * attempt in the file forever. Those attempts were never sent to the model and
   * the CLI stops showing them, so they are not read as conversation - but they
   * are shown folded rather than dropped, because a reader is entitled to see
   * that their conversation branched. */
  abandoned?: boolean
}

/** The reading column's items: a message, or one run of abandoned ones folded together. */
export type TranscriptGroup =
  | Readonly<{ kind: 'message'; message: TranscriptMessage }>
  | Readonly<{ kind: 'abandoned'; key: string; messages: TranscriptMessage[] }>

/** Why there is nothing to read. `null` means the transcript loaded.
 *
 * `indexing` is a first read of a very long conversation that has not finished yet:
 * it keeps running on the daemon, so the answer is "ask again shortly". */
export type TranscriptReason = 'not_agent' | 'no_transcript' | 'unreadable' | 'agent_bridge_unavailable' | 'indexing' | null

export type TranscriptIndexProgress = Readonly<{ read_bytes: number; total_bytes: number; building?: boolean }>

export type SessionTranscript = {
  session_id: string
  agent_run_id?: string | null
  backend: string
  observation_stale_since?: number | null
  /** A contiguous run of the conversation ending at its newest message: the tail page,
   *  plus whatever older pages the reader has scrolled up into. */
  messages: TranscriptMessage[]
  /** Calls made after the newest conversational message. Named, never with inputs:
   *  `/transcript/tool-calls` reads those when a disclosure opens. */
  trailing_tool_calls?: TranscriptToolCall[]
  /** Records the daemon classified as CLI machinery rather than conversation. */
  hidden: number
  /** Messages in `messages` that belong to a branch the conversation left. */
  abandoned_messages?: number
  truncated: boolean
  reason: TranscriptReason
  /** Opaque token naming the daemon's reading; a delta is only valid against the same one.
   *  Absent from an older daemon, which answers every request with a whole window. */
  generation?: string | null
  /** The reading's revision, which `since` asks for changes after. */
  seq?: number | null
  /** Messages in the whole conversation, loaded or not. */
  total?: number
  /** Messages exist before the oldest one in `messages`. */
  has_older?: boolean
  page?: 'tail' | 'older' | 'delta' | null
  delta?: boolean
  /** On a delta: the first message to replace; everything from it on is in `messages`. */
  replace_from?: string | null
  /** The daemon could not honour the request's cursor or generation and sent a fresh tail. */
  reset?: boolean
  progress?: TranscriptIndexProgress
}

/** Dispatched by the events socket when a session finishes a turn, which is the
 *  only moment its conversation can have gained a message. */
export const TURN_ENDED_EVENT = 'mux:turn-ended'
export const TRANSCRIPT_CHANGED_EVENT = 'mux:transcript-changed'
/** The daemon saw the conversation grow mid-turn (`transcript_appended`, throttled). */
export const TRANSCRIPT_APPENDED_EVENT = 'mux:transcript-appended'
/** The events socket reconnected across a gap: every reader re-asks once. */
export const TRANSCRIPT_RESYNC_EVENT = 'mux:transcript-resync'

/** Messages a reader asks for at a time: the tail it opens on, and each older page. */
export const TRANSCRIPT_PAGE_SIZE = 200

/** A reader lands at the newest message; only a deliberate scroll up overrides that. */
export const TRANSCRIPT_BOTTOM_SLACK = 48

/** Above this many characters a message is folded behind "Show more".
 *
 * A character count rather than a measured height: the drawer can be as narrow as
 * 300px and its body is unmounted and remounted on every tab switch, so measuring
 * would mean a layout pass per message per mount to answer a question a threshold
 * answers well enough. Set where a long-but-ordinary reply still shows whole and a
 * dumped file listing does not eat the scrollbar. */
export const TRANSCRIPT_CLAMP_CHARS = 1200

/** Device-local registry of messages the reader explicitly unfolded. */
export const TRANSCRIPT_EXPANSION_KEY = 'mux.transcript.expanded.v1'
export const TRANSCRIPT_EXPANSION_MAX_ENTRIES = 500

export type TranscriptExpansionEntry = Readonly<{
  sessionId: string
  runId: string
  messageId: string
  touchedAt: number
}>

const transcriptExpansionIdentity = (entry: Pick<TranscriptExpansionEntry, 'sessionId' | 'runId' | 'messageId'>): string =>
  JSON.stringify([entry.sessionId, entry.runId, entry.messageId])

function normalizeTranscriptExpansions(value: unknown): TranscriptExpansionEntry[] {
  if (!Array.isArray(value)) return []
  const latest = new Map<string, TranscriptExpansionEntry>()
  for (const candidate of value) {
    if (!candidate || typeof candidate !== 'object') continue
    const entry = candidate as Partial<TranscriptExpansionEntry>
    if (typeof entry.sessionId !== 'string' || !entry.sessionId) continue
    if (typeof entry.runId !== 'string' || !entry.runId) continue
    if (typeof entry.messageId !== 'string' || !entry.messageId) continue
    if (typeof entry.touchedAt !== 'number' || !Number.isFinite(entry.touchedAt) || entry.touchedAt < 0) continue
    const valid = entry as TranscriptExpansionEntry
    const identity = transcriptExpansionIdentity(valid)
    const previous = latest.get(identity)
    if (!previous || valid.touchedAt > previous.touchedAt) latest.set(identity, valid)
  }
  return [...latest.values()]
    .sort((left, right) => right.touchedAt - left.touchedAt || transcriptExpansionIdentity(left).localeCompare(transcriptExpansionIdentity(right)))
    .slice(0, TRANSCRIPT_EXPANSION_MAX_ENTRIES)
}

/** Parse untrusted browser storage, pruning malformed, duplicate, and excess entries. */
export function parseTranscriptExpansions(raw: string | null): TranscriptExpansionEntry[] {
  if (!raw) return []
  try {
    return normalizeTranscriptExpansions(JSON.parse(raw))
  } catch {
    return []
  }
}

export function serializeTranscriptExpansions(entries: readonly TranscriptExpansionEntry[]): string {
  return JSON.stringify(normalizeTranscriptExpansions(entries))
}

export function expandedTranscriptMessageIds(
  entries: readonly TranscriptExpansionEntry[],
  sessionId: string,
  runId: string,
): string[] {
  return entries
    .filter(entry => entry.sessionId === sessionId && entry.runId === runId)
    .map(entry => entry.messageId)
}

/** Add or remove one explicit fold preference without ever storing message text. */
export function setTranscriptMessageExpanded(
  entries: readonly TranscriptExpansionEntry[],
  sessionId: string,
  runId: string,
  messageId: string,
  expanded: boolean,
  touchedAt = Date.now(),
): TranscriptExpansionEntry[] {
  const retained = entries.filter(entry =>
    entry.sessionId !== sessionId || entry.runId !== runId || entry.messageId !== messageId,
  )
  return normalizeTranscriptExpansions(expanded
    ? [{ sessionId, runId, messageId, touchedAt }, ...retained]
    : retained)
}

export const transcriptClamped = (text: string): boolean => text.length > TRANSCRIPT_CLAMP_CHARS

type TranscriptTimestamp = string | number | null | undefined

function transcriptTimestampDate(value: TranscriptTimestamp): Date | null {
  if (value === undefined || value === null || value === '') return null
  const numeric = typeof value === 'number' ? value : /^\d+(?:\.\d+)?$/.test(value) ? Number(value) : null
  const date = new Date(numeric === null ? value : numeric > 10_000_000_000 ? numeric : numeric * 1000)
  return Number.isNaN(date.getTime()) ? null : date
}

// One formatter for every transcript timestamp. `toLocaleString` with options builds a
// new `Intl.DateTimeFormat` per call - locale negotiation and pattern compilation each
// time - and a column of a few hundred messages paid that a few hundred times a render.
let timestampFormat: Intl.DateTimeFormat | null = null

/** Full local date and time shared by live and historical transcript messages. */
export function transcriptTimestampLabel(value: TranscriptTimestamp): string {
  const date = transcriptTimestampDate(value)
  if (!date) return ''
  timestampFormat ??= new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  return timestampFormat.format(date)
}

export function transcriptTimestampIso(value: TranscriptTimestamp): string | undefined {
  return transcriptTimestampDate(value)?.toISOString()
}

export type TranscriptSearchPart = Readonly<{ text: string; match: boolean }>

const transcriptSearchPattern = (query: string, global: boolean): RegExp =>
  new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), global ? 'giu' : 'iu')

/** Literal, case-insensitive message filtering for the drawer's local search. */
export function transcriptMatchesQuery(message: TranscriptMessage, rawQuery: string): boolean {
  const query = rawQuery.trim()
  return !query || transcriptSearchPattern(query, false).test(message.text)
}

/** Split text for safe JSX highlighting without interpreting the query as a regexp. */
export function transcriptSearchParts(text: string, rawQuery: string): TranscriptSearchPart[] {
  const query = rawQuery.trim()
  if (!query) return [{ text, match: false }]
  const parts: TranscriptSearchPart[] = []
  let start = 0
  for (const match of text.matchAll(transcriptSearchPattern(query, true))) {
    const found = match.index
    if (found > start) parts.push({ text: text.slice(start, found), match: false })
    parts.push({ text: match[0], match: true })
    start = found + match[0].length
  }
  if (start < text.length) parts.push({ text: text.slice(start), match: false })
  return parts.length ? parts : [{ text, match: false }]
}

/**
 * Whether the view is close enough to the bottom to keep following new messages.
 *
 * The slack matters more than it looks: a browser's fractional scroll metrics mean
 * `scrollTop + clientHeight` lands a pixel or two short of `scrollHeight` while
 * visually pinned to the bottom, and an exact comparison would drop the follow the
 * first time an agent replied.
 */
export function isPinnedToBottom(scrollTop: number, scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight - (scrollTop + clientHeight) <= TRANSCRIPT_BOTTOM_SLACK
}

/** The parts of a live `Selection` the reading column has to read.
 *
 * Structural rather than the DOM types so the rule below is testable under the plain
 * node runner, like everything else in this file. A real `Selection` and a real
 * `Element` both satisfy these as they stand. */
export type TranscriptSelectionLike = Readonly<{
  isCollapsed: boolean
  rangeCount: number
  anchorNode: Node | null
  focusNode: Node | null
}>

export type TranscriptSelectionHost = Readonly<{ contains: (node: Node | null) => boolean }>

/**
 * Whether the reader is holding a manual selection over this column.
 *
 * Either endpoint inside the body counts, not both. A selection being dragged is
 * routinely reported with one end outside the column for a frame or two, and the whole
 * point of asking is to stop the follow-scroll from destroying a selection mid-drag —
 * so the reading that errs toward "yes, they are selecting" is the correct one.
 */
export function transcriptSelectionActive(
  selection: TranscriptSelectionLike | null,
  host: TranscriptSelectionHost | null,
): boolean {
  if (!selection || !host) return false
  // A collapsed selection is a caret, which every tap in the column leaves behind.
  if (selection.isCollapsed || selection.rangeCount < 1) return false
  return host.contains(selection.anchorNode) || host.contains(selection.focusNode)
}

/**
 * What "Copy selection" puts on the clipboard, given a text control's offsets.
 *
 * An empty selection copies the whole text rather than nothing: the reader opened this
 * sheet to copy, and a button that silently copies an empty string reads as broken. The
 * offsets are clamped and ordered because a text control reports them however the drag
 * went, and a browser that ever reported them out of range must not throw here.
 */
export function transcriptSelectedSlice(text: string, start: number, end: number): string {
  if (!Number.isFinite(start) || !Number.isFinite(end)) return text
  const from = Math.max(0, Math.min(text.length, Math.min(start, end)))
  const to = Math.max(0, Math.min(text.length, Math.max(start, end)))
  return from === to ? text : text.slice(from, to)
}

/** Where reading stopped, by the message on screen rather than by pixels alone.
 *
 * An offset is only meaningful against the exact column it was taken from, and this
 * column changes between mounts: older pages load above, the rendered window grows,
 * and folds open. The message at the top of the view, and how far into it the view
 * sat, survive all of that; the offset is the fallback for a message that has gone. */
export type TranscriptScrollAnchor = Readonly<{
  messageId: string
  /** Pixels from the message's top edge to the view's top edge. */
  delta: number
  /** How many trailing groups of the column were rendered, so the anchor is drawn again. */
  rendered: number
}>

// One slot, not a map. The rule is "keep my place while I am on this session, and
// start at the newest message when I move to another one", so remembering more than
// the current session would be remembering something nobody asked to keep — and a
// map keyed by session id is a leak that grows for the life of the tab.
let scrollMemory: { sessionId: string; offset: number; anchor: TranscriptScrollAnchor | null } | null = null

export function rememberTranscriptScroll(
  sessionId: string,
  offset: number,
  anchor: TranscriptScrollAnchor | null = null,
): void {
  scrollMemory = { sessionId, offset, anchor }
}

/** The remembered offset for this session, or `null` to open at the newest message. */
export function recallTranscriptScroll(sessionId: string): number | null {
  return scrollMemory?.sessionId === sessionId ? scrollMemory.offset : null
}

/** The remembered anchor for this session, when the place was taken over a message. */
export function recallTranscriptAnchor(sessionId: string): TranscriptScrollAnchor | null {
  return scrollMemory?.sessionId === sessionId ? scrollMemory.anchor : null
}

export function forgetTranscriptScroll(): void {
  scrollMemory = null
}

/** Groups rendered at once, and how many more each step up the column draws.
 *
 * Every loaded message is in memory and searchable; drawing them is what costs. A
 * phone opening a 200-message page paid layout for all 200 before the first frame,
 * and the reader looks at the last screenful. */
export const TRANSCRIPT_RENDER_STEP = 60

/** How many trailing groups to render: at least the step, never more than exist. */
export function transcriptRenderCount(groups: number, requested: number): number {
  return Math.max(0, Math.min(groups, Math.max(TRANSCRIPT_RENDER_STEP, requested)))
}

/** The query string that brings `current` up to date, or opens the conversation.
 *
 * A reading the daemon can extend asks for a delta - everything changed since its
 * `seq`, for messages from its oldest loaded one on - and an older daemon, or a
 * reading with nothing in it, asks for the tail. */
export function transcriptRefreshQuery(current: SessionTranscript | null): string {
  if (current && !current.reason && current.generation && typeof current.seq === 'number') {
    const params = new URLSearchParams({ since: String(current.seq), generation: current.generation })
    const oldest = current.messages[0]?.message_id
    if (oldest) params.set('oldest', oldest)
    return params.toString()
  }
  return new URLSearchParams({ limit: String(TRANSCRIPT_PAGE_SIZE) }).toString()
}

/** The query string for the page before the oldest loaded message, or `null` if none. */
export function transcriptOlderQuery(current: SessionTranscript | null): string | null {
  const oldest = current?.messages[0]?.message_id
  if (!current || current.reason || !current.has_older || !oldest) return null
  return new URLSearchParams({ limit: String(TRANSCRIPT_PAGE_SIZE), before: oldest }).toString()
}

/** Whether a reason means "try again", as opposed to a state to show. */
export const transcriptReasonRetries = (reason: TranscriptReason): boolean =>
  reason === 'indexing' || reason === 'unreadable'

const withCounts = (reading: SessionTranscript, messages: TranscriptMessage[]): SessionTranscript => ({
  ...reading,
  messages,
  abandoned_messages: messages.reduce((count, message) => count + (message.abandoned ? 1 : 0), 0),
})

/**
 * Fold one answer from the daemon into the reading the reader holds.
 *
 * Returns `null` when the two cannot be joined honestly - a delta whose first changed
 * message the reader does not hold - which the caller answers by asking for the tail.
 *
 * Messages the answer did not change keep their object identity, so a memoised row
 * for an unchanged message does not re-render when the conversation grows.
 */
export function mergeTranscript(current: SessionTranscript | null, incoming: SessionTranscript): SessionTranscript | null {
  // A transient failure to read says nothing about the conversation: keep what is on
  // screen rather than blanking a column the reader may be in the middle of.
  if (incoming.reason && transcriptReasonRetries(incoming.reason) && current?.messages.length && !current.reason) {
    return current
  }
  if (incoming.reason || !current || current.reason) return incoming
  const sameReading = !!incoming.generation && incoming.generation === current.generation
  if (incoming.page === 'delta' && !incoming.reset) {
    if (!sameReading) return null
    const first = incoming.messages[0]
    if (!first) {
      // Nothing changed - or the delta claims a first change it did not send.
      if (incoming.replace_from) return null
      return withCounts({ ...incoming, has_older: current.has_older }, current.messages)
    }
    if (incoming.replace_from && incoming.replace_from !== first.message_id) return null
    // Spliced by position. An ordinal is a message's place in the whole conversation,
    // and within one generation a place keeps its message, so the first changed message
    // is either one held (replaced from there) or the one just after the newest held
    // (appended). Anywhere else is a gap this reader cannot see across.
    const held = current.messages
    const cut = held.length ? first.ordinal - held[0].ordinal : 0
    if (cut < 0 || cut > held.length) return null
    if (cut < held.length && held[cut].message_id !== first.message_id) return null
    // Unchanged messages inside the replaced run keep their identity too.
    const previous = new Map(held.slice(cut).map(message => [message.message_id, message]))
    const merged = [
      ...held.slice(0, cut),
      ...incoming.messages.map(message => {
        const old = previous.get(message.message_id)
        return old && sameMessage(old, message) ? old : message
      }),
    ]
    return withCounts({ ...incoming, has_older: current.has_older }, merged)
  }
  if (incoming.page === 'older' && !incoming.reset) {
    if (!sameReading) return null
    const held = new Set(current.messages.map(message => message.message_id))
    const older = incoming.messages.filter(message => !held.has(message.message_id))
    return withCounts({ ...current, has_older: incoming.has_older, total: incoming.total }, [...older, ...current.messages])
  }
  return incoming
}

function sameMessage(left: TranscriptMessage, right: TranscriptMessage): boolean {
  return left.text === right.text
    && left.role === right.role
    && left.ts === right.ts
    && left.abandoned === right.abandoned
    && left.turn_complete === right.turn_complete
    && left.turn_id === right.turn_id
    && left.phase === right.phase
    && left.ordinal === right.ordinal
    && left.preceding_tool_calls === right.preceding_tool_calls
    && (left.preceding_tools || []).map(tool => tool.id).join('\0') === (right.preceding_tools || []).map(tool => tool.id).join('\0')
}

/** How many messages arrived after the one that was newest, or `null` if it is gone.
 *
 * Counted from the newest message rather than from the list's length, because older
 * pages loading above the reader also lengthen the list and are not news. */
export function transcriptArrivals(messages: readonly TranscriptMessage[], previousNewest: string | null): number | null {
  if (previousNewest === null) return null
  for (let index = messages.length - 1; index >= 0; index--) {
    if (messages[index].message_id === previousNewest) return messages.length - 1 - index
  }
  return null
}

/** What the column says while the daemon is still on its first read of a long conversation. */
export function transcriptIndexingLabel(progress: TranscriptIndexProgress | undefined): string {
  const total = progress?.total_bytes || 0
  const read = Math.min(total, progress?.read_bytes || 0)
  const share = total > 0 ? ` (${Math.floor((read / total) * 100)}%)` : ''
  return `Reading this long conversation for the first time${share}…`
}

// The last few conversations read, so returning to one draws it at once and then asks
// only for what changed. A handful rather than every session: each holds every page
// the reader scrolled into, and the drawer reads one session at a time.
const TRANSCRIPT_CACHE_ENTRIES = 6
const transcriptCache = new Map<string, SessionTranscript>()

/** Which conversation a cached reading belongs to: the pane, its run, and its binding. */
export const transcriptCacheKey = (sessionId: string, runId: string, nativeId: string): string =>
  JSON.stringify([sessionId, runId, nativeId])

export function readTranscriptCache(key: string): SessionTranscript | null {
  const hit = transcriptCache.get(key)
  if (!hit) return null
  transcriptCache.delete(key)
  transcriptCache.set(key, hit)
  return hit
}

export function writeTranscriptCache(key: string, reading: SessionTranscript): void {
  // Only a reading worth drawing again: a reason is re-asked anyway, and caching one
  // would show "not written yet" for a conversation that has since started.
  if (reading.reason) { transcriptCache.delete(key); return }
  transcriptCache.delete(key)
  transcriptCache.set(key, reading)
  while (transcriptCache.size > TRANSCRIPT_CACHE_ENTRIES) {
    const oldest = transcriptCache.keys().next().value
    if (oldest === undefined) break
    transcriptCache.delete(oldest)
  }
}

export function clearTranscriptCache(): void {
  transcriptCache.clear()
}

/** What the copy button puts on the clipboard: the message, and nothing else.
 *
 * No role prefix and no timestamp, because a copied reply is nearly always on its
 * way into a prompt, an issue, or a note, where a `agent:` prefix is something the
 * user then has to delete. The whole-conversation copy below is the one that needs
 * speakers, since without them it is unreadable. */
export function transcriptMessageText(message: TranscriptMessage): string {
  return message.text
}

export const transcriptSpeaker = (role: TranscriptMessage['role']): string => role === 'assistant' ? 'agent' : 'you'

/** The marker shown where the daemon stopped merging, or `''` for no boundary.
 *
 * Naming the count rather than drawing a bare rule is the point: a reader who
 * sees two agent messages in a row needs to know whether the gap between them is
 * nothing or twenty minutes of tool work, and that is the difference between the
 * second one continuing the first and answering it. */
export function transcriptToolBoundaryLabel(count: number): string {
  if (!Number.isFinite(count) || count < 1) return ''
  return `${count} tool call${count === 1 ? '' : 's'}`
}

/** Human-readable native input for one collapsed tool-call disclosure. */
export function transcriptToolInputText(input: unknown): string {
  if (input === undefined || input === null) return ''
  if (typeof input === 'string') return input.trim()
  try {
    return JSON.stringify(input, null, 2) || ''
  } catch {
    return String(input)
  }
}

/** Normalize the tool-use blocks returned by the History transcript route. */
export function transcriptToolCallsFromBlocks(
  blocks: TranscriptContentBlock[],
  sourceId: string,
): TranscriptToolCall[] {
  return blocks.flatMap((block, index) => {
    if (block.type !== 'tool_use') return []
    const name = String(block.name || '').trim()
    if (!name) return []
    return [{ id: String(block.id || `${sourceId}:tool:${index}`), name, input: block.input }]
  })
}

export function transcriptConversationText(messages: TranscriptMessage[]): string {
  return messages.map(message => `${transcriptSpeaker(message.role)}: ${message.text}`).join('\n\n')
}

/** Everything the conversation still says, with abandoned attempts left out.
 *
 * What "copy the conversation" has to mean. A branch the conversation left is
 * shown so the reader knows it happened; pasting it into a bug report as though
 * it were said is the opposite of that. */
export const transcriptLiveMessages = (messages: readonly TranscriptMessage[]): TranscriptMessage[] =>
  messages.filter(message => !message.abandoned)

/**
 * Fold each *run* of abandoned messages into one group, in reading order.
 *
 * A run rather than a message: an abandoned branch is contiguous in the file, so
 * nine retries of one prompt are one thing that happened, and nine separate folds
 * would be the noise this exists to remove. The group's key is the first member's
 * `message_id`, which is stable across the appends that move the window.
 */
export function groupTranscriptMessages(messages: readonly TranscriptMessage[]): TranscriptGroup[] {
  const groups: TranscriptGroup[] = []
  for (const message of messages) {
    if (!message.abandoned) {
      groups.push({ kind: 'message', message })
      continue
    }
    const previous = groups[groups.length - 1]
    if (previous && previous.kind === 'abandoned') previous.messages.push(message)
    else groups.push({ kind: 'abandoned', key: message.message_id, messages: [message] })
  }
  return groups
}

/** What a fold says about itself, in messages rather than records. */
export function transcriptAbandonedLabel(count: number): string {
  return count === 1
    ? '1 message from a branch this conversation left'
    : `${count} messages from a branch this conversation left`
}

/** The sentence shown in place of a conversation, per daemon-reported reason. */
export function transcriptEmptyMessage(reason: TranscriptReason, backend?: string): string {
  if (reason === 'agent_bridge_unavailable') return 'Transcript following is unavailable across this terminal boundary.'
  if (reason === 'not_agent') return backend === 'shell'
    ? 'This is a shell session, so it has no agent conversation to read.'
    : 'This session has no agent conversation to read.'
  if (reason === 'no_transcript') return 'This agent has not written its first message yet.'
  if (reason === 'unreadable') return 'The native transcript could not be read just now. Retrying.'
  if (reason === 'indexing') return transcriptIndexingLabel(undefined)
  return 'Nothing has been said in this conversation yet.'
}
