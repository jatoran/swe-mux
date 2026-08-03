import assert from 'node:assert/strict'
import test from 'node:test'
import { parseDelimitedText } from '../src/delimitedText.ts'

test('delimited preview handles quotes, escaped quotes, and embedded newlines', () => {
  const preview = parseDelimitedText(
    'name,detail\r\nalpha,"one, two"\r\nbeta,"line 1\nline ""2"""\r\n',
    ',',
  )
  assert.deepEqual(preview.rows, [
    ['name', 'detail'],
    ['alpha', 'one, two'],
    ['beta', 'line 1\nline "2"'],
  ])
  assert.equal(preview.columnCount, 2)
  assert.equal(preview.malformed, false)
  assert.equal(preview.truncated, false)
})

test('delimited preview supports tabs and retains empty trailing fields', () => {
  const preview = parseDelimitedText('a\tb\t\n1\t2\t3', '\t')
  assert.deepEqual(preview.rows, [['a', 'b', ''], ['1', '2', '3']])
})

test('delimited preview applies row, column, cell, and field bounds while parsing', () => {
  const preview = parseDelimitedText('abcdef,2,3,4\n5,6,7,8\n9,10,11,12', ',', {
    rows: 3,
    columns: 2,
    cells: 4,
    fieldChars: 3,
  })
  assert.deepEqual(preview.rows, [['abc…', '2'], ['5', '6']])
  assert.equal(preview.truncated, true)
  assert.equal(preview.fieldTruncated, true)
})

test('delimited preview reports malformed quoting without interpreting content', () => {
  const preview = parseDelimitedText('a,b\n"unterminated,cell', ',')
  assert.equal(preview.malformed, true)
  assert.deepEqual(preview.rows, [['a', 'b'], ['unterminated,cell']])
})
