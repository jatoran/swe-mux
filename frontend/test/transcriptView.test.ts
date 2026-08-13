import assert from 'node:assert/strict'
import test from 'node:test'
import {
  expandedTranscriptMessageIds,
  forgetTranscriptScroll,
  isPinnedToBottom,
  recallTranscriptScroll,
  rememberTranscriptScroll,
  parseTranscriptExpansions,
  serializeTranscriptExpansions,
  setTranscriptMessageExpanded,
  TRANSCRIPT_BOTTOM_SLACK,
  transcriptClamped,
  TRANSCRIPT_CLAMP_CHARS,
  TRANSCRIPT_EXPANSION_MAX_ENTRIES,
  transcriptConversationText,
  transcriptEmptyMessage,
  transcriptMatchesQuery,
  transcriptSearchParts,
  transcriptSpeaker,
  transcriptTimestampIso,
  transcriptTimestampLabel,
  transcriptToolBoundaryLabel,
  type TranscriptMessage,
} from '../src/transcriptView.ts'

const message = (over: Partial<TranscriptMessage> = {}): TranscriptMessage =>
  ({ message_id: 'offset:0', ordinal: 0, role: 'assistant', text: 'built it', preceding_tool_calls: 0, ...over })

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

test('every empty state says which kind of nothing it is', () => {
  assert.match(transcriptEmptyMessage('not_agent', 'shell'), /shell session/)
  assert.match(transcriptEmptyMessage('not_agent'), /no agent conversation/)
  assert.match(transcriptEmptyMessage('no_transcript'), /has not written its first message/)
  assert.match(transcriptEmptyMessage('unreadable'), /could not be read/)
  assert.match(transcriptEmptyMessage('agent_bridge_unavailable'), /terminal boundary/)
  // Loaded, and genuinely empty: a fresh agent nobody has spoken to yet.
  assert.match(transcriptEmptyMessage(null), /Nothing has been said/)
})
