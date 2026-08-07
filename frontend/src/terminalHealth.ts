import { terminalSurfaceChanged, type TerminalSurface } from './terminalViewport.ts'

/**
 * Terminal pane self-repair, the invariant half of "a terminal must always
 * display correctly". The event-driven machinery (ResizeObserver, visibility,
 * geometry frames, surface confirmation) covers every path anyone has named;
 * this sweep covers the paths nobody has: an event that never fired, a pass
 * that ran against a layout still in motion and whose confirmation raced, a
 * parser crash that silently killed xterm's write loop. Each check compares an
 * invariant against reality on a slow clock and routes the repair through the
 * ordinary machinery, so a false positive costs one cheap no-op pass rather
 * than a new behavior.
 */

/** Piggybacks on the pane's existing 5 s `terminal_state` report interval. */
export const TERMINAL_HEALTH_SWEEP_MS = 5000

/**
 * How far byte arrival may run ahead of parse progress before the write
 * pipeline is declared dead. xterm parses megabytes per second, so multiple
 * seconds of arrears means the write loop is no longer running — an exception
 * escaped `_innerWrite`, which never reschedules (measured 2026-08-06: the
 * bundling-broken DECRQM handler killed every OMP pane exactly this way).
 */
export const WRITE_PIPELINE_STALL_MS = 4000

/** Auto-remount budget: more failures than this inside the window means the
 *  poison is in the replayed bytes themselves and remounting only loops. */
export const REMOUNT_ATTEMPT_LIMIT = 2
export const REMOUNT_ATTEMPT_WINDOW_MS = 5 * 60_000

/**
 * Whether xterm's write pipeline has stopped consuming what the socket delivers.
 *
 * `lastBytesAt` advances on every binary frame; `lastParsedAt` advances on every
 * `onWriteParsed`. A live pipeline keeps them within milliseconds. Requiring the
 * last delivery itself to be old keeps a burst that landed this instant from
 * being judged before the parser had a turn of the event loop to consume it.
 */
export function writePipelineStalled(
  lastBytesAt: number | null,
  lastParsedAt: number | null,
  now: number,
  stallMs = WRITE_PIPELINE_STALL_MS,
): boolean {
  if (lastBytesAt === null) return false
  if (now - lastBytesAt < 1000) return false
  const parsed = lastParsedAt ?? 0
  return lastBytesAt - parsed > stallMs
}

/**
 * Whether a visible, settled pane is drawing a different surface than the one
 * it last confirmed — the state the user repairs by "bumping" a splitter.
 * Replay and hidden panes are excluded because their surfaces are legitimately
 * in flux; a letterboxed pane is NOT excluded, because its confirmed surface
 * records the letterboxed grid and only real drift differs from it.
 */
export function surfaceDrifted(
  confirmed: TerminalSurface | null,
  current: TerminalSurface | null,
  replaying: boolean,
  hidden: boolean,
): boolean {
  if (replaying || hidden || !current) return false
  return terminalSurfaceChanged(confirmed, current)
}

/**
 * Whether xterm's current grid differs from a fresh fit of its visible host.
 *
 * Surface dimensions alone cannot detect a stale grid: a half-height 20-row terminal
 * can be faithfully rendered and confirmed inside a host that now fits 40 rows. A
 * letterboxed pane is deliberately showing the shared grid instead of its local fit.
 */
export function terminalFitDrifted(
  current: { cols: number; rows: number },
  proposed: { cols: number; rows: number } | undefined,
  replaying: boolean,
  hidden: boolean,
  letterboxed: boolean,
): boolean {
  if (replaying || hidden || letterboxed || !proposed) return false
  if (!Number.isFinite(proposed.cols) || !Number.isFinite(proposed.rows)) return false
  return current.cols !== proposed.cols || current.rows !== proposed.rows
}

/**
 * Whether a dead pane may rebuild itself, and the attempt ledger after asking.
 * Bounded so a poison byte sequence in the retained buffer (which every rebuild
 * replays) degrades into one visible error instead of a remount loop.
 */
export function remountDecision(
  attempts: readonly number[],
  now: number,
  limit = REMOUNT_ATTEMPT_LIMIT,
  windowMs = REMOUNT_ATTEMPT_WINDOW_MS,
): { allow: boolean; attempts: number[] } {
  const recent = attempts.filter(at => now - at < windowMs)
  if (recent.length >= limit) return { allow: false, attempts: recent }
  return { allow: true, attempts: [...recent, now] }
}
