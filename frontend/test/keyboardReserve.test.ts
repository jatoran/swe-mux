import assert from 'node:assert/strict'
import test from 'node:test'
import {
  DEFAULT_KEYBOARD_FRACTION,
  RESERVE_DWELL_MS,
  MAX_RESERVE_FRACTION,
  nextReserveState,
  paintedRowCount,
  reservedKeyboardPx,
} from '../src/keyboardReserve.ts'

const rows = (...lines: string[]) => (index: number) => lines[index] ?? ''

test('painted rows are the ones with anything on them', () => {
  const grid = rows('claude v2', '', '  ', 'how do I', '')
  assert.equal(paintedRowCount(5, grid), 2)
  assert.equal(paintedRowCount(0, grid), 0)
})

test('the reservation is this device\'s measured keyboard, or a stand-in until it has one', () => {
  assert.equal(reservedKeyboardPx(415, 915), 415)
  // Never measured: a fraction, because the first pane to ask is on a device whose keyboard
  // has by definition not been seen yet.
  assert.equal(reservedKeyboardPx(0, 915), Math.round(915 * DEFAULT_KEYBOARD_FRACTION))
  // A bad measurement (browser chrome mistaken for a keyboard, a rotation) must not reserve
  // the pane away to a strip.
  assert.equal(reservedKeyboardPx(900, 915), Math.round(915 * MAX_RESERVE_FRACTION))
  assert.equal(reservedKeyboardPx(415, 0), 0)
})

const base = {
  reserved: false,
  rows: 48,
  reserveRows: 22,
  painted: 6,
  eligible: true,
  measurable: true,
  now: 100_000,
  changedAt: 0,
}

test('a fresh session reserves the keyboard space its content does not need', () => {
  // 6 painted rows in a 48-row grid: everything fits the 26 rows left after reserving, so
  // the resize that reaches the PTY cannot be the destructive one.
  assert.equal(nextReserveState(base).reserved, true)
})

test('nothing is reserved when the content would not survive the smaller grid', () => {
  // This is the whole safety argument. A screen with 30 painted rows cannot be redrawn into
  // 26 without losing four of them, and on the alternate screen they are gone for good.
  const full = nextReserveState({ ...base, painted: 30 })
  assert.equal(full.reserved, false)
  assert.equal(full.reason, 'would_not_fit')
  // Nor when the grid is too short to be worth typing into afterwards.
  assert.equal(nextReserveState({ ...base, rows: 30, reserveRows: 22 }).reserved, false)
})

test('the space goes back as soon as the session grows into it', () => {
  const reserved = { ...base, reserved: true, rows: 26, reserveRows: 22 }
  assert.equal(nextReserveState({ ...reserved, painted: 12 }).reserved, true)
  const grown = nextReserveState({ ...reserved, painted: 25 })
  assert.equal(grown.reserved, false)
  assert.equal(grown.reason, 'grew_into_grid')
  // Releasing is the lossless direction and never waits for the dwell timer: a reader whose
  // conversation just filled the grid wants the rows now, not in four seconds.
  assert.equal(
    nextReserveState({ ...reserved, painted: 25, changedAt: 99_000 }).reserved,
    false,
  )
})

test('a reservation cannot flap on a streaming reply', () => {
  const settled = { ...base, changedAt: base.now - RESERVE_DWELL_MS + 1 }
  assert.equal(nextReserveState(settled).reason, 'dwell')
  assert.equal(nextReserveState({ ...settled, changedAt: base.now - RESERVE_DWELL_MS }).reserved, true)
  // The deadband is the other half: content sitting exactly at the boundary must not engage
  // a reservation the very next reading would release.
  assert.equal(nextReserveState({ ...base, painted: 26 }).reserved, false)
  assert.equal(nextReserveState({ ...base, painted: 23 }).reserved, true)
})

test('a pane that cannot reserve gives the space straight back', () => {
  // Desktop layout, another device\'s geometry, or a hidden pane: no keyboard is coming, and
  // growing back is always safe.
  const ineligible = nextReserveState({ ...base, reserved: true, eligible: false })
  assert.equal(ineligible.reserved, false)
  assert.equal(ineligible.reason, 'ineligible')
  // A replaying buffer reads emptier than the session is. Holding still is the only safe
  // answer: reserving on that reading would shrink a PTY whose content has not arrived yet.
  assert.equal(nextReserveState({ ...base, measurable: false }).reserved, false)
  assert.equal(nextReserveState({ ...base, reserved: true, measurable: false }).reserved, true)
})
