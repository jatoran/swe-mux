import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { MAX_TERMINAL_CLIPBOARD_CHARS, ResilientClipboardProvider, claimTerminalTextPaste, clipboardImage, copyPreparedText, hasTerminalImage, isTerminalImage, pasteNeedsManualBracketing } from '../src/terminalClipboard.ts'

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
  await provider.writeText('c', 'prepared by Claude')
  assert.equal(pending, 'prepared by Claude')
})

test('OSC 52 clipboard payloads are bounded', async () => {
  let rejected = ''
  const provider = new ResilientClipboardProvider(
    () => assert.fail('oversized text must not become pending'),
    message => { rejected = message },
    { readText: async () => '', writeText: async () => undefined },
  )
  await provider.writeText('c', 'x'.repeat(MAX_TERMINAL_CLIPBOARD_CHARS + 1))
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
  await provider.writeText('c', 'stale replay payload')
  assert.deepEqual(writes, [])
  // A live copy once the predicate clears still writes through.
  suppressed = false
  await provider.writeText('c', 'live copy')
  assert.deepEqual(writes, ['live copy'])
})

test('manual copy fallback runs synchronously while mobile activation is live', async () => {
  const calls:string[] = []
  const textarea = {
    focus: () => calls.push('focus'),
    select: () => calls.push('select'),
    setSelectionRange: (start:number,end:number) => calls.push(`range:${start}:${end}`),
  } as HTMLTextAreaElement
  const copied = await copyPreparedText(
    'mobile text',
    textarea,
    { writeText: async () => { calls.push('modern'); throw new Error('blocked') } },
    () => { calls.push('legacy'); return true },
  )
  assert.equal(copied, true)
  assert.deepEqual(calls.slice(0,5), ['modern', 'focus', 'select', 'range:0:11', 'legacy'])
})

test('a multi-line paste into an agent with a stale mode is bracketed by hand', () => {
  // The regression: unwrapped, xterm turns each newline into Enter and the CLI submits
  // the paste line by line, leaving only the text after the last newline.
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'line one\nline two\nline three',
      agentBackend: true,
      bracketedPasteMode: false,
    }),
    true,
  )
})

test('xterm is trusted to wrap once it knows the mode is on', () => {
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'line one\nline two',
      agentBackend: true,
      bracketedPasteMode: true,
    }),
    false,
  )
})

test('a plain shell is never sent wrapper bytes it would print literally', () => {
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'line one\nline two',
      agentBackend: false,
      bracketedPasteMode: false,
    }),
    false,
  )
})

test('single-line text is left alone, since the bug needs a newline', () => {
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'just one line',
      agentBackend: true,
      bracketedPasteMode: false,
    }),
    false,
  )
})

test('a bare carriage return counts as multi-line', () => {
  assert.equal(
    pasteNeedsManualBracketing({
      text: 'first\rsecond',
      agentBackend: true,
      bracketedPasteMode: false,
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

test('a copy or paste confirmation is drawn over the terminal, never inside the rail', () => {
  const root = dirname(fileURLToPath(import.meta.url))
  const pane = readFileSync(join(root, '..', 'src', 'TerminalPane.tsx'), 'utf8')
  const styles = readFileSync(join(root, '..', 'src', 'style.css'), 'utf8')

  // The rail row keeps the selection readout, which is a state, and no longer carries the
  // momentary one, which narrowed the scrolling strip for as long as it was up.
  assert.match(pane, /<div class="terminal-clip-toast" role="status">\{clipboardStatus\|\|null\}<\/div>/)
  const railStatus = /status=\{index===renderedRailRows\.length-1\?([^}]*)\}/.exec(pane)?.[1] ?? ''
  assert.ok(railStatus, 'the rail row no longer takes a status prop')
  assert.doesNotMatch(railStatus, /clipboardStatus/)
  assert.match(railStatus, /selectionText/)

  const rule = declarations(styles, /\.terminal-clip-toast\{([^}]*)\}/)
  // Sharing the terminal's own grid cell is what keeps it from displacing anything: it
  // stacks over the pane's bottom-right corner, on the rail's top edge.
  assert.match(rule, /grid-row:1/)
  assert.match(rule, /grid-column:1/)
  assert.match(rule, /align-self:end/)
  assert.match(rule, /justify-self:end/)
  assert.match(rule, /margin:0 9px 0 0/)
  // It covers the jump-to-latest and peek chips rather than moving them, so it has to draw
  // above both and must never take a tap meant for one.
  assert.match(rule, /pointer-events:none/)
  const chip = declarations(styles, /\.terminal-jump-latest\{([^}]*)\}/)
  const zIndex = (declaration: string) => Number(/z-index:(\d+)/.exec(declaration)?.[1] ?? NaN)
  assert.ok(zIndex(rule) > zIndex(chip), 'the confirmation must draw over the chips it overlaps')
  // Emptied rather than unmounted, so the live region is in the accessibility tree before
  // the text it has to announce arrives.
  assert.match(styles, /\.terminal-clip-toast:empty\{display:none\}/)
})

/** One CSS rule's declarations, or a failure naming the selector that went missing. */
function declarations(styles: string, pattern: RegExp): string {
  const rule = pattern.exec(styles)
  if (!rule) throw new Error(`style.css no longer has a rule matching ${pattern}`)
  return rule[1]
}
