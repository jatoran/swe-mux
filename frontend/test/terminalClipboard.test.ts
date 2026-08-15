import assert from 'node:assert/strict'
import test from 'node:test'
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
