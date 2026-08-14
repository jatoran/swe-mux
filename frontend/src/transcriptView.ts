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
  /** Tool calls the agent made between the previous message and this one.
   *
   * Zero for anything a human typed and for the opening of a turn. Non-zero
   * wherever a reply resumed after tool use, which is the boundary the daemon
   * stopped merging across. */
  preceding_tool_calls: number
  /** Useful native call details at that boundary. Results are never included. */
  preceding_tools?: TranscriptToolCall[]
}

/** Why there is nothing to read. `null` means the transcript loaded. */
export type TranscriptReason = 'not_agent' | 'no_transcript' | 'unreadable' | 'agent_bridge_unavailable' | null

export type SessionTranscript = {
  session_id: string
  agent_run_id?: string | null
  backend: string
  observation_stale_since?: number | null
  messages: TranscriptMessage[]
  /** Calls made after the newest conversational message. */
  trailing_tool_calls?: TranscriptToolCall[]
  /** Records the daemon classified as CLI machinery rather than conversation. */
  hidden: number
  truncated: boolean
  reason: TranscriptReason
}

/** Dispatched by the events socket when a session finishes a turn, which is the
 *  only moment its conversation can have gained a message. */
export const TURN_ENDED_EVENT = 'mux:turn-ended'
export const TRANSCRIPT_CHANGED_EVENT = 'mux:transcript-changed'

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

/** Full local date and time shared by live and historical transcript messages. */
export function transcriptTimestampLabel(value: TranscriptTimestamp): string {
  return transcriptTimestampDate(value)?.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) || ''
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

// One slot, not a map. The rule is "keep my place while I am on this session, and
// start at the newest message when I move to another one", so remembering more than
// the current session would be remembering something nobody asked to keep — and a
// map keyed by session id is a leak that grows for the life of the tab.
let scrollMemory: { sessionId: string; offset: number } | null = null

export function rememberTranscriptScroll(sessionId: string, offset: number): void {
  scrollMemory = { sessionId, offset }
}

/** The remembered offset for this session, or `null` to open at the newest message. */
export function recallTranscriptScroll(sessionId: string): number | null {
  return scrollMemory?.sessionId === sessionId ? scrollMemory.offset : null
}

export function forgetTranscriptScroll(): void {
  scrollMemory = null
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

/** The sentence shown in place of a conversation, per daemon-reported reason. */
export function transcriptEmptyMessage(reason: TranscriptReason, backend?: string): string {
  if (reason === 'agent_bridge_unavailable') return 'Transcript following is unavailable across this terminal boundary.'
  if (reason === 'not_agent') return backend === 'shell'
    ? 'This is a shell session, so it has no agent conversation to read.'
    : 'This session has no agent conversation to read.'
  if (reason === 'no_transcript') return 'This agent has not written its first message yet.'
  if (reason === 'unreadable') return 'The native transcript could not be read just now. It will retry on the next turn.'
  return 'Nothing has been said in this conversation yet.'
}
