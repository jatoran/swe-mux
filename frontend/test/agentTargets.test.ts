import assert from 'node:assert/strict'
import test from 'node:test'
import {
  agentTargetName, agentTargets, backendFromTargetKey, defaultNewTarget, newTargetKey,
  retargetForProject, sessionIdFromTargetKey, sessionTargetKey,
} from '../src/agentTargets.ts'
import type { Project, Session } from '../src/types.ts'

const session = (overrides: Partial<Session> & Pick<Session, 'id'>): Session =>
  ({
    name: overrides.id,
    project_id: 'p1',
    backend: 'claude',
    state: 'idle',
    last_activity_ts: 0,
    ...overrides,
  }) as Session

const project = (overrides: Partial<Project>): Project => ({ id: 'p1', name: 'p1', ...overrides }) as Project

test('only live agent sessions of the named project are offered', () => {
  const sessions = [
    session({ id: 'claude-live' }),
    session({ id: 'codex-live', backend: 'codex' }),
    session({ id: 'shell', backend: 'shell' }),
    session({ id: 'other-project', project_id: 'p2' }),
    session({ id: 'exited', state: 'exited' }),
    session({ id: 'crashed', state: 'crashed' }),
    session({ id: 'optimistic', pending: true }),
  ]
  assert.deepEqual(
    agentTargets(sessions, 'p1').map(item => item.id),
    ['claude-live', 'codex-live'],
  )
})

test('targets are ordered most recently active first', () => {
  const sessions = [
    session({ id: 'stale', last_activity_ts: 10 }),
    session({ id: 'fresh', last_activity_ts: 99 }),
    session({ id: 'never', last_activity_ts: undefined as unknown as number }),
  ]
  assert.deepEqual(
    agentTargets(sessions, 'p1').map(item => item.id),
    ['fresh', 'stale', 'never'],
  )
})

test('a new session follows the project backend, and shell projects still get an agent', () => {
  assert.equal(defaultNewTarget(project({ effective_options: { backend: 'codex' } as Project['effective_options'] })), newTargetKey('codex'))
  assert.equal(defaultNewTarget(project({ effective_options: { backend: 'shell' } as Project['effective_options'] })), newTargetKey('claude'))
  assert.equal(defaultNewTarget(project({ default_backend: 'codex' })), newTargetKey('codex'))
  assert.equal(defaultNewTarget(undefined), newTargetKey('claude'))
})

test('target keys round-trip and never confuse a session with a new spawn', () => {
  assert.equal(sessionIdFromTargetKey(sessionTargetKey('abc')), 'abc')
  assert.equal(sessionIdFromTargetKey(newTargetKey('codex')), null)
  assert.equal(backendFromTargetKey(newTargetKey('codex')), 'codex')
  assert.equal(backendFromTargetKey(newTargetKey('claude')), 'claude')
  // A session key is not a backend; the fallback must be the safe "start Claude", never codex.
  assert.equal(backendFromTargetKey(sessionTargetKey('abc')), 'claude')
})

test('switching project drops a session target that no longer exists there', () => {
  const candidates = [session({ id: 'still-here' })]
  assert.equal(
    retargetForProject(sessionTargetKey('gone'), candidates, project({ default_backend: 'codex' })),
    newTargetKey('codex'),
  )
  assert.equal(
    retargetForProject(sessionTargetKey('still-here'), candidates, project({})),
    sessionTargetKey('still-here'),
  )
})

test('a new-session choice survives a project switch', () => {
  assert.equal(retargetForProject(newTargetKey('codex'), [], project({})), newTargetKey('codex'))
})

test('a generated title names a session only while it is auto-named', () => {
  assert.equal(agentTargetName(session({ id: 's', name: 'claude-abc', generated_title: 'Fix the parser' })), 'Fix the parser')
  assert.equal(
    agentTargetName(session({ id: 's', name: 'renamed', generated_title: 'Fix the parser', auto_named: false })),
    'renamed',
  )
})
