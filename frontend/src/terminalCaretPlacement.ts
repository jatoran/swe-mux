export type CaretPointerType = 'mouse' | 'pen' | 'touch'

export interface TerminalCaretCell {
  chars: string
  code: number
  width: number
  bgMode: number
  bg: number
  dim: boolean
}

export interface TerminalCaretLine {
  row: number
  cells: TerminalCaretCell[]
}

export interface TerminalCaretPosition {
  column: number
  row: number
}

export interface TerminalCaretSnapshot {
  cols: number
  rows: number
  viewportY: number
  baseY: number
  cursorX: number
  cursorY: number
  lines: TerminalCaretLine[]
}

export interface TerminalTapDecision {
  backend: string
  pointerType: CaretPointerType
  still: boolean
  primary: boolean
  modified: boolean
  readMode: boolean
  hasSelection: boolean
  mouseTracking: boolean
}

export type TerminalTapAction = 'none' | 'forward-mouse' | 'steer-caret'

export interface CaretSteerCommand {
  sequence: '\x1b[C' | '\x1b[D'
  count: number
  direction: -1 | 1
  distance: number
}

const DEFAULT_BG_MODE = 0
const CODEX_LIVE_PREFIXES = new Set(['›', '!', '»'])

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value))
}

function cellAt(snapshot: TerminalCaretSnapshot, row: number, column: number): TerminalCaretCell | undefined {
  const indexed=snapshot.lines[row-snapshot.viewportY]
  const line=indexed?.row===row?indexed:snapshot.lines.find(candidate=>candidate.row===row)
  return line?.cells[column]
}

function sameBackground(a: TerminalCaretCell | undefined, b: TerminalCaretCell | undefined): boolean {
  return !!a && !!b && a.bgMode === b.bgMode && a.bg === b.bg
}

function cellHasVisibleText(cell: TerminalCaretCell | undefined): boolean {
  return !!cell && cell.code !== 0 && cell.chars.trim().length > 0
}

function rowIsBlank(snapshot: TerminalCaretSnapshot, row: number): boolean {
  for (let column = 0; column < snapshot.cols; column += 1) {
    if (cellHasVisibleText(cellAt(snapshot, row, column))) return false
  }
  return true
}

function rowHasEmptyGutter(
  snapshot: TerminalCaretSnapshot,
  row: number,
  textStart: number,
  prompt?: { row: number; column: number },
): boolean {
  for (let column = 0; column < textStart; column += 1) {
    if (prompt?.row === row && prompt.column === column) continue
    if (cellHasVisibleText(cellAt(snapshot, row, column))) return false
  }
  return true
}

function rowHasComposerBackground(
  snapshot: TerminalCaretSnapshot,
  row: number,
  promptColumn: number,
  promptCell: TerminalCaretCell,
): boolean {
  if (promptCell.bgMode === DEFAULT_BG_MODE) return true
  const probes = [promptColumn, Math.floor(snapshot.cols / 2), Math.max(0, snapshot.cols - 2)]
  return probes.every(column => sameBackground(cellAt(snapshot, row, column), promptCell))
}

/**
 * Validate Codex's stable textarea frame when terminal palette detection leaves the
 * composer unstyled. Codex renders a blank row above and below the textarea, keeps its
 * live prefix in the left gutter, and places the hardware cursor inside that frame.
 */
function unstyledComposerLastRow(
  snapshot: TerminalCaretSnapshot,
  prompt: { row: number; column: number },
  cursorRow: number,
  textStart: number,
): number | null {
  if (prompt.row <= snapshot.viewportY || !rowIsBlank(snapshot, prompt.row - 1)) return null

  for (let row = prompt.row; row <= cursorRow; row += 1) {
    if (!rowHasEmptyGutter(snapshot, row, textStart, prompt)) return null
  }

  const viewportEnd = snapshot.viewportY + snapshot.rows
  for (let row = cursorRow + 1; row < viewportEnd; row += 1) {
    if (rowIsBlank(snapshot, row)) return row - 1
    if (!rowHasEmptyGutter(snapshot, row, textStart)) return null
  }
  return null
}

function contentBoundary(
  snapshot: TerminalCaretSnapshot,
  row: number,
  startColumn: number,
  ignoreDimPlaceholder: boolean,
  endColumn = snapshot.cols - 1,
): number {
  let boundary = startColumn
  for (let column = startColumn; column < endColumn; column += 1) {
    const cell = cellAt(snapshot, row, column)
    if (!cell || cell.code === 0 || !cell.chars || (ignoreDimPlaceholder && cell.dim)) continue
    boundary = Math.max(boundary, column + Math.max(1, cell.width))
  }
  return boundary
}

/**
 * Resolve a click/tap to a reachable cursor position inside Codex's live composer.
 *
 * Codex does not negotiate a terminal mouse protocol. Its stable observable contract is
 * the `›`/`!`/`»` live prefix, a two-column textarea inset, the composer frame, and the
 * hardware cursor. A distinct background is used when available, but Codex deliberately
 * falls back to the terminal's default background when palette detection is unavailable.
 * Refusing targets outside the remaining structure is important: arrow keys sent while a
 * dialog, transcript, or ordinary terminal output is active would be a real user-visible
 * mutation rather than a harmless missed click.
 */
export function resolveCodexCaretTarget(
  snapshot: TerminalCaretSnapshot,
  requested: TerminalCaretPosition,
): {
  current: TerminalCaretPosition
  target: TerminalCaretPosition
  promptRow: number
  textStart: number
} | null {
  if (snapshot.cols < 4 || snapshot.rows < 1 || snapshot.viewportY !== snapshot.baseY) return null
  const cursorRow = snapshot.baseY + snapshot.cursorY
  if (cursorRow < snapshot.viewportY || cursorRow >= snapshot.viewportY + snapshot.rows) return null
  const current = { column: clamp(snapshot.cursorX, 0, snapshot.cols), row: cursorRow }

  let prompt: { row: number; column: number; cell: TerminalCaretCell } | null = null
  for (let row = cursorRow; row >= snapshot.viewportY; row -= 1) {
    for (let column = 0; column <= Math.min(1, snapshot.cols - 1); column += 1) {
      const cell = cellAt(snapshot, row, column)
      if (!cell || !CODEX_LIVE_PREFIXES.has(cell.chars)) continue
      const textStart = column + 2
      if (current.column < textStart || !rowHasComposerBackground(snapshot, cursorRow, column, cell)) continue
      prompt = { row, column, cell }
      break
    }
    if (prompt) break
  }
  if (!prompt) return null

  const textStart = prompt.column + 2
  const unstyledLastRow = prompt.cell.bgMode === DEFAULT_BG_MODE
    ? unstyledComposerLastRow(snapshot, prompt, cursorRow, textStart)
    : null
  if (prompt.cell.bgMode === DEFAULT_BG_MODE && unstyledLastRow === null) return null

  let lastDraftRow = cursorRow
  for (let row = prompt.row; row < snapshot.viewportY + snapshot.rows; row += 1) {
    if (unstyledLastRow !== null) {
      if (row > unstyledLastRow || !rowHasEmptyGutter(snapshot, row, textStart, prompt)) break
    } else if (!rowHasComposerBackground(snapshot, row, prompt.column, prompt.cell)) break
    const ignorePlaceholder = row === prompt.row && current.row === prompt.row && current.column === textStart
    if (contentBoundary(snapshot, row, textStart, ignorePlaceholder) > textStart) lastDraftRow = row
  }

  const targetRow = clamp(requested.row, prompt.row, lastDraftRow)
  const targetIsComposerRow = unstyledLastRow !== null
    ? targetRow <= unstyledLastRow && rowHasEmptyGutter(snapshot, targetRow, textStart, prompt)
    : rowHasComposerBackground(snapshot, targetRow, prompt.column, prompt.cell)
  if (targetRow !== requested.row || !targetIsComposerRow) return null
  const ignorePlaceholder = targetRow === prompt.row && current.row === prompt.row && current.column === textStart
  const boundary = contentBoundary(snapshot, targetRow, textStart, ignorePlaceholder)
  let targetColumn = clamp(requested.column, textStart, boundary)
  const targetCell = cellAt(snapshot, targetRow, targetColumn)
  if (targetCell?.width === 0) targetColumn = Math.max(textStart, targetColumn - 1)

  return {
    current,
    target: { column: targetColumn, row: targetRow },
    promptRow: prompt.row,
    textStart,
  }
}

export type ResolvedCaretTarget = {
  current: TerminalCaretPosition
  target: TerminalCaretPosition
  promptRow: number
  textStart: number
}
export type CaretTargetResolver = (
  snapshot: TerminalCaretSnapshot,
  requested: TerminalCaretPosition,
) => ResolvedCaretTarget | null

// Measured against installed omp 17.2.10 (2026-08-07, 100x30 and 120x30): the
// composer is a rounded box whose top border embeds the status line and always
// reads `╭── π` at columns 0-4, draft lines sit on `│` interior rows with the
// final line fused into the `╰─ … ─╯` bottom border row, text starts at
// column 3 on every draft row, and the editor wraps before column cols-3, so
// the right border glyphs never meet content.
const OMP_BOX_TOP = '╭'
const OMP_BOX_SIDE = '│'
const OMP_BOX_BOTTOM = '╰'
const OMP_BRAND_COLUMN = 4
const OMP_BRAND = 'π'
const OMP_TEXT_START = 3

/**
 * Resolve a click/tap to a reachable cursor position inside OMP's composer.
 *
 * OMP negotiates no terminal mouse protocol (measured: no DECSET 1000-1016 at
 * startup), so like Codex the caret is steered with arrow keys against the
 * observable composer contract above. The `π`-branded top border is the
 * anchor: OMP's other bordered surfaces (model picker, approval dialogs)
 * carry titles or junctions there instead, and refusing them matters because
 * arrow keys sent into a picker move its selection rather than a caret.
 */
export function resolveOmpCaretTarget(
  snapshot: TerminalCaretSnapshot,
  requested: TerminalCaretPosition,
): ResolvedCaretTarget | null {
  if (snapshot.cols < 8 || snapshot.rows < 2 || snapshot.viewportY !== snapshot.baseY) return null
  const cursorRow = snapshot.baseY + snapshot.cursorY
  if (cursorRow < snapshot.viewportY || cursorRow >= snapshot.viewportY + snapshot.rows) return null
  const current = { column: clamp(snapshot.cursorX, 0, snapshot.cols), row: cursorRow }
  if (current.column < OMP_TEXT_START) return null

  const cursorEdge = cellAt(snapshot, cursorRow, 0)?.chars
  if (cursorEdge !== OMP_BOX_SIDE && cursorEdge !== OMP_BOX_BOTTOM) return null

  let topRow: number | null = null
  for (let row = cursorRow - 1; row >= snapshot.viewportY; row -= 1) {
    const edge = cellAt(snapshot, row, 0)?.chars
    if (edge === OMP_BOX_TOP) { topRow = row; break }
    if (edge !== OMP_BOX_SIDE) return null
  }
  if (topRow === null) return null
  if (cellAt(snapshot, topRow, OMP_BRAND_COLUMN)?.chars !== OMP_BRAND) return null

  let bottomRow = cursorRow
  if (cursorEdge === OMP_BOX_SIDE) {
    let found = -1
    for (let row = cursorRow + 1; row < snapshot.viewportY + snapshot.rows; row += 1) {
      const edge = cellAt(snapshot, row, 0)?.chars
      if (edge === OMP_BOX_BOTTOM) { found = row; break }
      if (edge !== OMP_BOX_SIDE) return null
    }
    if (found < 0) return null
    bottomRow = found
  }

  const targetRow = requested.row
  if (targetRow <= topRow || targetRow > bottomRow) return null
  const ignorePlaceholder = targetRow === current.row && current.column === OMP_TEXT_START
  const boundary = contentBoundary(
    snapshot, targetRow, OMP_TEXT_START, ignorePlaceholder, snapshot.cols - 3,
  )
  let targetColumn = clamp(requested.column, OMP_TEXT_START, boundary)
  const targetCell = cellAt(snapshot, targetRow, targetColumn)
  if (targetCell?.width === 0) targetColumn = Math.max(OMP_TEXT_START, targetColumn - 1)

  return {
    current,
    target: { column: targetColumn, row: targetRow },
    promptRow: topRow,
    textStart: OMP_TEXT_START,
  }
}

// Measured against installed pi 0.84.1 (2026-08-10, live panes at 100x30 and
// 64x24): the composer is bracketed by two rules of `─` spanning every column,
// draft text starts at column 0 with no gutter, prompt glyph, or right border,
// and the rows between the rules are exactly the wrapped draft — an empty draft
// is a single blank row, and the bottom rule sits directly under the last draft
// row. `/` and `!` are ordinary draft characters, and a `/` completion list
// renders *below* the bottom rule. A turn in progress leaves the composer in
// place with the spinner above the top rule.
const PI_RULE = '─'
const PI_LIST_MARKER = '→'
const PI_TEXT_START = 0

function rowIsPiRule(snapshot: TerminalCaretSnapshot, row: number): boolean {
  for (let column = 0; column < snapshot.cols; column += 1) {
    if (cellAt(snapshot, row, column)?.chars !== PI_RULE) return false
  }
  return true
}

/**
 * The reachable end of one pi draft row.
 *
 * pi hides the hardware cursor (`?25l`) and paints its own reverse-video caret,
 * which writes a blank cell at the caret. `contentBoundary` counts any written
 * cell, so it would report one column past the real end of the line and leave the
 * steering loop pushing an arrow key pi has nowhere to apply. The reachable end is
 * therefore one past the last *visible* character, widened on the caret's own row
 * to the caret itself — reachable by definition, and what keeps a draft ending in
 * spaces tappable.
 */
function piRowEnd(
  snapshot: TerminalCaretSnapshot,
  row: number,
  current: TerminalCaretPosition,
): number {
  let end = PI_TEXT_START
  for (let column = PI_TEXT_START; column < snapshot.cols; column += 1) {
    const cell = cellAt(snapshot, row, column)
    if (!cellHasVisibleText(cell)) continue
    end = column + Math.max(1, cell?.width ?? 1)
  }
  return row === current.row ? Math.max(end, current.column) : end
}

/**
 * Resolve a click/tap to a reachable cursor position inside pi's composer.
 *
 * pi negotiates no terminal mouse protocol (measured: only `?2004h` bracketed
 * paste and `?25l`), so like Codex and OMP its caret is steered with arrow keys
 * against the observable contract above. The pair of full-width rules is the
 * anchor, and two refusals carry the weight because pi's own pickers (`/model`,
 * `/settings`) reuse exactly that bracketing:
 *
 * - A picker lays a blank row directly under the top rule and its content below
 *   that, so a blank first row means the whole block is blank — an empty draft.
 *   A draft may still hold blank rows further down, because Ctrl+J inserts a
 *   newline, which is why this is not a contiguity test.
 * - A picker marks its selected row with `→` in column 0. `/settings` also parks
 *   the hardware cursor at the bottom-right corner, outside the rules entirely.
 *
 * Both refusals are cheap and both are positive detections of a picker rather
 * than assertions about the composer, because arrow keys sent into a list are a
 * real user-visible mutation instead of a harmlessly missed click.
 */
export function resolvePiCaretTarget(
  snapshot: TerminalCaretSnapshot,
  requested: TerminalCaretPosition,
): ResolvedCaretTarget | null {
  if (snapshot.cols < 4 || snapshot.rows < 3 || snapshot.viewportY !== snapshot.baseY) return null
  const viewportEnd = snapshot.viewportY + snapshot.rows
  const cursorRow = snapshot.baseY + snapshot.cursorY
  if (cursorRow < snapshot.viewportY || cursorRow >= viewportEnd) return null
  const current = { column: clamp(snapshot.cursorX, 0, snapshot.cols), row: cursorRow }

  let topRule = -1
  for (let row = cursorRow - 1; row >= snapshot.viewportY; row -= 1) {
    if (rowIsPiRule(snapshot, row)) { topRule = row; break }
  }
  if (topRule < 0) return null
  let bottomRule = -1
  for (let row = cursorRow + 1; row < viewportEnd; row += 1) {
    if (rowIsPiRule(snapshot, row)) { bottomRule = row; break }
  }
  if (bottomRule < 0) return null

  const firstDraftRow = topRule + 1
  const emptyDraft = rowIsBlank(snapshot, firstDraftRow)
  for (let row = firstDraftRow; row < bottomRule; row += 1) {
    if (cellAt(snapshot, row, 0)?.chars === PI_LIST_MARKER) return null
    if (emptyDraft && row > firstDraftRow && !rowIsBlank(snapshot, row)) return null
  }

  const targetRow = requested.row
  if (targetRow < firstDraftRow || targetRow >= bottomRule) return null
  let targetColumn = clamp(requested.column, PI_TEXT_START, piRowEnd(snapshot, targetRow, current))
  const targetCell = cellAt(snapshot, targetRow, targetColumn)
  if (targetCell?.width === 0) targetColumn = Math.max(PI_TEXT_START, targetColumn - 1)

  return {
    current,
    target: { column: targetColumn, row: targetRow },
    promptRow: topRule,
    textStart: PI_TEXT_START,
  }
}

/**
 * The single declared home for per-harness caret deviations.
 *
 * Tap routing itself carries no harness name: a harness that negotiates mouse
 * tracking is handed the tap and positions its own caret, and a harness that does
 * not appears here or gets no tap behaviour at all. Adding a harness means
 * measuring its composer and adding one entry — never a name check elsewhere.
 */
const CARET_RESOLVERS = new Map<string, CaretTargetResolver>([
  ['codex', resolveCodexCaretTarget],
  ['omp', resolveOmpCaretTarget],
  ['pi', resolvePiCaretTarget],
])

/** The caret resolver a backend steers with, or null when taps must not send keys. */
export function caretResolverForBackend(backend: string): CaretTargetResolver | null {
  return CARET_RESOLVERS.get(backend) ?? null
}

/** Resolve a target that stays attached to the composer when popup height moves it. */
export function resolveAnchoredCaretTarget(
  resolve: CaretTargetResolver,
  snapshot: TerminalCaretSnapshot,
  target: { column: number; rowOffset: number },
): ResolvedCaretTarget | null {
  const cursor={
    column:snapshot.cursorX,
    row:snapshot.baseY+snapshot.cursorY,
  }
  const composer=resolve(snapshot,cursor)
  if(!composer)return null
  const requested={column:target.column,row:composer.promptRow+target.rowOffset}
  const resolved=resolve(snapshot,requested)
  return resolved&&resolved.target.column===requested.column&&resolved.target.row===requested.row?resolved:null
}

/** Map a pointer coordinate to a terminal cursor boundary rather than a character cell. */
export function terminalCaretAtPoint(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  cols: number,
  rows: number,
  viewportY: number,
): TerminalCaretPosition {
  const column = clamp(Math.round(((clientX - rect.left) / Math.max(rect.width, 1)) * cols), 0, cols)
  const viewportRow = clamp(Math.floor(((clientY - rect.top) / Math.max(rect.height, 1)) * rows), 0, rows - 1)
  return { column, row: viewportY + viewportRow }
}

/**
 * Route one settled tap, on the measured mouse mode rather than on a harness name.
 *
 * `mouseTracking` is a runtime fact read off the terminal (`mouseTrackingMode`),
 * not a declaration, and it is the whole question: an application that enabled
 * mouse reporting asked for clicks and positions its own caret from them, exactly
 * as Claude and opencode do. Forwarding sends a click to a TUI that requested
 * clicks, so whatever it does with one is its own UI and the risk is bounded to
 * what Claude has always run. Only touch needs forwarding — a real mouse press
 * reaches xterm's own reporting directly, and the pane only intercepts the
 * compatibility mouse event a touch synthesizes.
 *
 * Everything else is mouse-less, and its caret is steered against the composer
 * contract its resolver encodes. The two branches cannot both fire: the tracking
 * fact separates them.
 */
export function terminalTapAction(decision: TerminalTapDecision): TerminalTapAction {
  if (!decision.still || !decision.primary || decision.modified || decision.readMode || decision.hasSelection) return 'none'
  if (decision.pointerType === 'touch' && decision.mouseTracking) return 'forward-mouse'
  if (caretResolverForBackend(decision.backend) && !decision.mouseTracking) return 'steer-caret'
  return 'none'
}

/** Dispatch the mouse pair xterm expects after a touch pointer gesture. */
export function dispatchTerminalMouseTap(target: HTMLElement, clientX: number, clientY: number): void {
  const view = target.ownerDocument.defaultView
  if (!view) return
  target.dispatchEvent(new view.MouseEvent('mousedown', {
    bubbles: true,
    cancelable: true,
    composed: true,
    view,
    button: 0,
    buttons: 1,
    clientX,
    clientY,
  }))
  target.ownerDocument.dispatchEvent(new view.MouseEvent('mouseup', {
    bubbles: true,
    cancelable: true,
    composed: true,
    view,
    button: 0,
    buttons: 0,
    clientX,
    clientY,
  }))
}

export function terminalCaretDistance(
  current: TerminalCaretPosition,
  target: TerminalCaretPosition,
  cols: number,
): number {
  return (target.row - current.row) * cols + target.column - current.column
}

/**
 * Produce a bounded arrow batch. Direction reversals fall back to one key so an
 * overestimate caused by a short wrapped line converges instead of oscillating.
 */
export function caretSteerCommand(
  current: TerminalCaretPosition,
  target: TerminalCaretPosition,
  cols: number,
  previousDirection: -1 | 1 | null,
): CaretSteerCommand | null {
  const signedDistance = terminalCaretDistance(current, target, cols)
  if (signedDistance === 0) return null
  const direction: -1 | 1 = signedDistance < 0 ? -1 : 1
  const distance = Math.abs(signedDistance)
  const count = previousDirection !== null && previousDirection !== direction
    ? 1
    : Math.min(24, Math.max(1, Math.floor(distance / 2)))
  return { sequence: direction < 0 ? '\x1b[D' : '\x1b[C', count, direction, distance }
}
