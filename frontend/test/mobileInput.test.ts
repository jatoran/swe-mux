import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applicationTouchScroll,
  defaultMobileInputSettings,
  mobileDragTarget,
  mobileInputSettings,
  terminalCellAtPoint,
  terminalScrollSteps,
  terminalSelectionSpan,
  terminalWordRange,
  touchWheelDelta,
} from '../src/mobileInput.ts'

const MULTIPLIED_SCROLL = { rowsPerReport: 3 }
const LINEAR_SCROLL = { rowsPerReport: 1 }

test('application scrolling measures travel in the rows a report is worth', () => {
  let state = { pixels: 0 }

  // Claude moves three rows for one wheel report, so two rows of finger travel are
  // carried and the third row is what produces the report.
  let result = applicationTouchScroll(state, 24, 12, MULTIPLIED_SCROLL)
  assert.deepEqual(result, { steps: 0, remainder: 24, distance: 0 })
  state = { pixels: result.remainder }
  result = applicationTouchScroll(state, 12, 12, MULTIPLIED_SCROLL)
  assert.deepEqual(result, { steps: 1, remainder: 0, distance: 36 })

  // Other mouse-aware TUIs move one row per report.
  assert.deepEqual(applicationTouchScroll({ pixels: 0 }, 120, 12, LINEAR_SCROLL), {
    steps: 10, remainder: 0, distance: 120,
  })
})

test('a fast application drag tracks the finger rather than a report rate', () => {
  // Nothing here throttles: a move event carrying nine rows of travel forwards all
  // three reports it is worth. Rate limiting on this side could only discard travel
  // the drag asked for; shedding a flick's excess is the wheel pacer's job.
  const fast = applicationTouchScroll({ pixels: 0 }, 108, 12, MULTIPLIED_SCROLL)
  assert.deepEqual(fast, { steps: 3, remainder: 0, distance: 108 })

  // Sub-report travel is carried, not truncated, so a slow drag still tracks 1:1.
  let state = { pixels: 0 }
  let steps = 0
  for (let move = 0; move < 12; move++) {
    const budget = applicationTouchScroll(state, 6, 12, MULTIPLIED_SCROLL)
    state = { pixels: budget.remainder }
    steps += budget.steps
  }
  assert.equal(steps, 2)

  // Reversing direction abandons the pending travel instead of scrolling backwards.
  const reversed = applicationTouchScroll({ pixels: 30 }, -6, 12, MULTIPLIED_SCROLL)
  assert.deepEqual(reversed, { steps: 0, remainder: -6, distance: 0 })
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
