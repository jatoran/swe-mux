import assert from 'node:assert/strict'
import test from 'node:test'
import { redrawVisibleTerminal, refitVisibleTerminal, terminalHostIsVisible } from '../src/terminalViewport.ts'

test('hidden terminal panes do not fit or redraw at zero size', () => {
  let fits = 0
  let redraws = 0
  const hidden = { isConnected: true, clientWidth: 0, clientHeight: 400 }
  assert.equal(terminalHostIsVisible(hidden), false)
  assert.equal(refitVisibleTerminal({ fit: () => { fits += 1 } }, hidden), false)
  assert.equal(redrawVisibleTerminal({ cols: 80, rows: 24, refresh: () => { redraws += 1 } }, hidden), false)
  assert.equal(fits, 0)
  assert.equal(redraws, 0)
})

test('visible terminal panes refit and invalidate every rendered row', () => {
  const visible = { isConnected: true, clientWidth: 900, clientHeight: 500 }
  let fits = 0
  let range: [number, number] | null = null
  assert.equal(refitVisibleTerminal({ fit: () => { fits += 1 } }, visible), true)
  assert.equal(redrawVisibleTerminal({ cols: 120, rows: 36, refresh: (start, end) => { range = [start, end] } }, visible), true)
  assert.equal(fits, 1)
  assert.deepEqual(range, [0, 35])
})
