// Giving the soft keyboard its own space instead of letting it cover the terminal.
//
// The mobile terminal cannot be resized while it holds a conversation: an
// alternate-screen PTY has no scrollback, so shrinking it discards the rows that no
// longer fit and growing back appends blanks rather than restoring them. That is why
// the keyboard slides the pane rather than shortening it (see `mobileKeyboard.ts`), and
// why the slide leaves half a grid off-screen.
//
// The slide is the wrong half on a fresh session. The grid is full height, the harness
// has painted a header at the top, its composer sits at the bottom, and everything
// between is blank — so the half the keyboard leaves visible is the blank half, and the
// reply to a first message lands in the hidden one.
//
// This module decides when the pane may reserve the keyboard's height *up front*, so the
// grid is born the size it will be typed into and the keyboard covers nothing. The whole
// safety argument is one sentence: a resize can only destroy rows that do not fit
// afterwards, so reserve only while everything painted still fits, and give the space
// back the moment the session grows into it. The dangerous direction (shrinking a full
// screen) is the one case the predicate refuses; growing back is lossless.
//
// Nothing here knows which harness is running. Painted extent, not a per-CLI table of
// startup layouts, is what decides — a harness that draws its whole UI immediately never
// qualifies, one that starts sparse does, and both answers come from the same grid read.

/** Below this the reserved grid is too short to be worth typing into. */
export const RESERVE_MIN_ROWS = 12

/**
 * Painted rows must clear the reserved grid by this much before reserving. Together with
 * `RESERVE_RELEASE_MARGIN_ROWS` it is the deadband that keeps a session sitting exactly
 * at the boundary from resizing back and forth on every write.
 */
export const RESERVE_ENGAGE_MARGIN_ROWS = 3

/** Painted rows this close to the bottom of the reserved grid mean the session has grown into it. */
export const RESERVE_RELEASE_MARGIN_ROWS = 1

/** Minimum time between reservation changes, so a streaming reply cannot drive a resize burst. */
export const RESERVE_DWELL_MS = 4000

/**
 * What to reserve before this device has ever shown its keyboard, as a fraction of the
 * layout viewport. Android keyboards land between 35% and 50% of a phone screen; the
 * first real measurement replaces this and is remembered from then on.
 */
export const DEFAULT_KEYBOARD_FRACTION = 0.42

/** Reserving more than this much of the pane would leave a strip, not a terminal. */
export const MAX_RESERVE_FRACTION = 0.6

/** How many rows of the grid have anything on them. */
export function paintedRowCount(rowCount: number, rowText: (index: number) => string): number {
  let painted = 0
  for (let index = 0; index < rowCount; index += 1) {
    if (rowText(index).trim() !== '') painted += 1
  }
  return painted
}

/**
 * The pixels to hold back for the keyboard.
 *
 * `measured` is this device's last observed keyboard inset, which is the only number that
 * matches the keyboard the user actually has. The fraction is the stand-in until one has
 * been seen, and the clamp is what keeps a bad measurement (a browser-chrome collapse
 * mistaken for a keyboard, a rotated tablet) from reserving away the whole pane.
 */
export function reservedKeyboardPx(measured: number, layoutHeight: number): number {
  if (!Number.isFinite(layoutHeight) || layoutHeight <= 0) return 0
  const wanted = Number.isFinite(measured) && measured > 0 ? measured : layoutHeight * DEFAULT_KEYBOARD_FRACTION
  return Math.round(Math.min(wanted, layoutHeight * MAX_RESERVE_FRACTION))
}

export type ReserveInput = {
  /** Whether the pane is currently holding space back. */
  reserved: boolean
  /** Rows the grid has right now (already reduced when `reserved`). */
  rows: number
  /** Rows the reservation costs, at the current row height. */
  reserveRows: number
  /** Rows of the current grid with content on them. */
  painted: number
  /**
   * Whether this pane may reserve at all: a compact touch layout, drawing its own fit
   * rather than another device's, and on screen. A pane that stops being eligible gives
   * the space back, because growing is always safe and a desktop pane has no keyboard.
   */
  eligible: boolean
  /**
   * Whether the grid can be trusted right now. A replaying or unsettled buffer reads as
   * emptier than the session really is, and reserving on that reading would shrink a PTY
   * whose content simply has not arrived yet — the one resize this module exists to avoid.
   */
  measurable: boolean
  now: number
  /** When the reservation last changed, or 0 if it never has. */
  changedAt: number
}

export type ReserveDecision = { reserved: boolean; reason: string }

/**
 * Whether the pane should be holding keyboard space back on this reading.
 *
 * Engaging is gated on everything painted fitting the smaller grid with room to spare;
 * releasing happens as soon as the session has filled the smaller grid, which is the
 * point the reader stops wanting the reservation and starts wanting the rows. Releasing
 * ignores the dwell timer: giving space back is the safe direction and should never wait.
 */
export function nextReserveState(input: ReserveInput): ReserveDecision {
  const { reserved, rows, reserveRows, painted, eligible, measurable, now, changedAt } = input
  if (!eligible) return { reserved: false, reason: reserved ? 'ineligible' : 'idle' }
  if (!measurable) return { reserved, reason: 'unmeasurable' }
  if (reserved) {
    if (painted >= rows - RESERVE_RELEASE_MARGIN_ROWS) return { reserved: false, reason: 'grew_into_grid' }
    return { reserved: true, reason: 'still_fits' }
  }
  const targetRows = rows - reserveRows
  if (targetRows < RESERVE_MIN_ROWS) return { reserved: false, reason: 'too_short' }
  if (painted + RESERVE_ENGAGE_MARGIN_ROWS > targetRows) return { reserved: false, reason: 'would_not_fit' }
  if (changedAt > 0 && now - changedAt < RESERVE_DWELL_MS) return { reserved: false, reason: 'dwell' }
  return { reserved: true, reason: 'fits' }
}
