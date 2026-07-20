import assert from 'node:assert/strict'
import test from 'node:test'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, resolveInitialFocus, viewUrl } from '../src/viewState.ts'

const sessions = [
  { id: 'first', project_id: 'default', state: 'running' },
  { id: 'wanted', project_id: 'work', state: 'idle' },
  { id: 'ended', project_id: 'work', state: 'exited' },
]

test('URL session wins and carries its actual project', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted'] },
    parseViewPreference('?project=default&session=wanted'),
    { lastProject: 'default', byProject: { default: 'first' } },
  )
  assert.deepEqual(selected, { projectId: 'work', sessionId: 'wanted' })
})

test('per-device focus wins before the first visible session', () => {
  const selected = resolveInitialFocus(
    sessions,
    ['default', 'work'],
    { default: ['first'], work: ['wanted'] },
    { projectId: 'work', sessionId: null },
    parseFocusMemory('{"lastProject":"default","byProject":{"work":"wanted"}}'),
  )
  assert.deepEqual(selected, { projectId: 'work', sessionId: 'wanted' })
  assert.deepEqual(focusMemoryWith({ lastProject: null, byProject: {} }, 'work', 'wanted'), {
    lastProject: 'work', byProject: { work: 'wanted' },
  })
})

test('view URL preserves unrelated parameters and hash without navigation history noise', () => {
  assert.equal(
    viewUrl('http://localhost/?debug=1#notes', 'work', 'wanted'),
    '/?debug=1&project=work&session=wanted#notes',
  )
})
