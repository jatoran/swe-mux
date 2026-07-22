import assert from 'node:assert/strict'
import test from 'node:test'
import { shouldLoadWebgl, terminalAttachReadyFrame } from '../src/terminalRenderer.ts'

test('desktop auto and webgl preferences keep accelerated rendering enabled', () => {
  assert.equal(shouldLoadWebgl('auto', false), true)
  assert.equal(shouldLoadWebgl('webgl', false), true)
  assert.equal(shouldLoadWebgl('dom', false), false)
})

test('mobile viewports always use the built-in DOM renderer', () => {
  assert.equal(shouldLoadWebgl('auto', true), false)
  assert.equal(shouldLoadWebgl('webgl', true), false)
  assert.equal(shouldLoadWebgl('dom', true), false)
})

test('attach readiness carries fitted dimensions and the active renderer', () => {
  assert.deepEqual(terminalAttachReadyFrame(132, 41, 'webgl'), {
    type: 'attach_ready',
    cols: 132,
    rows: 41,
    renderer: 'webgl',
  })
})
