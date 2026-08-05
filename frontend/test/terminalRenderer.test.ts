import assert from 'node:assert/strict'
import test from 'node:test'
import { shouldLoadWebgl, terminalAttachReadyFrame, terminalCursorOptions, windowsPtyCompatibility } from '../src/terminalRenderer.ts'

test('the mobile IME bridge keeps a visible terminal caret while it owns DOM focus', () => {
  assert.deepEqual(terminalCursorOptions(true), {
    cursorInactiveStyle: 'bar',
    cursorWidth: 2,
  })
  assert.deepEqual(terminalCursorOptions(false), {
    cursorInactiveStyle: 'outline',
    cursorWidth: 1,
  })
})

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

test('Codex stays on the DOM renderer under auto so off-tail scrollback stays stable', () => {
  assert.equal(shouldLoadWebgl('auto', false, 'codex'), false)
  assert.equal(shouldLoadWebgl('dom', false, 'codex'), false)
})

test('an explicit webgl preference reaches Codex, which is the one backend it was silently skipping', () => {
  // The setting existed and the user could select it, but the backend whose repaint
  // cost makes the renderer worth choosing ignored it. Opting in has a visible failure
  // mode and is the only way to find out whether the exclusion still earns its place.
  assert.equal(shouldLoadWebgl('webgl', false, 'codex'), true)
  // Never on a phone, whatever the preference says: Chromium device emulation can keep
  // a live context while changing pixel ratio and leave the pane blank.
  assert.equal(shouldLoadWebgl('webgl', true, 'codex'), false)
})

test('attach readiness carries fitted dimensions, renderer, and visibility', () => {
  assert.deepEqual(terminalAttachReadyFrame(132, 41, 'webgl'), {
    type: 'attach_ready',
    cols: 132,
    rows: 41,
    renderer: 'webgl',
    hidden: false,
  })
  // A pane attaching while its window is hidden registers no viewport at all, so it
  // cannot size the PTY for the device the user is actually looking at.
  assert.deepEqual(terminalAttachReadyFrame(132, 41, 'dom', true), {
    type: 'attach_ready',
    cols: 132,
    rows: 41,
    renderer: 'dom',
    hidden: true,
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
