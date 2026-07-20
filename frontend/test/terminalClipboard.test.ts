import assert from 'node:assert/strict'
import test from 'node:test'
import { MAX_TERMINAL_CLIPBOARD_CHARS, ResilientClipboardProvider, clipboardImage, copyPreparedText, hasTerminalImage, isTerminalImage } from '../src/terminalClipboard.ts'

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
