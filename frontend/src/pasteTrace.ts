// The paste-trace instrument: what a paste contained and what the composer did with it.
//
// Exists for one recurring field report that resists live reproduction: after a paste
// into an agent composer, the caret sits a few characters before the end of the pasted
// text and typing lands inside its tail. By the time anyone investigates, the clipboard,
// the composer, and the session state that conspired are all gone. So every paste into a
// harness whose composer can be read off the screen (`composerRegionForBackend`) records
// one bounded trace — a shape summary of the payload plus the composer and hardware
// cursor before the paste and again after its echo settles — durably enough to adjudicate
// the next occurrence after the fact.
//
// This trace deliberately carries pasted content, bounded: the head/tail excerpt and the
// flagged codepoints ARE the evidence (an invisible U+200B in the payload is the whole
// diagnosis). That is an explicit exception to the input-latency diagnostics'
// no-input-text rule, which is why it persists as its own event type
// (`terminal_paste_trace`) rather than as another `terminal_input_diagnostic` phase.

// Explicit extensions: this module is unit-tested under node's type-stripping runner,
// which does not resolve extensionless specifiers.
import { BRACKETED_PASTE_END, BRACKETED_PASTE_START } from './composerInsertion.ts'
import {
  cellAt,
  type ComposerRegion,
  type ComposerRegionReader,
  type TerminalCaretSnapshot,
} from './terminalCaretPlacement.ts'

export const PASTE_TRACE_PHASE = 'terminal_paste_trace'
/** Echo settle delay before the after-snapshot: covers the browser→daemon→ConPTY→CLI
 *  redraw round trip with margin, while staying inside the durable sink's per-phase
 *  one-second rate window for back-to-back test pastes. */
export const PASTE_TRACE_AFTER_MS = 600

const HEAD_TAIL_CHARS = 48
const FLAGGED_CODEPOINT_LIMIT = 64
const SCAN_CODEPOINT_LIMIT = 20_000
const REGION_ROW_LIMIT = 6
const REGION_ROW_CHARS = 120

export interface PasteTracePayload {
  /** Payload length in codepoints, after unwrapping, without the scan cap. */
  chars: number
  /** Whether the bytes carried xterm's bracketed-paste wrapper. */
  bracketed: boolean
  head: string
  tail: string
  /** `index:U+XXXX` for every codepoint outside printable ASCII, in scan order. */
  flagged: string[]
  flaggedClipped: boolean
  scanClipped: boolean
}

export interface PasteTraceSnapshot {
  /** Hardware cursor, absolute row (`baseY + cursorY`) and column. */
  cursorCol: number
  cursorRow: number
  /** False when the viewport is parked in scrollback — the frame is not the live one. */
  onTail: boolean
  cols: number
  /** The composer rectangle the backend's reader resolved, or null when it refused. */
  region: ComposerRegion | null
  /** Text of the region's last rows, gutter and right inset excluded. */
  rows: string[]
  rowsClipped: boolean
  /** Contents of the cell under the cursor, '' for an unwritten cell. */
  cursorCell: string | null
}

export function summarizePastePayload(data: string): PasteTracePayload {
  let body = data
  let bracketed = false
  if (body.startsWith(BRACKETED_PASTE_START) && body.endsWith(BRACKETED_PASTE_END)) {
    body = body.slice(BRACKETED_PASTE_START.length, body.length - BRACKETED_PASTE_END.length)
    bracketed = true
  }
  const flagged: string[] = []
  let flaggedClipped = false
  let scanClipped = false
  let index = 0
  let chars = 0
  for (const char of body) {
    chars += 1
    if (index >= SCAN_CODEPOINT_LIMIT) {
      scanClipped = true
      continue
    }
    const code = char.codePointAt(0) ?? 0
    if (code < 0x20 || code > 0x7e) {
      if (flagged.length < FLAGGED_CODEPOINT_LIMIT) {
        flagged.push(`${index}:U+${code.toString(16).toUpperCase().padStart(4, '0')}`)
      } else {
        flaggedClipped = true
      }
    }
    index += 1
  }
  return {
    chars,
    bracketed,
    head: body.slice(0, HEAD_TAIL_CHARS),
    tail: body.length > HEAD_TAIL_CHARS ? body.slice(-HEAD_TAIL_CHARS) : '',
    flagged,
    flaggedClipped,
    scanClipped,
  }
}

function regionRowText(snapshot: TerminalCaretSnapshot, row: number, region: ComposerRegion): string {
  let text = ''
  for (let column = region.textStart; column < region.textEnd; column += 1) {
    const cell = cellAt(snapshot, row, column)
    if (cell?.width === 0) continue
    text += cell?.chars || ' '
  }
  return text.replace(/\s+$/, '').slice(0, REGION_ROW_CHARS)
}

export function composerTraceSnapshot(
  snapshot: TerminalCaretSnapshot,
  readRegion: ComposerRegionReader,
): PasteTraceSnapshot {
  const region = readRegion(snapshot)
  const rows: string[] = []
  let rowsClipped = false
  if (region) {
    const firstReported = Math.max(region.firstRow, region.lastRow - REGION_ROW_LIMIT + 1)
    rowsClipped = firstReported > region.firstRow
    for (let row = firstReported; row <= region.lastRow; row += 1) {
      rows.push(regionRowText(snapshot, row, region))
    }
  }
  const cursorRow = snapshot.baseY + snapshot.cursorY
  return {
    cursorCol: snapshot.cursorX,
    cursorRow,
    onTail: snapshot.viewportY === snapshot.baseY,
    cols: snapshot.cols,
    region,
    rows,
    rowsClipped,
    cursorCell: cellAt(snapshot, cursorRow, snapshot.cursorX)?.chars ?? null,
  }
}

/** Whether this input burst is a paste worth tracing: either the pane saw the native
 *  paste event, or the bytes themselves carry the bracketed-paste wrapper (which xterm
 *  produces only for a paste, so button- and voice-driven pastes are caught too). */
export function inputIsTraceablePaste(data: string, captureSource: string | null): boolean {
  return captureSource === 'paste' || data.startsWith(BRACKETED_PASTE_START)
}
