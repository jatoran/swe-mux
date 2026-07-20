import assert from 'node:assert/strict'
import test from 'node:test'
import {
  defaultMobileInputSettings,
  mobileDragTarget,
  mobileInputSettings,
  terminalCellAtPoint,
  terminalScrollLines,
  terminalSelectionSpan,
  terminalWordRange,
  touchWheelDelta,
} from '../src/mobileInput.ts'

test('mobile input defaults favor smart natural scrolling', () => {
  assert.deepEqual(mobileInputSettings({}), defaultMobileInputSettings)
  assert.equal(touchWheelDelta(100, 80, defaultMobileInputSettings), 20)
  assert.equal(terminalScrollLines(20, 10), 2)
  assert.equal(terminalScrollLines(-2, 10), -1)
  assert.equal(mobileDragTarget('smart', false), 'terminal')
  assert.equal(mobileDragTarget('smart', true), 'application')
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
