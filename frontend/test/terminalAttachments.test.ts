import assert from 'node:assert/strict'
import test from 'node:test'
import { attachmentReferenceText, attachmentSafeBroadcast, MAX_ATTACHMENTS_PER_ACTION } from '../src/terminalAttachments.ts'

test('one attachment is inserted as a quoted draft reference', () => {
  assert.equal(
    attachmentReferenceText([String.raw`D:\project\.swe-mux\attachments\s\report.csv`]),
    String.raw`Attached file: "D:\project\.swe-mux\attachments\s\report.csv"`,
  )
})

test('multiple attachments become one bracketable list', () => {
  assert.equal(
    attachmentReferenceText(['/project/a.csv', '/project/book.xlsx']),
    'Attached files:\n- "/project/a.csv"\n- "/project/book.xlsx"',
  )
  assert.equal(attachmentReferenceText([]), '')
  assert.equal(MAX_ATTACHMENTS_PER_ACTION, 10)
})

test('quotes in a portable path are escaped', () => {
  assert.equal(attachmentReferenceText(['/tmp/a"b.txt']), 'Attached file: "/tmp/a\\"b.txt"')
})

test('attachment insertion cannot inherit terminal broadcast', () => {
  assert.equal(attachmentSafeBroadcast(true, 0), true)
  assert.equal(attachmentSafeBroadcast(true, 1), false)
  assert.equal(attachmentSafeBroadcast(false, 0), false)
})
