import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SOFT_KEYBOARD_MIN_INSET_PX,
  VISIBLE_PAINTED_ROWS_WORTH_READING,
  clampPeekOffset,
  deepActiveElement,
  hiddenOutputDeservesPeek,
  nextPeekOffset,
  peekToggleVisible,
  raisesSoftKeyboard,
  softKeyboardInputMode,
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

test('terminal action focus preserves keyboard visibility on coarse pointers', () => {
  assert.equal(softKeyboardInputMode(true, 0, false), 'none')
  assert.equal(softKeyboardInputMode(true, 415, false), 'text')
  assert.equal(softKeyboardInputMode(true, 0, true), 'text')
  // Desktop focus restoration remains ordinary text input regardless of viewport state.
  assert.equal(softKeyboardInputMode(false, 0, false), 'text')
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
  assert.equal(nextPeekOffset(0, 'toggle', 415), 415)
  assert.equal(nextPeekOffset(415, 'toggle', 415), 0)
  // A drag parks the window between the two ends, and the toggle from there is "put it back",
  // not "go further up" — the reader can already see the top of what they were reading.
  assert.equal(nextPeekOffset(200, 'toggle', 415), 0)
  // The one that matters, and the one easiest to get backwards: a streaming reply is exactly
  // when a reader is peeking, so writes must not drag them back to the composer.
  assert.equal(nextPeekOffset(415, 'output', 415), 415)
  assert.equal(nextPeekOffset(0, 'output', 415), 0)
  // Unless the writes landed in the half the keyboard is covering, which on a fresh session
  // is where a first reply paints — a pane that holds still there shows blank rows.
  assert.equal(nextPeekOffset(0, 'hiddenOutput', 415), 415)
  // Typing means they have stopped reading, and the caret is at the composer.
  assert.equal(nextPeekOffset(415, 'input', 415), 0)
  // Without the keyboard the whole grid fits, so there is no slice left to move.
  assert.equal(nextPeekOffset(415, 'keyboardClosed', 415), 0)
  assert.equal(nextPeekOffset(0, 'keyboardClosed', 415), 0)
})

test('a dragged peek is held inside the travel the keyboard covers', () => {
  assert.equal(clampPeekOffset(120, 415), 120)
  // Both ends are positions a reader parks at, so they clamp rather than springing back.
  assert.equal(clampPeekOffset(-40, 415), 0)
  assert.equal(clampPeekOffset(900, 415), 415)
  // No keyboard, no travel: the whole grid already fits.
  assert.equal(clampPeekOffset(120, 0), 0)
  assert.equal(clampPeekOffset(Number.NaN, 415), 0)
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

const hiddenOutput = {
  hiddenChanged: true,
  hiddenHasText: true,
  visiblePainted: 0,
  peekOffset: 0,
  sinceInputMs: null as number | null,
  inputGraceMs: 1500,
}

test('a reader with nothing on screen is moved to the output they cannot see', () => {
  // The case the trigger was written for: a first message and its reply both land in the
  // half the keyboard hides, on a grid whose visible half is blank.
  assert.equal(hiddenOutputDeservesPeek(hiddenOutput), true)
  assert.equal(
    hiddenOutputDeservesPeek({ ...hiddenOutput, visiblePainted: VISIBLE_PAINTED_ROWS_WORTH_READING }),
    true,
  )
})

test('a reader with something on screen is left alone', () => {
  // The bug: the condition was only ever stated as intent, so the jump fired for the whole
  // life of every session. An agent CLI repaints its whole screen constantly, so "the
  // hidden rows changed" is true on nearly every frame, and the pane scrolled itself to the
  // top by itself — most visibly right after a message, which is when the reader had
  // stopped typing long enough for the input grace to lapse.
  assert.equal(hiddenOutputDeservesPeek({ ...hiddenOutput, visiblePainted: 12 }), false)
  assert.equal(
    hiddenOutputDeservesPeek({
      ...hiddenOutput, visiblePainted: VISIBLE_PAINTED_ROWS_WORTH_READING + 1,
    }),
    false,
  )
})

test('nothing moves for a reader who is typing, or one already at the top', () => {
  assert.equal(hiddenOutputDeservesPeek({ ...hiddenOutput, sinceInputMs: 200 }), false)
  assert.equal(hiddenOutputDeservesPeek({ ...hiddenOutput, sinceInputMs: 1600 }), true)
  assert.equal(hiddenOutputDeservesPeek({ ...hiddenOutput, peekOffset: 200 }), false)
})

test('nothing moves for an unchanged or blank hidden region', () => {
  assert.equal(hiddenOutputDeservesPeek({ ...hiddenOutput, hiddenChanged: false }), false)
  assert.equal(hiddenOutputDeservesPeek({ ...hiddenOutput, hiddenHasText: false }), false)
})

test('a cyclic shadow tree terminates', () => {
  // Cannot happen in a real DOM; the depth cap is there so a mocked or malformed tree
  // cannot hang the touch handler that calls this on every two-finger gesture.
  const scope: FocusScope = { activeElement: null }
  const host: FocusedField = { tagName: 'LOOPING-WIDGET', shadowRoot: scope }
  scope.activeElement = { tagName: 'OTHER-WIDGET', shadowRoot: { activeElement: host } }
  assert.ok(deepActiveElement({ activeElement: host }))
})
