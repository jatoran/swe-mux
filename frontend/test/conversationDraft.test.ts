import assert from 'node:assert/strict'
import test from 'node:test'

import {
  appendUtterance,
  clearDraft,
  editDraft,
  EMPTY_DRAFT,
  undoUtterance,
} from '../src/conversationDraft.ts'

test('utterances accumulate into one spoken sentence', () => {
  const first = appendUtterance(EMPTY_DRAFT, 'refactor the scrollback ring')
  const second = appendUtterance(first, 'so it keeps bracketed paste')
  assert.equal(second.text, 'refactor the scrollback ring so it keeps bracketed paste')
  assert.equal(second.segments.length, 2)
})

test('blank recognitions leave the draft untouched', () => {
  const draft = appendUtterance(EMPTY_DRAFT, 'keep this')
  assert.equal(appendUtterance(draft, '   '), draft)
  assert.equal(appendUtterance(draft, ''), draft)
})

test('undo removes exactly the last phrase that was heard', () => {
  const draft = appendUtterance(appendUtterance(EMPTY_DRAFT, 'first phrase'), 'second phrase')
  assert.equal(undoUtterance(draft).text, 'first phrase')
  assert.equal(undoUtterance(undoUtterance(draft)).text, '')
  // Undoing past the start is not an error; it lands on the empty draft.
  assert.deepEqual(undoUtterance(EMPTY_DRAFT), EMPTY_DRAFT)
})

test('a typed edit keeps the whitespace being typed', () => {
  // draftText is not a trimmed re-derivation, because "foo " must survive long enough
  // for the user to type the next word after the space they just pressed.
  assert.equal(editDraft('foo ').text, 'foo ')
  assert.deepEqual(editDraft(''), EMPTY_DRAFT)
})

test('an utterance landing after a typed edit preserves the edit', () => {
  const heard = appendUtterance(EMPTY_DRAFT, 'refactor the scrollbak ring')
  const corrected = editDraft('refactor the scrollback ring')
  const next = appendUtterance(corrected, 'and its replay path')
  assert.equal(next.text, 'refactor the scrollback ring and its replay path')
  assert.notEqual(next.text, heard.text)
})

test('a typed edit flattens the log, so undo takes back only later speech', () => {
  const draft = appendUtterance(appendUtterance(EMPTY_DRAFT, 'one'), 'two')
  const corrected = editDraft('one two three')
  assert.equal(corrected.segments.length, 1)
  const next = appendUtterance(corrected, 'four')
  assert.equal(undoUtterance(next).text, 'one two three')
  // A second undo clears rather than dismembering wording the user rewrote.
  assert.equal(undoUtterance(undoUtterance(next)).text, '')
  assert.equal(draft.text, 'one two')
})

test('a trailing space typed before an utterance lands does not double up', () => {
  const next = appendUtterance(editDraft('half a sentence  '), 'finished by speech')
  assert.equal(next.text, 'half a sentence finished by speech')
})

test('clear returns the empty draft', () => {
  assert.deepEqual(clearDraft(), EMPTY_DRAFT)
})
