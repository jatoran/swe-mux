import assert from 'node:assert/strict'
import test from 'node:test'
import {
  claimDrawerNote,
  drawerNoteFor,
  EMPTY_DRAWER_NOTES,
  isDrawerOwned,
  parseDrawerNotes,
  pruneDrawerNotes,
  releaseDrawerNote,
  serializeDrawerNotes,
} from '../src/drawerNotes.ts'

test('a claim is per Project, so switching Projects and back restores the drawer note', () => {
  let map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  map = claimDrawerNote(map, 'p2', 'sessions:s9')
  assert.equal(drawerNoteFor(map, 'p1'), 'note:p1')
  assert.equal(drawerNoteFor(map, 'p2'), 'sessions:s9')
  assert.equal(drawerNoteFor(map, 'p3'), null)
})

test('claiming a second note in one Project replaces the first', () => {
  let map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  map = claimDrawerNote(map, 'p1', 'sessions:s1')
  assert.deepEqual(map, { p1: 'sessions:s1' })
})

test('claiming what is already claimed returns the same reference', () => {
  const map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  assert.equal(claimDrawerNote(map, 'p1', 'note:p1'), map)
})

test('releasing hands the note back and leaves other Projects alone', () => {
  let map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  map = claimDrawerNote(map, 'p2', 'note:p2')
  map = releaseDrawerNote(map, 'p1')
  assert.deepEqual(map, { p2: 'note:p2' })
  assert.equal(releaseDrawerNote(map, 'p1'), map)
})

test('empty ids are never claimed', () => {
  assert.equal(claimDrawerNote(EMPTY_DRAWER_NOTES, '', 'note:p1'), EMPTY_DRAWER_NOTES)
  assert.equal(claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', ''), EMPTY_DRAWER_NOTES)
})

// The predicate is the whole exclusivity rule: exactly one live editor per note per browser.

test('a pane leaf stands down only for the note the open drawer is holding', () => {
  const map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  assert.equal(isDrawerOwned(map, 'p1', 'note:p1', true), true)
  assert.equal(isDrawerOwned(map, 'p1', 'sessions:s1', true), false)
  assert.equal(isDrawerOwned(map, 'p2', 'note:p1', true), false)
})

test('a closed drawer owns nothing, so the pane takes its note back', () => {
  const map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  // The drawer unmounts with the panel, so there is no editor to defer to. The claim is
  // kept, which is why reopening resumes the same Notes sub-tab.
  assert.equal(isDrawerOwned(map, 'p1', 'note:p1', false), false)
  assert.equal(drawerNoteFor(map, 'p1'), 'note:p1')
})

test('persistence round-trips and bad stored shapes degrade to no claim', () => {
  const map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  assert.deepEqual(parseDrawerNotes(serializeDrawerNotes(map)), { p1: 'note:p1' })
  assert.deepEqual(parseDrawerNotes(null), {})
  assert.deepEqual(parseDrawerNotes('not json'), {})
  assert.deepEqual(parseDrawerNotes('["note:p1"]'), {})
  assert.deepEqual(parseDrawerNotes('"note:p1"'), {})
  assert.deepEqual(parseDrawerNotes('{"p1":17,"p2":"note:p2","":"note:x","p3":""}'), { p2: 'note:p2' })
})

test('claims for Projects that no longer exist are pruned, and a clean map is untouched', () => {
  let map = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  map = claimDrawerNote(map, 'gone', 'note:gone')
  assert.deepEqual(pruneDrawerNotes(map, ['p1']), { p1: 'note:p1' })
  const clean = claimDrawerNote(EMPTY_DRAWER_NOTES, 'p1', 'note:p1')
  assert.equal(pruneDrawerNotes(clean, ['p1', 'p2']), clean)
})
