import assert from 'node:assert/strict'
import test from 'node:test'
import {
  defaultMobileInputSettings,
  mobileDragTarget,
  mobileInputSettings,
  smoothTouchVelocity,
  terminalCellAtPoint,
  terminalScrollSteps,
  terminalSelectionSpan,
  terminalWordRange,
  TOUCH_SCROLL_ACCELERATION,
  touchScrollGain,
  touchWheelDelta,
} from '../src/mobileInput.ts'

test('mobile input defaults favor smart natural scrolling', () => {
  assert.deepEqual(mobileInputSettings({}), defaultMobileInputSettings)
  assert.equal(touchWheelDelta(100, 80, defaultMobileInputSettings), 20)
  assert.deepEqual(terminalScrollSteps(20, 10), { steps: 2, remainder: 0 })
  assert.equal(mobileDragTarget('smart', false), 'terminal')
  assert.equal(mobileDragTarget('smart', true), 'application')
})

test('sub-row drag travel is carried rather than truncated or rounded up', () => {
  // Three 6px moves against a 10px row: the first two scroll nothing and bank, the third
  // spends the accumulated 18px as one row and carries the rest.
  const first = terminalScrollSteps(6, 10)
  assert.deepEqual(first, { steps: 0, remainder: 6 })
  const second = terminalScrollSteps(first.remainder + 6, 10)
  assert.deepEqual(second, { steps: 1, remainder: 2 })
  // Direction is preserved through the truncation, and a degenerate row height scrolls nothing.
  assert.deepEqual(terminalScrollSteps(-25, 10), { steps: -2, remainder: -5 })
  assert.deepEqual(terminalScrollSteps(400, 0), { steps: 0, remainder: 0 })
})

test('touch scroll starts controlled and still accelerates to the full flick gain', () => {
  const { baseGain, slowVelocity, fastVelocity, maxGain } = TOUCH_SCROLL_ACCELERATION
  assert.equal(touchScrollGain(0), baseGain)
  assert.equal(touchScrollGain(slowVelocity), baseGain)
  assert.equal(touchScrollGain(fastVelocity), maxGain)
  assert.equal(touchScrollGain(fastVelocity * 10), maxGain)
  const middle = touchScrollGain((slowVelocity + fastVelocity) / 2)
  assert.ok(Math.abs(middle - (baseGain + maxGain) / 2) < 1e-9)
  // Velocity is px/ms and frame-rate independent: the same gesture sampled twice as often,
  // at half the distance per sample, converges on the same reading.
  let slow = 0
  let fast = 0
  for (let sample = 0; sample < 12; sample++) {
    slow = smoothTouchVelocity(slow, 32, 16)
    fast = smoothTouchVelocity(fast, 16, 8)
  }
  assert.ok(Math.abs(slow - fast) < 1e-9)
  assert.ok(Math.abs(slow - 2) < 0.05)
  // A move with no elapsed time carries no velocity and must not read as an infinite flick.
  assert.equal(smoothTouchVelocity(1.5, 40, 0), 1.5)
})

test('mobile input settings normalize configured direction and sensitivity', () => {
  const settings = mobileInputSettings({
    mobile_vertical_drag: 'application',
    mobile_scroll_direction: 'wheel',
    mobile_scroll_sensitivity: 1.5,
    mobile_long_press: 'disabled',
    terminal_auto_copy_selection: false,
  })
  assert.equal(settings.verticalDrag, 'application')
  assert.equal(settings.longPress, 'disabled')
  assert.equal(settings.autoCopySelection, false)
  assert.equal(touchWheelDelta(100, 80, settings), -30)
  assert.equal(mobileDragTarget(settings.verticalDrag, false), 'application')
})

test('terminal touch coordinates map to scrollback cells', () => {
  assert.deepEqual(terminalCellAtPoint(50, 25, { left: 0, top: 0, width: 100, height: 50 }, 10, 5, 20), { column: 5, row: 22 })
  assert.deepEqual(terminalCellAtPoint(-5, 100, { left: 0, top: 0, width: 100, height: 50 }, 10, 5, 20), { column: 0, row: 24 })
})

test('long press selects a word and drag extends in either direction', () => {
  assert.deepEqual(terminalWordRange('alpha beta gamma', 7), { start: 6, length: 4 })
  assert.deepEqual(terminalWordRange('alpha   beta', 6), { start: 8, length: 4 })
  assert.deepEqual(
    terminalSelectionSpan({ column: 6, row: 3 }, 4, { column: 2, row: 4 }, 10),
    { column: 6, row: 3, length: 7 },
  )
  assert.deepEqual(
    terminalSelectionSpan({ column: 6, row: 3 }, 4, { column: 2, row: 2 }, 10),
    { column: 2, row: 2, length: 18 },
  )
})
