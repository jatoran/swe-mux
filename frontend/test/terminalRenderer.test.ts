import assert from 'node:assert/strict'
import test from 'node:test'
import { shouldLoadWebgl, terminalAttachReadyFrame, windowsPtyCompatibility } from '../src/terminalRenderer.ts'

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

test('a ConPTY descriptor becomes xterm windowsPty options', () => {
  assert.deepEqual(windowsPtyCompatibility({ backend: 'conpty', build_number: 19045 }), {
    backend: 'conpty',
    buildNumber: 19045,
  })
})

test('non-Windows and malformed descriptors leave xterm on its defaults', () => {
  // The daemon sends null off Windows; the rest guard a hand-edited or stale
  // config payload from producing a windowsPty option xterm cannot interpret.
  assert.equal(windowsPtyCompatibility(null), undefined)
  assert.equal(windowsPtyCompatibility(undefined), undefined)
  assert.equal(windowsPtyCompatibility({ backend: 'winpty', build_number: 19045 }), undefined)
  assert.equal(windowsPtyCompatibility({ backend: 'conpty' }), undefined)
  assert.equal(windowsPtyCompatibility({ backend: 'conpty', build_number: '19045' }), undefined)
  assert.equal(windowsPtyCompatibility({ backend: 'conpty', build_number: 0 }), undefined)
})
