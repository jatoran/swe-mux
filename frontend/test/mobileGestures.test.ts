import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BACK_COMMAND,
  classifyGesture,
  classifyRailGesture,
  classifyRegionGesture,
  isHorizontalDirection,
  regionForPath,
  surfaceGestureFor,
  surfaceGesturesEnabled,
  defaultMobileGestureSettings,
  GESTURE_SHADOWING_SELECTORS,
  GESTURE_SLOTS,
  gestureOverlayDepth,
  mobileGestureSettings,
  overlayBackEnabled,
  pathOwnsHorizontalScroll,
  pathOwnsRailGesture,
  pathShadowsGesture,
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
  const drawerTabs = fakeElement('drawer-tabs', 520, 300, 'auto')

  assert.equal(pathOwnsRailGesture([railChip, rail]), true)
  // The drawer's tab bar is a horizontal scroller too, and keeps the older rule: a
  // vertical drag there is not a swe-mux gesture.
  assert.equal(pathOwnsRailGesture([drawerTabs]), false)
})

test('each region is recognized from the composed path, and everything else is not', () => {
  const region = (selector: string) => [{ matches: (query: string) => query === selector }]

  assert.equal(regionForPath(region('.terminal-action-rail')), 'commandRail')
  assert.equal(regionForPath(region('.mobile-unified-tabs')), 'tabRail')
  assert.equal(regionForPath(region('.voice-dock-head')), 'voiceDock')
  assert.equal(regionForPath(region('.mobile-project-name')), 'projectName')
  // Continuity's editor rail is named by its exported shadow part, not by the internal
  // class behind it. The Notes rail and Project-note header are ordinary light-DOM regions.
  assert.equal(regionForPath(region('[part~="command-rail"]')), 'noteRail')
  assert.equal(regionForPath(region('.notes-subtabs-row')), 'noteRail')
  assert.equal(regionForPath(region('.project-resource[data-resource-kind="note"]>header')), 'noteRail')
  assert.equal(regionForPath(region('.command-rail-buttons')), null)
  // The rest of the mobile top bar.
  assert.equal(regionForPath(region('.rail-quota')), 'quotaChip')
  assert.equal(regionForPath(region('.conversation-talk-toggle')), 'micToggle')
  assert.equal(regionForPath(region('.mobile-run-trigger')), 'runTrigger')
  assert.equal(regionForPath(region('.mobile-nav-toggle')), 'navToggle')
  assert.equal(regionForPath(region('.mobile-drawer-toggle')), 'drawerToggle')
  // The drawer tab bar and the desktop tab strip are scrollers, not regions.
  assert.equal(regionForPath(region('.drawer-tabs')), null)
  assert.equal(regionForPath(region('.stack-tabs')), null)
  assert.equal(regionForPath([]), null)
})

test('an overlay drawn over a region shadows it, and takes the whole touch', () => {
  const only = (selector: string) => ({ matches: (query: string) => query === selector })
  // The overflow popover is a DOM child of `.rail-row`, so the rail is genuinely in the
  // path behind it — and its chip grid scrolls vertically, which is the rail's own swipe.
  const inPopover = [only('.rail-overflow-grid'), only('.rail-overflow-popover'), only('.terminal-action-rail')]
  const onRail = [only('.term-key'), only('.terminal-action-rail')]

  assert.equal(regionForPath(inPopover), null)
  assert.equal(pathShadowsGesture(inPopover), true)
  // Shadowing is not "no region": the recognizer has to drop the sequence outright, or a
  // sideways drag across the panel resolves the workspace slots and changes tabs behind it.
  assert.equal(pathShadowsGesture(onRail), false)
  assert.equal(regionForPath(onRail), 'commandRail')
  assert.equal(pathShadowsGesture([]), false)
  // Drop-ups mount under `.terminal-surface`, outside the rail, so no region matches them
  // in the first place and they need no entry here.
  assert.equal(GESTURE_SHADOWING_SELECTORS.includes('.rail-dropup'), false)
})

test('the top bar opens downward, and only the mic can be put away again', () => {
  assert.equal(surfaceGestureFor('quotaChip', 'down'), 'quotaChip.accounts')
  assert.equal(surfaceGestureFor('runTrigger', 'down'), 'runTrigger.menu')
  assert.equal(surfaceGestureFor('micToggle', 'down'), 'micToggle.reveal')
  assert.equal(surfaceGestureFor('micToggle', 'up'), 'micToggle.hide')
  // The two that only open have no upward half: what they open is closed by its own
  // scrim, its own swipe-away, and a second tap.
  assert.equal(surfaceGestureFor('quotaChip', 'up'), null)
  assert.equal(surfaceGestureFor('runTrigger', 'up'), null)
  // Nor a sideways one — the top bar's own strip pans there when it overflows.
  for (const region of ['quotaChip', 'micToggle', 'runTrigger'] as const) {
    assert.equal(surfaceGestureFor(region, 'left'), null)
    assert.equal(surfaceGestureFor(region, 'right'), null)
  }
})

test('the two edge toggles answer to any direction, and only ever open', () => {
  // A thumb reaching the corner of a phone is not placing a precise tap, which is the
  // whole reason to drag one of these — so refusing three directions would put the
  // precision straight back.
  for (const direction of ['up', 'down', 'left', 'right'] as const) {
    assert.equal(surfaceGestureFor('navToggle', direction), 'navToggle.open')
    assert.equal(surfaceGestureFor('drawerToggle', direction), 'drawerToggle.open')
  }
})

test('a region swipe resolves a direction on one finger and nothing on two', () => {
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: 8, dy: -90, durationMs: 200 }), 'up')
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: 8, dy: 90, durationMs: 200 }), 'down')
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: -90, dy: 8, durationMs: 200 }), 'left')
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: 90, dy: 8, durationMs: 200 }), 'right')
  // Two fingers are the global slots' channel, region or not.
  assert.equal(classifyRegionGesture({ pointerCount: 2, dx: 8, dy: -90, durationMs: 200 }), null)
})

test('a region swipe answers to the same distance, axis and duration bars as every other', () => {
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: 4, dy: -30, durationMs: 120 }), null)
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: 60, dy: -62, durationMs: 150 }), null)
  // A long press that then travels is a hold-to-repeat or a drag, not a flick.
  assert.equal(classifyRegionGesture({ pointerCount: 1, dx: 4, dy: -120, durationMs: 2000 }), null)
})

test('the voice dock steps its size vertically and its mode horizontally', () => {
  // Down expands because the dock is top-anchored and grows downward — the same sense as
  // its own ▾/▴ buttons, and the opposite of a bottom-anchored sheet.
  assert.equal(surfaceGestureFor('voiceDock', 'down'), 'voiceDock.expand')
  assert.equal(surfaceGestureFor('voiceDock', 'up'), 'voiceDock.collapse')
  // Leftward is "next" because that is already what leftward means here (swipe_left is
  // mobileTab.next).
  assert.equal(surfaceGestureFor('voiceDock', 'left'), 'voiceDock.modeNext')
  assert.equal(surfaceGestureFor('voiceDock', 'right'), 'voiceDock.modePrevious')
})

test('the top bar Project name steps Projects sideways and opens its menu vertically', () => {
  assert.equal(surfaceGestureFor('projectName', 'left'), 'projectName.next')
  assert.equal(surfaceGestureFor('projectName', 'right'), 'projectName.previous')
  assert.equal(surfaceGestureFor('projectName', 'up'), 'projectName.menu')
  assert.equal(surfaceGestureFor('projectName', 'down'), 'projectName.menu')
})

test('the tab rail and the note rail claim only the directions they mean', () => {
  // Either vertical direction opens a tab's menu: the next touch anywhere dismisses it,
  // so a "close" direction would be a gesture for something that already happened.
  assert.equal(surfaceGestureFor('tabRail', 'up'), 'tabRail.menu')
  assert.equal(surfaceGestureFor('tabRail', 'down'), 'tabRail.menu')
  // Horizontal on the tab rail is its own pan, and always was.
  assert.equal(surfaceGestureFor('tabRail', 'left'), null)
  assert.equal(surfaceGestureFor('tabRail', 'right'), null)
  assert.equal(surfaceGestureFor('noteRail', 'down'), 'noteRail.outline')
  assert.equal(surfaceGestureFor('noteRail', 'up'), null)
  // The command rail resolves through the slot system instead, so it has no surface act.
  for (const direction of ['up', 'down', 'left', 'right'] as const) {
    assert.equal(surfaceGestureFor('commandRail', direction), null)
  }
})

test('only the two horizontal directions yield to something already panning', () => {
  assert.equal(isHorizontalDirection('left'), true)
  assert.equal(isHorizontalDirection('right'), true)
  assert.equal(isHorizontalDirection('up'), false)
  assert.equal(isHorizontalDirection('down'), false)
})

test('surface gestures are on unless the config explicitly disables them', () => {
  assert.equal(surfaceGesturesEnabled({}), true)
  assert.equal(surfaceGesturesEnabled({ mobile_surface_gestures: true }), true)
  assert.equal(surfaceGesturesEnabled({ mobile_surface_gestures: false }), false)
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
