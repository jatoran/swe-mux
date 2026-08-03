import assert from 'node:assert/strict'
import test from 'node:test'
import {
  forgetTranscriptScroll,
  isPinnedToBottom,
  recallTranscriptScroll,
  rememberTranscriptScroll,
  TRANSCRIPT_BOTTOM_SLACK,
  transcriptClamped,
  TRANSCRIPT_CLAMP_CHARS,
  transcriptConversationText,
  transcriptEmptyMessage,
  transcriptSpeaker,
  type TranscriptMessage,
} from '../src/transcriptView.ts'

const message = (over: Partial<TranscriptMessage> = {}): TranscriptMessage =>
  ({ ordinal: 0, role: 'assistant', text: 'built it', ...over })

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

test('long messages fold, ordinary ones do not', () => {
  assert.equal(transcriptClamped('short'), false)
  assert.equal(transcriptClamped('x'.repeat(TRANSCRIPT_CLAMP_CHARS)), false)
  assert.equal(transcriptClamped('x'.repeat(TRANSCRIPT_CLAMP_CHARS + 1)), true)
})

test('every empty state says which kind of nothing it is', () => {
  assert.match(transcriptEmptyMessage('not_agent', 'shell'), /shell session/)
  assert.match(transcriptEmptyMessage('not_agent'), /no agent conversation/)
  assert.match(transcriptEmptyMessage('no_transcript'), /has not written its first message/)
  assert.match(transcriptEmptyMessage('unreadable'), /could not be read/)
  // Loaded, and genuinely empty: a fresh agent nobody has spoken to yet.
  assert.match(transcriptEmptyMessage(null), /Nothing has been said/)
})
