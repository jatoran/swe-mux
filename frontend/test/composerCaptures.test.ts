// The composer readers against what real CLIs actually drew.
//
// Every other composer test builds its snapshot by hand, which is how a defect in
// the one function that builds snapshots from xterm went unseen: `isDim()` returns
// the attribute's bit, never 1, so `isDim() === 1` was false for every cell and an
// empty composer read back as its own placeholder hint on every harness. These
// fixtures are raw output captured 2026-10-18 from Claude Code v2.1.302 and Codex
// v0.163.0 under a real pseudoterminal (scrubbed: the capturing user's name, and the
// titles in Claude's agents view, which lists the machine's real sessions). Each is
// parsed by the app's own xterm, snapshotted by the production builder, and read by
// the production reader - the whole chain the daemon's delivery gate now trusts.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join } from 'node:path'
import test from 'node:test'
import { readComposerDraft, readComposerState, type ComposerScreenState } from '../src/composerText.ts'
import { terminalCaretSnapshot } from '../src/terminalCellSnapshot.ts'

interface Capture {
  harness: string
  version: string
  cols: number
  rows: number
  stream: string
  marks: Record<string, number>
}

interface XtermTerminal {
  cols: number
  rows: number
  buffer: Parameters<typeof terminalCaretSnapshot>[0]['buffer']
  write(data: Uint8Array, callback?: () => void): void
  dispose(): void
}

const require = createRequire(import.meta.url)
const { Terminal } = require('@xterm/xterm') as {
  Terminal: new (options: Record<string, unknown>) => XtermTerminal
}

function capture(name: string): Capture {
  return JSON.parse(readFileSync(join(import.meta.dirname, 'fixtures', 'composer', name), 'utf8'))
}

/** The screen a pane would show once it had parsed the stream up to `mark`. */
async function screenAt(fixture: Capture, mark: string) {
  const stream = Buffer.from(fixture.stream, 'base64')
  const end = fixture.marks[mark]
  assert.ok(end !== undefined, `the capture has no mark ${mark}`)
  const term = new Terminal({ cols: fixture.cols, rows: fixture.rows, allowProposedApi: true, scrollback: 1000 })
  await new Promise<void>(resolve => term.write(new Uint8Array(stream.subarray(0, end)), resolve))
  const snapshot = terminalCaretSnapshot(term)
  term.dispose()
  return snapshot
}

async function expectReading(
  fixture: Capture, mark: string, state: ComposerScreenState, chars = 0,
): Promise<void> {
  const reading = readComposerState(fixture.harness, await screenAt(fixture, mark))
  assert.deepEqual(reading, { state, chars }, `${fixture.harness} at ${mark}`)
}

const claude = capture('claude-2.1.302.json')
const codex = capture('codex-0.163.0.json')

test('claude: a composer showing only its dim hint reads as empty', async () => {
  await expectReading(claude, 'ready', 'empty')
  // The hint is still there for anyone to copy; the reader must not hand it out.
  assert.equal(readComposerDraft('claude', await screenAt(claude, 'ready')), '')
})

test('claude: typing, then deleting it all, reads as a draft and then as empty', async () => {
  await expectReading(claude, 'typed', 'draft', 'hello world'.length)
  await expectReading(claude, 'deleted', 'empty')
  // Backspace held past empty draws nothing at all, and the screen still says empty.
  assert.equal(claude.marks.extra_backspace, claude.marks.deleted)
  await expectReading(claude, 'extra_backspace', 'empty')
})

test('claude: the agents view is not this session’s composer', async () => {
  // `←` on the empty composer opened it; it draws its own composer, for a new
  // session, in the same frame. Reading it would mark this row with text that
  // is not in this session.
  await expectReading(claude, 'movement_on_empty', 'unreadable')
  await expectReading(claude, 'pasted', 'unreadable')
})

test('claude: after a full repaint the hint is undimmed, and reads as a placeholder', async () => {
  // Every frame after leaving the agents view draws `Try "…"` with no SGR at all.
  await expectReading(claude, 'double_esc', 'placeholder')
  await expectReading(claude, 'ctrl_u', 'placeholder')
  await expectReading(claude, 'idle1_end', 'placeholder')
  // Copy treats it as the empty composer it is.
  assert.equal(readComposerDraft('claude', await screenAt(claude, 'idle1_end')), '')
  // And typed text in that same undimmed rendering is a draft, not a placeholder.
  await expectReading(claude, 'up_arrow', 'draft', 1)
})

test('dim is read as the bit flag xterm returns, never as 1', () => {
  const cell = (dim: number) => ({
    getChars: () => 'x', getCode: () => 120, getWidth: () => 1,
    getBgColorMode: () => 0, getBgColor: () => 0, isDim: () => dim,
  })
  const term = {
    cols: 2, rows: 1,
    buffer: { active: {
      viewportY: 0, baseY: 0, cursorX: 0, cursorY: 0,
      getLine: () => ({ getCell: (column: number) => cell(column === 0 ? 0x8000000 : 0) }),
    } },
  }
  const snapshot = terminalCaretSnapshot(term)
  assert.equal(snapshot.lines[0].cells[0].dim, true)
  assert.equal(snapshot.lines[0].cells[1].dim, false)
})

test('an empty-looking claude composer with the caret moved on is holding something', async () => {
  // Claude does not draw trailing spaces, so a composer of only spaces looks empty;
  // the caret, which it parks at column 2 when the composer really is empty, is what
  // tells them apart.
  const snapshot = await screenAt(claude, 'deleted')
  assert.equal(readComposerState('claude', snapshot).state, 'empty')
  const moved = { ...snapshot, cursorX: snapshot.cursorX + 3 }
  assert.deepEqual(readComposerState('claude', moved), { state: 'draft', chars: 3 })
})

test('codex: its dim hint reads as empty, and a draft as a draft', async () => {
  await expectReading(codex, 'startup', 'empty')
  await expectReading(codex, 'typed', 'draft', 'hello world'.length)
  await expectReading(codex, 'deleted', 'empty')
  await expectReading(codex, 'extra_backspace', 'empty')
  const pasted = readComposerState('codex', await screenAt(codex, 'pasted'))
  assert.equal(pasted.state, 'draft')
  assert.ok(pasted.chars > 100)
})
