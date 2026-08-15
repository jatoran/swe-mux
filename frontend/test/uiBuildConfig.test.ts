import assert from 'node:assert/strict'
import test from 'node:test'
import { uiBuildId } from '../vite.config.ts'

test('UI build identity is stable across bundle enumeration order', () => {
  const first = uiBuildId(['assets/index-aaa.js', 'assets/index-bbb.css', 'index.html'])
  const reordered = uiBuildId(['index.html', 'assets/index-bbb.css', 'assets/index-aaa.js'])
  assert.equal(first, reordered)
  assert.match(first, /^[0-9a-f]{64}$/)
})

test('UI build identity changes with a content-addressed asset name', () => {
  const first = uiBuildId(['assets/index-aaa.js', 'assets/index-bbb.css'])
  const changed = uiBuildId(['assets/index-ccc.js', 'assets/index-bbb.css'])
  assert.notEqual(first, changed)
})
