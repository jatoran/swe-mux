// The cell grid of a terminal's visible screen, in the shape every composer
// reader and caret resolver takes (`terminalCaretPlacement.ts`).
//
// Its own module so the one translation from xterm's cell API is unit-testable
// without a browser, because that translation is where the composer readers broke
// for every harness at once: xterm's `isDim()` returns the attribute's *bit*
// (0x8000000), never 1, so `isDim() === 1` was false for every cell, no
// placeholder hint was ever discarded, and an empty composer read back as the
// placeholder's text - "Try "create a util logging.py that..."" on Claude Code,
// "Ask Codex to do anything" on Codex (measured 2026-10-18 against v2.1.302 and
// v0.163.0 through @xterm/headless 6.0.0). Every attribute getter here is read
// as a truthy flag for that reason.

import type { TerminalCaretCell, TerminalCaretSnapshot } from './terminalCaretPlacement.ts'

/** The part of xterm's `IBufferCell` this reads. */
export interface SnapshotCell {
  getChars(): string
  getCode(): number
  getWidth(): number
  getBgColorMode(): number
  getBgColor(): number
  isDim(): number
}

/** The part of xterm's `Terminal` this reads. */
export interface SnapshotTerminal {
  cols: number
  rows: number
  buffer: {
    active: {
      viewportY: number
      baseY: number
      cursorX: number
      cursorY: number
      getLine(row: number): { getCell(column: number): SnapshotCell | undefined } | undefined
    }
  }
}

function snapshotCell(cell: SnapshotCell | undefined): TerminalCaretCell {
  return {
    chars: cell?.getChars() ?? '',
    code: cell?.getCode() ?? 0,
    width: cell?.getWidth() ?? 1,
    bgMode: cell?.getBgColorMode() ?? 0,
    bg: cell?.getBgColor() ?? 0,
    // A bit flag, not a boolean: see the module comment.
    dim: !!cell?.isDim(),
  }
}

/** The visible screen, row by row, with the cursor and scroll position. */
export function terminalCaretSnapshot(term: SnapshotTerminal): TerminalCaretSnapshot {
  const buffer = term.buffer.active
  const lines = []
  for (let row = buffer.viewportY; row < buffer.viewportY + term.rows; row += 1) {
    const line = buffer.getLine(row)
    const cells: TerminalCaretCell[] = []
    for (let column = 0; column < term.cols; column += 1) cells.push(snapshotCell(line?.getCell(column)))
    lines.push({ row, cells })
  }
  return {
    cols: term.cols,
    rows: term.rows,
    viewportY: buffer.viewportY,
    baseY: buffer.baseY,
    cursorX: buffer.cursorX,
    cursorY: buffer.cursorY,
    lines,
  }
}
