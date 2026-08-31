import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SOFT_KEYBOARD_MIN_INSET_PX,
  TOUCH_COMPAT_MOUSE_WINDOW_MS,
  VISIBLE_PAINTED_ROWS_WORTH_READING,
  clampPeekOffset,
  deepActiveElement,
  hiddenOutputDeservesPeek,
  inputEndsPeek,
  nextPeekOffset,
  peekToggleVisible,
  raisesSoftKeyboard,
  shouldHoldBridgeFocus,
  softKeyboardInputMode,
  softKeyboardInset,
  softKeyboardLost,
  softKeyboardVisualOffset,
  touchCompatMouseEvent,
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

test('xterm\'s helper textarea taking focus is a keyboard loss, not a keyboard move', () => {
  // The invariant TerminalPane sets on a touch device: xterm's own helper textarea gets
  // `inputmode="none"`, so the mux IME bridge is the only element that can raise a
  // keyboard. Without it the helper reads as a field legitimately holding the keyboard —
  // xterm's `mousedown` handler is `preventDefault(); this.focus()`, so any press reaching
  // xterm lands focus there — and every predicate here answers "nothing was lost" while
  // mobile input has quietly stopped being routed through the bridge.
  const bridge = { tagName: 'TEXTAREA' }
  const helper = { tagName: 'TEXTAREA', inputMode: 'none' }
  assert.equal(softKeyboardLost(bridge, helper), true)
  // What it would have answered without the invariant, which is why this is worth pinning.
  assert.equal(softKeyboardLost(bridge, { tagName: 'TEXTAREA' }), false)
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

test('a touch gesture owns the mouse events the platform replays after it', () => {
  // In flight: the replay for a press this pane already handled as a pointer event.
  assert.equal(touchCompatMouseEvent({ gestureActive: true, endedAt: null, now: 5_000 }), true)
  // The common case — the replay lands a few milliseconds after the finger lifts.
  assert.equal(touchCompatMouseEvent({ gestureActive: false, endedAt: 5_000, now: 5_010 }), true)
})

test('the compat-mouse window is measured from the end of a gesture, not its start', () => {
  // The regression this encodes: the window used to run from the *start* of the gesture,
  // which made it a cap on how long a hold was allowed to be. A 450ms long-press plus a
  // careful selection drag outran it, the replayed press reached xterm's
  // `preventDefault(); this.focus()`, and the keyboard came up on its own — so short
  // gestures behaved and long ones did not, which is what read as intermittent.
  const heldFor = 4_000
  const endedAt = 1_000 + heldFor
  assert.equal(touchCompatMouseEvent({ gestureActive: false, endedAt, now: endedAt + 10 }), true)
  // Past the window it is a real mouse again, however the gesture went.
  assert.equal(
    touchCompatMouseEvent({ gestureActive: false, endedAt, now: endedAt + TOUCH_COMPAT_MOUSE_WINDOW_MS }),
    false,
  )
})

test('no touch gesture, or a clock that went backwards, means a real mouse', () => {
  assert.equal(touchCompatMouseEvent({ gestureActive: false, endedAt: null, now: 5_000 }), false)
  // A monotonic clock cannot do this, but a caller passing a wall clock could, and
  // "the gesture ended in the future" must not suppress every press from here on.
  assert.equal(touchCompatMouseEvent({ gestureActive: false, endedAt: 9_000, now: 5_000 }), false)
  assert.equal(touchCompatMouseEvent({ gestureActive: false, endedAt: Number.NaN, now: 5_000 }), false)
})

test('the bridge refuses to be blurred while it is holding the keyboard through a gesture', () => {
  const held = { holding: true, dismissalsAtGestureStart: 3, dismissals: 3 }
  // The platform's own focus move as a touch resolves against non-editable content —
  // relatedTarget is usually null for it, and the terminal body when it is not.
  assert.equal(shouldHoldBridgeFocus({ ...held, incoming: null }), true)
  assert.equal(shouldHoldBridgeFocus({ ...held, incoming: { tagName: 'DIV' } }), true)
  // xterm's helper textarea, which `inputmode="none"` makes keyboard-neutral. Without that
  // this would read as a legitimate field and the hold would stand down for it.
  assert.equal(shouldHoldBridgeFocus({ ...held, incoming: { tagName: 'TEXTAREA', inputMode: 'none' } }), true)
})

test('the focus hold can only ever keep a keyboard, never raise one', () => {
  // `holding` is set only when the bridge itself held the keyboard as the finger landed.
  // With it false the hold is inert, which is what makes a gesture that began with the
  // keyboard down incapable of opening one — the direction that had no guard at all.
  assert.equal(shouldHoldBridgeFocus({
    holding: false, dismissalsAtGestureStart: 3, dismissals: 3, incoming: null,
  }), false)
})

test('a deliberate dismissal mid-gesture outranks holding the keyboard', () => {
  // A mobile panel opened over the terminal and dismissed the keyboard on the way in. The
  // swipe that opened it crossed this grid, so the pane sees a focus loss it would
  // otherwise repair — and would slide the panel in over a keyboard it just closed.
  assert.equal(shouldHoldBridgeFocus({
    holding: true, dismissalsAtGestureStart: 3, dismissals: 4, incoming: null,
  }), false)
})

test('focus heading for a real field is a move something made on purpose', () => {
  const held = { holding: true, dismissalsAtGestureStart: 0, dismissals: 0 }
  // Fighting this would trap focus on the terminal and take the keyboard with it.
  assert.equal(shouldHoldBridgeFocus({ ...held, incoming: { tagName: 'INPUT', type: 'text' } }), false)
  assert.equal(shouldHoldBridgeFocus({ ...held, incoming: { tagName: 'DIV', isContentEditable: true } }), false)
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

test('the visual viewport scroll is subtracted from what the keyboard covers', () => {
  // Chrome scrolls the visual viewport to lift a focused field above the keys, because
  // `overflow:hidden` on the document and `position:fixed` on the panels leave it nothing
  // else to scroll. A surface that then shortens by the full inset stops that many pixels
  // short of the screen, and the strip below it shows the workspace through the scrim -
  // the black band under the drawer's prompt editor. 415 of keyboard with 120 already
  // scrolled away leaves 295 still to give up.
  assert.equal(softKeyboardVisualOffset(120, 415), 120)
  assert.equal(softKeyboardVisualOffset(0, 415), 0)
})

test('the visual scroll can never exceed the keyboard it compensates for', () => {
  // The clamp is not defensive tidying: `--keyboard-cover` subtracts this from the inset,
  // so an over-large value would *grow* a surface past full height instead of shortening
  // it, and a negative one would grow it too.
  assert.equal(softKeyboardVisualOffset(600, 415), 415)
  assert.equal(softKeyboardVisualOffset(-40, 415), 0)
  // Fractional device pixels round the same way the inset does, so the two always subtract
  // to a whole number.
  assert.equal(softKeyboardVisualOffset(119.6, 415), 120)
})

test('no keyboard means no scroll to compensate for', () => {
  // A scrolled visual viewport with no keyboard is browser chrome or pinch-zoom, and
  // nothing has shortened for it. Reporting an offset there would shorten a surface that
  // had no reason to move.
  assert.equal(softKeyboardVisualOffset(120, 0), 0)
  assert.equal(softKeyboardVisualOffset(120, Number.NaN), 0)
  assert.equal(softKeyboardVisualOffset(Number.NaN, 415), 0)
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

test('forwarded scroll reports are reading, not typing, so they do not end a peek', () => {
  // A drag that pans the peek to its end chains into wheel reports through the same
  // onData path as a keystroke; counting them as input snapped the peek back mid-gesture.
  assert.equal(inputEndsPeek('\x1b[<64;12;5M'), false)
  assert.equal(inputEndsPeek('\x1b[<65;12;5M'), false)
  assert.equal(inputEndsPeek('\x1b[<64;12;5M\x1b[<64;12;5M'), false)
  // Everything actually typed still returns the reader to the composer.
  assert.equal(inputEndsPeek('a'), true)
  assert.equal(inputEndsPeek('\r'), true)
  assert.equal(inputEndsPeek('\x1b'), true)
  // A non-wheel mouse report is the reader acting, the same statement a keystroke makes.
  assert.equal(inputEndsPeek('\x1b[<0;5;6M'), true)
  // A modified wheel is an application chord, not flick traffic — the pacer excludes it
  // and so does this, deliberately on the same classification.
  assert.equal(inputEndsPeek('\x1b[<68;12;5M'), true)
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
