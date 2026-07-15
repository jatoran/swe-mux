import assert from 'node:assert/strict'
import test from 'node:test'
import { indentColumns } from '../src/noteEditor.ts'

test('leading spaces count as one column each', () => {
  assert.equal(indentColumns('no indent', 2), 0)
  assert.equal(indentColumns('  - nested bullet', 2), 2)
  assert.equal(indentColumns('      deep', 2), 6)
})

test('tabs advance to the next tab stop', () => {
  assert.equal(indentColumns('\tone tab', 2), 2)
  assert.equal(indentColumns('\t\ttwo tabs', 2), 4)
  assert.equal(indentColumns('\tx', 4), 4)
  // A space then a tab: the tab still completes the stop.
  assert.equal(indentColumns(' \tmixed', 4), 4)
  assert.equal(indentColumns('   \tmixed', 4), 4)
})

test('only leading whitespace counts', () => {
  assert.equal(indentColumns('  text with  inner  spaces', 2), 2)
  assert.equal(indentColumns('', 2), 0)
  assert.equal(indentColumns('   ', 2), 3)
})
