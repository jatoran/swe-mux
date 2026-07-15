import assert from 'node:assert/strict'
import test from 'node:test'
import { defaultMobileInputSettings, mobileDragTarget, mobileInputSettings, terminalScrollLines, touchWheelDelta } from '../src/mobileInput.ts'

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
  })
  assert.equal(settings.verticalDrag, 'application')
  assert.equal(settings.longPress, 'disabled')
  assert.equal(touchWheelDelta(100, 80, settings), -30)
  assert.equal(mobileDragTarget(settings.verticalDrag, false), 'application')
})
