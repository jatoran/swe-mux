import { appliesWidthEnvelope, minDesktopColumns, ownsScrollViewport } from './harnessRegistry.ts'

type TerminalDimensions = { cols: number; rows: number; refresh: (start: number, end: number) => void }
type TerminalRendererOptions = { options: { customGlyphs?: boolean } }
type TerminalFit = { fit: () => void }
type TerminalHost = { isConnected: boolean; clientWidth: number; clientHeight: number }

/**
 * Desktop width envelope for agent TUIs with known layout boundaries.
 *
 * Claude's live-region renderer leaves stale and duplicated cells across large
 * column changes. Past a comfortable reading width, a wider pane should add margin
 * rather than keep resizing the PTY. Codex publishes an 80-column minimum in its own
 * terminal diagnostics; below it the composer can wrap through visually blank
 * rows, so desktop panes reduce the font before accepting a narrower grid.
 *
 * The Claude half is a *setting* rather than a constant. Its evidence is a rendering
 * defect in a CLI that ships independently of this app, so the number that was right
 * when it was measured is not necessarily right later - and the cost of being wrong
 * lands entirely on the user, who cannot tell a deliberate envelope from a broken
 * resize. The Codex minimum stays fixed: it is that vendor's own published floor
 * rather than an observation about a renderer, and there is nothing to tune.
 */
export const CODEX_MIN_DESKTOP_COLUMNS = 80
export const CODEX_MIN_DESKTOP_FONT_PX = 8

/** The setting's value for "no cap": a Claude pane fills its box like every other. */
export const CLAUDE_MAX_COLUMNS_OFF = 0

/**
 * Mirrors `CLAUDE_MAX_COLUMNS` in `config.py`, pinned by
 * `tests/test_frontend_claude_width_contract.py`.
 *
 * Discrete steps rather than a free number, for the same reason the chrome scale uses
 * them: there is no useful difference between a 121- and a 123-column envelope, only a
 * way to land on a value that reads as broken. The range spans "barely wider than
 * today" to "wider than most windows", with `off` covering everything past that.
 */
export const CLAUDE_MAX_COLUMN_STEPS = [0, 100, 120, 140, 160, 200, 240, 320] as const

export type ClaudeMaxColumns = (typeof CLAUDE_MAX_COLUMN_STEPS)[number]

/** Today's behaviour. Installing this build changes nothing on screen. */
export const DEFAULT_CLAUDE_MAX_COLUMNS: ClaudeMaxColumns = 120

export const claudeMaxColumnsLabel = (columns: ClaudeMaxColumns): string =>
  columns === CLAUDE_MAX_COLUMNS_OFF ? 'No limit' : `${columns} columns`

/**
 * A config value as a known step, falling back to the default for anything else -
 * including a daemon older than this build, which sends no key at all.
 */
export function claudeMaxColumnsFrom(config: Record<string, unknown>): ClaudeMaxColumns {
  const raw = config.claude_max_columns
  if (typeof raw !== 'number' || !Number.isInteger(raw)) return DEFAULT_CLAUDE_MAX_COLUMNS
  return CLAUDE_MAX_COLUMN_STEPS.find(step => step === raw) ?? DEFAULT_CLAUDE_MAX_COLUMNS
}

/**
 * The column cap this pane's host should carry, or `CLAUDE_MAX_COLUMNS_OFF` for none.
 *
 * Compact panes are excluded on the same grounds as the Codex minimum: a desktop
 * envelope has no business overriding the geometry of a device whose whole screen is
 * narrower than the smallest value this accepts. That exclusion is inert today and
 * deliberately stated anyway, so a larger touch device cannot inherit it by accident.
 */
export function claudeWidthCap(
  backend: string,
  compactLayout: boolean,
  maxColumns: number,
): number {
  // Which harnesses the envelope applies to is declared (`width_envelope`), not
  // decided here, so a future TUI with the same live-region defect opts in from its
  // descriptor rather than by editing this module.
  if (!appliesWidthEnvelope(backend) || compactLayout) return CLAUDE_MAX_COLUMNS_OFF
  if (!Number.isInteger(maxColumns) || maxColumns <= 0) return CLAUDE_MAX_COLUMNS_OFF
  return maxColumns
}

/**
 * Pixels the host must add on top of the cell grid: xterm reserves a scrollbar
 * gutter outside the columns it reports, so a host sized to exactly `Nch` renders
 * N-1 columns.
 */
export const CLAUDE_CAP_GUTTER_PX = 11

/** The host's `max-width` at a given cap. `ch` resolves against the host's own font,
 *  which is set to match xterm's, so the cap follows the terminal font and UI scale. */
export const claudeHostMaxWidth = (cap: number): string =>
  `calc(${cap}ch + ${CLAUDE_CAP_GUTTER_PX}px)`

type CappedHost = { clientWidth: number; parentElement: { clientWidth: number } | null }

/**
 * Whether the cap is currently taking width away from this pane.
 *
 * Measured against the track the host sits in rather than derived from the column
 * count: the cap is a `max-width` over a `width:100%`, so "narrower than the space
 * available" is exactly the condition, and it stays correct whatever the font, the
 * gutter, or a future cap unit turn out to be. One pixel of tolerance, because
 * fractional cell widths make an uncapped host land a hair under its track.
 */
export function claudeWidthCapClamping(host: CappedHost | null, cap: number): boolean {
  if (cap <= CLAUDE_MAX_COLUMNS_OFF || !host?.parentElement) return false
  return host.parentElement.clientWidth > host.clientWidth + 1
}

export function terminalWidthPolicyFontSize(
  backend: string,
  compactLayout: boolean,
  proposedColumns: number,
  baseFontSize: number,
): number {
  // The floor is the vendor's own published minimum, declared per harness. Zero
  // means the vendor publishes none and the pane accepts whatever grid it is given.
  const minimum = minDesktopColumns(backend)
  if (
    minimum <= 0
    || compactLayout
    || !Number.isFinite(proposedColumns)
    || proposedColumns >= minimum
    || baseFontSize <= 0
  ) return baseFontSize
  const scaled = Math.floor(baseFontSize * proposedColumns / minimum)
  const floor = Math.min(baseFontSize, CODEX_MIN_DESKTOP_FONT_PX)
  return Math.max(floor, Math.min(baseFontSize, scaled))
}

export function terminalHostIsVisible(host: TerminalHost | null): host is TerminalHost {
  return !!host && host.isConnected && host.clientWidth > 0 && host.clientHeight > 0
}

export function refitVisibleTerminal(fit: TerminalFit, host: TerminalHost | null): boolean {
  if (!terminalHostIsVisible(host)) return false
  fit.fit()
  return true
}

/**
 * Recalculate xterm's renderer pixels after a pane returns from a hidden interval.
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
 * sessions to xterm's 80x24 construction default. A pane whose host measures zero cannot
 * fit, so `term.cols/rows` are
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
  return ownsScrollViewport(backend) || appReceivesScroll
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
 * Bounded rather than a loop: a few frames covers ordinary layout settling after
 * `display:none` is lifted. The caller retains fit debt after the burst and resumes it
 * from the next visibility, renderer-repair, observer, or health-sweep signal.
 */
export const VIEWPORT_MEASURE_RETRY_FRAMES = 5

export interface AnimationFrameTimers {
  requestFrame: (fn: () => void) => number
  cancelFrame: (id: number) => void
}

export interface SurfaceRepairScheduler {
  /** Record that renderer dimensions and pixels still need a successful repair. */
  markOwed: () => void
  /** Record the debt and start attempting it on the next frame. */
  request: () => void
  /** Retry existing debt after a new visibility or measurement signal. */
  resume: () => void
  cancel: () => void
  readonly owed: boolean
}

/**
 * Keep a terminal surface repair pending until an attempt actually succeeds.
 *
 * A newly visible pane can still have a zero-sized host on the first
 * animation frame. A one-shot redraw silently loses the repair in that frame, as does
 * a delayed confirmation that happens to land after the pane was hidden again. This
 * scheduler retries briefly while the pane is logically visible, then retains the debt
 * without spinning. A later reveal, ResizeObserver pass, or health sweep calls `resume`.
 */
export function createSurfaceRepairScheduler(
  repair: () => boolean,
  mayRetry: () => boolean,
  frames: AnimationFrameTimers,
  maxRetryFrames = VIEWPORT_MEASURE_RETRY_FRAMES,
): SurfaceRepairScheduler {
  let frame: number | null = null
  let owed = false
  let retries = 0

  const cancelFrame = () => {
    if (frame === null) return
    frames.cancelFrame(frame)
    frame = null
  }
  const schedule = () => {
    cancelFrame()
    frame = frames.requestFrame(run)
  }
  const run = () => {
    frame = null
    if (!owed) return
    if (repair()) {
      owed = false
      retries = 0
      return
    }
    if (mayRetry() && retries < maxRetryFrames) {
      retries += 1
      schedule()
    }
  }
  const restart = () => {
    retries = 0
    schedule()
  }

  return {
    markOwed() {
      owed = true
    },
    request() {
      owed = true
      restart()
    },
    resume() {
      if (!owed) return
      restart()
    },
    cancel() {
      cancelFrame()
      owed = false
      retries = 0
    },
    get owed() {
      return owed
    },
  }
}

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
