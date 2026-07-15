import assert from 'node:assert/strict'
import test from 'node:test'
import { buildSpaceGroups } from '../src/spaceGroups.ts'

const session = (id: string, space_id: string, created_at: number) =>
  ({ id, name: id, space_id, created_at }) as never
const space = (id: string, name: string, position: number) =>
  ({ id, name, position }) as never
const group = (session_id: string, space_id: string) => ({ session_id, space_id, processes: [] })

test('spaces are ordered by position with a MAX fallback for an unknown space', () => {
  const groups = buildSpaceGroups(
    [group('s1', 'spaceB'), group('s2', 'spaceA')],
    [session('s1', 'spaceB', 1), session('s2', 'spaceA', 2)],
    [space('spaceA', 'A', 3), space('spaceB', 'B', 7)],
  )
  assert.deepEqual(groups.map(g => g.id), ['spaceA', 'spaceB'])
  assert.deepEqual(groups.map(g => g.label), ['A', 'B'])
})

test('sessions within a space are ordered by created_at', () => {
  const groups = buildSpaceGroups(
    [group('late', 'sp'), group('early', 'sp')],
    [session('late', 'sp', 100), session('early', 'sp', 1)],
    [space('sp', 'S', 0)],
  )
  assert.deepEqual(groups[0].groups.map(g => g.session_id), ['early', 'late'])
})

test('a group whose session is absent falls back to group.space_id then "unknown"', () => {
  const groups = buildSpaceGroups(
    [group('ghost', 'fallback-space'), group('nowhere', '')],
    [],
    [space('fallback-space', 'FB', 0)],
  )
  assert.deepEqual([...groups.map(g => g.id)].sort(), ['fallback-space', 'unknown'])
  assert.equal(groups.find(g => g.id === 'unknown')?.label, 'Unknown space')
})

test('space position 0 is honoured (?? not ||)', () => {
  const groups = buildSpaceGroups(
    [group('a', 'zero'), group('b', 'big')],
    [session('a', 'zero', 1), session('b', 'big', 1)],
    [space('zero', 'Z', 0), space('big', 'G', 10)],
  )
  assert.deepEqual(groups.map(g => g.id), ['zero', 'big'])
})
