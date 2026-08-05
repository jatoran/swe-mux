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

/**
 * Whether a pane returning to screen may treat its own fresh fit as the session's size.
 *
 * `serverGeometry` is one round trip stale by construction, and a pane coming back from
 * `display:none` has usually just measured a box that changed while it was hidden (the
 * window was resized, the drawer opened, the UI scale moved). Comparing the two then is
 * guaranteed to disagree, so the pane letterboxes to the grid it had *before* it was
 * hidden, renders a frame or two at that size, and snaps when the daemon confirms the
 * size it already reported. That snap is the whole "it comes back smaller and then
 * resizes itself" report.
 *
 * The owner is the one client allowed to skip that wait: the daemon takes the input
 * owner's viewport verbatim (`terminal_arbitration.effective_geometry`), so the fit this
 * pane just measured *is* what the confirmation will say. Adopting it early is not a
 * guess.
 *
 * Non-owners must still letterbox. Their fit is a proposal that arbitration can reduce,
 * and rendering it as though it were settled is the fight this whole module exists to
 * prevent: a background desktop pane rewrapping an agent TUI to desktop width on a
 * phone. A pane with no fit yet has nothing to adopt.
 */
export function adoptsOwnGeometryOnReveal(
  ownsInput: boolean,
  fit: { cols: number; rows: number } | null,
): boolean {
  return ownsInput && fit !== null
}
