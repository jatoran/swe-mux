import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BACK_COMMAND,
  classifyGesture,
  classifyRailGesture,
  defaultMobileGestureSettings,
  GESTURE_SLOTS,
  gestureOverlayDepth,
  mobileGestureSettings,
  overlayBackEnabled,
  pathOwnsHorizontalScroll,
  pathOwnsRailGesture,
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
  const drawerTabs = fakeElement('drawer-tabs', 300, 300, 'hidden')
  const manualDragRail = fakeElement('overflow-rail-touch-drag', 300, 300, 'hidden')
  const overflowX = (element: FakeElement) => element.overflowX

  assert.equal(pathOwnsHorizontalScroll([fittingStrip], overflowX), false)
  assert.equal(pathOwnsHorizontalScroll([registeredStrip], overflowX), true)
  assert.equal(pathOwnsHorizontalScroll([drawerTabs], overflowX), true)
  assert.equal(pathOwnsHorizontalScroll([manualDragRail], overflowX), true)
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
  assert.equal(GESTURE_SLOTS.length, 8)
  assert.equal(overridden.two_finger_swipe_down, defaultMobileGestureSettings.two_finger_swipe_down)
  // A slot added after a config was saved arrives at its default rather than disabled.
  assert.equal(overridden.rail_swipe_up, 'menu.toggle')
})

test('the command rail is recognized from the composed path, and only that rail', () => {
  const railChip = fakeElement('term-key', 30, 30, 'visible')
  const rail = fakeElement('terminal-action-rail', 520, 300, 'hidden')
  const noteRail = fakeElement('overflow-rail-touch-drag', 520, 300, 'auto')
  const drawerTabs = fakeElement('drawer-tabs', 520, 300, 'auto')

  assert.equal(pathOwnsRailGesture([railChip, rail]), true)
  // The note editor's own strip and the drawer's tab bar are horizontal scrollers too,
  // and both keep the older rule: a vertical drag there is not a swe-mux gesture.
  assert.equal(pathOwnsRailGesture([noteRail]), false)
  assert.equal(pathOwnsRailGesture([drawerTabs]), false)
})

test('a single-finger upward swipe on the command rail is a slot of its own', () => {
  assert.equal(classifyRailGesture({ pointerCount: 1, dx: 8, dy: -90, durationMs: 200 }), 'rail_swipe_up')
  // Downward is not the same gesture, and the rail keeps every horizontal touch for its
  // own pan — so nothing else the rail sees resolves at all.
  assert.equal(classifyRailGesture({ pointerCount: 1, dx: 8, dy: 90, durationMs: 200 }), null)
  assert.equal(classifyRailGesture({ pointerCount: 1, dx: -90, dy: 8, durationMs: 200 }), null)
  assert.equal(classifyRailGesture({ pointerCount: 2, dx: 8, dy: -90, durationMs: 200 }), null)
})

test('the rail swipe answers to the same distance, axis and duration bars as every other', () => {
  assert.equal(classifyRailGesture({ pointerCount: 1, dx: 4, dy: -30, durationMs: 120 }), null)
  assert.equal(classifyRailGesture({ pointerCount: 1, dx: 60, dy: -62, durationMs: 150 }), null)
  // A long press that then travels is a hold-to-repeat or a drag, not a flick.
  assert.equal(classifyRailGesture({ pointerCount: 1, dx: 4, dy: -120, durationMs: 2000 }), null)
})

test('the rail slot resolves through the ordinary rules, overlay suppression included', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  assert.equal(resolveGestureCommand('rail_swipe_up', defaultMobileGestureSettings, closed, true), 'menu.toggle')
  // Rebinding is the whole point of it being a slot.
  const rebound = { ...defaultMobileGestureSettings, rail_swipe_up: 'palette.open' }
  assert.equal(resolveGestureCommand('rail_swipe_up', rebound, closed, true), 'palette.open')
  // It is not a horizontal slot, so an open panel never repurposes it...
  const sidebar = { sidebarOpen: true, drawerOpen: false }
  assert.equal(resolveGestureCommand('rail_swipe_up', defaultMobileGestureSettings, sidebar, true), 'menu.toggle')
  // ...and an overlay painted over the workspace suppresses it like every non-back slot.
  assert.equal(resolveGestureCommand('rail_swipe_up', defaultMobileGestureSettings, closed, true, { depth: 1, enabled: true }), '')
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
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, sidebar, true), 'sidebar.close')
  assert.equal(resolveGestureCommand('two_finger_swipe_left', defaultMobileGestureSettings, sidebar, true), 'sidebar.close')
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, sidebar, true), 'sidebar.close')
  // Non-horizontal slots are never overridden.
  assert.equal(resolveGestureCommand('two_finger_tap', defaultMobileGestureSettings, drawer, true), 'palette.open')
})

test('the sidebar dismiss level does not shadow either sidebar-close direction', () => {
  const sidebar = { sidebarOpen: true, drawerOpen: false }
  const settings = {
    ...defaultMobileGestureSettings,
    swipe_left: 'drawer.toggle',
    swipe_right: 'sidebar.toggle',
  }
  const layeredSidebar = { depth: gestureOverlayDepth(1, sidebar), enabled: true }
  assert.equal(resolveGestureCommand('swipe_left', settings, sidebar, true, layeredSidebar), 'sidebar.close')
  assert.equal(resolveGestureCommand('swipe_right', settings, sidebar, true, layeredSidebar), 'sidebar.close')
})

test('gesture overlay depth excludes slide-in panel dismiss levels only', () => {
  assert.equal(gestureOverlayDepth(1, { sidebarOpen: true, drawerOpen: false }), 0)
  assert.equal(gestureOverlayDepth(2, { sidebarOpen: true, drawerOpen: true }), 0)
  assert.equal(gestureOverlayDepth(2, { sidebarOpen: true, drawerOpen: false }), 1)
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

test('overlay back is on unless the config explicitly disables it', () => {
  assert.equal(overlayBackEnabled({}), true)
  assert.equal(overlayBackEnabled({ mobile_gesture_overlay_back: true }), true)
  assert.equal(overlayBackEnabled({ mobile_gesture_overlay_back: false }), false)
})

test('an open overlay turns the rightward swipe into back', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const layered = { depth: 1, enabled: true }
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, true, layered), BACK_COMMAND)
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, closed, true, layered), BACK_COMMAND)
})

test('an open overlay suppresses every other slot rather than reassigning it', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const layered = { depth: 1, enabled: true }
  // The danger this prevents: a swipe inside a modal running its workspace binding and
  // changing tabs invisibly behind the modal.
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, closed, true, layered), '')
  assert.equal(resolveGestureCommand('two_finger_swipe_left', defaultMobileGestureSettings, closed, true, layered), '')
  assert.equal(resolveGestureCommand('two_finger_swipe_up', defaultMobileGestureSettings, closed, true, layered), '')
  assert.equal(resolveGestureCommand('two_finger_swipe_down', defaultMobileGestureSettings, closed, true, layered), '')
  assert.equal(resolveGestureCommand('two_finger_tap', defaultMobileGestureSettings, closed, true, layered), '')
})

test('an overlay outranks an open panel, and disabling overlay back restores immunity', () => {
  const both = { sidebarOpen: true, drawerOpen: true }
  // A modal is painted over the panels, so the swipe is about the modal, not the drawer.
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, both, true, { depth: 1, enabled: true }), BACK_COMMAND)
  // Turned off, an overlay goes back to swallowing gestures entirely — it does not fall
  // through to the panel override or the slot's binding.
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, both, true, { depth: 1, enabled: false }), '')
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, both, true, { depth: 1, enabled: false }), '')
})

test('with nothing layered the existing resolution is untouched', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const drawer = { sidebarOpen: false, drawerOpen: true }
  const none = { depth: 0, enabled: true }
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, true, none), 'mobileTab.previous')
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, drawer, true, none), 'drawer.toggle')
  // Callers that predate the overlay argument keep their behaviour.
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, true), 'mobileTab.previous')
})
test('an overlay with a left panel borrows the workspace sidebar’s gesture to open it', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const panel = { open: false, toggle: 'settingsNav.toggle', close: 'settingsNav.close' }
  const layered = { depth: 1, enabled: true, panel }
  // Whichever slot works the workspace sidebar works this one. With the defaults that is
  // the two-finger rightward swipe.
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, closed, true, layered), 'settingsNav.toggle')
  // ...and the back swipe is untouched, so backing out of the overlay still works.
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, true, layered), BACK_COMMAND)
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, closed, true, layered), '')
  assert.equal(resolveGestureCommand('two_finger_tap', defaultMobileGestureSettings, closed, true, layered), '')
})

test('rebinding the workspace sidebar moves the overlay panel with it', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const panel = { open: false, toggle: 'settingsNav.toggle', close: 'settingsNav.close' }
  const layered = { depth: 1, enabled: true, panel }
  const rebound = { ...defaultMobileGestureSettings, two_finger_swipe_right: 'palette.open', two_finger_swipe_up: 'sidebar.open' }
  assert.equal(resolveGestureCommand('two_finger_swipe_up', rebound, closed, true, layered), 'settingsNav.toggle')
  // The slot that used to open it no longer does, and falls back to the back swipe rule.
  assert.equal(resolveGestureCommand('two_finger_swipe_right', rebound, closed, true, layered), BACK_COMMAND)
})

test('an open overlay panel is closed by either horizontal direction', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const panel = { open: true, toggle: 'settingsNav.toggle', close: 'settingsNav.close' }
  const layered = { depth: 2, enabled: true, panel }
  for (const slot of ['swipe_left', 'swipe_right', 'two_finger_swipe_left', 'two_finger_swipe_right'] as const) {
    assert.equal(resolveGestureCommand(slot, defaultMobileGestureSettings, closed, true, layered), 'settingsNav.close')
  }
  // Vertical slots stay inert over an overlay, open panel or not.
  assert.equal(resolveGestureCommand('two_finger_swipe_up', defaultMobileGestureSettings, closed, true, layered), '')
})

test('with swipe-away off, only the sidebar slot closes the overlay panel and back still pops it', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const panel = { open: true, toggle: 'settingsNav.toggle', close: 'settingsNav.close' }
  const layered = { depth: 2, enabled: true, panel }
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, closed, false, layered), 'settingsNav.close')
  // The panel is the top dismiss level, so back closes it too - same outcome, other route.
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, false, layered), BACK_COMMAND)
  assert.equal(resolveGestureCommand('swipe_left', defaultMobileGestureSettings, closed, false, layered), '')
})

test('turning overlay back off keeps the overlay panel out of reach as well', () => {
  const closed = { sidebarOpen: false, drawerOpen: false }
  const layered = { depth: 1, enabled: false, panel: { open: false, toggle: 'settingsNav.toggle', close: 'settingsNav.close' } }
  // "A dialog ignored every swipe" is the behaviour this switch restores, and a panel
  // inside the dialog is not an exemption from it.
  assert.equal(resolveGestureCommand('two_finger_swipe_right', defaultMobileGestureSettings, closed, true, layered), '')
  assert.equal(resolveGestureCommand('swipe_right', defaultMobileGestureSettings, closed, true, layered), '')
})
