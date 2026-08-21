import assert from 'node:assert/strict'
import test from 'node:test'
import {
  LOOP_SAVE_LIMIT,
  LOOP_WINDOW_MS,
  NoteEditGuard,
  canonicalNoteText,
} from '../src/noteEditGuard.ts'

/** A guard on a hand-cranked clock, so the loop failsafe is testable without waiting. */
function guardAt(start = 1_000): { guard: NoteEditGuard; advance: (ms: number) => void } {
  let clock = start
  const guard = new NoteEditGuard({ now: () => clock })
  return { guard, advance: ms => { clock += ms } }
}

test('canonical form erases only differences the markdown cannot show', () => {
  const canonical = canonicalNoteText('# Title\n\nbody\n')
  assert.equal(canonicalNoteText('﻿# Title\r\n\r\nbody\r\n'), canonical)
  assert.equal(canonicalNoteText('# Title\n\nbody'), canonical)
  assert.equal(canonicalNoteText('# Title\n\nbody\n\n\n'), canonical)
  assert.equal(canonicalNoteText('# Title \n\nbody\t\n'), canonical)
  // Composed and decomposed é are the same character to a reader.
  assert.equal(canonicalNoteText('café'), canonicalNoteText('café'))
})

test('a hard line break survives canonicalization, and its width does not', () => {
  assert.equal(canonicalNoteText('one  \ntwo'), 'one  \ntwo')
  assert.equal(canonicalNoteText('one     \ntwo'), 'one  \ntwo')
  // One trailing space renders as nothing, so it is serialization rather than content.
  assert.notEqual(canonicalNoteText('one \ntwo'), canonicalNoteText('one  \ntwo'))
  assert.equal(canonicalNoteText('one \ntwo'), 'one\ntwo')
})

test('a commit that only re-serializes the loaded document is not a save', () => {
  const { guard } = guardAt()
  guard.adopt('# Note\n\nbody\n')
  guard.recordLocalInput()
  assert.equal(guard.commit('# Note\r\n\r\nbody\r\n\r\n'), 'unchanged')
  assert.equal(guard.commit('# Note\n\nbody\n'), 'unchanged')
  assert.equal(guard.commit('# Note\n\nbody edited\n'), 'save')
  // The accepted save becomes the new baseline: re-emitting it is not a second edit.
  assert.equal(guard.commit('# Note\n\nbody edited\n\n'), 'unchanged')
})

test('a reload never dirties the document, and the next local edit still saves', () => {
  const { guard } = guardAt()
  guard.adopt('one')
  // The re-seeded engine commits something genuinely different: still not this human's work.
  assert.equal(guard.commit('one\n- item'), 'reloaded')
  assert.equal(guard.commit('one\n- item'), 'reloaded')
  guard.recordLocalInput()
  assert.equal(guard.commit('one typed'), 'save')
  // A later reload closes the latch again.
  guard.adopt('two')
  assert.equal(guard.commit('two rewritten elsewhere'), 'reloaded')
})

test('input-free saves pause autosaving, and the pause holds until input or resume', () => {
  const { guard, advance } = guardAt()
  guard.adopt('note')
  guard.recordLocalInput()
  for (let index = 0; index < LOOP_SAVE_LIMIT; index++) {
    assert.equal(guard.commit(`note ${index}`), 'save')
    advance(500)
  }
  assert.equal(guard.commit('note again'), 'looping')
  assert.equal(guard.reading().paused, true)
  assert.equal(guard.commit('note once more'), 'paused')
  guard.resume()
  assert.equal(guard.reading().paused, false)
  assert.equal(guard.commit('note after resume'), 'save')
})

test('typing is never blocked, however fast, because the failsafe keys on input', () => {
  const { guard, advance } = guardAt()
  guard.adopt('note')
  for (let index = 0; index < LOOP_SAVE_LIMIT * 20; index++) {
    guard.recordLocalInput()
    assert.equal(guard.commit(`note ${index}`), 'save')
    advance(10)
  }
  assert.equal(guard.reading().paused, false)
})

test('input-free saves spread beyond the window are a slow writer, not a loop', () => {
  const { guard, advance } = guardAt()
  guard.adopt('note')
  guard.recordLocalInput()
  for (let index = 0; index < LOOP_SAVE_LIMIT * 3; index++) {
    assert.equal(guard.commit(`note ${index}`), 'save')
    advance(LOOP_WINDOW_MS)
  }
  assert.equal(guard.reading().paused, false)
})

test('local input releases a pause, so a person can always save over a loop', () => {
  const { guard } = guardAt()
  guard.adopt('note')
  guard.recordLocalInput()
  for (let index = 0; index < LOOP_SAVE_LIMIT; index++) guard.commit(`note ${index}`)
  assert.equal(guard.commit('trip'), 'looping')
  guard.recordLocalInput()
  assert.equal(guard.commit('typed over the loop'), 'save')
  assert.equal(guard.reading().paused, false)
})

test('a pause is reported once, with the evidence that ended the episode', () => {
  const { guard } = guardAt()
  guard.adopt('note')
  guard.recordLocalInput()
  for (let index = 0; index < LOOP_SAVE_LIMIT; index++) guard.commit(`note ${index}`)
  assert.equal(guard.takeReport(), null)
  guard.commit('trip')
  assert.deepEqual(guard.takeReport(), {
    kind: 'paused',
    commits: LOOP_SAVE_LIMIT,
    windowMs: LOOP_WINDOW_MS,
  })
  assert.equal(guard.takeReport(), null)
})

test('a reload ping-pong is reported even though guard 2 already made it harmless', () => {
  const { guard, advance } = guardAt()
  // Exactly the observed incident: every turn is a reload followed by the engine's echo.
  for (let index = 0; index < LOOP_SAVE_LIMIT; index++) {
    guard.adopt(`note ${index}`)
    assert.equal(guard.commit(`note ${index} re-serialized`), 'reloaded')
    advance(900)
  }
  const report = guard.takeReport()
  assert.equal(report?.kind, 'echo')
  assert.equal(report?.commits, LOOP_SAVE_LIMIT)
  // Reporting an echo episode does not stop the note from saving real work.
  assert.equal(guard.reading().paused, false)
  guard.recordLocalInput()
  assert.equal(guard.commit('typed'), 'save')
  // …and the evidence resets with that input, so one episode is reported once.
  assert.equal(guard.takeReport(), null)
  assert.equal(guard.reading().suppressedEchoes, 0)
})
