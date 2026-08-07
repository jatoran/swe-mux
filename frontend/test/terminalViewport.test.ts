import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  APP_TAIL_KEY,
  CLAUDE_MAX_DESKTOP_COLUMNS,
  CODEX_MIN_DESKTOP_COLUMNS,
  CODEX_MIN_DESKTOP_FONT_PX,
  EXPENSIVE_VIEWPORT_PASS_MS,
  VIEWPORT_SETTLE_MAX_MS,
  VIEWPORT_SETTLE_MS,
  appOffTailByDistance,
  appOwnsTail,
  attachRegistersViewport,
  createSurfaceRepairScheduler,
  createViewportScheduler,
  effectiveViewportCost,
  redrawVisibleTerminal,
  refitVisibleTerminal,
  reflowVisibleTerminalRenderer,
  restoreTerminalScrollAnchor,
  scrollTerminalToTail,
  terminalHostIsVisible,
  terminalRowsAboveTail,
  terminalSurface,
  terminalSurfaceChanged,
  terminalWidthPolicyFontSize,
  trackAppTailDistance,
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

/**
 * A terminal whose `scrollLines` is clamped the way xterm's is mid-resize burst: the DOM
 * scroller advertises a stale maximum, so the first move lands short and only the scroll it
 * fired republishes the real range. Same shape as `clampedTerminal`, for the off-tail path.
 */
function clampedScroller(viewportY: number, baseY: number, ceiling: number) {
  let range = ceiling
  const term = {
    calls: 0,
    buffer: { active: { viewportY, baseY } },
    scrollToBottom() { term.buffer.active.viewportY = term.buffer.active.baseY },
    scrollLines(amount: number) {
      term.calls += 1
      const wanted = term.buffer.active.viewportY + amount
      term.buffer.active.viewportY = Math.max(0, Math.min(range, wanted))
      range = term.buffer.active.baseY
    },
  }
  return term
}

test('the scroll anchor is the distance from the tail, not an absolute row', () => {
  // A reader 40 rows above the newest line. Growing the grid moves `baseY` (a ConPTY buffer
  // gains blank rows), and it is the distance that has to survive that, not `viewportY`.
  assert.equal(terminalRowsAboveTail({ buffer: { active: { viewportY: 960, baseY: 1000 } }, scrollToBottom() {} }), 40)
  // At the tail there is no anchor to take: `scrollTerminalToTail` owns that case.
  assert.equal(terminalRowsAboveTail({ buffer: { active: { viewportY: 1000, baseY: 1000 } }, scrollToBottom() {} }), 0)
  // Past the tail cannot go negative and become a scroll in the wrong direction.
  assert.equal(terminalRowsAboveTail({ buffer: { active: { viewportY: 1010, baseY: 1000 } }, scrollToBottom() {} }), 0)
  assert.equal(terminalRowsAboveTail(null), 0)
})

test('an off-tail viewport is restored to the same distance from a moved tail', () => {
  // The resize added 12 rows to the buffer: baseY 1000 -> 1012. Staying 40 rows above the
  // tail means landing on 972, which keeps the same text under the reader.
  const term = clampedScroller(960, 1012, 10_000)
  assert.equal(restoreTerminalScrollAnchor(term, 40), true)
  assert.equal(term.buffer.active.viewportY, 972)
  assert.equal(term.calls, 1)
})

test('a clamped anchor restore re-issues until it lands', () => {
  // The scroller still advertises the pre-resize maximum, so the first move stops short.
  const term = clampedScroller(300, 1000, 800)
  assert.equal(restoreTerminalScrollAnchor(term, 40), true)
  assert.equal(term.buffer.active.viewportY, 960)
  assert.ok(term.calls > 1, 'one clamped call is not enough')
})

test('an anchor the buffer can no longer reach stops instead of spinning', () => {
  // Scrollback has since been trimmed past the anchor: no call makes progress, so the retry
  // has to give up rather than re-issue forever.
  const stuck = {
    calls: 0,
    buffer: { active: { viewportY: 0, baseY: 1000 } },
    scrollToBottom() {},
    scrollLines() { stuck.calls += 1 },
  }
  assert.equal(restoreTerminalScrollAnchor(stuck, 40), false)
  assert.equal(stuck.calls, 1)
  // Nothing to restore is not a failure to restore.
  assert.equal(restoreTerminalScrollAnchor(stuck, 0), false)
  assert.equal(restoreTerminalScrollAnchor(null, 40), false)
})

test('a surface that changed shape owes a confirmation repaint', () => {
  const host = { isConnected: true, clientWidth: 390, clientHeight: 700 }
  const grown = { isConnected: true, clientWidth: 390, clientHeight: 1000 }
  const term = { cols: 80, rows: 24 }
  const before = terminalSurface(term, host)
  // The keyboard closed: same grid so far, bigger box. FitAddon skips `term.resize` when the
  // grid is unchanged, so this is exactly the case whose renderer holds the old dimensions.
  assert.equal(terminalSurfaceChanged(before, terminalSurface(term, grown)), true)
  // The grid changed on the same box — a font or scale change.
  assert.equal(terminalSurfaceChanged(before, terminalSurface({ cols: 80, rows: 40 }, host)), true)
  // Nothing moved: no repaint owed, which is what keeps this off every idle pass.
  assert.equal(terminalSurfaceChanged(before, terminalSurface(term, host)), false)
})

test('a surface with nothing confirmed yet always owes one, and a hidden pane never does', () => {
  const host = { isConnected: true, clientWidth: 390, clientHeight: 700 }
  const term = { cols: 80, rows: 24 }
  assert.equal(terminalSurfaceChanged(null, terminalSurface(term, host)), true)
  // A pane with no layout cannot be measured, so it cannot be judged stale either — and
  // arming a confirmation for it would repaint into a box that does not exist.
  assert.equal(terminalSurface(term, { isConnected: true, clientWidth: 0, clientHeight: 0 }), null)
  assert.equal(terminalSurfaceChanged(terminalSurface(term, host), null), false)
  assert.equal(terminalSurfaceChanged(null, null), false)
})

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
  const calls: boolean[] = []
  let customGlyphs = true
  const term = {
    options: {
      get customGlyphs() { return customGlyphs },
      set customGlyphs(value: boolean) { calls.push(value); customGlyphs = value },
    },
  }

  assert.equal(reflowVisibleTerminalRenderer(term, visible), true)
  assert.deepEqual(calls, [false, true])
  assert.equal(term.options.customGlyphs, true)

  const hidden = { ...visible, clientWidth: 0 }
  assert.equal(reflowVisibleTerminalRenderer(term, hidden), false)
  assert.deepEqual(calls, [false, true])

  assert.equal(reflowVisibleTerminalRenderer({ options: {} }, visible), false)
})

test('desktop Codex preserves its documented 80-column composer floor by reducing type', () => {
  assert.equal(CODEX_MIN_DESKTOP_COLUMNS, 80)
  assert.equal(terminalWidthPolicyFontSize('codex', false, 72, 11), 9)
  assert.equal(terminalWidthPolicyFontSize('codex', false, 40, 11), CODEX_MIN_DESKTOP_FONT_PX)
  assert.equal(terminalWidthPolicyFontSize('codex', false, 80, 11), 11)
  assert.equal(terminalWidthPolicyFontSize('codex', false, 120, 11), 11)
  // The unified mobile terminal is intentionally narrow and keeps the readable base type.
  assert.equal(terminalWidthPolicyFontSize('codex', true, 40, 11), 11)
  assert.equal(terminalWidthPolicyFontSize('claude', false, 40, 11), 11)
})

test('Claude panes stop expanding before its resize-corruption range', () => {
  assert.equal(CLAUDE_MAX_DESKTOP_COLUMNS, 120)
  const source = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'TerminalPane.tsx'), 'utf8')
  assert.match(source, /width:'100%'/)
  assert.match(source, /maxWidth:`calc\(\$\{CLAUDE_MAX_DESKTOP_COLUMNS\}ch \+ 11px\)`/)
  assert.match(source, /justifySelf:'center'/)
})

test('surface repair debt survives zero-sized frames and completes once measurable', () => {
  let measurable = false
  let attempts = 0
  let nextFrame = 1
  const frames = new Map<number, () => void>()
  const scheduler = createSurfaceRepairScheduler(
    () => {
      attempts += 1
      return measurable
    },
    () => true,
    {
      requestFrame: fn => {
        const id = nextFrame
        nextFrame += 1
        frames.set(id, fn)
        return id
      },
      cancelFrame: id => { frames.delete(id) },
    },
    2,
  )
  const flushFrame = () => {
    const entry = frames.entries().next().value as [number, () => void] | undefined
    assert.ok(entry, 'expected a scheduled surface repair frame')
    frames.delete(entry[0])
    entry[1]()
  }

  scheduler.request()
  flushFrame()
  flushFrame()
  flushFrame()
  assert.equal(attempts, 3)
  assert.equal(frames.size, 0, 'bounded retries must stop spinning')
  assert.equal(scheduler.owed, true, 'an unmeasurable attempt must not clear repair debt')

  measurable = true
  scheduler.resume()
  flushFrame()
  assert.equal(attempts, 4)
  assert.equal(scheduler.owed, false)
})

test('surface confirmation debt defers while hidden and resumes on reveal', () => {
  let visible = false
  let attempts = 0
  let pending: (() => void) | null = null
  const scheduler = createSurfaceRepairScheduler(
    () => {
      attempts += 1
      return visible
    },
    () => visible,
    {
      requestFrame: fn => { pending = fn; return 1 },
      cancelFrame: () => { pending = null },
    },
  )
  const flushFrame = () => {
    assert.ok(pending, 'expected a scheduled surface repair frame')
    const frame = pending
    pending = null
    frame()
  }

  scheduler.request()
  flushFrame()
  assert.equal(attempts, 1)
  assert.equal(pending, null, 'a hidden pane must not spin animation frames')
  assert.equal(scheduler.owed, true)

  visible = true
  scheduler.resume()
  flushFrame()
  assert.equal(attempts, 2)
  assert.equal(scheduler.owed, false)
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

// The other half of that: an application scrolling its own viewport also reports nothing when
// the reader drags their way back to it, so the pane has to total the scroll it forwards in
// both directions. Totalling only the drag back is what left a chip up over a viewport already
// sitting exactly where tapping it would have sent them.
test('the application-tail estimate is spent by dragging back toward the newest output', () => {
  const rowHeight = 17
  // A drag back through the history: five touch moves' worth of forwarded wheel pixels.
  let distance = 0
  for (let move = 0; move < 5; move += 1) distance = trackAppTailDistance(distance, -40)
  assert.equal(distance, 200)
  assert.equal(appOffTailByDistance(distance, rowHeight), true)
  // Dragging the other way spends the same total, and the chip goes with the last of it.
  for (let move = 0; move < 4; move += 1) distance = trackAppTailDistance(distance, 40)
  assert.equal(appOffTailByDistance(distance, rowHeight), true)
  distance = trackAppTailDistance(distance, 40)
  assert.equal(distance, 0)
  assert.equal(appOffTailByDistance(distance, rowHeight), false)
})

test('the application-tail estimate banks nothing past the tail and ignores a resting finger', () => {
  // The application clamps at its newest line, so scrolling down there moves nothing. Credit
  // for it would have to be spent again before the next drag back could raise the chip.
  assert.equal(trackAppTailDistance(0, 600), 0)
  assert.equal(trackAppTailDistance(50, 600), 0)
  // Sub-row jitter - a finger resting on the glass between touch events - is less than xterm
  // turns into a wheel report, so the application never moves and the chip must not appear.
  const jitter = trackAppTailDistance(0, -2)
  assert.equal(jitter, 2)
  assert.equal(appOffTailByDistance(jitter, 17), false)
  // A whole row of it does, since that is one wheel report the application acts on.
  assert.equal(appOffTailByDistance(trackAppTailDistance(jitter, -15), 17), true)
  // A pane with no measurable row still has to mean something by "scrolled".
  assert.equal(appOffTailByDistance(0, 0), false)
  assert.equal(appOffTailByDistance(1, 0), true)
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

// The defect this encodes: below ConPTY's reflow threshold a local resize just appends
// rows, so every pass measured "cheap" and the scheduler never engaged — a continuous
// splitter drag sent ~22 pseudoconsole resizes per second per visible pane, each one a
// full CLI repaint. The pass's real cost includes the half that runs in another process.
test('a pass that shipped a resize frame is expensive regardless of the local clock', () => {
  assert.equal(effectiveViewportCost(0.02, true), EXPENSIVE_VIEWPORT_PASS_MS)
  assert.equal(effectiveViewportCost(25, true), 25)
  // A pass that only measured (grid unchanged, nothing sent) stays as cheap as it was.
  assert.equal(effectiveViewportCost(0.02, false), 0.02)
})

test('resize-sending passes make a splitter drag coalesce even when local fits are free', () => {
  const { timers, advance } = fakeTimers()
  let runs = 0
  const scheduler = createViewportScheduler(() => { runs += 1 }, timers)
  // First pass of the drag runs eagerly and sends a resize; every local fit is
  // sub-millisecond, which without the downstream charge kept this eager forever.
  scheduler.request(true)
  assert.equal(runs, 1)
  scheduler.observeCost(effectiveViewportCost(0.05, true))
  for (let elapsed = 0; elapsed < VIEWPORT_SETTLE_MAX_MS * 2; elapsed += 16) {
    scheduler.request(true)
    advance(16)
  }
  // The drag floods requests every frame; the cap bounds updates to one per
  // VIEWPORT_SETTLE_MAX_MS rather than one per crossed cell boundary.
  assert.ok(runs <= 4, `expected capped cadence, got ${runs} passes`)
  assert.ok(runs >= 2, `the gesture must still update at the cap, got ${runs}`)
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
