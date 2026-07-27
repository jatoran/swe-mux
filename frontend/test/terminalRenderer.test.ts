import assert from 'node:assert/strict'
import test from 'node:test'
import { shouldLoadWebgl, terminalAttachReadyFrame } from '../src/terminalRenderer.ts'

test('desktop auto and webgl preferences keep accelerated rendering enabled', () => {
  assert.equal(shouldLoadWebgl('auto', false, 'shell'), true)
  assert.equal(shouldLoadWebgl('webgl', false, 'claude'), true)
  assert.equal(shouldLoadWebgl('dom', false, 'shell'), false)
})

test('mobile viewports always use the built-in DOM renderer', () => {
  assert.equal(shouldLoadWebgl('auto', true, 'shell'), false)
  assert.equal(shouldLoadWebgl('webgl', true, 'claude'), false)
  assert.equal(shouldLoadWebgl('dom', true, 'codex'), false)
})

test('Codex always uses the DOM renderer so off-tail scrollback stays stable', () => {
  assert.equal(shouldLoadWebgl('auto', false, 'codex'), false)
  assert.equal(shouldLoadWebgl('webgl', false, 'codex'), false)
  assert.equal(shouldLoadWebgl('dom', false, 'codex'), false)
})

test('attach readiness carries fitted dimensions and the active renderer', () => {
  assert.deepEqual(terminalAttachReadyFrame(132, 41, 'webgl'), {
    type: 'attach_ready',
    cols: 132,
    rows: 41,
    renderer: 'webgl',
  })
})
