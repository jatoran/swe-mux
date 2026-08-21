import assert from 'node:assert/strict'
import test from 'node:test'
import {
  composerClearSequence, composerIsReadable, joinComposerLines, readComposerText,
} from '../src/composerText.ts'
import {
  readClaudeComposerRegion,
  type TerminalCaretCell, type TerminalCaretSnapshot,
} from '../src/terminalCaretPlacement.ts'

const COLS = 24
const ROWS = 10

function blank(): TerminalCaretCell {
  return { chars: '', code: 0, width: 1, bgMode: 0, bg: 0, dim: false }
}

function grid(cursorX = 2, cursorY = 5): TerminalCaretSnapshot {
  return {
    cols: COLS,
    rows: ROWS,
    viewportY: 0,
    baseY: 0,
    cursorX,
    cursorY,
    lines: Array.from({ length: ROWS }, (_, row) => ({
      row,
      cells: Array.from({ length: COLS }, blank),
    })),
  }
}

function write(state: TerminalCaretSnapshot, row: number, column: number, text: string, dim = false): void {
  for (const char of text) {
    state.lines[row].cells[column] = { chars: char, code: char.codePointAt(0) ?? 0, width: 1, bgMode: 0, bg: 0, dim }
    column += 1
  }
}

function rule(state: TerminalCaretSnapshot, row: number): void {
  write(state, row, 0, '─'.repeat(COLS))
}

/**
 * Claude's composer as measured: two full-width rules, `❯` plus U+00A0 in the
 * first row's gutter, text from column 2, and two unwritten columns on the right.
 */
function claude(lines: string[], { cursorRow = 5 }: { cursorRow?: number } = {}): TerminalCaretSnapshot {
  const top = 4
  const state = grid(2, cursorRow)
  rule(state, top)
  lines.forEach((line, index) => {
    if (index === 0) write(state, top + 1, 0, '❯ ')
    write(state, top + 1 + index, 2, line)
  })
  rule(state, top + 1 + lines.length)
  return state
}

test('a Claude composer reads back as the text between its rules', () => {
  assert.equal(readComposerText('claude', claude(['fix the tests'])), 'fix the tests')
  assert.equal(readComposerText('claude', claude(['first', 'second', 'third'])), 'first\nsecond\nthird')
})

test('an empty Claude composer is an empty string, not an unreadable screen', () => {
  // The hint is painted with SGR 2, which is the whole reason the reader can tell
  // a placeholder from a draft without knowing a word of its wording.
  const state = claude([''])
  write(state, 5, 2, 'Try "write a test"', true)
  assert.equal(readComposerText('claude', state), '')
})

test('a row reaching the wrap column is joined to the next rather than newlined', () => {
  // Claude leaves the last two columns unwritten, so at 24 columns a wrapped row
  // fills columns 2..21 - twenty characters.
  const wrapped = 'abcdefghijklmnopqrst'
  assert.equal(wrapped.length, COLS - 4)
  assert.equal(readComposerText('claude', claude([wrapped, 'uvw'])), `${wrapped}uvw`)
  // One character short of the wrap column is an ordinary line ending.
  const short = wrapped.slice(0, -1)
  assert.equal(readComposerText('claude', claude([short, 'uvw'])), `${short}\nuvw`)
})

test('the blank rows a composer pads itself out with are not trailing newlines', () => {
  assert.equal(readComposerText('claude', claude(['only', '', ''])), 'only')
  // A blank row *between* text is a newline the operator typed, and survives.
  assert.equal(readComposerText('claude', claude(['top', '', 'bottom'])), 'top\n\nbottom')
})

test('a Claude screen without the prompt glyph is refused rather than guessed at', () => {
  // Claude brackets other surfaces in full-width rules too. Reading one of those
  // back as "your draft" is worse than reading nothing, so the glyph is required.
  const state = claude(['drafted'])
  state.lines[5].cells[0] = blank()
  assert.equal(readClaudeComposerRegion(state), null)
  assert.equal(readComposerText('claude', state), null)
})

test('a continuation row with something in its gutter is not a composer', () => {
  const state = claude(['one', 'two'])
  write(state, 6, 0, '>')
  assert.equal(readComposerText('claude', state), null)
})

test('a reader parked in scrollback is refused rather than shown an old frame', () => {
  const state = claude(['drafted'])
  state.baseY = 3
  assert.equal(readComposerText('claude', state), null)
})

test('a screen with no rule under the cursor is refused', () => {
  const state = claude(['one'])
  // Erase the bottom rule: an unbounded region has no measurable extent.
  state.lines[6].cells = Array.from({ length: COLS }, blank)
  assert.equal(readComposerText('claude', state), null)
})

test('an unmeasured harness reads nothing at all, and says so as null', () => {
  assert.equal(readComposerText('shell', claude(['one'])), null)
  assert.equal(readComposerText('opencode', claude(['one'])), null)
  assert.equal(composerIsReadable('claude'), true)
  assert.equal(composerIsReadable('codex'), true)
  assert.equal(composerIsReadable('omp'), true)
  assert.equal(composerIsReadable('pi'), true)
  assert.equal(composerIsReadable('shell'), false)
  assert.equal(composerIsReadable('opencode'), false)
  // The registry is a Map, so an inherited property name is not a backend.
  assert.equal(composerIsReadable('__proto__'), false)
  assert.equal(composerIsReadable('constructor'), false)
})

test('the measured 100-column Claude screen reads back exactly what was typed', () => {
  // Reproduced from a real capture (Claude Code v2.1.238, 100x30, 2026-08-20):
  // three lines whose last one is long enough to wrap. On that screen the wrapped
  // row's content ended at column 97 and its continuation was 70 characters long,
  // which is what pins textStart=2 and textEnd=cols-2 rather than any other pair -
  // 96 columns of text per row, and 166 - 96 = 70 left over.
  const cols = 100
  const rows = 30
  const draft = ['hello composer probe', 'second line here', `third ${'wrap'.repeat(40)}`]
  const state: TerminalCaretSnapshot = {
    cols, rows, viewportY: 0, baseY: 0, cursorX: 72, cursorY: 24,
    lines: Array.from({ length: rows }, (_, row) => ({ row, cells: Array.from({ length: cols }, blank) })),
  }
  write(state, 20, 0, '─'.repeat(cols))
  write(state, 25, 0, '─'.repeat(cols))
  write(state, 21, 0, '❯ ')
  const flat = draft.join('\n')
  let row = 21
  let column = 2
  for (const char of flat) {
    if (char === '\n') { row += 1; column = 2; continue }
    if (column >= cols - 2) { row += 1; column = 2 }
    write(state, row, column, char)
    column += 1
  }
  assert.equal(row, 24, 'the long line must have wrapped onto a fourth row')
  assert.equal(column, 72, 'the continuation row must end where the capture did')
  assert.equal(readComposerText('claude', state), flat)
})

test('joining is driven by the wrapped flag, not by line length', () => {
  assert.equal(joinComposerLines([]), '')
  assert.equal(joinComposerLines([{ text: 'a', wrapped: true }, { text: 'b', wrapped: false }]), 'ab')
  assert.equal(joinComposerLines([{ text: 'a', wrapped: false }, { text: 'b', wrapped: false }]), 'a\nb')
})

test('the whole-composer clear sequence is per harness', () => {
  // Measured against Claude Code v2.1.238: Ctrl+U kills one line of a four-line
  // draft and a bare Esc does nothing, so only a double Esc actually clears.
  assert.equal(composerClearSequence('claude'), '\x1b\x1b')
  assert.equal(composerClearSequence('codex'), '\x15')
  assert.equal(composerClearSequence('omp'), '\x15')
  assert.equal(composerClearSequence('shell'), '\x15')
  assert.equal(composerClearSequence('__proto__'), '\x15')
})
