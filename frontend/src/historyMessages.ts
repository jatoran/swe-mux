// The History reader's conversation window: which indexed messages are loaded, how a
// page joins them, and where tool calls attach.
//
// The daemon serves a conversation's text out of its message index a page at a time
// (`GET /api/history/{id}/messages`), so a long conversation is never in the browser
// whole. The reader holds a bounded window of it and extends that window from whichever
// edge the reader scrolls toward. It used to fetch the entire parsed transcript, tool
// inputs and all, before it drew anything - seconds and megabytes for a long
// conversation, and on a phone the Transcript band was where that showed.
//
// Pure, so the paging rules are pinned by unit tests rather than only by a renderer spec:
// a page that no longer touches the window it was asked for is dropped rather than
// leaving a hole, and a window that outgrows its bound gives up the far edge.

import { transcriptToolCallsFromBlocks, type TranscriptToolCall } from './transcriptView.ts'

/** One indexed message: the text a reader sees, at its ordinal in the parsed conversation. */
export type HistoryIndexedMessage = Readonly<{
  ordinal: number
  role: 'user' | 'assistant'
  ts?: string | null
  text: string
}>

export type HistoryMessagesPage = {
  messages: HistoryIndexedMessage[]
  has_more_before: boolean
  has_more_after: boolean
}

export type HistoryMessageWindow = Readonly<{
  messages: readonly HistoryIndexedMessage[]
  hasMoreBefore: boolean
  hasMoreAfter: boolean
}>

/** One message's tool calls as `GET /api/history/{id}/tool-calls` returns them. */
export type HistoryToolCallsMessage = {
  ordinal: number
  role: 'user' | 'assistant'
  ts?: string | null
  /** False for a message that is nothing but tool calls, which the index never held. */
  has_text: boolean
  tool_calls: { id?: string | null; name?: string | null; input?: unknown }[]
}

export type HistoryToolCallsPage = { messages: HistoryToolCallsMessage[]; mtime_ns: number; size: number }

/** A message's tool calls, ready to draw. `message` stands in for a tool-only message. */
export type HistoryToolEntry = Readonly<{
  message: HistoryIndexedMessage
  toolCalls: TranscriptToolCall[]
  hasText: boolean
}>

export type HistoryReaderRow = Readonly<{ message: HistoryIndexedMessage; toolCalls: TranscriptToolCall[] }>

/** An inclusive ordinal span; `to` is `Infinity` for "to the end of the conversation". */
export type HistoryOrdinalSpan = Readonly<{ from: number; to: number }>

export type HistoryPageEdge = 'before' | 'after'

/** Messages per page. The daemon's default is the same number (`HISTORY_MESSAGES_PAGE`). */
export const HISTORY_PAGE_SIZE = 60

/** The most messages the reader keeps at once.
 *
 *  Six pages. Enough that scrolling back and forth over a stretch of conversation does not
 *  refetch it, and bounded so a reader who scrolls through a 5,000-message conversation is
 *  not left holding 5,000 rendered articles - which is what made the old reader slow to
 *  scroll on a phone long after it had finished loading. */
export const HISTORY_WINDOW_MAX = 360

export const EMPTY_HISTORY_WINDOW: HistoryMessageWindow = Object.freeze({
  messages: Object.freeze([]) as readonly HistoryIndexedMessage[],
  hasMoreBefore: false,
  hasMoreAfter: false,
})

/** Shared so a row without tool calls hands the same array to its memoized component. */
export const NO_TOOL_CALLS: TranscriptToolCall[] = []

/** A first page, or a page around one ordinal, as the whole window. */
export function historyWindowFromPage(page: HistoryMessagesPage): HistoryMessageWindow {
  return { messages: page.messages, hasMoreBefore: page.has_more_before, hasMoreAfter: page.has_more_after }
}

/** The query that extends `window` from `edge`, or `null` when there is nothing there. */
export function historyEdgeQuery(
  window: HistoryMessageWindow,
  edge: HistoryPageEdge,
  limit: number = HISTORY_PAGE_SIZE,
): string | null {
  const { messages } = window
  if (!messages.length) return null
  if (edge === 'before') {
    return window.hasMoreBefore ? `before=${messages[0].ordinal}&limit=${limit}` : null
  }
  return window.hasMoreAfter ? `after=${messages[messages.length - 1].ordinal}&limit=${limit}` : null
}

/**
 * Join a page fetched from one edge onto the window.
 *
 * `requestedFrom` is the edge ordinal the page was asked for relative to. If the window's
 * edge has moved since - the other edge's load trimmed it, or a jump replaced it - the
 * page is dropped: joining it would put a gap in the middle of the conversation that
 * nothing would ever fill. The caller's observer asks again from the new edge.
 *
 * Messages already held keep their object identity, which is what lets a memoized row
 * skip re-rendering when a page lands beside it.
 */
export function mergeHistoryPage(
  window: HistoryMessageWindow,
  page: HistoryMessagesPage,
  edge: HistoryPageEdge,
  requestedFrom: number,
  max: number = HISTORY_WINDOW_MAX,
): HistoryMessageWindow {
  const held = window.messages
  if (!held.length) return historyWindowFromPage(page)
  if (edge === 'before') {
    if (held[0].ordinal !== requestedFrom) return window
    const first = held[0].ordinal
    const earlier = page.messages.filter(message => message.ordinal < first)
    let messages = [...earlier, ...held]
    let hasMoreAfter = window.hasMoreAfter
    if (messages.length > max) {
      messages = messages.slice(0, max)
      hasMoreAfter = true
    }
    return { messages, hasMoreBefore: page.has_more_before, hasMoreAfter }
  }
  const last = held[held.length - 1].ordinal
  if (last !== requestedFrom) return window
  const later = page.messages.filter(message => message.ordinal > last)
  let messages = [...held, ...later]
  let hasMoreBefore = window.hasMoreBefore
  if (messages.length > max) {
    messages = messages.slice(messages.length - max)
    hasMoreBefore = true
  }
  return { messages, hasMoreBefore, hasMoreAfter: page.has_more_after }
}

/** Whether the window holds the message at `ordinal`. Binary search: the window is sorted. */
export function historyWindowHas(window: HistoryMessageWindow, ordinal: number): boolean {
  const { messages } = window
  let low = 0
  let high = messages.length - 1
  while (low <= high) {
    const middle = (low + high) >> 1
    const found = messages[middle].ordinal
    if (found === ordinal) return true
    if (found < ordinal) low = middle + 1
    else high = middle - 1
  }
  return false
}

/**
 * The ordinals the window speaks for, which is what tool calls are fetched for.
 *
 * Wider than the loaded messages at an edge the conversation really ends at: a run of
 * tool-only messages before the first reply or after the last one belongs to this window,
 * because no later page will ever cover it.
 */
export function historyWindowSpan(window: HistoryMessageWindow): HistoryOrdinalSpan | null {
  const { messages } = window
  if (!messages.length) return null
  return {
    from: window.hasMoreBefore ? messages[0].ordinal : 0,
    to: window.hasMoreAfter ? messages[messages.length - 1].ordinal : Number.POSITIVE_INFINITY,
  }
}

/** The parts of `want` that no span in `covered` includes, in order. */
export function missingSpans(covered: readonly HistoryOrdinalSpan[], want: HistoryOrdinalSpan): HistoryOrdinalSpan[] {
  const sorted = [...covered].sort((left, right) => left.from - right.from)
  const missing: HistoryOrdinalSpan[] = []
  let cursor = want.from
  for (const span of sorted) {
    if (cursor > want.to) break
    if (span.to < cursor) continue
    if (span.from > want.to) break
    if (span.from > cursor) missing.push({ from: cursor, to: Math.min(span.from - 1, want.to) })
    cursor = Math.max(cursor, span.to + 1)
  }
  if (cursor <= want.to) missing.push({ from: cursor, to: want.to })
  return missing
}

/** `covered` with `span` added, overlapping and adjacent spans joined. */
export function addSpan(covered: readonly HistoryOrdinalSpan[], span: HistoryOrdinalSpan): HistoryOrdinalSpan[] {
  const sorted = [...covered, span].sort((left, right) => left.from - right.from)
  const joined: HistoryOrdinalSpan[] = []
  for (const item of sorted) {
    const last = joined[joined.length - 1]
    if (last && item.from <= last.to + 1) joined[joined.length - 1] = { from: last.from, to: Math.max(last.to, item.to) }
    else joined.push(item)
  }
  return joined
}

/** `covered` cut down to what lies inside `want`, for letting go of spans the window left. */
export function clipSpans(covered: readonly HistoryOrdinalSpan[], want: HistoryOrdinalSpan): HistoryOrdinalSpan[] {
  return covered.flatMap(span => {
    const from = Math.max(span.from, want.from)
    const to = Math.min(span.to, want.to)
    return from <= to ? [{ from, to }] : []
  })
}

/** The query string for one span of tool calls. An open end is left off. */
export function toolCallsQuery(span: HistoryOrdinalSpan): string {
  return Number.isFinite(span.to) ? `from=${span.from}&to=${span.to}` : `from=${span.from}`
}

/** A tool-calls answer, keyed by ordinal and normalized for `TranscriptToolCalls`. */
export function historyToolEntries(page: HistoryToolCallsPage, historyId: string): Map<number, HistoryToolEntry> {
  const entries = new Map<number, HistoryToolEntry>()
  for (const item of page.messages) {
    const blocks = item.tool_calls.map(call => ({
      type: 'tool_use',
      id: call.id || undefined,
      name: call.name || undefined,
      input: call.input,
    }))
    const toolCalls = transcriptToolCallsFromBlocks(blocks, `history:${historyId}:${item.ordinal}`)
    if (!toolCalls.length) continue
    entries.set(item.ordinal, {
      message: { ordinal: item.ordinal, role: item.role, ts: item.ts ?? null, text: '' },
      toolCalls,
      hasText: item.has_text,
    })
  }
  return entries
}

/**
 * What the reader draws: the window's messages, each with its tool calls when they are on,
 * and the tool-only messages that fall inside the window placed at their own ordinals.
 *
 * A tool-only message outside the window's span is held back until a page reaches it, so
 * the column never shows a tool call next to a message it did not follow.
 */
export function historyReaderRows(
  window: HistoryMessageWindow,
  tools: ReadonlyMap<number, HistoryToolEntry>,
  showToolCalls: boolean,
): HistoryReaderRow[] {
  if (!showToolCalls || !tools.size) {
    return window.messages.map(message => ({ message, toolCalls: NO_TOOL_CALLS }))
  }
  const span = historyWindowSpan(window)
  const toolOnly = span
    ? [...tools.values()]
      .filter(entry => !entry.hasText && entry.message.ordinal >= span.from && entry.message.ordinal <= span.to)
      .sort((left, right) => left.message.ordinal - right.message.ordinal)
    : []
  const rows: HistoryReaderRow[] = []
  let next = 0
  for (const message of window.messages) {
    while (next < toolOnly.length && toolOnly[next].message.ordinal < message.ordinal) {
      rows.push({ message: toolOnly[next].message, toolCalls: toolOnly[next].toolCalls })
      next++
    }
    rows.push({ message, toolCalls: tools.get(message.ordinal)?.toolCalls || NO_TOOL_CALLS })
  }
  for (; next < toolOnly.length; next++) {
    rows.push({ message: toolOnly[next].message, toolCalls: toolOnly[next].toolCalls })
  }
  return rows
}
