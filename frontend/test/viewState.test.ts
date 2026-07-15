import assert from 'node:assert/strict'
import test from 'node:test'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, resolveInitialFocus, viewUrl } from '../src/viewState.ts'

const sessions = [
  { id: 'first', space_id: 'default', state: 'running' },
  { id: 'wanted', space_id: 'work', state: 'idle' },
  { id: 'ended', space_id: 'work', state: 'exited' },
]

test('URL session wins and carries its actual space', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted'] },
    parseViewPreference('?space=default&session=wanted'),
    { lastSpace: 'default', bySpace: { default: 'first' } },
  )
  assert.deepEqual(selected, { spaceId: 'work', sessionId: 'wanted' })
})

test('per-device focus wins before the first visible session', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted'] },
    { spaceId: 'work', sessionId: null },
    parseFocusMemory('{"lastSpace":"default","bySpace":{"work":"wanted"}}'),
  )
  assert.deepEqual(selected, { spaceId: 'work', sessionId: 'wanted' })
  assert.deepEqual(focusMemoryWith({ lastSpace: null, bySpace: {} }, 'work', 'wanted'), {
    lastSpace: 'work', bySpace: { work: 'wanted' },
  })
})

test('view URL preserves unrelated parameters and hash without navigation history noise', () => {
  assert.equal(
    viewUrl('http://localhost/?debug=1#notes', 'work', 'wanted'),
    '/?debug=1&space=work&session=wanted#notes',
  )
})
