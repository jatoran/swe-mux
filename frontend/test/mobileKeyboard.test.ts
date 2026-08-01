import assert from 'node:assert/strict'
import test from 'node:test'
import { deepActiveElement, raisesSoftKeyboard } from '../src/mobileKeyboard.ts'
import type { FocusedField, FocusScope } from '../src/mobileKeyboard.ts'

test('text entry fields are what hold the soft keyboard up', () => {
  // The terminal's mobile live input, and every composer/search field.
  assert.equal(raisesSoftKeyboard({ tagName: 'TEXTAREA' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'text' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'search' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'number' }), true)
  // An input with no type attribute is a text input.
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT' }), true)
  // Any contenteditable surface, wherever it comes from.
  assert.equal(raisesSoftKeyboard({ tagName: 'DIV', isContentEditable: true }), true)
  // The Continuity note editor is neither: it is a custom element whose real input is a
  // `<textarea>` inside its shadow root. From outside only the host is visible, and the
  // host raises nothing — which is why the walk below exists.
  assert.equal(raisesSoftKeyboard({ tagName: 'CONTINUITY-EDITOR' }), false)
})

test('focus that raises no keyboard is left alone', () => {
  assert.equal(raisesSoftKeyboard(null), false)
  assert.equal(raisesSoftKeyboard(undefined), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'BODY' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'BUTTON' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'DIV' }), false)
  // Toggles, sliders, and pickers are focusable but keyboardless.
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'checkbox' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'range' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'color' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'file' }), false)
})

test('readonly fields and inputMode="none" opt out', () => {
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'text', readOnly: true }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'TEXTAREA', readOnly: true }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'TEXTAREA', inputMode: 'none' }), false)
  assert.equal(raisesSoftKeyboard({ tagName: 'DIV', isContentEditable: true, inputMode: 'none' }), false)
})

test('tag matching is case-insensitive, as DOM tagName is uppercase', () => {
  assert.equal(raisesSoftKeyboard({ tagName: 'textarea' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'input', type: 'TEXT' }), true)
  assert.equal(raisesSoftKeyboard({ tagName: 'INPUT', type: 'CHECKBOX' }), false)
})

test('focus in the light DOM is returned unchanged', () => {
  const field = { tagName: 'TEXTAREA' }
  assert.equal(deepActiveElement({ activeElement: field }), field)
  assert.equal(deepActiveElement({ activeElement: null }), null)
  assert.equal(deepActiveElement({}), null)
})

test('the walk descends into open shadow roots', () => {
  // What the Continuity editor looks like from `document`: the host is what
  // `document.activeElement` reports, and the textarea is what holds the keyboard.
  const input = { tagName: 'TEXTAREA' }
  const host = { tagName: 'CONTINUITY-EDITOR', shadowRoot: { activeElement: input } }
  assert.equal(deepActiveElement({ activeElement: host }), input)
  assert.equal(raisesSoftKeyboard(deepActiveElement({ activeElement: host })), true)
})

test('the walk descends through nested shadow roots', () => {
  const input = { tagName: 'INPUT', type: 'text' }
  const inner = { tagName: 'INNER-WIDGET', shadowRoot: { activeElement: input } }
  const outer = { tagName: 'OUTER-WIDGET', shadowRoot: { activeElement: inner } }
  assert.equal(deepActiveElement({ activeElement: outer }), input)
})

test('a host with nothing focused inside it is the answer', () => {
  // A closed shadow root reports no `activeElement`, and neither does an open one whose
  // focus sits on the host itself. Both stop at the host, which is what
  // `document.activeElement` says on its own — the best available answer, not a failure.
  const closed = { tagName: 'SEALED-WIDGET', shadowRoot: null }
  assert.equal(deepActiveElement({ activeElement: closed }), closed)
  const empty = { tagName: 'OPEN-WIDGET', shadowRoot: { activeElement: null } }
  assert.equal(deepActiveElement({ activeElement: empty }), empty)
})

test('a cyclic shadow tree terminates', () => {
  // Cannot happen in a real DOM; the depth cap is there so a mocked or malformed tree
  // cannot hang the touch handler that calls this on every two-finger gesture.
  const scope: FocusScope = { activeElement: null }
  const host: FocusedField = { tagName: 'LOOPING-WIDGET', shadowRoot: scope }
  scope.activeElement = { tagName: 'OTHER-WIDGET', shadowRoot: { activeElement: host } }
  assert.ok(deepActiveElement({ activeElement: host }))
})
