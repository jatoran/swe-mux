import assert from 'node:assert/strict'
import test from 'node:test'
import {
  CLIPBOARD_COPIED_EVENT,
  CaptureDedupe,
  configureClipboardCapture,
  installClipboardCapture,
  relativeAge,
  resetCaptureDedupe,
  selectionText,
  shouldCapture,
  sourceLabel,
  type ClipboardCopiedDetail,
} from '../src/clipboardHistory.ts'
import { chooseInsertTarget, type EditorHandle } from '../src/insertTarget.ts'

test('one gesture reported by both capture hooks is recorded once', () => {
  let now = 1_000
  const dedupe = new CaptureDedupe(1500, () => now)
  // The wrapped writeText reports first, then the native copy event reports the
  // same selection: only the first may reach the daemon.
  assert.equal(dedupe.accept('selected text'), true)
  now += 40
  assert.equal(dedupe.accept('selected text'), false)
  // A genuine re-copy after the window is a real event again (it promotes the entry).
  now += 1500
  assert.equal(dedupe.accept('selected text'), true)
  // Different text is never collapsed, however fast it arrives.
  now += 1
  assert.equal(dedupe.accept('other text'), true)
})

test('a burst of identical reports keeps the dedupe window sliding', () => {
  let now = 0
  const dedupe = new CaptureDedupe(1000, () => now)
  assert.equal(dedupe.accept('x'), true)
  for (let step = 0; step < 5; step++) {
    now += 900
    assert.equal(dedupe.accept('x'), false)
  }
})

test('trivia is not captured', () => {
  assert.equal(shouldCapture('ab'), true)
  assert.equal(shouldCapture('a'), false)
  assert.equal(shouldCapture('   \n '), false)
  assert.equal(shouldCapture(''), false)
  assert.equal(shouldCapture(null), false)
  assert.equal(shouldCapture(42), false)
})

test('clipboard feedback follows successful writes and carries no copied text', async () => {
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  const savedWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const savedDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')

  class TestClipboard {
    lastWrite: Promise<void> | null = null

    writeText(text: string): Promise<void> {
      this.lastWrite = text === 'blocked'
        ? Promise.reject(new Error('blocked'))
        : Promise.resolve()
      return this.lastWrite
    }
  }

  const clipboard = new TestClipboard()
  const fakeWindow = new EventTarget() as Window & typeof globalThis
  Object.assign(fakeWindow, { queueMicrotask })
  const fakeDocument = new EventTarget() as Document
  Object.defineProperty(fakeDocument, 'activeElement', { value: null })
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: { clipboard },
  })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: fakeWindow })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: fakeDocument })

  try {
    configureClipboardCapture({ enabled: () => false })
    resetCaptureDedupe()
    const feedback: ClipboardCopiedDetail[] = []
    fakeWindow.addEventListener(CLIPBOARD_COPIED_EVENT, event => {
      feedback.push((event as CustomEvent<ClipboardCopiedDetail>).detail)
    })
    installClipboardCapture()

    const successfulWrite = clipboard.writeText('copied text')
    assert.equal(successfulWrite, clipboard.lastWrite, 'the wrapper must return the native promise')
    await successfulWrite
    assert.deepEqual(feedback, [{ action: 'copy' }])
    assert.equal('text' in feedback[0]!, false)

    await assert.rejects(clipboard.writeText('blocked'), /blocked/)
    assert.deepEqual(feedback, [{ action: 'copy' }])
  } finally {
    if (savedNavigator) Object.defineProperty(globalThis, 'navigator', savedNavigator)
    else Reflect.deleteProperty(globalThis, 'navigator')
    if (savedWindow) Object.defineProperty(globalThis, 'window', savedWindow)
    else Reflect.deleteProperty(globalThis, 'window')
    if (savedDocument) Object.defineProperty(globalThis, 'document', savedDocument)
    else Reflect.deleteProperty(globalThis, 'document')
  }
})

test('native copy events read the focused field range before the window selection', () => {
  const field = {
    tagName: 'TEXTAREA',
    value: 'alpha beta gamma',
    selectionStart: 6,
    selectionEnd: 10,
  } as unknown as Element
  assert.equal(selectionText(field, { toString: () => 'page selection' }), 'beta')
  // A collapsed caret in the field is not a selection: fall through to the page.
  const caret = { tagName: 'INPUT', value: 'abc', selectionStart: 1, selectionEnd: 1 } as unknown as Element
  assert.equal(selectionText(caret, { toString: () => 'page selection' }), 'page selection')
  assert.equal(selectionText(null, { toString: () => 'page selection' }), 'page selection')
  assert.equal(selectionText(null, null), '')
})

test('inserted text goes to the last focused surface, not the focused DOM node', () => {
  const editor: EditorHandle = { insertText: () => {}, isConnected: true }
  assert.deepEqual(chooseInsertTarget({ kind: 'editor', editor, at: 2 }, 'sess-1'), { kind: 'editor', editor })
  assert.deepEqual(chooseInsertTarget({ kind: 'terminal', sessionId: 'sess-2', at: 2 }, 'sess-1'), { kind: 'terminal', sessionId: 'sess-2' })
  // A detached editor (its pane closed while the picker was open) must never
  // swallow the insert; the focused terminal takes it instead.
  const closed: EditorHandle = { insertText: () => assert.fail('detached editor received text'), isConnected: false }
  assert.deepEqual(chooseInsertTarget({ kind: 'editor', editor: closed, at: 9 }, 'sess-1'), { kind: 'terminal', sessionId: 'sess-1' })
  assert.deepEqual(chooseInsertTarget(null, 'sess-1'), { kind: 'terminal', sessionId: 'sess-1' })
  assert.deepEqual(chooseInsertTarget(null, null), { kind: 'none' })
})

test('a terminals-only insert refuses an editor rather than ranking it lower', () => {
  // Prompt templates route this way: a template dropped into whichever note or file
  // was last focused edits that document instead of filling an agent's composer.
  const editor: EditorHandle = { insertText: () => assert.fail('editor received a terminals-only insert'), isConnected: true }
  const focusedEditor = { kind: 'editor', editor, at: 5 } as const
  assert.deepEqual(chooseInsertTarget(focusedEditor, 'sess-1', { terminalsOnly: true }), { kind: 'terminal', sessionId: 'sess-1' })
  // No terminal to fall back on is "nowhere to put this", not "use the editor".
  assert.deepEqual(chooseInsertTarget(focusedEditor, null, { terminalsOnly: true }), { kind: 'none' })
  // The default routing is untouched.
  assert.deepEqual(chooseInsertTarget(focusedEditor, 'sess-1'), { kind: 'editor', editor })
})

test('entry rows label age and provenance compactly', () => {
  assert.equal(relativeAge(1_000, 1_010), 'now')
  assert.equal(relativeAge(1_000, 1_180), '3m')
  assert.equal(relativeAge(0, 7_200), '2h')
  assert.equal(relativeAge(0, 172_800), '2d')
  // Clock skew between devices must not render a negative age.
  assert.equal(relativeAge(2_000, 1_000), 'now')
  assert.equal(sourceLabel('reply'), 'agent reply')
  assert.equal(sourceLabel('terminal'), 'terminal')
  assert.equal(sourceLabel(''), 'copy')
  assert.equal(sourceLabel('something-new'), 'something-new')
})
