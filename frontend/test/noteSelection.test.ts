import assert from 'node:assert/strict'
import test from 'node:test'
import {
  BRACKETED_PASTE_END,
  BRACKETED_PASTE_START,
  comparePositions,
  composeAgentMessage,
  hasSelection,
  pastePayload,
  selectionText,
  utf16IndexForByte,
  type EditorSelection,
  type EditorSnapshot,
} from '../src/noteSelection.ts'

const caret = (
  anchorLine: number,
  anchorByte: number,
  headLine: number,
  headByte: number,
  kind: EditorSelection['kind'] = 'caret',
): EditorSelection => ({
  anchor: { line: anchorLine, byteInLine: anchorByte },
  head: { line: headLine, byteInLine: headByte },
  kind,
})

const snapshot = (text: string, selections: EditorSelection[]): EditorSnapshot => ({ text, selections })

test('byte offsets convert to UTF-16 indices across multi-byte characters', () => {
  assert.equal(utf16IndexForByte('hello', 0), 0)
  assert.equal(utf16IndexForByte('hello', 3), 3)
  // é is two UTF-8 bytes but one UTF-16 unit.
  assert.equal(utf16IndexForByte('café latte', 5), 4)
  // 🙂 is four UTF-8 bytes and a surrogate pair.
  assert.equal(utf16IndexForByte('a🙂b', 5), 3)
  assert.equal(utf16IndexForByte('a🙂b', 6), 4)
})

test('an offset inside a character clamps to that character rather than splitting it', () => {
  // Byte 2 lands in the middle of the 4-byte 🙂 that starts at byte 1.
  assert.equal(utf16IndexForByte('a🙂b', 2), 1)
})

test('a byte offset past the end of the line stops at the line end', () => {
  assert.equal(utf16IndexForByte('abc', 99), 3)
  assert.equal(utf16IndexForByte('', 4), 0)
})

test('a bare caret selects nothing', () => {
  const state = snapshot('alpha\nbeta', [caret(0, 2, 0, 2)])
  assert.equal(hasSelection(state), false)
  assert.equal(selectionText(state), '')
})

test('a single-line selection is cut on byte boundaries', () => {
  assert.equal(selectionText(snapshot('alpha\nbeta', [caret(1, 0, 1, 3)])), 'bet')
  assert.equal(selectionText(snapshot('café latte', [caret(0, 0, 0, 5)])), 'café')
})

test('a selection spanning lines keeps the newlines between them', () => {
  const state = snapshot('one\ntwo\nthree', [caret(0, 1, 2, 2)])
  assert.equal(hasSelection(state), true)
  assert.equal(selectionText(state), 'ne\ntwo\nth')
})

test('a backwards selection reads in document order', () => {
  assert.equal(selectionText(snapshot('one\ntwo', [caret(1, 2, 0, 1)])), 'ne\ntw')
})

test('multiple selections join in the order the editor reports them', () => {
  const state = snapshot('one\ntwo\nthree', [caret(0, 0, 0, 3), caret(2, 0, 2, 5)])
  assert.equal(selectionText(state), 'one\nthree')
})

test('lineWise selections take whole lines whatever the byte offsets say', () => {
  const state = snapshot('one\ntwo\nthree', [caret(0, 2, 1, 1, 'lineWise')])
  assert.equal(hasSelection(state), true)
  assert.equal(selectionText(state), 'one\ntwo')
})

test('blockWise selections take the same columns out of every covered line', () => {
  const state = snapshot('abcd\nefgh\nijkl', [caret(0, 1, 2, 3, 'blockWise')])
  assert.equal(selectionText(state), 'bc\nfg\njk')
})

test('positions beyond the document clamp to its last line', () => {
  assert.equal(selectionText(snapshot('one\ntwo', [caret(0, 0, 9, 99)])), 'one\ntwo')
  assert.equal(selectionText(snapshot('one\ntwo', [caret(5, 0, 5, 3, 'lineWise')])), 'two')
})

test('an empty document with a caret yields nothing to send', () => {
  assert.equal(selectionText(snapshot('', [caret(0, 0, 0, 0)])), '')
  assert.equal(selectionText(null), '')
  assert.equal(hasSelection(undefined), false)
})

test('positions order by line first, then byte', () => {
  assert.ok(comparePositions({ line: 1, byteInLine: 0 }, { line: 2, byteInLine: 0 }) < 0)
  assert.ok(comparePositions({ line: 2, byteInLine: 5 }, { line: 2, byteInLine: 2 }) > 0)
  assert.equal(comparePositions({ line: 3, byteInLine: 4 }, { line: 3, byteInLine: 4 }), 0)
})

test('a selection enters the composer without an origin preamble', () => {
  assert.equal(
    composeAgentMessage('body text', { label: '.docs/design.md', scope: 'selection' }),
    'body text',
  )
})

test('a whole-document message retains its source', () => {
  assert.equal(
    composeAgentMessage('body text', { label: 'project note (swe-mux)', scope: 'document' }),
    'From `project note (swe-mux)`:\n\nbody text',
  )
})

test('the message keeps leading indentation and drops only the trailing run', () => {
  const message = composeAgentMessage('    indented\n\n', { label: 'a.md', scope: 'selection' })
  assert.equal(message, '    indented')
})

test('a delivered body is wrapped in bracketed paste with CR line breaks', () => {
  const payload = pastePayload('one\ntwo')
  assert.equal(payload, `${BRACKETED_PASTE_START}one\rtwo${BRACKETED_PASTE_END}`)
  assert.ok(!payload.includes('\n'))
})

test('CRLF and bare CR normalize to the same single break', () => {
  assert.equal(pastePayload('a\r\nb'), pastePayload('a\nb'))
  assert.equal(pastePayload('a\rb'), pastePayload('a\nb'))
})
