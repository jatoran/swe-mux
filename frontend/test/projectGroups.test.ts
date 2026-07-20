import assert from 'node:assert/strict'
import test from 'node:test'
import { buildProjectGroups } from '../src/projectGroups.ts'

const session = (id: string, project_id: string, created_at: number) =>
  ({ id, name: id, project_id, created_at }) as never
const project = (id: string, name: string, position: number) =>
  ({ id, name, position }) as never
const group = (session_id: string, project_id: string) => ({ session_id, project_id, processes: [] })

test('projects are ordered by position with a MAX fallback for an unknown project', () => {
  const groups = buildProjectGroups(
    [group('s1', 'spaceB'), group('s2', 'spaceA')],
    [session('s1', 'spaceB', 1), session('s2', 'spaceA', 2)],
    [project('spaceA', 'A', 3), project('spaceB', 'B', 7)],
  )
  assert.deepEqual(groups.map(g => g.id), ['spaceA', 'spaceB'])
  assert.deepEqual(groups.map(g => g.label), ['A', 'B'])
})

test('sessions within a project are ordered by created_at', () => {
  const groups = buildProjectGroups(
    [group('late', 'sp'), group('early', 'sp')],
    [session('late', 'sp', 100), session('early', 'sp', 1)],
    [project('sp', 'S', 0)],
  )
  assert.deepEqual(groups[0].groups.map(g => g.session_id), ['early', 'late'])
})

test('a group whose session is absent falls back to group.project_id then "unknown"', () => {
  const groups = buildProjectGroups(
    [group('ghost', 'fallback-project'), group('nowhere', '')],
    [],
    [project('fallback-project', 'FB', 0)],
  )
  assert.deepEqual([...groups.map(g => g.id)].sort(), ['fallback-project', 'unknown'])
  assert.equal(groups.find(g => g.id === 'unknown')?.label, 'Unknown project')
})

test('project position 0 is honoured (?? not ||)', () => {
  const groups = buildProjectGroups(
    [group('a', 'zero'), group('b', 'big')],
    [session('a', 'zero', 1), session('b', 'big', 1)],
    [project('zero', 'Z', 0), project('big', 'G', 10)],
  )
  assert.deepEqual(groups.map(g => g.id), ['zero', 'big'])
})
