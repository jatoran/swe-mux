/** Rendering a session at a size this pane did not choose.
 *
 * One PTY has one size, and the device a human is typing into gets to pick it (see
 * the daemon's terminal_arbitration). Every other attached client therefore has to
 * render columns and rows it did not fit itself. Letterboxing does that by shrinking
 * the font until the owner's grid fits this pane, rather than by re-fitting — a
 * re-fit would push this pane's own dimensions back at the PTY, which is exactly the
 * fight that used to rewrap an agent TUI to desktop width on a phone screen.
 *
 * Font size, not a CSS transform: xterm measures cell geometry from the font, so
 * scaling that way keeps selection, mouse reporting and hit-testing consistent with
 * what is drawn. A transform would leave every coordinate off by the scale factor.
 */

/** Below this the text is not readable on any device, and shrinking further only
 *  hides that the pane is showing someone else's terminal. */
export const MIN_LETTERBOX_FONT_PX = 4

export type LetterboxInput = {
  /** Font size the cell measurements below were taken at. */
  fontSize: number
  cellWidth: number
  cellHeight: number
  cols: number
  rows: number
  hostWidth: number
  hostHeight: number
  /** This pane's normal font size; a letterbox never renders larger than it. */
  baseFontSize: number
}

/** Largest integer font size that fits `cols`x`rows` into the host box.
 *
 * Returns `baseFontSize` when the grid already fits (a desktop showing a phone's
 * 40x20 just leaves empty space) or when measurements are not usable yet. */
export function letterboxFontSize(input: LetterboxInput): number {
  const content = { width: input.cellWidth * input.cols, height: input.cellHeight * input.rows }
  if (content.width <= 0 || content.height <= 0 || input.fontSize <= 0) return input.baseFontSize
  if (input.hostWidth <= 0 || input.hostHeight <= 0) return input.baseFontSize
  const ratio = Math.min(input.hostWidth / content.width, input.hostHeight / content.height)
  if (!Number.isFinite(ratio) || ratio <= 0) return input.baseFontSize
  const scaled = Math.floor(input.fontSize * ratio)
  return Math.max(MIN_LETTERBOX_FONT_PX, Math.min(input.baseFontSize, scaled))
}

/** Whether the daemon's arbitrated size is this pane's own fit. */
export function geometryMatchesFit(
  geometry: { cols: number; rows: number } | null,
  fit: { cols: number; rows: number } | null,
): boolean {
  if (!geometry || !fit) return true
  return geometry.cols === fit.cols && geometry.rows === fit.rows
}
