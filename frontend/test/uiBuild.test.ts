import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeUiBuildId, uiUpdateReloadReady, uiUpdateRequired } from '../src/uiBuild.ts'

const OLD = 'a'.repeat(64)
const NEXT = 'b'.repeat(64)

test('UI build ids are strict lowercase SHA-256 values', () => {
  assert.equal(normalizeUiBuildId(` ${OLD.toUpperCase()} `), OLD)
  assert.equal(normalizeUiBuildId('short'), null)
  assert.equal(normalizeUiBuildId('z'.repeat(64)), null)
  assert.equal(normalizeUiBuildId(null), null)
})

test('an update requires two valid and different build identities', () => {
  assert.equal(uiUpdateRequired(OLD, OLD), false)
  assert.equal(uiUpdateRequired(OLD, NEXT), true)
  assert.equal(uiUpdateRequired(null, NEXT), false)
  assert.equal(uiUpdateRequired(OLD, null), false)
})

test('automatic reload waits until the document is hidden', () => {
  assert.equal(uiUpdateReloadReady(true, 'visible'), false)
  assert.equal(uiUpdateReloadReady(true, 'hidden'), true)
  assert.equal(uiUpdateReloadReady(false, 'hidden'), false)
})
