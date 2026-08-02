type TerminalDimensions = { cols: number; rows: number; refresh: (start: number, end: number) => void }
type TerminalRendererDimensions = { cols: number; rows: number; resize: (cols: number, rows: number) => void }
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
 * FitAddon deliberately skips `term.resize` when the grid has not changed. The DOM or
 * canvas surface can still retain dimensions from before the pane was hidden, producing a
 * terminal that occupies only part of its host. A same-grid resize reaches the renderer's
 * `handleResize` path without sending a resize frame to the PTY; viewport reporting is owned
 * separately by TerminalPane.
 */
export function reflowVisibleTerminalRenderer(
  term: TerminalRendererDimensions,
  host: TerminalHost | null,
): boolean {
  if (!terminalHostIsVisible(host) || term.cols < 1 || term.rows < 1) return false
  term.resize(term.cols, term.rows)
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
