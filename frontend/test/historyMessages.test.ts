import assert from 'node:assert/strict'
import test from 'node:test'
import {
  addSpan, clipSpans, EMPTY_HISTORY_WINDOW, HISTORY_WINDOW_MAX, historyEdgeQuery, historyReaderRows, historyToolEntries,
  historyWindowFromPage, historyWindowHas, historyWindowSpan, mergeHistoryPage, missingSpans, NO_TOOL_CALLS, toolCallsQuery,
  type HistoryIndexedMessage, type HistoryMessagesPage, type HistoryMessageWindow,
} from '../src/historyMessages.ts'

const message = (ordinal: number): HistoryIndexedMessage =>
  ({ ordinal, role: ordinal % 2 ? 'assistant' : 'user', ts: null, text: `message ${ordinal}` })
const page = (ordinals: number[], before: boolean, after: boolean): HistoryMessagesPage =>
  ({ messages: ordinals.map(message), has_more_before: before, has_more_after: after })
const ordinals = (window: HistoryMessageWindow) => window.messages.map(item => item.ordinal)
const range = (from: number, count: number, step = 1) => Array.from({ length: count }, (_, index) => from + index * step)

test('a page from either edge joins the window and carries that edge’s answer', () => {
  const opened = historyWindowFromPage(page([10, 11, 13], true, true))
  const later = mergeHistoryPage(opened, page([14, 16], true, false), 'after', 13)
  assert.deepEqual(ordinals(later), [10, 11, 13, 14, 16])
  assert.equal(later.hasMoreAfter, false)
  assert.equal(later.hasMoreBefore, true, 'the other edge keeps what it knew')
  const earlier = mergeHistoryPage(later, page([7, 9], false, true), 'before', 10)
  assert.deepEqual(ordinals(earlier), [7, 9, 10, 11, 13, 14, 16])
  assert.equal(earlier.hasMoreBefore, false)
  assert.equal(earlier.hasMoreAfter, false)
})

test('messages already held keep their identity, so their rows need not re-render', () => {
  const opened = historyWindowFromPage(page([1, 2], false, true))
  const later = mergeHistoryPage(opened, page([2, 3], true, false), 'after', 2)
  assert.deepEqual(ordinals(later), [1, 2, 3], 'an overlapping page is not duplicated')
  assert.equal(later.messages[0], opened.messages[0])
  assert.equal(later.messages[1], opened.messages[1])
})

test('a page that no longer touches the window is dropped rather than leaving a gap', () => {
  // Asked for from ordinal 10, but the other edge's load cut the window to start at 40 in
  // the meantime. Joining it would put 0-9 directly against 40 with 10-39 missing.
  const window = historyWindowFromPage(page(range(40, 5), true, true))
  assert.equal(mergeHistoryPage(window, page(range(0, 10), false, true), 'before', 10), window)
  assert.equal(mergeHistoryPage(window, page(range(50, 3), true, false), 'after', 44 - 1), window)
})

test('an outgrown window gives up the edge the reader is moving away from', () => {
  const full = historyWindowFromPage(page(range(0, HISTORY_WINDOW_MAX), false, true))
  const later = mergeHistoryPage(full, page(range(HISTORY_WINDOW_MAX, 60), true, true), 'after', HISTORY_WINDOW_MAX - 1)
  assert.equal(later.messages.length, HISTORY_WINDOW_MAX)
  assert.equal(later.messages[0].ordinal, 60)
  assert.equal(later.hasMoreBefore, true, 'what was cut can be fetched again')

  const back = mergeHistoryPage(later, page(range(0, 60), false, true), 'before', 60)
  assert.equal(back.messages.length, HISTORY_WINDOW_MAX)
  assert.deepEqual([back.messages[0].ordinal, back.messages.at(-1)?.ordinal], [0, HISTORY_WINDOW_MAX - 1])
  assert.equal(back.hasMoreAfter, true)
  assert.equal(back.hasMoreBefore, false)
})

test('an empty window takes the first page whole', () => {
  const opened = mergeHistoryPage(EMPTY_HISTORY_WINDOW, page([3, 4], true, false), 'after', 0)
  assert.deepEqual(ordinals(opened), [3, 4])
  assert.equal(opened.hasMoreBefore, true)
})

test('an edge is asked for only where the conversation goes on', () => {
  const window = historyWindowFromPage(page([5, 8], true, false))
  assert.equal(historyEdgeQuery(window, 'before', 20), 'before=5&limit=20')
  assert.equal(historyEdgeQuery(window, 'after'), null)
  assert.equal(historyEdgeQuery(EMPTY_HISTORY_WINDOW, 'before'), null)
  assert.equal(historyEdgeQuery(historyWindowFromPage(page([5, 8], false, true)), 'after', 60), 'after=8&limit=60')
})

test('the window knows which messages it holds', () => {
  const window = historyWindowFromPage(page(range(0, 50, 3), false, false))
  for (const ordinal of range(0, 50, 3)) assert.equal(historyWindowHas(window, ordinal), true, String(ordinal))
  for (const ordinal of [-1, 1, 2, 148, 1000]) assert.equal(historyWindowHas(window, ordinal), false, String(ordinal))
  assert.equal(historyWindowHas(EMPTY_HISTORY_WINDOW, 0), false)
})

test('a window speaks for the ordinals out to an edge the conversation really ends at', () => {
  assert.deepEqual(historyWindowSpan(historyWindowFromPage(page([4, 9], true, true))), { from: 4, to: 9 })
  assert.deepEqual(historyWindowSpan(historyWindowFromPage(page([4, 9], false, false))), { from: 0, to: Infinity })
  assert.equal(historyWindowSpan(EMPTY_HISTORY_WINDOW), null)
})

test('tool-call spans are fetched once and let go when the window leaves them', () => {
  assert.deepEqual(missingSpans([], { from: 0, to: 9 }), [{ from: 0, to: 9 }])
  const covered = addSpan(addSpan([], { from: 0, to: 9 }), { from: 20, to: 29 })
  assert.deepEqual(missingSpans(covered, { from: 5, to: 35 }), [{ from: 10, to: 19 }, { from: 30, to: 35 }])
  assert.deepEqual(missingSpans(covered, { from: 2, to: 8 }), [])
  assert.deepEqual(missingSpans(covered, { from: 25, to: Infinity }), [{ from: 30, to: Infinity }])
  // Adjacent spans join, so the covered list stays short however the reader scrolls.
  assert.deepEqual(addSpan(covered, { from: 10, to: 19 }), [{ from: 0, to: 29 }])
  assert.deepEqual(addSpan([{ from: 0, to: Infinity }], { from: 5, to: 6 }), [{ from: 0, to: Infinity }])
  assert.deepEqual(clipSpans(covered, { from: 5, to: 22 }), [{ from: 5, to: 9 }, { from: 20, to: 22 }])
  assert.deepEqual(clipSpans(covered, { from: 40, to: 50 }), [])
  assert.equal(toolCallsQuery({ from: 3, to: 9 }), 'from=3&to=9')
  assert.equal(toolCallsQuery({ from: 3, to: Infinity }), 'from=3')
})

test('tool calls are normalized once, with a stand-in for a message that is only calls', () => {
  const entries = historyToolEntries({
    mtime_ns: 1,
    size: 2,
    messages: [
      { ordinal: 4, role: 'assistant', ts: 't4', has_text: true, tool_calls: [{ id: 'toolu_1', name: 'Read', input: { path: 'a' } }] },
      { ordinal: 5, role: 'assistant', has_text: false, tool_calls: [{ name: 'Bash', input: 'ls' }] },
      { ordinal: 6, role: 'assistant', has_text: false, tool_calls: [{ name: '  ' }] },
    ],
  }, 'run-1')
  assert.deepEqual([...entries.keys()], [4, 5], 'a call with no name is not a call')
  assert.deepEqual(entries.get(4)?.toolCalls, [{ id: 'toolu_1', name: 'Read', input: { path: 'a' } }])
  assert.equal(entries.get(4)?.hasText, true)
  assert.equal(entries.get(5)?.toolCalls[0].id, 'history:run-1:5:tool:0', 'an unnamed id is made stable')
  assert.deepEqual(entries.get(5)?.message, { ordinal: 5, role: 'assistant', ts: null, text: '' })
})

test('the reader draws tool-only messages at their own place, and only inside the window', () => {
  const window = historyWindowFromPage(page([2, 6, 9], true, true))
  const tools = historyToolEntries({
    mtime_ns: 1,
    size: 2,
    messages: [
      { ordinal: 1, role: 'assistant', has_text: false, tool_calls: [{ name: 'Before' }] },
      { ordinal: 6, role: 'assistant', has_text: true, tool_calls: [{ name: 'Attached' }] },
      { ordinal: 7, role: 'assistant', has_text: false, tool_calls: [{ name: 'Between' }] },
      { ordinal: 8, role: 'assistant', has_text: false, tool_calls: [{ name: 'AlsoBetween' }] },
      { ordinal: 12, role: 'assistant', has_text: false, tool_calls: [{ name: 'After' }] },
    ],
  }, 'run-1')
  const shown = historyReaderRows(window, tools, true)
  assert.deepEqual(shown.map(row => row.message.ordinal), [2, 6, 7, 8, 9])
  assert.deepEqual(shown[1].toolCalls.map(call => call.name), ['Attached'])
  assert.equal(shown[0].toolCalls, NO_TOOL_CALLS)

  const hidden = historyReaderRows(window, tools, false)
  assert.deepEqual(hidden.map(row => row.message.ordinal), [2, 6, 9])
  assert.ok(hidden.every(row => row.toolCalls === NO_TOOL_CALLS))
  assert.equal(hidden[0].message, window.messages[0])

  // At an edge where the conversation ends, the calls past the last reply belong here.
  const ended = historyWindowFromPage(page([2, 6, 9], false, false))
  assert.deepEqual(historyReaderRows(ended, tools, true).map(row => row.message.ordinal), [1, 2, 6, 7, 8, 9, 12])
})
