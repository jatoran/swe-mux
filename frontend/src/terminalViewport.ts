type TerminalDimensions = { cols: number; rows: number; refresh: (start: number, end: number) => void }
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
 * Mouse tracking generalises the rule: an application that has taken the mouse is the thing
 * receiving scroll gestures, and `mobileDragTarget` already routes phone drags on that signal.
 * A shell is excluded outright — it owns no viewport, so the bytes would only land in whatever
 * command line the user was halfway through typing.
 */
export function appOwnsTail(backend: string, mouseTracking: boolean): boolean {
  if (backend === 'shell') return false
  return backend === 'claude' || mouseTracking
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
