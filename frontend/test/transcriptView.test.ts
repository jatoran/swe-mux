import assert from 'node:assert/strict'
import test from 'node:test'
import {
  clearTranscriptCache,
  expandedTranscriptMessageIds,
  forgetTranscriptScroll,
  groupTranscriptMessages,
  isPinnedToBottom,
  mergeTranscript,
  readTranscriptCache,
  recallTranscriptAnchor,
  recallTranscriptScroll,
  rememberTranscriptScroll,
  TRANSCRIPT_PAGE_SIZE,
  TRANSCRIPT_RENDER_STEP,
  transcriptArrivals,
  transcriptCacheKey,
  transcriptIndexingLabel,
  transcriptOlderQuery,
  transcriptReasonRetries,
  transcriptRefreshQuery,
  transcriptRenderCount,
  writeTranscriptCache,
  type SessionTranscript,
  parseTranscriptExpansions,
  serializeTranscriptExpansions,
  setTranscriptMessageExpanded,
  TRANSCRIPT_BOTTOM_SLACK,
  transcriptClamped,
  TRANSCRIPT_CLAMP_CHARS,
  TRANSCRIPT_EXPANSION_MAX_ENTRIES,
  transcriptAbandonedLabel,
  transcriptConversationText,
  transcriptEmptyMessage,
  transcriptLiveMessages,
  transcriptMatchesQuery,
  transcriptSearchParts,
  transcriptSelectedSlice,
  transcriptSelectionActive,
  transcriptSpeaker,
  transcriptTimestampIso,
  transcriptTimestampLabel,
  transcriptToolBoundaryLabel,
  transcriptToolCallsFromBlocks,
  transcriptToolInputText,
  type TranscriptMessage,
  type TranscriptSelectionLike,
} from '../src/transcriptView.ts'

const message = (over: Partial<TranscriptMessage> = {}): TranscriptMessage =>
  ({ message_id: 'offset:0', ordinal: 0, role: 'assistant', text: 'built it', preceding_tool_calls: 0, preceding_tools: [], ...over })

test('following the bottom survives fractional scroll metrics', () => {
  // Pinned: a browser reports `scrollTop + clientHeight` a pixel or two short of
  // `scrollHeight` while visually at the bottom, and an exact comparison would drop
  // the follow the first time an agent replied.
  assert.equal(isPinnedToBottom(900, 1000, 100), true)
  assert.equal(isPinnedToBottom(898, 1000, 100), true)
  assert.equal(isPinnedToBottom(1000 - 100 - TRANSCRIPT_BOTTOM_SLACK, 1000, 100), true)
  // Scrolled up to read: new messages must not yank the column.
  assert.equal(isPinnedToBottom(400, 1000, 100), false)
  // A conversation shorter than the viewport is always at its bottom.
  assert.equal(isPinnedToBottom(0, 80, 300), true)
})

test('scroll memory keeps one place, for the session you are still on', () => {
  forgetTranscriptScroll()
  assert.equal(recallTranscriptScroll('sess-1'), null, 'first sight opens at the newest message')
  rememberTranscriptScroll('sess-1', 420)
  // The drawer unmounts a tab body on every tab switch, so coming back has to land
  // where reading stopped rather than at the bottom again.
  assert.equal(recallTranscriptScroll('sess-1'), 420)
  // Moving to another session starts at its newest message: the memory is "where I
  // am in this conversation", not a per-session archive that grows for the life of
  // the tab.
  assert.equal(recallTranscriptScroll('sess-2'), null)
  rememberTranscriptScroll('sess-2', 88)
  assert.equal(recallTranscriptScroll('sess-1'), null, 'leaving a session forgets its place')
  assert.equal(recallTranscriptScroll('sess-2'), 88)
})

test('a copied message is the message, a copied conversation names its speakers', () => {
  assert.equal(transcriptSpeaker('assistant'), 'agent')
  assert.equal(transcriptSpeaker('user'), 'you')
  // No role prefix on a single message: a copied reply is nearly always on its way
  // into a prompt or a note, where `agent:` is something to delete.
  assert.equal(transcriptConversationText([
    message({ ordinal: 0, role: 'user', text: 'build it' }),
    message({ ordinal: 1, text: 'built' }),
  ]), 'you: build it\n\nagent: built')
  assert.equal(transcriptConversationText([]), '')
})

test('copying the conversation copies what it says, not what it abandoned', () => {
  // A retried prompt is in the column so the reader can see the retry happened.
  // Pasting all eight copies into a bug report is the opposite of showing it.
  const messages = [
    message({ ordinal: 0, role: 'user', text: 'review it', abandoned: true }),
    message({ ordinal: 1, role: 'user', text: 'review it' }),
    message({ ordinal: 2, text: 'reviewed' }),
  ]
  assert.deepEqual(transcriptLiveMessages(messages).map(item => item.ordinal), [1, 2])
  assert.equal(transcriptConversationText(transcriptLiveMessages(messages)), 'you: review it\n\nagent: reviewed')
})

test('one abandoned branch folds into one row, however many attempts it holds', () => {
  const groups = groupTranscriptMessages([
    message({ ordinal: 0, role: 'user', text: 'set it up' }),
    message({ ordinal: 1, message_id: 'offset:10', role: 'user', text: 'review it', abandoned: true }),
    message({ ordinal: 2, message_id: 'offset:20', role: 'user', text: 'review it', abandoned: true }),
    message({ ordinal: 3, message_id: 'offset:30', role: 'user', text: 'review it' }),
    message({ ordinal: 4, message_id: 'offset:40', text: 'reviewed' }),
  ])
  assert.deepEqual(groups.map(group => group.kind), ['message', 'abandoned', 'message', 'message'])
  const fold = groups[1]
  assert.equal(fold.kind, 'abandoned')
  if (fold.kind !== 'abandoned') return
  // Keyed by the run's first member, which survives the appends that move the window.
  assert.equal(fold.key, 'offset:10')
  assert.deepEqual(fold.messages.map(item => item.ordinal), [1, 2])
  assert.equal(transcriptAbandonedLabel(fold.messages.length), '2 messages from a branch this conversation left')
  assert.equal(transcriptAbandonedLabel(1), '1 message from a branch this conversation left')
})

test('two separate branches stay two folds', () => {
  // Adjacency is the whole grouping rule, so a live message between two abandoned
  // runs must not let them collapse into one thing that never happened together.
  const groups = groupTranscriptMessages([
    message({ ordinal: 0, message_id: 'offset:0', role: 'user', text: 'first try', abandoned: true }),
    message({ ordinal: 1, message_id: 'offset:10', role: 'user', text: 'first try' }),
    message({ ordinal: 2, message_id: 'offset:20', text: 'wrong turn', abandoned: true }),
    message({ ordinal: 3, message_id: 'offset:30', text: 'right turn' }),
  ])
  assert.deepEqual(groups.map(group => group.kind), ['abandoned', 'message', 'abandoned', 'message'])
})

test('a conversation with no branches produces no folds', () => {
  const groups = groupTranscriptMessages([
    message({ ordinal: 0, role: 'user', text: 'build it' }),
    message({ ordinal: 1, text: 'built' }),
  ])
  assert.deepEqual(groups.map(group => group.kind), ['message', 'message'])
})

test('the seam between two agent messages names how much work is in it', () => {
  // Zero is the ordinary case (a human turn, or the start of a reply) and draws
  // nothing: a rule between every pair of messages would say nothing at all.
  assert.equal(transcriptToolBoundaryLabel(0), '')
  assert.equal(transcriptToolBoundaryLabel(1), '1 tool call')
  assert.equal(transcriptToolBoundaryLabel(31), '31 tool calls')
  // A daemon that ever sent something else must not render "NaN tool calls".
  assert.equal(transcriptToolBoundaryLabel(Number.NaN), '')
  assert.equal(transcriptToolBoundaryLabel(-2), '')
})

test('tool disclosures keep useful names and inputs without result data', () => {
  assert.equal(transcriptToolInputText({ command: 'pytest -q', timeout_ms: 1000 }), '{\n  "command": "pytest -q",\n  "timeout_ms": 1000\n}')
  assert.equal(transcriptToolInputText('  README.md  '), 'README.md')
  assert.equal(transcriptToolInputText(null), '')
  assert.deepEqual(transcriptToolCallsFromBlocks([
    { type: 'text', text: 'Checking.' },
    { type: 'tool_use', id: 'call-1', name: 'Read', input: { file_path: 'README.md' } },
    { type: 'tool_result', id: 'call-1', text: 'private output' },
    { type: 'tool_use', name: '', input: { ignored: true } },
  ], 'history:4'), [
    { id: 'call-1', name: 'Read', input: { file_path: 'README.md' } },
  ])
})

test('long messages fold, ordinary ones do not', () => {
  assert.equal(transcriptClamped('short'), false)
  assert.equal(transcriptClamped('x'.repeat(TRANSCRIPT_CLAMP_CHARS)), false)
  assert.equal(transcriptClamped('x'.repeat(TRANSCRIPT_CLAMP_CHARS + 1)), true)
})

test('transcript timestamps expose a full local date and machine-readable value', () => {
  const value = '2026-08-05T15:30:00Z'
  assert.ok(transcriptTimestampLabel(value).includes('2026'))
  assert.equal(transcriptTimestampIso(value), '2026-08-05T15:30:00.000Z')
  assert.equal(transcriptTimestampLabel('not-a-date'), '')
  assert.equal(transcriptTimestampIso('not-a-date'), undefined)
})

test('explicitly expanded messages persist by session, run, and stable message id', () => {
  let entries = setTranscriptMessageExpanded([], 'session-1', 'run-1', 'offset:7', true, 100)
  entries = setTranscriptMessageExpanded(entries, 'session-1', 'run-1', 'offset:9', true, 200)

  assert.deepEqual(expandedTranscriptMessageIds(entries, 'session-1', 'run-1'), ['offset:9', 'offset:7'])
  assert.deepEqual(expandedTranscriptMessageIds(entries, 'session-1', 'run-2'), [])
  assert.deepEqual(expandedTranscriptMessageIds(entries, 'session-2', 'run-1'), [])

  entries = setTranscriptMessageExpanded(entries, 'session-1', 'run-1', 'offset:7', false, 300)
  assert.deepEqual(expandedTranscriptMessageIds(entries, 'session-1', 'run-1'), ['offset:9'])
  assert.deepEqual(JSON.parse(serializeTranscriptExpansions(entries)), [
    { sessionId: 'session-1', runId: 'run-1', messageId: 'offset:9', touchedAt: 200 },
  ])
})

test('the expansion registry validates, deduplicates, and caps browser storage', () => {
  assert.deepEqual(parseTranscriptExpansions(null), [])
  assert.deepEqual(parseTranscriptExpansions('{bad json'), [])
  assert.deepEqual(parseTranscriptExpansions(JSON.stringify({ entries: [] })), [])

  const candidates = Array.from({ length: TRANSCRIPT_EXPANSION_MAX_ENTRIES + 5 }, (_, index) => ({
    sessionId: 'session-1', runId: 'run-1', messageId: `offset:${index}`, touchedAt: index,
  }))
  candidates.push({ sessionId: 'session-1', runId: 'run-1', messageId: 'offset:4', touchedAt: 10_000 })
  const parsed = parseTranscriptExpansions(JSON.stringify([
    ...candidates,
    null,
    { sessionId: 'session-1', runId: 'run-1', messageId: '', touchedAt: 20_000 },
    { sessionId: 'session-1', runId: '', messageId: 'offset:1', touchedAt: 20_000 },
    { sessionId: 'session-1', runId: 'run-1', messageId: 'offset:1', touchedAt: 'later' },
  ]))

  assert.equal(parsed.length, TRANSCRIPT_EXPANSION_MAX_ENTRIES)
  assert.deepEqual(parsed[0], { sessionId: 'session-1', runId: 'run-1', messageId: 'offset:4', touchedAt: 10_000 })
  assert.equal(parsed.filter(entry => entry.messageId === 'offset:4').length, 1)
  assert.deepEqual(Object.keys(parsed[0]).sort(), ['messageId', 'runId', 'sessionId', 'touchedAt'])
})

test('drawer search is literal, case-insensitive, and highlights every occurrence', () => {
  assert.equal(transcriptMatchesQuery(message({ text: 'Built the Search Bar' }), ' search '), true)
  assert.equal(transcriptMatchesQuery(message({ text: 'Built the Search Bar' }), 'missing'), false)
  assert.equal(transcriptMatchesQuery(message({ text: 'anything' }), '   '), true)
  assert.deepEqual(transcriptSearchParts('Banana BAN', 'ban'), [
    { text: 'Ban', match: true },
    { text: 'ana ', match: false },
    { text: 'BAN', match: true },
  ])
  assert.deepEqual(transcriptSearchParts('a+b+a', '+'), [
    { text: 'a', match: false },
    { text: '+', match: true },
    { text: 'b', match: false },
    { text: '+', match: true },
    { text: 'a', match: false },
  ])
  assert.deepEqual(transcriptSearchParts('İA', 'a'), [
    { text: 'İ', match: false },
    { text: 'A', match: true },
  ])
})

test('a manual selection over the column is recognised from either endpoint', () => {
  const inside = { id: 'inside' } as unknown as Node
  const outside = { id: 'outside' } as unknown as Node
  const body = { contains: (node: Node | null) => node === inside }
  const selection = (over: Partial<TranscriptSelectionLike> = {}): TranscriptSelectionLike =>
    ({ isCollapsed: false, rangeCount: 1, anchorNode: inside, focusNode: inside, ...over })

  assert.equal(transcriptSelectionActive(selection(), body), true)
  // Mid-drag a browser routinely reports one end outside the column, and that is exactly
  // when the follow-scroll must not fire. Erring toward "yes, they are selecting" is the
  // whole point of reading either endpoint rather than both.
  assert.equal(transcriptSelectionActive(selection({ focusNode: outside }), body), true)
  assert.equal(transcriptSelectionActive(selection({ anchorNode: outside }), body), true)
  // A selection wholly in another surface is not this column's business.
  assert.equal(transcriptSelectionActive(selection({ anchorNode: outside, focusNode: outside }), body), false)
  // A caret is what every tap in the column leaves behind, so it must not freeze anything.
  assert.equal(transcriptSelectionActive(selection({ isCollapsed: true }), body), false)
  assert.equal(transcriptSelectionActive(selection({ rangeCount: 0 }), body), false)
  assert.equal(transcriptSelectionActive(selection({ anchorNode: null, focusNode: null }), body), false)
  // No selection, or a body that has not mounted yet.
  assert.equal(transcriptSelectionActive(null, body), false)
  assert.equal(transcriptSelectionActive(selection(), null), false)
})

test('the select sheet copies the dragged span, or all of it when nothing is dragged', () => {
  const text = 'built the search bar'
  assert.equal(transcriptSelectedSlice(text, 6, 9), 'the')
  // A text control reports the offsets however the drag went.
  assert.equal(transcriptSelectedSlice(text, 9, 6), 'the')
  // A caret is not a selection, and a copy button that copies an empty string reads as
  // broken to someone who opened this sheet specifically to copy.
  assert.equal(transcriptSelectedSlice(text, 4, 4), text)
  assert.equal(transcriptSelectedSlice(text, 0, 0), text)
  // Out-of-range and non-numeric offsets clamp rather than throw or slice to nothing.
  assert.equal(transcriptSelectedSlice(text, -20, 5), 'built')
  assert.equal(transcriptSelectedSlice(text, 6, 900), 'the search bar')
  assert.equal(transcriptSelectedSlice(text, Number.NaN, 5), text)
  assert.equal(transcriptSelectedSlice('', 0, 0), '')
})

test('every empty state says which kind of nothing it is', () => {
  assert.match(transcriptEmptyMessage('not_agent', 'shell'), /shell session/)
  assert.match(transcriptEmptyMessage('not_agent'), /no agent conversation/)
  assert.match(transcriptEmptyMessage('no_transcript'), /has not written its first message/)
  assert.match(transcriptEmptyMessage('unreadable'), /could not be read/)
  assert.match(transcriptEmptyMessage('agent_bridge_unavailable'), /terminal boundary/)
  // A first read of a long conversation still running: "soon", not "broken".
  assert.match(transcriptEmptyMessage('indexing'), /for the first time/)
  // Loaded, and genuinely empty: a fresh agent nobody has spoken to yet.
  assert.match(transcriptEmptyMessage(null), /Nothing has been said/)
})

test('a first read in progress says how far it has got', () => {
  assert.match(transcriptIndexingLabel({ read_bytes: 42, total_bytes: 100 }), /\(42%\)/)
  assert.doesNotMatch(transcriptIndexingLabel({ read_bytes: 0, total_bytes: 0 }), /%/)
  assert.doesNotMatch(transcriptIndexingLabel(undefined), /%/)
  // Progress past the size taken at the start (the file grew) never reads over 100%.
  assert.match(transcriptIndexingLabel({ read_bytes: 150, total_bytes: 100 }), /\(100%\)/)
})

// ---------------------------------------------------------------- incremental reading

const reading = (over: Partial<SessionTranscript> = {}): SessionTranscript => ({
  session_id: 's1', backend: 'claude', messages: [], hidden: 0, truncated: false, reason: null,
  generation: 'g.0', seq: 4, total: 0, has_older: false, page: 'tail', delta: false, replace_from: null,
  ...over,
})
const at = (ordinal: number, text = `m${ordinal}`, over: Partial<TranscriptMessage> = {}) =>
  message({ message_id: `offset:${ordinal}`, ordinal, text, ...over })

test('a reader asks for a delta it can extend, and for the tail otherwise', () => {
  assert.equal(transcriptRefreshQuery(null), `limit=${TRANSCRIPT_PAGE_SIZE}`)
  // An older daemon names no generation: every ask is for a whole window.
  assert.equal(transcriptRefreshQuery(reading({ generation: undefined, seq: undefined })), `limit=${TRANSCRIPT_PAGE_SIZE}`)
  // A reason is not a reading to extend.
  assert.equal(transcriptRefreshQuery(reading({ reason: 'unreadable' })), `limit=${TRANSCRIPT_PAGE_SIZE}`)
  const held = reading({ messages: [at(7), at(8)] })
  assert.deepEqual(Object.fromEntries(new URLSearchParams(transcriptRefreshQuery(held))), {
    since: '4', generation: 'g.0', oldest: 'offset:7',
  })
  assert.deepEqual(Object.fromEntries(new URLSearchParams(transcriptRefreshQuery(reading()))), {
    since: '4', generation: 'g.0',
  })
})

test('older pages are asked for from the oldest message held, and only while there are some', () => {
  assert.equal(transcriptOlderQuery(null), null)
  assert.equal(transcriptOlderQuery(reading({ messages: [at(7)], has_older: false })), null)
  assert.deepEqual(
    Object.fromEntries(new URLSearchParams(transcriptOlderQuery(reading({ messages: [at(7)], has_older: true })) || '')),
    { limit: String(TRANSCRIPT_PAGE_SIZE), before: 'offset:7' },
  )
})

test('a delta replaces from its first changed message and keeps every other row as it was', () => {
  const first = at(1)
  const second = at(2, 'working', { turn_complete: false })
  const current = reading({ messages: [first, second], seq: 4, total: 2 })
  const incoming = reading({
    page: 'delta', delta: true, seq: 6, total: 3, replace_from: 'offset:2',
    messages: [at(2, 'working', { turn_complete: true }), at(3)],
  })
  const merged = mergeTranscript(current, incoming)
  assert.ok(merged)
  assert.deepEqual(merged.messages.map(item => item.message_id), ['offset:1', 'offset:2', 'offset:3'])
  assert.equal(merged.messages[1].turn_complete, true)
  // The untouched row is the same object, which is what lets its memoised row skip.
  assert.equal(merged.messages[0], first)
  assert.equal(merged.seq, 6)
  assert.equal(merged.total, 3)
})

test('an unchanged message inside a replaced run keeps its identity too', () => {
  const kept = at(2)
  const current = reading({ messages: [at(1), kept] })
  const merged = mergeTranscript(current, reading({
    page: 'delta', seq: 5, replace_from: 'offset:2', messages: [at(2), at(3)],
  }))
  assert.equal(merged?.messages[1], kept)
})

test('an empty delta changes the reading and not the messages', () => {
  const current = reading({ messages: [at(1)], has_older: true })
  const merged = mergeTranscript(current, reading({ page: 'delta', seq: 9, replace_from: null, messages: [], has_older: false }))
  assert.equal(merged?.messages, current.messages)
  assert.equal(merged?.seq, 9)
  assert.equal(merged?.has_older, true, 'what is held decides whether there is more above it')
})

test('a delta of new messages only is appended after the newest held', () => {
  // The common case while an agent works: nothing held changed, so the first changed
  // message is one the reader has never seen.
  const current = reading({ messages: [at(1), at(2)] })
  const merged = mergeTranscript(current, reading({ page: 'delta', seq: 5, replace_from: 'offset:3', messages: [at(3), at(4)] }))
  assert.deepEqual(merged?.messages.map(item => item.ordinal), [1, 2, 3, 4])
  assert.equal(merged?.messages[1], current.messages[1])
})

test('a delta that cannot be joined asks for the tail instead of guessing', () => {
  const current = reading({ messages: [at(1)] })
  // From another generation: the daemon rebuilt its reading.
  assert.equal(mergeTranscript(current, reading({ page: 'delta', generation: 'g.1', replace_from: 'offset:1', messages: [at(1)] })), null)
  // Past the newest held, with a gap the reader cannot see across.
  assert.equal(mergeTranscript(current, reading({ page: 'delta', replace_from: 'offset:99', messages: [at(99)] })), null)
  // Before the oldest held.
  assert.equal(mergeTranscript(reading({ messages: [at(5)] }), reading({ page: 'delta', replace_from: 'offset:4', messages: [at(4)] })), null)
  // A place that holds another message than the delta says.
  assert.equal(mergeTranscript(current, reading({ page: 'delta', replace_from: 'record:x', messages: [message({ message_id: 'record:x', ordinal: 1 })] })), null)
  // A first change named but not sent.
  assert.equal(mergeTranscript(current, reading({ page: 'delta', replace_from: 'offset:1', messages: [] })), null)
})

test('an older page goes above what is held, once', () => {
  const current = reading({ messages: [at(3), at(4)], has_older: true, total: 4 })
  const merged = mergeTranscript(current, reading({
    page: 'older', has_older: false, total: 4, messages: [at(1), at(2), at(3)],
  }))
  assert.deepEqual(merged?.messages.map(item => item.ordinal), [1, 2, 3, 4])
  assert.equal(merged?.has_older, false)
  assert.equal(merged?.seq, current.seq, 'the reading it extends is still the one held')
  assert.equal(mergeTranscript(current, reading({ page: 'older', generation: 'g.9', messages: [at(1)] })), null)
})

test('a reset, a tail, and an older daemon replace the reading whole', () => {
  const current = reading({ messages: [at(1), at(2)] })
  const tail = reading({ page: 'tail', reset: true, generation: 'g.1', messages: [at(5)] })
  assert.equal(mergeTranscript(current, tail), tail)
  const legacy = { session_id: 's1', backend: 'claude', messages: [at(8)], hidden: 0, truncated: false, reason: null }
  assert.equal(mergeTranscript(current, legacy), legacy)
})

test('a read that failed for now leaves the column as it was', () => {
  const current = reading({ messages: [at(1)] })
  assert.equal(mergeTranscript(current, reading({ reason: 'indexing', messages: [] })), current)
  assert.equal(mergeTranscript(current, reading({ reason: 'unreadable', messages: [] })), current)
  // A fact about the session is shown, though: the pane is no longer an agent's.
  const gone = reading({ reason: 'not_agent', messages: [] })
  assert.equal(mergeTranscript(current, gone), gone)
  assert.ok(transcriptReasonRetries('indexing'))
  assert.ok(transcriptReasonRetries('unreadable'))
  assert.ok(!transcriptReasonRetries('no_transcript'))
})

test('news is counted from the newest message, so older pages are not news', () => {
  const messages = [at(1), at(2), at(3), at(4)]
  assert.equal(transcriptArrivals(messages, 'offset:2'), 2)
  assert.equal(transcriptArrivals(messages, 'offset:4'), 0)
  assert.equal(transcriptArrivals(messages, null), null)
  assert.equal(transcriptArrivals(messages, 'offset:99'), null)
})

test('the column draws a bounded number of groups, growing a step at a time', () => {
  assert.equal(transcriptRenderCount(500, 0), TRANSCRIPT_RENDER_STEP)
  assert.equal(transcriptRenderCount(500, TRANSCRIPT_RENDER_STEP * 2), TRANSCRIPT_RENDER_STEP * 2)
  assert.equal(transcriptRenderCount(10, 1000), 10)
  assert.equal(transcriptRenderCount(0, 60), 0)
})

test('the scroll memory keeps the message the reader was on, beside the offset', () => {
  forgetTranscriptScroll()
  rememberTranscriptScroll('sess-1', 420, { messageId: 'offset:9', delta: 12, rendered: 120 })
  assert.deepEqual(recallTranscriptAnchor('sess-1'), { messageId: 'offset:9', delta: 12, rendered: 120 })
  assert.equal(recallTranscriptScroll('sess-1'), 420)
  assert.equal(recallTranscriptAnchor('sess-2'), null)
  rememberTranscriptScroll('sess-1', 10)
  assert.equal(recallTranscriptAnchor('sess-1'), null, 'an offset taken over no message has no anchor')
  forgetTranscriptScroll()
})

test('the last few readings are kept, newest use first, and never a reason', () => {
  clearTranscriptCache()
  const keys = Array.from({ length: 8 }, (_, index) => transcriptCacheKey(`s${index}`, 'run', 'native'))
  keys.forEach((key, index) => writeTranscriptCache(key, reading({ session_id: `s${index}` })))
  // Six kept: the two written first are gone.
  assert.equal(readTranscriptCache(keys[0]), null)
  assert.equal(readTranscriptCache(keys[1]), null)
  assert.equal(readTranscriptCache(keys[2])?.session_id, 's2')
  // Reading one makes it the most recent, so the next write evicts another.
  writeTranscriptCache(transcriptCacheKey('s9', 'run', 'native'), reading())
  assert.ok(readTranscriptCache(keys[2]))
  assert.equal(readTranscriptCache(keys[3]), null)
  // A reason replaces what was kept rather than being kept: it is re-asked anyway.
  writeTranscriptCache(keys[2], reading({ reason: 'no_transcript' }))
  assert.equal(readTranscriptCache(keys[2]), null)
  // A rebinding (fork, rollover) is a different conversation, so a different key.
  assert.notEqual(transcriptCacheKey('s1', 'run', 'a'), transcriptCacheKey('s1', 'run', 'b'))
  clearTranscriptCache()
})
