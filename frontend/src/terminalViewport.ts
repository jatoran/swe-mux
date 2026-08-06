type TerminalDimensions = { cols: number; rows: number; refresh: (start: number, end: number) => void }
type TerminalRendererOptions = { options: { customGlyphs?: boolean } }
type TerminalFit = { fit: () => void }
type TerminalHost = { isConnected: boolean; clientWidth: number; clientHeight: number }

export function terminalHostIsVisible(host: TerminalHost | null): host is TerminalHost {
  return !!host && host.isConnected && host.clientWidth > 0 && host.clientHeight > 0
}

export function refitVisibleTerminal(fit: TerminalFit, host: TerminalHost | null): boolean {
  if (!terminalHostIsVisible(host)) return false
  fit.fit()
  return true
}

/**
 * Recalculate xterm's renderer pixels after a pane returns from `display:none`.
 *
 * FitAddon deliberately skips `term.resize` when the grid has not changed. The public resize
 * method has the same early return, while the DOM or canvas surface can still retain dimensions
 * from before the pane was hidden and occupy only part of its host. `customGlyphs` is a public,
 * non-geometric option that xterm treats as renderer-invalidating: changing it synchronously
 * clears the renderer, calls its `handleResize`, and refreshes every row. Restore it immediately
 * so the repair changes no user setting and sends no resize frame to the PTY.
 */
export function reflowVisibleTerminalRenderer(
  term: TerminalRendererOptions,
  host: TerminalHost | null,
): boolean {
  if (!terminalHostIsVisible(host)) return false
  const customGlyphs = term.options.customGlyphs
  if (typeof customGlyphs !== 'boolean') return false
  term.options.customGlyphs = !customGlyphs
  term.options.customGlyphs = customGlyphs
  return true
}

/**
 * Whether an attaching pane may register the dimensions it is about to report.
 *
 * Two things have to hold, and the second is not implied by the first: the pane is on
 * screen, *and* it has just fitted itself. Skipping the fit check is what pinned whole
 * sessions to xterm's 80x24 construction default. A pane whose host measures zero — a
 * warm pane behind another tab is `display:none` — cannot fit, so `term.cols/rows` are
 * whatever they last were: the unfitted default on a first attach, and on a reconnect
 * the grid `applyLetterbox` resized to *another* device's size, because leaving a
 * letterbox restores the font but not the grid. Reporting either re-registers a size
 * nobody measured, and since the owner dictates geometry it becomes the whole session's
 * size — including for the client that can actually see it.
 */
export function attachRegistersViewport(fitted: boolean, paneHidden: boolean): boolean {
  return fitted && !paneHidden
}

export function redrawVisibleTerminal(term: TerminalDimensions, host: TerminalHost | null): boolean {
  if (!terminalHostIsVisible(host) || term.rows < 1) return false
  term.refresh(0, term.rows - 1)
  return true
}

type TerminalTail = {
  buffer: { active: { viewportY: number; baseY: number } }
  scrollToBottom: () => void
}

// A scroll that lands short still made progress, so a bounded retry converges. The cap only
// exists so a terminal that somehow never settles costs a few calls instead of spinning.
const TAIL_SCROLL_ATTEMPTS = 4

// Ctrl+End, the same bytes the command rail's `^End` key sends.
export const APP_TAIL_KEY = '\x1b[1;5F'

/**
 * Whether reaching the newest output has to be asked of the *application* as well as of xterm.
 *
 * There are two viewports stacked here, and only one of them is xterm's. A TUI that keeps its
 * own scroll position moves that one instead, so scrolling the terminal alone lands on a
 * viewport nobody was looking at and the button reads as dead. Claude keeps such a viewport;
 * Codex does not — its binary carries no mouse-mode enables at all, so every scroll it is part
 * of is xterm's own — which is exactly why jump-to-latest has always worked in a Codex session
 * and not in a Claude one on a phone. The rail's `^End` worked in both because it happens to do
 * both halves; this is the same pair of moves, behind the chip.
 *
 * `appReceivesScroll` generalises the rule: whoever is being handed this pane's scroll gestures
 * is who has to be asked to undo them. That is an application holding the mouse — the signal
 * `mobileDragTarget` already routes phone drags on — or one the pane has been forwarding drags
 * to whatever its mouse mode says now. A shell is excluded outright: it owns no viewport, so
 * the bytes would only land in whatever command line the user was halfway through typing.
 */
export function appOwnsTail(backend: string, appReceivesScroll: boolean): boolean {
  if (backend === 'shell') return false
  return backend === 'claude' || appReceivesScroll
}

/**
 * How far an application's own viewport sits above its newest output, as a running estimate.
 *
 * There is nothing to read here, only something to remember. An application holding the mouse
 * scrolls a viewport it never reports, and the one buffer this pane can inspect - xterm's -
 * stays pinned to its tail throughout, which is why the buffer check answers "on the tail" for
 * every Claude session. So the pane totals the scroll it forwards, in the pixels those wheel
 * events carry, and reads the total back as the distance. Signs follow `WheelEvent.deltaY`:
 * negative is a drag back through the history, positive is a drag toward the newest output.
 *
 * Clamped at zero because the application clamps at its tail too. Scrolling down while already
 * there moves nothing, and banking it would leave a later drag back through the history paying
 * off phantom credit before the pane noticed it had happened.
 *
 * An estimate, not a fact, and its two drifts point in opposite directions. Dragging past the
 * top of the application's own history totals distance nothing travelled, so a drag back that
 * really did reach the newest output can leave the estimate short of zero - the chip stays up,
 * and a tap is what takes it down. Output arriving while the reader is scrolled up moves the
 * tail away from them with no gesture to total, so the chip can go early; the drag they are
 * already making is the recovery, and any drag back through the history restores it.
 */
export function trackAppTailDistance(distancePx: number, deltaPixels: number): number {
  return Math.max(0, distancePx - deltaPixels)
}

/**
 * Whether that estimate is far enough to mean the application actually scrolled.
 *
 * A row, because a row is the granularity it is scrolled at: the pane converts drag pixels into
 * whole wheel-button reports and carries the remainder (`terminalScrollSteps`), so a drag worth
 * less than a row moves nothing behind it. A finger resting on the glass delivers exactly that -
 * a pixel or two of jitter per touch event, in whichever direction the hand settles - and
 * counting it as a scroll raises a chip over a session nobody scrolled.
 */
export function appOffTailByDistance(distancePx: number, rowHeightPx: number): boolean {
  return distancePx >= Math.max(1, rowHeightPx)
}

/**
 * Put a terminal back on its newest line, and keep at it until it actually lands.
 *
 * One `scrollToBottom()` is not reliably enough. xterm applies every scroll through the DOM
 * scroller it owns in `browser/Viewport.ts`, and republishes that scroller's dimensions only
 * from `Viewport._sync()`. A refit (`fit.fit()` → `term.resize`) moves `baseY` — a shorter
 * grid pushes rows into scrollback — but answers `onResize` with `queueSync()`, which defers
 * the dimension update to a *queued* render callback. A scroll issued before that callback
 * runs is clamped to the pre-resize maximum and stops exactly `oldRows - newRows` rows short
 * of the tail. On a phone that window is wide open: the soft keyboard fires `visualViewport`
 * resizes all through its open animation, and each one refits the pane.
 *
 * The first call is what repairs it. Moving the viewport fires `onScroll`, and `_sync()` is
 * subscribed to that one directly rather than queued, so the scroller learns its real range
 * as a side effect. Re-issuing therefore converges inside the same event — no frame wait and
 * no timer. Stopping as soon as a pass makes no progress keeps it finite.
 */
export function scrollTerminalToTail(term: TerminalTail | null | undefined): boolean {
  if (!term) return false
  for (let attempt = 0; attempt < TAIL_SCROLL_ATTEMPTS; attempt += 1) {
    const { viewportY, baseY } = term.buffer.active
    if (viewportY >= baseY) return true
    term.scrollToBottom()
    if (term.buffer.active.viewportY <= viewportY) return false
  }
  return term.buffer.active.viewportY >= term.buffer.active.baseY
}

type TerminalScroll = TerminalTail & { scrollLines: (amount: number) => void }

/**
 * How far above the newest line the viewport is sitting.
 *
 * The anchor a resize has to preserve, and deliberately measured from the tail rather
 * than as an absolute `viewportY`. Growing the grid moves `baseY` — on a ConPTY buffer
 * the daemon tells xterm to add blank rows rather than pull scrollback down, and
 * shrinking pushes rows the other way — so absolute position is exactly the thing that
 * does not survive. Distance from the tail is what keeps the same text under the reader.
 */
export function terminalRowsAboveTail(term: TerminalTail | null | undefined): number {
  if (!term) return 0
  const { viewportY, baseY } = term.buffer.active
  return Math.max(0, baseY - viewportY)
}

/**
 * Put a viewport back the same distance from the tail a resize found it at.
 *
 * The off-tail half of `scrollTerminalToTail`, and needed for the same reason: xterm
 * republishes its DOM scroller's range from a *queued* render callback, so during a
 * resize burst the scroller's maximum is stale and every scroll against it is clamped.
 * A soft keyboard animating open or closed fires those resizes ~20 times, and a reader
 * who had scrolled up got clamped a little further each pass — walking the viewport to
 * the top of the transcript, which is only reachable at all in a session whose history
 * lives in scrollback rather than on the alternate screen.
 *
 * Re-issuing converges inside the same event, because moving the viewport fires
 * `onScroll` and the scroller learns its real range as a side effect. Bounded so a
 * buffer that never settles costs a few calls instead of spinning.
 */
export function restoreTerminalScrollAnchor(
  term: TerminalScroll | null | undefined,
  rowsAboveTail: number,
): boolean {
  if (!term || rowsAboveTail <= 0) return false
  const target = () => Math.max(0, term.buffer.active.baseY - rowsAboveTail)
  for (let attempt = 0; attempt < TAIL_SCROLL_ATTEMPTS; attempt += 1) {
    const { viewportY } = term.buffer.active
    const wanted = target()
    if (viewportY === wanted) return true
    term.scrollLines(wanted - viewportY)
    // No progress means the buffer cannot reach it (the anchor is older than the
    // scrollback now holds), and re-issuing would spin.
    if (term.buffer.active.viewportY === viewportY) return false
  }
  return term.buffer.active.viewportY === target()
}

/** What a pane is currently showing, in both the grid and the box drawn into. */
export type TerminalSurface = { cols: number; rows: number; width: number; height: number }

export function terminalSurface(
  term: { cols: number; rows: number },
  host: TerminalHost | null,
): TerminalSurface | null {
  if (!terminalHostIsVisible(host)) return null
  return { cols: term.cols, rows: term.rows, width: host.clientWidth, height: host.clientHeight }
}

/**
 * Whether a pass reshaped what is on screen, and so owes a confirmation repaint.
 *
 * The grid alone is not the question. A pane whose box changed while its grid did not
 * has a renderer holding pixel dimensions for the old box (FitAddon skips `term.resize`
 * when the grid is unchanged), and a pane whose grid changed has rows that were painted
 * — or missed — during a layout that was still moving. Both are the same bug to a reader:
 * a strip of the terminal that draws nothing.
 */
export function terminalSurfaceChanged(
  previous: TerminalSurface | null,
  current: TerminalSurface | null,
): boolean {
  if (!current) return false
  if (!previous) return true
  return previous.cols !== current.cols
    || previous.rows !== current.rows
    || previous.width !== current.width
    || previous.height !== current.height
}

/**
 * A viewport pass slower than this is not affordable once per animation frame.
 *
 * A fit is not a layout tweak. It is `term.resize`, which on a ConPTY-backed buffer
 * appends a `BufferLine` (with its own `Uint32Array`) per gained row and can rebuild
 * the whole `CircularList` backing array when the scrollback bound grows; a `resize`
 * frame to the daemon, which resizes the real pseudoconsole and makes the CLI repaint
 * everything it is showing; and a full `refresh()` of every row. On a pane holding one
 * screen that is microseconds. On one holding tens of thousands of real scrollback
 * lines — which is what mux's Codex sessions are, since they run with
 * `alternate_screen=never` so the transcript lives in scrollback — it is not.
 *
 * 8 ms is half a 60 Hz frame: above it, doing this per frame cannot keep up by
 * definition, so the work has to be coalesced instead.
 */
export const EXPENSIVE_VIEWPORT_PASS_MS = 8

/** Quiet period that ends a burst. Below the ~150 ms that reads as lag. */
export const VIEWPORT_SETTLE_MS = 120

/**
 * The cost a pass reports to the scheduler, given what it actually did.
 *
 * Timing the browser-side work alone misses the half of the cost that never runs in this
 * process: a pass that sent a `resize` frame resized the real pseudoconsole, and the CLI
 * behind it repaints everything it is showing — for an alternate-screen agent that is its
 * whole screen, ~20 KB of output per resize, at whatever rate the frames arrive. On this
 * side that pass can still measure microseconds: below ConPTY's reflow threshold xterm's
 * own resize just appends rows. Measured on a 2x2 grid, a continuous splitter drag sent
 * ~22 pseudoconsole resizes per second per pane — ~1,200 CLI repaints across one gesture —
 * precisely because every pass was "cheap". A pass that reshaped the PTY is therefore
 * expensive by definition, whatever the local clock says: reporting at least
 * EXPENSIVE_VIEWPORT_PASS_MS makes the next burst coalesce, which is one resize per
 * settle (or per VIEWPORT_SETTLE_MAX_MS during a gesture that never pauses) instead of
 * one per crossed cell boundary.
 */
export function effectiveViewportCost(elapsedMs: number, sentResize: boolean): number {
  return sentResize ? Math.max(elapsedMs, EXPENSIVE_VIEWPORT_PASS_MS) : elapsedMs
}

/**
 * How many frames a revealed pane may wait for its host to have layout.
 *
 * A pass whose host measures zero cannot fit, and returning was a silent dead end: the
 * pane kept whatever grid it had before it was hidden, and nothing rescheduled. The
 * ResizeObserver looks like the safety net and is not a reliable one — it fires
 * `scheduleBurstFit`, which on a pane expensive enough to matter is exactly the case the
 * scheduler coalesces, and it only fires at all if the box changes size again.
 *
 * Bounded rather than a loop: a few frames covers layout settling after `display:none`
 * is lifted, and anything longer is a pane that is not coming back, which the hidden
 * check ahead of this already handles.
 */
export const VIEWPORT_MEASURE_RETRY_FRAMES = 5

/**
 * Hard cap on coalescing, so a continuous gesture still updates.
 *
 * A soft keyboard animates for ~250-400 ms and then stops, so the settle above ends
 * it. Dragging a splitter does not stop, and without this the terminal would hold its
 * old grid for the whole drag.
 */
export const VIEWPORT_SETTLE_MAX_MS = 600

export interface SettleTimers {
  now: () => number
  setTimer: (fn: () => void, ms: number) => number
  clearTimer: (id: number) => void
}

export interface ViewportScheduler {
  /**
   * Ask for a viewport pass. `burst` marks a trigger that arrives in floods —
   * `visualViewport`/`window` resize and `ResizeObserver` — as opposed to a discrete
   * one (becoming visible, a pane revealed, a rail change), which always runs now.
   */
  request: (burst: boolean) => void
  /** Report how long the last pass took, so the next burst can be judged. */
  observeCost: (milliseconds: number) => void
  cancel: () => void
  readonly deferred: boolean
}

/**
 * Run viewport passes eagerly while they are cheap, and coalesce them once they are not.
 *
 * Deliberately adaptive rather than keyed on the backend or on a buffer-size guess. The
 * thing that makes a fit unaffordable is how much work *this* pane's buffer makes it,
 * which is exactly what timing the last pass measures — and it lands on the right answer
 * for a case nobody enumerated (a Claude session that has left the alternate screen, a
 * shell with a huge `cat` in its scrollback) without naming it.
 *
 * The first pass of a burst therefore always runs: it is both the responsive thing to do
 * and the measurement that decides the rest. On a small pane every frame keeps fitting,
 * exactly as before. On a large one the remaining ~20 frames of a keyboard animation
 * collapse into a single pass after it settles — and each frame skipped is also a
 * pseudoconsole resize the CLI does not have to repaint for.
 */
export function createViewportScheduler(
  run: () => void,
  timers: SettleTimers,
): ViewportScheduler {
  let timer: number | null = null
  let burstStartedAt = 0
  let lastCost = 0

  const fire = () => {
    timer = null
    burstStartedAt = 0
    run()
  }

  const cancel = () => {
    if (timer === null) return
    timers.clearTimer(timer)
    timer = null
    burstStartedAt = 0
  }

  return {
    request(burst: boolean) {
      if (!burst || lastCost < EXPENSIVE_VIEWPORT_PASS_MS) {
        cancel()
        run()
        return
      }
      const now = timers.now()
      if (timer === null) burstStartedAt = now
      else timers.clearTimer(timer)
      const elapsed = now - burstStartedAt
      const wait = Math.max(0, Math.min(VIEWPORT_SETTLE_MS, VIEWPORT_SETTLE_MAX_MS - elapsed))
      timer = timers.setTimer(fire, wait)
    },
    observeCost(milliseconds: number) {
      lastCost = milliseconds
    },
    cancel,
    get deferred() {
      return timer !== null
    },
  }
}
