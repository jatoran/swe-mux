import assert from 'node:assert/strict'
import test from 'node:test'
import {
  absoluteProjectPath,
  copySummary,
  FILE_COPY_MAX_CHARS,
  FILE_COPY_MAX_LINES,
  truncateForClipboard,
} from '../src/fileClipboard.ts'

test('absolute paths adopt the project root separator', () => {
  assert.equal(
    absoluteProjectPath('D:\\PROJECTS\\swe-mux', 'frontend/src/App.tsx'),
    'D:\\PROJECTS\\swe-mux\\frontend\\src\\App.tsx',
  )
  assert.equal(
    absoluteProjectPath('/home/dev/swe-mux', 'frontend/src/App.tsx'),
    '/home/dev/swe-mux/frontend/src/App.tsx',
  )
})

test('trailing root separators and leading path separators never double up', () => {
  assert.equal(absoluteProjectPath('D:\\PROJECTS\\swe-mux\\', 'a/b.ts'), 'D:\\PROJECTS\\swe-mux\\a\\b.ts')
  assert.equal(absoluteProjectPath('/home/dev/', '/a/b.ts'), '/home/dev/a/b.ts')
})

test('UNC and bare drive roots stay windows-shaped', () => {
  assert.equal(absoluteProjectPath('\\\\host\\share', 'a/b.ts'), '\\\\host\\share\\a\\b.ts')
  assert.equal(absoluteProjectPath('D:', 'a/b.ts'), 'D:\\a\\b.ts')
})

test('an empty relative path is the root itself', () => {
  assert.equal(absoluteProjectPath('D:\\PROJECTS\\swe-mux', ''), 'D:\\PROJECTS\\swe-mux')
})

test('text within both bounds is copied verbatim with no notice', () => {
  const text = 'line one\nline two\nline three'
  const slice = truncateForClipboard(text, 'a.txt')
  assert.equal(slice.text, text)
  assert.equal(slice.truncated, false)
  assert.equal(slice.lines, 3)
  assert.equal(slice.totalLines, 3)
})

test('line overflow cuts to the limit and names what was dropped', () => {
  const text = Array.from({ length: 20 }, (_, i) => `line ${i}`).join('\n')
  const slice = truncateForClipboard(text, 'big.ts', 5)
  assert.equal(slice.truncated, true)
  assert.equal(slice.lines, 5)
  assert.equal(slice.totalLines, 20)
  assert.ok(slice.text.startsWith('line 0\nline 1\nline 2\nline 3\nline 4'))
  assert.match(slice.text, /truncated big\.ts — copied 5 of 20 lines/)
})

test('character overflow prefers a line boundary', () => {
  const text = 'aaaa\nbbbb\ncccc\ndddd'
  const slice = truncateForClipboard(text, 'x.txt', 100, 12)
  assert.equal(slice.truncated, true)
  // 12 chars lands mid-"cccc"; the cut retreats to the end of "bbbb".
  assert.ok(slice.text.startsWith('aaaa\nbbbb'))
  assert.equal(slice.lines, 2)
})

test('a single enormous line still gets cut', () => {
  const slice = truncateForClipboard('x'.repeat(500), 'min.js', 100, 40)
  assert.equal(slice.truncated, true)
  assert.equal(slice.chars, 40)
  assert.equal(slice.totalChars, 500)
})

test('the shipped bounds admit a 2000-line source file whole', () => {
  const text = Array.from({ length: 2_000 }, (_, i) => `const value${i} = ${i}`).join('\n')
  assert.ok(text.length < FILE_COPY_MAX_CHARS)
  assert.ok(2_000 < FILE_COPY_MAX_LINES)
  assert.equal(truncateForClipboard(text, 'src.ts').truncated, false)
})

test('summaries distinguish a whole copy from a partial one', () => {
  assert.equal(copySummary(truncateForClipboard('a\nb', 'f')), 'Copied 2 lines')
  assert.equal(copySummary(truncateForClipboard('a', 'f')), 'Copied 1 line')
  assert.equal(
    copySummary(truncateForClipboard('a\nb\nc\nd', 'f', 2)),
    'Copied first 2 of 4 lines',
  )
})
