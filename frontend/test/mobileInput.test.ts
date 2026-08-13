import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applicationTouchScroll,
  CLAUDE_TOUCH_REPORT_INTERVAL_MS,
  defaultMobileInputSettings,
  mobileDragTarget,
  mobileInputSettings,
  terminalCellAtPoint,
  terminalScrollSteps,
  terminalSelectionSpan,
  terminalWordRange,
  touchWheelDelta,
} from '../src/mobileInput.ts'

test('Claude application scrolling compensates for its wheel multiplier', () => {
  let state = { pixels: 0, lastReportAt: Number.NEGATIVE_INFINITY }

  // Claude moves three rows for its first wheel report, so two rows of finger travel
  // remain banked and the third produces exactly one report.
  let result = applicationTouchScroll(state, 24, 12, 'claude', 0)
  assert.deepEqual(result, {
    steps: 0,
    remainder: 24,
    lastReportAt: Number.NEGATIVE_INFINITY,
    distance: 0,
    droppedPixels: 0,
  })
  state = { pixels: result.remainder, lastReportAt: result.lastReportAt }
  result = applicationTouchScroll(state, 12, 12, 'claude', 10)
  assert.equal(result.steps, 1)
  assert.equal(result.distance, 36)
  assert.equal(result.remainder, 0)
})

test('Claude fast touch scroll is rate capped without delayed backlog', () => {
  let state = { pixels: 0, lastReportAt: Number.NEGATIVE_INFINITY }
  const first = applicationTouchScroll(state, 120, 12, 'claude', 0)
  assert.equal(first.steps, 1)
  assert.equal(first.droppedPixels, 84)

  state = { pixels: first.remainder, lastReportAt: first.lastReportAt }
  const gated = applicationTouchScroll(state, 120, 12, 'claude', CLAUDE_TOUCH_REPORT_INTERVAL_MS - 1)
  assert.equal(gated.steps, 0)
  assert.equal(gated.remainder, 36)
  assert.equal(gated.droppedPixels, 84)

  state = { pixels: gated.remainder, lastReportAt: gated.lastReportAt }
  const released = applicationTouchScroll(state, 1, 12, 'claude', CLAUDE_TOUCH_REPORT_INTERVAL_MS)
  assert.equal(released.steps, 1)
  assert.equal(released.remainder, 0)
  assert.equal(released.droppedPixels, 1)

  // Other mouse-aware TUIs keep the existing one-report-per-row behavior.
  const generic = applicationTouchScroll(
    { pixels: 0, lastReportAt: Number.NEGATIVE_INFINITY },
    120, 12, 'codex', 0,
  )
  assert.equal(generic.steps, 10)
  assert.equal(generic.distance, 120)
})

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

test('touch scroll distance stays linear across pointer event rates', () => {
  const scrollGesture = (moves: number, fingerPixelsPerMove: number, rowHeight: number) => {
    let previousY = 200
    let remainder = 0
    let rows = 0
    for (let move = 0; move < moves; move++) {
      const currentY = previousY - fingerPixelsPerMove
      const delta = touchWheelDelta(previousY, currentY, defaultMobileInputSettings)
      const budget = terminalScrollSteps(remainder + delta, rowHeight)
      rows += budget.steps
      remainder = budget.remainder
      previousY = currentY
    }
    return { rows, remainder }
  }

  // The same 192px gesture produces the same 16 rows whether the phone reports 12 or 24 moves.
  assert.deepEqual(scrollGesture(12, 16, 12), { rows: 16, remainder: 0 })
  assert.deepEqual(scrollGesture(24, 8, 12), { rows: 16, remainder: 0 })
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
