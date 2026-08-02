import assert from 'node:assert/strict'
import test from 'node:test'
import {
  APP_TAIL_KEY,
  EXPENSIVE_VIEWPORT_PASS_MS,
  VIEWPORT_SETTLE_MAX_MS,
  VIEWPORT_SETTLE_MS,
  appOwnsTail,
  attachRegistersViewport,
  createViewportScheduler,
  redrawVisibleTerminal,
  refitVisibleTerminal,
  reflowVisibleTerminalRenderer,
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

test('a pane registers a viewport only when it fitted itself while on screen', () => {
  assert.equal(attachRegistersViewport(true, false), true)
  // The bug this exists for: a warm pane is `display:none` inside a *foreground* tab, so
  // `document.hidden` is false while the pane is not on screen at all. Its host measures
  // zero, so the attach-time fit no-ops and `term.cols/rows` are xterm's unfitted 80x24
  // default. Registering that made it the session's size for every visible client.
  assert.equal(attachRegistersViewport(false, false), false)
  assert.equal(attachRegistersViewport(true, true), false)
  assert.equal(attachRegistersViewport(false, true), false)
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

test('a restored pane forces renderer dimensions even when its grid is unchanged', () => {
  const visible = { isConnected: true, clientWidth: 900, clientHeight: 500 }
  const calls: Array<[number, number]> = []
  const term = { cols: 120, rows: 36, resize: (cols: number, rows: number) => calls.push([cols, rows]) }

  assert.equal(reflowVisibleTerminalRenderer(term, visible), true)
  assert.deepEqual(calls, [[120, 36]])

  const hidden = { ...visible, clientWidth: 0 }
  assert.equal(reflowVisibleTerminalRenderer(term, hidden), false)
  assert.deepEqual(calls, [[120, 36]])
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

// The half that made the chip dead on a phone: Claude keeps its own viewport, so scrolling
// xterm's moves a view the user was not looking at. Codex keeps none, which is why the same
// button has always worked there — and why it must not start typing at one that does not.
test('jump-to-latest asks the application too, but only one that owns a viewport', () => {
  assert.equal(appOwnsTail('claude', false), true)
  assert.equal(appOwnsTail('codex', false), false)
  // Whatever the backend, whoever is receiving this pane's scrolls is who has to undo them.
  assert.equal(appOwnsTail('codex', true), true)
  // A shell owns no viewport, so the bytes would only land in a half-typed command line.
  assert.equal(appOwnsTail('shell', true), false)
  assert.equal(APP_TAIL_KEY, '\x1b[1;5F')
})

// --- Adaptive viewport scheduling -------------------------------------------

function fakeTimers() {
  let clock = 0
  let nextId = 1
  const pending = new Map<number, { at: number; fn: () => void }>()
  return {
    timers: {
      now: () => clock,
      setTimer: (fn: () => void, ms: number) => {
        const id = nextId++
        pending.set(id, { at: clock + ms, fn })
        return id
      },
      clearTimer: (id: number) => { pending.delete(id) },
    },
    advance(ms: number) {
      const target = clock + ms
      for (;;) {
        const due = [...pending.entries()]
          .filter(([, entry]) => entry.at <= target)
          .sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        pending.delete(due[0])
        clock = due[1].at
        due[1].fn()
      }
      clock = target
    },
    get scheduled() { return pending.size },
  }
}

test('a cheap pane keeps fitting on every event, exactly as before', () => {
  const { timers, advance } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  for (let frame = 0; frame < 10; frame += 1) {
    scheduler.observeCost(1)
    scheduler.request(true)
    advance(16)
  }
  assert.equal(runs, 10)
  assert.equal(scheduler.deferred, false)
})

test('an expensive pane runs the first pass and coalesces the rest of the burst', () => {
  // The soft-keyboard case: ~20 visualViewport resizes across the animation, each
  // one otherwise a pseudoconsole resize the CLI repaints its whole transcript for.
  const { timers, advance } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  scheduler.request(true)
  assert.equal(runs, 1, 'the first pass always runs — it is also the measurement')
  scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS + 4)

  for (let frame = 0; frame < 20; frame += 1) {
    scheduler.request(true)
    advance(16)
  }
  assert.equal(runs, 1, 'nothing ran mid-animation')
  assert.ok(scheduler.deferred)

  advance(VIEWPORT_SETTLE_MS)
  assert.equal(runs, 2, 'one pass once the viewport settled')
  assert.equal(scheduler.deferred, false)
})

test('a continuous gesture still updates at the cap instead of holding forever', () => {
  // Dragging a splitter never stops, so the settle alone would freeze the grid for
  // the whole drag. The cap bounds how stale the shown grid can get.
  const { timers, advance } = fakeTimers()
  const firedAt: number[] = []
  const scheduler = createViewportScheduler(() => firedAt.push(timers.now()), timers)
  scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS)
  for (let elapsed = 0; elapsed < VIEWPORT_SETTLE_MAX_MS * 3; elapsed += 16) {
    scheduler.request(true)
    // Cost stays high: a real drag keeps doing the expensive work every pass.
    scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS)
    advance(16)
  }
  assert.ok(firedAt.length >= 2, `expected repeated cap fires, got ${firedAt.join()}`)
  assert.ok(
    firedAt[0] <= VIEWPORT_SETTLE_MAX_MS,
    `first update must land within the cap, landed at ${firedAt[0]}`,
  )
  for (let i = 1; i < firedAt.length; i += 1) {
    const gap = firedAt[i] - firedAt[i - 1]
    assert.ok(
      gap <= VIEWPORT_SETTLE_MAX_MS + VIEWPORT_SETTLE_MS,
      `gap between updates was ${gap}ms`,
    )
  }
})

test('a discrete trigger is never deferred, however expensive the pane', () => {
  // Becoming visible, a pane revealed, a rail change: these arrive once and the
  // user is looking at the result, so waiting on a settle that will never come
  // would leave a stale grid on screen.
  const { timers } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS * 10)
  scheduler.request(false)
  assert.equal(runs, 1)
  assert.equal(scheduler.deferred, false)
})

test('a discrete trigger supersedes a burst already waiting', () => {
  const { timers, advance } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS)
  scheduler.request(true)
  assert.ok(scheduler.deferred)
  scheduler.request(false)
  assert.equal(runs, 1)
  assert.equal(scheduler.deferred, false)
  advance(VIEWPORT_SETTLE_MS * 4)
  assert.equal(runs, 1, 'the superseded burst must not fire a second pass')
})

test('cancelling drops a pending burst', () => {
  const { timers, advance } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS)
  scheduler.request(true)
  scheduler.cancel()
  advance(VIEWPORT_SETTLE_MS * 4)
  assert.equal(runs, 0)
  assert.equal(scheduler.deferred, false)
})

test('a pane that becomes cheap again stops deferring', () => {
  // The cost is re-measured every pass, so a session whose buffer was trimmed, or
  // one whose fit turned into a no-op, goes back to live fitting on its own.
  const { timers, advance } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  scheduler.observeCost(EXPENSIVE_VIEWPORT_PASS_MS)
  scheduler.request(true)
  advance(VIEWPORT_SETTLE_MS)
  assert.equal(runs, 1)
  scheduler.observeCost(0)
  scheduler.request(true)
  assert.equal(runs, 2)
  assert.equal(scheduler.deferred, false)
})
