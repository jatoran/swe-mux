import assert from 'node:assert/strict'
import test from 'node:test'

import { MAX_PENDING_INPUT_CHARS, pendingInputDecision } from '../src/pendingInput.ts'

test('an ordinary paste is held through a replay rather than dropped', () => {
  // The regression this guards: at a 4 KiB ceiling a routine paste was silently
  // discarded whenever it landed mid-replay, which is most likely on a deep session
  // because that is when the replay window is longest.
  const paste = 'x'.repeat(150_000)
  assert.equal(pendingInputDecision(0, paste.length), 'hold')
})

test('keystrokes accumulated during a replay stay held', () => {
  let held = 0
  for (let i = 0; i < 500; i += 1) {
    assert.equal(pendingInputDecision(held, 1), 'hold')
    held += 1
  }
  assert.equal(held, 500)
})

test('the ceiling still bounds a replay that never completes', () => {
  assert.equal(pendingInputDecision(MAX_PENDING_INPUT_CHARS, 1), 'overflow')
  assert.equal(pendingInputDecision(MAX_PENDING_INPUT_CHARS - 1, 2), 'overflow')
})

test('the ceiling is an inclusive boundary', () => {
  assert.equal(pendingInputDecision(0, MAX_PENDING_INPUT_CHARS), 'hold')
  assert.equal(pendingInputDecision(1, MAX_PENDING_INPUT_CHARS), 'overflow')
})

test('an explicit limit overrides the default', () => {
  assert.equal(pendingInputDecision(0, 11, 10), 'overflow')
  assert.equal(pendingInputDecision(0, 10, 10), 'hold')
})
