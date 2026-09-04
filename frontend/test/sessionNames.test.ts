import assert from 'node:assert/strict'
import test from 'node:test'
import { runDisplayName, sessionDisplayName, titleStability } from '../src/sessionNames.ts'
import type { Session } from '../src/types.ts'

const session = (overrides: Partial<Session> & Pick<Session, 'id' | 'name'>): Session =>
  ({ project_id: 'p1', backend: 'claude', state: 'idle', ...overrides }) as Session

test('a generated title replaces the spawned name while the session is auto-named', () => {
  assert.equal(
    sessionDisplayName(session({ id: 's', name: 'claude-0e7d93', generated_title: 'Fix the parser' })),
    'Fix the parser',
  )
})

test('a rename outranks a title produced afterwards', () => {
  assert.equal(
    sessionDisplayName(session({ id: 's', name: 'release prep', generated_title: 'Fix the parser', auto_named: false })),
    'release prep',
  )
})

test('a session with no title keeps its name', () => {
  assert.equal(sessionDisplayName(session({ id: 's', name: 'claude-0e7d93' })), 'claude-0e7d93')
})

test('run rows apply the same rule to SQLite integers', () => {
  assert.equal(runDisplayName({ name: 'claude-0e7d93', generated_title: 'Fix the parser' }), 'Fix the parser')
  assert.equal(runDisplayName({ name: 'claude-0e7d93', generated_title: 'Fix the parser', auto_named: 1 }), 'Fix the parser')
  assert.equal(runDisplayName({ name: 'release prep', generated_title: 'Fix the parser', auto_named: 0 }), 'release prep')
})

test('an absent auto_named column means auto-named, which is what old rows are', () => {
  assert.equal(runDisplayName({ name: 'claude-0e7d93', generated_title: 'Fix the parser' }), 'Fix the parser')
})

test('an empty title is not a name', () => {
  assert.equal(sessionDisplayName(session({ id: 's', name: 'claude-0e7d93', generated_title: '' })), 'claude-0e7d93')
  assert.equal(runDisplayName({ name: 'claude-0e7d93', generated_title: '' }), 'claude-0e7d93')
})

test('a shown auto-title reports its lifecycle stability', () => {
  assert.equal(
    titleStability(session({ id: 's', name: 'claude-0e7d93', generated_title: 'Fix the parser', generated_title_annotation: { id: 'a', provenance: 'openrouter_observer', created_at: 0, stability: 'provisional' } })),
    'provisional',
  )
  assert.equal(
    titleStability(session({ id: 's', name: 'claude-0e7d93', generated_title: 'Fix the parser', generated_title_annotation: { id: 'a', provenance: 'openrouter_observer', created_at: 0, stability: 'settled' } })),
    'settled',
  )
})

test('a manually named session has no auto-title stability, even with a title behind it', () => {
  assert.equal(
    titleStability(session({ id: 's', name: 'release prep', auto_named: false, generated_title: 'Fix the parser', generated_title_annotation: { id: 'a', provenance: 'openrouter_observer', created_at: 0, stability: 'provisional' } })),
    undefined,
  )
})

test('a title with no lifecycle evidence, or no title at all, reports no stability', () => {
  assert.equal(titleStability(session({ id: 's', name: 'claude-0e7d93', generated_title: 'Fix the parser' })), undefined)
  assert.equal(titleStability(session({ id: 's', name: 'claude-0e7d93' })), undefined)
})
