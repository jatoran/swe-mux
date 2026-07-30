import assert from 'node:assert/strict'
import test from 'node:test'
import { horizontalWheelDelta, WHEEL_LINE_PX } from '../src/wheelScroll.ts'

const overflowing = { scrollWidth: 900, clientWidth: 300 }

test('a plain wheel over an overflowing strip scrolls it sideways', () => {
  // Chromium's pixel mode: one notch is 100px and moves the strip by 100px.
  assert.equal(horizontalWheelDelta({ deltaX: 0, deltaY: 100 }, overflowing), 100)
  assert.equal(horizontalWheelDelta({ deltaX: 0, deltaY: -100 }, overflowing), -100)
})

test('a strip that fits keeps its wheel events', () => {
  // Nothing to scroll, so the event stays available to whatever else wants it.
  assert.equal(horizontalWheelDelta({ deltaX: 0, deltaY: 100 }, { scrollWidth: 300, clientWidth: 300 }), 0)
})

test('horizontal intent is left to the browser', () => {
  // Shift+wheel and trackpad swipes arrive as deltaX and already scroll natively;
  // adding deltaY on top would double the movement.
  assert.equal(horizontalWheelDelta({ deltaX: 120, deltaY: 0 }, overflowing), 0)
  assert.equal(horizontalWheelDelta({ deltaX: 120, deltaY: 40 }, overflowing), 0)
  // A mostly-vertical diagonal is still a vertical wheel.
  assert.equal(horizontalWheelDelta({ deltaX: 10, deltaY: 100 }, overflowing), 100)
})

test('line and page deltas are converted to pixels', () => {
  // Firefox reports 3 lines per notch; unscaled that would move the strip 3px.
  assert.equal(horizontalWheelDelta({ deltaX: 0, deltaY: 3, deltaMode: 1 }, overflowing), 3 * WHEEL_LINE_PX)
  assert.equal(horizontalWheelDelta({ deltaX: 0, deltaY: 1, deltaMode: 2 }, overflowing), 300)
})
