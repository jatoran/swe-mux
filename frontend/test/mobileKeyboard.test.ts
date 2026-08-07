import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SOFT_KEYBOARD_MIN_INSET_PX,
  deepActiveElement,
  nextPeekState,
  peekToggleVisible,
  raisesSoftKeyboard,
  softKeyboardInset,
  softKeyboardLost,
} from '../src/mobileKeyboard.ts'
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

test('a gesture that dropped focus onto nothing has to hand the keyboard back', () => {
  const bridge = { tagName: 'TEXTAREA' }
  // The terminal's live input lost focus to the platform's own handling of a drag or
  // long-press over the (non-editable) terminal body. Nothing typed, so put it back.
  assert.equal(softKeyboardLost(bridge, null), true)
  assert.equal(softKeyboardLost(bridge, { tagName: 'BODY' }), true)
  // The jump-to-latest chip, when its press was allowed to take focus.
  assert.equal(softKeyboardLost(bridge, { tagName: 'BUTTON' }), true)
})

test('nothing is restored when the gesture left focus alone', () => {
  const bridge = { tagName: 'TEXTAREA' }
  assert.equal(softKeyboardLost(bridge, bridge), false)
  // The keyboard was already down before the gesture: there is nothing to hand back, and
  // raising one the user never asked for is the failure this whole path exists to avoid.
  assert.equal(softKeyboardLost(null, null), false)
  assert.equal(softKeyboardLost(null, bridge), false)
  assert.equal(softKeyboardLost(undefined, bridge), false)
})

test('focus that moved to another text field kept the keyboard, so it stands', () => {
  // Android holds the keyboard up across this move, so nothing was lost — and restoring
  // would yank the user back out of the field the gesture just reached.
  const bridge = { tagName: 'TEXTAREA' }
  assert.equal(softKeyboardLost(bridge, { tagName: 'INPUT', type: 'search' }), false)
  assert.equal(softKeyboardLost(bridge, { tagName: 'DIV', isContentEditable: true }), false)
  // A readonly or `inputMode="none"` field raises nothing, so landing there is a loss.
  assert.equal(softKeyboardLost(bridge, { tagName: 'TEXTAREA', readOnly: true }), true)
})

test('the keyboard inset is what the visual viewport lost, not a new layout height', () => {
  // A 915px layout viewport with 415px of keyboard over it. The layout viewport itself is
  // untouched under `interactive-widget=resizes-visual`, which is what keeps every terminal
  // grid — and so every PTY — the size it already was.
  assert.equal(softKeyboardInset(915, 500), 415)
  assert.equal(softKeyboardInset(915, 915), 0)
})

test('browser chrome and pinch-zoom are not keyboards', () => {
  // An address bar collapsing moves the visual viewport ~50-60px. Sliding the workspace up
  // by that would look like the UI twitching every time the page scrolled.
  assert.equal(softKeyboardInset(915, 860), 0)
  assert.equal(softKeyboardInset(915, 914), 0)
  // The threshold is inclusive: no soft keyboard is smaller than this, so anything at or
  // above it is one.
  assert.equal(softKeyboardInset(915, 915 - SOFT_KEYBOARD_MIN_INSET_PX), SOFT_KEYBOARD_MIN_INSET_PX)
  assert.equal(softKeyboardInset(915, 915 - SOFT_KEYBOARD_MIN_INSET_PX + 1), 0)
})

test('a visual viewport larger than the layout never slides the workspace down', () => {
  // Pinch-zoom out and rounding can invert the two. A negative inset would translate the
  // workspace *down*, off the bottom of the screen.
  assert.equal(softKeyboardInset(915, 1000), 0)
  assert.equal(softKeyboardInset(Number.NaN, 500), 0)
  assert.equal(softKeyboardInset(915, Number.NaN), 0)
})

test('peeking at the top of a grid survives output and ends on input', () => {
  assert.equal(nextPeekState(false, 'toggle'), true)
  assert.equal(nextPeekState(true, 'toggle'), false)
  // The one that matters, and the one easiest to get backwards: a streaming reply is exactly
  // when a reader is peeking, so writes must not drag them back to the composer.
  assert.equal(nextPeekState(true, 'output'), true)
  assert.equal(nextPeekState(false, 'output'), false)
  // Typing means they have stopped reading, and the caret is at the composer.
  assert.equal(nextPeekState(true, 'input'), false)
  // Without the keyboard the whole grid fits, so there is no slice left to move.
  assert.equal(nextPeekState(true, 'keyboardClosed'), false)
  assert.equal(nextPeekState(false, 'keyboardClosed'), false)
})

test('the peek toggle appears for a reader, not for every raised keyboard', () => {
  // At the composer of a conversation with any length the button is clutter:
  // everything the slide hides is reachable by scrolling.
  assert.equal(peekToggleVisible(415, false, false, false), false)
  // Scrolled off the tail on either axis means reading, and reading is when
  // the hidden top matters. The app-held viewport's estimate counts forwarded
  // drags even when the app moved nothing, so a fresh session's first swipe
  // up toward a reply trapped under the keyboard summons the control.
  assert.equal(peekToggleVisible(415, false, true, false), true)
  assert.equal(peekToggleVisible(415, false, false, true), true)
  // An active peek always keeps its toggle: it is the way back down.
  assert.equal(peekToggleVisible(415, true, false, false), true)
  // No keyboard, no slide, no button — whatever the scroll state says.
  assert.equal(peekToggleVisible(0, true, true, true), false)
})

test('a cyclic shadow tree terminates', () => {
  // Cannot happen in a real DOM; the depth cap is there so a mocked or malformed tree
  // cannot hang the touch handler that calls this on every two-finger gesture.
  const scope: FocusScope = { activeElement: null }
  const host: FocusedField = { tagName: 'LOOPING-WIDGET', shadowRoot: scope }
  scope.activeElement = { tagName: 'OTHER-WIDGET', shadowRoot: { activeElement: host } }
  assert.ok(deepActiveElement({ activeElement: host }))
})
