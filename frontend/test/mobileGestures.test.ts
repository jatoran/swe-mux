import assert from 'node:assert/strict'
import test from 'node:test'
import {
  classifyGesture,
  defaultMobileGestureSettings,
  GESTURE_SLOTS,
  mobileGestureSettings,
} from '../src/mobileGestures.ts'

test('single-finger horizontal swipes map to tab navigation slots', () => {
  assert.equal(classifyGesture({ pointerCount: 1, dx: -80, dy: 10, durationMs: 120 }), 'swipe_left')
  assert.equal(classifyGesture({ pointerCount: 1, dx: 80, dy: -10, durationMs: 120 }), 'swipe_right')
})

test('vertical single-finger drags are left to the terminal', () => {
  assert.equal(classifyGesture({ pointerCount: 1, dx: 10, dy: 120, durationMs: 200 }), null)
  // A short horizontal nudge below threshold is not a swipe.
  assert.equal(classifyGesture({ pointerCount: 1, dx: 30, dy: 4, durationMs: 90 }), null)
  // Diagonal that fails the axis ratio is ignored so scrolling is never hijacked.
  assert.equal(classifyGesture({ pointerCount: 1, dx: 60, dy: 55, durationMs: 150 }), null)
})

test('single-finger taps never trigger the two-finger tap action', () => {
  assert.equal(classifyGesture({ pointerCount: 1, dx: 2, dy: 2, durationMs: 100 }), null)
})

test('a slow single-finger horizontal drag is a text selection, not a swipe', () => {
  // Long-press then drag (>1.5s total) must not switch tabs.
  assert.equal(classifyGesture({ pointerCount: 1, dx: -120, dy: 8, durationMs: 2000 }), null)
})

test('two-finger gestures resolve tap and directional swipes', () => {
  assert.equal(classifyGesture({ pointerCount: 2, dx: 4, dy: 6, durationMs: 180 }), 'two_finger_tap')
  assert.equal(classifyGesture({ pointerCount: 2, dx: -90, dy: 12, durationMs: 220 }), 'two_finger_swipe_left')
  assert.equal(classifyGesture({ pointerCount: 2, dx: 90, dy: 12, durationMs: 220 }), 'two_finger_swipe_right')
})

test('a slow or long two-finger press is not a tap', () => {
  assert.equal(classifyGesture({ pointerCount: 2, dx: 4, dy: 6, durationMs: 900 }), null)
  assert.equal(classifyGesture({ pointerCount: 2, dx: 40, dy: 6, durationMs: 200 }), null)
})

test('gesture settings fall back to opinionated defaults and accept overrides', () => {
  assert.deepEqual(mobileGestureSettings({}), defaultMobileGestureSettings)
  const overridden = mobileGestureSettings({
    mobile_gestures: { swipe_left: 'palette.open', two_finger_tap: '', bogus_slot: 'x' },
  })
  assert.equal(overridden.swipe_left, 'palette.open')
  assert.equal(overridden.two_finger_tap, '')
  assert.equal(overridden.swipe_right, defaultMobileGestureSettings.swipe_right)
  assert.equal(GESTURE_SLOTS.length, 5)
})
