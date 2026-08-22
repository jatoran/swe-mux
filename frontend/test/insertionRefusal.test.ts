import assert from 'node:assert/strict'
import test from 'node:test'
import { DIALOG_AWAITING_REASONS, insertionRefusal } from '../src/terminalActions.ts'

test('a session showing a dialog refuses an insert', () => {
  for (const reason of DIALOG_AWAITING_REASONS) {
    const message = insertionRefusal({ state: 'awaiting', awaiting_reason: reason, name: 'api' })
    assert.ok(message, `${reason} must refuse`)
    assert.ok(message.includes('api'), 'the refusal names the session it is about')
    assert.ok(message.includes('Draft kept.'), 'the refusal says the text was not lost')
  }
})

test('an ordinary composer accepts an insert', () => {
  assert.equal(insertionRefusal({ state: 'idle' }), '')
  assert.equal(insertionRefusal({ state: 'working' }), '')
  assert.equal(insertionRefusal({ state: 'running' }), '')
  assert.equal(insertionRefusal(null), '')
  assert.equal(insertionRefusal(undefined), '')
})

test('a wait is not a dialog', () => {
  // Rate limiting and SSH authentication leave the composer where it was; only a
  // prompt that *consumes* typed text as its answer is worth refusing over.
  assert.equal(insertionRefusal({ state: 'awaiting', awaiting_reason: 'rate_limit' }), '')
  assert.equal(insertionRefusal({ state: 'awaiting', awaiting_reason: 'authentication' }), '')
})

test('awaiting with no declared reason is not treated as a dialog', () => {
  // Absent evidence is not evidence of a dialog; refusing on it would block
  // inserts on every daemon too old to publish the sub-reason.
  assert.equal(insertionRefusal({ state: 'awaiting', awaiting_reason: null }), '')
})
