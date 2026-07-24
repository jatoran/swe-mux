import assert from 'node:assert/strict'
import test from 'node:test'
import { focusMemoryWith, parseFocusMemory, parseViewPreference, rememberedView, resolveInitialFocus, viewUrl } from '../src/viewState.ts'

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
    { lastProject: 'default', byProject: { default: 'first' }, viewByProject: {} },
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
  assert.deepEqual(focusMemoryWith({ lastProject: null, byProject: {}, viewByProject: {} }, 'work', 'wanted'), {
    lastProject: 'work', byProject: { work: 'wanted' }, viewByProject: {},
  })
})

test('focus memory persists and restores the focused view, and clears it when absent', () => {
  const withNote = focusMemoryWith({ lastProject: null, byProject: {}, viewByProject: {} }, 'work', 'wanted', 'note:work')
  assert.deepEqual(withNote, { lastProject: 'work', byProject: { work: 'wanted' }, viewByProject: { work: 'note:work' } })
  assert.equal(rememberedView(withNote, 'work'), 'note:work')
  assert.equal(rememberedView(withNote, 'default'), null)
  // A later focus with no view id clears the remembered view for that project.
  const cleared = focusMemoryWith(withNote, 'work', 'wanted', null)
  assert.equal(rememberedView(cleared, 'work'), null)
})

test('parse tolerates legacy focus memory without a view map', () => {
  const parsed = parseFocusMemory('{"lastProject":"work","byProject":{"work":"wanted"}}')
  assert.deepEqual(parsed, { lastProject: 'work', byProject: { work: 'wanted' }, viewByProject: {} })
  assert.equal(rememberedView(parsed, 'work'), null)
})

test('view URL preserves unrelated parameters and hash without navigation history noise', () => {
  assert.equal(
    viewUrl('http://localhost/?debug=1#notes', 'work', 'wanted'),
    '/?debug=1&project=work&session=wanted#notes',
  )
})
