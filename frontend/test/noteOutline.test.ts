import assert from 'node:assert/strict'
import test from 'node:test'
import { headingIndexAt, outlineDepths, outlineHeadings } from '../src/noteOutline.ts'

test('ATX headings are collected in document order with their level and line', () => {
  const headings = outlineHeadings('# One\nbody\n## Two\n### Three\ntail')
  assert.deepEqual(
    headings.map(heading => [heading.level, heading.text, heading.line]),
    [[1, 'One', 0], [2, 'Two', 2], [3, 'Three', 3]],
  )
})

test('a heading range covers its whole line in byte offsets', () => {
  const [heading] = outlineHeadings('## é→ hit')
  assert.deepEqual(heading.start, { line: 0, byteInLine: 0 })
  // "##" + space + "é" (2 bytes) + "→" (3) + space + "hit" = 12 bytes.
  assert.deepEqual(heading.end, { line: 0, byteInLine: 12 })
})

test('hashes without a following space are not headings', () => {
  // Otherwise every "#tag" in a notes file becomes an outline entry.
  assert.deepEqual(outlineHeadings('#tag\n#1 thing'), [])
  assert.equal(outlineHeadings('# real').length, 1)
})

test('seven or more hashes is not a heading', () => {
  assert.deepEqual(outlineHeadings('####### too deep'), [])
  assert.equal(outlineHeadings('###### six').length, 1)
})

test('up to three leading spaces still heads; four or a tab does not', () => {
  assert.equal(outlineHeadings('   # indented').length, 1)
  assert.deepEqual(outlineHeadings('    # code block'), [])
  assert.deepEqual(outlineHeadings('\t# code block'), [])
})

test('a closing sequence is stripped, but only when it ends the line', () => {
  assert.equal(outlineHeadings('## foo ##')[0].text, 'foo')
  assert.equal(outlineHeadings('## foo #bar')[0].text, 'foo #bar')
  assert.equal(outlineHeadings('## foo  ')[0].text, 'foo')
  // Nothing but hashes after the marker is an empty heading, not a label of "#".
  assert.equal(outlineHeadings('# #')[0].text, '')
  assert.equal(outlineHeadings('###')[0].text, '')
})

test('headings inside a fenced code block are skipped', () => {
  const text = [
    '# Real',
    '```sh',
    '# not a heading',
    '```',
    '## Also real',
    '~~~',
    '### hidden',
    '~~~',
    '#### Last',
  ].join('\n')
  assert.deepEqual(outlineHeadings(text).map(heading => heading.text), ['Real', 'Also real', 'Last'])
})

test('a fence closes only on its own character and at least its own length', () => {
  // A shorter run, or the other fence character, leaves the block open.
  const text = ['```', '# hidden', '~~~', '``', '````', '# after'].join('\n')
  assert.deepEqual(outlineHeadings(text).map(heading => heading.text), ['after'])
})

test('an unterminated fence swallows the rest of the note', () => {
  assert.deepEqual(outlineHeadings('# Before\n```\n# after'), [{
    level: 1,
    text: 'Before',
    line: 0,
    start: { line: 0, byteInLine: 0 },
    end: { line: 0, byteInLine: 8 },
  }])
})

test('a backtick run with a backtick in its info string does not open a fence', () => {
  // ``` `a` ``` is an inline span, not the start of a block.
  assert.deepEqual(outlineHeadings('``` `a` ```\n# still real').map(h => h.text), ['still real'])
})

test('the caret maps to the heading it sits under', () => {
  const headings = outlineHeadings('# One\nbody\n## Two\nbody\n### Three')
  assert.equal(headingIndexAt(headings, { line: 0, byteInLine: 3 }), 0)
  assert.equal(headingIndexAt(headings, { line: 1, byteInLine: 0 }), 0)
  assert.equal(headingIndexAt(headings, { line: 2, byteInLine: 0 }), 1)
  assert.equal(headingIndexAt(headings, { line: 9, byteInLine: 0 }), 2)
  assert.equal(headingIndexAt(headings, null), -1)
  assert.equal(headingIndexAt([], { line: 0, byteInLine: 0 }), -1)
})

test('a caret above the first heading belongs to no heading', () => {
  const headings = outlineHeadings('preamble\n# One')
  assert.equal(headingIndexAt(headings, { line: 0, byteInLine: 0 }), -1)
})

test('depth counts distinct levels, not hashes', () => {
  // A note whose shallowest heading is "##" should not render every row indented, and the
  // jump from "#" to "###" below costs one step rather than two.
  assert.deepEqual(outlineDepths(outlineHeadings('## a\n### b\n## c')), [0, 1, 0])
  assert.deepEqual(outlineDepths(outlineHeadings('# a\n### b\n###### c')), [0, 1, 2])
  assert.deepEqual(outlineDepths([]), [])
})
