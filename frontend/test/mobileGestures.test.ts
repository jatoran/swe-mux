import assert from 'node:assert/strict'
import test from 'node:test'
import {
  classifyGesture,
  defaultMobileGestureSettings,
  GESTURE_SLOTS,
  mobileGestureSettings,
  pathOwnsHorizontalScroll,
  resolveGestureCommand,
  swipeAwayCloseEnabled,
} from '../src/mobileGestures.ts'

type FakeElement = {
  className: string
  scrollWidth: number
  clientWidth: number
  overflowX: string
  matches: (selector: string) => boolean
}

function fakeElement(className: string, scrollWidth: number, clientWidth: number, overflowX: string): FakeElement {
  return {
    className,
    scrollWidth,
    clientWidth,
    overflowX,
    matches: selector => selector.split(',').some(token => token.trim() === `.${className}`),
  }
}

test('single-finger horizontal swipes map to tab navigation slots', () => {
  assert.equal(classifyGesture({ pointerCount: 1, dx: -80, dy: 10, durationMs: 120 }), 'swipe_left')
  assert.equal(classifyGesture({ pointerCount: 1, dx: 80, dy: -10, durationMs: 120 }), 'swipe_right')
})

test('a composed path exposes an overflowing scroller inside a shadow root', () => {
  const button = fakeElement('command-rail-button', 48, 48, 'visible')
  const shadowStrip = fakeElement('command-rail-buttons', 520, 300, 'auto')
  const host = fakeElement('note-editor', 390, 390, 'hidden')
  const overflowX = (element: FakeElement) => element.overflowX

  // A window listener's retargeted event.target sees only the host and misses
  // the internal strip. TouchEvent.composedPath() includes both.
  assert.equal(pathOwnsHorizontalScroll([host], overflowX), false)
  assert.equal(pathOwnsHorizontalScroll([button, shadowStrip, host], overflowX), true)
})

test('horizontal scroll ownership requires overflow or a registered strip', () => {
  const fittingStrip = fakeElement('command-rail-buttons', 300, 300, 'auto')
  const registeredStrip = fakeElement('terminal-action-rail', 300, 300, 'hidden')
  const overflowX = (element: FakeElement) => element.overflowX

  assert.equal(pathOwnsHorizontalScroll([fittingStrip], overflowX), false)
  assert.equal(pathOwnsHorizontalScroll([registeredStrip], overflowX), true)
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

test('two-finger vertical swipes resolve, single-finger vertical still does not', () => {
  assert.equal(classifyGesture({ pointerCount: 2, dx: 10, dy: -90, durationMs: 220 }), 'two_finger_swipe_up')
  assert.equal(classifyGesture({ pointerCount: 2, dx: 10, dy: 90, durationMs: 220 }), 'two_finger_swipe_down')
  // Unbounded duration: a two-finger drag has no competing selection gesture.
  assert.equal(classifyGesture({ pointerCount: 2, dx: 0, dy: 120, durationMs: 3000 }), 'two_finger_swipe_down')
  // The single-finger vertical channel stays with the terminal.
  assert.equal(classifyGesture({ pointerCount: 1, dx: 10, dy: -120, durationMs: 200 }), null)
})

test('two-finger diagonals that fail the axis ratio stay unclassified', () => {
  assert.equal(classifyGesture({ pointerCount: 2, dx: 70, dy: 62, durationMs: 220 }), null)
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
  assert.equal(GESTURE_SLOTS.length, 7)
  assert.equal(overridden.two_finger_swipe_down, defaultMobileGestureSettings.two_finger_swipe_down)
})

test('swipe-away override closes the open panel instead of running the binding', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, true), 'mobileTab.previous')
  const drawer = { sidebarOpen: false, drawerOpen: true }
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, drawer, true), 'drawer.toggle')
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, drawer, true), 'drawer.toggle')
  // Swipes toward an open panel keep their binding (the drawer's own toggle
  // direction already closes it from over the scrim).
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, drawer, true), 'mobileTab.next')
  const sidebar = { sidebarOpen: true, drawerOpen: false }
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, sidebar, true), 'sidebar.close')
  assert.equal(resolveGestureCommand('two_finger_swipe_left', defaultMobileGestureSettings, sidebar, true), 'sidebar.close')
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, sidebar, true), 'sidebar.toggle')
  // Non-horizontal slots are never overridden.
  assert.equal(resolveGestureCommand('two_finger_tap', defaultMobileGestureSettings, drawer, true), 'palette.open')
})

test('swipe-away override covers unbound slots, prefers the drawer when both are open, and can be turned off', () => {
  const drawer = { sidebarOpen: false, drawerOpen: true }
  const unbound = { ...defaultMobileGestureSettings, swipe_right: '' }
  assert.equal(resolveGestureCommand('swipe_right', unbound, drawer, true), 'drawer.toggle')
  const both = { sidebarOpen: true, drawerOpen: true }
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, both, true), 'drawer.toggle')
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, both, true), 'sidebar.close')
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, drawer, false), 'mobileTab.previous')
})

test('swipe-away close is on unless the config explicitly disables it', () => {
  assert.equal(swipeAwayCloseEnabled({}), true)
  assert.equal(swipeAwayCloseEnabled({ mobile_gesture_swipe_away_close: true }), true)
  assert.equal(swipeAwayCloseEnabled({ mobile_gesture_swipe_away_close: false }), false)
})
