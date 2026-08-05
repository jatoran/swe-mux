import assert from 'node:assert/strict'
import test from 'node:test'
import {
  adoptsOwnGeometryOnReveal,
  geometryMatchesFit,
  letterboxFontSize,
  MIN_LETTERBOX_FONT_PX,
} from '../src/terminalLetterbox.ts'

const measured = { fontSize: 11, cellWidth: 8, cellHeight: 14, baseFontSize: 11 }

test('a grid wider than the pane is shrunk until it fits', () => {
  // Showing another device's 200 columns: 200 * 8px needs 1600px, the pane has 800.
  // Half the width means half the font, rounded down so it cannot overflow.
  const size = letterboxFontSize({ ...measured, cols: 200, rows: 50, hostWidth: 800, hostHeight: 700 })
  assert.equal(size, 5)
})

test('a grid that already fits renders at the normal font size', () => {
  // A desktop showing the phone's 40x20 letterboxes by leaving space, not by growing.
  assert.equal(letterboxFontSize({ ...measured, cols: 40, rows: 20, hostWidth: 1600, hostHeight: 900 }), 11)
})

test('the font never shrinks past the readable floor', () => {
  const size = letterboxFontSize({ ...measured, cols: 400, rows: 100, hostWidth: 120, hostHeight: 200 })
  assert.equal(size, MIN_LETTERBOX_FONT_PX)
})

test('unusable measurements leave the font alone', () => {
  assert.equal(letterboxFontSize({ ...measured, cellWidth: 0, cols: 80, rows: 24, hostWidth: 800, hostHeight: 400 }), 11)
  assert.equal(letterboxFontSize({ ...measured, cols: 80, rows: 24, hostWidth: 0, hostHeight: 0 }), 11)
})

test('a pane letterboxes exactly when the shared size is not its own fit', () => {
  assert.equal(geometryMatchesFit({ cols: 80, rows: 24 }, { cols: 80, rows: 24 }), true)
  assert.equal(geometryMatchesFit({ cols: 80, rows: 24 }, { cols: 80, rows: 25 }), false)
  // Nothing to compare yet: render normally rather than flashing a letterbox.
  assert.equal(geometryMatchesFit(null, { cols: 80, rows: 24 }), true)
  assert.equal(geometryMatchesFit({ cols: 80, rows: 24 }, null), true)
})

test('a revealed pane that owns input trusts the fit it just measured', () => {
  // The owner's viewport is taken verbatim by the daemon, so the confirmation this
  // pane is waiting for can only agree with what it measured. Waiting for it is what
  // drew one frame at the pre-hide grid and then snapped.
  assert.equal(adoptsOwnGeometryOnReveal(true, { cols: 120, rows: 40 }), true)
})

test('a revealed pane that does not own input still letterboxes', () => {
  // Its fit is a proposal arbitration may reduce. Rendering it as settled is how a
  // background desktop pane rewraps an agent TUI to desktop width on a phone.
  assert.equal(adoptsOwnGeometryOnReveal(false, { cols: 120, rows: 40 }), false)
})

test('a revealed pane with no fit yet has nothing to adopt', () => {
  assert.equal(adoptsOwnGeometryOnReveal(true, null), false)
})
