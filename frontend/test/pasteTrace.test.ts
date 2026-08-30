import assert from 'node:assert/strict'
import test from 'node:test'
import { BRACKETED_PASTE_END, BRACKETED_PASTE_START } from '../src/composerInsertion.ts'
import {
  composerTraceSnapshot,
  inputIsTraceablePaste,
  summarizePastePayload,
} from '../src/pasteTrace.ts'
import {
  readClaudeComposerRegion,
  type TerminalCaretCell,
  type TerminalCaretSnapshot,
} from '../src/terminalCaretPlacement.ts'

function cell(chars: string): TerminalCaretCell {
  return { chars, code: chars ? chars.codePointAt(0) ?? 0 : 0, width: 1, bgMode: 0, bg: 0, dim: false }
}

/** A snapshot whose rows are literal strings, padded with unwritten cells. */
function snapshotOf(rows: string[], cols: number, cursorX: number, cursorY: number): TerminalCaretSnapshot {
  return {
    cols,
    rows: rows.length,
    viewportY: 0,
    baseY: 0,
    cursorX,
    cursorY,
    lines: rows.map((text, row) => ({
      row,
      cells: Array.from({ length: cols }, (_, column) => cell(column < text.length ? text[column] : '')),
    })),
  }
}

// A minimal live Claude composer: full-width rules, `❯` prompt, one wrapped
// continuation row, cursor after the draft's last character.
const COLS = 20
const CLAUDE_ROWS = [
  'transcript output',
  '',
  '─'.repeat(COLS),
  '❯ hello wor',
  '  ld',
  '─'.repeat(COLS),
  '',
  '',
]

test('a bracketed payload is unwrapped and its hidden codepoints flagged by position', () => {
  const body = 'a b​c'
  const summary = summarizePastePayload(`${BRACKETED_PASTE_START}${body}${BRACKETED_PASTE_END}`)
  assert.equal(summary.bracketed, true)
  assert.equal(summary.chars, 5)
  assert.equal(summary.head, body)
  assert.equal(summary.tail, '')
  assert.deepEqual(summary.flagged, ['1:U+00A0', '3:U+200B'])
  assert.equal(summary.flaggedClipped, false)
  assert.equal(summary.scanClipped, false)
})

test('an unwrapped payload keeps its bytes and reports bracketed false', () => {
  const summary = summarizePastePayload('    plain ascii')
  assert.equal(summary.bracketed, false)
  assert.equal(summary.chars, 15)
  assert.deepEqual(summary.flagged, [])
})

test('head and tail bound a long payload without hiding its ends', () => {
  const body = `${'x'.repeat(60)}…end`
  const summary = summarizePastePayload(body)
  assert.equal(summary.chars, 64)
  assert.equal(summary.head, 'x'.repeat(48))
  assert.equal(summary.tail, body.slice(-48))
  assert.deepEqual(summary.flagged, ['60:U+2026'])
})

test('the flagged list clips at its cap and says so', () => {
  const summary = summarizePastePayload(' '.repeat(70))
  assert.equal(summary.flagged.length, 64)
  assert.equal(summary.flaggedClipped, true)
})

test('the snapshot reads the claude composer region, its rows, and the cursor', () => {
  const snapshot = snapshotOf(CLAUDE_ROWS, COLS, 4, 4)
  const trace = composerTraceSnapshot(snapshot, readClaudeComposerRegion)
  assert.deepEqual(trace.region, { firstRow: 3, lastRow: 4, textStart: 2, textEnd: COLS - 2 })
  assert.deepEqual(trace.rows, ['hello wor', 'ld'])
  assert.equal(trace.rowsClipped, false)
  assert.equal(trace.cursorCol, 4)
  assert.equal(trace.cursorRow, 4)
  assert.equal(trace.onTail, true)
  assert.equal(trace.cursorCell, '')
})

test('a refused surface still snapshots the cursor, with a null region', () => {
  const snapshot = snapshotOf(['just output', 'no composer here'], COLS, 3, 1)
  const trace = composerTraceSnapshot(snapshot, readClaudeComposerRegion)
  assert.equal(trace.region, null)
  assert.deepEqual(trace.rows, [])
  assert.equal(trace.cursorCol, 3)
  assert.equal(trace.cursorRow, 1)
})

test('a paste is recognized by its native event source or by its wrapper alone', () => {
  assert.equal(inputIsTraceablePaste('plain text', 'paste'), true)
  assert.equal(inputIsTraceablePaste(`${BRACKETED_PASTE_START}x${BRACKETED_PASTE_END}`, null), true)
  assert.equal(inputIsTraceablePaste('x', 'keydown'), false)
  assert.equal(inputIsTraceablePaste('x', null), false)
})

test('a write the pane’s own paste path produced says so, whatever the bytes look like', () => {
  // Exact where the heuristic could only guess: a single-line rail paste carries no
  // wrapper and raises no native event, and used to be traced by neither.
  assert.equal(inputIsTraceablePaste('one line', null, { origin: 'rail', kind: 'payload' }), true)
  assert.equal(inputIsTraceablePaste('one line', 'keydown', { origin: 'native', kind: 'payload' }), true)
})

test('the newline keys lifted out ahead of a Codex paste are not a paste of their own', () => {
  // Both writes reach onData with the native capture source still set, and both reports
  // would land 600 ms later microseconds apart - where the durable sink's one-per-second
  // window per phase drops the second. Tracing the leading write therefore does not add a
  // useless report, it replaces the real one.
  assert.equal(inputIsTraceablePaste('\x1b\r', 'paste', { origin: 'native', kind: 'leading' }), false)
  assert.equal(inputIsTraceablePaste('\x1b\r', null, { origin: 'insert', kind: 'leading' }), false)
})
