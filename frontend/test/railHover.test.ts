import assert from 'node:assert/strict'
import test from 'node:test'
import {
  RAIL_HOVER_ZONE_MIN_PX, railHoverApplies, railHoverShown, railHoverZonePx, type RailHoverReading,
} from '../src/railHover.ts'

const SURFACE = { top: 100, bottom: 700, left: 0, right: 900 }

const reading = (overrides: Partial<RailHoverReading> = {}): RailHoverReading => ({
  pointer: null,
  surface: SURFACE,
  railHeight: 34,
  pointerOnRail: false,
  pointerOnTerminalControl: false,
  dragging: false,
  engaged: false,
  ...overrides,
})

test('the mode applies only to a desktop with a pointer, and only while the rail is on', () => {
  const on = { railOn: true, hoverSetting: true, profile: 'desktop' as const, hoverCapable: true }
  assert.equal(railHoverApplies(on), true)
  assert.equal(railHoverApplies({ ...on, hoverSetting: false }), false)
  assert.equal(railHoverApplies({ ...on, railOn: false }), false, 'a rail that is off has nothing to reveal')
  assert.equal(railHoverApplies({ ...on, profile: 'mobile' }), false, 'the phone rail is the keyboard')
  assert.equal(railHoverApplies({ ...on, hoverCapable: false }), false, 'a touch tablet could never reach it')
})

test('the reveal zone is the rail\'s own footprint, with a floor', () => {
  assert.equal(railHoverZonePx(34), 34)
  assert.equal(railHoverZonePx(104), 104)
  assert.equal(railHoverZonePx(0), RAIL_HOVER_ZONE_MIN_PX)
})

test('a pointer over the strip the rail would occupy reveals it, and one above does not', () => {
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 690 } })), true)
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 700 - 34 } })), true, 'the zone includes its top edge')
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 700 - 35 } })), false)
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 300 } })), false)
  // A two-row rail reveals from twice as far up.
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 640 }, railHeight: 68 })), true)
})

test('the zone is bounded by the surface, so a neighbour pane\'s bottom edge is not this rail\'s', () => {
  assert.equal(railHoverShown(reading({ pointer: { x: 950, y: 690 } })), false)
  assert.equal(railHoverShown(reading({ pointer: { x: -5, y: 690 } })), false)
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 720 } })), false)
  assert.equal(railHoverShown(reading({ pointer: null })), false)
})

test('a pointer on the rail or one of its overlays keeps it up wherever that overlay is', () => {
  // The popover grows upward out of the zone; a pointer inside it is a pointer on the rail.
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 200 }, pointerOnRail: true })), true)
  assert.equal(railHoverShown(reading({ pointer: null, pointerOnRail: true })), true)
})

test('an open panel, arrange mode, or the context menu holds the rail up without the pointer', () => {
  assert.equal(railHoverShown(reading({ pointer: null, engaged: true })), true)
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 200 }, engaged: true })), true)
})

test('a drag through the zone and a pointer on a terminal control do not reveal', () => {
  // A selection drag that reaches the bottom of the buffer is not a request for the rail,
  // and revealing it mid-drag would put chips under the button being held.
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 690 }, dragging: true })), false)
  // The jump-to-latest chip lives in the zone; covering it with the rail would take the
  // control the pointer is on.
  assert.equal(railHoverShown(reading({ pointer: { x: 880, y: 690 }, pointerOnTerminalControl: true })), false)
  // Neither exemption outranks engagement or a pointer already on the rail.
  assert.equal(railHoverShown(reading({ pointer: { x: 400, y: 690 }, dragging: true, pointerOnRail: true })), true)
  assert.equal(railHoverShown(reading({ pointer: { x: 880, y: 690 }, pointerOnTerminalControl: true, engaged: true })), true)
})
