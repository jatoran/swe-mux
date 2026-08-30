import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import type { ClipboardSelectionType } from '@xterm/addon-clipboard'
import { MAX_TERMINAL_CLIPBOARD_CHARS, ResilientClipboardProvider, claimTerminalTextPaste, clipboardImage, copyPreparedText, hasTerminalImage, isTerminalImage, pasteNeedsManualBracketing, strayPasteBelongsToPane, type StrayPasteContext } from '../src/terminalClipboard.ts'

// xterm declares `ClipboardSelectionType` as an ambient `const enum`, which
// `isolatedModules` forbids reading at runtime, so the member cannot be named here.
// `SYSTEM` is the literal 'c' these tests already passed; the cast keeps the value and
// gives the calls the parameter type the provider actually declares.
const SYSTEM = 'c' as ClipboardSelectionType

test('ordinary paste events prefer an image file when one is present', () => {
  const image = new Blob(['png'], { type: 'image/png' })
  assert.equal(clipboardImage([
    { kind: 'string', type: 'text/plain', getAsFile: () => null },
    { kind: 'file', type: 'image/png', getAsFile: () => image },
  ]), image)
  assert.equal(clipboardImage([{ kind: 'string', type: 'text/plain', getAsFile: () => null }]), null)
  assert.equal(clipboardImage([{ kind: 'file', type: 'image/svg+xml', getAsFile: () => new Blob(['svg'], { type: 'image/svg+xml' }) }]), null)
  assert.equal(hasTerminalImage([{ kind: 'file', type: 'image/jpeg' }]), true)
  assert.equal(hasTerminalImage([{ kind: 'file', type: 'image/svg+xml' }]), false)
  assert.equal(isTerminalImage(new Blob(['gif'], { type: 'image/gif' })), true)
})

test('OSC 52 clipboard failures retain text for a user-gesture retry', async () => {
  let pending = ''
  const provider = new ResilientClipboardProvider(
    text => { pending = text },
    () => assert.fail('ordinary clipboard text should not be rejected'),
    { readText: async () => '', writeText: async () => { throw new Error('blocked') } },
  )
  await provider.writeText(SYSTEM, 'prepared by Claude')
  assert.equal(pending, 'prepared by Claude')
})

test('OSC 52 clipboard payloads are bounded', async () => {
  let rejected = ''
  const provider = new ResilientClipboardProvider(
    () => assert.fail('oversized text must not become pending'),
    message => { rejected = message },
    { readText: async () => '', writeText: async () => undefined },
  )
  await provider.writeText(SYSTEM, 'x'.repeat(MAX_TERMINAL_CLIPBOARD_CHARS + 1))
  assert.match(rejected, /safety limit/)
})

test('replayed OSC 52 writes are dropped instead of overwriting the clipboard', async () => {
  let suppressed = true
  const writes: string[] = []
  const provider = new ResilientClipboardProvider(
    () => assert.fail('suppressed writes must not become pending'),
    () => assert.fail('suppressed writes must not be rejected'),
    { readText: async () => '', writeText: async text => { writes.push(text) } },
    () => suppressed,
  )
  // While replaying (or the tab is hidden) the predicate reports true and the
  // stale scrollback payload never reaches the system clipboard.
  await provider.writeText(SYSTEM, 'stale replay payload')
  assert.deepEqual(writes, [])
  // A live copy once the predicate clears still writes through.
  suppressed = false
  await provider.writeText(SYSTEM, 'live copy')
  assert.deepEqual(writes, ['live copy'])
})

test('manual copy fallback runs synchronously while mobile activation is live', async () => {
  const calls:string[] = []
  const textarea = {
    focus: () => calls.push('focus'),
    select: () => calls.push('select'),
    setSelectionRange: (start:number,end:number) => calls.push(`range:${start}:${end}`),
  } as unknown as HTMLTextAreaElement
  const copied = await copyPreparedText(
    'mobile text',
    textarea,
    { writeText: async () => { calls.push('modern'); throw new Error('blocked') } },
    () => { calls.push('legacy'); return true },
  )
  assert.equal(copied, true)
  assert.deepEqual(calls.slice(0,5), ['modern', 'focus', 'select', 'range:0:11', 'legacy'])
})

test('a multi-line paste into an agent is bracketed by hand', () => {
  // The regression: unwrapped, xterm turns each newline into Enter and the CLI submits
  // the paste line by line, leaving only the text after the last newline.
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'line one\nline two\nline three',
      agentBackend: true,
    }),
    true,
  )
})

test('xterm’s belief about the child’s mode is not part of the decision', () => {
  // It used to be, in one direction only: the wrapper was applied when the mirror said
  // "off". A mirror stale the other way - set by a child that has since been replaced, or
  // by a CLI that has left its TUI - made xterm wrap bytes the child then ignored, and the
  // carriage returns submitted the paste a line at a time. Same symptom, opposite cause,
  // and no repair. The mode is now not an input at all, which is what the arity asserts.
  assert.equal(pasteNeedsManualBracketing.length, 1)
  assert.equal(
    pasteNeedsManualBracketing({ text: 'line one\nline two', agentBackend: true }),
    true,
  )
})

test('a plain shell is never sent wrapper bytes it would print literally', () => {
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'line one\nline two',
      agentBackend: false,
    }),
    false,
  )
})

test('single-line text is left alone, since the bug needs a newline', () => {
  // Also what keeps a short paste from becoming a `[Pasted text]` placeholder in Codex.
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'just one line',
      agentBackend: true,
    }),
    false,
  )
})

test('a bare carriage return counts as multi-line', () => {
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'first\rsecond',
      agentBackend: true,
    }),
    true,
  )
})

test('native text paste is claimed before xterm and handed to the pane paste path', () => {
  const calls: string[] = []
  const event = {
    clipboardData: { getData: (type: string) => type === 'text/plain' ? 'first\nsecond' : '' },
    preventDefault: () => calls.push('prevent'),
    stopPropagation: () => calls.push('stop'),
  }

  assert.equal(claimTerminalTextPaste(event as unknown as ClipboardEvent, text => calls.push(text)), true)
  assert.deepEqual(calls, ['prevent', 'stop', 'first\nsecond'])
})

const strayPaste = (overrides: Partial<StrayPasteContext> = {}): StrayPasteContext => ({
  paneHidden: false,
  targetInTerminalHost: false,
  inDialog: false,
  focusHeldByOtherField: false,
  focusedTerminalSessionId: 'session-a',
  sessionId: 'session-a',
  ...overrides,
})

test('Ctrl+V on a rail button reaches the terminal the operator was last typing into', () => {
  // The gap this closes: the pane's own listener is on its host, so a paste dispatched to a
  // focusable-but-not-editable element is heard by nobody and lands nowhere at all.
  assert.equal(strayPasteBelongsToPane(strayPaste()), true)
})

test('a stray paste is adopted by exactly one pane, whatever else is on screen', () => {
  // Every mounted pane runs this, so the rule has to be single-valued rather than merely
  // plausible: routing on visibility would have a split answer yes twice and paste twice.
  assert.equal(strayPasteBelongsToPane(strayPaste({ sessionId: 'session-b' })), false)
  assert.equal(strayPasteBelongsToPane(strayPaste({ focusedTerminalSessionId: null })), false)
  assert.equal(strayPasteBelongsToPane(strayPaste({ paneHidden: true })), false)
})

test('anything that will legitimately receive the paste itself keeps it', () => {
  // A terminal host owns its own event - and the document listener runs first, so adopting
  // one would paste it twice. A text field and a dialog are the operator pasting elsewhere.
  assert.equal(strayPasteBelongsToPane(strayPaste({ targetInTerminalHost: true })), false)
  assert.equal(strayPasteBelongsToPane(strayPaste({ focusHeldByOtherField: true })), false)
  assert.equal(strayPasteBelongsToPane(strayPaste({ inDialog: true })), false)
})

test('an empty native paste remains available to non-text handlers', () => {
  const calls: string[] = []
  const event = {
    clipboardData: { getData: () => '' },
    preventDefault: () => calls.push('prevent'),
    stopPropagation: () => calls.push('stop'),
  }

  assert.equal(claimTerminalTextPaste(event as unknown as ClipboardEvent, text => calls.push(text)), false)
  assert.deepEqual(calls, [])
})

test('every terminal message is drawn over the terminal, never inside the rail', () => {
  const root = dirname(fileURLToPath(import.meta.url))
  const pane = readFileSync(join(root, '..', 'src', 'TerminalPane.tsx'), 'utf8')
  const styles = readFileSync(join(root, '..', 'src', 'style.css'), 'utf8')

  // One toast, and nothing left in the rail to compete with it. The selection readout was
  // the last message living there, in a trailing cluster that does not shrink and under a
  // `34vw` cap, so a split pane narrower than that lost its chips behind the readout.
  assert.match(pane, /\{clipboardStatus&&<div class="terminal-clip-toast" role="status">\{clipboardStatus\}<\/div>\}/)
  assert.doesNotMatch(pane, /<RailStrip[^\n]*status=/)
  assert.match(pane, /showClipboardStatus\(`\$\{text\.length\.toLocaleString\(\)\} selected/)
  // A readout goes when its selection goes; a confirmation is about something that already
  // happened and runs out its own timer instead.
  assert.match(pane, /const clearSelectionStatus = \(\) => \{\n\s*if\(clipboardStatusKindRef\.current!=='selection'\)return/)

  const rule = declarations(styles, /\.terminal-clip-toast\{([^}]*)\}/)
  // Sharing the terminal's own grid cell is what keeps it from displacing anything: it
  // stacks over the pane's bottom-right corner, on the rail's top edge.
  assert.match(rule, /grid-row:1/)
  assert.match(rule, /grid-column:1/)
  assert.match(rule, /align-self:end/)
  assert.match(rule, /justify-self:end/)
  assert.match(rule, /margin:0 9px 0 0/)
  // Capped against the *pane*, not the window. A `vw` cap is a claim about the viewport, and
  // a pane in a split is a fraction of it - which is exactly how the readout came to bury a
  // narrow rail before it moved here.
  assert.match(rule, /max-width:min\(60%,340px\)/)
  // It covers the jump-to-latest and peek chips rather than moving them, so it has to draw
  // above both and must never take a tap meant for one.
  assert.match(rule, /pointer-events:none/)
  const chip = declarations(styles, /\.terminal-jump-latest\{([^}]*)\}/)
  const zIndex = (declaration: string) => Number(/z-index:(\d+)/.exec(declaration)?.[1] ?? NaN)
  assert.ok(zIndex(rule) > zIndex(chip), 'the confirmation must draw over the chips it overlaps')
})

/** One CSS rule's declarations, or a failure naming the selector that went missing. */
function declarations(styles: string, pattern: RegExp): string {
  const rule = pattern.exec(styles)
  if (!rule) throw new Error(`style.css no longer has a rule matching ${pattern}`)
  return rule[1]
}
