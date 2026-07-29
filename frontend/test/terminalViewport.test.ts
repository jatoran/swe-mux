import assert from 'node:assert/strict'
import test from 'node:test'
import {
  redrawVisibleTerminal,
  refitVisibleTerminal,
  scrollTerminalToTail,
  terminalHostIsVisible,
} from '../src/terminalViewport.ts'

/**
 * A terminal whose `scrollToBottom` is clamped the way xterm's is after a refit: the DOM
 * scroller still advertises the pre-resize range, so one call can only reach `clampedTo`.
 * Each call re-publishes the range (xterm's `Viewport._sync` runs off `onScroll`), which is
 * what lets the next one go further.
 */
function clampedTerminal(viewportY: number, baseY: number, clampedTo: number) {
  let range = clampedTo
  const term = {
    calls: 0,
    buffer: { active: { viewportY, baseY } },
    scrollToBottom() {
      term.calls += 1
      term.buffer.active.viewportY = Math.min(term.buffer.active.baseY, range)
      range = term.buffer.active.baseY
    },
  }
  return term
}

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

// The soft keyboard opening refits the pane, which pushes rows into scrollback and moves
// `baseY`, while xterm defers republishing its scroller's range to a queued render callback.
// A jump-to-latest issued in that window is clamped to the pre-resize maximum, landing
// exactly as many rows short as the refit cost — 28 here, a 42-row grid shrinking to 14.
test('jump-to-latest finishes a scroll that a refit clamped short', () => {
  const term = clampedTerminal(729, 787, 759)
  assert.equal(scrollTerminalToTail(term), true)
  assert.equal(term.buffer.active.viewportY, 787)
  assert.equal(term.calls, 2)
})

test('jump-to-latest costs one call when nothing clamps it', () => {
  const term = clampedTerminal(729, 759, 759)
  assert.equal(scrollTerminalToTail(term), true)
  assert.equal(term.buffer.active.viewportY, 759)
  assert.equal(term.calls, 1)
})

test('jump-to-latest is a no-op on the tail and gives up rather than spinning', () => {
  const onTail = clampedTerminal(759, 759, 759)
  assert.equal(scrollTerminalToTail(onTail), true)
  assert.equal(onTail.calls, 0)

  // A terminal that cannot move at all must cost one attempt, not TAIL_SCROLL_ATTEMPTS.
  const stuck = { calls: 0, buffer: { active: { viewportY: 700, baseY: 787 } }, scrollToBottom() { stuck.calls += 1 } }
  assert.equal(scrollTerminalToTail(stuck), false)
  assert.equal(stuck.calls, 1)

  assert.equal(scrollTerminalToTail(null), false)
})
